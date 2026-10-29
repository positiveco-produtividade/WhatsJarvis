"""Testes do parser do payload da Meta (puro, sem I/O).

As fixtures em tests/fixtures/meta/ sao "formato documentado, nao capturado".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.values import parse_optin_payload
from app.services.inbound import (
    ButtonReply,
    ContentKind,
    Sender,
    extract_sender,
    parse_payload,
    split_by_recipient,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "meta"
OUR_ID = "100000000000001"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def test_fixtures_are_marked_as_documented_not_captured() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 12
    for path in files:
        assert "documentado, não capturado" in load(path.stem)["_nota"]


def test_text_message() -> None:
    (msg,) = parse_payload(load("text")).messages
    assert msg.kind is ContentKind.TEXT
    assert msg.text == "Jungle tem glúten?"
    assert msg.text_length == len("Jungle tem glúten?")
    assert msg.wa_message_id == "wamid.TEXT0001"
    assert msg.sender == Sender(wa_id="551199990001", bsuid=None)
    assert msg.timestamp == 1761200000
    assert not msg.is_media


def test_text_length_counts_characters_not_bytes() -> None:
    payload = load("text")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "ação😀"
    (msg,) = parse_payload(payload).messages
    assert msg.text_length == 5


@pytest.mark.parametrize(
    ("fixture", "expected_payload", "expected_text"),
    [
        ("button_template", "OPTIN_ACCEPT:42", "Aceito"),
        ("button_interactive", "OPTIN_DECLINE:42", "Não quero"),
    ],
)
def test_both_button_shapes_unify_into_button_reply(
    fixture: str, expected_payload: str, expected_text: str
) -> None:
    (msg,) = parse_payload(load(fixture)).messages
    assert msg.kind is ContentKind.BUTTON
    assert msg.button == ButtonReply(payload=expected_payload, text=expected_text)
    assert msg.text is None
    assert parse_optin_payload(msg.button.payload) is not None


def test_audio_and_image_expose_type_and_mime_only() -> None:
    (audio,) = parse_payload(load("audio")).messages
    (image,) = parse_payload(load("image")).messages
    assert (audio.kind, audio.mime_type) == (ContentKind.AUDIO, "audio/ogg; codecs=opus")
    assert (image.kind, image.mime_type) == (ContentKind.IMAGE, "image/jpeg")
    assert audio.is_media and image.is_media
    assert audio.text is None and image.text is None and image.text_length is None


@pytest.mark.parametrize("status", ["sent", "delivered", "read", "failed"])
def test_statuses_produce_no_messages(status: str) -> None:
    parsed = parse_payload(load(f"status_{status}"))
    assert parsed.messages == ()
    (st,) = parsed.statuses
    assert st.status == status
    assert st.wa_message_id == "wamid.OUT00001"
    assert st.is_failed is (status == "failed")


def test_failed_status_carries_error_code() -> None:
    (st,) = parse_payload(load("status_failed")).statuses
    assert st.error_code == 131047
    assert st.error_title == "Re-engagement message"


def test_multiple_payload_keeps_each_message_with_its_own_sender() -> None:
    parsed = parse_payload(load("multiple"))
    assert [m.wa_message_id for m in parsed.messages] == ["wamid.MUL00001", "wamid.MUL00002"]
    assert [m.sender.wa_id for m in parsed.messages] == ["551199990001", "551199990002"]
    assert len(parsed.statuses) == 1


def test_split_by_recipient_discards_whole_foreign_value() -> None:
    mixed = load("other_phone_number_id")
    mixed["entry"][0]["changes"].append(load("text")["entry"][0]["changes"][0])
    split = split_by_recipient(parse_payload(mixed), OUR_ID)
    assert split.wrong_recipient_count == 1
    assert [m.wa_message_id for m in split.accepted.messages] == ["wamid.TEXT0001"]


def test_split_by_recipient_other_number_only() -> None:
    split = split_by_recipient(parse_payload(load("other_phone_number_id")), OUR_ID)
    assert split.accepted.values == ()
    assert split.wrong_recipient_count == 1


def test_split_by_recipient_also_drops_foreign_statuses() -> None:
    payload = load("status_sent")
    payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "999"
    split = split_by_recipient(parse_payload(payload), OUR_ID)
    assert split.accepted.statuses == ()
    assert split.wrong_recipient_count == 1


def test_bsuid_only_sender_has_no_wa_id() -> None:
    (msg,) = parse_payload(load("bsuid_only")).messages
    assert msg.sender == Sender(wa_id=None, bsuid="BR.13491208655302741918")
    assert not msg.sender.is_empty


def test_extract_sender_uses_contact_user_id_when_message_lacks_it() -> None:
    sender = extract_sender(
        {"from": "551199990001"}, [{"wa_id": "551199990001", "user_id": "BR.1"}]
    )
    assert sender == Sender(wa_id="551199990001", bsuid="BR.1")


def test_extract_sender_ignores_contact_of_another_wa_id() -> None:
    sender = extract_sender(
        {"from": "551199990001"}, [{"wa_id": "551199990002", "user_id": "BR.2"}]
    )
    assert sender == Sender(wa_id="551199990001", bsuid=None)


def test_extract_sender_without_identifiers_is_empty() -> None:
    assert extract_sender({}, []).is_empty


@pytest.mark.parametrize(
    ("msg_type", "body", "kind"),
    [
        ("video", {"id": "1", "mime_type": "video/mp4"}, ContentKind.VIDEO),
        ("document", {"id": "1", "mime_type": "application/pdf"}, ContentKind.DOCUMENT),
        ("sticker", {"id": "1", "mime_type": "image/webp"}, ContentKind.STICKER),
        ("location", {"latitude": 1.0, "longitude": 2.0}, ContentKind.LOCATION),
        ("contacts", [{"name": {"formatted_name": "X"}}], ContentKind.CONTACTS),
        ("reaction", {"message_id": "wamid.X", "emoji": "👍"}, ContentKind.REACTION),
        ("system", {"body": "mudou o número", "type": "user_changed_number"}, ContentKind.SYSTEM),
        ("unsupported", {}, ContentKind.UNSUPPORTED),
        ("order", {"catalog_id": "1"}, ContentKind.UNSUPPORTED),
    ],
)
def test_other_content_kinds(msg_type: str, body: object, kind: ContentKind) -> None:
    payload = load("text")
    raw = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del raw["text"]
    raw["type"] = msg_type
    raw[msg_type] = body
    (msg,) = parse_payload(payload).messages
    assert msg.kind is kind
    assert msg.raw_type == msg_type
    assert msg.text is None


def test_media_kinds_flag() -> None:
    media = {k for k in ContentKind if k.is_media}
    assert media == {
        ContentKind.AUDIO,
        ContentKind.IMAGE,
        ContentKind.VIDEO,
        ContentKind.DOCUMENT,
        ContentKind.STICKER,
        ContentKind.LOCATION,
        ContentKind.CONTACTS,
    }


def test_interactive_list_reply_is_interactive_without_button() -> None:
    payload = load("button_interactive")
    raw = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    raw["interactive"] = {"type": "list_reply", "list_reply": {"id": "a", "title": "A"}}
    (msg,) = parse_payload(payload).messages
    assert msg.kind is ContentKind.INTERACTIVE
    assert msg.button is None


def test_button_without_payload_is_unsupported() -> None:
    payload = load("button_template")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["button"] = {"text": "x"}
    (msg,) = parse_payload(payload).messages
    assert msg.kind is ContentKind.UNSUPPORTED
    assert msg.button is None


@pytest.mark.parametrize(
    "obj",
    [
        None,
        [],
        "x",
        {},
        {"object": "page"},
        {"object": "whatsapp_business_account"},
        {"object": "whatsapp_business_account", "entry": "x"},
        {"object": "whatsapp_business_account", "entry": [1, None, {"changes": 3}]},
        {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "other"}]}]},
        {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages"}]}]},
    ],
)
def test_malformed_payloads_never_raise_and_yield_nothing(obj: object) -> None:
    parsed = parse_payload(obj)
    assert parsed.messages == ()
    assert parsed.statuses == ()


def test_items_without_id_or_status_are_dropped() -> None:
    value = {
        "metadata": {"phone_number_id": OUR_ID},
        "messages": [{"from": "55", "type": "text", "text": {"body": "x"}}, "lixo"],
        "statuses": [{"id": "wamid.X"}, {"status": "sent"}],
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": value}]}],
    }
    parsed = parse_payload(payload)
    assert parsed.messages == ()
    assert parsed.statuses == ()
    assert parsed.values[0].phone_number_id == OUR_ID


def test_non_string_timestamp_and_bool_are_tolerated() -> None:
    payload = load("text")
    raw = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    raw["timestamp"] = True
    (msg,) = parse_payload(payload).messages
    assert msg.timestamp is None


def test_huge_digit_strings_do_not_raise() -> None:
    payload = load("text")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"] = "9" * 5000
    (msg,) = parse_payload(payload).messages
    assert msg.timestamp is None

    failed = load("status_failed")
    status = failed["entry"][0]["changes"][0]["value"]["statuses"][0]
    status["timestamp"] = "9" * 5000
    for err in status.get("errors", []):
        err["code"] = "9" * 5000
    (st,) = parse_payload(failed).statuses
    assert st.timestamp is None
