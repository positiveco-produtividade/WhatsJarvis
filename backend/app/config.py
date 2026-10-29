"""Configuração do Jarvis (spec porta-fechada.1, seção 4.5).

Lida do ambiente / `backend/.env` por pydantic-settings. Segredos são `SecretStr`
(mascarados em `repr`). Os nomes das variáveis de ambiente são os da spec em
maiúsculas; pydantic-settings ignora a caixa, então os campos ficam em minúsculas.
`TEST_DATABASE_URL` não pertence a esta classe: só os testes a leem.
"""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hosts permitidos nas URLs base (evita SSRF por configuração errada).
_GRAPH_ALLOWED_HOSTS: frozenset[str] = frozenset({"graph.facebook.com"})
_WAHA_ALLOWED_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost"})
_IAM_ALLOWED_HOSTS: frozenset[str] = frozenset({"accounts.positiveco.com.br"})


def _check_host(name: str, url: str, allowed: frozenset[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in allowed:
        raise ValueError(f"{name}: host fora da allow-list")
    return url.rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # Banco e WhatsApp (obrigatórios; ausência ou vazio derruba a subida)
    database_url: SecretStr
    whatsapp_app_secret: SecretStr
    whatsapp_verify_token: SecretStr
    whatsapp_access_token: SecretStr
    whatsapp_phone_number_id: str
    whatsapp_graph_version: str = "v25.0"
    whatsapp_api_base: str = "https://graph.facebook.com"
    welcome_template_name: str = ""
    welcome_template_lang: str = "pt_BR"
    notice_version: str = "rascunho-2026-10-19"

    # Limites e regras
    max_webhook_payload_bytes: int = 65536
    rate_limit_per_hour: int = 60
    rate_limit_window_minutes: int = 60
    optin_reminder_interval_hours: int = 24
    unknown_first_text_store: bool = False
    media_ack_enabled: bool = True

    # Grupo da equipe (WAHA)
    team_group_enabled: bool = False
    waha_base_url: str = "http://127.0.0.1:3000"
    waha_api_key: SecretStr = SecretStr("")
    waha_session: str = "default"
    team_group_chat_id: str = ""

    # E-mail (Resend); vazio = entrega `skipped`, nunca derruba
    resend_api_key: SecretStr = SecretStr("")
    resend_from_email: str = ""
    alert_recipient_email: SecretStr = SecretStr("")
    panel_base_url: str = ""
    email_max_attempts: int = 6
    email_backoff_seconds: str = "60,300,900,3600,10800"
    send_failure_alert_threshold: int = 3
    send_failure_alert_window_minutes: int = 30

    # Textos fixos (spec 4.3.4)
    text_under_construction: str = (
        "Oi! Ainda estou em construção e não consigo responder perguntas por aqui. "
        "Em breve estarei pronto para te ajudar. 🚧"
    )
    text_optin_confirmed: str = (
        "Obrigado! Seu aceite foi registrado. Já pode me mandar suas dúvidas."
    )
    text_optin_declined: str = (
        "Tudo bem. Sem o aceite eu não consigo te atender por aqui. "
        "Se mudar de ideia, fale com seu gestor."
    )
    text_optin_reminder: str = (
        "Para começar, preciso do seu aceite do aviso de privacidade. "
        "Toque em um dos botões."
    )
    text_rate_limit: str = (
        "Você atingiu o limite de mensagens por hora. Tente novamente em alguns minutos."
    )
    text_opt_out_confirmed: str = (
        "Pronto, você não receberá mais avisos do Jarvis. "
        "Continua podendo fazer perguntas por aqui."
    )
    text_media_ack: str = (
        "Recebi sua mensagem. Ainda não consigo ouvir áudios nem ver imagens, "
        "mas avisei a equipe."
    )

    # Borda HTTP e execução
    rl_webhook_max: int = 600
    rl_webhook_window: int = 60
    production: bool = True
    log_level: str = "INFO"

    # Painel (spec painel.1, 4.11). ADMIN_EMAILS e CSRF_SECRET nao tem padrao util:
    # `validate_panel_config()` recusa vazio em producao (chamado ao montar o painel,
    # para nao afetar o Jarvis do WhatsApp nem a construcao de Settings em outros testes).
    panel_enabled: bool = True
    panel_public_host: str = "jarvis.positiveco.com.br"
    panel_admin_emails: SecretStr = SecretStr("")
    panel_csrf_secret: SecretStr = SecretStr("")
    panel_session_idle_minutes: int = 120
    panel_session_absolute_hours: int = 12
    iam_base_url: str = "https://accounts.positiveco.com.br"
    iam_whoami_cache_ttl_seconds: int = 30
    iam_http_timeout_seconds: float = 3.0
    dev_auth_email: str = ""
    faq_cache_max_age_seconds: int = 60
    retention_months: int = 12
    retention_dry_run: bool = True
    unknown_first_text_display: bool = False
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"
    assistant_daily_limit: int = 200
    assistant_database_url: SecretStr = SecretStr("")

    # Monitor de sistema do painel (spec 4.11): limiares dos alertas automaticos.
    monitor_volume_factor: float = 3.0
    monitor_volume_min_per_hour: int = 30
    monitor_error_rate: float = 0.20
    monitor_error_min_messages: int = 20
    monitor_window_minutes: int = 60
    monitor_api_failures: int = 5

    # Cérebro (spec cerebro.1, 4.12). Desligado = UnderConstructionProvider.
    # Preços por milhão de tokens não têm padrão inventado: ligar exige informá-los.
    brain_enabled: bool = False
    brain_monthly_budget_usd: float = 100.0
    brain_budget_alert_pct: float = 0.8
    brain_gemini_timeout_seconds: float = 5.0
    brain_match_threshold: float = 0.55
    brain_candidate_floor: float = 0.30
    brain_direct_threshold: float = 0.85
    brain_candidates_k: int = 8
    brain_context_exchanges: int = 3
    brain_context_minutes: int = 30
    brain_answer_mode: Literal["grounded", "verbatim"] = "grounded"
    brain_price_in_per_mtok_usd: float | None = None
    brain_price_out_per_mtok_usd: float | None = None

    # Textos fixos do cérebro (spec cerebro.1, 4.12)
    text_brain_no_answer: str = (
        "Ainda não sei responder isso. Anotei a pergunta para a equipe."
    )
    text_brain_access_request: str = (
        "Você ainda não tem acesso às informações dessa marca. "
        "Avisei a equipe para pedir a liberação."
    )
    text_brain_refusal: str = (
        "Não posso ajudar com isso por aqui. Posso responder dúvidas sobre as marcas e produtos."
    )
    text_brain_greeting: str = (
        "Oi! Sou o Jarvis. Me pergunte sobre as marcas e produtos e eu te ajudo."
    )
    text_brain_thanks: str = "Por nada! Se precisar, é só mandar a pergunta."
    text_brain_ask_brand: str = "De qual marca você está falando?"
    text_brain_rating_thanks: str = "Obrigado, avisei a equipe para melhorar essa resposta."

    @field_validator(
        "database_url",
        "whatsapp_app_secret",
        "whatsapp_verify_token",
        "whatsapp_access_token",
    )
    @classmethod
    def _secret_not_empty(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("valor obrigatório não pode ser vazio")
        return v

    @field_validator(
        "monitor_volume_factor",
        "monitor_volume_min_per_hour",
        "monitor_error_rate",
        "monitor_error_min_messages",
        "monitor_window_minutes",
        "monitor_api_failures",
    )
    @classmethod
    def _monitor_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("limiar do monitor deve ser positivo")
        return v

    @field_validator("monitor_error_rate")
    @classmethod
    def _monitor_rate_max(cls, v: float) -> float:
        if v > 1:
            raise ValueError("MONITOR_ERROR_RATE: use um valor entre 0 e 1")
        return v

    @field_validator("whatsapp_phone_number_id")
    @classmethod
    def _phone_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("valor obrigatório não pode ser vazio")
        return v.strip()

    @field_validator("whatsapp_api_base")
    @classmethod
    def _graph_host(cls, v: str) -> str:
        return _check_host("WHATSAPP_API_BASE", v, _GRAPH_ALLOWED_HOSTS)

    @field_validator("waha_base_url")
    @classmethod
    def _waha_host(cls, v: str) -> str:
        return _check_host("WAHA_BASE_URL", v, _WAHA_ALLOWED_HOSTS)

    @field_validator("email_backoff_seconds")
    @classmethod
    def _backoff_format(cls, v: str) -> str:
        try:
            values = [int(p) for p in v.split(",") if p.strip()]
        except ValueError as exc:
            raise ValueError("EMAIL_BACKOFF_SECONDS: use inteiros separados por vírgula") from exc
        if not values or any(n <= 0 for n in values):
            raise ValueError("EMAIL_BACKOFF_SECONDS: informe inteiros positivos")
        return v

    @field_validator("iam_base_url")
    @classmethod
    def _iam_host(cls, v: str) -> str:
        if urlparse(v).scheme != "https":
            raise ValueError("IAM_BASE_URL: use https")
        return _check_host("IAM_BASE_URL", v, _IAM_ALLOWED_HOSTS)

    @model_validator(mode="after")
    def _dev_auth_not_in_production(self) -> Self:
        if self.production and self.dev_auth_email.strip():
            raise ValueError("DEV_AUTH_EMAIL definido com PRODUCTION=true: recusado")
        return self

    @model_validator(mode="after")
    def _team_group_requirements(self) -> Self:
        if self.team_group_enabled and not (
            self.waha_api_key.get_secret_value().strip() and self.team_group_chat_id.strip()
        ):
            raise ValueError("TEAM_GROUP_ENABLED=true exige WAHA_API_KEY e TEAM_GROUP_CHAT_ID")
        return self

    @field_validator(
        "brain_monthly_budget_usd",
        "brain_gemini_timeout_seconds",
        "brain_candidates_k",
        "brain_context_exchanges",
        "brain_context_minutes",
    )
    @classmethod
    def _brain_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("valor do cérebro deve ser positivo")
        return v

    @field_validator(
        "brain_budget_alert_pct",
        "brain_match_threshold",
        "brain_candidate_floor",
        "brain_direct_threshold",
    )
    @classmethod
    def _brain_unit_interval(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("use um valor entre 0 (exclusivo) e 1")
        return v

    @field_validator("brain_price_in_per_mtok_usd", "brain_price_out_per_mtok_usd")
    @classmethod
    def _brain_price_not_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("preço por milhão de tokens não pode ser negativo")
        return v

    @model_validator(mode="after")
    def _brain_requirements(self) -> Self:
        floor, match, direct = (
            self.brain_candidate_floor,
            self.brain_match_threshold,
            self.brain_direct_threshold,
        )
        if not floor <= match <= direct:
            raise ValueError(
                "BRAIN_CANDIDATE_FLOOR <= BRAIN_MATCH_THRESHOLD <= BRAIN_DIRECT_THRESHOLD"
            )
        if not self.brain_enabled:
            return self
        missing = []
        if not self.gemini_api_key.get_secret_value().strip():
            missing.append("GEMINI_API_KEY")
        if self.brain_price_in_per_mtok_usd is None:
            missing.append("BRAIN_PRICE_IN_PER_MTOK_USD")
        if self.brain_price_out_per_mtok_usd is None:
            missing.append("BRAIN_PRICE_OUT_PER_MTOK_USD")
        if missing:
            raise ValueError("BRAIN_ENABLED=true exige: " + ", ".join(missing))
        return self

    @property
    def panel_admin_email_list(self) -> tuple[str, ...]:
        """E-mails de administrador do painel, normalizados (minusculas, sem vazios)."""
        raw = self.panel_admin_emails.get_secret_value()
        return tuple(e.strip().lower() for e in raw.split(",") if e.strip())

    def validate_panel_config(self) -> None:
        """Fail-fast do painel: em producao exige admins e segredo de CSRF."""
        if not (self.panel_enabled and self.production):
            return
        missing = []
        if not self.panel_admin_email_list:
            missing.append("PANEL_ADMIN_EMAILS")
        if not self.panel_csrf_secret.get_secret_value().strip():
            missing.append("PANEL_CSRF_SECRET")
        if missing:
            raise ValueError("painel em producao exige: " + ", ".join(missing))

    @property
    def email_backoff_list(self) -> tuple[int, ...]:
        """Esperas (segundos) entre tentativas de e-mail, já parseadas."""
        return tuple(int(p) for p in self.email_backoff_seconds.split(",") if p.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # campos obrigatórios vêm do ambiente
