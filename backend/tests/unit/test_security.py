import pytest
from pydantic import SecretStr

from app.core.security import (
    compute_hub_signature,
    constant_time_equals,
    verify_hub_signature,
)

SECRET = "app-secret-de-teste"
RAW = b'{"object":"whatsapp_business_account","x":"a\xc3\xa7\xc3\xa3o"}'


@pytest.mark.security
def test_valid_signature_accepted() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, SECRET), SECRET) is True


@pytest.mark.security
def test_signature_is_over_raw_bytes_not_reserialized_json() -> None:
    header = compute_hub_signature(RAW, SECRET)
    reserialized = RAW.replace(b'{"object"', b'{ "object"')
    assert verify_hub_signature(reserialized, header, SECRET) is False


@pytest.mark.security
def test_secretstr_secret_supported() -> None:
    header = compute_hub_signature(RAW, SecretStr(SECRET))
    assert verify_hub_signature(RAW, header, SecretStr(SECRET)) is True


@pytest.mark.security
@pytest.mark.parametrize(
    "header",
    [None, "", "sha256=", "abc", "sha1=deadbeef", "sha256=" + "0" * 64, "SHA256=ab"],
)
def test_bad_headers_rejected(header: str | None) -> None:
    assert verify_hub_signature(RAW, header, SECRET) is False


@pytest.mark.security
@pytest.mark.parametrize(
    "header",
    ["sha256=çãõ", "sha256=" + "a" * 63 + "é", "‮sha256=abc", "sha256=😀"],
)
def test_non_ascii_header_returns_false_without_exception(header: str) -> None:
    assert verify_hub_signature(RAW, header, SECRET) is False


@pytest.mark.security
def test_non_ascii_header_does_not_reject_valid_ascii_one() -> None:
    header = compute_hub_signature(RAW, SECRET)
    assert verify_hub_signature(RAW, header, SECRET) is True
    assert verify_hub_signature(RAW, header + "é", SECRET) is False


@pytest.mark.security
def test_empty_secret_never_validates() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, ""), "") is False


@pytest.mark.security
def test_wrong_secret_rejected() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, "outro"), SECRET) is False


@pytest.mark.security
def test_constant_time_equals() -> None:
    assert constant_time_equals("token-123", "token-123") is True
    assert constant_time_equals("token-124", "token-123") is False
    assert constant_time_equals(None, "token-123") is False
    assert constant_time_equals("", "") is False
    assert constant_time_equals("tökén", "tökén") is True
    assert constant_time_equals("\ud83d", "token") is False
    assert constant_time_equals("token-123", SecretStr("token-123")) is True
