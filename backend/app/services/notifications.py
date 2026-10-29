"""Cliente de e-mail (Resend), so camada de rede (spec porta-fechada.1, AC-37).

Padrao AUTO-1 (httpx + Resend). Nunca levanta excecao para o chamador: toda
falha vira `DeliveryResult`. Logs nao carregam endereco, assunto nem corpo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 15.0
_TRANSIENT_STATUS = frozenset({408, 425, 429})

_skip_warned = False


class DeliveryStatus(StrEnum):
    OK = "ok"
    TRANSIENT = "transient"  # vale retentar (5xx, 429, timeout, rede)
    PERMANENT = "permanent"  # retentar nao adianta (4xx de configuracao/conteudo)
    SKIPPED = "skipped"  # sem configuracao: nada foi enviado


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Resultado de um envio. `detail` e um codigo curto, sem PII."""

    status: DeliveryStatus
    detail: str = ""
    message_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DeliveryStatus.OK


def classify_status(status_code: int) -> DeliveryStatus:
    """Mapeia o status HTTP para o resultado tipado."""
    if 200 <= status_code < 300:
        return DeliveryStatus.OK
    if status_code >= 500 or status_code in _TRANSIENT_STATUS:
        return DeliveryStatus.TRANSIENT
    return DeliveryStatus.PERMANENT


def _warn_skipped_once(reason: str) -> None:
    global _skip_warned
    if not _skip_warned:
        _skip_warned = True
        logger.warning("email skipped: %s", reason)


def reset_skip_warning() -> None:
    """Rearma o aviso unico (usado em testes)."""
    global _skip_warned
    _skip_warned = False


def _message_id(resp: httpx.Response) -> str | None:
    try:
        value = resp.json().get("id")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


async def _post(
    client: httpx.AsyncClient, settings: Settings, payload: dict[str, object]
) -> DeliveryResult:
    headers = {"Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}"}
    try:
        resp = await client.post(RESEND_API_URL, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("email send failed: timeout")
        return DeliveryResult(DeliveryStatus.TRANSIENT, "timeout")
    except httpx.HTTPError as exc:
        logger.warning("email send failed: %s", type(exc).__name__)
        return DeliveryResult(DeliveryStatus.TRANSIENT, "network_error")
    status = classify_status(resp.status_code)
    if status is DeliveryStatus.OK:
        return DeliveryResult(status, "sent", _message_id(resp))
    logger.warning("email send failed: http %s", resp.status_code)
    return DeliveryResult(status, f"http_{resp.status_code}")


async def send_email(
    subject: str,
    body: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DeliveryResult:
    """Envia e-mail de alerta ao destinatario de `Settings`. Nunca levanta."""
    try:
        cfg = settings or get_settings()
        if not (
            cfg.resend_api_key.get_secret_value()
            and cfg.resend_from_email
            and cfg.alert_recipient_email.get_secret_value()
        ):
            _warn_skipped_once("RESEND_API_KEY, RESEND_FROM_EMAIL ou ALERT_RECIPIENT_EMAIL ausente")
            return DeliveryResult(DeliveryStatus.SKIPPED, "not_configured")
        payload: dict[str, object] = {
            "from": cfg.resend_from_email,
            "to": [cfg.alert_recipient_email.get_secret_value()],
            "subject": subject,
            "text": body,
        }
        if client is not None:
            return await _post(client, cfg, payload)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as own:
            return await _post(own, cfg, payload)
    except Exception as exc:  # noqa: BLE001 - contrato: falha nunca chega ao chamador
        logger.error("email send unexpected error: %s", type(exc).__name__)
        return DeliveryResult(DeliveryStatus.TRANSIENT, "unexpected_error")
