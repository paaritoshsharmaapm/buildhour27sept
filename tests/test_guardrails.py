"""P9 - the safety core. No network: the LLM layer is never exercised here,
because its absence must be a safe default, not a crash."""

from __future__ import annotations

import pytest

from src.guardrails import (
    ADVICE_PATTERNS,
    MASKED,
    OFF_CORPUS_PATTERNS,
    PERFORMANCE_PATTERNS,
    PII_PATTERNS,
    PIIHit,
    REFUSAL_ADVICE,
    REFUSAL_NO_GROUNDING,
    REFUSAL_OUT_OF_SCOPE,
    REFUSAL_PERFORMANCE,
    REFUSAL_PII,
    Intent,
    classify,
    redact_for_trace,
    render,
    scan_pii,
    scrub_pii,
)

# One fixture per label. If a new pattern is added without a fixture, the label
# count and this list drift apart and the suite should say so.
PII_FIXTURES = [
    ("pan", "my PAN is ABCDE1234F"),
    ("aadhaar", "my aadhaar number is 2345 6789 0123"),
    ("ifsc", "transfer to HDFC0001234 please"),
    ("email", "write to me at arjun.sharma@hdfc.com"),
    ("demat", "my demat is 1234567890123456"),
    ("phone_in", "call me on 9876543210"),
    ("account", "my folio number is 123456789012"),
    ("otp", "the otp is 448213"),
]

# Real questions that must survive. An over-eager account pattern blocks every
# fee question and the product stops working.
SAFE_FIXTURES = [
    "expense ratio of HDFC Large Cap",
    "18 months",
    "0.52%",
    "Nifty 50 TRI",
    "what is the exit load of HDFC Flexi Cap Fund",
    "lock in period for HDFC ELSS Tax Saver Fund",
    "minimum SIP amount",
    "benchmark of HDFC Balanced Advantage Fund",
]


class TestPIIPatterns:
    def test_exactly_the_eight_required_labels(self):
        assert [label for label, _ in PII_PATTERNS] == [
            "pan", "aadhaar", "ifsc", "email", "demat", "phone_in", "account", "otp",
        ]

    @pytest.mark.parametrize("label,query", PII_FIXTURES)
    def test_each_label_is_caught(self, label, query):
        hit = scan_pii(query)
        assert hit is not None, f"{label} fixture was not caught: {query!r}"
        assert hit.label == label

    @pytest.mark.parametrize("query", SAFE_FIXTURES)
    def test_safe_questions_are_not_caught(self, query):
        assert scan_pii(query) is None, f"false positive on a real question: {query!r}"

    def test_account_pattern_does_not_fire_on_short_numbers(self):
        """The forbidden version is r"\b\d+\b", which blocks every fee."""
        for number in ["0.52", "18", "100", "1.03", "500"]:
            assert scan_pii(f"the rate is {number}") is None

    def test_nine_digits_is_account_because_the_pattern_is_deliberately_broad(self):
        """architecture.md 9.1 fixes account at 9-18 digits, so a 9-digit order
        number is blocked. Documented over-blocking, not a bug -- the cost is one
        clarifying message against leaking a folio number."""
        assert scan_pii("order 123456789").label == "account"

    def test_eight_digits_is_not_pii(self):
        assert scan_pii("order 12345678") is None

    def test_otp_needs_context(self):
        assert scan_pii("my otp is 448213") is not None
        assert scan_pii("verification code 9921") is not None
        assert scan_pii("the amount is 448213 rupees") is None

    def test_pan_inside_a_factual_question_still_blocks(self):
        hit = scan_pii("what is the expense ratio for ABCDE1234F")
        assert hit is not None and hit.label == "pan"

    def test_lowercase_pan_does_not_match(self):
        """PAN is defined uppercase; being strict here avoids false positives."""
        assert scan_pii("abcde1234f") is None


