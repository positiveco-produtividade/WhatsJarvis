"""Pagina Logs e Erros (spec painel.1; task PN-T60; PN-54, PN-43).

- ``GET /dashboard/logs``: abas Problemas, Servidor, Auditoria (T59) e Retencao (T62). ``?aba=``
  escolhe a aba inicial; as duas ultimas carregam suas sub-rotas por HTMX.
- ``GET /dashboard/logs/problemas``: Ticket/Data/Problema/Detalhe (falha de envio e rastros com
  ``decision='error'``). Telefone sempre mascarado; corpo de mensagem nunca e lido.
- ``GET /dashboard/logs/servidor``: final do log (ultimas 500 linhas JSON ja redigidas, guardadas
  em memoria por ``log_tail``; zera ao reiniciar).

So administradores. Uma conexao do pool por pedido, liberada antes de renderizar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.core.log_redaction import redact
from app.panel.auth.sessions import PanelSession
from app.panel.deps import PanelRoute, brand_scope, csrf_token_for, current_admin, templates_for
from app.panel.services.formatting import format_short_datetime
from app.panel.services.log_tail import MAX_LINES, get_ring
from app.panel.services.mask import mask_phone_display
from app.panel.services.scope import Scope

__all__ = ["PATH", "ProblemRow", "load_problems", "router"]

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/logs"
PROBLEMS_LIMIT = 100
DETAIL_MAX = 200
EMPTY = "—"
TABS: tuple[tuple[str, str, str], ...] = (
    ("problemas", "Problemas", f"{PATH}/problemas"),
    ("servidor", "Servidor", f"{PATH}/servidor"),
    ("auditoria", "Auditoria", f"{PATH}/auditoria"),
    ("retencao", "Retenção", f"{PATH}/retencao"),
)
TAB_KEYS = frozenset(key for key, _, _ in TABS)

# Nunca seleciona `body`: o corpo da mensagem nao entra na tela.
_PROBLEMS_SQL = (
    "SELECT t.ticket_number, t.received_at, t.decision, t.decision_reason, t.send_result, "
    "t.send_error, u.phone_e164 "
    "FROM chat.message_trace t LEFT JOIN access.users u ON u.id = t.user_id "
    "WHERE t.send_result = 'failed' OR t.decision = 'error' "
    "ORDER BY t.received_at DESC, t.id DESC LIMIT $1"
)

# Rotulos fixos do problema.
SEND_FAILED_LABEL = "Falha de envio"
ERROR_LABEL = "Erro ao processar"


@dataclass(frozen=True, slots=True)
class ProblemRow:
    ticket: str
    when: str
    problem: str
    detail: str


def _detail(row: Any) -> str:
    """Codigo/motivo curto, sem PII (passa pela redacao do log) e com telefone mascarado."""
    raw = row["send_error"] if row["send_result"] == "failed" else row["decision_reason"]
    text = redact(str(raw or "")).strip()[:DETAIL_MAX] or EMPTY
    phone = mask_phone_display(row["phone_e164"])
    return text if phone == EMPTY else f"{text} · {phone}"


def _problem_row(row: Any) -> ProblemRow:
    received: datetime | None = row["received_at"]
    return ProblemRow(
        ticket=row["ticket_number"] or EMPTY,
        when=format_short_datetime(received),
        problem=SEND_FAILED_LABEL if row["send_result"] == "failed" else ERROR_LABEL,
        detail=_detail(row),
    )


async def load_problems(request: Request) -> list[ProblemRow]:
    async with request.app.state.pool.acquire() as conn:
        records = await conn.fetch(_PROBLEMS_SQL, PROBLEMS_LIMIT)
    return [_problem_row(r) for r in records]


def server_lines() -> list[str]:
    """Final do log, mais nova por ultimo, redigido de novo por seguranca."""
    return [redact(line) for line in get_ring().lines()]


# Liga o anel assim que o painel e montado (o modulo e importado por `discover_routers`).
get_ring()


@router.get(PATH + "/problemas", include_in_schema=False)
async def problemas(
    request: Request, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    context = {"rows": await load_problems(request)}
    return templates_for(request).TemplateResponse(request, "_logs_problemas.html", context)


@router.get(PATH + "/servidor", include_in_schema=False)
async def servidor(
    request: Request, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    context = {"lines": server_lines(), "max_lines": MAX_LINES}
    return templates_for(request).TemplateResponse(request, "_logs_servidor.html", context)


@router.get(PATH, include_in_schema=False)
async def logs(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    aba: str | None = None,
) -> Response:
    tab = aba if aba in TAB_KEYS else "problemas"
    context: dict[str, Any] = {
        "active": "logs",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "tab": tab,
        "tabs": TABS,
        "rows": await load_problems(request) if tab == "problemas" else [],
        "lines": server_lines() if tab == "servidor" else [],
        "max_lines": MAX_LINES,
    }
    return templates_for(request).TemplateResponse(request, "logs.html", context)
