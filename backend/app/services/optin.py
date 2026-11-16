"""Opt-in (spec 4.4.3): emitir aviso, aceitar, recusar e lembrar. Tudo na transacao do chamador.

O chamador (users.register/resend/swap_number ou o pipeline) abre e commita a transacao;
o envio so acontece depois, pela fila de saida (`outbox`). Boas-vindas sempre por modelo
aprovado, nunca texto livre (AC-17). Botao de aviso antigo ou de outro usuario nao muda
nada (AC-19). Sem PII em log nem em auditoria (so ids).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.values import (
    OptinStatus,
    OutboundKind,
    UserStatus,
    optin_accept_payload,
    optin_decline_payload,
)
from app.services import audit, brand_session, outbox
from app.services.access import AccessUser, should_send_reminder

ACTOR = "whatsapp"
ACTOR_TYPE = "system"

_LOCK_USER = (
    "SELECT status, optin_reminder_at FROM access.users WHERE id = $1 FOR UPDATE"
)
_SUPERSEDE = (
    "UPDATE access.optin_requests SET status = 'superseded' "
    "WHERE user_id = $1 AND status = 'open'"
)
_INSERT_NOTICE = (
    "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, issued_by) "
    "VALUES ($1, 'open', $2, $3, $4) RETURNING id"
)
_LOCK_OPEN_NOTICE = (
    "SELECT id, notice_version FROM access.optin_requests "
    "WHERE id = $1 AND user_id = $2 AND status = 'open' FOR UPDATE"
)
_CLOSE_NOTICE = (
    "UPDATE access.optin_requests SET status = $2, responded_at = $3, "
    "responded_wa_message_id = $4 WHERE id = $1"
)
_ACCEPT_USER = (
    "UPDATE access.users SET status = 'active', status_changed_at = $2, accepted_at = $2, "
    "accepted_notice_version = $3, bsuid = COALESCE($4::text, bsuid), updated_at = $2 "
    "WHERE id = $1"
)
_DECLINE_USER = (
    "UPDATE access.users SET status = 'declined', status_changed_at = $2, declined_at = $2, "
    "updated_at = $2 WHERE id = $1"
)
_MARK_REMINDER = (
    "UPDATE access.users SET optin_reminder_at = $2, updated_at = $2 WHERE id = $1"
)
_OPEN_NOTICE_ID = (
    "SELECT id FROM access.optin_requests WHERE user_id = $1 AND status = 'open'"
)


class NoticeTarget(Protocol):
    """Pessoa que recebe o aviso: o que o opt-in precisa saber dela."""

    @property
    def id(self) -> int: ...

    @property
    def phone_e164(self) -> str: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True)
class IssuedNotice:
    """Aviso aberto e a saida de boas-vindas enfileirada."""

    notice_id: int
    outbound_id: int | None  # None no canal sem modelos (WAHA): nada e enviado no cadastro


def _now(clock: Clock | None) -> datetime:
    return (clock or SystemClock()).now()


def welcome_template(settings: Settings, name: str, notice_id: int) -> dict[str, Any]:
    """Modelo aprovado de boas-vindas: nome no corpo e payloads dos dois botoes (AC-17/19).

    Chaves iguais aos campos de `channel.TemplateMessage` (PF-25 monta o envio a partir delas).
    """
    return {
        "name": settings.welcome_template_name,
        "language": settings.welcome_template_lang,
        "body_params": [name],
        "quick_reply_payloads": [
            optin_accept_payload(notice_id),
            optin_decline_payload(notice_id),
        ],
    }


async def issue_notice(
    conn: Any,
    user: NoticeTarget,
    issued_by: str,
    *,
    settings: Settings,
    clock: Clock | None = None,
) -> IssuedNotice:
    """Marca o aviso anterior `superseded`, abre outro e enfileira o modelo de boas-vindas.

    Nao mexe em `access.users.status`: quem chama (register/resend/swap_number) deixa o
    usuario `pending_optin` na mesma transacao.
    """
    now = _now(clock)
    await conn.fetchrow(_LOCK_USER, user.id)
    await conn.execute(_SUPERSEDE, user.id)
    notice_id: int = await conn.fetchval(
        _INSERT_NOTICE, user.id, settings.notice_version, now, issued_by
    )
    if settings.channel_provider == "waha":
        # Canal sem modelos (canal-waha.1, WA-11): o cadastro so abre o aviso. O vendedor
        # inicia pelo link wa.me e recebe o aviso completo na 1a mensagem (maybe_remind).
        return IssuedNotice(notice_id=notice_id, outbound_id=None)
    outbound_id = await outbox.enqueue(
        conn,
        user,
        OutboundKind.WELCOME_TEMPLATE,
        template=welcome_template(settings, user.name, notice_id),
        optin_request_id=notice_id,
        clock=clock,
    )
    return IssuedNotice(notice_id=notice_id, outbound_id=outbound_id)


async def _lock_pending_notice(
    conn: Any, user: NoticeTarget, notice_id: int
) -> Any | None:
    """Aviso `open` do usuario, travado, se o usuario ainda esta `pending_optin`; senao None."""
    state = await conn.fetchrow(_LOCK_USER, user.id)
    if state is None or state["status"] != UserStatus.PENDING_OPTIN.value:
        return None
    return await conn.fetchrow(_LOCK_OPEN_NOTICE, notice_id, user.id)


async def accept(
    conn: Any,
    user: NoticeTarget,
    notice_id: int,
    bsuid: str | None,
    now: datetime,
    *,
    settings: Settings,
    wa_message_id: str | None = None,
    inbound_trace_id: int | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Aceite (AC-18): so vale se `notice_id` e o aviso `open` do usuario.

    Devolve o id da confirmacao enfileirada, ou None quando o toque nao vale (nada muda).
    """
    notice = await _lock_pending_notice(conn, user, notice_id)
    if notice is None:
        return None
    await conn.execute(_CLOSE_NOTICE, notice_id, OptinStatus.ACCEPTED.value, now, wa_message_id)
    await conn.execute(_ACCEPT_USER, user.id, now, notice["notice_version"], bsuid)
    await audit.write(
        conn, ACTOR_TYPE, ACTOR, "user.optin_accepted", "user", str(user.id),
        old={"status": UserStatus.PENDING_OPTIN.value},
        new={"status": UserStatus.ACTIVE.value, "notice_id": notice_id},
        clock=clock,
    )  # fmt: skip
    return await outbox.enqueue(
        conn,
        user,
        OutboundKind.OPTIN_CONFIRMATION,
        body=settings.text_optin_confirmed,
        inbound_trace_id=inbound_trace_id,
        optin_request_id=notice_id,
        clock=clock,
    )


