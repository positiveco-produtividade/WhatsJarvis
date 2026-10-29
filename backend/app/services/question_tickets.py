"""Tickets `kind='question'` do cerebro em chat.tickets (CEREBRO-1, spec 4.7).

Espelha `services/tickets.py` (porta-fechada.1) sem edita-lo: numero J-nnnn da
`ops.ticket_seq` e uma entrega `group` (`pending` ou `panel_only` conforme a chave),
nunca e-mail. Roda na transacao do chamador. LGPD: a pergunta vai em `message` e o texto
legivel/resposta em `summary` (a exclusao zera os dois); `meta` nunca identifica a pessoa.
Um ticket de pergunta por rastro (indice unico parcial `tickets_question_trace_uq`).
"""

import json
from dataclasses import dataclass
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.values import BrainOutcome
from app.services.brain.intents import NAO_IDENTIFICADO, urgency_for
from app.services.flags import Flags
from app.services.tickets import format_ticket_number

REASON_BRAND_ACCESS_REQUEST = "brand_access_request"

_INSERT_TICKET = (
    "INSERT INTO chat.tickets (ticket_number, user_id, trace_id, kind, brand, classification, "
    "confidence, message, summary, escalation_reason, escalated, status, urgency, meta, "
    "created_at, updated_at) "
    "VALUES ($1, $2, $3, 'question', $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $14) "
    "ON CONFLICT (trace_id) WHERE kind = 'question' AND trace_id IS NOT NULL DO NOTHING "
    "RETURNING id"
)
_SELECT_BY_TRACE = (
    "SELECT id, ticket_number FROM chat.tickets WHERE kind = 'question' AND trace_id = $1"
)
_INSERT_DELIVERY = (
    "INSERT INTO ops.deliveries (source_type, source_id, channel, status, next_attempt_at, "
    "created_at) VALUES ('ticket', $1, 'group', $2, $3, $3)"
)
_SELECT_OPEN_ACCESS = (
    "SELECT id, ticket_number FROM chat.tickets WHERE kind = 'question' "
    "AND status = 'awaiting' AND escalation_reason = $1 AND user_id = $2 AND brand = $3 "
    "ORDER BY id LIMIT 1"
)


@dataclass(frozen=True)
class QuestionTicket:
    id: int
    ticket_number: str
    created: bool  # False: ja existia (mesmo rastro ou pedido de acesso aberto)


async def find_open_access_request(
    conn: Any, user_id: int, brand: str
) -> QuestionTicket | None:
    """Pedido de acesso `awaiting` do mesmo vendedor e marca (dedup, spec 4.7)."""
    row = await conn.fetchrow(_SELECT_OPEN_ACCESS, REASON_BRAND_ACCESS_REQUEST, user_id, brand)
    if row is None:
        return None
    return QuestionTicket(id=row["id"], ticket_number=row["ticket_number"], created=False)


async def create_question_ticket(
    conn: Any,
    *,
    user_id: int,
    trace_id: int | None,
    message: str | None,
    flags: Flags,
    clock: Clock | None = None,
    classification: str = NAO_IDENTIFICADO,
    brand: str | None = None,
    summary: str | None = None,
    escalation_reason: str | None = None,
    escalated: bool = False,
    outcome: BrainOutcome | None = None,
    urgency: str | None = None,
    confidence: float | None = None,
    meta: dict[str, Any] | None = None,
) -> QuestionTicket:
    """Cria o ticket de pergunta e sua entrega `group`; devolve o existente se o rastro repete.

    Escalado -> `awaiting` com urgencia (a informada, senao `urgency_for(outcome, classificacao)`);
    nao escalado -> `answered_bot` sem urgencia.
    """
    now = (clock or SystemClock()).now()
    if trace_id is not None:
        existing = await conn.fetchrow(_SELECT_BY_TRACE, trace_id)
        if existing is not None:
            return QuestionTicket(existing["id"], existing["ticket_number"], created=False)
    if escalated:
        final_urgency = urgency or urgency_for(
            outcome if outcome is not None else BrainOutcome.NO_ANSWER, classification
        )
    else:
        final_urgency = None
    status = "awaiting" if escalated else "answered_bot"
    seq: int = await conn.fetchval("SELECT nextval('ops.ticket_seq')")
    number = format_ticket_number(seq)
    ticket_id = await conn.fetchval(
        _INSERT_TICKET, number, user_id, trace_id, brand, classification, confidence,
        message, summary, escalation_reason, escalated, status, final_urgency,
        json.dumps(meta or {}), now,
    )
    if ticket_id is None:  # corrida: outra transacao inseriu o mesmo rastro
        row = await conn.fetchrow(_SELECT_BY_TRACE, trace_id)
        return QuestionTicket(row["id"], row["ticket_number"], created=False)
    group_on = await flags.team_group_enabled(conn=conn)
    await conn.execute(_INSERT_DELIVERY, ticket_id, "pending" if group_on else "panel_only", now)
    return QuestionTicket(id=ticket_id, ticket_number=number, created=True)


async def create_access_request_ticket(
    conn: Any,
    *,
    user_id: int,
    trace_id: int | None,
    message: str | None,
    brand: str,
    brand_label: str,
    flags: Flags,
    clock: Clock | None = None,
) -> QuestionTicket:
    """Pedido de acesso a marca; sem duplicar se o vendedor ja tem um aberto para a marca."""
    open_one = await find_open_access_request(conn, user_id, brand)
    if open_one is not None:
        return open_one
    return await create_question_ticket(
        conn,
        user_id=user_id,
        trace_id=trace_id,
        message=message,
        flags=flags,
        clock=clock,
        brand=brand,
        summary=f"Pedido de acesso à marca {brand_label}",
        escalation_reason=REASON_BRAND_ACCESS_REQUEST,
        escalated=True,
        outcome=BrainOutcome.ACCESS_REQUEST,
    )
