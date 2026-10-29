import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.core.security import compute_hub_signature
from app.services.channel import Channel, ReplyButton, SendResult, TemplateMessage
from app.services.whatsapp import (
    InvalidGraphBase,
    WhatsAppCloudChannel,
    build_welcome_template,
    validate_graph_base,
)
from tests.fakes import FakeChannel

APP_SECRET = "app-secret-xyz"
TOKEN = "access-token-xyz-1234567890"
PHONE_ID = "123456789"
TO = "5511987654321"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://jarvis:s3cr3t-db@localhost:5432/jarvis",
        "whatsapp_app_secret": APP_SECRET,
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": TOKEN,
        "whatsapp_phone_number_id": PHONE_ID,
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


def make_channel(handler: Handler) -> WhatsAppCloudChannel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WhatsAppCloudChannel(make_settings(), client=client)


def ok_handler(captured: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    return handler


def error_response(status: int, code: int, sub: int | None = None) -> httpx.Response:
    error: dict[str, object] = {"message": "x", "code": code}
    if sub is not None:
        error["error_subcode"] = sub
    return httpx.Response(status, json={"error": error})


def raising(exc: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_welcome_template_builder_payloads() -> None:
    tpl = build_welcome_template("boas_vindas", "pt_BR", "Maria", 42)
    assert tpl.name == "boas_vindas"
    assert tpl.language == "pt_BR"
    assert tpl.body_params == ("Maria",)
    assert tpl.quick_reply_payloads == ("OPTIN_ACCEPT:42", "OPTIN_DECLINE:42")


async def test_send_template_payload_and_success() -> None:
    captured: list[httpx.Request] = []
    channel = make_channel(ok_handler(captured))
    tpl = build_welcome_template("boas_vindas", "pt_BR", "Maria", 7)
    result = await channel.send_template("+" + TO, tpl)
    assert result == SendResult(ok=True, wa_message_id="wamid.ABC")
    req = captured[0]
    assert str(req.url) == f"https://graph.facebook.com/v25.0/{PHONE_ID}/messages"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == TO
    assert body["type"] == "template"
    template = body["template"]
    assert template["name"] == "boas_vindas"
    assert template["language"] == {"code": "pt_BR"}
    body_comp, accept, decline = template["components"]
    assert body_comp == {"type": "body", "parameters": [{"type": "text", "text": "Maria"}]}
    assert accept == {
        "type": "button",
        "sub_type": "quick_reply",
        "index": "0",
        "parameters": [{"type": "payload", "payload": "OPTIN_ACCEPT:7"}],
    }
    assert decline["index"] == "1"
    assert decline["parameters"] == [{"type": "payload", "payload": "OPTIN_DECLINE:7"}]


async def test_send_text_payload() -> None:
    captured: list[httpx.Request] = []
    channel = make_channel(ok_handler(captured))
    result = await channel.send_text(TO, "ola" + "x" * 5000)
    assert result.ok is True
    body = json.loads(captured[0].content)
    assert body["type"] == "text"
    assert len(body["text"]["body"]) == 4096


async def test_send_reply_buttons_payload() -> None:
    captured: list[httpx.Request] = []
    channel = make_channel(ok_handler(captured))
    buttons = [ReplyButton("OPTIN_ACCEPT:1", "Aceito"), ReplyButton("OPTIN_DECLINE:1", "Nao")]
    result = await channel.send_reply_buttons(TO, "Aceita?", buttons)
    assert result.ok is True
    body = json.loads(captured[0].content)
    assert body["type"] == "interactive"
    interactive = body["interactive"]
    assert interactive["type"] == "button"
    assert interactive["body"] == {"text": "Aceita?"}
    assert interactive["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "OPTIN_ACCEPT:1", "title": "Aceito"}},
        {"type": "reply", "reply": {"id": "OPTIN_DECLINE:1", "title": "Nao"}},
    ]


async def test_reply_buttons_rejects_invalid_buttons() -> None:
    channel = make_channel(ok_handler([]))
    with pytest.raises(ValueError):
        await channel.send_reply_buttons(TO, "x", [])
    with pytest.raises(ValueError):
        await channel.send_reply_buttons(TO, "x", [ReplyButton("a", "t" * 21)])
    with pytest.raises(ValueError):
        await channel.send_reply_buttons(TO, "x", [ReplyButton(str(i), "t") for i in range(4)])


@pytest.mark.parametrize("status", [429, 503])
async def test_http_429_503_are_transient(status: int) -> None:
    channel = make_channel(lambda r: httpx.Response(status, json={}))
    result = await channel.send_text(TO, "oi")
    assert (result.ok, result.transient, result.ambiguous) == (False, True, False)
    assert result.error_code == f"http_{status}"


async def test_meta_rate_limit_code_on_4xx_is_transient() -> None:
    channel = make_channel(lambda r: error_response(400, 130429))
    result = await channel.send_text(TO, "oi")
    assert result.transient is True
    assert result.error_code == "130429"


async def test_4xx_is_definitive_with_raw_code_and_subcode() -> None:
    channel = make_channel(lambda r: error_response(400, 131030, 2494073))
    result = await channel.send_text(TO, "oi")
    assert result == SendResult(
        ok=False, error_code="131030", error_subcode="2494073", transient=False, ambiguous=False
    )


async def test_4xx_without_json_body_is_definitive() -> None:
    channel = make_channel(lambda r: httpx.Response(401, text="nope"))
    result = await channel.send_text(TO, "oi")
    assert (result.ok, result.transient, result.ambiguous) == (False, False, False)
    assert result.error_code == "http_401"


async def test_other_5xx_is_ambiguous() -> None:
    channel = make_channel(lambda r: httpx.Response(502))
    result = await channel.send_text(TO, "oi")
    assert (result.transient, result.ambiguous) == (False, True)


async def test_2xx_without_message_id_is_ambiguous() -> None:
    channel = make_channel(lambda r: httpx.Response(200, json={}))
    result = await channel.send_text(TO, "oi")
    assert (result.ok, result.ambiguous) == (False, True)


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_connect_failures_are_transient(exc_type: type[httpx.TransportError]) -> None:
    channel = make_channel(raising(exc_type("boom")))
    result = await channel.send_text(TO, "oi")
    assert (result.ok, result.transient, result.ambiguous) == (False, True, False)
    assert result.error_code == exc_type.__name__


async def test_read_timeout_is_ambiguous() -> None:
    channel = make_channel(raising(httpx.ReadTimeout("slow")))
    result = await channel.send_text(TO, "oi")
    assert (result.ok, result.transient, result.ambiguous) == (False, False, True)
    assert result.error_code == "ReadTimeout"


def test_graph_base_allow_list() -> None:
    assert validate_graph_base("https://graph.facebook.com/") == "https://graph.facebook.com"
    for bad in (
        "https://evil.example.com",
        "http://graph.facebook.com",
        "https://graph.facebook.com.evil.example",
    ):
        with pytest.raises(InvalidGraphBase):
            validate_graph_base(bad)


async def test_channel_refuses_host_outside_allow_list() -> None:
    settings = make_settings()
    settings.whatsapp_api_base = "https://evil.example.com"
    with pytest.raises(InvalidGraphBase):
        WhatsAppCloudChannel(settings)


@pytest.mark.security
def test_verify_signature_over_raw_bytes() -> None:
    channel = make_channel(ok_handler([]))
    raw = b'{"a": "\xc3\xa7"}'
    assert channel.verify_signature(raw, compute_hub_signature(raw, APP_SECRET)) is True
    assert channel.verify_signature(raw + b" ", compute_hub_signature(raw, APP_SECRET)) is False
    assert channel.verify_signature(raw, "sha256=00") is False


@pytest.mark.security
async def test_failure_log_has_no_token_or_phone(caplog: pytest.LogCaptureFixture) -> None:
    channel = make_channel(lambda r: error_response(400, 131030))
    with caplog.at_level(logging.WARNING, logger="jarvis.whatsapp"):
        await channel.send_text(TO, "segredo de conversa")
    text = " ".join(
        f"{r.getMessage()} {sorted(r.__dict__.items(), key=str)}" for r in caplog.records
    )
    assert "131030" in text
    assert TOKEN not in text
    assert TO not in text
    assert "segredo de conversa" not in text


async def test_fake_channel_records_order_and_fails_on_command() -> None:
    fake = FakeChannel()
    channel: Channel = fake
    tpl = TemplateMessage("t", "pt_BR")
    assert (await channel.send_template(TO, tpl)).ok is True
    fake.fail_next("transient", method="send_text")
    assert (await channel.send_text(TO, "a")).transient is True
    assert (await channel.send_text(TO, "b")).ok is True
    fake.fail_always("ambiguous")
    assert (await channel.send_reply_buttons(TO, "c", [ReplyButton("i", "t")])).ambiguous is True
    fake.clear_failures()
    assert (await channel.send_text(TO, "d")).ok is True
    assert fake.methods == [
        "send_template",
        "send_text",
        "send_text",
        "send_reply_buttons",
        "send_text",
    ]
    assert fake.calls[0].template == tpl
    assert fake.verify_signature(b"x", compute_hub_signature(b"x", "fake-app-secret")) is True
