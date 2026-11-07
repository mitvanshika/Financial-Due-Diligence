"""Tests for the pure parts of eval/probe_retrieval.py (the model-free logic),
and a check that every gold chunk id in the probe file really exists.

Run from the project root:   pytest
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("probe_retrieval", ROOT / "eval" / "probe_retrieval.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_first_hit_is_the_position_of_the_earliest_needed_chunk():
    assert probe.first_hit(["a", "b", "c"], ["c", "b"]) == 2
    assert probe.first_hit(["a", "b"], ["z"]) is None
    assert probe.first_hit([], ["z"]) is None


def test_changes_reports_gains_and_losses_only_for_needs_in_both_runs():
    old = {"tariffs": False, "cyber": True, "extra": True}
    new = {"tariffs": True, "cyber": False, "other": True}
    assert probe.changes(old, new) == (["tariffs"], ["cyber"])


def test_every_gold_chunk_exists_in_the_chunk_file():
    """If the chunks are rebuilt and ids shift, this says so before the probe
    silently measures the wrong passages."""
    chunks = ROOT / "data" / "processed" / "chunks.jsonl"
    if not chunks.exists():
        pytest.skip("chunks not built here (dd chunk)")
    ids = {json.loads(line)["chunk_id"] for line in chunks.read_text(encoding="utf-8").splitlines() if line.strip()}
    for p in json.loads(probe.PROBES.read_text(encoding="utf-8"))["probes"]:
        for need in p["needs"]:
            assert set(need["any_of"]) <= ids, need["name"]
