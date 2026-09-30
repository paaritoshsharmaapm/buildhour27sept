"""Interactive smoke test for the Groq key and the answer path.

    python scripts/ask.py                    # interactive: type questions
    python scripts/ask.py "exit load?"       # one-shot

This is a developer harness, not the product CLI (that is P14). It exists to
answer one question before any UI exists: does the key work, and does the
retrieved context reach the model intact?

The refusal paths deliberately skip the API call. That is the guardrails doing
their job, and spending a token to re-learn "this is advice" would be wrong.
A blocked question costs no tokens and no latency.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import CONFIG
from src.guardrails import (
    REFUSAL_ADVICE,
    REFUSAL_NO_GROUNDING,
    REFUSAL_OUT_OF_SCOPE,
    REFUSAL_PERFORMANCE,
    REFUSAL_PII,
    classify,
    render,
    scan_pii,
)
from src.retriever import retrieve
from src.sources import load_registry

# Provisional. P11 owns the real system prompt; this is the minimum needed to
# prove the key works and the context arrives readable.
SYSTEM_PROMPT = """You answer ONLY from the <context> below, which is extracted from HDFC Mutual Fund's official website.

Rules:
1. Use only facts present in the context. Never add numbers from memory.
2. Answer in at most 3 sentences.
3. If the context does not contain the answer, reply exactly: NO_GROUNDING
4. Reply with JSON only: {"answer": "...", "source_id": "<id or empty>"}
"""

DEBUG = False


def _registry():
    return load_registry(CONFIG.SOURCES_CSV)


def _fallback_link(registry) -> str:
    """A link for out-of-scope refusals, resolved from the registry.

    Never model-authored (C5). For a smoke test the first official citable page
    is honest enough; the pipeline picks per-scheme in P13.
    """
    for row in registry.rows.values():
        if not row.discover_only and row.authority == "official":
            return row.url
    return "https://www.hdfcfund.com/"


def _context(hits) -> str:
    blocks = []
    for hit in hits:
        meta = hit.metadata
        blocks.append(
            f"[source_id={meta['source_id']} section={meta.get('section', '')}]\n{hit.text}"
        )
    return "\n\n---\n\n".join(blocks)


def _client():
    from openai import OpenAI

    key = os.environ.get(CONFIG.GROQ_API_KEY_ENV)
    if not key:
        print(
            f"\n  No {CONFIG.GROQ_API_KEY_ENV} found.\n"
            f"  Create a .env in the project root:\n\n"
            f'      {CONFIG.GROQ_API_KEY_ENV}=gsk_...\n\n'
            f"  .env is already gitignored. Get a key at https://console.groq.com/keys\n"
        )
        raise SystemExit(1)
    return OpenAI(base_url=CONFIG.GROQ_BASE_URL, api_key=key)


def ask(query: str, client, registry) -> None:
    intent = classify(query, use_llm=False)
    print(f"\n  intent   {intent.kind} ({intent.layer}) {intent.matched or ''}")

    # Q1 - never even reaches the model
    if intent.kind == "PII":
        print(f"\n  {REFUSAL_PII}\n")
        print("  [no API call - guardrail, tokens not spent]")
        return

    # Q2
    if intent.kind == "ADVICE":
        text = REFUSAL_PERFORMANCE if "performance" in intent.matched else REFUSAL_ADVICE
        if text is REFUSAL_PERFORMANCE:
            hit = next(
                (h for h in retrieve(query, debug=DEBUG).hits if h.metadata.get("scheme")),
                None,
            )
            link = hit.metadata.get("url") if hit else _fallback_link(registry)
            text = render(text, factsheet_url=link)
        print(f"\n  {text}\n")
        print("  [no API call - guardrail, tokens not spent]")
        return

    # Q2, third class. Easy to omit, and the failure is silent: a Parag question
    # keeps its "flexi cap" alias match, retrieves HDFC Flexi Cap prose, and the
    # model happily answers a question about a fund we hold no documents for.
    if intent.kind == "OUT_OF_SCOPE":
        print(f"\n  {render(REFUSAL_OUT_OF_SCOPE, link=_fallback_link(registry))}\n")
        print("  [no API call - guardrail, tokens not spent]")
        return

    result = retrieve(query, debug=DEBUG)
    if DEBUG:
        print(f"  where    {result.trace['where']}")
        print(f"  pool     {result.trace['pool']}")
    print(
        f"  filter   {result.filtered_by}   max={result.max_score:.3f}   "
        f"grounded={result.grounded}   pool {result.candidates}->{result.after_mmr}"
    )

    if not result.grounded:
        print(f"\n  {render(REFUSAL_NO_GROUNDING, link=_fallback_link(registry))}\n")
        print("  [no API call - grounding gate, tokens not spent]")
        return

    for hit in result.hits:
        print(f"    {hit.score:.3f} {hit.metadata['block_kind']:7} {hit.metadata['source_id']:24} {hit.text[:56]}")

    started = time.time()
    try:
        response = client.chat.completions.create(
            model=CONFIG.GROQ_MODEL,
            temperature=CONFIG.LLM_TEMPERATURE,
            max_tokens=CONFIG.LLM_MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"<context>\n{_context(result.hits)}\n</context>\n\n<question>{query}</question>",
                },
            ],
        )
    except Exception as error:
        print(f"\n  GROQ CALL FAILED after {time.time() - started:.2f}s\n  {type(error).__name__}: {error}\n")
        return

    elapsed = (time.time() - started) * 1000
    raw = response.choices[0].message.content
    usage = response.usage
    print(
        f"\n  model    {response.model}   {elapsed:.0f}ms   "
        f"tokens {usage.prompt_tokens}->{usage.completion_tokens}"
    )
    print(f"  raw      {raw}")

    import json

    try:
        payload = json.loads(raw)
    except Exception:
        print("  (model did not return JSON)\n")
        return

    if payload.get("answer", "").strip().upper() == "NO_GROUNDING":
        print(f"\n  {render(REFUSAL_NO_GROUNDING, link=_fallback_link(registry))}\n")
        return

    print(f"\n  ANSWER   {payload.get('answer')}")
    source_id = payload.get("source_id")
    row = registry.resolve(source_id) if source_id else None
    if row is None:
        print("  CITATION [none - model gave an unknown source_id; Q9 would drop it]\n")
    else:
        print(f"  CITATION {row.url}\n")


def main() -> int:
    global DEBUG
    registry = _registry()
    client = _client()
    print(f"\n  model {CONFIG.GROQ_MODEL} via {CONFIG.GROQ_BASE_URL}")
    print("  type a question, or: /debug  /quit")

    if len(sys.argv) > 1:
        ask(" ".join(sys.argv[1:]), client, registry)
        return 0

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not query:
            continue
        if query in {"/quit", "/exit", "quit", "exit"}:
            return 0
        if query == "/debug":
            DEBUG = not DEBUG
            print(f"  debug {'on' if DEBUG else 'off'}")
            continue
        ask(query, client, registry)


if __name__ == "__main__":
    raise SystemExit(main())
