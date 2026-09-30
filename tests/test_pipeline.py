"""Pipeline tests. The LLM is stubbed, so these never touch the network.

The properties under test are the ones I8 exists for: the entry point is one
function, every path returns an Answer, PII returns before the network, and a
generator failure still returns the retrieved hits so a demo can show the
retrieval half working.
"""

from __future__ import annotations

import pytest

from src import pipeline
from src.generator import LLMResult
from src.pipeline import answer

CLEAN = LLMResult(answer="The TER is 1.03%.", source_id="hdfc_large_cap_scheme",
                  off_topic=False, attempts=1, latency_ms=5)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Default the generator to a clean result; individual tests override."""

    def ok(query, hits, registry=None):
        return CLEAN

    monkeypatch.setattr(pipeline, "generate", ok)


class TestPII:
    def test_pan_is_refused_before_any_network_call(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("Q1 must return before the network")

        monkeypatch.setattr(pipeline, "generate", explode)
        monkeypatch.setattr(pipeline.retriever, "retrieve", explode)
        out = answer("my pan is ABCDE1234F", debug=True)
        assert out.status == "REFUSED_PII"
        assert out.citation is None

    def test_pan_never_appears_in_the_trace(self):
        out = answer("my pan is ABCDE1234F", debug=True)
        assert "ABCDE1234F" not in str(out.trace)
        assert "ABCDE1234F" not in out.text

    def test_refusal_does_not_echo_the_input(self):
        out = answer("my pan is ABCDE1234F")
        assert "ABCDE1234F" not in out.text


class TestIntents:
    @pytest.mark.parametrize("query,expected", [
        ("Should I buy HDFC ELSS?", "REFUSED_ADVICE"),
        ("which HDFC fund is better for me?", "REFUSED_ADVICE"),
        ("What is the price of Bitcoin?", "REFUSED_OUT_OF_SCOPE"),
    ])
    def test_refusal_paths(self, query, expected):
        assert answer(query).status == expected

    def test_performance_question_never_returns_a_return_figure(self):
        out = answer("What are the returns of HDFC Large Cap?")
        assert out.status in {"REFUSED_ADVICE", "REFUSED_PERFORMANCE"}
        assert "%" not in out.text


class TestAnswered:
    def test_clean_answer_carries_one_citation_and_a_status(self):
        out = answer("What is the expense ratio of HDFC Large Cap Fund?")
        assert out.status == "ANSWERED"
        assert out.text.count("](") == 1
        assert "Last updated from sources:" in out.text
        assert out.citation is not None

    def test_latency_is_recorded_per_stage(self):
        out = answer("What is the expense ratio of HDFC Large Cap Fund?")
        assert out.latency_ms
        assert all(isinstance(v, int) for v in out.latency_ms.values())
        assert any(k.startswith("q") for k in out.latency_ms)


class TestTrace:
    def test_debug_trace_lists_hits_with_rank_and_score(self):
        out = answer("What is the exit load on HDFC Flexi Cap Fund?", debug=True)
        hits = out.trace.get("retrieval", {}).get("hits", [])
        assert hits, "debug mode must expose the retrieval evidence"
        assert hits[0]["rank"] == 1
        assert "score" in hits[0] and "source_id" in hits[0]

    def test_trace_is_empty_without_debug(self):
        assert answer("What is the TER of HDFC Large Cap?").trace == {}


class TestDegradation:
    def test_generator_error_returns_ERROR_but_keeps_the_hits(self, monkeypatch):
        monkeypatch.setattr(pipeline, "generate",
                            lambda *a, **k: LLMResult(answer="", source_id="", off_topic=True,
                                                      attempts=2, latency_ms=1, error="unreachable"))
        out = answer("What is the TER of HDFC Large Cap Fund?", debug=True)
        assert out.status == "ERROR"
        assert out.trace["retrieval"]["hits"], "retrieval evidence must survive an LLM outage"

    def test_unexpected_generator_exception_does_not_escape(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(pipeline, "generate", boom)
        out = answer("What is the TER of HDFC Large Cap Fund?")
        assert out.status == "ERROR"
        assert "Traceback" not in out.text

    def test_off_topic_becomes_no_grounding(self, monkeypatch):
        monkeypatch.setattr(pipeline, "generate", lambda *a, **k: LLMResult(
            answer="", source_id="", off_topic=True, attempts=1, latency_ms=1, error=None))
        assert answer("What is the TER of HDFC Large Cap Fund?").status == "NO_GROUNDING"

    def test_no_grounding_strings_never_contain_a_model_url(self, monkeypatch):
        monkeypatch.setattr(pipeline, "generate", lambda *a, **k: LLMResult(
            answer="Trust me: https://evil.example.com", source_id="hdfc_large_cap_scheme",
            off_topic=False, attempts=1, latency_ms=1, error=None))
        out = answer("What is the TER of HDFC Large Cap Fund?")
        assert "evil.example.com" not in out.text
