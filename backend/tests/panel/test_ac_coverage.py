"""Mapa AC -> teste (PN-T67): todo PN ativo tem teste automatizado real ou entrada manual."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
MAP_FILE = Path(__file__).resolve().parent / "AC_MAP.md"
REMOVED = {46, 62, 64}
ACTIVE = [n for n in range(1, 68) if n not in REMOVED]
PN_RE = re.compile(r"PN-(\d+)")
REF_RE = re.compile(r"`([^`]+\.py(?:::[^`]+)?)`")


def _sections() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in MAP_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def _parse_automated(lines: list[str]) -> dict[int, list[str]]:
    mapping: dict[int, list[str]] = {}
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        match = re.fullmatch(r"PN-(\d+)", cells[0])
        if match:
            mapping[int(match.group(1))] = REF_RE.findall(cells[1])
    return mapping


def _bullet_pns(lines: list[str]) -> dict[int, str]:
    found: dict[int, str] = {}
    for line in lines:
        match = re.match(r"- PN-(\d+):\s*(.+)", line)
        if match:
            found[int(match.group(1))] = match.group(2)
    return found


def _function_exists(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = name.split("::")
    body = tree.body
    for part in parts:
        node = next(
            (
                n
                for n in body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and n.name == part
            ),
            None,
        )
        if node is None:
            return False
        body = node.body
    return True


def _ref_exists(ref: str) -> bool:
    file_part, _, func = ref.partition("::")
    path = TESTS_DIR / file_part
    if not path.is_file():
        return False
    return _function_exists(path, func) if func else True


@pytest.fixture(scope="module")
def sections() -> dict[str, list[str]]:
    return _sections()


@pytest.fixture(scope="module")
def automated(sections: dict[str, list[str]]) -> dict[int, list[str]]:
    return _parse_automated(sections["automatizados"])


def test_ha_64_pn_ativos() -> None:
    assert len(ACTIVE) == 64


def test_pn_removidos_nao_aparecem_como_ativos(
    sections: dict[str, list[str]], automated: dict[int, list[str]]
) -> None:
    assert not REMOVED & set(automated)
    assert not REMOVED & set(_bullet_pns(sections["manuais"]))
    assert not REMOVED & set(_bullet_pns(sections["lacunas conhecidas"]))


def test_nenhum_pn_fora_da_faixa(automated: dict[int, list[str]]) -> None:
    assert set(automated) <= set(ACTIVE)


def test_todo_pn_ativo_tem_cobertura_automatizada_ou_manual(
    sections: dict[str, list[str]], automated: dict[int, list[str]]
) -> None:
    manual = _bullet_pns(sections["manuais"])
    uncovered = [n for n in ACTIVE if not automated.get(n) and not manual.get(n)]
    assert not uncovered, f"PN sem cobertura: {uncovered}"


def test_referencias_automatizadas_existem(automated: dict[int, list[str]]) -> None:
    missing = [(pn, ref) for pn, refs in automated.items() for ref in refs if not _ref_exists(ref)]
    assert not missing, f"referencias inexistentes: {missing}"


def test_referencias_de_lacunas_existem(sections: dict[str, list[str]]) -> None:
    refs = REF_RE.findall("\n".join(sections["lacunas conhecidas"]))
    assert refs, "a lacuna do PN-61 deve apontar para o teste xfail"
    assert all(_ref_exists(r) for r in refs)


def test_manuais_tem_motivo_e_lacunas_declaradas(sections: dict[str, list[str]]) -> None:
    manual = _bullet_pns(sections["manuais"])
    assert all(len(reason) >= 10 for reason in manual.values())
    assert 59 in manual
    gaps = _bullet_pns(sections["lacunas conhecidas"])
    assert 61 in gaps and "xfail" in gaps[61]
    assert "Playwright" in "\n".join(sections["lacunas conhecidas"])
