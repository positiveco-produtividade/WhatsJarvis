"""Drawer do ticket e suas acoes (spec painel.1 4.5; task PN-T19; PN-15..18, PN-63).

- ``GET  /dashboard/tickets/{n}``              fragmento do drawer (HTMX -> ``#drawer-body``)
- ``POST /dashboard/tickets/{n}/concluir``     registra quem concluiu; NADA e enviado ao vendedor
- ``POST /dashboard/tickets/{n}/reclassificar`` troca a classificacao (numero do ticket nao muda)
- ``POST /dashboard/tickets/{n}/urgencia``     troca a urgencia (ticket escalado)

As escritas passam por ``tickets_admin`` (auditoria na mesma transacao). Toda rota exige
``current_admin``; todo POST exige ``require_csrf``. O fragmento ``_ticket_drawer.html`` e
reaproveitado pelo Inbox e por Escalados via ``render_ticket_drawer``/``load_ticket_view``.
A conexao do pool e adquirida uma unica vez por pedido.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.core.clock import Clock
from app.core.errors import ValidationFailure
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.services import tickets_admin
from app.panel.services.formatting import format_datetime, format_time, to_sp
from app.panel.services.mask import mask_phone_display
from app.panel.values import (
    CLASSIFICATIONS,
    URGENCIES,
    classification_label,
    profile_label,
    ticket_status_label,
    urgency_label,
)

__all__ = [
    "TicketView",
    "load_ticket_view",
    "render_ticket_drawer",
    "router",
]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

TEMPLATE = "_ticket_drawer.html"
TICKET_NUMBER = re.compile(r"J-\d{1,9}")
NOT_FOUND_TEXT = "Ticket não encontrado."

_LOAD = """
SELECT t.id, t.ticket_number, t.created_at, t.kind, t.brand, t.classification, t.confidence,
       t.message, t.summary, t.escalation_reason, t.escalated, t.status, t.urgency,
       t.answered_by, t.answered_at, t.responsible,
       t.meta->>'parse' AS m_parse, t.meta->>'classifier' AS m_classifier,
       t.meta->>'agent' AS m_agent, t.meta->>'session' AS m_session,
       u.id AS seller_id, u.name AS seller_name, u.profile AS seller_profile,
       u.ufs AS seller_ufs, u.phone_e164 AS seller_phone,
       (SELECT count(*) FROM chat.message_trace mt WHERE mt.user_id = u.id) AS seller_msgs,
       (SELECT count(*) FROM ops.v_tickets x WHERE x.user_id = u.id) AS seller_tickets,
       m.send_error AS send_error
  FROM ops.v_tickets t
  JOIN access.users u ON u.id = t.user_id
  LEFT JOIN chat.message_trace m ON m.id = t.trace_id
 WHERE t.ticket_number = $1
