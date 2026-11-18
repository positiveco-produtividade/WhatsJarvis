"""deploy/deploy.sh: sintaxe e --dry-run (nao conecta em servidor nenhum)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "deploy.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash ausente")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_sintaxe() -> None:
    assert BASH is not None
    result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_dry_run_mostra_os_passos_sem_conectar() -> None:
    result = _run("servidor-de-teste", "--dry-run", "--migrate", "--pip")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for text in ("Enviar o codigo para servidor-de-teste", "pip install", "--status",
                 "Aplicar migrations como dono", "pm2 restart jarvis", "health"):  # fmt: skip
        assert text in out, text
    assert "continuar?" not in out  # dry-run nao pergunta nem roda nada


def test_sem_migrate_so_lista_pendentes() -> None:
    out = _run("--dry-run").stdout
    assert "--status" in out
    assert "Aplicar migrations" not in out
    assert "pip install" not in out


def test_nunca_envia_nem_imprime_o_env() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--exclude='.env*'" in text
    assert "cat .env" not in text and "echo $MIGRATION_DATABASE_URL" not in text
    # A URL do dono e lida no servidor, so dentro do comando do migrate.
    assert "MIGRATION_DATABASE_URL" in _run("--dry-run").stdout
    assert "postgresql://" not in _run("--dry-run", "--migrate").stdout


def test_opcao_desconhecida_recusada() -> None:
    result = _run("--apagar-tudo")
    assert result.returncode == 2
    assert "opcao desconhecida" in result.stderr
