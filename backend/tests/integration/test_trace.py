"""trace: idempotencia, corpo so de autorizado, sem processing, envio (AC-7/32/23/33)."""
import asyncio
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import AccessDecision, Decision, ReplyKind, SendResult
from app.services import trace

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CLOCK = FakeClock(NOW)

pytestmark = pytest.mark.db

COUNT_PROCESSING = "SELECT count(*) FROM chat.message_trace WHERE decision = 'processing'"


async def add_user(conn: asyncpg.Connection) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, "
            "channels, brands, manager_name, status, status_changed_at, accepted_at, "
            "accepted_notice_version, "
            "created_at, created_by, "
            "updated_at) VALUES ('Ana', '+5511900000001', '551100000001', 'vendedor', 'Acme', "
            "'{SP}', '{farma}', '{jungle}', 'Gestora', 'active', $1, $1, 'v1', $1, 'teste', $1) "
            "RETURNING id",
            NOW,
        )
    )


async def begin(conn: Any, wa_id: str = "wamid.A") -> int:
    trace_id = await trace.begin(conn, wa_id, msg_type="text", clock=CLOCK)
    assert trace_id is not None
    return trace_id


async def row(conn: asyncpg.Connection, trace_id: int) -> asyncpg.Record:
    r = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE id = $1", trace_id)
    assert r is not None
    return r


async def finalize_allowed(conn: asyncpg.Connection, trace_id: int, user_id: int) -> None:
    await trace.finalize(
        conn,
        trace_id,
        access_decision=AccessDecision.ALLOWED,
        decision=Decision.ALLOWED,
        user_id=user_id,
        reply_kind=ReplyKind.UNDER_CONSTRUCTION,
    )


async def test_begin_cria_processing_e_duplicata_devolve_none(db_conn: asyncpg.Connection) -> None:
    trace_id = await begin(db_conn)
    r = await row(db_conn, trace_id)
    assert r["decision"] == "processing"
    assert r["received_at"] == NOW
    assert await trace.begin(db_conn, "wamid.A", msg_type="text", clock=CLOCK) is None
    assert await db_conn.fetchval("SELECT count(*) FROM chat.message_trace") == 1


