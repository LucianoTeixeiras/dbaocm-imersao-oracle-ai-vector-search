"""Nome canônico da nota: gravar e checar usam a mesma função."""

from pathlib import Path

import pytest

from knowledge_extractor.note_naming import (
    audit_notes,
    legacy_note_filenames,
    note_filename_for,
)
from knowledge_extractor.synthesizer import LectureSynthesizer


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("Module 06 01 Resources.pdf", "aula-module-06-01-resources.md"),
        ("01 Introdução à Memória.mp4", "aula-01-introducao-a-memoria.md"),
        ("midia-aula-pratica.txt", "aula-midia-aula-pratica.md"),
        ("aula-02-rag.txt", "aula-02-rag.md"),
        ("mod02-aula-01-x.md", "mod02-aula-01-x.md"),
    ],
)
def test_canonical_names(src: str, expected: str) -> None:
    assert note_filename_for(src) == expected


def test_same_name_for_video_txt_pdf() -> None:
    assert (
        note_filename_for("raw/x y.mp4")
        == note_filename_for("transcricoes/x y.txt")
        == note_filename_for("x y.pdf")
    )


def _module(tmp_path: Path, name: str) -> tuple[Path, Path]:
    mod = tmp_path / "06-mod"
    (mod / "materiais" / "transcricoes").mkdir(parents=True)
    (mod / "aulas").mkdir()
    src = mod / "materiais" / "transcricoes" / name
    src.write_text("conteúdo da transcrição " * 20, encoding="utf-8")
    return mod, src


def _synth(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> LectureSynthesizer:
    s = LectureSynthesizer.__new__(LectureSynthesizer)  # sem rede/cliente

    def fake_process(
        self, transcript_file, module_dir, output_dir=None, force=False, keyframes=None
    ):
        from knowledge_extractor.note_naming import note_path_for
        from knowledge_extractor.synthesizer import _is_scaffold_file

        p = note_path_for(transcript_file, output_dir or module_dir / "aulas")
        if p.exists() and not force and not _is_scaffold_file(p):
            return p
        calls.append(1)
        p.write_text("# nota real\n" + "texto " * 200, encoding="utf-8")
        return p

    monkeypatch.setattr(LectureSynthesizer, "process_file", fake_process)
    return s


@pytest.mark.parametrize("name", ["Module 06 01 Resources.txt", "Aula Ação Única.txt"])
def test_write_then_check_skips_and_force_regenerates(
    tmp_path, monkeypatch, name
) -> None:
    from click.testing import CliRunner

    from knowledge_extractor import cli as cli_mod

    mod, src = _module(tmp_path, name)
    calls: list[int] = []
    _synth(monkeypatch, calls)
    monkeypatch.setattr(
        cli_mod,
        "LectureSynthesizer",
        lambda **kw: LectureSynthesizer.__new__(LectureSynthesizer),
    )
    monkeypatch.setattr(LectureSynthesizer, "print_mode", lambda self: None)
    r = CliRunner()
    r.invoke(
        cli_mod.cli, ["synthesize", str(mod), "--no-vision"], catch_exceptions=False
    )
    assert calls == [1]
    assert (mod / "aulas" / note_filename_for(src)).exists()
    out = r.invoke(
        cli_mod.cli, ["synthesize", str(mod), "--no-vision"], catch_exceptions=False
    ).output
    assert calls == [1] and "Pulando, nota já existe" in out
    r.invoke(
        cli_mod.cli,
        ["synthesize", str(mod), "--no-vision", "--force"],
        catch_exceptions=False,
    )
    assert calls == [1, 1]


def test_real_process_file_skip_uses_same_name(tmp_path: Path) -> None:
    mod, src = _module(tmp_path, "midia-x.txt")
    note = mod / "aulas" / note_filename_for(src)
    note.write_text("# nota real\n" + "texto " * 200, encoding="utf-8")
    s = LectureSynthesizer.__new__(LectureSynthesizer)
    assert s.process_file(src, mod) == note  # pula sem chamar LLM


def test_audit_lists_legacy_and_orphans_without_renaming(tmp_path: Path) -> None:
    mod, src = _module(tmp_path, "01 Introdução.txt")
    legacy = sorted(legacy_note_filenames(src))
    (mod / "aulas" / "aula-01-introdução.md").write_text("x", encoding="utf-8")
    (mod / "aulas" / "perdida.md").write_text("x", encoding="utf-8")
    res = audit_notes(mod)
    assert "aula-01-introdução.md" in legacy
    assert res["nome_antigo"] == [("aula-01-introdução.md", "aula-01-introducao.md")]
    assert res["orfas"] == [("perdida.md", "sem fonte correspondente")]
    assert (mod / "aulas" / "aula-01-introdução.md").exists()
