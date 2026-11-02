"""Tests for Layer 7, the shared room (src/dd/room.py) and its web server
(src/dd/server.py).

The room calls the real pipeline and the real Layer 6 actions through three
hooks in dd.room: run_answer, get_actions and warm_up. These tests swap those
hooks for fast stand-ins (pytest's monkeypatch, inside this file only), so the
room's rules - queue, live progress, voting - are checked in seconds without
loading models or calling Groq. The server code itself is unchanged.

Run from the project root:   pytest
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

import dd.room as room_mod
from dd.models import (
    Answer, Chunk, Citation, ConfidenceBreakdown, GradedChunk, RetrievalOutcome, RetrievedChunk,
)
from dd.room import Room
from dd.server import create_app

# --- stand-ins for the pipeline and the actions ----------------------------------


def make_answer(question: str, answered: bool = True) -> Answer:
    if not answered:
        return Answer(query=question, answered=False, text="the filing does not say")
    return Answer(
        query=question,
        answered=True,
        text="Net income was $112,010 million [S1]",
        citations=[Citation(marker="[S1]", chunk_id="structured:x", source="AAPL | 10-K | FY2025 | income statement")],
        confidence=ConfidenceBreakdown(score=0.9, band="high", hybrid_agreement=0.0,
                                       rerank_strength=0.0, loop_cleanliness=1.0),
    )


class FakePipeline:
    """Reports two steps like the real one, then answers. `gate` lets a test
    hold a question 'running' to see what queues behind it."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.asked: list[str] = []
        self.fail_on: set[str] = set()
        self.unanswered: set[str] = set()

    def __call__(self, question: str, on_progress) -> Answer:
        self.asked.append(question)
        on_progress("search", {"attempt": 1, "query": question})
        self.gate.wait(timeout=10)
        on_progress("write", {"passages": 1, "facts": 1})
        if question in self.fail_on:
            raise RuntimeError("Groq rate limit")
        return make_answer(question, answered=question not in self.unanswered)


class Draft(BaseModel):
    title: str


class FakeActions:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.problems: list[str] = []

    def table(self) -> dict:
        def propose(answer: Answer) -> Draft:
            return Draft(title=f"item for: {answer.query}")

        def validate(draft: Draft, answer: Answer) -> list[str]:
            return list(self.problems)

        def execute(draft: Draft, answer: Answer) -> str:
            self.executed.append(draft.title)
            return "written"

        return {"checklist": (propose, validate, execute), "flag": (propose, validate, execute)}


class FakeConn:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, data: Any) -> None:
        self.messages.append(data)

    @property
    def last_state(self) -> dict[str, Any]:
        return self.messages[-1]["state"]


@pytest.fixture
def fakes(monkeypatch):
    pipeline, actions = FakePipeline(), FakeActions()
    monkeypatch.setattr(room_mod, "run_answer", pipeline)
    monkeypatch.setattr(room_mod, "get_actions", actions.table)
    monkeypatch.setattr(room_mod, "warm_up", lambda: None)
    return pipeline, actions


def run(coro):
    return asyncio.run(coro)


async def until(condition, timeout: float = 5.0) -> None:
    async def poll():
        while not condition():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout)


async def started_room() -> Room:
    room = Room()
    await room.start()
    await until(lambda: room.status == "ready")
    return room


async def answered_question(room: Room, member: str, text: str = "What was net income in FY2025?") -> str:
    await room.ask(member, text)
    await until(lambda: any(h["text"] == text for h in room.history))
    return next(h["id"] for h in room.history if h["text"] == text)


# --- people ----------------------------------------------------------------------


def test_join_names_are_trimmed_and_kept_distinct(fakes):
    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "  Sam  ")
        b = await room.join(FakeConn(), "Sam")
        assert [room.members[a]["name"], room.members[b]["name"]] == ["Sam", "Sam (2)"]
        with pytest.raises(ValueError):
            await room.join(FakeConn(), "   ")
        await room.stop()
    run(scenario())


