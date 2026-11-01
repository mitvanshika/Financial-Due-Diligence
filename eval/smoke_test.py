"""End-to-end smoke test: the REAL pipeline, the real index, real Groq calls.
Nothing is faked. One command, a PASS/FAIL line per check.

    python eval/smoke_test.py             # all three stages
    python eval/smoke_test.py pipeline    # Layers 3-5: questions in, answers out
    python eval/smoke_test.py actions     # Layer 6: checklist, flag, memo
    python eval/smoke_test.py room        # Layer 7: two people, a vote, a reconnect, a restart

The checklist, review queue, memos and room state go to a temporary folder, so
your real data/ is never touched. Cost: roughly 25 Groq calls and 3-5 minutes.
Stop `dd serve` / `dd session` first: the search index can only be open once.

What each PASS means is in the check names. A FAIL prints what was seen.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

results: list[tuple[str, bool]] = []


def check(name: str, ok: object, seen: object = "") -> bool:
    ok = bool(ok)
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or seen == "" else f"\n          saw: {str(seen)[:300]}"), flush=True)
    return ok


def redirect_records(tmp: Path) -> None:
    """Point Layer 6's files at a temporary folder."""
    import dd.actions as act

    act.CHECKLIST_PATH = tmp / "checklist.json"
    act.REVIEW_QUEUE_PATH = tmp / "review_queue.json"
    act.MEMO_DIR = tmp / "memos"


def _get(state: dict, key: str, question: str):
    """An answer computed once and shared between stages."""
    from dd.generation import answer

    if key not in state:
        state[key] = answer(question)
    return state[key]


def pipeline_is_down(state: dict, stage: str) -> bool:
    """If the pipeline itself fails, every later check would fail for the same
    reason. Say that once instead."""
    broken = state.get("net_income")
    if broken is not None and broken.text.startswith("Model call failed"):
        check(f"{stage}: the pipeline works (fix this first - see the pipeline stage)", False, broken.text)
        return True
    return False


NET_INCOME = "What was Apple's net income for FY2025?"
FUTURE = "What is Apple's projected revenue for FY2027?"
UNITS = "How many physical iPhone units did Apple sell in FY2025?"
DISASTERS = "What risk does Apple disclose regarding natural disasters and its facilities?"


# --- stage 1: Layers 3-5 -----------------------------------------------------------


def stage_pipeline(state: dict, tmp: Path) -> None:
    print("\n== pipeline: Layers 3-5 ==")
    a = _get(state, "net_income", NET_INCOME)
    if a.text.startswith("Model call failed"):
        check("pipeline: a real question can be answered at all", False, a.text)
        return
    check("net income: answered", a.answered, a.text)
    check("net income: states 112,010", "112,010" in a.text, a.text)
    check("net income: cites the database figure [S1]", any(c.marker == "[S1]" for c in a.citations), a.text)
    check("net income: confidence is HIGH", a.confidence is not None and a.confidence.band == "high",
          a.confidence and (a.confidence.band, a.confidence.score, a.confidence.notes))

    a = _get(state, "future", FUTURE)
    check("FY2027 projection: refused (a 10-K has no forecasts)", not a.answered and not a.text.startswith("Model call failed"), a.text)

    a = _get(state, "units", UNITS)
    check("iPhone units: refused, and not by a model error", not a.answered and not a.text.startswith("Model call failed"), a.text)

    a = _get(state, "disasters", DISASTERS)
    text = a.text.lower()
    check("natural disasters: answered with at least one citation", a.answered and bool(a.citations), a.text)
    check("natural disasters: mentions the actual risk", any(w in text for w in ("earthquake", "natural disaster", "extreme weather")), a.text)


# --- stage 2: Layer 6 -----------------------------------------------------------------


def stage_actions(state: dict, tmp: Path) -> None:
    import json

    import dd.actions as act

    print("\n== actions: Layer 6 ==")
    answered = _get(state, "net_income", NET_INCOME)
    if pipeline_is_down(state, "actions"):
        return
    if not check("actions: there is an answered question to act on", answered.answered, answered.text):
        return
    refused = _get(state, "future", FUTURE)

    # checklist item
    item = act.propose_checklist_item(answered)
    problems = act.validate_checklist_item(item, answered)
    check("checklist: the draft passes the rules", not problems, problems)
    if not problems:
        act.execute_checklist_item(item, answered)
        saved = json.loads(act.CHECKLIST_PATH.read_text(encoding="utf-8"))
        check("checklist: one item was saved with its sources", len(saved) == 1 and saved[0]["sources"], saved)
        check("checklist: the same item cannot be added twice",
              any("already on the checklist" in p for p in act.validate_checklist_item(item, answered)))

    # review flag on a refusal, then close it
    flag = act.propose_flag(refused)
    check("flag: allowed on a refusal", act.validate_flag(flag, refused) == [])
    act.execute_flag(flag, refused)
    open_now = act.open_flags()
    check("flag: it is listed as open", len(open_now) == 1, open_now)
    check("flag: the same question cannot be flagged twice while open",
          bool(act.validate_flag(flag, refused)))
    if open_now:
        act.close_flag(open_now[0]["id"], "smoke test")
        check("flag: after closing, the question can be flagged again", act.validate_flag(flag, refused) == [])

    # memo
    memo = act.propose_memo(answered)
    problems = act.validate_memo(memo, answered)
    check("memo: the draft passes the rules", not problems, problems)
    if not problems:
        act.execute_memo(memo, answered)
        files = list(act.MEMO_DIR.glob("*.md"))
        check("memo: a file with a Sources section was written",
              len(files) == 1 and "## Sources" in files[0].read_text(encoding="utf-8"), files)


