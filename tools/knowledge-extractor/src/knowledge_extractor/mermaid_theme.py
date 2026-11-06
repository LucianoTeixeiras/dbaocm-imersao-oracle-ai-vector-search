"""Paleta única de alto contraste (fundo claro) para diagramas Mermaid.

Fonte única de verdade: usada pelo prompt de síntese (synthesizer) e pelo
pós-processador (mermaid_fixer). Não duplique valores em outro lugar.
Alvo: texto ≥ 7:1 (WCAG AAA) contra o fundo onde aparece; bordas/linhas ≥ 3:1.
"""

from __future__ import annotations

import json
import re

# Cores base
BG = "#FFFFFF"  # fundo do diagrama
TEXT = "#0F172A"  # texto principal (slate-900)
SURFACE = "#F1F5F9"  # preenchimento de nós/atores/caixas
SURFACE_ALT = "#E0E7FF"  # secundário (índigo claro)
SURFACE_3 = "#ECFDF5"  # terciário / clusters (verde muito claro)
LINE = "#1E3A8A"  # bordas, setas, linhas (azul-900)
NOTE_BG = "#FEF9C3"  # notas (amarelo claro)
CRIT = "#FEE2E2"  # crítico (vermelho claro)
CRIT_BORDER = "#991B1B"

# Série categórica clara (pie, journey, cScale, git): todas ≥ 7:1 com TEXT
SERIES = [
    "#DBEAFE",
    "#FEF3C7",
    "#DCFCE7",
    "#FCE7F3",
    "#E0E7FF",
    "#FFEDD5",
    "#CCFBF1",
    "#F3E8FF",
    "#FEF9C3",
    "#E2E8F0",
    "#FFE4E6",
    "#ECFCCB",
]
FONT_SIZE = "18px"


def _vars() -> dict[str, str]:
    v: dict[str, str] = {
        # gerais
        "darkMode": "false",
        "background": BG,
        "fontSize": FONT_SIZE,
        "fontFamily": "Arial, Helvetica, sans-serif",
        "primaryColor": SURFACE,
        "primaryTextColor": TEXT,
        "primaryBorderColor": LINE,
        "secondaryColor": SURFACE_ALT,
        "secondaryTextColor": TEXT,
        "secondaryBorderColor": LINE,
        "tertiaryColor": SURFACE_3,
        "tertiaryTextColor": TEXT,
        "tertiaryBorderColor": LINE,
        "mainBkg": SURFACE,
        "textColor": TEXT,
        "lineColor": LINE,
        "noteBkgColor": NOTE_BG,
        "noteTextColor": TEXT,
        "noteBorderColor": LINE,
        "errorBkgColor": CRIT,
        "errorTextColor": TEXT,
        # flowchart
        "nodeBorder": LINE,
        "nodeTextColor": TEXT,
        "clusterBkg": BG,
        "clusterBorder": LINE,
        "defaultLinkColor": LINE,
        "titleColor": TEXT,
        "edgeLabelBackground": BG,
        # sequence
        "actorBkg": SURFACE,
        "actorBorder": LINE,
        "actorTextColor": TEXT,
        "actorLineColor": LINE,
        "signalColor": TEXT,
        "signalTextColor": TEXT,
        "labelBoxBkgColor": SURFACE,
        "labelBoxBorderColor": LINE,
        "labelTextColor": TEXT,
        "loopTextColor": TEXT,
        "activationBkgColor": SURFACE_ALT,
        "activationBorderColor": LINE,
        "sequenceNumberColor": BG,
        # state / class / er
        "labelColor": TEXT,
        "altBackground": SURFACE_ALT,
        "classText": TEXT,
        "attributeBackgroundColorOdd": BG,
        "attributeBackgroundColorEven": SURFACE,
        # gantt
        "sectionBkgColor": SURFACE,
        "altSectionBkgColor": BG,
        "sectionBkgColor2": SURFACE_ALT,
        "taskBkgColor": SURFACE_ALT,
        "taskBorderColor": LINE,
        "taskTextColor": TEXT,
        "taskTextLightColor": TEXT,
        "taskTextDarkColor": TEXT,
        "taskTextOutsideColor": TEXT,
        "taskTextClickableColor": LINE,
        "activeTaskBkgColor": "#BFDBFE",
        "activeTaskBorderColor": LINE,
        "doneTaskBkgColor": "#E2E8F0",
        "doneTaskBorderColor": "#334155",
        "critBkgColor": CRIT,
        "critBorderColor": CRIT_BORDER,
        "gridColor": "#475569",
        "todayLineColor": CRIT_BORDER,
        "excludeBkgColor": "#F8FAFC",
        # pie
        "pieTitleTextColor": TEXT,
        "pieSectionTextColor": TEXT,
        "pieLegendTextColor": TEXT,
        "pieStrokeColor": LINE,
        "pieOuterStrokeColor": LINE,
        "pieOpacity": "1",
        "pieTitleTextSize": "24px",
        "pieSectionTextSize": FONT_SIZE,
        "pieLegendTextSize": FONT_SIZE,
        # gitGraph
        "commitLabelColor": TEXT,
        "commitLabelBackground": BG,
        "tagLabelColor": TEXT,
        "tagLabelBackground": NOTE_BG,
        "tagLabelBorder": LINE,
    }
    for i, c in enumerate(SERIES):
        v[f"pie{i + 1}"] = c
        v[f"cScale{i}"] = c  # mindmap, timeline, kanban
        v[f"cScaleLabel{i}"] = TEXT
        v[f"cScalePeer{i}"] = LINE
    for i in range(8):
        v[f"fillType{i}"] = SERIES[i]  # journey
        v[f"git{i}"] = SERIES[i]
        v[f"gitBranchLabel{i}"] = TEXT
        v[f"gitInv{i}"] = TEXT
    return v


