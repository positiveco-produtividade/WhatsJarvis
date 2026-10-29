"""Pagina Logs e Erros: problemas, final do log em memoria, sem PII, so admin (PN-54; PN-T60)."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from datetime import timedelta

import asyncpg
import pytest

from app.panel.api import logs as logs_api
from app.panel.services import log_tail
from app.panel.services.log_tail import MAX_LINES, RingBufferHandler, get_ring
from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
)

PHONE = "+55 85 99999-1201"
PHONE_DIGITS = "5585999991201"
EMAIL = "pessoa.secreta@example.com"
SECRET = "senha=SuperSegredo12345"
BODY = "texto secreto da mensagem do vendedor"


@pytest.fixture
def ring(monkeypatch: pytest.MonkeyPatch) -> Iterator[RingBufferHandler]:
    """Anel isolado e ligado na raiz; a pagina le este, nao o do processo."""
    handler = RingBufferHandler()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    monkeypatch.setattr(logs_api, "get_ring", lambda: handler)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old_level)


# ---- buffer (unitario, sem banco) ---------------------------------------------------------


def test_registro_com_telefone_email_e_segredo_fica_redigido(ring: RingBufferHandler) -> None:
    logging.getLogger("t60").warning("ligou %s de %s %s", PHONE, EMAIL, SECRET)
    logging.getLogger("t60").warning("outro", extra={"to": PHONE_DIGITS})
    text = "\n".join(ring.lines())
    assert "ligou" in text
    for leaked in ("99999-1201", PHONE_DIGITS, EMAIL, "SuperSegredo12345"):
        assert leaked not in text
    assert "***1201" in text and "[EMAIL]" in text and "[REDIGIDO]" in text


def test_linhas_sao_json_com_a_mais_nova_por_ultimo(ring: RingBufferHandler) -> None:
    log = logging.getLogger("t60")
    log.warning("primeira")
    log.warning("segunda")
    lines = ring.lines()
    assert len(lines) == 2
    assert lines[0].startswith("{") and '"event": "primeira"' in lines[0]
    assert '"event": "segunda"' in lines[1]


def test_buffer_limita_em_500_e_descarta_as_mais_antigas() -> None:
    handler = RingBufferHandler()
    log = logging.getLogger("t60.cap")
    log.propagate = False
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        for i in range(MAX_LINES + 25):
            log.info("linha %d", i)
    finally:
        log.removeHandler(handler)
    lines = handler.lines()
    assert MAX_LINES == 500 and len(lines) == 500
    assert '"event": "linha 25"' in lines[0]
    assert f'"event": "linha {MAX_LINES + 24}"' in lines[-1]


def test_buffer_aguenta_escrita_concorrente() -> None:
    handler = RingBufferHandler(maxlen=50)
    log = logging.getLogger("t60.thr")
    log.propagate = False
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    def work() -> None:
        for i in range(200):
            log.info("n %d", i)

    threads = [threading.Thread(target=work) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        log.removeHandler(handler)
    assert len(handler.lines()) == 50


def test_get_ring_liga_uma_unica_vez_na_raiz() -> None:
    first = get_ring()
    second = get_ring()
    assert first is second
    assert logging.getLogger().handlers.count(first) == 1
    logging.getLogger().removeHandler(first)
    assert get_ring() is first and logging.getLogger().handlers.count(first) == 1
    assert log_tail.get_ring() is first


# ---- pagina -------------------------------------------------------------------------------


async def _seed_failures(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        user = await factories.insert_user(conn, name="Fulana", seq=7, brands=["jungle"], now=NOW)
        rows = [
            ("w1", "failed", "131047/2494073", "send_failed", "J-0001", user, 5),
            ("w2", None, None, "error", None, None, 3),
            ("w3", "sent", None, "answered", "J-0002", user, 1),
        ]
        for wa, result, err, decision, ticket, uid, mins in rows:
            await conn.execute(
                "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
                "access_decision, decision, decision_reason, body, send_result, send_error, "
                "ticket_number) VALUES ($1, $2, 'text', $3, $4, $5, $6, $7, $8, $9, $10)",
                wa, NOW - timedelta(minutes=mins), uid, "error" if uid is None else "allowed",
                decision, "sender_missing" if decision == "error" else None, BODY,
                result, err, ticket,
            )
    finally:
        await conn.close()


def test_pagina_vazia_mostra_estados_vazios(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logs_api, "get_ring", lambda: RingBufferHandler())
    with env.client() as c:
        login(c)
        page = c.get("/dashboard/logs")
        servidor = c.get("/dashboard/logs?aba=servidor")
    assert page.status_code == 200
    assert "Nenhum problema registrado." in page.text
    for tab in ("Problemas", "Servidor", "Auditoria", "Retenção"):
        assert tab in page.text
    assert "Nenhuma linha de log guardada" in servidor.text


def test_problemas_lista_falhas_com_telefone_mascarado_e_sem_corpo(env: Env) -> None:
    asyncio.run(_seed_failures(env.dsn))
    with env.client() as c:
        login(c)
        text = c.get("/dashboard/logs/problemas").text
    assert "J-0001" in text and "Falha de envio" in text and "131047/2494073" in text
    assert "Erro ao processar" in text and "sender_missing" in text
    assert "J-0002" not in text
    assert "+55 11 9••••-0007" in text
    assert "99999" not in text and BODY not in text
    assert text.index("sender_missing") < text.index("131047")  # mais recente primeiro


def test_pagina_inicial_traz_a_aba_problemas_sem_pii(env: Env) -> None:
    asyncio.run(_seed_failures(env.dsn))
    with env.client() as c:
        login(c)
        text = c.get("/dashboard/logs").text
    assert "J-0001" in text and "•" in text
    assert BODY not in text and "+5511900000007" not in text and "5511900000007" not in text


def test_servidor_mostra_o_final_do_log_redigido(env: Env, ring: RingBufferHandler) -> None:
    logging.getLogger("t60").error("falha para %s %s", PHONE, EMAIL)
    with env.client() as c:
        login(c)
        sub = c.get("/dashboard/logs/servidor").text
        full = c.get("/dashboard/logs?aba=servidor").text
    for text in (sub, full):
        assert "falha para" in text and "***1201" in text
        assert "99999-1201" not in text and EMAIL not in text
        assert "&#34;" in text  # JSON escapado pelo autoescape


def test_abas_auditoria_e_retencao_carregam_as_sub_rotas(env: Env) -> None:
    with env.client() as c:
        login(c)
        aud = c.get("/dashboard/logs?aba=auditoria").text
        ret = c.get("/dashboard/logs?aba=retencao").text
        invalid = c.get("/dashboard/logs?aba=xyz").text
    assert 'hx-get="/dashboard/logs/auditoria"' in aud
    assert 'hx-get="/dashboard/logs/retencao"' in ret
    assert "Nenhum problema registrado." in invalid


@pytest.mark.parametrize(
    "path", ["/dashboard/logs", "/dashboard/logs/problemas", "/dashboard/logs/servidor"]
)
def test_exige_admin(env: Env, path: str) -> None:
    with env.client() as c:
        assert c.get(path).status_code == 302
    env.iam.email = "intruso@example.test"
    with env.client() as c:
        login(c)
        r = c.get(path)
    assert r.status_code == 403
    assert "Nenhum" not in r.text

