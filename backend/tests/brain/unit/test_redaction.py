"""Mascara de dados pessoais (CB-13)."""

from __future__ import annotations

import pytest

from app.services.brain.redaction import mask_pii


@pytest.mark.parametrize(
    "raw",
    [
        "+55 11 98765-4321",
        "+5511987654321",
        "5511987654321",
        "(11) 98765-4321",
        "11 98765-4321",
        "11987654321",
        "11 3456-7890",
        "(11)3456-7890",
        "011 98765-4321",
    ],
)
def test_phone_masked(raw: str) -> None:
    out = mask_pii(f"me liga no {raw} depois")
    assert out == "me liga no [telefone] depois"


def test_phone_at_end_with_period() -> None:
    assert mask_pii("fale com 11 98765-4321.") == "fale com [telefone]."


def test_email_masked() -> None:
    assert mask_pii("mande para Joao.Silva+x@empresa.com.br ok") == "mande para [email] ok"


def test_cpf_formatted_and_bare() -> None:
    assert mask_pii("cpf 123.456.789-09 ok") == "cpf [cpf] ok"
    assert mask_pii("cpf 12345678909 ok") == "cpf [cpf] ok"


def test_cnpj_formatted_and_bare() -> None:
    assert mask_pii("cnpj 12.345.678/0001-95 ok") == "cnpj [cnpj] ok"
    assert mask_pii("cnpj 12345678000195 ok") == "cnpj [cnpj] ok"


def test_several_in_one_text() -> None:
    out = mask_pii("a@b.com, 11 98765-4321 e 123.456.789-09")
    assert out == "[email], [telefone] e [cpf]"


@pytest.mark.parametrize(
    "text",
    [
        "Qual o preco do achocolatado 500 ml?",
        "R$ 12,90 a unidade",
        "R$ 1.234,56 no atacado",
        "caixa com 12 unidades",
        "desconto de 15% e 7,5% de margem",
        "EAN 7891234567890 da linha nova",
        "pedido 4521 de 2026",
        "lote 2026-10-28 validade 12/2027",
        "oi, tudo bem?",
        "",
    ],
)
def test_ordinary_text_intact(text: str) -> None:
    assert mask_pii(text) == text
