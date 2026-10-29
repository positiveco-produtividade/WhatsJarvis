"""Aba Auditoria de Logs (spec painel.1, 4.5; task PN-T59; PN-55).

``GET /dashboard/logs/auditoria`` (acao, alvo, alvo_id, page) devolve o fragmento
``_logs_auditoria.html``, carregado por HTMX dentro de ``/dashboard/logs``. O link antigo
``/dashboard/logs?aba=auditoria`` continua valendo: a pagina de Logs (PN-T60) carrega esta
sub-rota. So administradores; somente leitura (a tabela e imutavel). Uma conexao do pool por
pedido, liberada antes de renderizar; nenhum valor de auditoria vai para o log.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import PanelRoute, current_admin, templates_for
from app.panel.services.audit_read import (
    AuditFilters,
    audit_page,
    load_facets,
    parse_filters,
)

__all__ = ["router"]

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/logs/auditoria"
TEMPLATE = "_logs_auditoria.html"


def filters_query(f: AuditFilters) -> str:
    """Filtros ativos ja codificados (sem ``page``)."""
    pairs = [
        (key, value)
        for key, value in (("acao", f.action), ("alvo", f.target_type), ("alvo_id", f.target_id))
        if value
    ]
    return urlencode(pairs)


@router.get(PATH, include_in_schema=False)
async def auditoria(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    acao: str | None = None,
    alvo: str | None = None,
    alvo_id: str | None = None,
    page: str | None = None,
) -> Response:
    filters = parse_filters(action=acao, target_type=alvo, target_id=alvo_id, page=page)
    async with request.app.state.pool.acquire() as conn:
        result = await audit_page(conn, filters)
        facets = await load_facets(conn)
    context = {
        "audit_path": PATH,
        "page": result,
        "filters": filters,
        "qs": filters_query(filters),
        "facets": facets,
    }
    response = templates_for(request).TemplateResponse(request, TEMPLATE, context)
    response.headers["Vary"] = "HX-Request"
    return response
