"""Mascara de telefone para exibicao no painel (PN-31). Funcao pura.

Formato do prototipo: ``+55 85 9••••-1201``. A ficha da pessoa e o unico lugar
que mostra o numero inteiro; listas, logs e tabelas usam esta mascara.
"""

import re

BULLET = "•"
EMPTY = "—"
_BR_DDI = "55"


def mask_phone_display(value: str | None) -> str:
    """Mascara o telefone: so DDI, DDD, o 9 inicial e os 4 ultimos digitos aparecem."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) < 5:
        return EMPTY
    last4 = digits[-4:]
    if digits.startswith(_BR_DDI) and len(digits) in (12, 13):
        ddd, local = digits[2:4], digits[4:]
        first = local[0] if len(local) == 9 else ""
        return f"+{_BR_DDI} {ddd} {first}{BULLET * 4}-{last4}"
    return f"+{BULLET * 4}{last4}"
