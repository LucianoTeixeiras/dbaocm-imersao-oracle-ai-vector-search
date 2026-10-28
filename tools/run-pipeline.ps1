<#
.SYNOPSIS
    Orquestrador Fim-a-Fim do Knowledge Extractor (pipeline de estudos/treinamentos).

.DESCRIPTION
    Executa de forma interativa ou em lote as etapas de:
    1. Normalizacao de arquivos (De-Para & Kebab-case)
    2. Extracao & Transcricao (faster-whisper & PDF parser)
    3. Sintese multimodal de notas em aulas/ (Gemini API / Ollama local)
    4. Sincronizacao de portal/catalogo.md
    5. Auditoria de Governanca Git (Blindagem de Midias)

.PARAMETER Module
    Caminho relativo para a pasta do modulo (ex: modules/01-fundamentos).
    Se omitido, o script inicia pelo primeiro modulo e permite avancar
    ciclo a ciclo ou executar todos em sequencia.

.PARAMETER All
    Executa todas as etapas (1 a 5) em lote sem interrupcao. Se -Module nao for
    especificado, processa TODOS os modulos da formacao em sequencia (Ciclo Global).

.PARAMETER AllModules
    Forca a execucao sequencial de todos os modulos da formacao (Ciclo Global).

.PARAMETER Step
    Executa apenas uma etapa especifica (1 a 5).

.PARAMETER Force
    Forca o reprocessamento de arquivos ja existentes.

.PARAMETER ModelSize
    Tamanho do modelo Whisper (tiny, base, small, medium, large-v3). Padrao: base.

.PARAMETER Provider
    Modo de sintese: auto (Ollama -> Gemini -> scaffold), ollama ou gemini.
    Padrao: SYNTHESIS_PROVIDER do .env.

.PARAMETER OllamaModel
    Modelo Ollama local (ex: qwen2.5:7b-instruct). Padrao: OLLAMA_MODEL do .env.

.PARAMETER GeminiModel
    Modelo Gemini (ex: gemini-2.5-flash). Padrao: GEMINI_MODEL do .env.

.PARAMETER NoVision
    Desativa a extracao e analise multimodal dos frames de video.

.EXAMPLE
    .	ools
un-pipeline.ps1 -Module modules/01-fundamentos
    .	ools
un-pipeline.ps1 -All
    .	ools
un-pipeline.ps1 -Step 3 -Provider gemini
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Module = "",

    [switch]$All,
    [switch]$AllModules,
    [int]$Step,
    [switch]$Force,
    [string]$ModelSize = "base",
    [ValidateSet("", "auto", "ollama", "gemini")]
    [string]$Provider = "",
    [string]$OllamaModel = "",
    [string]$GeminiModel = "",
    [switch]$NoVision
)

$ErrorActionPreference = "Stop"

# Garante encoding UTF-8 no console PowerShell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Localiza a raiz do repositorio
$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
Set-Location $RepoRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Knowledge Extractor - Pipeline de Estudos e Treinamentos" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

# Verifica instalacao do uv
$uvPath = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvPath) {
    Write-Host "[ERRO] O gerenciador 'uv' nao foi encontrado no PATH." -ForegroundColor Red
    Write-Host "Instale via: winget install astral-sh.uv OU irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor Yellow
    exit 1
}

# Se solicitada execucao global em todos os modulos
$RunGlobalCycle = $AllModules -or ($All -and (-not $Module) -and (-not $Step))

# Modulo alvo: usa o informado ou auto-detecta o primeiro em modules/
if (-not $Module) {
    if (Test-Path "$RepoRoot/modules") {
        $first = Get-ChildItem "$RepoRoot/modules" -Directory | Sort-Object Name | Select-Object -First 1
        if ($first) {
            $Module = "modules/$($first.Name)"
            if (-not $RunGlobalCycle) {
                Write-Host "[INFO] Nenhum -Module informado. Iniciando pelo primeiro modulo: $Module" -ForegroundColor DarkGray
            }
        }
    }
    if (-not $Module) {
        Write-Host "[ERRO] Nenhuma pasta de modulo encontrada em modules/." -ForegroundColor Red
        Write-Host "Gere a estrutura com: uv run --project tools/knowledge-extractor knowledge-extractor scaffold-modules" -ForegroundColor Yellow
        exit 1
    }
}

$TargetModule = Join-Path $RepoRoot $Module
if (-not (Test-Path $TargetModule)) {
    Write-Host "[ERRO] Modulo nao encontrado em: $TargetModule" -ForegroundColor Red
    Write-Host "Modulos disponiveis em modules/: $((Get-ChildItem "$RepoRoot/modules" -Directory -ErrorAction SilentlyContinue).Name -join ', ')" -ForegroundColor Yellow
    exit 1
}

# Verifica GEMINI_API_KEY
if (-not $env:GEMINI_API_KEY) {
    if (Test-Path "$RepoRoot/.env") {
        Get-Content "$RepoRoot/.env" | ForEach-Object {
            if ($_ -match "^\s*GEMINI_API_KEY\s*=\s*(.+)$") {
                $env:GEMINI_API_KEY = $matches[1].Trim('"').Trim("'")
            }
        }
    }
}

if (-not $env:GEMINI_API_KEY) {
    Write-Host "[INFO] A variavel GEMINI_API_KEY nao foi detectada no ambiente." -ForegroundColor Yellow
    Write-Host "  A etapa de sintese gerara scaffolds estruturados offline." -ForegroundColor DarkGray
} else {
    Write-Host "[OK] GEMINI_API_KEY detectada." -ForegroundColor Green
}

# Monta os argumentos para o uv run
$cliArgs = @(
    "run",
    "--project",
    "tools/knowledge-extractor",
    "knowledge-extractor",
    "pipeline"
)

if ($RunGlobalCycle) {
    Write-Host "[INFO] Disparando Ciclo Global sequencial em TODOS os modulos..." -ForegroundColor Cyan
    $cliArgs += "--all-modules"
} else {
    $cliArgs += $Module
}

$cliArgs += @("-m", $ModelSize)

# So repassa provedor/modelos quando informados (senao vale o .env)
if ($Provider) { $cliArgs += @("--provider", $Provider) }
if ($OllamaModel) { $cliArgs += @("--ollama-model", $OllamaModel) }
if ($GeminiModel) { $cliArgs += @("--gemini-model", $GeminiModel) }

if ($All -and (-not $RunGlobalCycle)) {
    $cliArgs += "--all"
}

if ($Step -ge 1 -and $Step -le 5) {
    $cliArgs += @("--step", $Step.ToString())
}

if ($Force) {
    $cliArgs += "-f"
}

if ($NoVision) {
    $cliArgs += "--no-vision"
}

Write-Host ""
Write-Host "Executando: uv $($cliArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ""

& uv @cliArgs
