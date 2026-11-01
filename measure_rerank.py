from dd.retrieval import hybrid_retrieve

ANSWERABLE = [
    "What cybersecurity-related risks does Apple describe in its filing?",
    "What risk does Apple disclose regarding natural disasters and its facilities?",
    "Why did Services net sales increase in FY2025?",
    "What risk does Apple disclose about acquisitions and new business investments?",
]
NOT_ANSWERABLE = [
    "What is Apple's market share by country for smartphones?",
    "What is Apple's estimate of its total addressable market size for the next 5 years?",
    "Apple stance on deep sea mining regulations",
]

for label, questions in [("ANSWERABLE", ANSWERABLE), ("NOT ANSWERABLE", NOT_ANSWERABLE)]:
    print(f"\n=== {label} ===")
    for q in questions:
        results = hybrid_retrieve(q)
        scores = [round(r.rerank_score, 4) for r in results]
        print(f"top={scores[0]:<8} all={scores}  <- {q[:60]}")