"""Assistente do painel (spec painel.1, 4.10)."""
from __future__ import annotations

from app.panel.assistant.disabled import DisabledProvider
from app.panel.assistant.gemini import GeminiProvider
from app.panel.assistant.provider import AssistantProvider


def build_provider(api_key: str, model: str) -> AssistantProvider:
    """Chave vazia (ou so espacos) = provedor desligado; senao Gemini."""
    key = api_key.strip()
    if not key:
        return DisabledProvider()
    return GeminiProvider(api_key=key, model=model)
