"""Memoria de conversa contra o Postgres de teste (CB-23, CB-12, CB-13). Dados SINTETICOS."""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services.brain.memory import Exchange, recent_exchanges
from tests.brain import factories

pytestmark = pytest.mark.db

_N = itertools.count(1)

_INSERT_OUT = (
    "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
    "inbound_trace_id, status, next_attempt_at, created_at) "
    "SELECT $1, phone_e164, 'brain_answer', 'active', $2, $3, $4, $5, $5 "
    "FROM access.users WHERE id = $1"
)


async def _exchange(
    conn: asyncpg.Connection,
    user_id: int,
    at: datetime,
    *,
    q: str,
    a: str | None = "Resposta sintetica.",
    outcome: str = "answered",
    flags: list[str] | None = None,
    out_status: str = "sent",
) -> int:
    trace = await factories.create_pending_trace(
        conn, user_id, at, text=q, wa_message_id=f"wamid.mem.{next(_N)}"
    )
    await conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, outcome, path, flags, created_at) "
        "VALUES ($1, $2, 'ia', $3, $4)",
        trace, outcome, flags or [], at,
    )
    if a is not None:
        await conn.execute(_INSERT_OUT, user_id, a, trace, out_status, at)
    return trace


async def _get(
    conn: asyncpg.Connection, clock: FakeClock, user_id: int, current: int
) -> list[Exchange]:
    return await recent_exchanges(
        conn, user_id=user_id, trace_id=current, now=clock.now(), exchanges=3, minutes=30
    )


async def _current(conn: asyncpg.Connection, clock: FakeClock, user_id: int) -> int:
    return await factories.create_pending_trace(
        conn, user_id, clock.now(), text="e o de limao?", wa_message_id=f"wamid.mem.{next(_N)}"
    )


async def test_ultimas_tres_trocas_em_ordem_cronologica(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    for i in range(4):
        await _exchange(db_conn, brain_user, now - timedelta(minutes=20 - i), q=f"P{i}", a=f"R{i}")
    cur = await _current(db_conn, clock, brain_user)
    out = await _get(db_conn, clock, brain_user, cur)
    assert [(e.question, e.answer) for e in out] == [("P1", "R1"), ("P2", "R2"), ("P3", "R3")]


async def test_fora_da_janela_de_30_min_nao_entra(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    await _exchange(db_conn, brain_user, now - timedelta(minutes=31), q="velha")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=29), q="recente")
    cur = await _current(db_conn, clock, brain_user)
    assert [e.question for e in await _get(db_conn, clock, brain_user, cur)] == ["recente"]


async def test_so_id_menor_que_o_rastro_atual(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    before = await _exchange(db_conn, brain_user, now - timedelta(minutes=2), q="antes")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=1), q="depois")
    out = await _get(db_conn, clock, brain_user, before + 1)
    assert [e.question for e in out] == ["antes"]
    assert await _get(db_conn, clock, brain_user, before) == []


async def test_nunca_de_outro_vendedor(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    other = await factories.create_active_user(db_conn, clock.now(), seq=2)
    now = clock.now()
    await _exchange(db_conn, other, now - timedelta(minutes=5), q="do outro")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=4), q="meu")
    cur = await _current(db_conn, clock, brain_user)
    assert [e.question for e in await _get(db_conn, clock, brain_user, cur)] == ["meu"]
    assert [e.question for e in await _get(db_conn, clock, other, cur)] == ["do outro"]


@pytest.mark.parametrize(
    "outcome", ["no_answer", "brand_disabled", "access_request", "injection", "refusal", "greeting"]
)
async def test_so_desfecho_answered_ou_ask_brand(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int, outcome: str
) -> None:
    now = clock.now()
    await _exchange(db_conn, brain_user, now - timedelta(minutes=5), q="ruim", outcome=outcome)
    await _exchange(db_conn, brain_user, now - timedelta(minutes=4), q="marca", outcome="ask_brand")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=3), q="ok", outcome="answered")
    cur = await _current(db_conn, clock, brain_user)
    assert [e.question for e in await _get(db_conn, clock, brain_user, cur)] == ["marca", "ok"]


async def test_exclui_sinalizada_como_injection(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    await _exchange(db_conn, brain_user, now - timedelta(minutes=5), q="ataque",
                    flags=["injection"])
    cur = await _current(db_conn, clock, brain_user)
    assert await _get(db_conn, clock, brain_user, cur) == []


async def test_corpo_anulado_pela_lgpd_some(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    t1 = await _exchange(db_conn, brain_user, now - timedelta(minutes=6), q="sem rastro")
    t2 = await _exchange(db_conn, brain_user, now - timedelta(minutes=5), q="sem saida")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=4), q="intacta")
    await db_conn.execute("UPDATE chat.message_trace SET body = NULL WHERE id = $1", t1)
    await db_conn.execute(
        "UPDATE chat.outbound_messages SET body = NULL WHERE inbound_trace_id = $1", t2
    )
    cur = await _current(db_conn, clock, brain_user)
    assert [e.question for e in await _get(db_conn, clock, brain_user, cur)] == ["intacta"]


async def test_usuario_nao_active_nao_entra(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    await _exchange(db_conn, brain_user, now - timedelta(minutes=5), q="p")
    cur = await _current(db_conn, clock, brain_user)
    assert len(await _get(db_conn, clock, brain_user, cur)) == 1
    await db_conn.execute(
        "UPDATE access.users SET status = 'revoked', revoked_at = now(), "
        "revoked_by = 'teste' WHERE id = $1", brain_user
    )
    assert await _get(db_conn, clock, brain_user, cur) == []


async def test_saida_nao_enviada_ou_ausente_nao_forma_troca_e_usa_primeira_parte(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    now = clock.now()
    await _exchange(db_conn, brain_user, now - timedelta(minutes=9), q="falhou",
                    out_status="failed")
    await _exchange(db_conn, brain_user, now - timedelta(minutes=8), q="sem saida", a=None)
    multi = await _exchange(
        db_conn, brain_user, now - timedelta(minutes=7), q="longa", a="Parte 1"
    )
    await db_conn.execute(_INSERT_OUT, brain_user, "Parte 2", multi, "sent", now)
    cur = await _current(db_conn, clock, brain_user)
    out = await _get(db_conn, clock, brain_user, cur)
    assert [(e.question, e.answer) for e in out] == [("longa", "Parte 1")]


async def test_configuracao_zero_devolve_vazio(
    db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    out = await recent_exchanges(
        db_conn, user_id=brain_user, trace_id=10**9, now=clock.now(), exchanges=0, minutes=30
    )
    assert out == []
