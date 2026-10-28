"""Módulo de sincronização automática do portal/catalogo.md."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


class CatalogSynchronizer:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir or ".").resolve()

    def sync_module_catalog(self, module_dir: Path) -> Path | None:
        """Sincroniza o arquivo portal/catalogo.md com base nos arquivos presentes em raw, transcricoes e aulas."""
        module_dir = Path(module_dir).resolve()
        raw_dir = module_dir / "materiais" / "raw"
        trans_dir = module_dir / "materiais" / "transcricoes"
        aulas_dir = module_dir / "aulas"
        portal_dir = module_dir / "portal"
        catalogo_file = portal_dir / "catalogo.md"

        if not portal_dir.exists():
            portal_dir.mkdir(parents=True, exist_ok=True)

        # Mapeamento de aulas identificadas
        lessons_data: dict[str, dict[str, Any]] = {}

        def register_item(key_num: str, name_stem: str, has_raw: bool, has_trans: bool, has_aula: Path | None):
            if key_num not in lessons_data:
                lessons_data[key_num] = {
                    "num": key_num,
                    "title": name_stem,
                    "has_raw": False,
                    "has_trans": False,
                    "aula_file": None,
                }
            if has_raw:
                lessons_data[key_num]["has_raw"] = True
            if has_trans:
                lessons_data[key_num]["has_trans"] = True
            if has_aula:
                lessons_data[key_num]["aula_file"] = has_aula
            if name_stem and lessons_data[key_num]["title"] == key_num:
                lessons_data[key_num]["title"] = name_stem

        def extract_num_and_title(stem: str) -> tuple[str, str]:
            # Remove prefixos comuns de tipos de arquivos para parear simetricamente
            # ex: midia-aula-01-intro, aula-01-intro, doc-01-intro -> 01-intro
            clean = re.sub(r"^(?:aula-|midia-|doc-|video-)+", "", stem, flags=re.IGNORECASE)

            # Padrão: "aula-01-titulo" ou "01-titulo" ou "mod01-aula-01-titulo"
            m = re.match(r"^(?:mod\d+[-_])?(?:aula[-_])?(\d+)[-_ ]*(.*)$", clean, re.IGNORECASE)
            if m:
                num_str = f"{int(m.group(1)):02d}"
                raw_title = m.group(2).replace("-", " ").strip()
                title = raw_title.title() if raw_title else f"Aula {num_str}"
                return num_str, title

            # Sem numeração inicial: usa o slug limpo como chave unificada
            clean_title = clean.replace("-", " ").strip().title() or "Aula"
            return clean.lower(), clean_title

        # 1. Varre materiais/raw
        if raw_dir.exists():
            for f in sorted(raw_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    num, title = extract_num_and_title(f.stem)
                    register_item(num, title, has_raw=True, has_trans=False, has_aula=None)

        # 2. Varre materiais/transcricoes
        if trans_dir.exists():
            for f in sorted(trans_dir.iterdir()):
                if f.is_file() and f.suffix.lower() == ".txt" and not f.name.startswith("."):
                    num, title = extract_num_and_title(f.stem)
                    register_item(num, title, has_raw=False, has_trans=True, has_aula=None)

        # 3. Varre aulas/
        if aulas_dir.exists():
            for f in sorted(aulas_dir.iterdir()):
                if f.is_file() and f.suffix.lower() == ".md" and not f.name.startswith("."):
                    num, title = extract_num_and_title(f.stem)
                    register_item(num, title, has_raw=False, has_trans=False, has_aula=f)

        if not lessons_data:
            console.print(f"[yellow]ℹ Nenhum arquivo de aula encontrado para catalogar em {module_dir.name}[/yellow]")
            return None

        # Ordena as aulas
        sorted_keys = sorted(lessons_data.keys(), key=lambda x: int(x) if x.isdigit() else 999)

        # Gera o novo catalogo.md
        module_title = module_dir.name.replace("-", " ").title()
        lines = [
            f"# Catálogo de Aulas no Portal/AVA — {module_title}",
            "",
            "Registro oficial de acompanhamento do consumo de aulas no portal do curso e status de documentação no repositório.",
            "",
            "| # Aula | Título da Aula no Portal | Status | Notas no Repo | Insumos |",
            "| :---: | :--- | :---: | :--- | :--- |",
        ]

        summary_table = Table(title=f"Sincronização de Catálogo: {module_dir.name}")
        summary_table.add_column("#", justify="center", style="cyan")
        summary_table.add_column("Título", style="white")
        summary_table.add_column("Status", style="bold")
        summary_table.add_column("Notas no Repo", style="green")

        for k in sorted_keys:
            item = lessons_data[k]
            num_display = f"**{item['num']}**"
            title_display = item["title"]

            if item["aula_file"]:
                status = "✅ Concluído"
                nota_link = f"[`{item['aula_file'].name}`](../aulas/{item['aula_file'].name})"
            elif item["has_trans"]:
                status = "🟡 Transcrito (Pendente Síntese)"
                nota_link = "*Pendente*"
            elif item["has_raw"]:
                status = "⚪ Mídia Baixada (Pendente Transcrição)"
                nota_link = "-"
            else:
                status = "⚪ Planejado"
                nota_link = "-"

            insumos = []
            if item["has_raw"]:
                insumos.append("Vídeo/PDF")
            if item["has_trans"]:
                insumos.append("Transcrição (.txt)")
            insumos_str = ", ".join(insumos) if insumos else "-"

            lines.append(f"| {num_display} | {title_display} | {status} | {nota_link} | {insumos_str} |")
            summary_table.add_row(item["num"], title_display, status, nota_link)

        lines.append("")
        catalogo_file.write_text("\n".join(lines), encoding="utf-8")

        console.print(summary_table)
        console.print(f"[bold green]✓ Catálogo sincronizado com sucesso:[/bold green] {catalogo_file}")
        return catalogo_file