def test_everyone_receives_the_same_state_with_their_own_id(fakes):
    async def scenario():
        room = await started_room()
        c1, c2 = FakeConn(), FakeConn()
        a = await room.join(c1, "Ana")
        b = await room.join(c2, "Ben")
        assert c1.messages[-1]["you"] == a and c2.messages[-1]["you"] == b
        assert c1.last_state == c2.last_state
        assert [m["name"] for m in c1.last_state["members"]] == ["Ana", "Ben"]
        await room.stop()
    run(scenario())


# --- questions ---------------------------------------------------------------------


def test_question_length_is_checked(fakes):
    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        for bad in ["", "hi", "x" * (room_mod.MAX_QUESTION + 1)]:
            with pytest.raises(ValueError):
                await room.ask(m, bad)
        await room.stop()
    run(scenario())


def test_questions_queue_and_run_one_at_a_time_in_order(fakes):
    pipeline, _ = fakes

    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        pipeline.gate.clear()  # hold the first question "running"
        await room.ask(m, "first question?")
        await until(lambda: room.current is not None)
        await room.ask(m, "second question?")
        assert room.current["text"] == "first question?"
        assert [q["text"] for q in room.queue] == ["second question?"]

        pipeline.gate.set()
        await until(lambda: len(room.history) == 2)
        assert pipeline.asked == ["first question?", "second question?"]
        assert [h["text"] for h in room.history] == ["first question?", "second question?"]
        assert room.history[0]["answer"]["text"].startswith("Net income")
        await room.stop()
    run(scenario())


def test_live_steps_are_recorded_for_the_running_question(fakes):
    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        await answered_question(room, m)
        assert [s["stage"] for s in room.history[0]["steps"]] == ["search", "write"]
        await room.stop()
    run(scenario())


def test_a_late_step_is_never_filed_under_the_next_question(fakes):
    pipeline, _ = fakes

    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        first = await answered_question(room, m, "first question?")
        pipeline.gate.clear()
        await room.ask(m, "second question?")
        await until(lambda: room.current is not None)
        room._step_from_thread(first, "grade", {"attempt": 9})  # a straggler from question 1
        await asyncio.sleep(0.05)
        assert all(s["details"].get("attempt") != 9 for s in room.current["steps"])
        pipeline.gate.set()
        await room.stop()
    run(scenario())


def test_anyone_can_remove_a_waiting_question(fakes):
    pipeline, _ = fakes

    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        pipeline.gate.clear()
        await room.ask(m, "first question?")
        await until(lambda: room.current is not None)
        await room.ask(m, "second question?")
        await room.remove(m, room.queue[0]["id"])
        assert room.queue == []
        with pytest.raises(ValueError):
            await room.remove(m, "no-such-id")
        pipeline.gate.set()
        await room.stop()
    run(scenario())


def test_a_failed_question_is_reported_and_the_room_keeps_going(fakes):
    pipeline, _ = fakes
    pipeline.fail_on.add("broken question?")

    async def scenario():
        room = await started_room()
        m = await room.join(FakeConn(), "Ana")
        await room.ask(m, "broken question?")
        await room.ask(m, "working question?")
        await until(lambda: len(room.history) == 2)
        assert "rate limit" in room.history[0]["error"] and room.history[0]["answer"] is None
        assert room.history[1]["answer"] is not None
        await room.stop()
    run(scenario())


def test_full_passage_text_keeps_table_rows_but_drops_padding():
    table = (
        "Total assets          $          359,241      $          364,980\n"
        "────────────────────────────\n"
        "\n"
        "  Total liabilities                 285,508               308,030  "
    )
    assert room_mod.readable(table) == (
        "Total assets   $   359,241   $   364,980\n"
        "Total liabilities   285,508   308,030"
    )
    assert room_mod.readable("Net sales grew. Services rose.") == "Net sales grew. Services rose."


# --- actions: everyone present must approve ---------------------------------------


def test_action_runs_only_after_everyone_present_approves(fakes):
    _, actions = fakes

    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        b = await room.join(FakeConn(), "Ben")
        qid = await answered_question(room, a)
        await room.propose(a, "checklist", qid)
        action_id = room.pending_action["id"]
        assert room.pending_action["status"] == "voting"

        await room.vote(a, action_id, True)
        assert actions.executed == [], "ran before everyone approved"
        await room.vote(b, action_id, True)
        assert len(actions.executed) == 1
        assert room.pending_action is None and room.action_history[-1]["status"] == "done"
        await room.stop()
    run(scenario())


