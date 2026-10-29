"""Formatacao PT-BR e fuso America/Sao_Paulo (PN-12, PN-59, PN-31). Funcoes puras.

Datas sempre ``dd/mm/aaaa``; horas ``HH:MM``; numeros com ``.`` de milhar e ``,``
decimal. Datas sem fuso sao recusadas (``ValueError``): o banco devolve
``timestamptz`` e uma data ingenua esconderia um erro de fuso.
"""

import re
import unicodedata
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SP_TZ = ZoneInfo("America/Sao_Paulo")
EMPTY = "—"

DAY_TODAY = "Hoje"
DAY_YESTERDAY = "Ontem"


def to_sp(value: datetime) -> datetime:
    """Converte para America/Sao_Paulo; recusa data sem fuso."""
    if value.tzinfo is None:
        raise ValueError("datetime sem fuso horario")
    return value.astimezone(SP_TZ)


def format_date(value: datetime | None) -> str:
    """``dd/mm/aaaa`` em Sao Paulo."""
    return to_sp(value).strftime("%d/%m/%Y") if value else EMPTY


def format_time(value: datetime | None) -> str:
    """``HH:MM`` em Sao Paulo."""
    return to_sp(value).strftime("%H:%M") if value else EMPTY


def format_datetime(value: datetime | None) -> str:
    """``dd/mm/aaaa HH:MM`` em Sao Paulo."""
    return to_sp(value).strftime("%d/%m/%Y %H:%M") if value else EMPTY


def format_short_datetime(value: datetime | None) -> str:
    """``dd/mm HH:MM`` (listas compactas, como no prototipo)."""
    return to_sp(value).strftime("%d/%m %H:%M") if value else EMPTY


def day_label(value: datetime, now: datetime | None = None) -> str:
    """Separador de dia do chat: ``Hoje``, ``Ontem`` ou ``dd/mm/aaaa`` (dia de Sao Paulo)."""
    day = to_sp(value).date()
    today = to_sp(now if now is not None else datetime.now(UTC)).date()
    delta = (today - day).days
    if delta == 0:
        return DAY_TODAY
    if delta == 1:
        return DAY_YESTERDAY
    return day.strftime("%d/%m/%Y")


def is_new_day(previous: datetime | None, current: datetime) -> bool:
    """Verdadeiro quando ``current`` cai em outro dia de Sao Paulo que ``previous``."""
    return previous is None or to_sp(previous).date() != to_sp(current).date()


def response_time_label(seconds: float) -> str:
    """Tempo de resposta: ``45 s``, ``12 min``, ``2 h`` ou ``2 h 5 min`` (arredonda para baixo)."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{total // 60} min"
    hours, minutes = total // 3600, total % 3600 // 60
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def responded_in(question_at: datetime, answer_at: datetime) -> str:
    """``respondeu em X`` contado da pergunta do vendedor ate a resposta."""
    return f"respondeu em {response_time_label((answer_at - question_at).total_seconds())}"


def format_int(value: int | None) -> str:
    """``1.234`` (milhar com ponto)."""
    return f"{value:,}".replace(",", ".") if value is not None else EMPTY


def format_decimal(value: float | None, places: int = 1) -> str:
    """``1.234,5`` (virgula decimal)."""
    if value is None:
        return EMPTY
    text = f"{value:,.{places}f}"
    return text.replace(",", "#").replace(".", ",").replace("#", ".")


def format_percent(value: float | None, places: int = 0) -> str:
    """``87%`` / ``87,5%``; ``value`` em 0..100."""
    return EMPTY if value is None else f"{format_decimal(value, places)}%"


def fold_text(text: str) -> str:
    """Minusculas sem acento, mantendo o tamanho (um caractere vira um caractere)."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        base = unicodedata.normalize("NFD", ch)[0].lower()
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


def digits_only(text: str | None) -> str:
    """So os digitos (busca de telefone digitado com mascara)."""
    return re.sub(r"\D", "", text or "")


def highlight_parts(text: str, query: str) -> list[tuple[str, bool]]:
    """Divide ``text`` em trechos ``(texto, destacado)``; casa sem acento nem caixa.

    Devolve dados, nao HTML: o template escapa cada trecho (autoescape).
    """
    needle = fold_text(query.strip())
    plain = unicodedata.normalize("NFC", text)
    if not needle:
        return [(plain, False)] if plain else []
    folded = fold_text(plain)
    parts: list[tuple[str, bool]] = []
    pos = 0
    while (hit := folded.find(needle, pos)) != -1:
        if hit > pos:
            parts.append((plain[pos:hit], False))
        parts.append((plain[hit : hit + len(needle)], True))
        pos = hit + len(needle)
    if pos < len(plain):
        parts.append((plain[pos:], False))
    return parts
