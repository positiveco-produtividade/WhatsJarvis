"""Logs sem PII (PN-T63; spec 4.4 "Seguranca"; contrato de porta-fechada.1).

Conduz os fluxos do painel com `caplog` em DEBUG (telas, formularios, LGPD, importacao, FAQ,
tickets, controles, assistente com provedor falso, acesso negado) e confere que NENHUM registro
de log (mensagem, argumentos, `extra=` e traceback) contem telefone, e-mail (inclusive os
administradores da configuracao), nome, texto de mensagem ou segredo.
"""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.panel.assistant.provider import (
    ChatTurn,
    ProviderError,
    ProviderReply,
    ToolCaller,
    ToolSpec,
)
from app.panel.assistant.service import AssistantService
from app.panel.assistant.tools import AssistantTools
from tests.panel import factories
from tests.panel.factories import BRAND_A, insert_user
from tests.panel.integration.test_access import (  # noqa: F401
    ADMIN,
    CSRF_SECRET,
    NOW,
    Env,
    env,
    login,
    post_headers,
)
from tests.panel.integration.test_people_read import add_attempt, set_display

pytestmark = [pytest.mark.security, pytest.mark.db]

HX = {"HX-Request": "true"}
OTHER_ADMIN = "outro@example.test"  # segundo administrador da configuracao (test_access)
INTRUDER = "intruso-secreto@example.test"
PERSON = "Zelda Quimera Sobrenome"
MANAGER = "Gestora Confidencial Prado"
COMPANY = "Empresa Sigilosa Ltda"
NEW_PERSON = "Nadia Cadastrada Nova"
NEW_PHONE = "+55 85 98888-7777"
FAQ_ANSWER = "RESPOSTA-FAQ-CONFIDENCIAL"
SECRETS = {
    "app_secret": "app-secret-xyz",
    "verify_token": "verify-token-xyz",
    "access_token": "access-token-xyz",
    "csrf_secret": CSRF_SECRET,
    "iam_cookie": "iam-valido",
}
MESSAGES = {
    "ticket": "TEXTO-TICKET-CONFIDENCIAL",
    "inbox": "TEXTO-INBOX-CONFIDENCIAL",
    "unknown_first": "TEXTO-DESCONHECIDO-CONFIDENCIAL",
    "assistant_question": "PERGUNTA-CONFIDENCIAL-ASSISTENTE",
    "faq_import": "PERGUNTA-IMPORTADA-CONFIDENCIAL",
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# +55 DD 9XXXX-XXXX em qualquer formatacao, ou 55 + 10/11 digitos colados.
PHONE = re.compile(r"(?<!\d)(?:\+?55[\s-]?\d{2}[\s-]?9?\d{4}[\s-]?\d{4})(?!\d)")
PHONE_KEY_PREFIX = "55119000"
EXPECTED_EVENTS = (
    "panel_faq_created",
    "panel_ticket_concluded",
    "panel_person_registered",
    "panel_person_duplicate_phone",
    "panel_lgpd_exported",
    "panel_lgpd_deleted",
    "panel_person_revoked",
    "control_changed",
    "panel_attempt_dismissed",
    "assistant_call",
)
STD_ATTRS = frozenset(logging.LogRecord("n", 0, "p", 0, "m", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def record_text(record: logging.LogRecord) -> str:
    """Tudo o que um handler poderia gravar: mensagem, `extra=` e traceback."""
    parts = [record.getMessage(), record.name]
    parts += [f"{k}={v!r}" for k, v in record.__dict__.items() if k not in STD_ATTRS]
    if record.exc_info:
        parts.append(logging.Formatter().formatException(record.exc_info))
    if record.exc_text:
        parts.append(record.exc_text)
    return "\n".join(parts)


@pytest.fixture
def raw_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> list[str]:
    """Texto de cada registro ANTES de qualquer filtro (o `RedactingFilter` do app, instalado
    globalmente pela cauda de log, reescreve o registro e esconderia um vazamento do painel)."""
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    original = logging.Logger.handle

    def spy(self: logging.Logger, record: logging.LogRecord) -> None:
        seen.append(record_text(record))
        original(self, record)

    monkeypatch.setattr(logging.Logger, "handle", spy)
    return seen


def dsn_password(dsn: str) -> str:
    return urlparse(dsn).password or ""


async def _seed(dsn: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        world = await factories.seed_world(conn, NOW)
        uid = await insert_user(conn, name=PERSON, seq=60, brands=[BRAND_A], now=NOW)
        await conn.execute(
            "UPDATE access.users SET company = $2, manager_name = $3 WHERE id = $1",
            uid,
            COMPANY,
            MANAGER,
        )
        extra = await insert_user(conn, name=f"{PERSON} Dois", seq=61, brands=[BRAND_A], now=NOW)
        await factories.insert_ticket(
            conn, number=800, user_id=uid, status="awaiting", created_at=NOW, brand=BRAND_A,
            message=MESSAGES["ticket"], escalated=True, urgency="urgente",
        )
        await conn.execute(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, body) "
            "VALUES (gen_random_uuid()::text, $1, 'text', $2, 'allowed', 'allowed', $3)",
            NOW,
            uid,
            MESSAGES["inbox"],
        )
        await set_display(conn, True, NOW)
        attempt = await add_attempt(conn, 7, NOW, count=4, body=MESSAGES["unknown_first"])
        faq = world.faq.ids[0] if world.faq.available else 0
        return {"uid": uid, "extra": extra, "attempt": attempt, "faq": faq}
    finally:
        await conn.close()


class _Provider:
    """Provedor falso: responde, ou falha com a mensagem do modelo (que cita a pergunta)."""

    enabled = True

    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = 5,
    ) -> ProviderReply:
        if self.fail:
            raise ProviderError(f"falha do modelo ao processar: {messages[-1].text}")
        await call_tool("list_people", {"name": PERSON})
        return ProviderReply(f"Resposta sobre {PERSON}.", 5, 3, ("list_people",))


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(_token(c)), **HX})


