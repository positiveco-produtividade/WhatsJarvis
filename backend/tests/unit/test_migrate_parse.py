from pathlib import Path

import pytest

from app.db.migrate import MigrationError, discover


def _write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql, encoding="utf-8", newline="")


def test_ordena_pelo_prefixo_numerico(tmp_path: Path) -> None:
    for name in ("010_dez.sql", "002_dois.sql", "001_um.sql", "100_cem.sql"):
        _write(tmp_path, name)
    assert [m.number for m in discover(tmp_path)] == [1, 2, 10, 100]
    assert discover(tmp_path)[0].version == "001_um"


def test_recusa_prefixo_duplicado(tmp_path: Path) -> None:
    _write(tmp_path, "001_a.sql")
    _write(tmp_path, "001_b.sql")
    with pytest.raises(MigrationError, match="duplicado"):
        discover(tmp_path)


def test_recusa_nome_invalido(tmp_path: Path) -> None:
    _write(tmp_path, "abc.sql")
    with pytest.raises(MigrationError, match="invalido"):
        discover(tmp_path)


def test_checksum_muda_com_o_conteudo(tmp_path: Path) -> None:
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    first = discover(tmp_path)[0].checksum
    _write(tmp_path, "001_a.sql", "SELECT 2;")
    assert discover(tmp_path)[0].checksum != first


def test_checksum_ignora_fim_de_linha(tmp_path: Path) -> None:
    _write(tmp_path, "001_a.sql", "SELECT 1;\nSELECT 2;\n")
    lf = discover(tmp_path)[0].checksum
    _write(tmp_path, "001_a.sql", "SELECT 1;\r\nSELECT 2;\r\n")
    assert discover(tmp_path)[0].checksum == lf


def test_diretorio_vazio_ou_inexistente(tmp_path: Path) -> None:
    assert discover(tmp_path) == []
    assert discover(tmp_path / "nao_existe") == []
