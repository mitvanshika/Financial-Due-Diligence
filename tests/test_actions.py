"""Tests for how Layer 6 keeps its records (src/dd/actions.py).

The checklist, the review queue and the memos are plain files. These tests
point them at a temporary folder, so nothing here touches the real data/.

Run from the project root:   pytest
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import dd.actions as actions
from dd.config import get_config
from dd.models import Answer, Citation, ConfidenceBreakdown, RetrievalOutcome


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "CHECKLIST_PATH", tmp_path / "checklist.json")
    monkeypatch.setattr(actions, "REVIEW_QUEUE_PATH", tmp_path / "review_queue.json")
    monkeypatch.setattr(actions, "MEMO_DIR", tmp_path / "memos")
    return tmp_path


def test_an_empty_file_counts_as_no_entries(paths):
    """A crash can leave a 0-byte file; that must not break every later action."""
    (paths / "checklist.json").write_text("", encoding="utf-8")
    assert actions._load_json(paths / "checklist.json") == []


def test_a_garbled_file_is_reported_by_name_and_left_alone(paths):
    """Guessing "no entries" here would overwrite whatever is in the file."""
    p = paths / "checklist.json"
    p.write_text("[{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="checklist.json"):
        actions._append_json(p, {"id": "x"})
    assert p.read_text(encoding="utf-8") == "[{not json"


def test_a_crash_while_saving_keeps_the_old_entries(paths, monkeypatch):
    p = paths / "checklist.json"
    actions._append_json(p, {"id": "first"})

    real_write = Path.write_text

    def dies_half_way(self, data, *args, **kwargs):
        real_write(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", dies_half_way)
    with pytest.raises(OSError):
        actions._append_json(p, {"id": "second"})
    monkeypatch.setattr(Path, "write_text", real_write)

    assert [e["id"] for e in json.loads(p.read_text(encoding="utf-8"))] == ["first"]


def test_a_question_cannot_be_flagged_twice_while_it_is_open(paths):
    answer = Answer(query="Why did margins fall?", answered=False, text="the filing does not say")
    flag = actions.propose_flag(answer)
    assert actions.validate_flag(flag, answer) == []
    actions.execute_flag(flag, answer)
    assert actions.validate_flag(flag, answer) == ["this question is already in the review queue"]


# --- closing a review flag ---------------------------------------------------------


def test_a_reviewed_flag_can_be_closed_so_the_question_can_be_flagged_again(paths):
    answer = Answer(query="Why did margins fall?", answered=False, text="the filing does not say")
    flag = actions.propose_flag(answer)
    actions.execute_flag(flag, answer)
    flag_id = json.loads((paths / "review_queue.json").read_text(encoding="utf-8"))[0]["id"]

    assert [e["id"] for e in actions.open_flags()] == [flag_id]
    actions.close_flag(flag_id, "checked with the analyst")

    entry = json.loads((paths / "review_queue.json").read_text(encoding="utf-8"))[0]
    assert entry["status"] == "closed" and entry["closed_note"] == "checked with the analyst"
    assert "closed_at" in entry and actions.open_flags() == []
    assert actions.validate_flag(flag, answer) == [], "a closed flag no longer blocks a new one"
    with pytest.raises(ValueError, match="already closed"):
        actions.close_flag(flag_id)
    with pytest.raises(ValueError, match="no open flag"):
        actions.close_flag("nope")


# --- numbers in a draft must come from what the answer cites -------------------------


def _answer_citing_net_income() -> Answer:
    fact = {"table": "income_statement", "label": "Net income", "year": "2025",
            "value": 112_010e6, "concept": "x", "_matched_via": "keyword"}
    outcome = RetrievalOutcome(original_query="q", final_query="q", succeeded=True,
                               relevance_threshold=0.5, structured_facts=[fact])
    return Answer(
        query="What was net income?", answered=True, text="Net income was $112,010 million [S1].",
        citations=[Citation(marker="[S1]", chunk_id="structured:income_statement:Net income:2025",
                            source="AAPL | 10-K | FY2025 | income statement - Net income")],
        confidence=ConfidenceBreakdown(score=0.9, band="high", hybrid_agreement=0.0,
                                       rerank_strength=0.0, loop_cleanliness=1.0),
        outcome=outcome,
    )


def test_a_checklist_item_with_a_number_the_answer_never_had_is_rejected(paths):
    """A real [S1] next to a wrong number is exactly what Layer 5 refuses to
    show; a checklist item must not be a way around that."""
    answer = _answer_citing_net_income()
    good = actions.ChecklistItem(title="Net income check", severity="low", citations=["[S1]"],
                                 summary="Net income was $112,010 million in FY2025.")
    bad = good.model_copy(update={"summary": "Net income was $11.2 billion in FY2025."})
    assert actions.validate_checklist_item(good, answer) == []
    assert any("$11.2 billion" in p for p in actions.validate_checklist_item(bad, answer))


def test_a_memo_with_a_number_the_answer_never_had_is_rejected(paths):
    answer = _answer_citing_net_income()

    def memo(figure: str) -> actions.RiskMemo:
        para1 = f"Net income for fiscal year 2025 was {figure}, as stated in the income statement of the annual filing. [S1]"
        para2 = "This figure is the starting point for any review of the company's profitability and its trend. [S1]"
        return actions.RiskMemo(title="Net income memo", body=para1 + "\n\n" + para2)

    assert actions.validate_memo(memo("$112,010 million"), answer) == []
    assert any("$11.2 billion" in p for p in actions.validate_memo(memo("$11.2 billion"), answer))


# --- broken JSON from the model ------------------------------------------------------


class BadJsonError(Exception):
    status_code = 400

    def __init__(self, message: str = "Error code: 400 - json_validate_failed") -> None:
        super().__init__(message)


class FakeClient:
    """Replies with each outcome in turn: an Exception is raised, text is returned."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.temperatures: list[float] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.temperatures.append(kwargs["temperature"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])


