"""Tickets escalados de midia em chat.tickets (R1, AC-36).

Midia de usuario autorizado vira ticket imediato: sem alerta, sem e-mail (o CHECK
ticket_never_emails tambem garante), so uma entrega `group` conforme a chave.
Roda na transacao do chamador. Nunca guarda o conteudo da midia.
"""

from dataclasses import dataclass
from typing import Any

from app.core.clock import Clock, SystemClock
from app.services.flags import Flags

MEDIA_LABELS: dict[str, str] = {
    "audio": "[áudio]",
    "image": "[imagem]",
    "video": "[vídeo]",
    "document": "[documento]",
    "sticker": "[figurinha]",
}
DEFAULT_MEDIA_LABEL = "[mídia]"

_INSERT_TICKET = (
    "INSERT INTO chat.tickets (ticket_number, user_id, trace_id, kind, brand, classification, "
    "message, escalated, status, urgency, created_at, updated_at) "
    "VALUES ($1, $2, $3, 'media', NULL, 'MIDIA', $4, true, 'awaiting', 'urgente', $5, $5) "
    "RETURNING id"
)
_INSERT_DELIVERY = (
    "INSERT INTO ops.deliveries (source_type, source_id, channel, status, next_attempt_at, "
    "created_at) VALUES ('ticket', $1, 'group', $2, $3, $3)"
)


@dataclass(frozen=True)
class MediaTicket:
    id: int
    ticket_number: str


def media_label(msg_type: str) -> str:
    """Rotulo curto do tipo de midia; nunca o conteudo."""
    return MEDIA_LABELS.get(msg_type, DEFAULT_MEDIA_LABEL)


def format_ticket_number(n: int) -> str:
    """J-nnnn com no minimo 4 digitos (sem truncar acima de 9999)."""
    return f"J-{n:04d}"


async def create_media_ticket(
    conn: Any,
    user_id: int,
    trace_id: int | None,
    msg_type: str,
    *,
    flags: Flags,
    clock: Clock | None = None,
) -> MediaTicket:
    """Cria o ticket de midia e a unica entrega de grupo (`pending` ou `panel_only`)."""
    now = (clock or SystemClock()).now()
    seq: int = await conn.fetchval("SELECT nextval('ops.ticket_seq')")
    number = format_ticket_number(seq)
    ticket_id: int = await conn.fetchval(
        _INSERT_TICKET, number, user_id, trace_id, media_label(msg_type), now
    )
    group_on = await flags.team_group_enabled(conn=conn)
    await conn.execute(_INSERT_DELIVERY, ticket_id, "pending" if group_on else "panel_only", now)
    return MediaTicket(id=ticket_id, ticket_number=number)
