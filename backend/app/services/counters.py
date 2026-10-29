"""Contadores de seguranca: acumulam em memoria por (dia, kind) e gravam em lote.

Sem PII: so o tipo do evento e a contagem. O laco que chama `flush` a cada 30 s
e do PF-34 (spec 4.6). O dia e a data UTC do relogio injetado.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable
from datetime import date
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue

KINDS: tuple[str, ...] = (
    "invalid_signature",
    "oversize_payload",
    "bad_verify_token",
    "invalid_json",
    "wrong_recipient",
)

_UPSERT = (
    "INSERT INTO ops.security_counters (day, kind, count) "
    "SELECT * FROM unnest($1::date[], $2::text[], $3::bigint[]) "
    "ON CONFLICT (day, kind) DO UPDATE "
    "SET count = ops.security_counters.count + EXCLUDED.count"
)


class SecurityCounters:
    """Incremento em memoria (thread-safe) e flush em lote para ops.security_counters."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._pending: Counter[tuple[date, str]] = Counter()
        self._lock = threading.Lock()

    def incr(self, kind: str, amount: int = 1) -> None:
        if kind not in KINDS:
            raise InvalidValue("kind", f"valor nao aceito: {kind}")
        if amount < 1:
            raise InvalidValue("amount", "deve ser maior que zero")
        day = self._clock.now().date()
        with self._lock:
            self._pending[(day, kind)] += amount

    def pending(self) -> dict[tuple[date, str], int]:
        """Copia do que ainda nao foi gravado (para testes e diagnostico)."""
        with self._lock:
            return dict(self._pending)

    def _restore(self, items: Iterable[tuple[tuple[date, str], int]]) -> None:
        with self._lock:
            for key, value in items:
                self._pending[key] += value

    async def flush(self, conn: Any) -> int:
        """Grava os acumulados somando ao existente; devolve quantas linhas gravou.

        Se a gravacao falhar, os valores voltam para a memoria (nada se perde) e
        o erro propaga.
        """
        with self._lock:
            snapshot = list(self._pending.items())
            self._pending.clear()
        if not snapshot:
            return 0
        days = [key[0] for key, _ in snapshot]
        kinds = [key[1] for key, _ in snapshot]
        counts = [value for _, value in snapshot]
        try:
            await conn.execute(_UPSERT, days, kinds, counts)
        except BaseException:
            self._restore(snapshot)
            raise
        return len(snapshot)
