"""services/users: resend e swap_number (AC-35, AC-26, AC-19)."""
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import DuplicatePhone, InvalidValue
from app.core.values import RevokeReason
from app.services import optin, users
from app.services.users import UserAlreadyActive, ValidatedUser

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CLOCK = FakeClock(NOW)
PHONE_A = "+5511900000001"
PHONE_B = "+5511900000002"


def make_settings() -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "welcome_template_name": "boas_vindas",
        "notice_version": "v-teste",
    }
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


SETTINGS = make_settings()


def make_data(n: int = 1) -> ValidatedUser:
    return ValidatedUser(
        name="Fulana",
        phone_e164=f"+55119000000{n:02d}",
        phone_key=f"5511000000{n:02d}",
        profile="vendedor",
        company="Acme",
        ufs=("SP", "RJ"),
        channels=("farma",),
        brands=("jungle", "zaya"),
        manager_name="Gestora",
    )


async def reg(conn: asyncpg.Connection, n: int = 1) -> users.UserRow:
    return await users.register(
        conn, make_data(n), actor="cli:caio", settings=SETTINGS, clock=CLOCK
    )


async def fetch_user(conn: asyncpg.Connection, user_id: int) -> users.UserRow:
    row = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", user_id)
    assert row is not None
    return users._to_user_row(row)


async def activate(conn: asyncpg.Connection, user: users.UserRow) -> users.UserRow:
    notice_id = await conn.fetchval(
        "SELECT id FROM access.optin_requests WHERE user_id = $1 AND status = 'open'", user.id
    )
    out = await optin.accept(conn, user, notice_id, None, NOW, settings=SETTINGS, clock=CLOCK)
    assert out is not None
    return await fetch_user(conn, user.id)


async def resend(conn: asyncpg.Connection, user_id: int, **kw: Any) -> users.UserRow:
    return await users.resend(
        conn, user_id, "cli:caio", settings=SETTINGS, clock=FakeClock(LATER), **kw
    )


async def notices(conn: asyncpg.Connection, user_id: int) -> list[tuple[int, str]]:
    rows = await conn.fetch(
        "SELECT id, status FROM access.optin_requests WHERE user_id = $1 ORDER BY id", user_id
    )
    return [(r["id"], r["status"]) for r in rows]


async def totals(conn: asyncpg.Connection) -> tuple[int, int, int]:
    return (
        await conn.fetchval("SELECT count(*) FROM ops.audit_log"),
        await conn.fetchval("SELECT count(*) FROM chat.outbound_messages"),
        await conn.fetchval("SELECT count(*) FROM access.users"),
    )


@pytest.mark.db
async def test_resend_de_revogado_reativa_sem_acesso_ate_novo_aceite(
    db_conn: asyncpg.Connection,
) -> None:
    user = await activate(db_conn, await reg(db_conn))
    assert user.accepted_at == NOW
    await users.revoke(db_conn, user.id, RevokeReason.LEFT_COMPANY, "cli:ana", clock=CLOCK)
    old_notice = (await notices(db_conn, user.id))[0][0]

    again = await resend(db_conn, user.id)
    assert again.status == "pending_optin" and again.accepted_at is None
    assert again.revoked_at is None and again.revoked_by is None and again.revoked_reason is None
    assert again.status_changed_at == LATER and again.valid_until is None
    row = await db_conn.fetchrow(
        "SELECT accepted_notice_version, optin_reminder_at FROM access.users WHERE id = $1",
        user.id,
    )
    assert row is not None and row["accepted_notice_version"] is None
    assert row["optin_reminder_at"] is None

    # botao do aviso antigo nao vale; so o aviso novo ativa
    stale = await optin.accept(
        db_conn, again, old_notice, None, LATER, settings=SETTINGS, clock=CLOCK
    )
    assert stale is None
    assert (await fetch_user(db_conn, user.id)).status == "pending_optin"
    new_notice = (await notices(db_conn, user.id))[-1][0]
    fresh = await optin.accept(
        db_conn, again, new_notice, None, LATER, settings=SETTINGS, clock=CLOCK
    )
    assert fresh is not None
    final = await fetch_user(db_conn, user.id)
    assert final.status == "active" and final.accepted_at == LATER


@pytest.mark.db
async def test_resend_supersede_aviso_anterior_e_enfileira_modelo(
    db_conn: asyncpg.Connection,
) -> None:
    user = await reg(db_conn)
    await resend(db_conn, user.id)
    states = [s for _, s in await notices(db_conn, user.id)]
    assert states == ["superseded", "open"]
    outs = await db_conn.fetch(
        "SELECT kind, required_status, body, optin_request_id FROM chat.outbound_messages "
        "WHERE user_id = $1 ORDER BY id",
        user.id,
    )
    assert [o["kind"] for o in outs] == ["welcome_template", "welcome_template"]
    assert outs[1]["required_status"] == "pending_optin" and outs[1]["body"] is None
    assert outs[1]["optin_request_id"] == (await notices(db_conn, user.id))[-1][0]


