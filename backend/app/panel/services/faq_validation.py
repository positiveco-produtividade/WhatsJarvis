"""Validacao pura dos itens da FAQ (PN-23, PN-28). Sem banco: quem chama passa as chaves ja ativas.

Regras (uma por problema, todas reportadas de uma vez):
pergunta de 1 a 500 caracteres; resposta obrigatoria quando o bot responde; sem
telefone, e-mail ou link na resposta e nas variacoes; pergunta nao repetida na marca.
As categorias sao as classificacoes dos tickets (decisao do dono).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.panel.services.formatting import fold_text
from app.panel.values import BRANDS, CLASSIFICATIONS

__all__ = [
    "ALL_BRANDS_KEY",
    "ANSWER_REQUIRED",
    "BAD_BRAND",
    "BAD_CATEGORY",
    "CONTACT_EMAIL",
    "CONTACT_LINK",
    "CONTACT_PHONE",
    "DUPLICATE_QUESTION",
    "FAQ_CATEGORIES",
    "QUESTION_EMPTY",
    "QUESTION_MAX_LENGTH",
    "QUESTION_TOO_LONG",
    "FaqProblem",
    "FaqValidation",
    "clean_variations",
    "find_contact",
    "question_key",
    "validate_faq",
    "validate_question",
]

ALL_BRANDS_KEY = "todas"
# Mesmas chaves das classificações de ticket; a FAQ usa só as cinco primeiras
# (sem MIDIA e NAO_IDENTIFICADO), spec 2 e 4.3.2.
FAQ_CATEGORIES: tuple[str, ...] = tuple(
    c for c in CLASSIFICATIONS if c not in ("MIDIA", "NAO_IDENTIFICADO")
)
QUESTION_MAX_LENGTH = 500

QUESTION_EMPTY = "question_empty"
QUESTION_TOO_LONG = "question_too_long"
ANSWER_REQUIRED = "answer_required"
CONTACT_PHONE = "contact_phone"
CONTACT_EMAIL = "contact_email"
CONTACT_LINK = "contact_link"
DUPLICATE_QUESTION = "duplicate_question"
BAD_BRAND = "bad_brand"
BAD_CATEGORY = "bad_category"

_MESSAGES: dict[str, str] = {
    QUESTION_EMPTY: "A pergunta é obrigatória.",
    QUESTION_TOO_LONG: f"A pergunta passa de {QUESTION_MAX_LENGTH} caracteres.",
    ANSWER_REQUIRED: "Escreva a resposta ou desmarque “O Jarvis responde sozinho”.",
    CONTACT_PHONE: "Não coloque telefone no texto.",
    CONTACT_EMAIL: "Não coloque e-mail no texto.",
    CONTACT_LINK: "Não coloque link no texto.",
    DUPLICATE_QUESTION: "Já existe uma pergunta ativa igual nesta marca.",
    BAD_BRAND: "Marca inválida.",
    BAD_CATEGORY: "Categoria inválida.",
}

_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LINK = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|app|me|co|ly|link|gov|edu|br)\b"
    r"|\bwa\.me\b",
    re.IGNORECASE,
)
# Sequencia de digitos com separadores comuns de telefone; conta os digitos depois.
_PHONE_RUN = re.compile(r"\+?\d(?:[\d\s().-]*\d)?")
_PHONE_MIN_DIGITS = 8


@dataclass(frozen=True)
class FaqProblem:
    code: str
    message: str
    field: str


@dataclass(frozen=True)
class FaqValidation:
    problems: tuple[FaqProblem, ...] = ()
    question: str = ""
    question_key: str = ""
    answer: str | None = None
    variations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(p.code for p in self.problems)


def question_key(question: str) -> str:
    """Chave de duplicidade: sem acento, minuscula e espacos colapsados."""
    return " ".join(fold_text(question).split())


def clean_variations(variations: Iterable[str | None] | None) -> tuple[str, ...]:
    """Descarta variacoes vazias e apara espacos (mantem a ordem)."""
    return tuple(v.strip() for v in (variations or ()) if v and v.strip())


def find_contact(text: str | None) -> tuple[str, ...]:
    """Codigos de contato achados no texto (telefone, e-mail, link), sem repetir."""
    if not text:
        return ()
    found: list[str] = []
    if any(
        sum(c.isdigit() for c in m.group()) >= _PHONE_MIN_DIGITS for m in _PHONE_RUN.finditer(text)
    ):
        found.append(CONTACT_PHONE)
    if _EMAIL.search(text):
        found.append(CONTACT_EMAIL)
    # O dominio de um e-mail nao conta como link.
    if _LINK.search(_EMAIL.sub(" ", text)):
        found.append(CONTACT_LINK)
    return tuple(found)


def validate_question(question: str | None) -> tuple[str, ...]:
    """Codigo do problema da pergunta (vazia, longa demais) ou nada."""
    text = (question or "").strip()
    if not text:
        return (QUESTION_EMPTY,)
    if len(text) > QUESTION_MAX_LENGTH:
        return (QUESTION_TOO_LONG,)
    return ()


def _problem(code: str, field_name: str) -> FaqProblem:
    return FaqProblem(code=code, message=_MESSAGES[code], field=field_name)


def _contact_problems(answer: str | None, variations: tuple[str, ...]) -> list[FaqProblem]:
    out = [_problem(code, "answer") for code in find_contact(answer)]
    seen: set[str] = set()
    for variation in variations:
        for code in find_contact(variation):
            if code not in seen:
                seen.add(code)
                out.append(_problem(code, "variations"))
    return out


def validate_faq(
    *,
    brand: str,
    category: str,
    question: str | None,
    answer: str | None,
    variations: Iterable[str | None] | None,
    bot_responde: bool,
    active_keys: Iterable[str] = (),
) -> FaqValidation:
    """Valida o item inteiro e devolve TODOS os problemas.

    ``active_keys``: ``question_key`` dos itens ativos da mesma marca, ja sem o
    proprio item quando for edicao.
    """
    problems: list[FaqProblem] = []
    if brand != ALL_BRANDS_KEY and brand not in BRANDS:
        problems.append(_problem(BAD_BRAND, "brand"))
    if category not in FAQ_CATEGORIES:
        problems.append(_problem(BAD_CATEGORY, "category"))

    text = (question or "").strip()
    problems.extend(_problem(code, "question") for code in validate_question(question))
    key = question_key(text)
    if key and key in set(active_keys):
        problems.append(_problem(DUPLICATE_QUESTION, "question"))

    clean_answer = (answer or "").strip() or None
    if clean_answer is None and bot_responde:
        problems.append(_problem(ANSWER_REQUIRED, "answer"))
    clean_vars = clean_variations(variations)
    problems.extend(_contact_problems(clean_answer, clean_vars))

    return FaqValidation(
        problems=tuple(problems),
        question=text,
        question_key=key,
        answer=clean_answer,
        variations=clean_vars,
    )
