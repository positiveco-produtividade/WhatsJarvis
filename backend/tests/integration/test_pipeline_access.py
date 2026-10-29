"""Aceite via pipeline: acesso e silencio (AC-10/11/12/14/22/27).

Ponta a ponta contra o `Pipeline` real e Postgres real: desconhecido, revogado e recusado nao
geram resposta, alerta, entrega nem e-mail; so o registro para o painel.
"""
import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import MessageOutcome
from app.services import users
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
OTHER_PHONE = "5521987654321"
SECRET_TEXT = "texto-secreto-do-desconhecido"
SILENT_TABLES = ("ops.alerts", "ops.deliveries", "chat.outbound_messages", "chat.tickets")


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def make_pipeline(pool: asyncpg.Pool, clock: FakeClock, **over: Any) -> Pipeline:
    return Pipeline(pool, make_settings(**over), Flags(pool, clock), clock)


def msg(
    wamid: str,
    kind: ContentKind = ContentKind.TEXT,
    text: str = SECRET_TEXT,
    wa_id: str | None = PHONE,
    bsuid: str | None = None,
    payload: str | None = None,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=wa_id, bsuid=bsuid),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=text if is_text else None,
        text_length=len(text) if is_text else None,
        mime_type="audio/ogg" if kind is ContentKind.AUDIO else None,
        button=ButtonReply(payload) if payload is not None else None,
    )


async def add_user(conn: asyncpg.Connection, status: str, phone: str = "+" + PHONE) -> int:
    num = normalize_phone(phone)
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version, revoked_at, "
            "revoked_by, declined_at) VALUES ('Ana', $1, $2, 'vendedor', 'Acme', "
            "ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], 'Gestora', $3, $4, $4, 'teste', $4, "
            "CASE WHEN $3 = 'active' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'active' THEN 'v1' END, "
            "CASE WHEN $3 = 'revoked' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'revoked' THEN 'teste' END, "
            "CASE WHEN $3 = 'declined' THEN $4::timestamptz END) RETURNING id",
            num.phone_e164,
            num.match_key,
            status,
            NOW,
        )
    )


async def count(conn: asyncpg.Connection, table: str, where: str = "true") -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


async def assert_silent(conn: asyncpg.Connection) -> None:
    """AC-12: nenhum alerta, entrega (grupo ou e-mail), saida ou ticket."""
    for table in SILENT_TABLES:
        assert await count(conn, table) == 0, table


# ---- AC-10: desconhecido, 1a tentativa -------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        ContentKind.TEXT,
        ContentKind.AUDIO,
        ContentKind.IMAGE,
        ContentKind.BUTTON,
        ContentKind.REACTION,
    ],
)
async def test_desconhecido_qualquer_tipo_silencio_e_registro(
    db_pool: asyncpg.Pool, clock: FakeClock, kind: ContentKind, caplog: pytest.LogCaptureFixture
) -> None:
    channel = FakeChannel()
    pipe = make_pipeline(db_pool, clock)
    with caplog.at_level(logging.DEBUG):
        outcome = await pipe.handle_message(msg("w1", kind, payload="OPTIN_ACCEPT:1"))
    assert outcome == MessageOutcome()  # nada a despachar: nada sai
    assert channel.calls == []
    async with db_pool.acquire() as conn:
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["reason"] == "unknown" and att["attempt_count"] == 1
        assert att["first_attempt_at"] == NOW and att["last_attempt_at"] == NOW
        assert att["first_msg_type"] == kind.value
        tr = await conn.fetchrow("SELECT * FROM chat.message_trace")
        assert tr is not None and tr["decision"] == "unknown" and tr["body"] is None
        assert tr["attempt_id"] == att["id"] and not tr["counted_in_limit"]
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "access.unknown_first_messages") == 0  # chave desligada
        await assert_silent(conn)
    assert SECRET_TEXT not in caplog.text and PHONE not in caplog.text


