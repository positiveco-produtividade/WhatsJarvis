"""Borda HTTP do webhook do WAHA do Jarvis (spec canal-waha.1, WA-4, WA-5, WA-8, WA-9; WA-T10).

Mesma ordem obrigatoria do webhook da Meta: limite por IP -> tamanho -> assinatura (HMAC
SHA-512 do corpo bruto) -> JSON -> sessao/destinatario -> delega. Nada toca o banco antes da
assinatura valida. Cada requisicao do WAHA traz um evento so.

Dependencias em `app.state` (as mesmas do webhook da Meta, mais duas opcionais):
- `settings`, `counters`, `processor`, `webhook_limiter` (opcional), `outbound_dispatcher`
  (opcional);
- `lid_resolver` (opcional): resolve `@lid` para telefone; sem ele, o lid segue no lugar do
  BSUID e o remetente aparece em "Pedindo acesso" como "Numero oculto";
- `session_monitor` (opcional, WA-T16): recebe o estado da sessao (`session.status`).

Reentrega: o WAHA repete o evento quando a resposta nao e 2xx; a idempotencia por id da
mensagem absorve (WA-9). Falha transitoria de banco devolve 503 para o WAHA tentar de novo.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse

from app.api.webhook import (
    PayloadProcessor,
    _limiter,
    _read_capped,
    _run_messages,
    _run_statuses,
    _schedule_dispatch,
    _TooLarge,
)
from app.config import Settings
from app.core.security import verify_waha_hmac
from app.services import waha_inbound
from app.services.counters import SecurityCounters
from app.services.inbound import InboundMessage, Sender

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_PATH = "/webhook/waha"
HMAC_HEADER = "x-webhook-hmac"
HMAC_ALGORITHM_HEADER = "x-webhook-hmac-algorithm"
LID_SUFFIX = "@lid"


class LidLookup(Protocol):
    async def resolve(self, lid: str) -> str | None: ...


class SessionSink(Protocol):
    async def on_session_event(self, state: str) -> None: ...


async def _with_resolved_lid(request: Request, msg: InboundMessage) -> InboundMessage:
    """Troca `@lid` pelo telefone quando o WAHA souber; guarda o lid junto (vira o BSUID do
    usuario na identidade, para os proximos eventos so com o lid). Sem telefone, segue com o
    lid: o remetente nunca e tratado como autorizado so por ele."""
    lid = msg.sender.bsuid
    if msg.sender.wa_id is not None or lid is None or not lid.endswith(LID_SUFFIX):
        return msg
    resolver: LidLookup | None = getattr(request.app.state, "lid_resolver", None)
    phone = await resolver.resolve(lid) if resolver is not None else None
    if phone is None:
        logger.info("waha_lid_unresolved")
        return msg
    return InboundMessage(
        wa_message_id=msg.wa_message_id,
        sender=Sender(wa_id=phone, bsuid=lid),
        kind=msg.kind,
        timestamp=msg.timestamp,
        text=msg.text,
        text_length=msg.text_length,
        mime_type=msg.mime_type,
        button=msg.button,
        raw_type=msg.raw_type,
    )


@router.post(WEBHOOK_PATH)
async def receive_waha_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings: Settings = request.app.state.settings
    counters: SecurityCounters = request.app.state.counters

    # 0. limite por IP
    wait = _limiter(request).retry_after(request)
    if wait:
        return Response(status_code=429, headers={"Retry-After": str(wait)})

    # 1. tamanho (antes da assinatura)
    try:
        raw = await _read_capped(request, settings.max_webhook_payload_bytes)
    except _TooLarge:
        counters.incr("oversize_payload")
        logger.warning("waha_webhook_payload_too_large")
        return Response(status_code=413)

    # 2. assinatura sobre os bytes brutos; sem parse, sem banco
    if not verify_waha_hmac(
        raw,
        request.headers.get(HMAC_HEADER),
        request.headers.get(HMAC_ALGORITHM_HEADER),
        settings.jarvis_waha_hmac_key,
    ):
        counters.incr("invalid_signature")
        logger.warning("waha_webhook_signature_invalid")
        return Response(status_code=403)

    # 3. JSON
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        counters.incr("invalid_json")
        return Response(status_code=400)

    # 4. sessao/destinatario e traducao para o dominio
    event = waha_inbound.parse_event(
        body,
        session=settings.jarvis_waha_session,
        jarvis_number=settings.jarvis_whatsapp_number or None,
    )
    logger.info("waha_webhook_received", extra={"event_kind": type(event).__name__})

    if isinstance(event, waha_inbound.WahaIgnored):
        if event.reason == waha_inbound.WRONG_RECIPIENT:
            counters.incr("wrong_recipient")
        logger.info("waha_event_ignored", extra={"reason": event.reason})
        return JSONResponse({"status": "ok"})

    if isinstance(event, waha_inbound.WahaSession):
        sink: SessionSink | None = getattr(request.app.state, "session_monitor", None)
        if sink is not None:
            await sink.on_session_event(event.state)
        logger.info("channel_session_event", extra={"state": event.state})
        return JSONResponse({"status": "ok"})

    # 5-6. status ou mensagem, como no webhook da Meta
    processor: PayloadProcessor = request.app.state.processor
    if isinstance(event, waha_inbound.WahaStatus):
        outcomes, transient = await _run_statuses(processor, (event.status,))
    else:
        message = await _with_resolved_lid(request, event.message)
        outcomes, transient = await _run_messages(processor, (message,))

    # 7. despacho de saida depois da resposta
    _schedule_dispatch(request, background, outcomes)

    # 8. transitorio = 503 (o WAHA reentrega; a idempotencia absorve)
    if transient:
        return JSONResponse({"status": "unavailable"}, status_code=503, background=background)
    return JSONResponse({"status": "ok"}, background=background)
