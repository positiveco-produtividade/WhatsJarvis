"""Cliente Gemini do cerebro (REST por httpx, sem SDK) - CB-2, CB-13, CB-14.

Unico modulo do cerebro que fala com o Gemini. Premissas verificadas na T01:

- endpoint ``v1beta/models/{model}:generateContent`` com cabecalho ``x-goog-api-key``;
- NAO envia ``temperature``/``topP``/``topK``/``candidateCount`` (obsoletos no Gemini 3+);
- ``thinkingConfig.thinkingLevel = "low"`` e ``responseMimeType = application/json``;
  o esquema nao e delegado ao servidor: a saida e validada no codigo (fidelity.py);
- tokens_in = promptTokenCount; tokens_out = candidatesTokenCount + thoughtsTokenCount;
- decide so pelo status HTTP e pela presenca de texto; nunca le nem loga corpo de erro.

Privacidade: ``LlmRequest`` nao tem campo de usuario (nada de id, telefone, nome).
Nunca vai para o log: prompt, resposta, corpo de erro, chave (so status e tipo da excecao).
Uma chamada, sem retentativa: qualquer falha vira ``LlmError`` e o chamador usa a reserva.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

GEMINI_ALLOWED_HOSTS: frozenset[str] = frozenset({"generativelanguage.googleapis.com"})
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
THINKING_LEVEL = "low"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FENCE = re.compile(r"^```[A-Za-z]*\s*(.*?)\s*```$", re.DOTALL)

ERR_TIMEOUT = "timeout"
ERR_UNAVAILABLE = "unavailable"
ERR_HTTP = "http_status"
ERR_BAD_BODY = "bad_body"
ERR_EMPTY = "empty"
ERR_INVALID_JSON = "invalid_json"


class LlmError(Exception):
    """Falha da IA; ``kind`` e ``status`` sao seguros para log (sem conteudo)."""

    def __init__(self, kind: str, status: int | None = None) -> None:
        super().__init__(kind if status is None else f"{kind}:{status}")
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class LlmCandidate:
    ref: str
    title: str
    text: str


@dataclass(frozen=True)
class LlmTurn:
    role: str  # "user" | "assistant"
    text: str


@dataclass(frozen=True)
class LlmRequest:
    """Pedido sem identificacao de pessoa: so instrucao, candidatos, contexto e pergunta."""

    system: str
    candidates: tuple[LlmCandidate, ...]
    context: tuple[LlmTurn, ...]
    question: str


@dataclass(frozen=True)
class LlmResult:
    """``data`` e o JSON da saida (nao validado); ``usage_present`` False = sem usageMetadata."""

    data: dict[str, Any]
    tokens_in: int
    tokens_out: int
    usage_present: bool


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _check_base_url(base_url: str, allowed_hosts: Collection[str]) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("Gemini base_url: use https e um host da allow-list")
    return base_url.rstrip("/")


def render_user_text(request: LlmRequest) -> str:
    """Texto do usuario: tudo delimitado e marcado como dado, nao instrucao."""
    lines = ["<candidatos>"]
    for c in request.candidates:
        lines.append(f'<item ref="{c.ref}" titulo="{c.title}">\n{c.text}\n</item>')
    lines.append("</candidatos>\n<contexto>")
    for t in request.context:
        lines.append(f"<fala papel=\"{t.role}\">{t.text}</fala>")
    lines.append(f"</contexto>\n<pergunta>\n{request.question}\n</pergunta>")
    return "\n".join(lines)


def build_body(request: LlmRequest) -> dict[str, Any]:
    return {
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": [{"role": "user", "parts": [{"text": render_user_text(request)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": THINKING_LEVEL},
        },
    }


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse tolerante: aceita cerca de markdown e lixo ao redor do objeto."""
    raw = text.strip()
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1)
    for candidate in (raw, raw[raw.find("{") : raw.rfind("}") + 1]):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise LlmError(ERR_INVALID_JSON)


def _answer_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LlmError(ERR_EMPTY)
    parts = _as_dict(_as_dict(candidates[0]).get("content")).get("parts")
    items = parts if isinstance(parts, list) else []
    texts = [
        p["text"]
        for p in items
        if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")
    ]
    text = "".join(texts).strip()
    if not text:
        raise LlmError(ERR_EMPTY)
    return text


def _usage(data: dict[str, Any]) -> tuple[int, int, bool]:
    raw = data.get("usageMetadata")
    if not isinstance(raw, dict):
        return 0, 0, False
    tokens_in = _as_int(raw.get("promptTokenCount"))
    tokens_out = _as_int(raw.get("candidatesTokenCount")) + _as_int(raw.get("thoughtsTokenCount"))
    return tokens_in, tokens_out, True


class LlmClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = 5.0,
        base_url: str = DEFAULT_BASE_URL,
        allowed_hosts: Collection[str] = GEMINI_ALLOWED_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("LlmClient exige api_key")
        if not _MODEL_RE.match(model):
            raise ValueError("Gemini model invalido")
        self._api_key = api_key
        self._model = model
        self._base = _check_base_url(base_url, allowed_hosts)
        self._timeout = timeout
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> LlmClient:
        return cls(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout=settings.brain_gemini_timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"LlmClient(model={self._model!r})"

    async def complete(self, request: LlmRequest) -> LlmResult:
        response = await self._post(build_body(request))
        if response.status_code != 200:
            logger.warning("brain llm http status %s", response.status_code)
            raise LlmError(ERR_HTTP, response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlmError(ERR_BAD_BODY) from exc
        if not isinstance(payload, dict):
            raise LlmError(ERR_BAD_BODY)
        data = parse_json_object(_answer_text(payload))
        tokens_in, tokens_out, present = _usage(payload)
        return LlmResult(data, tokens_in, tokens_out, present)

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        url = f"{self._base}/v1beta/models/{self._model}:generateContent"
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
            ) as client:
                return await client.post(url, json=body, headers={"x-goog-api-key": self._api_key})
        except httpx.TimeoutException as exc:
            logger.warning("brain llm timeout")
            raise LlmError(ERR_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            logger.warning("brain llm request failed: %s", type(exc).__name__)
            raise LlmError(ERR_UNAVAILABLE) from exc
