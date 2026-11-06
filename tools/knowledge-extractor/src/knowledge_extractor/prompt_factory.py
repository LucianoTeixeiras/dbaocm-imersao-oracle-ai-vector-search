"""Módulo gerador de Especificações Executáveis (Issues-as-a-Prompt) e Catálogo de Prompts.

Padrão derivado da esteira comprovada ta-na-lista (SAGIDCFB) / agent-kit:
1. Gera tarefas técnicas auto-contidas em labs/ (Issue-as-a-Prompt com TDD, regras e critérios).
2. Mantém e sincroniza o catálogo geral de prompts da formação em docs/prompts/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rich.console import Console

from knowledge_extractor.course_config import load_course_config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (OSError, ValueError, KeyError):
        pass

console = Console(highlight=False)

ISSUE_PROMPT_TEMPLATE = """# TASK: {title}

> **Formação / Curso:** {course_title}
> **Módulo:** {module_name}
> **Origem Técnica:** `{source_ref}`
> **Padrão Arquitetural:** Issue-as-a-Prompt (Executável por Agentes / TDD)

---

## 1. Contexto & Visão de Arquitetura
{context}

---

## 2. Contrato de Entrada e Saída
### Entrada (Inputs)
{inputs}

### Saída Esperada (Outputs)
{outputs}

---

## 3. Restrições Rígidas & Anti-Padrões
- Proibido hardcode de credenciais ou chaves de API (usar `.env`).
- Manter convenções de nomenclatura e tipagem explícita (Python/TypeScript/SQL).
- NUNCA efetuar commits diretos na branch padrão (`main`/`master`); todo trabalho deve ser executado em branch de feature.
- Testes automatizados obrigatórios antes de concluir a entrega.

---

## 4. Roteiro de Implementação Guiado por Testes (TDD)
1. **RED:** Criar a suíte de testes unitários ou de integração especificando os comportamentos esperados.
2. **GREEN:** Implementar a lógica nuclear mínima e suficiente para aprovar todos os testes.
3. **REFACTOR:** Refatorar o código isolando responsabilidades, melhorando tipagem e documentação técnica.

---

