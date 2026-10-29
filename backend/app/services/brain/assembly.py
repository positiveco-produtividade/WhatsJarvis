"""Montagem da resposta do cerebro (spec 4.4 etapa 6, CB-11, CB-24, D9 revisto).

Funcao pura, sem I/O. Divide o texto por frase em partes de ate `PART_MAX_CHARS`
caracteres, quantas forem precisas: nada e cortado. Frase maior que o limite quebra em
limite de palavra; palavra maior que o limite, em corte duro (ultimo recurso). A
linha de fonte e a assinatura do agente vao so na ultima parte, que (com elas) tambem
respeita o limite, e portanto cabe no corpo interativo de 1024 dos botoes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PART_MAX_CHARS = 700
INTERACTIVE_BODY_MAX_CHARS = 1024
DEFAULT_AGENT = "Jarvis"
SOURCE_PREFIX = "Fonte:"

# marca (slug de app.core.values.BRANDS) -> assinatura do piloto (D10)
AGENT_SIGNATURES: dict[str, str] = {
    "jungle": "Duda · Jungle",
    "plant_power": "Léo · Plant Power",
    "possible": "Nico · Possible",
    "a_tal_da_castanha": "Rita · A Tal da Castanha",
    "zaya": "Tati · Zaya",
}

_SENTENCE_RE = re.compile(r".+?(?:[.!?…]+(?=\s)|\n+|$)\s*", re.DOTALL)
_MIN_BODY_ROOM = 100


def signature_for(brands: Iterable[str] | None) -> str:
    """Assinatura `— Agente · Marca`; Jarvis se a marca e ausente, mista ou `todas`."""
    unique = {b for b in (brands or ()) if b}
    if len(unique) == 1:
        name = AGENT_SIGNATURES.get(next(iter(unique)))
        if name:
            return f"— {name}"
    return f"— {DEFAULT_AGENT}"


def _split_long(sentence: str, limit: int) -> list[str]:
    """Quebra um trecho > limite em limite de palavra, sem perder caracteres."""
    chunks: list[str] = []
    rest = sentence
    while len(rest) > limit:
        cut = rest.rfind(" ", 1, limit + 1)
        if cut <= 0:
            cut = limit  # palavra maior que o limite: corte duro
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


def _pack(text: str, limit: int) -> list[str]:
    """Agrupa frases em partes de ate `limit`, sem cortar; bordas sem espaco."""
    pieces: list[str] = []
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0)
        pieces.extend(_split_long(sentence, limit) if len(sentence) > limit else [sentence])
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current.rstrip()) + len(piece.rstrip()) > limit:
            parts.append(current)
            current = ""
        current += piece
    if current:
        parts.append(current)
    # tira espacos so nas bordas: junta-las nao muda o texto util
    return [p.strip() for p in parts if p.strip()]


def assemble(
    text: str,
    *,
    brands: Iterable[str] | None = None,
    source_line: str | None = None,
    limit: int = PART_MAX_CHARS,
) -> tuple[str, ...]:
    """Devolve as partes finais: a ultima leva fonte (se houver) e assinatura."""
    body = text.strip()
    suffix_lines: list[str] = []
    if source_line and source_line.strip() and SOURCE_PREFIX not in body:
        suffix_lines.append(source_line.strip())
    suffix_lines.append(signature_for(brands))
    suffix = "\n\n" + "\n".join(suffix_lines)
    if not body:
        return (suffix.strip(),)

    parts = _pack(body, limit)
    if len(parts[-1]) + len(suffix) <= limit:
        parts[-1] += suffix
        return tuple(parts)
    room = limit - len(suffix)
    if room >= _MIN_BODY_ROOM:
        parts = parts[:-1] + _pack(parts[-1], room)
        parts[-1] += suffix
    else:
        parts.append(suffix.strip())  # sufixo enorme: vai sozinho, nada e cortado
    return tuple(parts)
