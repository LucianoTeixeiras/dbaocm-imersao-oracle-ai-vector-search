"""Módulo de fatiamento rápido de vídeos longos usando FFmpeg Stream Copy (-c copy).

Fatia vídeos grandes (ex: 2h a 9h+) em blocos configuráveis de 30 a 35 minutos
sem re-encoding (execução quase instantânea), mantendo qualidade original e
arquivando o vídeo bruto em materiais/raw/archive/ (gitignored).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from knowledge_extractor.video_frames import (
    VIDEO_EXTENSIONS,
    get_media_duration,
    is_ffmpeg_available,
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)

DEFAULT_CHUNK_MINUTES = 35


def format_seconds_to_hhmmss(seconds: float) -> str:
    """Formata segundos para HH:MM:SS."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def slice_video_file(
    video_path: Path,
    chunk_minutes: int = DEFAULT_CHUNK_MINUTES,
    output_dir: Path | None = None,
    archive_original: bool = True,
) -> list[Path]:
    """Fatia um arquivo de vídeo em partes de N minutos usando stream copy rápido."""
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        console.print(f"[red]❌ Arquivo de vídeo não encontrado:[/red] {video_path}")
        return []

    if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        console.print(f"[yellow]⚠️ Extensão não suportada como vídeo:[/yellow] {video_path.suffix}")
        return []

    if not is_ffmpeg_available():
        console.print("[red]❌ FFmpeg não encontrado no PATH. Instale o ffmpeg para fatiar vídeos.[/red]")
        return []

    duration_sec = get_media_duration(video_path)
    if duration_sec <= 0:
        console.print(f"[red]❌ Não foi possível determinar a duração do vídeo:[/red] {video_path.name}")
        return []

    chunk_seconds = chunk_minutes * 60
    if duration_sec <= chunk_seconds:
        console.print(
            f"[dim]ℹ Vídeo {video_path.name} tem {duration_sec/60:.1f} min (menor que o corte de {chunk_minutes} min). Nenhum corte necessário.[/dim]"
        )
        return [video_path]

    target_dir = Path(output_dir).resolve() if output_dir else video_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # Limpa stem retirando partes anteriores para evitar acúmulo de sufixos
    clean_stem = re.sub(r"-parte-\d+$", "", video_path.stem)
    ext = video_path.suffix.lower()

    num_parts = int(duration_sec // chunk_seconds) + (1 if duration_sec % chunk_seconds > 0 else 0)
    console.print(
        f"[bold cyan]🔪 Fatiando vídeo ({duration_sec/60:.1f} min) em {num_parts} partes de até {chunk_minutes} min:[/bold cyan] {video_path.name}"
    )

    sliced_files: list[Path] = []
    current_start = 0.0
    part_idx = 1

    while current_start < duration_sec:
        current_end = min(current_start + chunk_seconds, duration_sec)
        duration_part = current_end - current_start
        out_file = target_dir / f"{clean_stem}-parte-{part_idx:02d}{ext}"

        start_str = format_seconds_to_hhmmss(current_start)
        dur_str = format_seconds_to_hhmmss(duration_part)

        console.print(
            f"  [cyan]▶ Parte {part_idx:02d}:[/cyan] {out_file.name} [dim]({start_str} -> {format_seconds_to_hhmmss(current_end)} | {duration_part/60:.1f} min)[/dim]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            start_str,
            "-i",
            str(video_path),
            "-t",
            dur_str,
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(out_file),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0 or not out_file.exists() or out_file.stat().st_size == 0:
            console.print(f"[red]❌ Erro ao fatiar parte {part_idx}: {res.stderr[-200:] if res.stderr else 'falha'}[/red]")
        else:
            sliced_files.append(out_file)

        current_start += chunk_seconds
        part_idx += 1

    if archive_original and len(sliced_files) > 1:
        archive_dir = video_path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest_archive = archive_dir / video_path.name
        try:
            if dest_archive.exists():
                dest_archive.unlink()
            video_path.rename(dest_archive)
            console.print(f"[green]✓ Vídeo bruto original arquivado em:[/green] {dest_archive.relative_to(target_dir.parent.parent) if target_dir.parent.parent in dest_archive.parents else dest_archive.name}")
        except Exception as e:
            console.print(f"[yellow]⚠️ Não foi possível mover vídeo original para archive: {e}[/yellow]")

    console.print(f"[bold green]✨ Fatiamento concluído com sucesso ({len(sliced_files)} partes geradas)![/bold green]")
    return sliced_files