def _get_everything(c: TestClient, ids: dict[str, int]) -> None:
    uid = ids["uid"]
    urls = [
        "/dashboard/tickets", "/dashboard/tickets/J-0800", "/dashboard/escalados",
        "/dashboard/inbox", f"/dashboard/inbox?u={uid}", f"/dashboard/inbox/{uid}",
        "/dashboard/faq", "/dashboard/faq/nova", f"/dashboard/faq/{ids['faq']}/editar",
        "/dashboard/pessoas", "/dashboard/pessoas?aba=pedindo", "/dashboard/pessoas?aba=retiradas",
        f"/dashboard/pessoas?q={PERSON.split()[0]}", f"/dashboard/pessoas/{uid}",
        f"/dashboard/pessoas/{uid}/revogar", f"/dashboard/pessoas/{uid}/excluir",
        "/dashboard/pessoas/nova", f"/dashboard/pessoas/nova?pedido={ids['attempt']}",
        "/dashboard/pessoas/importar", "/dashboard/pessoas/importar/modelo",
        "/dashboard/pessoas/pedidos/exibicao", "/dashboard/alerts", "/dashboard/metrics",
        "/dashboard/relatorios", "/dashboard/relatorios/mensal.pdf", "/dashboard/logs",
        "/dashboard/logs/auditoria", "/dashboard/logs/problemas", "/dashboard/logs/servidor",
        "/dashboard/logs/retencao", "/dashboard/assistente-perguntas", "/dashboard/estado",
        "/dashboard/control/painel", "/dashboard/control/pausa", "/dashboard/marca",
        "/dashboard/faq/importar", "/dashboard/faq/importar/modelo", "/api/assistant/status",
        "/dashboard/tickets/J-9999", f"/dashboard/pessoas/{uid + 999}",
    ]
    for url in urls:
        for headers in ({}, HX):
            c.get(url, headers=headers)


