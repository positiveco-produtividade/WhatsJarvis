"""Escalado, midia e desconhecido NAO sao alerta (PN-44, PN-62, PN-67): zero alertas, zero e-mails.

Ponta a ponta pelo webhook real (`start_env`, Resend/WAHA por MockTransport) e pelas escritas
de tickets do painel. A contagem olha `ops.alerts` e as entregas de e-mail/alerta.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import tickets_admin as ta
from tests.integration.test_app_e2e import GROUP_ON, add_user, fixture_bytes, start_env, wait_for
from tests.panel import factories

pytestmark = pytest.mark.db

SETTLE_SECONDS = 0.4


async def _counts(pool: asyncpg.Pool) -> dict[str, int]:
    return {
        "alerts": int(await pool.fetchval("SELECT count(*) FROM ops.alerts")),
        "alert_deliveries": int(
            await pool.fetchval("SELECT count(*) FROM ops.deliveries WHERE source_type = 'alert'")
        ),
        "email_deliveries": int(
            await pool.fetchval("SELECT count(*) FROM ops.deliveries WHERE channel = 'email'")
        ),
    }


def _emails(env: Any) -> list[str]:
    return [str(c.url) for c in env.http_calls if "resend" in c.url.host]


ZERO = {"alerts": 0, "alert_deliveries": 0, "email_deliveries": 0}


async def test_midia_gera_ticket_escalado_mas_nenhum_alerta_nem_email(
    db_pool: asyncpg.Pool,
) -> None:
    await add_user(db_pool)
    env = start_env(**GROUP_ON)  # grupo ligado: ate o aviso ao grupo sai, e-mail nunca
    try:
        assert env.post(fixture_bytes("image")).status_code == 200

        async def sent() -> bool:
            return bool(await db_pool.fetchval("SELECT status FROM ops.deliveries") == "sent")

        await wait_for(sent)
        await asyncio.sleep(SETTLE_SECONDS)
        assert _emails(env) == []
    finally:
        env.client.__exit__(None, None, None)
    ticket = await db_pool.fetchrow("SELECT kind, escalated FROM chat.tickets")
    assert ticket is not None and ticket["kind"] == "media" and ticket["escalated"] is True
    assert await _counts(db_pool) == ZERO


async def test_midias_de_varios_tipos_nao_geram_alerta(db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    env = start_env()
    try:
        for name in ("image", "audio"):
            assert env.post(fixture_bytes(name)).status_code == 200
        await asyncio.sleep(SETTLE_SECONDS)
        assert _emails(env) == []
    finally:
        env.client.__exit__(None, None, None)
    assert await db_pool.fetchval("SELECT count(*) FROM chat.tickets") >= 1
    assert await _counts(db_pool) == ZERO


@pytest.mark.parametrize("fixture", ["text", "image", "audio", "bsuid_only"])
async def test_numero_desconhecido_nao_gera_alerta_nem_email(
    db_pool: asyncpg.Pool, fixture: str
) -> None:
    env = start_env(**GROUP_ON)
    try:
        assert env.post(fixture_bytes(fixture)).status_code == 200
        await asyncio.sleep(SETTLE_SECONDS)
        assert _emails(env) == [] and env.http_calls == []  # nem e-mail nem grupo
        assert env.channel.calls == []  # e nada ao remetente
    finally:
        env.client.__exit__(None, None, None)
    assert await db_pool.fetchval("SELECT count(*) FROM chat.tickets") == 0
    assert await _counts(db_pool) == ZERO


async def test_desconhecido_pedindo_acesso_continua_sem_alerta(db_pool: asyncpg.Pool) -> None:
    env = start_env()
    try:
        for _ in range(3):  # reentregas/insistencia do mesmo desconhecido
            assert env.post(fixture_bytes("text")).status_code == 200
        await asyncio.sleep(SETTLE_SECONDS)
        assert _emails(env) == []
    finally:
        env.client.__exit__(None, None, None)
    assert await db_pool.fetchval("SELECT count(*) FROM access.access_attempts") >= 1
    assert await _counts(db_pool) == ZERO


async def test_tickets_escalados_e_acoes_do_painel_nao_geram_alerta_nem_email(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        world = await factories.seed_world(conn, clock.now())
        escalated = [t for t in world.tickets if t.escalated and t.status == "awaiting"]
        assert escalated, "o cenario precisa ter escalados aguardando"
        first, second = escalated[0], escalated[1]
        await ta.set_urgency(conn, first.id, "urgente", actor="admin@x.com", clock=clock)
        await ta.reclassify(conn, first.id, "MATERIAL", actor="admin@x.com", clock=clock)
        await ta.conclude(conn, second.id, "Ana Souza", actor="admin@x.com", clock=clock)
    assert await _counts(db_pool) == ZERO
