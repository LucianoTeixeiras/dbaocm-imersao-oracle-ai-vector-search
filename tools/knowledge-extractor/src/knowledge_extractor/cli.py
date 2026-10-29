"""Interface de linha de comando para o Knowledge Extractor."""

from __future__ import annotations

import sys
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table

from knowledge_extractor.audio_transcriber import Transcriber
from knowledge_extractor.catalog_sync import CatalogSynchronizer
from knowledge_extractor.file_normalizer import FileNormalizer
from knowledge_extractor.metrics import track
from knowledge_extractor.pdf_parser import extract_text_from_pdf
from knowledge_extractor.pipeline import ModulePipeline, run_interactive_menu
from knowledge_extractor.synthesizer import LectureSynthesizer, _is_scaffold_file

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


@click.group()
def cli() -> None:
    """Ferramenta de extração de conhecimento, transcrição e síntese técnica para programas de treinamento e pós-graduação."""
    pass


@cli.command("normalize-files")
@click.argument("module_dir", type=click.Path(exists=True, path_type=Path))
@click.option("--apply", "apply_changes", is_flag=True, default=False, help="Aplica as renomeações e grava no log de auditoria")
def normalize_files_cmd(module_dir: Path, apply_changes: bool) -> None:
    """Normaliza nomes de arquivos em materiais/raw/ e materiais/transcricoes/ gerando log de auditoria De-Para."""
    normalizer = FileNormalizer()
    normalizer.execute_normalization(module_dir, dry_run=not apply_changes)


@cli.command("audit-log")
@click.option("-s", "--search", default=None, help="Filtra o log por termo de busca (nome antigo, novo ou módulo)")
def audit_log_cmd(search: str | None) -> None:
    """Exibe o histórico de auditoria de arquivos renomeados (De-Para)."""
    normalizer = FileNormalizer()
    records = normalizer.load_audit_log()

    if not records:
        console.print("[yellow]ℹ Nenhum registro de auditoria encontrado.[/yellow]")
        return

    if search:
        search_lower = search.lower()
        records = [
            r for r in records
            if search_lower in r.get("old_name", "").lower()
            or search_lower in r.get("new_name", "").lower()
            or search_lower in r.get("module", "").lower()
            or search_lower in r.get("sha256", "").lower()
        ]

    table = Table(title=f"Histórico de Auditoria De-Para ({len(records)} registros)")
    table.add_column("Data (UTC)", style="dim")
    table.add_column("Módulo", style="cyan")
    table.add_column("De (Original)", style="magenta")
    table.add_column("Para (Normalizado)", style="bold green")
    table.add_column("Tipo", style="yellow")
    table.add_column("SHA-256", style="dim")

    for r in records:
        ts = r.get("timestamp", "").replace("T", " ").split(".")[0]
        sha = (r.get("sha256") or "")[:8]
        table.add_row(ts, r.get("module", "-"), r.get("old_name", "-"), r.get("new_name", "-"), r.get("type", "-"), sha)

    console.print(table)


@cli.command("transcribe")
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=None, help="Diretório de saída para .txt e .srt")
@click.option("-m", "--model-size", default="base", help="Tamanho do modelo Whisper (tiny, base, small, medium, large-v3)")
@click.option("-d", "--device", default="auto", help="Device de execução ('cpu', 'cuda' ou 'auto')")
@click.option("-l", "--language", default=None, help="Idioma do áudio (ex: pt, en). Padrão: course.yaml ou auto-detecção.")
def transcribe_cmd(
    input_file: Path,
    output_dir: Path | None,
    model_size: str,
    device: str,
    language: str | None,
) -> None:
    """Transcreve um arquivo de vídeo (.mp4, .mkv) ou áudio (.mp3, .wav)."""
    transcriber = Transcriber(model_size=model_size, device=device, language=language)
    transcriber.transcribe_file(input_file, output_dir=output_dir)


