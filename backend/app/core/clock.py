"""Relogio injetavel: producao usa SystemClock, testes usam FakeClock."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Fonte de tempo. Sempre devolve datetime com fuso (UTC)."""

    def now(self) -> datetime: ...


class SystemClock:
    """Relogio real."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Relogio controlado para testes; so avanca quando mandado."""

    def __init__(self, start: datetime | None = None) -> None:
        initial = start if start is not None else datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        if initial.tzinfo is None:
            raise ValueError("FakeClock exige datetime com fuso")
        self._now = initial

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("FakeClock exige datetime com fuso")
        self._now = value

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> datetime:
        self._now = self._now + timedelta(seconds=seconds, minutes=minutes, hours=hours)
        return self._now
