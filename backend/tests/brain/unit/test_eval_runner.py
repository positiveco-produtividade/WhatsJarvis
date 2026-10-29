"""Executor do gate de avaliacao (CB-T29 / CB-21): modo palavras, nota, saida e privacidade."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.brain import eval as ev
from app.services.brain.eval import CaseResult, Summary

GUIA = {
    "itens": [
        {"marca": "Jungle", "q": "Jungle tem gluten?", "a": "Jungle e sem gluten."},
        {"marca": "Jungle", "q": "Qual o telefone do Jungle?", "a": "Ligue 11 91234-5678 agora."},
        {"marca": "Possible", "q": "O que e o Possible?", "a": "Possible e uma linha de teste."},
    ]
}
REG = {"brands": ["jungle"], "ufs": ["SP"]}


def _case(cid: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": cid, "category": "guia", "question": "Jungle tem gluten?", "registration": REG,
        "expected": "responde", "refs": ["guia:001"],
    }
    base.update(over)
    return base


def _write(root: Path, cases: list[dict[str, Any]]) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    cases_dir = root / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.jsonl").write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    guia = root / "guia.json"
    guia.write_text(json.dumps(GUIA), encoding="utf-8")
    return cases_dir, guia


def _run(tmp_path: Path, cases: list[dict[str, Any]]) -> Summary:
    cases_dir, guia = _write(tmp_path, cases)
    return asyncio.run(
        ev.run_eval(
            ev.load_cases(cases_dir), ev.load_guia(guia), mode=ev.MODE_WORDS,
            settings=ev._settings_words(),
        )
    )


def test_guia_refs_follow_file_order_and_unapproved_answer_is_not_answerable(
    tmp_path: Path,
) -> None:
    _, guia = _write(tmp_path, [_case("x")])
    items = ev.load_guia(guia)
    assert [i.ref for i in items] == ["guia:001", "guia:002", "guia:003"]
    assert [i.answerable for i in items] == [True, False, True]
    assert items[2].brand == "possible"


def test_correct_answer_with_refs_and_must_contain_passes(tmp_path: Path) -> None:
    summary = _run(tmp_path, [_case("a", must_contain=["sem gluten"])])
    assert summary.factual == 1.0 and summary.passed


def test_wrong_ref_and_missing_snippet_fail(tmp_path: Path) -> None:
    summary = _run(
        tmp_path,
        [_case("a", refs=["guia:003"]), _case("b", must_contain=["nao esta na fonte"])],
    )
    assert summary.factual == 0.0
    assert any("nao citou" in p for p in summary.results[0].problems)
    assert any("faltou trecho" in p for p in summary.results[1].problems)


def test_nao_sei_counts_when_escalated(tmp_path: Path) -> None:
    case = _case("a", question="Qual a cotacao do dolar hoje?", expected="nao_sei", refs=[])
    summary = _run(tmp_path, [case])
    assert summary.results[0].outcome == "no_answer" and summary.results[0].ok


def test_unanswerable_source_item_is_reported_not_crashing(tmp_path: Path) -> None:
    case = _case("a", question="Qual o telefone do Jungle?", refs=["guia:002"])
    summary = _run(tmp_path, [case])
    assert summary.results[0].skipped == "fonte_sem_resposta_aprovada"
    assert summary.scored == []


def test_followup_is_skipped_in_words_mode(tmp_path: Path) -> None:
    history = [{"role": "user", "text": "Jungle tem gluten?"}, {"role": "assistant", "text": "Nao"}]
    summary = _run(tmp_path, [_case("a", history=history)])
    assert summary.results[0].skipped == "followup_precisa_ia"


def test_inline_items_are_scoped_to_their_case_and_price_rule(tmp_path: Path) -> None:
    item = {
        "ref": "preco_sint:001", "brand": "jungle", "category": "PRECO", "origin": "manual",
        "title": "Qual o preco do Jungle Teste?", "text": "SINTETICO: R$ 7,50 a unidade",
    }
    with_price = _case(
        "p1", category="preco", question="Qual o preco do Jungle Teste?",
        refs=["preco_sint:001"], items=[item], price_in_source=True, must_contain=["R$ 7,50"],
    )
    other = _case(
        "p2", category="preco", question="Qual o preco do Jungle Teste?",
        expected="nao_sei", refs=[], price_in_source=False,
    )
    summary = _run(tmp_path, [with_price, other])
    assert [r.ok for r in summary.results] == [True, True]
    assert summary.price_failures == []


def test_gate_fails_on_any_price_failure_even_above_98(tmp_path: Path) -> None:
    ok = [_case(f"g{i}") for i in range(60)]
    bad_price = _case("p", category="preco", refs=["guia:003"], price_in_source=True)
    summary = _run(tmp_path, [*ok, bad_price])
    assert summary.factual > ev.FACTUAL_MIN
    assert summary.price_failures == ["p"] and not summary.passed


def test_gate_fails_below_98_percent() -> None:
    case = ev.EvalCase("c", "guia", "q", ("jungle",), (), "responde", ())
    results = [CaseResult(case=case, ok=True)] * 97 + [CaseResult(case=case, ok=False)] * 3
    assert Summary(results).factual == 0.97 and not Summary(results).passed


def test_main_exit_codes_and_console_never_shows_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases_dir, guia = _write(tmp_path / "bad", [_case("ok-1"), _case("bad-1", refs=["guia:003"])])
    report = tmp_path / "out" / "r.json"
    args = ["--mode", "palavras", "--cases", str(cases_dir), "--guia", str(guia),
            "--report", str(report)]
    assert ev.main(args) == 1
    console = capsys.readouterr().out
    assert "bad-1" in console and "gluten" not in console and "REPROVADO" in console
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body["categories"]["guia"] == {"ok": 1, "scored": 2, "skipped": 0}
    assert body["cases"][0]["question"] == "Jungle tem gluten?"  # texto so no relatorio local
    cases_ok, guia_ok = _write(tmp_path / "good", [_case("ok-1")])
    args_ok = ["--mode", "palavras", "--cases", str(cases_ok), "--guia", str(guia_ok),
               "--report", str(report)]
    assert ev.main(args_ok) == 0


def test_ia_mode_without_key_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases_dir, guia = _write(tmp_path, [_case("a")])

    def no_key() -> Any:
        raise ev.EvalConfigError("modo ia exige: GEMINI_API_KEY")

    monkeypatch.setattr(ev, "_settings_ai", no_key)
    code = ev.main(["--mode", "ia", "--cases", str(cases_dir), "--guia", str(guia),
                    "--report", str(tmp_path / "r.json")])
    assert code == 2 and "GEMINI_API_KEY" in capsys.readouterr().err


def test_real_case_set_runs_in_words_mode_and_records_score(tmp_path: Path) -> None:
    summary = asyncio.run(
        ev.run_eval(
            ev.load_cases(ev.DEFAULT_CASES), ev.load_guia(ev.DEFAULT_GUIA), mode=ev.MODE_WORDS,
            settings=ev._settings_words(),
        )
    )
    assert len(summary.results) == 245
    assert summary.price_failures == []  # preco: 100% mesmo so com palavras
    ev.write_report(summary, tmp_path / "real.json", ev.MODE_WORDS)
    print(f"factual palavras = {summary.factual:.4f}", summary.by_category())
