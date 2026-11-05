"""Módulo de síntese de notas de aula em Markdown via Gemini API."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Confirm

from knowledge_extractor.course_config import load_course_config
from knowledge_extractor.figures import (
    FIGURES_DIRNAME,
    MAX_FIGURES_DEFAULT,
    attach_figures,
    optimize_to_webp,
    select_figures,
)
from knowledge_extractor.metrics import fmt_duration, track
from knowledge_extractor.mermaid_fixer import sanitize_markdown
from knowledge_extractor.video_frames import extract_timestamp_label

# Carrega variáveis de ambiente de .env na raiz do repo ou cwd
load_dotenv()

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


def _is_scaffold_file(path: Path) -> bool:
    """Detecta se uma nota existente em aulas/ é apenas um rascunho/scaffold offline."""
    if not path.exists():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1200]
        return "🟡 Rascunho / Em Estruturação" in head or "gerado em modo scaffold offline" in head
    except Exception:
        return False


def _is_transient_error(exc: Exception) -> bool:
    """Identifica falhas transitórias da API (503/429/5xx/timeout) que merecem retry."""
    txt = f"{type(exc).__name__}: {exc}".upper()
    marks = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "502", "504", "DEADLINE", "TIMEOUT", "OVERLOADED")
    return any(m in txt for m in marks)

def build_system_prompt(module_name: str = "", has_vision: bool = False) -> str:
    """Constrói o system prompt dinâmico injetando metadados do curso e competências do módulo."""
    cfg = load_course_config()
    module_cfg = cfg.get_module(module_name) if module_name else None

    mod_context = ""
    if module_cfg:
        mod_context = f"\nDisciplina Vigente: {module_cfg.name} (Slug: {module_cfg.slug})"
        if module_cfg.summary:
            mod_context += f"\nEmenta Oficial do Módulo: {module_cfg.summary}"
        if module_cfg.competencies:
            mod_context += "\nCompetências Esperadas:\n" + "\n".join(f"- {c}" for c in module_cfg.competencies)

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

    translation_directive = ""
    is_foreign_audio = (cfg.audio_language and cfg.audio_language.lower() != "pt")
    if is_foreign_audio:
        translation_directive = f"""
7. DIRETRIZES DE TRADUÇÃO TÉCNICA E IDIOMA (Áudio original em {cfg.audio_language.upper()}):
   - A transcrição fornecida está no idioma original ({cfg.audio_language.upper()}).
   - Elabore a nota técnica inteiramente em PORTUGUÊS DO BRASIL fluente, formal e técnico de nível de Engenharia/Pós-Graduação.
   - PRESERVAÇÃO DE TERMOS DA INDÚSTRIA: NUNCA faça traduções literais grotescas de termos técnicos consagrados. Mantenha em inglês termos como: LangChain, LangGraph, StateGraph, checkpointer, human-in-the-loop, prompts, prompt engineering, few-shot, zero-shot, tool calling, memory, thread, streaming, fallback, router, LLM, RAG, agentic workflows, etc.
   - NUNCA invente palavras ou calques (como 'prontes', 'Lengraff', 'All-On', 'taxas' para tasks, 'automatear').
   - O texto deve soar como escrito por um Engenheiro de Software Sênior brasileiro especialista no assunto, integrando didática profunda com rigor conceitual.
"""

    return f"""Você é um {cfg.track_role} e Redator Técnico Sênior, responsável pela documentação técnica de nível de Especialização/Pós-Graduação/Treinamento Avançado para o "{cfg.title}" da {cfg.institution}.
{mod_context}

Sua missão é transformar a transcrição bruta da aula em uma APOSTILA TÉCNICA E CADERNO DE ENGENHARIA COMPLETO, EXAUSTIVO E ESTRUTURADO em Markdown.

