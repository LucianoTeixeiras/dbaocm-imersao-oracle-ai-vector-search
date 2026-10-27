# Template Canônico de Repositório de Estudos e Formação Técnica (AI-First)

<div align="center">

![Formato](https://img.shields.io/badge/Formato-Template%20Can%C3%B4nico-blue?style=for-the-badge)
![Arquitetura](https://img.shields.io/badge/Padr%C3%A3o-AI--First%20Learning-purple?style=for-the-badge)
![Automação](https://img.shields.io/badge/Pipeline-knowledge--extractor%20v1.2-darkgreen?style=for-the-badge)
![Governança](https://img.shields.io/badge/SSOT-course.yaml-orange?style=for-the-badge)

</div>

Repositório modelo para gerenciamento de estudos, pós-graduações, MBAs, certificações e programas de treinamento técnico intensivo. Desenvolvido para transformar o consumo de aulas gravadas e materiais acadêmicos em um **acervo estruturado de engenharia**, com notas técnicas detalhadas, diagramas Mermaid, código prático e rastreabilidade total com portais de ensino (AVAs).

---

## 🎯 Pilares da Arquitetura

1. **Fonte Única de Verdade (`course.yaml`):**
   Toda a estrutura do curso (título, instituição, ementa, competências pedagógicas e ordem no AVA) é declarada em um único YAML na raiz. As ferramentas de pipeline e síntese leem esse arquivo dinamicamente para parametrizar prompts e catálogos.

2. **Scaffolding Determinístico (`scaffold-modules`):**
   A árvore canônica de pastas (`aulas/`, `labs/`, `materiais/raw/`, `transcricoes/`, `portal/`) nasce de um comando único a partir do `course.yaml`.

3. **Pipeline Multimodal Local-First (`knowledge-extractor v1.2`):**
   - Transcrição de áudio acelerada via `faster-whisper` em PT-BR.
   - Extração híbrida de keyframes do vídeo (detecção de cena `> 0.22` + piso temporal de 180 s) com deduplicação perceptual (`dHash`) e ancoragem temporal `[MM:SS]`.
   - Síntese com SLMs locais (Ollama) ou LLMs multimodais (Google Gemini), com retry automático, backoff exponencial e cadeia de contingência (`gemini-2.5-flash` → `gemini-3.5-flash-lite` → `gemini-2.0-flash`).
   - Curadoria automática de figuras-chave convertidas em `.webp` ultra-leve (~40 KB) salvas em `materiais/figuras/` e anexadas à nota.
   - Validador e sanitizador determinístico de diagramas Mermaid.
   - Auditoria De-Para persistente em `docs/logs/` e telemetria em `pipeline-metrics.csv`.

---

## 📂 Anatomia de Pastas do Repositório

```text
meu-curso/
├── .env                              # Segredos locais (GEMINI_API_KEY, etc.) — GITIGNORED
├── .env.example                      # Template de variáveis de ambiente
├── .gitignore                        # Blindagem de mídias pesadas e frames temporários
├── course.yaml                       # SSOT pedagógica: ementa, módulos e competências
├── README.md                         # Visão geral do curso
├── docs/                             # Documentação perene e governança
│   ├── ementa-disciplinas.md         # Ementa detalhada oficial (opcional)
│   ├── logs/                         # Auditoria persistente De-Para de arquivos renomeados
│   │   ├── file-renaming-audit.json
│   │   └── file-renaming-audit.md
│   └── adrs/                         # Architecture Decision Records do repositório
├── modules/                          # Disciplinas/módulos do curso
│   └── XX-slug-do-modulo/
│       ├── README.md                 # Gerado pelo scaffold a partir do course.yaml
│       ├── aulas/                    # Notas de aula sintetizadas (.md) — VERSIONADO
│       ├── labs/                     # Laboratórios, scripts e notebooks práticos — VERSIONADO
│       ├── portal/
│       │   └── catalogo.md           # Espelho do AVA com status de cada aula — VERSIONADO
│       └── materiais/
│           ├── raw/                  # Mídias brutas baixadas (.mp4, .pdf) — GITIGNORED (só .gitkeep)
│           ├── transcricoes/         # .txt e .srt gerados pelo Whisper — VERSIONADO
│           ├── frames/               # Keyframes JPEG temporários da aula — GITIGNORED
│           └── figuras/              # 1 a 3 figuras .webp curadas pelo VLM — VERSIONADO
└── tools/
    ├── run-pipeline.ps1              # Orquestrador PowerShell facilitado
    └── knowledge-extractor/          # Ferramenta CLI interna (Python + uv)
```

---

## 🚀 Guia Passo a Passo: Do Clone à Primeira Aula

Siga rigorosamente estas etapas ao iniciar um novo curso a partir deste template:

### Passo 1 — Criar o Repositório
No GitHub, clique em **"Use this template"** (ou use a GitHub CLI) para criar o repositório do seu curso e clone-o na sua máquina:
```bash
git clone https://github.com/SEU_USUARIO/meu-novo-curso.git
cd meu-novo-curso
```

### Passo 2 — Configurar as Credenciais (.env)
Copie o template de variáveis de ambiente e preencha sua chave do Google Gemini (necessária para a síntese multimodal e curadoria de figuras):
```powershell
Copy-Item .env.example .env
```
Edite o `.env` e defina:
```ini
GEMINI_API_KEY="AIzaSy..."
GEMINI_MODEL="gemini-2.5-flash"
SYNTHESIS_PROVIDER="gemini"
WHISPER_MODEL="base"
```

### Passo 3 — Declarar a Ementa no `course.yaml`
Abra o `course.yaml` na raiz do repositório e substitua os dados de exemplo pelas informações reais do seu curso. Declare a lista exata dos módulos com seus slugs, títulos e competências esperadas:
```yaml
course:
  id: "curso-ia-para-devs"
  title: "Curso de IA para Devs"
  institution: "Programador BR"
  track_role: "AI Software Engineer"
  description: "Formação prática em agentes, LLMs, MCP e engenharia de contexto."

modules:
  01-fundamentos-de-llms:
    id: "01"
    name: "Fundamentos de LLMs"
    summary: "Tokens, system prompts e modelos open-source locais."
    portal_order: 1
    competencies:
      - "Compreender o funcionamento de LLMs e amostragem de tokens"
      - "Configurar modelos locais com Ollama"

  02-opencode:
    id: "02"
    name: "OpenCode e Ferramentas"
    summary: "Uso de ferramentas e extensões para desenvolvimento assistido por IA."
    portal_order: 2
    competencies:
      - "Integrar ferramentas de automação ao fluxo de código"
```

### Passo 4 — Gerar a Estrutura de Pastas (Scaffold)
**Atenção:** o menu do pipeline (`run-pipeline.ps1`) NÃO cria pastas de módulos — ele apenas processa módulos existentes. A criação da árvore de pastas é feita pelo comando `scaffold-modules`:

```bash
# 1. Simulação prévia: veja a lista de pastas que serão criadas
uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules --dry-run

# 2. Execução real: cria todas as pastas canônicas e o README.md de cada módulo
uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules
```

> **Dica de Limpeza:** Se o template veio com pastas de exemplo (como `01-fundamentos-e-introducao` ou `02-arquitetura-e-padroes`) que não fazem parte do seu curso, **exclua-as manualmente** de `modules/` antes de continuar, para que o auto-detector do pipeline não aponte para elas.

### Passo 5 — Depositar as Mídias Brutas da Aula
Copie os arquivos de vídeo (`.mp4`, `.mkv`) ou apostilas (`.pdf`) baixados do portal do curso diretamente para a pasta `materiais/raw/` do módulo correspondente:
```text
modules/01-fundamentos-de-llms/materiais/raw/
├── 01-Introducao aos LLMs.mp4
└── 02-Configurando Ollama.mp4
```
*(Não se preocupe com espaços ou nomes despadronizados — o pipeline cuida disso no próximo passo.)*

### Passo 6 — Executar o Pipeline de Processamento

#### Opção A: Menu Interativo (recomendado para acompanhar passo a passo)
```powershell
# Executa no primeiro módulo de modules/ (ou passe -Module modules/01-fundamentos-de-llms):
.\tools\run-pipeline.ps1 -Module modules/01-fundamentos-de-llms
```
No menu que abrir, digite:
- `6` para rodar o **Pipeline Completo (1 → 5)** em sequência:
  1. *Normalização e auditoria De-Para* (renomeia `01-Introducao...mp4` para `aula-01-introducao-aos-llms.mp4` e grava log).
  2. *Extração e Transcrição* (Whisper gera `.txt`/`.srt` e extrai keyframes do vídeo).
  3. *Síntese Multimodal* (Gemini cruza áudio + telas, gera diagramas Mermaid e anexa figuras WebP).
  4. *Sincronização do Catálogo* (atualiza `portal/catalogo.md` com status ✅ Concluído).
  5. *Auditoria de Governança* (garante que nenhum `.mp4` vazou para o Git).
- Ou digite `1`, `2`, `3`, `4` ou `5` para rodar apenas uma etapa individual.

#### Opção B: Em Lote (sem confirmações / modo headless)
```powershell
.\tools\run-pipeline.ps1 -Module modules/01-fundamentos-de-llms -All
```

#### Opção C: Diretamente via CLI do `uv` (multiplataforma)
```bash
uv run --project tools/knowledge-extractor knowledge-extractor pipeline modules/01-fundamentos-de-llms --all --vision
```

---

## 🛠️ Comandos Úteis do `knowledge-extractor`

Todos os comandos devem ser executados a partir da **raiz do repositório**:

| Ação | Comando |
| :--- | :--- |
| **Simular criação de módulos** | `uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules --dry-run` |
| **Criar/atualizar módulos** | `uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules` |
| **Simular normalização De-Para** | `uv run --project tools/knowledge-extractor knowledge-extractor normalize-files modules/<slug>` |
| **Aplicar normalização De-Para** | `uv run --project tools/knowledge-extractor knowledge-extractor normalize-files modules/<slug> --apply` |
| **Consultar histórico De-Para** | `uv run --project tools/knowledge-extractor knowledge-extractor audit-log [--search termo]` |
| **Transcrever vídeo avulso** | `uv run --project tools/knowledge-extractor knowledge-extractor transcribe video.mp4 -o saida/` |
| **Extrair texto de PDF avulso** | `uv run --project tools/knowledge-extractor knowledge-extractor extract-pdf doc.pdf -o saida/` |
| **Sintetizar notas de um módulo** | `uv run --project tools/knowledge-extractor knowledge-extractor synthesize modules/<slug> [--vision]` |
| **Curar e anexar figuras WebP** | `uv run --project tools/knowledge-extractor knowledge-extractor attach-figures modules/<slug> [--max-figures 3]` |
| **Corrigir diagramas Mermaid** | `uv run --project tools/knowledge-extractor knowledge-extractor fix-mermaid modules/<slug> --apply` |
| **Sincronizar catálogo AVA** | `uv run --project tools/knowledge-extractor knowledge-extractor sync-catalog modules/<slug>` |
| **Rodar testes unitários** | `uv run --project tools/knowledge-extractor --group dev pytest tools/knowledge-extractor/tests/ -q` |

---

## 🛡️ Regras de Governança e Boas Práticas

1. **Blindagem de Mídias Binárias:**
   - Vídeos (`.mp4`, `.mkv`), áudios (`.mp3`, `.wav`) e frames intermediários (`materiais/frames/`) **nunca entram no Git**. O `.gitignore` incluído bloqueia essas extensões automaticamente.
   - O repositório armazena apenas **transcrições (`.txt`/`.srt`), notas estruturadas (`.md`), código de laboratório e até 3 figuras leves (`.webp` ~40 KB) por aula em `materiais/figuras/`.
2. **Segredos e Tokens:**
   - O arquivo `.env` está no `.gitignore`. Nunca faça commit de chaves de API (`AIza...`, tokens do HuggingFace ou do Git).
3. **Branching e Commits:**
   - Nunca faça commits diretos na branch `main`. Crie branches de feature para novas disciplinas (`feat/modulo-XX`) e consolide via Pull Request aprovado.