async def test_primeira_mensagem_de_texto_guardada_so_com_a_chave_ligada(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    await make_pipeline(db_pool, clock, unknown_first_text_store=True).handle_message(msg("w1"))
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT body FROM access.unknown_first_messages") == SECRET_TEXT
        assert await conn.fetchval("SELECT first_msg_chars FROM access.access_attempts") == len(
            SECRET_TEXT
        )
        assert await conn.fetchval("SELECT body FROM chat.message_trace") is None
        await assert_silent(conn)


async def test_primeira_mensagem_de_midia_guarda_tipo_e_mime_nunca_corpo(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock, unknown_first_text_store=True)
    await pipe.handle_message(msg("w1", ContentKind.AUDIO))
    async with db_pool.acquire() as conn:
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None
        assert att["first_msg_type"] == "audio" and att["first_msg_mime"] == "audio/ogg"
        assert await count(conn, "access.unknown_first_messages") == 0
        await assert_silent(conn)


# ---- AC-11 / AC-12: tentativas seguintes -----------------------------------------------


async def test_tentativas_repetidas_so_atualizam_contador_sem_alerta(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    """Sem limiar de 3 tentativas (AC-12): 6 tentativas, zero alerta/entrega/saida."""
    pipe = make_pipeline(db_pool, clock, unknown_first_text_store=True)
    for i in range(1, 7):
        outcome = await pipe.handle_message(msg(f"w{i}", text=f"texto-{i}"))
        assert outcome == MessageOutcome()
        clock.advance(minutes=5)
    async with db_pool.acquire() as conn:
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["attempt_count"] == 6
        assert att["first_attempt_at"] == NOW
        assert att["last_attempt_at"] == NOW.replace(minute=25)
        # o texto da 1a mensagem nunca e sobrescrito e os demais nao sao guardados
        assert await conn.fetchval("SELECT body FROM access.unknown_first_messages") == "texto-1"
        assert await count(conn, "access.unknown_first_messages") == 1
        assert await count(conn, "chat.message_trace") == 6
        assert await count(conn, "chat.message_trace", "body IS NOT NULL") == 0
        await assert_silent(conn)


async def test_nova_tentativa_reabre_o_pedido_ignorado_no_painel(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(msg("w1"))
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE access.access_attempts SET dismissed_at = $1", NOW)
    clock.advance(minutes=1)
    await pipe.handle_message(msg("w2"))
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT dismissed_at FROM access.access_attempts") is None
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 2


# ---- AC-14: so BSUID -------------------------------------------------------------------


async def test_remetente_so_com_bsuid_sem_cadastro_e_desconhecido(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    for wamid in ("w1", "w2"):
        outcome = await pipe.handle_message(msg(wamid, wa_id=None, bsuid="BR.so-bsuid"))
        assert outcome == MessageOutcome()
    async with db_pool.acquire() as conn:
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["bsuid"] == "BR.so-bsuid" and att["phone_key"] is None
        assert att["reason"] == "unknown" and att["attempt_count"] == 2
        assert await conn.fetchval("SELECT decision FROM chat.message_trace LIMIT 1") == "unknown"
        await assert_silent(conn)


# ---- AC-27 / AC-12: revogado e recusado -------------------------------------------------


@pytest.mark.parametrize("status", ["revoked", "declined"])
@pytest.mark.parametrize(
    "kind", [ContentKind.TEXT, ContentKind.AUDIO, ContentKind.BUTTON, ContentKind.REACTION]
)
async def test_revogado_ou_recusado_silencio_com_motivo_sem_corpo(
    db_pool: asyncpg.Pool, clock: FakeClock, status: str, kind: ContentKind
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status)
    pipe = make_pipeline(db_pool, clock, unknown_first_text_store=True)
    for i in range(4):
        outcome = await pipe.handle_message(msg(f"w{i}", kind, payload="OPTIN_ACCEPT:1"))
        assert outcome == MessageOutcome()
    async with db_pool.acquire() as conn:
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["reason"] == status and att["user_id"] == uid
        assert att["attempt_count"] == 4
        assert await count(conn, "access.unknown_first_messages") == 0
        assert await count(conn, "chat.message_trace", f"decision = '{status}'") == 4
        assert await count(conn, "chat.message_trace", "body IS NOT NULL") == 0
        assert await count(conn, "chat.message_trace", "counted_in_limit") == 0
        assert await conn.fetchval("SELECT status FROM access.users") == status
        await assert_silent(conn)


# ---- AC-22: revogacao vale para a mensagem seguinte --------------------------------------


async def test_mensagem_seguinte_a_revogacao_ja_e_bloqueada(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, "active")
    pipe = make_pipeline(db_pool, clock)
    before = await pipe.handle_message(msg("antes", text="oi"))
    assert before.answer_request is not None
    async with db_pool.acquire() as conn, conn.transaction():
        await users.revoke(conn, uid, "left_company", "teste", clock=clock)
    clock.advance(seconds=1)  # bem menos que os 60 s do AC: sem cache de status
    after = await pipe.handle_message(msg("depois", text="outra"))
    assert after == MessageOutcome()
    async with db_pool.acquire() as conn:
        tr = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = 'depois'")
        assert tr is not None and tr["decision"] == "revoked" and tr["body"] is None
        assert not tr["counted_in_limit"]
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["reason"] == "revoked" and att["user_id"] == uid
        assert await count(conn, "ops.alerts") == 0
        assert await count(conn, "chat.outbound_messages") == 0


async def test_statuses_nao_geram_tentativa_de_acesso(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    from app.services.inbound import InboundStatus

    pipe = make_pipeline(db_pool, clock)
    for state in ("sent", "delivered", "read", "failed"):
        assert await pipe.handle_status(InboundStatus("wamid.desconhecido", state)) is None
    async with db_pool.acquire() as conn:
        assert await count(conn, "access.access_attempts") == 0
        assert await count(conn, "chat.message_trace") == 0
        await assert_silent(conn)


async def test_tentativas_de_numeros_diferentes_ficam_separadas(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(msg("a1"))
    await pipe.handle_message(msg("b1", wa_id=OTHER_PHONE))
    await pipe.handle_message(msg("a2"))
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT attempt_count FROM access.access_attempts")
        counts = sorted(r["attempt_count"] for r in rows)
        assert counts == [1, 2]
        await assert_silent(conn)
