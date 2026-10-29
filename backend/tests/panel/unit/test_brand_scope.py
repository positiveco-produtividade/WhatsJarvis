"""Escopo por marca (PN-5, PN-6): funcao pura, sem banco."""

import pytest

from app.panel.services.scope import Scope, parse_scope
from app.panel.values import BRANDS


@pytest.mark.parametrize(
    "raw", [None, "", "todas", "TODAS", "zzz", "plant_power; DROP", "x' OR 1=1"]
)
def test_invalid_or_all_cookie_is_all(raw: str | None) -> None:
    scope = parse_scope(raw)
    assert scope.is_all
    assert scope.cookie_value == "todas"


@pytest.mark.parametrize("brand", sorted(BRANDS))
def test_valid_brand_roundtrip(brand: str) -> None:
    scope = parse_scope(brand)
    assert scope.brand == brand and not scope.is_all
    assert scope.cookie_value == brand


def test_unknown_brand_rejected_by_constructor() -> None:
    with pytest.raises(ValueError):
        Scope("nao_existe")


def test_all_brands_adds_no_filter_and_no_params() -> None:
    params: list[object] = []
    for fragment in (
        Scope().sql("brand", params),
        Scope().sql_faq("brand", params),
        Scope().sql_alert("payload", params),
    ):
        assert fragment == "TRUE"
    assert params == []


def test_tickets_fragment_binds_brand_and_handles_null_media() -> None:
    params: list[object] = ["x", "y"]
    frag = Scope("plant_power").sql("t.brand", params, kind_column="t.kind")
    assert frag == "(t.brand = $3 OR (t.brand IS NULL AND t.kind = 'media'))"
    assert params == ["x", "y", "plant_power"]
    assert "plant_power" not in frag


def test_faq_fragment_includes_todas_items() -> None:
    params: list[object] = []
    assert Scope("zaya").sql_faq("brand", params) == "brand IN ($1, 'todas')"
    assert params == ["zaya"]


def test_alert_fragment_accepts_missing_brand() -> None:
    params: list[object] = []
    frag = Scope("jungle").sql_alert("a.payload", params)
    assert frag == "(a.payload->>'brand' = $1 OR a.payload->>'brand' IS NULL)"
    assert params == ["jungle"]


@pytest.mark.parametrize("bad", ["brand; DROP TABLE x", "a b", "brand--", "1col", "a.b.c", ""])
def test_unsafe_identifier_rejected(bad: str) -> None:
    for scope in (Scope("jungle"), Scope()):
        with pytest.raises(ValueError):
            scope.sql(bad, [])
        with pytest.raises(ValueError):
            scope.sql_faq(bad, [])
        with pytest.raises(ValueError):
            scope.sql_alert(bad, [])


def test_ticket_matching_media_without_brand_in_every_brand() -> None:
    for brand in BRANDS:
        assert Scope(brand).matches_ticket(None, "media")
        assert not Scope(brand).matches_ticket(None, "question")
    assert Scope("jungle").matches_ticket("jungle", "question")
    assert not Scope("jungle").matches_ticket("zaya", "question")
    assert not Scope("jungle").matches_ticket("zaya", "media")
    assert Scope().matches_ticket(None, "question")


def test_conversation_visibility_three_sellers() -> None:
    one = [("plant_power", "question")]
    two = [("plant_power", "question"), ("a_tal_da_castanha", "question")]
    media = [(None, "media")]
    pp, atc = Scope("plant_power"), Scope("a_tal_da_castanha")
    assert pp.shows_conversation(one) and not atc.shows_conversation(one)
    assert pp.shows_conversation(two) and atc.shows_conversation(two)
    assert pp.shows_conversation(media) and atc.shows_conversation(media)
    assert not pp.shows_conversation([])
    assert all(Scope().shows_conversation(c) for c in (one, two, media, []))
