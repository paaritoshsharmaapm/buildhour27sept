"""Formatter tests — the Q9 gates. All pure, no network, no LLM."""

from __future__ import annotations

import pytest

from src.format_answer import (
    numbers_supported,
    normalize_number,
    performance_violation,
    sentence_truncate,
)
from src.generator import LLMResult
from src.sources import load_registry
from src.config import CONFIG
from src import format_answer


class FakeHit:
    def __init__(self, text, source_id="hdfc_large_cap_scheme", category="large_cap",
                 section="Expenses"):
        self.text = text
        self.score = 0.8
        self.metadata = {"source_id": source_id, "scheme_category": category,
                         "section": section, "retrieved_at": "2026-09-29"}


CHUNK = "Ongoing charges | 0.52% | Exit load 1.00% if redeemed within 12 months"


def _llm(**kwargs):
    base = dict(answer="The expense ratio is 0.52%.", source_id="hdfc_large_cap_scheme",
                off_topic=False, attempts=1, latency_ms=10, error=None)
    base.update(kwargs)
    return LLMResult(**base)


@pytest.fixture
def registry():
    return load_registry(CONFIG.SOURCES_CSV)


# --- sentence_truncate -------------------------------------------------

def test_truncates_to_three_sentences():
    text = "One. Two. Three. Four. Five."
    assert sentence_truncate(text) == "One. Two. Three."


def test_decimal_points_are_not_sentence_ends():
    assert sentence_truncate("The TER is 1.03%. That is all.") == "The TER is 1.03%. That is all."


def test_abbreviations_are_not_sentence_ends():
    """"0.52% p.a." is one clause, so the run below is 4 sentences and the last
    must be dropped. Splitting on the period after "p.a" would instead cut the
    first sentence to "Charges are 0.52% p" and throw away the exit load."""
    out = sentence_truncate(
        "Charges are 0.52% p.a. Exit load is 1.00%. Lock-in is nil. "
        "Riskometer is Very High. This last sentence is dropped.")
    assert "0.52% p.a." in out, out
    assert "Exit load is 1.00%." in out
    assert "Lock-in is nil." in out
    assert "dropped" not in out


def test_short_text_is_unchanged():
    assert sentence_truncate("One. Two.") == "One. Two."


# --- normalize_number --------------------------------------------------

def test_number_forms_normalise_together():
    forms = [normalize_number(f) for f in ("0.52%", "0.52", "Rs. 0.52", "₹ 0.52")]
    assert len(set(forms)) == 1, forms


def test_leading_zeros_survive():
    assert normalize_number("0.52") == "0.52"


# --- numbers_supported -------------------------------------------------

def test_supported_number_passes():
    assert numbers_supported("The expense ratio is 0.52%.", CHUNK) is True


def test_unsupported_number_fails():
    assert numbers_supported("The expense ratio is 0.53%.", CHUNK) is False


def test_substring_of_a_longer_number_is_not_a_match():
    """The bug this guards: "0.52" appears inside "10.52"."""
    assert numbers_supported("It is 0.52%.", "The charge is 10.52% today.") is False


# --- performance_violation ---------------------------------------------

def test_performance_claim_is_a_violation():
    assert performance_violation("The fund returned 18% annually.") is True


def test_plain_fee_fact_is_not_a_violation():
    assert performance_violation("The expense ratio is 0.52%.") is False


# --- finalize ----------------------------------------------------------

def test_unknown_source_id_is_no_grounding(registry):
    out = format_answer.finalize(_llm(source_id="not_a_source"), [FakeHit(CHUNK)], registry)
    assert out.status == "NO_GROUNDING"


def test_discover_only_source_is_never_citable(registry):
    out = format_answer.finalize(_llm(source_id="hdfc_large_cap_groww"),
                                 [FakeHit(CHUNK, source_id="hdfc_large_cap_groww")], registry)
    assert out.status == "NO_GROUNDING", "I1/C1: aggregator must not be citable"


def test_unsupported_number_is_no_grounding(registry):
    out = format_answer.finalize(_llm(answer="The expense ratio is 0.99%."),
                                 [FakeHit(CHUNK)], registry)
    assert out.status == "NO_GROUNDING"


def test_clean_answer_is_answered_with_one_link(registry):
    out = format_answer.finalize(_llm(), [FakeHit(CHUNK)], registry)
    assert out.status == "ANSWERED"
    assert out.text.count("](") == 1, "exactly one markdown link"
    assert "Last updated from sources:" in out.text
    assert out.citation is not None


def test_number_in_any_chunk_of_the_cited_source_is_supported(registry):
    """Regression: retrieval returns several chunks from one source_id, so
    checking only the first match refused an answer whose figure was in the
    second chunk."""
    header = FakeHit("# HDFC Large Cap Fund / Equity / DIRECT REGULAR")
    with_fee = FakeHit("Total Expense Ratio TER: 1.03")
    out = format_answer.finalize(_llm(answer="The TER is 1.03%."),
                                 [header, with_fee], registry)
    assert out.status == "ANSWERED", out.text[:120]


def test_number_absent_from_every_chunk_is_still_refused(registry):
    header = FakeHit("# HDFC Large Cap Fund / Equity / DIRECT REGULAR")
    with_fee = FakeHit("Total Expense Ratio TER: 1.03")
    out = format_answer.finalize(_llm(answer="The TER is 9.99%."),
                                 [header, with_fee], registry)
    assert out.status == "NO_GROUNDING"


def test_a_url_typed_by_the_model_is_removed(registry):
    """I1/ADR-02. The model is asked never to emit a link; this enforces it."""
    out = format_answer.finalize(
        _llm(answer="The TER is 0.52%. See https://evil.example.com for more."),
        [FakeHit(CHUNK)], registry)
    assert "evil.example.com" not in out.text
    assert out.text.count("](") == 1, "only the registry citation may remain"


def test_markdown_link_from_the_model_keeps_its_text_but_loses_the_target(registry):
    out = format_answer.finalize(
        _llm(answer="The TER is 0.52% per [this page](https://evil.example.com)."),
        [FakeHit(CHUNK)], registry)
    assert "evil.example.com" not in out.text
    assert "this page" in out.text


def test_pan_is_scrubbed_from_the_output(registry):
    out = format_answer.finalize(_llm(answer="My PAN ABCDE1234F was accepted."),
                                 [FakeHit(CHUNK)], registry)
    assert "ABCDE1234F" not in out.text


def test_performance_answer_is_refused_with_no_figure(registry):
    out = format_answer.finalize(
        _llm(answer="The fund returned 18.5% every year."), [FakeHit(CHUNK)], registry)
    assert out.status == "REFUSED_PERFORMANCE"
    assert "18.5" not in out.text


def test_off_topic_becomes_no_grounding_with_a_model_free_string(registry):
    out = format_answer.finalize(_llm(off_topic=True, answer=""), [FakeHit(CHUNK)], registry)
    assert out.status == "NO_GROUNDING"
    assert "18.5" not in out.text


def test_generator_error_becomes_no_grounding(registry):
    out = format_answer.finalize(_llm(error="invalid_json", answer="", off_topic=True),
                                 [FakeHit(CHUNK)], registry)
    assert out.status == "NO_GROUNDING"
