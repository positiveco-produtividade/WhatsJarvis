"""Pool asyncpg e traducao de erros transitorios de banco.

O DSN vem de quem chama (o main.py passa o valor de Settings); este modulo nao
importa Settings.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import asyncpg

from app.core.errors import TransientDbError

# Erros em que repetir a operacao (ou responder 503 para a Meta reenviar) faz sentido.
_TRANSIENT: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,  # inclui ConnectionDoesNotExist e CannotConnectNow
    asyncpg.InterfaceError,  # pool fechando, conexao perdida
    asyncpg.DeadlockDetectedError,
    asyncpg.SerializationError,
    asyncpg.TooManyConnectionsError,
    asyncio.TimeoutError,  # espera por conexao do pool esgotado
    ConnectionError,
)


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, _TRANSIENT)


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Converte erros transitorios em TransientDbError; os demais passam intactos."""
    try:
        yield
    except TransientDbError:
        raise
    except _TRANSIENT as exc:
        raise TransientDbError(str(exc) or type(exc).__name__) from exc


async def create_pool(dsn: str, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    with translate_db_errors():
        pool: asyncpg.Pool | None = await asyncpg.create_pool(
            dsn, min_size=min_size, max_size=max_size, command_timeout=30
        )
    if pool is None:  # pragma: no cover - asyncpg so devolve None com parametros invalidos
        raise TransientDbError("pool nao criado")
    return pool


@asynccontextmanager
async def transaction(conn: Any) -> AsyncIterator[Any]:
    """Transacao na conexao dada; erros transitorios saem como TransientDbError."""
    with translate_db_errors():
        async with conn.transaction():
            yield conn
