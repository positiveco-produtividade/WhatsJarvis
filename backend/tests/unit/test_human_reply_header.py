"""Cabecalho, autor e motivo de bloqueio da resposta da equipe (retorno-humano.1; RH-T08)."""

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.human_reply import author_label, block_reason, first_name, header_for


def settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql://x"),
        "channel_provider": "meta",
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    return Settings(_env_file=None, **{**base, **over})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Caio Franco", "Retorno da equipe (Caio) sobre sua pergunta:"),
        ("  caio  ", "Retorno da equipe (caio) sobre sua pergunta:"),
        (None, "Retorno da equipe sobre sua pergunta:"),
        ("", "Retorno da equipe sobre sua pergunta:"),
        ("admin@example.test", "Retorno da equipe sobre sua pergunta:"),
    ],
)
def test_cabecalho_com_e_sem_nome(name: str | None, expected: str) -> None:
    assert header_for(name, settings()) == expected


def test_cabecalho_vem_da_configuracao() -> None:
    cfg = settings(
        text_human_reply_header="Oi! {nome} da equipe respondeu:",
        text_human_reply_header_no_name="Oi! A equipe respondeu:",
    )
    assert header_for("Caio Franco", cfg) == "Oi! Caio da equipe respondeu:"
    assert header_for(None, cfg) == "Oi! A equipe respondeu:"


def test_primeiro_nome() -> None:
    assert first_name("Maria das Dores") == "Maria"
    assert first_name("x@y.z") is None and first_name(None) is None


def test_autor_registrado_e_o_nome_ou_o_email() -> None:
    assert author_label("Caio  Franco", "a@b.c") == "Caio Franco"
    assert author_label(None, "a@b.c") == "a@b.c"
    assert len(author_label("x" * 200, "a@b.c")) == 80


@pytest.mark.parametrize(
    ("status", "name", "reason"),
    [
        ("active", "Ana", None),
        ("revoked", "Ana", "revoked"),
        ("declined", "Ana", "declined"),
        ("pending_optin", "Ana", "pending_optin"),
        ("revoked", "anon:12", "deleted"),
    ],
)
def test_motivo_de_bloqueio(status: str, name: str, reason: str | None) -> None:
    assert block_reason(status, name) == reason
