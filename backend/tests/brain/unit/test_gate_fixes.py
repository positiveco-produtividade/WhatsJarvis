"""Correcoes do gate com IA real (CB-R04): UF do follow-up, agradecimento, empate generico,
refs alternativas e contagem de chamadas. SINTETICO, sem banco e sem rede."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.config import Settings
from app.core.values import BrainOutcome
from app.services.answering import AnswerContext
from app.services.brain import eval as ev
from app.services.brain import intents
from app.services.brain.llm import LlmError, LlmRequest, LlmResult
from app.services.brain.matching import (
    Match,
    Searchable,
    is_generic_brand_tie,
    strip_ufs,
    tokens,
    ufs_in,
)
from app.services.brain.provider import BrainProvider
from app.services.brain.source import KnowledgeItem, Scored
from app.services.flags import Flags

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def _item(ref: str, brand: str, title: str) -> KnowledgeItem:
    return KnowledgeItem(
        ref=ref, version=1, brand=brand, category="PRODUTO", title=title,
        text=f"SINTETICO: resposta {ref}", origin="manual", answerable=True,
    )  # fmt: skip


class SpySource(ev.MemorySource):
    def __init__(self, items: Sequence[KnowledgeItem]) -> None:
        super().__init__(items)
        self.queries: list[str] = []

    async def search(self, query: str, **kw: Any) -> list[Scored]:
        self.queries.append(query)
        return await super().search(query, **kw)


class TimeoutLlm:
    async def complete(self, request: LlmRequest) -> LlmResult:
        raise LlmError("timeout")


def _ask(
    source: ev.MemorySource, text: str, brands: list[str], *, llm: Any = None,
    history: tuple[tuple[str, str], ...] = (),
) -> Any:
    db = ev._HistoryDb(history) if llm is not None else None
    provider = BrainProvider(
        source, cast(Flags, ev._AllBrandsOn()), Settings(), None, llm=llm, db=db
    )
    ctx = AnswerContext(
        user_id=0, profile="vendedor", ufs=["SP"], channels=[], brands=brands, text=text,
        inbound_wa_message_id="t", received_at=NOW, trace_id=1 if llm is not None else None,
    )  # fmt: skip
    return asyncio.run(provider.answer(ctx))


# ---------------------------------------------------------------- 1. UF do follow-up


def test_strip_ufs_mirrors_ufs_in() -> None:
    stripped = strip_ufs("Quem lidera isotônicos em CE?").split()
    assert stripped == ["quem", "lidera", "isotonicos", "em"]
    assert ufs_in(strip_ufs("Quem lidera isotônicos em CE? E no RJ")) == frozenset()
    assert tokens(strip_ufs("Como tomar o Ultracoffee?")) == tokens("Como tomar o Ultracoffee?")


def test_followup_query_does_not_inherit_uf_from_history() -> None:
    source = SpySource([
        _item("nielsen_sint:001", "jungle", "Quem lidera isotônicos em CE?"),
        _item("nielsen_sint:002", "jungle", "Quem lidera isotônicos no Brasil?"),
    ])
    history = (("user", "Quem lidera isotônicos em CE?"), ("assistant", "Exemplo Alfa."))
    answer = _ask(
        source, "e quem lidera no Brasil?", ["jungle"], llm=TimeoutLlm(), history=history
    )
    assert "nielsen_sint:002" in answer.effects.item_refs[0]
    assert ufs_in(source.queries[0]) == frozenset()
    assert answer.effects.outcome == BrainOutcome.ANSWERED


def test_followup_keeps_uf_of_the_current_message() -> None:
    source = SpySource([_item("nielsen_sint:001", "jungle", "Quem lidera isotônicos em CE?")])
    history = (("user", "Quem lidera isotônicos no Brasil?"), ("assistant", "Exemplo."))
    answer = _ask(source, "e em CE?", ["jungle"], llm=TimeoutLlm(), history=history)
    assert ufs_in(source.queries[0]) == frozenset({"ce"})
    assert answer.effects.outcome == BrainOutcome.ANSWERED


# ---------------------------------------------------------------- 2. agradecimento


@pytest.mark.parametrize("text", ["obrigado!", "Valeu, vlw", "muito obrigada", "brigado"])
def test_thanks_gets_its_own_text(text: str) -> None:
    answer = _ask(ev.MemorySource([]), text, ["jungle"])
    assert answer.effects.outcome == BrainOutcome.GREETING
    assert answer.text == Settings().text_brain_thanks != Settings().text_brain_greeting


@pytest.mark.parametrize("text", ["oi", "Bom dia, tudo bem?", "ok", "beleza", "oi, obrigado"])
def test_greeting_keeps_greeting_text(text: str) -> None:
    answer = _ask(ev.MemorySource([]), text, ["jungle"])
    assert answer.effects.outcome == BrainOutcome.GREETING
    assert answer.text == Settings().text_brain_greeting


def test_is_thanks() -> None:
    assert intents.is_thanks("Obrigadooo!!") and intents.is_thanks("valeu")
    assert not intents.is_thanks("oi") and not intents.is_thanks("obrigado! e o sabor limão?")


# ---------------------------------------------------------------- 3. empate generico

POSSIBLE = Searchable("g:105", "possible", "Quem tem diabetes pode tomar Possible?")
FIBER = Searchable("g:050", "plant_power", "Quem tem intestino sensível pode tomar Fibergut?")
ITEMS = {POSSIBLE.ref: POSSIBLE, FIBER.ref: FIBER}


def _tie(text: str, first: Searchable, second: Searchable, s1: float, s2: float) -> bool:
    matches = [Match(first.ref, first.brand, s1), Match(second.ref, second.brand, s2)]
    return is_generic_brand_tie(text, matches, ITEMS, threshold=0.55)


def test_generic_tie_ignores_brand_words_in_titles() -> None:
    assert _tie("Diabético pode tomar?", POSSIBLE, FIBER, 0.571, 0.5)


def test_generic_tie_needs_equal_coverage_and_scores() -> None:
    assert not _tie("Possible diabetes pode tomar?", POSSIBLE, FIBER, 0.8, 0.5)  # 105 cobre mais
    assert not _tie("Diabético pode tomar?", POSSIBLE, FIBER, 0.5, 0.5)  # 1o abaixo do limiar
    assert not _tie("Diabético pode tomar?", POSSIBLE, FIBER, 0.6, 0.3)  # 2o fraco demais
    same = Searchable("g:106", "possible", "Quem tem diabetes pode tomar?")
    assert not _tie("Diabético pode tomar?", POSSIBLE, same, 0.6, 0.55)  # mesma marca
    todas = Searchable("g:107", "todas", "Quem pode tomar?")
    items = {**ITEMS, todas.ref: todas}
    matches = [Match("g:105", "possible", 0.6), Match("g:107", "todas", 0.55)]
    assert not is_generic_brand_tie("pode tomar?", matches, items, threshold=0.55)


def test_provider_asks_brand_on_generic_question_with_two_brands() -> None:
    source = ev.MemorySource([
        _item("g:105", "possible", "Quem tem diabetes pode tomar Possible?"),
        _item("g:050", "plant_power", "Quem tem intestino sensível pode tomar Fibergut?"),
    ])
    asked = _ask(source, "Diabético pode tomar?", ["plant_power", "possible"])
    assert asked.effects.outcome == BrainOutcome.ASK_BRAND
    single = _ask(source, "Diabético pode tomar?", ["possible"])
    assert single.effects.outcome == BrainOutcome.ANSWERED


# ---------------------------------------------------------------- 4. refs alternativas


def _case(**over: Any) -> ev.EvalCase:
    base: dict[str, Any] = {
        "id": "c", "category": "followup", "question": "q", "brands": ("jungle",), "ufs": (),
        "expected": "responde", "refs": ("guia:013|guia:018",),
    }
    base.update(over)
    return ev.EvalCase(**base)


def test_alternative_refs_accept_either() -> None:
    case = _case()
    assert ev.score_case(case, BrainOutcome.ANSWERED, ["guia:013@v1"], "x") == []
    assert ev.score_case(case, BrainOutcome.ANSWERED, ["guia:018@v1"], "x") == []
    assert ev.score_case(case, BrainOutcome.ANSWERED, ["guia:099@v1"], "x")


def test_saudacao_case_checks_must_contain() -> None:
    case = _case(expected="saudacao", refs=(), must_contain=("Por nada",))
    assert ev.score_case(case, BrainOutcome.GREETING, [], "Por nada! Se precisar") == []
    assert ev.score_case(case, BrainOutcome.GREETING, [], "Oi! Sou o Jarvis")
    assert ev.score_case(case, BrainOutcome.ANSWERED, [], "Por nada")


# ---------------------------------------------------------------- 5. contagem do LLM


def test_counting_llm_and_report(tmp_path: Any) -> None:
    case = ev.EvalCase("f", "followup", "e o sabor?", ("jungle",), (), "nao_sei", ())
    guia = [_item("guia:001", "jungle", "Qual o sabor do Jungle de teste?")]
    summary = asyncio.run(
        ev.run_eval(
            [case, case], guia, mode=ev.MODE_AI, settings=ev._settings_words(), llm=TimeoutLlm()
        )
    )
    assert summary.llm.calls == 2 and summary.llm.timeouts == 2 and summary.llm.errors == 0
    assert all(isinstance(r.refs, list) for r in summary.results)
    out = tmp_path / "r.json"
    ev.write_report(summary, out, ev.MODE_AI)
    body = out.read_text(encoding="utf-8")
    assert '"llm"' in body and '"refs"' in body
