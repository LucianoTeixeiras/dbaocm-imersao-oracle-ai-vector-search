"""Sanitizador de diagramas Mermaid gerados por LLM.

Corrige os erros de sintaxe mais comuns em blocos ```mermaid do tipo flowchart/graph:
- títulos de subgraph com espaços/parênteses sem aspas  -> subgraph sg1["Título (x)"]
- rótulos de nós sem aspas contendo ( ) / : etc.         -> A["Texto (x)"]
- rótulos de arestas no formato -- texto -->            -> -->|"texto"|
- rótulos de arestas |texto| sem aspas                  -> |"texto"|
Outros tipos de diagrama (sequenceDiagram, stateDiagram, etc.) não são alterados.
"""

from __future__ import annotations

import re
from pathlib import Path

FENCE_RE = re.compile(r"(```mermaid[ \t]*\r?\n)(.*?)(\r?\n[ \t]*```)", re.DOTALL)
FLOW_HEADER_RE = re.compile(r"^\s*(graph|flowchart)\b", re.IGNORECASE)
SKIP_LINE_RE = re.compile(r"^\s*(style|classDef|class|linkStyle|click|end|direction|%%)\b", re.IGNORECASE)
SIMPLE_ID_RE = re.compile(r"^[A-Za-z_][\w-]*$")

# Formas de nó: (abre, fecha). Formas duplas primeiro para não serem engolidas pelas simples.
SHAPES = [
    ("((", "))"), ("([", "])"), ("[[", "]]"), ("[(", ")]"), ("{{", "}}"),
    ("[/", "/]"), ("[\\", "\\]"), ("[", "]"), ("(", ")"), ("{", "}"),
]
# O fechamento só vale se seguido de fim de linha, espaço, ';', '&' ou início de seta —
# assim rótulos com parênteses internos, ex.: F(create_session()), são capturados inteiros.
_CLOSE_END = r"(?=\s|$|;|&|-|=|\.|~)"
_shape_alt = "|".join(
    rf"{re.escape(o)}(?P<t{i}>[^\n]*?){re.escape(c)}{_CLOSE_END}" for i, (o, c) in enumerate(SHAPES)
)
NODE_RE = re.compile(rf"(?P<id>\b[A-Za-z_]\w*)(?:{_shape_alt})")
QUOTED_RE = re.compile(r'"[^"\n]*"')

EDGE_TEXT_RE = re.compile(r"(?P<pre>\s)(?P<op>--|==|-\.)\s+(?P<txt>[^|>\n]+?)\s+(?P<arrow>-->|==>|\.->|---)")
PIPE_LABEL_RE = re.compile(r"\|(?P<txt>[^|\n]*)\|")


def _q(text: str) -> str:
    """Envolve em aspas, escapando aspas internas no formato do Mermaid."""
    t = text.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        t = t[1:-1]
    return '"' + t.replace('"', "#quot;") + '"'


def _fix_nodes(line: str) -> str:
    # Protege textos já entre aspas (ex.: rótulos de arestas) para não confundi-los com nós
    saved: list[str] = []

    def mask(m: re.Match) -> str:
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    masked = QUOTED_RE.sub(mask, line)

    def unmask(s: str) -> str:
        return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], s)

    def repl(m: re.Match) -> str:
        for i, (o, c) in enumerate(SHAPES):
            txt = m.group(f"t{i}")
            if txt is not None:
                if re.fullmatch(r"\s*\x00\d+\x00\s*", txt):  # já estava entre aspas
                    return m.group(0)
                return f"{m.group('id')}{o}{_q(unmask(txt))}{c}"
        return m.group(0)

    return unmask(NODE_RE.sub(repl, masked))


ARROW_FOR_OP = {"--": "-->", "==": "==>", "-.": "-.->"}


def _fix_edges(line: str) -> str:
    def edge_repl(m: re.Match) -> str:
        arrow = "---" if m.group("arrow") == "---" else ARROW_FOR_OP[m.group("op")]
        return f"{m.group('pre')}{arrow}|{_q(m.group('txt'))}|"

    line = EDGE_TEXT_RE.sub(edge_repl, line)
    return PIPE_LABEL_RE.sub(lambda m: f"|{_q(m.group('txt'))}|" if m.group("txt").strip() else m.group(0), line)


def sanitize_flowchart(body: str) -> str:
    lines = body.splitlines()
    out: list[str] = []
    sg_count = 0
    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]

        if stripped.lower().startswith("subgraph "):
            title = stripped[len("subgraph "):].strip()
            # já no formato id["título"] ou id[título] -> só garante aspas
            m = re.match(r'^([A-Za-z_][\w-]*)\s*\[(.*)\]\s*$', title)
            if m:
                out.append(f"{indent}subgraph {m.group(1)}[{_q(m.group(2))}]")
            elif SIMPLE_ID_RE.match(title):
                out.append(line)
            else:
                sg_count += 1
                out.append(f"{indent}subgraph sg{sg_count}[{_q(title)}]")
            continue

        if re.fullmatch(r"end;?", stripped, re.IGNORECASE):  # 'End' / 'END' → 'end'
            out.append(f"{indent}end")
            continue

        # Comentários estilo // (inválidos) → %% em linha própria
        comment = ""
        cm = re.search(r"\s//\s?(.*)$", line)
        if cm and not QUOTED_RE.search(line[cm.start():]):
            comment = cm.group(1).strip()
            line = line[: cm.start()].rstrip()
            stripped = line.strip()
        if stripped.startswith("//"):
            out.append(f"{indent}%% {stripped[2:].strip()}")
            continue
        if comment:
            out.append(f"{indent}%% {comment}")

        if not stripped or SKIP_LINE_RE.match(stripped) or FLOW_HEADER_RE.match(stripped):
            out.append(line)
            continue

        out.append(_fix_nodes(_fix_edges(line)))
    return "\n".join(out)


def sanitize_markdown(markdown: str) -> tuple[str, int]:
    """Sanitiza todos os blocos mermaid de fluxograma. Retorna (texto, nº de blocos alterados)."""
    changed = 0

    def repl(m: re.Match) -> str:
        nonlocal changed
        body = m.group(2)
        first = next((ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("%%")), "")
        if not FLOW_HEADER_RE.match(first):
            return m.group(0)
        new_body = sanitize_flowchart(body)
        if new_body != body:
            changed += 1
        return m.group(1) + new_body + m.group(3)

    return FENCE_RE.sub(repl, markdown), changed


def sanitize_file(path: Path, dry_run: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    new_text, changed = sanitize_markdown(text)
    if changed and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return changed
