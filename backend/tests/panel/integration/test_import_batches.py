"""Lotes de importacao: TTL, gravacao unica, apagamento de PII (PN-T28)."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import import_batches as ib

pytestmark = pytest.mark.db

ACTOR = "caio@example.test"
PEOPLE_ROWS = [{"nome": "Fulana Teste", "telefone": "+5585999990001", "situacao": "entra"}]
FAQ_ROWS = [{"pergunta": "Qual o sabor?", "resposta": "Chocolate.", "situacao": "entra"}]


@pytest.fixture
async def conn(db_conn: asyncpg.Connection) -> asyncpg.Connection:
    return db_conn


async def _make(conn: asyncpg.Connection, clock: FakeClock, kind: str) -> ib.BatchRecord:
    return await ib.create_batch(
        conn,
        kind=kind,
        actor=ACTOR,
        filename="x.csv",
        sha256="a" * 64,
        size_bytes=10,
        rows=PEOPLE_ROWS if kind == "people" else FAQ_ROWS,
        summary={"entra": 1},
        brand="jungle" if kind == "faq" else None,
        clock=clock,
    )


async def _raw_rows(conn: asyncpg.Connection, batch_id: uuid.UUID) -> str:
    sql = "SELECT rows::text FROM ops.import_batches WHERE id=$1"
    return str(await conn.fetchval(sql, batch_id))


async def test_create_ttl_by_kind(conn: asyncpg.Connection, clock: FakeClock) -> None:
    faq = await _make(conn, clock, "faq")
    people = await _make(conn, clock, "people")
    assert faq.status == ib.PREVIEW and faq.is_open
    assert (faq.expires_at - faq.created_at).total_seconds() == 7200
    assert (people.expires_at - people.created_at).total_seconds() == 3600
    assert faq.rows == FAQ_ROWS and faq.summary == {"entra": 1}


async def test_create_rejects_unknown_kind(conn: asyncpg.Connection, clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        await ib.create_batch(
            conn,
            kind="x",
            actor=ACTOR,
            filename="f",
            sha256="a",
            size_bytes=1,
            rows=[],
            summary={},
            clock=clock,
        )


async def test_get_reopens_and_missing_is_none(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _make(conn, clock, "faq")
    again = await ib.get_batch(conn, batch.id, clock=clock)
    assert again is not None and again.rows == FAQ_ROWS and again.is_open
    assert await ib.get_batch(conn, uuid.uuid4(), clock=clock) is None


async def test_claim_commit_once_and_wipes_people(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _make(conn, clock, "people")
    first = await ib.claim_commit(conn, batch.id, clock=clock)
    assert first is not None and first.rows == PEOPLE_ROWS and first.status == ib.COMMITTED
    assert await ib.claim_commit(conn, batch.id, clock=clock) is None
    assert await _raw_rows(conn, batch.id) == "[]"
    after = await ib.get_batch(conn, batch.id, clock=clock)
    assert after is not None and after.status == ib.COMMITTED and after.finished_at is not None


async def test_claim_commit_faq_keeps_rows(conn: asyncpg.Connection, clock: FakeClock) -> None:
    batch = await _make(conn, clock, "faq")
    assert await ib.claim_commit(conn, batch.id, clock=clock) is not None
    after = await ib.get_batch(conn, batch.id, clock=clock)
    assert after is not None and after.rows == FAQ_ROWS


async def test_claim_rolls_back_with_caller(conn: asyncpg.Connection, clock: FakeClock) -> None:
    batch = await _make(conn, clock, "people")
    with pytest.raises(RuntimeError):
        async with conn.transaction():
            claimed = await ib.claim_commit(conn, batch.id, clock=clock)
            assert claimed is not None
            raise RuntimeError("falha na gravacao")
    again = await ib.get_batch(conn, batch.id, clock=clock)
    assert again is not None and again.is_open and again.rows == PEOPLE_ROWS


async def test_cancel_wipes_people_and_blocks_commit(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _make(conn, clock, "people")
    assert await ib.cancel_batch(conn, batch.id, clock=clock) is True
    assert await ib.cancel_batch(conn, batch.id, clock=clock) is False
    assert await _raw_rows(conn, batch.id) == "[]"
    assert await ib.claim_commit(conn, batch.id, clock=clock) is None
    after = await ib.get_batch(conn, batch.id, clock=clock)
    assert after is not None and after.status == ib.CANCELLED


async def test_cancel_after_commit_is_noop(conn: asyncpg.Connection, clock: FakeClock) -> None:
    batch = await _make(conn, clock, "faq")
    await ib.claim_commit(conn, batch.id, clock=clock)
    assert await ib.cancel_batch(conn, batch.id, clock=clock) is False


async def test_expired_people_wiped_on_read_and_cannot_commit(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _make(conn, clock, "people")
    clock.advance(minutes=61)
    assert await ib.claim_commit(conn, batch.id, clock=clock) is None
    after = await ib.get_batch(conn, batch.id, clock=clock)
    assert after is not None and after.status == ib.EXPIRED and after.rows == []
    assert await _raw_rows(conn, batch.id) == "[]"


async def test_faq_open_at_61_min_expires_after_2h(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _make(conn, clock, "faq")
    clock.advance(minutes=61)
    still = await ib.get_batch(conn, batch.id, clock=clock)
    assert still is not None and still.is_open
    clock.advance(minutes=60)
    gone = await ib.get_batch(conn, batch.id, clock=clock)
    assert gone is not None and gone.status == ib.EXPIRED


async def test_expire_due_sweeps_only_due(conn: asyncpg.Connection, clock: FakeClock) -> None:
    people = await _make(conn, clock, "people")
    faq = await _make(conn, clock, "faq")
    clock.advance(minutes=90)
    assert await ib.expire_due(conn, clock=clock) == 1
    assert await _raw_rows(conn, people.id) == "[]"
    open_faq = await ib.get_batch(conn, faq.id, clock=clock)
    assert open_faq is not None and open_faq.is_open
    assert await ib.expire_due(conn, clock=clock) == 0
