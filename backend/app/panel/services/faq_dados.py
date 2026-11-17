"""Carga das perguntas Nielsen e GMR na FAQ a partir dos resumos de ``docs/designs/dados``.

Mesmas regras do piloto (``piloto/faq.js``: ``itensNielsen``/``itensGmr``) e do PRD
(Decision Log 2026-10-22 e 2026-10-29); so entra o que serve de argumento de venda:

- So o recorte nacional (decisao do Caio, 2026-11-16): nada por estado.
- Nielsen (Total Brasil): lider (1o lugar); 2o/3o lugar sem queda de 0,5 ponto ou mais;
  crescimento de 0,5 ponto ou mais. Amostra com menos de 10 lojas fica de fora. Quedas,
  distribuicao e preco medio nunca entram (uso interno).
- GMR: os 3 produtos mais pedidos no Brasil, por marca e por canal (recorte nacional). Sem R$,
  volume ou nome de cliente.

Toda resposta termina com ``(Fonte: ... · periodo.)``: a Nielsen aprovou o repasse aos
vendedores sempre com fonte e periodo. Os itens entram com ``origin='nielsen'``/``'gmr'``,
``origin_ref`` = periodo (selo no painel) e categoria ARGUMENTO. Idempotente por
``(brand, question_key)``, como a carga do Guia (``faq_seed``). A carga substitui a anterior:
item ativo da mesma origem com texto, variacoes ou periodo diferente e atualizado (nova
versao); item ativo de uma origem carregada que nao foi gerado de novo e desativado (vai para
Desativadas, sem apagar). Respostas em tom de venda de comercial para comercial (decisao do
Caio, 2026-11-16), com variacoes de como o vendedor pergunta. Nunca grava texto de pergunta
ou resposta em log ou excecao: so contagens e posicoes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.clock import Clock
from app.panel.services import faq_admin
from app.panel.services import faq_validation as fv
from app.panel.services.faq_seed import BRAND_MAP
from app.services import audit

__all__ = [
    "NIELSEN_FILE",
    "GMR_FILE",
    "DadosFileError",
    "DadosItem",
    "DadosResult",
    "load_dir",
    "origins_in",
    "nielsen_items",
    "gmr_items",
    "seed",
]

NIELSEN_FILE = "nielsen-resumo.json"
GMR_FILE = "gmr-resumo.json"
CATEGORY = "ARGUMENTO"
MIN_STORES = 10
MIN_GAIN = 0.5
_MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
NATIONAL = "Brasil"
_CHANNELS = {
    "VAREJO TRADICIONAL": "no varejo tradicional",
    "VAREJO ESPECIALIZADO": "no varejo especializado",
    "FOOD": "no food service",
}


class DadosFileError(Exception):
    """Arquivo ilegivel ou fora do formato esperado (mensagem sem conteudo do arquivo)."""


@dataclass(frozen=True, slots=True)
class DadosItem:
    brand: str
    origin: str  # nielsen | gmr
    period: str  # vai para origin_ref (selo do painel)
    question: str
    answer: str
    variations: tuple[str, ...] = ()  # como o vendedor pergunta (entram na busca)


@dataclass(frozen=True, slots=True)
class DadosResult:
    total: int = 0
    inserted: int = 0
    skipped: int = 0  # ja existiam com o mesmo texto
    updated: int = 0  # ja existiam com texto ou periodo diferente
    deactivated: int = 0  # da carga anterior que sairam desta
    failed: tuple[int, ...] = field(default_factory=tuple)  # posicoes (1..n) recusadas
    by_origin: dict[str, int] = field(default_factory=dict)  # inseridas por origem

    @property
    def ok(self) -> bool:
        return not self.failed


# ------------------------------------------------------------------------------ texto


def _o(brand: str) -> str:
    return brand if brand.startswith("A Tal") else f"O {brand}"


def _do(brand: str) -> str:
    return f"da {brand}" if brand.startswith("A Tal") else f"do {brand}"


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _product(name: str) -> str:
    """Descricao do SAP em caixa de titulo (ate haver o nome comercial)."""
    text = re.sub(r"(^|[\s/(])(\S)", lambda m: m.group(1) + m.group(2).upper(), name.lower())
    text = re.sub(r"\b(\d+)X(\d)", r"\1x\2", text, flags=re.IGNORECASE)
    return re.sub(r"\bAtdc\b", "ATDC", text)


def _gmr_period(raw: str) -> str:
    return re.sub(
        r"(\d{4})-(\d\d)", lambda m: f"{_MONTHS[int(m.group(2)) - 1]}/{m.group(1)[2:]}", raw
    )


def _brand_key(name: Any, position: int) -> str:
    brand = BRAND_MAP.get(str(name).strip().casefold()) if isinstance(name, str) else None
    if brand is None:
        raise DadosFileError(f"linha {position}: marca desconhecida")
    return brand


# ---------------------------------------------------------------------------- geracao


def _nielsen_row(position: int, row: Any) -> list[DadosItem]:
    if not isinstance(row, dict):
        raise DadosFileError(f"nielsen linha {position}: formato invalido")
    try:
        marca, cat, lugar = str(row["marca"]), str(row["cat"]), str(row["lugar"])
        period, previous = str(row["periodo"]), row.get("periodoAnt")
        share, share_prev, pos = float(row["sh"]), row.get("shAnt"), int(row["pos"])
        stores = row.get("lojas")
    except (KeyError, TypeError, ValueError) as exc:
        raise DadosFileError(f"nielsen linha {position}: campo ausente ou invalido") from exc
    brand = _brand_key(marca, position)
    if lugar != NATIONAL or (stores is not None and int(stores) < MIN_STORES):
        return []
    fonte = f"Nielsen Retail · {cat} · Total Brasil · {period}"
    delta = share - float(share_prev) if share_prev is not None else 0.0
    out: list[DadosItem] = []

    def add(question: str, answer: str, variations: tuple[str, ...]) -> None:
        out.append(
            DadosItem(brand, "nielsen", period, question, f"{answer} (Fonte: {fonte}.)", variations)
        )

    # Texto de venda de comercial para comercial (decisao do Caio, 2026-11-16): o numero e o
    # fato; a frase seguinte e o convite a comprar, sem preco e sem citar outra marca.
    why = (f"Por que vender {marca}?", f"{marca} vende bem?", f"Por que ter {marca} na loja?")
    if pos == 1:
        add(
            f"Quem lidera {cat} no Brasil?",
            f"{marca} é a marca líder de {cat} no Brasil, com {_num(share)}% de participação "
            f"em volume. É a marca que o consumidor mais leva nessa categoria: ter {marca} na "
            "gôndola é ter o produto que o cliente já procura",
            why,
        )
    if 2 <= pos <= 3 and delta > -MIN_GAIN:
        add(
            f"Qual o share {_do(marca)} no Brasil?",
            f"{marca} está entre as 3 marcas mais vendidas de {cat} no Brasil: é a {pos}ª, com "
            f"{_num(share)}% de participação em volume. O consumidor já conhece e procura, "
            "então vale ter no mix da loja",
            why,
        )
    if share_prev is not None and round(delta, 6) >= MIN_GAIN:
        pontos = "pontos" if delta >= 1.95 else "ponto"
        add(
            f"{_o(marca)} está crescendo no Brasil?",
            f"Sim. {marca} ganhou {_num(delta)} {pontos} de participação no Brasil de {previous} "
            f"para {period} (de {_num(float(share_prev))}% para {_num(share)}%). Marca em "
            f"crescimento traz cliente novo para a categoria: é um bom momento para colocar ou "
            f"ampliar {marca} na loja",
            (f"{marca} está vendendo mais?", f"{marca} está ganhando mercado?"),
        )
    return out


def nielsen_items(data: Any) -> list[DadosItem]:
    rows = data.get("linhas") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise DadosFileError("nielsen: arquivo sem a lista 'linhas'")
    return [item for n, row in enumerate(rows, start=1) for item in _nielsen_row(n, row)]


def _top(row: Any, where: str, position: int) -> tuple[str, list[str]]:
    if not isinstance(row, dict) or not isinstance(row.get("top"), list) or not row["top"]:
        raise DadosFileError(f"gmr {where} linha {position}: formato invalido")
    return str(row.get("marca", "")), [str(p) for p in row["top"]]


def gmr_items(data: Any) -> list[DadosItem]:
    if not isinstance(data, dict) or not isinstance(data.get("periodo"), str):
        raise DadosFileError("gmr: arquivo sem 'periodo'")
    period = _gmr_period(data["periodo"])
    out: list[DadosItem] = []

    def add(marca: str, top: list[str], em: str, local: str, position: int) -> None:
        lista = "; ".join(f"{i}º {_product(p)}" for i, p in enumerate(top, start=1))
        out.append(
            DadosItem(
                _brand_key(marca, position),
                "gmr",
                period,
                f"Quais produtos {_do(marca)} os clientes mais pedem {em}?",
                f"Os campeões de pedido {_do(marca)} {em} são: {lista}. São os itens que os "
                "clientes da Positive mais compram: um bom ponto de partida para montar ou "
                "reforçar o mix da loja "
                f"(Fonte: GMR · pedidos de clientes Positive · {local} · {period}.)",
                (
                    f"Qual o mix {_do(marca)} {em}?",
                    f"O que comprar {_do(marca)} {em}?",
                    f"Quais os mais vendidos {_do(marca)} {em}?",
                ),
            )
        )

    for n, row in enumerate(data.get("brasil") or [], start=1):
        marca, top = _top(row, "brasil", n)
        add(marca, top, "no Brasil", NATIONAL, n)
    for n, row in enumerate(data.get("porCanal") or [], start=1):
        marca, top = _top(row, "canal", n)
        recorte = str(row.get("recorte", ""))
        em = _CHANNELS.get(recorte, f"no canal {recorte.lower()}")
        add(marca, top, em, f"{NATIONAL} · {recorte.lower()}", n)
    # porUF fica de fora: so recorte nacional (decisao do Caio, 2026-11-16).
    return out


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DadosFileError(f"{path.name}: nao foi possivel ler como JSON UTF-8") from exc


def origins_in(directory: Path) -> frozenset[str]:
    """Origens com resumo na pasta: so elas sao substituidas pela carga."""
    files = (("nielsen", NIELSEN_FILE), ("gmr", GMR_FILE))
    return frozenset(origin for origin, name in files if (directory / name).is_file())


def load_dir(directory: Path) -> list[DadosItem]:
    """Le os resumos presentes na pasta (pelo menos um). Erro de formato -> ``DadosFileError``."""
    nielsen, gmr = directory / NIELSEN_FILE, directory / GMR_FILE
    if not nielsen.is_file() and not gmr.is_file():
        raise DadosFileError(f"nenhum resumo ({NIELSEN_FILE} ou {GMR_FILE}) na pasta")
    items: list[DadosItem] = []
    if nielsen.is_file():
        items += nielsen_items(_read(nielsen))
    if gmr.is_file():
        items += gmr_items(_read(gmr))
    return items


# ----------------------------------------------------------------------------- gravacao


async def _active_items(conn: Any) -> dict[tuple[str, str], Any]:
    rows = await conn.fetch(
        "SELECT id, brand, question_key, answer, variations, origin, origin_ref, version "
        "FROM content.faq_items WHERE active"
    )
    return {(r["brand"], r["question_key"]): r for r in rows}


def _same(row: Any, item: DadosItem) -> bool:
    return (
        row["answer"] == item.answer
        and tuple(row["variations"] or ()) == item.variations
        and row["origin_ref"] == item.period
    )


def _input(item: DadosItem) -> faq_admin.FaqInput:
    return faq_admin.FaqInput(
        brand=item.brand,
        category=CATEGORY,
        question=item.question,
        answer=item.answer,
        variations=item.variations,
    )


async def _refresh(
    conn: Any, row: Any, item: DadosItem, *, actor: str, clock: Clock | None
) -> bool:
    """Atualiza texto, variacoes e periodo de um item desta origem (nova versao + auditoria)."""
    result = await faq_admin.update_item(
        conn, row["id"], _input(item), expected_version=row["version"], actor=actor, clock=clock
    )
    if not result.ok:
        return False
    if row["origin_ref"] != item.period:
        await conn.execute(
            "UPDATE content.faq_items SET origin_ref = $2 WHERE id = $1", row["id"], item.period
        )
    return True


async def _deactivate_missing(
    conn: Any,
    origins: frozenset[str],
    keep: set[tuple[str, str]],
    *,
    actor: str,
    clock: Clock | None,
) -> int:
    """Desativa os itens ativos dessas origens que a carga nao gerou de novo."""
    if not origins:
        return 0
    rows = await conn.fetch(
        "SELECT id, brand, question_key FROM content.faq_items "
        "WHERE active AND origin = ANY($1::text[]) ORDER BY id",
        sorted(origins),
    )
    count = 0
    for row in rows:
        if (row["brand"], row["question_key"]) in keep:
            continue
        result = await faq_admin.deactivate_item(conn, row["id"], actor=actor, clock=clock)
        count += 1 if result.ok else 0
    return count


async def seed(
    conn: Any,
    items: Sequence[DadosItem],
    *,
    actor: str,
    origins: frozenset[str] | None = None,
    clock: Clock | None = None,
) -> DadosResult:
    """Grava os itens que faltam, atualiza os desta origem cujo texto ou periodo mudou e
    desativa os que sairam da base das ``origins`` carregadas (padrao: as origens dos itens).
    Chamador decide a transacao (desfaz tudo se ``not ok``)."""
    active = await _active_items(conn)
    keep = {(item.brand, fv.question_key(item.question)) for item in items}
    inserted = skipped = updated = 0
    failed: list[int] = []
    by_origin: dict[str, int] = {}
    for position, item in enumerate(items, start=1):
        row = active.get((item.brand, fv.question_key(item.question)))
        if row is not None:
            if row["origin"] != item.origin or _same(row, item):
                skipped += 1  # igual, ou item de outra origem (Guia, manual): nao mexe
            elif await _refresh(conn, row, item, actor=actor, clock=clock):
                updated += 1
            else:
                failed.append(position)
            continue
        result = await faq_admin.create_item(
            conn,
            _input(item),
            actor=actor,
            clock=clock,
            origin=item.origin,
            origin_ref=item.period,
            change_kind="import",
        )
        if not result.ok or result.item is None:
            failed.append(position)
            continue
        active[(item.brand, result.item.question_key)] = {"origin": item.origin}
        inserted += 1
        by_origin[item.origin] = by_origin.get(item.origin, 0) + 1
    if failed:
        return DadosResult(len(items), inserted, skipped, updated, 0, tuple(failed), by_origin)
    loaded = origins if origins is not None else frozenset(i.origin for i in items)
    deactivated = await _deactivate_missing(conn, loaded, keep, actor=actor, clock=clock)
    summary = DadosResult(len(items), inserted, skipped, updated, deactivated, (), by_origin)
    if inserted or updated or deactivated:
        await audit.write(
            conn,
            "cli",
            actor,
            "faq.dados",
            "faq_dados",
            "nielsen+gmr",
            None,
            {
                "total": summary.total,
                "inserted": inserted,
                "skipped": skipped,
                "updated": updated,
                "deactivated": deactivated,
                **by_origin,
            },
            clock=clock,
        )
    return summary