DIRETRIZES CRÍTICAS:
1. NÃO FAÇA UM RESUMO TELEGRÁFICO OU SUPERFICIAL. Detalhe extensivamente cada conceito explicado, sem pular etapas.
2. Extraia todas as definições conceituais, termos técnicos, jargões e ferramentas mencionadas pelo professor.
3. Se houver comandos de terminal, código em tela ou arquiteturas demonstradas, reconstrua-os fielmente em blocos de código com comentários didáticos.
4. Inclua múltiplos diagramas Mermaid (fluxos de execução, máquinas de estado, sequências ou arquiteturas de componentes).
   REGRAS OBRIGATÓRIAS DE SINTAXE E ACESSIBILIDADE VISUAL (FUNDO CLARO / ALTO CONTRASTE):
   - ACESSIBILIDADE MANDATÓRIA (ASTIGMATISMO / TEMA CLARO): NUNCA gere diagramas com fundo escuro, preenchimentos pretos ou nós escuros. Toda diretiva de estilo deve usar fundo claro (#FFFFFF), caixas em tons pastéis claros e bordas/textos em alto contraste escuro (#0F172A, #1E3A8A). Subgraphs devem ter fundo branco (#FFFFFF) e borda azul escuro (#1E3A8A).
   - PROIBIÇÃO ABSOLUTA DE DIAGRAMAS ASCII OU TEXTO MONOESPAÇADO: NUNCA crie diagramas com caracteres de desenho de caixa (+---+, ┌───┐, |). TODO e qualquer diagrama ou fluxo arquitetural DEVE ser OBRIGATORIAMENTE um bloco renderizável Mermaid válido (```mermaid ... ```).
   - Inicie OBRIGATORIAMENTE todo bloco Mermaid com a diretiva:
     %%{{init: {{'theme': 'base', 'themeVariables': {{ 'darkMode': false, 'background': '#FFFFFF', 'mainBkg': '#FFFFFF', 'primaryColor': '#F1F5F9', 'primaryTextColor': '#0F172A', 'primaryBorderColor': '#1E3A8A', 'lineColor': '#1E3A8A', 'textColor': '#0F172A', 'clusterBkg': '#FFFFFF', 'clusterBorder': '#1E3A8A' }}}}%%
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
        self.provider = (provider or os.getenv("SYNTHESIS_PROVIDER", "auto")).strip().lower()
        if self.provider not in ("auto", "gemini", "deepseek", "ollama"):
            raise ValueError(f"SYNTHESIS_PROVIDER inválido: '{self.provider}' (use auto, gemini, deepseek ou ollama)")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.gemini_model = gemini_model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.ollama_url = (ollama_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
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
            except Exception as e:
                console.print(f"[dim]Google GenAI Client não inicializado: {e}[/dim]")

    def mode_label(self) -> str:
        if self.provider == "ollama":
            return f"ollama fixo ({self.ollama_model})"
        if self.provider == "gemini":
            return f"gemini fixo ({self.gemini_model})"
        if self.provider == "deepseek":
            return f"deepseek fixo ({self.deepseek_model})"
        return f"auto: 1º {self.gemini_model} → 2º {self.deepseek_model} → 3º {self.ollama_model} → scaffold"

    def print_mode(self) -> None:
        console.print(f"[bold]⚙️  Modo de síntese:[/bold] {self.mode_label()}")

    def is_ollama_available(self) -> bool:
        """Verifica se o servidor Ollama está respondendo."""
        try:
            req = request.Request(f"{self.ollama_url}/api/tags", method="GET")
            with request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _installed_ollama_models(self) -> list[str]:
        try:
            with request.urlopen(f"{self.ollama_url}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
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
        except Exception as e:
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
        console.print(f"[cyan]🤖 Invocando SLM Local ({self.ollama_model}) com contexto {self.ollama_num_ctx}...[/cyan]")

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
                raise RuntimeError(f"Ollama retornou resposta vazia para o modelo {self.ollama_model}.")
            return msg.strip()

    def synthesize_with_gemini(
        self,
        transcript_text: str,
        lesson_title: str,
        module_name: str,
        source_filename: str,
        keyframes: list[Path] | None = None,
    ) -> str:
        """Gera a nota técnica chamando a API do Gemini Cloud com suporte multimodal a frames de vídeo."""
        if not self.client:
            raise ValueError("GEMINI_API_KEY não configurada ou client não inicializado.")

        has_keyframes = bool(keyframes)
        tag_vision = f" [Multimodal: {len(keyframes)} frames visuais]" if has_keyframes and keyframes else ""
        console.print(f"[magenta]☁️  Invocando Gemini Cloud ({self.gemini_model}){tag_vision}...[/magenta]")

        user_prompt = f"""Módulo: {module_name}
Título da Aula: {lesson_title}
Arquivo de Transcrição: {source_filename}

Abaixo está a transcrição bruta da aula gravada:
---
{transcript_text}
---

Por favor, elabore a nota estruturada completa em Markdown seguindo as diretrizes e o formato solicitado."""

        sys_prompt = build_system_prompt(module_name, has_vision=has_keyframes)

        # Monta os conteúdos para inferência multimodal.
        # Com frames: um único Content com partes intercaladas [texto, rótulo, imagem, ...]
        # para garantir ordem cronológica e ancoragem [MM:SS] explícita ao modelo.
        if has_keyframes and keyframes:
            from google.genai import types

            manifest_lines = []
            for i, kf in enumerate(keyframes, start=1):
                label = extract_timestamp_label(kf)
                manifest_lines.append(f"[Frame {i:02d}] {label}" if label else f"[Frame {i:02d}]")
            manifest = (
                "Frames do vídeo anexados em ordem cronológica:\n"
                + "\n".join(manifest_lines)
                + "\n\nCada imagem é precedida pelo rótulo [Frame N — MM:SS] do instante correspondente no vídeo."
            )
            parts: list[Any] = [types.Part.from_text(text=f"{sys_prompt}\n\n{user_prompt}\n\n{manifest}")]
            for i, kf in enumerate(keyframes, start=1):
                if not kf.exists():
                    continue
                label = extract_timestamp_label(kf)
                if label:
                    parts.append(types.Part.from_text(text=f"[Frame {i:02d} — {label}]"))
                try:
                    parts.append(types.Part.from_bytes(data=kf.read_bytes(), mime_type="image/jpeg"))
                except Exception as e:
                    console.print(f"[dim]Aviso ao anexar frame {kf.name} ao Gemini: {e}[/dim]")
            contents = [types.Content(role="user", parts=parts)]
        else:
            contents = [f"{sys_prompt}\n\n{user_prompt}"]

        # Se não houver frames, tenta primeiro via interactions API
        if not has_keyframes:
            try:
                interaction = self.client.interactions.create(
                    model=self.gemini_model,
                    input=user_prompt,
                    system_instruction=sys_prompt,
                )
                if interaction.output_text:
                    return interaction.output_text.strip()
            except Exception:
                pass

        # Fallback ou execução multimodal via generate_content com retry + backoff exponencial
        # e fallback de modelo para 503/429 (ex: gemini-3.5-flash-lite -> gemini-3.5-flash -> gemini-2.5-flash)
        candidate_models = [self.gemini_model]
        for fb_model in ("gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"):
            if fb_model not in candidate_models:
                candidate_models.append(fb_model)

        last_exc: Exception | None = None
        for model_name in candidate_models:
            for attempt in range(1, 4):
                try:
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=contents,
                    )
                    if response and response.text:
                        if model_name != self.gemini_model:
                            console.print(f"[dim]ℹ Síntese concluída via modelo de contingência ({model_name}).[/dim]")
                        return response.text.strip()
                except Exception as exc:
                    last_exc = exc
                    if not _is_transient_error(exc):
                        raise
                    delay = 4 * (2 ** (attempt - 1))
                    console.print(
                        f"[yellow]⏳ {model_name} sobrecarregado (tentativa {attempt}/3) — aguardando {delay}s...[/yellow]"
                    )
                    time.sleep(delay)
            if model_name != candidate_models[-1]:
                console.print(f"[yellow]🔄 Alternando para modelo de contingência após sobrecarga em {model_name}...[/yellow]")

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("O modelo Gemini retornou uma resposta vazia.")

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

        console.print(f"[bold cyan]🚀 Invocando DeepSeek-V3 ({self.deepseek_model}) via {self.deepseek_base_url}...[/bold cyan]")
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
    ) -> str:
        """Gera um scaffold estruturado quando nenhuma chave de API estiver presente."""
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
- **Atenção:** Documento gerado em modo scaffold offline. Configure a `GEMINI_API_KEY` para síntese automatizada com IA.

---

## 📌 Principais Conclusões
- Nota estruturada pronta para detalhamento técnico.
"""

    def _collect_lab_code_context(self, module_dir: Path) -> str:
        """Coleta trechos de código reais presentes em materiais/raw/ ou materiais/raw/Material/."""
        raw_dir = module_dir / "materiais" / "raw"
        if not raw_dir.is_dir():
            return ""

        code_extensions = {".py", ".sql", ".ipynb", ".sh", ".yaml", ".yml", ".json", ".js", ".ts"}
        code_snippets: list[str] = []
        total_chars = 0

        for p in sorted(raw_dir.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() in code_extensions and "archive" not in p.parts:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace").strip()
                    if text and len(text) > 20:
                        trimmed = text[:4000] # Limite razoável por arquivo
                        code_snippets.append(f"### Arquivo de Apoio: `{p.name}`\n```{p.suffix.lstrip('.')}\n{trimmed}\n```")
                        total_chars += len(trimmed)
                        if total_chars > 25000:
                            break
                except Exception:
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
        # Garante nome padronizado no formato aula-XX-slug.md
        if not stem.startswith("aula-") and not stem.startswith("mod"):
            m = re.match(r"^(\d+)[-_ ]*(.+)$", stem)
            if m:
                target_filename = f"aula-{int(m.group(1)):02d}-{m.group(2).strip().lower().replace(' ', '-')}.md"
            else:
                target_filename = f"aula-{stem.lower().replace(' ', '-')}.md"
        else:
            target_filename = f"{stem}.md"

        out_dir = output_dir or (module_dir / "aulas")
        out_dir.mkdir(parents=True, exist_ok=True)
        target_path = out_dir / target_filename

        if target_path.exists() and not force and not _is_scaffold_file(target_path):
            console.print(f"[dim]Nota já existente (Skip):[/dim] {target_path.name}")
            return target_path

        had_valid_note = target_path.exists() and not _is_scaffold_file(target_path)
        transcript_text = transcript_file.read_text(encoding="utf-8")
        lab_code_context = self._collect_lab_code_context(module_dir)
        full_context_text = transcript_text + lab_code_context
        clean_title = stem.replace("-", " ").title()

        tag_vision = f" [Visão: {len(keyframes)} frames]" if keyframes else ""
        console.print(f"[cyan]📝 Sintetizando nota para:[/cyan] [bold]{target_filename}[/bold]{tag_vision}...")

        with track("sintese", modulo=module_dir.name, item=target_filename) as info:
            info["entrada_chars"] = len(full_context_text)
            if keyframes:
                info["keyframes_count"] = len(keyframes)
            content, provider = self._generate(
                full_context_text, clean_title, module_dir.name, transcript_file.name, info, keyframes=keyframes
            )
            content, fixed = sanitize_markdown(content)
            if fixed:
                info["mermaid_corrigidos"] = fixed
                console.print(f"[dim]🩹 {fixed} diagrama(s) Mermaid corrigido(s) automaticamente.[/dim]")
            info["provider"] = provider
            info["saida_chars"] = len(content)
            if provider == "scaffold":
                info["status"] = "scaffold"
                if had_valid_note:
                    console.print(
                        f"[yellow]🛡️ Proteção de conteúdo: mantendo nota completa existente em {target_path.name} (scaffold descartado).[/yellow]"
                    )
                    return target_path
            target_path.write_text(content, encoding="utf-8")

            # Curadoria e anexação automática de 2-3 figuras-chave em WebP (materiais/figuras/)
            if keyframes and self.client and provider != "scaffold":
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
            self.client, self.gemini_model, keyframes, lesson_title=lesson_title, max_figures=max_figures
        )
        if not chosen:
            console.print("[dim]ℹ Nenhum frame exigiu inclusão como figura estática (conteúdo 100% coberto por texto/Mermaid).[/dim]")
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

        # 1. 1ª Opção (Principal): Gemini Cloud
        if self.provider in ("auto", "gemini"):
            if self.client:
                t0 = time.perf_counter()
                try:
                    content = self.synthesize_with_gemini(*args, keyframes=keyframes)
                    info["gemini_s"] = f"{time.perf_counter() - t0:.0f}"
                    console.print(f"[bold green]✓ Síntese gerada via Gemini Cloud ({self.gemini_model})![/bold green]")
                    return content, f"gemini:{self.gemini_model}"
                except Exception as e:
                    info["gemini_s"] = f"{time.perf_counter() - t0:.0f}"
                    info["gemini_erro"] = type(e).__name__
                    if self.provider == "gemini":
                        console.print(f"[yellow]⚠️ Falha no Gemini ({e}). Provider restrito a 'gemini'. Gerando scaffold estruturado...[/yellow]")
                        return self.generate_scaffold_note(*args), "scaffold"
                    console.print(f"[yellow]⚠️ Gemini indisponível ({e}). Acionando 2ª Opção Cloud: DeepSeek-V3...[/yellow]")
            else:
                if self.provider == "gemini":
                    console.print("[dim]ℹ GEMINI_API_KEY não configurada ou inválida.[/dim]")
                    return self.generate_scaffold_note(*args), "scaffold"

        # 2. 2ª Opção (Fallback Cloud): DeepSeek-V3 / OpenAI-compatible
        if self.provider in ("auto", "deepseek"):
            if self.deepseek_api_key:
                t0 = time.perf_counter()
                try:
                    content = self.synthesize_with_deepseek(*args)
                    info["deepseek_s"] = f"{time.perf_counter() - t0:.0f}"
                    console.print(f"[bold green]✓ Síntese gerada via DeepSeek Cloud ({self.deepseek_model})![/bold green]")
                    return content, f"deepseek:{self.deepseek_model}"
                except Exception as e:
                    info["deepseek_s"] = f"{time.perf_counter() - t0:.0f}"
                    info["deepseek_erro"] = type(e).__name__
                    if self.provider == "deepseek":
                        console.print(f"[yellow]⚠️ Falha no DeepSeek ({e}). Gerando scaffold estruturado...[/yellow]")
                        return self.generate_scaffold_note(*args), "scaffold"
                    console.print(f"[yellow]⚠️ Falha no DeepSeek ({e}). Acionando 3ª Opção Local: SLM Ollama...[/yellow]")
            else:
                if self.provider == "deepseek":
                    console.print("[yellow]⚠️ DEEPSEEK_API_KEY não configurada. Gerando scaffold estruturado...[/yellow]")
                    return self.generate_scaffold_note(*args), "scaffold"

        # 3. 3ª Opção (Fallback Local): Ollama SLM
        if self.provider in ("auto", "ollama"):
            if self.is_ollama_available() and self.ensure_ollama_model():
                t0 = time.perf_counter()
                try:
                    content = self.synthesize_with_ollama(*args)
                    info["ollama_s"] = f"{time.perf_counter() - t0:.0f}"
                    console.print(f"[bold green]✓ Síntese gerada via SLM Local ({self.ollama_model})![/bold green]")
                    return content, f"ollama:{self.ollama_model}"
                except Exception as e:
                    info["ollama_s"] = f"{time.perf_counter() - t0:.0f}"
                    info["ollama_erro"] = type(e).__name__
                    console.print(f"[yellow]⚠️ Falha no SLM Local ({e}) após {fmt_duration(time.perf_counter() - t0)}.[/yellow]")
            else:
                if self.provider == "ollama":
                    console.print(f"[yellow]ℹ Servidor Ollama ({self.ollama_url}) não está acessível.[/yellow]")
                    return self.generate_scaffold_note(*args), "scaffold"

        # 4. 4ª Opção: Fallback final Scaffold
        console.print("[yellow]ℹ Nenhum provedor de IA disponível. Gerando scaffold offline...[/yellow]")
        return self.generate_scaffold_note(*args), "scaffold"

