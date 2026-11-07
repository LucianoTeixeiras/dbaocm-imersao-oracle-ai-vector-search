"""Módulo de síntese de notas de aula em Markdown via Gemini API."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib import request

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Confirm

from knowledge_extractor.course_config import load_course_config
from knowledge_extractor.errors import PROGRAMMING_ERRORS, describe
from knowledge_extractor.figures import (
    FIGURES_DIRNAME,
    MAX_FIGURES_DEFAULT,
    attach_figures,
    optimize_to_webp,
    select_figures,
)
from knowledge_extractor.mermaid_fixer import sanitize_markdown
from knowledge_extractor.metrics import fmt_duration, track
from knowledge_extractor.video_frames import extract_timestamp_label

# Carrega variáveis de ambiente de .env na raiz do repo ou cwd
load_dotenv()

if sys.platform == "win32":
    # Console legado sem reconfigure (ou stream redirecionado): segue com o encoding padrão.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

console = Console(highlight=False)
log = logging.getLogger(__name__)


def _is_scaffold_file(path: Path) -> bool:
    """Detecta se uma nota existente em aulas/ é apenas um rascunho/scaffold offline."""
    if not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1200]
        return (
            "🟡 Rascunho / Em Estruturação" in head
            or "gerado em modo scaffold offline" in head
        )
    except OSError as exc:
        log.warning("Não foi possível ler %s (%s)", path, exc)
        return False


def _is_transient_error(exc: Exception) -> bool:
    """Identifica falhas transitórias da API (503/429/5xx/timeout) que merecem retry."""
    txt = f"{type(exc).__name__}: {exc}".upper()
    marks = (
        "503",
        "UNAVAILABLE",
        "429",
        "RESOURCE_EXHAUSTED",
        "500",
        "502",
        "504",
        "DEADLINE",
        "TIMEOUT",
        "OVERLOADED",
    )
    return any(m in txt for m in marks)


def build_system_prompt(
    module_name: str = "", has_vision: bool = False, source_language: str | None = None
) -> str:
    """Constrói o system prompt dinâmico injetando metadados do curso e competências do módulo."""
    cfg = load_course_config()
    module_cfg = cfg.get_module(module_name) if module_name else None

    mod_context = ""
    if module_cfg:
        mod_context = (
            f"\nDisciplina Vigente: {module_cfg.name} (Slug: {module_cfg.slug})"
        )
        if module_cfg.summary:
            mod_context += f"\nEmenta Oficial do Módulo: {module_cfg.summary}"
        if module_cfg.competencies:
            mod_context += "\nCompetências Esperadas:\n" + "\n".join(
                f"- {c}" for c in module_cfg.competencies
            )

    vision_directive = ""
    if has_vision:
        vision_directive = """
6. DIRETRIZES DE ANÁLISE VISUAL MULTIMODAL (FRAMES DE VÍDEO / SLIDES / TELAS FORNECIDOS):
   - Você recebeu frames selecionados do vídeo gravado da aula, cada um precedido pelo seu instante no vídeo ([Frame N — MM:SS]).
   - Cruze o instante [MM:SS] do frame com o trecho correspondente da fala para contextualizar o que o instrutor mostrava naquele momento.
   - Diagramas & Arquiteturas em Tela: Se houver diagramas nos slides, Miro/Excalidraw ou telas, reconstrua-os OBRIGATORIAMENTE em diagramas Mermaid válidos, capturando todos os blocos, setas e rótulos exibidos.
   - Código, Markdown & IDE: Se o instrutor exibiu arquivos (código, .md, .yaml, prompts, templates de ADR/FDD) que não foram lidos integralmente em voz alta, transcreva fielmente sua estrutura, seções e trechos-chave na nota.
   - Comandos & Terminal: Capture flags, comandos CLI (git, docker, uv, etc.) e saídas de terminal exibidas em tela.
   - FILTRO DE RUÍDO OBRIGATÓRIO: Ignore completamente elementos da plataforma de ensino/gravação que apareçam nos frames (barras de navegação do portal, listas laterais de capítulos, controles/overlays de player de vídeo, avatares/webcam e popups administrativos ou financeiros). Extraia APENAS o conteúdo didático e técnico da aula.
"""

    from knowledge_extractor.language import language_directive

    translation_directive = language_directive(
        source_language, getattr(cfg, "keep_terms", [])
    )

    from knowledge_extractor.mermaid_theme import prompt_snippet

    mermaid_palette = prompt_snippet()
    return f"""Você é um {cfg.track_role} e Redator Técnico Sênior, responsável pela documentação técnica de nível de Especialização/Pós-Graduação/Treinamento Avançado para o "{cfg.title}" da {cfg.institution}.
{mod_context}

Sua missão é transformar a transcrição bruta da aula em uma APOSTILA TÉCNICA E CADERNO DE ENGENHARIA COMPLETO, EXAUSTIVO E ESTRUTURADO em Markdown.

