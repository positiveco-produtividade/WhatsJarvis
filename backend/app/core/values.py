"""Valores fixos do dominio (dono unico: ninguem mais edita este arquivo).

Enums, listas e o dataclass compartilhado entre webhook e pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UserStatus(StrEnum):
    PENDING_OPTIN = "pending_optin"
    ACTIVE = "active"
    DECLINED = "declined"
    REVOKED = "revoked"


class OptinStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    SUPERSEDED = "superseded"


class AccessDecision(StrEnum):
    ALLOWED = "allowed"
    UNKNOWN = "unknown"
    PENDING_OPTIN = "pending_optin"
    OPTIN_ACCEPTED = "optin_accepted"
    OPTIN_DECLINED = "optin_declined"
    DECLINED = "declined"
    REVOKED = "revoked"
    RATE_LIMITED = "rate_limited"
    ESCALATED_MEDIA = "escalated_media"
    IGNORED_TYPE = "ignored_type"
    IDENTITY_CONFLICT = "identity_conflict"
    ERROR = "error"
    PROCESSING = "processing"  # transitorio: nunca pode ficar commitado


class Decision(StrEnum):
    """Decisao final do rastro: access_decision mais send_failed/revoked_before_send."""

    ALLOWED = "allowed"
    UNKNOWN = "unknown"
    PENDING_OPTIN = "pending_optin"
    OPTIN_ACCEPTED = "optin_accepted"
    OPTIN_DECLINED = "optin_declined"
    DECLINED = "declined"
    REVOKED = "revoked"
    RATE_LIMITED = "rate_limited"
    ESCALATED_MEDIA = "escalated_media"
    IGNORED_TYPE = "ignored_type"
    IDENTITY_CONFLICT = "identity_conflict"
    ERROR = "error"
    SEND_FAILED = "send_failed"
    REVOKED_BEFORE_SEND = "revoked_before_send"


class AttemptReason(StrEnum):
    UNKNOWN = "unknown"
    REVOKED = "revoked"
    DECLINED = "declined"
    IDENTITY_CONFLICT = "identity_conflict"


class OutboundKind(StrEnum):
    """`kind` de chat.outbound_messages."""

    WELCOME_TEMPLATE = "welcome_template"
    OPTIN_REMINDER = "optin_reminder"
    OPTIN_CONFIRMATION = "optin_confirmation"
    OPTIN_DECLINED = "optin_declined"
    UNDER_CONSTRUCTION = "under_construction"
    RATE_LIMIT_NOTICE = "rate_limit_notice"
    MEDIA_ACK = "media_ack"
    OPT_OUT_CONFIRMATION = "opt_out_confirmation"
    BRAIN_ANSWER = "brain_answer"
    BRAIN_ANSWER_RATE = "brain_answer_rate"
    BRAIN_NOTICE = "brain_notice"


class OutboundStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    DISCARDED = "discarded"
    UNKNOWN = "unknown"


class ReplyKind(StrEnum):
    """`reply_kind` do rastro: none | pending | <kind de saida>."""

    NONE = "none"
    PENDING = "pending"
    WELCOME_TEMPLATE = "welcome_template"
    OPTIN_REMINDER = "optin_reminder"
    OPTIN_CONFIRMATION = "optin_confirmation"
    OPTIN_DECLINED = "optin_declined"
    UNDER_CONSTRUCTION = "under_construction"
    RATE_LIMIT_NOTICE = "rate_limit_notice"
    MEDIA_ACK = "media_ack"
    OPT_OUT_CONFIRMATION = "opt_out_confirmation"
    BRAIN_ANSWER = "brain_answer"
    BRAIN_ANSWER_RATE = "brain_answer_rate"
    BRAIN_NOTICE = "brain_notice"


class BrainOutcome(StrEnum):
    """`outcome` de chat.brain_runs (CHECK da migration)."""

    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BRAND_DISABLED = "brand_disabled"
    ACCESS_REQUEST = "access_request"
    INJECTION = "injection"
    REFUSAL = "refusal"
    GREETING = "greeting"
    ASK_BRAND = "ask_brand"


class SendResult(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    DISCARDED = "discarded"
    UNKNOWN = "unknown"
    ABANDONED = "abandoned"


SEND_RESULT: tuple[str, ...] = tuple(m.value for m in SendResult)


class DiscardReason(StrEnum):
    REVOKED_BEFORE_SEND = "revoked_before_send"
    STATUS_CHANGED = "status_changed"
    PAUSED = "paused"
    BRAND_DISABLED = "brand_disabled"


class RevokeReason(StrEnum):
    LEFT_COMPANY = "left_company"
    NUMBER_CHANGE = "number_change"
    PARTNER_CONTRACT_END = "partner_contract_end"
    MONTHLY_RECONCILIATION = "monthly_reconciliation"
    DECLINED_NOTICE = "declined_notice"
    OTHER = "other"


REVOKE_REASONS: tuple[str, ...] = tuple(m.value for m in RevokeReason)

PROFILES: tuple[str, ...] = (
    "vendedor",
    "gestor_comercial",
    "executivo",
    "parceiro_externo",
    "administrador",
)

BRANDS: tuple[str, ...] = ("jungle", "plant_power", "possible", "a_tal_da_castanha", "zaya")

UFS: tuple[str, ...] = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)  # fmt: skip

# Lista inicial de canais (Q4/Q15): mesma do prototipo aprovado do painel
# (docs/designs/painel-prototipo.html, cadastro de pessoa). Valor novo = editar aqui.
CHANNELS: tuple[str, ...] = (
    "atacarejo",
    "supermercado",
    "farma",
    "conveniencia",
    "food_service",
    "e_commerce",
)

OPTIN_ACCEPT_PREFIX = "OPTIN_ACCEPT:"
OPTIN_DECLINE_PREFIX = "OPTIN_DECLINE:"


def optin_accept_payload(notice_id: int) -> str:
    return f"{OPTIN_ACCEPT_PREFIX}{notice_id}"


def optin_decline_payload(notice_id: int) -> str:
    return f"{OPTIN_DECLINE_PREFIX}{notice_id}"


def parse_optin_payload(payload: str) -> tuple[bool, int] | None:
    """Devolve (aceitou?, id do aviso) ou None se nao for payload de opt-in valido."""
    for prefix, accepted in ((OPTIN_ACCEPT_PREFIX, True), (OPTIN_DECLINE_PREFIX, False)):
        if payload.startswith(prefix):
            raw = payload[len(prefix) :]
            if raw.isascii() and raw.isdigit():
                return accepted, int(raw)
            return None
    return None

RATE_UP_PREFIX = "RATE_UP:"
RATE_DOWN_PREFIX = "RATE_DOWN:"
_MAX_TICKET_ID = 2**63 - 1  # bigint do Postgres


def rate_up_payload(ticket_id: int) -> str:
    return f"{RATE_UP_PREFIX}{ticket_id}"


def rate_down_payload(ticket_id: int) -> str:
    return f"{RATE_DOWN_PREFIX}{ticket_id}"


def parse_rating_payload(payload: str) -> tuple[bool, int] | None:
    """Devolve (util?, id do ticket) ou None se nao for payload de avaliacao valido."""
    for prefix, useful in ((RATE_UP_PREFIX, True), (RATE_DOWN_PREFIX, False)):
        if payload.startswith(prefix):
            raw = payload[len(prefix) :]
            if raw.isascii() and raw.isdigit() and len(raw) <= 19:
                ticket_id = int(raw)
                if 0 < ticket_id <= _MAX_TICKET_ID:
                    return useful, ticket_id
            return None
    return None


@dataclass(frozen=True)
class MessageOutcome:
    """Resultado do tratamento de uma mensagem (webhook e pipeline compartilham).

    `answer_request` e opaco aqui (o tipo concreto vem do ponto de extensao
    `answering`); `transient_failure` indica que o webhook deve responder 503.
    """

    outbound_ids: tuple[int, ...] = ()
    delivery_ids: tuple[int, ...] = ()
    answer_request: object | None = None
    transient_failure: bool = False
