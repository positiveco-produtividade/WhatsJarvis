"""Toque de avaliacao no pipeline: fora do limite de 60/h, rotulos (CB-22/25/29). SINTETICO."""
from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import MessageOutcome, rate_down_payload, rate_up_payload
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from app.services.question_tickets import create_question_ticket
from tests.brain import factories

pytestmark = pytest.mark.db

PHONE = "5511910000001"


def make_pipeline(pool: asyncpg.Pool, clock: FakeClock, **over: Any) -> Pipeline:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    base.update(over)
    settings = Settings(_env_file=None, **base)  # type: ignore[call-arg]
    return Pipeline(pool, settings, Flags(pool, clock), clock)


def button(wamid: str, payload: str) -> InboundMessage:
    return InboundMessage(
        wamid, Sender(wa_id=PHONE), ContentKind.BUTTON, button=ButtonReply(payload)
    )


def text(wamid: str) -> InboundMessage:
    return InboundMessage(
        wamid, Sender(wa_id=PHONE), ContentKind.TEXT, text="oi", text_length=2
    )


@pytest.fixture(autouse=True)
async def _match_key(db_conn: asyncpg.Connection, brain_user: int) -> None:
    """A fabrica nao normaliza `phone_key`; o pipeline casa pela chave normalizada."""
    await db_conn.execute(
        "UPDATE access.users SET phone_key = $2 WHERE id = $1",
        brain_user, normalize_phone("+" + PHONE).match_key,
    )


async def answered_ticket(
    pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int
) -> int:
    trace = await factories.create_pending_trace(conn, user, clock.now(), wa_message_id="wamid.Q")
    t = await create_question_ticket(
        conn, user_id=user, trace_id=trace, message="Qual o sabor?", flags=Flags(pool, clock),
        clock=clock, classification="PRODUTO", brand=factories.BRAND_A,
    )
    return t.id


async def tap_trace(conn: asyncpg.Connection, wamid: str) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = $1", wamid)
    assert row is not None
    return row


async def test_up_grava_e_nao_conta_no_limite(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await answered_ticket(db_pool, db_conn, clock, brain_user)
    outcome = await make_pipeline(db_pool, clock).handle_message(
        button("wamid.UP", rate_up_payload(tid))
    )
    assert outcome == MessageOutcome()
    row = await tap_trace(db_conn, "wamid.UP")
    assert row["decision_reason"] == "rating_up" and row["counted_in_limit"] is False
    assert row["decision"] == "ignored_type"
    assert await db_conn.fetchval("SELECT rating FROM chat.answer_ratings") == "up"


async def test_down_agradece_e_devolve_saida(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await answered_ticket(db_pool, db_conn, clock, brain_user)
    outcome = await make_pipeline(db_pool, clock).handle_message(
        button("wamid.DN", rate_down_payload(tid))
    )
    assert len(outcome.outbound_ids) == 1 and outcome.answer_request is None
    row = await tap_trace(db_conn, "wamid.DN")
    assert row["decision_reason"] == "rating_down" and row["counted_in_limit"] is False
    out = await db_conn.fetchrow(
        "SELECT kind, inbound_trace_id FROM chat.outbound_messages WHERE id = $1",
        outcome.outbound_ids[0],
    )
    assert out is not None and out["kind"] == "brain_notice"
    assert out["inbound_trace_id"] == row["id"]


async def test_usuario_no_limite_ainda_avalia(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await answered_ticket(db_pool, db_conn, clock, brain_user)
    pipe = make_pipeline(db_pool, clock, rate_limit_per_hour=1)
    await pipe.handle_message(text("wamid.T1"))
    blocked = await pipe.handle_message(text("wamid.T2"))
    assert (await tap_trace(db_conn, "wamid.T2"))["decision"] == "rate_limited"
    assert blocked.answer_request is None
    await pipe.handle_message(button("wamid.DN", rate_down_payload(tid)))
    row = await tap_trace(db_conn, "wamid.DN")
    assert row["decision_reason"] == "rating_down" and row["counted_in_limit"] is False
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 1


async def test_segundo_toque_e_ignorado_sem_saida(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await answered_ticket(db_pool, db_conn, clock, brain_user)
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(button("wamid.UP", rate_up_payload(tid)))
    outcome = await pipe.handle_message(button("wamid.DN", rate_down_payload(tid)))
    assert outcome == MessageOutcome()
    row = await tap_trace(db_conn, "wamid.DN")
    assert row["decision_reason"] is None and row["counted_in_limit"] is False
    assert row["decision"] == "ignored_type"
    assert await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


@pytest.mark.parametrize("payload", ["RATE_UP:999999", "OPTIN_ACCEPT:1", "qualquer"])
async def test_botao_nao_reconhecido_segue_ignorado(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int,
    payload: str,
) -> None:
    outcome = await make_pipeline(db_pool, clock).handle_message(button("wamid.X", payload))
    assert outcome == MessageOutcome()
    row = await tap_trace(db_conn, "wamid.X")
    assert row["decision"] == "ignored_type" and row["decision_reason"] is None
    assert row["counted_in_limit"] is False
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 0


async def test_revogado_fica_em_silencio(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await answered_ticket(db_pool, db_conn, clock, brain_user)
    await db_conn.execute("UPDATE access.users SET status = 'revoked', revoked_at = now(), "
                          "revoked_by = 'teste' WHERE id = $1", brain_user)
    outcome = await make_pipeline(db_pool, clock).handle_message(
        button("wamid.UP", rate_up_payload(tid))
    )
    assert outcome == MessageOutcome()
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 0
