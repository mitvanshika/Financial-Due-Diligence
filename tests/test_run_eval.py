"""eval/run_eval.py: what each result saves about its score, and hand verdicts
surviving the next run. Offline: answer() is replaced by a stand-in."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import dd.generation as gen
from dd.models import (Answer, AttemptRecord, Chunk, Citation, ConfidenceBreakdown,
                       GradedChunk, RetrievalOutcome, RetrievedChunk)

_spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).parents[1] / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


@pytest.fixture(autouse=True)
def scratch_files(tmp_path, monkeypatch):
    """Never touch the real eval/ files."""
    for name, file in (("RESULTS", "results.jsonl"), ("REVIEW", "review.md"), ("HAND_GRADES", "hand_grades.json")):
        monkeypatch.setattr(run_eval, name, tmp_path / file)
    return tmp_path


def an_answer(text="Equity was $73,733 million [1].", refused=False) -> Answer:
    chunk = RetrievedChunk(chunk=Chunk(chunk_id="c1", text="Total shareholders' equity | 73,733", company="Apple Inc.",
                                       ticker="AAPL", form="10-K", fiscal_year="FY2025", item="Item 8", section="FS"))
    equity = {"table": "balance_sheet", "label": "Total shareholders' equity", "year": "2025",
              "value": 73_733e6, "_relevance": 0.9}
    outcome = RetrievalOutcome(
        original_query="q", final_query="q", succeeded=not refused, abstain_reason="nothing relevant" if refused else "",
        relevance_threshold=0.5, chunks=[GradedChunk(retrieved=chunk, relevance=0.85)], structured_facts=[equity],
        attempts=[AttemptRecord(attempt=1, query="q", strategy="original", n_retrieved=6, n_relevant=2,
                                mean_relevance=0.4, chunk_ids=["c1"])],
    )
    if refused:
        return Answer(query="q", answered=False, text="I can't answer that.", outcome=outcome)
    return Answer(
        query="q", answered=True, text=text, outcome=outcome,
        citations=[Citation(marker="[1]", chunk_id="c1", source="AAPL | 10-K | FY2025 | Item 8")],
        confidence=ConfidenceBreakdown(score=0.69, band="medium", hybrid_agreement=1.0, rerank_strength=0.9,
                                       loop_cleanliness=1.0, notes=["needed 2 retrieval attempts"]),
    )


# --- part 1: why an answer scored what it did ---------------------------------------


def test_a_result_saves_what_produced_its_score():
    e = run_eval.explain(an_answer())
    assert e["notes"] == ["needed 2 retrieval attempts"]
    assert e["signals"] == {"hybrid_agreement": 1.0, "rerank_strength": 0.9, "loop_cleanliness": 1.0}
    assert e["cited_grades"] == {"[1]": 0.85}
    assert e["figures"] == {"given": 1, "cited": 0}  # the 5.8 situation, readable from the file
    assert e["attempt_log"] == [{"strategy": "original", "query": "q", "relevant": 2, "retrieved": 6, "note": ""}]
    assert "refusal_reason" not in e


def test_a_refusal_saves_why():
    e = run_eval.explain(an_answer(refused=True))
    assert e["notes"] == [] and e["refusal_reason"] == "nothing relevant"


def test_the_run_writes_the_explanation_to_results(monkeypatch):
    monkeypatch.setattr(gen, "answer", lambda q: an_answer())
    results = run_eval.run({"numeric_lookup"}, 1)
    saved = [json.loads(line) for line in run_eval.RESULTS.read_text(encoding="utf-8").splitlines()][1:]
    assert len(saved) == len(results) == 8
    assert saved[0]["notes"] == ["needed 2 retrieval attempts"] and saved[0]["figures"]["given"] == 1


# --- part 2: hand verdicts survive the next run ----------------------------------------


def hand_result(text="Services grew because of advertising [1].", band="high", run=1) -> dict:
    return {"question": "Why did Services grow?", "run": run, "type": "narrative_comprehension",
            "expected": "Advertising, App Store, cloud.", "answered": True, "text": text,
            "citations": ["[1] AAPL | 10-K"], "confidence": 0.9, "band": band, "correct": None,
            "elapsed_s": 1.0, "notes": ["a note"]}


def type_verdict(verdict: str) -> None:
    review = run_eval.REVIEW.read_text(encoding="utf-8")
    run_eval.REVIEW.write_text(review.replace("**Verdict:** ", f"**Verdict:** {verdict}", 1), encoding="utf-8")


def test_a_verdict_is_saved_before_review_is_rewritten_and_comes_back_filled_in():
    results = [hand_result()]
    run_eval.write_review(results, {})
    review = run_eval.REVIEW.read_text(encoding="utf-8")
    assert "*note: a note*" in review and "**Verdict:** \n" in review + "\n"

    type_verdict("partly - misses the App Store")
    grades = run_eval.collect_verdicts()
    assert run_eval.verdict_of(results[0], grades) == "partly"
    assert json.loads(run_eval.HAND_GRADES.read_text(encoding="utf-8"))  # on disk

    run_eval.write_review(results, grades)  # the next run's review.md
    assert "**Verdict:** partly misses the App Store" in run_eval.REVIEW.read_text(encoding="utf-8")


def test_a_different_answer_to_the_same_question_needs_its_own_verdict():
    run_eval.write_review([hand_result()], {})
    type_verdict("correct")
    grades = run_eval.collect_verdicts()
    assert run_eval.verdict_of(hand_result(text="Services grew because of cloud [1]."), grades) is None


def test_a_blank_verdict_keeps_the_saved_one_and_an_unknown_word_is_not_saved(capsys):
    run_eval.write_review([hand_result()], {})
    type_verdict("wrong")
    run_eval.collect_verdicts()
    review = run_eval.REVIEW.read_text(encoding="utf-8")
    run_eval.REVIEW.write_text(review.replace("**Verdict:** wrong", "**Verdict:** "), encoding="utf-8")
    assert run_eval.verdict_of(hand_result(), run_eval.collect_verdicts()) == "wrong"

    run_eval.REVIEW.write_text(review.replace("**Verdict:** wrong", "**Verdict:** maybe"), encoding="utf-8")
    assert run_eval.verdict_of(hand_result(), run_eval.collect_verdicts()) == "wrong"
    assert "'maybe' not understood" in capsys.readouterr().out


def test_the_summary_counts_hand_verdicts_and_their_bands(capsys):
    a, b = hand_result(band="high"), hand_result(text="Because of cloud [1].", band="medium")
    c = hand_result(text="Because of ads [1].", band="medium")
    numeric = {**hand_result(text="$7.46 [1]", band="high"), "question": "EPS?", "type": "numeric_lookup",
               "correct": True}
    run_eval.write_review([a, b, c], {})
    review = run_eval.REVIEW.read_text(encoding="utf-8")
    run_eval.REVIEW.write_text(review.replace("**Verdict:** ", "**Verdict:** correct", 1)
                               .replace("**Verdict:** \n", "**Verdict:** partly\n", 1), encoding="utf-8")
    grades = run_eval.collect_verdicts()
    run_eval.summarize([numeric, a, b, c], 1, grades)
    out = capsys.readouterr().out
    assert "graded by hand: 1 correct, 1 partly; 1 still to grade" in out
    assert "high    2/2 correct" in out
    assert "medium  0/1 correct (1 partly)" in out
    assert "1 answer(s) not counted: not graded by hand yet" in out
