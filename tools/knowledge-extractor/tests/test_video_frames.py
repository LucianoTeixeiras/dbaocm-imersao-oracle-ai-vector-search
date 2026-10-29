"""Testes unitários da lógica pura de extração visual e normalização."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np

from knowledge_extractor.audio_transcriber import (
    CHUNK_THRESHOLD_SECONDS,
    format_timestamp,
    get_media_duration,
)
from knowledge_extractor.file_normalizer import suggest_normalized_stem
from knowledge_extractor.figures import (
    MARKER_END,
    MARKER_START,
    attach_figures,
    build_figures_block,
    sanitize_slug,
)
from knowledge_extractor.synthesizer import _is_scaffold_file, _is_transient_error
from knowledge_extractor.video_frames import (
    dedupe_and_anchor,
    extract_timestamp_label,
    format_frame_timestamp,
)


def test_format_and_extract_timestamp() -> None:
    assert format_frame_timestamp(3.1) == "00m03s"
    assert format_frame_timestamp(332.2) == "05m32s"
    assert format_frame_timestamp(1508.8) == "25m09s"
    assert format_frame_timestamp(32951.0) == "549m11s"
    assert extract_timestamp_label(Path("frame_0001_05m32s.jpg")) == "05:32"
    assert extract_timestamp_label(Path("frame_0001_549m11s.jpg")) == "09:09:11"
    assert extract_timestamp_label(Path("frame_0001_60m05s.jpg")) == "01:00:05"
    assert extract_timestamp_label(Path("frame_0001.jpg")) is None

    # Validação do timestamp SRT para mídias longas (HH:MM:SS,mmm)
    assert format_timestamp(32951.25) == "09:09:11,250"
    assert format_timestamp(45.5) == "00:00:45,500"
    assert CHUNK_THRESHOLD_SECONDS == 3600.0
    assert get_media_duration(Path("arquivo_inexistente_123.mp4")) == 0.0


def test_dedupe_drops_near_identical_and_subsamples(tmp_path: Path) -> None:
    files = []
    for i in range(6):
        p = tmp_path / f"_raw_{i:04d}.jpg"
        p.write_bytes(b"x")
        files.append(p)

    # Frame 0 e 1 idênticos (dist=0), demais distintos (dist=256)
    hashes = [
        np.zeros(256, dtype=bool),
        np.zeros(256, dtype=bool),  # duplicata do 0
        np.ones(256, dtype=bool),
        np.array([i % 2 == 0 for i in range(256)], dtype=bool),
        np.array([i % 2 == 1 for i in range(256)], dtype=bool),
        np.array([i < 128 for i in range(256)], dtype=bool),
    ]
    pts = [3.0, 4.0, 180.0, 360.0, 540.0, 720.0]

    with patch("knowledge_extractor.video_frames._compute_dhash", side_effect=hashes):
        kept = dedupe_and_anchor(files, pts, max_frames=3, hamming_threshold=28)

    # Removeu a duplicata (ficaram 5) e subamostrou uniformemente para max_frames=3,
    # preservando início e fim da aula (3s e 720s)
    assert len(kept) == 3
    assert kept[0][1] == 3.0
    assert kept[-1][1] == 720.0


def test_normalizer_does_not_prefix_doc_on_aula_pdf() -> None:
    stem = "aula-08-final-principais-frameworks-de-agentes"
    assert suggest_normalized_stem(stem, ".pdf") == stem
    assert suggest_normalized_stem("relatorio-arquitetura", ".pdf") == "doc-relatorio-arquitetura"


def test_transient_error_classification() -> None:
    assert _is_transient_error(RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand."))
    assert _is_transient_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert not _is_transient_error(RuntimeError("400 INVALID_ARGUMENT"))


def test_run_pipeline_script_is_utf8_bom_and_ascii() -> None:
    """O orquestrador .ps1 precisa de BOM UTF-8; sem ele o PowerShell 5.1 lê como ANSI e quebra o parse."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "tools" / "run-pipeline.ps1"
    if not script.exists():
        return
    raw = script.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "run-pipeline.ps1 deve começar com BOM UTF-8"
    assert all(b < 128 for b in raw[3:]), "run-pipeline.ps1 deve conter apenas ASCII (evita corrupção ANSI)"


def test_scaffold_detection(tmp_path: Path) -> None:
    scaffold = tmp_path / "aula-01.md"
    scaffold.write_text("# Aula 01\n\n> **Status:** 🟡 Rascunho / Em Estruturação\n", encoding="utf-8")
    real = tmp_path / "aula-02.md"
    real.write_text("# Aula 02\n\n> **Status:** Concluído / Documentado\n", encoding="utf-8")

    assert _is_scaffold_file(scaffold) is True
    assert _is_scaffold_file(real) is False
    assert _is_scaffold_file(tmp_path / "inexistente.md") is False


