"""Layer 6: verified actions.

Layers 3-5 answer a question. This layer lets the system DO something with
an answer, but never on the model's judgement alone:

1. propose  - a draft of the action, in a fixed format
2. validate - code checks it against hard rules; any failure stops it here
3. approve  - a person sees the draft and says yes (the CLI does this step)
4. execute  - only then is it written anywhere

Three actions, each with its own rules:

- checklist item: short record of an issue. Needs a confident, cited answer.
- review flag:    hands a question to a human analyst. Built by plain code,
                  no LLM, and allowed exactly where the other two are not:
                  on refusals and low-confidence answers.
- risk memo:      a few paragraphs. Strictest: every paragraph must cite.

Checklist items and memos can only cite sources the Layer 5 answer already
cited, so an action can never rest on evidence the answer didn't have.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import DATA_DIR, Config, get_config
from .generation import _build_evidence, _clean_facts, _unverified_numbers
from .models import Answer
from .self_healing import get_groq_client

CHECKLIST_PATH = DATA_DIR / "checklist.json"
REVIEW_QUEUE_PATH = DATA_DIR / "review_queue.json"
MEMO_DIR = DATA_DIR / "memos"

_MARKER_RE = re.compile(r"\[S?\d+\]")


# --- shared helpers -----------------------------------------------------------


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_json(path: Path) -> list[dict]:
    """The records in a file. A missing or empty file (a crash can leave a
    0-byte one) means no records yet. A file that is not valid JSON is an
    error that names the file: guessing "no records" would let the next save
    overwrite whatever is in it."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}); it was left untouched - fix or move it") from exc


@contextlib.contextmanager
def _locked(path: Path, timeout: float = 10.0, stale_after: float = 30.0):
    """One writer at a time per records file. The CLI and the room can both
    save to the same file; without this, both read the old list and the
    second save silently erases the first one's entry."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))  # fails if it exists
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_after:
                    lock.unlink(missing_ok=True)  # left behind by a writer that crashed
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock} is held by another writer; if none is running, delete it")
            time.sleep(0.01)
    try:
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _append_json(path: Path, entry: dict) -> None:
    with _locked(path):
        rows = _load_json(path)
        rows.append(entry)
        _write_json(path, rows)


def _write_json(path: Path, rows: list[dict]) -> None:
    """Save the records. Written to a temporary file first and swapped in, so
    a crash mid-write leaves the old records intact, never half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _answer_problems(answer: Answer) -> list[str]:
    """Rules shared by the checklist and the memo: both act on the answer's
    content, so the answer has to exist and be trusted."""
    if not answer.answered:
        return ["the question wasn't answered, so there is nothing to act on"]
    if answer.confidence is None or answer.confidence.band == "low":
        return ["answer confidence is LOW; too weak to act on"]
    return []


def _citation_problems(markers: list[str], answer: Answer) -> list[str]:
    known = {c.marker for c in answer.citations}
    invented = list(dict.fromkeys(m for m in markers if m not in known))
    return [f"cites sources the answer doesn't have: {', '.join(invented)}"] if invented else []


def _number_problems(text: str, answer: Answer) -> list[str]:
    """Layer 5 refuses to show a number that matches nothing the answer cites.
    An action must not be a way around that: a checklist item or memo can only
    state numbers the answer's own sources support (same check, same limits).
    An answer with no retrieval record (never the case for a real one) can't
    be checked."""
    if answer.outcome is None:
        return []
    _, lookup = _build_evidence(answer.outcome.relevant_chunks, _clean_facts(answer.outcome))
    cited = [lookup[c.marker] for c in answer.citations if c.marker in lookup]
    bad = _unverified_numbers(text, cited)
    return [f"number(s) not found in the answer's cited sources: {', '.join(bad)}"] if bad else []


def _sources(markers: list[str], answer: Answer) -> list[dict]:
    by_marker = {c.marker: c for c in answer.citations}
    return [
        {"marker": m, "source": by_marker[m].source, "chunk_id": by_marker[m].chunk_id}
        for m in dict.fromkeys(markers)
    ]


def _confidence(answer: Answer) -> dict | None:
    c = answer.confidence
    return {"score": c.score, "band": c.band} if c else None


def _ask_json(prompt: str, name: str, schema: dict, cfg: Config) -> str:
    """One call to the small model, forced into a JSON shape (same pattern
    as Layer 4's grader). Returns the raw JSON text for a Pydantic model."""
    client = get_groq_client(cfg)
    # The model sometimes writes JSON Groq rejects (400 "json_validate_failed").
    # At temperature 0 it repeats the same mistake, so the one retry uses 0.5.
    for temperature in (0.0, 0.5):
        try:
            resp = client.chat.completions.create(
                model=cfg.generation.utility_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=cfg.generation.utility_max_tokens,
                temperature=temperature,
                reasoning_effort=cfg.generation.utility_effort,
                response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
            )
            return resp.choices[0].message.content
        except Exception as exc:  # noqa: BLE001
            if temperature == 0.5 or not _is_broken_json(exc):
                raise


