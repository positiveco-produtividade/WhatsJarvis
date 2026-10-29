"""Cliente da WhatsApp Cloud API (Meta direta) sobre httpx.

Sem cliente de download de midia (AC-31/36). Nada aqui loga token, telefone ou
corpo de mensagem: so tipo, codigos de erro da Meta e flags de classificacao.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.core.security import verify_hub_signature
from app.core.values import optin_accept_payload, optin_decline_payload
from app.services.channel import ReplyButton, SendResult, TemplateMessage

logger = logging.getLogger("jarvis.whatsapp")

GRAPH_ALLOWED_HOSTS: frozenset[str] = frozenset({"graph.facebook.com"})
TEXT_MAX_CHARS = 4096
MAX_REPLY_BUTTONS = 3
BUTTON_TITLE_MAX = 20
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Tabela substituivel: codigo da Meta -> "transient" (nada foi aceito, vale retentar).
# Codigo ausente da tabela = definitivo se for 4xx. Ajustar aqui, sem tocar na logica.
META_ERROR_KINDS: Mapping[str, str] = {
    "4": "transient",  # limite de chamadas da aplicacao
    "17": "transient",  # limite por usuario da API
    "613": "transient",  # limite de chamadas
    "80007": "transient",  # limite da conta WhatsApp Business
    "130429": "transient",  # throughput da Cloud API
    "131056": "transient",  # limite de pares empresa-usuario
}
TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({429, 503})


class InvalidGraphBase(ValueError):
    """Base da Graph API fora da allow-list."""


def validate_graph_base(base: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.hostname not in GRAPH_ALLOWED_HOSTS:
        raise InvalidGraphBase("base da Graph API fora da allow-list")
    return base.rstrip("/")


def build_welcome_template(
    template_name: str, language: str, user_name: str, notice_id: int
) -> TemplateMessage:
    """Unico construtor do modelo de boas-vindas: 1 parametro (nome) e 2 botoes.

    Os payloads dos botoes sao `OPTIN_ACCEPT:<id>` e `OPTIN_DECLINE:<id>` (spec 4.4.3).
    """
    return TemplateMessage(
        name=template_name,
        language=language,
        body_params=(user_name,),
        quick_reply_payloads=(optin_accept_payload(notice_id), optin_decline_payload(notice_id)),
    )


def _template_payload(template: TemplateMessage) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    if template.body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in template.body_params],
            }
        )
    for index, payload in enumerate(template.quick_reply_payloads):
        components.append(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload}],
            }
        )
    return {
        "name": template.name,
        "language": {"code": template.language},
        "components": components,
    }


def _error_fields(response: httpx.Response) -> tuple[str, str | None]:
    """Codigo e subcodigo crus da Meta; sem corpo JSON usa `http_<status>`."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if not isinstance(error, dict):
        return f"http_{response.status_code}", None
    code = error.get("code")
    sub = error.get("error_subcode")
    return (
        str(code) if code is not None else f"http_{response.status_code}",
        str(sub) if sub is not None else None,
    )


def classify_response(response: httpx.Response) -> SendResult:
    """Traduz a resposta HTTP da Meta em `SendResult` (AC-33)."""
    if response.is_success:
        try:
            wa_id = response.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            # 2xx sem id: a Meta pode ter aceito; nao retentar as cegas.
            return SendResult(ok=False, error_code="no_message_id", ambiguous=True)
        return SendResult(ok=True, wa_message_id=str(wa_id))
    code, sub = _error_fields(response)
    status = response.status_code
    if status in TRANSIENT_HTTP_STATUS or (
        400 <= status < 500 and META_ERROR_KINDS.get(code) == "transient"
    ):
        return SendResult(ok=False, error_code=code, error_subcode=sub, transient=True)
    if status >= 500:
        # 500/502/504: a requisicao pode ter sido processada.
        return SendResult(ok=False, error_code=code, error_subcode=sub, ambiguous=True)
    return SendResult(ok=False, error_code=code, error_subcode=sub)


def classify_exception(exc: httpx.TransportError) -> SendResult:
    """Erro de rede: so conexao/pool garantem que nada foi enviado; o resto e ambiguo."""
    name = type(exc).__name__
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return SendResult(ok=False, error_code=name, transient=True)
    return SendResult(ok=False, error_code=name, ambiguous=True)


class WhatsAppCloudChannel:
    """`Channel` sobre a Cloud API. Nunca levanta excecao de rede: devolve `SendResult`."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        base = validate_graph_base(settings.whatsapp_api_base)
        self._url = (
            f"{base}/{settings.whatsapp_graph_version}/"
            f"{settings.whatsapp_phone_number_id}/messages"
        )
        self._token = settings.whatsapp_access_token
        self._app_secret = settings.whatsapp_app_secret
        self._client = client if client is not None else httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_text(self, to: str, body: str) -> SendResult:
        return await self._post(
            to, "text", {"text": {"body": body[:TEXT_MAX_CHARS], "preview_url": False}}
        )

    async def send_template(self, to: str, template: TemplateMessage) -> SendResult:
        return await self._post(to, "template", {"template": _template_payload(template)})

    async def send_reply_buttons(
        self, to: str, body: str, buttons: Sequence[ReplyButton]
    ) -> SendResult:
        if not 1 <= len(buttons) <= MAX_REPLY_BUTTONS:
            raise ValueError(f"mensagem interativa aceita de 1 a {MAX_REPLY_BUTTONS} botoes")
        if any(len(b.title) > BUTTON_TITLE_MAX for b in buttons):
            raise ValueError(f"titulo de botao acima de {BUTTON_TITLE_MAX} caracteres")
        interactive = {
            "type": "button",
            "body": {"text": body[:TEXT_MAX_CHARS]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in buttons
                ]
            },
        }
        return await self._post(to, "interactive", {"interactive": interactive})

    def verify_signature(self, raw_body: bytes, header: str) -> bool:
        return verify_hub_signature(raw_body, header, self._app_secret)

    async def _post(self, to: str, kind: str, content: dict[str, Any]) -> SendResult:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to.lstrip("+"),
            "type": kind,
            **content,
        }
        headers = {"Authorization": f"Bearer {self._token.get_secret_value()}"}
        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            result = classify_exception(exc)
        else:
            result = classify_response(response)
        if not result.ok:
            logger.warning(
                "envio WhatsApp falhou",
                extra={
                    "kind": kind,
                    "error_code": result.error_code,
                    "error_subcode": result.error_subcode,
                    "transient": result.transient,
                    "ambiguous": result.ambiguous,
                },
            )
        return result