@cli.command("extract-pdf")
@click.argument("pdf_file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=None, help="Diretório de saída para .md")
@click.option("--as-text", is_flag=True, default=False, help="Salva como .txt simples ao invés de Markdown estruturado")
def extract_pdf_cmd(
    pdf_file: Path,
    output_dir: Path | None,
    as_text: bool,
) -> None:
    """Extrai texto e páginas de um arquivo PDF."""
    extract_text_from_pdf(pdf_file, output_dir=output_dir, as_markdown=not as_text)


@cli.command("process-module")
@click.argument("module_dir", type=click.Path(exists=True, path_type=Path))
@click.option("-m", "--model-size", default="base", help="Tamanho do modelo Whisper (tiny, base, small, medium, large-v3)")
@click.option("-f", "--force", is_flag=True, default=False, help="Força o reprocessamento de arquivos já extraídos")
def process_module_cmd(module_dir: Path, model_size: str, force: bool) -> None:
    """Processa todos os arquivos em `materiais/raw/` de um módulo e gera saídas em `materiais/transcricoes/`."""
    raw_dir = module_dir / "materiais" / "raw"
    transcricoes_dir = module_dir / "materiais" / "transcricoes"

    if not raw_dir.exists():
        console.print(f"[red]❌ Diretório raw não encontrado:[/red] {raw_dir}")
        return

    transcricoes_dir.mkdir(parents=True, exist_ok=True)

    media_extensions = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wav", ".mp3", ".m4a"}
    pdf_extensions = {".pdf"}

    all_files = [p for p in raw_dir.iterdir() if p.is_file() and not p.name.startswith(".")]

    if not all_files:
        console.print(f"[yellow]ℹ Nenhum arquivo encontrado em:[/yellow] {raw_dir}")
        return

    table = Table(title=f"Arquivos em {module_dir.name} (materiais/raw)")
    table.add_column("Arquivo", style="cyan")
    table.add_column("Tipo", style="magenta")
    table.add_column("Tamanho", justify="right")
    table.add_column("Status", style="green")

    pending_media: list[Path] = []
    pending_pdf: list[Path] = []

    for f in all_files:
        size_mb = f.stat().st_size / (1024 * 1024)
        ext = f.suffix.lower()
        stem = f.stem
        is_media = ext in media_extensions
        is_pdf = ext in pdf_extensions
        tipo = "Vídeo/Áudio" if is_media else ("PDF" if is_pdf else "Outro")

        if is_media:
            txt_exists = (transcricoes_dir / f"{stem}.txt").exists()
            srt_exists = (transcricoes_dir / f"{stem}.srt").exists()
            if not force and txt_exists and srt_exists:
                status = "[dim]Já transcrito (Skip)[/dim]"
            else:
                status = "[bold yellow]Pendente[/bold yellow]"
                pending_media.append(f)
        elif is_pdf:
            md_exists = (transcricoes_dir / f"{stem}.md").exists()
            if not force and md_exists:
                status = "[dim]Já extraído (Skip)[/dim]"
            else:
                status = "[bold yellow]Pendente[/bold yellow]"
                pending_pdf.append(f)
        else:
            status = "[dim]Ignorado[/dim]"

        table.add_row(f.name, tipo, f"{size_mb:.1f} MB", status)

    console.print(table)

    # Processa mídias pendentes
    if pending_media:
        from knowledge_extractor.course_config import load_course_config
        cfg = load_course_config()
        audio_lang = cfg.audio_language or "pt"
        transcriber = Transcriber(model_size=model_size, device="auto", language=audio_lang)
        for media in pending_media:
            transcriber.transcribe_file(media, output_dir=transcricoes_dir)

    # Processa PDFs pendentes
    for pdf in pending_pdf:
        extract_text_from_pdf(pdf, output_dir=transcricoes_dir, as_markdown=True)

    console.print(f"[bold green]✨ Processamento concluído com sucesso![/bold green]")


@cli.command("synthesize")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=None, help="Diretório de saída para aulas/ (padrão: <modulo>/aulas/)")
@click.option("-m", "--gemini-model", default=None, help="Modelo Gemini para síntese técnica (padrão: env GEMINI_MODEL)")
@click.option("-p", "--provider", type=click.Choice(["auto", "ollama", "gemini"]), default=None, help="auto = fallback Ollama→Gemini; ollama/gemini = força um provedor (padrão: env SYNTHESIS_PROVIDER)")
@click.option("--ollama-model", default=None, help="Modelo Ollama local (padrão: env OLLAMA_MODEL)")
@click.option("-f", "--force", is_flag=True, default=False, help="Força a regeração mesmo se a nota já existir")
@click.option("--vision/--no-vision", default=True, help="Ativa (padrão) ou desativa a extração e análise multimodal dos frames de vídeo com Gemini")
def synthesize_cmd(target: Path, output_dir: Path | None, gemini_model: str | None, provider: str | None, ollama_model: str | None, force: bool, vision: bool) -> None:
    """Sintetiza notas estruturadas de estudo em Markdown (aulas/) a partir de transcrições .txt."""
    synthesizer = LectureSynthesizer(gemini_model=gemini_model, ollama_model=ollama_model, provider=provider)
    synthesizer.print_mode()

    if target.is_file():
        module_dir = target.parent.parent.parent
        out_dir = output_dir or (module_dir / "aulas")
        target_note = out_dir / f"{target.stem}.md"
        if target_note.exists() and not force and not _is_scaffold_file(target_note):
            console.print(f"[dim]Nota já existente (Skip):[/dim] {target_note.name}")
            return
        keyframes = None
        if vision:
            from knowledge_extractor.video_frames import extract_video_keyframes, VIDEO_EXTENSIONS
            raw_dir = module_dir / "materiais" / "raw"
            video_candidates = [raw_dir / f"{target.stem}{ext}" for ext in VIDEO_EXTENSIONS if (raw_dir / f"{target.stem}{ext}").exists()]
            if video_candidates:
                frames_dir = module_dir / "materiais" / "frames" / target.stem
                keyframes = extract_video_keyframes(video_candidates[0], output_dir=frames_dir, force=force)

        synthesizer.process_file(transcript_file=target, module_dir=module_dir, output_dir=output_dir, force=force, keyframes=keyframes)
    elif target.is_dir():
        trans_dir = target / "materiais" / "transcricoes" if (target / "materiais" / "transcricoes").exists() else target
        txt_files = sorted([f for f in trans_dir.iterdir() if f.is_file() and f.suffix.lower() == ".txt" and not f.name.startswith(".")])
        if not txt_files:
            console.print(f"[yellow]ℹ Nenhum arquivo .txt de transcrição encontrado em {trans_dir}[/yellow]")
            return

        raw_dir = target / "materiais" / "raw"
        out_dir = output_dir or (target / "aulas")
        for txt in txt_files:
            target_note = out_dir / f"{txt.stem}.md"
            if target_note.exists() and not force and not _is_scaffold_file(target_note):
                console.print(f"[dim]Nota já existente (Skip):[/dim] {target_note.name}")
                continue
            keyframes = None
            if vision and raw_dir.exists():
                from knowledge_extractor.video_frames import extract_video_keyframes, VIDEO_EXTENSIONS
                video_candidates = [raw_dir / f"{txt.stem}{ext}" for ext in VIDEO_EXTENSIONS if (raw_dir / f"{txt.stem}{ext}").exists()]
                if video_candidates:
                    frames_dir = target / "materiais" / "frames" / txt.stem
                    keyframes = extract_video_keyframes(video_candidates[0], output_dir=frames_dir, force=force)

            synthesizer.process_file(transcript_file=txt, module_dir=target, output_dir=output_dir, force=force, keyframes=keyframes)


