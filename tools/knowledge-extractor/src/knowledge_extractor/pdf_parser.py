"""Módulo de extração de texto e estruturação a partir de arquivos PDF."""

from __future__ import annotations

import sys
from pathlib import Path
from pypdf import PdfReader
from rich.console import Console

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


def extract_text_from_pdf(
    pdf_path: Path,
    output_dir: Path | None = None,
    as_markdown: bool = True,
) -> Path:
    """Extrai o texto de um arquivo PDF e grava um arquivo .md ou .txt correspondente."""
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Arquivo PDF não encontrado: {pdf_path}")

    if output_dir is None:
        output_dir = pdf_path.parent
    else:
        output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    extension = ".md" if as_markdown else ".txt"
    output_file = output_dir / f"{pdf_path.stem}{extension}"

    console.print(f"[yellow][...] Extraindo texto de PDF:[/yellow] {pdf_path.name}...")
    reader = PdfReader(str(pdf_path))
    num_pages = len(reader.pages)

    lines: list[str] = []
    if as_markdown:
        lines.append(f"# Extração de Conteúdo: {pdf_path.stem}")
        lines.append(f"\n*Total de Páginas: {num_pages}*\n")
        lines.append("---\n")

    for idx, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if as_markdown:
            lines.append(f"## Página {idx}\n")
            lines.append(page_text.strip())
            lines.append("\n---\n")
        else:
            lines.append(f"--- [PÁGINA {idx}] ---")
            lines.append(page_text.strip())
            lines.append("")

    output_file.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[bold green][OK] Conteúdo do PDF salvo em:[/bold green] [blue]{output_file}[/blue]")
    return output_file
