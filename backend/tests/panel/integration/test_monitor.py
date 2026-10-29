"""Monitor de sistema (PN-44 tipos de sistema, PN-65 e-mail sem PII) em Postgres real."""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.panel.services import monitor
from app.panel.services.monitor import MonitorDeps
from app.panel.services.workers import Loop, discover_loops
from tests.panel import factories

pytestmark = pytest.mark.db

_COUNTER = itertools.count(1)


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "resend_api_key": SecretStr("re_key"),
        "resend_from_email": "jarvis@example.com",
        "alert_recipient_email": SecretStr("destino@example.com"),
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def deps(clock: FakeClock) -> MonitorDeps:
    return MonitorDeps(settings=make_settings(), clock=clock)


@pytest.fixture
async def uid(db_conn: asyncpg.Connection, clock: FakeClock) -> int:
    return await factories.insert_user(
        db_conn, name="Vendedor M", seq=77, brands=["jungle"], now=clock.now()
    )


async def _traces(
    conn: asyncpg.Connection,
    uid: int,
    count: int,
    at: datetime,
    *,
    decision: str = "allowed",
    send_result: str | None = None,
) -> None:
    for _ in range(count):
        await conn.execute(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, send_result) "
            "VALUES ($1, $2, 'text', $3, 'allowed', $4, $5)",
            f"wamid.mon.{next(_COUNTER)}",
            at,
            uid,
            decision,
            send_result,
        )


async def _outbound_failed(conn: asyncpg.Connection, uid: int, count: int, at: datetime) -> None:
    for _ in range(count):
        await conn.execute(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, "
            "status, attempts, next_attempt_at, created_at, claimed_at) "
            "VALUES ($1, '+5511900000077', 'media_ack', 'active', 'failed', 3, $2, $2, $2)",
            uid,
            at,
        )


