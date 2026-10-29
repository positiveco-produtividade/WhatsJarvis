import pytest

from app.db import migrate
from app.db.migrate import MigrationError


def _fake_run(result: int | Exception):  # type: ignore[no-untyped-def]
    async def run(mode: str, dsn: str, directory: object, table: str = "") -> int:
        if isinstance(result, Exception):
            raise result
        return result

    return run


def test_sem_database_url_sai_com_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert migrate.main(["--check"]) == migrate.EXIT_ERROR


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (migrate.EXIT_OK, 0),
        (migrate.EXIT_PENDING, 1),
        (MigrationError("checksum"), 2),
        (OSError("sem rede"), 2),
    ],
)
def test_mapeamento_de_codigos(
    monkeypatch: pytest.MonkeyPatch, result: int | Exception, expected: int
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@localhost/z_test")
    monkeypatch.setattr(migrate, "_run", _fake_run(result))
    assert migrate.main(["--check"]) == expected
