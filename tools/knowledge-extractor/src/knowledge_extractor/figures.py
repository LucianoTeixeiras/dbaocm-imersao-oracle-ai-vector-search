"""Seleção, otimização e anexação de figuras-chave extraídas dos vídeos.

Fluxo:
1. O VLM recebe os frames já extraídos e escolhe apenas os 2-3 com real valor didático visual
   (diagramas, arquiteturas desenhadas, quadros brancos, Miro/Excalidraw).
2. Cada frame escolhido é otimizado para .webp (leve) e salvo em materiais/figuras/ (versionada).
3. A figura é anexada à nota em uma seção própria, de forma idempotente (marcadores HTML).

Frames de código/terminal NÃO viram imagem: são melhores como blocos de código no Markdown.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from rich.console import Console

from knowledge_extractor.video_frames import extract_timestamp_label

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)

FIGURES_DIRNAME = "figuras"
MARKER_START = "<!-- figuras:auto:start -->"
MARKER_END = "<!-- figuras:auto:end -->"
MAX_FIGURES_DEFAULT = 3
SUMMARY_HEADING_RE = re.compile(r"^##\s+.*Resumo Executivo", re.MULTILINE)
FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-2.0-flash")


def build_candidate_models(primary: str) -> list[str]:
    """Cadeia de modelos de contingência para a seleção de figuras (cada um tem cota própria)."""
    chain = [primary]
    for fb in FALLBACK_MODELS:
        if fb not in chain:
            chain.append(fb)
    return chain


def _is_transient(exc: Exception) -> bool:
    txt = f"{type(exc).__name__}: {exc}".upper()
    marks = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "502", "504", "DEADLINE", "TIMEOUT", "OVERLOADED", "QUOTA")
    return any(m in txt for m in marks)

SELECTION_PROMPT = """Você analisa capturas de tela de uma aula técnica gravada e escolhe QUAIS delas merecem virar figura permanente na apostila.

Critérios de ALTO valor visual (escolha apenas estas):
- Diagramas, fluxos, mapas mentais ou arquiteturas desenhadas (Miro, Excalidraw, quadro branco, slide com diagrama).
- Slides com matrizes/camadas/quadrantes que só fazem sentido visualmente.
- Telas de ferramentas cujo layout é o próprio conteúdo (ex: painel de observabilidade, grafo de agentes).

NÃO escolha:
- Slide apenas com texto corrido (isso já está na nota como texto/tabela).
- Código, YAML, terminal ou IDE (isso já está na nota como bloco de código).
- Interface do portal do curso, player de vídeo, popups, webcam do professor.

Responda SOMENTE um JSON válido no formato:
{{"figuras": [{{"frame": 6, "slug": "fluxo-worktrees", "legenda": "Fluxo de execução com Git Worktrees"}}]}}

Regras: no máximo {max_figures} figuras; "frame" é o número do Frame informado ([Frame N]); "slug" em kebab-case ascii curto; "legenda" em português com até 90 caracteres. Se nenhum frame tiver valor visual real, responda {{"figuras": []}}."""


def sanitize_slug(text: str, max_len: int = 40) -> str:
    """Converte texto livre em slug kebab-case ascii seguro para nome de arquivo."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    ascii_text = re.sub(r"[^\w\s-]", "", ascii_text).strip().lower()
    slug = re.sub(r"[-\s_]+", "-", ascii_text).strip("-")
    return slug[:max_len].strip("-") or "figura"