async def _alerts(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT * FROM ops.alerts ORDER BY id")


async def _cycle(conn: asyncpg.Connection, deps: MonitorDeps) -> dict[str, int | None]:
    return await monitor.run_checks(conn, deps)


async def test_sem_dados_nao_gera_alerta(db_conn: asyncpg.Connection, deps: MonitorDeps) -> None:
    assert set((await _cycle(db_conn, deps)).values()) == {None}
    assert await _alerts(db_conn) == []


async def test_volume_spike_acima_da_mediana_e_do_minimo(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    now = clock.now()
    for day in range(1, 8):  # 10 por dia nas mesmas horas -> mediana 10
        await _traces(db_conn, uid, 10, now - timedelta(days=day, minutes=30))
    await _traces(db_conn, uid, 31, now - timedelta(minutes=10))
    assert (await _cycle(db_conn, deps))["volume_spike"] is not None
    row = (await _alerts(db_conn))[0]
    assert row["kind"] == "volume_spike" and row["severity"] == "warn"
    payload = json.loads(row["payload"])
    assert payload["current_count"] == 31 and payload["baseline_median"] == 10.0


async def test_volume_no_limite_ou_abaixo_do_minimo_nao_alerta(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    now = clock.now()
    for day in range(1, 8):
        await _traces(db_conn, uid, 10, now - timedelta(days=day, minutes=30))
    await _traces(db_conn, uid, 30, now - timedelta(minutes=10))  # = 3x, nao acima
    assert (await _cycle(db_conn, deps))["volume_spike"] is None
    await db_conn.execute("TRUNCATE chat.message_trace CASCADE")
    await _traces(db_conn, uid, 29, now - timedelta(minutes=10))  # baseline 0, abaixo do minimo
    assert (await _cycle(db_conn, deps))["volume_spike"] is None


async def test_volume_sem_historico_usa_o_minimo(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    await _traces(db_conn, uid, 30, clock.now() - timedelta(minutes=5))
    assert (await _cycle(db_conn, deps))["volume_spike"] is not None


async def test_error_rate_conta_decision_error_e_falha_de_envio(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    at = clock.now() - timedelta(minutes=20)
    await _traces(db_conn, uid, 14, at)
    await _traces(db_conn, uid, 3, at, decision="error")
    await _traces(db_conn, uid, 3, at, send_result="failed")  # 6/20 = 30%
    assert (await _cycle(db_conn, deps))["error_rate"] is not None
    row = [r for r in await _alerts(db_conn) if r["kind"] == "error_rate"][0]
    payload = json.loads(row["payload"])
    assert row["severity"] == "high"
    assert payload["error_count"] == 6 and payload["total_count"] == 20
    assert payload["error_rate"] == 0.3


async def test_error_rate_abaixo_da_taxa_ou_do_minimo_nao_alerta(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    at = clock.now() - timedelta(minutes=20)
    await _traces(db_conn, uid, 17, at)
    await _traces(db_conn, uid, 3, at, decision="error")  # 15%
    assert (await _cycle(db_conn, deps))["error_rate"] is None
    await db_conn.execute("TRUNCATE chat.message_trace CASCADE")
    await _traces(db_conn, uid, 19, at, decision="error")  # 100%, mas so 19 mensagens
    assert (await _cycle(db_conn, deps))["error_rate"] is None


async def test_error_rate_ignora_mensagens_fora_da_janela(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    await _traces(db_conn, uid, 25, clock.now() - timedelta(minutes=90), decision="error")
    assert (await _cycle(db_conn, deps))["error_rate"] is None


async def test_api_failure_no_limite_alerta_e_abaixo_nao(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    at = clock.now() - timedelta(minutes=15)
    await _outbound_failed(db_conn, uid, 4, at)
    assert (await _cycle(db_conn, deps))["api_failure"] is None
    await _outbound_failed(db_conn, uid, 1, at)
    assert (await _cycle(db_conn, deps))["api_failure"] is not None
    row = [r for r in await _alerts(db_conn) if r["kind"] == "api_failure"][0]
    assert json.loads(row["payload"])["failure_count"] == 5


async def test_api_failure_antiga_nao_conta(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    await _outbound_failed(db_conn, uid, 6, clock.now() - timedelta(hours=3))
    assert (await _cycle(db_conn, deps))["api_failure"] is None


async def test_dedup_de_1h_por_tipo(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    await _outbound_failed(db_conn, uid, 6, clock.now() - timedelta(minutes=5))
    assert (await _cycle(db_conn, deps))["api_failure"] is not None
    clock.advance(minutes=30)
    assert (await _cycle(db_conn, deps))["api_failure"] is None
    clock.advance(minutes=31)
    await _outbound_failed(db_conn, uid, 6, clock.now() - timedelta(minutes=1))
    assert (await _cycle(db_conn, deps))["api_failure"] is not None
    assert len([r for r in await _alerts(db_conn) if r["kind"] == "api_failure"]) == 2


async def test_alerta_gera_entrega_de_email_e_payload_sem_pii(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    await _outbound_failed(db_conn, uid, 6, clock.now() - timedelta(minutes=5))
    await _cycle(db_conn, deps)
    row = (await _alerts(db_conn))[0]
    rows = await db_conn.fetch(
        "SELECT channel, status FROM ops.deliveries WHERE source_type = 'alert' AND source_id = $1",
        row["id"],
    )
    assert {r["channel"]: r["status"] for r in rows}["email"] == "pending"
    blob = row["message"] + row["payload"]
    assert "+55" not in blob and "@" not in blob and "Vendedor" not in blob
    assert row["user_id"] is None


async def test_escalados_e_desconhecidos_nao_geram_alerta(
    db_conn: asyncpg.Connection, deps: MonitorDeps, clock: FakeClock, uid: int
) -> None:
    at = clock.now() - timedelta(minutes=10)
    await _traces(db_conn, uid, 5, at, decision="escalated_media")
    await _traces(db_conn, uid, 5, at, decision="unknown")
    await _traces(db_conn, uid, 5, at, decision="ignored_type")
    await _cycle(db_conn, deps)
    assert await _alerts(db_conn) == []


async def test_run_usa_o_pool_do_app(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, uid: int
) -> None:
    now = await db_conn.fetchval("SELECT now()")
    await _traces(db_conn, uid, 30, now - timedelta(minutes=5))
    app = SimpleNamespace(state=SimpleNamespace(pool=db_pool, settings=make_settings(), flags=None))
    await monitor.run(app)
    assert [r["kind"] for r in await _alerts(db_conn)] == ["volume_spike"]


def test_loop_exposto_e_descoberto() -> None:
    assert isinstance(monitor.LOOP, Loop)
    assert monitor.LOOP.interval_seconds == 600
    assert monitor.LOOP in discover_loops()
