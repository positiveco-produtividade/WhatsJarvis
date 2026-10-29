"""Alertas (R3): criacao, entregas, email=False, grupo ligado/desligado, dedup, payload sem PII."""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services.alerts import raise_alert, validate_payload
from app.services.flags import Flags

pytestmark = pytest.mark.db


def make_settings(configured: bool = True, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    if configured:
        base.update(
            resend_api_key=SecretStr("re_key"),
            resend_from_email="jarvis@example.com",
            alert_recipient_email=SecretStr("destino@example.com"),
        )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _deliveries(conn: asyncpg.Connection, alert_id: int) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(
        "SELECT * FROM ops.deliveries WHERE source_type = 'alert' AND source_id = $1", alert_id
    )
    return {r["channel"]: r for r in rows}


async def _raise(conn: asyncpg.Connection, **kw: Any) -> int | None:
    args: dict[str, Any] = {"settings": make_settings(), "clock": FakeClock()}
    args.update(kw)
    return await raise_alert(
        conn, "rate_limit", "warn", "Limite por hora atingido.", {"count": 61}, **args
    )


async def test_cria_alerta_e_duas_entregas(db_conn: asyncpg.Connection) -> None:
    alert_id = await _raise(db_conn)
    assert alert_id is not None
    row = await db_conn.fetchrow("SELECT * FROM ops.alerts WHERE id = $1", alert_id)
    assert row is not None
    assert row["kind"] == "rate_limit" and row["severity"] == "warn"
    assert json.loads(row["payload"]) == {"count": 61}
    deliveries = await _deliveries(db_conn, alert_id)
    assert set(deliveries) == {"group", "email"}
    assert deliveries["group"]["status"] == "panel_only"
    assert deliveries["email"]["status"] == "pending"
    assert deliveries["email"]["attempts"] == 0


async def test_email_false_cria_so_grupo(db_conn: asyncpg.Connection) -> None:
    alert_id = await _raise(db_conn, email=False)
    assert alert_id is not None
    assert set(await _deliveries(db_conn, alert_id)) == {"group"}


async def test_grupo_ligado_pending_e_desligado_panel_only(db_pool: asyncpg.Pool) -> None:
    flags_on = Flags(db_pool, FakeClock(), team_group_initial=True)
    flags_off = Flags(db_pool, FakeClock(), team_group_initial=False)
    async with db_pool.acquire() as conn:
        on = await _raise(conn, flags=flags_on)
        off = await _raise(conn, flags=flags_off)
        assert on is not None and off is not None
        assert (await _deliveries(conn, on))["group"]["status"] == "pending"
        assert (await _deliveries(conn, off))["group"]["status"] == "panel_only"


async def test_email_nao_configurado_fica_skipped(db_conn: asyncpg.Connection) -> None:
    for cfg in (
        make_settings(configured=False),
        make_settings(alert_recipient_email=SecretStr("")),
    ):
        alert_id = await _raise(db_conn, settings=cfg)
        assert alert_id is not None
        email = (await _deliveries(db_conn, alert_id))["email"]
        assert email["status"] == "skipped"
        assert email["last_error"] == "not_configured"


async def test_dedup_na_janela_devolve_none_sem_entrega(db_conn: asyncpg.Connection) -> None:
    clock = FakeClock()
    kw: dict[str, Any] = {
        "dedup_key": "rate_limit:7",
        "dedup_window": timedelta(hours=1),
        "clock": clock,
    }
    first = await _raise(db_conn, **kw)
    assert first is not None
    clock.advance(minutes=59)
    assert await _raise(db_conn, **kw) is None
    assert await db_conn.fetchval("SELECT count(*) FROM ops.alerts") == 1
    assert await db_conn.fetchval("SELECT count(*) FROM ops.deliveries") == 2
    clock.advance(minutes=2)
    second = await _raise(db_conn, **kw)
    assert second is not None and second != first
    assert await db_conn.fetchval("SELECT count(*) FROM ops.deliveries") == 4


async def test_dedup_key_diferente_nao_suprime(db_conn: asyncpg.Connection) -> None:
    window = timedelta(hours=1)
    assert await _raise(db_conn, dedup_key="a", dedup_window=window) is not None
    assert await _raise(db_conn, dedup_key="b", dedup_window=window) is not None


async def test_dedup_concorrente_cria_um_so(db_pool: asyncpg.Pool) -> None:
    async def one() -> int | None:
        async with db_pool.acquire() as conn, conn.transaction():
            return await _raise(
                conn, dedup_key="identity_conflict:x", dedup_window=timedelta(hours=24)
            )

    results = await asyncio.gather(*(one() for _ in range(4)))
    assert sum(r is not None for r in results) == 1


async def test_user_id_gravado(db_conn: asyncpg.Connection) -> None:
    user_id = await db_conn.fetchval(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels,"
        " brands, manager_name, status, status_changed_at, created_at, created_by, updated_at) "
        "VALUES ('x', '+5511999990000', '11999990000', 'vendedor', 'c', '{SP}', '{b2b}', '{lg}',"
        " 'm', 'pending_optin', now(), now(), 'teste', now()) RETURNING id"
    )
    alert_id = await _raise(db_conn, user_id=user_id)
    assert await db_conn.fetchval("SELECT user_id FROM ops.alerts WHERE id=$1", alert_id) == user_id


async def test_payload_com_pii_e_recusado(db_conn: asyncpg.Connection) -> None:
    bad: list[dict[str, Any]] = [
        {"phone": "x"},
        {"user_name": "A"},
        {"texto": "oi"},
        {"nested": {"bsuid": "1"}},
        {"k": "+55 11 99999-0000"},
        {"k": "a@b.com"},
        {"body": "oi"},
    ]
    for payload in bad:
        with pytest.raises(InvalidValue):
            await raise_alert(
                db_conn, "k", "warn", "msg", payload, settings=make_settings(), clock=FakeClock()
            )
    assert await db_conn.fetchval("SELECT count(*) FROM ops.alerts") == 0


async def test_severidade_e_mensagem_invalidas(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(InvalidValue):
        await raise_alert(db_conn, "k", "urgent", "msg", {}, settings=make_settings())
    with pytest.raises(InvalidValue):
        await raise_alert(
            db_conn, "k", "warn", "ligue +55 11 99999-0000", {}, settings=make_settings()
        )


async def test_log_sem_pii(db_conn: asyncpg.Connection, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.alerts"):
        await _raise(db_conn)
    assert any(r.message == "alert_created" for r in caplog.records)
    assert "destino@example.com" not in caplog.text


def test_validate_payload_aceita_ids_e_contagens() -> None:
    validate_payload({"user_id": 3, "count": 61, "window_minutes": 60, "origin": {"attempt_id": 9}})
