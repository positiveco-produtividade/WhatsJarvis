"""Interface de canal (ADR 2026-10-19): so o que este incremento usa.

Contrato da spec porta-fechada.1, 4.3.2, ampliado em canal-waha.1 (9.3): capacidades do
canal e enquete. Implementacoes: `WhatsAppCloudChannel` (Meta), `WahaChannel` (WAHA) e
`tests.fakes.FakeChannel` (testes). sendList/sendMedia entram nos incrementos que precisarem.
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


@dataclass(frozen=True)
class ChannelCapabilities:
    """O que o canal entrega de forma confiavel (spec canal-waha.1, 9.3).

    Quem monta respostas consulta isto em vez de saber qual e o canal: sem `buttons`, nada de
    botoes (opt-in por texto, sem avaliacao); sem `templates`, nada de modelo de boas-vindas.
    """

    buttons: bool
    templates: bool


META_CAPABILITIES = ChannelCapabilities(buttons=True, templates=True)
WAHA_CAPABILITIES = ChannelCapabilities(buttons=False, templates=False)


@dataclass(frozen=True)
class PollOption:
    """Opcao de enquete (escolha de marca, WA-15): `id` e o payload do botao na Meta;
    `title` e o texto exibido, e e ele que volta no voto do WAHA."""

    id: str
    title: str


class Channel(Protocol):
    @property
    def capabilities(self) -> ChannelCapabilities: ...

    async def send_text(self, to: str, body: str) -> SendResult: ...

    async def send_template(self, to: str, template: TemplateMessage) -> SendResult: ...

    async def send_reply_buttons(
        self, to: str, body: str, buttons: Sequence[ReplyButton]
    ) -> SendResult: ...

    async def send_poll(
        self, to: str, question: str, options: Sequence[PollOption]
    ) -> SendResult: ...

    def verify_signature(self, raw_body: bytes, header: str) -> bool: ...
