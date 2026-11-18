"""Resposta da equipe ao vendedor de um ticket (retorno-humano.1, spec 9.4.2; RH-7 a RH-16).

`send_reply` roda numa transacao (savepoint se o chamador ja tiver uma): trava o ticket,
recusa vendedor que nao esta `active`, enfileira as partes `human_reply` na fila de saida,
conclui o ticket `awaiting` com o autor, cria ou completa a sugestao de FAQ e grava a
auditoria. Nada externo acontece aqui: o envio e do despacho da fila (outbox), depois do
COMMIT. Nao importa `app.panel`. Logs so com o numero do ticket e codigos (nunca texto,
nome ou telefone).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.values import OutboundKind, UserStatus
from app.services import audit, outbox
from app.services.brain.assembly import PART_MAX_CHARS, pack_parts

logger = logging.getLogger(__name__)

ReplyStatus = Literal["sent", "duplicate", "blocked", "not_found", "invalid"]

AUTHOR_LIMIT = 80
REASON_EMPTY = "empty"
REASON_TOO_LONG = "too_long"
REASON_DELETED = "deleted"
_ANON_PREFIX = "anon:"
# Mesma lista do CHECK de content.faq_items.category (migration 101).
_FAQ_CATEGORIES = frozenset({"PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA"})

_LOCK_TICKET = (
    "SELECT t.id, t.ticket_number, t.user_id, t.kind, t.brand, t.classification, t.message, "
    "t.status, u.status AS user_status, u.name AS user_name, u.phone_e164 "
    "FROM chat.tickets t JOIN access.users u ON u.id = t.user_id "
    "WHERE t.ticket_number = $1 FOR UPDATE OF t"
)
_TOKEN_USED = "SELECT 1 FROM chat.outbound_messages WHERE client_token = $1::uuid"
_CONCLUDE = (
    "UPDATE chat.tickets SET status = 'answered_human', answered_by = $2, answered_at = $3, "
    "updated_at = $3 WHERE id = $1 AND status = 'awaiting'"
)
_UPSERT_SUGGESTION = (
    "INSERT INTO content.faq_suggestions (ticket_id, ticket_number, user_id, brand, category, "
    "question, answer, author, created_at, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9) "
    "ON CONFLICT (ticket_number) DO UPDATE "
    "SET answer = concat_ws(E'\\n\\n', content.faq_suggestions.answer, EXCLUDED.answer), "
    "updated_at = EXCLUDED.updated_at "
    "WHERE content.faq_suggestions.status = 'pendente' "
    "RETURNING (xmax = 0) AS created"
)


@dataclass(frozen=True)
class ReplyResult:
    """Resultado de `send_reply`. `reason` explica `blocked` e `invalid`."""

    status: ReplyStatus
    ticket_number: str
    reason: str | None = None
    outbound_ids: tuple[int, ...] = ()
    complement: bool = False


@dataclass(frozen=True)
class _Target:
    """Destino da fila de saida (`outbox.OutboundTarget`)."""

    id: int
    phone_e164: str


def first_name(author_name: str | None) -> str | None:
    """Primeiro nome de quem respondeu (D7); None se nao houver nome (ou se for um e-mail)."""
    words = (author_name or "").split()
    if not words or "@" in words[0]:
        return None
    return words[0]


def header_for(author_name: str | None, settings: Settings) -> str:
    """'Retorno da equipe (Caio) sobre sua pergunta:' ou, sem nome, o cabecalho sem parenteses."""
    name = first_name(author_name)
    if name is None:
        return settings.text_human_reply_header_no_name
    return settings.text_human_reply_header.replace("{nome}", name)


def author_label(author_name: str | None, author_email: str) -> str:
    """Quem respondeu, como o painel registra no ticket: nome do admin, senao o e-mail."""
    return " ".join((author_name or author_email).split())[:AUTHOR_LIMIT]


def block_reason(user_status: str, user_name: str | None) -> str | None:
    """Motivo para nao enviar (RH-12), ou None se o vendedor esta ativo."""
    if user_status == UserStatus.ACTIVE.value:
        return None
    if (user_name or "").startswith(_ANON_PREFIX):
        return REASON_DELETED  # exclusao LGPD: revogado e anonimizado
    return user_status


async def send_reply(
    conn: Any,
    ticket_number: str,
    text: str,
    *,
    author_name: str | None,
    author_email: str,
    client_token: UUID,
    settings: Settings,
    clock: Clock | None = None,
) -> ReplyResult:
    """Envia (enfileira) a resposta da equipe ao vendedor do ticket e conclui o ticket."""
    body = text.strip()
    if not body:
        return ReplyResult("invalid", ticket_number, REASON_EMPTY)
    if len(body) > settings.human_reply_max_chars:
        return ReplyResult("invalid", ticket_number, REASON_TOO_LONG)
    now = (clock or SystemClock()).now()
    async with conn.transaction():
        ticket = await conn.fetchrow(_LOCK_TICKET, ticket_number)
        if ticket is None:
            return ReplyResult("not_found", ticket_number)
        if await conn.fetchval(_TOKEN_USED, client_token) is not None:
            return ReplyResult("duplicate", ticket_number)
        reason = block_reason(ticket["user_status"], ticket["user_name"])
        if reason is not None:
            logger.info("human_reply_blocked", extra={"ticket": ticket_number, "reason": reason})
            return ReplyResult("blocked", ticket_number, reason)

        author = author_label(author_name, author_email)
        message = f"{header_for(author_name, settings)}\n\n{body}"
        target = _Target(ticket["user_id"], ticket["phone_e164"])
        ids: list[int] = []
        for index, part in enumerate(pack_parts(message, PART_MAX_CHARS)):
            ids.append(
                await outbox.enqueue(
                    conn,
                    target,
                    OutboundKind.HUMAN_REPLY,
                    body=part,
                    ticket_id=ticket["id"],
                    author=author,
                    author_email=author_email,
                    client_token=client_token if index == 0 else None,
                    clock=clock,
                )
            )
        complement = ticket["status"] != "awaiting"
        if not complement:
            await conn.execute(_CONCLUDE, ticket["id"], author, now)
        await _suggest(conn, ticket, body, author, now)
        await audit.write(
            conn,
            "panel",
            author_email,
            "ticket.reply",
            "ticket",
            ticket_number,
            {"status": ticket["status"]},
            {"parts": len(ids), "chars": len(body), "complement": complement},
            clock=clock,
        )
    logger.info(
        "human_reply_queued",
        extra={"ticket": ticket_number, "parts": len(ids), "complement": complement},
    )
    return ReplyResult("sent", ticket_number, outbound_ids=tuple(ids), complement=complement)


async def _suggest(conn: Any, ticket: Any, answer: str, author: str, now: Any) -> None:
    """Sugestao de FAQ (RH-16): so pergunta com texto; complemento concatena se pendente."""
    question = (ticket["message"] or "").strip()
    if ticket["kind"] != "question" or not question:
        return
    category = ticket["classification"] if ticket["classification"] in _FAQ_CATEGORIES else None
    row = await conn.fetchrow(
        _UPSERT_SUGGESTION,
        ticket["id"],
        ticket["ticket_number"],
        ticket["user_id"],
        ticket["brand"],
        category,
        question,
        answer,
        author,
        now,
    )
    if row is not None and row["created"]:
        logger.info("faq_suggestion_created", extra={"ticket": ticket["ticket_number"]})
