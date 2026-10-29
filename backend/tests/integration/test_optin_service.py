"""services/optin: aviso, aceite, recusa e lembrete (AC-17..AC-21, AC-23, base de AC-35)."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services import optin

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CLOCK = FakeClock(NOW)


@dataclass(frozen=True)
class Target:
    id: int
    phone_e164: str
    name: str


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "welcome_template_name": "boas_vindas",
        "notice_version": "v-teste",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


SETTINGS = make_settings()


async def add_user(
    conn: asyncpg.Connection, n: int = 1, status: str = "pending_optin"
) -> Target:
    phone = f"+55119000000{n:02d}"
    accepted = "$2, 'antiga'" if status == "active" else "NULL, NULL"
    user_id: int = await conn.fetchval(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
        "brands, manager_name, status, status_changed_at, accepted_at, accepted_notice_version, "
        "created_at, created_by, updated_at) "
        f"VALUES ('Fulana', $1, $3, 'vendedor', 'Acme', '{{SP}}', '{{farma}}', '{{jungle}}', "
        f"'Gestora', '{status}', $2, {accepted}, $2, 'teste', $2) RETURNING id",
        phone,
        NOW,
        f"5511000000{n:02d}",
    )
    return Target(id=user_id, phone_e164=phone, name="Fulana")


async def user_row(conn: asyncpg.Connection, user: Target) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", user.id)
    assert row is not None
    return row


async def notice_status(conn: asyncpg.Connection, notice_id: int) -> str:
    status: str = await conn.fetchval(
        "SELECT status FROM access.optin_requests WHERE id = $1", notice_id
    )
    return status


async def outbound(conn: asyncpg.Connection, user: Target) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM chat.outbound_messages WHERE user_id = $1 ORDER BY id", user.id
    )


@pytest.mark.db
async def test_issue_notice_abre_aviso_e_enfileira_modelo(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    issued = await optin.issue_notice(db_conn, user, "cli:caio", settings=SETTINGS, clock=CLOCK)
    row = await db_conn.fetchrow(
        "SELECT * FROM access.optin_requests WHERE id = $1", issued.notice_id
    )
    assert row is not None
    assert row["status"] == "open" and row["notice_version"] == "v-teste"
    assert row["issued_by"] == "cli:caio" and row["issued_at"] == NOW
    (out,) = await outbound(db_conn, user)
    assert out["id"] == issued.outbound_id
    assert out["kind"] == "welcome_template" and out["required_status"] == "pending_optin"
    assert out["body"] is None  # nunca texto livre (AC-17)
    assert out["optin_request_id"] == issued.notice_id
    tpl = await db_conn.fetchval(
        "SELECT template::text FROM chat.outbound_messages WHERE id = $1", out["id"]
    )
    assert f"OPTIN_ACCEPT:{issued.notice_id}" in tpl
    assert f"OPTIN_DECLINE:{issued.notice_id}" in tpl
    assert "boas_vindas" in tpl and "Fulana" in tpl


@pytest.mark.db
async def test_novo_aviso_marca_anterior_superseded(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    first = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    second = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    assert await notice_status(db_conn, first.notice_id) == "superseded"
    assert await notice_status(db_conn, second.notice_id) == "open"


@pytest.mark.db
async def test_aceite_grava_data_versao_bsuid_e_confirmacao(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    issued = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    later = NOW + timedelta(minutes=5)
    out_id = await optin.accept(
        db_conn, user, issued.notice_id, "BR.123", later,
        settings=SETTINGS, wa_message_id="wamid.A", clock=CLOCK,
    )  # fmt: skip
    assert out_id is not None
    row = await user_row(db_conn, user)
    assert row["status"] == "active" and row["accepted_at"] == later
    assert row["accepted_notice_version"] == "v-teste" and row["bsuid"] == "BR.123"
    notice = await db_conn.fetchrow(
        "SELECT * FROM access.optin_requests WHERE id = $1", issued.notice_id
    )
    assert notice is not None
    assert notice["status"] == "accepted" and notice["responded_at"] == later
    assert notice["responded_wa_message_id"] == "wamid.A"
    conf = (await outbound(db_conn, user))[-1]
    assert conf["id"] == out_id and conf["kind"] == "optin_confirmation"
    assert conf["required_status"] == "active" and conf["body"] == SETTINGS.text_optin_confirmed
    audit = await db_conn.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'user.optin_accepted'"
    )
    assert audit == 1


@pytest.mark.db
async def test_aceite_sem_bsuid_mantem_o_existente(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    await db_conn.execute("UPDATE access.users SET bsuid = 'BR.OLD' WHERE id = $1", user.id)
    issued = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    await optin.accept(db_conn, user, issued.notice_id, None, NOW, settings=SETTINGS, clock=CLOCK)
    assert (await user_row(db_conn, user))["bsuid"] == "BR.OLD"


@pytest.mark.db
async def test_botao_de_aviso_antigo_nao_vale(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    old = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    new = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    assert await optin.accept(db_conn, user, old.notice_id, None, NOW, settings=SETTINGS) is None
    assert await optin.decline(db_conn, user, old.notice_id, NOW, settings=SETTINGS) is None
    assert (await user_row(db_conn, user))["status"] == "pending_optin"
    assert await notice_status(db_conn, new.notice_id) == "open"
    assert await notice_status(db_conn, old.notice_id) == "superseded"
    assert len(await outbound(db_conn, user)) == 2  # so as duas boas-vindas


@pytest.mark.db
async def test_botao_de_aviso_de_outro_usuario_nao_vale(db_conn: asyncpg.Connection) -> None:
    alice = await add_user(db_conn, 1)
    bia = await add_user(db_conn, 2)
    await optin.issue_notice(db_conn, alice, "cli", settings=SETTINGS, clock=CLOCK)
    bia_notice = await optin.issue_notice(db_conn, bia, "cli", settings=SETTINGS, clock=CLOCK)
    # Alice toca o botao do aviso da Bia
    stolen = await optin.accept(db_conn, alice, bia_notice.notice_id, None, NOW, settings=SETTINGS)
    assert stolen is None
    assert (await user_row(db_conn, alice))["status"] == "pending_optin"
    assert (await user_row(db_conn, bia))["status"] == "pending_optin"
    assert await notice_status(db_conn, bia_notice.notice_id) == "open"


@pytest.mark.db
async def test_aviso_inexistente_nao_vale(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    assert await optin.accept(db_conn, user, 999999, None, NOW, settings=SETTINGS) is None


@pytest.mark.db
async def test_aceite_repetido_nao_duplica(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    issued = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    first = await optin.accept(db_conn, user, issued.notice_id, None, NOW, settings=SETTINGS)
    again = await optin.accept(db_conn, user, issued.notice_id, None, NOW, settings=SETTINGS)
    assert first is not None and again is None
    assert len(await outbound(db_conn, user)) == 2  # boas-vindas + 1 confirmacao


@pytest.mark.db
async def test_recusa(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    issued = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    later = NOW + timedelta(minutes=1)
    out_id = await optin.decline(
        db_conn, user, issued.notice_id, later, settings=SETTINGS, clock=CLOCK
    )
    assert out_id is not None
    row = await user_row(db_conn, user)
    assert row["status"] == "declined" and row["declined_at"] == later
    assert row["accepted_at"] is None
    assert await notice_status(db_conn, issued.notice_id) == "declined"
    out = (await outbound(db_conn, user))[-1]
    assert out["kind"] == "optin_declined" and out["required_status"] == "declined"
    assert out["body"] == SETTINGS.text_optin_declined


@pytest.mark.db
async def test_usuario_ja_ativo_ou_recusado_nao_muda(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn, 1, status="active")
    await db_conn.execute(
        "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, issued_by) "
        "VALUES ($1, 'open', 'v', $2, 'x')",
        user.id,
        NOW,
    )
    notice_id = await db_conn.fetchval("SELECT id FROM access.optin_requests")
    assert await optin.decline(db_conn, user, notice_id, NOW, settings=SETTINGS) is None
    assert (await user_row(db_conn, user))["status"] == "active"


@pytest.mark.db
async def test_reativacao_com_novo_aceite(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    first = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    await optin.decline(db_conn, user, first.notice_id, NOW, settings=SETTINGS)
    # reativacao (feita por users.resend): volta a pending_optin e novo aviso
    await db_conn.execute(
        "UPDATE access.users SET status = 'pending_optin', declined_at = NULL WHERE id = $1",
        user.id,
    )
    second = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    assert await optin.accept(db_conn, user, first.notice_id, None, NOW, settings=SETTINGS) is None
    later = NOW + timedelta(days=3)
    assert await optin.accept(db_conn, user, second.notice_id, None, later, settings=SETTINGS)
    row = await user_row(db_conn, user)
    assert row["status"] == "active" and row["accepted_at"] == later


@pytest.mark.db
async def test_lembrete_no_maximo_um_por_24h(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    issued = await optin.issue_notice(db_conn, user, "cli", settings=SETTINGS, clock=CLOCK)
    first = await optin.maybe_remind(db_conn, user, NOW, settings=SETTINGS, clock=CLOCK)
    assert first is not None
    early = await optin.maybe_remind(
        db_conn, user, NOW + timedelta(hours=23), settings=SETTINGS
    )
    assert early is None
    again = await optin.maybe_remind(
        db_conn, user, NOW + timedelta(hours=24), settings=SETTINGS, clock=CLOCK
    )
    assert again is not None
    reminders = [o for o in await outbound(db_conn, user) if o["kind"] == "optin_reminder"]
    assert len(reminders) == 2
    assert reminders[0]["optin_request_id"] == issued.notice_id
    assert reminders[0]["body"] == SETTINGS.text_optin_reminder
    assert reminders[0]["required_status"] == "pending_optin"
    stored = (await user_row(db_conn, user))["optin_reminder_at"]
    assert stored == NOW + timedelta(hours=24)


@pytest.mark.db
async def test_lembrete_so_para_pendente_com_aviso_aberto(db_conn: asyncpg.Connection) -> None:
    sem_aviso = await add_user(db_conn, 1)
    assert await optin.maybe_remind(db_conn, sem_aviso, NOW, settings=SETTINGS) is None
    ativo = await add_user(db_conn, 2, status="active")
    assert await optin.maybe_remind(db_conn, ativo, NOW, settings=SETTINGS) is None
    assert await outbound(db_conn, sem_aviso) == [] and await outbound(db_conn, ativo) == []


@pytest.mark.db
async def test_active_needs_acceptance_nunca_violado(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        issued = await optin.issue_notice(conn, user, "cli", settings=SETTINGS, clock=CLOCK)
        await optin.accept(conn, user, issued.notice_id, None, NOW, settings=SETTINGS)
        rows = await conn.fetch(
            "SELECT 1 FROM access.users WHERE status = 'active' "
            "AND (accepted_at IS NULL OR accepted_notice_version IS NULL)"
        )
        assert rows == []
        # falha no meio (bsuid duplicado) desfaz tudo: o usuario nunca fica ativo sem aceite
        other = await add_user(conn, 2)
        await conn.execute("UPDATE access.users SET bsuid = 'BR.DUP' WHERE id = $1", user.id)
        other_notice = await optin.issue_notice(conn, other, "cli", settings=SETTINGS, clock=CLOCK)
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await optin.accept(
                    conn, other, other_notice.notice_id, "BR.DUP", NOW, settings=SETTINGS
                )
        assert (await user_row(conn, other))["status"] == "pending_optin"
        assert await notice_status(conn, other_notice.notice_id) == "open"
