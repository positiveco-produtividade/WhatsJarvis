"""CSRF do painel: token HMAC e Origin/Referer (PN-4; PN-T11)."""
from __future__ import annotations

from app.panel.auth.csrf import check_origin, csrf_token, verify_csrf, verify_token

HOST = "jarvis.positiveco.com.br"
SECRET = "segredo-de-teste"
SESSION = "sessao-abc"


def _verify(**kw: object) -> bool:
    args: dict[str, object] = {
        "secret": SECRET,
        "session_csrf_secret": SESSION,
        "provided_token": csrf_token(SECRET, SESSION),
        "origin": f"https://{HOST}",
        "referer": None,
        "public_host": HOST,
    }
    args.update(kw)
    return verify_csrf(**args)  # type: ignore[arg-type]


def test_token_e_deterministico_e_depende_dos_dois_segredos() -> None:
    assert csrf_token(SECRET, SESSION) == csrf_token(SECRET, SESSION)
    assert csrf_token(SECRET, SESSION) != csrf_token("outro", SESSION)
    assert csrf_token(SECRET, SESSION) != csrf_token(SECRET, "outra-sessao")
    assert len(csrf_token(SECRET, SESSION)) == 64


def test_verify_token_aceita_valido_e_rejeita_adulterado_ou_vazio() -> None:
    good = csrf_token(SECRET, SESSION)
    assert verify_token(SECRET, SESSION, good)
    assert not verify_token(SECRET, SESSION, good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not verify_token(SECRET, SESSION, "")
    assert not verify_token(SECRET, SESSION, None)
    assert not verify_token("", SESSION, good)
    assert not verify_token(SECRET, "", good)
    assert not verify_token(SECRET, SESSION, "çãõ-nao-ascii")


def test_origin_do_host_publico_passa() -> None:
    assert check_origin(f"https://{HOST}", None, HOST)
    assert check_origin(f"https://{HOST.upper()}", None, HOST)


def test_referer_vale_quando_nao_ha_origin() -> None:
    assert check_origin(None, f"https://{HOST}/dashboard/faq", HOST)
    assert check_origin("", f"https://{HOST}/dashboard", HOST)


def test_origin_prevalece_sobre_referer() -> None:
    assert not check_origin("https://evil.test", f"https://{HOST}/x", HOST)


def test_origem_ausente_nula_ou_estranha_falha() -> None:
    assert not check_origin(None, None, HOST)
    assert not check_origin("null", None, HOST)
    assert not check_origin("https://evil.test", None, HOST)
    assert not check_origin(f"https://{HOST}.evil.test", None, HOST)
    assert not check_origin(f"https://evil.test/{HOST}", None, HOST)
    assert not check_origin(f"https://{HOST}@evil.test", None, HOST)
    assert not check_origin("lixo", None, HOST)
    assert not check_origin("https://[::1", None, HOST)


def test_http_so_com_allow_http() -> None:
    assert not check_origin(f"http://{HOST}", None, HOST)
    assert check_origin(f"http://{HOST}", None, HOST, allow_http=True)


def test_verify_csrf_exige_token_e_origem() -> None:
    assert _verify()
    assert not _verify(provided_token=None)
    assert not _verify(provided_token="errado")
    assert not _verify(origin="https://evil.test")
    assert not _verify(origin=None, referer=None)
    assert _verify(origin=None, referer=f"https://{HOST}/dashboard")


def test_token_de_outra_sessao_nao_vale() -> None:
    assert not _verify(provided_token=csrf_token(SECRET, "outra-sessao"))
