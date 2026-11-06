"""Idioma de origem (para transcrever/traduzir) e garantia de saída SEMPRE em pt-BR.

Regra fixa: toda nota/resumo/rótulo de diagrama é escrita em português do Brasil.
O idioma detectado serve só para (1) o Whisper transcrever no idioma certo e
(2) informar ao LLM de qual idioma traduzir.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

AUTO = None  # None = detecção automática pelo Whisper


def normalize_lang(value: object) -> str | None:
    """'auto'/''/None -> None; 'en-US' -> 'en'; 'PT_br' -> 'pt'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s == "auto":
        return None
    return re.split(r"[-_]", s)[0] or None


def resolve_audio_language(
    cli_value: str | None = None, start_dir: Path | None = None
) -> str | None:
    """Precedência: CLI > WHISPER_LANGUAGE > course.yaml (audio_language) > auto (None)."""
    for cand in (cli_value, os.getenv("WHISPER_LANGUAGE")):
        if cand is not None and str(cand).strip():
            return normalize_lang(cand)  # 'auto' explícito também encerra a busca
    try:
        from knowledge_extractor.course_config import load_course_config

        return normalize_lang(load_course_config(start_dir).audio_language)
    except (OSError, ValueError) as exc:
        log.warning(
            "Idioma do áudio não lido do course.yaml (%s: %s)", type(exc).__name__, exc
        )
        return None


# ---------- sidecar .meta.json ----------
def meta_path_for(transcript: Path) -> Path:
    return transcript.with_name(f"{transcript.stem}.meta.json")


def write_meta(transcript: Path, data: dict) -> Path:
    p = meta_path_for(transcript)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_source_language(transcript: Path) -> str | None:
    p = meta_path_for(transcript)
    if p.exists():
        try:
            return normalize_lang(
                json.loads(p.read_text(encoding="utf-8")).get("language")
            )
        except (ValueError, OSError):
            return None
    return None


def dominant_language(chunks: list[tuple[str | None, float]]) -> str | None:
    """Idioma predominante ponderado pela duração de cada chunk."""
    acc: Counter[str] = Counter()
    for lang, dur in chunks:
        if lang:
            acc[lang] += max(dur, 0.0) or 1.0
    return acc.most_common(1)[0][0] if acc else None


# ---------- diretiva de idioma para o prompt ----------
def language_directive(
    source_language: str | None, keep_terms: list[str] | None = None
) -> str:
    terms = ", ".join(keep_terms or [])
    keep = f"   - Glossário do curso (NUNCA traduzir): {terms}.\n" if terms else ""
    common = (
        "   - Preserve sem tradução: nomes de produtos/bibliotecas/APIs, identificadores, comandos, código e saídas de terminal.\n"
        f"{keep}"
        "   - Não invente palavras nem calques fonéticos; na dúvida, mantenha o termo original entre crases.\n"
        "   - Use português do BRASIL (não de Portugal): 'usuário' (não 'utilizador'), 'tela' (não 'ecrã'), 'arquivo' (não 'ficheiro'), "
        "'fato' (não 'facto'), gerúndio ('está fazendo', não 'está a fazer').\n"
    )
    if source_language in (None, "pt"):
        origin = (
            "português (ou não identificado)"
            if source_language is None
            else "português"
        )
        head = (
            f"\n7. IDIOMA DA NOTA: SEMPRE PORTUGUÊS DO BRASIL (origem: {origin}).\n"
            "   - Não traduza; apenas normalize para pt-BR culto e técnico, corrigindo erros de transcrição.\n"
        )
    else:
        head = (
            f"\n7. IDIOMA DA NOTA: SEMPRE PORTUGUÊS DO BRASIL. A origem está em '{source_language}': TRADUZA tudo "
            "(texto, títulos, tabelas e rótulos de diagramas Mermaid) para pt-BR técnico fluente.\n"
        )
    return head + common


# ---------- validação pós-síntese (offline, heurística) ----------
_WORD = re.compile(r"[a-zà-ú]+", re.IGNORECASE)
_PT = {
    "de",
    "que",
    "não",
    "para",
    "com",
    "uma",
    "os",
    "as",
    "do",
    "da",
    "dos",
    "das",
    "em",
    "no",
    "na",
    "é",
    "ao",
    "se",
    "mais",
    "como",
    "mas",
    "também",
    "são",
    "pelo",
    "pela",
    "isso",
    "este",
    "esta",
    "você",
}
_EN = {
    "the",
    "and",
    "of",
    "to",
    "is",
    "that",
    "for",
    "with",
    "this",
    "are",
    "as",
    "be",
    "it",
    "on",
    "by",
    "from",
    "which",
    "you",
    "can",
    "we",
    "not",
    "or",
    "an",
}
_ES = {
    "el",
    "los",
    "las",
    "del",
    "que",
    "por",
    "para",
    "una",
    "con",
    "es",
    "y",
    "se",
    "lo",
    "como",
    "más",
    "pero",
    "también",
    "son",
    "este",
    "esta",
    "usted",
}
_PT_PT = re.compile(
    r"\b(utilizador(es)?|ecrã|ficheiro(s)?|facto|est(á|ão|ás) a \w+r)\b", re.IGNORECASE
)


def _strip_code(md: str) -> str:
    md = re.sub(r"```.*?```", " ", md, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", " ", md)


def check_ptbr(markdown: str) -> list[str]:
    """Lista de alertas (vazia = parece pt-BR). Ignora blocos de código."""
    words = [w.lower() for w in _WORD.findall(_strip_code(markdown))]
    if len(words) < 30:
        return []
    pt = sum(w in _PT for w in words)
    en = sum(w in _EN for w in words)
    es = sum(w in _ES and w not in _PT for w in words)
    alerts: list[str] = []
    if en > pt:
        alerts.append(f"texto parece inglês (pt={pt}, en={en})")
    elif es > pt:
        alerts.append(f"texto parece espanhol (pt={pt}, es={es})")
    elif pt / len(words) < 0.08:
        alerts.append(f"poucas palavras funcionais do português ({pt}/{len(words)})")
    pp = sorted({m.group(0).lower() for m in _PT_PT.finditer(markdown)})
    if pp:
        alerts.append("marcas de pt-PT: " + ", ".join(pp))
    return alerts


def detect_text_language(text: str) -> tuple[str | None, float]:
    """Detecção offline (pt/en/es) por palavras funcionais. Retorna (idioma, confiança 0-1).

    Sem dependência nova: é a mesma heurística do check_ptbr. Texto curto (< 30 palavras) -> (None, 0.0).
    """
    words = [w.lower() for w in _WORD.findall(_strip_code(text))]
    if len(words) < 30:
        return None, 0.0
    scores = {
        "pt": sum(w in _PT for w in words),
        "en": sum(w in _EN for w in words),
        "es": sum(w in _ES and w not in _PT for w in words),
    }
    total = sum(scores.values())
    if not total:
        return None, 0.0
    lang = max(scores, key=lambda k: scores[k])
    return lang, round(scores[lang] / total, 3)
