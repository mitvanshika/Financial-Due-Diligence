"""Show one question's answer from two eval runs side by side.

    python eval/compare_runs.py eval/results_round2_before.jsonl eval/results.jsonl cybersecurity

The last argument is any part of the question text (not case-sensitive).
Shows what produced each score (confidence notes, retrieval attempts) and
the hand verdict, where the results file and hand_grades.json have them.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_eval import load_hand_grades, verdict_of  # noqa: E402


def load(path: str) -> dict[str, dict]:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return {r["question"]: r for r in rows if "question" in r}  # skips the _meta line


before_path, after_path, search = sys.argv[1], sys.argv[2], sys.argv[3].lower()
before, after = load(before_path), load(after_path)
grades = load_hand_grades()

for question in [q for q in after if search in q.lower()] or ["(no question matches)"]:
    print(f"\n=== {question}\n")
    for label, rows in (("BEFORE", before), ("AFTER", after)):
        r = rows.get(question)
        if r is None:
            print(f"{label}: (not in this run)\n")
            continue
        status = "answered" if r["answered"] else "REFUSED"
        verdict = verdict_of(r, grades)
        print(f"{label}: {status}, band={r['band']}, confidence={r['confidence']}"
              + (f", graded {verdict}" if verdict else ""))
        print(r["text"])
        for c in r["citations"]:
            grade = (r.get("cited_grades") or {}).get(c.split(" ", 1)[0])
            print(f"  {c}" + (f"  (graded {grade:.2f})" if grade is not None else ""))
        if "notes" not in r:
            print("  (made before notes were saved)")
        for note in r.get("notes", []):
            print(f"  note: {note}")
        for i, at in enumerate(r.get("attempt_log", []), 1):
            print(f"  attempt {i} [{at['strategy']}] {at['relevant']}/{at['retrieved']} relevant: {at['query'][:90]}")
        if r.get("refusal_reason"):
            print(f"  refused because: {r['refusal_reason']}")
        print()
