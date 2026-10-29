"""Pagina Perguntas do assistente (PN-53, PN-43; task PN-T58) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.panel.integration.test_access import NOW, Env, env, login, sql  # noqa: F401

pytestmark = pytest.mark.db

PATH = "/dashboard/assistente-perguntas"
EMPTY = "Nenhuma pergunta ao assistente ainda."
_INSERT = (
    "INSERT INTO ops.assistant_questions (asked_at, asked_by, agent, question, answer, status, "
    "error_code) VALUES ($1, $2, $3, $4, $5, $6, $7)"
)


def add(env: Env, who: str, question: str, *, days: int = 1, ok: bool = True) -> None:
    sql(
        env.dsn, _INSERT, NOW - timedelta(days=days), who, "Jarvis", question,
        "Resposta <b>x</b>" if ok else None, "ok" if ok else "error", None if ok else "timeout",
    )


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:
    with env.client() as c:
        r = c.get(PATH)
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]


def test_vazio_mostra_zeros_e_estado_vazio(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(PATH)
    assert r.status_code == 200 and EMPTY in r.text
    assert 'id="perg-total">0<' in r.text and 'id="perg-pessoas">0<' in r.text


def test_contadores_30_dias_e_pessoas_distintas(env: Env) -> None:
    add(env, "a@example.test", "Q1")
    add(env, "a@example.test", "Q2", days=5)
    add(env, "b@example.test", "Q3", days=10)
    add(env, "c@example.test", "Antiga", days=40)
    with env.client() as c:
        login(c)
        html = c.get(PATH).text
    assert 'id="perg-total">3<' in html and 'id="perg-pessoas">2<' in html
    assert EMPTY not in html and "Q1" in html and "Antiga" in html


def test_falha_registrada_e_texto_escapado(env: Env) -> None:
    add(env, "a@example.test", "Pergunta <script>", ok=True)
    add(env, "a@example.test", "Falhou?", ok=False, days=2)
    with env.client() as c:
        login(c)
        html = c.get(PATH).text
    assert "<script>" not in html.split("pergList")[1]
    assert "Resposta &lt;b&gt;x&lt;/b&gt;" in html
    assert "Não foi possível responder esta pergunta." in html and "(timeout)" in html


def test_mais_recente_primeiro(env: Env) -> None:
    add(env, "a@example.test", "ANTIGA-QQ", days=3)
    add(env, "a@example.test", "NOVA-QQ", days=1)
    with env.client() as c:
        login(c)
        html = c.get(PATH).text
    assert html.index("NOVA-QQ") < html.index("ANTIGA-QQ")
