"""Run the eval set through the full pipeline (Layers 3-5) and score it.

    python eval/run_eval.py                       # every question in eval_set.json (29)
    python eval/run_eval.py numeric_lookup        # one type only
    python eval/run_eval.py numeric_lookup no_answer
    python eval/run_eval.py --repeat 3            # each question 3 times
    python eval/run_eval.py --summarize eval/results_old.jsonl   # no model calls

Scoring:
- numeric_lookup: automatic - the expected number must appear in a sentence
  of the answer that carries a citation, so a number the answer only
  mentions in passing doesn't count. "X million", "X billion", and bare
  numbers in an answer that says "in millions" all count. Bracketed
  negatives like (1,234) are compared by size.
- no_answer: automatic - correct only if the system refused to answer.
- narrative_comprehension / cross_referencing / trend: graded by hand. These
  are free text; the script writes eval/review.md with expected and actual
  answers side by side. Type correct / partly / wrong / refused next to each
  Verdict; the next run (or --summarize) saves them to eval/hand_grades.json
  before rewriting review.md, counts them in the summary, and fills them in
  again for any answer with exactly the same text.

Why a score is what it is: each result also saves its confidence notes, the
three search signals, each cited item's grade, database figures given vs.
cited, and every retrieval attempt. compare_runs.py prints them.

What produced the results: the first line of eval/results.jsonl records the
git commit, a fingerprint of any uncommitted changes to src/ and config.yaml,
fingerprints of config.yaml and eval_set.json, the model names and the date.
A run only resumes if all of those still match - otherwise it stops, so old
and new answers never get mixed in one file.

Resuming: each result is saved the moment it finishes, and questions already
in the file are skipped. Groq's free tier caps tokens per day, so a full run
may stop partway - run it again later and it picks up where it left off.

--repeat N: the model doesn't give the same answer every time. With N runs,
the summary shows how often each question passed, and marks a question
"unstable" when it passed some runs and failed others. A 3x run is ~87
questions, more than one day of Groq's free tier - resume covers that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).parent
ROOT = EVAL_DIR.parent
EVAL_SET = EVAL_DIR / "eval_set.json"
CONFIG = ROOT / "config.yaml"
RESULTS = EVAL_DIR / "results.jsonl"
REVIEW = EVAL_DIR / "review.md"

AUTO_TYPES = {"numeric_lookup", "no_answer"}
# Start of the text generation.py returns when a Groq call fails.
MODEL_FAILED = "Model call failed"

# --- scoring ---------------------------------------------------------------

_MARKER_RE = re.compile(r"\[S?\d+\]")
_NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(million|billion)?", re.IGNORECASE)
_SCALE = {"million": 1e6, "billion": 1e9}
# A new sentence starts after . ! or ? and a space - but not before a
# citation marker, so "...in 2025. [S1]" keeps its marker.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?!\[)")


def _numbers_in(text: str, in_millions: bool = False) -> list[float]:
    """Every number in the text, with 'million'/'billion' applied.
    Citation markers are removed first so [1] isn't read as the number 1.
    If the answer says "in millions", a bare number may be in millions, so
    both readings are kept: 112,010 -> 112010 and 112010000000."""
    text = _MARKER_RE.sub(" ", text)
    out = []
    for digits, unit in _NUMBER_RE.findall(text):
        value = float(digits.replace(",", ""))
        if unit:
            out.append(value * _SCALE[unit.lower()])
        else:
            out.append(value)
            if in_millions:
                out.append(value * 1e6)
    return out


def _cited_numbers(text: str) -> list[float]:
    """Numbers from sentences that carry a citation marker - the ones the
    answer actually backs with a source."""
    in_millions = "in millions" in text.lower()
    cited = [s for s in _SENTENCE_SPLIT_RE.split(text) if _MARKER_RE.search(s)]
    return [n for s in cited for n in _numbers_in(s, in_millions)]


def _expected_number(expected: str) -> float:
    """The first number in the expected answer: '$112,010,000,000 (~$112.0
    billion)' -> 112010000000.0. The part in brackets is just a restatement."""
    return _numbers_in(expected)[0]


def score(item: dict, result: dict) -> bool | None:
    """True/False for auto-graded types, None for ones graded by hand."""
    kind = item["type"]
    if kind == "no_answer":
        return not result["answered"]
    if kind == "numeric_lookup":
        if not result["answered"]:
            return False
        target = abs(_expected_number(item["expected_answer"]))
        # 0.1% tolerance: "$112.0 billion" is a rounded 112,010,000,000.
        # abs(): a bracketed negative "(1,234)" is read as 1,234.
        return any(abs(n - target) <= 0.001 * target for n in _cited_numbers(result["text"]))
    return None


# --- what produced these results -------------------------------------------


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