DIRETRIZES CRÍTICAS:
1. NÃO FAÇA UM RESUMO TELEGRÁFICO OU SUPERFICIAL. Detalhe extensivamente cada conceito explicado, sem pular etapas.
2. Extraia todas as definições conceituais, termos técnicos, jargões e ferramentas mencionadas pelo professor.
3. Se houver comandos de terminal, código em tela ou arquiteturas demonstradas, reconstrua-os fielmente em blocos de código com comentários didáticos.
4. Inclua múltiplos diagramas Mermaid (fluxos de execução, máquinas de estado, sequências ou arquiteturas de componentes).
   REGRAS OBRIGATÓRIAS DE SINTAXE E ACESSIBILIDADE VISUAL (FUNDO CLARO / ALTO CONTRASTE):
   - ACESSIBILIDADE MANDATÓRIA (ASTIGMATISMO / TEMA CLARO): NUNCA gere diagramas com fundo escuro, preenchimentos escuros ou texto claro. Paleta: {mermaid_palette}. Subgraphs devem ter fundo branco (#FFFFFF) e borda azul escuro (#1E3A8A).
   - DELIMITAÇÃO MANDATÓRIA DE BLOCOS MERMAID: TODO diagrama DEVE ser aberto obrigatoriamente com ```mermaid na primeira linha e fechado com ``` na última linha. NUNCA omita a abertura ```mermaid. NUNCA aninhe Mermaid dentro de outro bloco de código.
   - Todo bloco Mermaid DEVE conter a diretiva de inicialização de tema claro logo após a abertura:
```mermaid
%%{{init: {{'theme': 'base', 'themeVariables': {{ 'darkMode': false, 'background': '#FFFFFF', 'mainBkg': '#FFFFFF', 'primaryColor': '#F1F5F9', 'primaryTextColor': '#0F172A', 'primaryBorderColor': '#1E3A8A', 'lineColor': '#1E3A8A', 'textColor': '#0F172A', 'clusterBkg': '#FFFFFF', 'clusterBorder': '#1E3A8A' }}}}%%
flowchart TD
    A["Início"] --> B["Fim"]
```
   - PROIBIÇÃO ABSOLUTA DE DIAGRAMAS ASCII OU TEXTO MONOESPAÇADO: NUNCA crie diagramas com caracteres de desenho de caixa (+---+, ┌───┐, |). TODO e qualquer diagrama ou fluxo arquitetural DEVE ser OBRIGATORIAMENTE um bloco renderizável Mermaid válido (```mermaid ... ```).
   - Qualquer tipo Mermaid é permitido (flowchart, sequenceDiagram, classDiagram, stateDiagram-v2, erDiagram, gantt, pie, mindmap, timeline, journey, gitGraph); prefira flowchart para arquiteturas.
   - Use `flowchart TD` (ou LR). IDs de nós apenas com letras/números, sem espaços nem acentos (ex.: A, B1, llm).
   - TODO rótulo de nó entre aspas duplas: A["Texto (com parênteses)"], B{{"Decisão?"}}, C("Memória / Contexto").
   - Subgraph SEMPRE com id e título entre aspas: subgraph sg1["Processo de Aprendizagem (Kahneman)"] ... end
   - Rótulos de arestas no formato A -->|"texto"| B. Nunca use A -- texto --> B.
   - Nunca use aspas duplas dentro de rótulos; não use ponto e vírgula, HTML ou markdown dentro do diagrama.
5. Se o professor fizer comparações, crie tabelas comparativas.{vision_directive}{translation_directive}

A nota técnica DEVE conter no mínimo 150 a 250 linhas e seguir rigorosamente esta estrutura:

# [Título da Aula no Formato: Aula XX — Nome Descritivo]

> **Programa:** {cfg.title} — {cfg.institution}
> **Módulo:** [Número e Nome do Módulo]
> **Status:** Concluído / Documentado
> **Arquivo Base:** `[Nome do arquivo de transcrição original]`

---

## 🎯 Objetivos de Aprendizagem & Competências
- [Liste de 4 a 6 competências práticas e teóricas dominadas nesta aula, alinhadas à ementa do módulo]

---

## 🧠 Glossário e Definições Conceituais Fundamentais
| Conceito / Termo | Definição no Contexto de IA / Engenharia | Aplicação Prática |
| :--- | :--- | :--- |
| [Termo 1] | [Explicação técnica profunda] | [Onde e como é usado] |
| [Termo 2] | [Explicação técnica profunda] | [Onde e como é usado] |

---

## 🏗️ Arquitetura, Fundamentos Teóricos e Ciclo de Vida
[Aprofunde a teoria discutida. Diferencie paradigmas tradicionais vs orientados a agentes/IA, limites de contexto, determinismo vs inferência, etc.]

### Diagrama Arquitetural & Fluxo de Raciocínio (Mermaid)
```mermaid
[Diagrama Mermaid válido (flowchart TD, sequenceDiagram ou stateDiagram-v2) mapeando detalhadamente as etapas explicadas]
```

---

## 💻 Passo a Passo Técnico, Live-Coding e Implementação
[Reconstrua todo o processo demonstrado em aula pelo instrutor, passo a passo]

### Configuração de Ambiente e Pré-requisitos
- Dependências, pacotes, flags de CLI ou variáveis de ambiente ensinadas.

### Implementação Estruturada de Código
```python
# Código detalhado, tipado e com comentários técnicos explicando as decisões de design
```

---

## ⚖️ Análise de Engenharia: Trade-offs, Decisões e Armadilhas (Gotchas)
- **Vantagens & Benefícios:** [Pontos positivos da abordagem ensinada]
- **Limitações & Desvantagens:** [Quando esta solução falha ou se torna cara]
- **Gotchas / Armadilhas de Produção:** [Erros comuns de runtime, loop infinito em agentes, alucinação de ferramentas, consumo excessivo de tokens, segurança]

---

## 📋 Resumo Executivo & Rastreabilidade de Estudo
- [Síntese executiva dos 3 a 5 pontos centrais]
- **Laboratório Prático Sugerido:** [O que implementar na pasta `labs/` para consolidar o aprendizado]
"""


def _image_label(path: Path) -> str | None:
    """'MM:SS' para frames de vídeo; 'página N' para figuras extraídas de PDF (pNNN-MM.ext)."""
    from knowledge_extractor.pdf_parser import page_of_image

    label = extract_timestamp_label(path)
    if label:
        return label
    page = page_of_image(path)
    return f"página {page}" if page else None


def _image_mime(path: Path) -> str:
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "image/jpeg"


def _read_meta(transcript: Path) -> dict:
    from knowledge_extractor.language import meta_path_for

    mp = meta_path_for(transcript)
    try:
        return json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    except (OSError, ValueError):
        return {}


#: Tamanho máximo (caracteres) de cada parte de PDF enviada ao LLM (env PDF_PART_CHARS).
PDF_PART_CHARS_DEFAULT = 60000

PDF_DOC_HEADER = (
    "[FONTE: DOCUMENTO PDF, não é transcrição de áudio]\n"
    "- Preserve a estrutura do documento (seções, listas, tabelas) na nota.\n"
    "- Cite a origem das afirmações como (p. N), usando os cabeçalhos '## Página N' abaixo.\n"
    "- Ignore cabeçalhos/rodapés repetidos e números de página soltos.\n\n"
)


def _write_note_meta(note: Path, runs: list[dict]) -> None:
    """Grava <nota>.meta.json com provedor, modelo, tentativas, latência e tokens (LRT-07)."""
    ok = [r for r in runs if r.get("provider") != "scaffold"]
    last = (ok or runs)[-1]
    meta = {"synthesis": last}
    if len(runs) > 1:
        meta["calls"] = runs  # PDF em partes: uma entrada por chamada
        meta["tokens_in_total"] = sum(r.get("tokens_in") or 0 for r in ok)
        meta["tokens_out_total"] = sum(r.get("tokens_out") or 0 for r in ok)
    try:
        note.with_suffix(".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        console.print(f"[dim]Aviso: meta da nota não gravado ({e}).[/dim]")


class LectureSynthesizer:
    def __init__(
        self,
        api_key: str | None = None,
        gemini_model: str | None = None,
        ollama_model: str | None = None,
        ollama_url: str | None = None,
        provider: str | None = None,
        deepseek_model: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_base_url: str | None = None,
    ) -> None:
        self.provider = (
            (provider or os.getenv("SYNTHESIS_PROVIDER", "auto")).strip().lower()
        )
        if self.provider not in ("auto", "gemini", "deepseek", "ollama"):
            raise ValueError(
                f"SYNTHESIS_PROVIDER inválido: '{self.provider}' (use auto, gemini, deepseek ou ollama)"
            )
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.gemini_model = gemini_model or os.getenv(
            "GEMINI_MODEL", "gemini-3.5-flash-lite"
        )
        self.ollama_model = ollama_model or os.getenv(
            "OLLAMA_MODEL", "qwen2.5:7b-instruct"
        )
        self.ollama_url = (
            ollama_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.ollama_num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        self.ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))

        # DeepSeek-V3 / OpenAI-compatible Cloud Fallback
        self.deepseek_api_key = (
            deepseek_api_key
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("LLM_API_KEY")
        )
        self.deepseek_base_url = (
            deepseek_base_url
            or os.getenv("DEEPSEEK_BASE_URL")
            or (
                "https://openrouter.ai/api/v1"
                if os.getenv("OPENROUTER_API_KEY") and not os.getenv("DEEPSEEK_API_KEY")
                else "https://api.deepseek.com/v1"
            )
        ).rstrip("/")
        self.deepseek_model = (
            deepseek_model
            or os.getenv("DEEPSEEK_MODEL")
            or (
                "deepseek/deepseek-chat"
                if "openrouter" in self.deepseek_base_url
                else "deepseek-chat"
            )
        )

        self.client = None
        self._ollama_model_checked = False
        self._ollama_model_ready = False

        if self.api_key and not self.api_key.startswith("sua_gemini"):
            try:
                from google import genai

                self.client = genai.Client(api_key=self.api_key)
            except (
                Exception
            ) as e:  # SDK opcional: falha de import/config não impede Ollama/OpenRouter
                if isinstance(e, PROGRAMMING_ERRORS) and not isinstance(e, ImportError):
                    raise
                log.warning("Google GenAI Client não inicializado (%s)", describe(e))
                console.print(
                    f"[yellow]⚠️ Google GenAI Client não inicializado: {describe(e)}[/yellow]"
                )

    def has_gemini_key(self) -> bool:
        """True se há uma GEMINI_API_KEY real (não vazia nem o placeholder do .env.example)."""
        return bool(self.api_key) and not str(self.api_key).startswith("sua_gemini")

    def mode_label(self) -> str:
        if self.provider == "ollama":
            return f"ollama fixo ({self.ollama_model})"
        if self.provider == "deepseek":
            return f"deepseek fixo ({self.deepseek_model})"
        if self.provider == "gemini":
            return f"gemini fixo ({self.gemini_model})"
        return f"auto: 1º {self.gemini_model} → 2º {self.deepseek_model} / SYNTH_MODELS → 3º {self.ollama_model} → scaffold"

    def print_mode(self) -> None:
        console.print(f"[bold]⚙️  Modo de síntese:[/bold] {self.mode_label()}")

    def is_ollama_available(self) -> bool:
        """Verifica se o servidor Ollama está respondendo."""
        try:
            req = request.Request(f"{self.ollama_url}/api/tags", method="GET")
            with request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except (OSError, ValueError) as exc:  # URLError/timeout são OSError
            log.info("Ollama indisponível em %s (%s)", self.ollama_url, exc)
            return False

    def _installed_ollama_models(self) -> list[str]:
        try:
            with request.urlopen(f"{self.ollama_url}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])]
        except (OSError, ValueError) as exc:
            log.warning(
                "Não foi possível listar modelos do Ollama (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return []

    def ensure_ollama_model(self) -> bool:
        """Garante que o modelo local recomendado está instalado; sugere o pull antes de usar LLM.

        Pergunta apenas uma vez por execução. Retorna True se o modelo estiver pronto.
        """
        if self._ollama_model_checked:
            return self._ollama_model_ready
        self._ollama_model_checked = True

        installed = self._installed_ollama_models()
        wanted = self.ollama_model
        if wanted in installed or f"{wanted}:latest" in installed:
            self._ollama_model_ready = True
            return True

        console.print(
            f"[yellow]⚠️  Modelo local recomendado [bold]{wanted}[/bold] não está instalado no Ollama.[/yellow]"
        )
        if installed:
            console.print(f"[dim]Modelos disponíveis: {', '.join(installed)}[/dim]")

        if not sys.stdin.isatty() or not Confirm.ask(
            f"Deseja baixar agora com [cyan]ollama pull {wanted}[/cyan]? (não = usar fallback cloud)",
            default=True,
        ):
            return False

        console.print(f"[cyan]⬇️  Baixando {wanted}...[/cyan]")
        try:
            payload = json.dumps({"model": wanted, "stream": True}).encode("utf-8")
            req = request.Request(
                f"{self.ollama_url}/api/pull",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            last_status = ""
            with request.urlopen(req, timeout=None) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    evt = json.loads(line)
                    if evt.get("error"):
                        raise RuntimeError(evt["error"])
                    status = evt.get("status", "")
                    total, done = evt.get("total"), evt.get("completed")
                    if total and done:
                        console.print(f"\r  {status} {done * 100 // total}%", end="")
                    elif status != last_status:
                        console.print(f"\n  {status}", end="")
                    last_status = status
            console.print(f"\n[bold green]✓ Modelo {wanted} instalado.[/bold green]")
            self._ollama_model_ready = True
        except (OSError, ValueError) as e:
            log.warning("Falha no pull de %s (%s: %s)", wanted, type(e).__name__, e)
            console.print(f"\n[red]❌ Falha no pull de {wanted}: {e}[/red]")
        return self._ollama_model_ready

    def synthesize_with_ollama(
        self,
        transcript_text: str,
        lesson_title: str,
        module_name: str,
        source_filename: str,
    ) -> str:
        """Chama a API do Ollama localmente para síntese aprofundada."""
        console.print(
            f"[cyan]🤖 Invocando SLM Local ({self.ollama_model}) com contexto {self.ollama_num_ctx}...[/cyan]"
        )

        user_content = f"""Módulo: {module_name}
Título da Aula: {lesson_title}
Arquivo de Transcrição: {source_filename}

Abaixo está a transcrição bruta da aula:
---
{transcript_text}
---

Por favor, elabore a apostila técnica completa em Markdown conforme as diretrizes do sistema."""

        sys_prompt = build_system_prompt(
            module_name, source_language=getattr(self, "_source_language", None)
        )
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
            "options": {
                "num_ctx": self.ollama_num_ctx,
                "temperature": 0.2,
            },
            "stream": False,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.ollama_url}/api/chat",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with request.urlopen(req, timeout=self.ollama_timeout) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            msg = result.get("message", {}).get("content", "")
            if not msg:
                raise RuntimeError(
                    f"Ollama retornou resposta vazia para o modelo {self.ollama_model}."
                )
            return msg.strip()

    def _router(self):
        from knowledge_extractor.model_router import ModelRouter

        if getattr(self, "_model_router", None) is None:
            self._model_router = ModelRouter(
                gemini_client=getattr(self, "client", None)
            )
        return self._model_router

    def synthesize_cloud(
        self,
        transcript_text: str,
        lesson_title: str,
        module_name: str,
        source_filename: str,
        keyframes: list[Path] | None = None,
        providers: tuple[str, ...] | None = None,
    ):
        """Gera a nota pela cadeia ponderada SYNTH_MODELS (LRT-07). Retorna SynthResult."""
        from knowledge_extractor.model_router import PROVIDERS

        frames = [k for k in (keyframes or []) if k.exists()]
        tag_vision = f" [Multimodal: {len(frames)} frames visuais]" if frames else ""
        console.print(
            f"[magenta]☁️  Invocando cadeia de modelos cloud{tag_vision}...[/magenta]"
        )

        user_prompt = f"""Módulo: {module_name}
Título da Aula: {lesson_title}
Arquivo de Transcrição: {source_filename}

Abaixo está a transcrição bruta da aula gravada:
---
{transcript_text}
---

Por favor, elabore a nota estruturada completa em Markdown seguindo as diretrizes e o formato solicitado."""
        sys_prompt = build_system_prompt(
            module_name,
            has_vision=bool(frames),
            source_language=getattr(self, "_source_language", None),
        )
        if frames:
            manifest = "\n".join(
                f"[Frame {i:02d}] {_image_label(kf)}"
                if _image_label(kf)
                else f"[Frame {i:02d}]"
                for i, kf in enumerate(frames, start=1)
            )
            user_prompt += (
                "\n\nImagens anexadas em ordem (frames do vídeo ou figuras do PDF):\n"
                + manifest
                + "\n\nCada imagem é precedida pelo rótulo [Frame N — MM:SS] (instante do vídeo) "
                "ou [Frame N — página P] (página do PDF)."
            )
        images = [(_image_label(kf), kf, _image_mime(kf)) for kf in frames]
        return self._router().synthesize(
            sys_prompt, user_prompt, images, providers=providers or PROVIDERS
        )

    def synthesize_with_deepseek(
        self,
        transcript_text: str,
        lesson_title: str,
        module_name: str,
        source_filename: str,
    ) -> str:
        """Chama a API compatível com OpenAI (DeepSeek-V3 / OpenRouter / 9Router) para síntese técnica."""
        if not self.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY ou OPENROUTER_API_KEY não configurada.")

        console.print(
            f"[bold cyan]🚀 Invocando DeepSeek-V3 ({self.deepseek_model}) via {self.deepseek_base_url}...[/bold cyan]"
        )
        user_content = f"""Módulo: {module_name}
Título da Aula: {lesson_title}
Arquivo de Transcrição: {source_filename}

Abaixo está a transcrição bruta da aula:
---
{transcript_text}
---

Por favor, elabore a apostila técnica completa em Markdown conforme as diretrizes do sistema."""

        sys_prompt = build_system_prompt(module_name)
        payload = {
            "model": self.deepseek_model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_tokens": 8192,
            "stream": False,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.deepseek_base_url}/chat/completions",
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.deepseek_api_key}",
            },
            method="POST",
        )

        with request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            choices = result.get("choices", [])
            if not choices:
                raise RuntimeError("Resposta vazia retornada pela API DeepSeek.")
            msg = choices[0].get("message", {}).get("content", "")
            if not msg:
                raise RuntimeError("Conteúdo vazio retornado pelo modelo DeepSeek.")
            return msg.strip()

    def generate_scaffold_note(
        self,
        transcript_text: str,
        lesson_title: str,
        module_name: str,
        source_filename: str,
        reason: str | None = None,
    ) -> str:
        """Gera um scaffold estruturado (sem chave de API ou com a cadeia de IA indisponível).

        O aviso para configurar a ``GEMINI_API_KEY`` só aparece quando a chave está
        de fato ausente; nos demais casos a nota registra o motivo real da falha.
        """
        if not self.has_gemini_key():
            gotcha = "Configure a `GEMINI_API_KEY` para síntese automatizada com IA."
        else:
            gotcha = (
                f"A síntese com IA falhou: {reason or 'motivo desconhecido'}. "
                "Veja o log da síntese e rode novamente com `--force`."
            )
        cfg = load_course_config()
        ctx_inst = f"{cfg.title} — {cfg.institution}"
        module_cfg = cfg.get_module(module_name)
        comps = ""
        if module_cfg and module_cfg.competencies:
            comps = "\n".join(f"- {c}" for c in module_cfg.competencies)
        first_lines = "\n".join(transcript_text.strip().splitlines()[:15])
        return f"""# {lesson_title}

> **Programa:** {ctx_inst}
> **Módulo:** {module_name}
> **Status:** 🟡 Rascunho / Em Estruturação
> **Arquivo Base:** `{source_filename}`

---

## 🎯 Objetivos de Aprendizagem
- Compreender os fundamentos apresentados em `{lesson_title}`.
- Relacionar com as práticas e arquiteturas do módulo `{module_name}`.
{comps or ""}

---

## 🧠 Conceitos Fundamentais e Arquitetura

<!-- SÍNTESE BASEADA NA TRANSCRIÇÃO:
{first_lines}
-->

### Diagrama Arquitetural / Fluxo Conceitual
```mermaid
flowchart TD
    A[Entrada do Usuário] --> B[Raciocínio & Decisão]
    B --> C[Execução de Ação]
    C --> D[Resultado & Aprendizado]
```

---

## 💻 Implementação Prática & Componentes Técnicos
- **Ferramentas abordadas:** Ver transcrição em `materiais/transcricoes/{source_filename}`.
- **Implementações sugeridas:** Criar laboratório prático em `labs/`.

---

## ⚖️ Trade-offs e Gotchas
- **Atenção:** Documento gerado em modo scaffold offline. {gotcha}

---

## 📌 Principais Conclusões
- Nota estruturada pronta para detalhamento técnico.
"""

    def _collect_lab_code_context(self, module_dir: Path) -> str:
        """Coleta trechos de código reais presentes em materiais/raw/ ou materiais/raw/Material/."""
        raw_dir = module_dir / "materiais" / "raw"
        if not raw_dir.is_dir():
            return ""

        code_extensions = {
            ".py",
            ".sql",
            ".ipynb",
            ".sh",
            ".yaml",
            ".yml",
            ".json",
            ".js",
            ".ts",
        }
        code_snippets: list[str] = []
        total_chars = 0

        for p in sorted(raw_dir.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() in code_extensions and "archive" not in p.parts:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace").strip()
                    if text and len(text) > 20:
                        trimmed = text[:4000]  # Limite razoável por arquivo
                        code_snippets.append(
                            f"### Arquivo de Apoio: `{p.name}`\n```{p.suffix.lstrip('.')}\n{trimmed}\n```"
                        )
                        total_chars += len(trimmed)
                        if total_chars > 25000:
                            break
                except (OSError, UnicodeDecodeError):
                    continue

        if not code_snippets:
            return ""

        return (
            "\n\n## CÓDIGO E SCRIPTS REAIS DE LABORATÓRIO FORNECIDOS NO MATERIAL:\n"
            + "\n\n".join(code_snippets)
            + "\n\nDIRETRIZ MANDATÓRIA DE CÓDIGO: Baseie seus blocos de código e comandos de terminal nas rotinas REAIS fornecidas acima, explicando cada etapa com comentários didáticos e sem inventar código fictício.\n"
        )

    def process_file(
        self,
        transcript_file: Path,
        module_dir: Path,
        output_dir: Path | None = None,
        force: bool = False,
        keyframes: list[Path] | None = None,
    ) -> Path | None:
        """Processa um arquivo de transcrição individual gerando a nota em aulas/."""
        if not transcript_file.exists():
            console.print(f"[red]❌ Arquivo não encontrado:[/red] {transcript_file}")
            return None

        stem = transcript_file.stem
        from knowledge_extractor.note_naming import note_filename_for

        target_filename = note_filename_for(transcript_file)
        out_dir = output_dir or (module_dir / "aulas")
        out_dir.mkdir(parents=True, exist_ok=True)
        target_path = out_dir / target_filename

        if target_path.exists() and not force and not _is_scaffold_file(target_path):
            console.print(
                f"[dim]⏭ Pulando, nota já existe: {target_path}[/dim] (use --force para regenerar)"
            )
            return target_path

        had_valid_note = target_path.exists() and not _is_scaffold_file(target_path)
        meta = _read_meta(transcript_file)
        is_pdf = meta.get("source_kind") == "pdf"
        if is_pdf and meta.get("scanned"):
            console.print(
                f"[bold yellow]⚠ {meta.get('source', transcript_file.name)} parece escaneado (sem texto). "
                "Nota não gerada; OCR está no backlog (B-13).[/bold yellow]"
            )
            return None
        if is_pdf and not keyframes:
            from knowledge_extractor.pdf_parser import pdf_max_images

            imgs = [
                (transcript_file.parent / r).resolve() for r in meta.get("images", [])
            ]
            keyframes = [p for p in imgs if p.exists()][: pdf_max_images()] or None
        transcript_text = transcript_file.read_text(encoding="utf-8")
        lab_code_context = self._collect_lab_code_context(module_dir)
        full_context_text = transcript_text + lab_code_context
        clean_title = stem.replace("-", " ").title()

        tag_vision = f" [Visão: {len(keyframes)} frames]" if keyframes else ""
        console.print(
            f"[cyan]📝 Sintetizando nota para:[/cyan] [bold]{target_filename}[/bold]{tag_vision}..."
        )

        with track("sintese", modulo=module_dir.name, item=target_filename) as info:
            info["entrada_chars"] = len(full_context_text)
            if keyframes:
                info["keyframes_count"] = len(keyframes)
            from knowledge_extractor.course_config import load_course_config
            from knowledge_extractor.language import (
                check_ptbr,
                normalize_lang,
                read_source_language,
            )

            src_lang = read_source_language(transcript_file)
            if src_lang is None:
                try:
                    src_lang = normalize_lang(
                        load_course_config(module_dir).audio_language
                    )
                except (OSError, ValueError) as exc:
                    log.warning(
                        "Idioma de origem desconhecido (%s: %s)",
                        type(exc).__name__,
                        exc,
                    )
                    src_lang = None
            self._source_language = src_lang
            self._synth_log = []
            info["idioma_origem"] = src_lang or "desconhecido"
            if is_pdf:
                info["origem"] = "pdf"
                content, provider = self._generate_document(
                    transcript_text,
                    clean_title,
                    module_dir.name,
                    transcript_file.name,
                    info,
                    keyframes=keyframes,
                )
            else:
                content, provider = self._generate(
                    full_context_text,
                    clean_title,
                    module_dir.name,
                    transcript_file.name,
                    info,
                    keyframes=keyframes,
                )
            content, fixed = sanitize_markdown(content)
            if fixed:
                info["mermaid_corrigidos"] = fixed
                console.print(
                    f"[dim]🩹 {fixed} diagrama(s) Mermaid corrigido(s) automaticamente.[/dim]"
                )
            info["provider"] = provider
            if provider != "scaffold":
                alerts = check_ptbr(content)
                if alerts:
                    info["alerta_idioma"] = "; ".join(alerts)
                    console.print(
                        f"[yellow]⚠ A nota pode não estar em pt-BR: {'; '.join(alerts)}[/yellow]"
                    )
            info["saida_chars"] = len(content)
            if provider == "scaffold":
                info["status"] = "scaffold"
                if had_valid_note:
                    console.print(
                        f"[yellow]🛡️ Proteção de conteúdo: mantendo nota completa existente em {target_path.name} (scaffold descartado).[/yellow]"
                    )
                    return target_path
            target_path.write_text(content, encoding="utf-8")
            if self._synth_log:
                _write_note_meta(target_path, self._synth_log)

            # Curadoria e anexação automática de 2-3 figuras-chave em WebP (materiais/figuras/)
            if keyframes and self.client and provider != "scaffold" and not is_pdf:
                fig_count = self.curate_and_attach_figures(
                    note_path=target_path,
                    module_dir=module_dir,
                    lesson_stem=target_path.stem,
                    lesson_title=clean_title,
                    keyframes=keyframes,
                )
                if fig_count:
                    info["figuras_webp"] = fig_count

        console.print(f"[bold green]✓ Arquivo salvo:[/bold green] {target_path}")
        return target_path

    def curate_and_attach_figures(
        self,
        note_path: Path,
        module_dir: Path,
        lesson_stem: str,
        lesson_title: str,
        keyframes: list[Path],
        max_figures: int = MAX_FIGURES_DEFAULT,
    ) -> int:
        """Seleciona os 2-3 frames de alto valor visual, converte para WebP em materiais/figuras/ e anexa na nota."""
        if not self.client or not keyframes:
            return 0
        chosen = select_figures(
            self.client,
            self.gemini_model,
            keyframes,
            lesson_title=lesson_title,
            max_figures=max_figures,
        )
        if not chosen:
            console.print(
                "[dim]ℹ Nenhum frame exigiu inclusão como figura estática (conteúdo 100% coberto por texto/Mermaid).[/dim]"
            )
            return 0

        figures_dir = module_dir / "materiais" / FIGURES_DIRNAME
        figures_dir.mkdir(parents=True, exist_ok=True)

        ready: list[dict] = []
        used_slugs: set[str] = set()
        for fig in chosen:
            slug = fig["slug"]
            if slug in used_slugs:
                slug = f"{slug}-{fig['index']}"
            used_slugs.add(slug)
            fig = {**fig, "slug": slug}
            dst = figures_dir / f"{lesson_stem}--{slug}.webp"
            if optimize_to_webp(fig["path"], dst):
                ready.append(fig)

        if ready and attach_figures(note_path, lesson_stem=lesson_stem, figures=ready):
            console.print(
                f"[bold green]🖼️  {len(ready)} figura(s) WebP anexada(s) em:[/bold green] {figures_dir.relative_to(module_dir)}/"
            )
            return len(ready)
        return 0

    def _generate_document(
        self,
        text: str,
        clean_title: str,
        module_name: str,
        source_filename: str,
        info: dict,
        keyframes: list[Path] | None = None,
    ) -> tuple[str, str]:
        """PDF: uma chamada se couber; senão, uma nota parcial por faixa de páginas + síntese final."""
        from knowledge_extractor.pdf_parser import page_of_image, split_pdf_markdown

        try:
            limit = int(os.getenv("PDF_PART_CHARS", PDF_PART_CHARS_DEFAULT))
        except ValueError:
            limit = PDF_PART_CHARS_DEFAULT
        parts = split_pdf_markdown(text, limit)
        if len(parts) == 1:
            return self._generate(
                PDF_DOC_HEADER + text,
                clean_title,
                module_name,
                source_filename,
                info,
                keyframes=keyframes,
            )

        console.print(
            f"[cyan]📚 PDF grande: {len(parts)} partes + síntese final.[/cyan]"
        )
        info["pdf_partes"] = len(parts)
        partials: list[str] = []
        providers: list[str] = []
        for n, (first, last, chunk) in enumerate(parts, start=1):
            imgs = [
                k for k in (keyframes or []) if first <= (page_of_image(k) or 0) <= last
            ] or None
            header = f"{PDF_DOC_HEADER}[PARTE {n}/{len(parts)} — páginas {first} a {last}]\n\n"
            content, prov = self._generate(
                header + chunk, f"{clean_title} (parte {n}/{len(parts)}, p. {first}-{last})",
                module_name, source_filename, info, keyframes=imgs,
            )  # fmt: skip
            if prov == "scaffold":
                return content, prov
            partials.append(f"<!-- PARTE {n}: páginas {first}-{last} -->\n{content}")
            providers.append(prov)
        final_input = (
            f"{PDF_DOC_HEADER}[SÍNTESE FINAL] Abaixo estão {len(parts)} notas parciais, em ordem, do mesmo PDF. "
            "Unifique-as em UMA nota no formato pedido, sem repetir seções, mantendo as citações (p. N) "
            "e os diagramas relevantes.\n\n" + "\n\n".join(partials)
        )
        return self._generate(
            final_input, clean_title, module_name, source_filename, info
        )

    def _generate(
        self,
        transcript_text: str,
        clean_title: str,
        module_name: str,
        source_filename: str,
        info: dict,
        keyframes: list[Path] | None = None,
    ) -> tuple[str, str]:
        """Executa a cascata 1º Gemini → 2º DeepSeek-V3 → 3º Local SLM (Ollama) → 4º Scaffold."""
        args = (transcript_text, clean_title, module_name, source_filename)
        if not hasattr(self, "_synth_log"):
            self._synth_log = []

        # 1. Cadeia Cloud Ponderada (1º Gemini Cloud → 2º DeepSeek-V3 / OpenRouter, LRT-07)
        last_cloud_error: str | None = None
        if self.provider in ("auto", "gemini", "deepseek"):
            providers = (
                ("gemini",)
                if self.provider == "gemini"
                else (("openrouter",) if self.provider == "deepseek" else None)
            )
            t0 = time.perf_counter()
            try:
                res = self.synthesize_cloud(
                    *args, keyframes=keyframes, providers=providers
                )
                info["cloud_s"] = f"{res.latency_s:.0f}"
                self._synth_log.append(res.meta())
                console.print(
                    f"[bold green]✓ Síntese gerada via {res.provider}:{res.model}![/bold green]"
                )
                return res.content, f"{res.provider}:{res.model}"
            except Exception as e:
                info["cloud_s"] = f"{time.perf_counter() - t0:.0f}"
                info["cloud_erro"] = describe(e)
                last_cloud_error = describe(e)
                if isinstance(e, PROGRAMMING_ERRORS):
                    log.exception("Erro de programação na cadeia cloud")
                    raise
                log.warning("Cadeia cloud falhou (%s)", describe(e))
                self._synth_log.append(
                    {
                        "provider": "scaffold",
                        "error": info["cloud_erro"],
                        "attempts": getattr(e, "attempts", []),
                    }
                )
                if self.provider in ("gemini", "deepseek"):
                    console.print(
                        f"[yellow]⚠️ Provedor cloud {self.provider} falhou ({describe(e)}). Gerando scaffold...[/yellow]"
                    )
                    return self.generate_scaffold_note(
                        *args, reason=f"provedor {self.provider} falhou ({describe(e)})"
                    ), "scaffold"
                console.print(
                    f"[yellow]⚠️ Cadeia cloud falhou ({type(e).__name__}: {e}). Acionando 3ª Opção Local (Ollama)...[/yellow]"
                )

        # 2. 3ª Opção: SLM Local (Ollama)
        if self.provider in ("auto", "ollama"):
            if self.is_ollama_available() and self.ensure_ollama_model():
                t0 = time.perf_counter()
                try:
                    content = self.synthesize_with_ollama(*args)
                    info["ollama_s"] = f"{time.perf_counter() - t0:.0f}"
                    console.print(
                        f"[bold green]✓ Síntese gerada via SLM Local ({self.ollama_model})![/bold green]"
                    )
                    return content, f"ollama:{self.ollama_model}"
                except Exception as e:
                    info["ollama_s"] = f"{time.perf_counter() - t0:.0f}"
                    info["ollama_erro"] = type(e).__name__
                    if isinstance(e, PROGRAMMING_ERRORS):
                        raise
                    console.print(
                        f"[yellow]⚠️ Falha no SLM Local ({e}) após {fmt_duration(time.perf_counter() - t0)}.[/yellow]"
                    )
                    if self.provider == "ollama":
                        return self.generate_scaffold_note(
                            *args, reason=f"Ollama falhou ({describe(e)})"
                        ), "scaffold"
            else:
                if self.provider == "ollama":
                    console.print(
                        f"[yellow]ℹ Servidor Ollama ({self.ollama_url}) não está acessível.[/yellow]"
                    )
                    return self.generate_scaffold_note(
                        *args, reason="Ollama inacessível"
                    ), "scaffold"

        # 3. 4ª Opção: Fallback final Scaffold
        log.warning(
            "Nenhum provedor de IA disponível (provider=%s); gerando scaffold",
            self.provider,
        )
        console.print(
            "[yellow]ℹ Nenhum provedor de IA disponível. Gerando scaffold offline...[/yellow]"
        )
        reason = (
            f"cadeia cloud falhou ({last_cloud_error})"
            if last_cloud_error
            else f"nenhum provedor disponível (provider={self.provider})"
        )
        return self.generate_scaffold_note(*args, reason=reason), "scaffold"
