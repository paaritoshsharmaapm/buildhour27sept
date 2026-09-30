"""Q9 — where determinism replaces trust in the model (architecture.md §8 Q9).

This module holds no LLM and no network logic. Every rule here is a hard gate
that returns before the text can reach a user, which is the point: the prompt
*asks* for three sentences, a real source_id and no invented numbers, and a
prompt is a request rather than a control. The numeric guard is the single most
important line in the project — a hallucinated fee ratio becomes a refusal
instead of a wrong answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src import guardrails
from src.config import CONFIG

# Used only when no scheme could be resolved, so the refusal still points
# somewhere official rather than at nothing.
HDFC_INDEX_URL = "https://www.hdfcfund.com/mutual-funds"

# Abbreviations whose trailing period is not a sentence end. Splitting on a bare
# "." would truncate "1.00% p.a." to "1." and "0.52% p.a." to "0.52".
_ABBREVIATIONS = ("p.a", "rs", "no", "dr", "mr", "mrs", "ms", "vs", "etc", "approx", "min", "max")

# "Rs. 500" and "₹ 500" carry a space after the symbol in the corpus.
_NUMERIC_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)*%?")


@dataclass(frozen=True)
class Citation:
    title: str
    url: str
    retrieved_at: str = ""


@dataclass(frozen=True)
class Answer:
    query: str
    status: str
    text: str
    citation: Citation | None = None
    sources_consulted: int = 0
    latency_ms: dict = field(default_factory=dict)
    trace: dict = field(default_factory=dict)


def _split_sentences(text: str) -> list:
    """Split on . ! ? that end a sentence, sparing decimals and abbreviations."""
    guarded = text
    for abbreviation in _ABBREVIATIONS:
        guarded = re.sub(rf"(?i)\b{re.escape(abbreviation)}\.", f"{abbreviation}\x00", guarded)
    # "1.03%" and "0.52" — a period between digits is a decimal point.
    guarded = re.sub(r"(?<=\d)\.(?=\d)", "\x00", guarded)

    parts = re.split(r"(?<=[.!?])\s+", guarded)
    return [part.replace("\x00", ".").strip() for part in parts if part.strip()]


# A markdown link or a bare URL typed by the model. The model is told never to
# emit a link and in practice usually obeys, but "usually" is not a control:
# a fabricated URL reached the UI body once during testing, appended after a
# perfectly correct registry citation, where it looked like a second source.
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://)[^)]+\)")
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s)\]]+")


def strip_model_urls(text: str) -> str:
    """Remove links the model typed. The citation below is the only link we ship.

    Markdown links keep their label (it is often the fact) while losing the
    target; a bare URL is dropped entirely.
    """
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def sentence_truncate(text: str, max_sentences: int = CONFIG.MAX_SENTENCES) -> str:
    """Keep at most `max_sentences` sentences (C4). Inline markdown is left
    untouched; only whole trailing sentences are dropped."""
    if not text:
        return ""
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return text.strip()
    kept = sentences[:max_sentences]
    # A truncated final sentence should not end mid-thought with no punctuation
    # if the original did; rejoin and add a full stop only when one is missing.
    out = " ".join(kept).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out


def normalize_number(token: str) -> str:
    """Reduce a numeric token to a bare comparable form.

    Deliberately dull. This is a security check and clever regexes here are a
    liability: strip thousands separators, currency marks, a trailing percent and
    a trailing ".*". Leading zeros are preserved because "0.52" and ".52" are
    the same value but "0.50" and "0.5" should still compare equal.
    """
    value = token.strip().lower()
    value = value.replace(",", "")
    value = value.replace("₹", "").replace("rs.", "").replace("rs", "")
    value = value.replace("%", "")
    value = value.replace("*", "")
    # Strip again: "₹ 0.52" becomes " 0.52", and the leading space made it a
    # different key from "0.52".
    return value.strip().rstrip(".")


def numbers_supported(answer: str, chunk_text: str) -> bool:
    """Does every number in the answer appear in the cited chunk?

    A raw substring test is wrong and dangerous: "0.52" occurs inside "10.52", so
    a hallucinated figure matching the tail of a real one would slip through.
    The boundaries below reject a match preceded by a digit or a decimal point.
    """
    haystack = normalize_number(chunk_text)
    for match in _NUMERIC_TOKEN.finditer(answer):
        needle = normalize_number(match.group(0))
        if not needle:
            continue
        if not re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d])", haystack):
            return False
    return True


def performance_violation(text: str) -> bool:
    """C3 backstop: any returns/performance claim becomes a fixed refusal.

    Reuses guardrails' patterns rather than redefining them, and also catches a
    bare percentage paired with return vocabulary, which the index rules miss.
    """
    lowered = text.lower()
    if any(pattern.search(text) for pattern in guardrails.PERFORMANCE_PATTERNS):
        return True
    if "%" in text and re.search(r"(?i)\b(return|returns|cagr|annualis|annualiz|yield|growth)", lowered):
        return True
    return False


def _scheme_link(registry, hits: list) -> str:
    """The official page for the scheme of the top hit, else the HDFC index."""
    if hits:
        metadata = getattr(hits[0], "metadata", {}) or {}
        category = metadata.get("scheme_category")
        if category and registry is not None:
            for row in registry.citable_for(category):
                if row.doc_type == "scheme_page" or "scheme" in row.url:
                    return row.url
            rows = registry.citable_for(category)
            if rows:
                return rows[0].url
    return HDFC_INDEX_URL


def _no_grounding(query: str, link: str) -> Answer:
    """Every NO_GROUNDING string is model-free by construction: it is rendered
    from a constant, never from model output."""
    return Answer(
        query=query,
        status="NO_GROUNDING",
        text=guardrails.render(guardrails.REFUSAL_NO_GROUNDING, link=link),
    )


def finalize(llm, hits: list, registry, *, grounded: bool = True) -> Answer:
    """The Q9 sequence, in order, returning on the first failure.

    The order is load-bearing: (d) resolves the row before (e) reads the cited
    chunk, because an unknown or discover_only source has no trustworthy text to
    check numbers against.
    """
    query = getattr(llm, "query", "") or ""

    # (a) the model produced nothing usable, or retrieval found nothing to check
    if llm.error or llm.off_topic or not llm.answer:
        return _no_grounding(query, _scheme_link(registry, hits))
    if not hits or not grounded:
        return _no_grounding(query, _scheme_link(registry, hits))

    # (b) length limit, C4
    text = sentence_truncate(llm.answer)

    # (c) performance backstop, C3. No citation: the model gets no credit.
    if performance_violation(text):
        factsheet = _scheme_link(registry, hits)
        return Answer(
            query=query,
            status="REFUSED_PERFORMANCE",
            text=guardrails.render(guardrails.REFUSAL_PERFORMANCE, factsheet_url=factsheet),
        )

    # (d) citation resolution, C5. The model supplies an id; we own the URL.
    row = registry.resolve(llm.source_id) if registry is not None else None
    if row is None or not row.enabled:
        return _no_grounding(query, _scheme_link(registry, hits))
    if row.discover_only:
        # I1/C1: an aggregator is retrievable but never citable.
        return _no_grounding(query, _scheme_link(registry, hits))

    # (e) the numeric guard, A1. Every figure must exist in the cited source.
    #
    # The model returns a source_id, not a chunk id, so every retrieved chunk
    # from that source is fair evidence. Checking only the first match refuses a
    # correct answer: retrieval routinely returns several chunks from one source
    # and the fact often sits in the second, which made a verified "1.03%"
    # answer turn into NO_GROUNDING while the figure was plainly in context.
    cited = [h for h in hits
             if (getattr(h, "metadata", {}) or {}).get("source_id") == llm.source_id]
    if not cited:
        return _no_grounding(query, row.url)
    if not numbers_supported(text, "\n".join(getattr(h, "text", "") for h in cited)):
        return _no_grounding(query, row.url)

    # (f) scrub before appending the citation, or a PAN in the URL survives
    text = strip_model_urls(text)
    text = guardrails.scrub_pii(text)

    # (g) the citation is appended by us, from the registry
    retrieved_at = row.retrieved_at or (cited[0].metadata or {}).get("retrieved_at", "")
    text += (f"\n\nSource: [{row.scheme}]({row.url})\n"
             f"Last updated from sources: {retrieved_at or 'unknown'}")

    # (h)(i)
    return Answer(
        query=query,
        status="ANSWERED",
        text=text,
        citation=Citation(title=row.scheme, url=row.url, retrieved_at=retrieved_at),
        sources_consulted=len({(h.metadata or {}).get("source_id") for h in hits}),
    )
