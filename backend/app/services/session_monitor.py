"""Monitor da sessao do WhatsApp do Jarvis no WAHA (spec canal-waha.1, WA-18, WA-19; WA-T16).

Fontes do estado: o evento `session.status` do webhook (`on_session_event`) e uma checagem
periodica (`GET /api/sessions/{sessao}`, a cada `CHECK_INTERVAL_SECONDS`). O estado vai para
`ops.system_state` (chave `channel_session`), que o painel le para a faixa "canal fora".

Alertas (fluxo de alertas existente: painel + e-mail):
- `channel_down` (critico) na transicao para fora de WORKING; nao repete enquanto a queda
  durar (dedup pela transicao, e por janela de seguranca);
- `channel_restricted` (alto) quando o envio falha por restricao do numero (463/475).

Em desenvolvimento (`PRODUCTION=false`) os alertas de canal ficam so no painel, sem e-mail:
sem WAHA instalado na maquina, a sessao sempre parece fora e o e-mail seria alarme falso.

Enquanto a sessao esta fora, `is_working()` e False e o `WahaChannel` devolve transitorio sem
chamar o WAHA. Estado desconhecido (logo apos subir) conta como no ar. Nunca levanta excecao
para quem chama; logs sem numero nem chave.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.services import alerts
from app.services.flags import Flags

logger = logging.getLogger(__name__)

WORKING = "WORKING"
UNREACHABLE = "UNREACHABLE"  # o proprio WAHA nao respondeu
STATE_KEY = "channel_session"
CHECK_INTERVAL_SECONDS = 60.0
HTTP_TIMEOUT = httpx.Timeout(5.0)
DOWN_DEDUP_WINDOW = timedelta(hours=6)
RESTRICTED_DEDUP_WINDOW = timedelta(hours=1)
DOWN_DEDUP_KEY = "channel_down"
RESTRICTED_DEDUP_KEY = "channel_restricted"

_UPSERT_STATE = (
    "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
    "VALUES ($1, $2::jsonb, $3, 'system') "
    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
    "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by"
)
_READ_STATE = "SELECT value::text FROM ops.system_state WHERE key = $1"


async def read_state(conn: Any) -> dict[str, Any] | None:
    """Estado gravado da sessao (`{"state": ..., "since": iso}`) ou None (nunca registrado)."""
    raw = await conn.fetchval(_READ_STATE, STATE_KEY)
    if raw is None:
        return None
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


def is_down(value: dict[str, Any] | None) -> bool:
    return value is not None and value.get("state") not in (None, WORKING)


class SessionMonitor:
    def __init__(
        self,
        pool: Any,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        flags: Flags | None = None,
        clock: Clock | None = None,
        interval: float = CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._client = client
        self._flags = flags
        self._clock = clock or SystemClock()
        self._interval = interval
        self._state: str | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ consulta

    @property
    def state(self) -> str | None:
        return self._state

    def is_working(self) -> bool:
        return self._state in (None, WORKING)

    # ------------------------------------------------------------------ entradas

    async def on_session_event(self, state: str) -> None:
        """Evento `session.status` do webhook."""
        await self._apply(state.strip().upper() or UNREACHABLE)

    async def check_once(self) -> str:
        """Consulta o WAHA do Jarvis e aplica o estado; devolve o estado aplicado."""
        state = await self._fetch_state()
        await self._apply(state)
        return state

    async def on_restriction(self) -> None:
        """Envio recusado por restricao do numero (463/475): alerta alto, com dedup de 1 h."""
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await alerts.raise_alert(
                    conn,
                    "channel_restricted",
                    "high",
                    "O WhatsApp restringiu o numero do Jarvis para contatos novos. "
                    "Reduza o ritmo; a restricao costuma sair sozinha.",
                    {"source": "waha"},
                    dedup_key=RESTRICTED_DEDUP_KEY,
                    dedup_window=RESTRICTED_DEDUP_WINDOW,
                    email=self._settings.production,
                    flags=self._flags,
                    settings=self._settings,
                    clock=self._clock,
                )
        except Exception as exc:  # noqa: BLE001 - alerta nunca derruba o envio
            logger.error("channel_alert_failed", extra={"error": type(exc).__name__})

    # ------------------------------------------------------------------ laco

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="jarvis-session-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 - o laco nunca morre
                logger.error("channel_check_failed", extra={"error": type(exc).__name__})
            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------ internos

    async def _fetch_state(self) -> str:
        cfg = self._settings
        url = (
            f"{cfg.jarvis_waha_base_url.rstrip('/')}/api/sessions/"
            f"{quote(cfg.jarvis_waha_session, safe='')}"
        )
        headers = {"X-Api-Key": cfg.jarvis_waha_api_key.get_secret_value()}
        try:
            response = await self._client.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("channel_check_unreachable", extra={"error": type(exc).__name__})
            return UNREACHABLE
        if response.status_code == 404:
            return "STOPPED"  # sessao nao existe no WAHA
        if not response.is_success:
            logger.warning("channel_check_unreachable", extra={"status": response.status_code})
            return UNREACHABLE
        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            return UNREACHABLE
        return status.strip().upper() if isinstance(status, str) and status.strip() else UNREACHABLE

    async def _apply(self, state: str) -> None:
        async with self._lock:
            previous = self._state
            self._state = state
            if previous == state:
                return
            logger.info("channel_session_changed", extra={"state": state})
            now = self._clock.now()
            try:
                async with self._pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        _UPSERT_STATE,
                        STATE_KEY,
                        json.dumps({"state": state, "since": now.isoformat()}),
                        now,
                    )
                    went_down = state != WORKING and (previous is None or previous == WORKING)
                    if went_down:
                        await self._raise_down(conn, state, now)
            except Exception as exc:  # noqa: BLE001 - estado em memoria ja vale para o envio
                logger.error("channel_state_save_failed", extra={"error": type(exc).__name__})

    async def _raise_down(self, conn: Any, state: str, now: datetime) -> None:
        await alerts.raise_alert(
            conn,
            "channel_down",
            "critical",
            "Jarvis desconectado do WhatsApp: os vendedores nao recebem resposta. "
            "Verifique a sessao no WAHA (pode ser preciso ler o QR code de novo).",
            {"state": state},
            dedup_key=DOWN_DEDUP_KEY,
            dedup_window=DOWN_DEDUP_WINDOW,
            email=self._settings.production,
            flags=self._flags,
            settings=self._settings,
            clock=self._clock,
        )
