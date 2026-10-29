"""Escrita de tickets (PN-16, PN-17, PN-18, PN-21, PN-55, PN-63) em Postgres real."""
from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import ValidationFailure
from app.panel.services import tickets_admin as ta
from tests.panel import factories
from tests.panel.fakes import WahaSpy

pytestmark = pytest.mark.db


async def _awaiting(conn: asyncpg.Connection, clock: FakeClock, number: int = 900) -> int:
    uid = await factories.insert_user(
        conn, name="Vendedor X", seq=50 + number, brands=["jungle"], now=clock.now()
    )
    t = await factories.insert_ticket(
        conn, number=number, user_id=uid, status="awaiting", created_at=clock.now(),
        brand="jungle", classification="PRODUTO", escalated=True, urgency="urgente",
    )
    return t.id


async def _audit(conn: asyncpg.Connection, action: str) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT * FROM ops.audit_log WHERE action = $1 ORDER BY id", action)


async def _side_effects(conn: asyncpg.Connection) -> tuple[int, int, int]:
    """Mensagens de saida, alertas e entregas: nada disso pode surgir ao escrever tickets."""
    counts = []
    for table in ("chat.outbound_messages", "ops.alerts", "ops.deliveries"):
        counts.append(int(await conn.fetchval(f"SELECT count(*) FROM {table}")))  # noqa: S608
    return counts[0], counts[1], counts[2]


