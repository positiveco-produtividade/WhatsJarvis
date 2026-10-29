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


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **overrides})  # type: ignore[arg-type]


def test_brain_defaults() -> None:
    s = make()
    assert s.brain_enabled is False
    assert s.brain_monthly_budget_usd == 100
    assert s.brain_budget_alert_pct == 0.8
    assert s.brain_gemini_timeout_seconds == 5
    assert (s.brain_match_threshold, s.brain_candidate_floor, s.brain_direct_threshold) == (
        0.55,
        0.30,
        0.85,
    )
    assert s.brain_candidates_k == 8
    assert (s.brain_context_exchanges, s.brain_context_minutes) == (3, 30)
    assert s.brain_answer_mode == "grounded"
    assert s.brain_price_in_per_mtok_usd is None
    assert s.brain_price_out_per_mtok_usd is None


def test_brain_fixed_texts() -> None:
    s = make()
    expected = "Ainda não sei responder isso. Anotei a pergunta para a equipe."
    assert s.text_brain_no_answer == expected
    assert s.text_brain_rating_thanks == "Obrigado, avisei a equipe para melhorar essa resposta."
    for text in (
        s.text_brain_access_request,
        s.text_brain_refusal,
        s.text_brain_greeting,
        s.text_brain_thanks,
        s.text_brain_ask_brand,
    ):
        assert text.strip()


def test_enabled_requires_key_and_prices() -> None:
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        make(brain_enabled=True, brain_price_in_per_mtok_usd=1, brain_price_out_per_mtok_usd=2)
    with pytest.raises(ValidationError, match="BRAIN_PRICE_IN_PER_MTOK_USD"):
        make(brain_enabled=True, gemini_api_key="k", brain_price_out_per_mtok_usd=2)
    with pytest.raises(ValidationError, match="BRAIN_PRICE_OUT_PER_MTOK_USD"):
        make(brain_enabled=True, gemini_api_key="k", brain_price_in_per_mtok_usd=1)


def test_enabled_with_key_and_prices_ok() -> None:
    s = make(
        brain_enabled=True,
        gemini_api_key="k",
        brain_price_in_per_mtok_usd=0.3,
        brain_price_out_per_mtok_usd=2.5,
    )
    assert s.brain_enabled is True


def test_disabled_needs_nothing() -> None:
    assert make(brain_enabled=False).brain_enabled is False


@pytest.mark.parametrize("mode", ["grounded", "verbatim"])
def test_answer_mode_accepted(mode: str) -> None:
    assert make(brain_answer_mode=mode).brain_answer_mode == mode


@pytest.mark.parametrize("mode", ["free", "", "GROUNDED2"])
def test_answer_mode_rejected(mode: str) -> None:
    with pytest.raises(ValidationError):
        make(brain_answer_mode=mode)


@pytest.mark.parametrize(
    "overrides",
    [
        {"brain_monthly_budget_usd": 0},
        {"brain_gemini_timeout_seconds": -1},
        {"brain_candidates_k": 0},
        {"brain_budget_alert_pct": 1.5},
        {"brain_match_threshold": 0},
        {"brain_price_in_per_mtok_usd": -1},
        {"brain_candidate_floor": 0.9},  # acima do limiar de casamento
        {"brain_match_threshold": 0.9},  # acima do limiar direto
    ],
)
def test_invalid_values_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make(**overrides)


def test_env_names_are_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ANSWER_MODE", "verbatim")
    monkeypatch.setenv("BRAIN_CANDIDATES_K", "5")
    s = make()
    assert (s.brain_answer_mode, s.brain_candidates_k) == ("verbatim", 5)