def test_one_rejection_stops_the_action(fakes):
    _, actions = fakes

    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        b = await room.join(FakeConn(), "Ben")
        qid = await answered_question(room, a)
        await room.propose(a, "checklist", qid)
        action_id = room.pending_action["id"]
        await room.vote(a, action_id, True)
        await room.vote(b, action_id, False)
        assert actions.executed == []
        assert room.action_history[-1]["status"] == "rejected by Ben"
        await room.stop()
    run(scenario())


def test_a_voter_who_leaves_is_no_longer_waited_on(fakes):
    _, actions = fakes

    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        b = await room.join(FakeConn(), "Ben")
        qid = await answered_question(room, a)
        await room.propose(a, "checklist", qid)
        await room.vote(a, room.pending_action["id"], True)
        await room.leave(b)
        assert len(actions.executed) == 1
        await room.stop()
    run(scenario())


def test_someone_who_joins_mid_vote_does_not_vote(fakes):
    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        qid = await answered_question(room, a)
        await room.join(FakeConn(), "Ben")
        b = await room.join(FakeConn(), "Cat")
        await room.leave(b)  # Cat comes and goes before the proposal
        await room.propose(a, "checklist", qid)
        late = await room.join(FakeConn(), "Dev")
        with pytest.raises(ValueError, match="joined after"):
            await room.vote(late, room.pending_action["id"], True)
        await room.stop()
    run(scenario())


def test_a_draft_that_breaks_the_rules_never_reaches_a_vote(fakes):
    _, actions = fakes
    actions.problems = ["answer confidence is LOW; too weak to act on"]

    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        qid = await answered_question(room, a)
        await room.propose(a, "checklist", qid)
        assert room.pending_action is None
        last = room.action_history[-1]
        assert last["status"] == "rejected by the rules" and last["voters"] == []
        assert actions.executed == []
        await room.stop()
    run(scenario())


def test_only_flag_can_act_on_an_unanswered_question(fakes):
    pipeline, _ = fakes
    pipeline.unanswered.add("unanswerable question?")

    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        qid = await answered_question(room, a, "unanswerable question?")
        with pytest.raises(ValueError, match="only 'flag'"):
            await room.propose(a, "checklist", qid)
        await room.propose(a, "flag", qid)
        assert room.pending_action["status"] == "voting"
        await room.stop()
    run(scenario())


def test_one_action_at_a_time(fakes):
    async def scenario():
        room = await started_room()
        a = await room.join(FakeConn(), "Ana")
        await room.join(FakeConn(), "Ben")
        qid = await answered_question(room, a)
        with pytest.raises(ValueError, match="no answer"):
            await room.propose(a, "flag", "no-such-question")
        with pytest.raises(ValueError, match="unknown action"):
            await room.propose(a, "launch-rockets", qid)
        await room.propose(a, "checklist", qid)
        with pytest.raises(ValueError, match="already being decided"):
            await room.propose(a, "flag", qid)
        await room.stop()
    run(scenario())


# --- the WebSocket server ------------------------------------------------------------


def _next(ws, msg_type: str, limit: int = 20) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"no '{msg_type}' message")


def test_server_health_and_join(fakes):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["members"] == 0
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "join", "name": "Ana"})
            state = _next(ws, "state")["state"]
            assert [m["name"] for m in state["members"]] == ["Ana"]


def test_server_turns_bad_messages_into_errors_without_disconnecting(fakes):
    with TestClient(create_app()) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ask", "text": "too early?"})
        assert "join with a name first" in _next(ws, "error")["message"]

        ws.send_text("not json at all")
        _next(ws, "error")
        ws.send_text(json.dumps(["a", "list"]))
        assert "JSON object" in _next(ws, "error")["message"]

        ws.send_json({"type": "join", "name": "Ana"})
        _next(ws, "state")
        ws.send_json({"type": "vote", "action_id": "x", "approve": "false"})
        assert "must be true or false" in _next(ws, "error")["message"]

        ws.send_json({"type": "ask", "text": "still connected and working?"})
        state = _next(ws, "state")["state"]
        assert state["members"][0]["name"] == "Ana"


# --- nothing else changed --------------------------------------------------------------


