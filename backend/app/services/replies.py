"""Textos fixos de resposta e montagem das mensagens interativas (puro, sem I/O).

Os textos vem de `Settings.TEXT_*` (spec 4.3.4); este modulo so escolhe o texto certo
por `ReplyKind` e monta os botoes de aviso com o id do aviso no payload (AC-19).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.config import Settings
from app.core.values import ReplyKind, optin_accept_payload, optin_decline_payload
from app.services.channel import ReplyButton

ACCEPT_BUTTON_TITLE = "Aceito"
DECLINE_BUTTON_TITLE = "Não aceito"  # limite da Meta: 20 caracteres

_TEXT_GETTERS: dict[ReplyKind, Callable[[Settings], str]] = {
    ReplyKind.UNDER_CONSTRUCTION: lambda s: s.text_under_construction,
    ReplyKind.OPTIN_CONFIRMATION: lambda s: s.text_optin_confirmed,
    ReplyKind.OPTIN_DECLINED: lambda s: s.text_optin_declined,
    ReplyKind.OPTIN_REMINDER: lambda s: s.text_optin_reminder,
    ReplyKind.RATE_LIMIT_NOTICE: lambda s: s.text_rate_limit,
    ReplyKind.OPT_OUT_CONFIRMATION: lambda s: s.text_opt_out_confirmed,
    ReplyKind.MEDIA_ACK: lambda s: s.text_media_ack,
}


@dataclass(frozen=True)
class InteractiveReply:
    """Mensagem interativa de botoes de resposta: corpo e botoes."""

    body: str
    buttons: tuple[ReplyButton, ...]


def text_for(kind: ReplyKind, settings: Settings) -> str | None:
    """Texto fixo da resposta, ou None quando o tipo nao tem texto livre.

    `WELCOME_TEMPLATE` e enviado por modelo aprovado (nunca texto livre, AC-17);
    `NONE` e `PENDING` nao sao respostas.
    """
    getter = _TEXT_GETTERS.get(kind)
    return getter(settings) if getter is not None else None


def notice_buttons(notice_id: int) -> tuple[ReplyButton, ...]:
    """Botoes Aceito / Nao aceito cujo payload carrega o id do aviso."""
    return (
        ReplyButton(id=optin_accept_payload(notice_id), title=ACCEPT_BUTTON_TITLE),
        ReplyButton(id=optin_decline_payload(notice_id), title=DECLINE_BUTTON_TITLE),
    )


def build_optin_reminder(settings: Settings, notice_id: int) -> InteractiveReply:
    """Lembrete interativo de aceite (AC-21), com os botoes do aviso aberto."""
    return InteractiveReply(body=settings.text_optin_reminder, buttons=notice_buttons(notice_id))
