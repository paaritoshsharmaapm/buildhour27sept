"""Q1-Q2 - PII scanning, layered intent classification, fixed refusals.

Deliberate false-positive trade-off: `account` and `phone_in` are over-broad by
design. For a facts-only bot, a false positive costs the user one clarifying
message; a false negative leaks someone's PAN or OTP. The costs are not
symmetric, so the bias is toward blocking. The six safe fixtures in
tests/test_guardrails.py exist to stop that bias from becoming a wall -- a
pattern set that blocks "0.52%" has stopped being a guardrail and started being
an outage.

Precedence is the correctness-critical part and the ordering is not arbitrary.
ADVICE is checked BEFORE OUT_OF_SCOPE because "Should I buy HDFC ELSS?" names a
corpus scheme *and* asks for advice. If the out-of-scope check ran first it
would not fire, the question would fall through to FACTUAL, and the bot would
answer a scheme question when it was asked for a recommendation.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

from src.config import CONFIG

# Fixed length and content, so the placeholder leaks neither the value nor its
# length. Derived masks ("****1234") would leak both.
MASKED = "[redacted]"

# Ordered most-specific first: "first match wins", so a 16-digit demat number
# must not be reported as a 9-18 digit account number, and a 10-digit mobile
# must not be reported as a folio. Contextual `otp` is last because it is the
# broadest thing here.
PII_PATTERNS: list = [
    ("pan", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("aadhaar", re.compile(r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("email", re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("demat", re.compile(r"\b\d{16}\b")),
    ("phone_in", re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")),
    ("account", re.compile(r"\b\d{9,18}\b")),
    # Contextual: a bare 4-6 digit number is only PII next to a word that makes
    # it one. Without this, "18 months" and "500" would both be blocked.
    (
        "otp",
        re.compile(
            r"(?:otp|code|pin|verification)[^0-9]{0,25}\b\d{4,6}\b"
            r"|\b\d{4,6}\b[^0-9]{0,25}(?:otp|code|pin|verification)",
            re.IGNORECASE,
        ),
    ),
]

ADVICE_PATTERNS: list = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bshould i\b",
        r"\bwhich is better\b",
        r"\bis it good\b",
        r"\bworth it\b",
        r"\brecommend",
        r"\bsuggest",
        r"\bcan i sell\b",
        r"\bbest fund\b",
        r"\bportfolio\b",
        r"\ballocat",
        r"\bworth buying\b",
        r"\bis it safe\b",
        r"\bwhich one should\b",
        # Gaps found while testing against the retriever's boundary cases.
        # The list above is a floor, not a ceiling: "is HDFC ELSS a good fund"
        # and "best stocks to buy" are recommendation requests that phrase
        # themselves without any of those keywords, and both were reaching the
        # model as FACTUAL. Answering either one violates C3.
        r"\b(?:a|is it an?)?\s*good (?:fund|scheme|investment|option|choice)\b",
        r"\bbest\b[^.?!]{0,40}\b(?:buy|invest|fund|scheme|pick|add)\b",
        r"\bwhich (?:fund|scheme|mutual fund|one)\b",
        r"\bworth (?:it|buying|investing)\b",
    )
]

# FR-5.3: detected here rather than left to the prompt, so no performance
# number can reach an answer even if the model is asked nicely.
PERFORMANCE_PATTERNS: list = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\breturns\b",
        r"\bperformance\b",
        r"\bCAGR\b",
        r"\bhow much will i (?:earn|make)\b",
        r"\boutperform",
        r"\bcompare performance\b",
        r"\bexpected return",
    )
]

# AMCs, funds and indices that are not in the corpus. Anything here makes the
# question unanswerable from our sources, so refusing beats a plausible guess.
OFF_CORPUS_PATTERNS: list = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bparag\b", r"\baxis\b", r"\bmirae\b", r"\b kotak\b", r"\bsbi\b",
        r"\bicici\b", r"\bnifty\b", r"\bsensex\b", r"\bbajaj\b", r"\baxis\b",
        r"\bbandhan\b", r"\bsbi(?: large| small| flexi)?\b", r"\bcanara\b",
        r"\bunion\b", r"\bsbi\s+etf\b",
        r"\bgold\b", r"\bdebt fund\b", r"\bbond fund\b", r"\bindex fund\b",
        r"\bforeign\b", r"\bbitcoin\b",
    )
]

# "Nifty 50 TRI" is the benchmark HDFC's own pages name. Refusing a question
# about a benchmark would refuse the question the corpus is best at answering,
# so a benchmark context suspends the index rules.
BENCHMARK_CONTEXT = re.compile(
    r"\bbenchmark|\bindex\b|\btri\b|\bticker|\btracks?\b|\bnifty\b.*\btri\b", re.IGNORECASE
)

REFUSAL_PII = (
    "I don't accept personal identifiers. Please don't share PAN, Aadhaar, account "
    "numbers, OTPs, email addresses, or phone numbers. Ask me about scheme facts "
    "instead — e.g. expense ratio, exit load, lock-in, or benchmark."
)

REFUSAL_ADVICE = (
    "I'm a facts-only assistant, so I can't tell you what to buy or sell. I can "
    "share factual details like expense ratio, exit load, lock-in, or benchmark. "
    "For guidance on choosing a scheme, see "
    "[SEBI Mutual Fund Basics](https://www.investor.gov.in/)."
)

REFUSAL_OUT_OF_SCOPE = (
    "I only have documents for 5 HDFC Mutual Fund schemes — Large Cap, "
    "Flexi Cap, ELSS Tax Saver, Small Cap, and Balanced Advantage. "
    "Here's the official scheme page: {link}"
)

REFUSAL_PERFORMANCE = (
    "I don't compute or compare returns. The official monthly factsheet for "
    "this scheme has the complete, up-to-date performance table: {factsheet_url}"
)

REFUSAL_NO_GROUNDING = (
    "I don't have that in my sources. Here's the official scheme page to "
    "check: {link}"
)

# The classifier prompt is one place, reviewable, and never assembled inline.
CLASSIFIER_PROMPT = """Classify the user's message about mutual funds into exactly one category:
FACTUAL      - asks for a verifiable fact about a scheme (fee, exit load, lock-in,
               benchmark, riskometer, min SIP, how to download a document)