def test_pipeline_progress_is_optional_and_never_breaks_the_answer():
    """Layers 4-5 gained an optional on_progress argument. Existing callers
    (dd answer, dd heal, the eval) pass nothing; a broken listener is ignored."""
    import inspect

    from dd.generation import answer, generate_answer
    from dd.self_healing import emit, self_heal

    for fn in (answer, generate_answer, self_heal):
        param = inspect.signature(fn).parameters["on_progress"]
        assert param.default is None

    def broken_listener(stage: str, details: dict) -> None:
        raise RuntimeError("listener crashed")

    emit(None, "search", attempt=1)
    emit(broken_listener, "search", attempt=1)  # must not raise


# --- how the room shows a passage -------------------------------------------------


def _answer_with_passage(subsection: str) -> Answer:
    chunk = Chunk(chunk_id="c1", text="x", company="Apple Inc.", ticker="AAPL", form="10-K",
                  fiscal_year="FY2025", item="Item 1A", section="Risk Factors", subsection=subsection)
    outcome = RetrievalOutcome(
        original_query="q", final_query="q", succeeded=True, relevance_threshold=0.5,
        chunks=[GradedChunk(retrieved=RetrievedChunk(chunk=chunk), relevance=0.9, reason="r")],
    )
    return Answer(query="q", answered=True, text="t", outcome=outcome)


def test_a_passage_without_a_subsection_shows_its_section(fakes):
    """The grader is told "Item 1A / Risk Factors"; the card must say the same."""
    assert room_mod.answer_view(_answer_with_passage(""))["passages"][0]["where"] == "Item 1A / Risk Factors"
    assert room_mod.answer_view(_answer_with_passage("Tariffs"))["passages"][0]["where"] == "Item 1A / Tariffs"


def test_full_passage_text_keeps_ordinary_spacing():
    """Only the filing's wide column padding is squeezed, not every double space."""
    assert room_mod.readable("Item 1A.  Risk Factors") == "Item 1A.  Risk Factors"
    assert room_mod.readable("a" + " " * 30 + "b") == "a   b"


# --- a frozen browser must not freeze the room (review item 7) -----------------------


class StuckConn:
    """A browser whose connection has frozen: sending to it never finishes."""

    async def send_json(self, data: Any) -> None:
        await asyncio.Event().wait()


def test_a_stalled_connection_does_not_freeze_the_room_for_everyone(fakes, monkeypatch):
    monkeypatch.setattr(room_mod, "SEND_TIMEOUT", 0.2, raising=False)

    async def scenario():
        room = await started_room()
        await asyncio.wait_for(room.join(StuckConn(), "Stuck"), 3)
        healthy = FakeConn()
        await asyncio.wait_for(room.join(healthy, "Ana"), 3)
        assert [m["name"] for m in healthy.last_state["members"]] == ["Ana"], "the frozen one is dropped"
        await room.stop()
    run(scenario())


# --- nobody outside the page itself may join (review item 7.2) ---------------------------


def test_a_page_from_another_site_cannot_join_the_room(fakes):
    """Without this, any website open in a team member's browser could connect
    to a room on localhost, ask questions and vote."""
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as ws:
                ws.send_json({"type": "join", "name": "Mallory"})
                ws.receive_json()
        assert client.get("/api/health").json()["members"] == 0


def test_the_rooms_own_page_can_join(fakes):
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            ws.send_json({"type": "join", "name": "Ana"})
            assert _next(ws, "state")["state"]["members"][0]["name"] == "Ana"


# --- the room remembers across a restart (review item 7.1) ---------------------------------


def test_history_and_finished_actions_survive_a_restart(fakes, tmp_path):
    state_file = tmp_path / "room_state.json"

    async def first_session():
        room = Room(state_file=state_file)
        await room.start()
        await until(lambda: room.status == "ready")
        ana = await room.join(FakeConn(), "Ana")
        qid = await answered_question(room, ana, "Why did Services grow?")
        await room.propose(ana, "checklist", qid)
        await until(lambda: room.pending_action is not None and room.pending_action["status"] == "voting")
        await room.vote(ana, room.pending_action["id"], True)
        await until(lambda: room.pending_action is None)
        await room.stop()

    async def second_session():
        room = Room(state_file=state_file)
        await room.start()
        snap = room.snapshot()
        assert [h["text"] for h in snap["history"]] == ["Why did Services grow?"]
        assert snap["history"][0]["answer"]["text"].startswith("Net income")
        assert [(a["kind"], a["status"]) for a in snap["action_history"]] == [("checklist", "done")]
        assert any("proposed a checklist" in line["text"] for line in snap["log"])
        await room.stop()

    run(first_session())
    run(second_session())


