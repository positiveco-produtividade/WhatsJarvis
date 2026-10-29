import pytest
from pydantic import ValidationError

from app.config import Settings
from app.paths import FRONTEND_DIR, STATIC_DIR, TEMPLATES_DIR

REQUIRED = {
    "database_url": "postgresql://jarvis:s3cr3t-db@localhost:5432/jarvis",
    "whatsapp_app_secret": "app-secret-xyz",
    "whatsapp_verify_token": "verify-token-xyz",
    "whatsapp_access_token": "access-token-xyz",
    "whatsapp_phone_number_id": "123456789",
}


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **overrides})  # type: ignore[arg-type]


def test_panel_defaults() -> None:
    s = make()
    assert s.panel_enabled is True
    assert s.panel_public_host == "jarvis.positiveco.com.br"
    assert (s.panel_session_idle_minutes, s.panel_session_absolute_hours) == (120, 12)
    assert s.iam_base_url == "https://accounts.positiveco.com.br"
    assert s.iam_whoami_cache_ttl_seconds == 30
    assert s.iam_http_timeout_seconds == 3.0
    assert s.dev_auth_email == ""
    assert s.faq_cache_max_age_seconds == 60
    assert (s.retention_months, s.retention_dry_run) == (12, True)
    assert s.unknown_first_text_display is False
    assert s.assistant_daily_limit == 200
    assert s.media_ack_enabled is True


def test_panel_secrets_masked_and_without_default() -> None:
    s = make(panel_admin_emails="a@x.com", panel_csrf_secret="csrf-xyz", gemini_api_key="g-key")
    text = repr(s)
    for secret in ("a@x.com", "csrf-xyz", "g-key"):
        assert secret not in text
    assert make().panel_csrf_secret.get_secret_value() == ""


def test_admin_emails_parsed() -> None:
    s = make(panel_admin_emails=" Caio@X.com, ,b@y.com ")
    assert s.panel_admin_email_list == ("caio@x.com", "b@y.com")


@pytest.mark.parametrize(
    "overrides", [{}, {"panel_admin_emails": "a@x.com"}, {"panel_csrf_secret": "s"}]
)
def test_production_requires_admins_and_csrf(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="PANEL_"):
        make(production=True, **overrides).validate_panel_config()


def test_production_with_both_passes() -> None:
    make(panel_admin_emails="a@x.com", panel_csrf_secret="s").validate_panel_config()


def test_non_production_or_disabled_does_not_require() -> None:
    make(production=False).validate_panel_config()
    make(production=True, panel_enabled=False).validate_panel_config()


def test_dev_auth_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        make(production=True, dev_auth_email="dev@x.com")
    assert make(production=False, dev_auth_email="dev@x.com").dev_auth_email == "dev@x.com"


@pytest.mark.parametrize("url", ["https://evil.example.com", "http://accounts.positiveco.com.br"])
def test_iam_base_outside_allowlist_fails(url: str) -> None:
    with pytest.raises(ValidationError):
        make(iam_base_url=url)


def test_panel_env_names_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANEL_CSRF_SECRET", "from-env")
    monkeypatch.setenv("RETENTION_MONTHS", "6")
    s = make()
    assert s.panel_csrf_secret.get_secret_value() == "from-env"
    assert s.retention_months == 6


def test_paths_point_to_frontend_beside_backend() -> None:
    assert TEMPLATES_DIR == FRONTEND_DIR / "templates"
    assert STATIC_DIR == FRONTEND_DIR / "static"
    assert (FRONTEND_DIR.parent / "backend" / "app").is_dir()
