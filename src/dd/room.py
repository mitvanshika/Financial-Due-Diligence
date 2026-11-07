"""Layer 7: the shared room - who is here, what is being asked, what the
pipeline is doing right now, and what the team has agreed to do about it.

Everyone in the room sees the same state at the same time: the people present,
the question queue, each step of the running question (lookup, search,
grading, rewording, writing), the answer with its citations and confidence,
and any Layer 6 action waiting for approval.

It runs the real pipeline (Layers 3-5: models + Groq) and the real Layer 6
actions - the same code `dd answer` and `dd act` use. Nothing here changes how
those layers behave; the room only calls them and reports what they do.

Rules the team chose:
- Questions queue: a new question waits until the running one finishes.
  Anyone can take a waiting question out of the queue.
- An action needs EVERY person present to approve it; one rejection stops it.
  The voters are the people in the room when it was proposed; someone who
  leaves is no longer waited on, someone who joins later does not vote.
- People identify themselves by typing a display name. Getting in takes the
  room's one shared access code (server.py); there are no per-person accounts.

Concurrency: one asyncio event loop owns all room state, so no locks are
needed around it. The pipeline and the actions are slow, blocking calls, so
they run in worker threads and hand their results back to the loop. After
every change the whole state is sent to everyone - it is small, and sending
all of it means no client can drift out of sync.

This module knows nothing about HTTP or WebSockets: a member is anything with
an async `send_json(dict)` method. server.py does the networking.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import ValidationError

from .models import Answer

MAX_NAME = 40
MAX_QUESTION = 500
KEEP_HISTORY = 20
KEEP_LOG = 50
# A browser that takes longer than this to accept a message is treated as gone.
# Without a limit, one frozen connection would hold up every update for everyone.
SEND_TIMEOUT = 5.0
# A dropped connection (a Wi-Fi blink, a page refresh) keeps its place in the room
# this long. The page reconnects by itself every 2 seconds.
GRACE_SECONDS = 30.0


class Connection(Protocol):
    async def send_json(self, data: Any) -> None: ...


# --- the real pipeline and actions --------------------------------------------
# Imported inside the functions so importing this module (and starting the
# server) doesn't pay for loading torch until the models are actually needed.


def run_answer(question: str, on_progress: Callable[[str, dict], None]) -> Answer:
    """Layers 3-5: the same full pipeline as `dd answer`, reporting each step."""
    from .generation import answer

    return answer(question, on_progress=on_progress)


def get_actions() -> dict[str, tuple[Callable, Callable, Callable]]:
    """Layer 6: kind -> (propose, validate, execute), exactly as `dd act` uses."""
    from .actions import ACTIONS

    return ACTIONS


def warm_up() -> None:
    """Load the embedding model and the reranker once, before the first
    question, so nobody waits on a cold start mid-session."""
    from .retrieval import hybrid_retrieve

    hybrid_retrieve("warm-up")


# --- helpers ---------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def readable(text: str) -> str:
    """A passage as people read it: one table row per line, columns kept apart
    by three spaces. The filing pads its table columns with dozens of spaces
    and draws rules with ─ - layout only, and most of a table chunk's size."""
    lines = (re.sub(r"[ \t\u00a0]{4,}", "   ", line.replace("─", " ").strip()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def answer_view(a: Answer) -> dict[str, Any]:
    """What the browser needs from an Answer - plain JSON, no internals.
    `passages` is every passage Layer 4 graded, best first, so the team sees
    the search results behind the answer and not only the answer: a short
    `snippet` for the list and the full `text` to open."""
    passages = []
    if a.outcome is not None:
        for g in sorted(a.outcome.chunks, key=lambda g: g.relevance, reverse=True):
            c = g.retrieved.chunk
            passages.append({
                "where": " / ".join(x for x in (c.item, c.subsection or c.section) if x),
                "relevance": round(g.relevance, 2),
                "reason": g.reason,
                "snippet": " ".join(c.text.split())[:300],
                "text": readable(c.text),
            })
    cf = a.confidence
    return {
        "answered": a.answered,
        "text": a.text,
        "elapsed_s": a.elapsed_s,
        "citations": [{"marker": c.marker, "source": c.source} for c in a.citations],
        "confidence": None if cf is None else {
            "score": cf.score,
            "band": cf.band,
            "notes": cf.notes,
            "hybrid_agreement": cf.hybrid_agreement,
            "rerank_strength": cf.rerank_strength,
            "loop_cleanliness": cf.loop_cleanliness,
        },
        "attempts": [] if a.outcome is None else [
            {"attempt": t.attempt, "query": t.query, "relevant": t.n_relevant, "total": t.n_retrieved}
            for t in a.outcome.attempts
        ],
        "passages": passages,
    }


# --- the room --------------------------------------------------------------------


class Room:
    def __init__(self, state_file: Path | None = None) -> None:
        # With a state_file the room keeps its question history, finished
        # actions and activity log across restarts. Without one (the tests'
        # default) it stays purely in memory and writes nothing.
        self.state_file = state_file
        self.members: dict[str, dict[str, Any]] = {}  # id -> {"name": str, "conn": Connection}
        self.status = "loading"  # -> "ready" once the models are loaded
        self.queue: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None  # the question running now
        self.history: list[dict[str, Any]] = []  # finished questions, oldest first
        self.answers: dict[str, Answer] = {}  # question id -> full Answer, for actions
        self.pending_action: dict[str, Any] | None = None
        self.action_history: list[dict[str, Any]] = []
        self.log: list[dict[str, str]] = []

        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()  # set when a question is queued
        self._ready = asyncio.Event()  # set once the models are loaded
        self._send_lock = asyncio.Lock()  # one broadcast at a time per connection
        self._tasks: list[asyncio.Task] = []
        self._expiry: dict[str, asyncio.Task] = {}  # away member id -> task that removes them
        self._load_state()

    # --- saved state ---------------------------------------------------------

    def _load_state(self) -> None:
        """Restore what a restart would otherwise wipe. Not restored: the full
        answers (so actions can only be proposed on questions asked since the
        restart), the queue, and any vote in progress."""
        if self.state_file is None or not self.state_file.exists():
            return
        try:
            saved = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.history = list(saved["history"])[-KEEP_HISTORY:]
            self.action_history = [{"_view": v} for v in saved["action_history"]][-KEEP_HISTORY:]
            self.log = list(saved["log"])[-KEEP_LOG:]
        except (OSError, ValueError, KeyError, TypeError):
            # Unreadable: keep a copy for inspection and start empty rather
            # than refuse to start.
            self.history, self.action_history, self.log = [], [], []
            try:
                os.replace(self.state_file, self.state_file.with_name(self.state_file.name + ".bad"))
            except OSError:
                pass
            self._note("saved room state could not be read - starting empty")

    def _save_state(self) -> None:
        """Called when a question or an action finishes (not on every step: the
        state is large). Written to a temporary file and swapped in, so a crash
        never leaves half a file. A failed save never breaks the room."""
        if self.state_file is None:
            return
        data = {
            "history": self.history,
            "action_history": [self._saved_view(a) for a in self.action_history],
            "log": self.log,
        }
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError as exc:
            self._note(f"could not save room state: {type(exc).__name__}: {exc}")

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._tasks.append(asyncio.create_task(self._warm()))
        self._tasks.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        tasks = self._tasks + list(self._expiry.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _warm(self) -> None:
        try:
            await asyncio.to_thread(warm_up)
        except Exception as exc:  # noqa: BLE001 - the first question loads the models instead
            self._note(f"model warm-up failed, the first question will load them: {type(exc).__name__}: {exc}")
        self.status = "ready"
        self._ready.set()
        await self.broadcast()

    # --- people --------------------------------------------------------------

    async def join(self, conn: Connection, name: str) -> str:
        name = " ".join(name.split())[:MAX_NAME]
        if not name:
            raise ValueError("please enter a name")
        for member_id, m in self.members.items():
            if m.get("away") and m["name"] == name:
                # Same name as someone whose connection just dropped: this is
                # them coming back, so they keep their place (and any vote).
                m["conn"], m["away"] = conn, False
                self._cancel_expiry(member_id)
                self._note(f"{name} reconnected")
                await self.broadcast()
                return member_id
        taken = {m["name"] for m in self.members.values()}
        base, n = name, 2
        while name in taken:  # two people both called "Sam" stay distinguishable
            name, n = f"{base} ({n})", n + 1
        member_id = _new_id()
        self.members[member_id] = {"name": name, "conn": conn}
        self._note(f"{name} joined")
        await self.broadcast()
        return member_id

    async def disconnect(self, member_id: str, conn: Connection | None = None) -> None:
        """A connection ended. The person stays in the room, marked away, for
        GRACE_SECONDS: if they reconnect in time nothing changes for them; if
        not, they leave for good and a vote stops waiting on them. `conn` is the
        connection that ended: a late report about an OLD connection must not
        push someone away who has already reconnected on a new one."""
        member = self.members.get(member_id)
        if member is None or member.get("away") or (conn is not None and member["conn"] is not conn):
            return
        member["away"] = True
        self._note(f"{member['name']} lost connection")
        self._expiry[member_id] = asyncio.create_task(self._expire(member_id))
        await self.broadcast()

    async def _expire(self, member_id: str) -> None:
        await asyncio.sleep(GRACE_SECONDS)
        self._expiry.pop(member_id, None)
        await self.leave(member_id)

    def _cancel_expiry(self, member_id: str) -> None:
        task = self._expiry.pop(member_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def leave(self, member_id: str) -> None:
        self._cancel_expiry(member_id)
        member = self.members.pop(member_id, None)
        if member is None:
            return
        self._note(f"{member['name']} left")
        action = self.pending_action
        if action and action["status"] == "voting" and member_id in action["voters"]:
            # Someone who left can't vote; stop waiting on them.
            action["voters"].remove(member_id)
            action["votes"].pop(member_id, None)
            await self._settle_vote()
        await self.broadcast()

    def _name(self, member_id: str) -> str:
        return self.members.get(member_id, {}).get("name", "someone")

    # --- questions -----------------------------------------------------------

    async def ask(self, member_id: str, text: str) -> None:
        text = " ".join(text.split())
        if not 3 <= len(text) <= MAX_QUESTION:
            raise ValueError(f"a question must be 3 to {MAX_QUESTION} characters")
        asker = self._name(member_id)
        self.queue.append({"id": _new_id(), "text": text, "asked_by": asker, "asked_at": _now()})
        self._note(f"{asker} asked: {text}")
        self._wake.set()
        await self.broadcast()

    async def remove(self, member_id: str, question_id: str) -> None:
        """Take a question out of the queue before it starts - the way to
        change course without waiting for it to run."""
        before = len(self.queue)
        self.queue = [q for q in self.queue if q["id"] != question_id]
        if len(self.queue) == before:
            raise ValueError("that question is not waiting in the queue")
        self._note(f"{self._name(member_id)} removed a queued question")
        await self.broadcast()

    async def _worker(self) -> None:
        """Runs queued questions one at a time, oldest first."""
        await self._ready.wait()
        while True:
            if not self.queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            q = self.queue.pop(0)
            self.current = {**q, "started_at": _now(), "steps": []}
            await self.broadcast()

            # Bind the question id into the callback: a step that arrives late
            # must never be filed under the next question.
            def on_progress(stage: str, details: dict, qid: str = q["id"]) -> None:
                self._step_from_thread(qid, stage, details)

            try:
                a = await asyncio.to_thread(run_answer, q["text"], on_progress)
                self.answers[q["id"]] = a
                entry = {**q, "answer": answer_view(a), "error": None}
            except Exception as exc:  # noqa: BLE001 - one failed question must not stop the room
                entry = {**q, "answer": None, "error": f"{type(exc).__name__}: {exc}"}
            entry["steps"] = self.current["steps"]
            self.history.append(entry)
            self._trim_history()
            self.current = None
            self._save_state()
            await self.broadcast()

    def _step_from_thread(self, question_id: str, stage: str, details: dict) -> None:
        """Called by the pipeline from its worker thread; hands the step to
        the event loop, which owns all room state."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._add_step(question_id, stage, details), self._loop)

    async def _add_step(self, question_id: str, stage: str, details: dict) -> None:
        if self.current is not None and self.current["id"] == question_id:
            self.current["steps"].append({"stage": stage, "details": details, "at": _now()})
            await self.broadcast()

    def _trim_history(self) -> None:
        if len(self.history) > KEEP_HISTORY:
            for old in self.history[:-KEEP_HISTORY]:
                if not (self.pending_action and self.pending_action["question_id"] == old["id"]):
                    self.answers.pop(old["id"], None)
            self.history = self.history[-KEEP_HISTORY:]

    # --- actions: Layer 6, approved by the whole room ---------------------------

    async def propose(self, member_id: str, kind: str, question_id: str) -> None:
        actions = get_actions()
        if kind not in actions:
            raise ValueError(f"unknown action '{kind}' - use one of: {', '.join(actions)}")
        if self.pending_action is not None:
            raise ValueError("another action is already being decided - finish that one first")
        answer = self.answers.get(question_id)
        if answer is None:
            raise ValueError("that question has no answer to act on (yet)")
        if not answer.answered and kind != "flag":
            raise ValueError("this question was not answered - only 'flag' can act on it")

        propose, validate, _ = actions[kind]
        action = {
            "id": _new_id(), "kind": kind, "question_id": question_id,
            "question": answer.query, "proposed_by": self._name(member_id),
            "status": "drafting", "draft": None, "problems": [],
            "voters": [], "votes": {}, "result": None,
        }
        self.pending_action = action
        self._note(f"{action['proposed_by']} proposed a {kind}")
        await self.broadcast()

        # Layer 6 steps 1 and 2: draft it, then check it against the rules.
        try:
            draft = await asyncio.to_thread(propose, answer)
            action["draft"] = draft
            action["problems"] = await asyncio.to_thread(validate, draft, answer)
        except ValidationError as exc:
            action["problems"] = [f"the draft didn't fit the {kind} format ({exc.error_count()} problem(s))"]
        except Exception as exc:  # noqa: BLE001
            action["problems"] = [f"could not draft the {kind}: {type(exc).__name__}: {exc}"]

        if action["problems"]:
            self._finish_action("rejected by the rules")
            await self.broadcast()
            return

        # Step 3: the room votes. Everyone present right now must approve.
        action["status"] = "voting"
        action["voters"] = [i for i, m in self.members.items() if not m.get("away")]
        await self._settle_vote()  # if nobody is left in the room, cancel it
        await self.broadcast()

    async def vote(self, member_id: str, action_id: str, approve: bool) -> None:
        action = self.pending_action
        if action is None or action["id"] != action_id or action["status"] != "voting":
            raise ValueError("there is no vote open for that action")
        if member_id not in action["voters"]:
            raise ValueError("you joined after this action was proposed, so you don't vote on it")
        action["votes"][member_id] = approve
        self._note(f"{self._name(member_id)} {'approved' if approve else 'rejected'} the {action['kind']}")
        await self._settle_vote()
        await self.broadcast()

    async def _settle_vote(self) -> None:
        action = self.pending_action
        if action is None or action["status"] != "voting":
            return
        if not action["voters"]:
            self._finish_action("cancelled - nobody left to approve it")
            return
        rejecters = [self._name(m) for m, v in action["votes"].items() if v is False]
        if rejecters:
            self._finish_action(f"rejected by {', '.join(rejecters)}")
            return
        if all(action["votes"].get(m) is True for m in action["voters"]):
            # Step 4: everyone said yes - only now is anything written.
            action["status"] = "running"
            await self.broadcast()
            _, _, execute = get_actions()[action["kind"]]
            try:
                result = await asyncio.to_thread(execute, action["draft"], self.answers[action["question_id"]])
                self._finish_action("done", result=result)
            except Exception as exc:  # noqa: BLE001
                self._finish_action("failed", result=f"{type(exc).__name__}: {exc}")

    def _finish_action(self, outcome: str, result: str | None = None) -> None:
        action = self.pending_action
        if action is None:
            return
        action["status"] = outcome
        action["result"] = result
        self._note(f"{action['kind']}: {outcome}" + (f" - {result}" if result else ""))
        self.action_history.append(action)
        self.action_history = self.action_history[-KEEP_HISTORY:]
        self.pending_action = None
        self._save_state()

    # --- what everyone sees ------------------------------------------------------

    def _note(self, text: str) -> None:
        self.log.append({"at": _now(), "text": text})
        self.log = self.log[-KEEP_LOG:]

    def _saved_view(self, a: dict[str, Any]) -> dict[str, Any] | None:
        """An action_history entry as people see it. Entries restored from a
        previous run are already in that form."""
        return a["_view"] if "_view" in a else self._action_view(a)

    def _action_view(self, a: dict[str, Any] | None) -> dict[str, Any] | None:
        if a is None:
            return None
        draft = a["draft"]
        return {
            "id": a["id"], "kind": a["kind"], "question_id": a["question_id"],
            "question": a["question"], "proposed_by": a["proposed_by"],
            "status": a["status"], "problems": a["problems"], "result": a["result"],
            "draft": draft.model_dump() if draft is not None else None,
            "voters": [{"id": m, "name": self._name(m), "vote": a["votes"].get(m)} for m in a["voters"]],
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "members": [{"id": i, "name": m["name"], "away": bool(m.get("away"))} for i, m in self.members.items()],
            "queue": self.queue,
            "current": self.current,
            "history": self.history,
            "pending_action": self._action_view(self.pending_action),
            "action_history": [self._saved_view(a) for a in self.action_history],
            "actions": list(get_actions()),
            "log": self.log,
        }

    async def broadcast(self) -> None:
        """Send the whole state to everyone, one broadcast at a time so two
        updates never interleave on the same connection."""
        async with self._send_lock:
            state = self.snapshot()
            members = [(i, m) for i, m in self.members.items() if not m.get("away")]
            # All at once, each with a time limit: one slow or frozen browser
            # must not delay the others.
            results = await asyncio.gather(*(
                self._send(m["conn"], {"type": "state", "you": member_id, "state": state})
                for member_id, m in members
            ))
        for (member_id, _), in_time in zip(members, results):
            if not in_time:
                await self._drop(member_id)

    async def _send(self, conn: Connection, message: dict[str, Any]) -> bool:
        """False only when the connection took too long to accept the message.
        Any other failure is a dead connection, removed when it disconnects."""
        try:
            await asyncio.wait_for(conn.send_json(message), SEND_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        except Exception:  # noqa: BLE001
            pass
        return True

    async def _drop(self, member_id: str) -> None:
        """Remove a frozen connection from the room and close it, so its browser
        reconnects (the page does this by itself) instead of staying half-joined."""
        member = self.members.get(member_id)
        if member is None:
            return
        self._note(f"{member['name']} stopped responding and was removed")
        await self.leave(member_id)
        close = getattr(member["conn"], "close", None)
        if close is not None:
            try:
                await asyncio.wait_for(close(), 1)
            except Exception:  # noqa: BLE001 - best effort
                pass