@cli.command("fix-mermaid")
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--apply", "apply_changes", is_flag=True, default=False, help="Grava as correções (sem ela é apenas simulação)")
def fix_mermaid_cmd(target: Path, apply_changes: bool) -> None:
    """Corrige a sintaxe dos diagramas Mermaid em notas .md (arquivo, pasta aulas/ ou módulo)."""
    from knowledge_extractor.mermaid_fixer import sanitize_file

    if target.is_file():
        files = [target]
    else:
        base = target / "aulas" if (target / "aulas").is_dir() else target
        files = sorted(base.rglob("*.md"))

    total = 0
    for f in files:
        n = sanitize_file(f, dry_run=not apply_changes)
        if n:
            total += n
            console.print(f"[cyan]🩹 {f.name}[/cyan]: {n} diagrama(s) {'corrigido(s)' if apply_changes else 'a corrigir'}")
    if not total:
        console.print("[green]✓ Nenhum diagrama precisando de correção.[/green]")
    elif not apply_changes:
        console.print("[yellow]Simulação. Rode novamente com --apply para gravar.[/yellow]")


@cli.command("sync-catalog")
@click.argument("module_dir", type=click.Path(exists=True, path_type=Path))
def sync_catalog_cmd(module_dir: Path) -> None:
    """Sincroniza o arquivo portal/catalogo.md do módulo com base nos insumos e notas existentes."""
    synchronizer = CatalogSynchronizer()
    synchronizer.sync_module_catalog(module_dir)


