"""Rodízio ponderado de modelos de síntese (LRT-07): Gemini + OpenRouter, com circuit breaker.

Formato de SYNTH_MODELS / SYNTH_TEXT_MODELS (separados por vírgula):

    provedor:id-do-modelo[:peso][;flag;flag]

- provedor = 1º campo (``gemini`` ou ``openrouter``); o id pode conter ':' (ex.: ``x/y:free``).
- peso = último campo, se for inteiro >= 0 (padrão 1). Peso 0 = só como último recurso.
  Se o id terminar em ``:<inteiro>``, informe o peso explicitamente.
- flags: ``allow_training`` (OpenRouter: permite endpoints que coletam dados p/ treino),
  ``vision`` / ``text`` (sobrescreve a capacidade). SYNTH_MODELS = multimodal por padrão;
  SYNTH_TEXT_MODELS = só texto (usados apenas sem imagens, ex.: PDF sem figuras).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from knowledge_extractor.errors import PROGRAMMING_ERRORS, describe
from knowledge_extractor.paths import cache_dir

console = Console(highlight=False)
log = logging.getLogger(__name__)

DEFAULT_SYNTH_MODELS = (
    "gemini:gemini-2.5-flash:45,openrouter:z-ai/glm-5.3-flash:25,"
    "openrouter:nvidia/nemotron-3.5-super-vl:free:20;allow_training,"
    "gemini:gemini-3.5-flash-lite:5,openrouter:dots-studio/dots-3-note-preview:free:5,"
    "openrouter:google/gemma-4-31b-it:free:0"
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROVIDERS = ("gemini", "openrouter")
HEALTH_FILE_NAME = "model_health.json"  # em <KE_HOME>/.cache/ (ver paths.ke_home)


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    weight: int = 1
    vision: bool = True
    allow_training: bool = False

    @property
    def paid(self) -> bool:
        """Modelo pago = OpenRouter sem sufixo ':free' (Gemini fica fora do teto)."""
        return self.provider == "openrouter" and not self.model.endswith(":free")

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_models(raw: str, vision_default: bool = True) -> list[ModelSpec]:
    specs: list[ModelSpec] = []
    for entry in (e.strip() for e in (raw or "").split(",")):
        if not entry:
            continue
        head, *flags = [p.strip() for p in entry.split(";")]
        provider, sep, rest = head.partition(":")
        provider = provider.strip().lower()
        if not sep or not rest or provider not in PROVIDERS:
            raise ValueError(
                f"Entrada de modelo inválida: '{entry}' (use provedor:modelo[:peso], provedor ∈ {PROVIDERS})"
            )
        model, weight = rest, 1
        base, _, last = rest.rpartition(":")
        if base and last.isdigit():
            model, weight = base, int(last)
        flagset = {f.lower() for f in flags if f}
        unknown = flagset - {"allow_training", "vision", "text"}
        if unknown:
            raise ValueError(f"Flag desconhecida em '{entry}': {sorted(unknown)}")
        vision = vision_default
        if "vision" in flagset:
            vision = True
        if "text" in flagset:
            vision = False
        specs.append(
            ModelSpec(provider, model, weight, vision, "allow_training" in flagset)
        )
    return specs


def load_chain() -> list[ModelSpec]:
    """Cadeia completa: SYNTH_MODELS (multimodais) + SYNTH_TEXT_MODELS (só texto)."""
    chain = parse_models(
        os.getenv("SYNTH_MODELS") or DEFAULT_SYNTH_MODELS, vision_default=True
    )
    chain += parse_models(os.getenv("SYNTH_TEXT_MODELS", ""), vision_default=False)
    seen: set[str] = set()
    return [s for s in chain if not (s.key in seen or seen.add(s.key))]


def weighted_order(specs: list[ModelSpec], rng: random.Random) -> list[ModelSpec]:
    """Sorteio ponderado sem reposição; peso 0 vai ao fim, na ordem da config."""
    pool = [s for s in specs if s.weight > 0]
    order: list[ModelSpec] = []
    while pool:
        pick = rng.choices(pool, weights=[s.weight for s in pool])[0]
        order.append(pick)
        pool.remove(pick)
    return order + [s for s in specs if s.weight == 0]


class ModelHealth:
    """Circuit breaker: cooldown persistido (429/503 repetidos) + desativação na execução (403/404)."""

    def __init__(
        self,
        path: Path | None = None,
        cooldown_min: float | None = None,
        threshold: int = 2,
    ) -> None:
        self.path = Path(
            path or os.getenv("SYNTH_HEALTH_FILE") or (cache_dir() / HEALTH_FILE_NAME)
        )
        self.cooldown_s = 60 * float(
            cooldown_min
            if cooldown_min is not None
            else os.getenv("SYNTH_COOLDOWN_MIN", "10")
        )
        self.threshold = threshold
        self.disabled: dict[str, str] = {}
        self.state: dict[str, dict] = {}
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        except OSError:
            pass

    def available(self, key: str, now: float | None = None) -> bool:
        if key in self.disabled:
            return False
        return float(self.state.get(key, {}).get("cooldown_until", 0)) <= (
            now or time.time()
        )

    def record_transient(self, key: str, now: float | None = None) -> bool:
        """Conta falha transitória; retorna True se o modelo entrou em cooldown."""
        now = now or time.time()
        st = self.state.setdefault(key, {})
        st["failures"] = int(st.get("failures", 0)) + 1
        tripped = st["failures"] >= self.threshold
        if tripped:
            st["cooldown_until"] = now + self.cooldown_s
            st["failures"] = 0
        self._save()
        return tripped

    def record_success(self, key: str) -> None:
        if key in self.state:
            self.state.pop(key)
            self._save()

    def disable(self, key: str, reason: str) -> None:
        self.disabled[key] = reason


class ModelCallError(Exception):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"{status or 'erro'}: {message}")
        self.status = status
        self.message = message


@dataclass
class SynthResult:
    content: str
    provider: str
    model: str
    attempts: list[dict] = field(default_factory=list)
    latency_s: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None

    def meta(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "latency_s": round(self.latency_s, 2),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


def _status_of(exc: Exception) -> int | None:
    for attr in ("status", "code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    txt = str(exc).upper()
    for code, marks in ((429, ("429", "RESOURCE_EXHAUSTED")), (503, ("503", "UNAVAILABLE", "OVERLOADED")),
                        (404, ("404", "NOT_FOUND")), (403, ("403", "PERMISSION_DENIED"))):  # fmt: skip
        if any(m in txt for m in marks):
            return code
    return None


def _is_timeout(exc: Exception) -> bool:
    return (
        "timeout" in type(exc).__name__.lower()
        or "DEADLINE" in str(exc).upper()
        or "TIMED OUT" in str(exc).upper()
    )


def _policy_message(spec: ModelSpec, status: int, msg: str) -> str:
    low = msg.lower()
    if (
        spec.provider == "openrouter"
        and status == 404
        and ("data policy" in low or "training" in low)
    ):
        return (
            f"{spec.key}: nenhum endpoint aceita data_collection=deny (o provedor exige coletar dados p/ treino). "
            "Para usá-lo, libere a coleta em openrouter.ai/settings/privacy E marque o modelo com ';allow_training' "
            "em SYNTH_MODELS — ciente de que o conteúdo do curso poderá ser retido. Modelo desativado nesta execução."
        )
    return f"{spec.key}: HTTP {status} ({msg[:200]}). Modelo desativado nesta execução."


# ---------------------------------------------------------------- provedores

Images = list[tuple[str | None, Path, str]]  # (rótulo, caminho, mime)


def call_gemini(
    client: Any,
    spec: ModelSpec,
    sys_prompt: str,
    user_text: str,
    images: Images,
    timeout: float,
) -> tuple[str, int | None, int | None]:
    if client is None:
        raise ModelCallError(None, "GEMINI_API_KEY ausente")
    from google.genai import types

    parts: list[Any] = [types.Part.from_text(text=f"{sys_prompt}\n\n{user_text}")]
    for i, (label, path, mime) in enumerate(images, start=1):
        if label:
            parts.append(types.Part.from_text(text=f"[Frame {i:02d} — {label}]"))
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime))
    resp = client.models.generate_content(
        model=spec.model, contents=[types.Content(role="user", parts=parts)]
    )
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise ModelCallError(None, "resposta vazia")
    um = getattr(resp, "usage_metadata", None)
    return (
        text,
        getattr(um, "prompt_token_count", None),
        getattr(um, "candidates_token_count", None),
    )


def openrouter_payload(
    spec: ModelSpec, sys_prompt: str, user_text: str, images: Images
) -> dict:
    content: list[dict] = [{"type": "text", "text": user_text}]
    for i, (label, path, mime) in enumerate(images, start=1):
        if label:
            content.append({"type": "text", "text": f"[Frame {i:02d} — {label}]"})
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    return {
        "model": spec.model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.3,
        # Privacidade: nunca liberar coleta implicitamente.
        "provider": {"data_collection": "allow" if spec.allow_training else "deny"},
        # Pede o custo real (usage.cost, US$) para o teto SYNTH_MAX_COST_USD.
        "usage": {"include": True},
    }


def call_openrouter(
    http: Any,
    spec: ModelSpec,
    sys_prompt: str,
    user_text: str,
    images: Images,
    timeout: float,
    cost_sink: list[float] | None = None,
) -> tuple[str, int | None, int | None]:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise ModelCallError(None, "OPENROUTER_API_KEY ausente")
    import httpx

    payload = openrouter_payload(spec, sys_prompt, user_text, images)
    headers = {"Authorization": f"Bearer {key}", "X-Title": "knowledge-extractor"}
    client = http or httpx.Client()
    try:
        r = client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
    finally:
        if http is None:
            client.close()
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "") or r.text
        except ValueError:
            msg = r.text
        raise ModelCallError(
            r.status_code, msg
        )  # só a mensagem do servidor; nunca headers/chave
    data = r.json()
    if data.get("error"):
        err = data["error"]
        raise ModelCallError(
            err.get("code") if isinstance(err.get("code"), int) else None,
            str(err.get("message", err)),
        )
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise ModelCallError(None, "resposta vazia")
    usage = data.get("usage") or {}
    if cost_sink is not None:
        try:
            cost_sink.append(float(usage.get("cost") or 0.0))
        except (TypeError, ValueError):
            cost_sink.append(0.0)
    return text.strip(), usage.get("prompt_tokens"), usage.get("completion_tokens")


# ---------------------------------------------------------------- roteador


class ModelRouter:
    def __init__(
        self,
        chain: list[ModelSpec] | None = None,
        gemini_client: Any = None,
        http: Any = None,
        health: ModelHealth | None = None,
        seed: int | str | None = None,
        retries: int | None = None,
        backoff_s: float | None = None,
        timeout_s: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_cost_usd: float | None = None,
    ) -> None:
        self.chain = chain if chain is not None else load_chain()
        self.gemini_client = gemini_client
        self.http = http
        self.health = health or ModelHealth()
        seed = seed if seed is not None else os.getenv("SYNTH_SEED") or None
        self.rng = random.Random(
            int(seed) if isinstance(seed, str) and seed.isdigit() else seed
        )
        self.retries = (
            retries if retries is not None else int(os.getenv("SYNTH_RETRIES", "2"))
        )
        self.backoff_s = (
            backoff_s
            if backoff_s is not None
            else float(os.getenv("SYNTH_BACKOFF_S", "3"))
        )
        self.timeout_s = (
            timeout_s
            if timeout_s is not None
            else float(os.getenv("SYNTH_TIMEOUT_S", "300"))
        )
        self.sleep = sleep
        self.max_cost_usd = (
            max_cost_usd
            if max_cost_usd is not None
            else float(os.getenv("SYNTH_MAX_COST_USD") or "1.00")
        )
        self._costs: list[float] = []

    @property
    def spent_usd(self) -> float:
        """Gasto acumulado com modelos do OpenRouter nesta execução (usage.cost)."""
        return sum(self._costs)

    def budget_ok(self, spec: ModelSpec) -> bool:
        return not spec.paid or self.spent_usd < self.max_cost_usd

    def candidates(
        self, needs_vision: bool, providers: tuple[str, ...] = PROVIDERS
    ) -> list[ModelSpec]:
        pool = [
            s
            for s in self.chain
            if s.provider in providers and (s.vision or not needs_vision)
        ]
        return [
            s for s in weighted_order(pool, self.rng) if self.health.available(s.key)
        ]

    def _call(self, spec: ModelSpec, sys_prompt: str, user_text: str, images: Images):
        if spec.provider == "gemini":
            return call_gemini(
                self.gemini_client, spec, sys_prompt, user_text, images, self.timeout_s
            )
        return call_openrouter(
            self.http, spec, sys_prompt, user_text, images, self.timeout_s, self._costs
        )

    def synthesize(
        self,
        sys_prompt: str,
        user_text: str,
        images: Images | None = None,
        providers: tuple[str, ...] = PROVIDERS,
    ) -> SynthResult:
        images = images or []
        order = self.candidates(bool(images), providers)
        if not order:
            raise RuntimeError(
                "Nenhum modelo disponível na cadeia (filtro de visão, cooldown ou desativados)."
            )
        attempts: list[dict] = []
        t_all = time.perf_counter()
        for spec in order:
            if not self.health.available(spec.key):
                continue
            if not self.budget_ok(spec):
                console.print(
                    f"[yellow]💰 {spec.key} pulado: teto SYNTH_MAX_COST_USD="
                    f"{self.max_cost_usd:.2f} atingido (gasto {self.spent_usd:.4f}).[/yellow]"
                )
                attempts.append({"model": spec.key, "try": 0, "status": "teto_custo"})
                continue
            for n in range(1, self.retries + 1):
                t0 = time.perf_counter()
                rec = {"model": spec.key, "try": n}
                try:
                    text, tin, tout = self._call(spec, sys_prompt, user_text, images)
                except Exception as exc:  # SDKs/HTTP de provedores lançam tipos variados; tudo é logado e vira tentativa
                    if isinstance(exc, PROGRAMMING_ERRORS):
                        log.error(
                            "Erro de programação ao chamar %s: %s",
                            spec.key,
                            describe(exc),
                        )
                        raise
                    log.warning(
                        "%s tentativa %d falhou: %s", spec.key, n, describe(exc)
                    )
                    status = (
                        getattr(exc, "status", None)
                        if isinstance(exc, ModelCallError)
                        else _status_of(exc)
                    )
                    rec.update(status=status or ("timeout" if _is_timeout(exc) else "erro"),
                               latency_s=round(time.perf_counter() - t0, 2))  # fmt: skip
                    attempts.append(rec)
                    msg = getattr(exc, "message", str(exc))
                    if status in (403, 404):
                        reason = _policy_message(spec, status, msg)
                        self.health.disable(spec.key, reason)
                        console.print(f"[bold yellow]⛔ {reason}[/bold yellow]")
                        break
                    if status in (429, 503) or _is_timeout(exc):
                        if n < self.retries:
                            console.print(
                                f"[yellow]⏳ {spec.key}: {rec['status']} (tentativa {n}/{self.retries}) — aguardando {self.backoff_s * n:.0f}s[/yellow]"
                            )
                            self.sleep(self.backoff_s * n)
                            continue
                        if self.health.record_transient(spec.key):
                            console.print(
                                f"[yellow]🧊 {spec.key} em cooldown por {self.health.cooldown_s / 60:.0f} min.[/yellow]"
                            )
                        break
                    console.print(
                        f"[yellow]⚠ {spec.key} falhou ({type(exc).__name__}: {msg[:200]}).[/yellow]"
                    )
                    break
                rec.update(status="ok", latency_s=round(time.perf_counter() - t0, 2))
                attempts.append(rec)
                self.health.record_success(spec.key)
                return SynthResult(
                    text,
                    spec.provider,
                    spec.model,
                    attempts,
                    time.perf_counter() - t_all,
                    tin,
                    tout,
                )
            console.print("[yellow]🔄 Próximo modelo da cadeia...[/yellow]")
        err = RuntimeError(
            "Todos os modelos da cadeia falharam: "
            + ", ".join(f"{a['model']}={a['status']}" for a in attempts)
        )
        err.attempts = attempts  # type: ignore[attr-defined]
        raise err
