"""Desempenho do painel: p95 <= 2 s com a semente sintetica (PN-T64; spec NFR Desempenho).

Marcador `perf`: fora da suite padrao (pytest.ini); rode `pytest tests/panel/perf -m perf -q -s`.
Semeia no banco de TESTE e limpa com o TRUNCATE das fixtures de PF ao final.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.db_support import _truncate_all
from tests.panel.fakes import FakeIam
from tests.panel.integration.test_access import ADMIN, NOW, build_app, login, make_settings
from tests.panel.perf.seed import seed_perf

pytestmark = pytest.mark.perf

P95_LIMIT_S = 2.0
SAMPLES = 20
PAGES = {
    "Tickets": "/dashboard/tickets",
    "Inbox": "/dashboard/inbox",
    "Pessoas": "/dashboard/pessoas",
    "FAQ": "/dashboard/faq",
    "Metricas": "/dashboard/metrics",
}


def p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


@pytest.fixture(scope="module")
def perf_client(database_url: str) -> Iterator[TestClient]:
    from app.core.clock import FakeClock
    from tests.panel.integration.test_access import HOST, ORIGIN

    asyncio.run(_truncate_all(database_url))
    try:
        asyncio.run(seed_perf(database_url, NOW))
        app = build_app(make_settings(database_url), FakeIam(email=ADMIN), FakeClock(NOW))
        with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
            login(client)
            assert client.get(f"https://{HOST}/dashboard/tickets").status_code == 200
            yield client
    finally:
        asyncio.run(_truncate_all(database_url))


@pytest.mark.parametrize("page", list(PAGES))
def test_p95_ate_2s(perf_client: TestClient, page: str) -> None:
    url = PAGES[page]
    samples: list[float] = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        response = perf_client.get(url)
        samples.append(time.perf_counter() - start)
        assert response.status_code == 200
    result = p95(samples)
    print(f"\nPERF {page}: p95={result:.3f}s max={max(samples):.3f}s first={samples[0]:.3f}s")
    assert result <= P95_LIMIT_S
