# HDFC Mutual Fund FAQ Assistant

A facts-only question-answering assistant for five HDFC schemes. It answers from
publicly available HDFC fund pages using retrieval, and refuses rather than
guesses when the sources do not contain an answer. Every answer carries exactly
one citation, resolved from a source registry rather than from anything the
language model wrote.

**Scope:** HDFC Mutual Fund — Large Cap, Flexi Cap, ELSS Tax Saver, Small Cap,
and Balanced Advantage. Direct–Growth figures only.

**Official vs. aggregator:** only HDFC AMC and AMFI pages are cited. Groww pages
were used *only* to locate official URLs; they are marked `discover_only` in
`data/sources.csv` and are filtered out of anything the assistant can cite
(`Registry.citable_ids` in `src/sources.py`). An answer citing an aggregator
scores zero in the evaluation.

> **Facts-only. No investment advice.**

## Setup

Requires Python 3.10+ (developed and measured on 3.9.6 as well).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then paste your Groq key into .env
```

The first ingest downloads the `all-MiniLM-L6-v2` sentence-transformer (~90 MB)
from Hugging Face, so it needs internet. The vector store is not committed
(`chroma_db/` is gitignored), so build it once:

```bash
python -m src.ingest --force
```

## How to run

```bash
python -m src.cli                              # interactive REPL
python -m src.cli "exit load on HDFC Large Cap" # one-shot
python -m src.cli --debug "expense ratio"       # full Q1-Q9 decision trace
python -m src.cli --json "exit load"            # machine-readable

streamlit run app.py                            # UI, http://localhost:8501

