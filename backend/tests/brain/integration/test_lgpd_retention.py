"""LGPD e retencao do cerebro (CB-16, CB-23): exclusao de pessoa e purga. SINTETICO.

`lgpd.delete_person` e `retention.purge` vivem em `app.panel`; so os testes os importam (o
pacote do cerebro continua sem depender do painel).
"""
from __future__ import annotations

import itertools
import json
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import BrainOutcome, rate_down_payload, rate_up_payload
from app.panel.services import lgpd, retention
from app.services.brain.effects import BrainEffects
from app.services.brain.memory import recent_exchanges
from app.services.flags import Flags
from app.services.ratings import handle_rating
from tests.brain import factories

pytestmark = pytest.mark.db

_N = itertools.count(1)
QUESTION = "Qual o sabor do produto exemplo secreto?"
ANSWER = "Resposta sintetica sobre o sabor tropical."
ACCESS_Q = "Quero acesso a marca exemplo restrita"
TEXTS = (QUESTION, ANSWER, ACCESS_Q)
_INSERT_OUT = (
    "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
    "inbound_trace_id, status, attempts, next_attempt_at, created_at) "
    "SELECT $1, phone_e164, 'brain_answer', 'active', $2, $3, 'sent', 1, $4, $4 "
    "FROM access.users WHERE id = $1"
)


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


async def _trace(conn: asyncpg.Connection, user: int, clock: FakeClock, text: str) -> int:
    return await factories.create_pending_trace(
        conn, user, clock.now(), text=text, wa_message_id=f"wamid.lg.{next(_N)}"
    )


async def _answered(
    pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int
) -> tuple[int, int]:
    """Pergunta respondida pelo cerebro: rastro, ticket, `brain_runs` e a saida enviada."""
    trace = await _trace(conn, user, clock, QUESTION)
    fx = BrainEffects(
        outcome=BrainOutcome.ANSWERED, flags_service=Flags(pool, clock, team_group_initial=True),
        question=QUESTION, path="palavras", classification="PRODUTO", brand=factories.BRAND_A,
        confidence=0.9, item_refs=["faq:1@v1"], best_score=0.9, clock=clock,
        settings=make_settings(),
    )  # fmt: skip
    applied = await fx.apply(conn, trace, user)
    assert applied.ticket_id is not None
    await conn.execute(_INSERT_OUT, user, ANSWER, trace, clock.now())
    return trace, applied.ticket_id


async def _rate(
    pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int, payload: str
) -> None:
    tap = await _trace(conn, user, clock, "toque")
    result = await handle_rating(
        conn, user_id=user, payload=payload, trace_id=tap, clock=clock,
        settings=make_settings(), flags=Flags(pool, clock, team_group_initial=True),
    )
    assert result.recorded


async def _access_request(
    pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int
) -> int:
    trace = await _trace(conn, user, clock, ACCESS_Q)
    fx = BrainEffects(
        outcome=BrainOutcome.ACCESS_REQUEST,
        flags_service=Flags(pool, clock, team_group_initial=True), question=ACCESS_Q,
        brand=factories.BRAND_B, brand_label="Marca Exemplo", clock=clock,
        settings=make_settings(),
    )  # fmt: skip
    applied = await fx.apply(conn, trace, user)
    assert applied.ticket_id is not None
    return applied.ticket_id


async def _window(conn: asyncpg.Connection, clock: FakeClock, user: int) -> list[Any]:
    current = await _trace(conn, user, clock, "e o de limao?")
    return await recent_exchanges(
        conn, user_id=user, trace_id=current, now=clock.now(), exchanges=3, minutes=30
    )


async def _text_dump(conn: asyncpg.Connection, table: str) -> str:
    rows = await conn.fetch(f"SELECT to_jsonb(t)::text AS j FROM {table} t")  # noqa: S608
    return " ".join(str(r["j"]) for r in rows)


# --- LGPD: excluir pessoa ---


