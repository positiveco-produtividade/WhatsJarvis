"""Pagina Pessoas, tres abas (spec painel.1, 4.5 e 4.8; PN-31, PN-43; task PN-T33).

``GET /dashboard/pessoas`` (aba, q, page). Pedido HTMX devolve so o resultado (abas + busca +
tabela + paginacao); sem ele, a pagina inteira. Le somente via ``people_read``; o telefone
chega ja mascarado (completo so na ficha). Cadastrar, ficha, revogar, reativar, ignorar e
importar apontam para rotas de outras tarefas; a lista recarrega no evento ``pessoasChanged``.
Nenhum texto de mensagem vai para log.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
from app.panel.services.people_read import (
    PAGE_SIZE,
    PendingPage,
    PeopleCounts,
    PeoplePage,
    PersonRow,
    Tab,
    parse_tab,
    pending_requests,
    people_counts,
    people_page,
)
from app.panel.services.scope import Scope
from app.panel.values import brand_label, profile_label
from app.services.flags import Flags

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/pessoas"

TAB_META: tuple[tuple[Tab, str, str, str], ...] = (
    ("autorizadas", "Autorizadas", "Podem conversar com o Jarvis", "bg-mint-100 text-mint-700"),
    (
        "pedindo",
        "Pedindo acesso",
        "Números fora da lista que mandaram mensagem",
        "bg-amber-100 text-amber-800",
    ),
    (
        "retiradas",
        "Retiradas",
        "Acesso revogado — o histórico fica guardado",
        "bg-ink-100 text-ink-600",
    ),
)
EMPTY_TEXT: dict[Tab, str] = {
    "autorizadas": "Nenhuma pessoa autorizada ainda.",
    "pedindo": "Nenhum número pedindo acesso.",
    "retiradas": "Nenhuma pessoa retirada.",
}
# Perfis que values.PROFILE_LABELS nao cobre (CHECK de access.users.profile).
_EXTRA_PROFILES = {"parceiro_externo": "Parceiro", "administrador": "Administrador"}
REVOKE_REASON_LABELS: dict[str, str] = {
    "left_company": "Saiu da empresa",
    "number_change": "Troca de número",
    "partner_contract_end": "Fim do contrato do parceiro",
    "monthly_reconciliation": "Reconciliação mensal (3C)",
    "declined_notice": "Recusou o aviso de privacidade",
    "other": "Outro motivo",
}


@dataclass(frozen=True, slots=True)
class PersonView:
    """Linha de Autorizadas/Retiradas pronta para o template (rotulos em PT-BR)."""

    id: int
    name: str
    initial: str
    company: str
    phone_masked: str
    profile: str
    ufs: str
    brands: str
    pending_notice: bool
    changed_at: datetime
    reason: str


def person_view(row: PersonRow) -> PersonView:
    profile = _EXTRA_PROFILES.get(row.profile) or profile_label(row.profile)
    reason = row.revoked_reason
    return PersonView(
        id=row.id,
        name=row.name,
        initial=row.initial,
        company=row.company,
        phone_masked=row.phone_masked,
        profile=profile,
        ufs=", ".join(row.ufs) or "—",
        brands=", ".join(brand_label(b) for b in row.brands) or "—",
        pending_notice=row.status == "pending_optin",
        changed_at=row.status_changed_at,
        reason=(REVOKE_REASON_LABELS.get(reason, reason) if reason else "—"),
    )


def people_query(tab: Tab, q: str) -> str:
    """Aba e busca ja codificadas (sem ``page``); a aba padrao fica de fora."""
    pairs: list[tuple[str, str]] = []
    if tab != "autorizadas":
        pairs.append(("aba", tab))
    if q:
        pairs.append(("q", q))
    return urlencode(pairs)


def _flags(request: Request) -> Flags:
    state = request.app.state
    existing: Flags | None = getattr(state, "flags", None)
    if existing is not None:
        return existing
    settings = getattr(state, "settings", None)
    initial = bool(getattr(settings, "team_group_enabled", False))
    return Flags(state.pool, get_clock(request), team_group_initial=initial)


def _page_number(raw: str | None) -> int:
    try:
        return max(1, int(raw)) if raw else 1
    except ValueError:
        return 1


async def _load(
    request: Request, tab: Tab, q: str, number: int
) -> tuple[PeoplePage | PendingPage, PeopleCounts]:
    """Pagina da aba e totais sem busca (o "Y" do "X de Y"), numa so conexao."""
    async with request.app.state.pool.acquire() as conn:
        if tab == "pedindo":
            result: PeoplePage | PendingPage = await pending_requests(
                conn, _flags(request), q=q, page=number
            )
        else:
            result = await people_page(conn, tab, q=q, page=number)
        overall = await people_counts(conn) if q else result.counts
    return result, overall


def _tab_total(counts: PeopleCounts, tab: Tab) -> int:
    return {
        "autorizadas": counts.autorizadas,
        "pedindo": counts.pedindo,
        "retiradas": counts.retiradas,
    }[tab]


@router.get(PATH, include_in_schema=False)
async def pessoas(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    aba: str | None = None,
    q: str | None = None,
    page: str | None = None,
) -> Response:
    tab = parse_tab(aba)
    query = (q or "").strip()[:200]
    result, overall = await _load(request, tab, query, _page_number(page))
    is_htmx = bool(request.headers.get("hx-request"))
    rows = [] if isinstance(result, PendingPage) else [person_view(r) for r in result.rows]
    context = {
        "active": "pessoas",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "tab": tab,
        "tabs": TAB_META,
        "q": query,
        "page": result,
        "people": rows,
        "pending": result.rows if isinstance(result, PendingPage) else [],
        "counts": result.counts,
        "shown": result.total,
        "overall": _tab_total(overall, tab),
        "page_size": PAGE_SIZE,
        "qs": people_query(tab, query),
        "empty_text": EMPTY_TEXT[tab],
        "text_display": isinstance(result, PendingPage) and result.text_display,
    }
    name = "_pessoas_lista.html" if is_htmx else "pessoas.html"
    response = templates_for(request).TemplateResponse(request, name, context)
    response.headers["Vary"] = "HX-Request"
    return response
