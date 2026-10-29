from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.phone import MSG_LANDLINE
from app.services.users import FieldError, ValidatedUser, validate_row

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIELDS = ["nome", "telefone", "perfil", "empresa", "ufs", "canais", "marcas", "gestor"]


def good_row() -> dict[str, Any]:
    return {
        "nome": "Ana Exemplo",
        "telefone": "+55 11 91234-5678",
        "perfil": "vendedor",
        "empresa": "Empresa Exemplo",
        "ufs": "SP|RJ",
        "canais": "atacarejo;farma",
        "marcas": "jungle|zaya",
        "gestor": "Bruno Exemplo",
    }


def errors_of(row: dict[str, Any]) -> list[FieldError]:
    result = validate_row(row, now=NOW)
    assert isinstance(result, list)
    return result


def test_valid_row() -> None:
    result = validate_row(good_row(), now=NOW)
    assert isinstance(result, ValidatedUser)
    assert result.name == "Ana Exemplo"
    assert result.phone_e164 == "+5511912345678"
    assert result.phone_key == "551112345678"
    assert result.profile == "vendedor"
    assert result.company == "Empresa Exemplo"
    assert result.ufs == ("SP", "RJ")
    assert result.channels == ("atacarejo", "farma")
    assert result.brands == ("jungle", "zaya")
    assert result.manager_name == "Bruno Exemplo"
    assert result.valid_until is None


def test_python_lists_and_case_and_dedupe() -> None:
    row = good_row() | {"ufs": ["sp", " SP ", "rj"], "marcas": ["Jungle"], "perfil": " Executivo "}
    result = validate_row(row, now=NOW)
    assert isinstance(result, ValidatedUser)
    assert result.ufs == ("SP", "RJ")
    assert result.brands == ("jungle",)
    assert result.profile == "executivo"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("mode", ["missing", "empty", "blank", "none"])
def test_missing_field_reports_that_field(field: str, mode: str) -> None:
    row = good_row()
    if mode == "missing":
        del row[field]
    else:
        row[field] = {"empty": "", "blank": "  ", "none": None}[mode]
    assert [e.field for e in errors_of(row)] == [field]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("telefone", "11 91234-5678"),
        ("telefone", "+55 11 3123-4567"),
        ("telefone", "abc"),
        ("perfil", "chefe"),
        ("ufs", "SP|XX"),
        ("canais", "banca"),
        ("marcas", "outra"),
        ("ufs", [1, 2]),
        ("ufs", 5),
    ],
)
def test_invalid_value_reports_field(field: str, value: Any) -> None:
    errors = errors_of(good_row() | {field: value})
    assert [e.field for e in errors] == [field]
    assert errors[0].reason


def test_phone_reason_comes_from_phone_module() -> None:
    errors = errors_of(good_row() | {"telefone": "+55 11 3123-4567"})
    assert errors[0].reason == MSG_LANDLINE


def test_multiple_errors_at_once_in_field_order() -> None:
    row = good_row() | {"gestor": "", "telefone": "x", "marcas": "nope", "nome": ""}
    assert [e.field for e in errors_of(row)] == ["nome", "telefone", "marcas", "gestor"]


def test_empty_row_reports_all_eight_fields() -> None:
    assert [e.field for e in errors_of({})] == FIELDS


def test_does_not_mutate_input() -> None:
    row = good_row()
    snapshot = dict(row)
    validate_row(row, now=NOW)
    assert row == snapshot
