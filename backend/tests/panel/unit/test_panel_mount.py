"""Montagem do painel (PN-T06; spec 4.1): rotas, estaticos, templates e registro de lacos."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.panel import routes
from app.panel.services import workers
from app.panel.templating import build_templates, make_static_v, sp_dt

BASE = "http://localhost"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "whatsapp_app_secret": "app-secret-xyz",
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": "access-token-xyz",
        "whatsapp_phone_number_id": "100000000000001",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _paths(app: FastAPI) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


# ---- montagem -------------------------------------------------------------


def test_painel_ligado_monta_estaticos_e_templates() -> None:
    app = main.create_app(make_settings(panel_enabled=True))
    assert "/static" in _paths(app)
    assert app.state.templates is not None
    resp = TestClient(app, base_url=BASE).get("/static/vendor/htmx.min.js")
    assert resp.status_code == 200


def test_painel_desligado_sem_rotas_nem_estaticos() -> None:
    app = main.create_app(make_settings(panel_enabled=False))
    assert "/static" not in _paths(app)
    assert not hasattr(app.state, "templates")
    client = TestClient(app, base_url=BASE)
    assert client.get("/static/vendor/htmx.min.js").status_code == 404
    assert "content-security-policy" not in client.get("/health").headers


def test_health_e_webhook_continuam_com_o_painel() -> None:
    paths = _paths(main.create_app(make_settings(panel_enabled=True)))
    assert {"/health", "/webhook/whatsapp"} <= paths


def test_subida_real_valida_config_do_painel_em_producao(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = make_settings(production=True, panel_enabled=True)
    monkeypatch.setattr(main, "get_settings", lambda: bad)
    with pytest.raises(ValueError, match="PANEL_ADMIN_EMAILS"):
        main.create_app()


def test_subida_real_com_painel_desligado_nao_valida(monkeypatch: pytest.MonkeyPatch) -> None:
    off = make_settings(production=True, panel_enabled=False)
    monkeypatch.setattr(main, "get_settings", lambda: off)
    assert isinstance(main.create_app(), FastAPI)


def test_subida_real_com_config_completa_monta(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = make_settings(
        production=True, panel_admin_emails="caio@positiveco.com.br", panel_csrf_secret="s3gredo"
    )
    monkeypatch.setattr(main, "get_settings", lambda: ok)
    assert "/static" in _paths(main.create_app())


# ---- descoberta de routers ------------------------------------------------


@pytest.fixture
def fake_api_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    pkg = tmp_path / "fake_panel_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in ("b_second", "a_first"):
        letter = name[0]
        (pkg / f"{name}.py").write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            f"@router.get('/dashboard/{letter}')\n"
            f"def {letter}() -> dict[str, str]:\n"
            f"    return {{'r': '{letter}'}}\n"
        )
    (pkg / "helper.py").write_text("X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(routes, "API_PACKAGE", "fake_panel_api")
    yield "fake_panel_api"
    for name in [m for m in sys.modules if m.startswith("fake_panel_api")]:
        del sys.modules[name]


def test_descobre_routers_em_ordem_e_ignora_modulo_sem_router(fake_api_package: str) -> None:
    found = routes.discover_routers()
    assert [r.routes[0].path for r in found] == ["/dashboard/a", "/dashboard/b"]  # type: ignore[attr-defined]


def test_mount_inclui_routers_descobertos(fake_api_package: str) -> None:
    app = FastAPI()
    routes.mount_panel(app)
    assert {"/dashboard/a", "/dashboard/b", "/static"} <= _paths(app)
    assert TestClient(app, base_url=BASE).get("/dashboard/a").json() == {"r": "a"}


def test_sem_pacote_api_nao_ha_routers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "API_PACKAGE", "app.panel.nao_existe_xyz")
    assert routes.discover_routers() == ()


# ---- templating -----------------------------------------------------------


def test_sp_dt_converte_para_sao_paulo() -> None:
    assert sp_dt(datetime(2026, 10, 26, 15, 30, tzinfo=UTC)) == "26/10/2026 12:30"
    assert sp_dt(None) == "—"


def test_static_v_usa_mtime_e_barra_traversal(tmp_path: Path) -> None:
    (tmp_path / "js").mkdir()
    f = tmp_path / "js" / "a.js"
    f.write_text("x")
    static_v = make_static_v(tmp_path)
    assert static_v("js/a.js") == f"/static/js/a.js?v={int(f.stat().st_mtime)}"
    assert static_v("/js/a.js").startswith("/static/js/a.js?v=")
    assert static_v("js/ausente.js") == "/static/js/ausente.js"
    assert "?v=" not in static_v("../../etc/passwd")


def test_templates_com_autoescape_filtro_e_static_v(tmp_path: Path) -> None:
    tpl_dir = tmp_path / "t"
    tpl_dir.mkdir()
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "x.css").write_text("a")
    (tpl_dir / "p.html").write_text(
        "{{ nome }}|{{ quando|sp_dt }}|{{ static_v('x.css') }}|{{ vazio.campo }}"
    )
    templates = build_templates(tpl_dir, tmp_path / "s")
    out = templates.env.get_template("p.html").render(  # type: ignore[union-attr]
        nome="<script>alert(1)</script>", quando=datetime(2026, 1, 2, 12, 0, tzinfo=UTC), vazio={}
    )
    assert "&lt;script&gt;" in out
    assert "<script>" not in out
    assert "|02/01/2026 09:00|/static/x.css?v=" in out


# ---- registro de lacos ----------------------------------------------------


class FakeConn:
    def __init__(self, grant: bool, log: list[str]) -> None:
        self.grant = grant
        self.log = log

    async def fetchval(self, query: str, key: int) -> bool:
        self.log.append("lock" if "try" in query else "unlock")
        return self.grant


class FakePool:
    def __init__(self, grant: bool = True) -> None:
        self.log: list[str] = []
        self.grant = grant

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> FakeConn:
        return FakeConn(self.grant, self.log)

    async def __aexit__(self, *exc: object) -> None:
        return None


def make_loop(calls: list[str], name: str = "demo", fail: bool = False) -> workers.Loop:
    async def run(app: Any) -> None:
        calls.append(name)
        if fail:
            raise RuntimeError("boom")

    return workers.Loop(name=name, interval_seconds=1.0, run=run)


def test_registro_recusa_duplicata_e_loop_invalido() -> None:
    reg = workers.LoopRegistry()
    reg.register(make_loop([], "a"))
    with pytest.raises(ValueError, match="duplicado"):
        reg.register(make_loop([], "a"))
    assert len(reg) == 1
    run = make_loop([]).run
    with pytest.raises(ValueError):
        workers.Loop(name="", interval_seconds=1, run=run)
    with pytest.raises(ValueError):
        workers.Loop(name="x", interval_seconds=0, run=run)


def test_chave_da_trava_e_estavel_e_distinta() -> None:
    a, b = make_loop([], "a"), make_loop([], "b")
    assert a.lock_key == make_loop([], "a").lock_key
    assert a.lock_key != b.lock_key
    assert 0 <= a.lock_key < 2**31


async def test_ciclo_roda_sob_a_trava_consultiva() -> None:
    calls: list[str] = []
    pool = FakePool(grant=True)
    loop = make_loop(calls)
    assert await workers.build_workers(pool, (loop,)).run_once(loop, None) is True
    assert calls == ["demo"]
    assert pool.log == ["lock", "unlock"]


async def test_sem_a_trava_o_ciclo_nao_roda() -> None:
    calls: list[str] = []
    pool = FakePool(grant=False)
    loop = make_loop(calls)
    assert await workers.build_workers(pool, (loop,)).run_once(loop, None) is False
    assert calls == []
    assert pool.log == ["lock"]


async def test_falha_do_laco_libera_a_trava_e_nao_propaga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = FakePool()
    loop = make_loop([], fail=True)
    with caplog.at_level("ERROR"):
        await workers.build_workers(pool, (loop,))._guarded_cycle(loop, None)
    assert pool.log == ["lock", "unlock"]
    assert [r.error for r in caplog.records if hasattr(r, "error")] == ["RuntimeError"]


async def test_start_e_stop_executam_e_encerram_os_lacos() -> None:
    calls: list[str] = []
    ticks = asyncio.Event()

    async def fast_sleep(_: float) -> None:
        ticks.set()
        await asyncio.sleep(0)

    reg = workers.LoopRegistry()
    reg.register(make_loop(calls))
    pw = workers.PanelWorkers(FakePool(), reg, sleep=fast_sleep)
    app = FastAPI()
    await pw.start(app)
    await pw.start(app)  # idempotente
    await asyncio.wait_for(ticks.wait(), 2)
    await pw.stop()
    assert calls
    assert app.state.panel_workers is pw


def test_discover_loops_so_devolve_loops() -> None:
    assert all(isinstance(loop, workers.Loop) for loop in workers.discover_loops())


async def test_helper_do_lifespan_respeita_panel_enabled() -> None:
    app = FastAPI()
    off = await main._start_panel_workers(app, FakePool(), make_settings(panel_enabled=False))  # type: ignore[arg-type]
    assert off is None
    on = await main._start_panel_workers(app, FakePool(), make_settings(panel_enabled=True))  # type: ignore[arg-type]
    assert on is not None
    await on.stop()