## 5. Critérios de Aceite & Comando de Validação Factual
- [ ] Código fonte implementado e documentado.
- [ ] Suíte de testes automatizada verde (100% passing).
- [ ] Comando de verificação factual:
```bash
{test_command}
```
"""


def extract_code_snippets_from_note(note_content: str) -> list[dict[str, str]]:
    """Extrai blocos de código com linguagem de uma nota em Markdown."""
    pattern = re.compile(r"```([a-zA-Z0-9_-]+)?\r?\n(.*?)```", re.DOTALL)
    snippets: list[dict[str, str]] = []
    for match in pattern.finditer(note_content):
        lang = (match.group(1) or "text").strip().lower()
        code = match.group(2).strip()
        if lang not in {"mermaid", "text"} and len(code.splitlines()) >= 3:
            snippets.append({"lang": lang, "code": code})
    return snippets


def generate_task_prompt(
    note_path: Path,
    output_dir: Path | None = None,
    task_number: int = 1,
) -> Path | None:
    """Gera um arquivo de Issue-as-a-Prompt em labs/ a partir do conteúdo de uma nota de aula."""
    note_path = Path(note_path).resolve()
    if not note_path.exists():
        return None

    content = note_path.read_text(encoding="utf-8", errors="replace")
    cfg = load_course_config()

    # Detecta módulo e título
    module_dir = note_path.parent.parent
    mod_info = cfg.get_module(module_dir.name)
    mod_display = mod_info.name if mod_info else module_dir.name

    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    raw_title = title_match.group(1).strip() if title_match else note_path.stem

    # Limpa prefixos como 'Aula XX — '
    clean_title = re.sub(r"^(?:Aula\s+\d+\s*[-—:]\s*)", "", raw_title)

    # Identifica objetivos
    objectives_match = re.search(
        r"##\s+🎯\s+Objetivos de Aprendizagem.*?[\r\n]+(.*?)(?=---|\n##)",
        content,
        re.DOTALL,
    )
    objectives_text = (
        objectives_match.group(1).strip()
        if objectives_match
        else f"Implementar os fundamentos e práticas demonstrados em {clean_title}."
    )

    # Extrai códigos de referência
    snippets = extract_code_snippets_from_note(content)
    snippet_preview = ""
    if snippets:
        s = snippets[0]
        snippet_preview = f"Implementar a lógica descrita com base no modelo de referência:\n```{s['lang']}\n{s['code'][:400]}\n...\n```"

    target_dir = output_dir or (module_dir / "labs")
    target_dir.mkdir(parents=True, exist_ok=True)

    task_file = target_dir / f"task-{task_number:02d}-{note_path.stem}.md"

    body = ISSUE_PROMPT_TEMPLATE.format(
        title=f"TASK-{task_number:02d}: {clean_title}",
        course_title=cfg.title,
        module_name=mod_display,
        source_ref=note_path.name,
        context=f"Esta tarefa traduz o conhecimento da aula em um pacote de entrega executável.\n\n### Objetivos Específicos:\n{objectives_text}\n\n{snippet_preview}",
        inputs=f"- Especificação técnica e conceitos definidos em `aulas/{note_path.name}`.\n- Dependências e ambiente de execução.",
        outputs=f"- Código fonte funcional cobrindo o tema `{clean_title}`.\n- Suíte de testes automatizados com assertivas de conformidade.",
        test_command="pytest tests/ -q  # ou comando de teste específico da stack",
    )

    task_file.write_text(body, encoding="utf-8")
    return task_file


def sync_prompts_catalog(root_dir: Path | None = None) -> Path:
    """Atualiza o catálogo global de prompts em docs/prompts/README.md."""
    cfg = load_course_config(root_dir)
    root = cfg.root_dir

    prompts_dir = root / "docs" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    readme_path = prompts_dir / "README.md"

    lines = [
        "# 📚 Catálogo Geral de Prompts Executáveis (Issues-as-a-Prompt)",
        "",
        f"> **Curso / Treinamento:** {cfg.title} — {cfg.institution}",
        "> **Padrão de Engenharia:** Derived from `ta-na-lista` & `agent-kit` standards",
        "> **Objetivo:** Catálogo vivo de especificações executáveis geradas a partir das disciplinas e labs.",
        "",
        "---",
        "",
        "## 📑 Matriz de Prompts e Desafios Práticos por Módulo",
        "",
        "| Módulo | Arquivo de Especificação | Título da Task | Origem Técnica |",
        "| :--- | :--- | :--- | :--- |",
    ]

    modules_dir = root / "modules"
    found_tasks = 0
    if modules_dir.is_dir():
        for mod in sorted(modules_dir.iterdir()):
            if not mod.is_dir() or mod.name.startswith("."):
                continue
            labs_dir = mod / "labs"
            if not labs_dir.is_dir():
                continue
            for t_file in sorted(labs_dir.glob("task-*.md")):
                found_tasks += 1
                first_line = t_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[0]
                task_title = first_line.replace("# TASK:", "").replace("#", "").strip()
                rel_path = f"../../modules/{mod.name}/labs/{t_file.name}"
                lines.append(
                    f"| `{mod.name}` | [{t_file.name}]({rel_path}) | {task_title} | `labs/` |"
                )

    if found_tasks == 0:
        lines.append(
            "| *Nenhuma task gerada ainda* | — | Execute o pipeline ou `generate-tasks` | — |"
        )

    lines.append("")
    lines.append("---")
    lines.append(
        "*Catálogo mantido automaticamente pela esteira knowledge-extractor v1.3.*"
    )
    lines.append("")

    readme_path.write_text("\n".join(lines), encoding="utf-8")
    return readme_path