# --- stage 3: Layer 7 -----------------------------------------------------------------


def stage_room(state: dict, tmp: Path) -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from dd.server import create_app

    print("\n== room: Layer 7 (two people, over real WebSockets) ==")
    state_file = tmp / "room_state.json"

    def wait(ws, condition):
        """Read messages until the shared state satisfies `condition`."""
        while True:
            msg = ws.receive_json()
            if msg["type"] == "error":
                raise RuntimeError(msg["message"])
            if msg["type"] == "state" and condition(msg["state"]):
                return msg

    def join(ws, name):
        ws.send_json({"type": "join", "name": name})
        return wait(ws, lambda s: any(m["name"].startswith(name) for m in s["members"]))["you"]

    with TestClient(create_app(state_file=state_file)) as client:
        ana = client.websocket_connect("/ws").__enter__()
        ben_cm = client.websocket_connect("/ws")
        ben = ben_cm.__enter__()
        join(ana, "Ana")
        ben_id = join(ben, "Ben")
        wait(ana, lambda s: len(s["members"]) == 2)

        ana.send_json({"type": "ask", "text": NET_INCOME})
        print("  (asking the real pipeline - this takes a while)", flush=True)
        s = wait(ana, lambda s: len(s["history"]) == 1)["state"]
        entry = s["history"][0]
        got_answer = check("room: the question was answered", entry["answer"] and entry["answer"]["answered"],
                           entry.get("error") or entry["answer"])
        if not got_answer:
            return  # nothing to propose actions on; the rest would fail for the same reason
        check("room: live steps were recorded", len(entry["steps"]) >= 3, entry["steps"])
        qid = entry["id"]

        # an action everyone approves
        ana.send_json({"type": "propose", "kind": "checklist", "question_id": qid})
        s = wait(ana, lambda s: (s["pending_action"] or {}).get("status") == "voting" or len(s["action_history"]) == 1)["state"]
        voting = (s["pending_action"] or {}).get("status") == "voting"
        check("room: the checklist draft reached a vote", voting,
              s["action_history"] and (s["action_history"][-1]["status"], s["action_history"][-1]["problems"]))
        if voting:
            aid = s["pending_action"]["id"]
            ana.send_json({"type": "vote", "action_id": aid, "approve": True})
            ben.send_json({"type": "vote", "action_id": aid, "approve": True})
            s = wait(ana, lambda s: len(s["action_history"]) == 1)["state"]
            check("room: approved by both, then run", s["action_history"][0]["status"] == "done", s["action_history"][0])

        # a vote interrupted by a dropped connection
        ana.send_json({"type": "propose", "kind": "flag", "question_id": qid})
        s = wait(ana, lambda s: (s["pending_action"] or {}).get("status") == "voting")["state"]
        aid = s["pending_action"]["id"]
        ana.send_json({"type": "vote", "action_id": aid, "approve": True})
        wait(ana, lambda s: any(v["vote"] is True for v in (s["pending_action"] or {"voters": []})["voters"]))
        ben_cm.__exit__(None, None, None)  # Ben's Wi-Fi drops
        s = wait(ana, lambda s: any(m["away"] for m in s["members"]))["state"]
        check("room: the dropped person is shown away and the vote stays open",
              s["pending_action"] is not None and s["pending_action"]["status"] == "voting", s["pending_action"])
        time.sleep(1)
        with client.websocket_connect("/ws") as ben2:
            again = join(ben2, "Ben")
            check("room: reconnecting gives the same place back", again == ben_id, (again, ben_id))
            ben2.send_json({"type": "vote", "action_id": aid, "approve": True})
            s = wait(ana, lambda s: len(s["action_history"]) == 2)["state"]
            check("room: the interrupted vote completed", s["action_history"][1]["status"] == "done", s["action_history"][1])
        ana.__exit__(None, None, None)

    # a restart: new server, same state file
    with TestClient(create_app(state_file=state_file)) as client2:
        with client2.websocket_connect("/ws") as cara:
            cara.send_json({"type": "join", "name": "Cara"})
            s = wait(cara, lambda s: any(m["name"] == "Cara" for m in s["members"]))["state"]
            check("room: history survives a restart", [h["text"] for h in s["history"]] == [NET_INCOME], s["history"])
            check("room: finished actions survive a restart", len(s["action_history"]) == 2, s["action_history"])
        try:
            with client2.websocket_connect("/ws", headers={"origin": "http://evil.example"}) as bad:
                bad.send_json({"type": "join", "name": "Mallory"})
                bad.receive_json()
            check("room: a page from another site is refused", False, "it was let in")
        except WebSocketDisconnect:
            check("room: a page from another site is refused", True)


STAGES = {"pipeline": stage_pipeline, "actions": stage_actions, "room": stage_room}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", help="pipeline | actions | room (default: all)")
    args = ap.parse_args()
    chosen = args.stages or list(STAGES)
    unknown = [s for s in chosen if s not in STAGES]
    if unknown:
        ap.error(f"unknown stage(s): {', '.join(unknown)}")

    state: dict = {}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        redirect_records(tmp)
        for name in chosen:
            try:
                STAGES[name](state, tmp)
            except Exception as exc:  # noqa: BLE001 - a crash is a result, not the end of the run
                check(f"{name}: ran to the end without crashing", False, f"{type(exc).__name__}: {exc}")

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
