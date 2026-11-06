"""Diretório de estado do extractor (logs/ e .cache/).

Nunca dentro do pacote instalado: por padrão fica no repo do curso.

Ordem de resolução de :func:`ke_home`:
1. ``KE_HOME`` (variável de ambiente), se definida;
2. o diretório mais próximo que contém ``course.yaml``, subindo a partir do cwd;
3. o próprio cwd.
"""

from __future__ import annotations

import os
from pathlib import Path


def ke_home(start_dir: Path | None = None) -> Path:
    env = os.getenv("KE_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cur = (start_dir or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        if (parent / "course.yaml").is_file():
            return parent
    return cur


def logs_dir() -> Path:
    return ke_home() / "logs"


def cache_dir() -> Path:
    return ke_home() / ".cache"
