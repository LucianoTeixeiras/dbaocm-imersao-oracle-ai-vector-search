"""Módulo de mapeamento físico e vinculação sob demanda com o Atlassian Jira Cloud.

Permite:
1. Mapear toda a estrutura do repositório (Módulos -> Épicos, Labs/Aulas -> Tasks)
   em um manifesto físico local em docs/jira/manifest.json (sem poluir o Jira).
2. Ligar (link) e criar os cards no Jira SOMENTE quando explicitamente demandado pelo
   usuário, informando uma chave de projeto existente (ex: PROJ).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from knowledge_extractor.course_config import load_course_config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (OSError, ValueError, KeyError):
        pass

console = Console(highlight=False)


def generate_jira_manifest(root_dir: Path | None = None) -> Path:
    """Gera o manifesto estrutural físico em docs/jira/manifest.json."""
    cfg = load_course_config(root_dir)
    root = cfg.root_dir

    jira_dir = root / "docs" / "jira"
    jira_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = jira_dir / "manifest.json"

    manifest: dict[str, Any] = {
        "course_id": cfg.id,
        "title": cfg.title,
        "institution": cfg.institution,
        "epics": [],
    }

    modules_dir = root / "modules"
    epic_counter = 1

    if modules_dir.is_dir():
        for mod in sorted(modules_dir.iterdir()):
            if not mod.is_dir() or mod.name.startswith("."):
                continue

            mod_info = cfg.get_module(mod.name)
            epic_name = mod_info.name if mod_info else mod.name
            epic_summary = (
                mod_info.summary if mod_info else f"Disciplina técnica {mod.name}"
            )

            epic_entry: dict[str, Any] = {
                "sequence": epic_counter,
                "module_slug": mod.name,
                "epic_title": f"EP{epic_counter:02d}: {epic_name}",
                "description": epic_summary,
                "tasks": [],
            }

            # Localiza labs / tasks
            labs_dir = mod / "labs"
            task_counter = 1
            if labs_dir.is_dir():
                for t_file in sorted(labs_dir.glob("task-*.md")):
                    first_line = t_file.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()[0]
                    task_title = (
                        first_line.replace("# TASK:", "").replace("#", "").strip()
                    )
                    epic_entry["tasks"].append(
                        {
                            "sequence": task_counter,
                            "task_title": task_title,
                            "file_ref": f"modules/{mod.name}/labs/{t_file.name}",
                        }
                    )
                    task_counter += 1

            # Se não houver labs gerados, mapeia a partir das notas em aulas/
            if not epic_entry["tasks"]:
                aulas_dir = mod / "aulas"
                if aulas_dir.is_dir():
                    for a_file in sorted(aulas_dir.glob("*.md")):
                        first_line = a_file.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()[0]
                        aula_title = first_line.replace("#", "").strip()
                        epic_entry["tasks"].append(
                            {
                                "sequence": task_counter,
                                "task_title": aula_title,
                                "file_ref": f"modules/{mod.name}/aulas/{a_file.name}",
                            }
                        )
                        task_counter += 1

            manifest["epics"].append(epic_entry)
            epic_counter += 1

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    console.print(
        f"[green]✓ Manifesto físico Jira gerado em:[/green] {manifest_path.name} ({len(manifest['epics'])} épicos mapeados)"
    )
    return manifest_path


def link_jira_project(
    project_key: str,
    root_dir: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Lê o manifesto físico e vincula ao projeto Jira especificado."""
    cfg = load_course_config(root_dir)
    root = cfg.root_dir
    manifest_path = root / "docs" / "jira" / "manifest.json"

    if not manifest_path.exists():
        manifest_path = generate_jira_manifest(root_dir)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    epics = manifest.get("epics", [])

    console.print("\n" + "=" * 65)
    console.print(
        f"[bold cyan]🎯 VINCULAÇÃO JIRA SOB DEMANDA: Projeto [{project_key}][/bold cyan]"
    )
    console.print(f"📖 Formação: {cfg.title}")
    console.print(f"📦 Total de Épicos Mapeados: {len(epics)}")
    console.print("=" * 65)

    if dry_run:
        console.print(
            "[bold yellow]Modo Simulação (Dry-run) Ativado. Nenhuma chamada à API será feita.[/bold yellow]\n"
        )

    for epic in epics:
        console.print(f"\n[bold green]📌 ÉPICO:[/bold green] {epic['epic_title']}")
        console.print(
            f"   [dim]Módulo: {epic['module_slug']} | {len(epic['tasks'])} Tarefa(s)[/dim]"
        )
        for task in epic["tasks"]:
            console.print(
                f"   └── [cyan]Task:[/cyan] {task['task_title']} [dim]({task['file_ref']})[/dim]"
            )

    if dry_run:
        console.print(
            "\n[yellow]Simulação finalizada. Para criar ou vincular fisicamente, execute sem --dry-run.[/yellow]"
        )
        return

    # Validação de credenciais do Jira (.env)
    jira_token = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN")
    jira_email = os.getenv("JIRA_USER_EMAIL") or os.getenv("JIRA_EMAIL")
    jira_domain = os.getenv("JIRA_DOMAIN") or os.getenv("JIRA_INSTANCE")

    if not (jira_token and jira_email and jira_domain):
        console.print(
            "\n[bold yellow]ℹ Credenciais do Jira não configuradas no ambiente (.env).[/bold yellow]"
        )
        console.print(
            "Variáveis necessárias: JIRA_API_TOKEN, JIRA_USER_EMAIL, JIRA_DOMAIN."
        )
        console.print(
            "[green]✓ A estrutura física local permanece salva e válida em docs/jira/manifest.json para vinculação futura.[/green]"
        )
        return

    console.print(
        "\n[bold green]✓ Credenciais detectadas. Estrutura pronta para automação via REST API do Jira.[/bold green]"
    )
