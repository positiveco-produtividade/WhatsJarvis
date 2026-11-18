"""Inbox: link do aviso, caixa de resposta e linha do tempo da equipe (retorno-humano.1; RH-T12).

RH-5 (link abre o Inbox na pergunta; login devolve ao mesmo endereco; 404), RH-6 (caixa no chat,
CSRF), RH-10 (ha quanto tempo), RH-14 (bolha "Equipe · autor"; Concluir sem texto mantem a nota
antiga) e RH-15 (situacao do envio). Postgres real; sem rede.
"""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    HOST,
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

INBOX = "/dashboard/inbox"
AWAITING = 2  # world.tickets[2]: aguardando, vendedor de uma marca (ver factories)


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def world(env: Env) -> factories.PanelWorld:
    return asyncio.run(_seed(env.dsn))


def _ticket(world: factories.PanelWorld) -> Any:
    return world.tickets[AWAITING]


def _question(env: Env, ticket: Any, body: str = "Tem versão sem lactose?") -> None:
    sql(
        env.dsn,
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, body, ticket_number) "
        "VALUES (gen_random_uuid()::text, $1, 'text', $2, 'allowed', 'escalated', $3, $4) "
        "RETURNING id",
        NOW - timedelta(minutes=30),
        ticket.user_id,
        body,
        ticket.ticket_number,
    )


def _field(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]+)"', html)
    assert match is not None, f"chat sem campo {name}"
    return match.group(1)


def _chat(c: TestClient, user_id: int, number: str | None = None) -> str:
    url = f"{INBOX}/{user_id}" + (f"?t={number}" if number else "")
    r = c.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.text


def _reply(c: TestClient, number: str, html: str, texto: str) -> Any:
    return c.post(
        f"/dashboard/tickets/{number}/responder",
        data={"texto": texto, "token": _field(html, "token"), "origem": "inbox"},
        headers={**post_headers(_field(html, "csrf")), "HX-Request": "true"},
    )


# ---- RH-5: link do aviso ------------------------------------------------------------------


def test_link_do_aviso_redireciona_para_a_pagina_do_inbox(
    env: Env, world: factories.PanelWorld
) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        r = c.get(f"{INBOX}/ticket/{ticket.ticket_number}")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert url.path == INBOX
    assert parse_qs(url.query) == {"u": [str(ticket.user_id)], "t": [ticket.ticket_number]}


def test_link_sem_sessao_vai_ao_login_e_volta_ao_mesmo_endereco(
    env: Env, world: factories.PanelWorld
) -> None:
    number = _ticket(world).ticket_number
    with env.client() as c:
        r = c.get(f"{INBOX}/ticket/{number}")
    assert r.status_code == 302
    login_url = urlparse(r.headers["location"])
    assert login_url.netloc == "accounts.positiveco.com.br"
    assert parse_qs(login_url.query)["next"] == [f"https://{HOST}{INBOX}/ticket/{number}"]


@pytest.mark.parametrize("number", ["J-9999999", "nao-e-ticket"])
def test_link_de_ticket_inexistente_da_404(
    env: Env, world: factories.PanelWorld, number: str
) -> None:
    with env.client() as c:
        login(c)
        r = c.get(f"{INBOX}/ticket/{number}")
    assert r.status_code == 404


def test_pagina_passa_o_ticket_ao_chat(env: Env, world: factories.PanelWorld) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = c.get(INBOX, params={"u": ticket.user_id, "t": ticket.ticket_number}).text
        bad = c.get(INBOX, params={"u": ticket.user_id, "t": "<script>"}).text
    assert f'hx-get="{INBOX}/{ticket.user_id}?t={ticket.ticket_number}"' in html
    assert f'hx-get="{INBOX}/{ticket.user_id}"' in bad
    assert "<script>" not in bad.split("chat-pane")[1][:200]


def test_chat_destaca_a_pergunta_e_poe_foco_na_caixa(
    env: Env, world: factories.PanelWorld
) -> None:
    ticket = _ticket(world)
    _question(env, ticket)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id, ticket.ticket_number)
    assert f'data-ticket="{ticket.ticket_number}" data-focus' in html
    assert "data-reply-focus" in html and "Tem versão sem lactose?" in html
    number_span = f'<span class="font-mono text-xs text-ink-500">{ticket.ticket_number}'
    title = f"Responder ao vendedor {number_span}"
    assert title in html
    assert re.search(r"O vendedor escreveu há \d+ min", html)


def test_chat_sem_ticket_pedido_usa_o_aguardando_sem_foco(
    env: Env, world: factories.PanelWorld
) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id)
    assert "Responder ao vendedor" in html and "data-reply-focus" not in html
    assert "data-focus" not in html


