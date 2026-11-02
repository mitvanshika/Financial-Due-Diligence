"""One-off check: how does the reranker score the tariff chunks, with and
without the "[SEC 10-K filing, ...]" prefix that retrieval.py adds?

    python check_rerank.py
"""

import json

from sentence_transformers import CrossEncoder

QUESTION = "What does the Company say about tariffs and international trade restrictions?"
# Start of each chunk's text: the ones that made the final 6, and the ones that didn't.
STARTS = {
    "made top 6 (#1)": "The Company has a large, global business",
    "made top 6 (#6)": "The Company is exposed to credit risk",
    "MISSED: Item 7 tariffs": "Tariffs and other measures that are applied",
    "MISSED: Q2 2025 tariffs": "Beginning in the second quarter of 2025",
    "MISSED: 1A tariff countries": "Global supply chains can be highly concentrated",
}

chunks = [json.loads(line) for line in open("data/processed/chunks.jsonl", encoding="utf-8")]
picked = {}
for label, start in STARTS.items():
    match = next((c for c in chunks if c["text"].startswith(start)), None)
    if match is None:
        print(f"not found: {start}")
    else:
        picked[label] = match

model = CrossEncoder("BAAI/bge-reranker-large")
plain = model.predict([(QUESTION, c["text"]) for c in picked.values()])
prefixed = model.predict([
    (QUESTION, f"[SEC 10-K filing, {c['item']} - {c['section']}] {c['text']}")
    for c in picked.values()
])

print(f"\n{'chunk':<30} {'id':<24} {'chars':>6} {'no prefix':>10} {'prefix':>10}")
for (label, c), p, q in zip(picked.items(), plain, prefixed):
    print(f"{label:<30} {c['chunk_id']:<24} {len(c['text']):>6} {p:>10.4f} {q:>10.4f}")