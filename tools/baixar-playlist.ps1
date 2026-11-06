<#
.SYNOPSIS
    Baixa playlists ou videos do YouTube utilizando o yt-dlp.

.DESCRIPTION
    Wrapper PowerShell para o comando 'knowledge-extractor download-youtube'.
    Permite baixar videos individuais ou playlists completas, extrair apenas audio (MP3),
    listar itens antes de baixar e direcionar a saida diretamente para a pasta de um modulo.

.PARAMETER Url
    URL do video ou playlist do YouTube (obrigatorio).

.PARAMETER Audio
    Baixa apenas o audio em formato MP3.

.PARAMETER Listar
    Lista os itens da playlist sem realizar download.

.PARAMETER Module
    Caminho do modulo (ex: modules/01-fundamentos) para salvar diretamente em materiais/raw/.

.PARAMETER OutputDir
    Diretorio personalizado de destino (padrao: download/).

.PARAMETER Cookies
    Caminho do arquivo cookies.txt para conteudo restrito ou com login.

.PARAMETER MaxHeight
    Resolucao vertical maxima do video (padrao: 1080).

.EXAMPLE
    .\tools\baixar-playlist.ps1 -Url "https://www.youtube.com/playlist?list=PLYAGaV..."
    .\tools\baixar-playlist.ps1 -Url "https://www.youtube.com/watch?v=..." -Audio
    .\tools\baixar-playlist.ps1 -Url "https://www.youtube.com/playlist?list=..." -Module modules/01-fundamentos
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Url,

    [switch]$Audio,
    [switch]$Listar,
    [string]$Module = "",
    [string]$OutputDir = "",
    [string]$Cookies = "",
    [int]$MaxHeight = 1080
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path "$PSScriptRoot/..").Path
Set-Location $RepoRoot

$cliArgs = @(
    "run",
    "--project",
    "tools/knowledge-extractor",
    "knowledge-extractor",
    "download-youtube",
    $Url
)

if ($Audio) { $cliArgs += "--audio" }
if ($Listar) { $cliArgs += "--list-only" }
if ($Module) { $cliArgs += @("--module", $Module) }
if ($OutputDir) { $cliArgs += @("--output-dir", $OutputDir) }
if ($Cookies) { $cliArgs += @("--cookies", $Cookies) }
if ($MaxHeight -and $MaxHeight -ne 1080) { $cliArgs += @("--max-height", $MaxHeight.ToString()) }

# Argumentos adicionais repassados para contornar desafios do YouTube (android/ios/web player)
$cliArgs += @("--", "--extractor-args", "youtube:player_client=android,ios,web")

Write-Host "Executando: uv $($cliArgs -join ' ')" -ForegroundColor Cyan
& uv @cliArgs
