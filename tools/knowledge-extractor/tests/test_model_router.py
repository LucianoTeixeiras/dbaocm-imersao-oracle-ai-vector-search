"""LRT-07: rodízio ponderado, filtro multimodal, fallback, cooldown, política e privacidade (offline)."""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import httpx
import pytest

from knowledge_extractor import model_router as mr
from knowledge_extractor import synthesizer as sy
from knowledge_extractor.mermaid_fixer import sanitize_markdown

CHAIN = (
    "gemini:gemini-2.5-flash:45,openrouter:z-ai/glm-5.3-flash:25,"
    "openrouter:nvidia/nemotron-3.5-super-vl:free:20;allow_training,"
    "gemini:gemini-3.5-flash-lite:5,openrouter:dots-studio/dots-3-note-preview:free:5,"
    "openrouter:google/gemma-4-31b-it:free:0"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # fmt: off
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SEGREDO-123")  # gitleaks:allow
    # fmt: on
    monkeypatch.setenv("SYNTH_HEALTH_FILE", str(tmp_path / ".cache/model_health.json"))
    for k in ("SYNTH_MODELS", "SYNTH_TEXT_MODELS", "SYNTH_SEED"):
        monkeypatch.delenv(k, raising=False)


class Http:
    """Cliente httpx falso: responde por modelo com uma fila de (status, corpo)."""

    def __init__(self, script: dict[str, list[tuple[int, dict]]]):
        self.script, self.requests = script, []

    def post(self, url, json, headers, timeout):
        self.requests.append({"url": url, "json": json, "headers": headers})
        status, body = self.script[json["model"]].pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def ok(text="# Nota", tin=100, tout=50):
    return 200, {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout},
    }


def router(chain, http=None, health=None, seed=1, **kw):
    return mr.ModelRouter(mr.parse_models(chain) if isinstance(chain, str) else chain, http=http,
                          health=health, seed=seed, retries=2, backoff_s=0, sleep=lambda s: None, **kw)  # fmt: skip


def test_parse_format_ids_com_dois_pontos():
    specs = mr.parse_models(CHAIN)
    assert [(s.provider, s.model, s.weight) for s in specs] == [
        ("gemini", "gemini-2.5-flash", 45),
        ("openrouter", "z-ai/glm-5.3-flash", 25),
        ("openrouter", "nvidia/nemotron-3.5-super-vl:free", 20),
        ("gemini", "gemini-3.5-flash-lite", 5),
        ("openrouter", "dots-studio/dots-3-note-preview:free", 5),
        ("openrouter", "google/gemma-4-31b-it:free", 0),
    ]
    s = mr.parse_models("openrouter:x/y:free;allow_training", vision_default=False)[0]
    assert (s.model, s.weight, s.allow_training, s.vision) == (
        "x/y:free",
        1,
        True,
        False,
    )
    with pytest.raises(ValueError):
        mr.parse_models("ollama:qwen:3")
    assert mr.parse_models(mr.DEFAULT_SYNTH_MODELS) == specs  # padrão == .env.example


def test_env_example_usa_o_padrao():
    env = (Path(__file__).resolve().parents[3] / ".env.example").read_text(
        encoding="utf-8"
    )
    assert f"SYNTH_MODELS={mr.DEFAULT_SYNTH_MODELS}" in env


def test_sorteio_ponderado_com_seed():
    specs = mr.parse_models(CHAIN)
    a = [s.key for s in mr.weighted_order(specs, random.Random(7))]
    b = [s.key for s in mr.weighted_order(specs, random.Random(7))]
    assert (
        a == b and a[-1] == "openrouter:google/gemma-4-31b-it:free"
    )  # peso 0 sempre por último
    firsts = Counter(
        mr.weighted_order(specs, random.Random(i))[0].model for i in range(3000)
    )
    assert 0.39 < firsts["gemini-2.5-flash"] / 3000 < 0.51
    assert 0.15 < firsts["nvidia/nemotron-3.5-super-vl:free"] / 3000 < 0.25
    assert 0.20 < firsts["z-ai/glm-5.3-flash"] / 3000 < 0.30
    assert firsts["google/gemma-4-31b-it:free"] == 0


