"""Porta da fonte de conhecimento do cerebro (CB-27): FAQ hoje, banco proprio depois.

O restante do cerebro (intents, prompt, fidelidade, tickets) so conhece estes tipos.
Trocar a fonte = novo adaptador que implemente `KnowledgeSource.search`. Nao importa `app.panel`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.brain.matching import Searchable


@dataclass(frozen=True)
class KnowledgeItem:
    """Unidade recuperada, independente de FAQ."""

    ref: str  # "faq:123" (fonte:id); estavel para rastro
    version: int
    brand: str  # slug de core/values.BRANDS ou "todas"
    category: str  # PRODUTO | PRECO | ARGUMENTO | MATERIAL | CAMPANHA
    title: str  # pergunta cadastrada / rotulo do registro
    text: str  # texto aprovado ("(Fonte: ...)" ja incluso quando houver)
    origin: str  # guia|manual|import|seed|nielsen|gmr (informativo)
    answerable: bool  # False = bot_responde=false ou sem texto
    variations: tuple[str, ...] = ()  # formas extras da pergunta (entram na busca)


@dataclass(frozen=True)
class Scored:
    item: KnowledgeItem
    score: float  # 0..1 da busca por palavras


class KnowledgeSource(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        brands: Sequence[str],
        limit: int,
        floor: float,
        conn: Any | None = None,
    ) -> list[Scored]: ...


def to_searchable(item: KnowledgeItem) -> Searchable:
    """Mapeia o item para o tipo da busca por palavras (`ref` estavel, titulo e variacoes)."""
    return Searchable(
        ref=item.ref, brand=item.brand, title=item.title, variations=item.variations
    )