def _model_names(node, path: str = "") -> dict[str, str]:
    """Every config.yaml setting whose name contains 'model', e.g.
    generation.utility_model -> openai/gpt-oss-20b."""
    out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_model_names(value, f"{path}{key}."))
    elif "model" in path:
        out[path.rstrip(".")] = str(node)
    return out


def current_meta() -> dict:
    config_text = CONFIG.read_text(encoding="utf-8")
    # Uncommitted edits change the code without changing the commit, so they
    # get their own fingerprint. "" = clean.
    diff = _git("diff", "HEAD", "--", "src", "config.yaml")
    return {
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "uncommitted_changes": _sha(diff) if diff else "",
        "config_sha": _sha(config_text),
        "eval_set_sha": _sha(EVAL_SET.read_text(encoding="utf-8")),
        "models": _model_names(yaml.safe_load(config_text)),
    }


# --- why an answer scored what it did ---------------------------------------


def explain(a) -> dict:
    """What produced the score, saved with each result, so a change between
    two runs can be read off the file instead of guessed from the answer
    ("0.69: answer's numbers match none of the database figures").
    All of it already exists once answer() returns; none of it costs a call."""
    out: dict = {"notes": a.confidence.notes if a.confidence else []}
    if a.confidence:
        out["signals"] = {
            "hybrid_agreement": a.confidence.hybrid_agreement,
            "rerank_strength": a.confidence.rerank_strength,
            "loop_cleanliness": a.confidence.loop_cleanliness,
        }
    if a.outcome is None:
        return out
    o = a.outcome
    facts = [f for f in o.structured_facts if "value" in f]
    # Layer 4's grade for each cited item: passages by chunk id, database
    # figures by the "structured:table:label:year" id their citation carries.
    grades = {g.retrieved.chunk.chunk_id: g.relevance for g in o.chunks}
    grades.update({f"structured:{f['table']}:{f['label']}:{f['year']}": f.get("_relevance")
                   for f in o.structured_facts})
    out["cited_grades"] = {c.marker: grades.get(c.chunk_id) for c in a.citations}
    out["figures"] = {"given": len(facts), "cited": sum(c.marker.startswith("[S") for c in a.citations)}
    out["attempt_log"] = [
        {"strategy": at.strategy, "query": at.query, "relevant": at.n_relevant,
         "retrieved": at.n_retrieved, "note": at.note}
        for at in o.attempts
    ]
    if not o.succeeded:
        out["refusal_reason"] = o.abstain_reason
    elif a.refused_by_model:
        out["refusal_reason"] = "the answer model said the evidence doesn't answer the question"
    return out


# --- hand grades -------------------------------------------------------------
# Free-text answers are graded by hand in review.md. The verdicts are saved
# to hand_grades.json, keyed by question + exact answer text, so they survive
# the next run rewriting review.md, and an answer already graded comes back
# with its verdict filled in.

HAND_GRADES = EVAL_DIR / "hand_grades.json"
VERDICTS = ("correct", "partly", "wrong", "refused")
_ANSWER_ID_RE = re.compile(r"<!-- answer:([0-9a-f]{12}) -->")
_VERDICT_RE = re.compile(r"^\*\*Verdict:\*\*(.*)$")


def answer_key(question: str, text: str) -> str:
    return _sha(question + "\n" + text)


def load_hand_grades() -> dict[str, dict]:
    if not HAND_GRADES.exists():
        return {}
    return json.loads(HAND_GRADES.read_text(encoding="utf-8"))


