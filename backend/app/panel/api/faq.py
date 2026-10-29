"""Pagina FAQ: lista e filtros (spec painel.1, 4.5; PN-22, PN-43; task PN-T26).

``GET /dashboard/faq`` (brand, category, inativas, q, page). Pedido HTMX devolve so o
resultado (contagem + tabela + paginacao); sem ele, a pagina inteira. Le somente via
``faq_read``. Os botoes de criar, editar, desativar/reativar e importar apontam para rotas
de outras tarefas (faq_form, faq_import); a lista recarrega no evento ``faqChanged``.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    templates_for,
)
from app.panel.services.faq_read import (
    FaqFilters,
    active_count,
    faq_page,
    parse_faq_filters,
)
from app.panel.services.faq_validation import ALL_BRANDS_KEY, FAQ_CATEGORIES
from app.panel.services.scope import Scope
from app.panel.values import (
    ALL_BRANDS_LABEL,
    BRAND_LABELS,
    brand_label,
    classification_label,
)

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/faq"


def faq_query(f: FaqFilters, *, page: bool = False) -> str:
    """Filtros ativos ja codificados; a marca vai sempre (e a escolha explicita da tela)."""
    pairs: list[tuple[str, str]] = [("brand", f.brand)]
    if f.category:
        pairs.append(("category", f.category))
    if f.q:
        pairs.append(("q", f.q))
    if f.inactive:
        pairs.append(("inativas", "1"))
    if page and f.page > 1:
        pairs.append(("page", str(f.page)))
    return urlencode(pairs)


@router.get(PATH, include_in_schema=False)
async def faq(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    brand: str | None = None,
    category: str | None = None,
    inativas: str | None = None,
    q: str | None = None,
    page: str | None = None,
) -> Response:
    filters = parse_faq_filters(
        brand=brand,
        category=category,
        inactive=inativas,
        q=q,
        page=page,
        default_brand=scope.brand or ALL_BRANDS_KEY,
    )
    async with request.app.state.pool.acquire() as conn:
        result = await faq_page(conn, filters)
        total_active = await active_count(conn, filters.brand)
    shown = replace(filters, page=result.page)
    is_htmx = bool(request.headers.get("hx-request"))
    context = {
        "active": "faq",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "filters": shown,
        "page": result,
        "active_count": total_active,
        "focus_brand_label": brand_label(filters.brand) if filters.brand != ALL_BRANDS_KEY else "",
        "oob": is_htmx,
        "qs": faq_query(shown),
        "qs_page": faq_query(shown, page=True),
        "qs_toggle": faq_query(replace(shown, inactive=not shown.inactive, page=1)),
        "qs_clear": faq_query(FaqFilters(brand=shown.brand, inactive=shown.inactive)),
        "brand_options": [(ALL_BRANDS_KEY, ALL_BRANDS_LABEL), *BRAND_LABELS.items()],
        "category_options": [(c, classification_label(c)) for c in FAQ_CATEGORIES],
    }
    name = "_faq_tabela.html" if is_htmx else "faq.html"
    response = templates_for(request).TemplateResponse(request, name, context)
    response.headers["Vary"] = "HX-Request"
    return response
