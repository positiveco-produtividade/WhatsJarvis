import json
import logging

import pytest
from pydantic import BaseModel, SecretStr

from app.core import log_redaction
from app.core.log_redaction import (
    MASK,
    RedactingFilter,
    redact,
    register_model_secrets,
    register_secret_str,
)
from app.core.logging_json import (
    JsonFormatter,
    get_request_id,
    reset_request_id,
    set_request_id,
)


@pytest.fixture(autouse=True)
def _clean_exact() -> None:
    saved = list(log_redaction._EXACT)
    yield  # type: ignore[misc]
    log_redaction._EXACT[:] = saved


@pytest.mark.parametrize(
    "text",
    [
        "enviado para +5511987654321 ok",
        "enviado para 5511987654321 ok",
        "enviado para 551187654321 ok",
        "enviado para +55 (11) 98765-4321 ok",
        "enviado para +14155552671 ok",
    ],
)
def test_phone_is_masked(text: str) -> None:
    out = redact(text)
    assert "987654321" not in out and "87654321" not in out and "4155552671" not in out
    assert "***" in out


def test_phone_keeps_last_four_only() -> None:
    assert redact("tel 5511987654321") == "tel ***4321"


def test_short_numbers_untouched() -> None:
    assert redact("usuario #1234 id 98765") == "usuario #1234 id 98765"


def test_email_is_masked() -> None:
    out = redact("alerta para fulano@example.com enviado")
    assert "fulano" not in out
    assert "[EMAIL]" in out


def test_meta_token_and_key_value_masked() -> None:
    token = "EAA" + "x" * 30
    assert token not in redact(f"url?access_token={token}")
    assert "abcdefgh12345" not in redact('{"api_key": "abcdefgh12345"}')
    assert "supersecret99" not in redact("Authorization: Bearer supersecret99")


def test_dsn_password_masked() -> None:
    out = redact("postgresql://jarvis:minhasenha@localhost:5432/jarvis")
    assert "minhasenha" not in out
    assert out.startswith("postgresql://jarvis:" + MASK + "@")


def test_redact_is_idempotent() -> None:
    once = redact("tel 5511987654321 mail a@b.com")
    assert redact(once) == once


def test_registered_secretstr_masked_anywhere() -> None:
    register_secret_str(SecretStr("valor-sem-formato-conhecido"))
    assert "valor-sem-formato-conhecido" not in redact("x valor-sem-formato-conhecido y")


def test_register_model_secrets_scans_secretstr_fields() -> None:
    class Cfg(BaseModel):
        name: str = "jarvis"
        webhook_key: SecretStr = SecretStr("segredo-do-modelo-123")
        plain: str = "visivel"

    assert register_model_secrets(Cfg()) == 1
    assert redact("segredo-do-modelo-123") == MASK
    assert redact("visivel") == "visivel"


def test_secretstr_never_in_repr() -> None:
    secret = SecretStr("nao-pode-aparecer-123")
    assert "nao-pode-aparecer-123" not in repr(secret)
    assert "nao-pode-aparecer-123" not in str(secret)


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args or None, None)


def test_filter_masks_msg_and_args() -> None:
    rec = _record("envio %s", "5511987654321")
    assert RedactingFilter().filter(rec) is True
    assert "987654321" not in rec.getMessage()


def test_json_formatter_one_line_with_fields() -> None:
    token = set_request_id("req-42")
    try:
        rec = _record("evento_ok")
        rec.user_id = 7  # extra
        line = JsonFormatter().format(rec)
    finally:
        reset_request_id(token)
    assert "\n" not in line
    data = json.loads(line)
    assert data["event"] == "evento_ok"
    assert data["level"] == "INFO"
    assert data["logger"] == "t"
    assert data["request_id"] == "req-42"
    assert data["user_id"] == 7
    assert "ts" in data
    assert get_request_id() is None


def test_json_formatter_redacts_pii_and_extras() -> None:
    rec = _record("falha para %s", "+5511987654321")
    rec.email = "pessoa@empresa.com.br"
    line = JsonFormatter().format(rec)
    assert "987654321" not in line
    assert "empresa.com.br" not in line


def test_json_formatter_redacts_registered_secret_in_exception() -> None:
    register_secret_str(SecretStr("segredo-no-traceback-1"))
    try:
        raise RuntimeError("falhou com segredo-no-traceback-1")
    except RuntimeError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "erro", None, sys.exc_info())
    assert "segredo-no-traceback-1" not in JsonFormatter().format(rec)
