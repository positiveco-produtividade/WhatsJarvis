"""Leitura e regras de classificacao da planilha da FAQ (PN-T29, PN-27, PN-28)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.panel.services import faq_import as fi

HEADER = ["pergunta", "resposta", "categoria", "bot responde"]


def _xlsx(rows: Sequence[Sequence[object]], path: Path) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path.read_bytes()


def _csv(text: str, *, bom: bool = False) -> bytes:
    return (("﻿" if bom else "") + text).encode("utf-8")


def _classify(rows: Sequence[Sequence[str]], existing: Sequence[str] = ()) -> list[dict]:
    data = _csv("\n".join(";".join(r) for r in [HEADER, *rows]))
    up = fi.parse_table(data, "x.csv")
    return fi.classify_rows(up, brand="jungle", existing_keys=existing)


def test_csv_semicolon_with_bom_and_quotes() -> None:
    data = _csv('Pergunta;RESPOSTA;Assunto\n"Tem açúcar?";"Não; usa stevia.";Produto\n', bom=True)
    up = fi.parse_table(data, "m.CSV")
    assert up.column_index == {"question": 0, "answer": 1, "category": 2}
    assert up.rows == ((2, ("Tem açúcar?", "Não; usa stevia.", "Produto")),)
    assert len(up.sha256) == 64 and up.size_bytes == len(data)


def test_csv_comma_delimiter_skips_blank_lines() -> None:
    up = fi.parse_table(_csv("pergunta,resposta\n\nA?,B\n,\nC?,D\n"), "m.csv")
    assert [r[1] for r in up.rows] == [("A?", "B"), ("C?", "D")]
    assert [r[0] for r in up.rows] == [2, 3]


def test_xlsx_read(tmp_path: Path) -> None:
    data = _xlsx(
        [HEADER, ["Qual o sabor?", "Morango", "Produto", "sim"], [None, None]], tmp_path / "a.xlsx"
    )
    up = fi.parse_table(data, "a.xlsx")
    assert len(up.rows) == 1 and up.rows[0][1][:2] == ("Qual o sabor?", "Morango")


def test_xlsx_formula_is_not_evaluated(tmp_path: Path) -> None:
    data = _xlsx([["pergunta", "resposta"], ["Q?", "=1+1"]], tmp_path / "f.xlsx")
    up = fi.parse_table(data, "f.xlsx")
    assert up.rows[0][1][1] in ("", "=1+1")  # data_only: sem calculo, nunca "2" por avaliacao


def test_template_round_trip() -> None:
    up = fi.parse_table(fi.template_xlsx(), fi.TEMPLATE_FILENAME)
    assert len(up.rows) == 2
    rows = fi.classify_rows(up, brand="jungle", existing_keys=[])
    assert [r["situacao"] for r in rows] == [fi.ENTRA, fi.ENTRA]
    assert rows[1]["bot_responde"] is False and rows[1]["resposta"] == ""


@pytest.mark.parametrize(
    ("data", "name", "code"),
    [
        (b"x", "a.txt", "bad_format"),
        (b"x", "a", "bad_format"),
        (b"not a zip", "a.xlsx", "unreadable"),
        (b"PK\x03\x04garbage", "a.xlsx", "unreadable"),
        (b"\xff\xfe\x00bad", "a.csv", "unreadable"),
        (b"", "a.csv", "bad_header"),
        (b"foo;bar\n1;2\n", "a.csv", "bad_header"),
        (b"pergunta;outra\n1;2\n", "a.csv", "bad_header"),
        (b"pergunta;resposta\n", "a.csv", "empty"),
    ],
)
def test_file_errors(data: bytes, name: str, code: str) -> None:
    with pytest.raises(fi.ImportFileError) as exc:
        fi.parse_table(data, name)
    assert exc.value.code == code and exc.value.message


def test_size_cap() -> None:
    big = b"pergunta;resposta\n" + b"a" * fi.MAX_BYTES
    with pytest.raises(fi.ImportFileError) as exc:
        fi.parse_table(big, "a.csv")
    assert exc.value.code == "too_large"


def test_row_cap_csv_and_xlsx(tmp_path: Path) -> None:
    ok = "pergunta;resposta\n" + "".join(f"P{i}?;R\n" for i in range(fi.MAX_ROWS))
    assert len(fi.parse_table(_csv(ok), "a.csv").rows) == fi.MAX_ROWS
    over = ok + "extra?;R\n"
    with pytest.raises(fi.ImportFileError) as exc:
        fi.parse_table(_csv(over), "a.csv")
    assert exc.value.code == "too_many_rows"
    rows = [["pergunta", "resposta"]] + [[f"P{i}?", "R"] for i in range(fi.MAX_ROWS + 1)]
    with pytest.raises(fi.ImportFileError) as exc2:
        fi.parse_table(_xlsx(rows, tmp_path / "o.xlsx"), "o.xlsx")
    assert exc2.value.code == "too_many_rows"


async def test_load_upload_runs_in_thread(tmp_path: Path) -> None:
    up = await fi.load_upload(_csv("pergunta;resposta\nA?;B\n"), "a.csv")
    assert len(up.rows) == 1
    with pytest.raises(fi.ImportFileError):
        await fi.load_upload(b"x" * (fi.MAX_BYTES + 1), "a.csv")


def test_entra_with_category_variants() -> None:
    rows = _classify(
        [["A?", "R", "Preço", "sim"], ["B?", "R", "argumento", ""], ["C?", "R", "", ""]]
    )
    assert [r["categoria"] for r in rows] == ["PRECO", "ARGUMENTO", "PRODUTO"]
    assert [r["conferir_categoria"] for r in rows] == [False, False, True]
    assert all(r["situacao"] == fi.ENTRA for r in rows)


def test_unknown_category_defaults_and_flags() -> None:
    (row,) = _classify([["A?", "R", "Mídia", ""]])  # MIDIA nao e categoria da FAQ
    assert row["categoria"] == "PRODUTO" and row["conferir_categoria"] is True


def test_bot_flag_rules() -> None:
    rows = _classify([["A?", "R", "", "não"], ["B?", "R", "", "Sim"], ["C?", "", "", "sim"]])
    assert [r["bot_responde"] for r in rows] == [False, True, False]
    assert all(r["situacao"] == fi.ENTRA for r in rows)


@pytest.mark.parametrize(
    ("question", "answer", "text"),
    [
        ("", "R", "Pergunta vazia"),
        ("x" * 501, "R", "mais de 500"),
        ("A?", "ligue 85 99999-1234", "telefone"),
        ("A?", "escreva a@b.com", "e-mail"),
        ("A?", "veja https://x.com/y", "link"),
    ],
)
def test_error_rows(question: str, answer: str, text: str) -> None:
    (row,) = _classify([[question, answer, "", ""]])
    assert row["situacao"] == fi.ERRO and text in row["motivo"]


def test_existing_and_repeated_stay_out() -> None:
    rows = _classify(
        [
            ["Qual o sabor?", "R", "", ""],
            ["qual o sabor?", "R", "", ""],
            ["Outra?", "R", "", ""],
            ["OUTRA?", "R", "", ""],
        ],
        existing=["qual o sabor?"],
    )
    assert [r["situacao"] for r in rows] == [
        fi.FICA_DE_FORA,
        fi.FICA_DE_FORA,
        fi.ENTRA,
        fi.FICA_DE_FORA,
    ]
    assert rows[0]["motivo"] == "Já existe na FAQ."
    assert rows[3]["motivo"] == "Repetida na própria planilha."


def test_errored_row_does_not_block_later_duplicate() -> None:
    rows = _classify([["A?", "ligue 85 99999-1234", "", ""], ["A?", "R", "", ""]])
    assert [r["situacao"] for r in rows] == [fi.ERRO, fi.ENTRA]


def test_line_numbers_follow_spreadsheet() -> None:
    rows = _classify([["A?", "R", "", ""], ["B?", "R", "", ""]])
    assert [r["linha"] for r in rows] == [2, 3]