def _ask(client: FakeClient, monkeypatch) -> str:
    monkeypatch.setattr(actions, "get_groq_client", lambda cfg: client)
    return actions._ask_json("p", "x", {"type": "object"}, get_config())


def test_broken_json_is_retried_once_at_a_different_temperature(monkeypatch):
    """At temperature 0 the model repeats the same mistake, so the retry must change something."""
    client = FakeClient(BadJsonError(), '{"ok": 1}')
    assert _ask(client, monkeypatch) == '{"ok": 1}'
    assert client.temperatures == [0.0, 0.5]


def test_broken_json_twice_gives_up(monkeypatch):
    client = FakeClient(BadJsonError(), BadJsonError())
    with pytest.raises(BadJsonError):
        _ask(client, monkeypatch)
    assert len(client.temperatures) == 2


def test_any_other_error_is_not_retried(monkeypatch):
    client = FakeClient(BadJsonError("Error code: 400 - something else"), '{"ok": 1}')
    with pytest.raises(BadJsonError):
        _ask(client, monkeypatch)
    assert len(client.temperatures) == 1


# --- two writers at once ----------------------------------------------------------------


def test_two_writers_at_once_lose_nothing(paths, monkeypatch):
    """The CLI and the room can both save to the same file. Reading is slowed
    down here so every writer reads before anyone has written."""
    real_load = actions._load_json

    def slow_load(path):
        rows = real_load(path)
        time.sleep(0.02)
        return rows

    monkeypatch.setattr(actions, "_load_json", slow_load)
    p = paths / "checklist.json"
    writers = [threading.Thread(target=actions._append_json, args=(p, {"id": str(i)})) for i in range(8)]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    assert sorted(e["id"] for e in json.loads(p.read_text(encoding="utf-8"))) == [str(i) for i in range(8)]
