"""Leitor da FAQ para o bot (PN-25): cache em memoria por revisao.

Contrato: `FaqReader.snapshot(brand)` devolve os itens ativos da marca e da marca 'todas'
(inclusive os com `bot_responde=False`; a decisao de escalar e do modulo de resposta).
Cada chamada faz UMA leitura barata de `ops.system_state['faq_revision']` (mantida pelo
gatilho da migration 101) e so recarrega a tabela quando a revisao mudou ou quando o cache
passou do teto de idade (cinto e suspensorio). Nao importa `app.panel`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.clock import Clock, SystemClock

FAQ_CACHE_MAX_AGE_SECONDS = 60.0
FAQ_REVISION_KEY = "faq_revision"
ALL_BRANDS = "todas"

_REVISION_SQL = "SELECT (value #>> '{}')::bigint FROM ops.system_state WHERE key = $1"
_ITEMS_SQL = (
    "SELECT id, brand, category, question, answer, variations, bot_responde, version "
    "FROM content.faq_items WHERE active AND brand IN ($1, $2) ORDER BY id"
)


@dataclass(frozen=True, slots=True)
class FaqItem:
    id: int
    brand: str
    category: str
    question: str
    answer: str | None
    variations: tuple[str, ...]
    bot_responde: bool
    version: int


@dataclass(frozen=True, slots=True)
class _Entry:
    revision: int
    loaded_at: float
    items: tuple[FaqItem, ...]


class FaqReader:
    """`db`: pool ou conexao asyncpg (fetchval/fetch). Quem ja segura uma conexao passa `conn=`."""

    def __init__(
        self,
        db: Any,
        clock: Clock | None = None,
        max_age_seconds: float = FAQ_CACHE_MAX_AGE_SECONDS,
    ) -> None:
        self._db = db
        self._clock: Clock = clock or SystemClock()
        self._max_age = max_age_seconds
        self._cache: dict[str, _Entry] = {}

    def _stamp(self) -> float:
        return self._clock.now().timestamp()

    async def revision(self, conn: Any | None = None) -> int:
        reader = conn if conn is not None else self._db
        value = await reader.fetchval(_REVISION_SQL, FAQ_REVISION_KEY)
        return int(value) if value is not None else 0

    async def snapshot(self, brand: str, conn: Any | None = None) -> list[FaqItem]:
        reader = conn if conn is not None else self._db
        revision = await self.revision(reader)
        now = self._stamp()
        entry = self._cache.get(brand)
        if entry is None or entry.revision != revision or now - entry.loaded_at >= self._max_age:
            entry = await self._load(reader, brand, revision, now)
            self._cache[brand] = entry
        return list(entry.items)

    async def _load(self, reader: Any, brand: str, revision: int, now: float) -> _Entry:
        rows = await reader.fetch(_ITEMS_SQL, brand, ALL_BRANDS)
        items = tuple(
            FaqItem(
                id=int(r["id"]),
                brand=r["brand"],
                category=r["category"],
                question=r["question"],
                answer=r["answer"],
                variations=tuple(r["variations"]),
                bot_responde=bool(r["bot_responde"]),
                version=int(r["version"]),
            )
            for r in rows
        )
        return _Entry(revision=revision, loaded_at=now, items=items)

    def invalidate(self) -> None:
        self._cache.clear()
