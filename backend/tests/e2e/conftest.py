"""Fixtures dos E2E do painel (PN-T65, PN-T66): Playwright para Python + app real numa porta local.

Fora da suite padrao (marcador `e2e`, como o `perf`): rode `pytest tests/e2e -m e2e`.
Sem o pacote `playwright` ou sem o Chromium instalado (`playwright install chromium`), tudo pula
com o motivo, entao o CI nao e afetado.

O app e o mesmo do teste de acesso (`build_app` de test_access: painel inteiro + IAM falso),
servido pelo uvicorn numa thread em 127.0.0.1, com o banco de teste (papel `jarvis_app`).
O navegador abre `http://localhost:<porta>`: o Chromium aceita cookie `Secure` em localhost, e
`panel_public_host="localhost"` com `production=False` deixa o CSRF aceitar a origem http.
Nenhum acesso a rede fora de localhost: o resolvedor do Chromium so conhece localhost (o login do
IAM, accounts..., nao resolve; o teste de PN-1 confere o pedido que o navegador tentou fazer) e
toda requisicao para outro host e abortada pela rota do contexto.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
import uvicorn

from app.core.clock import SystemClock
from tests.db_support import *  # noqa: F401,F403
from tests.db_support import _truncate_all
from tests.panel import factories
from tests.panel.fakes import FakeIam
from tests.panel.integration.test_access import ADMIN, build_app, make_settings

try:  # opcional: sem o pacote, os modulos de teste pulam (importorskip) e nada aqui roda
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
except ImportError:  # pragma: no cover - depende da maquina
    sync_playwright = None


IAM_HOST = "accounts.positiveco.com.br"
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def run_async[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Roda uma corrotina numa thread propria: o Playwright sincrono ocupa o laco desta."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(make())
        except BaseException as exc:  # repassa para a thread do teste
            box["error"] = exc

    worker = threading.Thread(target=target, name="e2e-db")
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box["value"]  # type: ignore[no-any-return]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class LiveServer:
    base_url: str
    dsn: str
    iam: FakeIam
    now: datetime
    world: factories.PanelWorld

    def url(self, path: str) -> str:
        return self.base_url + path

    def sql(self, query: str, *args: Any) -> Any:
        async def run() -> Any:
            conn = await asyncpg.connect(self.dsn)
            try:
                return await conn.fetchval(query, *args)
            finally:
                await conn.close()

        return run_async(run)


async def _seed(dsn: str, now: datetime) -> factories.PanelWorld:
    await _truncate_all(dsn)
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, now)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def playwright_instance(migrated_database: str) -> Iterator[Playwright]:
    # Pede o banco antes: as fixtures de sessao do banco usam asyncio.run, que nao pode rodar
    # depois que o Playwright sincrono assume o laco desta thread.
    if sync_playwright is None:
        pytest.skip("playwright nao instalado (E2E opcional)")
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    try:
        launched = playwright_instance.chromium.launch(
            headless=True,
            # So localhost resolve: nada sai para a rede, nem por redirecionamento.
            args=["--host-resolver-rules=MAP localhost 127.0.0.1, MAP * ~NOTFOUND"],
        )
    except Exception as exc:  # Chromium ausente: `playwright install chromium`
        pytest.skip(f"Chromium do Playwright indisponivel: {type(exc).__name__}")
    try:
        yield launched
    finally:
        launched.close()


@pytest.fixture
def live_server(migrated_database: str) -> Iterator[LiveServer]:
    """App real com o mundo semeado (duas marcas, tres vendedores, tickets) e IAM falso."""
    now = datetime.now(UTC).replace(microsecond=0)
    world = run_async(lambda: _seed(migrated_database, now))
    iam = FakeIam(email=ADMIN)
    port = _free_port()
    settings = make_settings(
        migrated_database,
        panel_public_host="localhost",
        production=False,
        gemini_api_key="",  # assistente desligado (PN-50)
    )
    app = build_app(settings, iam, SystemClock())  # type: ignore[arg-type]
    app.state.panel_rate_limit = (100_000, 60)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn do E2E nao subiu")
        time.sleep(0.05)
    try:
        yield LiveServer(f"http://localhost:{port}", migrated_database, iam, now, world)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _route(route: Any) -> None:
    host = (urlparse(route.request.url).hostname or "").lower()
    if host in LOCAL_HOSTS:
        route.continue_()
    else:
        route.abort()


@pytest.fixture
def context(browser: Browser, live_server: LiveServer) -> Iterator[BrowserContext]:
    ctx = browser.new_context(base_url=live_server.base_url, locale="pt-BR")
    ctx.route("**/*", _route)
    ctx.set_default_timeout(10_000)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context: BrowserContext) -> Page:
    return context.new_page()


def login(context: BrowserContext) -> None:
    """Cookie do IAM (o FakeIam aceita qualquer valor): o painel troca pela propria sessao."""
    context.add_cookies(
        [{"name": "iam_session", "value": "iam-valido", "domain": "localhost", "path": "/"}]
    )


@pytest.fixture
def admin_page(context: BrowserContext, page: Page) -> Page:
    """Pagina de um admin ja autenticado pelo IAM falso."""
    login(context)
    return page


def no_native_dialogs(page: Page) -> list[str]:
    """Registra toda janela nativa (alert/confirm/prompt) e a recusa; devolve a lista."""
    seen: list[str] = []

    def on_dialog(dialog: Any) -> None:
        seen.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    page.on("dialog", on_dialog)
    return seen
