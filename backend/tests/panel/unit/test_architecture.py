"""Testes de arquitetura (PN-T06; spec 4.1): o painel e um pacote isolado."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]
APP_DIR = BACKEND_DIR / "app"
# So estes arquivos de PF podem conhecer o painel (montagem e CLI).
ALLOWED_PANEL_IMPORTERS = {APP_DIR / "main.py", APP_DIR / "cli.py"}


def _imports_panel(path: Path) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name.split(".")[:2] == ["app", "panel"]]
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:2] == ["app", "panel"] or (
                node.module == "app" and any(a.name == "panel" for a in node.names)
            ):
                hits.append(node.module)
    return hits


def test_nada_fora_do_painel_importa_app_panel() -> None:
    offenders: dict[str, list[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        if (APP_DIR / "panel") in path.parents or path in ALLOWED_PANEL_IMPORTERS:
            continue
        if hits := _imports_panel(path):
            offenders[str(path.relative_to(APP_DIR))] = hits
    assert offenders == {}


def test_webhook_e_servicos_nao_importam_o_painel() -> None:
    files = [APP_DIR / "api" / "webhook.py", *(APP_DIR / "services").rglob("*.py")]
    assert len(files) > 1
    assert {str(f): _imports_panel(f) for f in files if _imports_panel(f)} == {}


def test_importar_o_webhook_nao_carrega_o_painel() -> None:
    code = (
        "import sys, app.api.webhook, app.services.pipeline, app.services.workers;"
        "print(sorted(m for m in sys.modules if m.startswith('app.panel')))"
    )
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "[]"


def test_painel_nao_usa_import_relativo() -> None:
    for path in (APP_DIR / "panel").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"import relativo em {path}"
