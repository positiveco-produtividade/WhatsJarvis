"""Resposta da equipe ao vendedor pelo painel (retorno-humano.1; task RH-T09; RH-6 a RH-12).

- ``POST /dashboard/tickets/{n}/responder``  envia o texto ao vendedor e conclui o ticket

Ponto unico de envio do painel: o drawer do ticket (``origem=drawer``) posta aqui; o Inbox
(``origem=inbox``) reaproveita a rota (RH-T12). A regra de negocio e de
``app.services.human_reply.send_reply`` (uma transacao). Depois do COMMIT o envio e
disparado na hora pelo despachante do app (``app.state.outbound_dispatcher``), sem esperar a
varredura de fundo de 15 s (spec 4: p95 <= 10 s). Exige ``current_admin`` e
``require_csrf``. Logs so com o numero do ticket e codigos; nunca o texto da resposta.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response
from starlette.background import BackgroundTask

from app.panel.api.inbox_chat import load_chat_reply, render_chat, team_group_flags
from app.panel.api.ticket_drawer import load_reply, load_ticket_view, render_ticket_drawer
from app.panel.auth.sessions import PanelSession
from app.panel.deps import PanelRoute, current_admin, get_clock, get_settings, require_csrf
from app.panel.services.human_replies import BLOCK_TEXTS, BLOCKED_DEFAULT, INVALID_TEXTS
from app.panel.services.inbox_read import get_chat
from app.services.human_reply import ReplyResult, send_reply

__all__ = ["notice_for", "router", "schedule_dispatch"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

ORIGIN_DRAWER = "drawer"
ORIGIN_INBOX = "inbox"
TOKEN_TEXT = "O formulário expirou. Abra o ticket de novo e envie."
NOT_FOUND_TEXT = "Ticket não encontrado."
_TICKET_OWNER = "SELECT user_id FROM chat.tickets WHERE ticket_number = $1"


def notice_for(result: ReplyResult) -> dict[str, str]:
    """Aviso do resultado do envio (kind ok | aviso | erro)."""
    number = result.ticket_number
    if result.status == "sent":
        what = "Complemento enviado" if result.complement else "Resposta enviada"
        return {"kind": "ok", "text": f"{what} ao vendedor ({number})."}
    if result.status == "duplicate":
        return {"kind": "aviso", "text": "Essa resposta já tinha sido enviada."}
    if result.status == "blocked":
        return {"kind": "erro", "text": BLOCK_TEXTS.get(result.reason or "", BLOCKED_DEFAULT)}
    if result.status == "invalid":
        text = INVALID_TEXTS.get(result.reason or "", "Resposta inválida.")
        return {"kind": "erro", "text": text}
    return {"kind": "erro", "text": NOT_FOUND_TEXT}


def schedule_dispatch(request: Request, response: Response, outbound_ids: tuple[int, ...]) -> None:
    """Dispara o envio das partes depois da resposta HTTP (o COMMIT ja aconteceu)."""
    dispatcher = getattr(request.app.state, "outbound_dispatcher", None)
    if dispatcher is not None and outbound_ids:
        response.background = BackgroundTask(dispatcher, outbound_ids, ())


def _trigger(notice: dict[str, str], number: str, changed: bool) -> dict[str, Any]:
    trigger: dict[str, Any] = {"toast": {"message": notice["text"], "kind": notice["kind"]}}
    if changed:
        trigger["ticketChanged"] = {"ticket": number}
    return trigger


async def _drawer(
    request: Request,
    admin: PanelSession,
    number: str,
    notice: dict[str, str],
    *,
    changed: bool,
    text: str,
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        ticket = await load_ticket_view(conn, number)
        reply = await load_reply(request, conn, number) if ticket is not None else None
    if ticket is None:
        return render_ticket_drawer(
            request, admin, None, notice={"kind": "erro", "text": NOT_FOUND_TEXT}, status_code=404
        )
    response = render_ticket_drawer(
        request,
        admin,
        ticket,
        notice=notice,
        trigger=_trigger(notice, number, changed),
        reply=reply,
        reply_text="" if changed else text,
    )
    return response


async def _chat(
    request: Request,
    admin: PanelSession,
    number: str,
    notice: dict[str, str],
    *,
    changed: bool,
    text: str,
) -> Response:
    """Chat do Inbox depois do envio (``origem=inbox``), com a caixa no mesmo ticket."""
    async with request.app.state.pool.acquire() as conn:
        owner = await conn.fetchval(_TICKET_OWNER, number)
        chat = await get_chat(conn, int(owner), now=get_clock(request).now()) if owner else None
        team_group = await team_group_flags(request).team_group_enabled(conn=conn)
        reply, focus = (
            await load_chat_reply(request, conn, chat, number) if chat else (None, None)
        )
    if chat is None:
        return render_chat(
            request,
            admin,
            None,
            team_group=team_group,
            notice={"kind": "erro", "text": NOT_FOUND_TEXT},
            status_code=404,
        )
    return render_chat(
        request,
        admin,
        chat,
        team_group=team_group,
        notice=notice,
        trigger=_trigger(notice, number, changed),
        reply=reply,
        focus_ticket=focus,
        reply_text="" if changed else text,
    )


@router.post(
    "/dashboard/tickets/{number}/responder",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def responder(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    texto: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
    origem: Annotated[str, Form()] = ORIGIN_DRAWER,
) -> Response:
    render = _chat if origem == ORIGIN_INBOX else _drawer
    try:
        client_token = uuid.UUID(token)
    except ValueError:
        notice = {"kind": "erro", "text": TOKEN_TEXT}
        return await render(request, admin, number, notice, changed=False, text=texto)
    async with request.app.state.pool.acquire() as conn:
        result = await send_reply(
            conn,
            number,
            texto,
            author_name=admin.name or None,
            author_email=admin.email,
            client_token=client_token,
            settings=get_settings(request),
            clock=get_clock(request),
        )
    logger.info(
        "panel_ticket_reply",
        extra={"ticket": number, "status": result.status, "reason": result.reason},
    )
    notice = notice_for(result)
    changed = result.status == "sent"
    response = await render(request, admin, number, notice, changed=changed, text=texto)
    if changed:
        schedule_dispatch(request, response, result.outbound_ids)
    return response