async def test_excluir_vendedor_zera_perguntas_respostas_e_janela(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    _, ticket = await _answered(db_pool, db_conn, clock, brain_user)
    await _rate(db_pool, db_conn, clock, brain_user, rate_down_payload(ticket))
    await _access_request(db_pool, db_conn, clock, brain_user)
    assert len(await _window(db_conn, clock, brain_user)) == 1  # antes: janela tem a troca

    result = await lgpd.delete_person(db_conn, brain_user, "admin@teste", clock=clock)

    assert result.counts["tickets"] >= 3 and result.counts["outbound_messages"] >= 1
    tickets = await db_conn.fetch(
        "SELECT kind, escalation_reason, message, summary FROM chat.tickets WHERE user_id = $1",
        brain_user,
    )
    reasons = {t["escalation_reason"] for t in tickets}
    assert {"rating_down", "brand_access_request"} <= reasons and len(tickets) >= 3
    assert all(t["message"] is None and t["summary"] is None for t in tickets)
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.message_trace WHERE user_id = $1 AND body IS NOT NULL",
        brain_user,
    ) == 0
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.outbound_messages WHERE user_id = $1 AND body IS NOT NULL",
        brain_user,
    ) == 0
    assert await _window(db_conn, clock, brain_user) == []  # depois: janela vazia


async def test_brain_runs_e_ratings_nao_guardam_texto_antes_nem_depois(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    _, ticket = await _answered(db_pool, db_conn, clock, brain_user)
    await _rate(db_pool, db_conn, clock, brain_user, rate_down_payload(ticket))
    await _access_request(db_pool, db_conn, clock, brain_user)

    async def assert_clean() -> None:
        for table in ("chat.brain_runs", "chat.answer_ratings"):
            dump = await _text_dump(db_conn, table)
            assert dump and not any(text in dump for text in TEXTS)

    await assert_clean()
    await lgpd.delete_person(db_conn, brain_user, "admin@teste", clock=clock)
    await assert_clean()
    # agregados (custo, desfecho, nota) continuam: nao identificam a pessoa
    assert await db_conn.fetchval("SELECT count(*) FROM chat.brain_runs") == 2
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 1


async def test_colunas_de_texto_livre_nao_existem_no_rastro(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'chat' AND table_name IN ('brain_runs', 'answer_ratings') "
        "AND data_type = 'text'"
    )
    allowed = {
        "outcome", "path", "brand", "fidelity", "prompt_version", "model", "rating",
    }
    assert {r["column_name"] for r in rows} <= allowed


async def test_excluir_nao_apaga_dados_de_outro_vendedor(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    other = await factories.create_active_user(db_conn, clock.now(), seq=2)
    await _answered(db_pool, db_conn, clock, other)
    await lgpd.delete_person(db_conn, brain_user, "admin@teste", clock=clock)
    kept = await db_conn.fetchval(
        "SELECT message FROM chat.tickets WHERE user_id = $1", other
    )
    assert kept == QUESTION


# --- Retencao: purga com brain_runs / answer_ratings ---


async def _seed_period(
    pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int
) -> tuple[int, int]:
    """Uma resposta avaliada (up) no instante do `clock`; devolve (trace, ticket)."""
    trace, ticket = await _answered(pool, conn, clock, user)
    await _rate(pool, conn, clock, user, rate_up_payload(ticket))
    return trace, ticket


async def test_purge_remove_antigos_e_mantem_recentes_sem_falhar(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    old_clock = FakeClock(clock.now() - timedelta(days=430))
    old_trace, old_ticket = await _seed_period(db_pool, db_conn, old_clock, brain_user)
    new_trace, new_ticket = await _seed_period(db_pool, db_conn, clock, brain_user)
    settings = make_settings(retention_months=12)

    dry = await retention.purge(db_conn, settings, clock, dry_run=True)
    assert dry.counts["tickets"] >= 1
    assert await db_conn.fetchval("SELECT count(*) FROM chat.brain_runs") == 2
    assert await db_conn.fetchval("SELECT count(*) FROM chat.answer_ratings") == 2

    result = await retention.purge(db_conn, settings, clock, dry_run=False)

    assert result.counts["tickets"] >= 1 and result.counts["message_trace"] >= 1
    runs = await db_conn.fetch("SELECT trace_id, ticket_id FROM chat.brain_runs")
    assert [r["trace_id"] for r in runs] == [new_trace] and runs[0]["ticket_id"] == new_ticket
    ratings = await db_conn.fetch("SELECT ticket_id FROM chat.answer_ratings")
    assert [r["ticket_id"] for r in ratings] == [new_ticket]
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.tickets WHERE id = $1", old_ticket
    ) == 0
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.message_trace WHERE id = $1", old_trace
    ) == 0


async def test_purge_sem_linhas_do_cerebro_tambem_roda(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    result = await retention.purge(db_conn, make_settings(), clock, dry_run=False)
    assert result.total >= 0
    audit = await db_conn.fetchval(
        "SELECT new_value::text FROM ops.audit_log WHERE action = 'retention.purge' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert audit is not None and not any(text in str(audit) for text in TEXTS)
    assert "counts" in json.loads(audit)
