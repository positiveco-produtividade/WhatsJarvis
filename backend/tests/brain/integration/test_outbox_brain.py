"""Outbox do cerebro: kinds novos e envio com botoes (CB-24, CB-18, CB-11)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import OutboundKind, OutboundStatus
from app.services import outbox
from app.services.channel import ReplyButton
from app.services.flags import Flags
from app.services.reply_policy import FlagsReplyPolicy
from tests.brain import factories
from tests.fakes import FakeChannel

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
BUTTONS = [
    {"id": "RATE_UP:7", "title": "👍 Útil"},
    {"id": "RATE_DOWN:7", "title": "👎 Não útil"},
]
BRAIN_KINDS = (OutboundKind.BRAIN_ANSWER, OutboundKind.BRAIN_ANSWER_RATE, OutboundKind.BRAIN_NOTICE)


class Target:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.phone_e164 = "+551191000001"


async def _user(conn: asyncpg.Connection, status: str = "active") -> Target:
    return Target(await factories.create_active_user(conn, NOW, status=status))


async def _row(conn: asyncpg.Connection, out_id: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", out_id)
    assert row is not None
    return row


async def _run(pool: asyncpg.Pool, out_id: int, channel: FakeChannel, **kw: Any) -> Any:
    return await outbox.dispatch(pool, out_id, channel=channel, clock=FakeClock(NOW), **kw)


def _buttons_for(kind: OutboundKind) -> list[dict[str, str]] | None:
    return BUTTONS if kind is OutboundKind.BRAIN_ANSWER_RATE else None


@pytest.mark.parametrize("kind", BRAIN_KINDS)
def test_status_exigido_e_active(kind: OutboundKind) -> None:
    assert outbox.REQUIRED_STATUS[kind].value == "active"


@pytest.mark.db
async def test_enqueue_grava_botoes_como_json(db_conn: asyncpg.Connection) -> None:
    user = await _user(db_conn)
    out_id = await outbox.enqueue(
        db_conn, user, OutboundKind.BRAIN_ANSWER_RATE, body="Resposta", reply_buttons=BUTTONS
    )
    row = await _row(db_conn, out_id)
    assert json.loads(row["reply_buttons"]) == BUTTONS
    assert row["required_status"] == "active"


@pytest.mark.db
async def test_enqueue_sem_botoes_deixa_nulo(db_conn: asyncpg.Connection) -> None:
    user = await _user(db_conn)
    out_id = await outbox.enqueue(db_conn, user, OutboundKind.BRAIN_ANSWER, body="Parte 1")
    assert (await _row(db_conn, out_id))["reply_buttons"] is None


@pytest.mark.db
@pytest.mark.parametrize("kind", [OutboundKind.BRAIN_ANSWER, OutboundKind.BRAIN_NOTICE])
async def test_kinds_de_texto_saem_como_texto(db_pool: asyncpg.Pool, kind: OutboundKind) -> None:
    async with db_pool.acquire() as conn:
        user = await _user(conn)
        out_id = await outbox.enqueue(conn, user, kind, body="Parte 1")
        channel = FakeChannel()
        assert await _run(db_pool, out_id, channel) is OutboundStatus.SENT
    assert channel.methods == ["send_text"]


@pytest.mark.db
async def test_brain_answer_rate_sai_com_botoes(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await _user(conn)
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.BRAIN_ANSWER_RATE, body="Resposta", reply_buttons=BUTTONS
        )
        channel = FakeChannel()
        assert await _run(db_pool, out_id, channel) is OutboundStatus.SENT
    (call,) = channel.calls
    assert call.method == "send_reply_buttons" and call.body == "Resposta"
    assert call.buttons == (
        ReplyButton("RATE_UP:7", "👍 Útil"),
        ReplyButton("RATE_DOWN:7", "👎 Não útil"),
    )


async def _expect_invalid(
    pool: asyncpg.Pool, body: str | None, buttons: list[dict[str, str]] | None
) -> None:
    async with pool.acquire() as conn:
        user = await _user(conn)
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.BRAIN_ANSWER_RATE, body=body, reply_buttons=buttons
        )
        channel = FakeChannel()
        with pytest.raises(InvalidValue):
            await _run(pool, out_id, channel)
        row = await _row(conn, out_id)
    assert channel.calls == []
    assert row["status"] == "failed" and row["error_code"] == "invalid_content"


@pytest.mark.db
async def test_sem_botoes_falha_alto(db_pool: asyncpg.Pool) -> None:
    await _expect_invalid(db_pool, "Resposta", None)


@pytest.mark.db
async def test_lista_vazia_de_botoes_falha_alto(db_pool: asyncpg.Pool) -> None:
    await _expect_invalid(db_pool, "Resposta", [])


@pytest.mark.db
async def test_mais_de_tres_botoes_falha_alto(db_pool: asyncpg.Pool) -> None:
    many = [{"id": f"B:{i}", "title": f"Botao {i}"} for i in range(4)]
    await _expect_invalid(db_pool, "Resposta", many)


@pytest.mark.db
async def test_titulo_acima_de_20_falha_alto(db_pool: asyncpg.Pool) -> None:
    await _expect_invalid(db_pool, "Resposta", [{"id": "A", "title": "x" * 21}])


@pytest.mark.db
async def test_id_acima_de_256_falha_alto(db_pool: asyncpg.Pool) -> None:
    await _expect_invalid(db_pool, "Resposta", [{"id": "x" * 257, "title": "Ok"}])


@pytest.mark.db
async def test_corpo_acima_de_1024_falha_alto(db_pool: asyncpg.Pool) -> None:
    await _expect_invalid(db_pool, "a" * 1025, BUTTONS)


@pytest.mark.db
async def test_limites_exatos_passam(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await _user(conn)
        edge = [{"id": "x" * 256, "title": "t" * 20}]
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.BRAIN_ANSWER_RATE, body="a" * 1024, reply_buttons=edge
        )
        assert await _run(db_pool, out_id, FakeChannel()) is OutboundStatus.SENT


@pytest.mark.db
@pytest.mark.parametrize("kind", BRAIN_KINDS)
async def test_pausa_descarta_os_tres_kinds(db_pool: asyncpg.Pool, kind: OutboundKind) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    async with db_pool.acquire() as conn:
        await flags.set_bool(conn, "paused", True, "caio")
        user = await _user(conn)
        out_id = await outbox.enqueue(
            conn, user, kind, body="Oi", reply_buttons=_buttons_for(kind)
        )
        channel = FakeChannel()
        result = await _run(db_pool, out_id, channel, policy=FlagsReplyPolicy(flags))
        row = await _row(conn, out_id)
    assert result is OutboundStatus.DISCARDED
    assert row["discard_reason"] == "paused" and channel.calls == []


@pytest.mark.db
@pytest.mark.parametrize("kind", BRAIN_KINDS)
async def test_revogado_antes_do_envio_descarta(db_pool: asyncpg.Pool, kind: OutboundKind) -> None:
    async with db_pool.acquire() as conn:
        user = await _user(conn)
        out_id = await outbox.enqueue(
            conn, user, kind, body="Oi", reply_buttons=_buttons_for(kind)
        )
        await conn.execute(
            "UPDATE access.users SET status = 'revoked', revoked_at = $2, revoked_by = 'caio', "
            "revoked_reason = 'left_company' WHERE id = $1",
            user.id,
            NOW,
        )
        channel = FakeChannel()
        result = await _run(db_pool, out_id, channel)
        row = await _row(conn, out_id)
    assert result is OutboundStatus.DISCARDED
    assert row["discard_reason"] == "revoked_before_send" and channel.calls == []