async def test_duplicata_concorrente_cria_um_rastro(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as a, db_pool.acquire() as b:
        tx = a.transaction()
        await tx.start()
        await begin(a, "wamid.C")
        second = asyncio.create_task(trace.begin(b, "wamid.C", msg_type="text", clock=CLOCK))
        await asyncio.sleep(0.3)
        assert not second.done()  # espera o commit da primeira no indice unico
        await tx.commit()
        assert await asyncio.wait_for(second, 5) is None
        assert await b.fetchval("SELECT count(*) FROM chat.message_trace") == 1


async def test_rollback_libera_a_mensagem_para_reentrega(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as a:
        tx = a.transaction()
        await tx.start()
        await begin(a, "wamid.R")
        await tx.rollback()
        assert await trace.begin(a, "wamid.R", msg_type="text", clock=CLOCK) is not None


async def test_finalize_autorizado_guarda_corpo(db_conn: asyncpg.Connection) -> None:
    user_id = await add_user(db_conn)
    trace_id = await begin(db_conn)
    await trace.finalize(
        db_conn,
        trace_id,
        access_decision=AccessDecision.ALLOWED,
        decision=Decision.ALLOWED,
        user_id=user_id,
        counted_in_limit=True,
        body="oi",
        reply_kind=ReplyKind.PENDING,
    )
    r = await row(db_conn, trace_id)
    assert (r["decision"], r["access_decision"], r["body"]) == ("allowed", "allowed", "oi")
    assert r["counted_in_limit"] is True
    assert r["reply_kind"] == "pending"
    assert r["user_id"] == user_id
    await trace.ensure_finalized(db_conn, trace_id)


async def test_corpo_recusado_quando_nao_allowed(db_conn: asyncpg.Connection) -> None:
    user_id = await add_user(db_conn)
    trace_id = await begin(db_conn)
    with pytest.raises(InvalidValue):
        await trace.finalize(
            db_conn,
            trace_id,
            access_decision=AccessDecision.RATE_LIMITED,
            decision=Decision.RATE_LIMITED,
            user_id=user_id,
            body="segredo",
        )
    assert (await row(db_conn, trace_id))["body"] is None


async def test_nenhum_processing_apos_finalize_e_trava(db_conn: asyncpg.Connection) -> None:
    user_id = await add_user(db_conn)
    trace_id = await begin(db_conn)
    with pytest.raises(trace.TraceNotFinalized):
        await trace.ensure_finalized(db_conn, trace_id)
    await trace.finalize(
        db_conn,
        trace_id,
        access_decision=AccessDecision.ESCALATED_MEDIA,
        decision=Decision.ESCALATED_MEDIA,
        user_id=user_id,
        reply_kind=ReplyKind.MEDIA_ACK,
        ticket_number="J-0001",
    )
    assert await db_conn.fetchval(COUNT_PROCESSING) == 0
    assert (await row(db_conn, trace_id))["ticket_number"] == "J-0001"
    with pytest.raises(InvalidValue):  # ja finalizado: nao reescreve
        await finalize_allowed(db_conn, trace_id, user_id)


async def test_finalize_rejeita_processing(db_conn: asyncpg.Connection) -> None:
    trace_id = await begin(db_conn)
    with pytest.raises(InvalidValue):
        await trace.finalize(
            db_conn,
            trace_id,
            access_decision=AccessDecision.PROCESSING,
            decision=Decision.ERROR,
        )


async def test_mark_error_cria_e_nao_sobrescreve_final(db_pool: asyncpg.Pool) -> None:
    await trace.mark_error(db_pool, "wamid.E", msg_type="text", reason="KeyError", clock=CLOCK)
    async with db_pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = 'wamid.E'")
        assert r is not None
        assert (r["decision"], r["access_decision"], r["decision_reason"]) == (
            "error",
            "error",
            "KeyError",
        )
        user_id = await add_user(conn)
        final_id = await begin(conn, "wamid.F")
        await finalize_allowed(conn, final_id, user_id)
    await trace.mark_error(db_pool, "wamid.F", msg_type="text", reason="X", clock=CLOCK)
    async with db_pool.acquire() as conn:
        assert (await row(conn, final_id))["decision"] == "allowed"


async def test_mark_error_sobre_processing_residual(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        trace_id = await begin(conn, "wamid.P")
    await trace.mark_error(db_pool, "wamid.P", msg_type="text", reason="Boom", clock=CLOCK)
    async with db_pool.acquire() as conn:
        assert (await row(conn, trace_id))["decision"] == "error"
        assert await conn.fetchval(COUNT_PROCESSING) == 0


async def test_record_send_result_sucesso_e_falha(db_conn: asyncpg.Connection) -> None:
    user_id = await add_user(db_conn)
    trace_id = await begin(db_conn)
    await finalize_allowed(db_conn, trace_id, user_id)
    ok = await trace.record_send_result(
        db_conn,
        trace_id,
        send_result=SendResult.SENT,
        reply_wa_message_id="wamid.OUT",
        reply_sent_at=NOW,
        latency_ms=420,
    )
    r = await row(db_conn, trace_id)
    assert ok
    assert (r["send_result"], r["reply_wa_message_id"], r["latency_ms"]) == (
        "sent",
        "wamid.OUT",
        420,
    )
    assert r["decision"] == "allowed"
    await trace.record_send_result(
        db_conn,
        trace_id,
        send_result=SendResult.FAILED,
        decision=Decision.SEND_FAILED,
        send_error="131047",
    )
    r = await row(db_conn, trace_id)
    assert (r["decision"], r["send_result"], r["send_error"]) == ("send_failed", "failed", "131047")


async def test_revoked_before_send_e_decisao_invalida(db_conn: asyncpg.Connection) -> None:
    user_id = await add_user(db_conn)
    trace_id = await begin(db_conn)
    await finalize_allowed(db_conn, trace_id, user_id)
    await trace.record_send_result(
        db_conn,
        trace_id,
        send_result=SendResult.DISCARDED,
        decision=Decision.REVOKED_BEFORE_SEND,
    )
    assert (await row(db_conn, trace_id))["decision"] == "revoked_before_send"
    with pytest.raises(InvalidValue):
        await trace.record_send_result(
            db_conn, trace_id, send_result=SendResult.SENT, decision=Decision.ALLOWED
        )
    assert not await trace.record_send_result(db_conn, 999999, send_result=SendResult.SENT)
