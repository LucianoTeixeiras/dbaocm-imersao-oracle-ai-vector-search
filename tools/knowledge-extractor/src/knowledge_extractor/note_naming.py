"""Nome canônico da nota de aula — fonte única usada para GRAVAR e para CHECAR.

A mesma fonte (vídeo em raw/, .txt ou .md em transcricoes/, PDF) sempre gera o mesmo nome:
  "01 Intro.mp4"                 -> aula-01-intro.md
  "midia-x.txt"                  -> aula-midia-x.md
  "Module 06 01 Resources.pdf"   -> aula-module-06-01-resources.md
  "aula-02-rag.txt" / "mod02-…"  -> mantém o stem (já normalizado)
"""

from __future__ import annotations

import re
from pathlib import Path

from knowledge_extractor.file_normalizer import slugify

SOURCE_SUFFIXES = {".txt", ".md", ".pdf", ".srt"}


def note_filename_for(source: Path | str) -> str:
    stem = Path(source).stem.strip()
    # Se já tem prefixo canônico (aula-, modXX-, podcast-, video-, doc-, apostila-, guia-)
    if stem.startswith(
        (
            "aula-",
            "mod",
            "podcast-",
            "video-",
            "doc-",
            "apostila-",
            "guia-",
            "onboarding-",
            "setup-",
        )
    ):
        return f"{slugify(stem)}.md"

    # Se começa com midia-modXX ou midia-podcast, limpa o midia- e usa o canônico
    m_midia_mod = re.match(
        r"^midia[-_]mod(\d{2})[-_]aula[-_](\d{2})[-_](.+)$", stem, re.IGNORECASE
    )
    if m_midia_mod:
        m_num = int(m_midia_mod.group(1))
        a_num = int(m_midia_mod.group(2))
        return f"mod{m_num:02d}-aula-{a_num:02d}-{slugify(m_midia_mod.group(3))}.md"

    m_midia_pod = re.match(
        r"^midia[-_]podcast[-_](?:(\d{2,4})[-_])?(.+)$", stem, re.IGNORECASE
    )
    if m_midia_pod:
        p_num = m_midia_pod.group(1)
        p_title = slugify(m_midia_pod.group(2))
        if p_num:
            return f"podcast-{int(p_num):02d}-{p_title}.md"
        return f"podcast-{p_title}.md"

    m = re.match(r"^(\d+)[-_ ]*(.+)$", stem)
    if m:
        return f"aula-{int(m.group(1)):02d}-{slugify(m.group(2))}.md"
    return f"aula-{slugify(stem)}.md"


def note_path_for(source: Path | str, out_dir: Path) -> Path:
    return out_dir / note_filename_for(source)


def legacy_note_filenames(source: Path | str) -> set[str]:
    """Nomes que versões antigas podiam gravar/procurar (para detectar notas com nome antigo)."""
    stem = Path(source).stem
    names = {f"{stem}.md"}  # checagem antiga do CLI
    if not stem.startswith(("aula-", "mod")):
        m = re.match(r"^(\d+)[-_ ]*(.+)$", stem)
        if m:
            names.add(
                f"aula-{int(m.group(1)):02d}-{m.group(2).strip().lower().replace(' ', '-')}.md"
            )
        else:
            names.add(f"aula-{stem.lower().replace(' ', '-')}.md")
    names.discard(note_filename_for(source))
    return names


def audit_notes(module_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Compara fontes x notas. Retorna {'ok','faltando','nome_antigo','orfas'} com pares (fonte|nota, detalhe)."""
    aulas = module_dir / "aulas"
    sources: dict[str, Path] = {}
    for sub in ("materiais/transcricoes", "materiais/raw"):
        d = module_dir / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if (
                    f.is_file()
                    and not f.name.startswith(".")
                    and (f.suffix.lower() in SOURCE_SUFFIXES or sub.endswith("raw"))
                ):
                    sources.setdefault(note_filename_for(f), f)
    notes = (
        {f.name for f in aulas.glob("*.md") if not f.name.startswith(".")}
        if aulas.is_dir()
        else set()
    )
    res: dict[str, list[tuple[str, str]]] = {
        "ok": [],
        "faltando": [],
        "nome_antigo": [],
        "orfas": [],
    }
    claimed: set[str] = set()
    for name, src in sources.items():
        if name in notes:
            res["ok"].append((src.name, name))
            claimed.add(name)
            continue
        legacy = sorted(legacy_note_filenames(src) & notes)
        if legacy:
            res["nome_antigo"].append((legacy[0], name))
            claimed.update(legacy)
        else:
            res["faltando"].append((src.name, name))
    res["orfas"] = [
        (n, "sem fonte correspondente")
        for n in sorted(notes - claimed)
        if n.lower() != "readme.md"
    ]
    return res
