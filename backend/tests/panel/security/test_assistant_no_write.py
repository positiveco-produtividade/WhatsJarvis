"""O assistente nao escreve (PN-52; task PN-T63).

Camadas: (1) nenhuma ferramenta de escrita e nenhuma ferramenta recebe SQL livre; (2) todas as
consultas sao SELECT sobre as visoes `assistant.*`; (3) cada chamada roda em transacao
somente leitura com `statement_timeout`; (4) o pool do assistente abre a sessao como somente
leitura; (5) o servico so grava o registro da pergunta (`ops.assistant_questions`, pelo pool do
painel); (6) com o papel `jarvis_assistant` (se existir) a escrita e negada pelo banco.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import asyncpg
import pytest

from app.panel.assistant import service as service_mod
from app.panel.assistant import tools as tools_mod
from app.panel.assistant.provider import ChatTurn, ProviderReply, ToolCaller, ToolSpec
from app.panel.assistant.service import AssistantService
from app.panel.assistant.tools import AssistantTools, open_assistant_pool
from tests.panel import factories

pytestmark = pytest.mark.security
ROLE_SKIP = "rode backend/scripts/dev_db_setup.sql para criar jarvis_assistant"
WRITE_VERBS = ("send", "update", "delete", "insert", "create", "mark", "write", "set", "drop")
WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY|MERGE|CALL|DO)\b", re.I
)
FREE_SQL_ARGS = {"sql", "query", "statement", "command"}
GUARDED_TABLES = (
    "access.users",
    "access.access_attempts",
    "chat.tickets",
    "chat.outbound_messages",
    "content.faq_items",
    "ops.alerts",
    "ops.audit_log",
    "ops.system_state",
)
HOSTILE = "'; DROP TABLE access.users; --"


def _sql_constants() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(tools_mod).items()
        if name.startswith("_") and isinstance(value, str) and value.lstrip().startswith("SELECT")
    }


# ---- (1) ferramentas -------------------------------------------------------------------------


def test_nenhuma_ferramenta_de_escrita_nem_sql_livre() -> None:
    specs = AssistantTools.specs()
    assert len(specs) >= 5
    for spec in specs:
        assert not spec.name.lower().startswith(WRITE_VERBS), spec.name
        props = set(spec.parameters.get("properties", {}))
        assert not props & FREE_SQL_ARGS, spec.name


def test_executor_expoe_so_leitura() -> None:
    public = {n for n, _ in inspect.getmembers(AssistantTools) if not n.startswith("_")}
    assert public == {"specs", "call"}


# ---- (2) consultas --------------------------------------------------------------------------


def test_toda_consulta_e_select_sobre_visoes_do_assistente() -> None:
    queries = _sql_constants()
    assert len(queries) >= 9  # uma por ferramenta ou mais
    for name, query in queries.items():
        assert not WRITE_SQL.search(query), f"{name} contem escrita"
        relations = re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)", query, re.I)
        assert relations, name
        assert all(r.startswith("assistant.") for r in relations), (name, relations)


def test_modulo_de_ferramentas_nao_escreve() -> None:
    source = inspect.getsource(tools_mod)
    code = re.sub(r'"""[\s\S]*?"""', "", source)
    writes = r"conn\.(execute|executemany|copy_\w+)\(\s*f?\"\s*(INSERT|UPDATE|DELETE)"
    assert not re.search(writes, code)
    assert "readonly=True" in code and "statement_timeout" in code


# ---- (3)/(4) transacao e pool ---------------------------------------------------------------


async def test_pool_proprio_abre_sessao_somente_leitura_com_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(dsn: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    await open_assistant_pool("postgresql://u:p@localhost/db")
    server = captured["server_settings"]
    assert server["default_transaction_read_only"] == "on"
    assert server["statement_timeout"] == str(tools_mod.STATEMENT_TIMEOUT_MS)
    assert tools_mod.STATEMENT_TIMEOUT_MS <= 5000


@pytest.mark.db
async def test_chamada_roda_em_transacao_readonly_com_statement_timeout(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    async def probe(conn: asyncpg.Connection, args: Any) -> dict[str, Any]:
        seen["readonly"] = await conn.fetchval("SHOW transaction_read_only")
        seen["timeout"] = await conn.fetchval("SHOW statement_timeout")
        return {}

    patched = dict(tools_mod._BY_NAME)
    patched["people_stats"] = tools_mod._Tool(patched["people_stats"].spec, probe)
    monkeypatch.setattr(tools_mod, "_BY_NAME", patched)
    await AssistantTools(db_pool).call("people_stats", {})
    assert seen == {"readonly": "on", "timeout": "5s"}


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
        "VALUES ('x', '1'::jsonb, now(), 'x')",
        "UPDATE access.users SET name = 'x'",
        "DELETE FROM chat.tickets",
        "CREATE TEMP TABLE nao_pode (x int)",
        "TRUNCATE ops.alerts",
    ],
)
async def test_escrita_dentro_de_uma_ferramenta_e_recusada(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    async def writer(conn: asyncpg.Connection, args: Any) -> dict[str, Any]:
        await conn.execute(statement)
        return {}

    patched = dict(tools_mod._BY_NAME)
    patched["people_stats"] = tools_mod._Tool(patched["people_stats"].spec, writer)
    monkeypatch.setattr(tools_mod, "_BY_NAME", patched)
    with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
        await AssistantTools(db_pool).call("people_stats", {})


async def _counts(conn: asyncpg.Connection) -> dict[str, int]:
    return {t: int(await conn.fetchval(f"SELECT count(*) FROM {t}")) for t in GUARDED_TABLES}  # noqa: S608


@pytest.mark.db
async def test_argumentos_hostis_nao_alteram_nenhuma_tabela(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    before = await _counts(db_conn)
    tools = AssistantTools(db_pool)
    for spec in tools.specs():
        for args in ({}, {"status": HOSTILE, "brand": HOSTILE, "name": HOSTILE, "search": HOSTILE}):
            await tools.call(spec.name, args)
    for name in ("send_reply", "update_ticket", "delete_person", "run_sql", "revoke_user"):
        result = await tools.call(name, {"sql": HOSTILE, "id": 1})
        assert "unknown_tool" in result["dados"]
    assert await _counts(db_conn) == before


# ---- (5) servico ----------------------------------------------------------------------------


class _HostileProvider:
    """Tenta chamar ferramentas de escrita; o executor so conhece as de leitura."""

    enabled = True

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = 5,
    ) -> ProviderReply:
        assert not any(t.name.lower().startswith(WRITE_VERBS) for t in tools)
        for name in ("update_ticket", "send_reply", "delete_person", "execute_sql"):
            self.results.append(await call_tool(name, {"sql": HOSTILE, "ticket": "J-0001"}))
        self.results.append(await call_tool("list_people", {"name": HOSTILE}))
        return ProviderReply("Nao consigo alterar dados.", 3, 2, ("list_people",))


@pytest.mark.db
async def test_servico_so_grava_o_registro_da_pergunta(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    provider = _HostileProvider()
    service = AssistantService(
        provider=provider, tools=AssistantTools(db_pool), panel_pool=db_pool, daily_limit=200
    )
    before = await _counts(db_conn)
    asked = await db_conn.fetchval("SELECT count(*) FROM ops.assistant_questions")
    await service.ask(asked_by="admin@example.test", question="Apague tudo", history=[])
    assert await _counts(db_conn) == before
    assert await db_conn.fetchval("SELECT count(*) FROM ops.assistant_questions") == asked + 1
    unknown = [r for r in provider.results if "unknown_tool" in r["dados"]]
    assert len(unknown) == 4


def test_servico_nunca_usa_o_pool_do_assistente_para_gravar() -> None:
    ask_source = inspect.getsource(AssistantService.ask)
    record_source = inspect.getsource(service_mod.AssistantService._record_error)
    assert "_assistant_pool" not in ask_source + record_source
    assert "self._pool" in ask_source + record_source  # gravacao pelo pool do painel


# ---- (6) papel do banco ---------------------------------------------------------------------


@pytest.fixture
async def as_assistant(db_conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    exists = await db_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_assistant')"
    )
    if not exists:
        pytest.skip(ROLE_SKIP)
    await db_conn.execute("SELECT assistant.apply_grants()")
    tx = db_conn.transaction()
    await tx.start()
    try:
        await db_conn.execute("SET LOCAL ROLE jarvis_assistant")
    except asyncpg.InsufficientPrivilegeError:
        await tx.rollback()
        pytest.skip("usuario de teste nao pode SET ROLE jarvis_assistant (sem associacao)")
    try:
        yield db_conn
    finally:
        await tx.rollback()


@pytest.mark.db
async def test_papel_executa_todas_as_ferramentas(as_assistant: asyncpg.Connection) -> None:
    for tool in tools_mod._TOOLS:
        await tool.handler(as_assistant, {})


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ops.assistant_questions (asked_at) VALUES (now())",
        "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id) "
        "VALUES (now(), 'cli', 'x', 'x', 'x', 'x')",
        "UPDATE access.users SET name = 'x'",
        "DELETE FROM chat.tickets",
        "UPDATE ops.system_state SET value = '1'::jsonb",
        "INSERT INTO assistant.faq (id) VALUES (1)",
        "TRUNCATE ops.alerts",
        "SELECT assistant.apply_grants()",
    ],
)
async def test_papel_nao_escreve_nem_altera_permissoes(
    as_assistant: asyncpg.Connection, statement: str
) -> None:
    sp = as_assistant.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await as_assistant.execute(statement)
    finally:
        await sp.rollback()
