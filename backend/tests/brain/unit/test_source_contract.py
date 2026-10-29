"""Contrato da fonte de conhecimento (CB-27): a mesma suite roda com FaqSource e FakeSource.

Itens SINTETICOS. A `FaqSource` aqui le de um banco falso em memoria (sem Postgres).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.brain import matching
from app.services.brain.faq_source import FaqSource
from app.services.brain.source import KnowledgeItem, KnowledgeSource, Scored, to_searchable
from app.services.faq_reader import FaqReader

A, B, C = "plant_power", "a_tal_da_castanha", "jungle"
Q = "Qual o sabor do produto exemplo A?"
Row = dict[str, Any]


def _item(
    n: int,
    brand: str,
    title: str,
    text: str | None = "Resposta sintetica.",
    *,
    bot: bool = True,
    origin: str = "seed",
    variations: tuple[str, ...] = (),
) -> Row:
    return {
        "id": n, "brand": brand, "category": "PRODUTO", "question": title, "answer": text,
        "variations": list(variations), "bot_responde": bot, "version": 1, "origin": origin,
        "active": True,
    }


class FakeDb:
    """Imita as consultas do FaqReader/FaqSource sobre uma lista de linhas."""

    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows
        self.revision = 1

    def edit(self, n: int, **changes: Any) -> None:
        self.rows = [{**r, **changes} if r["id"] == n else r for r in self.rows]
        self.revision += 1

    async def fetchval(self, sql: str, *args: Any) -> int:
        return self.revision

    async def fetch(self, sql: str, *args: Any) -> list[Row]:
        if "origin FROM" in sql:
            return [r for r in self.rows if r["id"] in args[0]]
        brands = set(args)
        return [r for r in self.rows if r["active"] and r["brand"] in brands]


class FakeSource:
    """Segunda implementacao da porta: busca em memoria, sem FaqReader."""

    name = "fake"

    def __init__(self, rows: list[Row]) -> None:
        self.rows = rows

    def edit(self, n: int, **changes: Any) -> None:
        self.rows = [{**r, **changes} if r["id"] == n else r for r in self.rows]

    async def search(
        self,
        query: str,
        *,
        brands: Sequence[str],
        limit: int,
        floor: float,
        conn: Any | None = None,
    ) -> list[Scored]:
        items = [
            KnowledgeItem(
                ref=f"fake:{r['id']}", version=r["version"], brand=r["brand"],
                category=r["category"], title=r["question"], text=r["answer"] or "",
                origin=r["origin"], answerable=r["bot_responde"] and r["answer"] is not None,
                variations=tuple(r["variations"]),
            )
            for r in self.rows
            if r["active"]
        ]
        by_ref = {i.ref: i for i in items}
        found = matching.rank(
            query, [to_searchable(i) for i in items], brand_filter=tuple(brands),
            limit=limit, floor=floor,
        )
        return [Scored(by_ref[m.ref], m.score) for m in found]


@dataclass
class Harness:
    source: KnowledgeSource
    mutate: Callable[..., None]


def _build(kind: str, rows: list[Row]) -> Harness:
    if kind == "faq":
        db = FakeDb(rows)
        return Harness(FaqSource(FaqReader(db)), db.edit)
    fake = FakeSource(rows)
    return Harness(fake, fake.edit)


@pytest.fixture(params=["faq", "fake"])
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def _by_id(h: Harness, brands: Sequence[str], floor: float = 0.1) -> dict[str, KnowledgeItem]:
    out = await h.source.search(Q, brands=brands, limit=10, floor=floor)
    return {s.item.ref.split(":")[1]: s.item for s in out}


async def test_so_marcas_do_vendedor_e_todas(kind: str) -> None:
    h = _build(kind, [_item(1, A, Q), _item(2, B, Q), _item(3, "todas", Q), _item(4, C, Q)])
    found = await _by_id(h, [A, B])
    assert {i.brand for i in found.values()} == {A, B, "todas"}


async def test_sem_marcas_so_todas(kind: str) -> None:
    h = _build(kind, [_item(1, A, Q), _item(3, "todas", Q)])
    found = await _by_id(h, [])
    assert [i.brand for i in found.values()] == ["todas"]


async def test_sem_item_duplicado(kind: str) -> None:
    h = _build(kind, [_item(1, A, Q), _item(2, B, Q), _item(3, "todas", Q)])
    out = await h.source.search(Q, brands=[A, B, A], limit=10, floor=0.1)
    refs = [s.item.ref for s in out]
    assert len(refs) == len(set(refs)) == 3


async def test_answerable_false_para_bot_responde_false_ou_sem_texto(kind: str) -> None:
    rows = [_item(1, A, Q, bot=False), _item(2, A, Q + " b", text=None), _item(3, A, Q)]
    found = await _by_id(_build(kind, rows), [A])
    assert found["1"].answerable is False
    assert found["2"].answerable is False and found["2"].text == ""
    assert found["3"].answerable is True


async def test_edicao_e_desativacao_refletem(kind: str) -> None:
    h = _build(kind, [_item(1, A, Q, "Texto antigo."), _item(2, A, Q + " extra")])
    assert (await _by_id(h, [A]))["1"].text == "Texto antigo."
    h.mutate(1, answer="Texto novo.")
    assert (await _by_id(h, [A]))["1"].text == "Texto novo."
    h.mutate(1, active=False)
    assert "1" not in await _by_id(h, [A])


async def test_origem_e_linha_de_fonte_preservadas(kind: str) -> None:
    text = "Dado sintetico de mercado. (Fonte: Nielsen sintetico, 2026)"
    rows = [_item(1, A, Q, text, origin="nielsen"), _item(2, A, Q + " x", "t", origin="gmr")]
    found = await _by_id(_build(kind, rows), [A])
    assert found["1"].origin == "nielsen" and found["1"].text == text
    assert found["2"].origin == "gmr"


async def test_variations_entram_como_forma_extra(kind: str) -> None:
    h = _build(kind, [_item(1, A, "Pergunta totalmente diferente", variations=(Q,))])
    out = await h.source.search(Q, brands=[A], limit=10, floor=0.5)
    assert [s.score for s in out] == [1.0]


async def test_limite_piso_e_ordem(kind: str) -> None:
    rows = [
        _item(1, A, Q),
        _item(2, A, "Qual o sabor do produto exemplo"),
        _item(3, A, "Outro assunto qualquer"),
    ]
    h = _build(kind, rows)
    top = await h.source.search(Q, brands=[A], limit=1, floor=0.1)
    assert len(top) == 1 and top[0].item.ref.endswith(":1")
    scores = [s.score for s in await h.source.search(Q, brands=[A], limit=10, floor=0.1)]
    assert scores == sorted(scores, reverse=True)
    assert await h.source.search("zzz", brands=[A], limit=10, floor=0.1) == []


def test_to_searchable_mapeia_o_item() -> None:
    item = KnowledgeItem("faq:9", 3, A, "PRODUTO", "Titulo", "Texto", "seed", True, ("v1",))
    s = to_searchable(item)
    assert (s.ref, s.brand, s.title, s.variations) == ("faq:9", A, "Titulo", ("v1",))
