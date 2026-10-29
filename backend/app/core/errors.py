"""Excecoes tipadas do Jarvis. Mensagens nunca carregam PII nem segredos."""
from __future__ import annotations


class JarvisError(Exception):
    """Base de todos os erros esperados da aplicacao."""


class DuplicatePhone(JarvisError):
    """Telefone (phone_key) ja cadastrado."""


class InvalidSignature(JarvisError):
    """Assinatura X-Hub-Signature-256 ausente ou invalida."""


class PayloadTooLarge(JarvisError):
    """Corpo da requisicao acima do limite permitido."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"payload acima de {limit_bytes} bytes")
        self.limit_bytes = limit_bytes


class TransientDbError(JarvisError):
    """Falha transitoria de banco (conexao, deadlock, pool esgotado): vale retentar."""


class ValidationFailure(JarvisError):
    """Erro de validacao de um campo, com o campo e o motivo."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class InvalidFlagKey(ValidationFailure):
    """Chave de flag fora da lista aceita."""

    def __init__(self, key: str) -> None:
        super().__init__("flag", f"chave nao aceita: {key}")


class InvalidValue(ValidationFailure):
    """Valor fora da lista fixa (kind, motivo de revogacao, etc.)."""
