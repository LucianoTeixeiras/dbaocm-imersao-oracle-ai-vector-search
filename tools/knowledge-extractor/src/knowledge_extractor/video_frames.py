"""Módulo de extração inteligente de frames e diagramas de vídeos usando ffmpeg.

Estratégia híbrida de seleção (em uma única passada de decode):
1. Mudança de cena (scene > threshold, com cooldown de 4s): captura transições de slides/telas.
2. Piso temporal (1 frame a cada min_interval_sec): garante cobertura em live-coding,
   onde a tela muda incrementalmente sem corte brusco de cena.
3. Deduplicação perceptual (dHash) pós-extração: remove frames quase idênticos.
4. Ancoragem temporal: cada frame final recebe o timestamp real no nome (frame_0001_05m47s.jpg),
   permitindo ao modelo correlacionar tela x fala.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from rich.console import Console

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
_PTS_TIME_RE = re.compile(r"pts_time:([0-9.]+)")
_TIMESTAMP_NAME_RE = re.compile(r"_(\d{2,4})m(\d{2})s")


def is_ffmpeg_available() -> bool:
    """Verifica se o binário ffmpeg está disponível no PATH."""
    return shutil.which("ffmpeg") is not None


def get_media_duration(file_path: Path) -> float:
    """Obtém a duração total da mídia em segundos via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def format_frame_timestamp(seconds: float) -> str:
    """Formata segundos como MMmSSs (ex: 332.2 -> '05m32s' | 32951.0 -> '549m11s')."""
    total = max(0, int(round(seconds)))
    mins, secs = divmod(total, 60)
    return f"{mins:02d}m{secs:02d}s"


def extract_timestamp_label(frame_path: Path) -> str | None:
    """Extrai 'MM:SS' ou 'HH:MM:SS' do nome do frame."""
    m = _TIMESTAMP_NAME_RE.search(frame_path.stem)
    if m:
        mins = int(m.group(1))
        secs = m.group(2)
        if mins >= 60:
            h, rem_m = divmod(mins, 60)
            return f"{h:02d}:{rem_m:02d}:{secs}"
        return f"{mins:02d}:{secs}"
    return None


def _compute_dhash(image_path: Path):
    """dHash 16x16 (256 bits) via PyAV + numpy (presentes no venv via faster-whisper)."""
    try:
        import av

        with av.open(str(image_path)) as container:
            frame = next(container.decode(video=0))
            small = frame.reformat(width=17, height=16, format="gray").to_ndarray()
            return (small[:, 1:] > small[:, :-1]).flatten()
    except Exception:
        return None


def _hamming(a, b) -> int:
    """Distância de Hamming entre dois dHashes."""
    return int((a != b).sum())


def dedupe_and_anchor(
    raw_frames: list[Path],
    pts_times: list[float],
    max_frames: int,
    hamming_threshold: int = 28,
) -> list[tuple[Path, float]]:
    """Filtra duplicatas perceptuais consecutivas e retorna pares (path, timestamp).

    Função pura sobre lista de arquivos — testável sem ffmpeg.
    """
    if not raw_frames:
        return []

    kept: list[tuple[Path, float]] = []
    last_hash = None

    for idx, src in enumerate(raw_frames):
        if not src.exists():
            continue
        cur_hash = _compute_dhash(src)
        if cur_hash is not None and last_hash is not None:
            if _hamming(cur_hash, last_hash) < hamming_threshold:
                continue
        ts = pts_times[idx] if idx < len(pts_times) else (kept[-1][1] + 60 if kept else float(idx * 60))
        kept.append((src, ts))
        if cur_hash is not None:
            last_hash = cur_hash

    if len(kept) > max_frames:
        import numpy as np

        indices = np.round(np.linspace(0, len(kept) - 1, max_frames)).astype(int)
        kept = [kept[i] for i in sorted(set(indices.tolist()))]

    return kept


