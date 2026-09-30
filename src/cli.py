"""Terminal harness for the whole pipeline (P14). This is the tool you reach
for at 2 a.m., so it shows the decision trace rather than just the answer.

Four modes: an interactive REPL, a one-shot question, --debug for the full
Q1-Q9 trace, and --json for scripting.

Two rules from C2 shape this file. Chat history stays in-process and is never
written to disk, and any PII reaching the debug view is redacted through
`guardrails.redact_for_trace` rather than printed raw.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from src import guardrails
from src.format_answer import _split_sentences
from src.config import CONFIG
from src.pipeline import answer
from src.store import StoreMissing, require_store

PREVIEW_CHARS = 200


def _latency_line(latency: dict) -> str:
    order = [("q1_pii", "pii"), ("q2_intent", "intent"), ("q3_scheme", "scheme"),
             ("q4_q7_retrieve", "retrieve"), ("q8_generate", "llm"),
             ("q9_format", "post")]
    parts = []
    for key, label in order:
        if key in latency:
            parts.append(f"{label} {latency[key]}ms")
    if parts:
        parts.append(f"total {sum(latency.values())}ms")
    return ", ".join(parts)


def _print_turn(result) -> None:
    print(f"[{result.status}]")
    print(result.text)
    if _latency_line(result.latency_ms):
        print(f"   (latency: {_latency_line(result.latency_ms)})")


def _print_debug(result, query: str) -> None:
    """The seven trace sections, in pipeline order."""
    trace = result.trace or {}
    print(f"\n=== TRACE: {guardrails.redact_for_trace(query, guardrails.scan_pii(query))}"
          if guardrails.scan_pii(query) else f"\n=== TRACE: {query}")

    # 1 PII
    pii = trace.get("pii")
    print(f"[1] PII            : {'HIT ' + pii['hit'] + ' (redacted)' if pii else 'clean'}")

    # 2 intent
    intent = trace.get("intent") or {}
    print(f"[2] Intent         : {intent.get('kind')} via {intent.get('layer')} "
          f"matched={intent.get('matched')} conf={intent.get('confidence')}")

    # 3 scheme
    print(f"[3] Scheme filter  : {trace.get('scheme')}")

    # 4 retrieval
    retrieval = trace.get("retrieval") or {}
    print(f"[4] Candidates     : {retrieval.get('candidates')} -> "
          f"{retrieval.get('after_mmr')} after MMR")
    print(f"[5] Grounding      : max_score={retrieval.get('max_score')} "
          f"threshold={CONFIG.GROUNDING_THRESHOLD} grounded={retrieval.get('grounded')}")

    # 6 hits table
    hits = retrieval.get("hits") or []
    print(f"[6] Final hits ({len(hits)}):")
    print(f"    {'rk':>2} {'score':>6}  {'source_id':<26} {'doc_type':<12} "
          f"{'authority':<11} section / preview")
    for hit in hits:
        preview = (hit.get("preview") or "")[:PREVIEW_CHARS]
        print(f"    {hit['rank']:>2} {hit['score']:>6.3f}  "
              f"{str(hit.get('source_id')):<26} {str(hit.get('doc_type')):<12} "
              f"{str(hit.get('authority')):<11} {hit.get('section')}")
        print(f"        {preview}")

    # 7 model + post-checks
    llm = trace.get("llm") or {}
    print(f"[7] LLM            : model={CONFIG.GROQ_MODEL} "
          f"error={llm.get('error')} off_topic={llm.get('off_topic')} "
          f"source_id={llm.get('source_id')} attempts={llm.get('attempts')}")

    body = (result.text or "").split("\n\nSource:")[0]
    # The formatter's own splitter, not a naive split on "." — that counts
    # "1.00%" as a sentence boundary and reported 4 sentences for a compliant
    # 3-sentence answer. Duplicating the logic here would drift from it.
    sentences = _split_sentences(body)
    links = result.text.count("](")
    print(f"    post-checks    : sentences={len(sentences)} (max {CONFIG.MAX_SENTENCES}) "
          f"links={links} citation={'yes' if result.citation else 'no'} "
          f"final_status={trace.get('final_status', result.status)}")


def _run(query: str, debug: bool, as_json: bool) -> int:
    try:
        result = answer(query, debug=debug or as_json)
    except StoreMissing:
        if as_json:
            print(json.dumps({"status": "ERROR",
                              "text": "The chunk store is missing. Run python -m src.ingest first."}))
        else:
            print("The chunk store is missing or empty. Build it first:\n")
            print("    python -m src.ingest\n")
        return 0
    except Exception as error:
        # I9: never a raw traceback unless explicitly asked for it.
        message = f"Something went wrong: {type(error).__name__}."
        if as_json:
            print(json.dumps({"status": "ERROR", "text": message}))
        else:
            print(message)
            print("Re-run with --traceback for details.")
        return 1

    if as_json:
        payload = {
            "status": result.status,
            "text": result.text,
            "citation": ({"title": result.citation.title, "url": result.citation.url}
                         if result.citation else None),
            "latency_ms": result.latency_ms,
        }
        if debug:
            payload["trace"] = result.trace
        print(json.dumps(payload, indent=2))
        return 0

    _print_turn(result)
    if debug:
        _print_debug(result, query)
    return 0


def _repl() -> int:
    print("Mutual Fund FAQ Assistant — facts only, no investment advice.")
    print("Type a question, or 'exit'. Ctrl-C also exits.\n")
    history: list = []
    while True:
        try:
            query = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        if not query:
            continue
        if query.lower() in {"exit", "quit", ":q"}:
            print("bye.")
            return 0
        code = _run(query, debug=False, as_json=False)
        # In-process only. Nothing is written to disk (C2).
        history.append((query, code))
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    parser.add_argument("question", nargs="*", help="ask one question and exit")
    parser.add_argument("--debug", action="store_true", help="print the Q1-Q9 trace")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--traceback", action="store_true",
                        help="print a full traceback on unexpected failure")
    args = parser.parse_args(argv)

    if args.traceback:
        sys.excepthook = sys.__excepthook__

    try:
        require_store()
    except StoreMissing:
        if args.json:
            print(json.dumps({"status": "ERROR",
                              "text": "The chunk store is missing. Run python -m src.ingest first."}))
            return 0
        print("The chunk store is missing or empty. Build it first:\n")
        print("    python -m src.ingest\n")
        return 0

    query = " ".join(args.question).strip()
    if not query:
        return _repl()
    return _run(query, debug=args.debug, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