@cli.command("attach-figures")
@click.argument("module_dir", type=click.Path(exists=True, path_type=Path))
@click.option("--max-figures", default=3, show_default=True, help="Máximo de figuras por aula")
@click.option("-m", "--gemini-model", default=None, help="Modelo de visão (padrão: env GEMINI_MODEL)")
@click.option("-f", "--force", is_flag=True, default=False, help="Reavalia notas que já possuem figuras anexadas")
def attach_figures_cmd(module_dir: Path, max_figures: int, gemini_model: str | None, force: bool) -> None:
    """Seleciona frames de alto valor visual, converte para WebP em materiais/figuras/ e anexa nas notas existentes."""
    from knowledge_extractor.figures import MARKER_START
    from knowledge_extractor.video_frames import VIDEO_EXTENSIONS, extract_video_keyframes

    synthesizer = LectureSynthesizer(gemini_model=gemini_model)
    if not synthesizer.client:
        console.print("[red]❌ GEMINI_API_KEY não configurada — a seleção de figuras exige o modelo de visão.[/red]")
        return

    aulas_dir = module_dir / "aulas"
    raw_dir = module_dir / "materiais" / "raw"
    frames_dir = module_dir / "materiais" / "frames"

    notes = sorted(f for f in aulas_dir.glob("*.md") if not f.name.startswith("."))
    if not notes:
        console.print(f"[yellow]ℹ Nenhuma nota encontrada em {aulas_dir}[/yellow]")
        return

    total = 0
    for note in notes:
        if not force and MARKER_START in note.read_text(encoding="utf-8", errors="replace"):
            console.print(f"[dim]Figuras já anexadas (Skip):[/dim] {note.name}")
            continue
        keyframes = sorted((frames_dir / note.stem).glob("frame_*.jpg")) if (frames_dir / note.stem).exists() else []
        if not keyframes:
            video = next((raw_dir / f"{note.stem}{ext}" for ext in VIDEO_EXTENSIONS if (raw_dir / f"{note.stem}{ext}").exists()), None)
            if video is None:
                console.print(f"[dim]Sem vídeo/frames para {note.name} — ignorado.[/dim]")
                continue
            keyframes = extract_video_keyframes(video, output_dir=frames_dir / note.stem)

        if not keyframes:
            continue

        with track("figuras", modulo=module_dir.name, item=note.name) as info:
            info["keyframes_total"] = len(keyframes)
            count = synthesizer.curate_and_attach_figures(
                note_path=note,
                module_dir=module_dir,
                lesson_stem=note.stem,
                lesson_title=note.stem.replace("-", " ").title(),
                keyframes=keyframes,
                max_figures=max_figures,
            )
            info["figuras_webp"] = count
            total += count

    console.print(f"[bold green]✓ {total} figura(s) anexada(s) em {len(notes)} nota(s).[/bold green]")


