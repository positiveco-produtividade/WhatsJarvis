"""Capacidades do canal e enquete na Meta (spec canal-waha.1, WA-25 e WA-3; task WA-T05)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services.channel import (
    META_CAPABILITIES,
    WAHA_CAPABILITIES,
    PollOption,
)
from app.services.whatsapp import WhatsAppCloudChannel
from tests.fakes import FakeChannel


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        channel_provider="meta",
        database_url="postgresql://u:p@localhost/x_test",
        whatsapp_app_secret="s",
        whatsapp_verify_token="v",
        whatsapp_access_token="t",
        whatsapp_phone_number_id="123",
    )


def _channel() -> tuple[WhatsAppCloudChannel, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(sent)}"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WhatsAppCloudChannel(_settings(), client=client), sent


BRANDS = [
    PollOption("BRAND:jungle", "Jungle"),
    PollOption("BRAND:possible", "Possible"),
    PollOption("BRAND:a_tal_da_castanha", "A Tal da Castanha"),
    PollOption("BRAND:zaya", "Zaya"),
]


def test_meta_tem_botoes_e_modelos() -> None:
    channel, _ = _channel()
    assert channel.capabilities == META_CAPABILITIES
    assert META_CAPABILITIES.buttons and META_CAPABILITIES.templates
    assert not WAHA_CAPABILITIES.buttons and not WAHA_CAPABILITIES.templates


@pytest.mark.parametrize("count", [1, 2, 3])
async def test_ate_tres_marcas_vira_botoes(count: int) -> None:
    channel, sent = _channel()
    result = await channel.send_poll("+5500000000002", "Sobre qual marca?", BRANDS[:count])
    assert result.ok
    payload = sent[0]
    assert payload["type"] == "interactive"
    assert payload["interactive"]["body"]["text"] == "Sobre qual marca?"
    buttons = payload["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == [o.id for o in BRANDS[:count]]
    assert [b["reply"]["title"] for b in buttons] == [o.title for o in BRANDS[:count]]


async def test_quatro_ou_mais_marcas_vira_texto_com_a_lista() -> None:
    channel, sent = _channel()
    result = await channel.send_poll("+5500000000002", "Sobre qual marca?", BRANDS)
    assert result.ok
    payload = sent[0]
    assert payload["type"] == "text"
    body = payload["text"]["body"]
    assert body.startswith("Sobre qual marca?")
    assert all(f"• {o.title}" in body for o in BRANDS)


async def test_enquete_sem_opcoes_e_erro_de_programacao() -> None:
    channel, sent = _channel()
    with pytest.raises(ValueError):
        await channel.send_poll("+5500000000002", "Sobre qual marca?", [])
    assert sent == []


async def test_fake_registra_enquete_e_capacidades() -> None:
    meta = FakeChannel()
    assert meta.capabilities == META_CAPABILITIES
    waha = FakeChannel(capabilities=WAHA_CAPABILITIES)
    assert waha.capabilities == WAHA_CAPABILITIES
    result = await waha.send_poll("+5500000000002", "Sobre qual marca?", BRANDS[:2])
    assert result.ok and waha.methods == ["send_poll"]
    call = waha.calls[0]
    assert call.body == "Sobre qual marca?" and call.options == tuple(BRANDS[:2])
