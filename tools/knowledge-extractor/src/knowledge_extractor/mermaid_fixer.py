"""Sanitizador de diagramas Mermaid gerados por LLM.

Corrige os erros de sintaxe mais comuns em blocos ```mermaid do tipo flowchart/graph:
- títulos de subgraph com espaços/parênteses sem aspas  -> subgraph sg1["Título (x)"]
- rótulos de nós sem aspas contendo ( ) / : etc.         -> A["Texto (x)"]
- rótulos de arestas no formato -- texto -->            -> -->|"texto"|
- rótulos de arestas |texto| sem aspas                  -> |"texto"|
Em TODOS os tipos de diagrama aplica o tema claro de alto contraste (ver mermaid_theme).
"""

from __future__ import annotations

import re
from pathlib import Path

FENCE_RE = re.compile(r"(```mermaid[ \t]*\r?\n)(.*?)(\r?\n[ \t]*```)", re.DOTALL)
FLOW_HEADER_RE = re.compile(r"^\s*(graph|flowchart)\b", re.IGNORECASE)
SKIP_LINE_RE = re.compile(
    r"^\s*(style|classDef|class|linkStyle|click|end|direction|%%)\b", re.IGNORECASE
)
SIMPLE_ID_RE = re.compile(r"^[A-Za-z_][\w-]*$")

from knowledge_extractor.mermaid_theme import (
    BG,
    LINE,
    SURFACE,
    TEXT,
    contrast,
    init_directive,
    parse_color,
)

# Diretiva canônica gerada da paleta única (mermaid_theme)
LIGHT_THEME_INIT = init_directive()
BR_RE = re.compile(r"[ \t]*<br\s*/?>[ \t]*", re.IGNORECASE)
INIT_RE = re.compile(
    r"^[ \t]*%%\{\s*(init|initialize)\s*:.*?\}%%[ \t]*\r?\n?",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
STYLE_LINE_RE = re.compile(r"^(\s*)(style|classDef|linkStyle)\b(.*)$", re.IGNORECASE)
PROP_RE = re.compile(
    r"(?P<k>fill|color|stroke|background(?:-color)?)\s*:\s*(?P<v>#[0-9A-Fa-f]{3,8}|rgba?\([^)]*\)|[A-Za-z]+)"
)
MIN_TEXT = 7.0  # WCAG AAA
MIN_LINE = 3.0

# Formas de nó: (abre, fecha). Formas duplas primeiro para não serem engolidas pelas simples.
SHAPES = [
    ("((", "))"),
    ("([", "])"),
    ("[[", "]]"),
    ("[(", ")]"),
    ("{{", "}}"),
    ("[/", "/]"),
    ("[\\", "\\]"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
]
# O fechamento só vale se seguido de fim de linha, espaço, ';', '&' ou início de seta —
# assim rótulos com parênteses internos, ex.: F(create_session()), são capturados inteiros.
_CLOSE_END = r"(?=\s|$|;|&|-|=|\.|~)"
_shape_alt = "|".join(
    rf"{re.escape(o)}(?P<t{i}>[^\n]*?){re.escape(c)}{_CLOSE_END}"
    for i, (o, c) in enumerate(SHAPES)
)
NODE_RE = re.compile(rf"(?P<id>\b[A-Za-z_]\w*)(?:{_shape_alt})")
QUOTED_RE = re.compile(r'"[^"\n]*"')

EDGE_TEXT_RE = re.compile(
    r"(?P<pre>\s)(?P<op>--|==|-\.)\s+(?P<txt>[^|>\n]+?)\s+(?P<arrow>-->|==>|\.->|---)"
)
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
    return PIPE_LABEL_RE.sub(
        lambda m: f"|{_q(m.group('txt'))}|" if m.group("txt").strip() else m.group(0),
        line,
    )


def sanitize_flowchart(body: str) -> str:
    lines = body.splitlines()
    out: list[str] = []
    sg_count = 0
    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]

        if stripped.lower().startswith("subgraph "):
            title = stripped[len("subgraph ") :].strip()
            # já no formato id["título"] ou id[título] -> só garante aspas
            m = re.match(r"^([A-Za-z_][\w-]*)\s*\[(.*)\]\s*$", title)
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
        if cm and not QUOTED_RE.search(line[cm.start() :]):
            comment = cm.group(1).strip()
            line = line[: cm.start()].rstrip()
            stripped = line.strip()
        if stripped.startswith("//"):
            out.append(f"{indent}%% {stripped[2:].strip()}")
            continue
        if comment:
            out.append(f"{indent}%% {comment}")

        if (
            not stripped
            or SKIP_LINE_RE.match(stripped)
            or FLOW_HEADER_RE.match(stripped)
        ):
            out.append(line)
            continue

        out.append(_fix_nodes(_fix_edges(line)))
    return "\n".join(out)


def _strip_frontmatter_config(body: str) -> str:
    """Remove a seção `config:` de um frontmatter Mermaid (---), preservando title e demais chaves."""
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return body
    try:
        close = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == "---")
    except StopIteration:
        return body
    kept: list[str] = []
    skipping = False
    for ln in lines[i + 1 : close]:
        top = ln and not ln[0].isspace()
        if top:
            skipping = ln.strip().startswith("config:")
        if not skipping:
            kept.append(ln)
    fm = ["---", *kept, "---"] if any(k.strip() for k in kept) else []
    return "\n".join(lines[:i] + fm + lines[close + 1 :])


