"""The final-answer call (_ask_model) must survive Groq rejecting it with a
400 "json_validate_failed" (the model wrote JSON that does not match the schema).

A retry at the same temperature tends to repeat the mistake, so the second
try changes it. After two failures the error is raised, and the caller turns
it into the plain "Model call failed ... Try again in a minute" message.
Any other error is a real problem and is raised at once, with no retry.
"""

import json
from types import SimpleNamespace

import pytest

import dd.generation as gen
from dd.config import get_config

BROKEN = Exception(
    "Error code: 400 - {'error': {'message': 'Failed to validate JSON.', "
    "'type': 'invalid_request_error', 'code': 'json_validate_failed'}}"
)
GOOD = {"answerable": True, "answer": "$112,010 million [S1]", "citations": ["[S1]"]}


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


def _reply():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(GOOD)))])


def _ask(monkeypatch, fake):
    monkeypatch.setattr(gen, "get_groq_client", lambda cfg: fake)
    return gen._ask_model("What was net income?", "[S1] Net income FY2025: 112,010", get_config())


def test_broken_json_is_retried_once_at_a_higher_temperature(monkeypatch):
    fake = FakeGroq([BROKEN, _reply()])
    assert _ask(monkeypatch, fake) == GOOD
    assert fake.temperatures == [get_config().generation.temperature, 0.5]


def test_broken_json_twice_raises_after_exactly_two_calls(monkeypatch):
    fake = FakeGroq([BROKEN, BROKEN])
    with pytest.raises(Exception, match="json_validate_failed"):
        _ask(monkeypatch, fake)
    assert len(fake.temperatures) == 2


def test_any_other_error_is_raised_at_once_without_a_retry(monkeypatch):
    fake = FakeGroq([Exception("Error code: 401 - invalid api key")])
    with pytest.raises(Exception, match="invalid api key"):
        _ask(monkeypatch, fake)
    assert len(fake.temperatures) == 1