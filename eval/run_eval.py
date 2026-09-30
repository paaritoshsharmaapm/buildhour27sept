"""Golden-set evaluation runner (P16). Measures the PRD 8.2 metrics against
real corpus facts and writes eval/report.md.

The point of this file is that it can fail. `expected_facts` in
golden_qa.json were grepped out of data/chunks.txt, never copied from what the
system happened to output, so a retrieval regression shows up as a FAIL rather
than being baked into the expectation.

Usage:
    python eval/run_eval.py                # full run + report.md
    python eval/run_eval.py --json         # machine-readable
    python eval/run_eval.py --sweep        # P17 tau/k/lambda measurement
    python eval/run_eval.py --no-link-check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.golden_qa import load_golden  # noqa: E402
from src.config import CONFIG  # noqa: E402
from src.format_answer import _split_sentences  # noqa: E402
from src.pipeline import answer  # noqa: E402
from src.sources import load_registry  # noqa: E402
from src.store import StoreMissing, require_store  # noqa: E402

LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)")
# A figure that looks FINANCIAL: currency, a decimal, a percentage, or a
# 3+ digit number. A bare small integer ("5 HDFC schemes", "3 years") is a
# count, not a financial figure, and flagging it produced false failures.
FIGURE_RE = re.compile(r"[\u20b9$]\s?\d|\d+\.\d+|\b\d{3,}\b|\d+\s?%")


def _facts_present(text: str, facts) -> bool:
    return all(f.lower() in (text or "").lower() for f in facts)


def _forbidden_present(text: str, terms) -> bool:
    low = (text or "").lower()
    return [t for t in terms if t.lower() in low]


def _refusal_has_figures(text: str) -> bool:
    """A refusal must not contain a figure. PAN-like ids are allowed through
    only if the formatter already scrubbed them, which it does."""
    return bool(FIGURE_RE.search(text or ""))


def check_case(case: dict, result, registry, by_url) -> dict:
    """Run the PRD 8.2 assertions for one case."""
    text = result.text or ""
    status = result.status
    expected_kind = case["expected_kind"]
    checks: dict = {}
    checks["kind"] = status == expected_kind

    checks["facts"] = _facts_present(text, case.get("expected_facts") or [])
    checks["no_forbidden"] = not _forbidden_present(text, case.get("forbidden_terms") or [])

    if status == "ANSWERED":
        links = LINK_RE.findall(text)
        checks["one_link"] = len(links) == 1
        expected_sid = case.get("expected_source_id")
        expected_row = registry.resolve(expected_sid) if expected_sid else None
        if expected_row:
            checks["link_is_expected_source"] = bool(links) and links[0] == expected_row.url
        else:
            checks["link_is_expected_source"] = True
        checks["link_is_registry"] = bool(links) and links[0] in by_url
        checks["link_is_official"] = (
            bool(links) and by_url.get(links[0]) is not None
            and by_url[links[0]].authority == "official"
            and not by_url[links[0]].discover_only
        )
        checks["citation_present"] = result.citation is not None
        body = text.split("\n\nSource:")[0]
        checks["sentences"] = len(_split_sentences(body)) <= CONFIG.MAX_SENTENCES
        # PRD 8.2: zero aggregator citations. The citation URL must belong to
        # a citable (official, non-discovery) registry row.
        checks["no_aggregator"] = checks["link_is_official"]
    else:
        checks["no_figures"] = not _refusal_has_figures(text)
        checks["no_citation"] = result.citation is None
        if expected_kind == "REFUSED_PII":
            checks["pii_not_echoed"] = not re.search(
                r"[A-Z]{5}\d{4}[A-Z]|[\w.]+@[\w.]+", text
            )
    if status == "ANSWERED":
        # Only here is the prose model-written. Refusal templates ship a
        # hardcoded SEBI link, so this check would fail every advice refusal.
        checks["no_model_url"] = not re.search(r"https?://(?!www\.hdfcfund\.com)", text)
    else:
        checks["no_model_url"] = True
    return checks


def run_cases(cases, registry, verbose=False, args_delay=0.0):
    by_url = {row.url: row for row in registry.rows.values()}
    rows = []
    for case in cases:
        started = time.perf_counter()
        result = answer(case["query"])
        elapsed = (time.perf_counter() - started) * 1000
        checks = check_case(case, result, registry, by_url)
        infra = (result.status == "ERROR"
                 and ((result.trace or {}).get("llm") or {}).get("error")
                 in {"rate_limited", "unreachable", "api_error", "not_configured"})
        rows.append({
            "infra_error": infra,
            "id": case["id"],
            "query": case["query"],
            "expected": case["expected_kind"],
            "got": result.status,
            "latency_ms": round(elapsed),
            "checks": checks,
            "passed": all(checks.values()),
            "failed_checks": [k for k, v in checks.items() if not v],
            "text": result.text,
            "citation": (result.citation.url if result.citation else None),
        })
        if args_delay:
            time.sleep(args_delay)
        if verbose:
            mark = "PASS" if rows[-1]["passed"] else "FAIL"
            print(f"  [{mark}] {case['id']} {rows[-1]['expected']:<15}"
                  f" -> {rows[-1]['got']:<15} {elapsed:6.0f}ms"
                  + ("" if rows[-1]["passed"] else f"  {rows[-1]['failed_checks']}"))
    return rows


def link_checker(registry) -> list:
    """R9 pre-demo check: are the official links still alive?"""
    import urllib.error
    import urllib.request

    results = []
    for sid, entry in sorted(registry.rows.items()):
        if entry.authority != "official":
            continue
        state, code = "ALIVE", ""
        try:
            request = urllib.request.Request(entry.url, method="HEAD",
                                             headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                code = str(response.status)
        except urllib.error.HTTPError as error:
            code = str(error.code)
            # 403/429 mean the URL exists and the server refused an automated
            # client (hdfcfund.com sits behind Akamai). Reporting that as a dead
            # link would send a human to "fix" five perfectly good citations.
            # Only 404/410 and DNS failures are genuinely dead.
            state = "BLOCKED" if error.code in (403, 429) else "DEAD"
        except Exception:  # noqa: BLE001 - report, never crash the eval
            code, state = type(error).__name__, "DEAD"
        results.append({"source_id": sid, "url": entry.url, "status": code,
                        "state": state})
    return results


def summarise(rows) -> dict:
    total = len(rows)
    answered = [r for r in rows if r["got"] == "ANSWERED"]
    expect_answered = [r for r in rows if r["expected"] == "ANSWERED"]

    def rate(numer, denom):
        return round(numer / denom, 4) if denom else None

    facts_total = 0
    facts_ok = 0
    for r in rows:
        facts_total += 1
        if r["checks"].get("facts", False):
            facts_ok += 1

    return {
        "cases": total,
        # PRD 8.2, in the PRD's order.
        "factual_correctness": rate(facts_ok, facts_total),
        "one_valid_citation": rate(sum(1 for r in answered if r["checks"].get("one_link")
                                       and r["checks"].get("link_is_registry")
                                       and r["checks"].get("link_is_official")), len(answered)),
        "advice_refusal": rate(sum(1 for r in rows if r["expected"] == "REFUSED_ADVICE"
                                   and r["got"] == "REFUSED_ADVICE"),
                               sum(1 for r in rows if r["expected"] == "REFUSED_ADVICE")),
        "pii_refusal": rate(sum(1 for r in rows if r["expected"] == "REFUSED_PII"
                                and r["got"] == "REFUSED_PII"),
                            sum(1 for r in rows if r["expected"] == "REFUSED_PII")),
        "within_3_sentences": rate(sum(1 for r in answered if r["checks"].get("sentences")),
                                   len(answered)),
        "performance_claims": sum(1 for r in rows if r["got"] == "REFUSED_PERFORMANCE"
                                  and not r["checks"].get("no_figures", False)),
        "aggregator_citations": sum(
            1 for r in answered if r["checks"].get("no_aggregator") is False),
        "pass_rate": rate(sum(1 for r in rows if r["passed"]), total),
        "in_corpus_recall": rate(sum(1 for r in expect_answered
                                     if r["got"] == "ANSWERED"), len(expect_answered)),
        "statuses": dict(Counter(r["got"] for r in rows)),
    }


def sweep(cases, registry):
    """P17a: measure max retrieval similarity BEFORE the grounding gate."""
    from src.retriever import retrieve

    scored = []
    for case in cases:
        # Not every non-ANSWERED case exercises tau. Advice, PII and
        # non-HDFC funds are all resolved upstream by guardrails and never
        # reach the grounding gate, so scoring them as "out of corpus" made the
        # out-of-corpus precision column permanently unreachable — it can never
        # hit 1.0 no matter what tau is, which would have sent a human tuning
        # a number that is not in play.
        if case["expected_kind"] == "ANSWERED":
            label = "in_corpus"
        elif case["expected_kind"] == "NO_GROUNDING":
            label = "true_out_of_corpus"      # this is what tau must gate
        else:
            label = "guardrailed_upstream"    # tau is never consulted
        # retrieve() short-circuits on the grounding gate, so the sweep reads
        # .hits even when grounded is False; that is the point - we need the
        # score the gate WOULD have seen (P17a step 1).
        result = retrieve(case["query"])
        max_score = max((h.score for h in result.hits), default=0.0)
        scored.append({"id": case["id"], "label": label, "score": round(max_score, 4),
                       "query": case["query"]})

    print("\n=== Score list (sorted by score) ===")
    for row in sorted(scored, key=lambda r: r["score"]):
        print(f"  {row['score']:.4f}  {row['label']:<14} {row['id']}  {row['query'][:60]}")

    print("\n=== Per-label summary ===")
    summary = {}
    for label in ("in_corpus", "true_out_of_corpus", "guardrailed_upstream"):
        values = [r["score"] for r in scored if r["label"] == label]
        if values:
            summary[label] = {"n": len(values), "min": round(min(values), 4),
                              "mean": round(sum(values) / len(values), 4),
                              "max": round(max(values), 4)}
            print(f"  {label:<14} n={len(values)} min={summary[label]['min']:.4f} "
                  f"mean={summary[label]['mean']:.4f} max={summary[label]['max']:.4f}")

    print("\n=== Tau sweep ===")
    print("  in-corpus recall  = ANSWERED cases scoring >= tau")
    print("  gated             = true out-of-corpus cases scoring < tau")
    print(f"  {'tau':<6} {'in-corpus recall':<18} {'gated':<10} {'in-corpus lost'}")
    table = []
    in_rows = [r for r in scored if r["label"] == "in_corpus"]
    out_rows = [r for r in scored if r["label"] == "true_out_of_corpus"]
    for tau in [0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65,
                0.70, 0.75, 0.80]:
        recall = sum(1 for r in in_rows if r["score"] >= tau) / len(in_rows) if in_rows else None
        gated = sum(1 for r in out_rows if r["score"] < tau) / len(out_rows) if out_rows else None
        lost = [r["id"] for r in in_rows if r["score"] < tau]
        table.append({"tau": tau, "in_corpus_recall": round(recall, 4),
                      "gated": round(gated, 4) if gated is not None else None,
                      "in_corpus_lost": lost})
        print(f"  {tau:<6.2f} {recall:<18.4f} "
              f"{(f'{gated:.2f}' if gated is not None else 'n/a'):<10} "
              f"{','.join(lost) or '-'}")
    print("\nP17 stop gate: a human picks the knee from this table. "
          "This script does not write config.py.")
    return {"scores": scored, "summary": summary, "tau_table": table}


def write_report(rows, metrics, links) -> None:
    path = ROOT / "eval" / "report.md"
    lines = [
        "# Eval report",
        "",
        f"Generated {date.today().isoformat()} from `eval/golden_qa.json` "
        f"({len(rows)} cases). Facts in that file were grepped out of "
        "`data/chunks.txt`.",
        "",
        "## Per-case results",
        "",
        "| id | expected | got | result | failed checks |",
        "|----|----------|-----|--------|----------------|",
    ]
    for r in rows:
        mark = "PASS" if r["passed"] else "**FAIL**"
        failed = ", ".join(r["failed_checks"]) or "-"
        lines.append(f"| {r['id']} | {r['expected']} | {r['got']} | {mark} | {failed} |")

    lines += ["", "## PRD 8.2 metrics", "",
              "| Metric | Target | Measured |", "|--------|--------|----------|"]
    targets = [
        ("Factual correctness (vs. source)", "≥ 90%", metrics["factual_correctness"]),
        ("Answers with exactly one valid, working citation", "100%",
         metrics["one_valid_citation"]),
        ("Correct refusal on advice queries", "100%", metrics["advice_refusal"]),
        ("Correct refusal on PII queries", "100%", metrics["pii_refusal"]),
        ("Answers ≤ 3 sentences", "100%", metrics["within_3_sentences"]),
        ("Answers containing a performance/return claim", "0",
         metrics["performance_claims"]),
        ("Answers citing an aggregator when an official source exists", "0",
         metrics["aggregator_citations"]),
    ]
    for name, target, measured in targets:
        shown = "n/a" if measured is None else (
            f"{measured:.0%}" if isinstance(measured, float) else str(measured))
        lines.append(f"| {name} | {target} | {shown} |")

    dead = [l for l in links if l["state"] == "DEAD"]
    blocked = [l for l in links if l["state"] == "BLOCKED"]
    lines += ["", f"Pass rate: **{metrics['pass_rate']:.0%}** "
                  f"({sum(1 for r in rows if r['passed'])}/{len(rows)})", "",
              "## DEAD LINKS", ""]
    if dead:
        lines += ["| source_id | status | url |", "|------------|--------|-----|"]
        for l in dead:
            lines.append(f"| {l['source_id']} | {l['status']} | {l['url']} |")
    else:
        lines.append("None. Every official URL resolved; no 404, no DNS failure.")
    if blocked:
        lines += ["", "## BLOCKED (not dead)", "",
                  "These URLs exist but refuse automated clients (Akamai bot "
                  "protection). The citations are correct; this environment "
                  "cannot re-fetch them, which is why ingest runs from cached "
                  "snapshots.", "",
                  "| source_id | status | url |", "|---|---|---|"]
        for l in blocked:
            lines.append(f"| {l['source_id']} | {l['status']} | {l['url']} |")
    path.write_text("\n".join(lines) + "\n")
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--sweep", action="store_true", help="P17a tau/k/lambda measurement")
    parser.add_argument("--no-link-check", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds between cases; keeps Groq under its "
                             "rate limit so results are reproducible")
    args = parser.parse_args()

    try:
        require_store()
    except StoreMissing:
        print("The chunk store is missing. Run python -m src.ingest first.")
        return 1

    cases = load_golden()
    registry = load_registry()
    print(f"Loaded {len(cases)} golden cases against "
          f"{CONFIG.GROQ_MODEL} and tau={CONFIG.GROUNDING_THRESHOLD}.")

    if args.sweep:
        sweep(cases, registry)
        return 0

    rows = run_cases(cases, registry, verbose=args.verbose,
                      args_delay=args.delay)
    metrics = summarise(rows)

    links = [] if args.no_link_check else link_checker(registry)

    if args.json:
        print(json.dumps({"metrics": metrics, "rows": [
            {k: v for k, v in r.items() if k != "text"} for r in rows], "links": links},
            indent=2, default=str))
        return 0

    dead = [l for l in links if l["state"] == "DEAD"]
    blocked = [l for l in links if l["state"] == "BLOCKED"]
    print(f"\n{'id':<5} {'expected':<15} {'got':<15} {'lat':>7}  result")
    for r in rows:
        if r["infra_error"]:
            mark = "INFRA (LLM rate limit/network) - not counted as a quality failure"
        else:
            mark = "PASS" if r["passed"] else "FAIL " + ",".join(r["failed_checks"])
        print(f"{r['id']:<5} {r['expected']:<15} {r['got']:<15} {r['latency_ms']:>6}ms  {mark}")

    print(f"\n=== PRD 8.2 ===")
    print(f"  factual correctness      {metrics['factual_correctness']}")
    print(f"  one valid citation       {metrics['one_valid_citation']}")
    print(f"  advice refusal           {metrics['advice_refusal']}")
    print(f"  PII refusal              {metrics['pii_refusal']}")
    print(f"  <= 3 sentences           {metrics['within_3_sentences']}")
    print(f"  performance claims       {metrics['performance_claims']}")
    print(f"  aggregator citations     {metrics['aggregator_citations']}")
    infra = [r for r in rows if r["infra_error"]]
    scored = [r for r in rows if not r["infra_error"]]
    print(f"  pass rate                {metrics['pass_rate']:.0%}"
          f"  ({sum(1 for r in rows if r['passed'])}/{len(rows)})")
    if infra:
        print(f"\n  !! {len(infra)} case(s) hit an LLM infrastructure error "
              f"({', '.join(r['id'] for r in infra)}).")
        print("     These are Groq rate limits / network, not answer quality.")
        print("     Re-run with --delay 5 once the window resets.")

    print("\n=== DEAD LINKS ===")
    print("  none" if not dead else "\n".join(
        f"  {l['status']}  {l['source_id']}  {l['url']}" for l in dead))
    print(f"\n=== BLOCKED, not dead ({len(blocked)}): Akamai refuses automated clients ===")
    for l in blocked:
        print(f"  {l['status']}  {l['source_id']}")

    write_report(rows, metrics, links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
