import pytest

from app.services.phone import (
    MSG_LANDLINE,
    VALID_DDDS,
    InvalidPhoneError,
    PhoneNumber,
    mask_phone,
    normalize_phone,
)

CEL = PhoneNumber(phone_e164="+5511987654321", match_key="551187654321")


@pytest.mark.parametrize(
    "raw",
    [
        "+5511987654321",
        "5511987654321",
        "+55 (11) 98765-4321",
        "  +55 11 98765 4321 ",
        "+551187654321",  # sem o 9 (formato do wa_id)
        "551187654321",
        "+55 (11) 8765-4321",
    ],
)
def test_br_mobile_with_and_without_ninth_digit(raw: str) -> None:
    assert normalize_phone(raw) == CEL


def test_wa_id_and_registered_share_match_key() -> None:
    assert normalize_phone("5511987654321").match_key == normalize_phone("551187654321").match_key


@pytest.mark.parametrize("raw", ["+551132654321", "551132654321", "+55 (11) 5265-4321"])
def test_landline_rejected(raw: str) -> None:
    with pytest.raises(InvalidPhoneError, match=MSG_LANDLINE):
        normalize_phone(raw)


@pytest.mark.parametrize("raw", ["+5500987654321", "+5520987654321", "+5510987654321"])
def test_nonexistent_ddd(raw: str) -> None:
    with pytest.raises(InvalidPhoneError, match="DDD"):
        normalize_phone(raw)


@pytest.mark.parametrize("raw", ["11987654321", "(11) 98765-4321", "1187654321"])
def test_missing_ddi(raw: str) -> None:
    with pytest.raises(InvalidPhoneError, match="DDI"):
        normalize_phone(raw)


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "abc", "+55 11 98765-43x1", "+55119876543", "+5511887654321", "+551101234567"],
)
def test_invalid_inputs(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_other_countries_e164() -> None:
    assert normalize_phone("+1 (555) 123-4567") == PhoneNumber("+15551234567", "15551234567")
    assert normalize_phone("+351 912 345 678") == PhoneNumber("+351912345678", "351912345678")
    assert normalize_phone("15551234567").phone_e164 == "+15551234567"


@pytest.mark.parametrize("raw", ["+1234567", "+1234567890123456"])
def test_other_country_length_limits(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_other_country_length_boundaries() -> None:
    assert normalize_phone("+12345678").match_key == "12345678"
    assert normalize_phone("+123456789012345").match_key == "123456789012345"


def test_ddd_list_sanity() -> None:
    assert len(VALID_DDDS) == 67
    assert {"11", "21", "99"} <= VALID_DDDS
    invalid = {"10", "20", "23", "25", "26", "29", "30", "36", "39", "52", "56", "70", "72", "76"}
    assert not invalid & VALID_DDDS


def test_mask_phone() -> None:
    assert mask_phone("+5511987654321") == "***4321"
    assert mask_phone("") == "***"
    assert mask_phone(None) == "***"
