"""Cliente WAHA para o grupo da equipe (spec porta-fechada.1, AC-13).

So camada de rede: quem decide se o grupo esta ligado e a camada de entrega
(`Flags.team_group_enabled`); este modulo apenas envia quando chamado. Nunca
levanta excecao; logs nao carregam o chatId nem o texto.
"""
from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings
from app.services.notifications import DeliveryResult, DeliveryStatus, classify_status

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0


def _message_id(resp: httpx.Response) -> str | None:
    """Engine WEBJS devolve {"id": {"_serialized": ...}}; 2xx com corpo vazio e entrega valida."""
    try:
        ident = resp.json().get("id")
        value = ident.get("_serialized") if isinstance(ident, dict) else None
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


async def _post(client: httpx.AsyncClient, cfg: Settings, text: str) -> DeliveryResult:
    url = f"{cfg.waha_base_url}/api/sendText"
    payload = {"session": cfg.waha_session, "chatId": cfg.team_group_chat_id, "text": text}
    headers = {"X-Api-Key": cfg.waha_api_key.get_secret_value()}
    try:
        resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning("waha send failed: timeout")
        return DeliveryResult(DeliveryStatus.TRANSIENT, "timeout")
    except httpx.HTTPError as exc:
        logger.warning("waha send failed: %s", type(exc).__name__)
        return DeliveryResult(DeliveryStatus.TRANSIENT, "network_error")
    status = classify_status(resp.status_code)
    if status is DeliveryStatus.OK:
        return DeliveryResult(status, "sent", _message_id(resp))
    logger.warning("waha send failed: http %s", resp.status_code)
    return DeliveryResult(status, f"http_{resp.status_code}")


async def send_group_text(
    text: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DeliveryResult:
    """Envia texto ao grupo da equipe. Chamar so se a chave do grupo estiver ligada."""
    try:
        cfg = settings or get_settings()
        if not (cfg.waha_api_key.get_secret_value() and cfg.team_group_chat_id.strip()):
            logger.info("waha send skipped: not configured")
            return DeliveryResult(DeliveryStatus.SKIPPED, "not_configured")
        if client is not None:
            return await _post(client, cfg, text)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as own:
            return await _post(own, cfg, text)
    except Exception as exc:  # noqa: BLE001 - contrato: falha nunca chega ao chamador
        logger.error("waha send unexpected error: %s", type(exc).__name__)
        return DeliveryResult(DeliveryStatus.TRANSIENT, "unexpected_error")
