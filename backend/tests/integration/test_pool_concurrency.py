"""Regressao: nenhum caminho que segura uma conexao do pool pede outra (deadlock de pool).

Pool de 2 conexoes + cache de flags frio + 6 tarefas concorrentes: se uma tarefa segura uma
conexao e pede outra ao pool, todas travam. Cada caso roda sob asyncio.wait_for.
"""
import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.values import OutboundKind, OutboundStatus
from app.db.connection import create_pool
from app.services import outbox
from app.services.flags import Flags
from app.services.inbound import ContentKind
from app.services.pipeline import Pipeline
from app.services.reply_policy import FlagsReplyPolicy
from tests.db_support import _truncate_all
from tests.fakes import FakeChannel
from tests.integration.test_idempotency import add_active_user, make_settings, msg
from tests.integration.test_pipeline_core import PHONE_B, add_user, text_msg

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
TASKS = 6
TIMEOUT = 20.0


@pytest.fixture
async def tiny_pool(migrated_database: str) -> AsyncIterator[asyncpg.Pool]:
    await _truncate_all(migrated_database)
    pool = await create_pool(migrated_database, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


def cold_pipeline(pool: asyncpg.Pool, clock: FakeClock) -> Pipeline:
    return Pipeline(pool, make_settings(), Flags(pool, clock), clock)


async def test_midia_concorrente_com_cache_frio_nao_trava(tiny_pool: asyncpg.Pool) -> None:
    async with tiny_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = cold_pipeline(tiny_pool, FakeClock(NOW))
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(pipe.handle_message(msg(f"a{i}", ContentKind.AUDIO)) for i in range(TASKS))
        ),
        TIMEOUT,
    )
    assert len(outcomes) == TASKS
    async with tiny_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM chat.tickets") == TASKS


async def test_conflito_bsuid_concorrente_com_cache_frio_nao_trava(
    tiny_pool: asyncpg.Pool,
) -> None:
    async with tiny_pool.acquire() as conn:
        await add_user(conn, bsuid="bsuid-A")
        await add_user(conn, phone="+5521987654321")
    pipe = cold_pipeline(tiny_pool, FakeClock(NOW))
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                pipe.handle_message(text_msg(f"c{i}", wa_id=PHONE_B, bsuid="bsuid-A"))
                for i in range(TASKS)
            )
        ),
        TIMEOUT,
    )
    assert len(outcomes) == TASKS
    async with tiny_pool.acquire() as conn:
        decisions = await conn.fetchval(
            "SELECT count(*) FROM chat.message_trace WHERE decision = 'identity_conflict'"
        )
        assert decisions == TASKS


async def test_dispatch_com_flags_reply_policy_frio_nao_trava(tiny_pool: asyncpg.Pool) -> None:
    clock = FakeClock(NOW)
    out_ids: list[int] = []
    async with tiny_pool.acquire() as conn:
        uid = await add_active_user(conn)
        user: Any = type("U", (), {"id": uid, "phone_e164": "+5511987654321"})()
        for _ in range(TASKS):
            out_ids.append(
                await outbox.enqueue(
                    conn, user, OutboundKind.UNDER_CONSTRUCTION, body="Em construcao", clock=clock
                )
            )
    policy = FlagsReplyPolicy(Flags(tiny_pool, clock))
    channel = FakeChannel()
    statuses = await asyncio.wait_for(
        asyncio.gather(
            *(
                outbox.dispatch(tiny_pool, oid, channel=channel, policy=policy, clock=clock)
                for oid in out_ids
            )
        ),
        TIMEOUT,
    )
    assert statuses == [OutboundStatus.SENT] * TASKS