ADVICE       - asks what to buy/sell/hold, which is better, whether something is "good",
               or about their own portfolio or allocation
OUT_OF_SCOPE - about a fund, company, or topic outside HDFC Mutual Fund scheme facts

Message: "{query}"
Return JSON only: {{"intent": "...", "confidence": 0.0-1.0}}"""


@dataclass(frozen=True)
class PIIHit:
    label: str
    span_start: int
    span_end: int
    masked: str = MASKED


@dataclass(frozen=True)
class Intent:
    kind: str
    layer: str
    matched: list = field(default_factory=list)
    confidence: float = 1.0


def scan_pii(text: str) -> PIIHit | None:
    """First match wins, in PII_PATTERNS order."""
    for label, pattern in PII_PATTERNS:
        match = pattern.search(text)
        if match:
            return PIIHit(
                label=label,
                span_start=match.start(),
                span_end=match.end(),
                masked=MASKED,
            )
    return None


def scrub_pii(text: str) -> str:
    """Replace every match with the fixed placeholder.

    The output must not contain any substring of the original digits, so the
    replacement is uniform and length-independent.
    """
    for _, pattern in PII_PATTERNS:
        text = pattern.sub(MASKED, text)
    return text


def redact_for_trace(query: str, pii_hit: PIIHit) -> str:
    """Debug output must be safe to print, screenshot and paste into an issue."""
    if pii_hit is None:
        return query
    start, end = pii_hit.span_start, pii_hit.span_end
    return query[:start] + pii_hit.masked + query[end:]


def _first_match(patterns: list, query: str) -> list:
    return [pattern.pattern for pattern in patterns if pattern.search(query)]


def _classify_llm(query: str) -> Intent:
    """Second layer, only when the rules are inconclusive.

    Fail-safe: any missing key, missing package or transport error falls back to
    FACTUAL rather than raising. A classifier that crashes must not become a
    denial of service, and this layer is a mitigation (R8), not a gate.
    """
    try:
        import json
        import os

        from openai import OpenAI

        key = os.environ.get(CONFIG.GROQ_API_KEY_ENV)
        if not key:
            return Intent("FACTUAL", "default", [], 0.0)
        client = OpenAI(base_url=CONFIG.GROQ_BASE_URL, api_key=key)
        response = client.chat.completions.create(
            model=CONFIG.GROQ_MODEL,
            temperature=0,
            max_tokens=80,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": CLASSIFIER_PROMPT.replace("{query}", ""),
                },
                {"role": "user", "content": query},
            ],
        )
        payload = json.loads(response.choices[0].message.content)
        intent = str(payload.get("intent", "")).upper()
        if intent not in {"FACTUAL", "ADVICE", "OUT_OF_SCOPE"}:
            return Intent("FACTUAL", "default", [], 0.0)
        return Intent(intent, "llm", [intent], float(payload.get("confidence", 0.5)))
    except Exception:
        return Intent("FACTUAL", "default", [], 0.0)


def classify(query: str, *, use_llm: bool = True) -> Intent:
    """Q2. Strict precedence: PII > ADVICE > OUT_OF_SCOPE > FACTUAL."""
    hit = scan_pii(query)
    if hit is not None:
        return Intent("PII", "rule", [hit.label], 1.0)

    advice = _first_match(ADVICE_PATTERNS, query)
    if advice:
        return Intent("ADVICE", "rule", advice, 1.0)

    performance = _first_match(PERFORMANCE_PATTERNS, query)
    if performance:
        return Intent("ADVICE", "rule", ["performance"] + performance, 1.0)

    if not BENCHMARK_CONTEXT.search(query):
        off_corpus = _first_match(OFF_CORPUS_PATTERNS, query)
        if off_corpus:
            return Intent("OUT_OF_SCOPE", "rule", off_corpus, 1.0)

    if use_llm:
        return _classify_llm(query)

    # Reached only when the rules ran and matched nothing, so the decision was
    # made by the rule layer. "default" is reserved for the LLM fallback, where
    # no layer actually decided.
    return Intent("FACTUAL", "rule", [], 1.0)


def render(template: str, **values) -> str:
    """Fill a refusal template.

    Unknown *and* missing keys both raise. str.format silently ignores extra
    kwargs, which would let a caller pass a URL the template never uses and
    believe it had been applied.
    """
    names = {
        field for _, field, _, _ in string.Formatter().parse(template) if field
    }
    unknown = set(values) - names
    if unknown:
        raise KeyError(f"template does not use {sorted(unknown)}")
    missing = names - set(values)
    if missing:
        raise KeyError(f"template needs {sorted(missing)}")
    return template.format(**values)
