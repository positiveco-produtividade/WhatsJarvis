from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.panel.services import formatting as f

SP = timezone(timedelta(hours=-3))
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=SP)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (59, "59 s"),
        (60, "1 min"),
        (3599, "59 min"),
        (3600, "1 h"),
        (3660, "1 h 1 min"),
        (7500, "2 h 5 min"),
        (-5, "0 s"),
    ],
)
def test_response_time_label_limits(seconds: float, expected: str) -> None:
    assert f.response_time_label(seconds) == expected


def test_responded_in_across_midnight() -> None:
    q = datetime(2026, 10, 21, 23, 59, 30, tzinfo=SP)
    a = datetime(2026, 10, 22, 0, 0, 40, tzinfo=SP)
    assert f.responded_in(q, a) == "respondeu em 1 min"


def test_day_label_today_yesterday_and_date() -> None:
    assert f.day_label(datetime(2026, 10, 22, 0, 0, tzinfo=SP), NOW) == "Hoje"
    assert f.day_label(datetime(2026, 10, 21, 23, 59, tzinfo=SP), NOW) == "Ontem"
    assert f.day_label(datetime(2026, 10, 20, 12, 0, tzinfo=SP), NOW) == "20/10/2026"


def test_day_label_uses_sao_paulo_day_not_utc() -> None:
    # 02:30 UTC de 23/10 ainda e 22/10 (23:30) em Sao Paulo.
    late = datetime(2026, 10, 23, 2, 30, tzinfo=UTC)
    assert f.day_label(late, NOW) == "Hoje"
    # E "agora" em UTC apos a meia-noite de Sao Paulo vira o dia seguinte la.
    assert f.day_label(NOW, datetime(2026, 10, 23, 3, 30, tzinfo=UTC)) == "Ontem"


def test_is_new_day_flips_at_sao_paulo_midnight() -> None:
    a = datetime(2026, 10, 22, 2, 59, tzinfo=UTC)  # 21/10 23:59 SP
    b = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # 22/10 00:00 SP
    assert f.is_new_day(None, a)
    assert not f.is_new_day(a, a)
    assert f.is_new_day(a, b)


def test_datetime_formats_in_sao_paulo() -> None:
    value = datetime(2026, 10, 22, 12, 42, 13, tzinfo=UTC)  # 09:42 SP
    assert f.format_date(value) == "22/10/2026"
    assert f.format_time(value) == "09:42"
    assert f.format_datetime(value) == "22/10/2026 09:42"
    assert f.format_short_datetime(value) == "22/10 09:42"


def test_none_formats_to_dash() -> None:
    assert f.format_date(None) == "—"
    assert f.format_time(None) == "—"
    assert f.format_datetime(None) == "—"
    assert f.format_short_datetime(None) == "—"
    assert f.format_int(None) == "—"
    assert f.format_decimal(None) == "—"
    assert f.format_percent(None) == "—"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="sem fuso"):
        f.format_date(datetime(2026, 10, 22, 9, 0))


def test_numbers_ptbr() -> None:
    assert f.format_int(1234567) == "1.234.567"
    assert f.format_int(12) == "12"
    assert f.format_decimal(1234.5) == "1.234,5"
    assert f.format_decimal(0.25, 2) == "0,25"
    assert f.format_percent(87.5, 1) == "87,5%"
    assert f.format_percent(87) == "87%"


def test_fold_text_removes_accents_keeps_length() -> None:
    assert f.fold_text("José Conceição") == "jose conceicao"
    assert len(f.fold_text("Ação")) == len("Ação")
    assert f.fold_text("José") == "jose"  # decomposto


def test_digits_only() -> None:
    assert f.digits_only("+55 (85) 9••••-1201") == "5585" + "9" + "1201"
    assert f.digits_only(None) == ""


def test_highlight_parts_accent_insensitive() -> None:
    assert f.highlight_parts("José Silva", "jose") == [("José", True), (" Silva", False)]
    assert f.highlight_parts("Ana Maria", "ana") == [("Ana", True), (" Maria", False)]
    assert f.highlight_parts("banana", "an") == [
        ("b", False),
        ("an", True),
        ("an", True),
        ("a", False),
    ]


def test_highlight_parts_no_query_or_no_match() -> None:
    assert f.highlight_parts("Ana", "") == [("Ana", False)]
    assert f.highlight_parts("Ana", "zzz") == [("Ana", False)]
    assert f.highlight_parts("", "x") == []


def test_highlight_parts_returns_data_not_html() -> None:
    parts = f.highlight_parts("<b>x</b>", "x")
    assert ("<b>", False) in parts
    assert ("x", True) in parts