def test_sanitize_slug() -> None:
    assert sanitize_slug("Fluxo de Execução com Git Worktrees") == "fluxo-de-execucao-com-git-worktrees"
    assert sanitize_slug("  !!  Arquitetura & Design  ") == "arquitetura-design"
    assert sanitize_slug("   ") == "figura"


def test_figures_block_and_attach_idempotent(tmp_path: Path) -> None:
    note = tmp_path / "aula-01.md"
    note.write_text(
        "# Aula 01\n\n## 🧠 Conceitos\n\ntexto\n\n## 📋 Resumo Executivo & Rastreabilidade de Estudo\n\nfim\n",
        encoding="utf-8",
    )
    figs = [
        {"path": tmp_path / "frame_0001_18m26s.jpg", "slug": "fluxo-worktrees", "legenda": "Fluxo de execução com Git Worktrees"},
    ]
    block = build_figures_block("aula-01", figs)
    assert MARKER_START in block and MARKER_END in block
    assert "materiais/figuras/aula-01--fluxo-worktrees.webp" in block

    assert attach_figures(note, "aula-01", figs) is True
    text = note.read_text(encoding="utf-8")
    # Bloco inserido ANTES do Resumo Executivo
    assert text.index("Figuras de Referência") < text.index("Resumo Executivo")
    # Segunda chamada não duplica o bloco (idempotência)
    attach_figures(note, "aula-01", figs)
    assert note.read_text(encoding="utf-8").count(MARKER_START) == 1


def test_discover_modules_ordering(tmp_path: Path) -> None:
    """Valida que discover_modules encontra e ordena as pastas de módulos."""
    from knowledge_extractor.pipeline import discover_modules
    mods_dir = tmp_path / "modules"
    mods_dir.mkdir(parents=True)
    (mods_dir / "02-arquitetura").mkdir()
    (mods_dir / "01-fundamentos").mkdir()
    (mods_dir / ".gitkeep").write_text("", encoding="utf-8") # ignora arquivos
    (tmp_path / "course.yaml").write_text("course:\n  id: 't'\n", encoding="utf-8")

    found = discover_modules(tmp_path)
    assert len(found) == 2
    assert found[0].name == "01-fundamentos"
    assert found[1].name == "02-arquitetura"


def test_pipeline_imports_confirm() -> None:
    """Garante que Confirm esteja importado em pipeline.py (evita NameError na opção 8/menu)."""
    import knowledge_extractor.pipeline as p
    assert hasattr(p, "Confirm")
    from rich.prompt import Confirm
    assert p.Confirm is Confirm


def test_course_config_audio_language(tmp_path: Path) -> None:
    """Valida leitura e derivação de audio_language no course.yaml."""
    from knowledge_extractor.course_config import load_course_config
    c_yaml = tmp_path / "course.yaml"
    c_yaml.write_text("""
course:
  id: "test"
  title: "Curso Teste"
  language: "en-US"
  audio_language: "en"
""", encoding="utf-8")
    cfg = load_course_config(tmp_path)
    assert cfg.audio_language == "en"




def test_catalog_sync_normalization_pairs_correctly(tmp_path: Path) -> None:
    """Garante que midia-X e aula-midia-X resolvam para a mesma chave única no catálogo."""
    from knowledge_extractor.catalog_sync import CatalogSynchronizer
    syncer = CatalogSynchronizer()
    mod_dir = tmp_path / "01-teste"
    (mod_dir / "materiais" / "raw").mkdir(parents=True)
    (mod_dir / "materiais" / "transcricoes").mkdir(parents=True)
    (mod_dir / "aulas").mkdir(parents=True)

    (mod_dir / "materiais" / "raw" / "midia-aula-pratica.mp4").write_bytes(b"video")
    (mod_dir / "materiais" / "transcricoes" / "midia-aula-pratica.txt").write_text("transcricao", encoding="utf-8")
    (mod_dir / "aulas" / "aula-midia-aula-pratica.md").write_text("# Aula", encoding="utf-8")

    cat = syncer.sync_module_catalog(mod_dir)
    assert cat is not None and cat.exists()
    content = cat.read_text(encoding="utf-8")
    # Deve haver apenas UMA linha de dados (além do cabeçalho) com status Concluído
    linhas_tabela = [l for l in content.splitlines() if l.startswith("| **")]
    assert len(linhas_tabela) == 1
    assert "✅ Concluído" in linhas_tabela[0]
    assert "midia-aula-pratica" in linhas_tabela[0]

