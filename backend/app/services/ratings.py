"""Avaliacao positiva/negativa das respostas do cerebro (CEREBRO-1, spec 4.6; CB-25, CB-29).

Roda na transacao do chamador (o pipeline, no rastro do toque). Primeira avaliacao vale:
`chat.answer_ratings` e UNIQUE por ticket, entao repeticao ou botao oposto nao insere nada,
nao cria ticket e nao envia agradecimento. Botao de outro vendedor, ticket inexistente ou
payload adulterado e ignorado. A avaliacao negativa cria um ticket escalado (`rating_down`,
`pouco_urgente`) com a resposta dada em `summary`; sem alerta e sem e-mail (a entrega `group`
segue a chave). Nao importa `app.panel`.
"""

import json
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.clock import Clock
from app.core.values import OutboundKind, parse_rating_payload
from app.services import outbox
from app.services.flags import Flags
from app.services.question_tickets import create_question_ticket

REASON_RATING_DOWN = "rating_down"
URGENCY_RATING_DOWN = "pouco_urgente"

_SELECT_TICKET = (
    "SELECT id, ticket_number, trace_id, message, classification, brand, meta "
    "FROM chat.tickets WHERE id = $1 AND kind = 'question' AND user_id = $2"
)
_INSERT_RATING = (
    "INSERT INTO chat.answer_ratings (ticket_id, user_id, trace_id, rating, rated_at) "
    "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (ticket_id) DO NOTHING RETURNING id"
)
_SET_DOWN_TICKET = "UPDATE chat.answer_ratings SET down_ticket_id = $1 WHERE id = $2"
_SELECT_ANSWER = (
    "SELECT body FROM chat.outbound_messages WHERE inbound_trace_id = $1 AND user_id = $2 "
    "AND kind IN ($3, $4) AND body IS NOT NULL ORDER BY id"
)
_SELECT_TARGET = "SELECT id, phone_e164 FROM access.users WHERE id = $1"


@dataclass(frozen=True)
class RatingResult:
    recorded: bool  # False: ignorado (adulterado, de outro vendedor) ou ja avaliado
    useful: bool | None  # a nota gravada; None se nada foi gravado
    outbound_ids: tuple[int, ...] = ()  # agradecimento da nota negativa, p/ o despachante


@dataclass(frozen=True)
class _Target:
    id: int
    phone_e164: str


async def handle_rating(
    conn: Any,
    *,
    user_id: int,
    payload: str,
    trace_id: int,
    clock: Clock,
    settings: Settings,
    flags: Flags,
) -> RatingResult:
    """Grava a avaliacao (uma vez por ticket); na negativa abre o ticket e enfileira o aviso.

    `trace_id` e o rastro do toque no botao.
    """
    parsed = parse_rating_payload(payload)
    if parsed is None:
        return RatingResult(recorded=False, useful=None)
    useful, ticket_id = parsed
    ticket = await conn.fetchrow(_SELECT_TICKET, ticket_id, user_id)
    if ticket is None:
        return RatingResult(recorded=False, useful=None)
    rating_id = await conn.fetchval(
        _INSERT_RATING, ticket_id, user_id, trace_id, "up" if useful else "down", clock.now()
    )
    if rating_id is None:
        return RatingResult(recorded=False, useful=None)
    if useful:
        return RatingResult(recorded=True, useful=True)
    outbound_id = await _handle_down(
        conn, rating_id=rating_id, ticket=ticket, user_id=user_id, trace_id=trace_id,
        clock=clock, settings=settings, flags=flags,
    )
    return RatingResult(recorded=True, useful=False, outbound_ids=(outbound_id,))


async def _answer_given(conn: Any, rated_trace_id: int | None, user_id: int) -> str | None:
    if rated_trace_id is None:
        return None
    rows = await conn.fetch(
        _SELECT_ANSWER, rated_trace_id, user_id,
        OutboundKind.BRAIN_ANSWER.value, OutboundKind.BRAIN_ANSWER_RATE.value,
    )
    text = "\n\n".join(str(r["body"]) for r in rows)
    return text or None


def _inherited_refs(raw_meta: Any) -> Any:
    meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
    return meta.get("refs") if isinstance(meta, dict) else None


async def _handle_down(
    conn: Any,
    *,
    rating_id: int,
    ticket: Any,
    user_id: int,
    trace_id: int,
    clock: Clock,
    settings: Settings,
    flags: Flags,
) -> int:
    summary = await _answer_given(conn, ticket["trace_id"], user_id)
    meta: dict[str, Any] = {"rated_ticket_number": ticket["ticket_number"]}
    refs = _inherited_refs(ticket["meta"])
    if refs is not None:
        meta["refs"] = refs
    down = await create_question_ticket(
        conn,
        user_id=user_id,
        trace_id=trace_id,
        message=ticket["message"],
        flags=flags,
        clock=clock,
        classification=ticket["classification"],
        brand=ticket["brand"],
        summary=summary,
        escalation_reason=REASON_RATING_DOWN,
        escalated=True,
        urgency=URGENCY_RATING_DOWN,
        meta=meta,
    )
    await conn.execute(_SET_DOWN_TICKET, down.id, rating_id)
    row = await conn.fetchrow(_SELECT_TARGET, user_id)
    target = _Target(id=row["id"], phone_e164=row["phone_e164"])
    return await outbox.enqueue(
        conn, target, OutboundKind.BRAIN_NOTICE, body=settings.text_brain_rating_thanks,
        inbound_trace_id=trace_id, clock=clock,
    )
