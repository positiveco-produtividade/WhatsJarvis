"""Final do log do servidor em memoria (spec painel.1, 4 "Logs"; task PN-T60; PN-54).

Um handler em anel guarda as ultimas ``MAX_LINES`` linhas JSON **ja redigidas** (mesmo
``JsonFormatter`` e mesmo ``RedactingFilter`` do app). Nada e lido de arquivo do servidor e o
conteudo zera ao reiniciar o processo. ``get_ring()`` liga o handler na raiz uma unica vez
(idempotente) e e chamado quando o modulo da pagina de Logs e importado na montagem do painel.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

from app.core.log_redaction import RedactingFilter
from app.core.logging_json import JsonFormatter

__all__ = ["MAX_LINES", "RingBufferHandler", "get_ring"]

MAX_LINES = 500


class RingBufferHandler(logging.Handler):
    """Guarda as ultimas ``maxlen`` linhas formatadas (a mais nova por ultimo). Thread-safe."""

    def __init__(self, maxlen: int = MAX_LINES) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=maxlen)
        self.setFormatter(JsonFormatter())
        self.addFilter(RedactingFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 - log nunca derruba o chamador (e nao pode logar aqui)
            self.handleError(record)
            return
        self.acquire()
        try:
            self._lines.append(line)
        finally:
            self.release()

    def lines(self) -> list[str]:
        """Copia das linhas guardadas, da mais antiga para a mais nova."""
        self.acquire()
        try:
            return list(self._lines)
        finally:
            self.release()

    def clear(self) -> None:
        self.acquire()
        try:
            self._lines.clear()
        finally:
            self.release()


_ring: RingBufferHandler | None = None
_guard = threading.Lock()


def get_ring() -> RingBufferHandler:
    """O anel unico do processo; garante que esteja ligado na raiz (idempotente)."""
    global _ring
    with _guard:
        if _ring is None:
            _ring = RingBufferHandler()
        root = logging.getLogger()
        if _ring not in root.handlers:
            root.addHandler(_ring)
        return _ring
