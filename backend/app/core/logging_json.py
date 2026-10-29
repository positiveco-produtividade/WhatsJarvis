"""Log JSON de uma linha por registro, com request_id e redacao final."""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from app.core.log_redaction import RedactingFilter, redact

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Atributos padrao de um LogRecord: tudo fora disso e "extra" do chamador.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def set_request_id(value: str | None) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Uma linha JSON: ts, level, logger, event, request_id e extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": get_request_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return redact(line)


class _JarvisJsonHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marca o handler para o setup ser idempotente."""


def setup_logging(level: int = logging.INFO) -> None:
    """Configura a raiz com handler JSON + filtro de redacao. Idempotente."""
    raiz = logging.getLogger()
    raiz.setLevel(level)
    for existing in list(raiz.handlers):
        if isinstance(existing, _JarvisJsonHandler):
            raiz.removeHandler(existing)
    handler = _JarvisJsonHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    raiz.addHandler(handler)
