"""Generator tests. No test in this file touches the network: get_client is
monkeypatched with a fake whose completions object records its calls, which is
also how the retry count is asserted (exactly one retry, per NFR-2)."""

from __future__ import annotations

import json

import pytest

from src import generator
from src.generator import generate


class FakeCompletions:
    def __init__(self, payload=None, raises=None, raw=None):
        self.payload = payload
        self.raises = raises
        self.raw = raw
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        message = type("M", (), {"content": self.raw if self.raw is not None
                                 else json.dumps(self.payload)})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


class FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


@pytest.fixture
def hits():
    return []


def _patch(monkeypatch, completions):
    monkeypatch.setattr(generator, "get_client", lambda: FakeClient(completions))
    return completions


def test_valid_json_returns_a_parsed_result(monkeypatch, hits):
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "The TER is 1.03%.", "source_id": "hdfc_large_cap_scheme",
                 "off_topic": False}))
    result = generate("What is the TER of HDFC Large Cap Fund?", hits)
    assert result.off_topic is False
    assert result.answer == "The TER is 1.03%."
    assert result.source_id == "hdfc_large_cap_scheme"
    assert result.error is None


def test_invalid_json_yields_an_empty_answer(monkeypatch, hits):
    """The model said something unparseable. We do not echo it."""
    _patch(monkeypatch, FakeCompletions(raw="Sure! The expense ratio is 0.52% p.a."))
    result = generate("expense ratio", hits)
    assert result.error == "invalid_json"
    assert result.answer == ""
    assert result.off_topic is True


def test_missing_source_id_is_a_schema_error(monkeypatch, hits):
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "The TER is 1.03%.", "off_topic": False}))
    result = generate("ter", hits)
    assert result.error == "schema"
    assert result.answer == ""


def test_timeout_retries_exactly_once_then_reports_unreachable(monkeypatch, hits):
    calls = _patch(monkeypatch, FakeCompletions(raises=generator.APITimeoutError(request=None)))
    monkeypatch.setattr(generator.time, "sleep", lambda _seconds: None)
    result = generate("ter", hits)
    assert result.error == "unreachable"
    assert result.attempts == 2
    assert calls.calls == 2, "exactly one retry, never a retry loop"


def test_rate_limit_retries_once(monkeypatch, hits):
    response = type("R", (), {"status_code": 429, "headers": {}, "request": None})()
    calls = _patch(monkeypatch, FakeCompletions(raises=generator.RateLimitError(
        "rate limited", response=response, body=None)))
    monkeypatch.setattr(generator.time, "sleep", lambda _seconds: None)
    result = generate("ter", hits)
    assert result.error == "rate_limited"
    assert calls.calls == 2


def test_auth_error_is_not_retried(monkeypatch, hits):
    response = type("R", (), {"status_code": 401, "headers": {}, "request": None})()
    calls = _patch(monkeypatch, FakeCompletions(raises=generator.APIStatusError(
        "unauthorized", response=response, body=None)))
    result = generate("ter", hits)
    assert result.error == "api_error"
    assert calls.calls == 1, "a bad key will not fix itself"


def test_missing_key_raises_a_clear_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    generator.get_client.cache_clear()
    with pytest.raises(generator.LLMNotConfigured, match="GROQ_API_KEY"):
        generator.get_client()
    generator.get_client.cache_clear()


def test_off_topic_true_is_preserved(monkeypatch, hits):
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "", "source_id": "x", "off_topic": True}))
    result = generate("bitcoin price", hits)
    assert result.off_topic is True
    # This assertion used to demand error == "schema", encoding the bug fixed
    # in test_off_topic_with_empty_answer_is_a_success_not_a_schema_error:
    # declining to answer is a valid generation, not a malformed one.
    assert result.error is None


def test_off_topic_with_empty_answer_is_a_success_not_a_schema_error(monkeypatch):
    """Regression (found by eval/golden_qa.json q25).

    The model correctly declined a question that is absent from the corpus by
    returning off_topic=true with an empty answer. That was scored as a
    schema error, so the pipeline returned ERROR / "I couldn't reach the
    language model" and format_answer's NO_GROUNDING branch was unreachable:
    an honest refusal was reported to the user as a network failure.
    """
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "", "source_id": "", "off_topic": True}))
    result = generate("How many days to settle redemptions?", [])
    assert result.error is None
    assert result.off_topic is True
    assert result.answer == ""


def test_off_topic_discards_any_answer_text(monkeypatch):
    """A model that flags off_topic while still writing prose is not trusted."""
    _patch(monkeypatch, FakeCompletions(payload={
        "answer": "Probably two days.", "source_id": "hdfc_large_cap_scheme",
        "off_topic": True}))
    result = generate("How many days to settle redemptions?", [])
    assert result.error is None
    assert result.answer == ""
    assert "Probably" not in result.answer


def test_off_topic_false_with_empty_answer_is_still_a_schema_error(monkeypatch):
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "", "source_id": "hdfc_large_cap_scheme",
                 "off_topic": False}))
    assert generate("q", []).error == "schema"


def test_missing_off_topic_field_is_a_schema_error(monkeypatch):
    _patch(monkeypatch, FakeCompletions(
        payload={"answer": "TER is 1.03%.",
                 "source_id": "hdfc_large_cap_scheme"}))
    assert generate("q", []).error == "schema"
