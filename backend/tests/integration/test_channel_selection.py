"""Escolha do canal na montagem do app (spec canal-waha.1, WA-1, WA-2; task WA-T11)."""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.channel import META_CAPABILITIES, WAHA_CAPABILITIES
from app.services.channel_factory import build_channel, build_lid_resolver, webhook_router
from app.services.waha_channel import WahaChannel
from app.services.waha_lid import LidResolver
from app.services.whatsapp import WhatsAppCloudChannel

BASE: dict[str, Any] = {"database_url": "postgresql://u:p@localhost/x_test", "production": False}
WAHA: dict[str, Any] = {
    "channel_provider": "waha",
    "jarvis_waha_api_key": "chave",
    "jarvis_waha_hmac_key": "hmac",
    "jarvis_whatsapp_number": "+5500000000001",
}
META: dict[str, Any] = {
    "channel_provider": "meta",
    "whatsapp_app_secret": "s",
    "whatsapp_verify_token": "v",
    "whatsapp_access_token": "t",
    "whatsapp_phone_number_id": "123",
}


def settings(kind: dict[str, Any], **extra: Any) -> Settings:
    return Settings(_env_file=None, **{**BASE, **kind, "panel_enabled": False, **extra})  # type: ignore[arg-type]


def paths(cfg: Settings) -> set[str]:
    app = create_app(cfg)
    return {getattr(r, "path", "") for r in app.routes}


def test_waha_monta_so_o_webhook_do_waha() -> None:
    found = paths(settings(WAHA))
    assert "/webhook/waha" in found and "/webhook/whatsapp" not in found


def test_meta_monta_so_o_webhook_da_meta() -> None:
    found = paths(settings(META))
    assert "/webhook/whatsapp" in found and "/webhook/waha" not in found


@pytest.mark.parametrize(("kind", "other"), [(WAHA, "/webhook/whatsapp"), (META, "/webhook/waha")])
def test_webhook_do_outro_canal_responde_404(kind: dict[str, Any], other: str) -> None:
    client = TestClient(create_app(settings(kind)), base_url="http://localhost")
    assert client.post(other, content=b"{}").status_code == 404


def test_fabrica_cria_o_canal_certo() -> None:
    waha = build_channel(settings(WAHA))
    meta = build_channel(settings(META))
    assert isinstance(waha, WahaChannel) and waha.capabilities == WAHA_CAPABILITIES
    assert isinstance(meta, WhatsAppCloudChannel) and meta.capabilities == META_CAPABILITIES


def test_resolvedor_de_lid_so_no_waha() -> None:
    client = httpx.AsyncClient()
    assert isinstance(build_lid_resolver(settings(WAHA), client), LidResolver)
    assert build_lid_resolver(settings(META), client) is None


def test_router_pelo_canal() -> None:
    assert {r.path for r in webhook_router(settings(WAHA)).routes} == {"/webhook/waha"}  # type: ignore[attr-defined]
    assert "/webhook/whatsapp" in {r.path for r in webhook_router(settings(META)).routes}  # type: ignore[attr-defined]
