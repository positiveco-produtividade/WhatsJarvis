"""Cliente Gemini do cerebro (CB-T13): MockTransport, sem rede real."""
from __future__ import annotations

import dataclasses
import json
import logging

import httpx
import pytest

from app.services.brain.llm import (
    ERR_BAD_BODY,
    ERR_EMPTY,
    ERR_HTTP,
    ERR_INVALID_JSON,
    ERR_TIMEOUT,
    ERR_UNAVAILABLE,
    LlmCandidate,
    LlmClient,
    LlmError,
    LlmRequest,
    LlmTurn,
    parse_json_object,
)
from tests.brain.factories import FakeGemini, gemini_text_response

KEY = "chave-secreta-xyz"
ANSWER = '{"intent": "answer", "item_refs": ["a1"], "answer_text": "Frete gratis acima de R$ 50"}'


def _request() -> LlmRequest:
    return LlmRequest(
        system="Responda em JSON.",
        candidates=(LlmCandidate("a1", "Frete", "Frete gratis acima de R$ 50"),),
        context=(LlmTurn("user", "oi"), LlmTurn("assistant", "ola")),
        question="qual o frete?",
    )


def _client(fake: FakeGemini, **kw: object) -> LlmClient:
    return LlmClient(api_key=KEY, model="gemini-3.8-flash", transport=fake.transport, **kw)  # type: ignore[arg-type]


async def test_resposta_valida_e_uso_de_tokens() -> None:
    fake = FakeGemini()
    body = gemini_text_response(ANSWER, prompt_tokens=300, output_tokens=40)
    body["usageMetadata"]["thoughtsTokenCount"] = 25
    fake.responses.append(httpx.Response(200, json=body))
    result = await _client(fake).complete(_request())
    assert result.data["intent"] == "answer"
    assert result.data["item_refs"] == ["a1"]
    assert (result.tokens_in, result.tokens_out, result.usage_present) == (300, 65, True)


async def test_sem_usage_metadata_grava_zero() -> None:
    fake = FakeGemini()
    body = gemini_text_response(ANSWER)
    del body["usageMetadata"]
    fake.responses.append(httpx.Response(200, json=body))
    result = await _client(fake).complete(_request())
    assert (result.tokens_in, result.tokens_out, result.usage_present) == (0, 0, False)


async def test_corpo_enviado_sem_parametros_obsoletos_e_com_pensamento_baixo() -> None:
    fake = FakeGemini()
    fake.queue_text(ANSWER)
    await _client(fake).complete(_request())
    req = fake.requests[-1]
    assert req.method == "POST"
    assert req.url.host == "generativelanguage.googleapis.com"
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    body = fake.last_json()
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    for banned in ("temperature", "topP", "topK", "candidateCount", "responseSchema"):
        assert banned not in config
    assert "qual o frete?" in body["contents"][0]["parts"][0]["text"]


async def test_chave_so_no_cabecalho() -> None:
    fake = FakeGemini()
    fake.queue_text(ANSWER)
    await _client(fake).complete(_request())
    req = fake.requests[-1]
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url)
    assert KEY not in req.content.decode()
    assert KEY not in repr(_client(fake))


async def test_http_500_vira_erro_sem_ler_corpo(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeGemini()
    fake.queue_status(500, {"error": {"message": "SEGREDO-DO-ERRO"}})
    with caplog.at_level(logging.DEBUG), pytest.raises(LlmError) as info:
        await _client(fake).complete(_request())
    assert (info.value.kind, info.value.status) == (ERR_HTTP, 500)
    assert "SEGREDO-DO-ERRO" not in caplog.text


async def test_timeout_de_5_segundos() -> None:
    seen: list[dict[str, float | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        raise httpx.ReadTimeout("lento", request=request)

    client = LlmClient(
        api_key=KEY, model="gemini-3.8-flash", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LlmError) as info:
        await client.complete(_request())
    assert info.value.kind == ERR_TIMEOUT
    assert seen[0]["read"] == 5.0


async def test_falha_de_conexao_e_indisponivel() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    client = LlmClient(
        api_key=KEY, model="gemini-3.8-flash", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LlmError) as info:
        await client.complete(_request())
    assert info.value.kind == ERR_UNAVAILABLE


async def test_json_invalido_na_saida() -> None:
    fake = FakeGemini()
    fake.queue_text("isto nao e json")
    with pytest.raises(LlmError) as info:
        await _client(fake).complete(_request())
    assert info.value.kind == ERR_INVALID_JSON


async def test_corpo_http_nao_json() -> None:
    fake = FakeGemini()
    fake.responses.append(httpx.Response(200, content=b"<html>"))
    with pytest.raises(LlmError) as info:
        await _client(fake).complete(_request())
    assert info.value.kind == ERR_BAD_BODY


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"candidates": []},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": [{"text": "   "}]}}]},
        {"candidates": [{"content": {"parts": [{"text": ANSWER, "thought": True}]}}]},
    ],
)
async def test_corpo_vazio_ou_sem_texto(body: dict[str, object]) -> None:
    fake = FakeGemini()
    fake.responses.append(httpx.Response(200, json=body))
    with pytest.raises(LlmError) as info:
        await _client(fake).complete(_request())
    assert info.value.kind == ERR_EMPTY


async def test_sem_redirecionamento() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    client = LlmClient(
        api_key=KEY, model="gemini-3.8-flash", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LlmError) as info:
        await client.complete(_request())
    assert (info.value.kind, info.value.status) == (ERR_HTTP, 302)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "base",
    ["http://generativelanguage.googleapis.com", "https://evil.example", "https://127.0.0.1"],
)
def test_allow_list_de_host(base: str) -> None:
    with pytest.raises(ValueError):
        LlmClient(api_key=KEY, model="gemini-3.8-flash", base_url=base)


def test_modelo_e_chave_validados() -> None:
    with pytest.raises(ValueError):
        LlmClient(api_key="", model="gemini-3.8-flash")
    with pytest.raises(ValueError):
        LlmClient(api_key=KEY, model="../etc/passwd")


async def test_nunca_loga_prompt_resposta_nem_chave(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeGemini()
    fake.queue_text("lixo SEGREDO-RESPOSTA")
    fake.queue_status(503, {"error": "SEGREDO-ERRO"})
    with caplog.at_level(logging.DEBUG):
        for _ in range(2):
            with pytest.raises(LlmError):
                await _client(fake).complete(_request())
    for forbidden in ("qual o frete?", "SEGREDO-RESPOSTA", "SEGREDO-ERRO", KEY, "Responda em JSON"):
        assert forbidden not in caplog.text


def test_pedido_nao_tem_campo_de_usuario() -> None:
    fields = {f.name for f in dataclasses.fields(LlmRequest)}
    assert fields == {"system", "candidates", "context", "question"}
    sent = json.dumps(
        {"fields": sorted(fields)}
    )
    for banned in ("user", "phone", "bsuid", "name", "company", "manager"):
        assert banned not in sent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Segue: {"a": 1} fim', {"a": 1}),
    ],
)
def test_parse_tolerante(text: str, expected: dict[str, int]) -> None:
    assert parse_json_object(text) == expected


def test_parse_rejeita_nao_objeto() -> None:
    with pytest.raises(LlmError):
        parse_json_object("[1, 2]")
