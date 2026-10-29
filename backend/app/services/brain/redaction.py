"""Mascara de dados pessoais obvios antes do envio ao Gemini (spec 4.9, CB-13).

Funcao pura, sem I/O. Mascara telefone BR (com ou sem +55 e formatacao), e-mail,
CPF e CNPJ por padrao regular. Nomes proprios de terceiros nao sao removidos
(limite aceito, D12). Precos, quantidades e porcentagens ficam intactos.
"""

from __future__ import annotations

import re

PHONE_MASK = "[telefone]"
EMAIL_MASK = "[email]"
CPF_MASK = "[cpf]"
CNPJ_MASK = "[cnpj]"

_SEP = r"[\s.\-]?"

_CNPJ_RE = re.compile(
    r"(?<!\w)(?:\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{14})(?!\w)"
)
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_CPF_FORMATTED_RE = re.compile(r"(?<!\w)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\w)")
_PHONE_RE = re.compile(
    rf"(?<!\w)(?:\+?55{_SEP})?(?:\(0?[1-9]\d\)\s?|0?[1-9]\d{_SEP})"
    rf"(?:9{_SEP})?\d{{4}}{_SEP}\d{{4}}(?!\w)"
)
_CPF_BARE_RE = re.compile(r"(?<!\w)\d{11}(?!\w)")


def mask_pii(text: str) -> str:
    """Troca telefones, e-mails, CPF e CNPJ por marcadores fixos."""
    masked = _CNPJ_RE.sub(CNPJ_MASK, text)
    masked = _EMAIL_RE.sub(EMAIL_MASK, masked)
    masked = _CPF_FORMATTED_RE.sub(CPF_MASK, masked)
    masked = _PHONE_RE.sub(PHONE_MASK, masked)
    return _CPF_BARE_RE.sub(CPF_MASK, masked)
