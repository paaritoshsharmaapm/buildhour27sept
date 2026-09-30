"""Q1-Q9 in one function (architecture.md §8). The ONLY entry point for the UI,
the CLI and the eval. I8 exists so the graded system and the demoed system are
the same code.

Kept as a linear function with per-stage comments rather than a class with
injected collaborators: the whole value here is that the order is readable, and
the order is the security property. Q1 before anything else, and (d) before (e)
inside the formatter, are both load-bearing.

Never raises. Every path returns an `Answer`, including unexpected exceptions
(I9) — a traceback in a Streamlit console is a failure the user sees.
"""

from __future__ import annotations

import time
from dataclasses import replace

from src import format_answer, guardrails, retriever
from src.config import CONFIG
from src.format_answer import Answer
from src.generator import LLMNotConfigured, generate
from src.sources import load_registry
from src.store import StoreMissing, require_store

# Load the registry once. It is a frozen dataclass read from one CSV, so this is
# a cache, not shared mutable state.
_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry(CONFIG.SOURCES_CSV)
    return _REGISTRY


class _Stage:
    """Records per-stage latency. Cheap enough to leave on always; the UI shows it."""

    def __init__(self):
        self.latency_ms: dict = {}
        self._started = time.time()

    def mark(self, name: str) -> None:
        now = time.time()
        self.latency_ms[name] = int((now - self._started) * 1000)
        self._started = now


def _answer(query: str, status: str, text: str, stage: _Stage,
            trace: dict, *, citation=None, sources: int = 0) -> Answer:
    return Answer(
        query=query,
        status=status,
        text=text,
        citation=citation,
        sources_consulted=sources,
        latency_ms=dict(stage.latency_ms),
        trace=trace,
    )


def _hit_trace(hits: list) -> list:
    """The retrieval half of the trace, per architecture.md §16.

    A text preview is included deliberately: it is the evidence a reviewer needs
    to judge whether the retrieval was any good, and it contains no PII because
    the corpus is public fund data.
    """
    rows = []
    for rank, hit in enumerate(hits, start=1):
        metadata = hit.metadata or {}
        rows.append({
            "rank": rank,
            "score": round(float(hit.score), 4),
            "source_id": metadata.get("source_id"),
            "section": metadata.get("section"),
            "doc_type": metadata.get("doc_type"),
            "authority": metadata.get("authority"),
            # Carried so the demo sidebar can link each consulted source without
            # importing the registry. app.py is limited to pipeline/config/store
            # (I8), so anything the UI needs has to arrive through the trace.
            "url": metadata.get("url"),
            "title": metadata.get("source_title"),
            "chars": len(hit.text),
            "preview": hit.text[:160].replace("\n", " "),
        })
    return rows


def _no_grounding(query: str, stage: _Stage, trace: dict, hits=None, registry=None) -> Answer:
    """Route through the formatter so the string is model-free and identical to
    every other NO_GROUNDING path."""
    link = format_answer._scheme_link(registry or _registry(), hits or [])
    trace["grounding"] = {"grounded": False, "reason": "gate or generate failure"}
    return _answer(query, "NO_GROUNDING",
                   guardrails.render(guardrails.REFUSAL_NO_GROUNDING, link=link),
                   stage, trace)


