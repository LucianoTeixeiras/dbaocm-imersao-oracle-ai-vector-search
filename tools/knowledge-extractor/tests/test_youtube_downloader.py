"""Testes unitários para o módulo youtube_downloader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from knowledge_extractor import youtube_downloader as yd


def test_parse_playlist_id() -> None:
    assert (
        yd.parse_playlist_id(
            "https://www.youtube.com/playlist?list=PLYAGaVIlnsYYwZrQdUFFSb2dCpNWByG55"
        )
        == "PLYAGaVIlnsYYwZrQdUFFSb2dCpNWByG55"
    )
    assert (
        yd.parse_playlist_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123456789"
        )
        == "PL123456789"
    )
    assert yd.parse_playlist_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None
    assert yd.parse_playlist_id("") is None


def test_get_archive_filename(tmp_path: Path) -> None:
    p_url = "https://www.youtube.com/playlist?list=PL_TEST_123"
    p_archive = yd.get_archive_filename(p_url, tmp_path)
    assert p_archive.name == "baixados-PL_TEST_123.txt"
    assert p_archive.parent == tmp_path

    v_url = "https://www.youtube.com/watch?v=abc123xyz"
    v_archive = yd.get_archive_filename(v_url, tmp_path)
    assert v_archive.name.startswith("baixados-")
    assert v_archive.name.endswith(".txt")


def test_get_output_template(tmp_path: Path) -> None:
    p_url = "https://www.youtube.com/playlist?list=PL_TEST"
    v_url = "https://www.youtube.com/watch?v=123"

    # Playlist - Video
    tmpl_pv = yd.get_output_template(p_url, tmp_path, audio=False, flat=False)
    assert "%(playlist_title)s" in tmpl_pv
    assert "%(playlist_index)02d" in tmpl_pv

    # Playlist - Audio
    tmpl_pa = yd.get_output_template(p_url, tmp_path, audio=True, flat=False)
    assert "%(playlist_title)s - Áudio" in tmpl_pa
    assert tmpl_pa.endswith(".mp3")

    # Single video - Video
    tmpl_vv = yd.get_output_template(v_url, tmp_path, audio=False, flat=False)
    assert "%(title)s.%(ext)s" in tmpl_vv

    # Flat output
    tmpl_flat = yd.get_output_template(p_url, tmp_path, audio=False, flat=True)
    assert "%(playlist_index&{:02d} - |)s%(title)s.%(ext)s" in tmpl_flat


def test_find_cookies_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit file
    c_file = tmp_path / "custom_cookies.txt"
    c_file.write_text("test cookies")
    assert yd.find_cookies_file(c_file) == c_file.resolve()

    # Via env var
    monkeypatch.setenv("COOKIES_FILE", str(c_file))
    assert yd.find_cookies_file() == c_file.resolve()

    # None found
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    assert yd.find_cookies_file(tmp_path / "non_existent.txt") is None


def test_download_youtube_empty_url() -> None:
    with pytest.raises(ValueError, match="não pode ser vazia"):
        yd.download_youtube("")


def test_download_youtube_list_only(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = yd.download_youtube(
            "https://www.youtube.com/playlist?list=PL123",
            output_dir=tmp_path,
            list_only=True,
        )
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert "--flat-playlist" in cmd
        assert "--print" in cmd


def test_download_youtube_audio_mode(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = yd.download_youtube(
            "https://www.youtube.com/watch?v=123",
            output_dir=tmp_path,
            audio_only=True,
            extra_args=["--audio-quality", "3"],
        )
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert "-x" in cmd
        assert "--audio-format" in cmd
        assert "mp3" in cmd
        assert "--audio-quality" in cmd
        assert "3" in cmd


def test_download_youtube_video_mode(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        code = yd.download_youtube(
            "https://www.youtube.com/watch?v=123",
            output_dir=tmp_path,
            max_height=720,
            flat_output=True,
        )
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert "--merge-output-format" in cmd
        assert "mp4" in cmd
        assert "height<=720" in "".join(cmd)


def test_download_youtube_cli_integration(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from knowledge_extractor.cli import cli

    runner = CliRunner()
    with patch("knowledge_extractor.youtube_downloader.download_youtube") as mock_dl:
        mock_dl.return_value = 0
        mod_dir = tmp_path / "modules/01-test"
        res = runner.invoke(
            cli,
            [
                "download-youtube",
                "https://www.youtube.com/playlist?list=PL123",
                "-m",
                str(mod_dir),
                "--audio",
                "--max-height",
                "720",
            ],
        )
        assert res.exit_code == 0
        assert mock_dl.called
        kwargs = mock_dl.call_args[1]
        assert kwargs["url"] == "https://www.youtube.com/playlist?list=PL123"
        assert kwargs["audio_only"] is True
        assert kwargs["flat_output"] is True
        assert kwargs["max_height"] == 720
        assert (
            str(kwargs["output_dir"])
            .replace("\\", "/")
            .endswith("modules/01-test/materiais/raw")
        )