"""
_TICKET_ID = "SELECT id FROM chat.tickets WHERE ticket_number = $1"


@dataclass(frozen=True, slots=True)
class TicketView:
    """Dados do drawer ja formatados para exibicao (o template so escreve)."""

    id: int
    number: str
    created_label: str
    brand: str | None
    classification: str
    classification_name: str
    status: str
    status_name: str
    confidence_pct: int | None
    message: str
    summary: str | None
    escalation_reason: str | None
    escalated: bool
    urgency: str | None
    urgency_name: str
    answered_by: str | None
    answered_label: str
    send_error: str | None
    responsible: str | None
    parse: str
    classifier: str
    agent: str
    session: str
    seller_id: int
    seller_name: str
    seller_profile: str
    seller_ufs: str
    seller_phone: str
    seller_msgs: int
    seller_tickets: int
    reclass_options: list[tuple[str, str]]
    urgency_options: list[tuple[str, str]]


def _confidence_pct(value: Any) -> int | None:
    return None if value is None else round(float(value) * 100)


def _view(row: asyncpg.Record) -> TicketView:
    status: str = row["status"]
    return TicketView(
        id=int(row["id"]),
        number=row["ticket_number"],
        created_label=format_datetime(row["created_at"]),
        brand=row["brand"],
        classification=row["classification"],
        classification_name=classification_label(row["classification"]),
        status=status,
        status_name=ticket_status_label(status),
        confidence_pct=_confidence_pct(row["confidence"]),
        message=row["message"] or "",
        summary=row["summary"],
        escalation_reason=row["escalation_reason"],
        escalated=bool(row["escalated"]),
        urgency=row["urgency"],
        urgency_name=urgency_label(row["urgency"]),
        answered_by=row["answered_by"],
        answered_label=format_datetime(row["answered_at"]),
        send_error=row["send_error"] if status == "send_error" else None,
        responsible=row["responsible"],
        parse=row["m_parse"] or "—",
        classifier=row["m_classifier"] or "—",
        agent=row["m_agent"] or "—",
        session=row["m_session"] or "—",
        seller_id=int(row["seller_id"]),
        seller_name=row["seller_name"],
        seller_profile=profile_label(row["seller_profile"]),
        seller_ufs=", ".join(row["seller_ufs"] or []),
        seller_phone=mask_phone_display(row["seller_phone"]),
        seller_msgs=int(row["seller_msgs"]),
        seller_tickets=int(row["seller_tickets"]),
        reclass_options=[
            (c, classification_label(c)) for c in CLASSIFICATIONS if c != row["classification"]
        ],
        urgency_options=[(u, urgency_label(u)) for u in URGENCIES],
    )


async def load_ticket_view(conn: asyncpg.Connection, number: str) -> TicketView | None:
    """Ticket pelo numero (``J-0142``) pronto para o drawer; ``None`` se nao existir."""
    if not TICKET_NUMBER.fullmatch(number):
        return None
    row = await conn.fetchrow(_LOAD, number)
    return _view(row) if row is not None else None


def render_ticket_drawer(
    request: Request,
    admin: PanelSession,
    ticket: TicketView | None,
    *,
    notice: dict[str, str] | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
    trigger: dict[str, Any] | None = None,
) -> Response:
    """Fragmento do drawer. ``trigger`` vira ``HX-Trigger`` (JSON ASCII; ex.: toast)."""
    context = {
        "ticket": ticket,
        "admin_name": admin.name,
        "csrf_token": csrf_token_for(request, admin),
        "notice": notice,
        "errors": errors or [],
    }
    response = templates_for(request).TemplateResponse(
        request, TEMPLATE, context, status_code=status_code
    )
    if trigger:
        response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def _toast(message: str, kind: str, number: str) -> dict[str, Any]:
    return {"toast": {"message": message, "kind": kind}, "ticketChanged": {"ticket": number}}


def _when(value: datetime | None, clock: Clock) -> str:
    """``hh:mm`` no mesmo dia (Sao Paulo); em outro dia, ``dd/mm/aaaa hh:mm``."""
    if value is None:
        return "—"
    if to_sp(value).date() == to_sp(clock.now()).date():
        return format_time(value)
    return format_datetime(value)


def _conclude_notice(
    number: str, result: tickets_admin.ConcludeResult, clock: Clock
) -> dict[str, str]:
    """Aviso do resultado de concluir (PN-16 sucesso; PN-17 "Ja concluido por X em hh:mm")."""
    if result.concluded:
        text = f"Ticket {number} concluído por {result.by}. Nenhuma mensagem foi enviada."
        return {"kind": "ok", "text": text}
    if result.status == "answered_human":
        when = _when(result.at, clock)
        return {"kind": "aviso", "text": f"Já concluído por {result.by} em {when}."}
    status = ticket_status_label(result.status)
    return {"kind": "aviso", "text": f"Este ticket não está aguardando atendimento ({status})."}


async def _ticket_id(conn: asyncpg.Connection, number: str) -> int | None:
    if not TICKET_NUMBER.fullmatch(number):
        return None
    found = await conn.fetchval(_TICKET_ID, number)
    return int(found) if found is not None else None


def _not_found(request: Request, admin: PanelSession) -> Response:
    return render_ticket_drawer(
        request, admin, None, notice={"kind": "erro", "text": NOT_FOUND_TEXT}, status_code=404
    )


async def _respond(
    request: Request,
    admin: PanelSession,
    conn: asyncpg.Connection,
    number: str,
    notice: dict[str, str],
    *,
    changed: bool,
) -> Response:
    ticket = await load_ticket_view(conn, number)
    if ticket is None:
        return _not_found(request, admin)
    kind = notice["kind"]
    trigger = _toast(notice["text"], kind, number) if changed or kind != "ok" else None
    return render_ticket_drawer(request, admin, ticket, notice=notice, trigger=trigger)


@router.get("/dashboard/tickets/{number}", include_in_schema=False)
async def ticket_drawer(
    request: Request, number: str, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        ticket = await load_ticket_view(conn, number)
    if ticket is None:
        return _not_found(request, admin)
    return render_ticket_drawer(request, admin, ticket)


@router.post(
    "/dashboard/tickets/{number}/concluir",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def concluir(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    author: Annotated[str, Form()] = "",
) -> Response:
    clock = get_clock(request)
    async with request.app.state.pool.acquire() as conn:
        ticket_id = await _ticket_id(conn, number)
        if ticket_id is None:
            return _not_found(request, admin)
        try:
            result = await tickets_admin.conclude(
                conn, ticket_id, author, actor=admin.email, clock=clock
            )
        except ValidationFailure as exc:
            notice = {"kind": "erro", "text": f"Quem concluiu: {exc.reason}."}
            return await _respond(request, admin, conn, number, notice, changed=False)
        if result.concluded:
            logger.info("panel_ticket_concluded")
        notice = _conclude_notice(number, result, clock)
        return await _respond(request, admin, conn, number, notice, changed=result.concluded)


@router.post(
    "/dashboard/tickets/{number}/reclassificar",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def reclassificar(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    classification: Annotated[str, Form()] = "",
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        ticket_id = await _ticket_id(conn, number)
        if ticket_id is None:
            return _not_found(request, admin)
        try:
            old = await tickets_admin.reclassify(
                conn, ticket_id, classification, actor=admin.email, clock=get_clock(request)
            )
        except ValidationFailure as exc:
            notice = {"kind": "erro", "text": f"Classificação: {exc.reason}."}
            return await _respond(request, admin, conn, number, notice, changed=False)
        logger.info("panel_ticket_reclassified")
        text = (
            f"Classificação alterada de {classification_label(old)} para "
            f"{classification_label(classification)}. O número do ticket não muda."
        )
        return await _respond(request, admin, conn, number, {"kind": "ok", "text": text},
                              changed=True)


@router.post(
    "/dashboard/tickets/{number}/urgencia",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def urgencia(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    urgency: Annotated[str, Form()] = "",
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        ticket_id = await _ticket_id(conn, number)
        if ticket_id is None:
            return _not_found(request, admin)
        try:
            old = await tickets_admin.set_urgency(
                conn, ticket_id, urgency, actor=admin.email, clock=get_clock(request)
            )
        except ValidationFailure as exc:
            notice = {"kind": "erro", "text": f"Urgência: {exc.reason}."}
            return await _respond(request, admin, conn, number, notice, changed=False)
        changed = old != urgency
        if changed:
            logger.info("panel_ticket_urgency_changed")
        text = f"Urgência definida como {urgency_label(urgency)}."
        return await _respond(request, admin, conn, number, {"kind": "ok", "text": text},
                              changed=changed)
