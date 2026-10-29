"""Provedor Gemini por REST (httpx), sem SDK (spec painel.1, 4.10).

- base na allow-list SSRF (https + host permitido), sem seguir redirecionamentos;
- timeout de 60 s por chamada;
- rodadas de ferramenta: ate ``max_tool_rounds``; esgotadas, uma ultima chamada sem
  ferramentas forca a resposta em texto;
- a chave vai no cabecalho ``x-goog-api-key`` e nunca e logada; prompts, respostas e
  corpos de erro tambem nunca vao para o log (so status e tipo da excecao).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Collection, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from app.panel.assistant.provider import (
    DEFAULT_MAX_TOOL_ROUNDS,
    PROVIDER_TIMEOUT_SECONDS,
    ChatTurn,
    ProviderError,
    ProviderReply,
    ToolCaller,
    ToolSpec,
)

logger = logging.getLogger(__name__)

GEMINI_ALLOWED_HOSTS: frozenset[str] = frozenset({"generativelanguage.googleapis.com"})
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-2.5-flash"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _check_base_url(base_url: str, allowed_hosts: Collection[str]) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("Gemini base_url: use https e um host da allow-list")
    return base_url.rstrip("/")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class GeminiProvider:
    enabled = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        allowed_hosts: Collection[str] = GEMINI_ALLOWED_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = PROVIDER_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiProvider exige api_key")
        model = model or DEFAULT_MODEL
        if not _MODEL_RE.match(model):
            raise ValueError("Gemini model invalido")
        self._api_key = api_key
        self._model = model
        self._base = _check_base_url(base_url, allowed_hosts)
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self._model!r})"

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> ProviderReply:
        contents: list[dict[str, Any]] = [self._turn(t) for t in messages]
        used: list[str] = []
        tokens_in = tokens_out = 0
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=False,
        ) as client:
            for round_no in range(max(max_tool_rounds, 0) + 1):
                offer_tools = bool(tools) and round_no < max_tool_rounds
                data = await self._generate(client, system, contents, tools if offer_tools else ())
                usage = _as_dict(data.get("usageMetadata"))
                tokens_in += _as_int(usage.get("promptTokenCount"))
                tokens_out += _as_int(usage.get("candidatesTokenCount"))
                content = self._first_content(data)
                raw_parts = content.get("parts")
                items = raw_parts if isinstance(raw_parts, list) else []
                parts = [p for p in items if isinstance(p, dict)]
                calls = [_as_dict(p["functionCall"]) for p in parts if "functionCall" in p]
                if not calls or not offer_tools:
                    text = "".join(p["text"] for p in parts if isinstance(p.get("text"), str))
                    if not text.strip():
                        raise ProviderError("Gemini devolveu resposta vazia")
                    return ProviderReply(text.strip(), tokens_in, tokens_out, tuple(used))
                contents.append({"role": "model", "parts": parts})
                responses = [await self._run_tool(call, call_tool, used) for call in calls]
                contents.append({"role": "user", "parts": responses})
        raise ProviderError("Gemini sem resposta final")  # pragma: no cover

    @staticmethod
    def _turn(turn: ChatTurn) -> dict[str, Any]:
        role = "model" if turn.role == "assistant" else "user"
        return {"role": role, "parts": [{"text": turn.text}]}

    @staticmethod
    def _first_content(data: dict[str, Any]) -> dict[str, Any]:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderError("Gemini sem candidatos na resposta")
        return _as_dict(_as_dict(candidates[0]).get("content"))

    async def _run_tool(
        self, call: dict[str, Any], call_tool: ToolCaller, used: list[str]
    ) -> dict[str, Any]:
        name = str(call.get("name", ""))
        args = _as_dict(call.get("args"))
        used.append(name)
        try:
            result = await call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 - falha de ferramenta vira dado para o modelo
            logger.warning("assistant tool failed: %s (%s)", name, type(exc).__name__)
            result = {"error": "tool_failed"}
        return {"functionResponse": {"name": name, "response": {"result": result}}}

    async def _generate(
        self,
        client: httpx.AsyncClient,
        system: str,
        contents: list[dict[str, Any]],
        tools: Sequence[ToolSpec],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [self._declaration(t) for t in tools]}]
        url = f"{self._base}/v1beta/models/{self._model}:generateContent"
        try:
            response = await client.post(url, json=body, headers={"x-goog-api-key": self._api_key})
        except httpx.TimeoutException as exc:
            raise ProviderError("Gemini excedeu o tempo limite") from exc
        except httpx.HTTPError as exc:
            logger.warning("gemini request failed: %s", type(exc).__name__)
            raise ProviderError("Gemini indisponivel") from exc
        if response.status_code != 200:
            logger.warning("gemini http status %s", response.status_code)
            raise ProviderError(f"Gemini respondeu HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Gemini devolveu corpo invalido") from exc
        if not isinstance(data, dict):
            raise ProviderError("Gemini devolveu corpo invalido")
        return data

    @staticmethod
    def _declaration(tool: ToolSpec) -> dict[str, Any]:
        decl: dict[str, Any] = {"name": tool.name, "description": tool.description}
        if tool.parameters:
            decl["parameters"] = dict(tool.parameters)
        return decl
