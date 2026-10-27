"""Controle de início/fim e tempo de processamento das atividades do pipeline."""

from __future__ import annotations

import csv
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console

console = Console(highlight=False)

# Padrão: tools/knowledge-extractor/logs/pipeline-metrics.csv (sobrescrevível via env METRICS_LOG)
DEFAULT_LOG = Path(__file__).resolve().parents[2] / "logs" / "pipeline-metrics.csv"
FIELDS = ["inicio", "fim", "duracao_s", "duracao", "status", "modulo", "atividade", "item", "detalhes"]


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _log_path() -> Path:
    return Path(os.getenv("METRICS_LOG") or DEFAULT_LOG)


def _append(row: dict[str, Any]) -> None:
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:  # métricas nunca devem quebrar o pipeline
        console.print(f"[dim]⚠️ Não foi possível gravar métricas: {e}[/dim]")


@contextmanager
def track(atividade: str, modulo: str = "", item: str = "", quiet: bool = False) -> Iterator[dict[str, Any]]:
    """Mede uma atividade: imprime início/fim e grava uma linha no CSV de métricas.

    O dict retornado aceita detalhes extras (ex.: info["provider"] = "ollama")
    e info["status"] para sobrescrever o status final (ex.: "skip").
    """
    info: dict[str, Any] = {}
    inicio = datetime.now()
    t0 = time.perf_counter()
    label = f"{atividade}" + (f" — {item}" if item else "")
    if not quiet:
        console.print(f"[dim]⏱  Início {inicio:%H:%M:%S} · {label}[/dim]")

    status = "ok"
    try:
        yield info
    except BaseException as e:
        status = "interrompido" if isinstance(e, KeyboardInterrupt) else f"erro: {type(e).__name__}"
        raise
    finally:
        dur = time.perf_counter() - t0
        fim = datetime.now()
        status = info.pop("status", status)
        if not quiet or status != "ok":
            color = "green" if status == "ok" else "yellow"
            console.print(f"[{color}]⏱  Fim {fim:%H:%M:%S} · {label} · {fmt_duration(dur)} ({status})[/{color}]")
        _append({
            "inicio": inicio.isoformat(timespec="seconds"),
            "fim": fim.isoformat(timespec="seconds"),
            "duracao_s": f"{dur:.1f}",
            "duracao": fmt_duration(dur),
            "status": status,
            "modulo": modulo,
            "atividade": atividade,
            "item": item,
            "detalhes": " ".join(f"{k}={v}" for k, v in info.items()),
        })
