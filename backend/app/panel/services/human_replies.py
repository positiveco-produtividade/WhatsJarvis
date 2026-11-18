"""Leitura da caixa de resposta ao vendedor e das respostas ja enviadas (retorno-humano.1).

Usado pelo drawer do ticket (RH-T09) e pelo Inbox (RH-T12): se o ticket aceita resposta,
por que nao (RH-12), ha quanto tempo o vendedor escreveu (RH-10), um token novo por
renderizacao (RH-11) e a situacao de cada resposta da equipe (RH-15). So leitura; a escrita
e de ``app.services.human_reply.send_reply``. Nada aqui vai para log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from app.panel.services.formatting import format_datetime
from app.services.human_reply import block_reason

__all__ = [
    "BLOCK_TEXTS",
    "INVALID_TEXTS",
    "ReplyBox",
    "SentReply",
    "ago_label",
    "load_reply_box",
    "outbound_state",
]

BLOCK_TEXTS: dict[str, str] = {
    "revoked": "O acesso deste vendedor foi revogado: a resposta não pode ser enviada.",
    "declined": "Este vendedor recusou o aviso de privacidade: a resposta não pode ser enviada.",
    "pending_optin": "Este vendedor ainda não aceitou o aviso: a resposta não pode ser enviada.",
    "deleted": "Os dados deste vendedor foram excluídos (LGPD): a resposta não pode ser enviada.",
}
INVALID_TEXTS: dict[str, str] = {
    "empty": "Escreva a resposta antes de enviar.",
    "too_long": "A resposta passou do limite de caracteres.",
}
BLOCKED_DEFAULT = "Este vendedor não está ativo: a resposta não pode ser enviada."
_DISCARD_TEXTS: dict[str, str] = {
    "revoked_before_send": "acesso revogado antes do envio",
    "status_changed": "o vendedor mudou de situação",
    "no_inbound_window": "fora da janela de 24 h",
}
_REPLYABLE_STATUSES = frozenset({"awaiting", "answered_human", "send_error"})

_BOX = """
SELECT t.id, t.status, u.status AS user_status, u.name AS user_name,
       (SELECT max(m.received_at) FROM chat.message_trace m WHERE m.user_id = t.user_id)
         AS last_seller_at
  FROM chat.tickets t JOIN access.users u ON u.id = t.user_id
 WHERE t.ticket_number = $1
"""
_REPLIES = """
SELECT body, author, status, created_at, sent_at, delivered_at, read_at, error_code,
       discard_reason
  FROM chat.outbound_messages
 WHERE ticket_id = $1 AND kind = 'human_reply'
 ORDER BY id
"""


@dataclass(frozen=True, slots=True)
class SentReply:
    """Uma parte de resposta da equipe, pronta para exibir."""

    body: str
    author: str
    when_label: str
    state_label: str
    tone: str  # ok | pending | erro


@dataclass(frozen=True, slots=True)
class ReplyBox:
    """Caixa de resposta de um ticket. ``enabled`` falso = sem caixa (``block_text`` explica)."""

    ticket_number: str
    allowed: bool  # ticket aguardando ou ja respondido pela equipe: a caixa aparece
    enabled: bool
    block_text: str | None
    seller_wrote_label: str
    token: str
    max_chars: int
    replies: tuple[SentReply, ...]


def ago_label(then: datetime | None, now: datetime) -> str:
    """``há 45 min``, ``há 3 h``, ``há 2 dias``; sem data, ``—``."""
    if then is None:
        return "—"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "há menos de 1 min"
    if seconds < 3600:
        return f"há {seconds // 60} min"
    if seconds < 86400:
        return f"há {seconds // 3600} h"
    days = seconds // 86400
    return f"há {days} dia" if days == 1 else f"há {days} dias"


def outbound_state(row: Any) -> tuple[str, str]:
    """(rotulo, tom) da situacao de uma resposta (RH-15)."""
    status = row["status"]
    if status in ("failed", "unknown"):
        code = row["error_code"]
        return (f"falhou ({code})" if code else "falhou"), "erro"
    if status == "discarded":
        reason = _DISCARD_TEXTS.get(row["discard_reason"] or "", "descartada")
        return f"não enviada: {reason}", "erro"
    if status in ("pending", "sending"):
        return "na fila", "pending"
    if row["read_at"] is not None:
        return "lida", "ok"
    if row["delivered_at"] is not None:
        return "entregue", "ok"
    return "enviada", "ok"


def _reply_view(row: Any) -> SentReply:
    label, tone = outbound_state(row)
    return SentReply(
        body=row["body"] or "",
        author=row["author"] or "Equipe",
        when_label=format_datetime(row["sent_at"] or row["created_at"]),
        state_label=label,
        tone=tone,
    )


async def load_reply_box(
    conn: asyncpg.Connection, ticket_number: str, *, now: datetime, max_chars: int
) -> ReplyBox | None:
    """Caixa de resposta do ticket; ``None`` se o ticket nao existir."""
    row = await conn.fetchrow(_BOX, ticket_number)
    if row is None:
        return None
    # Ticket que espera a equipe ou ja respondido por ela (complemento); o respondido pelo bot
    # nao tem caixa. Em producao todo `awaiting` e escalado (question_tickets, tickets).
    allowed = row["status"] in _REPLYABLE_STATUSES
    reason = block_reason(row["user_status"], row["user_name"])
    replies = tuple(_reply_view(r) for r in await conn.fetch(_REPLIES, row["id"]))
    return ReplyBox(
        ticket_number=ticket_number,
        allowed=allowed,
        enabled=allowed and reason is None,
        block_text=BLOCK_TEXTS.get(reason, BLOCKED_DEFAULT) if reason else None,
        seller_wrote_label=ago_label(row["last_seller_at"], now),
        token=str(uuid.uuid4()),
        max_chars=max_chars,
        replies=replies,
    )
