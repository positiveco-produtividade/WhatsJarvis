"""PF-10: clientes Resend e WAHA com httpx.MockTransport (sem rede)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services import notifications, waha
from app.services.notifications import DeliveryStatus

RECIPIENT = "destino-secreto@example.com"
CHAT_ID = "120363000000000000@g.us"

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "resend_api_key": SecretStr("re_key"),
        "resend_from_email": "jarvis@example.com",
        "alert_recipient_email": SecretStr(RECIPIENT),
        "waha_api_key": SecretStr("waha_key"),
        "team_group_chat_id": CHAT_ID,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def status_handler(code: int, text: str = "") -> Handler:
    return lambda _r: httpx.Response(code, text=text)


def raising(exc: Exception) -> Handler:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.fixture(autouse=True)
def _rearm() -> Iterator[None]:
    notifications.reset_skip_warning()
    yield


async def test_email_success_payload_and_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"id": "em_1"})

    res = await notifications.send_email(
        "Assunto", "Corpo", settings=make_settings(), client=client_for(handler)
    )
    assert res.ok and res.message_id == "em_1"
    body = json.loads(seen[0].content)
    assert body["to"] == [RECIPIENT] and body["from"] == "jarvis@example.com"
    assert seen[0].headers["authorization"] == "Bearer re_key"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (500, DeliveryStatus.TRANSIENT),
        (503, DeliveryStatus.TRANSIENT),
        (429, DeliveryStatus.TRANSIENT),
        (422, DeliveryStatus.PERMANENT),
        (401, DeliveryStatus.PERMANENT),
    ],
)
async def test_email_http_error_classification(code: int, expected: DeliveryStatus) -> None:
    res = await notifications.send_email(
        "a", "b", settings=make_settings(), client=client_for(status_handler(code, "erro"))
    )
    assert res.status is expected and res.detail == f"http_{code}"


async def test_email_timeout_and_network_error_are_transient_and_do_not_raise() -> None:
    r1 = await notifications.send_email(
        "a", "b", settings=make_settings(), client=client_for(raising(httpx.ReadTimeout("t")))
    )
    r2 = await notifications.send_email(
        "a", "b", settings=make_settings(), client=client_for(raising(httpx.ConnectError("c")))
    )
    assert (r1.status, r1.detail) == (DeliveryStatus.TRANSIENT, "timeout")
    assert (r2.status, r2.detail) == (DeliveryStatus.TRANSIENT, "network_error")


async def test_email_skipped_without_key_or_recipient_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    never = raising(AssertionError("nao deveria chamar a rede"))
    for over in ({"resend_api_key": SecretStr("")}, {"alert_recipient_email": SecretStr("")}):
        res = await notifications.send_email(
            "a", "b", settings=make_settings(**over), client=client_for(never)
        )
        assert res.status is DeliveryStatus.SKIPPED
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_email_recipient_never_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    handlers = [
        status_handler(500, f"falha para {RECIPIENT}"),
        status_handler(422, f"invalido {RECIPIENT}"),
        raising(httpx.ConnectError(f"x {RECIPIENT}")),
    ]
    for handler in handlers:
        await notifications.send_email(
            "a", "b", settings=make_settings(), client=client_for(handler)
        )
    await notifications.send_email("a", "b", settings=make_settings(resend_api_key=SecretStr("")))
    assert caplog.records
    assert RECIPIENT not in caplog.text
    assert "destino-secreto" not in caplog.text


async def test_waha_success_payload_headers_and_id() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"id": {"_serialized": "true_x_abc"}})

    res = await waha.send_group_text("ola", settings=make_settings(), client=client_for(handler))
    assert res.ok and res.message_id == "true_x_abc"
    assert seen[0].url.path == "/api/sendText"
    assert seen[0].headers["x-api-key"] == "waha_key"
    assert json.loads(seen[0].content) == {"session": "default", "chatId": CHAT_ID, "text": "ola"}


async def test_waha_201_empty_body_is_ok() -> None:
    res = await waha.send_group_text(
        "x", settings=make_settings(), client=client_for(status_handler(201))
    )
    assert res.ok and res.message_id is None


async def test_waha_failures_typed_and_logs_have_no_chat_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    cfg = make_settings()
    r5 = await waha.send_group_text("x", settings=cfg, client=client_for(status_handler(503)))
    r4 = await waha.send_group_text("x", settings=cfg, client=client_for(status_handler(404)))
    rt = await waha.send_group_text(
        "x", settings=cfg, client=client_for(raising(httpx.ConnectTimeout("t")))
    )
    assert [r5.status, r4.status, rt.status] == [
        DeliveryStatus.TRANSIENT,
        DeliveryStatus.PERMANENT,
        DeliveryStatus.TRANSIENT,
    ]
    assert CHAT_ID not in caplog.text


async def test_waha_skipped_when_not_configured() -> None:
    res = await waha.send_group_text("x", settings=make_settings(waha_api_key=SecretStr("")))
    assert res.status is DeliveryStatus.SKIPPED
    res = await waha.send_group_text("x", settings=make_settings(team_group_chat_id=""))
    assert res.status is DeliveryStatus.SKIPPED
