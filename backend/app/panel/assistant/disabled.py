"""Provedor desligado: GEMINI_API_KEY vazio (PN-50). Nunca chama rede."""
from __future__ import annotations

from collections.abc import Sequence

from app.panel.assistant.provider import (
    DEFAULT_MAX_TOOL_ROUNDS,
    ChatTurn,
    ProviderDisabledError,
    ProviderReply,
    ToolCaller,
    ToolSpec,
)

DISABLED_REASON = "O assistente será ativado quando a chave do Gemini for configurada"


class DisabledProvider:
    enabled = False
    reason = DISABLED_REASON

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ) -> ProviderReply:
        raise ProviderDisabledError(DISABLED_REASON)
