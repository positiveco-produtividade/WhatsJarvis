"""Configuracao do canal (spec canal-waha.1, WA-1, WA-20 e 9.5; task WA-T02)."""

import pytest
from pydantic import ValidationError

from app.config import Settings

DB = {"database_url": "postgresql://jarvis:s3cr3t-db@localhost:5432/jarvis"}
WAHA = {
    "jarvis_waha_api_key": "waha-key-xyz",
    "jarvis_waha_hmac_key": "hmac-key-xyz",
    "jarvis_whatsapp_number": "+5500000000001",
}
META = {
    "whatsapp_app_secret": "app-secret-xyz",
    "whatsapp_verify_token": "verify-token-xyz",
    "whatsapp_access_token": "access-token-xyz",
    "whatsapp_phone_number_id": "123456789",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem nada do ambiente: o conftest liga a Meta (JARVIS_TEST_CHANNEL_PROVIDER) e poe
    chaves ficticias; aqui vale o padrao de producao."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("JARVIS_TEST_CHANNEL_PROVIDER", raising=False)
    monkeypatch.delenv("CHANNEL_PROVIDER", raising=False)


def waha(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**DB, **WAHA, **overrides})  # type: ignore[arg-type]


def test_canal_padrao_e_waha_sem_campos_da_meta() -> None:
    s = waha()
    assert s.channel_provider == "waha"
    assert s.whatsapp_app_secret.get_secret_value() == ""
    assert s.whatsapp_phone_number_id == ""


def test_padroes_do_waha_seguem_a_spec() -> None:
    s = waha()
    assert s.jarvis_waha_base_url == "http://127.0.0.1:3001"
    assert s.jarvis_waha_session == "jarvis"
    assert s.outbound_reply_only is True
    assert s.brand_session_minutes == 30
    assert "ACEITO" in s.text_optin_notice and "NÃO" in s.text_optin_notice
    assert s.text_brand_poll_start == "Sobre qual marca você quer falar?"
    assert s.text_brand_poll_question == "Sobre qual marca é a sua pergunta?"
    assert s.text_brand_selected.format(marca="Jungle").startswith("Certo, vamos falar de Jungle")


@pytest.mark.parametrize("missing", list(WAHA))
def test_waha_exige_seus_campos(missing: str) -> None:
    data = {k: v for k, v in WAHA.items() if k != missing}
    with pytest.raises(ValidationError, match=missing.upper()):
        Settings(_env_file=None, **DB, **data)  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", list(WAHA))
def test_waha_recusa_campo_em_branco(blank: str) -> None:
    with pytest.raises(ValidationError):
        waha(**{blank: "   "})


def test_waha_recusa_sessao_em_branco() -> None:
    with pytest.raises(ValidationError, match="JARVIS_WAHA_SESSION"):
        waha(jarvis_waha_session="  ")


def test_meta_exige_os_campos_da_meta_e_nao_os_do_waha() -> None:
    s = Settings(_env_file=None, channel_provider="meta", **DB, **META)  # type: ignore[arg-type]
    assert s.channel_provider == "meta"
    assert s.jarvis_waha_api_key.get_secret_value() == ""
    with pytest.raises(ValidationError, match="WHATSAPP_ACCESS_TOKEN"):
        Settings(  # type: ignore[arg-type]
            _env_file=None,
            channel_provider="meta",
            **DB,
            **{k: v for k, v in META.items() if k != "whatsapp_access_token"},
        )


def test_canal_invalido_impede_a_subida() -> None:
    with pytest.raises(ValidationError):
        waha(channel_provider="twilio")


@pytest.mark.parametrize(
    "url", ["http://10.0.0.5:3001", "https://waha.exemplo.com", "ftp://127.0.0.1:3001"]
)
def test_waha_do_jarvis_so_em_localhost(url: str) -> None:
    with pytest.raises(ValidationError, match="JARVIS_WAHA_BASE_URL"):
        waha(jarvis_waha_base_url=url)


def test_waha_do_jarvis_aceita_localhost_e_tira_barra() -> None:
    assert waha(jarvis_waha_base_url="http://localhost:3001/").jarvis_waha_base_url == (
        "http://localhost:3001"
    )


@pytest.mark.parametrize("number", ["5585999999999", "+55 85 99999-9999", "+0585999999999", "+55"])
def test_numero_do_jarvis_em_e164(number: str) -> None:
    with pytest.raises(ValidationError, match="E.164"):
        waha(jarvis_whatsapp_number=number)


def test_marca_da_conversa_zero_desliga_e_negativo_recusa() -> None:
    assert waha(brand_session_minutes=0).brand_session_minutes == 0
    with pytest.raises(ValidationError):
        waha(brand_session_minutes=-5)


def test_confirmacao_de_marca_precisa_do_marcador() -> None:
    with pytest.raises(ValidationError, match="marca"):
        waha(text_brand_selected="Certo, pode perguntar!")


def test_segredos_do_waha_mascarados() -> None:
    text = repr(waha())
    assert "waha-key-xyz" not in text and "hmac-key-xyz" not in text


def test_variaveis_em_maiusculas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB["database_url"])
    monkeypatch.setenv("JARVIS_WAHA_API_KEY", "k")
    monkeypatch.setenv("JARVIS_WAHA_HMAC_KEY", "h")
    monkeypatch.setenv("JARVIS_WHATSAPP_NUMBER", "+5500000000001")
    monkeypatch.setenv("BRAND_SESSION_MINUTES", "45")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.channel_provider == "waha" and s.brand_session_minutes == 45


def test_env_nao_escolhe_canal_jarvis_e_sempre_waha(monkeypatch: pytest.MonkeyPatch) -> None:
    """D5 revisto (2026-11-16): CHANNEL_PROVIDER no .env/ambiente nao existe mais e e ignorado."""
    monkeypatch.setenv("CHANNEL_PROVIDER", "meta")
    monkeypatch.setenv("CHANNEL_PROVIDER".lower(), "meta")
    assert waha().channel_provider == "waha"


def test_meta_so_pela_variavel_de_teste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_TEST_CHANNEL_PROVIDER", "meta")
    s = Settings(_env_file=None, **DB, **META)  # type: ignore[arg-type]
    assert s.channel_provider == "meta"


def test_arquivo_env_com_channel_provider_e_ignorado(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regressao (2026-11-16): o arquivo .env tem outro caminho de leitura que o ambiente; um
    CHANNEL_PROVIDER=meta esquecido nele subia o Jarvis como Meta."""
    env_file = tmp_path / "jarvis.env"  # type: ignore[operator]
    env_file.write_text(
        "CHANNEL_PROVIDER=meta\nchannel_provider=meta\n"
        f"DATABASE_URL={DB['database_url']}\n"
        "JARVIS_WAHA_API_KEY=k\nJARVIS_WAHA_HMAC_KEY=h\nJARVIS_WHATSAPP_NUMBER=+5500000000001\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHANNEL_PROVIDER", "meta")
    assert Settings(_env_file=env_file).channel_provider == "waha"  # type: ignore[call-arg]
