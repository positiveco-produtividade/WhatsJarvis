"""`Channel` sobre o WAHA do Jarvis (spec canal-waha.1, 9.3 e 9.4; task WA-T08).

Antes de cada envio, so marca a conversa como vista (recomendacao do WAHA contra bloqueio);
sem "digitando" nem espera, para responder o mais rapido possivel (decisao do Caio em
2026-11-16). Falha no "visto" so vira log; nunca impede o envio. Nunca levanta excecao de
rede: devolve `SendResult`.

O canal nao tem botoes nem modelos confiaveis (`WAHA_CAPABILITIES`): `send_template` devolve
`unsupported` (nunca e chamado, WA-11) e `send_reply_buttons` manda so o corpo como texto.

Classificacao de erros (WA-18): conexao recusada, 429 e 503 = transitorio (nada saiu);
tempo esgotado depois do envio e 500/502/504 = ambiguo (nao retentar); corpo com 463/475
(restricao do numero) = definitivo com `error_code` `waha_463`/`waha_475`, que o monitor da
sessao transforma em alerta; outro 4xx = definitivo. Logs sem numero, texto nem chave.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.core.security import WAHA_HMAC_ALGORITHM, verify_waha_hmac
from app.services.channel import (
    WAHA_CAPABILITIES,
    ChannelCapabilities,
    PollOption,
    ReplyButton,
    SendResult,
    TemplateMessage,
)
from app.services.whatsapp import classify_exception

logger = logging.getLogger("jarvis.waha")

HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
TEXT_MAX_CHARS = 4096
TRANSIENT_HTTP_STATUS: frozenset[int] = frozenset({429, 503})
AMBIGUOUS_HTTP_STATUS: frozenset[int] = frozenset({500, 502, 504})
RESTRICTION_CODES: tuple[str, ...] = ("463", "475")
RESTRICTED_PREFIX = "waha_"
_RESTRICTION_RE = re.compile(r"(?<!\d)(463|475)(?!\d)")

SessionWorking = Callable[[], bool]
OnRestriction = Callable[[], Awaitable[None]]


WA_ME_TEXT = "Oi Jarvis"


def wa_me_link(number_e164: str, text: str = WA_ME_TEXT) -> str:
    """Link `wa.me` do numero do Jarvis com texto pronto (canal-waha.1, WA-11): o gestor
    repassa ao vendedor, que inicia a conversa (o Jarvis nunca inicia no WAHA)."""
    digits = number_e164.strip().lstrip("+")
    return f"https://wa.me/{digits}?text={quote(text)}"


def chat_id(to: str) -> str:
    """`+5585999999999` -> `5585999999999@c.us` (formato de chat individual do WAHA)."""
    digits = to.strip().lstrip("+")
    if not (digits.isascii() and digits.isdigit()):
        raise ValueError("destino do WAHA precisa ser um telefone em E.164")
    return f"{digits}@c.us"


def extract_message_id(data: Any) -> str | None:
    """Id da mensagem enviada nas formas conhecidas do WAHA (premissa R2).

    WEBJS: `{"id": {"_serialized": ...}}`; outras engines: `{"id": "..."}`,
    `{"key": {"id": ...}}` ou `{"messageId": ...}`.
    """
    if not isinstance(data, dict):
        return None
    ident = data.get("id")
    if isinstance(ident, dict):
        ident = ident.get("_serialized") or ident.get("id")
    if isinstance(ident, str) and ident:
        return ident
    key = data.get("key")
    if isinstance(key, dict) and isinstance(key.get("id"), str) and key["id"]:
        return str(key["id"])
    other = data.get("messageId")
    return other if isinstance(other, str) and other else None


def _error_text(response: httpx.Response) -> str:
    """Texto das mensagens de erro do corpo (sem o corpo inteiro: pode ecoar o destino)."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for field in ("message", "error", "exception", "code"):
        value = data.get(field)
        if isinstance(value, str | int):
            parts.append(str(value))
        elif isinstance(value, dict):
            parts.extend(str(v) for v in value.values() if isinstance(v, str | int))
    return " ".join(parts)


def classify_response(response: httpx.Response) -> SendResult:
    """Traduz a resposta HTTP do WAHA em `SendResult` (tabela da spec 9.3)."""
    status = response.status_code
    if response.is_success:
        try:
            message_id = extract_message_id(response.json())
        except ValueError:
            message_id = None
        if message_id is None:
            return SendResult(ok=False, error_code="no_message_id", ambiguous=True)
        return SendResult(ok=True, wa_message_id=message_id)
    restriction = _RESTRICTION_RE.search(_error_text(response))
    if restriction is not None:
        return SendResult(ok=False, error_code=f"{RESTRICTED_PREFIX}{restriction.group(1)}")
    code = f"http_{status}"
    if status in TRANSIENT_HTTP_STATUS:
        return SendResult(ok=False, error_code=code, transient=True)
    if status in AMBIGUOUS_HTTP_STATUS or status >= 500:
        return SendResult(ok=False, error_code=code, ambiguous=True)
    return SendResult(ok=False, error_code=code)


