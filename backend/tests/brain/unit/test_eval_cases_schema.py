"""Esquema do conjunto de avaliacao (CB-T24 / CB-21).

Valida todo `tests/brain/eval/cases/*.jsonl` e varre marcadores de dado licenciado
(Nielsen/GMR reais): so itens sinteticos sao aceitos.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

CASES_DIR = Path(__file__).resolve().parents[1] / "eval" / "cases"
EXPECTED = {"responde", "saudacao", "nao_sei", "pede_marca", "acesso", "recusa"}
CATEGORIES = {"guia", "piloto", "preco", "followup", "ataque", "sintetico"}
FILE_CATEGORY = {
    "guia.jsonl": "guia",
    "piloto.jsonl": "piloto",
    "preco.jsonl": "preco",
    "followup.jsonl": "followup",
    "ataques.jsonl": "ataque",
    "nielsen_gmr_sinteticos.jsonl": "sintetico",
}
BRANDS = {"jungle", "plant_power", "possible", "a_tal_da_castanha", "zaya"}
UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}
ITEM_CATEGORIES = {"PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA"}
ITEM_ORIGINS = {"guia", "manual", "import", "seed", "nielsen", "gmr"}
GUIA_ITEMS = 112  # piloto/guia.json: ref "guia:NNN" = posicao (1..112) na carga inicial
REF_RE = re.compile(r"^(guia:\d{3}|(nielsen|gmr|preco)_sint:\d{3})$")
CASE_KEYS = {
    "id", "category", "question", "registration", "history", "expected", "refs",
    "items", "must_contain", "price_in_source", "classification", "note",
}
REQUIRED_KEYS = {"id", "category", "question", "registration", "expected", "refs"}
ITEM_KEYS = {"ref", "brand", "category", "origin", "title", "text", "answerable"}

# Marcadores de dado licenciado real: planilhas, pasta de dados, rodape de relatorio.
LICENSED_MARKERS = [
    re.compile(r"\.xlsx?\b", re.I),
    re.compile(r"designs/dados", re.I),
    re.compile(r"scantrack|retail\s+index|nielseniq", re.I),
    re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*)?(?:nielsen|gmr)", re.I),
    re.compile(r"(?:nielsen|gmr)[^\n]{0,40}(?:confidencial|proibida a reprodu)", re.I),
]


def scan_licensed(text: str) -> list[str]:
    """Padroes de dado licenciado encontrados no texto (vazio = limpo)."""
    return [m.pattern for m in LICENSED_MARKERS if m.search(text)]


def _load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:  # pragma: no cover - mensagem util
            pytest.fail(f"{path.name}:{n} JSON invalido: {exc}")
    return rows


def _all_cases() -> list[tuple[str, dict[str, Any]]]:
    return [(p.name, c) for p in sorted(CASES_DIR.glob("*.jsonl")) for c in _load(p)]


def _check_item(name: str, case_id: str, item: dict[str, Any]) -> None:
    where = f"{name}:{case_id}"
    assert set(item) <= ITEM_KEYS and ITEM_KEYS - {"answerable"} <= set(item), where
    assert REF_RE.match(item["ref"]) and not item["ref"].startswith("guia:"), where
    assert item["brand"] in BRANDS and item["category"] in ITEM_CATEGORIES, where
    assert item["origin"] in ITEM_ORIGINS, where
    assert item["title"].strip() and item["text"].strip(), where
    assert "SINTETICO" in item["text"], f"{where}: item inline deve ser marcado SINTETICO"
    if item["origin"] in {"nielsen", "gmr"}:
        assert item["ref"].startswith(f"{item['origin']}_sint:"), where
        assert "sintético" in item["text"], where


def _check_registration(where: str, reg: dict[str, Any]) -> None:
    assert set(reg) == {"brands", "ufs"}, where
    assert reg["brands"] and set(reg["brands"]) <= BRANDS, where
    assert len(set(reg["brands"])) == len(reg["brands"]), where
    assert reg["ufs"] and set(reg["ufs"]) <= UFS, where


def _check_history(where: str, history: list[dict[str, str]]) -> None:
    assert history, where
    for turn in history:
        assert set(turn) == {"role", "text"}, where
        assert turn["role"] in {"user", "assistant"} and turn["text"].strip(), where


def _check_case(name: str, c: dict[str, Any]) -> None:
    where = f"{name}:{c.get('id')}"
    assert REQUIRED_KEYS <= set(c) <= CASE_KEYS, where
    assert c["category"] == FILE_CATEGORY[name], where
    assert isinstance(c["question"], str) and c["question"].strip(), where
    assert c["expected"] in EXPECTED, where
    _check_registration(where, c["registration"])
    if "history" in c:
        _check_history(where, c["history"])
    assert isinstance(c["refs"], list) and len(set(c["refs"])) == len(c["refs"]), where
    alternatives = [a for ref in c["refs"] for a in ref.split("|")]  # "a|b" = qualquer um
    for ref in alternatives:
        assert REF_RE.match(ref), f"{where}: ref {ref!r}"
        if ref.startswith("guia:"):
            assert 1 <= int(ref[5:]) <= GUIA_ITEMS, f"{where}: ref fora do guia"
    inline = {i["ref"] for i in c.get("items", [])}
    for item in c.get("items", []):
        _check_item(name, c["id"], item)
    for ref in alternatives:
        assert ref.startswith("guia:") or ref in inline, f"{where}: ref sem item"
    if c["expected"] in {"nao_sei", "acesso", "recusa"}:
        assert not c["refs"] or c["category"] == "ataque", where
    if c["expected"] == "pede_marca":
        assert len(c["registration"]["brands"]) >= 2 and len(c["refs"]) >= 2, where
    if c["expected"] == "acesso":
        assert not c["refs"], where


def test_cases_dir_has_all_files() -> None:
    assert {p.name for p in CASES_DIR.glob("*.jsonl")} == set(FILE_CATEGORY)


def test_every_case_matches_schema() -> None:
    cases = _all_cases()
    assert cases
    for name, case in cases:
        _check_case(name, case)


def test_ids_are_unique_across_files() -> None:
    ids = [c["id"] for _, c in _all_cases()]
    assert len(ids) == len(set(ids))


def test_volume_is_enough_for_98_percent_gate() -> None:
    cases = [c for _, c in _all_cases()]
    assert len(cases) >= 100
    assert sum(c["category"] == "preco" for c in cases) >= 15
    assert {c["expected"] for c in cases} == EXPECTED
    assert {c["category"] for c in cases} == CATEGORIES


def test_guia_covers_all_112_items() -> None:
    refs = {r for n, c in _all_cases() if n == "guia.jsonl" for r in c["refs"]}
    assert {f"guia:{i:03d}" for i in range(1, GUIA_ITEMS + 1)} <= refs


def test_piloto_has_five_cases() -> None:
    assert len(_load(CASES_DIR / "piloto.jsonl")) == 5


def test_price_cases_with_and_without_value() -> None:
    price = [c for n, c in _all_cases() if n == "preco.jsonl"]
    with_value = [c for c in price if c["price_in_source"]]
    without = [c for c in price if not c["price_in_source"]]
    assert len(with_value) >= 5 and len(without) >= 5
    for c in with_value:
        assert c["expected"] == "responde" and c["must_contain"]
        text = " ".join(i["text"] for i in c["items"])
        assert all(s in text for s in c["must_contain"]) and "R$" in text
    for c in without:
        assert c["expected"] == "nao_sei" and c["classification"] == "PRECO"
        assert not any(re.search(r"\d", i["text"]) for i in c.get("items", []))


def test_must_contain_is_present_in_cited_inline_items() -> None:
    for name, c in _all_cases():
        if "must_contain" not in c:
            continue
        if c["expected"] == "saudacao":  # o trecho vem do texto fixo das configuracoes
            assert not c["refs"] and not c.get("items"), f"{name}:{c['id']}"
            continue
        cited = " ".join(i["text"] for i in c["items"] if i["ref"] in c["refs"])
        for s in c["must_contain"]:
            assert s in cited, f"{name}:{c['id']}: {s!r} nao esta no item citado"


def test_followups_have_history_and_attacks_exist() -> None:
    follow = [c for n, c in _all_cases() if n == "followup.jsonl"]
    assert len(follow) >= 10 and all("history" in c for c in follow)
    attacks = [c for n, c in _all_cases() if n == "ataques.jsonl"]
    assert len(attacks) >= 15 and any(c["expected"] == "recusa" for c in attacks)


def test_synthetic_file_only_has_synthetic_nielsen_gmr_items() -> None:
    cases = [c for n, c in _all_cases() if n == "nielsen_gmr_sinteticos.jsonl"]
    assert len(cases) >= 15
    origins = {i["origin"] for c in cases for i in c["items"]}
    assert origins == {"nielsen", "gmr"}


def test_no_licensed_data_markers_in_any_case_file() -> None:
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        found = scan_licensed(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name}: marcador de dado licenciado {found}"


@pytest.mark.parametrize(
    "sample",
    [
        "dados_nielsen_2026.xlsx",
        "ver docs/designs/dados/base.csv",
        "NielsenIQ Retail Index out/2026",
        "Scantrack Brasil",
        "© 2026 Nielsen - todos os direitos",
        "GMR confidencial, proibida a reproducao",
    ],
)
def test_scanner_rejects_real_markers(sample: str) -> None:
    assert scan_licensed(sample)


def test_scanner_accepts_synthetic_text() -> None:
    assert not scan_licensed("SINTETICO: marca Exemplo 01 lidera em CE. (Fonte: Nielsen sintético)")


def test_pede_marca_candidates_are_in_registered_brands() -> None:
    for name, c in _all_cases():
        if c["expected"] != "pede_marca":
            continue
        assert name in {"guia.jsonl", "followup.jsonl"}
        assert len(c["registration"]["brands"]) >= 2
