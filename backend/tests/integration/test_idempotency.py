"""Aceite via pipeline: idempotencia por message_id, sequencial e simultanea (AC-7).

Duplicata = sem nova resposta, alerta, ticket, e-mail, sem incremento de limite ou de tentativas.
"""
import asyncio
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import MessageOutcome
from app.services.flags import Flags
from app.services.inbound import ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
CONCURRENCY = 8


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


def make_pipeline(pool: asyncpg.Pool, clock: FakeClock) -> Pipeline:
    return Pipeline(pool, make_settings(), Flags(pool, clock), clock)


def msg(
    wamid: str,
    kind: ContentKind = ContentKind.TEXT,
    text: str = "texto-do-usuario",
    wa_id: str = PHONE,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=wa_id),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=text if is_text else None,
        text_length=len(text) if is_text else None,
        mime_type="audio/ogg" if kind is ContentKind.AUDIO else None,
    )


async def add_active_user(conn: asyncpg.Connection) -> int:
    num = normalize_phone("+" + PHONE)
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version) VALUES ('Ana', $1, "
            "$2, 'vendedor', 'Acme', ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], 'Gestora', "
            "'active', $3, $3, 'teste', $3, $3, 'v1') RETURNING id",
            num.phone_e164,
            num.match_key,
            NOW,
        )
    )


async def count(conn: asyncpg.Connection, table: str, where: str = "true") -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


def effective(outcomes: list[MessageOutcome]) -> list[MessageOutcome]:
    return [o for o in outcomes if o != MessageOutcome()]


# ---- duplicata sequencial ----------------------------------------------------------------


async def test_duplicata_sequencial_de_texto_nao_repete_pedido_nem_limite(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(msg("w1"))
    assert first.answer_request is not None
    for _ in range(3):
        assert await pipe.handle_message(msg("w1")) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "chat.message_trace", "counted_in_limit") == 1
        for table in ("ops.alerts", "ops.deliveries", "chat.outbound_messages", "chat.tickets"):
            assert await count(conn, table) == 0, table


async def test_duplicata_sequencial_de_midia_nao_cria_segundo_ticket_nem_ack(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(msg("w1", ContentKind.AUDIO))
    assert len(first.outbound_ids) == 1 and len(first.delivery_ids) == 1
    for _ in range(3):
        assert await pipe.handle_message(msg("w1", ContentKind.AUDIO)) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "chat.tickets") == 1
        assert await count(conn, "chat.outbound_messages") == 1
        assert await count(conn, "ops.deliveries") == 1
        assert await count(conn, "ops.alerts") == 0


async def test_duplicata_de_sair_nao_reenvia_confirmacao(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(msg("w1", text="sair"))
    assert len(first.outbound_ids) == 1
    assert await pipe.handle_message(msg("w1", text="sair")) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.outbound_messages") == 1
        assert await count(conn, "chat.message_trace") == 1


async def test_duplicata_de_desconhecido_nao_incrementa_tentativas(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    for _ in range(3):
        await pipe.handle_message(msg("w1"))
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 1
        assert await count(conn, "chat.message_trace") == 1


async def test_message_ids_diferentes_nao_sao_confundidos(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    results = [await pipe.handle_message(msg(f"w{i}")) for i in range(3)]
    assert all(r.answer_request is not None for r in results)
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 3


# ---- duplicata simultanea ----------------------------------------------------------------


async def test_duplicata_simultanea_de_texto_um_rastro_um_efeito(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    outcomes = await asyncio.gather(*(pipe.handle_message(msg("w1")) for _ in range(CONCURRENCY)))
    winners = effective(list(outcomes))
    assert len(winners) == 1 and winners[0].answer_request is not None
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "chat.message_trace", "counted_in_limit") == 1
        assert await count(conn, "chat.message_trace", "decision = 'allowed'") == 1
        assert await count(conn, "ops.alerts") == 0


async def test_duplicata_simultanea_de_midia_um_ticket_um_ack_uma_entrega(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    outcomes = await asyncio.gather(
        *(pipe.handle_message(msg("w1", ContentKind.AUDIO)) for _ in range(CONCURRENCY))
    )
    winners = effective(list(outcomes))
    assert len(winners) == 1
    assert len(winners[0].outbound_ids) == 1 and len(winners[0].delivery_ids) == 1
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "chat.tickets") == 1
        assert await count(conn, "chat.outbound_messages") == 1
        assert await count(conn, "ops.deliveries") == 1
        assert await count(conn, "ops.alerts") == 0
        assert await count(conn, "ops.deliveries", "channel = 'email'") == 0


async def test_duplicata_simultanea_de_desconhecido_uma_tentativa(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    await asyncio.gather(*(pipe.handle_message(msg("w1")) for _ in range(CONCURRENCY)))
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 1


async def test_mensagens_distintas_simultaneas_do_desconhecido_nao_perdem_contagem(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    await asyncio.gather(*(pipe.handle_message(msg(f"w{i}")) for i in range(CONCURRENCY)))
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == CONCURRENCY
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == (
            CONCURRENCY
        )
        assert await count(conn, "access.access_attempts") == 1


async def test_mistura_de_duplicatas_e_novas_simultaneas(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    batch = [msg("a"), msg("a"), msg("b"), msg("b")]
    outcomes = await asyncio.gather(*(pipe.handle_message(m) for m in batch))
    assert len(effective(list(outcomes))) == 2
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 2
        assert await count(conn, "chat.message_trace", "counted_in_limit") == 2
