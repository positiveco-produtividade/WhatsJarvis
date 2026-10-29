"""Busca por palavras (port do piloto). Itens SINTETICOS: nenhum dado Nielsen/GMR real."""

from __future__ import annotations

from app.services.brain.matching import (
    Match,
    Searchable,
    brands_in,
    detect_brand,
    f1,
    is_brand_tie,
    margin,
    normalize,
    rank,
    tokens,
    ufs_in,
)

THRESHOLD = 0.55

ITEMS = [
    Searchable("faq:1", "jungle", "Jungle tem glúten?"),
    Searchable("faq:2", "jungle", "Quais são os sabores de Jungle?"),
    Searchable("faq:3", "possible", "Quais são os sabores de Possible?"),
    Searchable("nielsen:1", "jungle", "Quem lidera isotônicos em CE?"),
    Searchable("nielsen:2", "jungle", "Quem lidera isotônicos no Brasil?"),
    Searchable("faq:4", "todas", "Como funciona o pedido mínimo?"),
]


def test_normalize_nfd_and_symbols() -> None:
    assert normalize("Glúten? Açaí!") == "gluten  acai "


def test_tokens_stopwords_synonyms_plural() -> None:
    assert tokens("Qual a validade dos isotônicos?") == ["validade", "isotonico"]
    assert tokens("vendidos") == ["pedem"]
    assert tokens("A Tal da Castanha") == ["castanha", "castanha"]
    assert tokens("em ce se") == []  # siglas de 2 letras nao sao tokens


def test_tokens_stopword_checked_before_plural_strip() -> None:
    assert tokens("Quais são os sabores?") == ["sao", "sabore"]  # "quais" nao vira "quai"
    assert tokens("posso treinar") == ["pode", "treino"]
    assert tokens("dura") == tokens("duram") == ["dura"]


def test_f1_symmetric() -> None:
    assert f1(frozenset("ab"), frozenset("ab")) == 1.0
    assert f1(frozenset("ab"), frozenset("bc")) == 0.5
    assert f1(frozenset(), frozenset()) == 0.0


def test_uf_rule_uppercase_or_after_preposition() -> None:
    assert ufs_in("vendas em CE") == {"ce"}
    assert ufs_in("vendas em ce") == {"ce"}
    assert ufs_in("o pe de feijao") == frozenset()  # "pe" solto nao e UF
    assert ufs_in("SP e RJ") == {"sp", "rj"}


def test_brand_detection_slugs_and_plant_power_products() -> None:
    assert detect_brand("Jungle tem glúten?") == "jungle"
    assert detect_brand("qual o preço do nitro?") == "plant_power"
    assert detect_brand("tem ultracoffee e creapower?") == "plant_power"
    assert detect_brand("a Tal da Castanha vende bem?") == "a_tal_da_castanha"
    assert detect_brand("e a Zaya?") == "zaya"
    assert detect_brand("Jungle ou Possible?") is None  # duas marcas
    assert brands_in("Jungle ou Possible?") == ["jungle", "possible"]
    assert detect_brand("bom dia") is None
    # "proteina" e a palavra comum, nao o produto Protein da Plant Power
    assert detect_brand("O isotônico de vocês tem proteína?") is None
    assert detect_brand("como funciona o Protein?") == "plant_power"


def test_case1_faq_answered() -> None:
    found = rank("jungle tem gluten?", ITEMS)
    assert found[0].ref == "faq:1"
    assert found[0].score >= THRESHOLD


def test_case2_outside_faq_below_threshold() -> None:
    found = rank("qual o preço do proteico em PE?", ITEMS)
    assert not found or found[0].score < THRESHOLD


def test_case3_no_tokens_returns_nothing() -> None:
    assert rank("oi", ITEMS) == []


def test_case4_uf_rule_excludes_other_state() -> None:
    refs = [m.ref for m in rank("Quem lidera isotônicos em SP?", ITEMS)]
    assert "nielsen:1" not in refs  # item de CE
    assert "nielsen:2" not in refs  # Brasil nao responde pedido de estado
    found = rank("Quem lidera isotônicos em CE?", ITEMS)
    assert found[0].ref == "nielsen:1" and found[0].score == 1.0


def test_case5_brasil_rule_and_brand_filter() -> None:
    found = rank("Quem lidera isotônicos no Brasil?", ITEMS)
    assert found[0].ref == "nielsen:2"
    refs = [m.ref for m in rank("sabores de Possible", ITEMS)]
    assert "faq:3" in refs and "faq:2" not in refs  # marca citada filtra
    assert "faq:4" in [m.ref for m in rank("Jungle pedido mínimo", ITEMS)]  # "todas" passa


def test_variations_are_extra_forms_best_wins() -> None:
    item = Searchable("faq:9", "jungle", "Jungle é adequado para celíacos?", ("jungle tem gluten",))
    assert rank("jungle tem gluten", [item])[0].score == 1.0
    plain = Searchable("faq:9", "jungle", "Jungle é adequado para celíacos?")
    assert rank("jungle tem gluten", [plain])[0].score < 1.0


def test_brand_filter_and_floor_and_limit() -> None:
    assert rank("sabores", ITEMS, brand_filter=["possible"])[0].ref == "faq:3"
    assert rank("sabores", ITEMS, floor=0.99) == []
    assert len(rank("sabores", ITEMS, limit=1)) == 1


def test_margin_between_first_and_second() -> None:
    ms = [Match("a", "jungle", 0.9), Match("b", "possible", 0.6)]
    assert margin(ms) == 0.9 - 0.6
    assert margin(ms[:1]) == 0.9
    assert margin([]) == 0.0


def test_brand_tie_ask_brand() -> None:
    close = [Match("a", "jungle", 0.80), Match("b", "possible", 0.78)]
    assert is_brand_tie(close, threshold=THRESHOLD)
    same = [Match("a", "jungle", 0.8), Match("b", "jungle", 0.79)]
    assert not is_brand_tie(same, threshold=THRESHOLD)
    far = [Match("a", "jungle", 0.8), Match("b", "possible", 0.6)]
    assert not is_brand_tie(far, threshold=THRESHOLD)
    low = [Match("a", "jungle", 0.3), Match("b", "possible", 0.3)]
    assert not is_brand_tie(low, threshold=THRESHOLD)
