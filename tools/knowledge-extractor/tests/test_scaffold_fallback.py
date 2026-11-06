"""Scaffold só para falhas de ambiente; erro de programação propaga e é logado."""

from __future__ import annotations

import logging

import pytest

from knowledge_extractor import model_router as mr
from knowledge_extractor import synthesizer as sy

ARGS = ("transcrição de teste", "Aula X", "mod-01", "aula-x.txt")


@pytest.fixture
def synth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # load_course_config() sem course.yaml -> padrões

    def make(api_key: str):
        s = sy.LectureSynthesizer(api_key=api_key, provider="auto")
        s.client = None  # nunca chama a rede
        monkeypatch.setattr(s, "is_ollama_available", lambda: False)
        return s

    return make


def _raise(exc):
    def f(*a, **k):
        raise exc

    return f


@pytest.mark.parametrize(
    "exc", [NameError("x"), KeyError("choices"), TypeError("bad"), AttributeError("y")]
)
def test_erro_de_programacao_na_cadeia_cloud_propaga_sem_scaffold(
    synth, monkeypatch, caplog, exc
):
    s = synth("chave-real-de-teste")
    monkeypatch.setattr(s, "synthesize_cloud", _raise(exc))
    with caplog.at_level(logging.ERROR), pytest.raises(type(exc)):
        s._generate(*ARGS, info={})
    assert "Erro de programação" in caplog.text
    assert type(exc).__name__ in caplog.text


def test_falha_de_ambiente_gera_scaffold_com_motivo_e_sem_pedir_chave(
    synth, monkeypatch, caplog
):
    s = synth("chave-real-de-teste")
    monkeypatch.setattr(s, "synthesize_cloud", _raise(RuntimeError("503 UNAVAILABLE")))
    with caplog.at_level(logging.WARNING):
        content, provider = s._generate(*ARGS, info={})
    assert provider == "scaffold"
    assert "Configure a `GEMINI_API_KEY`" not in content
    assert "RuntimeError: 503 UNAVAILABLE" in content
    assert "gerado em modo scaffold offline" in content
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("key", ["", "sua_gemini_api_key_aqui"])
def test_aviso_de_chave_so_quando_ausente(synth, monkeypatch, key):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = synth(key)
    s.api_key = key or None
    monkeypatch.setattr(
        s,
        "synthesize_cloud",
        _raise(mr.ModelCallError(None, "OPENROUTER_API_KEY ausente")),
    )
    content, provider = s._generate(*ARGS, info={})
    assert provider == "scaffold"
    assert "Configure a `GEMINI_API_KEY`" in content


def test_router_propaga_erro_de_programacao(monkeypatch):
    r = mr.ModelRouter(mr.parse_models("openrouter:m/a:1,openrouter:m/b:1"), seed=1, backoff_s=0,
                       sleep=lambda x: None)  # fmt: skip
    monkeypatch.setattr(r, "_call", _raise(TypeError("bug")))
    with pytest.raises(TypeError):
        r.synthesize("sys", "user", [], providers=("openrouter",))
