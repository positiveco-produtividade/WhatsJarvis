import re
from dataclasses import FrozenInstanceError

import pytest

from app.core import values
from app.core.values import (
    BRANDS,
    CHANNELS,
    PROFILES,
    REVOKE_REASONS,
    SEND_RESULT,
    UFS,
    AccessDecision,
    BrainOutcome,
    Decision,
    MessageOutcome,
    OutboundKind,
    ReplyKind,
    UserStatus,
    optin_accept_payload,
    optin_decline_payload,
    parse_optin_payload,
    parse_rating_payload,
    rate_down_payload,
    rate_up_payload,
)


def test_user_status_values() -> None:
    assert {s.value for s in UserStatus} == {"pending_optin", "active", "declined", "revoked"}


def test_access_decision_matches_spec() -> None:
    assert {d.value for d in AccessDecision} == {
        "allowed", "unknown", "pending_optin", "optin_accepted", "optin_declined",
        "declined", "revoked", "rate_limited", "escalated_media", "ignored_type",
        "identity_conflict", "error", "processing",
    }  # fmt: skip


def test_decision_is_access_decision_plus_send_outcomes() -> None:
    final = {d.value for d in Decision}
    base = {d.value for d in AccessDecision} - {"processing"}
    assert final == base | {"send_failed", "revoked_before_send"}


def test_outbound_kinds_match_spec_check() -> None:
    assert {k.value for k in OutboundKind} == {
        "welcome_template", "optin_reminder", "optin_confirmation", "optin_declined",
        "under_construction", "rate_limit_notice", "media_ack", "opt_out_confirmation",
        "brain_answer", "brain_answer_rate", "brain_notice",
    }  # fmt: skip


def test_reply_kind_is_none_pending_plus_outbound_kinds() -> None:
    assert {r.value for r in ReplyKind} == {"none", "pending"} | {k.value for k in OutboundKind}


def test_send_result_values() -> None:
    assert set(SEND_RESULT) == {"sent", "failed", "discarded", "unknown", "abandoned"}


def test_lists_sizes_and_uniqueness() -> None:
    assert len(PROFILES) == 5 and len(set(PROFILES)) == 5
    assert len(BRANDS) == 5 and len(set(BRANDS)) == 5
    assert set(BRANDS) == {"jungle", "plant_power", "possible", "a_tal_da_castanha", "zaya"}
    assert len(UFS) == 27 and len(set(UFS)) == 27
    assert {"SP", "RJ", "DF", "TO"} <= set(UFS)
    assert len(CHANNELS) == len(set(CHANNELS)) > 0
    assert set(REVOKE_REASONS) == {
        "left_company", "number_change", "partner_contract_end",
        "monthly_reconciliation", "declined_notice", "other",
    }  # fmt: skip


def test_payload_helpers_roundtrip() -> None:
    assert optin_accept_payload(12) == "OPTIN_ACCEPT:12"
    assert optin_decline_payload(12) == "OPTIN_DECLINE:12"
    assert values.OPTIN_ACCEPT_PREFIX == "OPTIN_ACCEPT:"
    assert values.OPTIN_DECLINE_PREFIX == "OPTIN_DECLINE:"
    assert parse_optin_payload("OPTIN_ACCEPT:12") == (True, 12)
    assert parse_optin_payload("OPTIN_DECLINE:7") == (False, 7)


@pytest.mark.parametrize(
    "payload", ["", "OPTIN_ACCEPT:", "OPTIN_ACCEPT:abc", "OPTIN_ACCEPT:-1", "OPTIN_ACCEPT:1 2",
                "optin_accept:1", "OTHER:1", "OPTIN_ACCEPT:١٢"]
)
def test_parse_payload_rejects_garbage(payload: str) -> None:
    assert parse_optin_payload(payload) is None


def test_brain_outcome_matches_spec_check() -> None:
    assert {o.value for o in BrainOutcome} == {
        "answered", "no_answer", "brand_disabled", "access_request",
        "injection", "refusal", "greeting", "ask_brand",
    }  # fmt: skip


def test_rating_payload_roundtrip() -> None:
    assert rate_up_payload(12) == "RATE_UP:12"
    assert rate_down_payload(12) == "RATE_DOWN:12"
    assert parse_rating_payload("RATE_UP:12") == (True, 12)
    assert parse_rating_payload("RATE_DOWN:7") == (False, 7)
    assert parse_rating_payload(rate_up_payload(2**63 - 1)) == (True, 2**63 - 1)


@pytest.mark.parametrize(
    "payload", ["", "RATE_UP:", "RATE_UP:abc", "RATE_UP:-1", "RATE_UP:+1", "RATE_UP:1 2",
                "RATE_UP: 1", "RATE_UP:1 ", "rate_up:1", "OTHER:1", "RATE_UP:١٢", "RATE_UP:0",
                "RATE_UP:9223372036854775808", "RATE_DOWN:" + "9" * 40, "OPTIN_ACCEPT:1",
                "RATE_UP:1\n"]
)
def test_parse_rating_payload_rejects_garbage(payload: str) -> None:
    assert parse_rating_payload(payload) is None


def test_message_outcome_defaults_and_frozen() -> None:
    out = MessageOutcome()
    assert out.outbound_ids == () and out.delivery_ids == ()
    assert out.answer_request is None and out.transient_failure is False
    with pytest.raises(FrozenInstanceError):
        out.transient_failure = True  # type: ignore[misc]


def test_message_outcome_fields() -> None:
    out = MessageOutcome(outbound_ids=(1, 2), delivery_ids=(3,), transient_failure=True)
    assert out.outbound_ids == (1, 2) and out.delivery_ids == (3,) and out.transient_failure


def test_enum_values_are_snake_case_strings() -> None:
    for enum in (UserStatus, AccessDecision, Decision, OutboundKind, ReplyKind):
        for member in enum:
            assert re.fullmatch(r"[a-z_]+", member.value)
            assert member == member.value
