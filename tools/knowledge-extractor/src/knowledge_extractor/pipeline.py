"""Orquestrador do Pipeline Fim-a-Fim para o MBA Full Cycle."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from knowledge_extractor.audio_transcriber import Transcriber
from knowledge_extractor.catalog_sync import CatalogSynchronizer
from knowledge_extractor.file_normalizer import FileNormalizer
from knowledge_extractor.metrics import track
from knowledge_extractor.pdf_parser import extract_text_from_pdf
from knowledge_extractor.synthesizer import LectureSynthesizer, _is_scaffold_file
from knowledge_extractor.video_frames import extract_video_keyframes, VIDEO_EXTENSIONS

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False)


class ModulePipeline:
    def __init__(
        self,
        module_dir: Path,
        model_size: str = "base",
        gemini_model: str | None = None,
        provider: str | None = None,
        ollama_model: str | None = None,
        vision: bool = True,
    ) -> None:
        self.module_dir = Path(module_dir).resolve()
        self.model_size = model_size
        self.gemini_model = gemini_model
        self.provider = provider
        self.ollama_model = ollama_model
        self.vision = vision
        self.root_dir = self.find_repo_root(self.module_dir)

    @staticmethod
    def find_repo_root(start_dir: Path) -> Path:
        curr = start_dir
        while curr != curr.parent:
            if (curr / ".git").exists() or (curr / "tools").exists():
                return curr
            curr = curr.parent
        return start_dir

    def make_synthesizer(self) -> LectureSynthesizer:
        return LectureSynthesizer(gemini_model=self.gemini_model, ollama_model=self.ollama_model, provider=self.provider)

    def choose_synthesis_mode(self) -> None:
        """Escolha interativa: fallback automático ou provedor/modelo fixo (vale para esta sessão)."""
        current = self.make_synthesizer()
        console.print("\n[bold]Modos:[/bold] [cyan]auto[/cyan] = Ollama → Gemini → scaffold · "
                      "[cyan]ollama[/cyan] = só local · [cyan]gemini[/cyan] = só cloud")
        self.provider = Prompt.ask("Provedor", choices=["auto", "ollama", "gemini"], default=current.provider)

        if self.provider in ("auto", "ollama"):
            installed = current._installed_ollama_models()
            if installed:
                console.print(f"[dim]Instalados no Ollama: {', '.join(installed)}[/dim]")
            self.ollama_model = Prompt.ask("Modelo Ollama", default=current.ollama_model)
        if self.provider in ("auto", "gemini"):
            self.gemini_model = Prompt.ask("Modelo Gemini", default=current.gemini_model)

        console.print(f"[green]✓ Modo de síntese: {self.make_synthesizer().mode_label()}[/green]")

    def run_step_1_normalization(self, apply: bool = True) -> None:
        """Etapa 1: Normalização de arquivos em materiais/raw/ e transcricoes/ com auditoria De-Para."""
        console.print(Panel.fit("[bold cyan]Etapa 1: Normalização de Nomes e Auditoria De-Para[/bold cyan]", border_style="cyan"))
        normalizer = FileNormalizer(root_dir=self.root_dir)
        normalizer.execute_normalization(self.module_dir, dry_run=not apply)

    def run_step_2_extraction(self, force: bool = False) -> None:
        """Etapa 2: Extração e Transcrição (faster-whisper e PDF parser)."""
        console.print(Panel.fit("[bold cyan]Etapa 2: Extração e Transcrição Automática (Whisper/PDF)[/bold cyan]", border_style="cyan"))
        raw_dir = self.module_dir / "materiais" / "raw"
        transcricoes_dir = self.module_dir / "materiais" / "transcricoes"

        if not raw_dir.exists():
            console.print(f"[red]❌ Diretório raw não encontrado:[/red] {raw_dir}")
            return

        transcricoes_dir.mkdir(parents=True, exist_ok=True)
        media_extensions = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wav", ".mp3", ".m4a"}
        pdf_extensions = {".pdf"}

        files = [p for p in sorted(raw_dir.iterdir()) if p.is_file() and not p.name.startswith(".")]

        pending_media: list[Path] = []
        pending_pdf: list[Path] = []

        table = Table(title=f"Verificação de Mídias em {self.module_dir.name}")
        table.add_column("Arquivo", style="cyan")
        table.add_column("Tipo", style="magenta")
        table.add_column("Status", style="green")

        for f in files:
            ext = f.suffix.lower()
            stem = f.stem
            is_media = ext in media_extensions
            is_pdf = ext in pdf_extensions

            if is_media:
                txt_ok = (transcricoes_dir / f"{stem}.txt").exists()
                srt_ok = (transcricoes_dir / f"{stem}.srt").exists()
                if not force and txt_ok and srt_ok:
                    status = "[dim]Já transcrito (Skip)[/dim]"
                else:
                    status = "[bold yellow]Pendente[/bold yellow]"
                    pending_media.append(f)
                table.add_row(f.name, "Vídeo/Áudio", status)
            elif is_pdf:
                md_ok = (transcricoes_dir / f"{stem}.md").exists()
                if not force and md_ok:
                    status = "[dim]Já extraído (Skip)[/dim]"
                else:
                    status = "[bold yellow]Pendente[/bold yellow]"
                    pending_pdf.append(f)
                table.add_row(f.name, "PDF", status)

        console.print(table)

        if pending_media:
            console.print(f"[bold yellow]Transcrevendo {len(pending_media)} mídia(s) com modelo '{self.model_size}'...[/bold yellow]")
            transcriber = Transcriber(model_size=self.model_size, device="auto", language="pt")
            for media in pending_media:
                with track("transcricao", modulo=self.module_dir.name, item=media.name) as info:
                    info["whisper"] = self.model_size
                    info["tamanho_mb"] = f"{media.stat().st_size / 1_048_576:.0f}"
                    transcriber.transcribe_file(media, output_dir=transcricoes_dir)

        # Coleta de frames visuais de vídeo (Detecção de Cenas / ffmpeg)
        if self.vision:
            video_files = [f for f in files if f.suffix.lower() in VIDEO_EXTENSIONS]
            if video_files:
                frames_base_dir = self.module_dir / "materiais" / "frames"
                console.print(f"[bold cyan]🎬 Coletando frames e diagramas de {len(video_files)} vídeo(s)...[/bold cyan]")
                for vid in video_files:
                    fdir = frames_base_dir / vid.stem
                    extract_video_keyframes(vid, output_dir=fdir, force=force)

        if pending_pdf:
            console.print(f"[bold yellow]Extraindo {len(pending_pdf)} PDF(s)...[/bold yellow]")
            for pdf in pending_pdf:
                with track("extracao_pdf", modulo=self.module_dir.name, item=pdf.name):
                    extract_text_from_pdf(pdf, output_dir=transcricoes_dir, as_markdown=True)

        if not pending_media and not pending_pdf:
            console.print("[green]✓ Todas as mídias e PDFs já estavam transcritos e atualizados.[/green]")

    def run_step_3_synthesis(self, force: bool = False) -> None:
        """Etapa 3: Síntese de Notas Estruturadas em aulas/ via Gemini API."""
        console.print(Panel.fit("[bold cyan]Etapa 3: Síntese Multimodal de Notas de Aula (Gemini API / Markdown)[/bold cyan]", border_style="cyan"))
        transcricoes_dir = self.module_dir / "materiais" / "transcricoes"
        aulas_dir = self.module_dir / "aulas"
        aulas_dir.mkdir(parents=True, exist_ok=True)

        if not transcricoes_dir.exists():
            console.print(f"[yellow]ℹ Nenhuma transcrição encontrada em:[/yellow] {transcricoes_dir}")
            return

        txt_files = sorted([f for f in transcricoes_dir.iterdir() if f.is_file() and f.suffix.lower() == ".txt" and not f.name.startswith(".")])

        if not txt_files:
            console.print("[yellow]ℹ Nenhum arquivo .txt de transcrição encontrado para sintetizar.[/yellow]")
            return

        synthesizer = self.make_synthesizer()
        synthesizer.print_mode()

        raw_dir = self.module_dir / "materiais" / "raw"
        frames_base_dir = self.module_dir / "materiais" / "frames"

        for txt in txt_files:
            target_note = aulas_dir / f"{txt.stem}.md"
            if target_note.exists() and not force and not _is_scaffold_file(target_note):
                console.print(f"[dim]Nota já existente (Skip):[/dim] {target_note.name}")
                continue

            keyframes = None
            if self.vision and raw_dir.exists():
                video_candidates = [
                    raw_dir / f"{txt.stem}{ext}"
                    for ext in VIDEO_EXTENSIONS
                    if (raw_dir / f"{txt.stem}{ext}").exists()
                ]
                if video_candidates:
                    target_video = video_candidates[0]
                    frames_dir = frames_base_dir / txt.stem
                    keyframes = extract_video_keyframes(target_video, output_dir=frames_dir, force=force)

            synthesizer.process_file(
                transcript_file=txt,
                module_dir=self.module_dir,
                output_dir=aulas_dir,
                force=force,
                keyframes=keyframes,
            )

    def run_step_4_catalog_sync(self) -> None:
        """Etapa 4: Sincronização do portal/catalogo.md."""
        console.print(Panel.fit("[bold cyan]Etapa 4: Sincronização do Catálogo (portal/catalogo.md)[/bold cyan]", border_style="cyan"))
        synchronizer = CatalogSynchronizer(root_dir=self.root_dir)
        synchronizer.sync_module_catalog(self.module_dir)

    def run_step_5_governance(self) -> None:
        """Etapa 5: Validação e Governança Git (Proteção de mídias e status)."""
        console.print(Panel.fit("[bold cyan]Etapa 5: Auditoria de Governança Git & Blindagem de Mídias[/bold cyan]", border_style="cyan"))

        # Executa git status para ver arquivos que seriam comitados
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", str(self.module_dir)],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            changes = res.stdout.strip().splitlines()

            # Checa se há arquivos de mídia acidentalmente rastreados
            media_exts = {".mp4", ".mkv", ".mov", ".avi", ".wav", ".mp3"}
            leaked_media = [c for c in changes if any(c.lower().endswith(ext) for ext in media_exts)]

            if leaked_media:
                console.print("[bold red]🚨 ALERTA: Foram detectados arquivos de mídia pesada não ignorados no Git:[/bold red]")
                for lm in leaked_media:
                    console.print(f"  [red]- {lm}[/red]")
            else:
                console.print("[bold green]✓ Blindagem Git OK:[/bold green] Nenhuma mídia pesada de vídeo/áudio está sendo rastreada pelo Git.")

            console.print(f"[dim]Total de alterações prontas/rastreadas neste módulo: {len(changes)} arquivo(s)[/dim]")

        except Exception as e:
            console.print(f"[yellow]ℹ Não foi possível executar git status: {e}[/yellow]")

    def run_all(self, force: bool = False) -> None:
        """Executa todas as etapas em sequência."""
        console.print(Panel.fit(f"[bold magenta]🚀 Iniciando Pipeline Completo para {self.module_dir.name}[/bold magenta]", border_style="magenta"))
        steps = [
            ("etapa_1_normalizacao", lambda: self.run_step_1_normalization(apply=True)),
            ("etapa_2_extracao", lambda: self.run_step_2_extraction(force=force)),
            ("etapa_3_sintese", lambda: self.run_step_3_synthesis(force=force)),
            ("etapa_4_catalogo", self.run_step_4_catalog_sync),
            ("etapa_5_governanca", self.run_step_5_governance),
        ]
        mod = self.module_dir.name
        with track("pipeline_completo", modulo=mod):
            for name, fn in steps:
                with track(name, modulo=mod):
                    fn()
                console.print("")
        console.print(Panel.fit("[bold green]✨ Pipeline Executado com Sucesso![/bold green]", border_style="green"))


def run_interactive_menu(pipeline: ModulePipeline) -> None:
    """Menu Interativo de Execução do Pipeline."""
    module_dir = pipeline.module_dir

    while True:
        mode = pipeline.make_synthesizer().mode_label()
        from knowledge_extractor.course_config import load_course_config
        cfg = load_course_config()
        mod_info = cfg.get_module(module_dir.name)
        mod_display = mod_info.name if mod_info else module_dir.name
        console.print("\n" + "=" * 60)
        console.print(f"[bold cyan]🎯 PIPELINE {cfg.title.upper()} — Módulo: [green]{mod_display}[/green][/bold cyan]")
        console.print("=" * 60)
        console.print("[bold]1.[/bold] 🏷️  Normalizar Nomes de Arquivos (De-Para & Kebab-case)")
        console.print("[bold]2.[/bold] 🎙️  Extrair Mídias e Transcrever (Whisper, Frames de Vídeo e PDFs)")
        console.print("[bold]3.[/bold] 📝 Sintetizar Notas de Estudo em [cyan]aulas/[/cyan] (Gemini Multimodal)")
        console.print("[bold]4.[/bold] 📑 Sincronizar Catálogo AVA ([cyan]portal/catalogo.md[/cyan])")
        console.print("[bold]5.[/bold] 🛡️  Auditoria de Governança Git (Blindagem de Mídias)")
        console.print("[bold]6.[/bold] 🚀 [bold green]Executar Pipeline Fim-a-Fim Completo (1 → 5)[/bold green]")
        vision_status = "[green]Ativada (Padrão)[/green]" if pipeline.vision else "[dim]Desativada (Apenas Áudio)[/dim]"
        console.print(f"[bold]7.[/bold] ⚙️  Modo de Síntese [dim](atual: {mode})[/dim]")
        console.print(f"[bold]8.[/bold] 🎬 Alternar Visão Multimodal de Vídeo [dim](atual: {vision_status})[/dim]")
        console.print("[bold]0.[/bold] 🚪 Sair")
        console.print("=" * 60)

        choice = Prompt.ask("Escolha uma opção", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"], default="6")

        if choice == "0":
            console.print("[yellow]Encerrando pipeline.[/yellow]")
            break
        elif choice == "1":
            pipeline.run_step_1_normalization(apply=True)
        elif choice == "2":
            pipeline.run_step_2_extraction(force=False)
        elif choice == "3":
            pipeline.run_step_3_synthesis(force=False)
        elif choice == "4":
            pipeline.run_step_4_catalog_sync()
        elif choice == "5":
            pipeline.run_step_5_governance()
        elif choice == "6":
            pipeline.run_all(force=False)
        elif choice == "7":
            pipeline.choose_synthesis_mode()
        elif choice == "8":
            pipeline.vision = not pipeline.vision
            novo_status = "[bold green]ATIVADA[/bold green]" if pipeline.vision else "[bold yellow]DESATIVADA[/bold yellow]"
            console.print(f"🎬 Visão Multimodal agora está {novo_status}!")
