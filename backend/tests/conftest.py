"""Configuracao global dos testes.

Pula testes marcados com @pytest.mark.db quando TEST_DATABASE_URL nao existe.
Se a variavel nao estiver no ambiente, tenta le-la de backend/.env (parse simples
de linhas CHAVE=valor; arquivo ausente e normal; valores nunca sao impressos).
"""

import os
from pathlib import Path

import pytest

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_KEY = "TEST_DATABASE_URL"


def _load_test_url_from_env_file() -> None:
    if os.environ.get(_KEY) or not _ENV_FILE.is_file():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == _KEY:
            cleaned = value.strip().strip('"').strip("'")
            if cleaned:
                os.environ[_KEY] = cleaned
            return


_load_test_url_from_env_file()

# A suite nunca le o backend/.env de quem roda (CANAL-WAHA-1, T01): antes, campos do .env
# (chaves da Meta, PRODUCTION, DEV_AUTH_EMAIL) mudavam o resultado e quebravam o CI.
# So TEST_DATABASE_URL vem do .env (acima). Valores abaixo sao ficticios e so preenchem o
# que e obrigatorio; o ambiente real (CI, terminal) continua podendo sobrescreve-los.
from app.config import Settings  # noqa: E402  (depois de carregar TEST_DATABASE_URL)

Settings.model_config["env_file"] = None

_TEST_SETTINGS_DEFAULTS = {
    # Suites existentes cobrem a Meta (WA-3); as do WAHA fixam `waha` explicitamente.
    "CHANNEL_PROVIDER": "meta",
    "DATABASE_URL": "postgresql://ficticio:ficticio@localhost:5432/ficticio_test",
    "WHATSAPP_APP_SECRET": "segredo-de-teste",
    "WHATSAPP_VERIFY_TOKEN": "token-de-verificacao-de-teste",
    "WHATSAPP_ACCESS_TOKEN": "token-de-acesso-de-teste",
    "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
}
for _name, _value in _TEST_SETTINGS_DEFAULTS.items():
    os.environ.setdefault(_name, _value)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(_KEY):
        return
    skip_db = pytest.mark.skip(reason="defina TEST_DATABASE_URL; ver backend/DEV_SETUP.md")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
