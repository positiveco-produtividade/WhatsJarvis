"""Acks de entrega e leitura gravados na saida (retorno-humano.1, RH-15; task RH-T05)."""

import logging

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services.inbound import InboundStatus
from tests.integration.test_pipeline_core import NOW, _seed_outbound, add_user, make_pipeline

pytestmark = pytest.mark.db


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


async def _acks(pool: asyncpg.Pool, out_id: int) -> tuple[object, object, str]:
    row = await pool.fetchrow(
        "SELECT delivered_at, read_at, status FROM chat.outbound_messages WHERE id = $1", out_id
    )
    assert row is not None
    return row["delivered_at"], row["read_at"], row["status"]


async def _seed(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        uid = await add_user(conn)
        _, out_id = await _seed_outbound(conn, uid)
    return out_id


async def test_entregue_e_lida_gravam_horario(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    out_id = await _seed(db_pool)
    pipe = make_pipeline(db_pool, clock)
    entregue_em = clock.now()
    assert await pipe.handle_status(InboundStatus("wamid.OUT", "delivered")) is None
    assert await _acks(db_pool, out_id) == (entregue_em, None, "sent")
    clock.advance(minutes=5)
    assert await pipe.handle_status(InboundStatus("wamid.OUT", "read")) is None
    assert await _acks(db_pool, out_id) == (entregue_em, clock.now(), "sent")


async def test_lida_sem_entregue_preenche_os_dois(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    out_id = await _seed(db_pool)
    await make_pipeline(db_pool, clock).handle_status(InboundStatus("wamid.OUT", "read"))
    assert await _acks(db_pool, out_id) == (clock.now(), clock.now(), "sent")


async def test_ack_atrasado_nao_muda_horario(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    """Ack repetido ou fora de ordem (entregue depois de lida) nunca apaga nem adianta."""
    out_id = await _seed(db_pool)
    pipe = make_pipeline(db_pool, clock)
    lida_em = clock.now()
    await pipe.handle_status(InboundStatus("wamid.OUT", "read"))
    clock.advance(minutes=10)
    await pipe.handle_status(InboundStatus("wamid.OUT", "delivered"))
    await pipe.handle_status(InboundStatus("wamid.OUT", "read"))
    assert await _acks(db_pool, out_id) == (lida_em, lida_em, "sent")


async def test_falha_continua_como_antes(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    out_id = await _seed(db_pool)
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_status(InboundStatus("wamid.OUT", "delivered"))
    await pipe.handle_status(InboundStatus("wamid.OUT", "failed", error_code=131047))
    delivered, read, status = await _acks(db_pool, out_id)
    assert (delivered, read, status) == (clock.now(), None, "failed")


async def test_sent_e_id_desconhecido_sem_efeito(
    db_pool: asyncpg.Pool, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    out_id = await _seed(db_pool)
    pipe = make_pipeline(db_pool, clock)
    caplog.set_level(logging.DEBUG)
    assert await pipe.handle_status(InboundStatus("wamid.OUT", "sent")) is None
    assert await pipe.handle_status(InboundStatus("wamid.NADA", "read")) is None
    assert await _acks(db_pool, out_id) == (None, None, "sent")
    assert "wamid.NADA" not in caplog.text and "wamid.OUT" not in caplog.text
