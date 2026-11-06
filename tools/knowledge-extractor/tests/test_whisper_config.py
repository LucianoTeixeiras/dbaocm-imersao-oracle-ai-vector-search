"""LRT-04: configuração do faster-whisper (GPU mockada)."""

import re
from pathlib import Path

import pytest

from knowledge_extractor import whisper_config as wc

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in (*wc.ENV_VARS, "WHISPER_MODEL"):
        monkeypatch.delenv(v, raising=False)


def gpu(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(wc, "cuda_available", lambda: on)


def test_auto_gpu(monkeypatch) -> None:
    gpu(monkeypatch, True)
    c = wc.resolve_whisper_config(log=False)
    assert (c.model_size, c.device, c.compute_type) == ("large-v3", "cuda", "float16")


def test_auto_cpu(monkeypatch) -> None:
    gpu(monkeypatch, False)
    c = wc.resolve_whisper_config(log=False)
    assert (c.model_size, c.device, c.compute_type) == ("small", "cpu", "int8")


def test_medium_allowed_and_precedence(monkeypatch) -> None:
    gpu(monkeypatch, False)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "medium")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float32")
    c = wc.resolve_whisper_config(log=False)
    assert (c.model_size, c.compute_type, c.sources["model_size"]) == (
        "medium",
        "float32",
        ".env",
    )
    c = wc.resolve_whisper_config(model_size="tiny", compute_type="int8", log=False)
    assert (c.model_size, c.compute_type, c.sources["model_size"]) == (
        "tiny",
        "int8",
        "cli",
    )


def test_float16_on_cpu_falls_back(monkeypatch, capsys) -> None:
    gpu(monkeypatch, False)
    c = wc.resolve_whisper_config(compute_type="float16")
    assert c.compute_type == "int8" and c.warnings
    out = capsys.readouterr().out
    assert (
        "não é suportado em CPU" in out and "compute_type=int8" in out
    )  # log dos valores efetivos


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"model_size": "gigante"}, "WHISPER_MODEL_SIZE"),
        ({"device": "tpu"}, "WHISPER_DEVICE"),
        ({"compute_type": "int4"}, "WHISPER_COMPUTE_TYPE"),
    ],
)
def test_invalid_values(monkeypatch, kw, msg) -> None:
    gpu(monkeypatch, True)
    with pytest.raises(wc.WhisperConfigError, match=msg):
        wc.resolve_whisper_config(log=False, **kw)


def test_cuda_requested_without_gpu(monkeypatch) -> None:
    gpu(monkeypatch, False)
    with pytest.raises(wc.WhisperConfigError, match="nenhuma GPU"):
        wc.resolve_whisper_config(device="cuda", log=False)


def test_deprecated_whisper_model(monkeypatch) -> None:
    gpu(monkeypatch, False)
    monkeypatch.setenv("WHISPER_MODEL", "base")
    c = wc.resolve_whisper_config(log=False)
    assert c.model_size == "base" and any("obsoleta" in w for w in c.warnings)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "medium")
    assert wc.resolve_whisper_config(log=False).model_size == "medium"


def test_language_from_env(monkeypatch, tmp_path) -> None:
    gpu(monkeypatch, False)
    monkeypatch.setenv("WHISPER_LANGUAGE", "en")
    assert wc.resolve_whisper_config(start_dir=tmp_path, log=False).language == "en"


def test_cuda_detection_ctranslate2(monkeypatch) -> None:
    import sys
    import types

    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        types.SimpleNamespace(get_cuda_device_count=lambda: 1),
    )
    assert wc.cuda_available() is True
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        types.SimpleNamespace(get_cuda_device_count=lambda: 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
    )
    assert wc.cuda_available() is False


def test_env_example_matches_code() -> None:
    env_keys = set(
        re.findall(
            r"^(WHISPER_[A-Z_]+)=",
            (ROOT / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "tools/knowledge-extractor/src").rglob("*.py")
    )
    code_keys = set(re.findall(r"getenv\(\s*\"(WHISPER_[A-Z_]+)\"", src)) | set(
        wc.ENV_VARS
    )
    code_keys -= set(wc.DEPRECATED_ENV)
    assert env_keys == code_keys


def test_transcriber_uses_config(monkeypatch) -> None:
    from knowledge_extractor import audio_transcriber as at

    seen = {}
    monkeypatch.setattr(at, "WhisperModel", lambda m, **kw: seen.update(model=m, **kw))
    cfg = wc.WhisperConfig("small", "cpu", "int8", None)
    t = at.Transcriber(config=cfg)
    assert (
        seen["model"] == "small"
        and seen["device"] == "cpu"
        and seen["compute_type"] == "int8"
        and t.language is None
    )
