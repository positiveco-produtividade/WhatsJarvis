"""Pagina Metricas (PN-41, PN-43, CSP; task PN-T47) contra Postgres real."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from app.panel.services.metrics_read import DEFAULT_CACHE
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B
from tests.panel.integration.test_access import NOW, Env, env, login  # noqa: F401
from tests.panel.integration.test_metrics_read import _clean, _trace

pytestmark = pytest.mark.db

URL = "/dashboard/metrics"
HX = {"HX-Request": "true"}
DATA = re.compile(r'<script type="application/json" id="metrics-data">(.*?)</script>', re.S)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    DEFAULT_CACHE.clear()
    yield
    DEFAULT_CACHE.clear()


async def _seed(dsn: str, *, with_data: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        world = await factories.seed_world(conn, NOW)
        await _clean(conn)
        if not with_data:
            return
        user = world.sellers.one_brand
        await _trace(conn, user, NOW - timedelta(hours=1), latency_ms=2000)
        await _trace(conn, user, NOW - timedelta(days=1), latency_ms=4000)
        await _trace(conn, None, NOW - timedelta(hours=2), decision="unknown")
        for n, brand in enumerate([BRAND_A, BRAND_A, BRAND_B], start=1):
            await factories.insert_ticket(
                conn,
                number=900 + n,
                user_id=user,
                brand=brand,
                status="answered_bot",
                created_at=NOW - timedelta(hours=n),
            )
    finally:
        await conn.close()


def _chart(html: str) -> Any:
    match = DATA.search(html)
    assert match is not None
    return json.loads(match.group(1))


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        r = c.get(URL)
    assert r.status_code == 302


def test_pagina_com_dados_kpis_e_graficos(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "<title>Métricas" in html and 'aria-current="page"' in html
    for needle in ("Usuários ativos", "Tickets", "Resolvidos sem escalar", "Avaliados como úteis"):
        assert needle in html
    assert "sem avaliações" in html
    assert 'id="chart-days"' in html and 'id="chart-brands"' in html
    chart = _chart(html)
    assert len(chart["days"]["labels"]) == 30 and sum(chart["days"]["values"]) == 3
    assert sorted(chart["brands"]["values"]) == [1, 2]
    assert "Números desconhecidos" in html and "1 tentativa" in html
    assert "vendor/chart.min.js" in html and "js/metrics-charts.js" in html
    assert "cdn" not in html.lower() and "Sem dados neste período" not in html


def test_json_dos_graficos_fica_em_script_application_json(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert "script-src 'self'" in r.headers["content-security-policy"]
    bodies = re.findall(r"<script\b([^>]*)>(.*?)</script>", r.text, re.S)
    inline = [attrs for attrs, body in bodies if body.strip()]
    assert inline and all("application/json" in attrs for attrs in inline)


def test_estado_vazio_sem_grafico_quebrado(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=False))
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    html = r.text
    assert "Sem dados neste período" in html
    assert "<canvas" not in html and "metrics-data" not in html
    assert "NaN" not in html and "None" not in html
    assert "sem avaliações" in html


def test_periodo_hoje_7_e_invalido(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        hoje = c.get(URL, params={"periodo": "hoje"}, headers=HX)
        sete = c.get(URL, params={"periodo": "7"}, headers=HX)
        ruim = c.get(URL, params={"periodo": "999"}, headers=HX)
    assert len(_chart(hoje.text)["days"]["labels"]) == 1
    assert len(_chart(sete.text)["days"]["labels"]) == 7
    assert len(_chart(ruim.text)["days"]["labels"]) == 30


def test_htmx_devolve_so_o_resultado(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        r = c.get(URL, headers=HX)
    assert r.status_code == 200 and r.headers["vary"] == "HX-Request"
    assert "<html" not in r.text and 'id="chart-days"' in r.text


def test_marca_em_foco_filtra_tickets(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", BRAND_B, domain="jarvis.positiveco.com.br")
        r = c.get(URL)
    assert r.status_code == 200
    assert _chart(r.text)["brands"]["values"] == [1]
