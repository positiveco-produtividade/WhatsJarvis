"""Jinja2 do painel: autoescape ligado, filtro `sp_dt` e `static_v` (spec 4.4.4, 4.5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import jinja2
from fastapi.templating import Jinja2Templates

from app.panel.services.formatting import format_datetime
from app.paths import STATIC_DIR, TEMPLATES_DIR


def sp_dt(value: datetime | None) -> str:
    """Filtro `{{ x|sp_dt }}`: ``dd/mm/aaaa HH:MM`` em Sao Paulo (``—`` se vazio)."""
    return format_datetime(value)


def make_static_v(static_dir: Path) -> Callable[[str], str]:
    """Devolve `static_v(path)`: URL em /static com `?v=<mtime>` (arquivo ausente: sem `v`)."""
    root = static_dir.resolve()

    def static_v(path: str) -> str:
        rel = path.lstrip("/")
        url = f"/static/{rel}"
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            return url
        return f"{url}?v={int(target.stat().st_mtime)}"

    return static_v


def build_templates(
    templates_dir: Path = TEMPLATES_DIR, static_dir: Path = STATIC_DIR
) -> Jinja2Templates:
    """Ambiente Jinja com autoescape para tudo (`|safe` e proibido nos templates)."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    env.filters["sp_dt"] = sp_dt
    env.globals["static_v"] = make_static_v(static_dir)
    return Jinja2Templates(env=env)


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """Instancia unica para as rotas (`templates.TemplateResponse(request, nome, ctx)`)."""
    return build_templates()
