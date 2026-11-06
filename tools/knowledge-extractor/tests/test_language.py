"""LRT-01: detecção real de idioma (Whisper mockado) e saída sempre em pt-BR (LLM mockado)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_extractor import audio_transcriber as at
from knowledge_extractor.language import (
    check_ptbr,
    dominant_language,
    language_directive,
    normalize_lang,
    resolve_audio_language,
)

PT = (
    "Nesta aula vamos entender como o agente decide qual ferramenta usar. O modelo recebe a pergunta do usuário "
    "e, com base no contexto, escolhe a ação mais adequada para a tarefa. Isso é importante porque a qualidade "
    "da resposta depende da forma como os dados são recuperados e de como o prompt é montado pelo sistema."
)
EN = (
    "In this lesson we will understand how the agent decides which tool to use. The model receives the question "
    "from the user and, based on the context, it picks the best action for the task. This is important because "
    "the quality of the answer depends on how the data is retrieved and how the prompt is built by the system."
)


@pytest.mark.parametrize(
    ("v", "exp"),
    [("auto", None), ("", None), (None, None), ("en-US", "en"), ("PT_br", "pt")],
)
def test_normalize(v, exp) -> None:
    assert normalize_lang(v) == exp


def _course(tmp_path: Path, audio: str | None) -> Path:
    extra = f'\n  audio_language: "{audio}"' if audio else ""
    (tmp_path / "course.yaml").write_text(
        f'course:\n  id: x\n  language: "pt-BR"{extra}\n  keep_terms: ["RAG", "checkpointer"]\n',
        encoding="utf-8",
    )
    return tmp_path


def test_precedence_cli_env_course_auto(tmp_path, monkeypatch) -> None:
    d = _course(tmp_path, "es")
    monkeypatch.setenv("WHISPER_LANGUAGE", "fr")
    assert resolve_audio_language("en", d) == "en"
    assert resolve_audio_language(None, d) == "fr"
    monkeypatch.setenv("WHISPER_LANGUAGE", "auto")
    assert (
        resolve_audio_language(None, d) is None
    )  # auto explícito no .env vence o course.yaml
    monkeypatch.delenv("WHISPER_LANGUAGE")
    assert resolve_audio_language(None, d) == "es"
    assert (
        resolve_audio_language(
            None,
            _course(
                tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, None
            ),
        )
        is None
    )


def test_keep_terms_loaded(tmp_path) -> None:
    from knowledge_extractor.course_config import load_course_config

    cfg = load_course_config(_course(tmp_path, None))
    assert cfg.keep_terms == ["RAG", "checkpointer"] and cfg.audio_language is None


class FakeModel:
    def __init__(self, langs):
        self.langs, self.calls = list(langs), []

    def transcribe(self, path, language=None, **kw):
        self.calls.append(language)
        lang = language or self.langs.pop(0)
        seg = SimpleNamespace(start=0.0, end=5.0, text=f"texto {lang}")
        return iter([seg]), SimpleNamespace(
            language=lang, language_probability=0.91, duration=600.0
        )


def _transcriber(model, language=None):
    t = at.Transcriber.__new__(at.Transcriber)
    t.model, t.model_size, t.language = model, "small", language
    return t


def test_auto_detects_each_chunk_and_writes_meta(tmp_path, monkeypatch) -> None:
    media = tmp_path / "aula.mp3"
    media.write_bytes(b"x")
    monkeypatch.setattr(
        at, "get_media_duration", lambda p: 5400.0 if p == media.resolve() else 600.0
    )

    def chunks(_inp, out, chunk_seconds):
        out.mkdir(parents=True)
        files = [out / f"chunk_{i}.wav" for i in range(3)]
        for f in files:
            f.write_bytes(b"x")
        return files

    monkeypatch.setattr(at, "extract_audio_chunks", chunks)
    model = FakeModel(["en", "en", "es"])
    _txt, _ = _transcriber(model).transcribe_file(media, tmp_path / "out")
    assert model.calls == [None, None, None]  # não fixa o idioma do chunk 1
    meta = json.loads((tmp_path / "out" / "aula.meta.json").read_text(encoding="utf-8"))
    assert (
        meta["language"] == "en"
        and meta["mixed_languages"]
        and meta["languages_detected"] == ["en", "es"]
    )
    assert (
        meta["whisper_model"] == "small"
        and meta["language_probability"] == 0.91
        and meta["output_language"] == "pt-BR"
    )


def test_fixed_language_passed_to_whisper(tmp_path, monkeypatch) -> None:
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    monkeypatch.setattr(at, "get_media_duration", lambda p: 60.0)
    model = FakeModel([])
    _transcriber(model, "en").transcribe_file(media, tmp_path)
    assert model.calls == ["en"]
    assert (
        json.loads((tmp_path / "a.meta.json").read_text())["language_mode"] == "fixed"
    )


def test_dominant_language_weighted() -> None:
    assert dominant_language([("en", 100), ("pt", 900)]) == "pt"


def test_directive_translates_foreign_and_normalizes_pt() -> None:
    en = language_directive("en", ["RAG"])
    assert "TRADUZA" in en and "RAG" in en and "PORTUGUÊS DO BRASIL" in en
    pt = language_directive("pt", [])
    assert "Não traduza" in pt and "PORTUGUÊS DO BRASIL" in pt and "utilizador" in pt
    assert "LangChain" not in en + pt  # sem lista fixa


def test_prompt_uses_course_keep_terms(tmp_path, monkeypatch) -> None:
    from knowledge_extractor import synthesizer as sy

    monkeypatch.chdir(_course(tmp_path, None))
    p = sy.build_system_prompt(source_language="en")
    assert "checkpointer" in p and "TRADUZA" in p


def test_check_ptbr() -> None:
    assert check_ptbr(PT) == []
    assert check_ptbr(EN) and "inglês" in check_ptbr(EN)[0]
    assert any(
        "pt-PT" in a for a in check_ptbr(PT + " O utilizador abre o ficheiro no ecrã.")
    )
    assert check_ptbr("```python\n" + EN + "\n```\n" + PT) == []  # código ignorado


def test_synthesis_reads_meta_and_warns_when_not_ptbr(
    tmp_path, monkeypatch, capsys
) -> None:
    from knowledge_extractor import synthesizer as sy

    mod = tmp_path / "01-m"
    tdir = mod / "materiais" / "transcricoes"
    tdir.mkdir(parents=True)
    src = tdir / "aula-01-x.txt"
    src.write_text(EN, encoding="utf-8")
    (tdir / "aula-01-x.meta.json").write_text('{"language": "en"}', encoding="utf-8")
    s = sy.LectureSynthesizer.__new__(sy.LectureSynthesizer)
    seen = {}

    def fake_generate(self, text, title, module, fname, info, keyframes=None):
        seen["prompt"] = sy.build_system_prompt(
            module, source_language=self._source_language
        )
        return "# Nota\n\n" + EN, "gemini"

    monkeypatch.setattr(sy.LectureSynthesizer, "_generate", fake_generate)
    monkeypatch.setattr(
        sy.LectureSynthesizer,
        "curate_and_attach_figures",
        lambda *a, **k: 0,
        raising=False,
    )
    s.process_file(src, mod, force=True)
    assert "'en'" in seen["prompt"] and "TRADUZA" in seen["prompt"]
    assert "pode não estar em pt-BR" in capsys.readouterr().out
