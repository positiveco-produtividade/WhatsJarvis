"""Responder ao vendedor pelo drawer do ticket (retorno-humano.1; task RH-T09).

RH-6 (caixa, admin, CSRF), RH-7 (envia e conclui), RH-8 (Concluir sem texto igual), RH-10
(ha quanto tempo o vendedor escreveu), RH-11 (token repetido), RH-12 (bloqueio) e o disparo do
envio logo depois do COMMIT. Postgres real; sem rede.
"""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    ADMIN,
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

BASE = "/dashboard/tickets"
BOT, HUMAN, AWAITING = 0, 1, 2


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:
    return asyncio.run(_seed(env.dsn))


def _num(world: factories.PanelWorld, index: int) -> str:
    return world.tickets[index].ticket_number


def _field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match is not None, f"drawer sem campo {name}"
    return match.group(1)


def _reply(c: TestClient, number: str, html: str, texto: str, **over: str) -> Any:
    data = {"texto": texto, "token": _field(html, "token"), "origem": "drawer", **over}
    return c.post(
        f"{BASE}/{number}/responder",
        data=data,
        headers={**post_headers(_field(html, "csrf")), "HX-Request": "true"},
    )


async def _fetch(dsn: str, query: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(await conn.fetch(query))
    finally:
        await conn.close()


def _outbound(env: Env) -> list[Any]:
    return asyncio.run(
        _fetch(
            env.dsn,
            "SELECT kind, body, author, author_email, status FROM chat.outbound_messages "
            "ORDER BY id",
        )
    )


class SpyDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[int, ...], tuple[int, ...]]] = []

    async def __call__(self, outbound: tuple[int, ...], deliveries: tuple[int, ...]) -> None:
        self.calls.append((outbound, deliveries))


def test_ticket_aguardando_tem_caixa_com_token(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{_num(seeded, AWAITING)}").text
    assert "Responder ao vendedor" in html and 'name="texto"' in html
    assert 'name="origem" value="drawer"' in html and "Enviar e concluir" in html
    uuid.UUID(_field(html, "token"))
    assert "O vendedor escreveu" in html


def test_respondido_pelo_bot_nao_tem_caixa(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{_num(seeded, BOT)}").text
    assert "Responder ao vendedor" not in html and "<textarea" not in html


def test_enviar_conclui_avisa_e_dispara_o_envio(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    spy = SpyDispatcher()
    with env.client() as c:
        c.app.state.outbound_dispatcher = spy  # type: ignore[attr-defined]
        login(c)
        html = c.get(f"{BASE}/{number}").text
        r = _reply(c, number, html, "Temos sim, a linha Zero.")
    assert r.status_code == 200
    assert f"Resposta enviada ao vendedor ({number})." in r.text
    trigger = json.loads(r.headers["HX-Trigger"])
    assert trigger["ticketChanged"] == {"ticket": number} and trigger["toast"]["kind"] == "ok"
    [out] = _outbound(env)
    assert out["kind"] == "human_reply" and out["author_email"] == ADMIN
    assert out["body"].endswith("Temos sim, a linha Zero.")
    assert out["body"].startswith("Retorno da equipe (Admin) sobre sua pergunta:")
    status = sql(env.dsn, "SELECT status FROM chat.tickets WHERE ticket_number = $1", number)
    assert status == "answered_human"
    assert len(spy.calls) == 1 and len(spy.calls[0][0]) == 1 and spy.calls[0][1] == ()
    assert "Respostas da equipe" in r.text and "na fila" in r.text
    assert "Enviar complemento" in r.text  # depois de responder, a caixa vira complemento


def test_mesmo_token_nao_reenvia(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        _reply(c, number, html, "Temos sim.")
        again = _reply(c, number, html, "Temos sim.")
    assert "Essa resposta já tinha sido enviada." in again.text
    assert len(_outbound(env)) == 1


def test_texto_vazio_volta_com_erro_sem_gravar(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        r = _reply(c, number, html, "    ")
    assert "Escreva a resposta antes de enviar." in r.text
    assert _outbound(env) == []
    trigger = json.loads(r.headers["HX-Trigger"])
    assert "ticketChanged" not in trigger and trigger["toast"]["kind"] == "erro"


def test_vendedor_revogado_caixa_bloqueada(env: Env, seeded: factories.PanelWorld) -> None:
    ticket = seeded.tickets[AWAITING]
    number = ticket.ticket_number
    sql(
        env.dsn,
        "UPDATE access.users SET status = 'revoked', revoked_at = $2, revoked_by = 'caio', "
        "revoked_reason = 'left_company' WHERE id = $1",
        ticket.user_id,
        NOW,
    )
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        token = str(uuid.uuid4())
        r = c.post(
            f"{BASE}/{number}/responder",
            data={"texto": "Temos.", "token": token, "origem": "drawer"},
            headers={**post_headers(_field(html, "csrf")), "HX-Request": "true"},
        )
    assert "O acesso deste vendedor foi revogado" in html and 'name="texto"' not in html
    assert "O acesso deste vendedor foi revogado" in r.text  # envio forcado recusado
    assert _outbound(env) == []


def test_token_invalido(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        r = _reply(c, number, html, "Temos.", token="nao-e-uuid")
    assert "O formulário expirou" in r.text and _outbound(env) == []


def test_sem_csrf_recusado(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        r = c.post(
            f"{BASE}/{number}/responder",
            data={"texto": "Temos.", "token": str(uuid.uuid4())},
            headers={"HX-Request": "true"},
        )
    assert r.status_code == 403 and _outbound(env) == []


def test_sem_sessao_nao_envia(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        r = c.post(f"{BASE}/{number}/responder", data={"texto": "x", "token": str(uuid.uuid4())})
    assert r.status_code in (302, 401, 403) and _outbound(env) == []


def test_ticket_inexistente(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{_num(seeded, AWAITING)}").text
        r = _reply(c, "J-9999999", html, "Temos.")
    assert r.status_code == 404 and "Ticket não encontrado." in r.text


def test_concluir_sem_texto_continua_igual(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        r = c.post(
            f"{BASE}/{number}/concluir",
            data={"author": "Caio Franco"},
            headers={**post_headers(_field(html, "csrf")), "HX-Request": "true"},
        )
    assert "Nenhuma mensagem foi enviada" in r.text and _outbound(env) == []
    assert sql(env.dsn, "SELECT count(*) FROM content.faq_suggestions") == 0


def test_mostra_ha_quanto_tempo_o_vendedor_escreveu(
    env: Env, seeded: factories.PanelWorld
) -> None:
    ticket = seeded.tickets[AWAITING]
    sql(
        env.dsn,
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision) VALUES ('wamid.ago', $1, 'text', $2, 'allowed', 'answered')",
        NOW - timedelta(hours=26),
        ticket.user_id,
    )
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{ticket.ticket_number}").text
    assert re.search(r"O vendedor escreveu há \d+ (h|min|dia|dias)", html)


def test_resposta_falhou_aparece_no_drawer(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        _reply(c, number, html, "Temos sim.")
        sql(env.dsn, "UPDATE chat.outbound_messages SET status = 'failed', error_code = '463'")
        again = c.get(f"{BASE}/{number}").text
    assert "falhou (463)" in again and 'data-reply-state="erro"' in again