@pytest.mark.db
async def test_resend_de_declinado_volta_a_pending_optin(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    notice_id = (await notices(db_conn, user.id))[0][0]
    declined = await optin.decline(
        db_conn, user, notice_id, NOW, settings=SETTINGS, clock=CLOCK
    )
    assert declined is not None
    assert (await fetch_user(db_conn, user.id)).status == "declined"
    again = await resend(db_conn, user.id)
    assert again.status == "pending_optin"
    declined_at = await db_conn.fetchval(
        "SELECT declined_at FROM access.users WHERE id = $1", user.id
    )
    assert declined_at is None


@pytest.mark.db
async def test_resend_audita_estado_antigo_sem_pii(db_conn: asyncpg.Connection) -> None:
    user = await activate(db_conn, await reg(db_conn))
    await users.revoke(db_conn, user.id, RevokeReason.OTHER, "cli:ana", clock=CLOCK)
    await resend(db_conn, user.id, actor_type="panel")
    audit = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'user.resent'")
    assert audit is not None and audit["actor_type"] == "panel"
    old, new = audit["old_value"], audit["new_value"]
    assert '"revoked_by": "cli:ana"' in old and '"revoked_reason": "other"' in old
    assert NOW.isoformat() in old  # accepted_at e revoked_at antigos preservados
    assert '"status": "revoked"' in old and '"status": "pending_optin"' in new
    assert PHONE_A not in old and PHONE_A not in new


@pytest.mark.db
async def test_resend_de_active_recusa_sem_efeitos(db_conn: asyncpg.Connection) -> None:
    user = await activate(db_conn, await reg(db_conn))
    before = (await notices(db_conn, user.id), await totals(db_conn))
    with pytest.raises(UserAlreadyActive) as exc:
        await resend(db_conn, user.id)
    assert isinstance(exc.value, InvalidValue) and "ativo" in str(exc.value)
    assert (await notices(db_conn, user.id), await totals(db_conn)) == before
    assert (await fetch_user(db_conn, user.id)).status == "active"


@pytest.mark.db
async def test_resend_de_inexistente(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(InvalidValue):
        await resend(db_conn, 987654)


@pytest.mark.db
async def test_swap_number_revoga_a_cria_b_com_mesmos_dados(db_conn: asyncpg.Connection) -> None:
    a = await activate(db_conn, await reg(db_conn, 1))
    revoked, b = await users.swap_number(
        db_conn, "+55 (11) 90000-0001", PHONE_B, "cli:caio", settings=SETTINGS, clock=CLOCK
    )
    assert revoked.id == a.id and revoked.status == "revoked"
    assert revoked.revoked_reason == "number_change" and revoked.revoked_by == "cli:caio"
    assert b.id != a.id and b.status == "pending_optin" and b.accepted_at is None
    assert b.phone_e164 == PHONE_B and b.phone_key == "551100000002"
    assert (b.name, b.profile, b.company, b.ufs, b.channels, b.brands, b.manager_name) == (
        a.name, a.profile, a.company, a.ufs, a.channels, a.brands, a.manager_name,
    )  # fmt: skip
    assert (await notices(db_conn, b.id))[-1][1] == "open"
    out = await db_conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE user_id = $1", b.id)
    assert out is not None and out["kind"] == "welcome_template"
    assert out["to_phone"] == PHONE_B
    actions = {r["action"] for r in await db_conn.fetch("SELECT action FROM ops.audit_log")}
    assert {"user.revoked", "user.registered", "user.number_swapped"} <= actions


@pytest.mark.db
async def test_swap_number_b_duplicado_desfaz_revogacao_de_a(db_conn: asyncpg.Connection) -> None:
    a = await activate(db_conn, await reg(db_conn, 1))
    await reg(db_conn, 2)
    before = await totals(db_conn)
    with pytest.raises(DuplicatePhone):
        await users.swap_number(
            db_conn, PHONE_A, PHONE_B, "cli:caio", settings=SETTINGS, clock=CLOCK
        )
    still = await fetch_user(db_conn, a.id)
    assert still.status == "active" and still.revoked_at is None
    assert await totals(db_conn) == before


@pytest.mark.db
async def test_swap_number_a_inexistente_ou_telefone_invalido(
    db_conn: asyncpg.Connection,
) -> None:
    with pytest.raises(InvalidValue):
        await users.swap_number(db_conn, PHONE_A, PHONE_B, "cli", settings=SETTINGS, clock=CLOCK)
    await reg(db_conn, 1)
    with pytest.raises(InvalidValue):
        await users.swap_number(db_conn, PHONE_A, "123", "cli", settings=SETTINGS, clock=CLOCK)
    assert await db_conn.fetchval("SELECT count(*) FROM access.users") == 1


@pytest.mark.db
async def test_swap_number_painel_em_transacao_do_chamador(db_conn: asyncpg.Connection) -> None:
    await reg(db_conn, 1)
    await reg(db_conn, 2)
    async with db_conn.transaction():
        with pytest.raises(DuplicatePhone):
            await users.swap_number(
                db_conn, PHONE_A, PHONE_B, "painel:ana", actor_type="panel",
                settings=SETTINGS, clock=CLOCK,
            )  # fmt: skip
        # a transacao do chamador segue utilizavel (o savepoint foi desfeito)
        _, b = await users.swap_number(
            db_conn, PHONE_A, "+5511900000003", "painel:ana", actor_type="panel",
            settings=SETTINGS, clock=CLOCK,
        )  # fmt: skip
    assert b.status == "pending_optin"
    swapped = await db_conn.fetchrow(
        "SELECT actor_type FROM ops.audit_log WHERE action = 'user.number_swapped'"
    )
    assert swapped is not None and swapped["actor_type"] == "panel"


@pytest.mark.db
async def test_resend_recusa_quem_teve_os_dados_excluidos(db_conn: asyncpg.Connection) -> None:
    """Depois da exclusao LGPD (nome/telefone = anon:<id>) nao ha para onde mandar o aviso."""
    user = await reg(db_conn)
    await users.revoke(db_conn, user.id, RevokeReason("other"), "cli:caio", clock=CLOCK)
    anon = f"{users.ANONYMIZED_PREFIX}{user.id}"
    await db_conn.execute(
        "UPDATE access.users SET name = $2, phone_e164 = $2, phone_key = $2 WHERE id = $1",
        user.id,
        anon,
    )
    before = await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages")
    with pytest.raises(InvalidValue):
        await resend(db_conn, user.id)
    assert await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == before