class TestPIIHit:
    def test_span_identifies_the_match(self):
        hit = scan_pii("my PAN is ABCDE1234F")
        assert "ABCDE1234F" == "my PAN is ABCDE1234F"[hit.span_start:hit.span_end]

    def test_masked_is_a_fixed_length_placeholder(self):
        assert scan_pii("my PAN is ABCDE1234F").masked == MASKED

    def test_masked_does_not_reveal_the_length(self):
        short = scan_pii("pin 1234").masked
        long = scan_pii("demat 1234567890123456").masked
        assert short == long

    def test_hit_is_frozen(self):
        hit = scan_pii("my PAN is ABCDE1234F")
        with pytest.raises(Exception):
            hit.label = "other"


class TestScrub:
    def test_scrub_removes_a_pan(self):
        assert "ABCDE1234F" not in scrub_pii("my PAN is ABCDE1234F")

    def test_scrub_output_has_no_substring_of_the_original(self):
        query = "my PAN is ABCDE1234F"
        scrubbed = scrub_pii(query)
        assert "ABCDE" not in scrubbed
        assert "1234" not in scrubbed

    def test_scrub_replaces_every_match(self):
        scrubbed = scrub_pii("PAN ABCDE1234F and IFSC HDFC0001234")
        assert "ABCDE1234F" not in scrubbed
        assert "HDFC0001234" not in scrubbed

    def test_scrub_leaves_a_clean_query_alone(self):
        query = "expense ratio of HDFC Large Cap"
        assert scrub_pii(query) == query

    def test_scrubbed_text_is_rescannable_clean(self):
        assert scan_pii(scrub_pii("my PAN is ABCDE1234F")) is None


class TestRedactForTrace:
    def test_trace_output_hides_the_value(self):
        query = "my PAN is ABCDE1234F"
        trace = redact_for_trace(query, scan_pii(query))
        assert "ABCDE1234F" not in trace
        assert MASKED in trace

    def test_clean_query_passes_through(self):
        assert redact_for_trace("expense ratio?", None) == "expense ratio?"

    def test_keeps_surrounding_text_useful_for_debugging(self):
        query = "my PAN is ABCDE1234F"
        trace = redact_for_trace(query, scan_pii(query))
        assert trace.startswith("my PAN is ")


class TestPrecedence:
    def test_pii_beats_everything(self):
        assert classify("Should I buy HDFC ELSS with PAN ABCDE1234F?").kind == "PII"

    def test_advice_beats_out_of_scope(self):
        """"Should I buy HDFC ELSS?" names a corpus scheme AND asks for advice.
        An out-of-scope first check would not fire, the question would fall
        through to FACTUAL, and the bot would answer a scheme question."""
        intent = classify("Should I buy HDFC ELSS?", use_llm=False)
        assert intent.kind == "ADVICE"
        assert intent.kind != "OUT_OF_SCOPE"

    def test_advice_beats_off_corpus(self):
        intent = classify("Should I buy Parag Flexi Cap?", use_llm=False)
        assert intent.kind == "ADVICE"

    def test_performance_is_an_advice_subclass(self):
        intent = classify("What are the returns of HDFC Large Cap?", use_llm=False)
        assert intent.kind == "ADVICE"
        assert "performance" in intent.matched

    def test_factual_is_the_default(self):
        assert classify("What is the exit load?", use_llm=False).kind == "FACTUAL"

    def test_layer_is_recorded(self):
        assert classify("expense ratio?", use_llm=False).layer == "rule"

    def test_a_rule_layer_factual_is_not_the_llm_fallback(self):
        """No rule matched, so the rule layer still decided."""
        assert classify("what is a mutual fund?", use_llm=False).layer == "rule"

    def test_fallback_layer_is_default(self, offline_llm):
        """With the LLM unreachable, no layer decided, so it reports "default"."""
        assert classify("what is a mutual fund?", use_llm=True).layer == "default"

    def test_every_intent_is_a_frozen_dataclass(self):
        intent = classify("expense ratio?", use_llm=False)
        assert isinstance(intent, Intent)
        with pytest.raises(Exception):
            intent.kind = "ADVICE"


