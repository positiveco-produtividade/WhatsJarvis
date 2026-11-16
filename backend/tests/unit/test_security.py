import pytest
from pydantic import SecretStr

from app.core.security import (
    compute_hub_signature,
    compute_waha_hmac,
    constant_time_equals,
    verify_hub_signature,
    verify_waha_hmac,
)

SECRET = "app-secret-de-teste"
RAW = b'{"object":"whatsapp_business_account","x":"a\xc3\xa7\xc3\xa3o"}'


@pytest.mark.security
def test_valid_signature_accepted() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, SECRET), SECRET) is True


@pytest.mark.security
def test_signature_is_over_raw_bytes_not_reserialized_json() -> None:
    header = compute_hub_signature(RAW, SECRET)
    reserialized = RAW.replace(b'{"object"', b'{ "object"')
    assert verify_hub_signature(reserialized, header, SECRET) is False


@pytest.mark.security
def test_secretstr_secret_supported() -> None:
    header = compute_hub_signature(RAW, SecretStr(SECRET))
    assert verify_hub_signature(RAW, header, SecretStr(SECRET)) is True


@pytest.mark.security
@pytest.mark.parametrize(
    "header",
    [None, "", "sha256=", "abc", "sha1=deadbeef", "sha256=" + "0" * 64, "SHA256=ab"],
)
def test_bad_headers_rejected(header: str | None) -> None:
    assert verify_hub_signature(RAW, header, SECRET) is False


@pytest.mark.security
@pytest.mark.parametrize(
    "header",
    ["sha256=çãõ", "sha256=" + "a" * 63 + "é", "‮sha256=abc", "sha256=😀"],
)
def test_non_ascii_header_returns_false_without_exception(header: str) -> None:
    assert verify_hub_signature(RAW, header, SECRET) is False


@pytest.mark.security
def test_non_ascii_header_does_not_reject_valid_ascii_one() -> None:
    header = compute_hub_signature(RAW, SECRET)
    assert verify_hub_signature(RAW, header, SECRET) is True
    assert verify_hub_signature(RAW, header + "é", SECRET) is False


@pytest.mark.security
def test_empty_secret_never_validates() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, ""), "") is False


@pytest.mark.security
def test_wrong_secret_rejected() -> None:
    assert verify_hub_signature(RAW, compute_hub_signature(RAW, "outro"), SECRET) is False


@pytest.mark.security
def test_constant_time_equals() -> None:
    assert constant_time_equals("token-123", "token-123") is True
    assert constant_time_equals("token-124", "token-123") is False
    assert constant_time_equals(None, "token-123") is False
    assert constant_time_equals("", "") is False
    assert constant_time_equals("tökén", "tökén") is True
    assert constant_time_equals("\ud83d", "token") is False
    assert constant_time_equals("token-123", SecretStr("token-123")) is True


# ---- WAHA (spec canal-waha.1, WA-4; task WA-T06) ------------------------------------

WAHA_SECRET = "hmac-waha-de-teste"
WAHA_RAW = '{"event":"message","session":"jarvis","payload":{"body":"ação"}}'.encode()


@pytest.mark.security
def test_waha_hmac_valido_aceito() -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET)
    assert len(header) == 128  # SHA-512 em hex
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", WAHA_SECRET) is True


@pytest.mark.security
def test_waha_hmac_aceita_hex_maiusculo_e_espacos_nas_pontas() -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET).upper()
    assert verify_waha_hmac(WAHA_RAW, f" {header} ", " SHA512 ", WAHA_SECRET) is True


@pytest.mark.security
def test_waha_hmac_secretstr() -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET)
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", SecretStr(WAHA_SECRET)) is True


@pytest.mark.security
def test_waha_hmac_e_sobre_os_bytes_brutos() -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET)
    reserializado = WAHA_RAW.replace(b'":"', b'": "')
    assert verify_waha_hmac(reserializado, header, "sha512", WAHA_SECRET) is False


@pytest.mark.security
def test_waha_hmac_corpo_alterado_recusado() -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET)
    assert verify_waha_hmac(WAHA_RAW + b" ", header, "sha512", WAHA_SECRET) is False


@pytest.mark.security
def test_waha_hmac_chave_errada_recusada() -> None:
    header = compute_waha_hmac(WAHA_RAW, "outra-chave")
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", WAHA_SECRET) is False


@pytest.mark.security
@pytest.mark.parametrize("algorithm", [None, "", "sha256", "md5", "sha512 extra"])
def test_waha_hmac_algoritmo_diferente_recusado(algorithm: str | None) -> None:
    header = compute_waha_hmac(WAHA_RAW, WAHA_SECRET)
    assert verify_waha_hmac(WAHA_RAW, header, algorithm, WAHA_SECRET) is False


@pytest.mark.security
@pytest.mark.parametrize("header", [None, "", "abc", "zz" * 64, "sha512=" + "0" * 128])
def test_waha_hmac_cabecalho_invalido_recusado(header: str | None) -> None:
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", WAHA_SECRET) is False


@pytest.mark.security
def test_waha_hmac_cabecalho_nao_ascii_sem_excecao() -> None:
    assert verify_waha_hmac(WAHA_RAW, "ç" * 128, "sha512", WAHA_SECRET) is False


@pytest.mark.security
def test_waha_hmac_segredo_vazio_nunca_valida() -> None:
    header = compute_waha_hmac(WAHA_RAW, "")
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", "") is False
    assert verify_waha_hmac(WAHA_RAW, header, "sha512", SecretStr("")) is False
