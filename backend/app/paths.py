"""Caminhos do frontend (spec painel.1, 4.1): `frontend/` fica ao lado de `backend/`."""

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
FRONTEND_DIR: Path = REPO_ROOT / "frontend"
TEMPLATES_DIR: Path = FRONTEND_DIR / "templates"
STATIC_DIR: Path = FRONTEND_DIR / "static"
