"""Editar uma pessoa (cadastro.1, CA-T02): `users.update` e `people_edit.edit_person`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.panel.services import people_edit
from app.services import users
from tests.brain import factories

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
ADMIN = "admin@example.test"
PII_NAME = "Ana Nome Sigiloso"
PII_COMPANY = "Empresa Sigilosa"
PII_MANAGER = "Gestor Sigiloso"


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://u:p@localhost/db_test",  # type: ignore[arg-type]
    )


async def _user(conn: asyncpg.Connection, *, status: str = "active", seq: int = 1) -> int:
    initial = "active" if status == "revoked" else status
    uid = await factories.create_active_user(
        conn, NOW - timedelta(days=1), seq=seq, brands=["jungle"], status=initial
    )
    if status == "revoked":
        await conn.execute(
            "UPDATE access.users SET status = 'revoked', revoked_at = $2, revoked_by = 'caio', "
            "revoked_reason = 'left_company' WHERE id = $1",
            uid, NOW,
        )  # fmt: skip
    return uid


async def _row(conn: asyncpg.Connection, uid: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
    assert row is not None
    return row


def _data(row: asyncpg.Record, **over: Any) -> users.ValidatedUser:
    base: dict[str, Any] = {
        users.KEY_NAME: row["name"],
        users.KEY_PHONE: row["phone_e164"],
        users.KEY_PROFILE: row["profile"],
        users.KEY_COMPANY: row["company"],
        users.KEY_UFS: list(row["ufs"]),
        users.KEY_CHANNELS: list(row["channels"]),
        users.KEY_BRANDS: list(row["brands"]),
        users.KEY_MANAGER: row["manager_name"],
    }
    base.update(over)
    checked = users.validate_row(base, now=NOW)
    assert not isinstance(checked, list), checked
    return checked


async def _edit(
    conn: asyncpg.Connection, uid: int, data: users.ValidatedUser, expected: asyncpg.Record | None
) -> people_edit.EditOutcome:
    version = users.data_version(expected) if expected is not None else None
    return await people_edit.edit_person(
        conn, uid, data, expected_version=version, admin_email=ADMIN,
        admin_name="Caio Franco", settings=_settings(), clock=FakeClock(NOW),
    )  # fmt: skip


async def _request(conn: asyncpg.Connection, uid: int, brand: str, number: str) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.tickets (ticket_number, user_id, kind, brand, message, status, "
            "escalated, urgency, escalation_reason, created_at, updated_at) "
            "VALUES ($1, $2, 'question', $3, 'Quero acesso', 'awaiting', true, 'urgente', "
            "'brand_access_request', $4, $4) RETURNING id",
            number, uid, brand, NOW - timedelta(hours=2),
        )
    )  # fmt: skip


async def test_edita_e_audita_sem_pii(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    before = await _row(db_conn, uid)
    data = _data(before, **{
        users.KEY_NAME: PII_NAME, users.KEY_COMPANY: PII_COMPANY, users.KEY_MANAGER: PII_MANAGER,
        users.KEY_PROFILE: "gestor_comercial", users.KEY_UFS: ["CE", "SP"],
    })  # fmt: skip
    out = await _edit(db_conn, uid, data, before)
    assert out.result.status == users.UPDATE_OK
    assert set(out.result.changed) == {"name", "company", "manager_name", "profile", "ufs"}
    after = await _row(db_conn, uid)
    assert (after["name"], after["profile"], sorted(after["ufs"])) == (
        PII_NAME, "gestor_comercial", ["CE", "SP"],
    )  # fmt: skip
    # CA-7: status, aceite e telefone intactos
    for col in ("status", "accepted_at", "accepted_notice_version", "phone_e164", "phone_key"):
        assert after[col] == before[col], col
    audit_row = await db_conn.fetchrow(
        "SELECT old_value::text AS o, new_value::text AS n FROM ops.audit_log "
        "WHERE action = 'user.update' AND target_id = $1", str(uid),
    )  # fmt: skip
    assert audit_row is not None
    dumped = audit_row["o"] + audit_row["n"]
    for pii in (PII_NAME, PII_COMPANY, PII_MANAGER, before["phone_e164"]):
        assert pii not in dumped
    new = json.loads(audit_row["n"])
    assert set(new["changed"]) == set(out.result.changed)
    assert new["profile"] == "gestor_comercial" and json.loads(audit_row["o"])["profile"]


async def test_sem_mudanca_nao_grava(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    row = await _row(db_conn, uid)
    out = await _edit(db_conn, uid, _data(row), row)
    assert out.result.status == users.UPDATE_NOOP
    assert (await _row(db_conn, uid))["updated_at"] == row["updated_at"]
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 0


async def test_edicao_concorrente_recusada(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    row = await _row(db_conn, uid)
    first = await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["RJ"]}), row)
    assert first.result.status == users.UPDATE_OK
    late = await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["MG"]}), row)
    assert late.result.status == users.UPDATE_CONFLICT
    assert late.result.current is not None and late.result.current["ufs"] == ("RJ",)
    assert list((await _row(db_conn, uid))["ufs"]) == ["RJ"]


@pytest.mark.parametrize("status", ["revoked"])
async def test_revogada_nao_edita(db_conn: asyncpg.Connection, status: str) -> None:
    uid = await _user(db_conn, status=status)
    row = await _row(db_conn, uid)
    out = await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["RJ"]}), row)
    assert out.result.status == users.UPDATE_NOT_EDITABLE


async def test_anonimizada_e_inexistente(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    row = await _row(db_conn, uid)
    await db_conn.execute(
        "UPDATE access.users SET phone_e164 = $2 WHERE id = $1", uid, f"anon:{uid}"
    )
    fresh = await _row(db_conn, uid)
    out = await _edit(db_conn, uid, _data(row), fresh)
    assert out.result.status == users.UPDATE_NOT_EDITABLE
    missing = await _edit(db_conn, 999_999, _data(row), fresh)
    assert missing.result.status == users.UPDATE_NOT_FOUND


async def test_declined_edita(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn, status="declined")
    row = await _row(db_conn, uid)
    out = await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["BA"]}), row)
    assert out.result.status == users.UPDATE_OK


async def test_marca_da_conversa_retirada_e_apagada(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    await db_conn.execute(
        "UPDATE access.users SET brands = '{jungle,zaya}', active_brand = 'zaya', "
        "active_brand_at = $2 WHERE id = $1", uid, NOW,
    )  # fmt: skip
    row = await _row(db_conn, uid)
    await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["CE"]}), row)
    assert (await _row(db_conn, uid))["active_brand"] == "zaya"  # marca continua: mantida
    row = await _row(db_conn, uid)
    await _edit(db_conn, uid, _data(row, **{users.KEY_BRANDS: ["jungle"]}), row)
    after = await _row(db_conn, uid)
    assert after["active_brand"] is None and after["active_brand_at"] is None


async def test_marca_pedida_conclui_e_avisa(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    t_old = await _request(db_conn, uid, "zaya", "J-0901")
    t_new = await _request(db_conn, uid, "zaya", "J-0902")
    other = await _request(db_conn, uid, "possible", "J-0903")  # marca nao liberada
    row = await _row(db_conn, uid)
    out = await _edit(
        db_conn, uid, _data(row, **{users.KEY_BRANDS: ["jungle", "zaya"]}), row
    )
    assert out.result.granted == ("zaya",)
    assert set(out.concluded_tickets) == {t_old, t_new}
    statuses = dict(await db_conn.fetch("SELECT id, status FROM chat.tickets"))
    assert statuses[t_old] == statuses[t_new] == "answered_human"
    assert statuses[other] == "awaiting"
    (oid,) = out.outbound_ids
    msg = await db_conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", oid)
    assert msg["kind"] == "brand_access_granted" and msg["status"] == "pending"
    assert msg["body"] == "Seu acesso a Zaya foi liberado. Pode perguntar!"
    assert msg["ticket_id"] == t_old or msg["ticket_id"] == t_new
    audit_actions = [r["action"] for r in await db_conn.fetch("SELECT action FROM ops.audit_log")]
    assert audit_actions.count("ticket.conclude") == 2 and "user.update" in audit_actions


async def test_marca_sem_pedido_nao_avisa(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn)
    row = await _row(db_conn, uid)
    out = await _edit(
        db_conn, uid, _data(row, **{users.KEY_BRANDS: ["jungle", "zaya"]}), row
    )
    assert out.result.granted == ("zaya",) and out.outbound_ids == ()
    assert await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


async def test_pendente_conclui_pedido_sem_mensagem(db_conn: asyncpg.Connection) -> None:
    uid = await _user(db_conn, status="pending_optin")
    ticket = await _request(db_conn, uid, "zaya", "J-0904")
    row = await _row(db_conn, uid)
    out = await _edit(
        db_conn, uid, _data(row, **{users.KEY_BRANDS: ["jungle", "zaya"]}), row
    )
    assert out.concluded_tickets == (ticket,) and out.outbound_ids == ()


async def test_lacos_de_fundo_nao_geram_conflito(db_conn: asyncpg.Connection) -> None:
    """Revisao 2026-11-19: lembrete/limite mexem em updated_at, nao nos dados editaveis."""
    uid = await _user(db_conn)
    row = await _row(db_conn, uid)
    await db_conn.execute(
        "UPDATE access.users SET rate_limit_notice_at = $2, updated_at = $2 WHERE id = $1",
        uid, NOW + timedelta(minutes=5),
    )  # fmt: skip
    out = await _edit(db_conn, uid, _data(row, **{users.KEY_UFS: ["RJ"]}), row)
    assert out.result.status == users.UPDATE_OK


async def test_troca_pelo_id_recusa_quem_foi_retirado(db_conn: asyncpg.Connection) -> None:
    """Revisao 2026-11-19: retirada depois que o modal abriu nao reativa ninguem."""
    uid = await _user(db_conn, status="revoked")
    with pytest.raises(InvalidValue) as exc:
        await users.swap_number_by_id(
            db_conn, uid, "+5585988887777", ADMIN, settings=_settings(), clock=FakeClock(NOW)
        )
    assert exc.value.field == "from"
    assert await db_conn.fetchval("SELECT count(*) FROM access.users") == 1
