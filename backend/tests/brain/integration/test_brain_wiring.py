"""Instalacao do provedor no `main` (CB-14, CB-16, CB-22)."""
from __future__ import annotations

from typing import Any

import pytest

from app import main
from app.config import Settings
from app.services import answering
from app.services.brain.provider import BrainProvider
from app.services.flags import Flags


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/x",
        "whatsapp_app_secret": "s",
        "whatsapp_verify_token": "v",
        "whatsapp_access_token": "t",
        "whatsapp_phone_number_id": "1",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


ON: dict[str, Any] = {
    "brain_enabled": True,
    "gemini_api_key": "key-sintetica",
    "brain_price_in_per_mtok_usd": 0.1,
    "brain_price_out_per_mtok_usd": 0.4,
}


def test_desligado_mantem_em_construcao() -> None:
    cfg = _settings()
    provider = main._build_provider(None, cfg, None)  # type: ignore[arg-type]
    assert isinstance(provider, answering.UnderConstructionProvider)


def test_ligado_com_chave_instala_cerebro() -> None:
    cfg = _settings(**ON)
    flags = Flags(None, team_group_initial=False)  # type: ignore[arg-type]
    provider = main._build_provider(None, cfg, flags)  # type: ignore[arg-type]
    assert isinstance(provider, BrainProvider)


def test_ligado_sem_chave_e_recusado_na_config() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        _settings(**{**ON, "gemini_api_key": ""})


def test_ligado_sem_chave_em_settings_sem_validacao_volta_em_construcao() -> None:
    cfg = _settings(**ON).model_copy(update={"gemini_api_key": _settings().gemini_api_key})
    provider = main._build_provider(None, cfg, None)  # type: ignore[arg-type]
    assert isinstance(provider, answering.UnderConstructionProvider)


@pytest.mark.db
@pytest.mark.parametrize(("over", "expected"), [
    ({}, answering.UnderConstructionProvider),
    (ON, BrainProvider),
])
def test_lifespan_instala_o_provedor(
    db_pool: Any, over: dict[str, Any], expected: type
) -> None:
    import os

    from fastapi.testclient import TestClient

    cfg = _settings(database_url=os.environ["TEST_DATABASE_URL"], **over)
    with TestClient(main.create_app(cfg), base_url="http://localhost") as client:
        assert type(client.app.state.processor._provider) is expected  # type: ignore[attr-defined]
