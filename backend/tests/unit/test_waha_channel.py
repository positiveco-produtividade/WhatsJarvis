"""Cliente WahaChannel (spec canal-waha.1, WA-17, WA-18, WA-20; task WA-T08)."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.core.security import compute_waha_hmac
from app.services.channel import WAHA_CAPABILITIES, PollOption, ReplyButton, TemplateMessage
from app.services.waha_channel import (
    WahaChannel,
    chat_id,
    classify_response,
    extract_message_id,
    is_restriction,
)

API_KEY = "chave-waha-de-teste"
HMAC_KEY = "hmac-waha-de-teste"
TO = "+5500000000002"
CHAT = "5500000000002@c.us"
BASE = "http://127.0.0.1:3001"


def _settings(**overrides: Any) -> Settings:
    data: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/x_test",
        "channel_provider": "waha",
        "jarvis_waha_api_key": API_KEY,
        "jarvis_waha_hmac_key": HMAC_KEY,
        "jarvis_whatsapp_number": "+5500000000001",
        **overrides,
    }
    return Settings(_env_file=None, **data)  # type: ignore[arg-type]


class Recorder:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.responses = responses or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((endpoint, json.loads(request.content), request.headers["X-Api-Key"]))
        reply = self.responses.get(endpoint)
        if callable(reply):
            result: httpx.Response = reply(request)
            return result
        if isinstance(reply, httpx.Response):
            return reply
        message_id = f"true_{CHAT}_ID{len(self.calls)}"
        return httpx.Response(200, json={"id": {"_serialized": message_id}})

    @property
    def endpoints(self) -> list[str]:
        return [c[0] for c in self.calls]


def _channel(rec: Recorder, **kw: Any) -> WahaChannel:
    settings = kw.pop("settings", None) or _settings()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    return WahaChannel(settings, client=client, **kw)


# ---------------------------------------------------------------- funcoes puras


def test_chat_id_individual() -> None:
    assert chat_id("+5500000000002") == CHAT
    assert chat_id("5500000000002") == CHAT
    with pytest.raises(ValueError):
        chat_id("+55 85 9999")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"id": {"_serialized": "true_x_1"}}, "true_x_1"),
        ({"id": "true_x_2"}, "true_x_2"),
        ({"key": {"id": "ABC"}}, "ABC"),
        ({"messageId": "M1"}, "M1"),
        ({"id": ""}, None),
        ({}, None),
        ([], None),
    ],
)
def test_extrator_de_id(data: Any, expected: str | None) -> None:
    assert extract_message_id(data) == expected


@pytest.mark.parametrize(
    ("response", "ok", "transient", "ambiguous", "code"),
    [
        (httpx.Response(201, json={"id": "true_x"}), True, False, False, None),
        (httpx.Response(200, json={}), False, False, True, "no_message_id"),
        (httpx.Response(200, content=b"nao json"), False, False, True, "no_message_id"),
        (httpx.Response(429), False, True, False, "http_429"),
        (httpx.Response(503), False, True, False, "http_503"),
        (httpx.Response(500, json={"message": "erro interno"}), False, False, True, "http_500"),
        (httpx.Response(502), False, False, True, "http_502"),
        (httpx.Response(504), False, False, True, "http_504"),
        (httpx.Response(400, json={"message": "bad"}), False, False, False, "http_400"),
        (httpx.Response(404), False, False, False, "http_404"),
        (httpx.Response(500, json={"message": "server returned error 463"}), False, False,
         False, "waha_463"),
        (httpx.Response(500, json={"exception": {"message": "error 475 capped"}}), False,
         False, False, "waha_475"),
    ],
)  # fmt: skip
def test_classificacao_dos_erros(
    response: httpx.Response, ok: bool, transient: bool, ambiguous: bool, code: str | None
) -> None:
    result = classify_response(response)
    assert (result.ok, result.transient, result.ambiguous, result.error_code) == (
        ok, transient, ambiguous, code,
    )  # fmt: skip


def test_restricao_e_reconhecida() -> None:
    assert is_restriction(classify_response(httpx.Response(500, json={"message": "463"})))
    assert not is_restriction(classify_response(httpx.Response(500, json={"message": "4630"})))


# ---------------------------------------------------------------- envio


async def test_capacidades_sem_botoes_nem_modelos() -> None:
    assert _channel(Recorder()).capabilities == WAHA_CAPABILITIES


async def test_texto_com_visto_e_sem_digitando() -> None:
    """Sem "digitando" nem espera (decisao do Caio em 2026-11-16): visto e envio direto."""
    rec = Recorder()
    result = await _channel(rec).send_text(TO, "x" * 120)
    assert result.ok and result.wa_message_id == f"true_{CHAT}_ID2"
    assert rec.endpoints == ["sendSeen", "sendText"]
    endpoint, payload, key = rec.calls[-1]
    assert payload == {
        "session": "jarvis", "chatId": CHAT, "text": "x" * 120, "linkPreview": False,
    }  # fmt: skip
    assert key == API_KEY
    assert all(c[1]["chatId"] == CHAT and c[1]["session"] == "jarvis" for c in rec.calls)


async def test_falha_no_visto_nao_impede_envio() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusada", request=request)

    for reply in (httpx.Response(500), boom):
        rec = Recorder({"sendSeen": reply})
        result = await _channel(rec).send_text(TO, "oi")
        assert result.ok and rec.endpoints == ["sendSeen", "sendText"]


async def test_enquete_escolha_unica() -> None:
    rec = Recorder()
    options = [PollOption("BRAND:jungle", "Jungle"), PollOption("BRAND:possible", "Possible")]
    result = await _channel(rec).send_poll(TO, "Sobre qual marca?", options)
    assert result.ok and rec.endpoints[-1] == "sendPoll"
    assert rec.calls[-1][1] == {
        "session": "jarvis",
        "chatId": CHAT,
        "poll": {"name": "Sobre qual marca?", "options": ["Jungle", "Possible"],
                 "multipleAnswers": False},
    }  # fmt: skip


async def test_enquete_sem_opcoes_e_erro_de_programacao() -> None:
    rec = Recorder()
    with pytest.raises(ValueError):
        await _channel(rec).send_poll(TO, "Sobre qual marca?", [])
    assert rec.calls == []


async def test_modelo_nao_suportado_sem_chamar_o_waha() -> None:
    rec = Recorder()
    result = await _channel(rec).send_template(TO, TemplateMessage("boas_vindas", "pt_BR"))
    assert not result.ok and result.error_code == "unsupported" and rec.calls == []


async def test_botoes_viram_so_o_corpo_em_texto() -> None:
    rec = Recorder()
    buttons = [ReplyButton("OPTIN_ACCEPT:1", "Aceito"), ReplyButton("OPTIN_DECLINE:1", "Não")]
    result = await _channel(rec).send_reply_buttons(TO, "Responda ACEITO ou NÃO", buttons)
    assert result.ok and rec.calls[-1][0] == "sendText"
    assert rec.calls[-1][1]["text"] == "Responda ACEITO ou NÃO"


async def test_sessao_fora_do_ar_e_transitorio_sem_chamar() -> None:
    rec = Recorder()
    result = await _channel(rec, session_working=lambda: False).send_text(TO, "oi")
    assert result.transient and result.error_code == "session_not_working" and rec.calls == []


@pytest.mark.parametrize(
    ("exc", "transient", "ambiguous"),
    [(httpx.ConnectError, True, False), (httpx.ReadTimeout, False, True)],
)
async def test_erro_de_rede_no_envio(
    exc: type[Exception], transient: bool, ambiguous: bool
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise exc("falhou", request=request)  # type: ignore[call-arg]

    rec = Recorder({"sendText": boom})
    result = await _channel(rec).send_text(TO, "oi")
    assert not result.ok and (result.transient, result.ambiguous) == (transient, ambiguous)


async def test_texto_longo_e_truncado_no_limite() -> None:
    rec = Recorder()
    await _channel(rec).send_text(TO, "a" * 5000)
    assert len(rec.calls[-1][1]["text"]) == 4096


def test_assinatura_do_webhook() -> None:
    channel = _channel(Recorder())
    raw = b'{"event":"message"}'
    assert channel.verify_signature(raw, compute_waha_hmac(raw, HMAC_KEY)) is True
    assert channel.verify_signature(raw, compute_waha_hmac(raw, "outra")) is False


async def test_log_sem_numero_texto_nem_chave(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rec = Recorder({"sendText": httpx.Response(400, json={"message": f"bad {CHAT}"})})
    await _channel(rec).send_text(TO, "segredo comercial")
    records = [r for r in caplog.records if r.name == "jarvis.waha"]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    assert "waha_send_failed" in text
    assert "5500000000002" not in text and "segredo comercial" not in text
    assert API_KEY not in text
