"""Módulo de carregamento de configuração do curso/treinamento (course.yaml).

Fornece metadados pedagógicos, ementa oficial e competências para o sintetizador,
orquestrador de pipeline e sincronizador de catálogo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModuleConfig:
    id: str
    slug: str
    name: str
    summary: str = ""
    competencies: list[str] = field(default_factory=list)
    portal_order: int | None = None
    previous_slugs: list[str] = field(default_factory=list)


@dataclass
class CourseConfig:
    id: str = "generic-course"
    title: str = "Programa de Formação Técnica"
    institution: "Treinamento Especializado" = "Treinamento Especializado"
    track_role: str = "Especialista em Engenharia de Software e IA"
    description: str = "Caderno técnico e documentação avançada de engenharia."
    syllabus_ref: str = ""
    modules: dict[str, ModuleConfig] = field(default_factory=dict)
    root_dir: Path = field(default_factory=Path.cwd)

    def get_module(self, slug_or_id: str) -> ModuleConfig | None:
        """Busca configuração do módulo por slug completo (ex: 08-dev-agentes) ou ID (08)."""
        if slug_or_id in self.modules:
            return self.modules[slug_or_id]
        for mod in self.modules.values():
            if mod.slug == slug_or_id or mod.id == slug_or_id:
                return mod
        for mod in self.modules.values():
            if slug_or_id in mod.previous_slugs:
                return mod
        return None

    def get_syllabus_content(self) -> str:
        """Lê o conteúdo textual da ementa referenciada se existir."""
        if not self.syllabus_ref:
            return ""
        target = (self.root_dir / self.syllabus_ref).resolve()
        if target.exists() and target.is_file():
            try:
                return target.read_text(encoding="utf-8")
            except Exception:
                return ""
        return ""


def find_repo_root(start_dir: Path | None = None) -> Path:
    """Encontra a raiz do repositório procurando por course.yaml ou .git."""
    cur = (start_dir or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "course.yaml").exists() or (parent / ".git").exists():
            return parent
    return cur


def load_course_config(start_dir: Path | None = None) -> CourseConfig:
    """Carrega as configurações a partir do course.yaml mais próximo."""
    root = find_repo_root(start_dir)
    cfg_file = root / "course.yaml"

    if not cfg_file.exists():
        return CourseConfig(root_dir=root)

    try:
        data: dict[str, Any] = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        raw_course = data.get("course", {})

        raw_modules = data.get("modules") or raw_course.get("modules") or {}
        modules_map: dict[str, ModuleConfig] = {}

        if isinstance(raw_modules, dict):
            for slug, mod_raw in raw_modules.items():
                if isinstance(mod_raw, dict):
                    mod_id = str(mod_raw.get("id", slug.split("-")[0])).zfill(2)
                    name = mod_raw.get("name", slug)
                    summary = mod_raw.get("summary", "")
                    comps = mod_raw.get("competencies", [])
                    modules_map[slug] = ModuleConfig(
                        id=mod_id,
                        slug=slug,
                        name=name,
                        summary=summary,
                        competencies=comps if isinstance(comps, list) else [],
                        portal_order=mod_raw.get("portal_order"),
                        previous_slugs=list(mod_raw.get("previous_slugs") or []),
                    )
        elif isinstance(raw_modules, list):
            for mod_raw in raw_modules:
                if isinstance(mod_raw, dict):
                    mod_id = str(mod_raw.get("id", "")).zfill(2)
                    slug = mod_raw.get("slug", f"{mod_id}-modulo")
                    name = mod_raw.get("name", slug)
                    summary = mod_raw.get("summary", "")
                    comps = mod_raw.get("competencies", [])
                    modules_map[slug] = ModuleConfig(
                        id=mod_id,
                        slug=slug,
                        name=name,
                        summary=summary,
                        competencies=comps if isinstance(comps, list) else [],
                        portal_order=mod_raw.get("portal_order"),
                        previous_slugs=list(mod_raw.get("previous_slugs") or []),
                    )

        return CourseConfig(
            id=raw_course.get("id", "generic-course"),
            title=raw_course.get("title", "Programa de Formação Técnica"),
            institution=raw_course.get("institution", "Treinamento Especializado"),
            track_role=raw_course.get("track_role", "Especialista Técnico"),
            description=raw_course.get("description", ""),
            syllabus_ref=raw_course.get("syllabus_ref", ""),
            modules=modules_map,
            root_dir=root,
        )
    except Exception:
        return CourseConfig(root_dir=root)
