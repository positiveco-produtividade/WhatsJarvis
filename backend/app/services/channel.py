"""Interface de canal (ADR 2026-10-19): so o que este incremento usa.

Contrato da spec porta-fechada.1, 4.3.2. Implementacoes: `WhatsAppCloudChannel`
(producao) e `tests.fakes.FakeChannel` (testes). sendList/sendMedia entram nos
incrementos que precisarem.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SendResult:
    """Resultado de um envio.

    `transient`: nada foi aceito pela Meta, vale retentar.
    `ambiguous`: a Meta pode ter aceito (ex.: ReadTimeout); retentar pode duplicar.
    Os dois falsos com `ok=False` = falha definitiva. Codigo/subcodigo sao os crus da Meta.
    """

    ok: bool
    wa_message_id: str | None = None
    error_code: str | None = None
    error_subcode: str | None = None
    transient: bool = False
    ambiguous: bool = False


@dataclass(frozen=True)
class ReplyButton:
    """Botao de resposta rapida de mensagem interativa (titulo ate 20 caracteres)."""

    id: str
    title: str


@dataclass(frozen=True)
class TemplateMessage:
    """Mensagem de modelo aprovado: parametros do corpo e payloads dos botoes de resposta rapida."""

    name: str
    language: str
    body_params: tuple[str, ...] = ()
    quick_reply_payloads: tuple[str, ...] = ()


class Channel(Protocol):
    async def send_text(self, to: str, body: str) -> SendResult: ...

    async def send_template(self, to: str, template: TemplateMessage) -> SendResult: ...

    async def send_reply_buttons(
        self, to: str, body: str, buttons: Sequence[ReplyButton]
    ) -> SendResult: ...

    def verify_signature(self, raw_body: bytes, header: str) -> bool: ...
