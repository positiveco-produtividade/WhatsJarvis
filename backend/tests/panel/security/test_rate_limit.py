"""Limite do painel: 240 requisicoes por 60 s por IP (spec 4.4 "Seguranca"; task PN-T63).

Padrao do AUTO-1: janela deslizante em memoria, chave = IP (`X-Real-IP` do nginx, senao o peer),
resposta 429 com `Retry-After`. O limite vale nas rotas do painel; o webhook tem limitador proprio.

ATENCAO: o limitador por IP do painel AINDA NAO EXISTE no codigo do app (so ha o do webhook e
o de 12 perguntas/min do assistente). Estes testes ficam FALHANDO de proposito ate a tarefa que
o implementar; os nomes dizem o que falta. Nao ha `xfail`: a falha e o aviso.
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.panel.integration.test_access import Env, env, login  # noqa: F401

pytestmark = [pytest.mark.security, pytest.mark.db]

LIMIT = 240
WINDOW_SECONDS = 60
ROUTE = "/dashboard/estado"  # leve; qualquer rota do painel serve
OK_STATUSES = frozenset({200, 302, 303})


def _hit(c: TestClient, ip: str | None = None, path: str = ROUTE) -> int:
    headers = {"X-Real-IP": ip} if ip else {}
    return c.get(path, headers=headers).status_code


def _burst(c: TestClient, n: int, ip: str | None = None) -> list[int]:
    return [_hit(c, ip) for _ in range(n)]


def test_painel_aceita_240_requisicoes_por_ip_em_60s(env: Env) -> None:
    with env.client() as c:
        login(c)
        statuses = _burst(c, LIMIT, "203.0.113.10")
    assert 429 not in statuses
    assert all(s in OK_STATUSES for s in statuses), set(statuses)


def test_painel_responde_429_com_retry_after_na_requisicao_241(env: Env) -> None:
    with env.client() as c:
        login(c)
        _burst(c, LIMIT, "203.0.113.11")
        blocked = c.get(ROUTE, headers={"X-Real-IP": "203.0.113.11"})
    assert blocked.status_code == 429, "falta o limitador de 240/60 s por IP nas rotas do painel"
    retry = int(blocked.headers["Retry-After"])
    assert 1 <= retry <= WINDOW_SECONDS
    assert blocked.headers.get("cache-control") == "no-store, private"


def test_painel_limite_e_por_ip_e_um_ip_nao_bloqueia_outro(env: Env) -> None:
    with env.client() as c:
        login(c)
        _burst(c, LIMIT + 1, "203.0.113.12")
        other = _hit(c, "203.0.113.13")
        same = _hit(c, "203.0.113.12")
    assert same == 429, "falta o limitador de 240/60 s por IP nas rotas do painel"
    assert other in OK_STATUSES


def test_painel_libera_o_ip_depois_da_janela_de_60s(env: Env) -> None:
    with env.client() as c:
        login(c)
        _burst(c, LIMIT + 1, "203.0.113.14")
        assert _hit(c, "203.0.113.14") == 429, "falta o limitador de 240/60 s por IP"
        env.clock.advance(seconds=WINDOW_SECONDS + 1)
        after = _hit(c, "203.0.113.14")
    assert after in OK_STATUSES


def test_painel_limite_vale_tambem_para_quem_nao_tem_sessao(env: Env) -> None:
    with env.client() as c:  # sem login: o gate de sessao responde 302, o limite vem antes
        statuses = _burst(c, LIMIT + 1, "203.0.113.15")
    assert statuses[-1] == 429, "falta o limitador de 240/60 s por IP nas rotas do painel"


def test_painel_ignora_x_forwarded_for_na_chave_do_ip(env: Env) -> None:
    with env.client() as c:
        login(c)
        for i in range(LIMIT + 1):
            forged = {"X-Real-IP": "203.0.113.16", "X-Forwarded-For": f"10.0.0.{i % 250}"}
            c.get(ROUTE, headers=forged)
        blocked = c.get(
            ROUTE, headers={"X-Real-IP": "203.0.113.16", "X-Forwarded-For": "198.51.100.77"}
        )
    assert blocked.status_code == 429, "falta o limitador de 240/60 s por IP nas rotas do painel"