def extract_video_keyframes(
    video_path: Path,
    output_dir: Path,
    max_frames: int = 25,
    scene_threshold: float = 0.22,
    min_interval_sec: int = 180,
    force: bool = False,
) -> list[Path]:
    """Extrai keyframes híbridos (cena + piso temporal) com ancoragem [MM:SS]."""
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        console.print(f"[yellow]⚠️ Vídeo não encontrado:[/yellow] {video_path}")
        return []

    if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return []

    if not is_ffmpeg_available():
        console.print("[yellow]⚠️ ffmpeg não encontrado no PATH. Extração de frames ignorada.[/yellow]")
        return []

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_frames = sorted(output_dir.glob("frame_*.jpg"))
    timestamped = [f for f in existing_frames if _TIMESTAMP_NAME_RE.search(f.stem)]
    legacy = [f for f in existing_frames if not _TIMESTAMP_NAME_RE.search(f.stem)]

    if timestamped and not force:
        console.print(
            f"[dim]Frames visuais em cache ({len(timestamped)} com ancoragem temporal em {output_dir.name}).[/dim]"
        )
        return timestamped[:max_frames]

    for old in legacy + (timestamped if force else []):
        try:
            old.unlink()
        except Exception:
            pass

    # Ajusta o piso temporal proporcionalmente para vídeos longos (> 1h),
    # garantindo que os frames cubram a íntegra da gravação (sem esgotar o cap nas primeiras horas).
    duration = get_media_duration(video_path)
    if duration > 3600.0:
        proportional_interval = int(duration / (max_frames * 1.5))
        if proportional_interval > min_interval_sec:
            console.print(
                f"[dim]ℹ Vídeo longo ({format_frame_timestamp(duration)}): ajustando piso temporal de {min_interval_sec}s para {proportional_interval}s (~{proportional_interval//60} min).[/dim]"
            )
            min_interval_sec = proportional_interval

    console.print(
        f"[cyan]🎬 Extraindo keyframes híbridos (cena>{scene_threshold} + piso {min_interval_sec}s):[/cyan] {video_path.name}"
    )

    raw_pattern = str(output_dir / "_raw_%04d.jpg")
    raw_cap = max_frames * 3 if duration > 3600.0 else max_frames * 2

    select_expr = (
        f"gt(t,2)*(isnan(prev_selected_t)"
        f"+gt(scene,{scene_threshold})*gte(t-prev_selected_t,4)"
        f"+gte(t-prev_selected_t,{min_interval_sec}))"
    )
    vf_chain = f"select='{select_expr}',scale='min(1280,iw)':-2,showinfo"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf_chain,
        "-fps_mode",
        "vfr",
        "-q:v",
        "3",
        "-frames:v",
        str(raw_cap),
        raw_pattern,
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            cmd_legacy = [("-vsync" if c == "-fps_mode" else c) for c in cmd]
            result = subprocess.run(
                cmd_legacy,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-1] if (result.stderr or "").strip() else "sem detalhes"
            console.print(f"[yellow]⚠️ ffmpeg falhou na extração de frames: {tail}[/yellow]")
            return []
        pts_times = [float(m.group(1)) for m in _PTS_TIME_RE.finditer(result.stderr or "")]
    except Exception as e:
        console.print(f"[yellow]⚠️ Erro ao executar ffmpeg para extração de frames: {e}[/yellow]")
        return []

    raw_frames = sorted(output_dir.glob("_raw_*.jpg"))
    if len(raw_frames) >= raw_cap:
        console.print("[dim]Teto bruto atingido — trecho final do vídeo pode ficar sub-representado.[/dim]")

    kept = dedupe_and_anchor(raw_frames, pts_times, max_frames=max_frames)

    final: list[Path] = []
    for i, (src, ts) in enumerate(kept, start=1):
        dst = output_dir / f"frame_{i:04d}_{format_frame_timestamp(ts)}.jpg"
        try:
            if dst.exists():
                dst.unlink()
            src.rename(dst)
            final.append(dst)
        except Exception:
            final.append(src)

    for leftover in output_dir.glob("_raw_*.jpg"):
        try:
            leftover.unlink()
        except Exception:
            pass

    console.print(
        f"[bold green]✓ {len(final)} keyframes distintos prontos com ancoragem [MM:SS].[/bold green]"
    )
    return final
