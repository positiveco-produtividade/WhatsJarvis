"""PN-T68: o tailwind.css versionado confere com o build do input.css.

Reconstroi o CSS num arquivo temporario quando o binario do Tailwind existe
(maquina de desenvolvimento) e compara. Sem o binario (CI sem Node) o teste e
pulado com motivo explicito.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[4] / "frontend"
CSS_FILE = FRONTEND / "static" / "css" / "tailwind.css"
INPUT_CSS = FRONTEND / "static" / "css" / "input.css"
AUTO1_BIN = Path(
    "C:/Users/User/Documents/GitHub/agente-instagram-direct-n8n/"
    "frontend/node_modules/.bin/tailwindcss.cmd"
)


def _find_binary() -> str | None:
    env = os.environ.get("TAILWIND_BIN")
    if env and Path(env).exists():
        return env
    if AUTO1_BIN.exists():
        return str(AUTO1_BIN)
    return shutil.which("tailwindcss")


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def test_tailwind_css_esta_em_sincronia(tmp_path: Path) -> None:
    binary = _find_binary()
    if binary is None:
        pytest.skip(
            "binario do Tailwind ausente (CI sem Node); defina TAILWIND_BIN ou "
            "rode o build manual descrito em frontend/package.json"
        )
    out = tmp_path / "tailwind.css"
    result = subprocess.run(
        [binary, "-i", str(INPUT_CSS), "-o", str(out), "--minify"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, "build do Tailwind falhou"
    assert _normalize(out.read_text(encoding="utf-8")) == _normalize(
        CSS_FILE.read_text(encoding="utf-8")
    ), "tailwind.css desatualizado: regenere com o comando de build (sem npm install)"
