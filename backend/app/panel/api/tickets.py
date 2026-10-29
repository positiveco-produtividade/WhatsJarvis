"""Pagina Tickets (spec painel.1, 4.5; PN-7, PN-8, PN-9, PN-43; task PN-T18).

``GET /dashboard/tickets`` (q, classification, status, days, page). Pedido HTMX
(``HX-Request``) devolve so o resultado (KPIs + tabela); sem ele, a pagina inteira. Le somente
via ``tickets_read``; nenhum texto de mensagem vai para log.
"""
from __future__ import annotations

from dataclasses import dataclass
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
from app.panel.services.formatting import format_int, format_percent
from app.panel.services.scope import Scope
from app.panel.services.tickets_read import (
    DEFAULT_DAYS,
    PERIOD_DAYS,
    TicketFilters,
    TicketKpis,
    parse_filters,
    ticket_kpis,
    tickets_page,
)
from app.panel.values import (
    CLASSIFICATIONS,
    TICKET_STATUSES,
    classification_label,
    ticket_status_label,
)

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/tickets"
INBOX_PATH = "/dashboard/inbox"


@dataclass(frozen=True, slots=True)
class KpiView:
    """KPIs ja formatados em PT-BR (``—`` quando nao ha base de calculo, PN-43)."""

    days: int
    total: str
    awaiting: str
    awaiting_over_48h: str
    awaiting_media: str
    escalated: str
    unclassified: str
    bot_answers: str
    bot_share: str


def kpi_view(k: TicketKpis, days: int) -> KpiView:
    share = (k.bot_answers * 100.0 / k.total) if k.total else None
    return KpiView(
        days=days,
        total=format_int(k.total),
        awaiting=format_int(k.awaiting),
        awaiting_over_48h=format_int(k.awaiting_over_48h),
        awaiting_media=format_int(k.awaiting_media),
        escalated=format_int(k.escalated),
        unclassified=format_percent(k.unclassified_pct),
        bot_answers=format_int(k.bot_answers),
        bot_share=format_percent(share) if share is not None else "",
    )


def filters_query(f: TicketFilters) -> str:
    """Filtros ativos ja codificados (sem ``page``); valores padrao ficam de fora."""
    pairs: list[tuple[str, str]] = []
    if f.q:
        pairs.append(("q", f.q))
    if f.classification:
        pairs.append(("classification", f.classification))
    if f.status:
        pairs.append(("status", f.status))
    if f.days != DEFAULT_DAYS:
        pairs.append(("days", str(f.days)))
    return urlencode(pairs)


@router.get(PATH, include_in_schema=False)
async def tickets(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    q: str | None = None,
    classification: str | None = None,
    status: str | None = None,
    days: str | None = None,
    page: str | None = None,
) -> Response:
    filters = parse_filters(q=q, classification=classification, status=status, days=days, page=page)
    now = get_clock(request).now()
    async with request.app.state.pool.acquire() as conn:
        kpis = await ticket_kpis(conn, scope, now=now, days=filters.days)
        result = await tickets_page(conn, scope, filters, now=now)
    context = {
        "active": "tickets",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "kpis": kpi_view(kpis, filters.days),
        "page": result,
        "filters": filters,
        "qs": filters_query(filters),
        "inbox_path": INBOX_PATH,
        "class_options": [(c, classification_label(c)) for c in CLASSIFICATIONS],
        "status_options": [(s, ticket_status_label(s)) for s in TICKET_STATUSES],
        "period_options": list(PERIOD_DAYS),
    }
    name = "_tickets_tabela.html" if request.headers.get("hx-request") else "tickets.html"
    response = templates_for(request).TemplateResponse(request, name, context)
    response.headers["Vary"] = "HX-Request"
    return response
