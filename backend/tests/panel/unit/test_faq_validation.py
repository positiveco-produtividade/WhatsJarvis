import pytest

from app.panel.services import faq_validation as fv
from app.panel.values import CLASSIFICATIONS


def _validate(**over: object) -> fv.FaqValidation:
    base: dict[str, object] = {
        "brand": "jungle",
        "category": "PRODUTO",
        "question": "Qual o sabor?",
        "answer": "Morango.",
        "variations": [],
        "bot_responde": True,
        "active_keys": [],
    }
    base.update(over)
    return fv.validate_faq(**base)  # type: ignore[arg-type]


def test_valid_item_has_no_problems_and_is_cleaned() -> None:
    r = _validate(question="  Qual o sabor?  ", variations=["", "  ", " outra "])
    assert r.ok and r.problems == ()
    assert r.question == "Qual o sabor?"
    assert r.variations == ("outra",)
    assert r.question_key == "qual o sabor?"


@pytest.mark.parametrize("q", ["", "   ", None])
def test_empty_question(q: str | None) -> None:
    assert _validate(question=q).codes == (fv.QUESTION_EMPTY,)


def test_question_length_limits() -> None:
    assert _validate(question="a" * 500).ok
    assert _validate(question="a" * 501).codes == (fv.QUESTION_TOO_LONG,)


def test_answer_required_only_when_bot_responds() -> None:
    assert _validate(answer=None).codes == (fv.ANSWER_REQUIRED,)
    assert _validate(answer="  ").codes == (fv.ANSWER_REQUIRED,)
    r = _validate(answer=None, bot_responde=False)
    assert r.ok and r.answer is None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Ligue (11) 98765-4321", fv.CONTACT_PHONE),
        ("Fale no +55 11 987654321", fv.CONTACT_PHONE),
        ("Escreva para joao@empresa.com.br", fv.CONTACT_EMAIL),
        ("Veja https://exemplo.com/x", fv.CONTACT_LINK),
        ("Veja www.exemplo.com", fv.CONTACT_LINK),
        ("Veja exemplo.com.br", fv.CONTACT_LINK),
    ],
)
def test_contact_in_answer_and_variation(text: str, code: str) -> None:
    in_answer = _validate(answer=text)
    assert code in in_answer.codes
    assert [p.field for p in in_answer.problems if p.code == code] == ["answer"]
    in_var = _validate(variations=[text])
    assert code in in_var.codes
    assert [p.field for p in in_var.problems if p.code == code] == ["variations"]


@pytest.mark.parametrize("text", ["Rende 12,5 kg", "Pacote de 500 g, nota 4.5", "Lote 2026"])
def test_plain_numbers_are_not_contacts(text: str) -> None:
    assert fv.find_contact(text) == ()
    assert _validate(answer=text).ok


def test_duplicate_ignores_accent_case_and_spaces() -> None:
    key = fv.question_key("Qual o  SABOR?")
    assert _validate(question="Qual o sabôr?", active_keys=[key]).codes == (fv.DUPLICATE_QUESTION,)
    assert _validate(question="Outra coisa", active_keys=[key]).ok


def test_question_key_normalizes() -> None:
    assert fv.question_key("  Preço   do  Pão? ") == "preco do pao?"


def test_all_problems_reported_at_once() -> None:
    r = _validate(
        brand="x",
        category="y",
        question="",
        answer=None,
        variations=["a@b.com"],
    )
    assert set(r.codes) == {
        fv.BAD_BRAND,
        fv.BAD_CATEGORY,
        fv.QUESTION_EMPTY,
        fv.ANSWER_REQUIRED,
        fv.CONTACT_EMAIL,
    }
    assert all(p.message for p in r.problems)


def test_categories_are_ticket_classifications() -> None:
    assert fv.FAQ_CATEGORIES == ("PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA")
    for c in fv.FAQ_CATEGORIES:
        assert c in CLASSIFICATIONS
        assert _validate(category=c).ok
    for c in ("MIDIA", "NAO_IDENTIFICADO", "produto"):
        assert fv.BAD_CATEGORY in _validate(category=c).codes


def test_brand_todas_and_real_brands_accepted() -> None:
    assert _validate(brand="todas").ok
    assert _validate(brand="zaya").ok
    assert _validate(brand="").codes == (fv.BAD_BRAND,)


def test_clean_variations_handles_none() -> None:
    assert fv.clean_variations(None) == ()
    assert fv.clean_variations([None, " a "]) == ("a",)


def test_validate_question_single_rule() -> None:
    assert fv.validate_question("ok") == ()
    assert fv.validate_question(None) == (fv.QUESTION_EMPTY,)
    assert fv.validate_question("x" * 501) == (fv.QUESTION_TOO_LONG,)