def _fix_style_line(line: str) -> str:
    m = STYLE_LINE_RE.match(line)
    if not m:
        return line
    rest = m.group(3)
    props = {pm.group("k").lower(): pm.group("v") for pm in PROP_RE.finditer(rest)}
    fill = props.get("fill") or props.get("background") or props.get("background-color")
    eff_fill = fill if fill and parse_color(fill) else BG
    if fill and parse_color(fill) and contrast(fill, TEXT) < MIN_TEXT:
        eff_fill = SURFACE

    def repl(pm: re.Match) -> str:
        k, v = pm.group("k"), pm.group("v")
        kl = k.lower()
        if parse_color(v) is None:
            return pm.group(0)
        if kl in ("fill", "background", "background-color"):
            return f"{k}:{eff_fill}" if contrast(v, TEXT) < MIN_TEXT else pm.group(0)
        if kl == "color":
            return f"{k}:{TEXT}" if contrast(v, eff_fill) < MIN_TEXT else pm.group(0)
        if kl == "stroke":
            return f"{k}:{LINE}" if contrast(v, BG) < MIN_LINE else pm.group(0)
        return pm.group(0)

    new_rest = PROP_RE.sub(repl, rest)
    # texto claro implícito: se o fill mudou e não havia color, nada a fazer (o tema já usa TEXT)
    return f"{m.group(1)}{m.group(2)}{new_rest}"


def apply_light_theme(body: str) -> str:
    """Aplica o tema claro a QUALQUER tipo de diagrama: remove init/frontmatter config, injeta o canônico
    e neutraliza style/classDef/linkStyle com fill escuro ou texto de baixo contraste. Idempotente."""
    new = INIT_RE.sub("", body)
    new = _strip_frontmatter_config(new)
    new = "\n".join(_fix_style_line(ln) for ln in new.split("\n"))
    lines = new.split("\n")
    # o init vai após o frontmatter (se houver), antes da declaração do diagrama
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        close = next(
            (j for j in range(i + 1, len(lines)) if lines[j].strip() == "---"), None
        )
        if close is not None:
            return "\n".join(
                lines[: close + 1] + [LIGHT_THEME_INIT] + lines[close + 1 :]
            )
    return LIGHT_THEME_INIT + "\n" + new.lstrip("\n")


def _first_decl(body: str) -> str:
    """Primeira linha de declaração do diagrama, ignorando frontmatter, init e comentários."""
    in_fm = False
    for ln in body.split("\n"):
        t = ln.strip()
        if t == "---":
            in_fm = not in_fm
            continue
        if in_fm or not t or t.startswith("%%"):
            continue
        return t
    return ""


def sanitize_markdown(markdown: str) -> tuple[str, int]:
    """Sanitiza todos os blocos mermaid de fluxograma e injeta tema claro de alto contraste. Retorna (texto, nº de blocos alterados)."""
    changed = 0

    def repl(m: re.Match) -> str:
        nonlocal changed
        body = m.group(2)
        lines = body.splitlines()
        first = next(
            (ln for ln in lines if ln.strip() and not ln.strip().startswith("%%")), ""
        )
        new_body = BR_RE.sub(
            " ", body
        )  # <br/> é HTML: alguns renderizadores o exibem literal
        if FLOW_HEADER_RE.match(first) or _first_decl(body).lower().startswith(
            ("graph", "flowchart")
        ):
            new_body = sanitize_flowchart(new_body)
        new_body = apply_light_theme(new_body)

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
