from dd.config import get_config
from dd.generation import _ask_model, _build_evidence, _clean_facts
from dd.self_healing import self_heal

q = "What was Apple's total shareholders' equity at the end of FY2025?"
cfg = get_config()
o = self_heal(q, cfg)
print("succeeded:", o.succeeded)
print("structured_facts:", o.structured_facts)
evidence, lookup = _build_evidence(o.relevant_chunks, _clean_facts(o))
print("markers given to the model:", list(lookup))
result = _ask_model(q, evidence, cfg)
print("RAW MODEL OUTPUT:", repr(result))