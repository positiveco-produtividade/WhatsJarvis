"""Zero custo (PF-39; AC-10, AC-12, AC-31, AC-36): numero fora da lista nao gera envio,
alerta, e-mail, grupo nem chamada ao cerebro; nada de midia e baixado.
"""
from __future__ import annotations

import ast
import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from app.services import answering
from tests.security.test_signature import (
    Env,
    add_user,
    count_effects,
    fixture_bytes,
    signed_payload,
    start_env,
)

pytestmark = [pytest.mark.security, pytest.mark.db]

GROUP_ON = {
    "team_group_enabled": True,
    "waha_api_key": "waha-key-sec",
    "team_group_chat_id": "120363000000000000@g.us",
}
APP_DIR = Path(__file__).resolve().parents[2] / "app"


class SpyProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self.calls += 1
        return answering.Answer(kind="under_construction", text="em construcao")


def start_spy_env(**over: Any) -> tuple[Env, SpyProvider]:
    e = start_env(**over)
    spy = SpyProvider()
    e.app.state.processor._provider = spy
    return e, spy


@pytest.fixture
def spy_env(db_pool: asyncpg.Pool) -> Iterator[tuple[Env, SpyProvider]]:
    pair = start_spy_env(**GROUP_ON)  # ate com grupo ligado, nada pode sair
    try:
        yield pair
    finally:
        pair[0].client.__exit__(None, None, None)


async def quiet() -> None:
    await asyncio.sleep(0.4)  # deixa tarefas de fundo, se existissem, aparecerem


async def assert_zero_cost(env: Env, spy: SpyProvider, pool: asyncpg.Pool) -> None:
    await quiet()
    effects = await count_effects(pool)
    assert env.channel.calls == []  # nada enviado a Meta
    assert env.http_calls == []  # nem e-mail, nem WAHA, nem Graph
    assert spy.calls == 0
    assert effects["chat.outbound_messages"] == 0
    assert effects["ops.alerts"] == 0
    assert effects["ops.deliveries"] == 0
    assert effects["chat.tickets"] == 0


# ---------------------------------------------------------------- desconhecido (AC-10, AC-12)


@pytest.mark.parametrize(
    "fixture", ["text", "audio", "image", "button_interactive", "button_template", "bsuid_only"]
)
async def test_desconhecido_de_qualquer_tipo_nao_gera_custo(
    spy_env: tuple[Env, SpyProvider], db_pool: asyncpg.Pool, fixture: str
) -> None:
    env, spy = spy_env
    assert env.post(fixture_bytes(fixture)).status_code == 200
    await assert_zero_cost(env, spy, db_pool)
    assert await db_pool.fetchval("SELECT count(*) FROM access.access_attempts") == 1


async def test_enxurrada_de_numeros_desconhecidos_nao_alerta_nem_envia(
    spy_env: tuple[Env, SpyProvider], db_pool: asyncpg.Pool
) -> None:
    env, spy = spy_env
    for i in range(30):
        raw = signed_payload(wa_id=f"5511988{i:06d}", msg_id=f"wamid.FLOOD{i:04d}")
        assert env.post(raw).status_code == 200
    await assert_zero_cost(env, spy, db_pool)
    assert await db_pool.fetchval("SELECT count(*) FROM access.access_attempts") == 30


async def test_mesmo_desconhecido_insistindo_nunca_vira_alerta(
    spy_env: tuple[Env, SpyProvider], db_pool: asyncpg.Pool
) -> None:
    env, spy = spy_env
    for i in range(20):  # bem acima de qualquer limiar antigo de 3 tentativas
        assert env.post(signed_payload(msg_id=f"wamid.INSIST{i:03d}")).status_code == 200
    await assert_zero_cost(env, spy, db_pool)
    row = await db_pool.fetchrow("SELECT attempt_count FROM access.access_attempts")
    assert row is not None and row["attempt_count"] == 20


@pytest.mark.parametrize("status", ["revoked", "declined"])
async def test_revogado_ou_recusado_fica_em_silencio_total(
    spy_env: tuple[Env, SpyProvider], db_pool: asyncpg.Pool, status: str
) -> None:
    env, spy = spy_env
    await add_user(db_pool, status)
    for i in range(6):
        assert env.post(signed_payload(msg_id=f"wamid.SIL{i:03d}")).status_code == 200
    assert env.post(fixture_bytes("audio")).status_code == 200
    await assert_zero_cost(env, spy, db_pool)