def test_a_garbled_state_file_does_not_stop_the_room_starting(fakes, tmp_path):
    state_file = tmp_path / "room_state.json"
    state_file.write_text("{oops", encoding="utf-8")

    async def scenario():
        room = Room(state_file=state_file)
        await room.start()
        assert room.history == [] and room.action_history == []
        await room.stop()
    run(scenario())


def test_without_a_state_file_nothing_is_written(fakes, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def scenario():
        room = await started_room()
        ana = await room.join(FakeConn(), "Ana")
        await answered_question(room, ana)
        await room.stop()
    run(scenario())
    assert list(tmp_path.iterdir()) == []


# --- a dropped connection gets a grace period (a Wi-Fi blip is not leaving) ---------------


async def open_vote(room: Room, ana: str) -> str:
    qid = await answered_question(room, ana)
    await room.propose(ana, "checklist", qid)
    await until(lambda: room.pending_action is not None and room.pending_action["status"] == "voting")
    return room.pending_action["id"]


def test_a_voter_who_reconnects_in_time_keeps_their_place(fakes):
    async def scenario():
        room = await started_room()
        ana = await room.join(FakeConn(), "Ana")
        ben = await room.join(FakeConn(), "Ben")
        action_id = await open_vote(room, ana)
        await room.vote(ana, action_id, True)

        await room.disconnect(ben)  # Ben's Wi-Fi blinks
        assert room.pending_action["status"] == "voting", "must not run without Ben"
        assert [(m["name"], m["away"]) for m in room.snapshot()["members"]] == [("Ana", False), ("Ben", True)]

        again = await room.join(FakeConn(), "Ben")  # the page reconnects by itself
        assert again == ben, "same person, same place - not 'Ben (2)'"
        await room.vote(again, action_id, True)
        await until(lambda: room.pending_action is None)
        assert room.action_history[-1]["status"] == "done"
        await room.stop()
    run(scenario())


def test_a_voter_who_does_not_come_back_is_dropped_after_the_grace_period(fakes, monkeypatch):
    monkeypatch.setattr(room_mod, "GRACE_SECONDS", 0.2, raising=False)

    async def scenario():
        room = await started_room()
        ana = await room.join(FakeConn(), "Ana")
        ben = await room.join(FakeConn(), "Ben")
        action_id = await open_vote(room, ana)
        await room.vote(ana, action_id, True)
        await room.disconnect(ben)
        await until(lambda: room.pending_action is None, timeout=3)
        assert room.action_history[-1]["status"] == "done"
        assert [m["name"] for m in room.snapshot()["members"]] == ["Ana"]
        await room.stop()
    run(scenario())


def test_someone_who_was_away_is_not_asked_to_vote_on_a_new_action(fakes):
    async def scenario():
        room = await started_room()
        ana = await room.join(FakeConn(), "Ana")
        ben = await room.join(FakeConn(), "Ben")
        await room.disconnect(ben)
        await open_vote(room, ana)
        assert [v["name"] for v in room.snapshot()["pending_action"]["voters"]] == ["Ana"]
        await room.stop()
    run(scenario())


def test_a_late_close_report_from_an_old_connection_does_not_push_a_reconnected_person_away(fakes):
    async def scenario():
        room = await started_room()
        old, new = FakeConn(), FakeConn()
        ben = await room.join(old, "Ben")
        await room.disconnect(ben, old)
        assert await room.join(new, "Ben") == ben
        await room.disconnect(ben, old)  # the old socket is only now reported closed
        assert room.snapshot()["members"][0]["away"] is False
        await room.disconnect(ben, new)  # the live connection really closing still counts
        assert room.snapshot()["members"][0]["away"] is True
        await room.stop()
    run(scenario())
