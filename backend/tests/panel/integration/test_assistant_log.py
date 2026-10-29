"""Registro de perguntas do assistente (PN-T54; PN-53)."""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.assistant import log

pytestmark = pytest.mark.db

ADMIN = "caio@example.test"
OTHER = "outra@example.test"


@pytest.fixture
async def conn(db_conn: asyncpg.Connection) -> asyncpg.Connection:
    return db_conn


async def test_answer_recorded(conn: asyncpg.Connection, clock: FakeClock) -> None:
    rec = await log.record_answer(
        conn,
        asked_by=ADMIN,
        agent="jarvis",
        question="Quantos tickets abertos?",
        answer="Tres.",
        tools_used=["count_tickets"],
        tokens_in=10,
        tokens_out=4,
        latency_ms=120,
        clock=clock,
    )
    assert rec.status == "ok"
    assert rec.answer == "Tres."
    assert rec.tools_used == ("count_tickets",)
    assert rec.error_code is None
    assert rec.asked_at == clock.now()


async def test_error_recorded_without_answer(conn: asyncpg.Connection, clock: FakeClock) -> None:
    rec = await log.record_error(
        conn,
        asked_by=ADMIN,
        agent="duda",
        question="Oi?",
        error_code="Provider Timeout!",
        clock=clock,
    )
    assert rec.status == "error"
    assert rec.answer is None
    assert rec.error_code == "provider_timeout"
    assert rec.tools_used == ()


async def test_empty_error_code_becomes_unknown(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    rec = await log.record_error(
        conn, asked_by=ADMIN, agent="jarvis", question="x", error_code="  !! ", clock=clock
    )
    assert rec.error_code == "unknown"


async def test_question_truncated(conn: asyncpg.Connection, clock: FakeClock) -> None:
    rec = await log.record_answer(
        conn, asked_by=ADMIN, agent="jarvis", question="a" * 5000, answer="ok", clock=clock
    )
    assert len(rec.question) == log.MAX_QUESTION_CHARS


async def test_stats_empty_is_zero(conn: asyncpg.Connection, clock: FakeClock) -> None:
    assert await log.stats_30d(conn, clock=clock) == log.QuestionStats(0, 0)
    assert await log.list_recent(conn) == []


async def test_stats_30_days_and_distinct_people(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    start = clock.now()
    clock.set(start - timedelta(days=31))
    await log.record_answer(conn, asked_by=ADMIN, agent="jarvis", question="velha", answer="a",
                            clock=clock)
    clock.set(start - timedelta(days=29))
    await log.record_answer(conn, asked_by=ADMIN, agent="jarvis", question="q1", answer="a",
                            clock=clock)
    await log.record_error(conn, asked_by=ADMIN, agent="jarvis", question="q2", error_code="e",
                           clock=clock)
    clock.set(start)
    await log.record_answer(conn, asked_by=OTHER, agent="leo", question="q3", answer="a",
                            clock=clock)
    stats = await log.stats_30d(conn, clock=clock)
    assert stats == log.QuestionStats(questions=3, people=2)


async def test_list_recent_newest_first_and_paging(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    start = clock.now()
    for i in range(3):
        clock.set(start + timedelta(minutes=i))
        await log.record_answer(conn, asked_by=ADMIN, agent="jarvis", question=f"q{i}",
                                answer="a", clock=clock)
    rows = await log.list_recent(conn, limit=2)
    assert [r.question for r in rows] == ["q2", "q1"]
    rest = await log.list_recent(conn, limit=2, offset=2)
    assert [r.question for r in rest] == ["q0"]