def _post_everything(c: TestClient, env: Env, ids: dict[str, int]) -> None:
    uid = ids["uid"]
    faq = {"brand": "jungle", "category": "PRODUTO", "question": MESSAGES["faq_import"] + "?"}
    _post(c, "/dashboard/faq/nova", {**faq, "answer": FAQ_ANSWER, "bot_responde": "1"})
    _post(c, "/dashboard/faq/nova", {**faq, "answer": ""})
    _post(c, f"/dashboard/faq/{ids['faq']}/desativar")
    _post(c, f"/dashboard/faq/{ids['faq']}/reativar")
    _post(c, "/dashboard/tickets/J-0800/reclassificar", {"classification": "PRECO"})
    _post(c, "/dashboard/tickets/J-0800/urgencia", {"urgency": "urgente"})
    _post(c, "/dashboard/tickets/J-0800/concluir", {"author": "Admin Teste"})
    _post(c, "/dashboard/control/pausa", {"desired": "pause"})
    _post(c, "/dashboard/control/pausa", {"desired": "resume"})
    _post(c, f"/dashboard/pessoas/pedidos/{ids['attempt']}/ignorar")
    _post(c, "/dashboard/pessoas/pedidos/exibicao", {"value": "off"})
    _post(c, "/dashboard/pessoas/nova", _person_form(NEW_PERSON, NEW_PHONE))
    _post(c, "/dashboard/pessoas/nova", _person_form(NEW_PERSON, NEW_PHONE))  # duplicado
    _post(c, "/dashboard/pessoas/nova", _person_form(NEW_PERSON, "123"))  # invalido
    _post(c, f"/dashboard/pessoas/{uid}/exportar")
    _post(c, f"/dashboard/pessoas/{ids['extra']}/revogar", {"reason": "left_company"})
    _post(c, f"/dashboard/pessoas/{ids['extra']}/reativar")
    _post(c, f"/dashboard/pessoas/{uid}/excluir", {"confirm_name": "errado"})
    _post(c, f"/dashboard/pessoas/{uid}/excluir", {"confirm_name": PERSON})
    _post(c, f"/dashboard/pessoas/{uid}/excluir", {"confirm_name": PERSON})  # ja apagada
    _imports(c)


def _person_form(name: str, phone: str) -> dict[str, Any]:
    return {
        "nome": name, "telefone": phone, "perfil": "vendedor", "empresa": COMPANY,
        "ufs": ["CE"], "canais": ["farma"], "marcas": ["jungle"], "gestor": MANAGER,
    }


def _imports(c: TestClient) -> None:
    people = (
        "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor\n"
        f"Importada Oculta,+5585991231201,vendedor,{COMPANY},CE,farma,jungle,{MANAGER}\n"
        "Linha Ruim,123,vendedor,X,CE,farma,jungle,G\n"
    )
    faq = f"pergunta;resposta;categoria;bot responde\n{MESSAGES['faq_import']}?;ok;Produto;sim\n"
    for base, content, extra in (
        ("/dashboard/pessoas/importar", people, {"x": "1"}),
        ("/dashboard/faq/importar", faq, {"brand": "jungle"}),
    ):
        up = c.post(
            base,
            files={"file": ("arquivo.csv", content.encode("utf-8"), "text/csv")},
            data=extra,
            headers=post_headers(_token(c)),
        )
        if up.status_code == 303:
            batch = up.headers["location"]
            c.get(batch)
            c.post(batch + "/gravar", data={"x": "1"}, headers=post_headers(_token(c)))
            c.post(batch + "/cancelar", data={"x": "1"}, headers=post_headers(_token(c)))
    c.post(  # arquivo recusado
        "/dashboard/pessoas/importar",
        files={"file": ("ruim.csv", b"\xff\xfe\x00bad", "text/csv")},
        data={"x": "1"},
        headers=post_headers(_token(c)),
    )


def _assistant(c: TestClient) -> None:
    pool = c.app.state.pool  # type: ignore[attr-defined]
    for fail in (False, True):
        c.app.state.assistant_service = AssistantService(  # type: ignore[attr-defined]
            provider=_Provider(fail),
            tools=AssistantTools(pool),
            panel_pool=pool,
            daily_limit=200,
        )
        r = c.post(
            "/api/assistant/chat",
            json={"message": MESSAGES["assistant_question"], "history": []},
            headers=post_headers(_token(c)),
        )
        assert r.status_code in (200, 502)


