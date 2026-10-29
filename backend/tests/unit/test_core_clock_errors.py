from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import Clock, FakeClock, SystemClock
from app.core.errors import (
    DuplicatePhone,
    InvalidFlagKey,
    InvalidSignature,
    JarvisError,
    PayloadTooLarge,
    TransientDbError,
    ValidationFailure,
)


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


def test_fake_clock_is_controlled() -> None:
    start = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    clock: Clock = FakeClock(start)
    assert clock.now() == start
    assert clock.now() == start  # nao avanca sozinho
    assert isinstance(clock, FakeClock)
    clock.advance(minutes=30, seconds=15)
    assert clock.now() == start + timedelta(minutes=30, seconds=15)
    clock.set(start)
    assert clock.now() == start


def test_fake_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        FakeClock().set(datetime(2026, 1, 1))


def test_errors_share_base_class() -> None:
    for exc in (DuplicatePhone(), InvalidSignature(), TransientDbError()):
        assert isinstance(exc, JarvisError)


def test_validation_failure_carries_field_and_reason() -> None:
    err = ValidationFailure("telefone", "fixo nao recebe WhatsApp")
    assert err.field == "telefone"
    assert err.reason == "fixo nao recebe WhatsApp"
    assert str(err) == "telefone: fixo nao recebe WhatsApp"


def test_invalid_flag_key_is_validation_failure() -> None:
    err = InvalidFlagKey("foo")
    assert isinstance(err, ValidationFailure)
    assert err.field == "flag"
    assert "foo" in str(err)


def test_payload_too_large_keeps_limit() -> None:
    err = PayloadTooLarge(65536)
    assert err.limit_bytes == 65536
    assert "65536" in str(err)
