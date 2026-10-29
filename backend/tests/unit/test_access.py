"""Testes puros da decisao de acesso (4.4.2) e dos textos fixos."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.core.values import (
    AccessDecision,
    ReplyKind,
    UserStatus,
    optin_accept_payload,
    optin_decline_payload,
    parse_optin_payload,
)
from app.services.access import (
    AccessUser,
    SenderState,
    can_send_proactive,
    decide,
    is_opt_out_text,
    normalize_keyword,
    should_send_reminder,
)
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.replies import build_optin_reminder, notice_buttons, text_for

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
NOTICE = 7

ALL_KINDS = list(ContentKind)
MEDIA = [k for k in ContentKind if k.is_media]
OTHER = [
    ContentKind.BUTTON,
    ContentKind.INTERACTIVE,
    ContentKind.REACTION,
    ContentKind.SYSTEM,
    ContentKind.UNSUPPORTED,
]
NO_TEXT_KINDS = (
    ReplyKind.NONE,
    ReplyKind.PENDING,
    ReplyKind.WELCOME_TEMPLATE,
    # texto do cerebro e dinamico (CEREBRO-1)
    ReplyKind.BRAIN_ANSWER,
    ReplyKind.BRAIN_ANSWER_RATE,
    ReplyKind.BRAIN_NOTICE,
)


def msg(
    kind: ContentKind, *, text: str | None = None, payload: str | None = None
) -> InboundMessage:
    button = ButtonReply(payload=payload) if payload is not None else None
    return InboundMessage(
        wa_message_id="wamid.X",
        sender=Sender(wa_id="5511999990000"),
        kind=kind,
        text=text,
        button=button,
    )


def user(status: UserStatus, open_notice_id: int | None = NOTICE) -> SenderState:
    return SenderState(user=AccessUser(status=status, open_notice_id=open_notice_id))


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_unknown_always_unknown(kind: ContentKind) -> None:
    assert decide(SenderState(), msg(kind), NOW) is AccessDecision.UNKNOWN


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_conflict_always_conflict(kind: ContentKind) -> None:
    assert decide(SenderState(conflict=True), msg(kind), NOW) is AccessDecision.IDENTITY_CONFLICT


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize(
    ("status", "expected"),
    [(UserStatus.DECLINED, AccessDecision.DECLINED), (UserStatus.REVOKED, AccessDecision.REVOKED)],
)
def test_declined_revoked_silent_for_everything(
    status: UserStatus, expected: AccessDecision, kind: ContentKind
) -> None:
    assert decide(user(status), msg(kind), NOW) is expected


@pytest.mark.parametrize("status", [UserStatus.DECLINED, UserStatus.REVOKED, UserStatus.ACTIVE])
@pytest.mark.parametrize("payload", [optin_accept_payload(NOTICE), optin_decline_payload(NOTICE)])
def test_old_button_changes_nothing_outside_pending(status: UserStatus, payload: str) -> None:
    decision = decide(user(status), msg(ContentKind.BUTTON, payload=payload), NOW)
    expected = {
        UserStatus.DECLINED: AccessDecision.DECLINED,
        UserStatus.REVOKED: AccessDecision.REVOKED,
        UserStatus.ACTIVE: AccessDecision.IGNORED_TYPE,
    }[status]
    assert decision is expected


def test_pending_accept_and_decline_of_open_notice() -> None:
    s = user(UserStatus.PENDING_OPTIN)
    accept = msg(ContentKind.BUTTON, payload=optin_accept_payload(NOTICE))
    decline = msg(ContentKind.BUTTON, payload=optin_decline_payload(NOTICE))
    assert decide(s, accept, NOW) is AccessDecision.OPTIN_ACCEPTED
    assert decide(s, decline, NOW) is AccessDecision.OPTIN_DECLINED


@pytest.mark.parametrize(
    "payload",
    [optin_accept_payload(NOTICE + 1), optin_decline_payload(NOTICE - 1), "OPTIN_ACCEPT:x", "oi"],
)
def test_pending_old_or_invalid_button_is_pending(payload: str) -> None:
    got = decide(user(UserStatus.PENDING_OPTIN), msg(ContentKind.BUTTON, payload=payload), NOW)
    assert got is AccessDecision.PENDING_OPTIN


def test_pending_without_open_notice_never_accepts() -> None:
    s = user(UserStatus.PENDING_OPTIN, open_notice_id=None)
    got = decide(s, msg(ContentKind.BUTTON, payload=optin_accept_payload(NOTICE)), NOW)
    assert got is AccessDecision.PENDING_OPTIN


@pytest.mark.parametrize("kind", [k for k in ALL_KINDS if k is not ContentKind.BUTTON])
def test_pending_other_content_is_pending_never_accepts(kind: ContentKind) -> None:
    text = "aceito" if kind is ContentKind.TEXT else None
    got = decide(user(UserStatus.PENDING_OPTIN), msg(kind, text=text), NOW)
    assert got is AccessDecision.PENDING_OPTIN


def test_active_text_allowed() -> None:
    got = decide(user(UserStatus.ACTIVE), msg(ContentKind.TEXT, text="oi"), NOW)
    assert got is AccessDecision.ALLOWED


@pytest.mark.parametrize("kind", MEDIA)
def test_active_media_escalated(kind: ContentKind) -> None:
    assert decide(user(UserStatus.ACTIVE), msg(kind), NOW) is AccessDecision.ESCALATED_MEDIA


@pytest.mark.parametrize("kind", OTHER)
def test_active_other_ignored(kind: ContentKind) -> None:
    assert decide(user(UserStatus.ACTIVE), msg(kind), NOW) is AccessDecision.IGNORED_TYPE


def test_active_opt_out_is_allowed_decision() -> None:
    got = decide(user(UserStatus.ACTIVE), msg(ContentKind.TEXT, text="Sair"), NOW)
    assert got is AccessDecision.ALLOWED


@pytest.mark.parametrize("text", ["sair", "SAIR", "  Sair ", "sair.", "Sair!", "sáir"])
def test_opt_out_normalized(text: str) -> None:
    assert is_opt_out_text(msg(ContentKind.TEXT, text=text))


@pytest.mark.parametrize("text", ["quero sair", "sairr", "", "sair agora", "s a i r"])
def test_not_opt_out(text: str) -> None:
    assert not is_opt_out_text(msg(ContentKind.TEXT, text=text))


def test_opt_out_requires_text_kind() -> None:
    assert not is_opt_out_text(msg(ContentKind.AUDIO, text="sair"))
    assert normalize_keyword("  SÁIR?! ") == "sair"


def test_reminder_once_per_24h() -> None:
    never = AccessUser(status=UserStatus.PENDING_OPTIN)
    assert should_send_reminder(never, NOW)
    recent = AccessUser(
        status=UserStatus.PENDING_OPTIN, optin_reminder_at=NOW - timedelta(hours=23)
    )
    assert not should_send_reminder(recent, NOW)
    edge = AccessUser(
        status=UserStatus.PENDING_OPTIN, optin_reminder_at=NOW - timedelta(hours=24)
    )
    assert should_send_reminder(edge, NOW)
    assert should_send_reminder(recent, NOW, interval_hours=12)


@pytest.mark.parametrize(
    ("u", "expected"),
    [
        (AccessUser(UserStatus.ACTIVE, accepted_at=NOW), True),
        (AccessUser(UserStatus.ACTIVE, accepted_at=NOW, proactive_opt_out=True), False),
        (AccessUser(UserStatus.ACTIVE, accepted_at=None), False),
        (AccessUser(UserStatus.PENDING_OPTIN, accepted_at=NOW), False),
        (AccessUser(UserStatus.DECLINED, accepted_at=NOW), False),
        (AccessUser(UserStatus.REVOKED, accepted_at=NOW), False),
    ],
)
def test_can_send_proactive(u: AccessUser, expected: bool) -> None:
    assert can_send_proactive(u) is expected


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql://x",
        whatsapp_app_secret="s",
        whatsapp_verify_token="v",
        whatsapp_access_token="t",
        whatsapp_phone_number_id="1",
    )


def test_texts_come_from_settings(settings: Settings) -> None:
    assert text_for(ReplyKind.UNDER_CONSTRUCTION, settings) == settings.text_under_construction
    assert text_for(ReplyKind.MEDIA_ACK, settings) == settings.text_media_ack
    assert text_for(ReplyKind.RATE_LIMIT_NOTICE, settings) == settings.text_rate_limit
    assert text_for(ReplyKind.OPT_OUT_CONFIRMATION, settings) == settings.text_opt_out_confirmed
    assert text_for(ReplyKind.OPTIN_CONFIRMATION, settings) == settings.text_optin_confirmed
    assert text_for(ReplyKind.OPTIN_DECLINED, settings) == settings.text_optin_declined


@pytest.mark.parametrize("kind", NO_TEXT_KINDS)
def test_no_free_text_for_non_text_kinds(kind: ReplyKind, settings: Settings) -> None:
    assert text_for(kind, settings) is None


def test_every_other_kind_has_text(settings: Settings) -> None:
    for kind in ReplyKind:
        if kind not in NO_TEXT_KINDS:
            assert text_for(kind, settings)


def test_reminder_buttons_carry_notice_id(settings: Settings) -> None:
    reply = build_optin_reminder(settings, 42)
    assert reply.body == settings.text_optin_reminder
    accept, decline = reply.buttons
    assert parse_optin_payload(accept.id) == (True, 42)
    assert parse_optin_payload(decline.id) == (False, 42)
    assert all(len(b.title) <= 20 for b in notice_buttons(1))