class TestOffCorpus:
    def test_another_amc_is_out_of_scope(self):
        assert classify("Is Parag Parag Flexi Cap good?", use_llm=False).kind == "OUT_OF_SCOPE"

    def test_benchmark_context_suspends_the_index_rules(self):
        """HDFC's own pages name Nifty 50 TRI as the benchmark. Refusing a
        question about a benchmark would refuse what the corpus knows best."""
        assert classify("Is HDFC Large Cap benchmarked to Nifty 50 TRI?", use_llm=False).kind == "FACTUAL"

    def test_a_bare_index_question_is_still_out_of_scope(self):
        assert classify("What is the Nifty 50 today?", use_llm=False).kind == "OUT_OF_SCOPE"


class TestAcceptedProbes:
    """The six probes in the phase acceptance criteria, asserted exactly."""

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("expense ratio of HDFC Large Cap", "FACTUAL"),
            ("Should I buy HDFC ELSS?", "ADVICE"),
            ("What are the returns of HDFC Small Cap?", "ADVICE"),
            ("my PAN is ABCDE1234F", "PII"),
            ("Is Parag Parag Flexi Cap good?", "OUT_OF_SCOPE"),
            ("What is the exit load?", "FACTUAL"),
        ],
    )
    def test_probe(self, query, expected):
        intent = classify(query, use_llm=False)
        assert (intent.kind, intent.layer) == (expected, "rule")


@pytest.fixture
def offline_llm(monkeypatch):
    """Force the LLM layer to fail, without touching the network.

    These tests used to rely on GROQ_API_KEY being absent, so once a real key
    existed they silently made live API calls and asserted an outcome decided
    by a remote model. CONFIG is frozen, so the seam is the client factory:
    _classify_llm does `from openai import OpenAI` at call time, so patching the
    module attribute reaches it. The stub raises from `create` so the real
    try/except around the call is what gets tested.
    """
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")

    class _Unreachable:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise ConnectionError("simulated: no route to the endpoint")

    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: _Unreachable)


