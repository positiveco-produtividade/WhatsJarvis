"""Pagina Inbox, lista de conversas (spec painel.1, 4.5; task PN-T21; PN-10, PN-9, PN-43).

- ``GET /dashboard/inbox`` (``aba`` conversas|midias, ``q``, ``so_aguardando``, ``page``,
  ``u`` = vendedor aberto no painel do chat). Pagina completa; com ``HX-Request`` devolve so o
  fragmento da lista (``#inbox-lista``).

O chat (``GET /dashboard/inbox/{user_id}``), "Concluir" e o lote sao da tarefa PN-T22: aqui so
ha o link para eles. Somente leitura; nada e enviado a ninguem.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    templates_for,
)
from app.panel.services.inbox_read import InboxPage, inbox_page, parse_tab
from app.panel.services.scope import Scope

router = APIRouter(route_class=PanelRoute)

MAX_QUERY_LENGTH = 100
TRUTHY = frozenset({"1", "true", "on", "sim"})


def _positive_int(raw: str | None, default: int) -> int:
    """Inteiro >= 1; lixo ou ausente vira ``default`` (a tela nunca quebra por querystring)."""
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return value if value >= 1 else default


def _wants_fragment(request: Request) -> bool:
    """HTMX pede so a lista; a restauracao de historico do HTMX precisa da pagina inteira."""
    headers = request.headers
    return bool(headers.get("hx-request")) and not headers.get("hx-history-restore-request")


def _filters_qs(tab: str, q: str, only_awaiting: bool) -> str:
    """Querystring dos filtros ja codificada (sem ``page`` nem ``u``)."""
    params = {"aba": tab}
    if q:
        params["q"] = q
    if only_awaiting:
        params["so_aguardando"] = "1"
    return urlencode(params)


def _build_context(
    request: Request,
    admin: PanelSession,
    scope: Scope,
    page: InboxPage,
    q: str,
    selected: int | None,
) -> dict[str, object]:
    pages = max(1, -(-page.total // page.page_size))
    return {
        "active": "inbox",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "inbox": page,
        "q": q,
        "selected": selected,
        "pages": pages,
        "filters_qs": _filters_qs(page.tab, q, page.awaiting_only),
        "tab_conversas_qs": _filters_qs("conversas", q, page.awaiting_only),
        "tab_midias_qs": _filters_qs("midias", q, page.awaiting_only),
        "toggle_qs": _filters_qs(page.tab, q, not page.awaiting_only),
        "awaiting_in_tab": page.counts.midias if page.tab == "midias" else page.counts.conversas,
    }


@router.get("/dashboard/inbox", include_in_schema=False)
async def inbox(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    aba: str | None = None,
    q: str | None = None,
    so_aguardando: str | None = None,
    page: str | None = None,
    u: str | None = None,
) -> Response:
    query = (q or "").strip()[:MAX_QUERY_LENGTH]
    only_awaiting = (so_aguardando or "").strip().lower() in TRUTHY
    selected = _positive_int(u, 0) or None
    now = get_clock(request).now()
    async with request.app.state.pool.acquire() as conn:
        result = await inbox_page(
            conn,
            scope,
            tab=parse_tab(aba),
            query=query,
            awaiting_only=only_awaiting,
            page=_positive_int(page, 1),
            now=now,
        )
    context = _build_context(request, admin, scope, result, query, selected)
    name = "_inbox_lista.html" if _wants_fragment(request) else "inbox.html"
    return templates_for(request).TemplateResponse(request, name, context)
