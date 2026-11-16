"""Monitor da sessao do WAHA e alertas de canal (spec canal-waha.1, WA-18, WA-19; WA-T16)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.services import session_monitor as sm

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _settings(**extra: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        channel_provider="waha",
        database_url="postgresql://u:p@localhost/x_test",
        jarvis_waha_api_key="chave",
        jarvis_waha_hmac_key="hmac",
        jarvis_whatsapp_number="+5500000000001",
        **extra,
    )


def _monitor(pool: asyncpg.Pool, reply: Any, **extra: Any) -> sm.SessionMonitor:
    def handler(request: httpx.Request) -> httpx.Response:
        result: httpx.Response = reply(request) if callable(reply) else reply
        return result

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return sm.SessionMonitor(pool, _settings(**extra), client, clock=FakeClock(NOW))


async def _alerts(pool: asyncpg.Pool, kind: str) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(await conn.fetch("SELECT * FROM ops.alerts WHERE kind = $1", kind))


async def _state(pool: asyncpg.Pool) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        return await sm.read_state(conn)


async def test_estado_desconhecido_conta_como_no_ar(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"status": "WORKING"}))
    assert monitor.state is None and monitor.is_working()


async def test_checagem_working_grava_estado_sem_alerta(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"name": "jarvis", "status": "WORKING"}))
    assert await monitor.check_once() == "WORKING"
    assert (await _state(db_pool) or {}).get("state") == "WORKING"
    assert await _alerts(db_pool, "channel_down") == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (httpx.Response(200, json={"status": "SCAN_QR_CODE"}), "SCAN_QR_CODE"),
        (httpx.Response(200, json={"status": "FAILED"}), "FAILED"),
        (httpx.Response(404), "STOPPED"),
        (httpx.Response(500), "UNREACHABLE"),
    ],
)
async def test_sessao_fora_abre_um_alerta_critico(
    db_pool: asyncpg.Pool, reply: httpx.Response, expected: str
) -> None:
    monitor = _monitor(db_pool, reply)
    assert await monitor.check_once() == expected
    assert not monitor.is_working()
    assert sm.is_down(await _state(db_pool))
    [alert] = await _alerts(db_pool, "channel_down")
    assert alert["severity"] == "critical"
    await monitor.check_once()  # continua fora: nao repete o alerta
    assert len(await _alerts(db_pool, "channel_down")) == 1


async def test_waha_inacessivel_conta_como_fora(db_pool: asyncpg.Pool) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    monitor = _monitor(db_pool, boom)
    assert await monitor.check_once() == "UNREACHABLE"
    assert len(await _alerts(db_pool, "channel_down")) == 1


async def test_volta_a_working_limpa_o_estado(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"status": "WORKING"}))
    await monitor.on_session_event("FAILED")
    assert not monitor.is_working()
    await monitor.on_session_event("WORKING")
    assert monitor.is_working()
    assert not sm.is_down(await _state(db_pool))


async def test_evento_do_webhook_abre_alerta(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"status": "WORKING"}))
    await monitor.on_session_event("scan_qr_code")
    assert monitor.state == "SCAN_QR_CODE"
    assert len(await _alerts(db_pool, "channel_down")) == 1


async def test_restricao_abre_alerta_alto_com_dedup(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"status": "WORKING"}))
    await monitor.on_restriction()
    await monitor.on_restriction()
    [alert] = await _alerts(db_pool, "channel_restricted")
    assert alert["severity"] == "high"


async def test_sem_email_fora_de_producao(db_pool: asyncpg.Pool) -> None:
    monitor = _monitor(db_pool, httpx.Response(200, json={"status": "FAILED"}), production=False)
    await monitor.check_once()
    async with db_pool.acquire() as conn:
        channels = await conn.fetch(
            "SELECT d.channel FROM ops.deliveries d JOIN ops.alerts a ON a.id = d.source_id "
            "WHERE d.source_type = 'alert' AND a.kind = 'channel_down'"
        )
    assert "email" not in {r["channel"] for r in channels}
