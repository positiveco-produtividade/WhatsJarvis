"""Carga inicial da FAQ a partir de ``piloto/guia.json`` (PN-30; spec 4.7).

Cada item entra por ``faq_admin.create_item`` (versao ``seed`` + auditoria do item), com
``origin='seed'``. Idempotente por ``(brand, question_key)``: o que ja existe na marca
(ativo ou nao) e pulado, entao rodar de novo insere 0. Nunca grava texto de pergunta ou
resposta em log ou excecao: so contagens e posicoes.

Mapeamento documentado (o guia nao traz categoria em 103 dos 112 itens):
- marca: Jungle -> jungle, Plant Power -> plant_power, Possible -> possible, Todas -> todas
  (as gerais);
- categoria: ``cat`` do guia (hoje so ``Argumento``) -> ARGUMENTO; sem ``cat`` -> PRODUTO
  (o guia e um guia de produtos; PRECO, MATERIAL e CAMPANHA ficam para a edicao manual);
- resposta vazia, ou que a validacao recusa (telefone, e-mail ou link no texto), entra
  SEM resposta e com ``bot_responde=false``: o time escreve a aprovada depois.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.clock import Clock
from app.panel.services import faq_admin
from app.panel.services import faq_validation as fv
from app.services import audit

__all__ = [
    "ORIGIN",
    "BRAND_MAP",
    "SeedFileError",
    "SeedItem",
    "SeedResult",
    "load_items",
    "seed",
]

ORIGIN = "seed"
DEFAULT_CATEGORY = "PRODUTO"
BRAND_MAP = {
    "jungle": "jungle",
    "plant power": "plant_power",
    "possible": "possible",
    "a tal da castanha": "a_tal_da_castanha",
    "zaya": "zaya",
    "todas": fv.ALL_BRANDS_KEY,
}
_CATEGORY_MAP = {"argumento": "ARGUMENTO"}


class SeedFileError(Exception):
    """Arquivo ilegivel ou fora do formato esperado (mensagem sem conteudo do arquivo)."""


@dataclass(frozen=True, slots=True)
class SeedItem:
    brand: str
    category: str
    question: str
    answer: str | None


@dataclass(frozen=True, slots=True)
class SeedResult:
    total: int = 0
    inserted: int = 0
    skipped: int = 0
    without_answer: int = 0
    failed: tuple[int, ...] = field(default_factory=tuple)  # posicoes (1..n) recusadas

    @property
    def ok(self) -> bool:
        return not self.failed


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_item(position: int, raw: Any) -> SeedItem:
    if not isinstance(raw, dict):
        raise SeedFileError(f"item {position}: formato invalido")
    brand = BRAND_MAP.get(_text(raw.get("marca")).casefold())
    if brand is None:
        raise SeedFileError(f"item {position}: marca desconhecida")
    category = _CATEGORY_MAP.get(_text(raw.get("cat")).casefold(), DEFAULT_CATEGORY)
    question = _text(raw.get("q"))
    if not question:
        raise SeedFileError(f"item {position}: pergunta vazia")
    return SeedItem(brand, category, question, _text(raw.get("a")) or None)


def load_items(path: Path) -> list[SeedItem]:
    """Le o guia (``{"itens": [{marca, q, a, cat?}]}``). Erro de formato -> ``SeedFileError``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SeedFileError("nao foi possivel ler o arquivo como JSON UTF-8") from exc
    raw_items = data.get("itens") if isinstance(data, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        raise SeedFileError("arquivo sem a lista 'itens'")
    return [_parse_item(n, raw) for n, raw in enumerate(raw_items, start=1)]


def _approved_answer(item: SeedItem) -> str | None:
    """A resposta so segue se existir e passar na validacao de contatos."""
    if item.answer is None or fv.find_contact(item.answer):
        return None
    return item.answer


async def _existing_keys(conn: Any) -> set[tuple[str, str]]:
    rows = await conn.fetch("SELECT brand, question_key FROM content.faq_items")
    return {(r["brand"], r["question_key"]) for r in rows}


async def _insert_one(
    conn: Any, item: SeedItem, *, actor: str, clock: Clock | None
) -> faq_admin.FaqWriteResult:
    answer = _approved_answer(item)
    data = faq_admin.FaqInput(
        brand=item.brand,
        category=item.category,
        question=item.question,
        answer=answer,
        bot_responde=answer is not None,
    )
    return await faq_admin.create_item(
        conn, data, actor=actor, clock=clock, origin=ORIGIN, change_kind="seed"
    )


async def seed(
    conn: Any, items: Sequence[SeedItem], *, actor: str, clock: Clock | None = None
) -> SeedResult:
    """Grava os itens que faltam. Chamador decide a transacao (desfaz tudo se ``not ok``)."""
    existing = await _existing_keys(conn)
    inserted = skipped = without_answer = 0
    failed: list[int] = []
    for position, item in enumerate(items, start=1):
        if (item.brand, fv.question_key(item.question)) in existing:
            skipped += 1
            continue
        result = await _insert_one(conn, item, actor=actor, clock=clock)
        if not result.ok or result.item is None:
            failed.append(position)
            continue
        existing.add((item.brand, result.item.question_key))
        inserted += 1
        without_answer += 0 if result.item.bot_responde else 1
    summary = SeedResult(len(items), inserted, skipped, without_answer, tuple(failed))
    if inserted:
        await _audit_summary(conn, summary, actor=actor, clock=clock)
    return summary


async def _audit_summary(
    conn: Any, summary: SeedResult, *, actor: str, clock: Clock | None
) -> None:
    """Uma linha ``actor_type='cli'`` da carga (as linhas por item saem de ``faq_admin``)."""
    await audit.write(
        conn,
        "cli",
        actor,
        "faq.seed",
        "faq_seed",
        ORIGIN,
        None,
        {
            "total": summary.total,
            "inserted": summary.inserted,
            "skipped": summary.skipped,
            "without_answer": summary.without_answer,
        },
        clock=clock,
    )
