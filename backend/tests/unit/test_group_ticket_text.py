"""Texto do aviso de ticket escalado no grupo (retorno-humano.1, RH-2; task RH-T07)."""

from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.deliveries import (
    DEFAULT_REASON,
    GROUP_MESSAGE_MAX_CHARS,
    MEDIA_REASON,
    REASON_LABELS,
    build_ticket_group_text,
    seller_label,
)

PHONE = "+5585999990000"
TEMPLATE = Settings(
    _env_file=None,  # type: ignore[call-arg]
    database_url=SecretStr("postgresql://x"),
    channel_provider="meta",
    whatsapp_app_secret=SecretStr("s"),
    whatsapp_verify_token=SecretStr("v"),
    whatsapp_access_token=SecretStr("t"),
    whatsapp_phone_number_id="1",
).text_team_group_ticket


def text(**over: Any) -> str:
    args: dict[str, Any] = {
        "template": TEMPLATE,
        "ticket_number": "J-0123",
        "kind": "question",
        "brand": "jungle",
        "escalation_reason": "no_match",
        "message": "Tem versão sem lactose?",
        "seller_name": "Ana Maria Souza",
        "seller_ufs": ["CE"],
        "link_base": "https://jarvis.positiveco.com.br/",
    }
    args.update(over)
    return build_ticket_group_text(**args)


def test_aviso_completo_no_formato_da_spec() -> None:
    assert text() == (
        "🔔 Vendedor enviou nova pergunta (ticket J-0123)\n"
        "Marca: Jungle · Motivo: sem resposta na FAQ\n"
        "Vendedor: Ana (CE)\n"
        "\n"
        "Mensagem:\n"
        "“Tem versão sem lactose?”\n"
        "\n"
        "Responda pelo Inbox: https://jarvis.positiveco.com.br/dashboard/inbox/ticket/J-0123\n"
        "(Mensagens escritas aqui no grupo não chegam ao vendedor.)"
    )


def test_nunca_leva_telefone_nem_sobrenome() -> None:
    out = text(message=f"Meu numero e {PHONE}? brincadeira", seller_name="Ana Maria Souza")
    assert "Souza" not in out and "Maria" not in out
    assert "5585999990000" not in out.replace(PHONE, "")  # so o que o proprio vendedor escreveu


@pytest.mark.parametrize(("reason", "label"), sorted(REASON_LABELS.items()))
def test_todos_os_motivos_traduzidos(reason: str, label: str) -> None:
    assert f"Motivo: {label}\n" in text(escalation_reason=reason)


@pytest.mark.parametrize("reason", [None, "motivo_novo"])
def test_motivo_ausente_ou_desconhecido(reason: str | None) -> None:
    assert f"Motivo: {DEFAULT_REASON}\n" in text(escalation_reason=reason)


@pytest.mark.parametrize("label", ["[áudio]", "[imagem]", "[vídeo]", "[documento]"])
def test_midia_sem_aspas_e_com_motivo_proprio(label: str) -> None:
    out = text(kind="media", brand=None, escalation_reason=None, message=label)
    assert f"Mensagem:\n{label}\n" in out
    assert f"Motivo: {MEDIA_REASON}" in out and "Marca: não identificada" in out


def test_pergunta_longa_cortada_em_1000() -> None:
    longa = "x" * (GROUP_MESSAGE_MAX_CHARS + 50)
    mensagem = text(message=longa).split("Mensagem:\n")[1].split("\n")[0]
    assert len(mensagem.strip("“”")) == GROUP_MESSAGE_MAX_CHARS and mensagem.endswith("…”")


def test_mensagem_apagada_pela_lgpd() -> None:
    assert "Mensagem:\n(mensagem removida)\n" in text(message=None)


@pytest.mark.parametrize(
    ("name", "ufs", "expected"),
    [
        ("Ana Maria", ["CE"], "Ana (CE)"),
        ("Ana", ["CE", "PI"], "Ana (CE/PI)"),
        ("Ana", [], "Ana"),
        ("anon:42", ["CE"], "Vendedor (CE)"),
        ("", None, "Vendedor"),
        (None, ["SP"], "Vendedor (SP)"),
    ],
)
def test_rotulo_do_vendedor(name: str | None, ufs: list[str] | None, expected: str) -> None:
    assert seller_label(name, ufs) == expected


def test_chaves_no_texto_do_vendedor_nao_quebram_o_modelo() -> None:
    out = text(message="Preço {marca} e {link}?")
    assert "“Preço {marca} e {link}?”" in out


def test_modelo_com_campo_extra_desconhecido_fica_intacto() -> None:
    out = text(template=TEMPLATE + "\n{rodape}")
    assert out.endswith("{rodape}")
