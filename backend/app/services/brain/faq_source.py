"""Adaptador `FaqSource`: a FAQ (`FaqReader`) como fonte de conhecimento (CB-17, CB-27, CB-28).

Cada marca permitida (mais "todas") vem de `FaqReader.snapshot` (cache por revisao, <= 60 s);
o resultado e unido sem duplicar por `id` e pontuado por `matching.rank`. Nao importa `app.panel`.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.brain import matching
from app.services.brain.source import KnowledgeItem, Scored, to_searchable
from app.services.faq_reader import FaqItem, FaqReader

_ORIGIN_SQL = "SELECT id, origin FROM content.faq_items WHERE id = ANY($1::bigint[])"
_DEFAULT_ORIGIN = "manual"


class FaqSource:
    name = "faq"

    def __init__(self, reader: FaqReader) -> None:
        self._reader = reader
        self._db = reader._db
        # (id -> (version, origin)): `FaqItem` nao traz a origem; a versao invalida a entrada.
        self._origins: dict[int, tuple[int, str]] = {}

    async def search(
        self,
        query: str,
        *,
        brands: Sequence[str],
        limit: int,
        floor: float,
        conn: Any | None = None,
    ) -> list[Scored]:
        faq_items = await self._collect(brands, conn)
        items = await self._to_items(faq_items, conn)
        by_ref = {i.ref: i for i in items}
        matches = matching.rank(
            query,
            [to_searchable(i) for i in items],
            brand_filter=tuple(brands),
            limit=limit,
            floor=floor,
        )
        return [Scored(item=by_ref[m.ref], score=m.score) for m in matches]

    async def _collect(self, brands: Sequence[str], conn: Any | None) -> list[FaqItem]:
        seen: dict[int, FaqItem] = {}
        for brand in dict.fromkeys(brands or (matching.ALL_BRANDS,)):
            for item in await self._reader.snapshot(brand, conn=conn):
                seen.setdefault(item.id, item)
        return [seen[k] for k in sorted(seen)]

    async def _origin_map(self, faq_items: list[FaqItem], conn: Any | None) -> None:
        stale = [
            i.id
            for i in faq_items
            if i.id not in self._origins or self._origins[i.id][0] != i.version
        ]
        if not stale:
            return
        reader = conn if conn is not None else self._db
        versions = {i.id: i.version for i in faq_items}
        for row in await reader.fetch(_ORIGIN_SQL, stale):
            self._origins[int(row["id"])] = (versions[int(row["id"])], str(row["origin"]))

    async def _to_items(self, faq_items: list[FaqItem], conn: Any | None) -> list[KnowledgeItem]:
        await self._origin_map(faq_items, conn)
        return [
            KnowledgeItem(
                ref=f"faq:{i.id}",
                version=i.version,
                brand=i.brand,
                category=i.category,
                title=i.question,
                text=i.answer or "",
                origin=self._origins.get(i.id, (i.version, _DEFAULT_ORIGIN))[1],
                answerable=i.bot_responde and i.answer is not None,
                variations=i.variations,
            )
            for i in faq_items
        ]
