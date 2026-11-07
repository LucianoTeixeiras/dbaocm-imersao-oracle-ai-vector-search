"""LRT-02: tema Mermaid claro de alto contraste em todos os tipos de diagrama."""

import pytest

from knowledge_extractor import mermaid_theme as mt
from knowledge_extractor.mermaid_fixer import (
    LIGHT_THEME_INIT,
    apply_light_theme,
    sanitize_markdown,
)

DIAGRAMS = {
    "flowchart": "flowchart TD\n  A[Início] --> B[Fim]",
    "sequence": "sequenceDiagram\n  Alice->>Bob: Oi\n  Note right of Bob: pensa",
    "class": "classDiagram\n  class Animal {\n    +nome\n  }",
    "state": "stateDiagram-v2\n  [*] --> Ativo\n  Ativo --> [*]",
    "er": "erDiagram\n  CLIENTE ||--o{ PEDIDO : faz",
    "gantt": "gantt\n  title Plano\n  dateFormat YYYY-MM-DD\n  section A\n  Tarefa :a1, 2026-01-01, 3d",
    "pie": 'pie title Uso\n  "A" : 40\n  "B" : 60',
    "mindmap": "mindmap\n  root((RAG))\n    Busca\n    Geração",
    "timeline": "timeline\n  title Hist\n  2024 : v1\n  2025 : v2",
    "journey": "journey\n  title Dia\n  section Manhã\n    Café: 5: Eu",
    "gitGraph": "gitGraph\n  commit\n  branch dev\n  commit\n  checkout main\n  merge dev",
}


def _md(body: str) -> str:
    return f"# Nota\n\n```mermaid\n{body}\n```\n"


@pytest.mark.parametrize("kind", list(DIAGRAMS))
def test_every_diagram_type_gets_canonical_init(kind: str) -> None:
    fixed, changed = sanitize_markdown(_md(DIAGRAMS[kind]))
    assert changed == 1
    assert fixed.count(LIGHT_THEME_INIT) == 1
    assert "'theme':'base'" in LIGHT_THEME_INIT


@pytest.mark.parametrize("kind", list(DIAGRAMS))
def test_idempotent(kind: str) -> None:
    once, _ = sanitize_markdown(_md(DIAGRAMS[kind]))
    twice, changed = sanitize_markdown(once)
    assert twice == once and changed == 0


def test_type_specific_variables_present() -> None:
    v = mt.THEME_VARIABLES
    for key in [
        "actorBkg",
        "noteBkgColor",
        "pie1",
        "pie12",
        "cScale0",
        "cScale11",
        "git0",
        "git7",
        "fillType0",
        "taskTextColor",
        "classText",
        "labelColor",
        "attributeBackgroundColorOdd",
    ]:
        assert key in v


@pytest.mark.parametrize(
    ("fg", "bg"),
    [
        (mt.TEXT, mt.BG),
        (mt.TEXT, mt.SURFACE),
        (mt.TEXT, mt.SURFACE_ALT),
        (mt.TEXT, mt.SURFACE_3),
        (mt.TEXT, mt.NOTE_BG),
        (mt.TEXT, mt.CRIT),
        *[(mt.TEXT, c) for c in mt.SERIES],
    ],
)
def test_text_contrast_is_aaa(fg: str, bg: str) -> None:
    assert mt.contrast(fg, bg) >= 7.0


def test_lines_contrast() -> None:
    assert mt.contrast(mt.LINE, mt.BG) >= 7.0
    assert all(mt.contrast(mt.LINE, c) >= 4.5 for c in mt.SERIES)


def test_contrast_reference_values() -> None:
    assert mt.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert mt.contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


def test_existing_dark_init_is_replaced() -> None:
    body = "%%{init: {'theme': 'dark'}}%%\nflowchart TD\n  A --> B"
    fixed, _ = sanitize_markdown(_md(body))
    assert "'dark'" not in fixed and fixed.count("%%{init") == 1


@pytest.mark.parametrize("theme", ["dark", "forest", "neutral", "redux-dark"])
def test_frontmatter_theme_removed_title_kept(theme: str) -> None:
    body = f"---\ntitle: Meu\nconfig:\n  theme: {theme}\n  themeVariables:\n    primaryColor: '#000'\n---\nflowchart TD\n  A --> B"
    out = apply_light_theme(body)
    assert f"theme: {theme}" not in out and "title: Meu" in out
    assert out.index("---", 1) < out.index("%%{init")


def test_dark_fill_and_light_text_neutralized() -> None:
    body = (
        "flowchart TD\n  A --> B\n  style A fill:#111,color:#fff,stroke:#eee\n"
        "  classDef escuro fill:black,color:white\n  linkStyle 0 stroke:#f0f0f0\n"
        "  classDef ok fill:#DBEAFE,color:#0F172A"
    )
    out = apply_light_theme(body)
    assert f"style A fill:{mt.SURFACE},color:{mt.TEXT},stroke:{mt.LINE}" in out
    assert f"classDef escuro fill:{mt.SURFACE},color:{mt.TEXT}" in out
    assert f"linkStyle 0 stroke:{mt.LINE}" in out
    assert "classDef ok fill:#DBEAFE,color:#0F172A" in out  # já acessível: intocado


def test_mid_tone_fill_detected_by_luminance_not_list() -> None:
    out = apply_light_theme(
        "flowchart TD\n  A\n  style A fill:#3B82F6"
    )  # azul médio: 4.2:1 com TEXT
    assert f"fill:{mt.SURFACE}" in out


def test_non_mermaid_blocks_untouched() -> None:
    md = "```python\nstyle = 'fill:#000'\n```\n\n```\n%%{init: {'theme':'dark'}}%%\n```\n"
    out, changed = sanitize_markdown(md)
    assert out == md and changed == 0


def test_prompt_uses_palette_constants() -> None:
    s = mt.prompt_snippet()
    assert mt.TEXT in s and mt.SURFACE in s and mt.LINE in s


def test_recover_orphan_unfenced_mermaid() -> None:
    """Garante que diagramas gerados sem a fence ```mermaid de abertura sejam encapsulados e sanitizados."""
    raw = (
        "# Título\n\n"
        "%%{init: {'theme': 'base'}}%%\n"
        "flowchart TD\n"
        '    A["Início"] --> B["Fim"]\n'
        "```\n\n"
        "Texto seguinte."
    )
    fixed, changed = sanitize_markdown(raw)
    assert changed >= 1
    assert "```mermaid\n" in fixed
    assert fixed.count("```mermaid") == 1
    assert fixed.count("```\n\nTexto seguinte.") == 1
    assert mt.BG in fixed


def test_recover_generic_code_block_with_mermaid() -> None:
    """Garante que blocos de código genéricos ``` sem linguagem contendo Mermaid sejam convertidos para ```mermaid."""
    raw = "```\nflowchart LR\n    A --> B\n```\n"
    fixed, changed = sanitize_markdown(raw)
    assert changed >= 1
    assert "```mermaid\n" in fixed
    assert mt.BG in fixed


def test_recover_nested_mermaid_inside_markdown_block() -> None:
    """Garante que ```mermaid aninhado dentro de ```markdown seja desaninhado."""
    raw = "```markdown\n# Exemplo\n\n```mermaid\nflowchart TD\n    A --> B\n```\n"
    fixed, changed = sanitize_markdown(raw)
    assert changed >= 1
    assert "```markdown\n" in fixed
    assert "```mermaid\n" in fixed