def answer(query: str, *, debug: bool = False) -> Answer:
    """The whole pipeline. Returns an Answer on every path."""
    stage = _Stage()
    trace: dict = {"query_shape": {"chars": len(query), "words": len(query.split())}}

    try:
        registry = _registry()
    except Exception:
        registry = None

    # --- Q1 PII scan. Must be first: no embed, no log, no network. (I3) ---
    pii = guardrails.scan_pii(query)
    if pii is not None:
        stage.mark("q1_pii")
        # Redacted form only. The raw query must not appear in the trace (C2).
        trace["pii"] = {"hit": pii.label, "redacted": guardrails.redact_for_trace(query, pii)}
        return _answer(guardrails.MASKED, "REFUSED_PII", guardrails.REFUSAL_PII, stage, trace)
    stage.mark("q1_pii")

    # --- Q2 intent. Rule layer first; the classifier only breaks ties. ---
    intent = guardrails.classify(query, use_llm=True)
    stage.mark("q2_intent")
    trace["intent"] = {"kind": intent.kind, "layer": intent.layer,
                       "matched": intent.matched, "confidence": intent.confidence}

    if intent.kind == "ADVICE":
        # REFUSAL_ADVICE carries its own regulator link and takes no placeholder;
        # render() is strict and rejects an unused argument.
        return _answer(query, "REFUSED_ADVICE", guardrails.REFUSAL_ADVICE, stage, trace)

    if intent.kind == "PERFORMANCE":
        link = format_answer._scheme_link(registry, [])
        return _answer(query, "REFUSED_PERFORMANCE",
                       guardrails.render(guardrails.REFUSAL_PERFORMANCE, factsheet_url=link),
                       stage, trace)

    if intent.kind == "OUT_OF_SCOPE":
        link = format_answer._scheme_link(registry, [])
        return _answer(query, "REFUSED_OUT_OF_SCOPE",
                       guardrails.render(guardrails.REFUSAL_OUT_OF_SCOPE, link=link),
                       stage, trace)
    stage.mark("q2_guard")

    # --- Q3 scheme resolution, recorded so a reviewer can see the filter ---
    scheme = retriever.resolve_scheme(query)
    trace["scheme"] = scheme or "unresolved"
    stage.mark("q3_scheme")

    # --- Q4-Q7 embed, filter, MMR, grounding gate ---
    try:
        require_store()
        result = retriever.retrieve(query, debug=debug)
    except StoreMissing:
        stage.mark("q4_q7_retrieve")
        return _no_grounding(query, stage, trace, registry=registry)
    stage.mark("q4_q7_retrieve")

    trace["retrieval"] = {
        "filtered_by": result.filtered_by,
        "candidates": result.candidates,
        "after_mmr": result.after_mmr,
        "max_score": round(result.max_score, 4),
        "grounded": result.grounded,
        "hits": _hit_trace(result.hits),
    }

    if not result.grounded:
        stage.mark("q7_gate")
        return _no_grounding(query, stage, trace, hits=result.hits, registry=registry)
    stage.mark("q7_gate")

    # --- Q8 generate. Any failure still returns the hits, so a demo can show
    # retrieval working without the LLM (architecture.md §14 E2). ---
    try:
        # Only CONTEXT_TOP_K hits go into the prompt: the full prompt was
        # ~838 tokens and sat right on the provider's token-per-minute
        # ceiling. `result.hits` still carries TOP_K, so finalize() keeps
        # checking every chunk from the cited source and the trace still
        # shows all five.
        llm = generate(query, result.hits[:CONFIG.CONTEXT_TOP_K], registry)
    except LLMNotConfigured as error:
        stage.mark("q8_generate")
        trace["llm"] = {"error": "not_configured", "detail": str(error)}
        return _answer(query, "ERROR",
                       "I couldn't reach the language model. Your question was "
                       "retrieved successfully — see the sources below.",
                       stage, trace)
    except Exception as error:  # never let a traceback escape (I9)
        stage.mark("q8_generate")
        trace["llm"] = {"error": "unexpected", "detail": type(error).__name__}
        return _answer(query, "ERROR",
                       "I couldn't reach the language model. Your question was "
                       "retrieved successfully — see the sources below.",
                       stage, trace, sources=len({(h.metadata or {}).get("source_id")
                                                   for h in result.hits}))

    stage.mark("q8_generate")
    trace["llm"] = {"error": llm.error, "off_topic": llm.off_topic,
                    "source_id": llm.source_id, "attempts": llm.attempts}

    if llm.error:
        return _answer(query, "ERROR",
                       "I couldn't reach the language model. Your question was "
                       "retrieved successfully — see the sources below.",
                       stage, trace, sources=len({(h.metadata or {}).get("source_id")
                                                   for h in result.hits}))

    # --- Q9 deterministic post-processing ---
    try:
        final = format_answer.finalize(llm, result.hits, registry, grounded=result.grounded)
    except Exception as error:
        stage.mark("q9_format")
        trace["format"] = {"error": "unexpected", "detail": type(error).__name__}
        return _no_grounding(query, stage, trace, hits=result.hits, registry=registry)
    stage.mark("q9_format")

    trace["final_status"] = final.status
    return replace(final, latency_ms=dict(stage.latency_ms), trace=trace if debug else {})
