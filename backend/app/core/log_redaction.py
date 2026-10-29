"""Filtro de log que apaga credenciais e PII antes de qualquer coisa ir pro disco.

Adaptado do AUTO-1 (vazamento de token da Meta em 28/08/2026, via URL logada
pelo httpx). Estrategia dupla:

1. Por formato: tokens da Meta, pares chave=valor, Bearer, DSN com senha,
   telefones (E.164 e ``55`` + 10/11 digitos) e e-mails.
2. Por valor exato: todo ``SecretStr`` registrado e mascarado literalmente.

Superficie publica:
    redact(texto) -> str
    register_secret(valor) / register_secret_str(SecretStr)
    register_model_secrets(modelo_pydantic)
    RedactingFilter, install()
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, SecretStr

MASK = "[REDIGIDO]"
EMAIL_MASK = "[EMAIL]"

_META_TOKEN = re.compile(r"\b(?:IGAA|IGQV|EAA)[A-Za-z0-9_-]{20,}")

_KEY_VALUE = re.compile(
    r"""(?ix)
    \b(access[_-]?token|api[_-]?key|client[_-]?secret|refresh[_-]?token
       |secret|password|passwd|senha|token|apikey)
    ["']? \s* [=:] \s*
    ["']? ([A-Za-z0-9._~+/=-]{8,})
    """,
)

_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{8,})")

_DSN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s]+):([^@/\s]+)@")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# E.164 com "+" (aceitando separadores comuns) e telefone brasileiro sem "+"
# (55 + DDD + 8/9 digitos).
_PHONE_PLUS = re.compile(r"(?<![\w+])\+\d[\d ()\-]{8,17}\d")
_PHONE_BR = re.compile(r"(?<![\d])55\d{10,11}(?!\d)")

_EXACT: list[str] = []


def register_secret(value: str | None) -> None:
    """Registra um valor literal para ser mascarado onde quer que apareca."""
    if value and len(value) >= 12 and value not in _EXACT:
        _EXACT.append(value)
        _EXACT.sort(key=len, reverse=True)


def register_secret_str(secret: SecretStr | None) -> None:
    """Registra o valor de um SecretStr (ex.: ALERT_RECIPIENT_EMAIL)."""
    if secret is not None:
        register_secret(secret.get_secret_value())


def register_model_secrets(model: BaseModel) -> int:
    """Registra todo campo SecretStr de um modelo pydantic (ex.: Settings).

    Varre os campos em vez de listar nomes: segredo novo ja nasce mascarado.
    Devolve quantos campos SecretStr foram encontrados.
    """
    count = 0
    for name in type(model).model_fields:
        value = getattr(model, name, None)
        if isinstance(value, SecretStr):
            register_secret_str(value)
            count += 1
    return count


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"***{digits[-4:]}"


def redact(text: str) -> str:
    """Devolve `text` sem credenciais nem PII de contato. Idempotente."""
    if not text:
        return text

    for secret in _EXACT:
        if secret in text:
            text = text.replace(secret, MASK)

    text = _META_TOKEN.sub(MASK, text)
    text = _DSN.sub(lambda m: f"{m.group(1)}:{MASK}@", text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}={MASK}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _EMAIL.sub(EMAIL_MASK, text)
    text = _PHONE_PLUS.sub(_mask_phone, text)
    text = _PHONE_BR.sub(_mask_phone, text)
    return text


class RedactingFilter(logging.Filter):
    """Mascara a mensagem e os args de todo LogRecord que passa.

    Deve ficar nos handlers (filtro de logger nao ve o que propaga dos filhos).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)

        if record.exc_text:
            record.exc_text = redact(record.exc_text)

        return True  # nunca descarta a linha, so limpa


def install() -> None:
    """Pluga o RedactingFilter na raiz e nos handlers. Idempotente."""
    raiz = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in raiz.filters):
        raiz.addFilter(RedactingFilter())
    for handler in raiz.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