python -m pytest -q                             # 294 tests
python eval/run_eval.py                         # golden-set metrics + link check
python eval/run_eval.py --sweep                 # tau / k / lambda calibration
python eval/recall_check.py -v                  # retrieval-only, no LLM calls
python eval/chunk_coverage.py                   # block-coverage invariants
```

## Architecture

`loaders → cleaner → chunker → embedder → Chroma → ingest`, then
`guardrails → retriever → generator → formatter → pipeline`.

`src/pipeline.py::answer()` is the only entry point. The Streamlit UI calls it
directly and is forbidden from importing the retriever, generator or formatter,
so presentation can never diverge from CLI behaviour.

Nine ordered gates, any of which can end the request:

| Gate | Rule |
|---|---|
| Q1 | PII (PAN, email, phone) → refuse, never echo the input |
| Q2 | Intent: advice / performance / out-of-scope → refuse before retrieval |
| Q3 | Resolve the named scheme, filter every hit to it |
| Q4 | Retrieve a pool of 20 from Chroma |
| Q5 | MMR re-rank, diversified |
| Q6 | Keep `TOP_K = 5` |
| Q7 | Grounding threshold |
| Q8 | Generate with Groq in JSON mode |
| Q9 | Deterministic: numeric support, citation, ≤3 sentences, scrub |

Deterministic gates do the real work. The model is never trusted with a
citation: it returns a `source_id`, and Q9 looks the URL up in the registry.
Any figure in the answer that does not appear in the cited source becomes
`NO_GROUNDING`. Citations are validated against the registry, so a fabricated
URL cannot reach a user.

Full detail: [`architecture.md`](architecture.md).

### Parameters

| Parameter | Value | Note |
|---|---|---|
| `EMBED_MODEL` | `all-MiniLM-L6-v2` | 384-dim, L2-normalised, cosine |
| `CHUNK_SIZE_WP` / overlap | 256 / 40 | word-pieces, ADR-01 |
| `RETRIEVE_POOL` | 20 | re-ranking is meaningless without a pool |
| `TOP_K` | 5 | hits retrieved and cited |
| `CONTEXT_TOP_K` | 3 | hits placed in the prompt |
| `MMR_LAMBDA` | 0.7 | |
| `GROUNDING_THRESHOLD` | 0.35 | calibrated — see below |

### Grounding threshold

`τ = 0.35`, calibrated 2026-09-30 against 25 golden cases
(`python eval/run_eval.py --sweep`). In-corpus recall 1.00, out-of-corpus
gated 0.00 — the gate does not fire.

That is the honest result, not an oversight. MiniLM cosine similarity between
any financial question and any financial chunk is high: in-corpus scores span
0.6463–0.8596 and out-of-corpus scores span 0.5680–0.8064, so the two
distributions overlap and there is no knee. `τ = 0.60` was the only value that
separated anything, but it is tuned on a single negative example with a 0.078
margin, so it was rejected as overfitting. Advice, PII and non-HDFC funds are
all resolved by Q1/Q2 and never reach this gate.

The mechanisms that actually keep the assistant honest are Q1, Q2, the model's
own `off_topic` decline, and the Q9 numeric guard.

## Evaluation

Targets are from `PRD.md` §8.2. Last clean run: **23/25 (92%)**, 294 tests
passing, block coverage 100%.

| Metric | Target | Measured |
|---|---|---|
| Factual correctness (vs. source) | ≥ 90% | 96% |
| Answers with exactly one valid citation | 100% | 100% |
| Correct refusal on advice queries | 100% | 100% |
| Correct refusal on PII queries | 100% | 100% |
| Answers ≤ 3 sentences | 100% | 100% |
| Answers containing a performance claim | 0 | 0 |
| Answers citing an aggregator | 0 | 0 |

Retrieval-only, re-measured after the chunker change: recall@5 **11/12 (92%)**,
scheme isolation 6/6, negative refusal 6/7.

**Two caveats, stated rather than buried.** The 23/25 golden run predates two
later changes — the metric-aware chunk boundary and `CONTEXT_TOP_K = 3` — and has
not been re-measured, because the Groq free tier began returning HTTP 429
mid-session. `run_eval.py` classifies those as infrastructure errors and reports
`None` rather than a false `0`. The two known retrieval failures (a Flexi Cap
TER miss and an unreachable statement-download page) are recorded in
[`implementation.md`](implementation.md) §18c.

**Dependency on a free-tier model.** The provider's limit is *tokens per
minute*, not requests: a ~1,100-token prompt fails while a 108-token one
succeeds. Reducing context from 5 hits to 3 cut the production prompt from ~838
to ~565 tokens. Under throttling the assistant returns `ERROR` and never a
wrong answer — but it is not usable for a demo while throttled.

## Sample Q&A

Full generated set: [`sample_qa.md`](sample_qa.md) — currently stale, see the
warning at its top.

| Query | Behaviour |
|---|---|
| What is the exit load on HDFC Large Cap Fund? | ANSWERED — 1.00% within 1 year, nil after, cited to the official scheme page |
| What is the lock-in period for HDFC ELSS Tax Saver Fund? | ANSWERED — 3 years |
| Who are the fund managers of HDFC Large Cap Fund? | ANSWERED — Dhruv Muchhal, Rahul Baijal |
| Should I buy HDFC ELSS Tax Saver Fund? | REFUSED_ADVICE — no figures, no LLM call |
| My PAN is ABCDE1234F… what is the exit load? | REFUSED_PII — input never echoed |
| How many days to settle redemptions? | NO_GROUNDING — absent from the corpus |

## Known limits

From `PRD.md` §13:

1. **One AMC, five schemes, Direct–Growth only.** No Regular/Direct
   comparison, no other AMCs.
2. **Snapshot, not live.** Data reflects a single ingestion date; NAV and AUM
   are not served.
3. **No return analysis by design.** Performance questions route to the
   official factsheet.
4. **English only.** MiniLM-L6-v2 is English; Hindi/regional queries will
   underperform.
5. **Website-scoped corpus.** Only what is on the ingested pages is
   answerable — a question answerable in a SID PDF we did not ingest will be
   refused.
6. **No memory.** Each question is independent; no follow-up pronouns ("what
   about the ELSS one?"). Conversation memory is future work.
7. **Single retrieval pass.** No query rewriting, decomposition, or agentic
   multi-hop.
8. **Aggregator pages used for discovery only** where official pages exist.

Architectural limits from `architecture.md` §23 that apply here:

- **No cross-encoder reranking.** Precision ceiling is MiniLM + MMR, accepted
  for a CPU demo (ADR-07).
- **Rule layer is English- and India-specific.** PII patterns and advice
  keywords are tuned for Indian retail finance.
- **Aggregator discovery is manual.** No automated Groww→official resolution;
  `sources.csv` is hand-curated.
- **The numeric guard is lexical, not semantic.** It catches `0.53%` not in
  the source; it would not catch a correct number attached to the wrong
  scheme. The Q3 scheme filter covers that gap.
- **No multi-source reconciliation.** If two official pages disagree on a fee,
  both are retrievable and the model may pick either.
- **Grounding threshold has weak discriminative power** on this corpus, as
  measured above.

Two further limits found during evaluation, not in either document:

- `hdfc_request_statement_guide` is a JavaScript shell. It never wins
  retrieval and should be demoted.
- Live fetches from `hdfcfund.com` return HTTP 403 (Akamai bot protection)
  from automated clients, so ingestion runs from cached snapshots in
  `data/raw/`. The link checker reports these as `BLOCKED`, distinct from
  `DEAD`, so a bot wall is not mistaken for a broken citation.

## Deliverables

| ID | Deliverable | Where |
|---|---|---|
| D1 | Working prototype | [`app.py`](app.py), `streamlit run app.py` |
| D2 | Demo video | not included |
| D3 | Source list | [`data/sources.csv`](data/sources.csv) — 11 sources |
| D4 | README | this file |
| D5 | Sample Q&A | [`sample_qa.md`](sample_qa.md) |
| D6 | Disclaimer | `app.py` and quoted at the top of this README |
| D7 | Inspectable chunk dump | [`data/chunks.txt`](data/chunks.txt) — 483 chunks |
| D8 | PRD | [`PRD.md`](PRD.md) |

## Sources

All 11 rows are in [`data/sources.csv`](data/sources.csv). Citable (official,
`discover_only=false`):

| source_id | Authority | URL |
|---|---|---|
| `hdfc_large_cap_scheme` | official | https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct |
| `hdfc_flexi_cap_scheme` | official | https://www.hdfcfund.com/explore/mutual-funds/hdfc-flexi-cap-fund/direct |
| `hdfc_elss_scheme` | official | https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct |
| `hdfc_small_cap_scheme` | official | https://www.hdfcfund.com/explore/mutual-funds/hdfc-small-cap-fund/direct |
| `hdfc_balanced_scheme` | official | https://www.hdfcfund.com/explore/mutual-funds/hdfc-balanced-advantage-fund/direct |
| `hdfc_request_statement_guide` | official | https://www.hdfcfund.com/services/additional-info/request-statement |

Five additional Groww rows exist for discovery only and are never citable.

## Security

`.env` is gitignored and holds the real Groq key. `.env.example` contains a
placeholder only. If a key was ever pasted into a tracked file, **rotate it** —
Git history retains it after the fact.
