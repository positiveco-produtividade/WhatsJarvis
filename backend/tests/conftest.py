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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(_KEY):
        return
    skip_db = pytest.mark.skip(reason="defina TEST_DATABASE_URL; ver backend/DEV_SETUP.md")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
