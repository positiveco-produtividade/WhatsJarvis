"""Drawer do ticket e acoes (PN-15..18, PN-63, PN-4; task PN-T19) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    ADMIN,
    NOW,
    ORIGIN,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

BASE = "/dashboard/tickets"
# Cenario de create_tickets: 1 bot, 2 humano, 3 aguardando, 4 erro de envio (vendedor uma marca),
# 5 aguardando escalado PRECO (duas marcas), 7 ... ; ver factories.
BOT, HUMAN, AWAITING, SEND_ERROR, ESCALATED = 0, 1, 2, 3, 4


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


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None, "drawer sem campo csrf"
    return match.group(1)


def _row(env: Env, number: str) -> Any:
    rows = asyncio.run(_fetch(env.dsn, number))
    return rows[0]


async def _fetch(dsn: str, number: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch("SELECT * FROM chat.tickets WHERE ticket_number = $1", number)
    finally:
        await conn.close()


def _audit(env: Env, action: str) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = $1", action))


def _open(c: TestClient, number: str) -> tuple[str, str]:
    html = c.get(f"{BASE}/{number}").text
    return html, _csrf(html)


def _post(c: TestClient, number: str, action: str, token: str, **data: str) -> Any:
    return c.post(
        f"{BASE}/{number}/{action}",
        data=data,
        headers={**post_headers(token), "HX-Request": "true"},
    )


# ---- GET: conteudo (PN-15) -------------------------------------------------------------


def test_sem_sessao_redireciona_ao_iam(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        r = c.get(f"{BASE}/{_num(seeded, AWAITING)}")
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]


def test_drawer_aguardando_mostra_conteudo_sem_responder(
    env: Env, seeded: factories.PanelWorld
) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/{number}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert number in html and "Mensagem do vendedor" in html
    assert f"Pergunta de teste {number}" in html
    assert "Marcar como concluído" in html and "Nenhuma mensagem é enviada ao vendedor" in html
    assert "Quem concluiu" in html and "Reclassificar" in html
    assert "Vendedor Uma Marca" in html and "/dashboard/pessoas/" in html
    assert "<textarea" not in html and "esponder ao vendedor" not in html
    assert "<html" not in html  # fragmento, nao pagina
    assert "+55 11 9" in html and "+5511900000001" not in html  # telefone mascarado
    assert "Plant Power" in html


def test_drawer_por_status(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        bot = c.get(f"{BASE}/{_num(seeded, BOT)}").text
        human = c.get(f"{BASE}/{_num(seeded, HUMAN)}").text
        err = c.get(f"{BASE}/{_num(seeded, SEND_ERROR)}").text
    assert "Enviado automaticamente pelo Jarvis" in bot and "Marcar como concluído" not in bot
    assert "Por <strong" in human and "Admin Teste" in human
    assert "Marcar como concluído" not in human
    assert "Erro no envio" in err  # chip do status (sem trace: sem detalhe do erro)


def test_erro_de_envio_mostra_codigo_da_meta(env: Env, seeded: factories.PanelWorld) -> None:
    ticket = seeded.tickets[SEND_ERROR]
    sql(
        env.dsn,
        "WITH t AS (INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, send_error) VALUES ('wamid.x1', $1, 'text', $2, 'allowed', "
        "'answered', 'Meta 131047: janela de 24 h expirada') RETURNING id) "
        "UPDATE chat.tickets SET trace_id = (SELECT id FROM t) WHERE id = $3 RETURNING id",
        NOW,
        ticket.user_id,
        ticket.id,
    )
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{ticket.ticket_number}").text
    assert "Meta 131047: janela de 24 h expirada" in html


def test_drawer_escalado_tem_urgencia_e_motivo(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESCALATED)
    sql(env.dsn, "UPDATE chat.tickets SET escalation_reason = 'Sem argumento aprovado' "
        "WHERE ticket_number = $1", number)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{number}").text
        plain = c.get(f"{BASE}/{_num(seeded, AWAITING)}").text
    assert "Motivo da escalação" in html and "Sem argumento aprovado" in html
    assert 'name="urgency"' in html and "Muito urgente" in html
    assert 'name="urgency"' not in plain and "Motivo da escalação" not in plain


def test_ticket_inexistente_ou_numero_invalido_da_404(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        missing = c.get(f"{BASE}/J-9999")
        bad = c.get(f"{BASE}/abc")
    for r in (missing, bad):
        assert r.status_code == 404 and "Ticket não encontrado." in r.text


# ---- concluir (PN-16, PN-17) -----------------------------------------------------------


def test_concluir_registra_autor_sem_enviar_nada(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    outbox_before = int(sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages"))
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        r = _post(c, number, "concluir", token, author="  Caio   Franco ")
    assert r.status_code == 200
    assert "concluído por Caio Franco" in r.text and "Nenhuma mensagem foi enviada" in r.text
    assert "Marcar como concluído" not in r.text  # saiu de aguardando
    assert "toast" in r.headers["hx-trigger"] and "ticketChanged" in r.headers["hx-trigger"]
    row = _row(env, number)
    assert row["status"] == "answered_human" and row["answered_by"] == "Caio Franco"
    assert row["answered_at"] is not None
    assert _audit(env, "ticket.conclude") == 1
    assert int(sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages")) == outbox_before


def test_concluir_sem_autor_e_recusado(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        r = _post(c, number, "concluir", token, author="   ")
    assert r.status_code == 200 and "Quem concluiu: informe quem concluiu" in r.text
    assert 'role="alert"' in r.text and "Marcar como concluído" in r.text
    assert _row(env, number)["status"] == "awaiting"
    assert _audit(env, "ticket.conclude") == 0


def test_segunda_conclusao_mostra_ja_concluido_por(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        _post(c, number, "concluir", token, author="Primeiro")
        second = _post(c, number, "concluir", token, author="Segundo")
    assert re.search(r"Já concluído por Primeiro em \d\d:\d\d\.", second.text)
    assert "Segundo" not in second.text
    row = _row(env, number)
    assert row["answered_by"] == "Primeiro"
    assert _audit(env, "ticket.conclude") == 1


def test_duas_conclusoes_simultaneas_so_uma_vence(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)

    def attempt(author: str) -> str:
        with env.client() as c:
            login(c)
            _, token = _open(c, number)
            return str(_post(c, number, "concluir", token, author=author).text)

    with ThreadPoolExecutor(max_workers=2) as pool:
        texts = list(pool.map(attempt, ["Aba A", "Aba B"]))
    winners = [t for t in texts if "Nenhuma mensagem foi enviada" in t]
    losers = [t for t in texts if "Já concluído por" in t]
    assert len(winners) == 1 and len(losers) == 1
    assert _audit(env, "ticket.conclude") == 1
    assert _row(env, number)["answered_by"] in ("Aba A", "Aba B")


def test_concluir_ticket_ja_respondido_pelo_bot_avisa(
    env: Env, seeded: factories.PanelWorld
) -> None:
    number = _num(seeded, BOT)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        r = _post(c, number, "concluir", token, author="Caio")
    assert "não está aguardando atendimento" in r.text
    assert _row(env, number)["status"] == "answered_bot"


# ---- reclassificar (PN-18) e urgencia (PN-63) ------------------------------------------


def test_reclassificar_troca_audita_e_mantem_numero(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)  # PRODUTO
    with env.client() as c:
        login(c)
        html, token = _open(c, number)
        assert 'value="PRODUTO"' not in html  # a atual nao e oferecida
        r = _post(c, number, "reclassificar", token, classification="PRECO")
    assert "Classificação alterada de Produto para Preço" in r.text
    assert "O número do ticket não muda" in r.text and number in r.text
    assert _row(env, number)["classification"] == "PRECO"
    assert _audit(env, "ticket.reclassify") == 1


@pytest.mark.parametrize("value", ["PRODUTO", "XYZ", ""])
def test_reclassificar_recusa_atual_e_invalida(
    env: Env, seeded: factories.PanelWorld, value: str
) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        r = _post(c, number, "reclassificar", token, classification=value)
    assert r.status_code == 200 and 'role="alert"' in r.text
    assert _row(env, number)["classification"] == "PRODUTO"
    assert _audit(env, "ticket.reclassify") == 0


def test_urgencia_troca_e_continua_escalado(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESCALATED)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        r = _post(c, number, "urgencia", token, urgency="pouco_urgente")
        again = _post(c, number, "urgencia", token, urgency="pouco_urgente")
        bad = _post(c, number, "urgencia", token, urgency="nada")
    assert "Urgência definida como Pouco urgente" in r.text
    row = _row(env, number)
    assert row["urgency"] == "pouco_urgente" and row["escalated"] and row["status"] == "awaiting"
    assert _audit(env, "ticket.urgency") == 1  # repetir o mesmo valor nao audita de novo
    assert "hx-trigger" not in {k.lower() for k in again.headers}
    assert 'role="alert"' in bad.text


# ---- CSRF e sessao (PN-4) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "data"),
    [
        ("concluir", {"author": "Caio"}),
        ("reclassificar", {"classification": "PRECO"}),
        ("urgencia", {"urgency": "urgente"}),
    ],
)
def test_post_sem_csrf_ou_origin_e_403_e_nada_muda(
    env: Env, seeded: factories.PanelWorld, action: str, data: dict[str, str]
) -> None:
    number = _num(seeded, ESCALATED)
    before = _row(env, number)
    with env.client() as c:
        login(c)
        _, token = _open(c, number)
        no_token = c.post(f"{BASE}/{number}/{action}", data=data, headers={"Origin": ORIGIN})
        bad_origin = c.post(
            f"{BASE}/{number}/{action}",
            data=data,
            headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
        )
    assert no_token.status_code == 403 and bad_origin.status_code == 403
    after = _row(env, number)
    assert (after["status"], after["classification"], after["urgency"]) == (
        before["status"], before["classification"], before["urgency"])
    assert _audit(env, f"ticket.{ 'urgency' if action == 'urgencia' else action }") == 0


def test_post_sem_sessao_nao_executa(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, AWAITING)
    with env.client() as c:
        r = c.post(f"{BASE}/{number}/concluir", data={"author": "X"}, headers={"Origin": ORIGIN})
    assert r.status_code == 302
    assert _row(env, number)["status"] == "awaiting"


def test_nao_admin_nao_ve_o_drawer(env: Env, seeded: factories.PanelWorld) -> None:
    env.iam.email = "intruso@example.test"
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/{_num(seeded, AWAITING)}")
    assert r.status_code == 403 and "Pergunta de teste" not in r.text
    assert ADMIN not in r.text
