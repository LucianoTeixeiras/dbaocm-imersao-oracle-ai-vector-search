"""Extração de PDF para Markdown por página, com figuras e metadados (LRT-06)."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from rich.console import Console

from knowledge_extractor.errors import PROGRAMMING_ERRORS, describe

if sys.platform == "win32":
    # Console legado sem reconfigure (ou stream redirecionado): segue com o encoding padrão.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

console = Console(highlight=False)
log = logging.getLogger(__name__)

#: Máximo de imagens do PDF salvas e enviadas como frames multimodais (env PDF_MAX_IMAGES).
PDF_MAX_IMAGES_DEFAULT = 8
#: Página com menos que isto de texto conta como "sem texto" (provável digitalização).
MIN_PAGE_CHARS = 20
#: Imagens menores que isto (bytes) são ícones/ornamentos e são descartadas.
MIN_IMAGE_BYTES = 2048
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".jp2"}


@dataclass
class PdfExtraction:
    output_file: Path
    pages: int
    pages_without_text: list[int] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)
    language: str | None = None

    @property
    def scanned(self) -> bool:
        return self.pages > 0 and len(self.pages_without_text) == self.pages


def pdf_max_images() -> int:
    try:
        return max(0, int(os.getenv("PDF_MAX_IMAGES", PDF_MAX_IMAGES_DEFAULT)))
    except ValueError:
        return PDF_MAX_IMAGES_DEFAULT


def _default_figures_dir(output_dir: Path, pdf_path: Path) -> Path | None:
    """transcricoes/ -> ../figuras/<slug-da-nota>/ (padrão do repositório). Fora disso, não salva imagens."""
    if output_dir.name != "transcricoes":
        return None
    from knowledge_extractor.note_naming import note_filename_for

    return output_dir.parent / "figuras" / Path(note_filename_for(pdf_path)).stem


def _save_page_images(page, page_no: int, dest: Path, budget: int) -> list[Path]:
    saved: list[Path] = []
    if budget <= 0:
        return saved
    try:
        images = list(page.images)
    except ImportError:
        console.print(
            "[yellow]⚠ Pillow não encontrado: figuras do PDF não serão extraídas. "
            "Rode `uv sync` em tools/knowledge-extractor.[/yellow]"
        )
        return saved
    except Exception as e:  # pypdf/Pillow lançam tipos variados em PDF corrompido; pula só as figuras da página
        if isinstance(e, PROGRAMMING_ERRORS):
            raise
        log.warning("Imagens da página %s não extraídas (%s)", page_no, describe(e))
        console.print(
            f"[dim]Aviso: imagens da página {page_no} não extraídas ({type(e).__name__}: {e}).[/dim]"
        )
        return saved
    for n, img in enumerate(images, start=1):
        if len(saved) >= budget:
            break
        data = getattr(img, "data", b"") or b""
        if len(data) < MIN_IMAGE_BYTES:
            continue
        ext = Path(getattr(img, "name", "") or "").suffix.lower()
        ext = ext if ext in _IMG_EXT else ".png"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"p{page_no:03d}-{n:02d}{ext}"
        out.write_bytes(data)
        saved.append(out)
    return saved


def extract_text_from_pdf(
    pdf_path: Path,
    output_dir: Path | None = None,
    as_markdown: bool = True,
    figures_dir: Path | None = None,
    max_images: int | None = None,
) -> Path:
    """Extrai o texto de um PDF para .md (um `## Página N` por página) ou .txt.

    Também grava `<stem>.meta.json` (source_kind=pdf, idioma detectado offline, páginas sem texto
    e figuras salvas) e avisa quando o PDF parece escaneado (OCR: backlog B-13).
    """
    return extract_pdf(
        pdf_path, output_dir, as_markdown, figures_dir, max_images
    ).output_file


def extract_pdf(
    pdf_path: Path,
    output_dir: Path | None = None,
    as_markdown: bool = True,
    figures_dir: Path | None = None,
    max_images: int | None = None,
) -> PdfExtraction:
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Arquivo PDF não encontrado: {pdf_path}")
    output_dir = pdf_path.parent if output_dir is None else Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{pdf_path.stem}{'.md' if as_markdown else '.txt'}"
    if figures_dir is None:
        figures_dir = _default_figures_dir(output_dir, pdf_path)
    budget = pdf_max_images() if max_images is None else max_images

    console.print(f"[yellow][...] Extraindo texto de PDF:[/yellow] {pdf_path.name}...")
    reader = PdfReader(str(pdf_path))
    res = PdfExtraction(output_file=output_file, pages=len(reader.pages))

    lines: list[str] = []
    if as_markdown:
        lines += [
            f"# Extração de Conteúdo: {pdf_path.stem}",
            f"\n*Total de Páginas: {res.pages}*\n",
            "---\n",
        ]
    all_text: list[str] = []
    for idx, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if len(page_text) < MIN_PAGE_CHARS:
            res.pages_without_text.append(idx)
        all_text.append(page_text)
        if figures_dir is not None:
            res.images += _save_page_images(
                page, idx, figures_dir, budget - len(res.images)
            )
        if as_markdown:
            lines += [f"## Página {idx}\n", page_text, "\n---\n"]
        else:
            lines += [f"--- [PÁGINA {idx}] ---", page_text, ""]
    output_file.write_text("\n".join(lines), encoding="utf-8")

    from knowledge_extractor.language import detect_text_language, write_meta

    res.language, prob = detect_text_language("\n".join(all_text))
    write_meta(
        output_file,
        {
            "source": pdf_path.name,
            "source_kind": "pdf",
            "language": res.language,
            "language_probability": prob,
            "language_mode": "offline-heuristic",
            "pages": res.pages,
            "pages_without_text": res.pages_without_text,
            "scanned": res.scanned,
            "images": [
                _rel(p, output_dir) for p in res.images
            ],  # relativo a transcricoes/
            "output_language": "pt-BR",
        },
    )
    if res.scanned:
        console.print(
            f"[bold yellow]⚠ {pdf_path.name} parece escaneado (nenhuma página com texto). "
            "A nota não será gerada; OCR está no backlog (B-13).[/bold yellow]"
        )
    elif res.pages_without_text:
        console.print(
            f"[yellow]⚠ Páginas sem texto extraível (imagem?): {res.pages_without_text}[/yellow]"
        )
    console.print(
        f"[bold green][OK] Conteúdo do PDF salvo em:[/bold green] [blue]{output_file}[/blue] · {len(res.images)} figura(s)"
    )
    return res


def _rel(p: Path, root: Path) -> str:
    return Path(os.path.relpath(p.resolve(), root.resolve())).as_posix()


_PAGE_RE = re.compile(r"^## Página (\d+)\s*$", re.MULTILINE)


def split_pdf_markdown(markdown: str, max_chars: int) -> list[tuple[int, int, str]]:
    """Divide o .md de PDF em partes de até ~max_chars, sem cortar páginas. Retorna (pág_ini, pág_fim, texto)."""
    marks = list(_PAGE_RE.finditer(markdown))
    if not marks or len(markdown) <= max_chars:
        last = int(marks[-1].group(1)) if marks else 1
        return [(int(marks[0].group(1)) if marks else 1, last, markdown)]
    pages = [
        (
            int(m.group(1)),
            markdown[
                m.start() : marks[i + 1].start()
                if i + 1 < len(marks)
                else len(markdown)
            ],
        )
        for i, m in enumerate(marks)
    ]
    parts: list[tuple[int, int, str]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for no, txt in pages:
        if cur and size + len(txt) > max_chars:
            parts.append((cur[0][0], cur[-1][0], "".join(t for _, t in cur)))
            cur, size = [], 0
        cur.append((no, txt))
        size += len(txt)
    if cur:
        parts.append((cur[0][0], cur[-1][0], "".join(t for _, t in cur)))
    return parts


def page_of_image(path: Path) -> int | None:
    m = re.match(r"p(\d+)-", Path(path).name)
    return int(m.group(1)) if m else None


def is_pdf_transcript(path: Path) -> bool:
    """True para o .md extraído de PDF (tem .meta.json com source_kind=pdf)."""
    import json

    from knowledge_extractor.language import meta_path_for

    path = Path(path)
    if path.suffix.lower() != ".md":
        return False
    mp = meta_path_for(path)
    try:
        return (
            mp.exists()
            and json.loads(mp.read_text(encoding="utf-8")).get("source_kind") == "pdf"
        )
    except (OSError, ValueError):
        return False


def synthesis_sources(transcricoes_dir: Path) -> list[Path]:
    """Fontes da etapa 3: transcrições .txt e .md extraídos de PDF."""
    if not transcricoes_dir.is_dir():
        return []
    return sorted(
        (
            f
            for f in transcricoes_dir.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and (f.suffix.lower() == ".txt" or is_pdf_transcript(f))
        ),
        key=lambda p: p.name,
    )
