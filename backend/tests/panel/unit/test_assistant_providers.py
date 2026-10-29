"""Provedores do assistente (PN-T52; PN-50, PN-52, 4.10). Sem rede: MockTransport."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from app.panel.assistant import build_provider
from app.panel.assistant.agents import choose_agent
from app.panel.assistant.disabled import DisabledProvider
from app.panel.assistant.gemini import GeminiProvider
from app.panel.assistant.provider import (
    ChatTurn,
    ProviderDisabledError,
    ProviderError,
    ToolSpec,
)

KEY = "fake-key-123456"
TOOLS = [ToolSpec("metricas", "Metricas agregadas", {"type": "object", "properties": {}})]
SECRET_PROMPT = "pergunta com Maria Silva 11999990000"


def text_reply(text: str, tin: int = 10, tout: int = 5) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": tin, "candidatesTokenCount": tout},
    }


def call_reply(name: str = "metricas", args: dict[str, Any] | None = None) -> dict[str, Any]:
    part = {"functionCall": {"name": name, "args": args or {}}}
    return {
        "candidates": [{"content": {"role": "model", "parts": [part]}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
    }


class Server:
    def __init__(self, replies: list[httpx.Response | Exception]) -> None:
        self.replies = replies
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)  # type: ignore[no-any-return]


def ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


def provider(server: Server) -> GeminiProvider:
    return GeminiProvider(api_key=KEY, model="gemini-test", transport=httpx.MockTransport(server))


async def no_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("ferramenta nao deveria ser chamada")


USER = [ChatTurn("user", SECRET_PROMPT)]


async def test_desligado_sem_chave_e_nunca_chama_rede() -> None:
    p = build_provider("   ", "m")
    assert isinstance(p, DisabledProvider)
    assert p.enabled is False
    assert "chave do Gemini" in p.reason
    with pytest.raises(ProviderDisabledError):
        await p.chat(system="s", messages=USER, tools=TOOLS, call_tool=no_tool)


def test_chave_presente_gera_gemini() -> None:
    p = build_provider(KEY, "gemini-test")
    assert isinstance(p, GeminiProvider)
    assert p.enabled is True
    assert KEY not in repr(p)


async def test_texto_simples_envia_requisicao_correta() -> None:
    server = Server([ok(text_reply(" Ola ", 11, 4))])
    reply = await provider(server).chat(
        system="SISTEMA",
        messages=[ChatTurn("user", "oi"), ChatTurn("assistant", "ola"), ChatTurn("user", "e ai")],
        tools=TOOLS,
        call_tool=no_tool,
    )
    assert (reply.text, reply.tokens_in, reply.tokens_out, reply.tools_used) == ("Ola", 11, 4, ())
    req = server.requests[0]
    assert req.url.host == "generativelanguage.googleapis.com"
    assert req.url.path == "/v1beta/models/gemini-test:generateContent"
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url)
    body = server.body(0)
    assert body["systemInstruction"]["parts"][0]["text"] == "SISTEMA"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "metricas"


async def test_rodada_de_ferramenta_executa_e_devolve_resultado() -> None:
    server = Server([ok(call_reply(args={"dias": 7})), ok(text_reply("Total 3", 20, 6))])
    seen: list[tuple[str, dict[str, Any]]] = []

    async def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen.append((name, args))
        return {"total": 3}

    reply = await provider(server).chat(system="s", messages=USER, tools=TOOLS, call_tool=tool)
    assert seen == [("metricas", {"dias": 7})]
    assert reply.text == "Total 3"
    assert reply.tools_used == ("metricas",)
    assert (reply.tokens_in, reply.tokens_out) == (27, 8)
    second = server.body(1)["contents"]
    assert second[-2]["role"] == "model" and "functionCall" in second[-2]["parts"][0]
    resp = second[-1]["parts"][0]["functionResponse"]
    assert resp == {"name": "metricas", "response": {"result": {"total": 3}}}


async def test_limite_de_rodadas_forca_resposta_sem_ferramentas() -> None:
    server = Server([ok(call_reply()), ok(call_reply()), ok(text_reply("fim"))])
    calls = 0

    async def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {}

    reply = await provider(server).chat(
        system="s", messages=USER, tools=TOOLS, call_tool=tool, max_tool_rounds=2
    )
    assert reply.text == "fim" and calls == 2 and len(server.requests) == 3
    assert "tools" in server.body(0) and "tools" in server.body(1)
    assert "tools" not in server.body(2)


async def test_modelo_insiste_em_ferramenta_apos_limite_sem_texto_e_erro() -> None:
    server = Server([ok(call_reply())])

    async def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    with pytest.raises(ProviderError):
        await provider(server).chat(
            system="s", messages=USER, tools=TOOLS, call_tool=tool, max_tool_rounds=1
        )
    assert len(server.requests) == 2


async def test_falha_da_ferramenta_vira_dado_para_o_modelo() -> None:
    server = Server([ok(call_reply()), ok(text_reply("nao consegui"))])

    async def tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("detalhe interno com PII")

    reply = await provider(server).chat(system="s", messages=USER, tools=TOOLS, call_tool=tool)
    assert reply.text == "nao consegui"
    result = server.body(1)["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert result == {"result": {"error": "tool_failed"}}
    assert "PII" not in json.dumps(server.body(1))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(200, text="nao e json"),
        httpx.Response(200, json=[1]),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json=text_reply("   ")),
    ],
)
async def test_respostas_ruins_viram_provider_error(response: httpx.Response) -> None:
    with pytest.raises(ProviderError):
        await provider(Server([response])).chat(
            system="s", messages=USER, tools=[], call_tool=no_tool
        )


async def test_timeout_e_erro_de_rede_viram_provider_error() -> None:
    for exc in (httpx.ReadTimeout("t"), httpx.ConnectError("c")):
        with pytest.raises(ProviderError):
            await provider(Server([exc])).chat(
                system="s", messages=USER, tools=[], call_tool=no_tool
            )


def test_timeout_padrao_60s() -> None:
    p = GeminiProvider(api_key=KEY)
    assert p._timeout == 60.0


@pytest.mark.parametrize(
    "base",
    [
        "http://generativelanguage.googleapis.com",
        "https://evil.example.com",
        "https://generativelanguage.googleapis.com.evil.com",
        "https://user@evil.com",
        "file:///etc/passwd",
    ],
)
def test_ssrf_base_fora_da_allow_list_e_recusada(base: str) -> None:
    with pytest.raises(ValueError):
        GeminiProvider(api_key=KEY, base_url=base)


def test_modelo_invalido_e_chave_vazia_recusados() -> None:
    with pytest.raises(ValueError):
        GeminiProvider(api_key=KEY, model="a/../b")
    with pytest.raises(ValueError):
        GeminiProvider(api_key="")


async def test_redirecionamento_nao_e_seguido() -> None:
    server = Server([httpx.Response(302, headers={"location": "https://evil.example.com/"})])
    with pytest.raises(ProviderError):
        await provider(server).chat(system="s", messages=USER, tools=[], call_tool=no_tool)
    assert len(server.requests) == 1


async def test_logs_nao_vazam_chave_nem_prompt(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    server = Server([httpx.Response(500, text=f"eco {SECRET_PROMPT} {KEY}")])
    with pytest.raises(ProviderError) as info:
        await provider(server).chat(system="s", messages=USER, tools=[], call_tool=no_tool)
    blob = caplog.text + str(info.value)
    assert KEY not in blob and "Maria" not in blob and "11999990000" not in blob


def test_agents_continua_importavel() -> None:
    assert choose_agent("como esta a zaya?").brand == "zaya"
