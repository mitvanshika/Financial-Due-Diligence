"""Retrieval-only check: does the final 6 contain the passage that holds the answer?

Why this exists: tuning search against one question (tariffs) can quietly break
another (cybersecurity did, once). This runs every probe in
eval/retrieval_probe.json through Layer 3 only - no Groq calls, no quota, no
randomness - and says which needed passages made the final 6.

    python eval/probe_retrieval.py --label baseline
    # change something in config.yaml or retrieval.py, then:
    python eval/probe_retrieval.py --label try1 --compare eval/probe_baseline.json

A change is only worth keeping if it GAINS needs and LOSES none. For every
needed passage that is missed, it prints WHY: its rank in each search (up to
100) and the reranker's score for it, so "never retrieved" and "retrieved but
scored low" can be told apart.

Stop `dd serve` / `dd session` first (the index can only be open once).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).parent
PROBES = EVAL_DIR / "retrieval_probe.json"


def first_hit(final_ids: list[str], any_of: list[str]) -> int | None:
    """1-based position of the first needed chunk in the final list, else None."""
    for pos, cid in enumerate(final_ids, start=1):
        if cid in any_of:
            return pos
    return None


def changes(old: dict[str, bool], new: dict[str, bool]) -> tuple[list[str], list[str]]:
    """(gained, lost) need names between two runs. Needs only in one run are ignored."""
    both = old.keys() & new.keys()
    gained = sorted(k for k in both if new[k] and not old[k])
    lost = sorted(k for k in both if old[k] and not new[k])
    return gained, lost


def _rank(ranked: list[tuple[str, float]], cid: str) -> str:
    for pos, (c, _) in enumerate(ranked, start=1):
        if c == cid:
            return f"#{pos}"
    return "-"


def run(label: str, compare: Path | None) -> None:
    from dd.chunking import read_chunks
    from dd.config import CHUNKS_PATH, CONFIG_PATH, get_config
    from dd.models import RetrievedChunk
    from dd.retrieval import dense_search, hybrid_retrieve, rerank, sparse_search

    cfg = get_config()
    by_id = {c.chunk_id: c for c in read_chunks(CHUNKS_PATH)}
    probes = json.loads(PROBES.read_text(encoding="utf-8"))["probes"]

    results: dict[str, bool] = {}
    questions_complete = 0
    for p in probes:
        q = p["question"]
        final = hybrid_retrieve(q, cfg)
        final_ids = [c.chunk.chunk_id for c in final]
        dense = dense_search(q, cfg, 100)
        sparse = sparse_search(q, cfg, 100)
        print(f"\n{q[:100]}")
        met = 0
        for need in p["needs"]:
            pos = first_hit(final_ids, need["any_of"])
            results[f"{q[:60]} :: {need['name']}"] = pos is not None
            met += pos is not None
            print(f"  {'HIT ' if pos else 'MISS'} {need['name']}" + (f"  (final #{pos})" if pos else ""))
            if pos is None:
                gold = [RetrievedChunk(chunk=by_id[c]) for c in need["any_of"] if c in by_id]
                for rc in rerank(q, gold, cfg):
                    cid = rc.chunk.chunk_id
                    print(f"       {cid[-4:]}: vector {_rank(dense, cid):>5}  bm25 {_rank(sparse, cid):>5}  "
                          f"reranker {rc.rerank_score:+.2f}")
        questions_complete += met == len(p["needs"])

    total = len(results)
    found = sum(results.values())
    print(f"\nneeds found in the final {cfg.retrieval.final_top_k}: {found}/{total}   "
          f"questions with every need found: {questions_complete}/{len(probes)}")

    out = EVAL_DIR / f"probe_{label}.json"
    cfg_sha = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()[:12]
    out.write_text(json.dumps({"label": label, "config_sha": cfg_sha, "results": results}, indent=2),
                   encoding="utf-8")
    print(f"saved {out}")

    if compare:
        old = json.loads(compare.read_text(encoding="utf-8"))["results"]
        gained, lost = changes(old, results)
        print(f"\nvs {compare.name}: gained {len(gained)}, lost {len(lost)}")
        for k in gained:
            print(f"  GAINED  {k}")
        for k in lost:
            print(f"  LOST    {k}")
        if lost:
            print("  -> this change breaks something that worked; do not keep it as is")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="name for this run; saved as eval/probe_<label>.json")
    ap.add_argument("--compare", type=Path, help="an earlier eval/probe_<label>.json to compare with")
    args = ap.parse_args()
    if not PROBES.exists():
        sys.exit(f"{PROBES} not found")
    run(args.label, args.compare)
