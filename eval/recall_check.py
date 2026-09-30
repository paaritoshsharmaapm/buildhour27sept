"""Retrieval accuracy harness. Run it to measure, not to guess.

    python eval/recall_check.py             # table + summary
    python eval/recall_check.py -v          # plus where each failure ranked
    python eval/recall_check.py --only vocab

Three things are measured, because they fail independently:

  FACT     A known fact is in the top-k, from the right source. This is recall:
           does the right text come back at all?
  ISOLATE  Every hit comes from the scheme the question named. This catches
           cross-scheme contamination, which recall alone cannot see -- a query
           can return five confident chunks from the wrong fund and score 5/5.
  NEGATIVE An out-of-corpus question does NOT clear the grounding gate. This is
           precision at the threshold, and it is the number that decides whether
           the bot invents an answer.

Known failures are kept in the golden set and marked, not deleted. A harness that
hides its misses is a press release.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import CONFIG
from src.embedder import embed_query
from src.retriever import retrieve
from src.store import query_store

# (question, fact that must appear, source_id that must own it, note)
FACTS = [
    ("What is the minimum SIP for HDFC Large Cap Fund?", "Min SIP: ₹ 100", "hdfc_large_cap_scheme", ""),
    ("Minimum SIP for HDFC Flexi Cap Fund?", "Min SIP: ₹ 100", "hdfc_flexi_cap_scheme", ""),
    ("What is the minimum SIP for HDFC ELSS Tax Saver?", "Min SIP: ₹ 500", "hdfc_elss_scheme", ""),
    ("Lock in period of HDFC ELSS Tax Saver Fund?", "Lock in: 3 years", "hdfc_elss_scheme", ""),
    ("Minimum SIP for HDFC Small Cap Fund?", "Min SIP: ₹ 100", "hdfc_small_cap_scheme", ""),
    ("Expense ratio of HDFC Balanced Advantage Fund?", "TER: 0.78", "hdfc_balanced_scheme", ""),
    ("What is the exit load on HDFC Flexi Cap Fund?", "Exit Load", "hdfc_flexi_cap_scheme", ""),
    ("total expense ratio of HDFC Large Cap", "TER: 1.03", "hdfc_large_cap_scheme", ""),
    ("What is the TER of HDFC Large Cap Fund?", "TER: 1.03", "hdfc_large_cap_scheme", "vocab"),
    ("expense ratio of HDFC Large Cap Fund", "TER: 1.03", "hdfc_large_cap_scheme", "vocab"),
    ("expense ratio of HDFC Flexi Cap Fund", "TER: 0.77", "hdfc_flexi_cap_scheme", "vocab"),
    ("expense ratio of HDFC Small Cap Fund", "TER: 0.78", "hdfc_small_cap_scheme", "vocab"),
]

# (question, expected scheme_category) - every hit must belong to it
ISOLATION = [
    ("benchmark of HDFC Balanced Advantage Fund", "hybrid"),
    ("riskometer of HDFC Small Cap Fund", "small_cap"),
    ("who should invest in HDFC ELSS Tax Saver Fund", "elss"),
    ("HDFC Equity fund exit load", "flexi_cap"),
    ("expense ratio of HDFC Large Cap Fund", "large_cap"),
    ("what is the exit load of HDFC Flexi Cap", "flexi_cap"),
]

NEGATIVES = [
    "quantum physics entanglement",
    "how do I bake sourdough bread",
    "weather in mumbai this week",
    "python list comprehension tutorial",
    "who won the 2011 cricket world cup",
    "how to change a car tyre",
    "best stocks to buy tomorrow",
]


def pool_rank(query: str, needle: str) -> str:
    """Where the wanted fact actually sat in the pre-MMR pool."""
    result = retrieve(query, debug=True)
    where = result.trace["where"]
    pool = query_store(embed_query(query), where=where, n_results=CONFIG.RETRIEVE_POOL)
    for index, hit in enumerate(pool):
        if needle in hit.text:
            return f"pool#{index}"
    return "not in pool"


def check_facts(verbose: bool) -> tuple:
    rows = []
    for question, needle, source_id, note in FACTS:
        result = retrieve(question)
        hit = next((h for h in result.hits if needle in h.text), None)
        right_source = hit is not None and hit.metadata["source_id"] == source_id
        rows.append({
            "q": question, "pass": hit is not None and right_source,
            "score": result.max_score, "top1": result.hits[0].metadata["source_id"] if result.hits else "-",
            "src_ok": right_source, "note": note,
            "why": pool_rank(question, needle) if verbose else "",
        })
    return rows


def check_isolation() -> tuple:
    rows = []
    for question, category in ISOLATION:
        result = retrieve(question)
        categories = {h.metadata["scheme_category"] for h in result.hits}
        rows.append({
            "q": question, "pass": categories == {category},
            "want": category, "got": ",".join(sorted(categories)),
            "score": result.max_score,
        })
    return rows


def check_negatives() -> tuple:
    rows = []
    for question in NEGATIVES:
        result = retrieve(question)
        rows.append({
            "q": question, "pass": not result.grounded,
            "score": result.max_score,
            "top1": result.hits[0].metadata["source_id"] if result.hits else "-",
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true", help="show pool rank for misses")
    parser.add_argument("--only", choices=["facts", "isolate", "negatives"])
    args = parser.parse_args()

    print(f"\nRetrieval accuracy   k={CONFIG.TOP_K}  pool={CONFIG.RETRIEVE_POOL} "
          f"tau={CONFIG.GROUNDING_THRESHOLD}  lambda={CONFIG.MMR_LAMBDA}\n")

    failures = 0

    if args.only in (None, "facts"):
        rows = check_facts(args.verbose)
        print("A. FACT RECALL - is the right text in the top-k, from the right source?")
        print(f"   {'':2} {'score':>6} {'src ok':>6} {'note':<5} question")
        for row in rows:
            mark = "OK" if row["pass"] else "XX"
            why = f"   [{row['why']}]" if row["why"] else ""
            print(f"   {mark:2} {row['score']:6.3f} {('yes' if row['src_ok'] else 'NO'):>6} "
                  f"{row['note']:<5} {row['q'][:52]}{why}")
        passed = sum(r["pass"] for r in rows)
        failures += len(rows) - passed
        print(f"   -> {passed}/{len(rows)} = {100*passed/len(rows):.0f}% recall@{CONFIG.TOP_K}\n")

    if args.only in (None, "isolate"):
        rows = check_isolation()
        print("B. SCHEME ISOLATION - does every hit belong to the named scheme?")
        for row in rows:
            mark = "OK" if row["pass"] else "XX"
            print(f"   {mark:2} {row['score']:6.3f} {row['want']:<11} got={row['got']:<11} {row['q'][:46]}")
        passed = sum(r["pass"] for r in rows)
        failures += len(rows) - passed
        print(f"   -> {passed}/{len(rows)} = {100*passed/len(rows):.0f}% isolation\n")

    if args.only in (None, "negatives"):
        rows = check_negatives()
        print("C. NEGATIVES - out-of-corpus questions must NOT clear the gate")
        for row in rows:
            mark = "OK" if row["pass"] else "XX"
            flag = "   <- would be answered" if not row["pass"] else ""
            print(f"   {mark:2} {row['score']:6.3f} {'':<6} {row['q'][:52]}{flag}")
        passed = sum(r["pass"] for r in rows)
        failures += len(rows) - passed
        print(f"   -> {passed}/{len(rows)} = {100*passed/len(rows):.0f}% correct refusal\n")

    total = (len(FACTS) + len(ISOLATION) + len(NEGATIVES)) if args.only is None else 0
    print("=" * 78)
    if failures:
        print(f"{failures} failure(s) of {total} checks.")
    else:
        print(f"All {total} checks pass.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
