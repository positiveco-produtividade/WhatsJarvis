"""Testes de arquitetura do cerebro (CB-T26; CB-22, CB-13, CB-27)."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[3]
APP_DIR = BACKEND_DIR / "app"
BRAIN_DIR = APP_DIR / "services" / "brain"
LLM_FILE = BRAIN_DIR / "llm.py"
GEMINI_HOST = "generativelanguage.googleapis.com"


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_panel(path: Path) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name.split(".")[:2] == ["app", "panel"]]
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:2] == ["app", "panel"] or (
                node.module == "app" and any(a.name == "panel" for a in node.names)
            ):
                hits.append(node.module)
    return hits


def _imports_httpx(path: Path) -> bool:
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "httpx" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx":
            return True
    return False


def _mentions_gemini_host(path: Path) -> bool:
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and GEMINI_HOST in node.value
        for node in ast.walk(_parse(path))
    )


def _brain_files() -> list[Path]:
    return sorted(BRAIN_DIR.rglob("*.py"))


def test_cerebro_nao_importa_o_painel() -> None:
    files = _brain_files()
    assert LLM_FILE in files
    ratings = APP_DIR / "services" / "ratings.py"
    if ratings.exists():  # T21 roda em paralelo; some quando existir.
        files.append(ratings)
    tickets = APP_DIR / "services" / "question_tickets.py"
    assert tickets.exists()
    files.append(tickets)
    offenders = {str(f.relative_to(APP_DIR)): _imports_panel(f) for f in files if _imports_panel(f)}
    assert offenders == {}


@pytest.mark.skipif(
    not (APP_DIR / "services" / "ratings.py").exists(), reason="ratings.py (T21) ainda ausente"
)
def test_ratings_existe_e_nao_importa_o_painel() -> None:
    assert _imports_panel(APP_DIR / "services" / "ratings.py") == []


def test_so_o_llm_importa_httpx_no_cerebro() -> None:
    offenders = [str(f.relative_to(BRAIN_DIR)) for f in _brain_files() if _imports_httpx(f)]
    assert offenders == ["llm.py"]


def test_so_o_llm_referencia_o_host_do_gemini() -> None:
    offenders = [str(f.relative_to(BRAIN_DIR)) for f in _brain_files() if _mentions_gemini_host(f)]
    assert offenders == ["llm.py"]


def test_importar_o_pipeline_nao_carrega_o_painel() -> None:
    code = (
        "import sys, app.services.pipeline;"
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
