"""O texto da primeira mensagem de desconhecido so e MOSTRADO na aba Pedindo acesso (PN-37).

Varredura estatica (codigo, SQL e templates) mais prova viva: com a exibicao ligada, o texto
aparece SO em `people_read.pending_requests` -> parcial da aba Pedindo acesso, e em nenhuma
outra rota GET do painel. Semantica da lista permitida (a mesma de
`tests/panel/integration/test_people_read.py`): `attempts` (PF) so grava, `lgpd` e `retention`
so apagam, `logs_retencao` so tem um rotulo de contagem, e so `people_read` le.
"""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg
import pytest
from fastapi.routing import APIRoute

from app.panel.services import people_read
from tests.panel import factories
from tests.panel.integration.test_access import NOW, Env, env, login  # noqa: F401
from tests.panel.integration.test_people_read import add_attempt, set_display

pytestmark = pytest.mark.security

APP_DIR = Path(people_read.__file__).resolve().parents[2]  # backend/app
ROOT = APP_DIR.parents[1]
FRONTEND_DIR = ROOT / "frontend"
TABLE = "unknown_first_messages"
SECRET = "segredo-da-primeira-mensagem-xyz"

READERS = {"backend/app/panel/services/people_read.py"}
WRITER = {"backend/app/services/attempts.py"}  # grava (PF); nunca le
DELETERS = {"backend/app/panel/services/lgpd.py", "backend/app/panel/services/retention.py"}
LABEL_ONLY = {"backend/app/panel/api/logs_retencao.py"}
TEMPLATE_WITH_TEXT = "_pessoas_lista.html"  # aba Pedindo acesso (`tab == 'pedindo'`)
FIRST_TEXT_PY = {  # o campo `first_text`: a consulta e o docstring da rota de pedidos
    "backend/app/panel/services/people_read.py",
    "backend/app/panel/api/pessoas_pedidos.py",
}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _files(base: Path, suffixes: set[str]) -> list[Path]:
    return [p for p in base.rglob("*") if p.is_file() and p.suffix in suffixes]


FIELD = re.compile(r"(?<!\w)first_text(?!\w)")  # o campo, nao `store_first_text` & cia


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# ---- varredura estatica -------------------------------------------------------------------


def test_tabela_so_e_citada_pelos_modulos_permitidos() -> None:
    offenders = {
        _rel(p)
        for base in (APP_DIR, FRONTEND_DIR)
        for p in _files(base, {".py", ".html", ".js", ".sql"})
        if TABLE in _text(p)
    }
    allowed = READERS | WRITER | DELETERS | LABEL_ONLY
    assert offenders - allowed == set(), "modulo novo citando a tabela do texto"
    assert READERS <= offenders  # a varredura enxerga o leitor (nao e vazia)


def test_so_people_read_consulta_a_tabela() -> None:
    source = _text(Path(people_read.__file__))
    assert source.count("JOIN access." + TABLE) == 1
    assert len(re.findall(r"FROM\s+access\." + TABLE, source)) == 0
    pending = source.split("async def pending_requests")[1]
    assert "_TEXT_JOIN" in pending and source.count("_TEXT_JOIN") == 2


def test_gravador_so_insere_apagadores_so_apagam_e_rotulo_nao_consulta() -> None:
    writer = _text(ROOT / next(iter(WRITER)))
    lines = [ln for ln in writer.splitlines() if TABLE in ln and "INSERT" in ln]
    assert lines and not re.search(r"SELECT[^\n]*" + TABLE, writer)
    lgpd = _text(ROOT / "backend/app/panel/services/lgpd.py")
    statements = re.findall(r'"([^"]*' + TABLE + r'[^"]*)"', lgpd)
    sql = [s for s in statements if s.split()[0] in {"DELETE", "SELECT", "UPDATE", "INSERT"}]
    assert sql and all(s.startswith("DELETE FROM") for s in sql)
    retention = _text(ROOT / "backend/app/panel/services/retention.py")
    for ln in retention.splitlines():
        if TABLE in ln and not ln.lstrip().startswith(("#", "`", '"')):
            assert "SELECT" not in ln.upper()
    label = _text(ROOT / next(iter(LABEL_ONLY)))
    assert label.count(TABLE) == 1
    assert re.search(r'"' + TABLE + r'":\s*"', label), "so um rotulo de contagem"
    assert not re.search(r"\b(SELECT|JOIN|FROM)\b[^\n]*" + TABLE, label)


