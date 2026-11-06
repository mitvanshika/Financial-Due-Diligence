"""grade_chunks must survive Groq rejecting the call with a 400
"json_validate_failed" (the model wrote broken JSON).

At temperature 0 the model repeats the same mistake, so the retry has to
change something: one more try at a higher temperature. If that fails too,
the item is failed honestly (the question is refused) instead of crashing.
Any other error is a real problem and must still raise.
"""

import json
from types import SimpleNamespace

import pytest

import dd.self_healing as sh
from dd.config import get_config

BROKEN = Exception(
    "Error code: 400 - {'error': {'message': 'Failed to generate JSON.', "
    "'type': 'invalid_request_error', 'code': 'json_validate_failed'}}"
)


class FakeGroq:
    """Plays back one outcome per call: an Exception is raised, anything
    else is returned as the model's reply. Remembers each call's temperature."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.temperatures = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.temperatures.append(kwargs["temperature"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _reply(score):
    body = {"grades": [{"item": "[S1]", "relevance": score, "reason": "ok"}]}
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])


def _grade(monkeypatch, fake):
    """One database fact and no passages: the smallest real call."""
    monkeypatch.setattr(sh, "get_groq_client", lambda cfg: fake)
    monkeypatch.setattr(sh, "describe_fact", lambda f: "Net income FY2025: 112,010")
    return sh.grade_chunks("What was net income?", [], get_config(), [{"label": "net income"}])


def test_broken_json_is_retried_once_at_a_higher_temperature(monkeypatch):
    fake = FakeGroq([BROKEN, _reply(1.0)])
    _, fact_scores = _grade(monkeypatch, fake)
    assert fact_scores == [1.0]
    assert fake.temperatures == [0.0, 0.5]


def test_broken_json_twice_fails_the_item_instead_of_crashing(monkeypatch):
    fake = FakeGroq([BROKEN, BROKEN])
    _, fact_scores = _grade(monkeypatch, fake)
    assert fact_scores == [0.0]
    assert len(fake.temperatures) == 2


def test_any_other_error_still_raises_and_is_not_retried(monkeypatch):
    fake = FakeGroq([Exception("Error code: 401 - invalid api key")])
    with pytest.raises(Exception, match="invalid api key"):
        _grade(monkeypatch, fake)
    assert len(fake.temperatures) == 1