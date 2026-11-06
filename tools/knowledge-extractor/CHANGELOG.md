# Changelog — knowledge-extractor

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e [SemVer](https://semver.org/lang/pt-BR/).
A versão do pacote (`pyproject.toml` e `__version__`) acompanha a tag `vX.Y.Z` do template.

## [Não publicado]

### Alterado
- `logs/` e `.cache/` (métricas e `model_health.json`) saem do pacote e vão para `KE_HOME`: por padrão a pasta com `course.yaml` mais próxima subindo do cwd, senão o cwd.

### Corrigido
- Erros de programação (NameError, KeyError, TypeError…) na síntese não viram mais nota scaffold silenciosa: são logados e propagam. O aviso "Configure a GEMINI_API_KEY" só aparece quando a chave está ausente.

## [1.0.0] — ainda sem tag
Primeira versão empacotada como dependência git (`uv add 'knowledge-extractor @ git+…@v1.0.0#subdirectory=tools/knowledge-extractor'`).

### Adicionado
- LRT-01: detecção real do idioma por chunk, `.meta.json` e nota sempre em pt-BR, com `keep_terms`.
- LRT-02: tema Mermaid claro de alto contraste.
- LRT-04: configuração única do Whisper (CLI > .env > padrão) com auto GPU/CPU.
- LRT-06: PDF → nota pt-BR (páginas, figuras, partes, aviso de PDF escaneado).
- LRT-07: rodízio ponderado Gemini + OpenRouter com circuit breaker; `glm-5.3-flash` como 2ª linha paga (`data_collection: deny`) e teto `SYNTH_MAX_COST_USD`.
- CI: pytest + ruff + gitleaks no GitHub Actions; workflow de propagação de versão.

### Corrigido
- Checagem de "nota já existe" usa o mesmo nome da gravação.
- `Panel` não importado no `cli.py` (NameError ao final do ciclo global).