class TestLLMLayer:
    def test_missing_api_key_falls_back_to_factual(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        assert classify("some ambiguous question", use_llm=True).kind == "FACTUAL"

    def test_unreachable_llm_does_not_raise(self, offline_llm):
        """The layer is a mitigation (R8), not a gate. An unreachable endpoint
        must not become a denial of service."""
        assert classify("ambiguous question", use_llm=True).kind == "FACTUAL"

    def test_rule_layer_short_circuits_the_llm(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "src.guardrails._classify_llm", lambda q: called.append(q) or Intent("FACTUAL", "llm")
        )
        classify("Should I buy HDFC ELSS?", use_llm=True)
        assert called == []


class TestRefusals:
    @pytest.mark.parametrize(
        "constant",
        [REFUSAL_PII, REFUSAL_ADVICE, REFUSAL_OUT_OF_SCOPE, REFUSAL_PERFORMANCE, REFUSAL_NO_GROUNDING],
    )
    def test_is_a_non_empty_string(self, constant):
        assert isinstance(constant, str) and constant.strip()

    @pytest.mark.parametrize(
        "constant",
        [REFUSAL_PII, REFUSAL_ADVICE, REFUSAL_OUT_OF_SCOPE, REFUSAL_PERFORMANCE, REFUSAL_NO_GROUNDING],
    )
    def test_contains_no_figure_that_could_be_read_as_a_fact(self, constant):
        """A refusal that quotes a number invents a fact. None of ours should."""
        import re
        assert not re.findall(r"\d+\.\d+", constant), constant

    def test_advice_refusal_carries_the_edu_link(self):
        assert "[SEBI Mutual Fund Basics](https://www.investor.gov.in/)" in REFUSAL_ADVICE

    def test_advice_refusal_does_not_mention_a_scheme_link(self):
        assert "hdfcfund.com" not in REFUSAL_ADVICE

    def test_out_of_scope_names_the_five_schemes(self):
        for scheme in ["Large Cap", "Flexi Cap", "ELSS Tax Saver", "Small Cap", "Balanced Advantage"]:
            assert scheme in REFUSAL_OUT_OF_SCOPE

    def test_pii_refusal_lists_what_not_to_share(self):
        for item in ["PAN", "Aadhaar", "OTP"]:
            assert item in REFUSAL_PII

    def test_templates_are_not_generated(self):
        """They are module constants, so a diff shows any wording change."""
        import src.guardrails as module
        for name in ["REFUSAL_PII", "REFUSAL_ADVICE", "REFUSAL_OUT_OF_SCOPE",
                     "REFUSAL_PERFORMANCE", "REFUSAL_NO_GROUNDING"]:
            assert isinstance(getattr(module, name), str)


class TestRender:
    def test_fills_a_placeholder(self):
        out = render(REFUSAL_NO_GROUNDING, link="https://www.hdfcfund.com/x")
        assert "https://www.hdfcfund.com/x" in out
        assert "{link}" not in out

    def test_fills_the_factsheet_placeholder(self):
        out = render(REFUSAL_PERFORMANCE, factsheet_url="https://factsheet")
        assert "https://factsheet" in out

    def test_raises_on_a_missing_key(self):
        with pytest.raises(KeyError, match="link"):
            render(REFUSAL_NO_GROUNDING)

    def test_raises_on_an_unknown_key(self):
        """str.format ignores extra kwargs silently, so a caller could pass a
        URL the template never uses and believe it had been applied."""
        with pytest.raises(KeyError):
            render(REFUSAL_NO_GROUNDING, link="u", bogus="v")

    def test_renders_a_template_with_no_placeholders(self):
        assert render(REFUSAL_ADVICE) == REFUSAL_ADVICE

    def test_result_never_leaves_a_placeholder(self):
        for template, key in [
            (REFUSAL_OUT_OF_SCOPE, "link"),
            (REFUSAL_NO_GROUNDING, "link"),
            (REFUSAL_PERFORMANCE, "factsheet_url"),
        ]:
            assert "{" not in render(template, **{key: "URL"})


class TestPatternSanity:
    def test_advice_patterns_are_compiled(self):
        assert ADVICE_PATTERNS and all(hasattr(p, "search") for p in ADVICE_PATTERNS)

    def test_performance_patterns_are_compiled(self):
        assert PERFORMANCE_PATTERNS and all(hasattr(p, "search") for p in PERFORMANCE_PATTERNS)

    def test_off_corpus_patterns_are_compiled(self):
        assert OFF_CORPUS_PATTERNS and all(hasattr(p, "search") for p in OFF_CORPUS_PATTERNS)

    def test_pii_hit_can_be_built_by_hand(self):
        hit = PIIHit(label="pan", span_start=0, span_end=4, masked=MASKED)
        assert redact_for_trace("ABCD rest", hit) == "[redacted] rest"


class TestAdviceGapsFoundAgainstTheRetriever:
    """Recommendation requests that phrase themselves without any keyword in
    the base list. Each of these reached the model as FACTUAL and was answered
    before these patterns existed, which is a C3 violation, not a nicety."""

    @pytest.mark.parametrize(
        "query",
        [
            "is HDFC ELSS a good fund",
            "is HDFC Large Cap a good scheme",
            "which fund should I invest in",
            "best stocks to buy tomorrow",
            "which mutual fund is best for me",
            "is HDFC Flexi Cap worth buying",
        ],
    )
    def test_recommendation_request_is_refused(self, query):
        assert classify(query, use_llm=False).kind == "ADVICE"

    @pytest.mark.parametrize(
        "query",
        [
            "expense ratio of HDFC Large Cap",
            "what is the exit load",
            "lock in period for HDFC ELSS Tax Saver Fund",
            "benchmark of HDFC Balanced Advantage Fund",
            "Nifty 50 TRI",
            "riskometer of HDFC Large Cap Fund",
        ],
    )
    def test_the_widening_did_not_swallow_real_questions(self, query):
        assert classify(query, use_llm=False).kind == "FACTUAL"
