"""Borda HTTP do webhook da WhatsApp Cloud API (spec 4.3.1 e 4.4.1 passos 0-5, 7-8).

Ordem obrigatoria (AC-31): limite por IP -> tamanho -> assinatura -> JSON ->
destinatario -> delega. So HTTP: a logica de dominio chega por `PayloadProcessor`,
obtido de `request.app.state`; este modulo nao importa o pipeline.

Dependencias em `app.state`:
- `settings` (Settings), `counters` (SecurityCounters), `processor` (PayloadProcessor);
- `webhook_limiter` (WebhookRateLimiter, opcional: se ausente, cria-se um a partir de `settings`);
- `outbound_dispatcher` (opcional): corrotina `(outbound_ids, delivery_ids)` agendada em
  `BackgroundTasks`, depois da resposta, com os ids devolvidos pelo processador.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import Settings
from app.core.errors import TransientDbError
from app.core.security import constant_time_equals, verify_hub_signature
from app.core.values import MessageOutcome
from app.services.counters import SecurityCounters
from app.services.inbound import InboundMessage, InboundStatus, parse_payload, split_by_recipient
from app.services.rate_limit import WebhookRateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()

SIGNATURE_HEADER = "x-hub-signature-256"

OutboundDispatcher = Callable[[tuple[int, ...], tuple[int, ...]], Awaitable[None]]


class PayloadProcessor(Protocol):
    """Logica de dominio injetada (pipeline). Cada chamada e independente."""

    async def handle_message(self, msg: InboundMessage) -> MessageOutcome: ...

    async def handle_status(self, st: InboundStatus) -> MessageOutcome | None: ...


class _TooLarge(Exception):
    """Corpo acima do teto (interno a este modulo)."""


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        return None
    return int(raw)


async def _read_capped(request: Request, limit: int) -> bytes:
    """Le o corpo em stream e aborta assim que passar do teto (AC-4)."""
    declared = _declared_length(request)
    if declared is not None and declared > limit:
        raise _TooLarge
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _limiter(request: Request) -> WebhookRateLimiter:
    state = request.app.state
    limiter: WebhookRateLimiter | None = getattr(state, "webhook_limiter", None)
    if limiter is None:
        settings: Settings = state.settings
        limiter = WebhookRateLimiter(settings.rl_webhook_max, settings.rl_webhook_window)
        state.webhook_limiter = limiter
    return limiter


@router.get("/webhook/whatsapp")
async def verify_webhook(
    request: Request,
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    """Handshake da Meta: ecoa o challenge so com modo e token corretos (AC-1)."""
    settings: Settings = request.app.state.settings
    counters: SecurityCounters = request.app.state.counters
    if (
        mode == "subscribe"
        and challenge is not None
        and constant_time_equals(token, settings.whatsapp_verify_token)
    ):
        return PlainTextResponse(challenge)
    counters.incr("bad_verify_token")
    return Response(status_code=403)


async def _run_statuses(
    processor: PayloadProcessor, statuses: tuple[InboundStatus, ...]
) -> tuple[list[MessageOutcome], bool]:
    outcomes: list[MessageOutcome] = []
    transient = False
    for st in statuses:
        try:
            outcome = await processor.handle_status(st)
        except TransientDbError:
            transient = True
            continue
        except Exception:
            logger.exception("webhook_processing_error", extra={"item": "status"})
            continue
        if outcome is not None:
            outcomes.append(outcome)
            transient = transient or outcome.transient_failure
    return outcomes, transient


async def _run_messages(
    processor: PayloadProcessor, messages: tuple[InboundMessage, ...]
) -> tuple[list[MessageOutcome], bool]:
    outcomes: list[MessageOutcome] = []
    transient = False
    for msg in messages:
        try:
            outcome = await processor.handle_message(msg)
        except TransientDbError:
            transient = True
            continue
        except Exception:
            logger.exception("webhook_processing_error", extra={"item": "message"})
            continue
        outcomes.append(outcome)
        transient = transient or outcome.transient_failure
    return outcomes, transient


def _schedule_dispatch(
    request: Request, background: BackgroundTasks, outcomes: list[MessageOutcome]
) -> None:
    dispatcher: OutboundDispatcher | None = getattr(request.app.state, "outbound_dispatcher", None)
    outbound = tuple(i for o in outcomes for i in o.outbound_ids)
    deliveries = tuple(i for o in outcomes for i in o.delivery_ids)
    if dispatcher is not None and (outbound or deliveries):
        background.add_task(dispatcher, outbound, deliveries)


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings: Settings = request.app.state.settings
    counters: SecurityCounters = request.app.state.counters
    processor: PayloadProcessor = request.app.state.processor

    # 0. limite por IP
    wait = _limiter(request).retry_after(request)
    if wait:
        return Response(status_code=429, headers={"Retry-After": str(wait)})

    # 1. tamanho (antes da assinatura)
    try:
        raw = await _read_capped(request, settings.max_webhook_payload_bytes)
    except _TooLarge:
        counters.incr("oversize_payload")
        logger.warning("webhook_payload_too_large")
        return Response(status_code=413)

    # 2. assinatura sobre os bytes brutos; sem parse, sem banco
    if not verify_hub_signature(
        raw, request.headers.get(SIGNATURE_HEADER), settings.whatsapp_app_secret
    ):
        counters.incr("invalid_signature")
        logger.warning("webhook_signature_invalid")
        return Response(status_code=403)

    # 3. JSON
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        counters.incr("invalid_json")
        return Response(status_code=400)
    parsed = parse_payload(body)

    # 4. destinatario: descarta o `value` inteiro de outro numero
    split = split_by_recipient(parsed, settings.whatsapp_phone_number_id)
    if split.wrong_recipient_count:
        counters.incr("wrong_recipient", split.wrong_recipient_count)
        logger.warning("webhook_wrong_recipient", extra={"count": split.wrong_recipient_count})
    accepted = split.accepted
    logger.info(
        "webhook_received",
        extra={"messages": len(accepted.messages), "statuses": len(accepted.statuses)},
    )

    # 5-6. statuses e mensagens, cada um em chamada independente
    status_outcomes, status_transient = await _run_statuses(processor, accepted.statuses)
    msg_outcomes, msg_transient = await _run_messages(processor, accepted.messages)

    # 7. despacho de saida depois da resposta
    _schedule_dispatch(request, background, status_outcomes + msg_outcomes)

    # 8. transitorio = 503 (a Meta reentrega; a idempotencia absorve)
    if status_transient or msg_transient:
        return JSONResponse({"status": "unavailable"}, status_code=503, background=background)
    return JSONResponse({"status": "ok"}, background=background)
