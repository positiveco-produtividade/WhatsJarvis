"""Lacos de fundo (spec 4.4.7, 4.6): varredura de saidas/entregas vencidas, recuperacao e flush.

Um ciclo (`run_cycle`): recupera travados, despacha `outbound_messages` e `deliveries`
`pending` com `next_attempt_at <= agora` (o dispatch de entregas nao filtra a data) e,
a cada `flush_interval` (30 s), grava os contadores de seguranca. A recuperacao tambem
roda na subida (`start`). Uma falha nunca derruba o laco: loga so o nome da classe.
Relogio, intervalo e `sleep` sao injetaveis; a ligacao no lifespan e do PF-38.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.core.values import OutboundStatus
from app.services import deliveries, outbox
from app.services.channel import Channel
from app.services.counters import SecurityCounters
from app.services.flags import Flags

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 15.0
FLUSH_INTERVAL_SECONDS = 30.0
DELIVERY_SENDING_TIMEOUT = timedelta(minutes=5)
BATCH_LIMIT = 50

_DUE_OUTBOUND = (
    "SELECT id FROM chat.outbound_messages WHERE status = 'pending' AND next_attempt_at <= $1 "
    "ORDER BY next_attempt_at, id LIMIT $2"
)
_DUE_DELIVERIES = (
    "SELECT id FROM ops.deliveries WHERE status = 'pending' AND next_attempt_at <= $1 "
    "ORDER BY next_attempt_at, id LIMIT $2"
)
_RECOVER_DELIVERIES = (
    "UPDATE ops.deliveries SET status = 'pending', claimed_at = NULL "
    "WHERE status = 'sending' AND claimed_at < $1"
)

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class CycleResult:
    """Resumo de um ciclo (para testes e diagnostico)."""

    outbound: int
    deliveries: int
    flushed: int


class Workers:
    """Laco de fundo unico; `start(app)` cria a tarefa e `stop()` encerra limpo."""

    def __init__(
        self,
        pool: Any,
        *,
        channel: Channel | None,
        flags: Flags,
        counters: SecurityCounters,
        settings: Settings | None = None,
        clock: Clock | None = None,
        interval: float = SWEEP_INTERVAL_SECONDS,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        batch_limit: int = BATCH_LIMIT,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._channel = channel
        self._flags = flags
        self._counters = counters
        self._settings = settings or get_settings()
        self._clock: Clock = clock or SystemClock()
        self._interval = interval
        self._flush_interval = timedelta(seconds=flush_interval)
        self._batch_limit = batch_limit
        self._sleep = sleep
        self._last_flush: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    # ---- recuperacao -------------------------------------------------------

    async def recover(self) -> None:
        """`outbox.recover_stuck` + entregas `sending` > 5 min voltam a `pending`."""
        now = self._clock.now()
        await self._guard("recover_outbox", self._recover_outbox(now))
        await self._guard("recover_deliveries", self._recover_deliveries(now))

    async def _recover_outbox(self, now: datetime) -> None:
        async with self._pool.acquire() as conn:
            await outbox.recover_stuck(conn, now)

    async def _recover_deliveries(self, now: datetime) -> None:
        await self._pool.execute(_RECOVER_DELIVERIES, now - DELIVERY_SENDING_TIMEOUT)

    # ---- varredura ---------------------------------------------------------

    async def _dispatch_outbound(self, outbound_id: int) -> None:
        if self._channel is None:
            return
        try:
            status = await outbox.dispatch(
                self._pool, outbound_id, channel=self._channel, clock=self._clock
            )
            failed = status is OutboundStatus.FAILED
        except InvalidValue:
            failed = True
        if failed:
            async with self._pool.acquire() as conn:
                await outbox.check_send_failures(
                    conn,
                    outbound_id,
                    settings=self._settings,
                    flags=self._flags,
                    clock=self._clock,
                )

    async def _dispatch_delivery(self, delivery_id: int) -> None:
        await deliveries.dispatch(
            self._pool,
            delivery_id,
            flags=self._flags,
            settings=self._settings,
            clock=self._clock,
        )

    async def _sweep(
        self,
        query: str,
        now: datetime,
        handler: Callable[[int], Awaitable[None]],
        name: str,
    ) -> int:
        rows = await self._pool.fetch(query, now, self._batch_limit)
        for row in rows:
            await self._guard(name, handler(row["id"]))
        return len(rows)

    async def _guard(self, name: str, work: Awaitable[Any]) -> Any:
        """Executa `work`; qualquer Exception vira log (so a classe) e devolve None."""
        try:
            return await work
        except Exception as exc:  # noqa: BLE001 - o laco nunca morre por uma falha
            logger.error("worker_step_failed", extra={"step": name, "error": type(exc).__name__})
            return None

    async def _flush_if_due(self, now: datetime, *, force: bool = False) -> int:
        last = self._last_flush
        if not force and last is not None and now - last < self._flush_interval:
            return 0
        self._last_flush = now
        async with self._pool.acquire() as conn:
            return int(await self._counters.flush(conn))

    async def run_cycle(self) -> CycleResult:
        """Um ciclo completo; cada etapa e isolada das demais."""
        await self.recover()
        now = self._clock.now()
        outbound = await self._guard(
            "sweep_outbound",
            self._sweep(_DUE_OUTBOUND, now, self._dispatch_outbound, "dispatch_outbound"),
        )
        due = await self._guard(
            "sweep_deliveries",
            self._sweep(_DUE_DELIVERIES, now, self._dispatch_delivery, "dispatch_delivery"),
        )
        flushed = await self._guard("flush_counters", self._flush_if_due(now))
        return CycleResult(outbound or 0, due or 0, flushed or 0)

    # ---- ciclo de vida -----------------------------------------------------

    async def _loop(self) -> None:
        while True:
            await self._guard("cycle", self.run_cycle())
            await self._sleep(self._interval)

    async def start(self, app: Any = None) -> None:
        """Recupera na subida e cria a tarefa do laco (idempotente)."""
        if self._task is not None and not self._task.done():
            return
        await self.recover()
        self._task = asyncio.create_task(self._loop(), name="jarvis-workers")
        if app is not None:
            app.state.workers = self

    async def stop(self) -> None:
        """Cancela o laco, espera terminar e grava os contadores que sobraram."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._guard("final_flush", self._flush_if_due(self._clock.now(), force=True))


def from_app(app: Any) -> Workers:
    """Monta `Workers` com o que o lifespan guardou em `app.state` (pool, settings, ...)."""
    state = app.state
    return Workers(
        state.pool,
        channel=getattr(state, "channel", None),
        flags=state.flags,
        counters=state.counters,
        settings=state.settings,
    )
