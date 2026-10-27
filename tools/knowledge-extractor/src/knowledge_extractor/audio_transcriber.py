"""Módulo de extração de áudio e transcrição usando faster-whisper."""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Generator

from faster_whisper import WhisperModel
from rich.console import Console

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


def format_timestamp(seconds: float) -> str:
    """Formata segundos em formato de timestamp SRT (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def extract_audio_from_video(video_path: Path, output_audio_path: Path) -> Path:
    """Extrai a faixa de áudio em WAV/16kHz mono de um arquivo de vídeo usando ffmpeg."""
    console.print(f"[cyan][INFO] Extraindo áudio de:[/cyan] {video_path.name}")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_audio_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"Erro no ffmpeg ao extrair áudio: {result.stderr}")
    return output_audio_path


class Transcriber:
    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "default",
        language: str = "pt",
    ) -> None:
        self.model_size = model_size
        self.language = language

        # Configura device
        if device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        if compute_type == "default":
            compute_type = "float16" if device == "cuda" else "int8"

        cpu_threads = os.cpu_count() or 4
        console.print(f"[bold green][OK] Inicializando WhisperModel ({model_size}) no device '{device}' ({cpu_threads} threads) com compute_type '{compute_type}'...[/bold green]")
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=cpu_threads)

    def transcribe_file(
        self,
        input_file: Path,
        output_dir: Path | None = None,
    ) -> tuple[Path, Path]:
        """Transcreve arquivo de vídeo ou áudio e grava .txt e .srt no diretório de saída."""
        input_file = Path(input_file).resolve()
        if not input_file.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {input_file}")

        if output_dir is None:
            output_dir = input_file.parent
        else:
            output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        stem = input_file.stem
        txt_path = output_dir / f"{stem}.txt"
        srt_path = output_dir / f"{stem}.srt"

        temp_audio_needed = input_file.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv"}

        with tempfile.TemporaryDirectory() as tmpdir:
            if temp_audio_needed:
                audio_target = Path(tmpdir) / f"{stem}.wav"
                extract_audio_from_video(input_file, audio_target)
                target_to_transcribe = audio_target
            else:
                target_to_transcribe = input_file

            console.print(f"[yellow][...] Transcrevendo:[/yellow] {input_file.name} (Idioma: {self.language})...")
            segments, info = self.model.transcribe(
                str(target_to_transcribe),
                language=self.language,
                beam_size=5,
                vad_filter=True,
            )

            console.print(f"[cyan][INFO] Duração detectada:[/cyan] {info.duration:.1f}s | Probabilidade de {self.language}: {info.language_probability:.2f}")

            txt_lines = []
            srt_lines = []
            segment_idx = 1

            last_reported = -30.0
            for segment in segments:
                start_str = format_timestamp(segment.start)
                end_str = format_timestamp(segment.end)
                text = segment.text.strip()

                txt_lines.append(text)

                srt_lines.append(f"{segment_idx}")
                srt_lines.append(f"{start_str} --> {end_str}")
                srt_lines.append(f"{text}\n")
                segment_idx += 1

                if segment.start - last_reported >= 15.0:
                    last_reported = segment.start
                    console.print(f"  [cyan]>[/cyan] [{start_str} / {format_timestamp(info.duration)}] {text[:70]}...")

            txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
            srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

        console.print(f"[bold green][OK] Transcrição salva:[/bold green]\n  - [blue]{txt_path}[/blue]\n  - [blue]{srt_path}[/blue]")
        return txt_path, srt_path
