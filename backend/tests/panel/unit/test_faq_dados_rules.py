"""Regras de geracao das perguntas Nielsen e GMR (`faq_dados`), sem banco."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.panel.services import faq_dados as fd


def _nielsen(**row: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cat": "bebidas vegetais",
        "marca": "A Tal da Castanha",
        "lugar": "Brasil",
        "periodo": "set 26",
        "periodoAnt": "set 25",
        "sh": 50.0,
        "shAnt": 50.0,
        "pos": 1,
        "lojas": 100,
        "dn": 40,
        "preco": 20.0,
    }
    base.update(row)
    return {"linhas": [base]}


def test_lider_gera_pergunta_de_lideranca_com_fonte_e_periodo() -> None:
    [item] = fd.nielsen_items(_nielsen())
    assert item.question == "Quem lidera bebidas vegetais no Brasil?"
    assert item.answer == (
        "A Tal da Castanha é a marca líder de bebidas vegetais no Brasil, com 50,0% de "
        "participação em volume. É a marca que o consumidor mais leva nessa categoria: ter "
        "A Tal da Castanha na gôndola é ter o produto que o cliente já procura "
        "(Fonte: Nielsen Retail · bebidas vegetais · Total Brasil · set 26.)"
    )
    assert "Por que vender A Tal da Castanha?" in item.variations
    assert (item.brand, item.origin, item.period) == ("a_tal_da_castanha", "nielsen", "set 26")


def test_linha_de_estado_nao_gera_pergunta() -> None:
    """So recorte nacional (decisao do Caio, 2026-11-16)."""
    assert fd.nielsen_items(_nielsen(lugar="CE", sh=60.0, shAnt=50.0)) == []


def test_crescimento_de_meio_ponto_gera_pergunta() -> None:
    items = fd.nielsen_items(_nielsen(marca="Jungle", cat="isotônicos", pos=5, sh=3.2, shAnt=2.7))
    assert [i.question for i in items] == ["O Jungle está crescendo no Brasil?"]
    answer = items[0].answer
    assert "ganhou 0,5 ponto de participação no Brasil de set 25 para set 26" in answer
    assert "(de 2,7% para 3,2%)" in answer
    assert "bom momento para colocar ou ampliar Jungle na loja" in answer


def test_terceiro_lugar_sem_queda_e_com_crescimento() -> None:
    items = fd.nielsen_items(_nielsen(marca="Jungle", cat="isotônicos", pos=3, sh=3.2, shAnt=2.7))
    assert [i.question for i in items] == [
        "Qual o share do Jungle no Brasil?",
        "O Jungle está crescendo no Brasil?",
    ]
    assert "entre as 3 marcas mais vendidas de isotônicos no Brasil: é a 3ª, com 3,2%" in (
        items[0].answer
    )


@pytest.mark.parametrize(
    "row",
    [
        {"pos": 2, "sh": 10.0, "shAnt": 11.0},  # 2o lugar caindo: queda nao entra
        {"pos": 5, "sh": 3.0, "shAnt": 2.8},  # cresceu menos de 0,5
        {"pos": 1, "lojas": 9},  # amostra pequena
    ],
)
def test_o_que_nao_e_argumento_fica_de_fora(row: dict[str, object]) -> None:
    assert fd.nielsen_items(_nielsen(**row)) == []


def test_resposta_nunca_cita_preco_nem_distribuicao() -> None:
    items = fd.nielsen_items(_nielsen(preco=21.49, dn=61, pos=2, shAnt=40.0))
    assert items
    for item in items:
        assert "21,49" not in item.answer and "21.49" not in item.answer
        assert "61" not in item.answer


def test_marca_desconhecida_e_erro_de_arquivo() -> None:
    with pytest.raises(fd.DadosFileError):
        fd.nielsen_items(_nielsen(marca="Outra Marca"))


GMR = {
    "periodo": "2026-07 a 2026-09",
    "brasil": [
        {"marca": "Jungle", "top": ["SUPLEMENTO ALIM JUNG LOW UVA 12X500ML", "B", "C"]},
        {"marca": "Possible", "top": ["ALIMENTO POSS ORIG NF LV 12X1L", "B", "C"]},
    ],
    "porCanal": [{"marca": "Possible", "recorte": "FOOD", "top": ["X", "Y", "Z"]}],
    "porUF": [{"marca": "Jungle", "recorte": "SP", "top": ["D", "B", "C"]}],
}


def test_gmr_top3_no_brasil_e_por_canal_sem_estado() -> None:
    items = fd.gmr_items(GMR)
    assert [i.question for i in items] == [
        "Quais produtos do Jungle os clientes mais pedem no Brasil?",
        "Quais produtos do Possible os clientes mais pedem no Brasil?",
        "Quais produtos do Possible os clientes mais pedem no food service?",
    ]
    assert items[0].answer == (
        "Os campeões de pedido do Jungle no Brasil são: 1º Suplemento Alim Jung Low Uva "
        "12x500ml; 2º B; 3º C. São os itens que os clientes da Positive mais compram: um bom "
        "ponto de partida para montar ou reforçar o mix da loja "
        "(Fonte: GMR · pedidos de clientes Positive · Brasil · jul/26 a set/26.)"
    )
    assert "Qual o mix do Jungle no Brasil?" in items[0].variations
    assert "· Brasil · food ·" in items[2].answer
    assert {i.origin for i in items} == {"gmr"} and {i.period for i in items} == {"jul/26 a set/26"}


def test_gmr_sem_periodo_e_erro() -> None:
    with pytest.raises(fd.DadosFileError):
        fd.gmr_items({"brasil": []})


def test_pasta_sem_resumos_e_erro(tmp_path: Path) -> None:
    with pytest.raises(fd.DadosFileError):
        fd.load_dir(tmp_path)


def test_origens_carregadas_sao_os_arquivos_presentes(tmp_path: Path) -> None:
    (tmp_path / fd.GMR_FILE).write_text("{}", encoding="utf-8")
    assert fd.origins_in(tmp_path) == frozenset({"gmr"})


@pytest.mark.parametrize("word", ["R$", "concorr", "Gatorade"])
def test_texto_de_venda_sem_preco_nem_concorrente(word: str) -> None:
    rows = [
        _nielsen(pos=1)["linhas"][0],
        _nielsen(marca="Jungle", pos=3, sh=3.2, shAnt=2.7)["linhas"][0],  # type: ignore[index]
    ]
    items = fd.nielsen_items({"linhas": rows}) + fd.gmr_items(GMR)
    assert all(word.casefold() not in i.answer.casefold() for i in items)
