# PRD — Mutual Fund FAQ Assistant (Facts-Only RAG Chatbot)

**Status:** Draft for milestone brief
**Type:** Class milestone demo — working prototype
**Owner:** Team (4 members, TBD)
**Last updated:** 2026-09-29

---

## 1. Problem Statement

Retail users comparing mutual fund schemes repeatedly ask the same factual questions: *what is the expense ratio? Does this ELSS have a lock-in? What is the minimum SIP? Is there an exit load? What benchmark does it track? How do I download my capital-gains statement?*

These questions are answerable, but:

- Official answers are scattered across factsheets, KIM/SID documents, fee/charge pages, and AMFI/SEBI notices.
- Aggregator sites answer them in a form that is easy to misread and drifts out of date.
- Generic LLM chatbots will happily **hallucinate** a fee, a lock-in period, or a tax rule, and will freely volunteer **investment advice** they have no basis for.

A wrong expense ratio or a wrong lock-in claim causes real financial harm. So the product must be **facts-only, source-cited, and refusal-aware**.

### 1.1 What we are building

A **RAG (Retrieval-Augmented Generation) chatbot** that answers mutual fund questions using **only** a small, curated corpus of public pages for **one AMC — HDFC Mutual Funds — and 5 schemes**.

```
Ingestion:  Load → Clean → Chunk → Embed → Store in Vector DB (Chroma, persisted)
Query:      Question → Guardrails → Embed → Retrieve top-k → LLM → Cite → Format
```

**Core product principle:** the assistant never answers from model memory. It answers from retrieved chunks, and if the retrieved chunks do not support an answer, it says so.

---

## 2. Goals & Non-Goals

### Goals (demo must prove these)

| # | Goal | Success signal |
|---|------|----------------|
| G1 | Answer factual MF questions correctly from our corpus | ≥90% correct on a 10-query golden set |
| G2 | **Every** answer carries exactly one working source link | 100% of factual answers have a citation |
| G3 | Refuse advice/suitability questions politely | 100% refusal on "should I buy/sell?" class |
| G4 | Never perform or imply return/performance comparison | 0 performance claims emitted |
| G5 | Collect and refuse PII (PAN, Aadhaar, account no., OTP, email, phone) | 100% blocked/redacted on test inputs |
| G6 | Answers are short and dated | ≤3 sentences + "Last updated from sources:" |
| G7 | Ingestion is a one-time, inspectable artifact | `chunks.txt` readable; vector store persisted |

### Non-Goals (explicitly out of scope)

- Live NAV, fund screening, portfolio analysis, or recommendation engine.
- Multiple AMCs. (One AMC only — HDFC.)
- User accounts, login, chat history persistence across users.
- Ingestion of PDFs requiring OCR (we prefer HTML pages / text factsheets; if a PDF is needed, text-extract only).
- Any model fine-tuning.

---

## 3. Users & Use Cases

**Primary — Retail investor comparing HDFC schemes.**
Walks into a demo, asks: *"What's the exit load on HDFC Large Cap?"* Expects a number and a link they can verify.

**Secondary — Support/content team member.**
Asks repetitive operational questions: *"How do I download the capital-gains statement?"* Expects step-by-step guidance plus the official guide link.

**Out of scope (refuse):** "Is HDFC ELSS better than Parag Parag Flexi Cap for me?" "Should I sell my HDFC Large Cap now?" "What returns will I get?"

---

## 4. Scope

### 4.1 AMC

**HDFC Asset Management Company (HDFC Mutual Funds)**

### 4.2 Schemes in corpus (5)

| # | Scheme | Category | Groww seed URL |
|---|--------|----------|----------------|
| 1 | HDFC Large Cap Fund – Direct – Growth | Large Cap | `https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth` |
| 2 | HDFC Equity (Flexi Cap) Fund – Direct – Growth | Flexi Cap | `https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth` |
| 3 | HDFC ELSS Tax Saver Fund – Direct – Growth | ELSS | `https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-growth` |
| 4 | HDFC Small Cap Fund – Direct – Growth | Small Cap | `https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth` |
| 5 | HDFC Balanced Advantage Fund – Direct – Growth | Hybrid | `https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth` |