def test_ticket_de_outra_conversa_e_ignorado(env: Env, world: factories.PanelWorld) -> None:
    ticket = _ticket(world)
    other = next(t for t in world.tickets if t.user_id != ticket.user_id)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id, other.ticket_number)
    assert other.ticket_number not in html.split("Responder ao vendedor")[1][:120]


# ---- RH-6, RH-14, RH-15: responder pelo chat ----------------------------------------------


def test_responder_pelo_chat_volta_o_chat_com_a_bolha_da_equipe(
    env: Env, world: factories.PanelWorld
) -> None:
    ticket = _ticket(world)
    _question(env, ticket)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id, ticket.ticket_number)
        r = _reply(c, ticket.ticket_number, html, "Temos sim, a linha Zero.")
    assert r.status_code == 200
    assert f"Resposta enviada ao vendedor ({ticket.ticket_number})." in r.text
    assert 'data-role="team"' in r.text and "Equipe · Admin Teste" in r.text
    assert "Temos sim, a linha Zero." in r.text and 'data-reply-state="pending"' in r.text
    assert "na fila" in r.text and "Enviar complemento" in r.text
    trigger = json.loads(r.headers["HX-Trigger"])
    assert trigger["ticketChanged"] == {"ticket": ticket.ticket_number}
    status = sql(
        env.dsn, "SELECT status FROM chat.tickets WHERE ticket_number = $1", ticket.ticket_number
    )
    assert status == "answered_human"


@pytest.mark.parametrize(
    ("update", "label", "tone"),
    [
        ("status = 'sent', sent_at = $1", "enviada", "ok"),
        ("status = 'sent', sent_at = $1, delivered_at = $1", "entregue", "ok"),
        ("status = 'sent', sent_at = $1, delivered_at = $1, read_at = $1", "lida", "ok"),
        ("status = 'failed', error_code = '463', claimed_at = $1", "falhou (463)", "erro"),
    ],
)
def test_situacao_do_envio_na_bolha(
    env: Env, world: factories.PanelWorld, update: str, label: str, tone: str
) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id, ticket.ticket_number)
        _reply(c, ticket.ticket_number, html, "Temos sim.")
        sql(env.dsn, f"UPDATE chat.outbound_messages SET {update} RETURNING id", NOW)  # noqa: S608
        after = _chat(c, ticket.user_id)
    assert f'data-reply-state="{tone}"' in after and label in after


def test_bot_nao_mostra_a_resposta_da_equipe_como_bot(
    env: Env, world: factories.PanelWorld
) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id, ticket.ticket_number)
        _reply(c, ticket.ticket_number, html, "Temos sim.")
        after = _chat(c, ticket.user_id)
    assert after.count("Retorno da equipe (Admin) sobre sua pergunta:") == 1
    assert 'data-role="bot"' not in after.split("Retorno da equipe")[0][-400:]


def test_concluir_sem_texto_mantem_a_nota_antiga(env: Env, world: factories.PanelWorld) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id)
        r = c.post(
            f"{INBOX}/{ticket.user_id}/concluir",
            data={"ticket": ticket.ticket_number},
            headers={**post_headers(_field(html, "csrf")), "HX-Request": "true"},
        )
    assert "Nenhuma mensagem foi enviada." in r.text and 'data-role="team"' not in r.text


def test_responder_pelo_chat_exige_csrf(env: Env, world: factories.PanelWorld) -> None:
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id)
        r = c.post(
            f"/dashboard/tickets/{ticket.ticket_number}/responder",
            data={"texto": "x", "token": _field(html, "token"), "origem": "inbox"},
            headers={"HX-Request": "true"},
        )
    assert r.status_code == 403
    assert sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages") == 0


def test_vendedor_revogado_caixa_bloqueada_no_chat(env: Env, world: factories.PanelWorld) -> None:
    ticket = _ticket(world)
    sql(
        env.dsn,
        "UPDATE access.users SET status = 'revoked', revoked_at = $2, revoked_by = 'caio', "
        "revoked_reason = 'left_company' WHERE id = $1 RETURNING id",
        ticket.user_id,
        NOW,
    )
    with env.client() as c:
        login(c)
        html = _chat(c, ticket.user_id)
    assert "O acesso deste vendedor foi revogado" in html and 'name="texto"' not in html


def test_layout_de_celular(env: Env, world: factories.PanelWorld) -> None:
    """RH-5/NFR: no celular o chat ocupa a tela (JS) e ha volta para a lista."""
    ticket = _ticket(world)
    with env.client() as c:
        login(c)
        page = c.get(INBOX, params={"u": ticket.user_id}).text
        html = _chat(c, ticket.user_id)
    assert "data-inbox-list" in page
    assert "data-show-list" in html and "md:hidden" in html
