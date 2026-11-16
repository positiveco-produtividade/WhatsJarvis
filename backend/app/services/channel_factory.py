"""Montagem do canal dos vendedores (spec canal-waha.1, WA-1, WA-2; WA-T11).

O Jarvis opera so pelo WAHA (D5 revisto em 2026-11-16): canal de envio, webhook montado e
resolvedor de numero oculto saem daqui. O codigo da Meta fica guardado, desligado; so a suite
de testes o liga (`Settings.channel_provider == "meta"`, via JARVIS_TEST_CHANNEL_PROVIDER),
para os testes dele continuarem valendo. O webhook do canal desligado nao e montado, entao
responde 404 (WA-2). A volta da Meta e uma historia futura (spec 9.8).
"""
from __future__ import annotations

from typing import Protocol

import httpx
from fastapi import APIRouter

from app.config import Settings
from app.core.clock import Clock
from app.services.channel import Channel
from app.services.waha_channel import WahaChannel
from app.services.waha_lid import LidResolver
from app.services.whatsapp import WhatsAppCloudChannel


class OwnedChannel(Channel, Protocol):
    """Canal criado pela aplicacao: alem do envio, fecha o proprio cliente HTTP."""

    async def aclose(self) -> None: ...


def build_channel(settings: Settings) -> OwnedChannel:
    if settings.channel_provider == "waha":
        return WahaChannel(settings)
    return WhatsAppCloudChannel(settings)


def build_lid_resolver(
    settings: Settings, client: httpx.AsyncClient, clock: Clock | None = None
) -> LidResolver | None:
    """So o WAHA manda `@lid`; com a Meta, nao ha o que resolver."""
    if settings.channel_provider != "waha":
        return None
    return LidResolver(
        base_url=settings.jarvis_waha_base_url,
        api_key=settings.jarvis_waha_api_key,
        session=settings.jarvis_waha_session,
        client=client,
        clock=clock,
    )


def webhook_router(settings: Settings) -> APIRouter:
    """Router do webhook do canal ativo (import tardio: so o canal escolhido e montado)."""
    if settings.channel_provider == "waha":
        from app.api.webhook_waha import router as waha_router

        return waha_router
    from app.api.webhook import router as meta_router

    return meta_router
