"""Busca por palavras (port do piloto `piloto/faq.js`), sem IA, sem banco, sem I/O.

Etapas 1-2 da recuperacao: normaliza o texto, pontua cada pergunta cadastrada
(e suas `variations`, como formas extras) com nota F1 simetrica e aplica as
regras de marca, de UF e de "no Brasil". Funcoes puras.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

ALL_BRANDS = "todas"

STOPWORDS: frozenset[str] = frozenset(
    (
        "a o e de da do das dos em no na nos nas um uma uns umas para pra pro com sem por que "
        "qual quais quem como quando onde e é eh tem ter ser os as se ao aos me te voce vc tu "
        "eu ele ela isso esse essa este esta mais mesmo ja so tambem muito pouco hoje ainda la "
        "aqui ai entao sobre seu sua seus suas meu minha nosso nossa vocês voces"
    ).split(" ")
)

SYNONYMS: dict[str, str] = {
    "atal": "castanha",
    "tal": "castanha",
    "plantpower": "plant",
    "ppower": "plant",
    "isotonico": "isotonicos",
    "vegetal": "vegetais",
    "lider": "lidera",
    "liderando": "lidera",
    "vende": "pedem",
    "vendem": "pedem",
    "vendido": "pedem",
    "vendidos": "pedem",
    "mais-vendido": "pedem",
    "cresceu": "crescendo",
    "cresce": "crescendo",
    "participacao": "share",
    "ingrediente": "ingredientes",
    "validade": "validade",
    "vencimento": "validade",
    "gluten": "gluten",
    "calorias": "calorias",
    "kcal": "calorias",
    "posso": "pode",
    "treinar": "treino",
    "treinos": "treino",
    "treinando": "treino",
    "duram": "dura",
}

UF_CODES: frozenset[str] = frozenset(
    "ac al am ap ba ce df es go ma mg ms mt pa pb pe pi pr rj rn ro rr rs sc se sp to".split(" ")
)

# (slug de core/values.BRANDS, regex sobre o texto normalizado)
BRAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jungle", re.compile(r"\bjungle\b")),
    (
        "plant_power",
        re.compile(
            r"\bplant\b|plant ?power|ultracoffee|nitro|fibergut|defense|the one|\bprotein\b"
            r"|creatin|creapower"
        ),
    ),
    ("possible", re.compile(r"\bpossible\b")),
    ("a_tal_da_castanha", re.compile(r"tal da castanha|\batal\b|castanha")),
    ("zaya", re.compile(r"\bzaya\b")),
)

_COMBINING = re.compile(r"[̀-ͯ]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_UPPER_UF = re.compile(r"\b([A-Z]{2})\b")
_PREP_UF = re.compile(r"\b(?:em|no|na|do|da|de|pro|pra)\s+([a-z]{2})\b")
_BRASIL_SUFFIX = re.compile(r" no Brasil\??\Z")


def normalize(text: str) -> str:
    """NFD, sem acentos, minusculas, so [a-z0-9] e espacos."""
    decomposed = unicodedata.normalize("NFD", str(text))
    return _NON_ALNUM.sub(" ", _COMBINING.sub("", decomposed).lower())


def tokens(text: str) -> list[str]:
    """Tokens uteis: sinonimos, plural simples, sem stopwords nem siglas de 2 letras."""
    out: list[str] = []
    for raw in normalize(text).split():
        if raw in STOPWORDS:  # antes do plural: "quais" nao pode virar "quai" e escapar
            continue
        tok = SYNONYMS.get(raw, raw)
        if len(tok) > 4 and tok.endswith("s"):
            tok = tok[:-1]
        if len(tok) > 2 and tok not in STOPWORDS:
            out.append(tok)
    return out


def ufs_in(text: str) -> frozenset[str]:
    """UF so conta em maiusculas ("CE") ou depois de em/no/na/do/da/de/pro/pra."""
    found: set[str] = set()
    for m in _UPPER_UF.finditer(str(text)):
        if m.group(1).lower() in UF_CODES:
            found.add(m.group(1).lower())
    for m in _PREP_UF.finditer(normalize(text)):
        if m.group(1) in UF_CODES:
            found.add(m.group(1))
    return frozenset(found)


def strip_ufs(text: str) -> str:
    """Texto normalizado sem as UFs que `ufs_in` enxergaria (mesma regra, espelhada).

    Usado ao somar a pergunta anterior a um follow-up: o historico so acrescenta palavras, nunca
    o recorte de estado (senao "e no Brasil?" herda o "CE" da pergunta anterior)."""
    upper = _UPPER_UF.sub(
        lambda m: "" if m.group(1).lower() in UF_CODES else m.group(0), str(text)
    )
    return _PREP_UF.sub(
        lambda m: m.group(0).split()[0] if m.group(1) in UF_CODES else m.group(0),
        normalize(upper),
    )


def brands_in(text: str) -> list[str]:
    """Slugs de todas as marcas citadas (ordem de BRAND_PATTERNS)."""
    norm = normalize(text)
    return [slug for slug, pattern in BRAND_PATTERNS if pattern.search(norm)]


def detect_brand(text: str) -> str | None:
    """Slug da marca citada, so quando e exatamente uma."""
    found = brands_in(text)
    return found[0] if len(found) == 1 else None


def f1(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    """Nota F1 simetrica entre dois conjuntos de tokens (0..1)."""
    total = len(a) + len(b)
    return (2 * len(a & b)) / total if total else 0.0


@dataclass(frozen=True)
class Form:
    """Uma forma da pergunta cadastrada (titulo ou variacao) ja preparada."""

    tokens: frozenset[str]
    ufs: frozenset[str]
    brasil: bool


@dataclass(frozen=True)
class Searchable:
    """Item pesquisavel: `ref` estavel, marca (slug ou "todas"), titulo e variacoes."""

    ref: str
    brand: str
    title: str
    variations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    ref: str
    brand: str
    score: float


def prepare_form(text: str) -> Form:
    return Form(
        tokens=frozenset(tokens(text)),
        ufs=ufs_in(text),
        brasil=bool(_BRASIL_SUFFIX.search(str(text))),
    )


def prepare_forms(item: Searchable) -> tuple[Form, ...]:
    return tuple(prepare_form(t) for t in (item.title, *item.variations))


def score_form(form: Form, msg_tokens: frozenset[str], msg_ufs: frozenset[str]) -> float | None:
    """Nota da forma contra a mensagem; None quando uma regra de UF/Brasil a exclui."""
    if form.ufs and not (form.ufs & msg_ufs):
        return None  # recorte de outro estado
    if form.brasil and msg_ufs:
        return None  # pediu um estado: nao responde com o Brasil
    return f1(form.tokens, msg_tokens)


def _best_score(item: Searchable, msg_tokens: frozenset[str], msg_ufs: frozenset[str]) -> float:
    best = 0.0
    for form in prepare_forms(item):
        score = score_form(form, msg_tokens, msg_ufs)
        if score is not None and score > best:
            best = score
    return best


def rank(
    message: str,
    items: Iterable[Searchable],
    *,
    brand: str | None = None,
    limit: int = 8,
    floor: float = 0.0,
    brand_filter: Sequence[str] | None = None,
) -> list[Match]:
    """Candidatos ordenados por nota (desc; empate mantem a ordem de entrada).

    `brand`: marca citada na mensagem (None = detectar). Itens de outra marca
    (exceto "todas") saem. `brand_filter`: marcas permitidas ao vendedor.
    Notas abaixo de `floor` e notas zero saem.
    """
    msg_tokens = frozenset(tokens(message))
    if not msg_tokens:
        return []
    cited = brand if brand is not None else detect_brand(message)
    msg_ufs = ufs_in(message)
    allowed = None if brand_filter is None else {*brand_filter, ALL_BRANDS}
    matches: list[Match] = []
    for item in items:
        if cited and item.brand not in (cited, ALL_BRANDS):
            continue
        if allowed is not None and item.brand not in allowed:
            continue
        best = _best_score(item, msg_tokens, msg_ufs)
        if best > 0 and best >= floor:
            matches.append(Match(item.ref, item.brand, best))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


# Palavras de marca/produto (ja normalizadas): nao distinguem a pergunta, so a marca.
BRAND_WORDS: frozenset[str] = frozenset(
    (
        "jungle plant power plantpower possible castanha zaya ultracoffee nitro fibergut defense "
        "one protein creatin creapower"
    ).split(" ")
)
GENERIC_TIE_MIN_SCORE = 0.4  # nota minima do 2o candidato (outra marca) no empate generico


def _generic_tokens(text: str) -> frozenset[str]:
    return frozenset(tokens(text)) - BRAND_WORDS


def _generic_overlap(msg: frozenset[str], item: Searchable) -> int:
    """Palavras da mensagem (sem nomes de marca/produto) cobertas pela melhor forma do item."""
    return max(len(_generic_tokens(t) & msg) for t in (item.title, *item.variations))


def is_generic_brand_tie(
    message: str,
    matches: Sequence[Match],
    items: dict[str, Searchable],
    *,
    threshold: float,
    second_min: float = GENERIC_TIE_MIN_SCORE,
) -> bool:
    """Pergunta generica (CB-10): as melhores de DUAS marcas reais cobrem as mesmas palavras.

    Palavras de marca/produto nos titulos desviam a nota F1 (ex.: "Possible" e "Fibergut"), entao o
    empate olha so a cobertura das palavras da mensagem. Exige 1o >= threshold, 2o >= second_min."""
    msg = _generic_tokens(message)
    real = [m for m in matches if m.brand != ALL_BRANDS and m.ref in items]
    if not msg or not real or real[0].score < threshold:
        return False
    other = next((m for m in real[1:] if m.brand != real[0].brand), None)
    if other is None or other.score < second_min:
        return False
    first = _generic_overlap(msg, items[real[0].ref])
    return first > 0 and first == _generic_overlap(msg, items[other.ref])


def margin(matches: Sequence[Match]) -> float:
    """Diferenca de nota entre o 1o e o 2o candidato (1o sozinho: a propria nota; vazio: 0)."""
    if not matches:
        return 0.0
    second = matches[1].score if len(matches) > 1 else 0.0
    return matches[0].score - second


def is_brand_tie(matches: Sequence[Match], *, threshold: float, gap: float = 0.05) -> bool:
    """Os dois primeiros sao de marcas diferentes, diferenca < gap e nota >= threshold (CB-10)."""
    if len(matches) < 2:
        return False
    first, second = matches[0], matches[1]
    return (
        first.brand != second.brand
        and first.score - second.score < gap
        and first.score >= threshold
    )
