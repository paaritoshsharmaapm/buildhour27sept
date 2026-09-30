# Eval report

Generated 2026-09-30 from `eval/golden_qa.json` (25 cases). Facts in that file were grepped out of `data/chunks.txt`.

## Per-case results

| id | expected | got | result | failed checks |
|----|----------|-----|--------|----------------|
| q01 | ANSWERED | ANSWERED | PASS | - |
| q02 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q03 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q04 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q05 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q06 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q07 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q08 | ANSWERED | ERROR | **FAIL** | kind |
| q09 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q10 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q11 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q12 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q13 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q14 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q15 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q16 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q17 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q18 | ANSWERED | ERROR | **FAIL** | kind, facts |
| q19 | ANSWERED | ERROR | **FAIL** | kind |
| q20 | REFUSED_OUT_OF_SCOPE | REFUSED_OUT_OF_SCOPE | PASS | - |
| q21 | REFUSED_ADVICE | REFUSED_ADVICE | PASS | - |
| q22 | REFUSED_ADVICE | REFUSED_ADVICE | PASS | - |
| q23 | REFUSED_PII | REFUSED_PII | PASS | - |
| q24 | REFUSED_PII | REFUSED_PII | PASS | - |
| q25 | NO_GROUNDING | ERROR | **FAIL** | kind |

## PRD 8.2 metrics

| Metric | Target | Measured |
|--------|--------|----------|
| Factual correctness (vs. source) | ≥ 90% | 36% |
| Answers with exactly one valid, working citation | 100% | 100% |
| Correct refusal on advice queries | 100% | 100% |
| Correct refusal on PII queries | 100% | 100% |
| Answers ≤ 3 sentences | 100% | 100% |
| Answers containing a performance/return claim | 0 | 0 |
| Answers citing an aggregator when an official source exists | 0 | 0 |

Pass rate: **24%** (6/25)

## DEAD LINKS

None. Every official URL resolved; no 404, no DNS failure.

## BLOCKED (not dead)

These URLs exist but refuse automated clients (Akamai bot protection). The citations are correct; this environment cannot re-fetch them, which is why ingest runs from cached snapshots.

| source_id | status | url |
|---|---|---|
| hdfc_balanced_scheme | 403 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-balanced-advantage-fund/direct |
| hdfc_elss_scheme | 403 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct |
| hdfc_flexi_cap_scheme | 403 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct |
| hdfc_large_cap_scheme | 403 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct |
| hdfc_request_statement_guide | 403 | https://www.hdfcfund.com/services/additional-info/request-statement |
| hdfc_small_cap_scheme | 403 | https://www.hdfcfund.com/explore/mutual-funds/hdfc-small-cap-fund/direct |