All **Direct–Growth** plans only. This is a deliberate simplification: Direct vs Regular fee differences would otherwise double the corpus for no demo value.

### 4.3 Source hierarchy (important)

The brief says *"official public pages"* / *AMC–SEBI–AMFI*, but supplies Groww links as the seed set. Groww is a **broker/aggregator**, not the issuer. Resolution we propose:

1. **Primary (authoritative, used for cited facts):** `hdfcmutualfund.com` scheme pages, factsheets, fee & charge pages, KIM/SID, and `amfiindia.com` scheme data.
2. **Secondary (navigation/discovery only):** Groww pages, used to *find* official URLs — **not** cited as the source of a number where an official equivalent exists.
3. **Never:** third-party blogs, YouTube, Reddit, news articles, screenshots.

> The final `sources.csv` will record, per source: `url`, `authority` (official/aggregator), `scheme`, `doc_type`, `retrieved_at`. This makes the provenance decision auditable during the demo.

### 4.4 Required facts to be answerable

| Category | Example question |
|----------|------------------|
| Expense ratio | "What is the expense ratio of HDFC Large Cap?" |
| Exit load | "Is there an exit load on HDFC Flexi Cap?" |
| Minimum SIP / lump sum | "What is the minimum SIP?" |
| Lock-in (ELSS) | "What's the lock-in period on HDFC ELSS?" |
| Riskometer | "What is the riskometer level of HDFC Small Cap?" |
| Benchmark | "What benchmark does HDFC Balanced Advantage track?" |
| Statements & tax docs | "How do I download the capital-gains statement?" |
| Fund basics | "What is the expense ratio… / Who is the AMC / Fund objective" |

---

## 5. Functional Requirements

### FR-1 — Source registry
A checked-in `sources.csv` listing every URL in scope with metadata (see §4.3). Ingestion reads from this file, not from hardcoded URLs.

### FR-2 — Ingestion pipeline (one-time, persisted)
`Load → Clean → Chunk → Embed → Store`

- **FR-2.1 Load:** fetch each source, extract readable text.
- **FR-2.2 Clean:** strip nav, cookie banners, ads, related-fund carousels, footer boilerplate. Preserve headings.
- **FR-2.3 Chunk:** strategy chosen *after inspecting the data* and justified in writing (§6.3). Every chunk is written to a human-readable `chunks.txt` with its metadata inline.
- **FR-2.4 Embed:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim), same model for documents and queries.
- **FR-2.5 Store:** ChromaDB with `persist_directory`. Re-running ingestion is idempotent — same input ⇒ same store. A `--force` flag allows rebuild.
- **FR-2.6 Last-updated tracking:** each source records a fetch timestamp, surfaced in answers.