def _forbidden_and_iam_failures(env: Env) -> None:
    env.iam.email = INTRUDER
    with env.client() as c:
        login(c)
        c.get("/dashboard")
        c.get("/dashboard/pessoas")
        c.post("/dashboard/faq/nova", data={}, headers={"Origin": "https://evil.example"})
    for mode in ("timeout", "server_error", "unauthorized"):
        env.iam.email = ADMIN
        env.iam.mode = mode
        with env.client() as c:
            login(c)
            c.get("/dashboard")
    env.iam.mode = "ok"


def _violations(text: str, dsn: str, sellers: list[str], cookie_values: list[str]) -> list[str]:
    found: list[str] = []
    needles = {
        **{f"segredo {k}": v for k, v in SECRETS.items()},
        **{f"mensagem {k}": v for k, v in MESSAGES.items()},
        "nome": PERSON,
        "nome (novo)": NEW_PERSON,
        "gestor": MANAGER,
        "empresa": COMPANY,
        "resposta da FAQ": FAQ_ANSWER,
        "e-mail admin": ADMIN,
        "e-mail do 2o admin": OTHER_ADMIN,
        "e-mail do intruso": INTRUDER,
        "telefone novo": NEW_PHONE,
        "telefone novo (digitos)": "558588887777",
        "telefone (chave)": PHONE_KEY_PREFIX,
        "nome importado": "Importada Oculta",
    }
    for label, value in needles.items():
        if value and value in text:
            found.append(label)
    for index, name in enumerate(sellers):
        if name in text:
            found.append(f"nome de vendedor {index}")
    for index, value in enumerate(cookie_values):
        if value and value in text:
            found.append(f"valor de cookie/token {index}")
    password = dsn_password(dsn)
    if len(password) >= 4 and password in text:
        found.append("senha do banco")
    found += [f"e-mail: {m}" for m in set(EMAIL.findall(text))]
    found += [f"telefone: {m}" for m in set(PHONE.findall(text))]
    return found


def test_nenhum_log_do_painel_tem_pii(env: Env, raw_logs: list[str]) -> None:
    ids = asyncio.run(_seed(env.dsn))
    cookie_values: list[str] = []
    with env.client(welcome_template_name="boas_vindas_teste") as c:
        login(c)
        _get_everything(c, ids)
        cookie_values += [v for k, v in c.cookies.items() if k != "iam_session"]
        cookie_values.append(_token(c))
        _post_everything(c, env, ids)
        _assistant(c)
    _forbidden_and_iam_failures(env)
    assert raw_logs, "o teste nao capturou log algum (captura quebrada)"
    joined = "\n".join(raw_logs)
    for event in EXPECTED_EVENTS:  # os fluxos de escrita rodaram de verdade (teste nao vazio)
        assert event in joined, f"fluxo nao executado: {event}"
    sellers = ["Vendedor Uma Marca", "Vendedor Duas Marcas", "Vendedor So Midia"]
    bad = _violations("\n".join(raw_logs), env.dsn, sellers, cookie_values)
    assert not bad, "PII/segredo nos logs: " + ", ".join(sorted(bad))


def test_captura_de_logs_e_sensivel_o_bastante(raw_logs: list[str]) -> None:
    """Prova de que o detector funciona: um log com PII e pego (mensagem, extra e traceback)."""
    log = logging.getLogger("teste.pii")
    log.info("enviado para %s", "+55 85 99123-1201")
    log.info("evento", extra={"quem": "fulano@example.test"})
    try:
        raise ValueError(PERSON)
    except ValueError:
        log.exception("falhou")
    bad = _violations("\n".join(raw_logs), "postgresql://u:p@h/db", [], [])
    assert any(b.startswith("telefone") for b in bad)
    assert any(b.startswith("e-mail") for b in bad)
    assert "nome" in bad
