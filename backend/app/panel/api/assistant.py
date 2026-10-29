"""API do assistente (spec painel.1 4.5 e 4.10; task PN-T56; PN-50, PN-53, PN-4).

- ``GET  /api/assistant/status``  ``{"enabled", "reason", "agents", "max_chars"}``; desligado
                                  (sem chave do Gemini ou sem banco de leitura) = ``enabled: false``
- ``POST /api/assistant/chat``    JSON ``{"message", "history": [{"role", "text"}]}``;
                                  devolve ``{"text", "agent", "tools_used"}``

Ordem das recusas no chat: sessao/CSRF (401/403) -> desligado (503, sem registrar nada) ->
texto vazio (422) ou acima de 4.000 caracteres (413) -> 12 perguntas por minuto por admin (429
com ``Retry-After``) -> limite diario (429) -> falha do modelo (502, ja registrada em Perguntas).
Pergunta, resposta e historico nunca vao para o log. O servico (``app.state.assistant_service``)
e montado na primeira chamada a partir da configuracao; testes injetam o proprio.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.panel.assistant.agents import AGENTS, COORDINATOR, Agent
from app.panel.assistant.disabled import DISABLED_REASON
from app.panel.assistant.provider import ChatTurn
from app.panel.assistant.service import (
    MAX_HISTORY_TURNS,
    AssistantDisabledError,
    AssistantFailedError,
    AssistantService,
    DailyLimitExceededError,
    build_service,
)
from app.panel.auth.sessions import PanelSession
from app.panel.deps import PanelRoute, current_admin, get_clock, get_settings, require_csrf
from app.services.rate_limit import SlidingWindowLimiter

__all__ = ["MAX_MESSAGE_CHARS", "RATE_LIMIT_PER_MINUTE", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

MAX_MESSAGE_CHARS = 4000
RATE_LIMIT_PER_MINUTE = 12
RATE_WINDOW_SECONDS = 60.0
NO_DB_REASON = "O assistente será ativado quando o banco de leitura for configurado"
FAILED_TEXT = "Não consegui responder agora. Tente de novo em instantes."
DAILY_TEXT = "O limite diário de perguntas ao assistente foi atingido."
RATE_TEXT = "Muitas perguntas seguidas. Aguarde um instante."


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatBody(BaseModel):
    message: str
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS * 5)


def _agent_json(agent: Agent) -> dict[str, Any]:
    return asdict(agent)


async def _service(request: Request) -> AssistantService:
    state = request.app.state
    found: AssistantService | None = getattr(state, "assistant_service", None)
    if found is not None:
        return found
    lock: asyncio.Lock | None = getattr(state, "assistant_lock", None)
    if lock is None:
        lock = state.assistant_lock = asyncio.Lock()
    async with lock:
        found = getattr(state, "assistant_service", None)
        if found is None:
            found = await build_service(get_settings(request), state.pool, clock=get_clock(request))
            state.assistant_service = found
    return found


def _limiter(request: Request) -> SlidingWindowLimiter:
    state = request.app.state
    limiter: SlidingWindowLimiter | None = getattr(state, "assistant_limiter", None)
    if limiter is None:
        limiter = state.assistant_limiter = SlidingWindowLimiter(get_clock(request))
    return limiter


def _disabled_reason(request: Request) -> str:
    key = get_settings(request).gemini_api_key.get_secret_value().strip()
    return NO_DB_REASON if key else DISABLED_REASON


def _error(
    status: int, code: str, text: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": code, "text": text}, status_code=status, headers=headers)


@router.get("/api/assistant/status")
async def status(
    request: Request, _: Annotated[PanelSession, Depends(current_admin)]
) -> JSONResponse:
    service = await _service(request)
    body: dict[str, Any] = {
        "enabled": service.enabled,
        "agents": [_agent_json(a) for a in (COORDINATOR, *AGENTS.values())],
        "max_chars": MAX_MESSAGE_CHARS,
    }
    if not service.enabled:
        body["reason"] = _disabled_reason(request)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/api/assistant/chat", dependencies=[Depends(require_csrf)])
async def chat(
    request: Request, body: ChatBody, admin: Annotated[PanelSession, Depends(current_admin)]
) -> JSONResponse:
    service = await _service(request)
    if not service.enabled:
        return _error(503, "assistant_disabled", _disabled_reason(request))
    question = body.message.strip()
    if not question:
        return _error(422, "empty_message", "Escreva uma pergunta.")
    if len(question) > MAX_MESSAGE_CHARS:
        return _error(413, "message_too_long", f"Máximo de {MAX_MESSAGE_CHARS} caracteres.")
    key = f"assistant:{admin.email}"
    wait = _limiter(request).check(key, RATE_LIMIT_PER_MINUTE, RATE_WINDOW_SECONDS)
    if wait:
        return _error(429, "rate_limited", RATE_TEXT, {"Retry-After": str(wait)})
    history = [ChatTurn(t.role, t.text) for t in body.history]
    try:
        answer = await service.ask(asked_by=admin.email, question=question, history=history)
    except AssistantDisabledError:
        return _error(503, "assistant_disabled", _disabled_reason(request))
    except DailyLimitExceededError:
        return _error(429, "daily_limit", DAILY_TEXT)
    except AssistantFailedError:
        return _error(502, "assistant_failed", FAILED_TEXT)
    logger.info("assistant_call", extra={"agent": answer.agent.name})
    return JSONResponse(
        {
            "text": answer.text,
            "agent": _agent_json(answer.agent),
            "tools_used": list(answer.tools_used),
        },
        headers={"Cache-Control": "no-store"},
    )