def test_filtro_multimodal(monkeypatch):
    monkeypatch.setenv("SYNTH_MODELS", "openrouter:a/vl:free:10")
    monkeypatch.setenv(
        "SYNTH_TEXT_MODELS", "openrouter:nvidia/nemotron-3.5-lightning:free:90"
    )
    r = mr.ModelRouter(seed=3, http=None)
    assert [s.model for s in r.candidates(needs_vision=True)] == ["a/vl:free"]
    assert {s.model for s in r.candidates(needs_vision=False)} == {
        "a/vl:free",
        "nvidia/nemotron-3.5-lightning:free",
    }


def test_fallback_429_vai_ao_proximo_e_chave_nao_vaza(capsys):
    http = Http(
        {"m/a": [(429, {"error": {"message": "rate limited"}})] * 2, "m/b": [ok()]}
    )
    res = router("openrouter:m/a:1,openrouter:m/b:0", http=http).synthesize(
        "sys", "user"
    )
    assert (res.model, res.content, res.tokens_in, res.tokens_out) == (
        "m/b",
        "# Nota",
        100,
        50,
    )
    assert [(a["model"], a["status"]) for a in res.attempts] == [
        ("openrouter:m/a", 429), ("openrouter:m/a", 429), ("openrouter:m/b", "ok")]  # fmt: skip
    assert "SEGREDO" not in capsys.readouterr().out
    assert http.requests[0]["headers"]["Authorization"] == "Bearer sk-or-SEGREDO-123"


def test_cooldown_persistido(tmp_path):
    hp = tmp_path / "h.json"
    health = mr.ModelHealth(hp, cooldown_min=10, threshold=1)
    http = Http({"m/a": [(503, {})] * 2, "m/b": [ok(), ok()]})
    r = router("openrouter:m/a:1,openrouter:m/b:0", http=http, health=health)
    r.synthesize("s", "u")
    assert json.loads(hp.read_text())["openrouter:m/a"]["cooldown_until"] > 0
    fresh = mr.ModelHealth(hp, cooldown_min=10)  # nova execução lê o arquivo
    r2 = router("openrouter:m/a:1,openrouter:m/b:0", http=http, health=fresh)
    assert [s.model for s in r2.candidates(False)] == ["m/b"]
    assert len(r2.synthesize("s", "u").attempts) == 1


def test_404_politica_desativa_com_mensagem(capsys):
    body = {
        "error": {
            "message": "No endpoints found matching your data policy (Free model training)"
        }
    }
    http = Http({"m/vl:free": [(404, body)], "m/b": [ok()]})
    r = router("openrouter:m/vl:free:9,openrouter:m/b:0", http=http)
    res = r.synthesize("s", "u")
    assert res.model == "m/b" and len(http.requests) == 2  # 404 não é repetido
    out = capsys.readouterr().out.replace("\n", " ")
    assert "openrouter.ai/settings/privacy" in out and "allow_training" in out
    assert not r.health.available("openrouter:m/vl:free")


