"""Verificador de fidelidade da resposta da IA (CB-2, CB-8), deterministico.

Funcoes puras, sem banco, sem I/O. A saida do modelo e tratada como dado nao
confiavel: tudo o que a resposta afirma precisa existir nos itens citados.
O reparo troca a resposta pelo texto do melhor item citado (verbatim).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.services.brain.matching import brands_in, normalize

MAX_LENGTH_RATIO = 1.3

V_REFS_OUTSIDE = "refs_outside_candidates"
V_NO_REFS = "no_valid_refs"
V_NUMBER = "number_not_in_items"
V_PRICE_MISSING = "price_missing"
V_PRICE_VALUE = "price_not_in_items"
V_NAME = "name_not_in_items"
V_LENGTH = "too_long"
V_CONTACT = "contact_not_in_items"
V_EMPTY = "empty_answer"

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_MONEY = re.compile(r"R\$\s*(\d+(?:[.,]\d+)*)")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_CAPITALIZED = re.compile(r"[A-ZÀ-Ý][\wÀ-ÿ]*")
_SENTENCE_START = re.compile(r"(?:\A|[.!?\n:]\s*|[-*•—]\s+)\Z")
_THOUSANDS_DOT = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")
_THOUSANDS_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_MIN_PHONE_DIGITS = 8


class FidelityStatus(StrEnum):
    OK = "ok"
    REPAIRED = "repaired"
    FAILED = "failed"


@dataclass(frozen=True)
class Candidate:
    """Item enviado a IA: `ref` estavel e texto aprovado."""

    ref: str
    text: str


@dataclass(frozen=True)
class FidelityResult:
    """`text` e a resposta final (None em `failed` = no_answer); `refs` os itens citados."""

    status: FidelityStatus
    text: str | None
    refs: tuple[str, ...]
    violations: tuple[str, ...]


def parse_number(raw: str) -> Decimal | None:
    """Valor numerico com milhar e virgula decimal normalizados ("1.299,90" -> 1299.90)."""
    if _THOUSANDS_DOT.fullmatch(raw):
        cleaned = raw.replace(".", "").replace(",", ".")
    elif _THOUSANDS_COMMA.fullmatch(raw):
        cleaned = raw.replace(",", "")
    else:
        cleaned = raw.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _numbers(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in _NUMBER.finditer(text):
        value = parse_number(m.group(0))
        if value is not None:
            out.add(value)
    return out


def _money(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in _MONEY.finditer(text):
        value = parse_number(m.group(1))
        if value is not None:
            out.add(value)
    return out


def _urls(text: str) -> set[str]:
    return {m.group(0).lower().rstrip(".,;:!?)") for m in _URL.finditer(text)}


def _emails(text: str) -> set[str]:
    return {m.group(0).lower() for m in _EMAIL.finditer(text)}


def _phones(text: str) -> set[str]:
    digits = (re.sub(r"\D", "", m.group(0)) for m in _PHONE.finditer(text))
    return {d for d in digits if len(d) >= _MIN_PHONE_DIGITS}


def _word_forms(text: str) -> set[str]:
    """Palavras normalizadas, com e sem "s" final (tolera plural)."""
    forms: set[str] = set()
    for word in normalize(text).split():
        forms.add(word)
        if word.endswith("s"):
            forms.add(word[:-1])
    return forms


def _invented_names(answer: str, reference: str) -> bool:
    """True se a resposta cita nome proprio ou marca ausente dos itens e da pergunta."""
    if set(brands_in(answer)) - set(brands_in(reference)):
        return True
    known = _word_forms(reference)
    for m in _CAPITALIZED.finditer(answer):
        if _SENTENCE_START.search(answer[: m.start()]):
            continue
        norm = normalize(m.group(0)).strip()
        if norm and norm not in known and norm.rstrip("s") not in known:
            return True
    return False


def _contacts_ok(answer: str, cited: str) -> bool:
    return (
        _urls(answer) <= _urls(cited)
        and _emails(answer) <= _emails(cited)
        and _phones(answer) <= _phones(cited)
    )


def _violations(
    answer: str, cited_text: str, question: str, *, price_intent: bool
) -> list[str]:
    found: list[str] = []
    if not answer.strip():
        return [V_EMPTY]
    if not _numbers(answer) <= _numbers(cited_text):
        found.append(V_NUMBER)
    found.extend(_price_violations(answer, cited_text, price_intent=price_intent))
    if _invented_names(answer, f"{cited_text}\n{question}"):
        found.append(V_NAME)
    if len(answer) > MAX_LENGTH_RATIO * len(cited_text):
        found.append(V_LENGTH)
    if not _contacts_ok(answer, cited_text):
        found.append(V_CONTACT)
    return found


def _price_violations(answer: str, cited_text: str, *, price_intent: bool) -> list[str]:
    values = _money(answer)
    found: list[str] = []
    if price_intent and not values:
        found.append(V_PRICE_MISSING)
    if not values <= _money(cited_text):
        found.append(V_PRICE_VALUE)
    return found


def _repair(best: Candidate, *, price_intent: bool) -> FidelityResult | None:
    """Texto verbatim do melhor item, se passar na regra de preco; senao None."""
    if price_intent and not _money(best.text):
        return None
    return FidelityResult(FidelityStatus.REPAIRED, best.text, (best.ref,), ())


def _fail(violations: Sequence[str]) -> FidelityResult:
    return FidelityResult(FidelityStatus.FAILED, None, (), tuple(violations))


def check(
    answer_text: str,
    item_refs: Sequence[str],
    candidates: Sequence[Candidate],
    *,
    question: str = "",
    price_intent: bool = False,
) -> FidelityResult:
    """Verifica a saida da IA contra os candidatos enviados.

    O "melhor item citado" e o primeiro de `item_refs` que e candidato valido.
    """
    by_ref = {c.ref: c for c in candidates}
    valid = [r for r in dict.fromkeys(item_refs) if r in by_ref]
    if not valid:
        return _fail([V_NO_REFS])
    found: list[str] = []
    if any(r not in by_ref for r in item_refs):
        found.append(V_REFS_OUTSIDE)
    cited_text = "\n".join(by_ref[r].text for r in valid)
    found.extend(_violations(answer_text, cited_text, question, price_intent=price_intent))
    if not found:
        return FidelityResult(FidelityStatus.OK, answer_text, tuple(valid), ())
    repaired = _repair(by_ref[valid[0]], price_intent=price_intent)
    if repaired is None:
        return _fail(found)
    return FidelityResult(repaired.status, repaired.text, repaired.refs, tuple(found))


def check_verbatim(
    item_refs: Sequence[str],
    candidates: Sequence[Candidate],
    *,
    price_intent: bool = False,
) -> FidelityResult:
    """Modo `verbatim`: a IA so escolhe `item_refs`; a resposta e o texto do melhor item."""
    by_ref = {c.ref: c for c in candidates}
    valid = [r for r in dict.fromkeys(item_refs) if r in by_ref]
    if not valid:
        return _fail([V_NO_REFS])
    best = by_ref[valid[0]]
    if price_intent and not _money(best.text):
        return _fail([V_PRICE_MISSING])
    return FidelityResult(FidelityStatus.OK, best.text, (best.ref,), ())
