"""Rotas de sistema do painel (spec painel.1, 4.5 "Entrada e sistema"; task PN-T13).

- `GET /` e `GET /dashboard`: entrada. O retorno do login do IAM cai aqui (ou na pagina
  pedida): `current_admin` troca o cookie `iam_session` pela sessao `jarvis_panel`.
- `POST /dashboard/sair`: revoga a sessao e leva ao login do IAM.
- `GET /dashboard/marca?b=<slug>`: grava `jarvis_brand` e volta pela `Referer` validada.
- `GET /dashboard/estado`: fragmento da faixa de pausa e de alertas nao vistos.
"""
from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.panel.auth.sessions import COOKIE_NAME, PanelSession
from app.panel.deps import (
    BRAND_COOKIE,
    BRAND_COOKIE_MAX_AGE,
    PanelRoute,
    brand_scope,
    current_admin,
    get_sessions,
    get_settings,
    iam_login_url,
    require_csrf,
    templates_for,
)
from app.panel.services.scope import Scope, parse_scope
from app.services.flags import Flags

router = APIRouter(route_class=PanelRoute)

HOME = "/dashboard/tickets"


def safe_return_path(referer: str | None, request: Request, fallback: str = HOME) -> str:
    """Caminho (sem host) da `Referer` se ela for do proprio painel; senao `fallback`."""
    if not referer:
        return fallback
    host = get_settings(request).panel_public_host.lower()
    try:
        parsed = urlparse(referer)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return fallback
    path = parsed.path or "/"
    if hostname != host or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return fallback
    return f"{path}?{parsed.query}" if parsed.query else path


@router.get("/", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
async def entrada(_: Annotated[PanelSession, Depends(current_admin)]) -> Response:
    return RedirectResponse(HOME, status_code=302)


@router.post("/dashboard/sair", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def sair(request: Request) -> Response:
    await get_sessions(request).revoke(request.cookies.get(COOKIE_NAME))
    response = RedirectResponse(iam_login_url(request), status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/dashboard/marca", include_in_schema=False)
async def marca(
    request: Request,
    _: Annotated[PanelSession, Depends(current_admin)],
    b: str = "todas",
) -> Response:
    scope = parse_scope(b)
    target = safe_return_path(request.headers.get("referer"), request)
    response = RedirectResponse(target, status_code=302)
    response.set_cookie(
        BRAND_COOKIE,
        scope.cookie_value,
        max_age=BRAND_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/dashboard/estado", include_in_schema=False)
async def estado(
    request: Request,
    scope: Annotated[Scope, Depends(brand_scope)],
    _: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    state = request.app.state
    flags: Flags = getattr(state, "flags", None) or Flags(state.pool)
    params: list[object] = []
    where = scope.sql_alert("payload", params)
    async with state.pool.acquire() as conn:
        paused = await flags.paused(conn=conn)
        unseen = await conn.fetchval(
            f"SELECT count(*) FROM ops.alerts WHERE acknowledged_at IS NULL AND {where}",
            *params,
        )
    context = {"estado": {"paused": paused, "unseen_alerts": int(unseen or 0)}}
    return templates_for(request).TemplateResponse(request, "_banner_estado.html", context)
