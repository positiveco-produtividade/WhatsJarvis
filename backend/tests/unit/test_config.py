import pytest
from pydantic import ValidationError

from app.config import Settings

REQUIRED = {
    "database_url": "postgresql://jarvis:s3cr3t-db@localhost:5432/jarvis",
    "whatsapp_app_secret": "app-secret-xyz",
    "whatsapp_verify_token": "verify-token-xyz",
    "whatsapp_access_token": "access-token-xyz",
    "whatsapp_phone_number_id": "123456789",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """O CI exporta DATABASE_URL; o teste de campo ausente precisa de ausencia real."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isola o ambiente: o CI exporta DATABASE_URL; o teste de campo ausente exige ausencia real."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **overrides})  # type: ignore[arg-type]


def test_defaults() -> None:
    s = make()
    assert s.team_group_enabled is False
    assert s.unknown_first_text_store is False
    assert s.media_ack_enabled is True
    assert s.whatsapp_graph_version == "v25.0"
    assert s.whatsapp_api_base == "https://graph.facebook.com"
    assert s.max_webhook_payload_bytes == 65536
    assert s.rate_limit_per_hour == 60
    assert s.rate_limit_window_minutes == 60
    assert s.optin_reminder_interval_hours == 24
    assert s.email_max_attempts == 6
    assert s.send_failure_alert_threshold == 3
    assert s.send_failure_alert_window_minutes == 30
    assert s.rl_webhook_max == 600
    assert s.rl_webhook_window == 60
    assert s.production is True
    assert s.log_level == "INFO"
    assert s.notice_version == "rascunho-2026-10-19"
    assert s.waha_base_url == "http://127.0.0.1:3000"
    assert s.waha_session == "default"


def test_text_defaults_follow_spec() -> None:
    s = make()
    assert s.text_under_construction.startswith("Oi! Ainda estou em construção")
    assert s.text_optin_confirmed == (
        "Obrigado! Seu aceite foi registrado. Já pode me mandar suas dúvidas."
    )
    assert "fale com seu gestor" in s.text_optin_declined
    assert "Toque em um dos botões" in s.text_optin_reminder
    assert "limite de mensagens por hora" in s.text_rate_limit
    assert "não receberá mais avisos" in s.text_opt_out_confirmed
    assert "avisei a equipe" in s.text_media_ack


def test_text_override() -> None:
    assert make(text_media_ack="outro").text_media_ack == "outro"


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_fails(missing: str) -> None:
    data = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)  # type: ignore[arg-type]


@pytest.mark.parametrize("empty", list(REQUIRED))
def test_empty_required_fails(empty: str) -> None:
    with pytest.raises(ValidationError):
        make(**{empty: "  "})


def test_repr_masks_secrets() -> None:
    s = make(
        waha_api_key="waha-key-xyz",
        resend_api_key="resend-key-xyz",
        alert_recipient_email="alvo@example.com",
    )
    text = repr(s) + str(s)
    for secret in (
        "s3cr3t-db",
        "app-secret-xyz",
        "verify-token-xyz",
        "access-token-xyz",
        "waha-key-xyz",
        "resend-key-xyz",
        "alvo@example.com",
    ):
        assert secret not in text


def test_alert_recipient_has_no_default_and_is_secret() -> None:
    s = make()
    assert s.alert_recipient_email.get_secret_value() == ""
    assert type(s.alert_recipient_email).__name__ == "SecretStr"


def test_missing_email_config_does_not_fail() -> None:
    s = make(alert_recipient_email="", resend_api_key="")
    assert s.resend_api_key.get_secret_value() == ""


def test_env_var_names_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "10")
    monkeypatch.setenv("TEAM_GROUP_ENABLED", "false")
    assert make().rate_limit_per_hour == 10


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com",
        "http://169.254.169.254",
        "https://graph.facebook.com.evil.com",
        "ftp://graph.facebook.com",
    ],
)
def test_graph_host_outside_allowlist_fails(url: str) -> None:
    with pytest.raises(ValidationError):
        make(whatsapp_api_base=url)


def test_graph_base_trailing_slash_trimmed() -> None:
    assert make(whatsapp_api_base="https://graph.facebook.com/").whatsapp_api_base == (
        "https://graph.facebook.com"
    )


def test_waha_host_outside_allowlist_fails() -> None:
    with pytest.raises(ValidationError):
        make(waha_base_url="http://10.0.0.5:3000")


def test_team_group_enabled_requires_key_and_chat() -> None:
    with pytest.raises(ValidationError):
        make(team_group_enabled=True)
    with pytest.raises(ValidationError):
        make(team_group_enabled=True, waha_api_key="k")
    with pytest.raises(ValidationError):
        make(team_group_enabled=True, team_group_chat_id="123@g.us")
    s = make(team_group_enabled=True, waha_api_key="k", team_group_chat_id="123@g.us")
    assert s.team_group_enabled is True


def test_backoff_list_parsed() -> None:
    assert make().email_backoff_list == (60, 300, 900, 3600, 10800)
    assert make(email_backoff_seconds="5, 10").email_backoff_list == (5, 10)


@pytest.mark.parametrize("bad", ["abc", "", "60,-1", "0"])
def test_backoff_invalid_fails(bad: str) -> None:
    with pytest.raises(ValidationError):
        make(email_backoff_seconds=bad)


def test_empty_env_vars_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "")
    monkeypatch.setenv("MEDIA_ACK_ENABLED", "")
    monkeypatch.setenv("TEAM_GROUP_ENABLED", "")
    monkeypatch.setenv("TEXT_MEDIA_ACK", "")
    s = make()
    assert s.rate_limit_per_hour == 60
    assert s.media_ack_enabled is True
    assert s.team_group_enabled is False
    assert "avisei a equipe" in s.text_media_ack


def test_empty_required_env_var_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "")
    data = {k: v for k, v in REQUIRED.items() if k != "whatsapp_phone_number_id"}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)  # type: ignore[arg-type]