def collect_verdicts() -> dict[str, dict]:
    """Save the verdicts typed in review.md, before a run rewrites it.
    A verdict is the first word after **Verdict:** (correct / partly / wrong /
    refused); anything after it is kept as a comment. A blank verdict
    leaves an earlier one as it was."""
    grades = load_hand_grades()
    if not REVIEW.exists():
        return grades
    changed, key = 0, None
    for line in REVIEW.read_text(encoding="utf-8").splitlines():
        if m := _ANSWER_ID_RE.search(line):
            key = m.group(1)
            continue
        m = _VERDICT_RE.match(line.strip())
        if not m or key is None:
            continue
        typed = m.group(1).strip()
        word = typed.split(None, 1)[0].strip("*:,.-").lower() if typed else ""
        if word in VERDICTS:
            comment = typed.split(None, 1)[1].lstrip(" -:—") if " " in typed else ""
            entry = {"verdict": word, "comment": comment}
            if {k: grades.get(key, {}).get(k) for k in entry} != entry:
                grades[key] = {**entry, "graded": _now()}
                changed += 1
        elif word:
            print(f"note: review.md verdict {typed!r} not understood - use one of {', '.join(VERDICTS)}")
        key = None
    if changed:
        HAND_GRADES.write_text(json.dumps(grades, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {changed} hand verdict(s) from {REVIEW.name} to {HAND_GRADES.name}")
    return grades


def verdict_of(result: dict, grades: dict[str, dict]) -> str | None:
    """correct / partly / wrong / refused, or None if not graded (yet).
    Auto-graded types are scored by score() instead."""
    entry = grades.get(answer_key(result["question"], result["text"]))
    return entry["verdict"] if entry else None


# --- running ---------------------------------------------------------------


def _load_done(meta: dict) -> dict[tuple[str, int], dict]:
    """Results already in results.jsonl, keyed by (question, run number).
    Refuses to continue a file made by different code, config or questions."""
    if not RESULTS.exists():
        RESULTS.write_text(
            json.dumps({"_meta": {**meta, "started": _now()}}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {}

    lines = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    saved = lines[0].get("_meta") if lines else None
    if saved is None:
        sys.exit(
            f"{RESULTS} has no record of what produced it (made by an older version "
            "of this script). Rename it, e.g. results_old.jsonl, and run again."
        )
    changed = [k for k in meta if saved.get(k) != meta[k]]
    if changed:
        sys.exit(
            f"{RESULTS} was made with a different {', '.join(changed)}:\n"
            + "\n".join(f"  {k}: then {saved.get(k)!r}, now {meta[k]!r}" for k in changed)
            + "\nMixing the two would make the results meaningless. Rename the file "
            "(e.g. results_<what changed>.jsonl) and run again."
        )
    return {(r["question"], r.get("run", 1)): r for r in lines[1:]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(types: set[str] | None, repeat: int) -> list[dict]:
    # Imported here, not at the top: loading the pipeline takes seconds, and
    # compare_runs.py and --summarize use this file without running it.
    from dd.generation import answer

    items = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if types:
        items = [i for i in items if i["type"] in types]
    meta = current_meta()
    if meta["uncommitted_changes"]:
        print("note: src/ or config.yaml has uncommitted changes - commit first so "
              "these results can be traced to a commit.\n")
    done = _load_done(meta)

    total = len(items) * repeat
    n = 0
    for run_no in range(1, repeat + 1):
        for item in items:
            n += 1
            q = item["question"]
            if (q, run_no) in done:
                continue
            print(f"[{n}/{total}] run {run_no} {item['type']}: {q[:60]}", flush=True)
            try:
                a = answer(q)
            except Exception as exc:  # noqa: BLE001 - one failure shouldn't end the run
                # Not saved, so the next run retries it. Usually a rate limit.
                print(f"    ERROR (will retry next run): {type(exc).__name__}: {exc}")
                continue
            if a.text.startswith(MODEL_FAILED):
                # Layer 5 reports a failed model call as a refusal instead of
                # crashing. That is not an answer: saved, it would score as a
                # correct refusal on no_answer questions. Skip it and retry.
                print(f"    ERROR (will retry next run): {a.text}")
                continue

            result = {
                "question": q,
                "run": run_no,
                "type": item["type"],
                "expected": item["expected_answer"],
                "answered": a.answered,
                "text": a.text,
                "citations": [f"{c.marker} {c.source}" for c in a.citations],
                "confidence": a.confidence.score if a.confidence else None,
                "band": a.confidence.band if a.confidence else None,
                "attempts": len(a.outcome.attempts) if a.outcome else 0,
                "elapsed_s": a.elapsed_s,
                **explain(a),
            }
            result["correct"] = score(item, result)
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
            done[(q, run_no)] = result
            mark = {True: "CORRECT", False: "WRONG", None: "(grade by hand)"}[result["correct"]]
            print(f"    {mark}  band={result['band']}  {result['elapsed_s']}s")

    return [done[(i["question"], r)] for r in range(1, repeat + 1) for i in items
            if (i["question"], r) in done]


# --- reporting -------------------------------------------------------------


def _by_question(results: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in results:
        out.setdefault(r["question"], []).append(r)
    return out


def summarize(results: list[dict], repeat: int, grades: dict[str, dict] | None = None) -> None:
    groups = _by_question(results)
    grades = grades or {}

    print("\n=== by question type ===")
    for kind in sorted({r["type"] for r in results}):
        qs = {q: rows for q, rows in groups.items() if rows[0]["type"] == kind}
        rows = [r for rs in qs.values() for r in rs]
        if kind in AUTO_TYPES:
            ok = sum(1 for r in rows if r["correct"])
            always = sum(1 for rs in qs.values() if all(r["correct"] for r in rs))
            line = f"  {kind:<26} {ok}/{len(rows)} correct"
            if repeat > 1:
                line += f"  ({always}/{len(qs)} questions correct every run)"
            print(line)
        else:
            verdicts = [verdict_of(r, grades) for r in rows]
            counts = ", ".join(f"{verdicts.count(v)} {v}" for v in VERDICTS if verdicts.count(v))
            todo = verdicts.count(None)
            print(f"  {kind:<26} graded by hand: {counts or 'none yet'}"
                  + (f"; {todo} still to grade in {REVIEW.name}" if todo else ""))

    if repeat > 1:
        # Unstable = the same question gave a different outcome across runs:
        # passed some and failed others, or answered some and refused others.
        print("\n=== per question (unstable = different outcome across runs) ===")
        for q, rows in groups.items():
            if rows[0]["type"] in AUTO_TYPES:
                k = sum(1 for r in rows if r["correct"])
                label, flips = f"{k}/{len(rows)} correct", 0 < k < len(rows)
            else:
                k = sum(1 for r in rows if r["answered"])
                label, flips = f"{k}/{len(rows)} answered", 0 < k < len(rows)
            print(f"  {'UNSTABLE ' if flips else '         '}{label:<14} {q[:70]}")

    # Does "high" confidence actually mean "usually right"? Every answer with
    # a band and a grade can say: auto-graded ones, and hand-graded ones once
    # their verdict is in. A refusal has no band.
    outcome = {}
    for i, r in enumerate(results):
        if not r["band"]:
            continue
        if r["correct"] is not None:
            outcome[i] = "correct" if r["correct"] else "wrong"
        elif v := verdict_of(r, grades):
            outcome[i] = v
    if outcome:
        print("\n=== confidence vs. correctness (graded answers) ===")
        for band in ("high", "medium", "low"):
            got = [v for i, v in outcome.items() if results[i]["band"] == band]
            if got:
                rest = ", ".join(f"{got.count(v)} {v}" for v in VERDICTS[1:] if got.count(v))
                print(f"  {band:<7} {got.count('correct')}/{len(got)} correct" + (f" ({rest})" if rest else ""))
        ungraded = sum(1 for r in results if r["band"] and r["correct"] is None and not verdict_of(r, grades))
        if ungraded:
            print(f"  ({ungraded} answer(s) not counted: not graded by hand yet)")

    times = [r["elapsed_s"] for r in results]
    if times:
        print(f"\naverage time: {sum(times) / len(times):.1f}s per question")


def write_review(results: list[dict], grades: dict[str, dict] | None = None) -> None:
    groups = {q: rows for q, rows in _by_question(results).items() if rows[0]["type"] not in AUTO_TYPES}
    if not groups:
        return
    grades = grades or {}
    lines = ["# Answers to grade by hand", "",
             "For each: is the answer correct and supported by its sources? Write correct / partly / "
             "wrong / refused next to Verdict, optionally followed by a comment. Verdicts are saved to "
             f"{HAND_GRADES.name} on the next run of run_eval.py; one already filled in was given to "
             "the same answer before. Leave the <!-- answer:... --> lines as they are.", ""]
    todo = 0
    for n, (q, rows) in enumerate(groups.items(), 1):
        lines += [f"## {n}. [{rows[0]['type']}] {q}", "", f"**Expected:** {rows[0]['expected']}", ""]
        for r in rows:
            run = f"run {r.get('run', 1)}, " if len(rows) > 1 else ""
            key = answer_key(r["question"], r["text"])
            saved = grades.get(key)
            todo += saved is None
            verdict = f"{saved['verdict']} {saved.get('comment', '')}".strip() if saved else ""
            lines += [
                f"**System ({run}{'answered' if r['answered'] else 'REFUSED'}, band={r['band']}, "
                f"confidence={r['confidence']}):** {r['text']}", "",
                *[f"- {c}" for c in r["citations"]],
                *[f"- *note: {note}*" for note in r.get("notes", [])], "",
                f"<!-- answer:{key} -->",
                f"**Verdict:** {verdict}", "",
            ]
    REVIEW.write_text("\n".join(lines), encoding="utf-8")
    total = sum(len(r) for r in groups.values())
    print(f"\nwrote {REVIEW} - {total} hand-graded answers, {todo} still to grade")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run and score the eval set.")
    parser.add_argument("types", nargs="*", help="question types to run (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="run each question N times")
    parser.add_argument("--summarize", metavar="RESULTS", type=Path,
                        help="no model calls: summarize a saved results file (and write its review.md)")
    args = parser.parse_args()
    # Before anything else: once the run starts, review.md gets rewritten.
    grades = collect_verdicts()
    if args.summarize:
        rows = [json.loads(line) for line in args.summarize.read_text(encoding="utf-8").splitlines() if line.strip()]
        results = [r for r in rows if "question" in r and (not args.types or r["type"] in args.types)]
        repeat = max((r.get("run", 1) for r in results), default=1)
    else:
        results, repeat = run(set(args.types) or None, args.repeat), args.repeat
    summarize(results, repeat, grades)
    write_review(results, grades)