def _is_broken_json(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 400 and "json_validate_failed" in str(exc)


def _source_list(answer: Answer) -> str:
    return "\n".join(f"{c.marker} {c.source}" for c in answer.citations)


# --- action 1: checklist item -------------------------------------------------


class ChecklistItem(BaseModel):
    """Field limits are the first check: a draft that doesn't fit this shape
    never gets as far as the rules in validate_checklist_item()."""

    title: str = Field(min_length=5, max_length=120)
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=10, max_length=600)
    citations: list[str] = Field(min_length=1)  # markers from the answer, e.g. "[1]", "[S1]"

    @field_validator("citations")
    @classmethod
    def _bracket_markers(cls, markers: list[str]) -> list[str]:
        """The model sometimes writes "1" or "S1" instead of "[1]" / "[S1]".
        Same source, different spelling - normalise it, so the check in
        validate_checklist_item() only rejects markers that point to nothing."""
        out = []
        for m in markers:
            m = m.strip()
            out.append(m if m.startswith("[") else f"[{m}]")
        return out


_CHECKLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "severity", "summary", "citations"],
}

_CHECKLIST_PROMPT = """Turn this answer from a company's 10-K into ONE item for \
a due-diligence checklist.

Question: {question}

Answer:
{answer}

Sources the answer cites:
{sources}

Write:
- title: a short name for the issue (under 100 characters)
- severity: low, medium or high, for how much this matters to an investor
- summary: two or three short sentences, under 400 characters in total, \
using only facts from the answer, with numbers written exactly as the answer \
writes them
- citations: the markers from the list above that the summary relies on"""


def propose_checklist_item(answer: Answer, cfg: Config | None = None) -> ChecklistItem:
    conf = cfg or get_config()
    prompt = _CHECKLIST_PROMPT.format(
        question=answer.query, answer=answer.text, sources=_source_list(answer)
    )
    return ChecklistItem.model_validate_json(_ask_json(prompt, "checklist_item", _CHECKLIST_SCHEMA, conf))


def validate_checklist_item(item: ChecklistItem, answer: Answer) -> list[str]:
    """Every rule that fails, in plain words. Empty list = allowed to go on
    to approval. All problems at once, so a person sees the full picture."""
    problems = _answer_problems(answer) + _citation_problems(item.citations, answer)
    problems += _number_problems(f"{item.title} {item.summary}", answer)
    existing = {e["title"].strip().lower() for e in _load_json(CHECKLIST_PATH)}
    if item.title.strip().lower() in existing:
        problems.append(f"an item titled '{item.title}' is already on the checklist")
    return problems


def execute_checklist_item(item: ChecklistItem, answer: Answer) -> str:
    """Each entry keeps the question, the full source lines and the
    confidence, so anyone reading the checklist can trace it to the filing."""
    entry = {
        "id": _new_id(),
        "added_at": _now(),
        "question": answer.query,
        "title": item.title,
        "severity": item.severity,
        "summary": item.summary,
        "sources": _sources(item.citations, answer),
        "confidence": _confidence(answer),
    }
    _append_json(CHECKLIST_PATH, entry)
    return f"added {entry['id']} to {CHECKLIST_PATH}"


# --- action 2: review flag ----------------------------------------------------


class ReviewFlag(BaseModel):
    reason: str = Field(min_length=10, max_length=400)
    priority: Literal["low", "medium", "high"]


def propose_flag(answer: Answer, cfg: Config | None = None) -> ReviewFlag:
    """No LLM here: the reason comes straight from the answer's state, and
    plain code can't make it up. Refusals and LOW answers are exactly what a
    human should look at, so they get a higher priority."""
    if not answer.answered:
        return ReviewFlag(reason=f"System could not answer: {answer.text}"[:400], priority="medium")
    if answer.confidence is None or answer.confidence.band == "low":
        score = answer.confidence.score if answer.confidence else "n/a"
        return ReviewFlag(reason=f"Low-confidence answer (score {score}); check it against the filing.", priority="medium")
    return ReviewFlag(reason="Reviewer asked to double-check this answer.", priority="low")


