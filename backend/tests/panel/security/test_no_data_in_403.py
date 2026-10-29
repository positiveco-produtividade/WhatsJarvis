"""Nenhuma recusa (302/401/403) carrega dado de pessoa, ticket ou sessao (PN-T14; PN-2).

Semeia nomes, telefones e textos de ticket com marcas unicas e varre TODAS as rotas do
painel (as mesmas do `test_route_sweep`) sem sessao, como nao-admin e como admin sem CSRF.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from tests.db_support import _truncate_all
from tests.panel import factories
from tests.panel.fakes import FakeIam
from tests.panel.security.test_route_sweep import (
    ADMIN,
    HOST,
    INTRUDER,
    ORIGIN,
    build_app,
    panel_targets,
)

pytestmark = [pytest.mark.security, pytest.mark.db]

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SECRET_NAME = "Zeferina Quixotesca Sigilosa"
SECRET_TEXT = "Pedido-confidencial-xyzzy sobre desconto especial"
REFUSALS = (302, 401, 403)


async def _seed(dsn: str) -> list[str]:
    """Pessoa e ticket com valores unicos; devolve as marcas que nunca podem vazar."""
    conn = await asyncpg.connect(dsn)
    try:
        uid = await factories.insert_user(
            conn, name=SECRET_NAME, seq=77, brands=[factories.BRAND_A], now=NOW
        )
        await factories.insert_ticket(
            conn, number=77, user_id=uid, status="awaiting", created_at=NOW, message=SECRET_TEXT
        )
        phone = await conn.fetchval("SELECT phone_e164 FROM access.users WHERE id = $1", uid)
    finally:
        await conn.close()
    return [SECRET_NAME, "Zeferina", SECRET_TEXT, "xyzzy", str(phone), str(phone).lstrip("+")]


@pytest.fixture
def iam() -> FakeIam:
    return FakeIam(email=ADMIN)


@pytest.fixture
def seeded(migrated_database: str) -> list[str]:
    asyncio.run(_truncate_all(migrated_database))
    return asyncio.run(_seed(migrated_database))


@pytest.fixture
def app(migrated_database: str, iam: FakeIam, seeded: list[str]) -> Iterator[FastAPI]:
    yield build_app(migrated_database, iam)


def _clean(r: Response, forbidden: list[str], context: str) -> list[str]:
    """Violacoes: dado semeado, e-mail ou token de CSRF no corpo / cabecalhos da recusa."""
    haystack = (r.text + " " + " ".join(f"{k}={v}" for k, v in r.headers.items())).lower()
    needles = [*forbidden, INTRUDER, ADMIN, "csrf_secret", "jarvis_panel="]
    return [f"{context}: vazou {n!r}" for n in needles if n.lower() in haystack]


def _sweep(
    c: TestClient, forbidden: list[str], *, headers: dict[str, str], only_unsafe: bool = False
) -> tuple[list[str], int]:
    leaks: list[str] = []
    refusals = 0
    for method, path in panel_targets(c.app):  # type: ignore[arg-type]
        if only_unsafe and method == "GET":
            continue
        r = c.request(method, path, headers=headers)
        if r.status_code in REFUSALS:
            refusals += 1
            leaks += _clean(r, forbidden, f"{method} {path} [{r.status_code}]")
    return leaks, refusals


def _client(app: FastAPI, *, iam_cookie: bool) -> TestClient:
    c = TestClient(app, base_url=ORIGIN, follow_redirects=False, raise_server_exceptions=False)
    if iam_cookie:
        c.cookies.set("iam_session", "iam-valido", domain=HOST)
    return c


def test_recusa_sem_sessao_nao_tem_dado(app: FastAPI, seeded: list[str]) -> None:
    with _client(app, iam_cookie=False) as c:
        leaks, refusals = _sweep(c, seeded, headers={"Origin": ORIGIN})
    assert refusals > 10
    assert not leaks, "\n".join(leaks)


def test_recusa_de_nao_admin_nao_tem_dado(app: FastAPI, iam: FakeIam, seeded: list[str]) -> None:
    iam.email = INTRUDER
    with _client(app, iam_cookie=True) as c:
        leaks, refusals = _sweep(c, seeded, headers={"Origin": ORIGIN})
    assert refusals > 10
    assert not leaks, "\n".join(leaks)


def test_recusa_de_admin_sem_csrf_nao_tem_dado(app: FastAPI, seeded: list[str]) -> None:
    with _client(app, iam_cookie=True) as c:
        leaks, refusals = _sweep(c, seeded, headers={"Origin": ORIGIN}, only_unsafe=True)
    assert refusals > 5
    assert not leaks, "\n".join(leaks)


def test_o_dado_semeado_existe_de_verdade_para_o_admin(app: FastAPI) -> None:
    """Guarda do teste: o admin logado enxerga a pessoa (senao a varredura seria vazia)."""
    with _client(app, iam_cookie=True) as c:
        r = c.get("/dashboard/pessoas", headers={"Origin": ORIGIN})
        body = r.text
    assert r.status_code == 200 and "Zeferina" in body, r.status_code
