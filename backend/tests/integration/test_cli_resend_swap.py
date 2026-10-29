"""CLI parte 2: user resend e user swap-number (AC-35, AC-26)."""
from datetime import UTC, datetime

import asyncpg
import pytest

from app import cli
from app.core.clock import FakeClock
from tests.integration.test_cli_users import PHONE, SETTINGS, add_args, count

CLOCK = FakeClock(datetime(2026, 10, 23, 12, 0, tzinfo=UTC))
PHONE_B = "+55 11 90000-0002"


async def go(pool: asyncpg.Pool, argv: list[str]) -> int:
    return await cli.run(argv, pool=pool, settings=SETTINGS, clock=CLOCK, wait_seconds=0)


async def status_of(pool: asyncpg.Pool, user_id: int) -> str:
    value: str = await pool.fetchval("SELECT status FROM access.users WHERE id = $1", user_id)
    return value


async def make_active(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        "UPDATE access.users SET status = 'active', accepted_at = now(), "
        "accepted_notice_version = 'v1' WHERE id = $1",
        user_id,
    )


@pytest.mark.db
async def test_resend_pending_por_id_e_por_telefone(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    capsys.readouterr()
    assert await go(db_pool, ["user", "resend", "--id", "1", "--by", "caio"]) == 0
    out = capsys.readouterr().out
    assert "***0001" in out and "na fila" in out and "pending_optin" in out
    assert "90000" not in out and "Fulana" not in out
    assert await go(db_pool, ["user", "resend", "--phone", PHONE]) == 0
    assert await count(db_pool, "chat.outbound_messages") == 3
    notices = await db_pool.fetch("SELECT status FROM access.optin_requests ORDER BY id")
    assert [r["status"] for r in notices].count("superseded") == 2
    audited = await db_pool.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'user.resent' AND actor = 'caio'"
    )
    assert audited == 1


@pytest.mark.db
async def test_resend_revogado_volta_a_pending_e_zera_aceite(db_pool: asyncpg.Pool) -> None:
    await go(db_pool, add_args())
    await make_active(db_pool, 1)
    await go(db_pool, ["user", "revoke", "--id", "1", "--reason", "other"])
    assert await go(db_pool, ["user", "resend", "--id", "1"]) == 0
    row = await db_pool.fetchrow(
        "SELECT status, accepted_at, revoked_at, revoked_reason FROM access.users WHERE id = 1"
    )
    assert row is not None and row["status"] == "pending_optin"
    assert row["accepted_at"] is None and row["revoked_at"] is None
    assert row["revoked_reason"] is None
    old = await db_pool.fetchval(
        "SELECT old_value->>'status' FROM ops.audit_log WHERE action = 'user.resent'"
    )
    assert old == "revoked"


@pytest.mark.db
async def test_resend_active_recusa_com_mensagem_clara(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    await make_active(db_pool, 1)
    capsys.readouterr()
    assert await go(db_pool, ["user", "resend", "--id", "1"]) == 1
    assert "ja esta ativa" in capsys.readouterr().err
    assert await status_of(db_pool, 1) == "active"
    assert await count(db_pool, "chat.outbound_messages") == 1


@pytest.mark.db
async def test_resend_inexistente_e_telefone_invalido(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, ["user", "resend", "--id", "99"]) == 1
    assert "nao encontrada" in capsys.readouterr().err
    assert await go(db_pool, ["user", "resend", "--phone", PHONE]) == 1
    assert await go(db_pool, ["user", "resend", "--phone", "abc"]) == 1
    assert await count(db_pool, "chat.outbound_messages") == 0


@pytest.mark.db
async def test_resend_resultado_do_envio_falho_sai_com_1(db_pool: asyncpg.Pool) -> None:
    await go(db_pool, add_args())
    original = cli._welcome_result

    async def failed(ctx: cli.Context, user_id: int) -> str:
        return "falhou: 131026"

    cli._welcome_result = failed  # type: ignore[assignment]
    try:
        assert await go(db_pool, ["user", "resend", "--id", "1"]) == 1
    finally:
        cli._welcome_result = original  # type: ignore[assignment]


@pytest.mark.db
async def test_swap_number_atomico_ok(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    capsys.readouterr()
    argv = ["user", "swap-number", "--from", PHONE, "--to", PHONE_B, "--by", "caio"]
    assert await go(db_pool, argv) == 0
    out = capsys.readouterr().out
    assert "***0001" in out and "***0002" in out and "na fila" in out
    assert "90000" not in out
    rows = await db_pool.fetch(
        "SELECT status, revoked_reason, name, profile FROM access.users ORDER BY id"
    )
    assert [r["status"] for r in rows] == ["revoked", "pending_optin"]
    assert rows[0]["revoked_reason"] == "number_change"
    assert rows[0]["name"] == rows[1]["name"] and rows[0]["profile"] == rows[1]["profile"]
    swapped = await db_pool.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'user.number_swapped'"
    )
    assert swapped == 1


@pytest.mark.db
async def test_swap_number_destino_duplicado_nao_altera_nada(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    await go(db_pool, add_args(phone=PHONE_B))
    capsys.readouterr()
    assert await go(db_pool, ["user", "swap-number", "--from", PHONE, "--to", PHONE_B]) == 1
    assert "ja cadastrado" in capsys.readouterr().err
    rows = await db_pool.fetch("SELECT status FROM access.users ORDER BY id")
    assert [r["status"] for r in rows] == ["pending_optin", "pending_optin"]
    assert await count(db_pool, "chat.outbound_messages") == 2


@pytest.mark.db
async def test_swap_number_origem_inexistente_e_telefone_invalido(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    capsys.readouterr()
    argv = ["user", "swap-number", "--from", PHONE_B, "--to", "+5511900000003"]
    assert await go(db_pool, argv) == 1
    assert "nao cadastrado" in capsys.readouterr().err
    assert await go(db_pool, ["user", "swap-number", "--from", PHONE, "--to", "abc"]) == 2
    assert await count(db_pool, "access.users") == 1
    assert await status_of(db_pool, 1) == "pending_optin"


async def test_uso_incorreto_sai_com_2() -> None:
    for argv in (["user", "resend"], ["user", "swap-number", "--from", PHONE]):
        with pytest.raises(SystemExit) as exc:
            await go(None, argv)  # type: ignore[arg-type]
        assert exc.value.code == 2
