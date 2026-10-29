"""Sessao do painel sobre o IAM (PN-1, PN-3, PN-4; PN-T11)."""
from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.auth.iam import IamClient
from app.panel.auth.sessions import COOKIE_NAME, SessionService, hash_session_id
from tests.panel.fakes import IAM_BASE_URL, FakeIam

pytestmark = pytest.mark.db

EMAIL = "admin@example.test"
Parts = tuple[SessionService, FakeIam, FakeClock]


@pytest.fixture
def parts(db_pool: asyncpg.Pool, clock: FakeClock, iam: FakeIam) -> Parts:
    iam.email = EMAIL
    client = IamClient(iam.client(), clock, base_url=IAM_BASE_URL, cache_ttl_seconds=0.0)
    return SessionService(db_pool, clock, client), iam, clock


async def _row(pool: asyncpg.Pool, cookie: str) -> asyncpg.Record | None:
    async with pool.acquire() as c:
        return await c.fetchrow(
            "SELECT * FROM ops.panel_sessions WHERE id_hash = $1", hash_session_id(cookie)
        )


async def test_cria_com_cookie_opaco_e_so_o_hash_no_banco(
    parts: Parts, db_pool: asyncpg.Pool
) -> None:
    svc, _, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    assert COOKIE_NAME == "jarvis_panel"
    assert s.cookie is not None and len(s.cookie) >= 43  # 256 bits em base64url
    async with db_pool.acquire() as c:
        rows = await c.fetch("SELECT * FROM ops.panel_sessions")
    dump = " ".join(str(v) for r in rows for v in r.values())
    assert s.cookie not in dump
    row = await _row(db_pool, s.cookie)
    assert row is not None
    assert row["expires_at"] == clock.now() + timedelta(hours=12)
    assert row["csrf_secret"] == s.csrf_secret and row["revoked_at"] is None


async def test_resolve_devolve_sessao_viva_e_rejeita_desconhecido(parts: Parts) -> None:
    svc, _, _ = parts
    s = await svc.create(email=EMAIL, name="Admin")
    got = await svc.resolve(s.cookie, "iam")
    assert got is not None and got.email == EMAIL and got.csrf_secret == s.csrf_secret
    assert got.cookie is None
    assert await svc.resolve("inventado", "iam") is None
    assert await svc.resolve(None, "iam") is None
    assert await svc.resolve("", "iam") is None


async def test_ociosa_de_2h_expira_e_atividade_renova(parts: Parts) -> None:
    svc, _, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    for _ in range(3):  # 3 x 100 min: cada uso renova a janela ociosa
        clock.advance(minutes=100)
        assert await svc.resolve(s.cookie, "iam") is not None
    clock.advance(minutes=121)
    assert await svc.resolve(s.cookie, "iam") is None


async def test_absoluta_de_12h_expira_mesmo_com_atividade(parts: Parts) -> None:
    svc, _, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    for _ in range(6):
        clock.advance(minutes=110)
        assert await svc.resolve(s.cookie, "iam") is not None
    clock.advance(minutes=110)  # 12 h 10 min desde a criacao
    assert await svc.resolve(s.cookie, "iam") is None


async def test_revalida_no_iam_so_a_cada_5_minutos(parts: Parts) -> None:
    svc, iam, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    clock.advance(minutes=4)
    assert await svc.resolve(s.cookie, "iam") is not None
    assert iam.calls == 0
    clock.advance(minutes=2)
    assert await svc.resolve(s.cookie, "iam") is not None
    assert iam.calls == 1
    clock.advance(minutes=1)
    assert await svc.resolve(s.cookie, "iam") is not None
    assert iam.calls == 1  # a checagem reiniciou


async def test_401_do_iam_revoga_a_sessao(parts: Parts, db_pool: asyncpg.Pool) -> None:
    svc, iam, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    iam.mode = "unauthorized"
    clock.advance(minutes=6)
    assert await svc.resolve(s.cookie, "iam") is None
    row = await _row(db_pool, s.cookie or "")
    assert row is not None and row["revoked_at"] is not None
    iam.mode = "ok"  # revogada fica revogada
    assert await svc.resolve(s.cookie, "iam") is None


async def test_iam_fora_do_ar_nao_derruba_sessao_viva(
    parts: Parts, db_pool: asyncpg.Pool
) -> None:
    svc, iam, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    iam.mode = "timeout"
    clock.advance(minutes=6)
    assert await svc.resolve(s.cookie, "iam") is not None
    iam.mode = "server_error"
    clock.advance(minutes=6)
    assert await svc.resolve(s.cookie, "iam") is not None
    row = await _row(db_pool, s.cookie or "")
    assert row is not None and row["revoked_at"] is None
    iam.mode = "ok"
    clock.advance(minutes=1)
    assert await svc.resolve(s.cookie, "iam") is not None


async def test_iam_fora_nao_estende_alem_do_expires_at(parts: Parts) -> None:
    svc, iam, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    iam.mode = "timeout"
    for _ in range(6):
        clock.advance(minutes=110)
        assert await svc.resolve(s.cookie, "iam") is not None
    clock.advance(minutes=110)
    assert await svc.resolve(s.cookie, "iam") is None


async def test_identidade_diferente_no_iam_revoga(parts: Parts) -> None:
    svc, iam, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    iam.email = "outra@example.test"
    clock.advance(minutes=6)
    assert await svc.resolve(s.cookie, "iam") is None


async def test_sem_cookie_do_iam_na_revalidacao_revoga(parts: Parts) -> None:
    svc, _, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    clock.advance(minutes=6)
    assert await svc.resolve(s.cookie, None) is None


async def test_sair_revoga_e_e_idempotente(parts: Parts, db_pool: asyncpg.Pool) -> None:
    svc, _, clock = parts
    s = await svc.create(email=EMAIL, name="Admin")
    await svc.revoke(s.cookie)
    first = await _row(db_pool, s.cookie or "")
    clock.advance(minutes=1)
    await svc.revoke(s.cookie)
    await svc.revoke(None)
    second = await _row(db_pool, s.cookie or "")
    assert first is not None and second is not None
    assert first["revoked_at"] is not None
    assert first["revoked_at"] == second["revoked_at"]
    assert await svc.resolve(s.cookie, "iam") is None
