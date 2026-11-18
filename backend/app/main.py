"""Aplicacao FastAPI do Jarvis (spec porta-fechada.1, 4.3.1, 4.4.1 e 5).

Casca (pool, TrustedHost, cabecalhos de seguranca, request_id, erros sem detalhe,
GET /health) mais a ligacao dos servicos (PF-38): router do webhook, `Pipeline` como
processador, despacho de saidas e entregas depois da resposta, politica de resposta
(R4), laco de fundo e os clientes httpx, todos abertos e fechados no lifespan.

Subida (a partir de backend/): `uvicorn app.main:create_app --factory`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.webhook import PayloadProcessor
from app.config import Settings, get_settings
from app.core.clock import SystemClock
from app.core.errors import InvalidValue
from app.core.log_redaction import register_model_secrets
from app.core.logging_json import reset_request_id, set_request_id, setup_logging
from app.core.values import MessageOutcome, OutboundStatus
from app.db import migrate, roles
from app.db.connection import create_pool
from app.services import answering, deliveries, outbox
from app.services.brain.faq_source import FaqSource
from app.services.brain.llm import LlmClient
from app.services.brain.meter import BrainMeter
from app.services.brain.provider import BrainProvider
from app.services.channel import Channel
from app.services.channel_factory import (
    OwnedChannel,
    build_channel,
    build_lid_resolver,
    webhook_router,
)
from app.services.counters import SecurityCounters
from app.services.faq_reader import FaqReader
from app.services.flags import Flags
from app.services.inbound import InboundMessage, InboundStatus
from app.services.pipeline import Pipeline
from app.services.reply_policy import AllowAllPolicy, FlagsReplyPolicy
from app.services.session_monitor import SessionMonitor
from app.services.waha_channel import WahaChannel
from app.services.workers import Workers, from_app

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = ["*.positiveco.com.br", "localhost", "127.0.0.1"]
# So em desenvolvimento (PRODUCTION=false): tunel do VS Code para a Meta alcancar o webhook local.
DEV_TUNNEL_HOSTS = ["*.devtunnels.ms"]
HEALTH_TIMEOUT_SECONDS = 5.0
REQUEST_ID_HEADER = "X-Request-ID"
HTTP_CLIENT_TIMEOUT_SECONDS = 15.0

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store",
}


def _error_response(status_code: int, code: str, headers: dict[str, str] | None = None) -> Response:
    """Corpo de erro fixo: sem detalhe, sem traceback, sem eco da entrada."""
    return JSONResponse({"error": code}, status_code=status_code, headers=headers)


def _apply_headers(response: Response, request_id: str) -> None:
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    response.headers[REQUEST_ID_HEADER] = request_id


async def _request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """request_id novo por requisicao (nunca aceito do cliente) e cabecalhos."""
    request_id = uuid.uuid4().hex
    token = set_request_id(request_id)
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled_error", extra={"path": request.url.path})
            response = _error_response(500, "internal_error")
        _apply_headers(response, request_id)
        return response
    finally:
        reset_request_id(token)


async def _http_error_handler(request: Request, exc: Exception) -> Response:
    status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    headers = dict(exc.headers) if isinstance(exc, StarletteHTTPException) and exc.headers else None
    return _error_response(status, "http_error", headers)


async def _validation_error_handler(request: Request, exc: Exception) -> Response:
    return _error_response(422, "invalid_request")


async def _check_database(pool: asyncpg.Pool | None) -> bool:
    """True se o banco responde e nao falta migration (nunca levanta)."""
    if pool is None:
        return False
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_SECONDS):
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                return await migrate.is_up_to_date(conn)
    except Exception as exc:
        # So o tipo: a mensagem de erro do driver pode trazer host/usuario do DSN.
        logger.warning("health_degraded", extra={"error_type": type(exc).__name__})
        return False


def _channel_state(request: Request) -> str | None:
    """Estado da sessao do WhatsApp (canal-waha.1, WA-19): `working`, `down` ou None."""
    monitor: SessionMonitor | None = getattr(request.app.state, "session_monitor", None)
    if monitor is None:
        return None
    return "working" if monitor.is_working() else "down"


async def health(request: Request) -> Response:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    body: dict[str, Any] = {}
    channel = _channel_state(request)
    if channel is not None:
        body["channel"] = channel  # informativo: o 503 continua so para banco fora
    if await _check_database(pool):
        return JSONResponse({"status": "ok", **body})
    return JSONResponse({"status": "degraded", **body}, status_code=503)


class OutboundDispatcher:
    """Despacho pos-resposta (4.4.1 passo 7): saidas pelo outbox, entregas pelo `deliveries`.

    Nunca levanta: a falha de uma linha vira log (so a classe) e as demais seguem.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        settings: Settings,
        flags: Flags,
        channel: Channel,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._flags = flags
        self._channel = channel
        self._http_client = http_client

    async def __call__(self, outbound_ids: tuple[int, ...], delivery_ids: tuple[int, ...]) -> None:
        for outbound_id in outbound_ids:
            await self._guard("dispatch_outbound", self._dispatch_outbound(outbound_id))
        for delivery_id in delivery_ids:
            await self._guard("dispatch_delivery", self._dispatch_delivery(delivery_id))

    async def _dispatch_outbound(self, outbound_id: int) -> None:
        try:
            status = await outbox.dispatch(
                self._pool,
                outbound_id,
                channel=self._channel,
                reply_only=outbox.reply_only_for(self._settings, self._channel),
            )
            failed = status is OutboundStatus.FAILED
        except InvalidValue:
            failed = True
        if failed:
            async with self._pool.acquire() as conn:
                await outbox.check_send_failures(
                    conn, outbound_id, settings=self._settings, flags=self._flags
                )

    async def _dispatch_delivery(self, delivery_id: int) -> None:
        await deliveries.dispatch(
            self._pool,
            delivery_id,
            flags=self._flags,
            settings=self._settings,
            email_client=self._http_client,
            waha_client=self._http_client,
        )

    async def _guard(self, step: str, work: Awaitable[None]) -> None:
        try:
            await work
        except Exception as exc:  # noqa: BLE001 - o despacho de fundo nunca propaga
            logger.error("dispatch_step_failed", extra={"step": step, "error": type(exc).__name__})


