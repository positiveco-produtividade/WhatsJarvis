"""Resolucao de numero oculto pelo WAHA (spec canal-waha.1, WA-8; task WA-T09)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.clock import FakeClock
from app.core.log_redaction import RedactingFilter, register_secret
from app.services.waha_lid import LidResolver, extract_phone

LID = "100000000000001@lid"
PHONE = "5500000000002"
BASE = "http://127.0.0.1:3001"
API_KEY = "chave-waha-de-teste"


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result: httpx.Response = self.handler(request)
        return result


def make(handler: Any, clock: FakeClock | None = None, **kw: Any) -> tuple[LidResolver, Transport]:
    transport = Transport(handler)
    resolver = LidResolver(
        base_url=BASE + "/",
        api_key=SecretStr(API_KEY),
        session="jarvis",
        client=httpx.AsyncClient(transport=transport),
        clock=clock or FakeClock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC)),
        **kw,
    )
    return resolver, transport


def ok(body: Any) -> Any:
    return lambda request: httpx.Response(200, json=body)


@pytest.mark.parametrize(
    "body",
    [
        {"lid": LID, "pn": f"{PHONE}@c.us"},
        {"lid": LID, "phoneNumber": f"{PHONE}@s.whatsapp.net"},
        {"phone": f"+{PHONE}"},
        [{"lid": LID, "pn": f"{PHONE}@c.us"}],
    ],
)
async def test_resolve_formatos_aceitos(body: Any) -> None:
    resolver, transport = make(ok(body))
    assert await resolver.resolve(LID) == PHONE
    request = transport.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}/api/jarvis/lids/100000000000001%40lid"
    assert request.headers["X-Api-Key"] == API_KEY


@pytest.mark.parametrize(
    "body",
    [
        {"lid": LID, "pn": None},
        {"lid": LID},
        {"pn": "abc@c.us"},
        {"pn": "120363000000000000@g.us"},
        {"pn": "123@c.us"},
        [],
        [{"pn": f"{PHONE}@c.us"}, {"pn": "5500000000003@c.us"}],
        "texto",
    ],
)
def test_extrator_recusa_resposta_sem_telefone_valido(body: Any) -> None:
    assert extract_phone(body) is None


@pytest.mark.parametrize("status", [404, 401, 500, 503])
async def test_erro_http_nao_resolve(status: int) -> None:
    resolver, _ = make(lambda r: httpx.Response(status, json={"pn": f"{PHONE}@c.us"}))
    assert await resolver.resolve(LID) is None


async def test_json_invalido_nao_resolve() -> None:
    resolver, _ = make(lambda r: httpx.Response(200, content=b"nao e json"))
    assert await resolver.resolve(LID) is None


async def test_erro_de_rede_nao_levanta() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    resolver, _ = make(boom)
    assert await resolver.resolve(LID) is None


async def test_nao_lid_nem_consulta() -> None:
    resolver, transport = make(ok({"pn": f"{PHONE}@c.us"}))
    assert await resolver.resolve(f"{PHONE}@c.us") is None
    assert transport.requests == []


async def test_cache_resolvido_vale_24h() -> None:
    clock = FakeClock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
    resolver, transport = make(ok({"pn": f"{PHONE}@c.us"}), clock)
    assert await resolver.resolve(LID) == PHONE
    clock.advance(hours=23)
    assert await resolver.resolve(LID) == PHONE
    assert len(transport.requests) == 1
    clock.advance(hours=2)
    assert await resolver.resolve(LID) == PHONE
    assert len(transport.requests) == 2


async def test_cache_nao_resolvido_vale_5_min() -> None:
    clock = FakeClock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
    resolver, transport = make(lambda r: httpx.Response(404), clock)
    assert await resolver.resolve(LID) is None
    clock.advance(minutes=4)
    assert await resolver.resolve(LID) is None
    assert len(transport.requests) == 1
    clock.advance(minutes=2)
    await resolver.resolve(LID)
    assert len(transport.requests) == 2


async def test_cache_respeita_o_teto() -> None:
    resolver, transport = make(ok({"pn": f"{PHONE}@c.us"}), max_entries=2)
    for n in range(3):
        await resolver.resolve(f"10000000000000{n}@lid")
    assert resolver.cached("100000000000000@lid") == (False, None)  # o mais antigo saiu
    assert resolver.cached("100000000000002@lid") == (True, PHONE)
    assert len(transport.requests) == 3


async def test_log_sem_lid_nem_numero(caplog: pytest.LogCaptureFixture) -> None:
    """Como em producao: o RedactingFilter fica nos handlers (logging_json.setup_logging).

    O httpx loga a URL da consulta (com o lid no caminho) como objeto `URL`; o filtro precisa
    limpar tambem esse argumento (WA-20)."""
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(RedactingFilter())
    register_secret(API_KEY)
    resolver, _ = make(lambda r: httpx.Response(500, json={"pn": f"{PHONE}@c.us"}))
    await resolver.resolve(LID)
    text = " ".join(caplog.handler.format(r) for r in caplog.records)
    assert "waha_lid_lookup_failed" in text and "[LID]" in text
    assert "100000000000001" not in text and PHONE not in text and API_KEY not in text
