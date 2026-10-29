"""Verificador de fidelidade. Itens SINTETICOS: nenhum dado Nielsen/GMR real."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.brain.fidelity import (
    Candidate,
    FidelityStatus,
    check,
    check_verbatim,
    parse_number,
)

PRICE = Candidate(
    "faq:1",
    "A caixa de Jungle Granola custa R$ 1.299,90 na tabela de maio. Cresceu 12,5% em 2024.",
)
GENERIC = Candidate(
    "faq:2",
    "A Plant Power lidera o segmento de cafe funcional. Site: https://exemplo.test/plant "
    "contato: vendas@exemplo.test ou (11) 4000-1234.",
)
CANDS = [PRICE, GENERIC]


def run(answer: str, refs: list[str] | None = None, **kw: object):
    return check(answer, refs if refs is not None else ["faq:1"], CANDS, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.299,90", Decimal("1299.90")),
        ("1,299.90", Decimal("1299.90")),
        ("12,5", Decimal("12.5")),
        ("1.500", Decimal("1500")),
        ("2024", Decimal("2024")),
    ],
)
def test_parse_number(raw: str, expected: Decimal) -> None:
    assert parse_number(raw) == expected


def test_faithful_answer_is_ok() -> None:
    res = run("A caixa de Jungle Granola custa R$ 1299,90.", price_intent=True)
    assert res.status is FidelityStatus.OK
    assert res.refs == ("faq:1",)
    assert res.violations == ()


def test_thousands_and_decimal_normalization_accepts_equivalent_forms() -> None:
    res = run("Cresceu 12.5% e custa R$ 1.299,90.", price_intent=True)
    assert res.status is FidelityStatus.OK


def test_refs_outside_candidates_without_valid_ref_fails() -> None:
    res = run("Qualquer coisa.", ["faq:999"])
    assert res.status is FidelityStatus.FAILED
    assert res.text is None
    assert "no_valid_refs" in res.violations


def test_empty_refs_fails() -> None:
    assert run("Texto.", []).status is FidelityStatus.FAILED


def test_ref_outside_with_valid_one_is_repaired() -> None:
    res = run("A caixa de Jungle Granola custa R$ 1299,90.", ["faq:1", "faq:999"])
    assert res.status is FidelityStatus.REPAIRED
    assert res.text == PRICE.text
    assert "refs_outside_candidates" in res.violations


def test_invented_number_is_repaired_with_best_item_text() -> None:
    res = run("A caixa de Jungle Granola cresceu 18% em 2024.")
    assert res.status is FidelityStatus.REPAIRED
    assert res.text == PRICE.text
    assert res.refs == ("faq:1",)
    assert "number_not_in_items" in res.violations


def test_invented_price_is_repaired_and_never_keeps_the_value() -> None:
    res = run("A caixa de Jungle Granola custa R$ 999,00.", price_intent=True)
    assert res.status is FidelityStatus.REPAIRED
    assert res.text is not None
    assert "999" not in res.text
    assert "price_not_in_items" in res.violations


def test_invented_number_and_price_with_simulated_ai() -> None:
    res = run("Custa R$ 899,00 e cresceu 40% no ano.", price_intent=True)
    assert res.status is FidelityStatus.REPAIRED
    assert res.text == PRICE.text


def test_invented_date_is_flagged() -> None:
    res = run("Valido ate 31/12/2030.")
    assert res.status is FidelityStatus.REPAIRED
    assert "number_not_in_items" in res.violations


def test_price_intent_requires_money_in_answer() -> None:
    res = run("A Jungle Granola esta na tabela de maio.", price_intent=True)
    assert res.status is FidelityStatus.REPAIRED
    assert "price_missing" in res.violations
    assert res.text == PRICE.text


def test_price_intent_without_money_in_items_fails() -> None:
    res = run("A Plant Power lidera o segmento.", ["faq:2"], price_intent=True)
    assert res.status is FidelityStatus.FAILED
    assert res.text is None
    assert "price_missing" in res.violations


def test_no_price_intent_does_not_require_money() -> None:
    res = run("A Plant Power lidera o segmento.", ["faq:2"])
    assert res.status is FidelityStatus.OK


def test_invented_brand_name_is_flagged() -> None:
    res = check(
        "A Plant Power lidera, junto da Possible.",
        ["faq:2"],
        CANDS,
        question="Como esta a Plant Power?",
    )
    assert res.status is FidelityStatus.REPAIRED
    assert "name_not_in_items" in res.violations


def test_invented_capitalized_product_name_is_flagged() -> None:
    res = run("A Plant Power lidera, com o Superblend em alta.", ["faq:2"])
    assert res.status is FidelityStatus.REPAIRED
    assert "name_not_in_items" in res.violations


def test_name_from_question_is_allowed() -> None:
    res = check(
        "A Plant Power lidera o segmento, sim, Rafael.",
        ["faq:2"],
        CANDS,
        question="Rafael pergunta: a Plant Power lidera?",
    )
    assert res.status is FidelityStatus.OK


def test_length_over_1_3x_is_flagged() -> None:
    padded = "A Plant Power lidera o segmento de cafe funcional " + "e segue crescendo " * 20
    res = run(padded, ["faq:2"])
    assert res.status is FidelityStatus.REPAIRED
    assert "too_long" in res.violations


def test_length_within_limit_is_ok() -> None:
    res = run("A Plant Power lidera o segmento de cafe funcional.", ["faq:2"])
    assert res.status is FidelityStatus.OK


@pytest.mark.parametrize(
    "answer",
    [
        "Veja em https://outro.test/promo agora.",
        "Escreva para fraude@outro.test hoje.",
        "Ligue para (21) 99999-0000 agora.",
    ],
)
def test_contacts_not_in_items_are_flagged(answer: str) -> None:
    res = run(answer, ["faq:2"])
    assert res.status is FidelityStatus.REPAIRED
    assert res.text == GENERIC.text


def test_contacts_present_in_items_are_allowed() -> None:
    res = run("Fale com vendas@exemplo.test ou (11) 4000-1234.", ["faq:2"])
    assert res.status is FidelityStatus.OK


def test_url_present_in_items_is_allowed_with_trailing_dot() -> None:
    res = run("Veja https://exemplo.test/plant.", ["faq:2"])
    assert res.status is FidelityStatus.OK


def test_empty_answer_is_repaired_or_failed() -> None:
    res = run("   ")
    assert res.status is FidelityStatus.REPAIRED
    assert "empty_answer" in res.violations


def test_only_cited_items_count_for_numbers() -> None:
    res = run("A Plant Power lidera, cresceu 12,5%.", ["faq:2"])
    assert res.status is FidelityStatus.REPAIRED
    assert res.text == GENERIC.text


def test_verbatim_mode_returns_item_text() -> None:
    res = check_verbatim(["faq:999", "faq:1"], CANDS, price_intent=True)
    assert res.status is FidelityStatus.OK
    assert res.text == PRICE.text


def test_verbatim_mode_price_without_money_fails() -> None:
    res = check_verbatim(["faq:2"], CANDS, price_intent=True)
    assert res.status is FidelityStatus.FAILED


def test_verbatim_mode_without_valid_refs_fails() -> None:
    assert check_verbatim(["x"], CANDS).status is FidelityStatus.FAILED