class AppProcessor:
    """`PayloadProcessor` do app: `Pipeline` + resposta do cerebro fora da requisicao.

    O `answer_request` do pipeline nao viaja pelo webhook (que so repassa ids): vira uma
    tarefa de fundo que chama o provedor, enfileira e despacha a saida. O provedor nunca
    atrasa o 200; o rastro fica `pending` se a tarefa falhar (a recuperacao o fecha).
    """

    def __init__(
        self,
        pipeline: PayloadProcessor,
        pool: asyncpg.Pool,
        provider: answering.AnswerProvider,
        dispatcher: OutboundDispatcher,
        *,
        rating: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._pool = pool
        self._provider = provider
        self._dispatcher = dispatcher
        self._rating = rating  # sem botoes no canal (WAHA): resposta sem avaliacao (WA-14)
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle_message(self, msg: InboundMessage) -> MessageOutcome:
        outcome = await self._pipeline.handle_message(msg)
        request = outcome.answer_request
        if not isinstance(request, answering.AnswerRequest):
            return outcome
        task = asyncio.create_task(self._answer(request), name="jarvis-answer")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return dataclasses.replace(outcome, answer_request=None)

    async def handle_status(self, st: InboundStatus) -> MessageOutcome | None:
        return await self._pipeline.handle_status(st)

    async def _answer(self, request: answering.AnswerRequest) -> None:
        try:
            outbound_ids = await answering.answer_and_enqueue(
                self._pool, request, self._provider, rating=self._rating
            )
            if outbound_ids:
                await self._dispatcher(outbound_ids, ())
        except Exception as exc:  # noqa: BLE001 - o rastro fica `pending`
            logger.error("answer_failed", extra={"error": type(exc).__name__})

    async def drain(self) -> None:
        """Espera as respostas em andamento (encerramento limpo)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _build_provider(
    pool: asyncpg.Pool, settings: Settings, flags: Flags
) -> answering.AnswerProvider:
    """`UnderConstructionProvider` por padrao; `BrainProvider` so com BRAIN_ENABLED (CB-14).

    A config ja recusa BRAIN_ENABLED sem chave; a checagem aqui cobre settings injetadas e
    devolve o "em construcao" em vez de subir um cerebro sem IA por engano.
    """
    if not settings.brain_enabled or not settings.gemini_api_key.get_secret_value().strip():
        return answering.UnderConstructionProvider(settings)
    clock = SystemClock()
    return BrainProvider(
        FaqSource(FaqReader(pool, clock)),
        flags,
        settings,
        clock,
        llm=LlmClient.from_settings(settings),
        meter=BrainMeter(settings, clock),
        db=pool,
    )


def _install_services(
    app: FastAPI,
    pool: asyncpg.Pool,
    settings: Settings,
    channel: Channel,
    http_client: httpx.AsyncClient,
) -> AppProcessor:
    """Monta flags, despacho e processador em `app.state` e instala a politica (R4)."""
    flags = Flags(pool, team_group_initial=settings.team_group_enabled)
    dispatcher = OutboundDispatcher(pool, settings, flags, channel, http_client)
    processor = AppProcessor(
        Pipeline(pool, settings, flags),
        pool,
        _build_provider(pool, settings, flags),
        dispatcher,
        rating=channel.capabilities.buttons,
    )
    app.state.pool = pool
    app.state.flags = flags
    app.state.channel = channel
    app.state.processor = processor
    app.state.outbound_dispatcher = dispatcher
    outbox.set_reply_policy(FlagsReplyPolicy(flags))
    return processor


async def _start_panel_workers(app: FastAPI, pool: asyncpg.Pool, settings: Settings) -> Any:
    """Lacos do painel (registro proprio); so com PANEL_ENABLED. Import tardio de app.panel."""
    if not settings.panel_enabled:
        return None
    from app.panel.services.workers import build_workers

    panel_workers = build_workers(pool)
    await panel_workers.start(app)
    return panel_workers


def _mount_panel(app: FastAPI, settings: Settings, *, from_env: bool) -> None:
    """Monta o painel so com PANEL_ENABLED.

    Subida real (`settings` vindas do ambiente): fail-fast da config do painel em producao.
    Settings injetadas (testes) montam sem essa checagem, para nao quebrar o app de PF.
    """
    if not settings.panel_enabled:
        return
    if from_env:
        settings.validate_panel_config()
    from app.panel.routes import mount_panel

    mount_panel(app)


def _start_session_monitor(
    app: FastAPI,
    pool: asyncpg.Pool,
    settings: Settings,
    channel: Channel,
    http_client: httpx.AsyncClient,
) -> SessionMonitor | None:
    """Monitor da sessao do WAHA (canal-waha.1, WA-19); so com o canal do WAHA."""
    if settings.channel_provider != "waha":
        return None
    monitor = SessionMonitor(pool, settings, http_client, flags=app.state.flags)
    app.state.session_monitor = monitor
    if isinstance(channel, WahaChannel):
        channel.attach_monitor(monitor.is_working, monitor.on_restriction)
    return monitor


def _lifespan(
    settings: Settings,
    channel: Channel | None,
    http_client: httpx.AsyncClient | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO))
        pool = await create_pool(settings.database_url.get_secret_value())
        if settings.production:
            # PA-12 (D2): em producao o app nao sobe com o papel dono nem superusuario.
            try:
                async with pool.acquire() as conn:
                    await roles.assert_app_role(conn)
            except BaseException:
                await pool.close()
                raise
        owned_channel: OwnedChannel | None = None
        owned_http: httpx.AsyncClient | None = None
        if channel is None:
            owned_channel = build_channel(settings)
        if http_client is None:
            owned_http = httpx.AsyncClient(timeout=HTTP_CLIENT_TIMEOUT_SECONDS)
        active_channel: Channel = owned_channel or channel  # type: ignore[assignment]
        active_http = owned_http or http_client
        assert active_http is not None
        processor = _install_services(app, pool, settings, active_channel, active_http)
        # Numero oculto (@lid) do WAHA; com a Meta e None (canal-waha.1, WA-8).
        app.state.lid_resolver = build_lid_resolver(settings, active_http)
        monitor = _start_session_monitor(app, pool, settings, active_channel, active_http)
        if monitor is not None:
            await monitor.start()
        workers: Workers = from_app(app)
        await workers.start(app)
        panel_workers = await _start_panel_workers(app, pool, settings)
        logger.info("app_started")
        try:
            yield
        finally:
            if panel_workers is not None:
                await panel_workers.stop()
            await workers.stop()
            if monitor is not None:
                await monitor.stop()
            await processor.drain()
            outbox.set_reply_policy(AllowAllPolicy())
            if owned_channel is not None:
                await owned_channel.aclose()
            if owned_http is not None:
                await owned_http.aclose()
            await pool.close()
            logger.info("app_stopped")

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    channel: Channel | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Monta a aplicacao. Os argumentos explicitos servem aos testes; senao vem do ambiente.

    Sem `channel` o lifespan cria o canal do WAHA (a Meta so liga nos testes); sem
    `http_client`, o cliente httpx de e-mail/WAHA. O que for injetado pertence ao chamador
    (nao e fechado aqui).
    """
    cfg = settings if settings is not None else get_settings()
    register_model_secrets(cfg)
    counters = SecurityCounters()
    app = FastAPI(
        title="Jarvis",
        lifespan=_lifespan(cfg, channel, http_client),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        debug=False,
    )
    app.state.settings = cfg
    app.state.counters = counters
    # TrustedHost entra primeiro (fica por dentro); o contexto de requisicao, por
    # fora, para que ate a resposta 400 de host invalido leve cabecalhos e request_id.
    hosts = ALLOWED_HOSTS if cfg.production else ALLOWED_HOSTS + DEV_TUNNEL_HOSTS
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.middleware("http")(_request_context)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_api_route("/health", health, methods=["GET"], include_in_schema=False)
    # So o webhook do canal escolhido e montado; o do outro responde 404 (canal-waha.1, WA-2).
    app.include_router(webhook_router(cfg))
    _mount_panel(app, cfg, from_env=settings is None)
    return app
