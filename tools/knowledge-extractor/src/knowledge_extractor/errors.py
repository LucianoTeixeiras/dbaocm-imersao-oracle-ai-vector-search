"""Classificação de exceções compartilhada pela esteira.

A esteira é resiliente a falhas *de ambiente* (rede, cota, ffmpeg, arquivo
corrompido), mas erros *de programação* nunca devem virar fallback silencioso
(ex.: uma nota scaffold no lugar de um traceback). Quem captura ``Exception``
de forma ampla deve re-lançar :data:`PROGRAMMING_ERRORS`.
"""

from __future__ import annotations

PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (
    NameError,  # inclui UnboundLocalError
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    AssertionError,
    SyntaxError,
    NotImplementedError,
)


def describe(exc: BaseException) -> str:
    """'Tipo: mensagem' curto para logs (nunca inclui headers/chaves)."""
    return f"{type(exc).__name__}: {exc}"[:300]
