"""Chat do Inbox, concluir e lote (spec painel.1, 4.5; task PN-T22; PN-11, PN-12, PN-13, PN-14).

- ``GET  /dashboard/inbox/{user_id}``           fragmento do chat (HTMX -> ``#chat-pane``)
- ``POST /dashboard/inbox/{user_id}/concluir``  conclui os tickets aguardando da conversa
- ``POST /dashboard/inbox/concluir-lote``       conclui os aguardando das conversas marcadas

Somente leitura e conclusao: NAO ha caixa de resposta e NADA e enviado ao vendedor. "Responder
em: Grupo da equipe" e so um indicador (desativado quando a chave ``team_group_enabled`` esta
desligada, lida pela mesma conexao do pedido). Os tickets recentes abrem o drawer (T19).
Toda rota exige ``current_admin``; todo POST, ``require_csrf``. Uma conexao do pool por pedido.
Nenhum texto de mensagem nem telefone vai a log.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.services import tickets_admin
from app.panel.services.inbox_read import Chat, RecentTicket, get_chat, parse_tab
from app.panel.services.scope import MEDIA_KIND, Scope
from app.panel.values import classification_label, profile_label, ticket_status_label
from app.services.flags import Flags

__all__ = ["RecentView", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

TEMPLATE = "_inbox_chat.html"
MAX_BATCH = 200
AUTHOR_LIMIT = 80
NOT_FOUND_TEXT = "Conversa não encontrada."


@dataclass(frozen=True, slots=True)
class RecentView:
    """Um botao de "Tickets recentes" ja formatado."""

    number: str
    classification_name: str
    badge: str | None
    badge_tone: str


@dataclass(frozen=True, slots=True)
class Escalated:
    """Selo "<Classificacao> J-nnnn escalado" (ticket escalado ainda aguardando)."""

    number: str
    classification_name: str


def _badge(ticket: RecentTicket) -> tuple[str | None, str]:
    if ticket.status == "answered_bot":
        return None, ""
    if ticket.status == "answered_human":
        return "Respondido por humano", "mint"
    if ticket.status == "send_error":
        return "Erro no envio", "red"
    return ("Escalado" if ticket.escalated else ticket_status_label(ticket.status)), "amber"


def _recent_views(chat: Chat) -> list[RecentView]:
    views: list[RecentView] = []
    for ticket in chat.recent_tickets:
        badge, tone = _badge(ticket)
        views.append(
            RecentView(
                number=ticket.ticket_number,
                classification_name=classification_label(ticket.classification),
                badge=badge,
                badge_tone=tone,
            )
        )
    return views


def _escalated(chat: Chat) -> Escalated | None:
    awaiting = set(chat.awaiting_ticket_numbers)
    for ticket in chat.recent_tickets:
        if ticket.escalated and ticket.ticket_number in awaiting:
            return Escalated(ticket.ticket_number, classification_label(ticket.classification))
    return None


def _flags(request: Request) -> Flags:
    state = request.app.state
    existing: Flags | None = getattr(state, "flags", None)
    if existing is not None:
        return existing
    settings = getattr(state, "settings", None)
    initial = bool(getattr(settings, "team_group_enabled", False))
    return Flags(state.pool, get_clock(request), team_group_initial=initial)


def _toast(message: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"toast": {"message": message, "kind": kind}, **extra}


def render_chat(
    request: Request,
    admin: PanelSession,
    chat: Chat | None,
    *,
    team_group: bool,
    notice: dict[str, str] | None = None,
    status_code: int = 200,
    trigger: dict[str, Any] | None = None,
) -> Response:
    context = {
        "chat": chat,
        "recent": _recent_views(chat) if chat else [],
        "escalated": _escalated(chat) if chat else None,
        "profile_name": profile_label(chat.header.profile) if chat else "",
        "team_group": team_group,
        "notice": notice,
        "admin_name": admin.name,
        "csrf_token": csrf_token_for(request, admin),
    }
    response = templates_for(request).TemplateResponse(
        request, TEMPLATE, context, status_code=status_code
    )
    if trigger:
        response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def _author(admin: PanelSession, typed: str = "") -> str:
    """Quem concluiu: o informado ou o nome do admin logado (ou e-mail, se sem nome)."""
    name = " ".join((typed or admin.name or admin.email).split())
    return name[:AUTHOR_LIMIT]


@router.get("/dashboard/inbox/{user_id}", include_in_schema=False)
async def inbox_chat(
    request: Request, user_id: int, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        chat = await get_chat(conn, user_id, now=get_clock(request).now())
        team_group = await _flags(request).team_group_enabled(conn=conn)
    if chat is None:
        return render_chat(
            request,
            admin,
            None,
            team_group=team_group,
            notice={"kind": "erro", "text": NOT_FOUND_TEXT},
            status_code=404,
        )
    return render_chat(request, admin, chat, team_group=team_group)


async def _conclude_all(
    conn: asyncpg.Connection, ticket_ids: list[int], admin: PanelSession, request: Request
) -> int:
    """Conclui cada ticket (transacao propria, com auditoria); devolve quantos mudaram."""
    clock = get_clock(request)
    done = 0
    for ticket_id in ticket_ids:
        result = await tickets_admin.conclude(
            conn, ticket_id, _author(admin), actor=admin.email, clock=clock
        )
        done += 1 if result.concluded else 0
    return done


async def _awaiting_ids(
    conn: asyncpg.Connection,
    user_ids: list[int],
    scope: Scope,
    *,
    tab: str | None = None,
    number: str | None = None,
) -> list[int]:
    params: list[object] = [user_ids]
    parts = ["user_id = ANY($1::bigint[])", "status = 'awaiting'"]
    parts.append(scope.sql("brand", params, kind_column="kind"))
    if tab is not None:
        parts.append(f"kind {'=' if tab == 'midias' else '<>'} '{MEDIA_KIND}'")
    if number is not None:
        params.append(number)
        parts.append(f"ticket_number = ${len(params)}")
    rows = await conn.fetch(
        "SELECT id FROM ops.v_tickets WHERE " + " AND ".join(parts) + " ORDER BY id",  # noqa: S608
        *params,
    )
    return [int(r["id"]) for r in rows]


@router.post(
    "/dashboard/inbox/{user_id}/concluir",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def concluir_conversa(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    ticket: Annotated[str, Form()] = "",
) -> Response:
    """Conclui o ticket indicado (ou todos os aguardando) da conversa; nada e enviado."""
    async with request.app.state.pool.acquire() as conn:
        ids = await _awaiting_ids(
            conn, [user_id], scope, number=ticket.strip() or None
        )
        done = await _conclude_all(conn, ids, admin, request)
        if done:
            logger.info("panel_inbox_concluded", extra={"count": done})
        chat = await get_chat(conn, user_id, now=get_clock(request).now())
        team_group = await _flags(request).team_group_enabled(conn=conn)
    if chat is None:
        return render_chat(
            request,
            admin,
            None,
            team_group=team_group,
            notice={"kind": "erro", "text": NOT_FOUND_TEXT},
            status_code=404,
        )
    if done:
        text = f"{done} ticket{'' if done == 1 else 's'} concluído{'' if done == 1 else 's'}. "
        notice = {"kind": "ok", "text": text + "Nenhuma mensagem foi enviada."}
    else:
        notice = {"kind": "aviso", "text": "Nada aguardando atendimento nesta conversa."}
    trigger = _toast(notice["text"], notice["kind"], ticketChanged={"user": user_id})
    return render_chat(
        request, admin, chat, team_group=team_group, notice=notice, trigger=trigger
    )


def _parse_user_ids(raw: list[str]) -> list[int]:
    seen: dict[int, None] = {}
    for item in raw:
        text = item.strip()
        if text.isdigit() and 0 < int(text) < 2**62:
            seen[int(text)] = None
        if len(seen) >= MAX_BATCH:
            break
    return list(seen)


def _current_tab(request: Request) -> str:
    """Aba da lista de onde o lote saiu (``aba`` da URL atual do HTMX); padrao conversas."""
    current = request.headers.get("hx-current-url", "")
    values = parse_qs(urlparse(current).query).get("aba", [])
    return parse_tab(values[0] if values else None)


@router.post(
    "/dashboard/inbox/concluir-lote",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def concluir_lote(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    user_ids: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Conclui os tickets aguardando (da aba e da marca em foco) das conversas marcadas."""
    ids = _parse_user_ids(user_ids or [])
    if not ids:
        return Response(
            status_code=200,
            headers={
                "HX-Trigger": json.dumps(_toast("Selecione ao menos uma conversa.", "aviso"))
            },
        )
    async with request.app.state.pool.acquire() as conn:
        ticket_ids = await _awaiting_ids(conn, ids, scope, tab=_current_tab(request))
        done = await _conclude_all(conn, ticket_ids, admin, request)
    logger.info("panel_inbox_batch_concluded", extra={"count": done})
    if done == 0:
        message = "Nada aguardando nas conversas marcadas."
        return Response(
            status_code=200, headers={"HX-Trigger": json.dumps(_toast(message, "aviso"))}
        )
    return Response(status_code=200, headers={"HX-Refresh": "true"})