def test_privacidade_deny_por_padrao_allow_so_marcado(tmp_path):
    img = tmp_path / "f.png"
    img.write_bytes(b"\x89PNG fake")
    http = Http({"m/a": [ok()], "m/b": [ok()]})
    router("openrouter:m/a:1", http=http).synthesize(
        "s", "u", [("00:10", img, "image/png")]
    )
    router("openrouter:m/b:1;allow_training", http=http).synthesize("s", "u")
    a, b = (r["json"] for r in http.requests)
    assert a["provider"]["data_collection"] == "deny"
    assert b["provider"]["data_collection"] == "allow"
    kinds = [c["type"] for c in a["messages"][1]["content"]]
    assert kinds == ["text", "text", "image_url"]
    assert a["messages"][1]["content"][2]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_meta_json_da_nota(tmp_path, monkeypatch):
    module = tmp_path / "05-mod"
    (module / "materiais/transcricoes").mkdir(parents=True)
    t = module / "materiais/transcricoes/aula-05-lesson-5.md"
    t.write_text(
        "Hoje falamos de memória de longo prazo em agentes. " * 20, encoding="utf-8"
    )
    monkeypatch.setenv("SYNTH_MODELS", "openrouter:m/a:1,openrouter:m/b:0")
    http = Http(
        {
            "m/a": [(429, {})] * 2,
            "m/b": [ok('# Nota\n\n```mermaid\ngraph TD\n  A["x<br/>y"] --> B\n```\n')],
        }
    )
    s = sy.LectureSynthesizer(api_key="sua_gemini", provider="auto")
    s._model_router = mr.ModelRouter(
        http=http, seed=1, backoff_s=0, sleep=lambda x: None
    )
    monkeypatch.setattr(s, "is_ollama_available", lambda: False)
    note = s.process_file(t, module)
    meta = json.loads(note.with_suffix(".meta.json").read_text())["synthesis"]
    assert (meta["provider"], meta["model"], meta["tokens_in"], meta["tokens_out"]) == (
        "openrouter",
        "m/b",
        100,
        50,
    )
    assert len(meta["attempts"]) == 3 and "latency_s" in meta
    body = note.read_text()
    assert "<br" not in body and "%%{init" in body  # pós-processador LRT-02 sempre roda


def test_sanitize_remove_br_em_rotulos():
    md = "```mermaid\nsequenceDiagram\n  A->>B: oi<br/>tchau\n```\n"
    out, n = sanitize_markdown(md)
    assert n == 1 and "<br" not in out and "oi tchau" in out


def test_system_prompt_sem_nameerror():
    # regressão do NameError: name 'init' (f-string com %%{init: ...}%%); o init é injetado no pós-processamento
    p = sy.build_system_prompt("m", has_vision=True, source_language="en")
    assert "%%{init}%%" in p  # texto literal, sem interpolação


def _ok(cost):
    return (
        200,
        {"choices": [{"message": {"content": "# Nota"}}], "usage": {"cost": cost}},
    )


def test_teto_de_custo_pula_pagos(monkeypatch):
    chain = "openrouter:z-ai/glm-5.3-flash:10,openrouter:nvidia/nemotron-3.5-super-vl:free:0;allow_training"
    http = Http(
        {
            "z-ai/glm-5.3-flash": [_ok(0.6), _ok(0.6)],
            "nvidia/nemotron-3.5-super-vl:free": [_ok(0)],
        }
    )
    r = router(chain, http=http, max_cost_usd=1.0)
    assert r.synthesize("s", "u").model == "z-ai/glm-5.3-flash"
    assert r.synthesize("s", "u").model == "z-ai/glm-5.3-flash"  # 0.6 < 1.0
    assert abs(r.spent_usd - 1.2) < 1e-9
    res = r.synthesize("s", "u")  # 1.2 >= 1.0 → pago pulado
    assert res.model == "nvidia/nemotron-3.5-super-vl:free"
    assert {
        "model": "openrouter:z-ai/glm-5.3-flash",
        "try": 0,
        "status": "teto_custo",
    } in res.attempts
    assert http.requests[0]["json"]["usage"] == {"include": True}
    assert http.requests[0]["json"]["provider"]["data_collection"] == "deny"


def test_teto_zero_e_env(monkeypatch):
    monkeypatch.setenv("SYNTH_MAX_COST_USD", "0")
    chain = "openrouter:z-ai/glm-5.3-flash:10,gemini:gemini-2.5-flash:0"
    r = router(chain)
    assert r.max_cost_usd == 0.0
    spec = mr.parse_models(chain)
    assert spec[0].paid and not spec[1].paid
    assert not r.budget_ok(spec[0]) and r.budget_ok(spec[1])
    assert not mr.parse_models("openrouter:x/y:free")[0].paid
    monkeypatch.delenv("SYNTH_MAX_COST_USD")
    assert router(chain).max_cost_usd == 1.0
