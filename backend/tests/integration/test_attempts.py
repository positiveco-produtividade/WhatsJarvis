"""attempts.record/dismiss: contador, 1a mensagem so com a chave, midia sem corpo (AC-10..12/27)."""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services import attempts

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
KEY = "551187654321"  # numero ficticio


async def row(conn: asyncpg.Connection, attempt_id: int) -> asyncpg.Record:
    r = await conn.fetchrow("SELECT * FROM access.access_attempts WHERE id = $1", attempt_id)
    assert r is not None
    return r


async def stored(conn: asyncpg.Connection) -> list[str]:
    return [r["body"] for r in await conn.fetch("SELECT body FROM access.unknown_first_messages")]


async def test_primeira_tentativa_cria_linha_sem_guardar_texto_por_padrao(
    db_conn: asyncpg.Connection,
) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text",
        now=NOW, text="oi, quero acesso",
    )
    assert (res.is_first, res.attempt_count, res.text_stored) == (True, 1, False)
    r = await row(db_conn, res.attempt_id)
    assert r["first_msg_type"] == "text" and r["first_msg_chars"] == 16
    assert r["first_attempt_at"] == NOW and r["dismissed_at"] is None
    assert await stored(db_conn) == []


async def test_texto_guardado_so_com_a_chave_ligada(db_conn: asyncpg.Connection) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text",
        now=NOW, text="primeira", store_first_text=True,
    )
    assert res.text_stored is True
    assert await stored(db_conn) == ["primeira"]


async def test_texto_da_primeira_nunca_e_sobrescrito(db_conn: asyncpg.Connection) -> None:
    for i, body in enumerate(["primeira", "segunda", "terceira"]):
        res = await attempts.record(
            db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text",
            now=NOW + timedelta(minutes=i), text=body, store_first_text=True,
        )
    assert res.attempt_count == 3 and res.text_stored is False
    assert await stored(db_conn) == ["primeira"]
    r = await row(db_conn, res.attempt_id)
    assert r["last_attempt_at"] == NOW + timedelta(minutes=2)
    assert r["first_attempt_at"] == NOW


async def test_reenvio_com_chave_ligada_nao_guarda_se_primeira_foi_midia(
    db_conn: asyncpg.Connection,
) -> None:
    await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="image",
        now=NOW, mime="image/jpeg", store_first_text=True,
    )
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text",
        now=NOW, text="depois", store_first_text=True,
    )
    assert res.text_stored is False and await stored(db_conn) == []


async def test_midia_guarda_tipo_e_mime_sem_corpo(db_conn: asyncpg.Connection) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="audio",
        now=NOW, text="ignorado", mime="audio/ogg", store_first_text=True,
    )
    r = await row(db_conn, res.attempt_id)
    assert (r["first_msg_type"], r["first_msg_mime"], r["first_msg_chars"]) == (
        "audio", "audio/ogg", None,
    )
    assert res.text_stored is False and await stored(db_conn) == []


@pytest.mark.parametrize("reason", ["revoked", "declined"])
async def test_revogado_e_recusado_registram_motivo_sem_corpo(
    db_conn: asyncpg.Connection, reason: str
) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason=reason, msg_type="text",
        now=NOW, text="oi", store_first_text=True,
    )
    assert (await row(db_conn, res.attempt_id))["reason"] == reason
    assert await stored(db_conn) == []


async def test_ignorada_reabre_com_nova_tentativa(db_conn: asyncpg.Connection) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text", now=NOW
    )
    assert await attempts.dismiss(db_conn, res.attempt_id, "caio", clock=FakeClock(NOW))
    assert (await row(db_conn, res.attempt_id))["dismissed_at"] == NOW
    await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text", now=NOW
    )
    assert (await row(db_conn, res.attempt_id))["dismissed_at"] is None


async def test_dismiss_audita_e_e_idempotente(db_conn: asyncpg.Connection) -> None:
    res = await attempts.record(
        db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text", now=NOW
    )
    assert await attempts.dismiss(db_conn, res.attempt_id, "caio") is True
    assert await attempts.dismiss(db_conn, res.attempt_id, "caio") is False
    assert await attempts.dismiss(db_conn, 999999, "caio") is False
    n = await db_conn.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'access_attempt.dismiss'"
    )
    assert n == 1


async def test_identifica_por_bsuid_e_completa_o_telefone(db_conn: asyncpg.Connection) -> None:
    first = await attempts.record(
        db_conn, phone_key=None, bsuid="BS.1", reason="unknown", msg_type="text", now=NOW
    )
    second = await attempts.record(
        db_conn, phone_key=KEY, bsuid="BS.1", reason="unknown", msg_type="text", now=NOW
    )
    assert second.attempt_id == first.attempt_id and second.attempt_count == 2
    assert (await row(db_conn, first.attempt_id))["phone_key"] == KEY
    assert await db_conn.fetchval("SELECT count(*) FROM access.access_attempts") == 1


async def test_nunca_gera_alerta_nem_entrega_nem_resposta(db_conn: asyncpg.Connection) -> None:
    for _ in range(5):
        await attempts.record(
            db_conn, phone_key=KEY, bsuid=None, reason="unknown", msg_type="text", now=NOW
        )
    for table in ("ops.alerts", "ops.deliveries", "chat.outbound_messages"):
        assert await db_conn.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_entradas_invalidas(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(InvalidValue):
        await attempts.record(
            db_conn, phone_key=KEY, bsuid=None, reason="x", msg_type="text", now=NOW
        )
    with pytest.raises(InvalidValue):
        await attempts.record(
            db_conn, phone_key=None, bsuid=None, reason="unknown", msg_type="text", now=NOW
        )
