"""Escopo por marca do painel (PN-5, PN-6; spec 4.6). Objeto de valor puro, sem banco.

O cookie ``jarvis_brand`` guarda ``todas`` ou o slug de uma marca; valor invalido
vira ``todas``. ``Scope`` produz fragmentos SQL com parametros posicionais do asyncpg
(``$n``): o valor da marca nunca e interpolado no texto, so entra na lista ``params``.
Os nomes de coluna sao identificadores do codigo, validados contra uma expressao estrita.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.panel.values import BRANDS

__all__ = ["ALL_SCOPE", "MEDIA_KIND", "Scope", "parse_scope"]

ALL_SCOPE = "todas"
MEDIA_KIND = "media"
_IDENT = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?")


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"identificador SQL invalido: {name!r}")
    return name


@dataclass(frozen=True, slots=True)
class Scope:
    """Marca em foco: ``None`` = Todas as marcas; senao o slug de uma marca valida."""

    brand: str | None = None

    def __post_init__(self) -> None:
        if self.brand is not None and self.brand not in BRANDS:
            raise ValueError(f"marca desconhecida: {self.brand!r}")

    @property
    def is_all(self) -> bool:
        return self.brand is None

    @property
    def cookie_value(self) -> str:
        return ALL_SCOPE if self.brand is None else self.brand

    def _bind(self, params: list[object]) -> str:
        params.append(self.brand)
        return f"${len(params)}"

    def sql(
        self,
        column: str = "brand",
        params: list[object] | None = None,
        *,
        kind_column: str = "kind",
    ) -> str:
        """Tickets: ``brand = $n OR (brand IS NULL AND kind = 'media')``.

        Midia sem marca aparece em qualquer marca. Em Todas as marcas: ``TRUE`` e
        nenhum parametro. ``params`` recebe a marca; o ``$n`` segue o tamanho da lista.
        """
        col, kind = _ident(column), _ident(kind_column)
        if self.brand is None:
            return "TRUE"
        ph = self._bind(params if params is not None else [])
        return f"({col} = {ph} OR ({col} IS NULL AND {kind} = '{MEDIA_KIND}'))"

    def sql_faq(self, column: str = "brand", params: list[object] | None = None) -> str:
        """FAQ: ``brand IN ($n, 'todas')`` (itens de todas as marcas sempre entram)."""
        col = _ident(column)
        if self.brand is None:
            return "TRUE"
        ph = self._bind(params if params is not None else [])
        return f"{col} IN ({ph}, '{ALL_SCOPE}')"

    def sql_alert(self, payload_column: str = "payload", params: list[object] | None = None) -> str:
        """Alertas: ``payload->>'brand' = $n OR payload->>'brand' IS NULL``."""
        expr = f"{_ident(payload_column)}->>'brand'"
        if self.brand is None:
            return "TRUE"
        ph = self._bind(params if params is not None else [])
        return f"({expr} = {ph} OR {expr} IS NULL)"

    def matches_ticket(self, brand: str | None, kind: str | None) -> bool:
        """Mesma regra de ``sql`` em Python (usada no Inbox e nos testes)."""
        if self.brand is None:
            return True
        return brand == self.brand or (brand is None and kind == MEDIA_KIND)

    def shows_conversation(self, tickets: Iterable[tuple[str | None, str | None]]) -> bool:
        """PN-6: a conversa entra se ao menos um ticket (marca, tipo) casar com o escopo."""
        return self.brand is None or any(self.matches_ticket(b, k) for b, k in tickets)


def parse_scope(raw: str | None) -> Scope:
    """Valida o valor do cookie/query; ausente, ``todas`` ou invalido = Todas as marcas."""
    if raw is not None and raw in BRANDS:
        return Scope(raw)
    return Scope(None)