def optimize_to_webp(src: Path, dst: Path, max_width: int = 1280, quality: int = 78) -> bool:
    """Converte um frame JPEG em WebP otimizado (~30-50 KB) usando ffmpeg/libwebp."""
    if not src.exists():
        return False
    if not shutil.which("ffmpeg"):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale='min({max_width},iw)':-2",
        "-c:v",
        "libwebp",
        "-quality",
        str(quality),
        "-compression_level",
        "6",
        str(dst),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return res.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def select_figures(client: Any, model: str, keyframes: list[Path], lesson_title: str, max_figures: int = MAX_FIGURES_DEFAULT) -> list[dict]:
    """Pergunta ao VLM quais frames têm valor visual real. Retorna lista de dicts."""
    if not client or not keyframes:
        return []

    from google.genai import types

    parts: list[Any] = [
        types.Part.from_text(
            text=f"Aula: {lesson_title}\n\n{SELECTION_PROMPT.format(max_figures=max_figures)}"
        )
    ]
    for i, kf in enumerate(keyframes, start=1):
        if not kf.exists():
            continue
        label = extract_timestamp_label(kf)
        parts.append(types.Part.from_text(text=f"[Frame {i:02d}] {label or ''}".strip()))
        try:
            parts.append(types.Part.from_bytes(data=kf.read_bytes(), mime_type="image/jpeg"))
        except Exception:
            continue

    try:
        response = None
        last_exc: Exception | None = None
        tried: list[str] = []
        for model_name in build_candidate_models(model):
            tried.append(model_name)
            for attempt in range(1, 3):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[types.Content(role="user", parts=parts)],
                        config=types.GenerateContentConfig(response_mime_type="application/json"),
                    )
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if _is_transient(exc):
                        # Sobrecarga/cota: vale insistir no mesmo modelo
                        if attempt < 2:
                            time.sleep(4 * attempt)
                        continue
                    # Erro definitivo do modelo (404 modelo descontinuado, 400 inválido):
                    # não insiste neste modelo, passa para o próximo da cadeia.
                    console.print(f"[dim]ℹ Modelo {model_name} indisponível ({type(exc).__name__}) — tentando contingência.[/dim]")
                    break
            if response is not None:
                if model_name != model:
                    console.print(f"[dim]ℹ Seleção de figuras via modelo de contingência ({model_name}).[/dim]")
                break
        if response is None and last_exc is not None:
            console.print(f"[yellow]⚠️ Nenhum modelo de visão disponível agora ({', '.join(tried)}). Detalhe: {str(last_exc)[:160]}[/yellow]")
            return []
        raw = (response.text or "").strip()
        data = json.loads(raw)
        figs = data.get("figuras", []) if isinstance(data, dict) else []
    except Exception as e:
        console.print(f"[yellow]⚠️ Falha ao selecionar figuras: {e}[/yellow]")
        return []

    valid: list[dict] = []
    for item in figs:
        try:
            idx = int(item.get("frame"))
        except (TypeError, ValueError):
            continue
        if not 1 <= idx <= len(keyframes):
            continue
        valid.append(
            {
                "index": idx,
                "path": keyframes[idx - 1],
                "slug": sanitize_slug(str(item.get("slug") or f"figura-{idx}")),
                "legenda": str(item.get("legenda") or "").strip()[:120],
            }
        )
    return valid[:max_figures]


def build_figures_block(lesson_stem: str, figures: list[dict]) -> str:
    """Monta o bloco Markdown das figuras (com marcadores idempotentes)."""
    if not figures:
        return ""
    lines = [MARKER_START, "", "## 🖼️ Figuras de Referência da Aula", ""]
    for n, fig in enumerate(figures, start=1):
        rel = f"../materiais/{FIGURES_DIRNAME}/{lesson_stem}--{fig['slug']}.webp"
        ts = extract_timestamp_label(fig["path"])
        caption = fig["legenda"] or f"Figura {n}"
        suffix = f" (tela aos {ts})" if ts else ""
        lines.append(f"![{caption}]({rel})")
        lines.append("")
        lines.append(f"*Figura {n} — {caption}{suffix}*")
        lines.append("")
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def attach_figures(note_path: Path, lesson_stem: str, figures: list[dict]) -> bool:
    """Insere (ou substitui) o bloco de figuras na nota, antes do Resumo Executivo."""
    block = build_figures_block(lesson_stem, figures)
    if not block.strip():
        return False

    text = note_path.read_text(encoding="utf-8")

    # Remove bloco anterior, se existir (idempotência)
    clean = re.sub(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?",
        "",
        text,
        flags=re.DOTALL,
    )

    match = SUMMARY_HEADING_RE.search(clean)
    if match:
        new_text = clean[: match.start()] + block + "\n" + clean[match.start() :]
    else:
        new_text = clean.rstrip() + "\n\n" + block

    note_path.write_text(new_text, encoding="utf-8")
    return True
