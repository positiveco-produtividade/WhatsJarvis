"""Ponto de extensao do cerebro (`answering`, spec 4.3.2).

Um texto `allowed` de usuario `active` fecha a transacao 1 com `reply_kind='pending'`; depois,
FORA de transacao, o `AnswerProvider` e chamado (pode demorar: FAQ + IA no futuro) e uma
transacao 2 curta grava a saida em `chat.outbound_messages` e troca o `reply_kind` do rastro.
Quem implementar o cerebro troca o provedor; o pipeline nao muda. A politica de resposta
(pausa, marca) e consultada no despacho (outbox.dispatch), nao aqui.

Falha do provedor nao perde o rastro: ele continua `pending` e a recuperacao (PF-30) o marca
`abandoned`. Nunca registra telefone nem texto da mensagem em log.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import InvalidValue
from app.core.values import OutboundKind, ReplyKind, rate_down_payload, rate_up_payload
from app.services.outbox import enqueue, parse_kind
from app.services.replies import text_for

logger = logging.getLogger(__name__)

RATE_UP_TITLE = "👍 Útil"
RATE_DOWN_TITLE = "👎 Não útil"

_SET_REPLY_KIND = (
    "UPDATE chat.message_trace SET reply_kind = $2::text, "
    "ticket_number = coalesce($4::text, ticket_number) "
    "WHERE id = $1::bigint AND reply_kind = $3::text RETURNING id"
)


@dataclass(frozen=True)
class AnswerContext:
    """Tudo que o cerebro precisa saber de quem perguntou e do que foi perguntado."""

    user_id: int
    profile: str
    ufs: list[str]
    channels: list[str]
    brands: list[str]
    text: str
    inbound_wa_message_id: str
    received_at: datetime
    trace_id: int | None = None


@dataclass(frozen=True)
class AppliedEffects:
    """O que os efeitos gravaram na transacao 2 (ticket e se a saida leva os botoes de nota)."""

    ticket_id: int | None = None
    ticket_number: str | None = None
    rate: bool = False


class AnswerEffects(Protocol):
    """Efeitos de banco do cerebro; rodam DENTRO da transacao 2, antes de enfileirar."""

    async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects: ...


@dataclass(frozen=True)
class Answer:
    """`kind` precisa existir no CHECK de chat.outbound_messages (e em ReplyKind).

    `parts` vazio vale `(text,)`; com varias partes saem N mensagens, na ordem, e os botoes de
    avaliacao (se `effects` pedir `rate`) vao so na ultima.
    """

    kind: str
    text: str
    parts: tuple[str, ...] = ()
    effects: AnswerEffects | None = None


class AnswerProvider(Protocol):
    async def answer(self, ctx: AnswerContext) -> Answer: ...


@dataclass(frozen=True)
class AnswerRequest:
    """Pedido de resposta devolvido pelo pipeline (`MessageOutcome.answer_request`)."""

    trace_id: int
    phone_e164: str
    context: AnswerContext


@dataclass(frozen=True)
class _Recipient:
    id: int
    phone_e164: str


class UnderConstructionProvider:
    """Provedor padrao: sempre o texto fixo 'em construcao' (sem modelo nem busca)."""

    def __init__(self, settings: Settings) -> None:
        self._text = text_for(ReplyKind.UNDER_CONSTRUCTION, settings) or ""

    async def answer(self, ctx: AnswerContext) -> Answer:
        return Answer(kind=ReplyKind.UNDER_CONSTRUCTION.value, text=self._text)


async def _call_provider(provider: AnswerProvider, ctx: AnswerContext) -> Answer | None:
    try:
        return await provider.answer(ctx)
    except Exception as exc:  # o cerebro e plugavel: qualquer falha dele vira rastro pendente
        logger.error("answer_provider_failed error=%s", type(exc).__name__)
        return None


def _rate_buttons(ticket_id: int | None) -> list[dict[str, str]]:
    if ticket_id is None:
        raise InvalidValue("ticket_id", "avaliacao exige um ticket")
    return [
        {"id": rate_up_payload(ticket_id), "title": RATE_UP_TITLE},
        {"id": rate_down_payload(ticket_id), "title": RATE_DOWN_TITLE},
    ]


async def _enqueue_parts(
    conn: Any,
    recipient: _Recipient,
    kind: OutboundKind,
    parts: tuple[str, ...],
    applied: AppliedEffects,
    trace_id: int,
    clock: Clock | None,
) -> tuple[int, ...]:
    ids: list[int] = []
    last = len(parts) - 1
    for index, body in enumerate(parts):
        buttons = _rate_buttons(applied.ticket_id) if applied.rate and index == last else None
        # Partes sem botao saem como texto: o kind com botoes exige reply_buttons no outbox.
        part_kind = (
            OutboundKind.BRAIN_ANSWER
            if kind is OutboundKind.BRAIN_ANSWER_RATE and buttons is None
            else kind
        )
        ids.append(
            await enqueue(
                conn,
                recipient,
                part_kind,
                body=body,
                inbound_trace_id=trace_id,
                reply_buttons=buttons,
                clock=clock,
            )
        )
    return tuple(ids)


async def answer_and_enqueue(
    pool: Any,
    request: AnswerRequest,
    provider: AnswerProvider,
    *,
    clock: Clock | None = None,
) -> tuple[int, ...]:
    """Chama o provedor (sem transacao) e grava as saidas; devolve os ids em ordem.

    Devolve `()` se o provedor falhar (rastro segue `pending`, sem saida). Um `kind`
    invalido levanta `InvalidValue` antes de gravar qualquer coisa; rastro que nao esta mais
    `pending` levanta `InvalidValue` e desfaz a transacao inteira (efeitos e saidas).
    """
    ctx = request.context
    if ctx.trace_id is None:
        ctx = dataclasses.replace(ctx, trace_id=request.trace_id)
    answer = await _call_provider(provider, ctx)
    if answer is None:
        return ()
    kind = parse_kind(answer.kind)
    if kind.value not in {k.value for k in ReplyKind}:
        raise InvalidValue("kind", "tipo de resposta sem reply_kind correspondente")
    parts = answer.parts or (answer.text,)
    recipient = _Recipient(id=ctx.user_id, phone_e164=request.phone_e164)
    async with pool.acquire() as conn, conn.transaction():
        applied = AppliedEffects()
        if answer.effects is not None:
            applied = await answer.effects.apply(conn, request.trace_id, ctx.user_id)
        ids = await _enqueue_parts(conn, recipient, kind, parts, applied, request.trace_id, clock)
        updated = await conn.fetchval(
            _SET_REPLY_KIND,
            request.trace_id,
            kind.value,
            ReplyKind.PENDING.value,
            applied.ticket_number,
        )
        if updated is None:
            raise InvalidValue("trace_id", "rastro inexistente ou sem resposta pendente")
    return ids
