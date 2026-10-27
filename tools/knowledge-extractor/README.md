# 🛠️ Knowledge Extractor v1.2 — Manual de Execução

Ferramenta CLI interna (Python + `uv`) que automatiza todo o ciclo de vida do acervo de estudos: **estrutura de pastas → normalização → transcrição → extração visual → síntese multimodal → figuras → catálogo → governança Git**.

> **Como este manual está organizado:**
> 1. [Fluxo Completo de Execução](#-fluxo-completo-de-execução) — a sequência obrigatória do zero à primeira nota de aula.
> 2. [Anatomia de Pastas](#-anatomia-de-pastas-geradas) — o que é criado, onde e quando (versionado × ignorado).
> 3. [Comandos da CLI](#-comandos-da-cli) — referência completa de todos os comandos e opções.
> 4. [Síntese, Modos e Modelos](#-síntese-modos-e-modelos) — cascata Ollama→Gemini→scaffold, retry e contingência.
> 5. [Variáveis de Ambiente](#-variáveis-de-ambiente-env) e [Solução de Problemas](#-solução-de-problemas).

---

## 🚀 Fluxo Completo de Execução

### Passo 0 — Pré-requisitos

| Requisito | Para quê | Instalação |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Ambiente e dependências Python | `winget install astral-sh.uv` |
| `ffmpeg` | Extração de áudio **e** keyframes de vídeo | `winget install Gyan.FFmpeg` |
| [Ollama](https://ollama.com/) *(opcional)* | Síntese local, sem custo de API | `winget install Ollama.Ollama` |
| `GEMINI_API_KEY` *(recomendado)* | Síntese multimodal, figuras e contingência | [Google AI Studio](https://aistudio.google.com/) |

Configure o `.env` na raiz (copie de `.env.example`):
```ini
GEMINI_API_KEY="AIzaSy..."
GEMINI_MODEL="gemini-2.5-flash"
SYNTHESIS_PROVIDER="gemini"
```

Sem Ollama e sem chave Gemini, a síntese gera apenas **scaffold** (rascunho genérico — e nunca sobrescreve uma nota boa).

### Passo 1 — Criar a estrutura de módulos (SCAFFOLD)

**A criação de pastas NÃO faz parte do menu do pipeline.** Ela é feita exclusivamente pelo comando `scaffold-modules`, que lê o `course.yaml`:

```bash
# Simulação (não altera nada — mostra a tabela de pastas planejadas)
uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules --dry-run

# Execução real — cria a árvore canônica de CADA módulo declarado no course.yaml
uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules
```

Para cada módulo declarado (`modules/<slug>/`) são criados:

```text
modules/<slug>/
├── README.md               # ementa + competências geradas a partir do course.yaml
├── aulas/                  # destino das notas de aula (.md) — VERSIONADO
├── labs/                   # laboratórios práticos — VERSIONADO
├── portal/                 # catalogo.md (espelho do AVA) — VERSIONADO
└── materiais/
    ├── raw/                # ← COLOQUE AQUI os vídeos/PDFs baixados — GITIGNORED
    └── transcricoes/       # .txt/.srt gerados pelo Whisper — VERSIONADO
```

**Regras do scaffold:**
- Reexecutar é idempotente: pastas existentes são mantidas; o `README.md` do módulo é **regenerado** a partir do `course.yaml` (atualize o YAML antes de reexecutar).
- Pastas de módulos **não declaradas** no `course.yaml` são **ignoradas** (nem criadas, nem apagadas). Exclua manualmente placeholders do template que não pertencem ao seu curso.

### Passo 2 — Depositar as mídias brutas

Copie os vídeos (`.mp4`, `.mkv`) e PDFs baixados do portal para `modules/<slug>/materiais/raw/`. Nomes despadronizados são corrigidos na Etapa 1 do pipeline.

### Passo 3 — Executar o pipeline

```powershell
# Menu interativo (acompanhe etapa por etapa; opção 6 roda tudo)
.\tools\run-pipeline.ps1 -Module modules/<slug>

# Lote headless (sem menu)
.\tools\run-pipeline.ps1 -Module modules/<slug> -All

# Multiplataforma (Linux/macOS/Windows)
uv run --project tools/knowledge-extractor knowledge-extractor pipeline modules/<slug> --all --vision
```

| Etapa do pipeline | O que faz | Saídas |
|---|---|---|
| **1. Normalização** | Renomeia arquivos para o padrão `aula-XX-slug` (kebab-case), registra auditoria De-Para | `docs/logs/file-renaming-audit.{json,md}` |
| **2. Extração** | Transcreve áudio (Whisper `base`) **e** extrai keyframes híbridos do vídeo | `materiais/transcricoes/*.txt` + `*.srt`; `materiais/frames/<aula>/` (temporário) |
| **3. Síntese multimodal** | Cruza transcrição + telas do vídeo no Gemini; reconstrói diagramas Mermaid, código de tela e anexa figuras WebP | `aulas/aula-XX-*.md`; `materiais/figuras/*.webp` |
| **4. Catálogo** | Sincroniza o espelho do AVA com status de cada aula | `portal/catalogo.md` |
| **5. Governança** | Confere que nenhuma mídia pesada vazou para o Git | relatório no terminal |

> **Menu interativo** (opções): `1` normalizar · `2` extrair/transcrever · `3` sintetizar · `4` sincronizar catálogo · `5` auditoria Git · `6` pipeline completo (1→5) · `7` modo de síntese · `8` alternar visão multimodal · `0` sair.

---

## 📂 Anatomia de Pastas Geradas

| Caminho | Origem | Git | Conteúdo |
|---|---|---|---|
| `modules/<slug>/` | scaffold-modules | ✅ | Pasta canônica do módulo |
| `modules/<slug>/README.md` | scaffold-modules | ✅ | Ementa e competências |
| `modules/<slug>/aulas/*.md` | pipeline Etapa 3 | ✅ | **Notas de aula** (produto final) |
| `modules/<slug>/labs/` | manual/scaffold | ✅ | Laboratórios e scripts |
| `modules/<slug>/materiais/raw/` | você | ❌ | Vídeos/PDFs brutos (blindados) |
| `modules/<slug>/materiais/transcricoes/` | pipeline Etapa 2 | ✅ | `.txt` + `.srt` |
| `modules/<slug>/materiais/frames/` | pipeline Etapa 2 | ❌ | Keyframes JPEG temporários (`frame_NNNN_MMmSSs.jpg`) |
| `modules/<slug>/materiais/figuras/` | pipeline Etapa 3 | ✅ | 1–3 figuras `.webp` curadas (~40 KB) |
| `modules/<slug>/portal/catalogo.md` | pipeline Etapa 4 | ✅ | Status das aulas no AVA |
| `docs/logs/file-renaming-audit.*` | Etapa 1 | ✅ | Auditoria De-Para persistente |
| `tools/knowledge-extractor/logs/pipeline-metrics.csv` | todas | ❌ | Telemetria (tempo, tokens, provider) |

---

## 🖼️ Extrator Visual Multimodal (v1.2)

A visão enriquece notas onde o professor mostra mas não fala:

1. **Keyframes híbridos** (`video_frames.py`): detecção de mudança de cena (`scene > 0.22`, cooldown 4s) **+ piso temporal** (1 frame a cada 180s) — cobre tanto troca de slides quanto live-coding sem corte brusco. Deduplicação perceptual (`dHash`) remove telas quase idênticas. Cada frame carrega o instante real no nome: `frame_0004_05m32s.jpg`.
2. **Ancoragem `[MM:SS]` na síntese**: cada imagem é enviada ao Gemini precedida de `[Frame N — MM:SS]`, permitindo cruzar a tela com a fala do professor.
3. **Filtro anti-ruído**: o prompt instrui o modelo a ignorar interface do portal, player, popups e webcam.
4. **Curadoria de figuras** (`figures.py`): após a síntese, o VLM escolhe no máximo 3 frames com real valor visual (diagramas, arquiteturas), converte para `.webp` em `materiais/figuras/` e anexa à nota na seção `## 🖼️ Figuras de Referência da Aula` (antes do Resumo Executivo). Frames de código/terminal viram blocos de código, não imagem.
5. **Idempotência**: o bloco de figuras é delimitado por marcadores `<!-- figuras:auto:start/end -->`; reexecutar substitui o bloco sem duplicar.

**Comando dedicado** (sem re-sintetizar a nota — útil para enriquecer notas existentes):
```bash
uv run --project tools/knowledge-extractor knowledge-extractor attach-figures modules/<slug> --max-figures 3
```

---

## 🛠️ Comandos da CLI

Todos os comandos rodam a partir da **raiz do repositório**:

| Ação | Comando |
| :--- | :--- |
| Simular criação de módulos | `knowledge-extractor scaffold-modules --dry-run` |
| **Criar/atualizar módulos** | `knowledge-extractor scaffold-modules` |
| Simular normalização | `knowledge-extractor normalize-files modules/<slug>` |
| Aplicar normalização | `knowledge-extractor normalize-files modules/<slug> --apply` |
| Histórico De-Para | `knowledge-extractor audit-log [--search termo]` |
| Transcrever 1 vídeo | `knowledge-extractor transcribe aula.mp4 -o saida/ [-m base]` |
| Extrair 1 PDF | `knowledge-extractor extract-pdf doc.pdf -o saida/` |
| Processar módulo bruto | `knowledge-extractor process-module modules/<slug> [-f]` |
| Sintetizar notas | `knowledge-extractor synthesize modules/<slug> [--vision\|--no-vision] [-f]` |
| Anexar figuras WebP | `knowledge-extractor attach-figures modules/<slug> [--max-figures 3] [-f]` |
| Corrigir Mermaid | `knowledge-extractor fix-mermaid modules/<slug> [--apply]` |
| Sincronizar catálogo | `knowledge-extractor sync-catalog modules/<slug>` |
| Pipeline completo | `knowledge-extractor pipeline modules/<slug> [--all] [--step 1-5] [--vision] [-f]` |
| Testes unitários | `uv run --project tools/knowledge-extractor --group dev pytest tools/knowledge-extractor/tests/ -q` |

*(Prefixo comum: `uv run --project tools/knowledge-extractor`.)*

**Script PowerShell `.\tools\run-pipeline.ps1`**: `-Module <caminho>` (padrão: primeiro módulo de `modules/`), `-All`, `-Step N`, `-Force`, `-ModelSize` (Whisper), `-Provider`, `-OllamaModel`, `-GeminiModel`, `-NoVision`.

---

## 🧠 Síntese, Modos e Modelos

| Modo | Comportamento |
|---|---|
| `auto` *(padrão)* | Ollama local → se falhar/timeout, Gemini → se falhar, scaffold |
| `ollama` | Só local. Nunca chama a nuvem. Sem visão (texto puro) |
| `gemini` | Só cloud, com visão multimodal quando houver vídeo |

- **Com keyframes presentes no modo `auto`**, o pipeline pula o Ollama (que não processa imagens) e vai direto ao Gemini.
- **Retry e contingência:** falhas transitórias (`503`, `429`, quota) são re-tentadas 3× com backoff (4s/8s/16s). Se o modelo primário esgotar a cota, a cadeia tenta `gemini-3.5-flash-lite` e depois `gemini-2.0-flash`.
- **Proteção anti-sobrescrita:** se todos os provedores falharem e a nota já existir completa, o scaffold é **descartado** e a nota boa é preservada. Notas-scaffold antigas são **regeneradas automaticamente** mesmo sem `-f`.
- **Skip inteligente:** notas prontas são puladas (use `-f` para regerar). `-f` no pipeline completo também retranscreve vídeos (lento) — prefira `synthesize <modulo> -f` para refazer só notas.

Precedência de configuração: flag CLI → menu (opção 7) → `.env` → padrões internos.

---

## ⚙️ Variáveis de Ambiente (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `SYNTHESIS_PROVIDER` | `auto` | Modo de síntese (`auto`, `ollama`, `gemini`) |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Endereço do Ollama |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | Modelo local |
| `OLLAMA_NUM_CTX` | `32768` | Janela de contexto local |
| `OLLAMA_TIMEOUT_SECONDS` | `1800` (recomendado) | Limite por aula no modelo local |
| `GEMINI_API_KEY` | — | Chave do Google AI Studio |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Modelo cloud primário |
| `WHISPER_MODEL_SIZE` / `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `base` / `auto` / `int8` | Transcrição |
| `METRICS_LOG` | `tools/knowledge-extractor/logs/pipeline-metrics.csv` | CSV de telemetria |

---

## 🩺 Solução de Problemas

| Sintoma | Causa provável | Ação |
|---|---|---|
| `Unrecognized option 'vsync'` ao extrair frames | ffmpeg antigo | Atualize o ffmpeg (`winget upgrade Gyan.FFmpeg`) |
| `❌ Módulo não encontrado` no `run-pipeline.ps1` | Pasta não existe ou não foi scaffoldada | Rode `scaffold-modules` (Passo 1) |
| Menu do pipeline não mostra meu módulo | Módulo não declarado no `course.yaml` | Adicione o módulo ao `course.yaml` e reexecute `scaffold-modules` |
| `503 UNAVAILABLE` / `429` do Gemini | Sobrecarga ou cota free tier esgotada | Automático: retry + modelos de contingência. Se persistir, aguarde o reset diário |
| Nota com ~2 KB e *"modo scaffold"* | Nenhum provedor disponível | Configure Ollama/Gemini; a próxima execução regenera sozinha |
| `Falha no SLM Local (HTTP Error 404)` | Modelo não instalado no Ollama | `ollama pull <modelo>` |
| `Servidor Ollama ... não está acessível` | Ollama parado | Abra o app Ollama ou `ollama serve` |
| `mermaid render error: Parse error ...` | Sintaxe inválida gerada pelo modelo | `knowledge-extractor fix-mermaid modules/<slug> --apply` |
| `Project directory 'knowledge-extractor' does not exist` | Executado fora da raiz do repo | Rode da raiz com `--project tools/knowledge-extractor` |

---

## ⏱️ Métricas de Processamento

Cada atividade grava uma linha em `tools/knowledge-extractor/logs/pipeline-metrics.csv` (separador `;`): `inicio`, `fim`, `duracao_s`, `status` (`ok`/`scaffold`), `modulo`, `atividade` (`sintese`, `transcricao`, `figuras`, `etapa_1..5`, `pipeline_completo`), `item` e `detalhes` (ex.: `provider=gemini:gemini-2.5-flash keyframes_count=11 figuras_webp=1`).

Análise rápida no PowerShell:
```powershell
Import-Csv tools/knowledge-extractor/logs/pipeline-metrics.csv -Delimiter ';' |
  Where-Object atividade -eq 'sintese' | Format-Table inicio, duracao, status, item
```
