"""limits.check_and_count: 60 passam, a 61a e bloqueada, janela deslizante (AC-29)."""

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services import limits
from app.services.limits import rate_limit_notice_due

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


async def add_user(
    conn: asyncpg.Connection, phone_e164: str = "+5511987654321", key: str = "551187654321"
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, "
            "channels, brands, manager_name, status, status_changed_at, created_at, created_by, "
            "updated_at) VALUES ('Ana', $2, $3, 'vendedor', 'Acme', "
            "ARRAY['SP'], ARRAY['varejo'], ARRAY['X'], 'Gestora', 'pending_optin', $1, $1, "
            "'teste', $1) RETURNING id",
            NOW, phone_e164, key,
        )
    )


async def add_trace(
    conn: asyncpg.Connection, user_id: int, at: datetime, counted: bool = True
) -> None:
    await conn.execute(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, counted_in_limit) "
        "VALUES (gen_random_uuid()::text, $1, 'text', $2, 'allowed', 'x', $3)",
        at, user_id, counted,
    )


@pytest.mark.db
async def test_sessenta_passam_e_a_61a_e_bloqueada(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    for i in range(59):
        await add_trace(db_conn, uid, NOW - timedelta(minutes=50) + timedelta(seconds=i))
    res = await limits.check_and_count(db_conn, uid, NOW)
    assert (res.allowed, res.count) == (True, 59)  # a 60a ainda passa
    await add_trace(db_conn, uid, NOW)
    res = await limits.check_and_count(db_conn, uid, NOW)
    assert (res.allowed, res.count) == (False, 60)  # a 61a


@pytest.mark.db
async def test_fora_da_janela_libera_com_relogio_falso(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    clock = FakeClock(NOW)
    for _ in range(60):
        await add_trace(db_conn, uid, NOW)
    assert not (await limits.check_and_count(db_conn, uid, clock.now())).allowed
    clock.advance(minutes=59)
    assert not (await limits.check_and_count(db_conn, uid, clock.now())).allowed
    clock.advance(minutes=1, seconds=1)  # a janela deslizou
    res = await limits.check_and_count(db_conn, uid, clock.now())
    assert (res.allowed, res.count) == (True, 0)


@pytest.mark.db
async def test_bloqueadas_nao_contam_nem_prolongam_a_janela(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    for _ in range(60):
        await add_trace(db_conn, uid, NOW - timedelta(minutes=61))  # ja fora
    for m in range(5):
        await add_trace(db_conn, uid, NOW - timedelta(minutes=m), counted=False)
    res = await limits.check_and_count(db_conn, uid, NOW)
    assert (res.allowed, res.count) == (True, 0)


@pytest.mark.db
async def test_conta_so_o_proprio_usuario(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    other = await add_user(db_conn, "+5511912345678", "5511912345678")
    for _ in range(60):
        await add_trace(db_conn, other, NOW)
    assert (await limits.check_and_count(db_conn, uid, NOW)).allowed


@pytest.mark.db
async def test_limite_configuravel(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    for _ in range(3):
        await add_trace(db_conn, uid, NOW - timedelta(minutes=5))
    assert not (await limits.check_and_count(db_conn, uid, NOW, limit=3)).allowed
    assert (await limits.check_and_count(db_conn, uid, NOW, limit=3, window_minutes=2)).allowed


@pytest.mark.db
async def test_concorrencia_com_trava_da_linha_nao_passa_de_60(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as setup:
        uid = await add_user(setup)
        for _ in range(58):
            await add_trace(setup, uid, NOW - timedelta(minutes=10))

    async def one() -> bool:
        async with db_pool.acquire() as conn, conn.transaction():
            await conn.fetchval("SELECT id FROM access.users WHERE id = $1 FOR UPDATE", uid)
            res = await limits.check_and_count(conn, uid, NOW)
            await add_trace(conn, uid, NOW, counted=res.allowed)
            return res.allowed

    results = await asyncio.gather(*(one() for _ in range(5)))
    assert sum(results) == 2


def test_aviso_uma_vez_por_janela() -> None:
    assert rate_limit_notice_due(None, NOW)
    assert not rate_limit_notice_due(NOW - timedelta(minutes=59), NOW)
    assert rate_limit_notice_due(NOW - timedelta(minutes=60), NOW)
    assert rate_limit_notice_due(NOW - timedelta(hours=3), NOW)
