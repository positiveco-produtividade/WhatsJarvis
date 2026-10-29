"""Limite HTTP por IP: janela deslizante, X-Real-IP, 429 (spec 4.3.1)."""

from typing import Any

import pytest
from fastapi import HTTPException, Request

from app.core.clock import FakeClock
from app.services.rate_limit import SlidingWindowLimiter, WebhookRateLimiter, client_ip


def make_request(headers: dict[str, str] | None = None, peer: str | None = "10.0.0.9") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/webhook/whatsapp",
        "headers": raw,
        "client": (peer, 1234) if peer else None,
    }
    return Request(scope)


def test_client_ip_prefere_x_real_ip() -> None:
    assert client_ip(make_request({"X-Real-IP": " 1.2.3.4 "})) == "1.2.3.4"


def test_client_ip_ignora_x_forwarded_for() -> None:
    req = make_request({"X-Forwarded-For": "6.6.6.6"}, peer="10.0.0.9")
    assert client_ip(req) == "10.0.0.9"


def test_client_ip_sem_nada_e_unknown() -> None:
    assert client_ip(make_request(peer=None)) == "unknown"


def test_permite_ate_o_maximo_e_bloqueia_o_seguinte() -> None:
    limiter = WebhookRateLimiter(3, 60, FakeClock())
    req = make_request({"X-Real-IP": "1.1.1.1"})
    for _ in range(3):
        limiter(req)
    with pytest.raises(HTTPException) as exc:
        limiter(req)
    assert exc.value.status_code == 429
    assert exc.value.headers is not None
    assert exc.value.headers["Retry-After"] == "60"


def test_janela_desliza_e_libera_depois() -> None:
    clock = FakeClock()
    limiter = WebhookRateLimiter(2, 60, clock)
    req = make_request({"X-Real-IP": "1.1.1.1"})
    limiter(req)
    clock.advance(seconds=30)
    limiter(req)
    assert limiter.retry_after(req) == 30  # o 1o acesso sai da janela em 30 s
    clock.advance(seconds=30)
    limiter(req)  # o 1o saiu; cabe um
    assert limiter.retry_after(req) == 30


def test_ips_diferentes_tem_baldes_separados() -> None:
    limiter = WebhookRateLimiter(1, 60, FakeClock())
    limiter(make_request({"X-Real-IP": "1.1.1.1"}))
    limiter(make_request({"X-Real-IP": "2.2.2.2"}))
    with pytest.raises(HTTPException):
        limiter(make_request({"X-Real-IP": "1.1.1.1"}))


def test_x_forwarded_for_forjado_nao_burla_o_limite() -> None:
    limiter = WebhookRateLimiter(1, 60, FakeClock())
    limiter(make_request({"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "7.7.7.1"}))
    with pytest.raises(HTTPException):
        limiter(make_request({"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "7.7.7.2"}))


def test_acesso_bloqueado_nao_estende_a_janela() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    assert limiter.check("k", 1, 60) == 0
    clock.advance(seconds=59)
    assert limiter.check("k", 1, 60) == 1
    clock.advance(seconds=1)
    assert limiter.check("k", 1, 60) == 0


def test_chaves_ociosas_sao_varridas() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock)
    for i in range(10):
        limiter.check(f"ip{i}", 5, 60)
    clock.advance(seconds=120)
    for _ in range(1024):
        limiter.check("ativo", 10**9, 60)
    assert limiter.size() == 1