_USER_BRANDS = "SELECT brands FROM access.users WHERE id = $1::bigint"


async def after_accept(
    conn: Any,
    user: NoticeTarget,
    now: datetime,
    *,
    settings: Settings,
    inbound_trace_id: int | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Marca da conversa logo depois do aceite (canal-waha.1, WA-15), na mesma transacao.

    Com 2+ marcas no cadastro, enfileira a enquete "Sobre qual marca voce quer falar?" e
    devolve o id; com 1 marca, ela ja vira a marca da conversa (sem enquete) e devolve None.
    So no canal do WAHA e com `BRAND_SESSION_MINUTES` > 0; senao nao faz nada.
    """
    if settings.channel_provider != "waha" or not brand_session.enabled(
        settings.brand_session_minutes
    ):
        return None
    brands = brand_session.ordered(await conn.fetchval(_USER_BRANDS, user.id) or [])
    if not brands:
        return None
    if len(brands) == 1:
        await brand_session.set_brand(conn, user.id, brands[0], now)
        return None
    return await brand_session.enqueue_poll(
        conn,
        user,
        settings.text_brand_poll_start,
        brands,
        inbound_trace_id=inbound_trace_id,
        clock=clock,
    )


async def decline(
    conn: Any,
    user: NoticeTarget,
    notice_id: int,
    now: datetime,
    *,
    settings: Settings,
    wa_message_id: str | None = None,
    inbound_trace_id: int | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Recusa (AC-20): `declined`, `declined_at` e texto de recusa na fila.

    Devolve o id da saida enfileirada, ou None quando o toque nao vale (nada muda).
    """
    notice = await _lock_pending_notice(conn, user, notice_id)
    if notice is None:
        return None
    await conn.execute(_CLOSE_NOTICE, notice_id, OptinStatus.DECLINED.value, now, wa_message_id)
    await conn.execute(_DECLINE_USER, user.id, now)
    await audit.write(
        conn, ACTOR_TYPE, ACTOR, "user.optin_declined", "user", str(user.id),
        old={"status": UserStatus.PENDING_OPTIN.value},
        new={"status": UserStatus.DECLINED.value, "notice_id": notice_id},
        clock=clock,
    )  # fmt: skip
    return await outbox.enqueue(
        conn,
        user,
        OutboundKind.OPTIN_DECLINED,
        body=settings.text_optin_declined,
        inbound_trace_id=inbound_trace_id,
        optin_request_id=notice_id,
        clock=clock,
    )


async def maybe_remind(
    conn: Any,
    user: NoticeTarget,
    now: datetime,
    *,
    settings: Settings,
    inbound_trace_id: int | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Lembrete interativo (AC-21), no maximo 1 por intervalo (padrao 24 h).

    Exige `pending_optin` e um aviso `open`. Os botoes saem de `optin_request_id` na hora do
    envio (`replies.build_optin_reminder`). Devolve o id da saida, ou None se nao e hora.
    """
    state = await conn.fetchrow(_LOCK_USER, user.id)
    if state is None or state["status"] != UserStatus.PENDING_OPTIN.value:
        return None
    notice_id = await conn.fetchval(_OPEN_NOTICE_ID, user.id)
    if notice_id is None:
        return None
    snapshot = AccessUser(
        status=UserStatus.PENDING_OPTIN, optin_reminder_at=state["optin_reminder_at"]
    )
    if not should_send_reminder(snapshot, now, settings.optin_reminder_interval_hours):
        return None
    await conn.execute(_MARK_REMINDER, user.id, now)
    return await outbox.enqueue(
        conn,
        user,
        OutboundKind.OPTIN_REMINDER,
        # No WAHA o lembrete e o aviso completo em texto, terminado em "Responda ACEITO..."
        # (WA-12); na Meta, o texto curto de hoje com os botoes do aviso.
        body=(
            settings.text_optin_notice
            if settings.channel_provider == "waha"
            else settings.text_optin_reminder
        ),
        inbound_trace_id=inbound_trace_id,
        optin_request_id=notice_id,
        clock=clock,
    )
