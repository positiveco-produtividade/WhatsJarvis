"""Decisao de acesso (spec 4.4.2): funcao pura, sem I/O, relogio injetado via `now`.

Le so o estado do remetente ja resolvido (identidade) e o conteudo. Limite de 60/h,
efeitos (tentativa, alerta, ticket, saida) e `reason=proactive_opt_out` ficam no
pipeline; aqui so se decide e se respondem predicados. `valid_until` e ignorado (Q6).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.values import AccessDecision, UserStatus, parse_optin_payload
from app.services.inbound import ContentKind, InboundMessage

OPT_OUT_KEYWORD = "sair"
_NON_WORD_EDGES = re.compile(r"^[\W_]+|[\W_]+$")


@dataclass(frozen=True)
class AccessUser:
    """Fatos de `access.users` de que a decisao depende."""

    status: UserStatus
    accepted_at: datetime | None = None
    proactive_opt_out: bool = False
    optin_reminder_at: datetime | None = None
    open_notice_id: int | None = None  # id do aviso `open` do usuario (AC-19)


@dataclass(frozen=True)
class SenderState:
    """Resultado da resolucao de identidade: desconhecido, conflito ou usuario."""

    user: AccessUser | None = None
    conflict: bool = False


def normalize_keyword(text: str) -> str:
    """Minusculas, sem acento, sem espacos/pontuacao nas pontas."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_WORD_EDGES.sub("", plain.strip())


def is_opt_out_text(content: InboundMessage) -> bool:
    """Texto "sair" (normalizado). A mensagem tem que ser texto."""
    return (
        content.kind is ContentKind.TEXT
        and content.text is not None
        and normalize_keyword(content.text) == OPT_OUT_KEYWORD
    )


def _accepted_open_notice(user: AccessUser, content: InboundMessage) -> bool | None:
    """True/False se o botao e Aceito/Nao aceito do aviso aberto; None se nao vale."""
    if content.kind is not ContentKind.BUTTON or content.button is None:
        return None
    parsed = parse_optin_payload(content.button.payload)
    if parsed is None or user.open_notice_id is None:
        return None
    accepted, notice_id = parsed
    return accepted if notice_id == user.open_notice_id else None


def _decide_pending(user: AccessUser, content: InboundMessage) -> AccessDecision:
    answer = _accepted_open_notice(user, content)
    if answer is None:
        return AccessDecision.PENDING_OPTIN
    return AccessDecision.OPTIN_ACCEPTED if answer else AccessDecision.OPTIN_DECLINED


def _decide_active(content: InboundMessage) -> AccessDecision:
    if content.kind is ContentKind.TEXT:
        return AccessDecision.ALLOWED
    if content.kind.is_media:
        return AccessDecision.ESCALATED_MEDIA
    return AccessDecision.IGNORED_TYPE


def decide(sender: SenderState, content: InboundMessage, now: datetime) -> AccessDecision:
    """Tabela 4.4.2. `now` fica na assinatura por contrato (o lembrete usa should_send_reminder)."""
    if sender.conflict:
        return AccessDecision.IDENTITY_CONFLICT
    user = sender.user
    if user is None:
        return AccessDecision.UNKNOWN
    if user.status is UserStatus.DECLINED:
        return AccessDecision.DECLINED
    if user.status is UserStatus.REVOKED:
        return AccessDecision.REVOKED
    if user.status is UserStatus.PENDING_OPTIN:
        return _decide_pending(user, content)
    return _decide_active(content)


def should_send_reminder(user: AccessUser, now: datetime, interval_hours: int = 24) -> bool:
    """Lembrete interativo no maximo 1 vez por intervalo (AC-21)."""
    if user.optin_reminder_at is None:
        return True
    return now - user.optin_reminder_at >= timedelta(hours=interval_hours)


def can_send_proactive(user: AccessUser) -> bool:
    """Aviso proativo so para `active` com aceite e sem opt-out (AC-28)."""
    return (
        user.status is UserStatus.ACTIVE
        and user.accepted_at is not None
        and not user.proactive_opt_out
    )
