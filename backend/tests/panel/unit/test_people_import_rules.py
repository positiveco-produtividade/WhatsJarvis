"""Leitura e classificacao do CSV de pessoas (PN-T37, PN-40, PN-33)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.panel.services import people_import as pi

HEADER = "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor"
GOOD = "Ana Souza,+5585991231201,vendedor,Loja X,CE|PE,atacarejo;farma,jungle,Bia Lima"


def _parse(text: str, name: str = "p.csv") -> pi.ParsedUpload:
    return pi.parse_table(text.encode("utf-8"), name)


def _classify(lines: Sequence[str], existing: Sequence[str] = ()) -> list[dict]:
    up = _parse("\n".join([HEADER, *lines]) + "\n")
    return pi.classify_rows(up, existing_keys=existing)


def test_csv_built_in_tmp_path_reads_rows_with_line_numbers(tmp_path: Path) -> None:
    f = tmp_path / "pessoas.csv"
    f.write_text(f"﻿{HEADER}\n\n{GOOD}\n", encoding="utf-8")
    up = pi.parse_table(f.read_bytes(), f.name)
    assert [ln for ln, _ in up.rows] == [3]
    assert up.rows[0][1]["ufs"] == "CE|PE"
    assert len(up.sha256) == 64


def test_semicolon_delimited_file_and_header_in_any_order() -> None:
    header = "gestor;nome;telefone;perfil;empresa;ufs;canais;marcas"
    up = _parse(f"{header}\nBia;Ana;+5585991231201;vendedor;X;CE;atacarejo;jungle\n")
    assert up.rows[0][1]["nome"] == "Ana" and up.rows[0][1]["gestor"] == "Bia"


@pytest.mark.parametrize(
    ("data", "name", "code"),
    [
        (b"x", "p.xlsx", "bad_format"),
        (b"a,b\n1,2\n", "p.csv", "bad_header"),
        (b"", "p.csv", "bad_header"),
        (HEADER.encode() + b"\n", "p.csv", "empty"),
        (b"\xff\xfe\x00", "p.csv", "unreadable"),
        pytest.param(b"x" * (pi.MAX_BYTES + 1), "p.csv", "too_large", id="too_large"),
    ],
)
def test_file_errors(data: bytes, name: str, code: str) -> None:
    with pytest.raises(pi.ImportFileError) as exc:
        pi.parse_table(data, name)
    assert exc.value.code == code and exc.value.message


def test_too_many_rows() -> None:
    text = HEADER + "\n" + "\n".join([GOOD] * (pi.MAX_ROWS + 1))
    with pytest.raises(pi.ImportFileError) as exc:
        _parse(text)
    assert exc.value.code == "too_many_rows"


def test_valid_row_multiple_values_with_pipe_and_semicolon() -> None:
    (row,) = _classify([GOOD])
    assert row["situacao"] == pi.ENTRA
    assert row["ufs"] == ["CE", "PE"] and row["canais"] == ["atacarejo", "farma"]
    assert row["telefone"] == "+5585991231201"


def test_invalid_rows_name_the_fields() -> None:
    rows = _classify(
        [
            "Ana,123,vendedor,X,CE,atacarejo,jungle,Bia",
            ",+5585991231201,xx,X,ZZ,atacarejo,jungle,",
        ]
    )
    assert [r["situacao"] for r in rows] == [pi.ERRO, pi.ERRO]
    assert rows[0]["campos_com_erro"] == ["telefone"]
    assert rows[1]["campos_com_erro"] == ["nome", "perfil", "ufs", "gestor"]
    assert "telefone" in rows[0]["motivo"]


def test_duplicate_in_file_covers_ninth_digit() -> None:
    other = GOOD.replace("Ana Souza", "Ana Dois").replace("+5585991231201", "+558591231201")
    rows = _classify([GOOD, other])
    assert [r["situacao"] for r in rows] == [pi.ENTRA, pi.FICA_DE_FORA]
    assert "repetido" in rows[1]["motivo"]


def test_duplicate_in_base_by_phone_key() -> None:
    key = "5585" + "91231201"
    rows = _classify([GOOD], existing=[key])
    assert rows[0]["situacao"] == pi.FICA_DE_FORA and "cadastrado" in rows[0]["motivo"]


def test_summary_has_counts_and_no_pii() -> None:
    rows = _classify([GOOD, "Ze,1,vendedor,X,CE,atacarejo,jungle,Bia"])
    summary = pi._summary(rows)
    assert summary["entra"] == 1 and summary["erro"] == 1 and summary["total"] == 2
    assert summary["erros_por_campo"] == {"telefone": 1}
    assert "Ana" not in str(summary) and "1201" not in str(summary)
