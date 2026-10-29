"""Migration 202: chat.brain_runs e chat.answer_ratings (CB-7, CB-20, CB-25, CB-26)."""
from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import BrainOutcome
from app.db import migrate
from app.panel.services import retention
from tests.brain import factories
from tests.db_support import MIGRATIONS_DIR
from tests.panel import factories as panel_factories

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
OLD = datetime(2025, 10, 25, 12, 0, tzinfo=UTC)  # 12 meses e 1 dia antes do NOW
_N = itertools.count(1)


def make_settings() -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "retention_dry_run": False,
    }
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _trace(conn: asyncpg.Connection, user_id: int, at: datetime = NOW) -> int:
    return await factories.create_pending_trace(
        conn, user_id, at, wa_message_id=f"wamid.m202.{next(_N)}"
    )


async def _ticket(
    conn: asyncpg.Connection, user_id: int, at: datetime = NOW, *,
    trace_id: int | None = None, kind: str = "question",
) -> int:
    t = await panel_factories.insert_ticket(
        conn, number=next(_N), user_id=user_id, status="awaiting", created_at=at, kind=kind
    )
    if trace_id is not None:
        await conn.execute("UPDATE chat.tickets SET trace_id = $1 WHERE id = $2", trace_id, t.id)
    return t.id


async def _run(
    conn: asyncpg.Connection, trace_id: int, *, outcome: str = "answered", path: str = "ia",
    fidelity: str | None = None, ticket_id: int | None = None, at: datetime = NOW,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.brain_runs (trace_id, ticket_id, outcome, path, fidelity, "
            "created_at) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            trace_id, ticket_id, outcome, path, fidelity, at,
        )
    )


async def _rate(
    conn: asyncpg.Connection, ticket_id: int, user_id: int, trace_id: int, rating: str = "up",
    *, down_ticket_id: int | None = None, at: datetime = NOW,
) -> None:
    await conn.execute(
        "INSERT INTO chat.answer_ratings (ticket_id, user_id, trace_id, rating, down_ticket_id, "
        "rated_at) VALUES ($1, $2, $3, $4, $5, $6)",
        ticket_id, user_id, trace_id, rating, down_ticket_id, at,
    )


@pytest.fixture
async def uid(db_conn: asyncpg.Connection) -> int:
    return await factories.create_active_user(db_conn, NOW)


@pytest.mark.parametrize("outcome", [o.value for o in BrainOutcome])
async def test_aceita_todos_os_desfechos(
    db_conn: asyncpg.Connection, uid: int, outcome: str
) -> None:
    await _run(db_conn, await _trace(db_conn, uid), outcome=outcome)


async def test_desfechos_do_check_batem_com_o_enum(db_conn: asyncpg.Connection) -> None:
    for bad in ("inexistente", "ANSWERED"):
        uid = await factories.create_active_user(db_conn, NOW, seq=next(_N) + 100)
        with pytest.raises(asyncpg.CheckViolationError):
            await _run(db_conn, await _trace(db_conn, uid), outcome=bad)


async def test_recusa_path_e_fidelity_invalidos(db_conn: asyncpg.Connection, uid: int) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _run(db_conn, await _trace(db_conn, uid), path="web")
    with pytest.raises(asyncpg.CheckViolationError):
        await _run(db_conn, await _trace(db_conn, uid), fidelity="bad")
    for fid in ("ok", "repaired", "failed", None):
        await _run(db_conn, await _trace(db_conn, uid), fidelity=fid)


async def test_brain_runs_defaults_e_unicidade_por_trace(
    db_conn: asyncpg.Connection, uid: int
) -> None:
    trace = await _trace(db_conn, uid)
    await _run(db_conn, trace)
    row = await db_conn.fetchrow("SELECT * FROM chat.brain_runs")
    assert row is not None
    assert (row["llm_calls"], row["tokens_in"], row["tokens_out"]) == (0, 0, 0)
    assert str(row["cost_usd"]) == "0.000000"
    assert list(row["item_refs"]) == [] and list(row["flags"]) == []
    with pytest.raises(asyncpg.UniqueViolationError):
        await _run(db_conn, trace)


async def test_brain_runs_sem_colunas_de_texto_de_pergunta_ou_resposta(
    db_conn: asyncpg.Connection,
) -> None:
    cols = {
        r["column_name"]
        for r in await db_conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'chat' AND table_name = 'brain_runs'"
        )
    }
    forbidden = {"question", "answer", "body", "message", "text", "reply", "summary"}
    assert not cols & forbidden
    assert {"outcome", "cost_usd", "tokens_in", "tokens_out", "timings_ms"} <= cols