THEME_VARIABLES: dict[str, str] = _vars()


def init_directive() -> str:
    """Diretiva %%{init}%% canônica (uma linha, determinística)."""
    tv = {k: (False if v == "false" else v) for k, v in THEME_VARIABLES.items()}
    payload = {"theme": "base", "themeVariables": tv}
    return (
        "%%{init: "
        + json.dumps(payload, separators=(",", ":")).replace('"', "'")
        + "}%%"
    )


def prompt_snippet() -> str:
    """Trecho curto para o prompt: o LLM NÃO precisa copiar a diretiva (o pós-processador injeta)."""
    return (
        f"fundo {BG}, texto {TEXT}, nós {SURFACE}, linhas/bordas {LINE}, notas {NOTE_BG}. "
        "NÃO escreva %%{init}%%, frontmatter config, theme dark/forest nem cores escuras em style/classDef/linkStyle; "
        "o tema claro de alto contraste é aplicado automaticamente"
    )


# ---------- contraste (WCAG 2.x) ----------
_NAMED = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#008000",
    "blue": "#0000FF",
    "gray": "#808080",
    "grey": "#808080",
    "navy": "#000080",
    "yellow": "#FFFF00",
    "orange": "#FFA500",
    "purple": "#800080",
    "darkblue": "#00008B",
    "darkgreen": "#006400",
    "darkred": "#8B0000",
    "lightgray": "#D3D3D3",
    "silver": "#C0C0C0",
    "maroon": "#800000",
    "teal": "#008080",
}


def parse_color(value: str) -> tuple[int, int, int] | None:
    s = value.strip().lower()
    s = _NAMED.get(s, s).lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", s)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        if len(h) >= 6:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return None
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+).*\)", s)
    if m:
        return tuple(min(255, int(x)) for x in m.groups())  # type: ignore[return-value]
    return None


def luminance(rgb: tuple[int, int, int]) -> float:
    def ch(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    ra, rb = parse_color(a), parse_color(b)
    if ra is None or rb is None:
        raise ValueError(f"cor inválida: {a!r} / {b!r}")
    la, lb = sorted((luminance(ra), luminance(rb)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
