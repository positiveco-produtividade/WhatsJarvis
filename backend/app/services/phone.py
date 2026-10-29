"""Telefone: normalizacao, validacao e chave de comparacao (spec 4.4.8).

Funcao pura, sem I/O. Entrada tolerante (espacos, parenteses, hifens, ``+``);
o DDI e obrigatorio. Brasil: DDD valido, celular sempre com o 9 no canonico.
"""

import re
from dataclasses import dataclass

# Codigos de area (DDD) vigentes no Brasil, conforme o Plano de Numeracao da
# Anatel (Resolucao 263/2001 e atualizacoes; lista publica "Codigos Nacionais
# - DDD", https://www.gov.br/anatel). Conferida de memoria, sem consulta online:
# revisar contra a Anatel se um DDD novo for criado.
VALID_DDDS: frozenset[str] = frozenset(
    [f"{n}" for n in range(11, 20)]
    + ["21", "22", "24", "27", "28"]
    + ["31", "32", "33", "34", "35", "37", "38"]
    + [f"{n}" for n in range(41, 50)]
    + ["51", "53", "54", "55"]
    + [f"{n}" for n in range(61, 70)]
    + ["71", "73", "74", "75", "77", "79"]
    + [f"{n}" for n in range(81, 90)]
    + [f"{n}" for n in range(91, 100)]
)

MSG_LANDLINE = "Telefone fixo não recebe WhatsApp"
MSG_EMPTY = "Telefone vazio"
MSG_CHARS = "Telefone com caracteres inválidos"
MSG_NO_DDI = "Telefone sem DDI (use o código do país, ex.: +55)"
MSG_BAD_DDD = "DDD inexistente"
MSG_BAD_LENGTH = "Telefone com quantidade de dígitos inválida"
MSG_BAD_BR = "Número brasileiro inválido"

_ALLOWED = re.compile(r"^\+?[\d\s().\-]+$")
_BR_DDI = "55"


class InvalidPhoneError(ValueError):
    """Telefone recusado; a mensagem e o motivo, pronta para exibir."""


@dataclass(frozen=True)
class PhoneNumber:
    phone_e164: str  # canonico, com "+" (BR movel sempre com o 9)
    match_key: str  # sem "+"; celular BR de 13 digitos sem o 9o digito


def _looks_like_br_without_ddi(digits: str) -> bool:
    """10 digitos (DDD+8) ou 11 (DDD+9...) com DDD valido e sem '+': falta o DDI."""
    if digits.startswith(_BR_DDI) or digits[:2] not in VALID_DDDS:
        return False
    if len(digits) == 10:
        return True
    return len(digits) == 11 and digits[2] == "9"


def _normalize_br(national: str) -> str:
    """Recebe DDD + numero local; devolve os digitos canonicos (sem DDI)."""
    if len(national) not in (10, 11):
        raise InvalidPhoneError(MSG_BAD_LENGTH)
    ddd, local = national[:2], national[2:]
    if ddd not in VALID_DDDS:
        raise InvalidPhoneError(MSG_BAD_DDD)
    if len(local) == 9:
        if local[0] != "9":
            raise InvalidPhoneError(MSG_BAD_BR)
        return national
    if local[0] in "2345":
        raise InvalidPhoneError(MSG_LANDLINE)
    if local[0] in "6789":
        return f"{ddd}9{local}"
    raise InvalidPhoneError(MSG_BAD_BR)


def normalize_phone(raw: str) -> PhoneNumber:
    """Normaliza ``raw`` ou levanta ``InvalidPhoneError`` com o motivo."""
    text = (raw or "").strip()
    if not text:
        raise InvalidPhoneError(MSG_EMPTY)
    if not _ALLOWED.match(text):
        raise InvalidPhoneError(MSG_CHARS)
    explicit_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        raise InvalidPhoneError(MSG_EMPTY)
    if not explicit_plus and _looks_like_br_without_ddi(digits):
        raise InvalidPhoneError(MSG_NO_DDI)
    if digits.startswith("0"):
        raise InvalidPhoneError(MSG_NO_DDI)
    if digits.startswith(_BR_DDI):
        national = _normalize_br(digits[len(_BR_DDI):])
        canonical = _BR_DDI + national
        key = canonical
        if len(national) == 11:
            key = canonical[:4] + canonical[5:]
        return PhoneNumber(phone_e164="+" + canonical, match_key=key)
    if not 8 <= len(digits) <= 15:
        raise InvalidPhoneError(MSG_BAD_LENGTH)
    return PhoneNumber(phone_e164="+" + digits, match_key=digits)


def mask_phone(value: str | None) -> str:
    """Mascara para log: ``***1234`` (ultimos 4 digitos); sem PII alem disso."""
    digits = re.sub(r"\D", "", value or "")
    return "***" + digits[-4:] if digits else "***"
