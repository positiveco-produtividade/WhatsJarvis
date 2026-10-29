import pytest

from app.panel.services.mask import mask_phone_display
from app.services.phone import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+5585912341201", "+55 85 9••••-1201"),
        ("+55 81 9 8877-3344", "+55 81 9••••-3344"),
        ("5511977777788", "+55 11 9••••-7788"),
        ("+558533221201", "+55 85 ••••-1201"),
        ("+14155551234", "+••••1234"),
    ],
)
def test_mask_formats(raw: str, expected: str) -> None:
    assert mask_phone_display(raw) == expected


def test_mask_hides_middle_digits() -> None:
    masked = mask_phone_display("+5585987654321")
    assert "8765" not in masked
    assert masked.endswith("4321")


def test_mask_works_with_canonical_phone() -> None:
    canonical = normalize_phone("+55 (85) 98765-1201").phone_e164
    assert mask_phone_display(canonical) == "+55 85 9••••-1201"


@pytest.mark.parametrize("value", [None, "", "abc", "123"])
def test_mask_empty_or_short(value: str | None) -> None:
    assert mask_phone_display(value) == "—"
