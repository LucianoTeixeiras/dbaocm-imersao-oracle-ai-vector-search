"""Configuração única do faster-whisper (LRT-04).

Precedência de cada valor: opção da CLI > variável do .env > padrão.
O idioma segue a LRT-01 (CLI > WHISPER_LANGUAGE > course.yaml > auto).

Padrões:
- device=auto      -> cuda se houver GPU (ctranslate2, depois torch), senão cpu.
- compute_type=auto -> float16 na GPU, int8 na CPU.
- model_size padrão -> large-v3 na GPU, small na CPU. O large-v3 em CPU leva
  várias vezes a duração da aula. O small é o melhor equilíbrio entre qualidade
  e tempo na CPU (base erra muito termo técnico). Para mais fidelidade na CPU,
  use WHISPER_MODEL_SIZE=medium (cerca de 2 a 3x mais lento que o small).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

log = logging.getLogger(__name__)

console = Console()

#: Variáveis lidas por este módulo (o teste compara com o .env.example).
ENV_VARS: tuple[str, ...] = (
    "WHISPER_MODEL_SIZE",
    "WHISPER_DEVICE",
    "WHISPER_COMPUTE_TYPE",
    "WHISPER_LANGUAGE",
)
DEPRECATED_ENV: dict[str, str] = {"WHISPER_MODEL": "WHISPER_MODEL_SIZE"}

VALID_MODELS: tuple[str, ...] = (
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large-v1", "large-v2", "large-v3", "large", "large-v3-turbo", "turbo",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
)  # fmt: skip
VALID_DEVICES: tuple[str, ...] = ("auto", "cpu", "cuda")
VALID_COMPUTE: tuple[str, ...] = (
    "auto", "default", "int8", "int8_float32", "int8_float16", "int8_bfloat16",
    "int16", "float16", "bfloat16", "float32",
)  # fmt: skip
GPU_ONLY_COMPUTE: frozenset[str] = frozenset(
    {"float16", "bfloat16", "int8_float16", "int8_bfloat16"}
)
DEFAULT_MODEL_GPU = "large-v3"
DEFAULT_MODEL_CPU = "small"


class WhisperConfigError(ValueError):
    """Valor inválido de configuração do Whisper."""


@dataclass
class WhisperConfig:
    model_size: str
    device: str
    compute_type: str
    language: str | None
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = self.sources
        return (
            f"model={self.model_size} ({s.get('model_size')}) · device={self.device} ({s.get('device')}) · "
            f"compute_type={self.compute_type} ({s.get('compute_type')}) · language={self.language or 'auto'}"
        )


def cuda_available() -> bool:
    """True se houver GPU CUDA utilizável pelo ctranslate2 (fallback: torch)."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except (
        ImportError,
        RuntimeError,
        OSError,
    ) as exc:  # sem ctranslate2 ou sem driver CUDA
        log.debug("ctranslate2 sem CUDA (%s: %s)", type(exc).__name__, exc)
    try:
        import torch

        return bool(torch.cuda.is_available())
    except (ImportError, RuntimeError, OSError) as exc:
        log.debug("torch sem CUDA (%s: %s)", type(exc).__name__, exc)
        return False


def _pick(cli: str | None, env_name: str) -> tuple[str | None, str]:
    if cli not in (None, ""):
        return str(cli).strip(), "cli"
    val = os.getenv(env_name)
    if val not in (None, ""):
        return str(val).strip(), ".env"
    return None, "padrão"


def _model_from_env() -> tuple[str | None, str, list[str]]:
    warnings: list[str] = []
    val = os.getenv("WHISPER_MODEL_SIZE")
    if val:
        return val.strip(), ".env", warnings
    old = os.getenv("WHISPER_MODEL")
    if old:
        warnings.append(
            "WHISPER_MODEL está obsoleta; renomeie para WHISPER_MODEL_SIZE no .env."
        )
        return old.strip(), ".env (WHISPER_MODEL, obsoleta)", warnings
    return None, "padrão", warnings


def resolve_whisper_config(
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    language: str | None = None,
    start_dir: Path | None = None,
    *,
    log: bool = True,
) -> WhisperConfig:
    warnings: list[str] = []
    sources: dict[str, str] = {}

    dev, sources["device"] = _pick(device, "WHISPER_DEVICE")
    dev = (dev or "auto").lower()
    if dev not in VALID_DEVICES:
        raise WhisperConfigError(
            f"WHISPER_DEVICE/--device inválido: '{dev}'. Use: {', '.join(VALID_DEVICES)}."
        )
    has_gpu = cuda_available()
    if dev == "auto":
        dev = "cuda" if has_gpu else "cpu"
        sources["device"] += " → auto"
    elif dev == "cuda" and not has_gpu:
        raise WhisperConfigError(
            "WHISPER_DEVICE=cuda, mas nenhuma GPU CUDA foi encontrada. Use 'auto' ou 'cpu'."
        )

    if model_size not in (None, ""):
        model, sources["model_size"] = str(model_size).strip(), "cli"
    else:
        model, sources["model_size"], w = _model_from_env()
        warnings += w
    if not model:
        model = DEFAULT_MODEL_GPU if dev == "cuda" else DEFAULT_MODEL_CPU
    elif model not in VALID_MODELS and not Path(model).is_dir():
        raise WhisperConfigError(
            f"WHISPER_MODEL_SIZE/--model-size inválido: '{model}'. Use um de: {', '.join(VALID_MODELS)} "
            "(ou o caminho de um modelo CTranslate2 local)."
        )

    ct, sources["compute_type"] = _pick(compute_type, "WHISPER_COMPUTE_TYPE")
    ct = (ct or "auto").lower()
    if ct not in VALID_COMPUTE:
        raise WhisperConfigError(
            f"WHISPER_COMPUTE_TYPE/--compute-type inválido: '{ct}'. Use: {', '.join(VALID_COMPUTE)}."
        )
    if ct in ("auto", "default"):
        ct = "float16" if dev == "cuda" else "int8"
    elif dev == "cpu" and ct in GPU_ONLY_COMPUTE:
        warnings.append(f"compute_type '{ct}' não é suportado em CPU; usando 'int8'.")
        ct = "int8"

    from knowledge_extractor.language import resolve_audio_language

    cfg = WhisperConfig(
        model, dev, ct, resolve_audio_language(language, start_dir), sources, warnings
    )
    if log:
        for w in warnings:
            console.print(f"[yellow]⚠ {w}[/yellow]")
        console.print(f"[cyan][Whisper][/cyan] {cfg.summary()}")
    return cfg