async def test_conclude_grava_autor_data_e_audita_sem_enviar(
    db_conn: asyncpg.Connection, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    tid = await _awaiting(db_conn, clock)
    before = await _side_effects(db_conn)
    res = await ta.conclude(db_conn, tid, "  Ana   Souza ", actor="admin@x.com", clock=clock)
    assert res.concluded and res.status == "answered_human" and res.by == "Ana Souza"
    row = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", tid)
    assert row["status"] == "answered_human"
    assert row["answered_by"] == "Ana Souza" and row["answered_at"] == clock.now()
    assert row["ticket_number"] == res.ticket_number
    assert await _side_effects(db_conn) == before
    assert waha_spy.requests == []
    logs = await _audit(db_conn, "ticket.conclude")
    assert len(logs) == 1 and logs[0]["actor_type"] == "panel"
    assert logs[0]["actor"] == "admin@x.com" and logs[0]["target_id"] == res.ticket_number
    assert json.loads(logs[0]["old_value"]) == {"status": "awaiting"}
    assert json.loads(logs[0]["new_value"])["status"] == "answered_human"


@pytest.mark.parametrize("author", ["", "   ", None, "x" * (ta.AUTHOR_MAX_LENGTH + 1)])
async def test_conclude_exige_autor(
    db_conn: asyncpg.Connection, clock: FakeClock, author: str | None
) -> None:
    tid = await _awaiting(db_conn, clock)
    with pytest.raises(ValidationFailure):
        await ta.conclude(db_conn, tid, author, actor="admin", clock=clock)
    status = await db_conn.fetchval("SELECT status FROM chat.tickets WHERE id = $1", tid)
    assert status == "awaiting"
    assert await _audit(db_conn, "ticket.conclude") == []


async def test_conclude_inexistente(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    with pytest.raises(ta.TicketNotFound):
        await ta.conclude(db_conn, 987654, "Ana", actor="admin", clock=clock)


async def test_segundo_conclude_e_noop_com_aviso(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    tid = await _awaiting(db_conn, clock)
    first = await ta.conclude(db_conn, tid, "Ana", actor="admin", clock=clock)
    clock.advance(minutes=3)
    second = await ta.move_to_concluded(db_conn, tid, "Beto", actor="admin", clock=clock)
    assert first.concluded and not second.concluded
    assert second.by == "Ana" and second.at == first.at and second.status == "answered_human"
    row = await db_conn.fetchrow(
        "SELECT answered_by, answered_at FROM chat.tickets WHERE id = $1", tid
    )
    assert row["answered_by"] == "Ana" and row["answered_at"] == first.at
    assert len(await _audit(db_conn, "ticket.conclude")) == 1


async def test_dois_conclude_concorrentes_um_vence(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as setup:
        tid = await _awaiting(setup, clock)

    async def run(author: str) -> ta.ConcludeResult:
        async with db_pool.acquire() as conn:
            return await ta.conclude(conn, tid, author, actor="admin", clock=clock)

    results = await asyncio.gather(run("Ana"), run("Beto"))
    winners = [r for r in results if r.concluded]
    losers = [r for r in results if not r.concluded]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].by == winners[0].by
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT answered_by FROM chat.tickets WHERE id = $1", tid)
        assert row["answered_by"] == winners[0].by
        assert len(await _audit(conn, "ticket.conclude")) == 1


async def test_falha_na_auditoria_desfaz_a_acao(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    tid = await _awaiting(db_conn, clock)
    await db_conn.execute("ALTER TABLE ops.audit_log RENAME TO audit_log_off")
    try:
        with pytest.raises(asyncpg.PostgresError):
            await ta.conclude(db_conn, tid, "Ana", actor="admin", clock=clock)
    finally:
        await db_conn.execute("ALTER TABLE ops.audit_log_off RENAME TO audit_log")
    status = await db_conn.fetchval("SELECT status FROM chat.tickets WHERE id = $1", tid)
    assert status == "awaiting"


async def test_reclassify_audita_e_mantem_numero(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    tid = await _awaiting(db_conn, clock)
    number = await db_conn.fetchval("SELECT ticket_number FROM chat.tickets WHERE id = $1", tid)
    old = await ta.reclassify(db_conn, tid, "PRECO", actor="admin", clock=clock)
    assert old == "PRODUTO"
    row = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", tid)
    assert row["classification"] == "PRECO" and row["ticket_number"] == number
    assert row["status"] == "awaiting" and row["escalated"]
    logs = await _audit(db_conn, "ticket.reclassify")
    assert json.loads(logs[0]["old_value"]) == {"classification": "PRODUTO"}
    assert json.loads(logs[0]["new_value"]) == {"classification": "PRECO"}


@pytest.mark.parametrize("value", ["PRODUTO", "INVALIDO", ""])
async def test_reclassify_recusa_atual_ou_invalida(
    db_conn: asyncpg.Connection, clock: FakeClock, value: str
) -> None:
    tid = await _awaiting(db_conn, clock)
    with pytest.raises(ValidationFailure):
        await ta.reclassify(db_conn, tid, value, actor="admin", clock=clock)
    assert await _audit(db_conn, "ticket.reclassify") == []


async def test_set_urgency_audita_e_continua_escalado(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    tid = await _awaiting(db_conn, clock)
    before = await _side_effects(db_conn)
    old = await ta.set_urgency(db_conn, tid, "muito_urgente", actor="admin", clock=clock)
    assert old == "urgente"
    row = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", tid)
    assert row["urgency"] == "muito_urgente" and row["escalated"] and row["status"] == "awaiting"
    assert await _side_effects(db_conn) == before
    assert len(await _audit(db_conn, "ticket.urgency")) == 1
    await ta.set_urgency(db_conn, tid, "muito_urgente", actor="admin", clock=clock)
    assert len(await _audit(db_conn, "ticket.urgency")) == 1


async def test_set_urgency_valor_invalido_e_inexistente(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    tid = await _awaiting(db_conn, clock)
    with pytest.raises(ValidationFailure):
        await ta.set_urgency(db_conn, tid, "agora", actor="admin", clock=clock)
    with pytest.raises(ta.TicketNotFound):
        await ta.set_urgency(db_conn, 987654, "urgente", actor="admin", clock=clock)


async def test_actor_obrigatorio(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    tid = await _awaiting(db_conn, clock)
    with pytest.raises(ValidationFailure):
        await ta.conclude(db_conn, tid, "Ana", actor=" ", clock=clock)
