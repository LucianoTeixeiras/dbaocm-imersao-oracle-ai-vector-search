"""Estado (logs/.cache) fica no repo do curso ou em KE_HOME — nunca no pacote instalado."""

from __future__ import annotations

from pathlib import Path

import knowledge_extractor
from knowledge_extractor import metrics, model_router, paths

PKG = Path(knowledge_extractor.__file__).resolve().parent


def test_sobe_ate_course_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("KE_HOME", raising=False)
    (tmp_path / "course.yaml").write_text("course: {}\n")
    sub = tmp_path / "modules" / "m1"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert paths.ke_home() == tmp_path.resolve()
    assert paths.logs_dir() == tmp_path.resolve() / "logs"
    assert paths.cache_dir() == tmp_path.resolve() / ".cache"


def test_sem_course_yaml_usa_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("KE_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.ke_home() == tmp_path.resolve()


def test_ke_home_tem_prioridade(tmp_path, monkeypatch):
    (tmp_path / "course.yaml").write_text("course: {}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KE_HOME", str(tmp_path / "estado"))
    assert paths.ke_home() == (tmp_path / "estado").resolve()


def test_metricas_e_health_fora_do_pacote(tmp_path, monkeypatch):
    for v in ("KE_HOME", "METRICS_LOG", "SYNTH_HEALTH_FILE"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / "course.yaml").write_text("course: {}\n")
    monkeypatch.chdir(tmp_path)
    log = metrics._log_path()
    health = model_router.ModelHealth().path
    assert log == tmp_path.resolve() / "logs" / "pipeline-metrics.csv"
    assert health == tmp_path.resolve() / ".cache" / "model_health.json"
    for p in (log, health):
        assert PKG not in p.parents


def test_overrides_especificos_continuam(tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_LOG", str(tmp_path / "m.csv"))
    monkeypatch.setenv("SYNTH_HEALTH_FILE", str(tmp_path / "h.json"))
    assert metrics._log_path() == tmp_path / "m.csv"
    assert model_router.ModelHealth().path == tmp_path / "h.json"
