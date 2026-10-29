"""services/users: register e revoke (AC-15, AC-16, AC-17, AC-34, R2)."""
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import DuplicatePhone, InvalidValue
from app.core.values import RevokeReason
from app.services import users
from app.services.users import ValidatedUser

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CLOCK = FakeClock(NOW)


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
TABLES = ("access.users", "access.optin_requests", "chat.outbound_messages", "ops.audit_log")


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


async def count(conn: asyncpg.Connection, table: str) -> int:
    value: int = await conn.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
    return value


async def reg(conn: asyncpg.Connection, n: int = 1) -> users.UserRow:
    return await users.register(
        conn, make_data(n), actor="cli:caio", settings=SETTINGS, clock=CLOCK
    )


@pytest.mark.db
async def test_register_cria_usuario_aviso_saida_e_auditoria(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    assert user.status == "pending_optin" and user.valid_until is None
    assert user.ufs == ("SP", "RJ") and user.brands == ("jungle", "zaya")
    assert user.created_by == "cli:caio" and user.created_at == NOW
    assert user.accepted_at is None and user.revoked_at is None
    notice = await db_conn.fetchrow(
        "SELECT * FROM access.optin_requests WHERE user_id = $1", user.id
    )
    assert notice is not None
    assert notice["status"] == "open" and notice["notice_version"] == "v-teste"
    assert notice["issued_by"] == "cli:caio"
    out = await db_conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE user_id = $1", user.id)
    assert out is not None
    assert out["kind"] == "welcome_template" and out["required_status"] == "pending_optin"
    assert out["body"] is None and out["to_phone"] == user.phone_e164  # so modelo (AC-17)
    assert out["optin_request_id"] == notice["id"]
    audit = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'user.registered'")
    assert audit is not None
    assert audit["actor_type"] == "cli" and audit["actor"] == "cli:caio"
    assert audit["target_id"] == str(user.id)
    assert user.phone_e164 not in str(audit["new_value"])  # sem PII


@pytest.mark.db
async def test_register_ator_painel_e_enqueue_welcome_falso(db_conn: asyncpg.Connection) -> None:
    user = await users.register(
        db_conn, make_data(), actor="painel:ana", actor_type="panel", enqueue_welcome=False,
        clock=CLOCK,
    )  # fmt: skip
    assert user.status == "pending_optin"
    assert await count(db_conn, "access.optin_requests") == 0
    assert await count(db_conn, "chat.outbound_messages") == 0
    audit = await db_conn.fetchrow("SELECT actor_type FROM ops.audit_log")
    assert audit is not None and audit["actor_type"] == "panel"


@pytest.mark.db
async def test_register_exige_settings_para_boas_vindas(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(TypeError):
        await users.register(db_conn, make_data(), actor="cli")
    assert await count(db_conn, "access.users") == 0


@pytest.mark.db
async def test_duplicata_rejeitada_sem_efeitos(db_conn: asyncpg.Connection) -> None:
    await reg(db_conn)
    before = [
        await count(db_conn, t)
        for t in TABLES
    ]
    with pytest.raises(DuplicatePhone):
        await reg(db_conn)
    after = [
        await count(db_conn, t)
        for t in TABLES
    ]
    assert after == before


@pytest.mark.db
async def test_duplicata_de_revogado_tambem_rejeitada(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    await users.revoke(db_conn, user.id, RevokeReason.OTHER, "cli", clock=CLOCK)
    with pytest.raises(DuplicatePhone):
        await reg(db_conn)


@pytest.mark.db
async def test_duplicata_nao_aborta_transacao_do_chamador(db_conn: asyncpg.Connection) -> None:
    """Painel (so as validas): captura DuplicatePhone e segue gravando as demais."""
    await reg(db_conn, 1)
    async with db_conn.transaction():
        await reg(db_conn, 2)
        with pytest.raises(DuplicatePhone):
            await reg(db_conn, 1)
        await reg(db_conn, 3)
    assert await count(db_conn, "access.users") == 3


@pytest.mark.db
async def test_lote_com_erro_na_segunda_linha_nao_grava_nada(db_conn: asyncpg.Connection) -> None:
    """CLI (tudo ou nada): a excecao desfaz a transacao inteira (AC-16)."""
    await reg(db_conn, 1)
    before = await count(db_conn, "ops.audit_log")
    with pytest.raises(DuplicatePhone):
        async with db_conn.transaction():
            await reg(db_conn, 2)
            await reg(db_conn, 1)
    assert await count(db_conn, "access.users") == 1
    assert await count(db_conn, "chat.outbound_messages") == 1
    assert await count(db_conn, "access.optin_requests") == 1
    assert await count(db_conn, "ops.audit_log") == before


@pytest.mark.db
async def test_revoke_grava_autor_data_motivo_e_audita(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    later = FakeClock(NOW + timedelta(hours=1))
    revoked = await users.revoke(
        db_conn, user.id, RevokeReason.LEFT_COMPANY, "painel:ana", actor_type="panel", clock=later
    )
    assert revoked.status == "revoked" and revoked.revoked_by == "painel:ana"
    assert revoked.revoked_reason == "left_company"
    assert revoked.revoked_at == NOW + timedelta(hours=1)
    assert revoked.status_changed_at == NOW + timedelta(hours=1)
    row = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'user.revoked'")
    assert row is not None and row["actor_type"] == "panel" and row["target_id"] == str(user.id)


@pytest.mark.db
async def test_revoke_de_ativo_mantem_historico_de_aceite(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    await db_conn.execute(
        "UPDATE access.users SET status='active', accepted_at=$2, accepted_notice_version='v1' "
        "WHERE id=$1",
        user.id,
        NOW,
    )
    revoked = await users.revoke(db_conn, user.id, "partner_contract_end", "cli", clock=CLOCK)
    assert revoked.status == "revoked" and revoked.accepted_at == NOW
    audit = await db_conn.fetchval("SELECT old_value->>'status' FROM ops.audit_log "
                                   "WHERE action = 'user.revoked'")
    assert audit == "active"


@pytest.mark.db
async def test_revogar_duas_vezes_gera_uma_auditoria(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    first = await users.revoke(db_conn, user.id, RevokeReason.NUMBER_CHANGE, "cli:a", clock=CLOCK)
    second = await users.revoke(
        db_conn, user.id, RevokeReason.OTHER, "cli:b", clock=FakeClock(NOW + timedelta(days=1))
    )
    assert second == first  # motivo, autor e data originais
    n = await db_conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE action = 'user.revoked'")
    assert n == 1


@pytest.mark.db
async def test_motivo_invalido_recusado_sem_efeito(db_conn: asyncpg.Connection) -> None:
    user = await reg(db_conn)
    with pytest.raises(InvalidValue):
        await users.revoke(db_conn, user.id, "porque_sim", "cli", clock=CLOCK)
    status = await db_conn.fetchval("SELECT status FROM access.users WHERE id = $1", user.id)
    assert status == "pending_optin"
    n = await db_conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE action = 'user.revoked'")
    assert n == 0


@pytest.mark.db
async def test_revogar_usuario_inexistente(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(InvalidValue):
        await users.revoke(db_conn, 999999, RevokeReason.OTHER, "cli", clock=CLOCK)
