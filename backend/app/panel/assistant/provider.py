"""Interface de provedor do assistente (spec painel.1, 4.10).

O provedor recebe o contexto da conversa e as ferramentas, executa as rodadas de
ferramenta chamando ``call_tool`` e devolve o texto final. Nada aqui conhece o banco.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

DEFAULT_MAX_TOOL_ROUNDS = 5
PROVIDER_TIMEOUT_SECONDS = 60.0

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class ProviderError(Exception):
    """Falha do provedor (rede, timeout, resposta invalida). A mensagem nunca tem PII."""


class ProviderDisabledError(ProviderError):
    """Assistente desligado (sem chave)."""


@dataclass(frozen=True)
class ChatTurn:
    """Um turno da conversa. ``role`` e ``user`` ou ``assistant``."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ToolSpec:
    """Declaracao de ferramenta (``parameters`` e um JSON Schema de objeto)."""

    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tokens_in: int
    tokens_out: int
    tools_used: tuple[str, ...] = ()


class AssistantProvider(Protocol):
    enabled: bool

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> ProviderReply: ...
