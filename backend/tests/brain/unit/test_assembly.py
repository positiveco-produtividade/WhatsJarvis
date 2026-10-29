"""Testes da montagem da resposta (CB-11, CB-24)."""

from __future__ import annotations

import re

import pytest

from app.core.values import BRANDS
from app.services.brain.assembly import (
    AGENT_SIGNATURES,
    INTERACTIVE_BODY_MAX_CHARS,
    PART_MAX_CHARS,
    assemble,
    signature_for,
)


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _body(parts: tuple[str, ...], suffix: str) -> str:
    return "".join(parts).removesuffix(suffix).rstrip()


def test_short_text_one_part_with_signature() -> None:
    assert assemble("Oi, tudo certo.", brands=["jungle"]) == (
        "Oi, tudo certo.\n\n— Duda · Jungle",
    )


@pytest.mark.parametrize(
    ("brand", "name"),
    [
        ("jungle", "Duda · Jungle"),
        ("plant_power", "Léo · Plant Power"),
        ("possible", "Nico · Possible"),
        ("a_tal_da_castanha", "Rita · A Tal da Castanha"),
        ("zaya", "Tati · Zaya"),
    ],
)
def test_signature_per_brand(brand: str, name: str) -> None:
    assert signature_for([brand]) == f"— {name}"


def test_signature_map_covers_all_brands() -> None:
    assert set(AGENT_SIGNATURES) == set(BRANDS)


@pytest.mark.parametrize("brands", [None, [], ["todas"], ["jungle", "zaya"], ["xyz"]])
def test_signature_jarvis_when_absent_mixed_or_todas(brands: list[str] | None) -> None:
    assert signature_for(brands) == "— Jarvis"


def test_splits_by_sentence_and_signature_only_on_last() -> None:
    text = " ".join(f"Frase numero {i} com algum texto de enchimento aqui." for i in range(40))
    parts = assemble(text, brands=["zaya"])
    assert len(parts) > 1
    assert all(len(p) <= PART_MAX_CHARS for p in parts)
    assert all("— Tati" not in p for p in parts[:-1])
    assert parts[-1].endswith("\n\n— Tati · Zaya")
    for p in parts[:-1]:
        assert p.endswith(".")  # corta em fim de frase
    assert _squash(_body(parts, "— Tati · Zaya")) == _squash(text)


def test_single_long_sentence_splits_on_word_boundary() -> None:
    text = " ".join(f"palavra{i}" for i in range(400))
    parts = assemble(text)
    assert len(parts) > 1
    assert all(len(p) <= PART_MAX_CHARS for p in parts)
    assert " ".join(parts).removesuffix("— Jarvis").split() == text.split()


def test_single_giant_word_hard_split() -> None:
    text = "x" * 1800
    parts = assemble(text)
    assert all(len(p) <= PART_MAX_CHARS for p in parts)
    assert _squash(_body(parts, "— Jarvis")) == text


def test_last_part_with_signature_fits_limit_by_resplit() -> None:
    sentence = "a" * 98 + "."
    text = " ".join([sentence] * 6) + " " + "b" * 90 + "."
    suffix = "Fonte: tabela Jungle 2026\n— Rita · A Tal da Castanha"
    parts = assemble(text, brands=["a_tal_da_castanha"], source_line="Fonte: tabela Jungle 2026")
    assert all(len(p) <= PART_MAX_CHARS for p in parts)
    assert len(parts[-1]) <= INTERACTIVE_BODY_MAX_CHARS
    assert parts[-1].endswith(suffix)
    assert _squash(_body(parts, suffix)) == _squash(text)


def test_source_line_added_once_at_end() -> None:
    parts = assemble("Resposta.", brands=["possible"], source_line="Fonte: FAQ")
    assert parts == ("Resposta.\n\nFonte: FAQ\n— Nico · Possible",)


def test_source_not_duplicated_when_already_in_text() -> None:
    text = "Share de 12% (Fonte: Nielsen, 2025)."
    parts = assemble(text, source_line="Fonte: Nielsen, 2025")
    assert "".join(parts).count("Fonte:") == 1


def test_oversized_suffix_goes_alone_without_cutting() -> None:
    parts = assemble("Oi.", source_line="Fonte: " + "z" * 900)
    assert parts[0] == "Oi."
    assert "z" * 900 in parts[-1]
    assert parts[-1].endswith("— Jarvis")


def test_empty_text_returns_signature_only() -> None:
    assert assemble("  ") == ("— Jarvis",)


@pytest.mark.parametrize("n", [1, 3, 15, 60])
def test_property_concat_reproduces_original(n: int) -> None:
    text = "\n".join(f"Linha {i}: valor R$ {i},90! Confere? Sim... ok." * 3 for i in range(n))
    parts = assemble(text, brands=["jungle"], source_line="Fonte: x")
    assert all(len(p) <= PART_MAX_CHARS for p in parts)
    assert _squash(_body(parts, "Fonte: x\n— Duda · Jungle")) == _squash(text)