def test_campo_first_text_so_na_consulta_e_no_parcial_da_aba_pedindo() -> None:
    py = {_rel(p) for p in _files(APP_DIR, {".py"}) if FIELD.search(_text(p))}
    assert py - FIRST_TEXT_PY == set()
    templates = {
        p.name for p in _files(FRONTEND_DIR / "templates", {".html"}) if FIELD.search(_text(p))
    }
    assert templates == {TEMPLATE_WITH_TEXT}
    js = [p for p in _files(FRONTEND_DIR / "static" / "js", {".js"}) if FIELD.search(_text(p))]
    assert js == []


def test_parcial_so_renderiza_o_texto_dentro_da_aba_pedindo() -> None:
    html = _text(FRONTEND_DIR / "templates" / TEMPLATE_WITH_TEXT)
    start = html.index("{% elif tab == 'pedindo' %}")
    end = html.index("{% else %}", html.index("{% endfor %}", start) + 1)
    inside = html[start:end]
    outside = html[:start] + html[end:]
    assert "first_text" in inside and "first_text" not in outside
    assert "{{ r.first_text }}" in inside and "|safe" not in inside


def test_texto_nao_vai_para_log_nem_alerta_nem_auditoria_no_codigo() -> None:
    """Nenhum modulo do painel junta o corpo da primeira mensagem a alerta, log ou auditoria."""
    for path in _files(APP_DIR / "panel", {".py"}):
        source = _text(path)
        if FIELD.search(source) and _rel(path) not in FIRST_TEXT_PY:
            pytest.fail(f"{_rel(path)} usa first_text")
        for ln in source.splitlines():
            if FIELD.search(ln):
                assert not re.search(r"\b(logger|log|audit|alerts?)\b\.", ln), ln


# ---- prova viva -----------------------------------------------------------------------------


async def _seed(dsn: str, *, display: bool) -> tuple[int, int]:
    conn = await asyncpg.connect(dsn)
    try:
        await factories.seed_world(conn, NOW)
        attempt = await add_attempt(conn, 1, NOW, count=4, body=SECRET)
        user = int(await conn.fetchval("SELECT min(id) FROM access.users"))
        await set_display(conn, display, NOW)
        return attempt, user
    finally:
        await conn.close()


def _plain_get_paths(client_app: object) -> list[str]:
    paths: list[str] = []
    for route in client_app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and "GET" in route.methods and "{" not in route.path:
            if route.path not in {"/docs", "/redoc", "/openapi.json"}:
                paths.append(route.path)
    return sorted(set(paths))


def _urls(client_app: object, attempt: int, user: int) -> list[str]:
    urls = _plain_get_paths(client_app)
    urls += [
        "/dashboard/pessoas?aba=autorizadas",
        "/dashboard/pessoas?aba=retiradas",
        "/dashboard/pessoas?q=segredo",
        "/dashboard/tickets/J-0001",
        "/dashboard/inbox?u=" + str(user),
        f"/dashboard/inbox/{user}",
        f"/dashboard/pessoas/{user}",
        f"/dashboard/pessoas/{user}/revogar",
        f"/dashboard/pessoas/{user}/excluir",
        f"/dashboard/pessoas/nova?pedido={attempt}",
        "/dashboard/logs?aba=auditoria",
    ]
    return urls


@pytest.mark.db
def test_com_exibicao_ligada_o_texto_so_sai_na_aba_pedindo(env: Env) -> None:
    attempt, user = asyncio.run(_seed(env.dsn, display=True))
    leaks: list[str] = []
    with env.client() as c:
        login(c)
        shown = c.get("/dashboard/pessoas", params={"aba": "pedindo"})
        fragment = c.get(
            "/dashboard/pessoas", params={"aba": "pedindo"}, headers={"HX-Request": "true"}
        )
        assert SECRET in shown.text and SECRET in fragment.text
        for url in _urls(c.app, attempt, user):
            for headers in ({}, {"HX-Request": "true"}):
                r = c.get(url, headers=headers)
                if SECRET.encode() in r.content:
                    leaks.append(f"{url} {'HX' if headers else ''}")
    assert not leaks, "texto da primeira mensagem fora da aba Pedindo acesso:\n" + "\n".join(leaks)


@pytest.mark.db
def test_com_exibicao_desligada_o_texto_nao_sai_em_lugar_nenhum(env: Env) -> None:
    attempt, user = asyncio.run(_seed(env.dsn, display=False))
    leaks: list[str] = []
    with env.client() as c:
        login(c)
        for url in [*_urls(c.app, attempt, user), "/dashboard/pessoas?aba=pedindo"]:
            for headers in ({}, {"HX-Request": "true"}):
                if SECRET.encode() in c.get(url, headers=headers).content:
                    leaks.append(url)
    assert not leaks, "\n".join(leaks)
