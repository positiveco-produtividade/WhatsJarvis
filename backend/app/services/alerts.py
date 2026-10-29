"""Alertas (R3, spec 4.4.6): registro em ops.alerts + linhas de entrega em ops.deliveries.

Roda na transacao de quem chama. Dedup sob pg_advisory_xact_lock. Ao criar, insere a
entrega `group` (pending se a chave do grupo esta ligada, senao panel_only) e, so se
`email=True`, a entrega `email` (pending, ou skipped se o provedor nao esta configurado).
Mensagem e payload nunca carregam PII: o payload e validado de forma defensiva.
Tentativas de acesso e tickets escalados NAO passam por aqui.
"""

import json
import logging
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.services.flags import Flags

logger = logging.getLogger(__name__)

SEVERITIES: tuple[str, ...] = ("info", "warn", "high", "critical")

# Tokens (separados por nao alfanumericos) que indicam PII ou texto de mensagem.
FORBIDDEN_PAYLOAD_TOKENS: frozenset[str] = frozenset(
    {
        "phone", "telefone", "fone", "celular", "whatsapp", "wa", "msisdn", "e164",
        "name", "nome", "text", "texto", "body", "corpo", "message", "mensagem", "msg",
        "bsuid", "email", "mail", "address", "endereco", "cpf", "content", "conteudo",
    }
)  # fmt: skip
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_EMAIL_RE = re.compile(r"\S+@\S+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_MAX_STR_VALUE = 80

_INSERT_ALERT = (
    "INSERT INTO ops.alerts (severity, kind, message, payload, user_id, dedup_key, created_at) "
    "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id"
)
_INSERT_DELIVERY = (
    "INSERT INTO ops.deliveries "
    "(source_type, source_id, channel, status, attempts, next_attempt_at, last_error, created_at) "
    "VALUES ('alert', $1, $2, $3, 0, $4, $5, $4)"
)
_RECENT = (
    "SELECT 1 FROM ops.alerts WHERE dedup_key = $1 "
    "AND ($2::timestamptz IS NULL OR created_at > $2) LIMIT 1"
)


def _check_key(path: str, key: str) -> None:
    tokens = set(_TOKEN_SPLIT.split(key.lower()))
    if tokens & FORBIDDEN_PAYLOAD_TOKENS:
        raise InvalidValue("payload", f"chave nao permitida: {path}{key}")


def _check_scalar(path: str, value: Any) -> None:
    if isinstance(value, str) and (
        len(value) > _MAX_STR_VALUE or _EMAIL_RE.search(value) or _PHONE_RE.search(value)
    ):
        raise InvalidValue("payload", f"valor nao permitido em: {path}")


def validate_payload(payload: Mapping[str, Any], path: str = "") -> None:
    """Recusa chaves de PII/texto e valores que parecem telefone ou e-mail (recursivo)."""
    for key, value in payload.items():
        _check_key(path, str(key))
        if isinstance(value, Mapping):
            validate_payload(value, f"{path}{key}.")
        elif isinstance(value, list | tuple):
            for item in value:
                if isinstance(item, Mapping):
                    validate_payload(item, f"{path}{key}.")
                else:
                    _check_scalar(f"{path}{key}", item)
        else:
            _check_scalar(f"{path}{key}", value)


def _validate(severity: str, message: str, payload: Mapping[str, Any]) -> None:
    if severity not in SEVERITIES:
        raise InvalidValue("severity", f"valor nao aceito: {severity}")
    if not message.strip():
        raise InvalidValue("message", "vazia")
    if _EMAIL_RE.search(message) or _PHONE_RE.search(message):
        raise InvalidValue("message", "parece conter telefone ou e-mail")
    validate_payload(payload)


def email_configured(settings: Settings) -> bool:
    return bool(
        settings.resend_api_key.get_secret_value()
        and settings.resend_from_email
        and settings.alert_recipient_email.get_secret_value()
    )


async def _is_duplicate(conn: Any, dedup_key: str, window: timedelta | None, now: Any) -> bool:
    """Trava por chave e procura alerta igual na janela (sem janela = qualquer um)."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", dedup_key)
    since = None if window is None else now - window
    return await conn.fetchval(_RECENT, dedup_key, since) is not None


async def _insert_deliveries(
    conn: Any, alert_id: int, *, group_on: bool, email: bool, settings: Settings, now: Any
) -> list[str]:
    group_status = "pending" if group_on else "panel_only"
    await conn.execute(_INSERT_DELIVERY, alert_id, "group", group_status, now, None)
    channels = ["group"]
    if email:
        ok = email_configured(settings)
        await conn.execute(
            _INSERT_DELIVERY,
            alert_id,
            "email",
            "pending" if ok else "skipped",
            now,
            None if ok else "not_configured",
        )
        channels.append("email")
    return channels


async def raise_alert(
    conn: Any,
    kind: str,
    severity: str,
    message: str,
    payload: Mapping[str, Any],
    *,
    user_id: int | None = None,
    dedup_key: str | None = None,
    dedup_window: timedelta | None = None,
    email: bool = True,
    flags: Flags | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Cria o alerta e suas entregas; devolve o id, ou None se o dedup suprimiu.

    `flags`/`settings`/`clock` sao injetaveis; sem `flags`, vale TEAM_GROUP_ENABLED do ambiente.
    """
    _validate(severity, message, payload)
    cfg = settings or get_settings()
    now = (clock or SystemClock()).now()
    if dedup_key is not None and await _is_duplicate(conn, dedup_key, dedup_window, now):
        return None
    group_on = (
        await flags.team_group_enabled(conn=conn) if flags is not None else cfg.team_group_enabled
    )
    alert_id: int = await conn.fetchval(
        _INSERT_ALERT, severity, kind, message, json.dumps(dict(payload)), user_id, dedup_key, now
    )
    channels = await _insert_deliveries(
        conn, alert_id, group_on=group_on, email=email, settings=cfg, now=now
    )
    logger.info(
        "alert_created",
        extra={"alert_id": alert_id, "kind": kind, "severity": severity, "channels": channels},
    )
    return alert_id