### FR-3 — Guardrails (pre-retrieval)
- **FR-3.1 PII filter on input:** regex-detect PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), Aadhaar (12-digit), account numbers (8–18 digit runs), OTPs, emails, phone numbers. On hit: do **not** call the LLM, do **not** log the raw string. Return a fixed neutral message. Never echo the detected value back.
- **FR-3.2 Intent classification:** classify query as `FACTUAL` / `ADVICE` / `OUT_OF_SCOPE` / `PII` via keyword+rule layer first, LLM-assisted second.
- **FR-3.3 Advice refusal:** for `ADVICE` (should I buy/sell, which is better, is it good, portfolio allocation), respond with a fixed facts-only message + one relevant **educational** link (e.g. SEBI's "Mutual Fund Basics" / investor education page). No hedging, no numbers, no suggestions.

### FR-4 — Retrieval
- **FR-4.1 Query embedding:** same MiniLM model.
- **FR-4.2 Top-k retrieval:** k = 5, cosine distance.
- **FR-4.3 Metadata filtering:** if the query names a scheme (or category), pre-filter to that scheme's chunks. Fall back to global search when the scheme is unknown.
- **FR-4.4 Diversity:** MMR re-ranking to avoid 5 near-duplicate chunks from one page.
- **FR-4.5 Grounding gate:** if the best chunk's similarity is below a tuned threshold, respond with "I don't have that in my sources" + a pointer to the official page — **never** let the LLM free-associate.

### FR-5 — Answer generation
- **FR-5.1 Strict system prompt:** answer *only* from provided chunks; if chunks are insufficient, say so; never use prior knowledge; never compute or compare returns.
- **FR-5.2 Exactly one citation:** every factual answer ends with one markdown link to the specific source page.
- **FR-5.3 Performance-claim refusal:** if asked about returns/performance, do not compute or compare. Reply pointing to the official factsheet.
- **FR-5.4 Output format (enforced, post-processed):**
  ```
  <answer, ≤3 sentences>
  Source: [<title>](<url>)
  Last updated from sources: <YYYY-MM-DD>
  ```
- **FR-5.5 Length guard:** deterministic post-process truncates to 3 sentences if the model overruns, rather than trusting the prompt alone.

### FR-6 — UI (tiny, per brief)
- Welcome line, e.g. *"Hi! I answer facts about 5 HDFC Mutual Fund schemes using official sources only."*
- **3 example question chips** (clickable), one from each of: fees, ELSS lock-in, statements.
- Persistent note under the input: **"Facts-only. No investment advice."**
- Message thread: user bubble → assistant bubble (answer + citation + last-updated).
- Optional small line: *"Sources consulted: 2"* for transparency.
- Run locally via Streamlit (single `streamlit run app.py`).

### FR-7 — Inspectability
- `chunks.txt` — every chunk + its metadata, human-readable.
- `sources.csv` — source list deliverable.
- `sample_qa.md` — 5–10 queries with answers + links.
- Print retrieval scores for a sample query in a debug script to show the pipeline working.

---

## 6. Non-Functional Requirements

### NFR-1 — Determinism & reproducibility
Same question + same store ⇒ same answer. Temperature 0. No hidden network calls at query time (Groq is the only runtime API).

### NFR-2 — Performance
- Ingestion: < 3 min for 5–15 pages, run once.
- Query: < 5 s end-to-end (embed + retrieve + LLM).
- Runs on CPU-only laptop, no GPU.

### NFR-3 — Cost
Free/local only: MiniLM (local), Chroma (local), Streamlit (local), Groq free tier.

### NFR-4 — Security & privacy
- `GROQ_API_KEY` in `.env`; `.env` gitignored; `.env.example` committed.
- No PII persisted to disk, logs, or vector store.
- No analytics, no telemetry, no third-party trackers in the UI.

### NFR-5 — Cost-of-error (highest severity)
An incorrect financial fact is the worst possible failure. Mitigations: narrow corpus, mandatory citation, grounding threshold, and no-parameter-recall by design.

### NFR-6 — Offline robustness
If Groq is unreachable, the app must fail with a clear message, not a stack trace. Retrieval still runs so the demo can show retrieved context.

---

## 7. Technical Architecture

### 7.1 Stack (fixed by brief)

| Layer | Choice | Note |
|-------|--------|------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Local, no API key, 384-dim. **Same model both sides.** |
| Vector DB | ChromaDB | `persist_directory=./chroma_db` |
| LLM | Groq (`llama-3.x` / `llama-3.1-8b-instant`) | Key in `.env` only |
| UI | Streamlit | Single-file app |
| Orchestration | Python 3.10+, plain functions first | Notebook fallback allowed |

### 7.2 Repository layout

```
├── PRD.md
├── README.md
├── Problemstatement.txt
├── requirements.txt
├── .env.example
├── .gitignore
├── data/
│   ├── sources.csv           # FR-1 source registry
│   ├── raw/                  # fetched raw HTML/text, per source
│   └── chunks.txt            # FR-2.3 inspectable chunks + metadata
├── chroma_db/                # persisted vector store
├── src/
│   ├── config.py             # paths, model names, thresholds
│   ├── loaders.py            # FR-2.1 fetch + extract
│   ├── cleaner.py            # FR-2.2
│   ├── chunker.py            # FR-2.3
│   ├── embedder.py           # FR-2.4
│   ├── store.py              # FR-2.5 Chroma persist
│   ├── ingest.py             # FR-2 orchestration CLI
│   ├── guardrails.py         # FR-3
│   ├── retriever.py          # FR-4
│   ├── generator.py          # FR-5
│   └── format_answer.py      # FR-5.4/5.5
├── app.py                    # FR-6 Streamlit UI
├── eval/
│   ├── golden_qa.json
│   └── run_eval.py           # §8 metrics
└── sample_qa.md              # deliverable
```

### 7.3 Ingestion stages (each independently runnable & inspectable)

**S1 — Load.** Read `sources.csv`. Fetch each URL → `data/raw/<source_id>.html`. Log status code, bytes, fetch time. Fail loudly per-source, continue others.

**S2 — Clean.** Strip boilerplate; keep `h1/h2/h3`, table rows, and list items. Tables are the highest-value content (expense ratios, exit-load slabs) and are **preserved as text rows** rather than dropped.

**S3 — Chunk.** See §7.4.

**S4 — Embed.** Batch-embed all chunks. Log count + mean vector norm as a sanity check.

**S5 — Store.** Upsert into persistent Chroma. Verify `collection.count()` matches the number of chunks written. Idempotency via stable `chunk_id = sha256(source_id + chunk_index + text)`.

### 7.4 Chunking strategy (proposal — to be validated against the data)

We will inspect the fetched pages first and adjust before writing code. Working proposal:

| Parameter | Value | Why |
|-----------|-------|-----|
| Granularity | Heading-aware recursive splitting | MF pages are sectioned (`Expenses`, `Exit load`, `Riskometer`); splitting mid-section destroys answerability |
| Chunk size | ~450 tokens | A fee table + its heading/label fits without merging two schemes |
| Overlap | ~60 tokens (~13%) | Preserves the subject/verb across a boundary without heavy duplication |
| Hard rule | **Never split a table row**; never merge chunks across `source_id` | Cross-page merges break single-citation accuracy |
| Keep together | Heading + its body + the URL footer | Self-contained → better standalone embeddings |

**Metadata kept per chunk (this is what powers filtering, citation, and "last updated"):**

```json
{
  "chunk_id": "sha256...",
  "text": "...",
  "source_id": "hdfc_large_cap_official",
  "source_title": "HDFC Large Cap Fund – Direct – Growth",
  "url": "https://hdfcmutualfund.com/...",
  "authority": "official",
  "scheme": "HDFC Large Cap Fund",
  "scheme_category": "large_cap",
  "doc_type": "scheme_page | factsheet | fee_charges | kim | sid | guide",
  "section": "Expenses and Taxes",
  "faq_question": null,
  "chunk_index": 3,
  "token_count": 442,
  "retrieved_at": "2026-09-29"
}
```

`scheme` + `doc_type` + `section` are the fields FR-4.3 filters and FR-5.2 cites. `retrieved_at` powers "Last updated from sources".

### 7.5 Query-time stages

```
user input
   ↓
[FR-3.1] PII scan ──────────────── hit → fixed neutral reply (no LLM, no logging)
   ↓ pass
[FR-3.2] Intent classify ───────── ADVICE → refusal template + educational link
   ↓ FACTUAL
[FR-4.1] Embed query (MiniLM)
[FR-4.3] Optional metadata filter (scheme / category / doc_type)
[FR-4.2] Chroma top-k=5 (cosine)
[FR-4.4] MMR diversity re-rank
[FR-4.5] Grounding gate (score < τ) → "not in my sources" + official page link
   ↓ pass
[FR-5.1] Groq call, temperature 0, chunks as the only context
   ↓
[FR-5.4/5.5] Post-process: enforce ≤3 sentences, attach 1 citation, append last-updated
   ↓
[FR-3.4] Output PII scrub (never echo PAN/account no.)
   ↓
Streamlit render
```

### 7.6 Prompt contracts

**System prompt (generation).** Non-negotiable clauses, in order:
1. You answer only from `<context>`. Outside knowledge is forbidden.
2. If the context does not contain the answer, reply exactly: `I don't have that in my sources.`
3. Never give advice, opinions, or "should I" answers.
4. Never state, compute, compare, or estimate returns or performance. For performance questions, point to the factsheet.
5. Maximum 3 sentences. No preamble, no restating the question.
6. Do not invent numbers. If a number is absent from context, omit it.

**Refusal template (advice).** Fixed, non-negotiable, no numbers:
> "I'm a facts-only assistant, so I can't tell you what to buy or sell. I can share factual details like expense ratio, exit load, lock-in, or benchmark. For guidance on choosing a scheme, see [SEBI Mutual Fund Basics](<edu-link>)."

**Out-of-scope template.** For anything outside the 5 schemes:
> "I only have documents for 5 HDFC Mutual Fund schemes, so I can't answer that. Here's the official scheme page: <link>."

---

## 8. Evaluation & Acceptance Criteria

### 8.1 Golden set
`sample_qa.md` (deliverable) plus an expanded `eval/golden_qa.json` of ~20 labelled queries spanning: 5 schemes × (expense ratio, exit load, min SIP), ELSS lock-in, riskometer, benchmark, statement download, 1 out-of-scope, 2 advice-style, 2 PII-style.

### 8.2 Metrics

| Metric | Target |
|--------|--------|
| Factual correctness (vs. source) | ≥ 90% |
| Answers with exactly one valid, working citation | 100% |
| Correct refusal on advice queries | 100% |
| Correct refusal on PII queries | 100% |
| Answers ≤ 3 sentences | 100% |
| Answers containing a performance/return claim | 0 |
| Answers citing an aggregator when an official source exists | 0 |

### 8.3 Manual demo checks
- [ ] Kill the network → app shows a clear error, not a crash
- [ ] Rename `chroma_db/` → app detects and instructs to run ingestion
- [ ] Ask an off-corpus question → graceful "not in my sources"
- [ ] Ask "Should I buy HDFC ELSS?" → refusal, no numbers
- [ ] Paste a PAN → blocked, value not echoed

---

## 9. Constraints (from the brief — non-negotiable)

| ID | Constraint | Enforcement |
|----|-----------|-------------|
| C1 | **Public sources only.** No third-party blogs. No backend screenshots. | `sources.csv.authority` allowlist: `official`, `aggregator` (discovery only). Review at build time. |
| C2 | **No PII.** No PAN, Aadhaar, account no., OTP, email, phone — not accepted, not stored. | FR-3.1 input filter + FR-3.4 output scrub; `.gitignore` raw logs. |
| C3 | **No performance claims.** Don't compute or compare returns; link the factsheet. | Prompt clause 4 + regex scan in eval for return words (`%`, `CAGR`, `returns`, `outperformed`). |
| C4 | **Clarity & transparency.** ≤3 sentences + "Last updated from sources:". | FR-5.5 deterministic enforcement, not just a prompt instruction. |
| C5 | **Exactly one source link per answer.** | FR-5.2; the LLM returns a `source_id` key that we map to a URL — the model may not invent URLs. |
| C6 | **Same embedding model both sides.** | Single `embedder.py`; no second model. |
| C7 | **Chroma persisted to disk; ingest once.** | `persist_directory`; startup checks store exists. |
| C8 | **Groq key in `.env`, never committed.** | `.gitignore` + `.env.example`. |

> **Design note on C5:** we will *not* let the LLM emit a URL. It returns the `source_id` of the chunk it used; the application maps `source_id → url` from `sources.csv`. This makes hallucinated links structurally impossible.

---

## 10. Deliverables

| # | Deliverable | File / location | Status |
|---|-------------|-----------------|--------|
| D1 | Working prototype (app) | deployed link or repo + `app.py` | ☐ |
| D2 | ≤3-min demo video (fallback if hosting fails) | — | ☐ |
| D3 | Source list of the URLs used | `data/sources.csv` (5+ URLs) | ☐ |
| D4 | README: setup steps, scope (AMC + schemes), known limits | `README.md` | ☐ |
| D5 | Sample Q&A: 5–10 queries with answers + links | `sample_qa.md` | ☐ |
| D6 | Disclaimer snippet used in the UI | in `app.py` + quoted in README | ☐ |
| D7 | Inspectable chunk dump | `data/chunks.txt` | ☐ |
| D8 | PRD (this document) | `PRD.md` | ☑ |

---

## 11. Milestones

| Phase | Work | Output | Owner |
|-------|------|--------|-------|
| M1 | Scope corpus, build `sources.csv`, resolve official-vs-aggregator URLs | `sources.csv` | TBD |
| M2 | Loader + cleaner; inspect raw text; **propose chunking strategy in writing** | `raw/`, chunking rationale | TBD |
| M3 | Chunker + `chunks.txt` + embedder + Chroma persist | persisted store | TBD |
| M4 | Guardrails (PII + intent) + refusal templates | `guardrails.py` | TBD |
| M5 | Retriever (filter, k=5, MMR, threshold) + generator + formatter | end-to-end CLI Q&A | TBD |
| M6 | Streamlit UI + disclaimer + 3 example questions | `app.py` | TBD |
| M7 | Golden set, eval script, fix failures, tune τ and k | `run_eval.py` report | TBD |
| M8 | README, `sample_qa.md`, demo video, rehearse | all deliverables | TBD |

---

## 12. Risks & Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|-----------|
| R1 | Groww pages are JS-heavy; server fetch returns little text | Ingestion yields empty chunks | Use official HDFC/AMFI HTML as primary; keep a headless-render fallback; validate non-empty text at load time and fail loudly |
| R2 | Aggregator and official fees disagree | Wrong answer, lost credibility | Official wins; record `authority`; cross-check 2 schemes during build |
| R3 | MiniLM (384-dim, English-only) is weak on numeric/table lookups | Wrong numbers retrieved | Heading-aware chunks, metadata filter on scheme, grounding threshold, manual golden-set regression on numbers |
| R4 | Chunking merges two schemes' fee tables | Cross-scheme contamination | Hard rule: never merge across `source_id`; assert in ingest |
| R5 | LLM invents a plausible fee anyway | High-severity error | Citation must resolve to a real `source_id`; answer text must be entailed by chunks; add a numeric-claim check in eval |
| R6 | Groq free tier rate-limits / key invalid at demo time | Demo fails live | Small demo script, cached transcript of 10 Q&As as backup, `.env.example` with setup step in README |
| R7 | Scope creep into 3 AMCs / 20 schemes | Demo not finished | AMC and scheme count are frozen at §4 |
| R8 | Advisor-style phrasing slips past the refusal layer | Policy violation | Keyword layer *plus* LLM classifier *plus* eval assertions on 6 adversarial queries |
| R9 | Sites change / go down before demo | Broken links | `retrieved_at` timestamp, snapshot raw text in `data/raw/`, re-run a link-checker in the eval script |

---

## 13. Known Limits (to state in README and during the demo)

1. **One AMC, five schemes, Direct–Growth only.** No Regular/Direct comparison, no other AMCs.
2. **Snapshot, not live.** Data reflects a single ingestion date; NAV and AUM are not served.
3. **No return analysis by design.** Performance questions route to the official factsheet.
4. **English only.** MiniLM-L6-v2 is English; Hindi/regional queries will underperform.
5. **Website-scoped corpus.** Only what's on the ingested pages is answerable — a question answerable in a SID PDF we did not ingest will be refused.
6. **No memory.** Each question is independent; no follow-up pronouns ("what about the ELSS one?"). Adding conversation memory is future work.
7. **Single retrieval pass.** No query rewriting, decomposition, or agentic multi-hop.
8. **Aggregator pages used for discovery only** where official pages exist.

---

## 14. Out of Scope / Future Work

- Multi-AMC corpus with per-AMC routing
- Query rewriting for conversational follow-ups
- Cross-source claim reconciliation (flag when two sources disagree)
- A "compare schemes" view that presents facts side-by-side without any recommendation
- Offline LLM (Ollama) to remove the API dependency entirely
- Scheduled re-ingestion with change detection and a diff report

---

## 15. Open Questions

1. **Official vs. Groww** — are we graded on using *only* official AMC/AMFI URLs, or are the supplied Groww pages acceptable as primary sources? (§4.3 proposes official-primary.) *Needs instructor confirmation.*
2. **Hosting** — is a public deployment expected, or is repo + local run acceptable? Affects the D1/D2 deliverable choice.
3. **Groq model** — which exact model ID is permitted/available on the team's account?
4. **PDFs** — are factsheet/SID PDFs required in the corpus, or are HTML pages sufficient? Affects the parser and chunker.
5. **Group size and role split** — needed to assign M1–M8 owners.
6. **Refresh cadence** — is a pre-demo re-ingestion run required, and how recent must `retrieved_at` be?
