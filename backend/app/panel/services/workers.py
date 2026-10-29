"""Registro de lacos de fundo do painel (spec 4.1; task PN-T06, desvio 4).

Separado de `app.services.workers` (PF). Cada servico do painel com laco expoe
`LOOP: Loop`; `discover_loops()` os encontra em `app.panel.services.*`. Cada ciclo
roda sob uma trava consultiva do Postgres (`pg_try_advisory_lock`), de modo que dois
processos (ex.: sobreposicao de deploy) nunca executam o mesmo laco ao mesmo tempo.
Uma falha nunca derruba o laco: loga so a classe do erro.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import pkgutil
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]
LoopRun = Callable[[Any], Awaitable[None]]

_TRY_LOCK = "SELECT pg_try_advisory_lock($1)"
_UNLOCK = "SELECT pg_advisory_unlock($1)"
_SERVICES_PACKAGE = "app.panel.services"
_SELF_MODULE = "workers"


@dataclass(frozen=True)
class Loop:
    """Um laco periodico: `run(app)` e chamado a cada `interval_seconds`."""

    name: str
    interval_seconds: float
    run: LoopRun

    def __post_init__(self) -> None:
        if not self.name or self.interval_seconds <= 0:
            raise ValueError("laco exige nome e intervalo positivo")

    @property
    def lock_key(self) -> int:
        """Chave estavel (32 bits) da trava consultiva, derivada do nome."""
        return zlib.crc32(f"jarvis-panel-loop:{self.name}".encode()) & 0x7FFFFFFF


class LoopRegistry:
    """Colecao de lacos; nomes unicos."""

    def __init__(self) -> None:
        self._loops: dict[str, Loop] = {}

    def register(self, loop: Loop) -> None:
        if loop.name in self._loops:
            raise ValueError(f"laco duplicado: {loop.name}")
        self._loops[loop.name] = loop

    @property
    def loops(self) -> tuple[Loop, ...]:
        return tuple(self._loops.values())

    def __len__(self) -> int:
        return len(self._loops)


def discover_loops() -> tuple[Loop, ...]:
    """Importa `app.panel.services.*` e coleta o `LOOP` de quem o expoe (ordem por nome)."""
    package = importlib.import_module(_SERVICES_PACKAGE)
    found: list[Loop] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name == _SELF_MODULE:
            continue
        module = importlib.import_module(f"{_SERVICES_PACKAGE}.{info.name}")
        loop = getattr(module, "LOOP", None)
        if isinstance(loop, Loop):
            found.append(loop)
    return tuple(found)


class PanelWorkers:
    """Executa os lacos registrados; `start(app)` cria as tarefas, `stop()` as encerra."""

    def __init__(
        self,
        pool: Any,
        registry: LoopRegistry,
        *,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._sleep = sleep
        self._tasks: list[asyncio.Task[None]] = []

    async def run_once(self, loop: Loop, app: Any) -> bool:
        """Um ciclo sob a trava consultiva. False se outro processo ja tem a trava."""
        async with self._pool.acquire() as conn:
            if not await conn.fetchval(_TRY_LOCK, loop.lock_key):
                return False
            try:
                await loop.run(app)
            finally:
                await conn.fetchval(_UNLOCK, loop.lock_key)
        return True

    async def _guarded_cycle(self, loop: Loop, app: Any) -> None:
        try:
            await self.run_once(loop, app)
        except Exception as exc:  # noqa: BLE001 - o laco nunca morre por uma falha
            logger.error(
                "panel_loop_failed", extra={"loop": loop.name, "error": type(exc).__name__}
            )

    async def _forever(self, loop: Loop, app: Any) -> None:
        while True:
            await self._guarded_cycle(loop, app)
            await self._sleep(loop.interval_seconds)

    async def start(self, app: Any = None) -> None:
        """Cria uma tarefa por laco (idempotente)."""
        if self._tasks:
            return
        for loop in self._registry.loops:
            self._tasks.append(
                asyncio.create_task(self._forever(loop, app), name=f"jarvis-panel-{loop.name}")
            )
        if app is not None:
            app.state.panel_workers = self

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def build_workers(pool: Any, loops: tuple[Loop, ...] | None = None) -> PanelWorkers:
    """Monta `PanelWorkers` com os lacos descobertos (ou os informados, nos testes)."""
    registry = LoopRegistry()
    for loop in discover_loops() if loops is None else loops:
        registry.register(loop)
    return PanelWorkers(pool, registry)
