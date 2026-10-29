"""Cliente IAM do painel (PN-T10; PN-1, PN-3). Sem rede: httpx.MockTransport."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from app.config import Settings
from app.panel.auth.iam import (
    BREAKER_OPEN_SECONDS,
    IamClient,
    IamUnavailableError,
    login_redirect_url,
    safe_next_url,
)
from tests.panel.fakes import IAM_BASE_URL, FakeClock, FakeIam

COOKIE = "cookie-secreto-123"
HOSTS = frozenset({"jarvis.positiveco.com.br", "accounts.positiveco.com.br"})
FALLBACK = "https://jarvis.positiveco.com.br/"


def make_client(iam: FakeIam, clock: FakeClock, cache_max_entries: int = 1024) -> IamClient:
    return IamClient(
        iam.client(), clock, base_url=IAM_BASE_URL, cache_max_entries=cache_max_entries
    )


async def test_ok_devolve_identidade_e_envia_cookie(iam: FakeIam, clock: FakeClock) -> None:
    client = make_client(iam, clock)
    result = await client.whoami(COOKIE)
    assert result is not None
    assert result.email == "admin@example.test"
    assert result.user_id == 1
    request = iam.requests[0]
    assert request.url.path == "/api/v1/auth/whoami"
    assert request.headers["cookie"] == f"iam_session={COOKIE}"


async def test_401_devolve_none_e_nao_entra_no_cache(iam: FakeIam, clock: FakeClock) -> None:
    iam.mode = "unauthorized"
    client = make_client(iam, clock)
    assert await client.whoami(COOKIE) is None
    assert await client.whoami(COOKIE) is None
    assert iam.calls == 2


@pytest.mark.parametrize("cookie", ["", "x" * 129])
async def test_cookie_vazio_ou_longo_nao_chama_iam(
    iam: FakeIam, clock: FakeClock, cookie: str
) -> None:
    assert await make_client(iam, clock).whoami(cookie) is None
    assert iam.calls == 0


async def test_cache_30s_expira(iam: FakeIam, clock: FakeClock) -> None:
    client = make_client(iam, clock)
    await client.whoami(COOKIE)
    clock.advance(seconds=29)
    await client.whoami(COOKIE)
    assert iam.calls == 1
    clock.advance(seconds=2)
    await client.whoami(COOKIE)
    assert iam.calls == 2


async def test_cache_lru_limitado(iam: FakeIam, clock: FakeClock) -> None:
    client = make_client(iam, clock, cache_max_entries=2)
    for cookie in ("a", "b", "a", "c"):  # "a" recente; "b" sai quando "c" entra
        await client.whoami(cookie)
    assert iam.calls == 3
    await client.whoami("a")
    assert iam.calls == 3
    await client.whoami("b")
    assert iam.calls == 4


async def test_single_flight_uma_chamada_para_varias(iam: FakeIam, clock: FakeClock) -> None:
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return iam._handle(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = IamClient(http, clock, base_url=IAM_BASE_URL)
    tasks = [asyncio.create_task(client.whoami(COOKIE)) for _ in range(5)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert iam.calls == 1
    assert all(r is not None and r.email == "admin@example.test" for r in results)


async def test_single_flight_propaga_falha_e_libera(iam: FakeIam, clock: FakeClock) -> None:
    gate = asyncio.Event()
    iam.mode = "timeout"

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return iam._handle(request)

    client = IamClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock, base_url=IAM_BASE_URL
    )
    tasks = [asyncio.create_task(client.whoami(COOKIE)) for _ in range(3)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert iam.calls == 1
    assert all(isinstance(r, IamUnavailableError) for r in results)
    iam.mode = "ok"
    assert await client.whoami(COOKIE) is not None  # nada ficou preso em andamento


@pytest.mark.parametrize("mode", ["timeout", "server_error"])
async def test_falha_levanta_indisponivel(iam: FakeIam, clock: FakeClock, mode: str) -> None:
    iam.mode = mode  # type: ignore[assignment]
    with pytest.raises(IamUnavailableError):
        await make_client(iam, clock).whoami(COOKIE)


async def test_corpo_invalido_e_indisponivel(clock: FakeClock) -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"email": "x"}))
    )
    client = IamClient(http, clock, base_url=IAM_BASE_URL)
    with pytest.raises(IamUnavailableError):
        await client.whoami(COOKIE)


async def test_disjuntor_abre_apos_3_falhas_e_fecha_em_10s(
    iam: FakeIam, clock: FakeClock
) -> None:
    iam.mode = "timeout"
    client = make_client(iam, clock)
    for i in range(3):
        with pytest.raises(IamUnavailableError):
            await client.whoami(f"c{i}")
    assert iam.calls == 3
    assert client.breaker_open
    with pytest.raises(IamUnavailableError, match="disjuntor"):
        await client.whoami("outro")
    assert iam.calls == 3  # rejeitada sem rede
    iam.mode = "ok"
    clock.advance(seconds=BREAKER_OPEN_SECONDS + 1)
    assert not client.breaker_open
    assert await client.whoami("outro") is not None
    assert iam.calls == 4


async def test_duas_falhas_nao_abrem_e_sucesso_zera(iam: FakeIam, clock: FakeClock) -> None:
    client = make_client(iam, clock)
    iam.mode = "server_error"
    for i in range(2):
        with pytest.raises(IamUnavailableError):
            await client.whoami(f"c{i}")
    iam.mode = "ok"
    await client.whoami("bom")
    iam.mode = "server_error"
    for i in range(2):
        with pytest.raises(IamUnavailableError):
            await client.whoami(f"d{i}")
    assert not client.breaker_open


async def test_cache_vale_com_disjuntor_aberto(iam: FakeIam, clock: FakeClock) -> None:
    client = make_client(iam, clock)
    await client.whoami(COOKIE)
    iam.mode = "timeout"
    for i in range(3):
        with pytest.raises(IamUnavailableError):
            await client.whoami(f"c{i}")
    assert await client.whoami(COOKIE) is not None


async def test_timeout_de_3s_vai_na_requisicao(clock: FakeClock) -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(401)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await IamClient(http, clock, base_url=IAM_BASE_URL).whoami(COOKIE)
    assert seen[0] == {"connect": 3.0, "read": 3.0, "write": 3.0, "pool": 3.0}


async def test_log_nao_vaza_cookie_nem_email(
    iam: FakeIam, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = make_client(iam, clock)
    await client.whoami(COOKIE)
    iam.mode = "timeout"
    for i in range(3):
        with pytest.raises(IamUnavailableError) as info:
            await client.whoami(f"c{i}")
        assert f"c{i}" not in str(info.value)
    assert COOKIE not in caplog.text
    assert "admin@example.test" not in caplog.text


@pytest.mark.parametrize(
    "base",
    [
        "http://accounts.positiveco.com.br",
        "https://evil.example.com",
        "https://accounts.positiveco.com.br.evil.com",
        "https://127.0.0.1",
        "https://169.254.169.254",
    ],
)
def test_ssrf_base_url_recusada(iam: FakeIam, clock: FakeClock, base: str) -> None:
    with pytest.raises(ValueError, match="allow-list"):
        IamClient(iam.client(), clock, base_url=base)


def test_config_recusa_mesmos_hosts() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate(
            {
                "database_url": "postgresql://x",
                "whatsapp_app_secret": "s",
                "whatsapp_verify_token": "v",
                "whatsapp_access_token": "t",
                "whatsapp_phone_number_id": "1",
                "iam_base_url": "https://evil.example.com",
            }
        )


@pytest.mark.parametrize(
    "candidate",
    [
        "https://jarvis.positiveco.com.br/dashboard?x=1",
        "https://accounts.positiveco.com.br/",
    ],
)
def test_next_permitido(candidate: str) -> None:
    assert safe_next_url(candidate, allowed_hosts=HOSTS, fallback=FALLBACK) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "http://jarvis.positiveco.com.br/",
        "https://evil.example.com/",
        "https://jarvis.positiveco.com.br.evil.com/",
        "https://jarvis.positiveco.com.br@evil.com/",
        "https://evil.com@jarvis.positiveco.com.br/",
        "https://jarvis.positiveco.com.br\\@evil.com/",
        "//evil.com/",
        "/dashboard",
        "javascript:alert(1)",
        "https://jarvis.positiveco.com.br/\r\nSet-Cookie: a=b",
        "https://jarvis.positiveco.com.br:bad/",
        "https://jarvis.positiveco.com.br/" + "a" * 1800,
    ],
)
def test_next_invalido_cai_no_fallback(candidate: str) -> None:
    assert safe_next_url(candidate, allowed_hosts=HOSTS, fallback=FALLBACK) == FALLBACK


def test_login_redirect_url_codifica_next() -> None:
    url = login_redirect_url(
        "https://jarvis.positiveco.com.br/dashboard?a=1",
        iam_base_url=IAM_BASE_URL,
        allowed_hosts=HOSTS,
        fallback=FALLBACK,
    )
    assert url == (
        "https://accounts.positiveco.com.br/login?next="
        "https%3A%2F%2Fjarvis.positiveco.com.br%2Fdashboard%3Fa%3D1"
    )


def test_login_redirect_url_next_externo_vira_fallback() -> None:
    url = login_redirect_url(
        "https://evil.example.com/",
        iam_base_url=IAM_BASE_URL,
        allowed_hosts=HOSTS,
        fallback=FALLBACK,
    )
    assert "evil" not in url
    assert url.endswith("https%3A%2F%2Fjarvis.positiveco.com.br%2F")