@cli.command("pipeline")
@click.argument("module_dir", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.option("--all", "run_all_steps", is_flag=True, default=False, help="Executa todas as etapas sequencialmente em lote")
@click.option("--all-modules", "run_all_modules", is_flag=True, default=False, help="Executa o ciclo completo em TODOS os módulos da formação em sequência")
@click.option("--step", type=int, default=None, help="Executa uma etapa específica (1 a 5)")
@click.option("-m", "--model-size", default="base", help="Tamanho do modelo Whisper (tiny, base, small, medium, large-v3)")
@click.option("--gemini-model", default=None, help="Modelo Gemini para síntese de notas (padrão: env GEMINI_MODEL)")
@click.option("-p", "--provider", type=click.Choice(["auto", "ollama", "gemini"]), default=None, help="auto = fallback Ollama→Gemini; ollama/gemini = força um provedor (padrão: env SYNTHESIS_PROVIDER)")
@click.option("--ollama-model", default=None, help="Modelo Ollama local (padrão: env OLLAMA_MODEL)")
@click.option("-f", "--force", is_flag=True, default=False, help="Força o reprocessamento de itens já existentes")
@click.option("--vision/--no-vision", default=True, help="Ativa (padrão) ou desativa a extração de frames de vídeo e síntese multimodal com Gemini")
def pipeline_cmd(
    module_dir: Path | None,
    run_all_steps: bool,
    run_all_modules: bool,
    step: int | None,
    model_size: str,
    gemini_model: str | None,
    provider: str | None,
    ollama_model: str | None,
    force: bool,
    vision: bool,
) -> None:
    """Orquestrador do Pipeline Fim-a-Fim (Interativo, Lote ou Ciclo Global)."""
    from knowledge_extractor.pipeline import discover_modules

    all_mods = discover_modules()
    if not all_mods:
        console.print("[red]❌ Nenhuma pasta de módulo encontrada em modules/.[/red]")
        console.print("Gere a estrutura inicial com: [yellow]knowledge-extractor scaffold-modules[/yellow]")
        raise SystemExit(1)

    target_module = module_dir if module_dir is not None else all_mods[0]

    if run_all_modules:
        console.print(f"[bold cyan]🚀 Executando Ciclo Global em TODOS os {len(all_mods)} módulos da formação...[/bold cyan]")
        for idx, m_dir in enumerate(all_mods, start=1):
            console.print(f"\n[bold cyan]════════════════════════════════════════════════════════════[/bold cyan]")
            console.print(f"[bold green]▶ Processando Módulo {idx}/{len(all_mods)}: {m_dir.name}[/bold green]")
            console.print(f"[bold cyan]════════════════════════════════════════════════════════════[/bold cyan]")
            p = ModulePipeline(
                module_dir=m_dir,
                model_size=model_size,
                gemini_model=gemini_model,
                provider=provider,
                ollama_model=ollama_model,
                vision=vision,
            )
            p.run_all(force=force)
        console.print(Panel.fit("[bold green]✨ Ciclo Global Concluído com Sucesso em Todos os Módulos![/bold green]", border_style="green"))
        return

    pipeline = ModulePipeline(
        module_dir=target_module,
        model_size=model_size,
        gemini_model=gemini_model,
        provider=provider,
        ollama_model=ollama_model,
        vision=vision,
    )

    if step is not None:
        if step == 1:
            pipeline.run_step_1_normalization(apply=True)
        elif step == 2:
            pipeline.run_step_2_extraction(force=force)
        elif step == 3:
            pipeline.run_step_3_synthesis(force=force)
        elif step == 4:
            pipeline.run_step_4_catalog_sync()
        elif step == 5:
            pipeline.run_step_5_governance()
        else:
            console.print(f"[red]❌ Etapa inválida: {step}. Escolha entre 1 e 5.[/red]")
    elif run_all_steps:
        pipeline.run_all(force=force)
    else:
        run_interactive_menu(pipeline=pipeline)


@cli.command("scaffold-modules")
@click.option("--dry-run", is_flag=True, default=False, help="Mostra o plano sem criar arquivos")
def scaffold_modules_cmd(dry_run: bool) -> None:
    """Cria estrutura canônica de módulos a partir do course.yaml (raiz do repositório)."""
    from knowledge_extractor.course_config import load_course_config

    cfg = load_course_config()
    repo_root = cfg.root_dir
    modules_root = repo_root / "modules"

    if not cfg.modules:
        console.print("[red]❌ Nenhum módulo declarado em course.yaml.[/red]")
        raise SystemExit(1)

    table = Table(title=f"Scaffold de Módulos — {cfg.title}", show_header=True)
    table.add_column("Módulo", style="cyan")
    table.add_column("Slug")
    table.add_column("Ação")

    planned = 0
    for slug, mod in cfg.modules.items():
        mod_dir = modules_root / slug
        dirs = [
            mod_dir / "aulas",
            mod_dir / "labs",
            mod_dir / "materiais" / "raw",
            mod_dir / "materiais" / "transcricoes",
            mod_dir / "portal",
        ]
        readme = mod_dir / "README.md"
        readme_content = f"""# Módulo {mod.id} — {mod.name}

> **Programa:** {cfg.title} — {cfg.institution}
> **Trilha:** {cfg.track_role}

## Ementa do Módulo

{mod.summary or cfg.description}

## Competências Desenvolvidas

""" + "\n".join(f"- {c}" for c in mod.competencies) + """

## Estrutura

| Pasta | Conteúdo |
| :--- | :--- |
| `aulas/` | Apostilas técnicas e cadernos de aula sintetizados |
| `labs/` | Exercícios práticos, notebooks e scripts de laboratório |
| `materiais/raw/` | Materiais brutos originais (PDFs, slides, handouts) |
| `materiais/transcricoes/` | Transcrições processadas pelo knowledge-extractor |
| `portal/` | Catálogo de aulas (`catalogo.md`) e espelho do AVA |
"""
        status = "atualizar" if readme.exists() else "criar"
        table.add_row(f"{mod.id} — {mod.name}", slug, status)
        planned += 1
        if not dry_run:
            for d in dirs:
                d.mkdir(parents=True, exist_ok=True)
                gitkeep = d / ".gitkeep"
                if not any(d.iterdir()):
                    gitkeep.write_text("", encoding="utf-8")
            readme.write_text(readme_content, encoding="utf-8")

    console.print(table)
    console.print(f"[green]✔ {planned} módulo(s) {'planejado(s)' if dry_run else 'processado(s)'}[/green]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
