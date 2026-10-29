"""Aceite via pipeline: limite de 60 mensagens/hora (AC-29), incluindo concorrencia.

Regra (decisao do Caio, 2026-10-26): o limite conta as mensagens aceitas de usuario ativo;
reacao e tipos ignorados ficam fora.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import MessageOutcome
from app.services import outbox
from app.services.flags import Flags
from app.services.inbound import ContentKind, InboundMessage, InboundStatus, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
OTHER_PHONE = "5521987654321"
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
    wa_id: str = PHONE,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=wa_id),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text="texto-do-usuario" if is_text else None,
        text_length=len("texto-do-usuario") if is_text else None,
    )


async def add_active_user(conn: asyncpg.Connection, phone: str = "+" + PHONE) -> int:
    num = normalize_phone(phone)
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


async def seed_counted(
    conn: asyncpg.Connection, uid: int, n: int, at: datetime, tag: str = "seed"
) -> None:
    await conn.execute(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, counted_in_limit) "
        "SELECT $4::text || '.' || g, $2, 'text', $1, 'allowed', 'allowed', true "
        "FROM generate_series(1, $3::int) g",
        uid,
        at,
        n,
        tag,
    )


async def count(conn: asyncpg.Connection, table: str, where: str = "true") -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


async def counted(conn: asyncpg.Connection) -> int:
    return await count(conn, "chat.message_trace", "counted_in_limit")


def passed(outcomes: list[MessageOutcome]) -> int:
    return sum(1 for o in outcomes if o.answer_request is not None)


# ---- 60 passam, a 61a nao ----------------------------------------------------------------


async def test_sessenta_passam_e_a_61a_e_bloqueada_sequencial(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_active_user(conn)
    pipe = make_pipeline(db_pool, clock)
    outcomes = [await pipe.handle_message(msg(f"w{i}")) for i in range(1, 61)]
    assert passed(outcomes) == 60
    blocked = await pipe.handle_message(msg("w61"))
    assert blocked.answer_request is None
    async with db_pool.acquire() as conn:
        assert await counted(conn) == 60
        row = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = 'w61'")
        assert row is not None and row["decision"] == "rate_limited" and row["body"] is None
        assert not row["counted_in_limit"]


# ---- 61a simultanea -----------------------------------------------------------------------


@pytest.mark.parametrize(("seeded", "expected_pass"), [(59, 1), (58, 2), (56, 4)])
async def test_mensagens_simultaneas_nao_ultrapassam_o_limite(
    db_pool: asyncpg.Pool, clock: FakeClock, seeded: int, expected_pass: int
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, seeded, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    outcomes = list(
        await asyncio.gather(*(pipe.handle_message(msg(f"c{i}")) for i in range(CONCURRENCY)))
    )
    assert passed(outcomes) == expected_pass
    async with db_pool.acquire() as conn:
        assert await counted(conn) <= 60
        assert await counted(conn) == seeded + expected_pass
        blocked = CONCURRENCY - expected_pass
        assert await count(conn, "chat.message_trace", "decision = 'rate_limited'") == blocked


async def test_bloqueio_simultaneo_gera_um_aviso_e_um_alerta(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    outcomes = list(
        await asyncio.gather(*(pipe.handle_message(msg(f"c{i}")) for i in range(CONCURRENCY)))
    )
    assert passed(outcomes) == 0
    assert sum(len(o.outbound_ids) for o in outcomes) == 1  # aviso unico por janela
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.outbound_messages", "kind = 'rate_limit_notice'") == 1
        assert await count(conn, "ops.alerts", "kind = 'rate_limit'") == 1
        assert await count(conn, "ops.deliveries", "channel = 'email'") == 1
        assert await count(conn, "chat.message_trace", "decision = 'rate_limited'") == CONCURRENCY
        assert await counted(conn) == 60


# ---- aviso unico, alerta 1/h ---------------------------------------------------------------


async def test_aviso_enviado_uma_vez_e_texto_correto(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    channel = FakeChannel()
    for i in range(5):
        outcome = await pipe.handle_message(msg(f"b{i}"))
        for out_id in outcome.outbound_ids:
            await outbox.dispatch(db_pool, out_id, channel=channel, clock=clock)
        clock.advance(minutes=2)
    assert [c.body for c in channel.calls] == [make_settings().text_rate_limit]
    async with db_pool.acquire() as conn:
        assert await count(conn, "ops.alerts") == 1


async def test_nova_janela_novo_aviso_e_novo_alerta(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=1))
    pipe = make_pipeline(db_pool, clock)
    assert len((await pipe.handle_message(msg("x1"))).outbound_ids) == 1
    clock.advance(minutes=30)
    assert await pipe.handle_message(msg("x2")) == MessageOutcome()  # mesma janela
    clock.advance(minutes=31)  # 61 min depois do primeiro aviso
    async with db_pool.acquire() as conn:
        await seed_counted(conn, uid, 60, clock.now() - timedelta(minutes=1), "seed2")
    assert len((await pipe.handle_message(msg("x3"))).outbound_ids) == 1
    async with db_pool.acquire() as conn:
        assert await count(conn, "ops.alerts") == 2
        assert await count(conn, "chat.outbound_messages", "kind = 'rate_limit_notice'") == 2


# ---- janela deslizante ----------------------------------------------------------------------


async def test_bloqueadas_nao_prolongam_a_janela(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=59))
    pipe = make_pipeline(db_pool, clock)
    for i in range(3):
        assert (await pipe.handle_message(msg(f"blk{i}"))).answer_request is None
    async with db_pool.acquire() as conn:
        assert await counted(conn) == 60  # bloqueadas nao entram na contagem
    clock.advance(minutes=2)  # as 60 do seed saem da janela de 60 min
    assert (await pipe.handle_message(msg("depois"))).answer_request is not None


async def test_mensagens_antigas_fora_da_janela_nao_contam(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=61))
    outcome = await make_pipeline(db_pool, clock).handle_message(msg("novo"))
    assert outcome.answer_request is not None


async def test_limite_e_por_usuario(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await add_active_user(conn, "+" + OTHER_PHONE)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=5))
    pipe = make_pipeline(db_pool, clock)
    assert (await pipe.handle_message(msg("a"))).answer_request is None
    assert (await pipe.handle_message(msg("b", wa_id=OTHER_PHONE))).answer_request is not None


# ---- o que nao conta --------------------------------------------------------------------------


async def test_duplicatas_nao_contam_para_o_limite(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 59, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    assert (await pipe.handle_message(msg("m60"))).answer_request is not None
    for _ in range(5):
        assert await pipe.handle_message(msg("m60")) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await counted(conn) == 60
        assert await count(conn, "ops.alerts") == 0
        assert await count(conn, "chat.outbound_messages") == 0
    blocked = await pipe.handle_message(msg("m61"))  # so agora a 61a distinta e bloqueada
    assert blocked.answer_request is None and len(blocked.outbound_ids) == 1


async def test_statuses_nao_contam_para_o_limite(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 59, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    for state in ("sent", "delivered", "read", "failed"):
        assert await pipe.handle_status(InboundStatus("wamid.qualquer", state)) is None
    async with db_pool.acquire() as conn:
        assert await counted(conn) == 59
    assert (await pipe.handle_message(msg("m60"))).answer_request is not None


async def test_reacao_de_ativo_nao_conta_no_limite(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    """Decisao do Caio (2026-10-26): reacao nao gasta o limite de 60/h."""
    async with db_pool.acquire() as conn:
        uid = await add_active_user(conn)
        await seed_counted(conn, uid, 59, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    for i in range(5):
        assert await pipe.handle_message(msg(f"r{i}", ContentKind.REACTION)) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await counted(conn) == 59
    assert (await pipe.handle_message(msg("t1"))).answer_request is not None
