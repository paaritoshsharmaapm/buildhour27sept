"""Answer accuracy: does the model state the right value, not just the right chunk?

    python eval/answer_check.py          # run the suite
    python eval/answer_check.py -v       # print every answer and its source
    python eval/answer_check.py -k ter   # only cases whose id contains "ter"

Retrieval checks prove the right text was returned. That is necessary and not
sufficient: a model given the correct chunk can still quote the wrong number,
pick the direct-plan figure when asked for regular, or blend two schemes. Only
calling the model tests the thing the user actually reads.

Each case states the expected string and, where it matters, the value that must
NOT appear. `wrong` is what catches silent substitution, which is the failure
that is invisible to a retrieval metric and dangerous in a financial answer.

The harness deliberately scores the answer text, not an LLM judge, so a run is
reproducible and costs one Groq call per case.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CONFIG
from src.retriever import retrieve

SYSTEM = """Answer ONLY from the <context> below, which is extracted from HDFC Mutual Fund's official website.

Rules:
- Use only the context. If it does not contain the answer, say so.
- Be concise: one or two sentences.
- End with the source URL on its own line, prefixed by "Source:".

<context>
{context}
</context>"""

# Ground truth read from data/chunks.txt, not from memory. The first pass of
# this file guessed several values wrong and the model turned out to be right,
# which is why the expectations live next to the corpus rather than in prose:
#
#   Min SIP  large_cap Rs100  flexi_cap Rs100  elss Rs500  small_cap Rs100
#   TER      large_cap 1.03   flexi_cap 0.77   elss 1.21   small_cap 0.78
#
# `expect_any` is any-of: listing accepted spellings must not require all of
# them. `wrong` holds values belonging to other schemes, so a cross-scheme leak
# fails even when the right value is also present.
CASES = [
    {"id": "ter_large_cap", "q": "What is the TER of HDFC Large Cap Fund?",
     "expect_any": ["1.03"], "wrong": ["0.77", "1.21", "0.78"]},
    {"id": "sip_large_cap", "q": "What is the minimum SIP for HDFC Large Cap Fund?",
     "expect_any": ["100"], "wrong": ["500"]},
    {"id": "sip_flexi_cap", "q": "Minimum SIP for HDFC Flexi Cap Fund?",
     "expect_any": ["100"], "wrong": ["500"]},
    {"id": "exit_load_flexi_cap", "q": "What is the exit load on HDFC Flexi Cap Fund?",
     "expect_any": ["1.00%", "1%"], "wrong": []},
    {"id": "lockin_elss", "q": "Lock in period of HDFC ELSS Tax Saver Fund?",
     "expect_any": ["3 year", "3 years"], "wrong": []},
    {"id": "riskometer_small_cap", "q": "What is the riskometer level of HDFC Small Cap Fund?",
     "expect_any": ["very high", "high"], "wrong": ["low"]},
    {"id": "ter_small_cap", "q": "What is the expense ratio of HDFC Small Cap Fund?",
     "expect_any": ["0.78"], "wrong": ["1.03", "1.21", "0.77"]},
    {"id": "ter_elss", "q": "What is the TER of HDFC ELSS Tax Saver Fund?",
     "expect_any": ["1.21"], "wrong": ["1.03", "0.77", "0.78"]},
    {"id": "sip_elss", "q": "What is the minimum SIP for HDFC ELSS Tax Saver Fund?",
     "expect_any": ["500"], "wrong": ["100"]},
    {"id": "benchmark_balanced",
     "q": "What is the benchmark of HDFC Balanced Advantage Fund?",
     "expect_any": ["nifty"], "wrong": []},
]


def normalise(text: str) -> str:
    """Fold formatting differences so 1.00% and 1% compare equal.

    Substring matching on "1%" fails against "1.00%", which reported a correct
    exit-load answer as wrong. Percentages are normalised to one decimal and
    rupee signs are stripped so "Rs 100" and "Rs100" compare equal.
    """
    text = text.lower().replace("\u20b9", "").replace("rs.", "").replace("rs", "")
    text = re.sub(r"(\d+)\.0+%", r"\1%", text)
    return re.sub(r"\s+", " ", text)


def client():
    from openai import OpenAI

    key = os.getenv(CONFIG.GROQ_API_KEY_ENV)
    if not key:
        raise SystemExit("no key in env")
    return OpenAI(base_url=CONFIG.GROQ_BASE_URL, api_key=key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-k", default="", help="only run ids containing this")
    args = ap.parse_args()

    llm = client()
    cases = [c for c in CASES if args.k in c["id"]]

    passed = failed = 0
    print("\nAnswer accuracy - does the model state the right value?\n")
    for case in cases:
        started = time.time()
        result = retrieve(case["q"])
        context = "\n\n".join(hit.text for hit in result.hits)

        if not result.hits:
            print(f"   XX  {case['id']:<22} no hits (grounded={result.grounded})")
            failed += 1
            continue

        reply = llm.chat.completions.create(
            model=CONFIG.GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM.format(context=context)},
                # The question must be a user message: this model rejects a
                # request whose only message is the system prompt with
                # "No user query found in messages."
                {"role": "user", "content": case["q"]},
            ],
            temperature=0.0,
            max_tokens=200,
        ).choices[0].message.content

        norm = normalise(reply)
        # any-of, not all-of: listing accepted spellings must not require every
        # one of them to appear.
        missing = [] if any(normalise(e) in norm for e in case["expect_any"]) else case["expect_any"]
        leaked = [w for w in case["wrong"] if normalise(w) in norm]
        cites = "http" in norm
        ms = int((time.time() - started) * 1000)

        ok = not missing and not leaked
        passed += ok
        failed += not ok

        note = []
        if missing:
            note.append(f"missing {missing}")
        if leaked:
            note.append(f"WRONG VALUE {leaked}")
        if not cites:
            note.append("no citation")

        print(f"   {'OK ' if ok else 'XX '} {case['id']:<22} {ms:>5}ms  "
              f"hits={len(result.hits)} top={result.hits[0].score:.3f}  {'; '.join(note) or 'clean'}")
        if args.verbose:
            print(f"       Q: {case['q']}")
            print(f"       A: {reply.strip()[:400]}")
            print(f"       src: {result.hits[0].metadata.get('source_id')}")
    total = passed + failed
    print(f"\n   -> {passed}/{total} answers correct")
    print("\n" + "=" * 78)
    print(f"{failed} failure(s)" if failed else "all answer checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