async def test_avaliacao_unica_por_ticket_primeira_vale(
    db_conn: asyncpg.Connection, uid: int
) -> None:
    trace = await _trace(db_conn, uid)
    ticket = await _ticket(db_conn, uid, trace_id=trace)
    await _rate(db_conn, ticket, uid, trace, "up")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _rate(db_conn, ticket, uid, trace, "down")
    inserted = await db_conn.fetchval(
        "INSERT INTO chat.answer_ratings (ticket_id, user_id, trace_id, rating, rated_at) "
        "VALUES ($1, $2, $3, 'down', $4) ON CONFLICT (ticket_id) DO NOTHING RETURNING id",
        ticket, uid, trace, NOW,
    )
    assert inserted is None
    assert await db_conn.fetchval("SELECT rating FROM chat.answer_ratings") == "up"


async def test_recusa_rating_invalido(db_conn: asyncpg.Connection, uid: int) -> None:
    trace = await _trace(db_conn, uid)
    ticket = await _ticket(db_conn, uid, trace_id=trace)
    with pytest.raises(asyncpg.CheckViolationError):
        await _rate(db_conn, ticket, uid, trace, "meh")


async def test_indice_parcial_um_ticket_de_pergunta_por_trace(
    db_conn: asyncpg.Connection, uid: int
) -> None:
    trace = await _trace(db_conn, uid)
    await _ticket(db_conn, uid, trace_id=trace, kind="question")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _ticket(db_conn, uid, trace_id=trace, kind="question")


async def test_indice_parcial_nao_afeta_media_nem_trace_nulo(
    db_conn: asyncpg.Connection, uid: int
) -> None:
    trace = await _trace(db_conn, uid)
    await _ticket(db_conn, uid, trace_id=trace, kind="question")
    await _ticket(db_conn, uid, trace_id=trace, kind="media")
    await _ticket(db_conn, uid, trace_id=trace, kind="media")
    await _ticket(db_conn, uid, kind="question")
    await _ticket(db_conn, uid, kind="question")


async def test_retention_purge_real_com_linhas_do_cerebro(db_conn: asyncpg.Connection) -> None:
    uid = await factories.create_active_user(db_conn, NOW, seq=7)
    old_trace = await _trace(db_conn, uid, OLD)
    old_ticket = await _ticket(db_conn, uid, OLD, trace_id=old_trace)
    rate_trace = await _trace(db_conn, uid, OLD)
    down_ticket = await _ticket(db_conn, uid, OLD)
    await _run(db_conn, old_trace, ticket_id=old_ticket, at=OLD)
    await _run(db_conn, rate_trace, outcome="greeting", ticket_id=down_ticket, at=OLD)
    await _rate(
        db_conn, old_ticket, uid, rate_trace, "down", down_ticket_id=down_ticket, at=OLD
    )
    new_trace = await _trace(db_conn, uid, NOW)
    new_ticket = await _ticket(db_conn, uid, NOW, trace_id=new_trace)
    await _run(db_conn, new_trace, ticket_id=new_ticket)
    await _rate(db_conn, new_ticket, uid, new_trace, "up")

    result = await retention.purge(db_conn, make_settings(), FakeClock(NOW))

    assert result.counts["tickets"] == 2
    assert result.counts["message_trace"] == 2
    assert await db_conn.fetchval("SELECT count(*) FROM chat.brain_runs") == 1
    assert await db_conn.fetchval("SELECT trace_id FROM chat.brain_runs") == new_trace
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 1
    assert await db_conn.fetchval("SELECT ticket_id FROM chat.answer_ratings") == new_ticket


async def test_set_null_em_ticket_apagado_mantem_o_run(
    db_conn: asyncpg.Connection, uid: int
) -> None:
    trace = await _trace(db_conn, uid)
    ticket = await _ticket(db_conn, uid)
    await _run(db_conn, trace, ticket_id=ticket)
    await db_conn.execute("DELETE FROM chat.tickets WHERE id = $1", ticket)
    assert await db_conn.fetchval("SELECT ticket_id FROM chat.brain_runs") is None
    assert await db_conn.fetchval("SELECT count(*) FROM chat.brain_runs") == 1


async def test_migration_idempotente(db_conn: asyncpg.Connection, uid: int) -> None:
    sql = (MIGRATIONS_DIR / "202_brain_runs_ratings.sql").read_text(encoding="utf-8")
    trace = await _trace(db_conn, uid)
    await _run(db_conn, trace)
    await db_conn.execute(sql)
    await db_conn.execute(sql)
    assert await db_conn.fetchval("SELECT count(*) FROM chat.brain_runs") == 1


async def test_migrate_check_sem_pendentes(db_conn: asyncpg.Connection) -> None:
    migrations = migrate.discover(MIGRATIONS_DIR)
    assert any(m.version == "202_brain_runs_ratings" for m in migrations)
    assert await migrate.is_up_to_date(db_conn, MIGRATIONS_DIR)
