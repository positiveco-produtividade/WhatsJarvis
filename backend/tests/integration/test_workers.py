"""Lacos de fundo (PF-34): varredura de vencidos, recuperacao, flush e resiliencia a falhas."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import OutboundKind
from app.services import outbox
from app.services.alerts import raise_alert
from app.services.counters import SecurityCounters
from app.services.flags import Flags
from app.services.workers import Workers
from tests.fakes import FakeChannel
from tests.integration.test_schema_access_chat import add_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr("postgresql://x"),
        whatsapp_app_secret=SecretStr("s"),
        whatsapp_verify_token=SecretStr("v"),
        whatsapp_access_token=SecretStr("t"),
        whatsapp_phone_number_id="1",
    )


class Env:
    """Pool, relogio, canal falso e o Workers sob teste."""

    def __init__(self, pool: asyncpg.Pool, channel: Any = None) -> None:
        self.pool = pool
        self.clock = FakeClock(NOW)
        self.channel = channel or FakeChannel()
        self.counters = SecurityCounters(self.clock)
        self.flags = Flags(pool, self.clock, team_group_initial=False)
        self.settings = make_settings()

    def workers(self, **over: Any) -> Workers:
        return Workers(
            self.pool,
            channel=self.channel,
            flags=self.flags,
            counters=self.counters,
            settings=self.settings,
            clock=self.clock,
            **over,
        )


async def _active_user(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await add_user(
            conn, status="active", accepted_at=NOW, accepted_notice_version="v1"
        )


async def _outbound(pool: asyncpg.Pool, clock: FakeClock, user_id: int, *, delay: int) -> int:
    async with pool.acquire() as conn:
        out_id = await outbox.enqueue(
            conn,
            _Target(user_id),
            OutboundKind.UNDER_CONSTRUCTION,
            body="texto",
            clock=clock,
        )
        await conn.execute(
            "UPDATE chat.outbound_messages SET next_attempt_at = $2 WHERE id = $1",
            out_id,
            NOW + timedelta(seconds=delay),
        )
    return out_id


class _Target:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.phone_e164 = "+5511987654321"


async def _group_delivery(pool: asyncpg.Pool, env: Env, *, due_in: int, **over: Any) -> int:
    async with pool.acquire() as conn:
        alert_id = await raise_alert(
            conn, "teste", "warn", "Alerta de teste", {}, email=False,
            flags=env.flags, settings=env.settings, clock=env.clock,
        )  # fmt: skip
        await conn.execute("DELETE FROM ops.deliveries WHERE source_id = $1", alert_id)
        delivery_id: int = await conn.fetchval(
            "INSERT INTO ops.deliveries (source_type, source_id, channel, status, attempts, "
            "next_attempt_at, claimed_at, created_at) "
            "VALUES ('alert', $1, 'group', $2, 1, $3, $4, $5) RETURNING id",
            alert_id,
            over.get("status", "pending"),
            NOW + timedelta(seconds=due_in),
            over.get("claimed_at"),
            NOW,
        )
    return delivery_id


async def _status(pool: asyncpg.Pool, table: str, row_id: int) -> str:
    value: str = await pool.fetchval(f"SELECT status FROM {table} WHERE id = $1", row_id)  # noqa: S608
    return value


async def test_ciclo_despacha_vencido_e_ignora_nao_vencido(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    user_id = await _active_user(db_pool)
    due = await _outbound(db_pool, env.clock, user_id, delay=0)
    later = await _outbound(db_pool, env.clock, user_id, delay=3600)
    delivery_due = await _group_delivery(db_pool, env, due_in=0)
    delivery_later = await _group_delivery(db_pool, env, due_in=3600)

    result = await env.workers().run_cycle()

    assert (result.outbound, result.deliveries) == (1, 1)
    assert await _status(db_pool, "chat.outbound_messages", due) == "sent"
    assert await _status(db_pool, "chat.outbound_messages", later) == "pending"
    assert await _status(db_pool, "ops.deliveries", delivery_due) == "panel_only"
    assert await _status(db_pool, "ops.deliveries", delivery_later) == "pending"
    assert len(env.channel.calls) == 1


async def test_vencimento_chega_com_o_relogio(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    user_id = await _active_user(db_pool)
    later = await _outbound(db_pool, env.clock, user_id, delay=600)
    workers = env.workers()
    await workers.run_cycle()
    assert await _status(db_pool, "chat.outbound_messages", later) == "pending"
    env.clock.advance(minutes=11)
    await workers.run_cycle()
    assert await _status(db_pool, "chat.outbound_messages", later) == "sent"


async def test_recuperacao_de_travados(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    user_id = await _active_user(db_pool)
    stuck_out = await _outbound(db_pool, env.clock, user_id, delay=0)
    await db_pool.execute(
        "UPDATE chat.outbound_messages SET status = 'sending', claimed_at = $2 WHERE id = $1",
        stuck_out,
        NOW - timedelta(minutes=3),
    )
    stuck = await _group_delivery(
        db_pool, env, due_in=0, status="sending", claimed_at=NOW - timedelta(minutes=6)
    )
    fresh = await _group_delivery(
        db_pool, env, due_in=0, status="sending", claimed_at=NOW - timedelta(minutes=1)
    )

    await env.workers().recover()

    assert await _status(db_pool, "chat.outbound_messages", stuck_out) == "unknown"
    assert await _status(db_pool, "ops.deliveries", stuck) == "pending"
    assert await _status(db_pool, "ops.deliveries", fresh) == "sending"
    assert env.channel.calls == []  # recuperar nao reenvia


async def test_start_recupera_na_subida_e_stop_encerra_limpo(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    stuck = await _group_delivery(
        db_pool, env, due_in=3600, status="sending", claimed_at=NOW - timedelta(minutes=9)
    )
    gate = asyncio.Event()

    async def blocked_sleep(_: float) -> None:
        await gate.wait()

    workers = env.workers(sleep=blocked_sleep)
    await workers.start()
    assert await _status(db_pool, "ops.deliveries", stuck) == "pending"
    task = workers._task
    assert task is not None and not task.done()
    await workers.stop()
    assert task.done() and task.cancelled()


async def test_flush_grava_contadores_a_cada_30s(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    workers = env.workers()
    env.counters.incr("invalid_signature", 2)
    assert (await workers.run_cycle()).flushed == 1

    env.counters.incr("invalid_signature")
    env.clock.advance(seconds=10)
    assert (await workers.run_cycle()).flushed == 0
    assert sum(env.counters.pending().values()) == 1

    env.clock.advance(seconds=25)
    assert (await workers.run_cycle()).flushed == 1
    total = await db_pool.fetchval(
        "SELECT sum(count) FROM ops.security_counters WHERE kind = 'invalid_signature'"
    )
    assert total == 3


async def test_stop_faz_flush_final(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    workers = env.workers()
    env.counters.incr("invalid_json")
    await workers.stop()
    total = await db_pool.fetchval(
        "SELECT sum(count) FROM ops.security_counters WHERE kind = 'invalid_json'"
    )
    assert total == 1


class _ExplodingChannel(FakeChannel):
    async def send_text(self, to: str, body: str) -> Any:
        raise RuntimeError("segredo-do-canal")


async def test_excecao_nao_mata_o_laco_e_loga_so_a_classe(
    db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    env = Env(db_pool, _ExplodingChannel())
    user_id = await _active_user(db_pool)
    await _outbound(db_pool, env.clock, user_id, delay=0)
    ticks = 0
    reached = asyncio.Event()

    async def counting_sleep(_: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= 3:
            reached.set()
        await asyncio.sleep(0)

    workers = env.workers(sleep=counting_sleep)
    with caplog.at_level(logging.ERROR):
        await workers.start()
        await asyncio.wait_for(reached.wait(), timeout=10)
        task = workers._task
        assert task is not None and not task.done()
        await workers.stop()

    errors = [r for r in caplog.records if r.getMessage() == "worker_step_failed"]
    assert errors
    assert all(getattr(r, "error", "") == "RuntimeError" for r in errors)
    assert "segredo-do-canal" not in caplog.text


async def test_falha_no_flush_nao_impede_o_resto_do_ciclo(db_pool: asyncpg.Pool) -> None:
    env = Env(db_pool)
    user_id = await _active_user(db_pool)
    due = await _outbound(db_pool, env.clock, user_id, delay=0)

    async def broken_flush(conn: Any) -> int:
        raise RuntimeError("flush falhou")

    env.counters.flush = broken_flush  # type: ignore[method-assign]
    result = await env.workers().run_cycle()
    assert result.outbound == 1 and result.flushed == 0
    assert await _status(db_pool, "chat.outbound_messages", due) == "sent"
