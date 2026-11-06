"""Módulo de download e ingestão de vídeos e playlists do YouTube via yt-dlp.

Inspirado e compatível com a esteira do baixar-playlist.sh:
- Suporta playlists completas, vídeos avulsos ou listas de URLs.
- Download de vídeo até 1080p mesclado em MP4 ou extração direta de áudio em MP3.
- Modo listagem (--listar) sem realizar download.
- Controle de idempotência via arquivo de archive (baixados-<id>.txt).
- Detecção e uso de cookies.txt para conteúdo restrito ou com login.
- Organização automática por Canal/Playlist ou modo plano para pastas de módulo.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console

console = Console()

PLAYLIST_RE = re.compile(r"[\?\&]list=([A-Za-z0-9_-]+)")
CHANNEL_TMPL = (
    "%(playlist_channel,playlist_uploader,channel,uploader|Canal Desconhecido)s"
)


def resolve_ytdlp_cmd() -> list[str]:
    """Retorna o comando base para executar o yt-dlp.

    Prioriza o módulo yt_dlp no ambiente Python ativo, caindo para o binário yt-dlp no PATH.
    """
    try:
        res = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
    except (OSError, subprocess.SubprocessError):
        pass

    bin_path = shutil.which("yt-dlp")
    if bin_path:
        return [bin_path]

    raise RuntimeError(
        "yt-dlp não foi encontrado. Instale via 'uv add yt-dlp' ou garanta que 'yt-dlp' esteja no PATH."
    )


def parse_playlist_id(url: str) -> str | None:
    """Extrai o ID da playlist a partir da URL, se presente."""
    m = PLAYLIST_RE.search(url)
    return m.group(1) if m else None


def get_archive_filename(url: str, base_dir: Path) -> Path:
    """Gera o caminho canônico do arquivo de controle (archive) para evitar re-downloads."""
    list_id = parse_playlist_id(url)
    if list_id:
        filename = f"baixados-{list_id}.txt"
    else:
        url_hash = hashlib.md5(url.strip().encode("utf-8")).hexdigest()[:8]
        filename = f"baixados-{url_hash}.txt"
    return base_dir / filename


def get_output_template(
    url: str, output_dir: Path, audio: bool = False, flat: bool = False
) -> str:
    """Constrói o template de saída do yt-dlp."""
    ext = "mp3" if audio else "%(ext)s"
    out_str = str(output_dir).replace("\\", "/")

    if flat:
        return f"{out_str}/%(playlist_index&{{:02d}} - |)s%(title)s.{ext}"

    is_playlist = bool(parse_playlist_id(url))
    if is_playlist:
        if audio:
            return f"{out_str}/{CHANNEL_TMPL}/%(playlist_title)s - Áudio/%(playlist_index)02d - %(title)s.{ext}"
        return f"{out_str}/{CHANNEL_TMPL}/%(playlist_title)s/%(playlist_index)02d - %(title)s.{ext}"

    if audio:
        return f"{out_str}/{CHANNEL_TMPL}/Áudio/%(title)s.{ext}"
    return f"{out_str}/{CHANNEL_TMPL}/%(title)s.{ext}"


def find_cookies_file(cookies_path: Path | str | None = None) -> Path | None:
    """Localiza o arquivo de cookies (opcional)."""
    candidates: list[Path] = []
    if cookies_path:
        candidates.append(Path(cookies_path))

    env_cookies = os.environ.get("COOKIES_FILE")
    if env_cookies:
        candidates.append(Path(env_cookies))

    candidates.append(Path("cookies.txt"))
    candidates.append(Path.cwd() / "cookies.txt")

    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def download_youtube(
    url: str,
    output_dir: Path | str | None = None,
    audio_only: bool = False,
    list_only: bool = False,
    cookies_file: Path | str | None = None,
    max_height: int = 1080,
    flat_output: bool = False,
    archive_file: Path | str | None = None,
    extra_args: Sequence[str] | None = None,
    quiet: bool = False,
) -> int:
    """Executa o download ou listagem de vídeos/playlists do YouTube via yt-dlp."""
    if not url or not url.strip():
        raise ValueError("URL do YouTube não pode ser vazia.")

    url = url.strip()
    out_path = Path(output_dir) if output_dir else Path("download")
    out_path.mkdir(parents=True, exist_ok=True)

    cmd = resolve_ytdlp_cmd()

    # Configura cliente android/ios/web para contornar bloqueio n-sig / bot do YouTube
    cmd.extend(["--extractor-args", "youtube:player_client=android,ios,web"])

    # 1. Modo Listagem
    if list_only:
        if not quiet:
            console.print(
                f"[cyan]📋 Listando conteúdo da URL:[/cyan] [bold]{url}[/bold]"
            )
        cmd.extend(
            [
                "--flat-playlist",
                "--print",
                "%(playlist_index&{:02d} - |)s%(title)s [%(playlist_channel,playlist_uploader,channel,uploader|Canal Desconhecido)s]",
            ]
        )
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        res = subprocess.run(cmd, check=False)
        return res.returncode

    # 2. Configura cookies
    cookie_found = find_cookies_file(cookies_file)
    if cookie_found:
        if not quiet:
            console.print(
                f"[green]🍪 Usando cookies autenticados:[/green] [dim]{cookie_found.name}[/dim]"
            )
        cmd.extend(["--cookies", str(cookie_found)])
    elif not quiet and cookies_file:
        console.print(
            f"[yellow]⚠️ Arquivo de cookies não encontrado: {cookies_file} (baixando conteúdo público)[/yellow]"
        )

    # 3. Arquivo de controle (archive)
    archive_path = (
        Path(archive_file) if archive_file else get_archive_filename(url, out_path)
    )

    # 4. Template de saída
    out_template = get_output_template(
        url, out_path, audio=audio_only, flat=flat_output
    )

    if not quiet:
        mode_str = "Áudio (MP3)" if audio_only else f"Vídeo MP4 (até {max_height}p)"
        console.print(f"[cyan]🎬 Baixando do YouTube:[/cyan] [bold]{url}[/bold]")
        console.print(
            f"[dim]📁 Destino: {out_path} | Modo: {mode_str} | Archive: {archive_path.name}[/dim]"
        )

    if audio_only:
        cmd.extend(
            [
                "-x",
                "--audio-format",
                "mp3",
                "--embed-metadata",
                "-o",
                out_template,
                "--download-archive",
                str(archive_path),
                "--ignore-errors",
            ]
        )
    else:
        format_spec = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
        cmd.extend(
            [
                "-f",
                format_spec,
                "--merge-output-format",
                "mp4",
                "--embed-metadata",
                "-o",
                out_template,
                "--download-archive",
                str(archive_path),
                "--ignore-errors",
            ]
        )

    if extra_args:
        cmd.extend(extra_args)

    cmd.append(url)

    res = subprocess.run(cmd, check=False)
    if res.returncode == 0 and not quiet:
        console.print("[bold green]✓ Download concluído com sucesso![/bold green]")
    elif res.returncode != 0 and not quiet:
        console.print(
            f"[bold red]❌ yt-dlp finalizou com código de saída {res.returncode}[/bold red]"
        )

    return res.returncode
