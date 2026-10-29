"""Chat do Inbox, concluir e lote (PN-11, PN-12, PN-13, PN-14; task PN-T22) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

INBOX = "/dashboard/inbox"
# Cenario de create_tickets: indices em world.tickets (ver factories).
ONE_AWAITING, TWO_ESC_A, TWO_ESC_B, MEDIA_AWAITING = 2, 4, 6, 10


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def world(env: Env) -> factories.PanelWorld:
    return asyncio.run(_seed(env.dsn))


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None, "chat sem campo csrf"
    return match.group(1)


def _status(env: Env, number: str) -> str:
    return str(
        sql(env.dsn, "SELECT status FROM chat.tickets WHERE ticket_number = $1", number)
    )


def _trace(env: Env, user_id: int, body: str, at_minutes: int, msg_type: str = "text") -> None:
    sql(
        env.dsn,
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, body) "
        "VALUES (gen_random_uuid()::text, $1, $2, $3, 'allowed', 'allowed', $4) RETURNING id",
        NOW - timedelta(minutes=at_minutes),
        msg_type,
        user_id,
        body,
    )


def _open(c: TestClient, user_id: int) -> str:
    r = c.get(f"{INBOX}/{user_id}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.text


# ---- acesso -----------------------------------------------------------------------------


def test_sem_sessao_redireciona_ao_iam(env: Env, world: factories.PanelWorld) -> None:
    with env.client() as c:
        r = c.get(f"{INBOX}/{world.sellers.one_brand}")
        p = c.post(f"{INBOX}/concluir-lote", data={"user_ids": "1"})
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]
    assert p.status_code == 302


def test_conversa_inexistente_da_404_sem_dados(env: Env, world: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        r = c.get(f"{INBOX}/999999")
    assert r.status_code == 404 and "Conversa não encontrada" in r.text


# ---- PN-12 / PN-13: conteudo ----------------------------------------------------------


def test_chat_sem_caixa_de_resposta_e_grupo_desativado(
    env: Env, world: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        html = _open(c, world.sellers.one_brand)
    assert "<html" not in html
    assert "<textarea" not in html and "esponder ao vendedor" not in html
    assert "Responder em" in html and "Grupo da equipe desativado" in html
    assert 'data-team-group="off"' in html and "Grupo da equipe (WhatsApp)" not in html
    assert "Vendedor Uma Marca" in html and "Vendedor · SP" in html
    assert "+55 11 9" in html and "+5511900000001" not in html  # telefone mascarado
    assert f"/dashboard/pessoas/{world.sellers.one_brand}" in html and "Perfil completo" in html


def test_grupo_ativo_com_a_chave_ligada(env: Env, world: factories.PanelWorld) -> None:
    sql(
        env.dsn,
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
        "VALUES ('team_group_enabled', 'true'::jsonb, $1, 'teste') RETURNING key",
        NOW,
    )
    with env.client() as c:
        login(c)
        html = _open(c, world.sellers.one_brand)
    assert 'data-team-group="on"' in html and "Grupo da equipe (WhatsApp)" in html
    assert "desativado" not in html


def test_bolhas_com_horario_dia_bot_e_tempo_de_resposta(
    env: Env, world: factories.PanelWorld
) -> None:
    user = world.sellers.one_brand
    _trace(env, user, "Quanto custa o Nitro 400?", at_minutes=26 * 60)  # ontem
    _trace(env, user, "Tem estoque <b>hoje</b>?", at_minutes=10)
    sql(
        env.dsn,
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "status, next_attempt_at, created_at, sent_at) VALUES ($1, '+5511900000001', "
        "'media_ack', 'active', "
        "'Tem sim.', 'sent', $2, $2, $2) RETURNING id",
        user,
        NOW - timedelta(minutes=8),
    )
    with env.client() as c:
        login(c)
        html = _open(c, user)
    assert "Quanto custa o Nitro 400?" in html and "Ontem" in html and "Hoje" in html
    assert "Tem estoque &lt;b&gt;hoje&lt;/b&gt;?" in html and "<b>hoje</b>" not in html
    assert ">Bot<" in html and "respondeu em 2 min" in html
    assert 'data-role="bot"' in html and 'data-role="seller"' in html


def test_selo_escalado_e_botao_so_com_ticket_aguardando(
    env: Env, world: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        esc = _open(c, world.sellers.two_brands)
    number = world.tickets[TWO_ESC_A].ticket_number
    assert f"Preço {number} escalado" in esc and "Marcar como concluído" in esc
    assert f"/dashboard/inbox/{world.sellers.two_brands}/concluir" in esc


def test_tickets_recentes_abrem_o_drawer(env: Env, world: factories.PanelWorld) -> None:
    number = world.tickets[ONE_AWAITING].ticket_number
    with env.client() as c:
        login(c)
        html = _open(c, world.sellers.one_brand)
    assert "Tickets recentes" in html
    assert f'hx-get="/dashboard/tickets/{number}"' in html and 'hx-target="#drawer-body"' in html
    assert "Respondido por humano" in html and "Erro no envio" in html


def test_conversa_sem_mensagens_nem_tickets_renderiza(env: Env) -> None:
    async def _make() -> int:
        conn = await asyncpg.connect(env.dsn)
        try:
            return await factories.insert_user(
                conn, name="Sem Historico", seq=9, brands=[factories.BRAND_A], now=NOW
            )
        finally:
            await conn.close()

    user = asyncio.run(_make())
    with env.client() as c:
        login(c)
        html = _open(c, user)
    assert "Ainda não há mensagens" in html and "Nenhum ticket ainda." in html
    assert "Marcar como concluído" not in html


# ---- concluir da conversa (PN-13) -----------------------------------------------------


def test_marcar_como_concluido_registra_autor_e_nao_envia(
    env: Env, world: factories.PanelWorld
) -> None:
    user = world.sellers.two_brands
    esc_a, esc_b = world.tickets[TWO_ESC_A], world.tickets[TWO_ESC_B]
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, user))
        r = c.post(
            f"{INBOX}/{user}/concluir",
            data={"ticket": esc_a.ticket_number},
            headers={**post_headers(token), "HX-Request": "true"},
        )
    assert r.status_code == 200
    assert _status(env, esc_a.ticket_number) == "answered_human"
    assert _status(env, esc_b.ticket_number) == "awaiting"  # so o indicado
    assert "Nenhuma mensagem foi enviada" in r.text
    assert "Marcar como concluído" in r.text  # ainda resta o da outra marca
    trigger = json.loads(r.headers["HX-Trigger"])
    assert trigger["toast"]["kind"] == "ok"
    assert sql(env.dsn, "SELECT answered_by FROM chat.tickets WHERE id = $1", esc_a.id)
    assert sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages") == 0
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action LIKE 'ticket.%'") >= 1


def test_concluir_sem_ticket_conclui_todos_e_repetir_avisa(
    env: Env, world: factories.PanelWorld
) -> None:
    user = world.sellers.two_brands
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, user))
        headers = {**post_headers(token), "HX-Request": "true"}
        first = c.post(f"{INBOX}/{user}/concluir", headers=headers)
        again = c.post(f"{INBOX}/{user}/concluir", headers=headers)
    assert "2 tickets concluídos" in first.text
    assert "Marcar como concluído" not in first.text
    assert _status(env, world.tickets[TWO_ESC_A].ticket_number) == "answered_human"
    assert _status(env, world.tickets[TWO_ESC_B].ticket_number) == "answered_human"
    assert json.loads(again.headers["HX-Trigger"])["toast"]["kind"] == "aviso"


def test_concluir_exige_csrf(env: Env, world: factories.PanelWorld) -> None:
    user = world.sellers.two_brands
    with env.client() as c:
        login(c)
        _open(c, user)
        r = c.post(f"{INBOX}/{user}/concluir", headers={"Origin": "https://jarvis.positiveco.com.br"})
    assert r.status_code == 403
    assert _status(env, world.tickets[TWO_ESC_A].ticket_number) == "awaiting"


# ---- lote (PN-11) -----------------------------------------------------------------------


def _batch(c: TestClient, token: str, user_ids: list[int], url: str = INBOX) -> Any:
    return c.post(
        f"{INBOX}/concluir-lote",
        data={"user_ids": [str(u) for u in user_ids]},
        headers={**post_headers(token), "HX-Request": "true", "HX-Current-URL": url},
    )


def test_lote_conclui_so_os_aguardando_da_aba_e_pede_refresh(
    env: Env, world: factories.PanelWorld
) -> None:
    one, two, media = (
        world.sellers.one_brand,
        world.sellers.two_brands,
        world.sellers.media_only,
    )
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, one))
        r = _batch(c, token, [one, two, media], f"https://x.test{INBOX}?aba=conversas")
    assert r.status_code == 200 and r.headers["HX-Refresh"] == "true"
    for index in (ONE_AWAITING, TWO_ESC_A, TWO_ESC_B):
        assert _status(env, world.tickets[index].ticket_number) == "answered_human"
    # midia fica de fora: a aba era Conversas
    assert _status(env, world.tickets[MEDIA_AWAITING].ticket_number) == "awaiting"
    assert sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages") == 0


def test_lote_na_aba_midias_conclui_a_midia(env: Env, world: factories.PanelWorld) -> None:
    one, media = world.sellers.one_brand, world.sellers.media_only
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, one))
        _batch(c, token, [one, media], f"https://x.test{INBOX}?aba=midias")
    assert _status(env, world.tickets[MEDIA_AWAITING].ticket_number) == "answered_human"
    assert _status(env, world.tickets[ONE_AWAITING].ticket_number) == "awaiting"


def test_lote_respeita_a_marca_em_foco(env: Env, world: factories.PanelWorld) -> None:
    two = world.sellers.two_brands
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, two))
        c.cookies.set("jarvis_brand", factories.BRAND_A, domain="jarvis.positiveco.com.br")
        _batch(c, token, [two])
    assert _status(env, world.tickets[TWO_ESC_A].ticket_number) == "answered_human"
    assert _status(env, world.tickets[TWO_ESC_B].ticket_number) == "awaiting"


def test_lote_sem_selecao_ou_sem_aguardando_avisa_sem_alterar(
    env: Env, world: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        token = _csrf(_open(c, world.sellers.two_brands))
        empty = _batch(c, token, [])
        junk = c.post(
            f"{INBOX}/concluir-lote",
            data={"user_ids": ["abc", "-1", ""]},
            headers={**post_headers(token), "HX-Request": "true"},
        )
        nothing = _batch(c, token, [999999])
    for r in (empty, junk, nothing):
        assert r.status_code == 200
        assert "HX-Refresh" not in r.headers
        trigger = json.loads(r.headers["HX-Trigger"])
        assert trigger["toast"]["kind"] == "aviso"
    assert _status(env, world.tickets[TWO_ESC_A].ticket_number) == "awaiting"


def test_lote_exige_csrf(env: Env, world: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        _open(c, world.sellers.one_brand)
        r = c.post(
            f"{INBOX}/concluir-lote",
            data={"user_ids": [str(world.sellers.one_brand)]},
            headers={"Origin": "https://jarvis.positiveco.com.br"},
        )
    assert r.status_code == 403
    assert _status(env, world.tickets[ONE_AWAITING].ticket_number) == "awaiting"
