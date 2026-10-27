"""Módulo de normalização de nomes de arquivos e registro de auditoria De-Para."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
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

DEFAULT_AUDIT_JSON = Path("docs/logs/file-renaming-audit.json")
DEFAULT_AUDIT_MD = Path("docs/logs/file-renaming-audit.md")

# Mapeamentos conhecidos para identificadores UUIDs e títulos de PDFs institucionais / apostilas
KNOWN_PDF_MAPPINGS: dict[str, str] = {
    "9dd02265-307e-4cc9-a049-88d2801cbb32": "doc-ppc-projeto-pedagogico-curso",
    "e8ecaabe-0726-484b-a0bf-ebbbde333aac": "doc-guia-academico-pos-graduacao-ead",
    "c3216598-f0c6-496a-a199-2d2c219226fc": "apostila-ambientacao-ead-higa",
    "917817da-e408-437e-b4d6-f91134b9f008": "doc-guia-biblioteca-virtual",
    "1b6f2929-0367-4163-a22e-74915c159d32": "apostila-fundamentos-ia-erick-wendel",
    "2fb78d53-bdee-49a0-9e90-d2ef6ef84958": "guia-leituras-recomendadas-modulo-01",
    "88d21a78-57e8-4855-9c08-1a636fd6627e": "guia-links-e-referencias-modulo-01",
    "c836c05f-4994-4126-93e0-ba044202e89d": "guia-codigos-fonte-repositorio-unipds",
}

# Carrega mapeamento externo opcional de PDFs por repositório (pdf_mappings_data.json)
_pdf_mapping_json = Path(__file__).parent / "pdf_mappings_data.json"
if _pdf_mapping_json.exists():
    try:
        KNOWN_PDF_MAPPINGS.update(json.loads(_pdf_mapping_json.read_text(encoding="utf-8")))
    except Exception:
        pass

# Mapeamentos diretos de títulos conhecidos
KNOWN_TITLE_MAPPINGS: dict[str, str] = {
    "01-Aula Complementar para Windows": "setup-00-configuracao-ambiente-windows",
    "02-COMECE POR AQUI": "onboarding-01-comece-por-aqui",
    "03-ENVIO DE DOCUMENTO (TERMO DE COMPROMISSO)": "onboarding-02-termo-de-compromisso-estagio",
    "04-BIBLIOTECA VIRTUAL": "onboarding-03-acesso-biblioteca-virtual",
    "05-ENVIO DE DOCUMENTOS OBRIGATÓRIOS": "onboarding-04-envio-documentos-obrigatorios",
    "05-ENVIO DE DOCUMENTOS OBRIGATORIOS": "onboarding-04-envio-documentos-obrigatorios",
}


def compute_sha256(file_path: Path) -> str:
    """Calcula hash SHA256 de um arquivo para fins de auditoria e integridade."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def slugify(text: str) -> str:
    """Transforma texto em kebab-case limpo, removendo acentos e caracteres especiais."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def suggest_normalized_stem(stem: str, suffix: str) -> str:
    """Gera sugestão padronizada de nome (stem sem extensão) a partir do padrão institucional."""
    # 1. Checagem em mapeamentos diretos de PDF/UUID
    if stem in KNOWN_PDF_MAPPINGS:
        return KNOWN_PDF_MAPPINGS[stem]

    # 2. Checagem em títulos de onboarding / setup
    if stem in KNOWN_TITLE_MAPPINGS:
        return KNOWN_TITLE_MAPPINGS[stem]

    # 3. Padrão: "XX-MÓDULO YY - TÍTULO DA AULA"
    mod_match = re.match(r"^(\d+)\s*[-_]?\s*m[oó]dulo\s*\d+\s*[-_]\s*(.+)$", stem, re.IGNORECASE)
    if mod_match:
        num = int(mod_match.group(1))
        title = mod_match.group(2)
        clean_title = slugify(title)
        # Ajustes de títulos comuns para brevidade e clareza
        clean_title = clean_title.replace("o-que-voce-vera-durante-o-curso-como-praticar-e-nossa-comunidade", "visao-geral-pratica-e-comunidade")
        clean_title = clean_title.replace("machine-learning-deep-learning-e-artificial-intelligence", "machine-learning-deep-learning-ai")
        clean_title = clean_title.replace("criando-e-treinando-minha-primeira-rede-neural-para-determinar-a-categoria-de-alunos", "primeira-rede-neural-js")
        clean_title = clean_title.replace("teachable-machine", "teachable-machine-e-visao-web")
        clean_title = clean_title.replace("conceito-de-redes-neurais-e-como-elas-aprendem", "conceito-redes-neurais-e-como-aprendem")
        return f"aula-{num:02d}-{clean_title}"

    # 4. Padrão: "XX - Título"
    num_match = re.match(r"^(\d+)\s*[-_]\s*(.+)$", stem)
    if num_match:
        num = int(num_match.group(1))
        title = num_match.group(2)
        clean_title = slugify(title)
        return f"aula-{num:02d}-{clean_title}"

    # 5. Padrão: "MÓDULO XXYY - TÍTULO" (ex: MÓDULO 0101 -> aula-01-... | MÓDULO 0201 -> mod02-aula-01-...)
    mod_prefix_match = re.match(r"^m[oó]dulo\s*(\d{2})(\d{2})\s*[-_]\s*(.+)$", stem, re.IGNORECASE)
    if mod_prefix_match:
        mod_num = int(mod_prefix_match.group(1))
        aula_num = int(mod_prefix_match.group(2))
        title = mod_prefix_match.group(3)
        clean_title = slugify(title)
        if mod_num == 1:
            return f"aula-{aula_num:02d}-{clean_title}"
        else:
            return f"mod{mod_num:02d}-aula-{aula_num:02d}-{clean_title}"

    # 5.1 Padrão Anhanguera AVA: "MOD-XX-Ambiente[_ ]Virtual[_ ]Aprendizagem-YY"
    ava_match = re.match(r"^(?:mod[-_]?(\d+)[-_]?)?ambiente[_\s]+virtual[_\s]+aprendizagem[-_]?(\d+)$", stem, re.IGNORECASE)
    if ava_match:
        mod_part = int(ava_match.group(1)) if ava_match.group(1) else 1
        video_num = int(ava_match.group(2))
        return f"mod{mod_part:02d}-video-{video_num:02d}"

    # 5.2 Padrão de apresentação
    if re.match(r"^mod[-_]?\d*[-_]?apresentacao$", stem, re.IGNORECASE):
        return "video-00-apresentacao"

    # 6. Fallback genérico
    clean = slugify(stem)
    if suffix.lower() == ".pdf" and not (clean.startswith("apostila") or clean.startswith("doc") or clean.startswith("guia") or clean.startswith("aula")):
        clean = f"doc-{clean}"
    elif suffix.lower() in {".mp4", ".mkv", ".mov"} and not (clean.startswith("aula") or clean.startswith("onboarding") or clean.startswith("setup") or clean.startswith("midia")):
        clean = f"midia-{clean}"
    return clean


class FileNormalizer:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir or ".").resolve()
        self.audit_json_path = self.root_dir / DEFAULT_AUDIT_JSON
        self.audit_md_path = self.root_dir / DEFAULT_AUDIT_MD

    def load_audit_log(self) -> list[dict[str, Any]]:
        """Carrega o histórico de auditoria existente."""
        if self.audit_json_path.exists():
            try:
                return json.loads(self.audit_json_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def save_audit_log(self, records: list[dict[str, Any]]) -> None:
        """Persiste o log de auditoria em JSON e atualiza a tabela em Markdown."""
        self.audit_json_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

        # Gera o Markdown legível
        md_lines = [
            "# 📋 Registro de Auditoria: Normalização de Nomes de Arquivos (De-Para)",
            "",
            "> Log persistente de mapeamento de arquivos brutos, transcrições e documentos normalizados no repositório de formação técnica.",
            "",
            f"*Última atualização: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}* | *Total de Registros: {len(records)}*",
            "",
            "| # | Data/Hora (UTC) | Módulo / Contexto | De (Nome Original) | Para (Nome Normalizado) | Tipo | SHA-256 (Parcial) |",
            "| :-: | :--- | :--- | :--- | :--- | :---: | :--- |",
        ]

        for idx, rec in enumerate(records, start=1):
            ts = rec.get("timestamp", "").replace("T", " ").split(".")[0]
            modulo = rec.get("module", "-")
            old_name = rec.get("old_name", "-")
            new_name = rec.get("new_name", "-")
            ftype = rec.get("type", "-")
            sha_short = (rec.get("sha256") or "")[:8]
            md_lines.append(f"| {idx} | {ts} | `{modulo}` | `{old_name}` | **`{new_name}`** | `{ftype}` | `{sha_short}` |")

        md_lines.append("")
        self.audit_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    def plan_module_normalization(self, module_dir: Path) -> list[dict[str, Any]]:
        """Mapeia os arquivos em materiais/raw/ e correspondentes em materiais/transcricoes/."""
        module_dir = Path(module_dir).resolve()
        raw_dir = module_dir / "materiais" / "raw"
        trans_dir = module_dir / "materiais" / "transcricoes"

        if not raw_dir.exists():
            return []

        operations: list[dict[str, Any]] = []

        for raw_file in sorted(raw_dir.iterdir()):
            if not raw_file.is_file() or raw_file.name.startswith("."):
                continue

            old_stem = raw_file.stem
            suffix = raw_file.suffix
            new_stem = suggest_normalized_stem(old_stem, suffix)
            new_name = f"{new_stem}{suffix}"

            if old_stem != new_stem:
                # Entrada do arquivo raw
                operations.append({
                    "category": "raw",
                    "module": module_dir.name,
                    "old_path": raw_file,
                    "new_path": raw_dir / new_name,
                    "old_name": raw_file.name,
                    "new_name": new_name,
                    "type": "video" if suffix.lower() in {".mp4", ".mkv", ".mov", ".avi"} else ("pdf" if suffix.lower() == ".pdf" else "raw"),
                })

                # Mapeia transcrições/extrações associadas em materiais/transcricoes
                if trans_dir.exists():
                    for ext in [".txt", ".srt", ".md"]:
                        associated = trans_dir / f"{old_stem}{ext}"
                        if associated.exists():
                            new_assoc_name = f"{new_stem}{ext}"
                            operations.append({
                                "category": "transcript",
                                "module": module_dir.name,
                                "old_path": associated,
                                "new_path": trans_dir / new_assoc_name,
                                "old_name": associated.name,
                                "new_name": new_assoc_name,
                                "type": f"transcript{ext}",
                            })

        return operations

    def execute_normalization(
        self,
        module_dir: Path,
        dry_run: bool = True,
    ) -> list[dict[str, Any]]:
        """Executa a normalização planejada ou exibe prévia se dry_run=True."""
        module_dir = Path(module_dir).resolve()
        operations = self.plan_module_normalization(module_dir)

        if not operations:
            console.print(f"[yellow]ℹ Todos os arquivos em {module_dir.name} já estão normalizados.[/yellow]")
            return []

        table = Table(title=f"Normalização de Arquivos: {module_dir.name} ({'SIMULAÇÃO' if dry_run else 'APLICAÇÃO REAL'})")
        table.add_column("Categoria", style="magenta")
        table.add_column("De (Nome Original)", style="cyan")
        table.add_column("Para (Normalizado)", style="bold green")
        table.add_column("Tipo", style="yellow")

        for op in operations:
            table.add_row(op["category"], op["old_name"], op["new_name"], op["type"])

        console.print(table)

        if dry_run:
            console.print("[bold yellow]⚠️ Modo Dry-Run (Simulação). Nenhuma alteração foi realizada em disco.[/bold yellow]")
            console.print("[dim]Para aplicar as alterações e gerar o log de auditoria, use a flag --apply.[/dim]\n")
            return operations

        # Executa as alterações e registra auditoria
        audit_records = self.load_audit_log()
        executed_records: list[dict[str, Any]] = []

        for op in operations:
            old_path: Path = op["old_path"]
            new_path: Path = op["new_path"]

            if not old_path.exists():
                continue

            sha256_hash = compute_sha256(old_path)
            size_bytes = old_path.stat().st_size

            # Se new_path já existe, verifica se é idêntico (duplicata de renomeação prévia)
            if new_path.exists():
                if new_path.stat().st_size == size_bytes and compute_sha256(new_path) == sha256_hash:
                    # Remove a cópia original legada duplicada
                    old_path.unlink()
                else:
                    console.print(f"[yellow]⚠️ Destino {new_path.name} já existe com conteúdo diferente. Ignorando renomeação.[/yellow]")
                    continue
            else:
                # Renomeia
                old_path.rename(new_path)

            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "module": op["module"],
                "category": op["category"],
                "old_name": op["old_name"],
                "new_name": op["new_name"],
                "type": op["type"],
                "size_bytes": size_bytes,
                "sha256": sha256_hash,
            }
            audit_records.append(record)
            executed_records.append(record)

        self.save_audit_log(audit_records)
        console.print(f"[bold green]✓ {len(executed_records)} arquivos renomeados com sucesso e auditados em:[/bold green]")
        console.print(f"  - [blue]{self.audit_json_path}[/blue]")
        console.print(f"  - [blue]{self.audit_md_path}[/blue]")

        return executed_records
