"""Montagem do painel no app FastAPI (spec 4.1, 4.5; task PN-T06).

`panel/api/*.py` que exponham `router` sao descobertos sozinhos: nenhuma tarefa de tela
edita este arquivo. Chamado por `app.main` somente com `PANEL_ENABLED=true`.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil

from fastapi import APIRouter, FastAPI
from starlette.staticfiles import StaticFiles

from app.panel.headers import PanelHeadersMiddleware
from app.panel.ratelimit import PanelRateLimitMiddleware
from app.panel.templating import get_templates
from app.paths import STATIC_DIR

logger = logging.getLogger(__name__)

API_PACKAGE = "app.panel.api"


def discover_routers() -> tuple[APIRouter, ...]:
    """Routers de `app.panel.api.*` (ordem alfabetica do modulo; sem `router`, ignora)."""
    if importlib.util.find_spec(API_PACKAGE) is None:
        return ()
    package = importlib.import_module(API_PACKAGE)
    routers: list[APIRouter] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{API_PACKAGE}.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            routers.append(router)
    return tuple(routers)


def mount_panel(app: FastAPI) -> None:
    """Instala cabecalhos, estaticos (/static), templates e os routers do painel."""
    app.state.templates = get_templates()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for router in discover_routers():
        app.include_router(router)
    # Limite por IP: por fora do gate de sessao (vale sem login) e por dentro dos cabecalhos.
    app.add_middleware(PanelRateLimitMiddleware)
    # Por ultimo = por fora de tudo: o Cache-Control do painel vence o `no-store` de PF.
    app.add_middleware(PanelHeadersMiddleware)
    logger.info("panel_mounted")