async def test_pending_optin_texto_ou_audio_nao_chama_cerebro_nem_baixa(
    spy_env: tuple[Env, SpyProvider], db_pool: asyncpg.Pool
) -> None:
    env, spy = spy_env
    await add_user(db_pool, "pending_optin")
    assert env.post(fixture_bytes("text")).status_code == 200
    assert env.post(fixture_bytes("audio")).status_code == 200
    await quiet()
    assert spy.calls == 0
    assert env.http_calls == []  # nada baixado, nada ao grupo
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM chat.tickets") == 0


# ---------------------------------------------------------------- o cerebro so ve texto allowed


async def test_cerebro_so_e_chamado_para_texto_de_ativo(
    db_pool: asyncpg.Pool,
) -> None:
    env, spy = start_spy_env()
    try:
        await add_user(db_pool)
        for name in ("audio", "image", "button_interactive", "button_template"):
            assert env.post(fixture_bytes(name)).status_code == 200
        await quiet()
        assert spy.calls == 0  # midia e botao de ativo nunca chegam ao cerebro
        assert env.http_calls == []  # e nada foi baixado
        assert env.post(fixture_bytes("text")).status_code == 200
        await quiet()
        assert spy.calls == 1  # unico caso: texto allowed
    finally:
        env.client.__exit__(None, None, None)


async def test_cerebro_nao_e_chamado_em_reentrega(db_pool: asyncpg.Pool) -> None:
    env, spy = start_spy_env()
    try:
        await add_user(db_pool)
        for _ in range(4):
            assert env.post(fixture_bytes("text")).status_code == 200
            await asyncio.sleep(0.1)
        await quiet()
        assert spy.calls == 1
        assert len(env.channel.calls) == 1
    finally:
        env.client.__exit__(None, None, None)


async def test_midia_de_ativo_nao_baixa_nada_e_nao_envia_email(db_pool: asyncpg.Pool) -> None:
    env, spy = start_spy_env()  # grupo desligado (padrao)
    try:
        await add_user(db_pool)
        assert env.post(fixture_bytes("image")).status_code == 200
        await quiet()
        assert env.http_calls == []
        assert spy.calls == 0
        assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 0
        assert await db_pool.fetchval("SELECT count(*) FROM chat.tickets") == 1
        # unico envio: a resposta curta de "equipe avisada" ao proprio usuario (AC-36)
        assert [c.method for c in env.channel.calls] == ["send_text"]
    finally:
        env.client.__exit__(None, None, None)


# ---------------------------------------------------------------- arquitetura (AC-36)

_FORBIDDEN_NAME_PARTS = ("download", "lookaside", "media_url", "fetch_media", "get_media")
_FORBIDDEN_STRINGS = ("lookaside", "/media", "media_url", "download")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.alias):
            names.append(node.name)
        for name in names:
            if any(part in name.lower() for part in _FORBIDDEN_NAME_PARTS):
                found.append(f"{path.name}:{getattr(node, 'lineno', '?')} nome `{name}`")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            low = node.value.lower()
            if any(s in low for s in _FORBIDDEN_STRINGS):
                found.append(f"{path.name}:{node.lineno} texto `{node.value[:40]}`")
    return found


def test_nenhum_modulo_referencia_download_de_midia_da_graph_api() -> None:
    files = sorted(APP_DIR.rglob("*.py"))
    assert len(files) > 20  # a varredura realmente achou o codigo
    problems = [v for f in files for v in _violations(f)]
    assert problems == [], "download de midia nao e permitido (AC-36): " + "; ".join(problems)


def test_so_o_cliente_whatsapp_fala_com_a_graph_api_e_so_envia() -> None:
    graph_users = [
        p.name
        for p in APP_DIR.rglob("*.py")
        if "whatsapp_api_base" in p.read_text(encoding="utf-8") and p.name != "config.py"
    ]
    assert graph_users == ["whatsapp.py"]
    tree = ast.parse((APP_DIR / "services" / "whatsapp.py").read_text(encoding="utf-8"))
    http_verbs = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr in {"get", "stream", "request", "head"}
        and isinstance(n.value, ast.Attribute) and n.value.attr == "_client"
    }
    assert http_verbs == set()  # so `_client.post` (envio de mensagens)


def test_sanidade_da_varredura_detecta_download(tmp_path: Path) -> None:
    sample = tmp_path / "ruim.py"
    sample.write_text('def baixar():\n    return client.get("https://x/media/123")\n')
    sample2 = tmp_path / "ruim2.py"
    sample2.write_text("def download_media(media_id):\n    return media_id\n")
    assert _violations(sample) and _violations(sample2)
    ok = tmp_path / "bom.py"
    ok.write_text('"""Sem cliente de download."""\n\ndef enviar():\n    return 1\n')
    assert _violations(ok) == []