def validate_flag(flag: ReviewFlag, answer: Answer) -> list[str]:
    """Allowed on any answer, refused or not. Only rule: don't queue the
    same question twice while it's still waiting for a reviewer."""
    open_questions = {
        e["question"].strip().lower() for e in _load_json(REVIEW_QUEUE_PATH) if e["status"] == "open"
    }
    if answer.query.strip().lower() in open_questions:
        return ["this question is already in the review queue"]
    return []


def execute_flag(flag: ReviewFlag, answer: Answer) -> str:
    entry = {
        "id": _new_id(),
        "added_at": _now(),
        "status": "open",
        "question": answer.query,
        "priority": flag.priority,
        "reason": flag.reason,
        "system_answered": answer.answered,
        "system_answer": answer.text,
        "confidence": _confidence(answer),
    }
    _append_json(REVIEW_QUEUE_PATH, entry)
    return f"flagged {entry['id']} in {REVIEW_QUEUE_PATH}"


def open_flags() -> list[dict]:
    """Review flags still waiting for a reviewer."""
    return [e for e in _load_json(REVIEW_QUEUE_PATH) if e["status"] == "open"]


def close_flag(flag_id: str, note: str = "") -> str:
    """A reviewer has looked at it. Until a flag is closed, its question can't
    be flagged again (see validate_flag)."""
    with _locked(REVIEW_QUEUE_PATH):
        rows = _load_json(REVIEW_QUEUE_PATH)
        for entry in rows:
            if entry["id"] == flag_id:
                if entry["status"] != "open":
                    raise ValueError(f"flag {flag_id} is already {entry['status']}")
                entry.update(status="closed", closed_at=_now(), closed_note=note)
                _write_json(REVIEW_QUEUE_PATH, rows)
                return f"closed {flag_id}"
    raise ValueError(f"no open flag with id {flag_id}")


# --- action 3: risk memo ------------------------------------------------------


class RiskMemo(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    body: str = Field(min_length=200, max_length=3000)  # markdown paragraphs


_MEMO_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
    "required": ["title", "body"],
}

_MEMO_PROMPT = """Write a short due-diligence risk memo from this answer about \
a company's 10-K.

Question: {question}

Answer:
{answer}

Sources the answer cites:
{sources}

Write:
- title: a short title for the memo (under 100 characters)
- body: 2 to 4 paragraphs separated by a blank line, under 2000 characters in \
total. Use only facts from the answer, with numbers written exactly as the \
answer writes them. EVERY paragraph must end with the \
marker(s) of its source, written exactly as in the list above, e.g. [1] or [S1]."""


def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def propose_memo(answer: Answer, cfg: Config | None = None) -> RiskMemo:
    conf = cfg or get_config()
    prompt = _MEMO_PROMPT.format(question=answer.query, answer=answer.text, sources=_source_list(answer))
    return RiskMemo.model_validate_json(_ask_json(prompt, "risk_memo", _MEMO_SCHEMA, conf))


def validate_memo(memo: RiskMemo, answer: Answer) -> list[str]:
    """The strictest rules of the three. A longer text has more room for a
    sentence the filing doesn't back, so citations are checked paragraph by
    paragraph, not just "somewhere in the memo"."""
    problems = _answer_problems(answer)
    paragraphs = _paragraphs(memo.body)
    # The prompt asks for 2-4; allow 1-6 so a small miss isn't a rejection.
    if not 1 <= len(paragraphs) <= 6:
        problems.append(f"memo has {len(paragraphs)} paragraphs; expected 1 to 6")
    for n, p in enumerate(paragraphs, 1):
        if not _MARKER_RE.search(p):
            problems.append(f"paragraph {n} cites no source")
    problems += _citation_problems(_MARKER_RE.findall(memo.body), answer)
    problems += _number_problems(f"{memo.title} {memo.body}", answer)
    return problems


def execute_memo(memo: RiskMemo, answer: Answer) -> str:
    memo_id = _new_id()
    sources = _sources(_MARKER_RE.findall(memo.body), answer)
    conf = _confidence(answer)
    lines = [
        f"# {memo.title}",
        "",
        f"Question: {answer.query}  ",
        f"Written: {_now()}  ",
        f"Answer confidence: {conf['band']} ({conf['score']})" if conf else "Answer confidence: n/a",
        "",
        memo.body.strip(),
        "",
        "## Sources",
        "",
        *[f"- {s['marker']} {s['source']}" for s in sources],
        "",
    ]
    MEMO_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMO_DIR / f"{memo_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return f"wrote memo {memo_id} to {path}"


# --- the three actions, by name (used by the CLI) ------------------------------

ACTIONS = {
    "checklist": (propose_checklist_item, validate_checklist_item, execute_checklist_item),
    "flag": (propose_flag, validate_flag, execute_flag),
    "memo": (propose_memo, validate_memo, execute_memo),
}