def is_restriction(result: SendResult) -> bool:
    """True se o envio falhou por restricao do numero (463/475): vira alerta (WA-18)."""
    return result.error_code in {f"{RESTRICTED_PREFIX}{c}" for c in RESTRICTION_CODES}


class WahaChannel:
    """Canal dos vendedores pelo WAHA do Jarvis (`JARVIS_WAHA_*`)."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        session_working: SessionWorking | None = None,
    ) -> None:
        self._base = settings.jarvis_waha_base_url.rstrip("/")
        self._session = settings.jarvis_waha_session
        self._api_key = settings.jarvis_waha_api_key
        self._hmac_key = settings.jarvis_waha_hmac_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        self._owns_client = client is None
        self._session_working = session_working
        self._on_restriction: OnRestriction | None = None

    @property
    def capabilities(self) -> ChannelCapabilities:
        return WAHA_CAPABILITIES

    def attach_monitor(
        self, session_working: SessionWorking, on_restriction: OnRestriction
    ) -> None:
        """Liga o monitor da sessao (WA-T16): com a sessao fora, envio vira transitorio sem
        chamar o WAHA; restricao do numero (463/475) avisa o monitor, que abre o alerta."""
        self._session_working = session_working
        self._on_restriction = on_restriction

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------ envio

    async def send_text(self, to: str, body: str) -> SendResult:
        text = body[:TEXT_MAX_CHARS]
        return await self._send(
            to, "text", lambda chat: {"chatId": chat, "text": text, "linkPreview": False}
        )

    async def send_poll(
        self, to: str, question: str, options: Sequence[PollOption]
    ) -> SendResult:
        if not options:
            raise ValueError("enquete precisa de ao menos uma opcao")
        poll = {"name": question, "options": [o.title for o in options], "multipleAnswers": False}
        return await self._send(to, "poll", lambda chat: {"chatId": chat, "poll": poll})

    async def send_template(self, to: str, template: TemplateMessage) -> SendResult:
        logger.warning("waha_send_failed", extra={"kind": "template", "error_code": "unsupported"})
        return SendResult(ok=False, error_code="unsupported")

    async def send_reply_buttons(
        self, to: str, body: str, buttons: Sequence[ReplyButton]
    ) -> SendResult:
        # Sem botoes confiaveis no WAHA: o corpo ja traz a instrucao em texto (ex.: ACEITO/NAO).
        return await self.send_text(to, body)

    def verify_signature(self, raw_body: bytes, header: str) -> bool:
        return verify_waha_hmac(raw_body, header, WAHA_HMAC_ALGORITHM, self._hmac_key)

    # ------------------------------------------------------------------ internos

    async def _send(
        self,
        to: str,
        kind: str,
        build: Callable[[str], dict[str, Any]],
    ) -> SendResult:
        if self._session_working is not None and not self._session_working():
            result = SendResult(ok=False, error_code="session_not_working", transient=True)
            self._log_failure(kind, result)
            return result
        chat = chat_id(to)
        await self._best_effort("sendSeen", chat)
        endpoint = "sendText" if kind == "text" else "sendPoll"
        payload = {"session": self._session, **build(chat)}
        try:
            response = await self._client.post(
                f"{self._base}/api/{endpoint}", json=payload, headers=self._headers()
            )
        except httpx.TransportError as exc:
            result = classify_exception(exc)
        else:
            result = classify_response(response)
        if not result.ok:
            self._log_failure(kind, result)
            if is_restriction(result) and self._on_restriction is not None:
                try:
                    await self._on_restriction()
                except Exception as exc:  # noqa: BLE001 - alerta nunca derruba o envio
                    logger.error(
                        "waha_restriction_alert_failed", extra={"error": type(exc).__name__}
                    )
        return result

    async def _best_effort(self, endpoint: str, chat: str) -> None:
        try:
            response = await self._client.post(
                f"{self._base}/api/{endpoint}",
                json={"session": self._session, "chatId": chat},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            logger.info(
                "waha_presence_failed", extra={"step": endpoint, "error": type(exc).__name__}
            )
            return
        if not response.is_success:
            logger.info(
                "waha_presence_failed", extra={"step": endpoint, "status": response.status_code}
            )

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key.get_secret_value()}

    @staticmethod
    def _log_failure(kind: str, result: SendResult) -> None:
        logger.warning(
            "waha_send_failed",
            extra={
                "kind": kind,
                "error_code": result.error_code,
                "transient": result.transient,
                "ambiguous": result.ambiguous,
            },
        )
