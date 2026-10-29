"""Rotulos do painel para o cerebro (CB-12/14/15/25; task CB-T20), Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from app.panel.api.alerts import ALERT_LABELS
from app.panel.services.inbox_read import get_chat, inbox_page
from app.panel.services.scope import Scope
from tests.brain.factories import create_active_user
from tests.panel.integration.test_access import NOW, Env, env, login, sql  # noqa: F401

pytestmark = pytest.mark.db

ALERTS = "/dashboard/alerts"
BRAIN_LABELS = {
    "brain_budget": "Orçamento do cérebro",
    "brain_api_failure": "Falha da IA (Gemini)",
}
_INSERT_ALERT = (
    "INSERT INTO ops.alerts (severity, kind, message, payload, created_at) "
    "VALUES ($1, $2, $3, '{}'::jsonb, $4) RETURNING id"
)


def _cards(html: str) -> list[int]:
    return [int(i) for i in re.findall(r'<article id="alerta-(\d+)"', html)]


def _add_alert(env: Env, kind: str, severity: str) -> int:
    return int(sql(env.dsn, _INSERT_ALERT, severity, kind, "msg " + kind, NOW))


def test_rotulos_do_cerebro_existem_em_pt_br() -> None:
    for kind, label in BRAIN_LABELS.items():
        assert ALERT_LABELS[kind] == label
    assert ALERT_LABELS["injection_attempt"] == "Tentativa de injection"  # ja existia


def test_alertas_do_cerebro_aparecem_e_filtram(env: Env) -> None:
    budget = _add_alert(env, "brain_budget", "warn")
    failure = _add_alert(env, "brain_api_failure", "critical")
    with env.client() as c:
        login(c)
        todos = c.get(ALERTS)
        criticos = c.get(f"{ALERTS}?filtro=criticos")
    assert todos.status_code == 200
    for label in BRAIN_LABELS.values():
        assert label in todos.text
    assert set(_cards(todos.text)) == {budget, failure}
    assert _cards(criticos.text) == [failure]


async def _seed_tap(dsn: str, reason: str | None) -> tuple[int, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        uid = await create_active_user(conn, NOW)
        await conn.execute(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, decision_reason, body) "
            "VALUES (gen_random_uuid()::text, $1, 'button', $2, 'allowed', 'allowed', $3, NULL)",
            NOW - timedelta(minutes=5), uid, reason,
        )
        page = await inbox_page(conn, Scope(None), now=NOW, tab="conversas")
        chat = await get_chat(conn, uid, now=NOW)
        return uid, (page, chat)
    finally:
        await conn.close()


@pytest.mark.parametrize("reason", ["rating_up", "rating_down"])
def test_toque_de_avaliacao_aparece_como_avaliacao_no_inbox(env: Env, reason: str) -> None:
    uid, (page, chat) = asyncio.run(_seed_tap(env.dsn, reason))
    row = next(r for r in page.rows if r.user_id == uid)
    assert row.last_text == "[avaliação]"
    assert chat is not None
    assert [e.body for e in chat.entries if e.role == "seller"] == ["[avaliação]"]


@pytest.mark.parametrize("reason", ["optin_accepted", "optin_declined", None])
def test_toque_de_optin_nao_vira_avaliacao(env: Env, reason: str | None) -> None:
    uid, (page, chat) = asyncio.run(_seed_tap(env.dsn, reason))
    row = next(r for r in page.rows if r.user_id == uid)
    assert row.last_text == "[button]"
    assert chat is not None
    assert [e.body for e in chat.entries if e.role == "seller"] == ["[button]"]
