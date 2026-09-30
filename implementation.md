# Implementation Guide — Mutual Fund FAQ Assistant (Facts-Only RAG Chatbot)

**Companion to:** `architecture.md` (design) and `PRD.md` (requirements)
**Purpose:** Phase-by-phase build instructions you can hand to Cursor (or OpenCode / Claude Code) one phase at a time.
**Last updated:** 2026-09-29

---

## 0. How to Use This Document

Each phase below is a self-contained work order. Copy the **Prompt Block** into Cursor, let it build, then run the **Verify** command. Only move to the next phase when the acceptance criteria are green.

**Why phase-at-a-time matters here.** This is a RAG system with hard safety constraints (no advice, no PII, no hallucinated citations). The value of going slowly is that every constraint in `PRD.md` §9 has a *specific* place it gets enforced. If a phase is allowed to blur into the next, those enforcement points quietly move or disappear.

**Worked example of the loop:**

```
1. Open PRD.md + architecture.md in the workspace.
2. Read Phase 3 (Cleaner) below.
3. Copy the Prompt Block → paste into Cursor → Enter.
4. Run:  python -m pytest tests/test_cleaner.py -v
5. If green → commit → move to Phase 4.
6. If Cursor's output diverges from the architecture, do not accept it.
   §19 lists the invariants that are non-negotiable.
```

**Context budget note.** Each phase tells Cursor exactly which `architecture.md` sections to load. Don't paste the whole file every time — the relevant sections are listed per phase.

**Legend**

| Marker | Meaning |
|--------|---------|
| 🤖 | Fully automatable — Cursor can complete it unaided |
| 🤝 | Needs a human decision or judgement before/during the work |
| ⛔ | **Stop gate.** Do not proceed past this without a human reviewing output |

---

## 1. Phase Overview

| # | Phase | Milestone | Depends on | Marker | Effort |
|---|-------|-----------|------------|--------|--------|
| P0 | Project scaffold | — | — | 🤖 | 30 min |
| P1 | Config + source registry | M1 | P0 | 🤖🤝 | 1 h |
| P2 | **Inspect the corpus** | M2 | P1 | ⛔🤝 | 1 h |
| P3 | Loader | M2 | P2 | 🤖 | 1.5 h |
| P4 | Cleaner | M2 | P3 | 🤖 | 1.5 h |
| P5 | Chunker | M2, M3 | P4 | 🤖🤝 | 2 h |
| P6 | Embedder | M3 | P5 | 🤖 | 45 min |
| P7 | Store (Chroma) | M3 | P6 | 🤖 | 1 h |
| P8 | Ingest orchestrator | M3 | P7 | 🤖 | 1 h |
| P9 | Guardrails | M4 | P8 | 🤖 | 2 h |
| P10 | Retriever | M5 | P9 | 🤖 | 1.5 h |
| P11 | Generator + prompts | M5 | P10 | 🤖🤝 | 1.5 h |
| P12 | Formatter | M5 | P11 | 🤖 | 2 h |
| P13 | Pipeline (Q1–Q9) | M5 | P12 | 🤖 | 1.5 h |
| P14 | CLI + debug view | M7 | P13 | 🤖 | 1 h |
| P15 | Streamlit UI | M6 | P13 | 🤖 | 1.5 h |
| P16 | Eval harness | M7 | P14 | 🤖🤝 | 2.5 h |
| P17 | Tune τ, k, λ | M7 | P16 | ⛔🤝 | 2 h |
| P18 | Deliverables | M8 | P17 | 🤖🤝 | 1.5 h |

**Total ≈ 28 h** across 4 people ≈ 1 working week with parallel ownership (see §1.1).

### 1.1 Suggested parallel split

| Person | Phases | Rationale |
|--------|--------|-----------|
| A | P0, P1, P2, P3, P4, P5 | Owns the data/corpus end of the pipeline |
| B | P6, P7, P8 | Owns vectors + ingestion |
| C | P9, P10, P11, P12, P13 | Owns the whole query path (the critical path) |
| D | P14, P15, P16, P17, P18 | Owns surface + evidence |

C and D are the critical path and the highest-risk work. **Give C the most review attention.**

### 1.2 Dependency graph

```
P0 scaffold
   └── P1 config + sources.csv
          └── P2 ⛔ INSPECT CORPUS (read real pages, write chunking proposal)
                 └── P3 loader
                        └── P4 cleaner
                               └── P5 chunker (+ chunks.txt)
                                      └── P6 embedder
                                             └── P7 chroma store
                                                    └── P8 ingest CLI
                                                           └── P9 guardrails
                                                                  └── P10 retriever
                                                                         └── P11 generator
                                                                                └── P12 formatter
                                                                                       └── P13 pipeline
                                                                                              ├── P14 CLI
                                                                                              ├── P15 Streamlit
                                                                                              └── P16 eval
                                                                                                     └── P17 ⛔ tune τ
                                                                                                            └── P18 deliverables
```

---

## 2. Ground Rules (Apply to Every Phase)

Cursor will drift if you don't restate these. Paste them into every prompt block, or keep them in a `CURSOR_RULES.md` at the repo root and reference it.

### 2.1 Hard invariants — never violate

These come from `PRD.md` §9 and `architecture.md` §19. A phase that breaks one of these is wrong even if it passes its tests.

| ID | Rule |
|----|------|
| I1 | The LLM **never** produces a URL. It returns a `source_id`; `format_answer.py` resolves the URL from the registry. (C5) |
| I2 | The embedding model is used in **exactly one module**, `src/embedder.py`. No second model, no second loader. (C6) |
| I3 | The PII scan runs **before** anything else touches the query — before embed, before logging, before any network call. (C2) |
| I4 | Chunks **never** span two `source_id`s. (R4) |
| I5 | Every chunk is `≤ 256` word-piece tokens, because `all-MiniLM-L6-v2` truncates there. (ADR-01) |
| I6 | No magic numbers in logic modules. Every tunable lives in `src/config.py`. |
| I7 | No PII is ever written to disk, logs, or the vector store. |
| I8 | `app.py`, `src/cli.py`, and `eval/run_eval.py` import only from `src/pipeline.py` — never from individual stages. |
| I9 | No raw stack traces reach the UI. |
| I10 | Don't add libraries beyond those in §2.3 without asking. |

### 2.2 Code conventions

- Python 3.10+. Type hints on every public function. `@dataclass(frozen=True)` for data carriers.
- `from __future__ import annotations` at the top of every module.
- Google-style docstrings, one line, on every public function. **No comments explaining *what* the code does** — only docstrings saying *why*.
- Modules stay under ~250 lines. If one grows past that, split it.
- Errors that a human must act on (missing store, missing key) get custom exception classes, not bare `Exception`.
- Every stage is independently runnable. No hidden global state, no import-time network calls except in `app.py` (see P15).

### 2.3 Dependency allowlist

```
requests
beautifulsoup4
lxml
trafilatura
chromadb
sentence-transformers
openai              # Groq exposes an OpenAI-compatible client
streamlit
python-dotenv
numpy
pandas             # sources.csv + eval reports
pytest
```

If Cursor reaches for `langchain`, `llama-index`, `sentence-transformers` alternatives, `playwright`, `rank_bm25`, or a reranker model — **stop it.** Every one of those is a deliberate exclusion in `architecture.md` §19 (ADR-07) or §10. The hand-rolled stage functions are the deliverable.

### 2.4 What "done" means for a phase

All four must hold:

1. The **Verify** command passes.
2. The **Acceptance criteria** are all satisfied.
3. Nothing outside the phase's file list was modified (check `git status`).
4. The code is committed with the stated message.

---

## 3. P0 — Project Scaffold 🤖

**Goal:** A repo that imports cleanly, has pinned deps, and a test runner that works. Nothing else.

**Depends on:** —
**Files to create:** `.gitignore`, `requirements.txt`, `.env.example`, `src/__init__.py`, `src/config.py`, `tests/__init__.py`, `tests/conftest.py`

**Read first:** `architecture.md` §15 (Configuration)

### Prompt Block — P0

```
Read PRD.md §7.1 and architecture.md §15, then set up the project scaffold.

Create:
1. requirements.txt with exactly: requests, beautifulsoup4, lxml, trafilatura,
   chromadb, sentence-transformers, openai, streamlit, python-dotenv, numpy,
   pandas, pytest

   Role of each, since the list is alphabetical and gives no hint which ones
   actually matter. `beautifulsoup4` + `lxml` are the **primary** extractor
   (ADR-11) and are load-bearing for every answer; `trafilatura` is now only a
   fallback for pages with no usable `<main>`, so a version bump there is low
   risk, but note it is v2 — the `favor_*` kwargs, not `favour_*`.
2. .gitignore containing: .env, .venv/, __pycache__/, *.pyc, chroma_db/,
   data/raw/, .pytest_cache/, .ipynb_checkpoints/
3. .env.example containing: GROQ_API_KEY= and GROQ_MODEL=llama-3.1-8b-instant
   (real value, no API key, with a one-line comment saying to copy to .env)
4. src/__init__.py (empty)
5. src/config.py — a frozen dataclass `Config` with EXACTLY these fields and
   default values, copied from architecture.md §15: ROOT, SOURCES_CSV, RAW_DIR,
   CHUNKS_TXT, MANIFEST, CHROMA_DIR, EMBED_MODEL, EMBED_DIM, EMBED_MAX_SEQ,
   GROQ_MODEL, GROQ_BASE_URL, LLM_TEMPERATURE, LLM_MAX_TOKENS, LLM_TIMEOUT_S,
   CHUNK_SIZE_WP, CHUNK_OVERLAP_WP, MIN_EXTRACTED_CHARS, TOP_K, RETRIEVE_POOL,
   MMR_LAMBDA, GROUNDING_THRESHOLD, MAX_SENTENCES, FETCH_DELAY_S,
   COLLECTION_NAME, COLLECTION_SCHEMA_VERSION, ALLOWED_HOSTS.
   Add a module-level `CONFIG = Config()` instance. Add a comment on
   GROUNDING_THRESHOLD reading "UNTUNED - calibrate in P17". Add a comment on
   EMBED_MAX_SEQ reading "hard model limit, do not exceed (ADR-01)".
6. tests/__init__.py (empty) and tests/conftest.py that adds the repo root to
   sys.path and exposes a `cfg` fixture returning CONFIG.

Do not create any other files. Do not create data/ or chroma_db/ yet.
```

**Verify**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "from src.config import CONFIG; print(CONFIG.EMBED_MODEL, CONFIG.EMBED_MAX_SEQ, len(CONFIG.ALLOWED_HOSTS))"
python -m pytest --collect-only -q
```

**Acceptance criteria**
- [ ] `EMBED_MODEL` prints `sentence-transformers/all-MiniLM-L6-v2`
- [ ] `EMBED_MAX_SEQ` prints `256`
- [ ] `ALLOWED_HOSTS` has 7 entries
- [ ] `.env` appears in `git check-ignore .env` (test it, don't assume)
- [ ] `git status` shows only the 7 files above

**Common mistakes**
- Cursor adds `langchain` "for convenience" → reject, I10.
- `.gitignore` missing `chroma_db/` → the whole point of persistence is a rebuildable artifact.

**Commit:** `chore: scaffold project, pin deps, add config dataclass`

---

## 4. P1 — Config Consumers + Source Registry 🤖🤝

**Goal:** `src/sources.py` — the registry that owns every URL in the system, with invariants enforced in code.

**Depends on:** P0
**Files to create:** `src/sources.py`, `data/sources.csv`, `tests/test_sources.py`

**Read first:** `architecture.md` §6.1 (registry schema + 4 invariants), §13 (API contracts)

### Prompt Block — P1

```
Read architecture.md §6.1 and §13, then implement src/sources.py.

1. Define a frozen dataclass `SourceRow` with fields: source_id, url, authority,
   discover_only, scheme, scheme_category, doc_type, enabled, retrieved_at,
   fetch_status, notes.
2. Define a frozen dataclass `Registry` with:
     rows: dict[str, SourceRow]
     def resolve(self, source_id) -> SourceRow | None
     def citable_ids(self) -> set[str]          # enabled AND not discover_only
     def factsheet_url(self, scheme_category) -> str | None
     def official_page_url(self, scheme_category) -> str | None
3. `load_registry(path) -> Registry` reads a CSV via pandas and calls
   `validate_registry`.
4. `validate_registry` raises `RegistryError` on ANY of these violations:
     a. duplicate source_id
     b. url host not in CONFIG.ALLOWED_HOSTS
     c. authority not in {"official", "aggregator"}
     d. authority == "aggregator" and discover_only is not True
     e. scheme not in the frozen 5-scheme list
        (HDFC Large Cap Fund, HDFC Equity (Flexi Cap) Fund, HDFC ELSS Tax Saver
         Fund, HDFC Small Cap Fund, HDFC Balanced Advantage Fund)
     f. doc_type not in {scheme_page, factsheet, fee_charges, kim, sid, guide, faq}
   The error message must name the offending source_id and which rule broke.
5. `factsheet_url` and `official_page_url` look up by scheme_category, preferring
   doc_type == "factsheet" / "scheme_page", falling back to the first citable
   row. Return None if nothing matches.
6. The 5-scheme list must be a module-level constant, not inline in the check.

Then create data/sources.csv with EXACTLY this header and the 5 Groww rows
below, with discover_only=true (they are aggregators, used for discovery only
per PRD §4.3). Leave retrieved_at and fetch_status empty — ingest fills those.

source_id,url,authority,scheme,scheme_category,doc_type,discover_only,enabled,retrieved_at,fetch_status,notes
hdfc_large_cap_groww,https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth,aggregator,HDFC Large Cap Fund,large_cap,scheme_page,true,true,,,Discovery only
hdfc_flexi_cap_groww,https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth,aggregator,HDFC Equity (Flexi Cap) Fund,flexi_cap,scheme_page,true,true,,,Discovery only
hdfc_elss_groww,https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-growth,aggregator,HDFC ELSS Tax Saver Fund,elss,scheme_page,true,true,,,Discovery only
hdfc_small_cap_groww,https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth,aggregator,HDFC Small Cap Fund,small_cap,scheme_page,true,true,,,Discovery only
hdfc_balanced_groww,https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth,aggregator,HDFC Balanced Advantage Fund,hybrid,scheme_page,true,true,,,Discovery only

Then write tests/test_sources.py covering all 6 validation rules — for each,
build an in-memory CSV fixture (tmp_path) that SHOULD fail, and assert the
RegistryError message names both the source_id and the rule.
```

**Verify**
```bash
python -m pytest tests/test_sources.py -v
python -c "from src.sources import load_registry; r=load_registry('data/sources.csv'); print(len(r.rows), r.citable_ids())"
```

**Acceptance criteria**
- [ ] All 6 tests pass
- [ ] Second command prints `5 set()` — zero citable ids, because every current row is `discover_only`. **This is correct and expected at this stage.** It proves the I1/C1 mechanism already blocks aggregator citations.
- [ ] `data/sources.csv` parses; `source_id` values are unique

**Common mistakes**
- Cursor writes `if host not in ALLOWED_HOSTS: logger.warning(...)` and continues. **It must raise.** A soft failure here is how a third-party blog ends up in the corpus.
- Cursor makes the 5-scheme list derive from `sources.csv` itself, making invariant (e) vacuous.

**Commit:** `feat: source registry with code-enforced host and scheme allowlists`

---

## 5. P2 — Inspect the Corpus ⛔🤝

> **This is a mandated stop gate.** `Problemstatement.txt` says: *"the AI agent should inspect the data and propose a strategy, say why it suits this data, and specify chunk size, overlap, and what metadata each chunk keeps"* before writing chunk code. Skipping this is a grading failure.

**Goal:** Fetch the real pages, read them, and produce a written chunking proposal that supersedes `PRD.md` §7.4.

**Depends on:** P1
**Files to create:** `data/raw/*.html`, `data/raw/*.txt`, `docs/chunking_proposal.md`

### Prompt Block — P2a — fetch and dump

```
Read architecture.md §6.1 and PRD.md §4.2, then extend data/sources.csv.

For each of the 5 schemes, find the OFFICIAL source pages on
hdfcmutualfund.com (and amfiindia.com where it is the authoritative record).
For each scheme add rows to data/sources.csv with authority=official and
discover_only=false, using doc_type values from:
{scheme_page, factsheet, fee_charges, kim, sid, guide, faq}

Target per scheme: the official scheme page, the expense-ratio / fees-and-charges
page, and the factsheet URL. For HDFC ELSS also find a page stating the lock-in
period. Add at least one `guide` row covering how to download the
capital-gains statement.

Rules:
- Only hosts in CONFIG.ALLOWED_HOSTS.
- authority=official MUST mean discover_only=false.
- If you cannot find an official page for something, do NOT invent a URL.
  Leave it out and report it in your summary.
- Do not modify the existing 5 Groww rows.

Then write a standalone throwaway script fetch_probe.py that uses requests +
trafilatura to fetch every enabled row in data/sources.csv, and writes
data/raw/<source_id>.html and data/raw/<source_id>.txt. Print a table of
source_id, HTTP status, bytes, and extracted_chars. Do not keep this script
afterwards — P3 replaces it with the real loader.
```

### Human review checklist ⛔

```
[ ] Every official URL returns HTTP 200
[ ] extracted_chars > 2000 for each (if not -> R1 risk, note it)
[ ] No host outside the allowlist
[ ] At least one source per scheme
[ ] ELSS lock-in is actually findable in the corpus
[ ] Capital-gains statement guide is actually findable
```

### Prompt Block — P2b — inspect and propose

```
Now READ the fetched text. Do not skip this.

1. Read data/raw/*.txt for at least 3 schemes in full.
2. Report, as a markdown table per source:
   - total characters
   - the heading hierarchy you actually see
   - which sections contain NUMERIC data (expense ratio, exit load slabs,
     minimum SIP, lock-in) and how those numbers are formatted
   - how many tables, and whether trafilatura preserved them
   - boilerplate that must be stripped
   - any legal disclaimer text that CONTAINS a real fact (e.g. the ELSS
     three-year lock-in often lives inside a disclaimer) and therefore must
     NOT be stripped
3. Based on what you actually read, write docs/chunking_proposal.md containing:
   - Granularity, with the reason it fits THESE pages
   - Exact chunk size in word-pieces, and the arithmetic showing it fits within
     the 256-token embedding limit (heading prefix + body + URL footer)
   - Exact overlap
   - The full list of metadata fields per chunk, each with one line on which
     pipeline stage consumes it
   - How tables are handled so a fee row is never split
   - A short "risks and what would make me change this" section
4. Flag any page that looks JS-rendered and returned mostly empty.
```

**Acceptance criteria**
- [ ] `docs/chunking_proposal.md` exists and cites specifics from the real pages (actual heading names, actual fee-table format) — not generic advice
- [ ] Chunk size arithmetic sums to ≤ 256
- [ ] The legal-disclaimer observation is addressed
- [ ] JS-rendered pages are listed, if any

**Common mistakes**
- Cursor writes the proposal from theory without reading the files. Check whether it quotes real heading names.
- Cursor proposes 450 tokens again (copied from PRD §7.4). That is **wrong** — see ADR-01. The 256 limit is non-negotiable (I5).

**Commit:** `docs: corpus inspection and chunking strategy proposal`

---

## 6. P3 — Loader 🤖 ✅

**Goal:** Replace `fetch_probe.py` with a real, tiered, fault-tolerant loader.

**Depends on:** P2
**Files to create:** `src/loaders.py`, `tests/test_loaders.py` · **Delete:** `fetch_probe.py`

**Read first:** `architecture.md` §7 S1, §14 E11/E12, §9.1 (PII patterns), ADR-11, ADR-12

> **Amended at P3.** The original prompt prescribed trafilatura as the primary
> extractor and a single transport. Both were wrong in ways the P2 corpus
> inspection measured, so this section has been rewritten to match what shipped.
> The original intent is preserved in the Deviations table below.

### Prompt Block — P3

```
Delete fetch_probe.py. Read architecture.md §7 S1, §14, ADR-11 and ADR-12,
then write src/loaders.py.

1. Define frozen dataclasses:
     RawDoc(source_id, url, http_status, raw_bytes, extracted_text,
            status: str,          # "ok" | "cached" | "short" | "failed" | "blocked"
            retrieved_at: str,    # ISO date
            error: str | None,
            pii_labels: tuple = ())
     SourceFetchError(Exception)   # programmer error only, never network

2. Tier by TRANSPORT first, then by extractor:
   - requests.get(timeout=30, headers=CONFIG.BROWSER_HEADERS)
   - on 403 ONLY, retry via system curl subprocess with the same headers.
     A connection error or a 404 fails identically under curl, so retrying
     those just doubles the wall-clock on a URL already known to be dead.
   - never raise. One dead URL must not abort the run (E11/E12).

3. Extract with BeautifulSoup on <main> (ADR-11). trafilatura survives only as
   a fallback for pages with no usable <main>, using favor_recall=True.
   The API is favor_*, NOT favour_* — spelling it the trafilatura-1.x way
   raises TypeError on v2.

4. `fetch_source(row, *, force=False) -> RawDoc`
   - honours the on-disk cache at data/raw/<source_id>.html unless force=True
   - writes the raw response BEFORE extraction, so extraction bugs stay
     debuggable without re-fetching
   - status "short" if extracted text < CONFIG.MIN_EXTRACTED_CHARS, with a
     "likely JS-rendered" note. DO NOT add Playwright — escalate to a human.
   - sets retrieved_at on success

5. PII scrubbing at load time, over extracted_text, per architecture.md §9.1.
   Replace matches with "[redacted]", record the labels in RawDoc.pii_labels.
   Module-level PII_PATTERNS constant, with a comment pointing at P9 to
   consolidate with guardrails.

6. `raw_dir()` is a module-level function, not a bare CONFIG.RAW_DIR read.
   CONFIG is a frozen dataclass, so tests cannot monkeypatch an attribute on
   it; they redirect this seam instead.

7. `scrub_snapshots()` — off-line, in-place redaction of the cached HTML.
   See the Deviations table for why the fetch path must not do this.

8. `fetch_all(registry, *, force=False, delay_s=None) -> list[RawDoc]`
   - iterates enabled rows, sleeps CONFIG.FETCH_DELAY_S between requests
   - never raises. Collects all results.

9. tests/test_loaders.py — 17 tests, all with the network mocked:
   status transitions, 403->curl escalation, PII scrub, and its two
   false-positive controls (float fractions, and fund facts surviving).
```

**Verify**
```bash
python -m pytest tests/test_loaders.py -q
python -m src.loaders            # built-in report: status, http, chars, pii
```

**Acceptance criteria** — all met
- [x] 4 required tests pass; (c) proves no exception escapes
- [x] 17 loader tests + 15 registry tests = 32 passing
- [x] Live run: 11/11 sources usable, one line each, no traceback
- [x] 0 PAN / email / phone / aadhaar matches in any extracted text
- [x] `fetch_probe.py` deleted

**Common mistakes** — two of these were hit for real
- Cursor adds `playwright` to requirements to make Tier 3 work. **Reject** — §14 E11/E12 explicitly accepts skipping a source with a warning. Escalate to a human instead.
- Cursor raises on `requests.ConnectionError` inside `fetch_source`. That breaks `fetch_all`.
- **Hit:** An unguarded `\b\d{12}\b` Aadhaar pattern matches the 12-digit fraction of a return float like `53.830423188357`, and `90.670247196002` matches the mobile pattern. Redacting those silently corrupts performance data. Every digit-run pattern needs `(?<![\d.,])` and `(?![\d])`. `test_float_fractions_are_not_mistaken_for_pii` locks this in.
- **Hit:** Escalating every non-200 to curl. A dead URL then gets two transports' worth of timeout. Escalate on 403 only.
- **Hit:** Spelling trafilatura's kwarg `favour_recall` (v1.x) against v2.0, which takes `favor_recall`.

### Deviations from the original prompt

| Original | Shipped | Reason |
|---|---|---|
| trafilatura primary, `favour_precision` | BeautifulSoup on `<main>`, trafilatura fallback | ADR-11. trafilatura returned 1,233 chars from a 167 KB page and **zero** "expense ratio" occurrences on all five scheme pages. The original spec would have shipped a corpus unable to answer the most-asked question. |
| `headers={"User-Agent": ...}` | `CONFIG.BROWSER_HEADERS`, full browser set | ADR-12. Akamai fingerprints the TLS handshake, not the header. Bare requests gets 403 whatever it sends. |
| No transport tiering | requests → curl → skip | ADR-12. Only curl was served 200 during P2. |
| `FETCH_DELAY_S = 2.0` | `6.0` | Measured: 2.0s pacing lost most of the official corpus to burst throttling. |
| PII labels recorded in `RawDoc.error` | dedicated `pii_labels` tuple | `error` means "something went wrong". Overloading it made the run report unreadable. |
| Acceptance: no PII in `data/raw/*.txt` | scrub `extracted_text`; add `scrub_snapshots()` for the raw HTML | The raw snapshot is written **verbatim** so it stays re-extractable after a parser fix. Redacting the markup would make a cached copy useless for exactly the debugging it exists to support. `scrub_snapshots()` is the off-line path for the one case where a mangled copy is acceptable: committing `data/raw/`. It is destructive, so it is never implicit. |

### Carry-forward — these bind later phases

- **ADR-11:** P4 and P5 consume `extracted_text` from this loader. Do not
  reintroduce trafilatura as primary.
- **ADR-14:** P5's `Chunk` gains `block_kind` ∈ {section, faq, fact, table,
  prose}, and label/value pairs become atomic chunks. P2 found the min-SIP and
  TER facts render as bare label/value lines with **sibling** DOM elements, so a
  purely heading-aware chunker buries them in philosophy prose.
- **Known content gap:** `hdfc_request_statement_guide` extracts to 2,084 chars
  against a 2,000 threshold, and is a JS form ("enter your folio number") rather
  than a content document. It passes validation but carries no retrievable
  facts. Decide whether to keep it citable before P16 builds the golden set.

**Commit:** `feat: tiered fault-tolerant loader with load-time PII scrub`

---

## 7. P4 — Cleaner 🤖

**Goal:** Raw HTML → ordered blocks + flat text, with tables preserved and boilerplate gone.

**Depends on:** P3
**Files to create:** `src/cleaner.py`, `tests/test_cleaner.py`

**Read first:** `architecture.md` §7 S2, §6.3 (the `section` field depends on heading retention)

### Prompt Block — P4

```
Read architecture.md §7 S2, then write src/cleaner.py.

1. Frozen dataclasses:
     Block(kind: str, text: str, level: int)   # kind in
                                               # {heading, paragraph, list_item,
                                               #  table_row, footnote, fact}
     CleanDoc(source_id: str, url: str, blocks: list[Block], text: str,
              warnings: list[str])

   The `fact` kind is ADR-14 and is not optional decoration. It is the only
   thing that lets P5 emit an atomic "Min SIP: ₹500" chunk.

2. `clean(raw: RawDoc) -> CleanDoc`
   - Parse raw.raw_bytes with BeautifulSoup (lxml parser)
   - DROP: script, style, noscript, nav, footer, aside, header, and any element
     whose class or id matches: cookie, consent, banner, popup, modal,
     ad-, advert, newsletter, subscribe, related, trending, social, share
   - KEEP with structure: h1-h4 (as kind="heading" with the right level),
     p, li, th/td, figcaption
   - TABLES: convert every table to consecutive kind="table_row" blocks with
     text formatted as "cell | cell | cell". NEVER merge or drop a row. A
     row is atomic and must never be split (architecture.md §7 S3 rule).
   - FACTS (ADR-14): pair each recognised label with the value that follows it,
     as a kind="fact" block rendered "Label: value". The known labels are the
     ones the demo is judged on — min SIP, expense ratio / TER, exit load, risk
     / riskometer, AUM, NAV, lock-in, benchmark, and the fund's objective.
     P2 measured these in the real pages: the label and value are SIBLING
     elements, or consecutive bare lines, with no heading and no wrapper. So
     look ahead to the next non-empty leaf node, do not require a container.
     A label with no value following it is not a fact; skip it rather than
     pairing it with the wrong number.
   - Whitespace: collapse runs, strip, normalise NBSP to space, unify
     en/em dashes, normalise smart quotes to ASCII
   - Drop blocks whose text matches a bare mutual-fund legal disclaimer ONLY IF
     an identical or near-identical block appears elsewhere in the same doc.
     Reason: the ELSS three-year lock-in is sometimes stated inside disclaimer
     text and stripping it would destroy the answer. Emit a warning for every
     disclaimer block you drop, with its char length.
   - Truncate trailing boilerplate after a detected footer boundary
     (last-updated marker / "Contact Us" / address block), if present
   - Build `text` by joining blocks in order: headings as "#"*level + " " + text,
     table rows verbatim
   - If len(text) < CONFIG.MIN_EXTRACTED_CHARS, append a warning

3. `flatten(doc: CleanDoc) -> str` returns doc.text.

4. `tests/test_cleaner.py` with small INLINE HTML fixtures — no network:
   - a fixture with a cookie banner and a nav: assert those strings are absent
     from the output
   - a fixture with a 3-column table: assert all 9 cells survive and produce
     3 table_row blocks, in order
   - a fixture with a duplicate disclaimer: assert the first is dropped and a
     warning emitted
   - a fixture where the lock-in phrase appears inside a disclaimer: assert the
     phrase SURVIVES
   - a fixture with h1 > h2 > h3: assert heading levels are 1, 2, 3
```

**Verify**
```bash
python -m pytest tests/test_cleaner.py -v
python -c "
from src.cleaner import clean
from src.loaders import fetch_source
from src.sources import load_registry
r = load_registry('data/sources.csv')
row = [x for x in r.rows.values() if 'large_cap' in x.scheme_category and not x.discover_only][0]
d = clean(fetch_source(row))
print(f'blocks={len(d.blocks)} chars={len(d.text)} warnings={d.warnings}')
import collections; print(collections.Counter(b.kind for b in d.blocks))"
```

**Acceptance criteria**
- [ ] 5 tests pass, especially the lock-in-survives-disclaimer one
- [ ] Counter shows `table_row` > 0 for a real page — if zero, table preservation is broken and the corpus has lost its fee data
- [ ] No cookie/nav strings in the output for any real page

**Common mistakes**
- Cursor uses `trafilatura` output only and discards structure, so `section` metadata can never be populated. `architecture.md` §6.3 requires heading-aware extraction.
- Cursor strips all disclaimers unconditionally → ELSS lock-in disappears from the corpus. This is exactly the bug the 4th test exists to catch.

**Commit:** `feat: structure-preserving cleaner, tables kept, disclaimer-aware`

---

## 8. P5 — Chunker 🤖🤝

**Goal:** The chunking strategy from P2, implemented, with `chunks.txt` as a real deliverable.

**Depends on:** P4
**Files to create:** `src/chunker.py`, `tests/test_chunker.py`, `data/chunks.txt` (generated)

**Read first:** `architecture.md` §7 S3 + §6.3, `docs/chunking_proposal.md` from P2

### Prompt Block — P5

```
Read architecture.md §7 S3 and §6.3, plus docs/chunking_proposal.md that you
wrote in an earlier phase. Implement src/chunker.py to match THAT proposal.

1. Frozen dataclass `Chunk` with EXACTLY these 18 fields, in this order:
   chunk_id, text, source_id, source_title, url, authority, discover_only,
   scheme, scheme_category, doc_type, section, faq_question, chunk_index,
   token_count, char_start, char_end, retrieved_at, block_kind

   Note: retrieved_at is carried on the Chunk as well. Do not drop it.
   block_kind ∈ {section, faq, fact, table, prose} is ADR-14. The original spec
   said "15 fields" and then listed 17; the count is what the code must match.

2. `chunk_id = sha256(f"{source_id}|{chunk_index}|{text}").hexdigest()[:32]`
   Deterministic. Never a uuid. (architecture.md ADR-09)

3. `chunk_document(doc: CleanDoc, row: SourceRow, cfg) -> list[Chunk]`
   Algorithm:
   - Walk blocks in order into a buffer
   - On a heading with level <= 2: FLUSH the buffer, start a new one, and
     prefix the buffer with the heading rendered as "#"*level + " " + text.
     This preserves section boundaries.
   - table_row blocks are ATOMIC: never split one. Pack rows into the buffer
     until the next heading or a body overflow, then flush.
   - fact blocks are ATOMIC too (ADR-14). A label and its value sit in SIBLING
     DOM elements, so the value has no heading of its own to anchor to. Emit
     "Min SIP: ₹500" as its own chunk rather than letting it drift into the
     surrounding philosophy prose.
   - a question line whose next block is prose becomes ONE chunk with
     block_kind="faq" and faq_question set to that question. S2 has no `faq`
     block kind, so detect it here. Keeping the question inside the chunk text
     matters for retrieval: the user asks in question form, so the question is
     usually the better embedding. Never leave faq_question as None — P7 needs
     a string for Chroma metadata.
   - block_kind on each chunk is the kind of its dominant block, which is what
     makes the table-coverage and fact-capture assertions below checkable.
   - Flush when the body reaches cfg.CHUNK_SIZE_WP word-pieces, carrying the
     last cfg.CHUNK_OVERLAP_WP word-pieces as overlap into the next buffer.
   - Each chunk's text is self-contained: heading prefix + body + a footer line
     "Source: <url> (retrieved <retrieved_at>)"
   - section = the most recent heading text seen
   - chunk_index is 0-based, per source_id
   - char_start / char_end are offsets into doc.text
   - token_count is a WHITESPACE word count of the final text, for budget
     assertion. A word-piece estimator would need the tokenizer; a word count
     is a safe upper bound per word, so a whitespace count exceeding 256 means
     the real count definitely does. Keep the budget conservative so this holds.

4. `assert_chunk_invariants(chunks, source_ids)` — raise ChunkInvariantError on:
   a. any token_count > 256                                  (I5, ADR-01)
   b. duplicate chunk_id
   c. any chunk whose text mentions a different source_id's url  (I4 / R4)
   d. zero chunks for a source that had status "ok"
   e. table coverage: >= 80% of the source's table_row block texts appear in
      at least one chunk. Report the exact percentage in the error.
      This is the highest-value regression guard in the whole pipeline —
      if fee tables are being dropped, answers will be wrong.
   f. fact coverage: every fact block text in a source appears verbatim in at
      least one chunk (ADR-14). The table check above would still pass while
      every min-SIP and TER value was buried, because those facts are not
      table rows. Report the exact percentage.

5. `write_chunks_txt(chunks, path)` — the exact format in architecture.md
   §17.2, with ==== rules, labelled fields, and a ---- separator before the
   body. Must be stable and diffable across re-runs.

6. `tests/test_chunker.py` — build a synthetic CleanDoc by hand (no network)
   and test: heading boundaries produce separate chunks; a table row is never
   split; overlap is carried; the 256 assertion fires on an oversized chunk;
   chunk_id is stable across two identical calls.
```

**Verify**
```bash
python -m pytest tests/test_chunker.py -v
python -c "
from src.config import CONFIG
from src.sources import load_registry
from src.loaders import fetch_source
from src.cleaner import clean
from src.chunker import chunk_document, assert_chunk_invariants, write_chunks_txt
r = load_registry('data/sources.csv'); allc = []; ids = set()
for row in r.rows.values():
    if not row.enabled: continue
    raw = fetch_source(row)
    if raw.status != 'ok': print('SKIP', row.source_id, raw.status); continue
    d = clean(raw)
    if not d.blocks: continue
    cs = chunk_document(d, row, CONFIG); allc += cs; ids.add(row.source_id)
assert_chunk_invariants(allc, ids)
print(f'chunks={len(allc)} max_tokens={max(c.token_count for c in allc)}')
write_chunks_txt(allc, CONFIG.CHUNKS_TXT); print('wrote', CONFIG.CHUNKS_TXT)"
head -30 data/chunks.txt
```

**Acceptance criteria**
- [ ] 6 tests pass
- [ ] `max_tokens ≤ 256` prints
- [ ] `table coverage ≥ 80%` — the assertion passing means fee tables survived
- [ ] `fact coverage = 100%` — every min-SIP and TER value is individually retrievable
- [ ] `data/chunks.txt` exists, is > 100 lines, and reads sensibly by eye
- [ ] Spot-check: grep `chunks.txt` for the ELSS lock-in phrase and the Large Cap expense ratio. **Both must be present.** If they're not, stop — the corpus or chunker is broken.

**Common mistakes**
- Cursor reintroduces a 450-token chunk size. `all-MiniLM-L6-v2` truncates at 256 word-pieces; the tail of every large chunk is silently invisible to search. This is ADR-01 and the single highest-impact bug available in this build.
- Cursor drops `retrieved_at` because it "belongs to the source". It must be denormalised onto every chunk — Q9 step 8 reads it per-chunk.
- Cursor lets a table row straddle a flush boundary.
- Cursor chunks only by heading and buries the label/value facts. P2 found them
  with no heading and in sibling DOM elements, so heading-aware chunking alone
  loses exactly the numbers the demo is judged on.

**Commit:** `feat: heading-aware chunker with token budget and table-coverage invariant`

---

## 9. P6 — Embedder 🤖

**Goal:** One module, one model, used by both documents and queries.

**Depends on:** P5
**Files to create:** `src/embedder.py`, `tests/test_embedder.py`

**Read first:** `architecture.md` §7 S4 (the MiniLM pragma comment), §19 ADR-08

### Prompt Block — P6

```
Read architecture.md §7 S4, then write src/embedder.py.

1. A single module-level, lazily-initialised, cached SentenceTransformer
   (use functools.lru_cache on a getter function, NOT a module-level
   instantiation — importing this module must not load weights, because
   pytest and CLI both import it).

2. The ONLY public function:
     encode(texts: list[str], *, is_query: bool = False) -> np.ndarray
   - batch_size=32
   - normalize_embeddings=True
   - convert_to_numpy=True
   - returns .astype("float32") of shape (len(texts), CONFIG.EMBED_DIM)
   - `is_query` is accepted for call-site readability and future use but
     currently makes NO difference. Add a docstring saying so. Do NOT
     implement the "query:"/"passage:" prefix convention — architecture.md
     §7 S4 explains it hurts general-purpose retrieval here.

3. `sanity_check(vectors) -> dict` returning {"count", "dim", "mean_norm"}.
   Used by ingest to log that normalisation is actually happening.

4. `tests/test_embedder.py` — marked slow, one real encode of 2 short strings:
   assert shape == (2, 384)
   assert all L2 norms are within 0.01 of 1.0
   assert identical input gives an identical vector
   Add `pytest.mark.slow` and register the marker in tests/conftest.py.
```

**Verify**
```bash
python -m pytest tests/test_embedder.py -v -m slow
python -c "
from src.embedder import encode, sanity_check
v = encode(['exit load is 1% within 12 months','HDFC Large Cap Fund'])
print(sanity_check(v))"
```

**Acceptance criteria**
- [ ] `dim == 384`
- [ ] `mean_norm` within 0.01 of 1.0 — if not, normalisation is broken and the grounding threshold τ will be meaningless (ADR-08)
- [ ] `import src.embedder` is fast (no weight load at import)

**Common mistakes**
- Cursor adds a `cross-encoder` / `bge-reranker` import "for reranking quality". **Reject** — ADR-07, and it breaks the CPU-only constraint.
- Cursor sets `normalize_embeddings=False` for speed. That invalidates every cosine threshold.

**Commit:** `feat: single cached MiniLM embedder, normalised output`

---

## 10. P7 — Store (ChromaDB) 🤖

**Goal:** Persistent vector store with the metadata-coercion layer and the store-missing contract.

**Depends on:** P6
**Files to create:** `src/store.py`, `tests/test_store.py`

**Read first:** `architecture.md` §7 S5, §6.4 (incl. the `chroma_safe()` coercion table), §14 E1

### Prompt Block — P7

```
Read architecture.md §7 S5 and §6.4, then write src/store.py.

1. `@lru_cache`ed `get_client() -> chromadb.PersistentClient` with
   path=CONFIG.CHROMA_DIR.
2. `get_collection() -> Collection` using
   get_or_create_collection(name=CONFIG.COLLECTION_NAME,
                            metadata={"hnsw:space": "cosine",
                                      "schema_version": CONFIG.COLLECTION_SCHEMA_VERSION})
   cached.
3. `StoreMissing(Exception)` and `require_store() -> None`:
   - if CHROMA_DIR does not exist, or the collection does not exist, or
     collection.count() == 0 -> raise StoreMissing with a message that includes
     the literal command "python -m src.ingest"
   - if collection metadata schema_version != CONFIG.COLLECTION_SCHEMA_VERSION,
     raise StoreMissing saying a rebuild is required (this is the ADR-09
     rebuild path)

4. `chroma_safe(chunk: Chunk) -> dict` — coerce to Chroma's allowed metadata
   types (str | int | float | bool ONLY). None becomes "". Bool must stay bool,
   not "True" as a string. Add a module docstring explaining that chromadb
   rejects None, lists and dicts in metadata and that this function is the
   single coercion point.

5. `upsert_chunks(chunks, vectors, *, force=False) -> int`:
   - if force, delete the collection first and recreate
   - upsert(ids, documents, embeddings, metadatas)
   - verify collection.count() >= len(chunks), else raise
   - return the count

6. `query_store(qvec, *, where, n_results) -> list[Hit]` where Hit is a frozen
   dataclass: (chunk_id, text, metadata, score, embedding) with embedding pulled
   from the returned embeddings list.

7. `count() -> int`

8. `tests/test_store.py` using a tmp_path monkeypatched CHROMA_DIR (never touch
   the real chroma_db/): test upsert->count, test upsert is idempotent (same
   chunk_id twice -> count unchanged), test require_store raises on an empty
   dir, test chroma_safe converts None -> "" and keeps bools as bools.
```

**Verify**
```bash
python -m pytest tests/test_store.py -v
```

**Acceptance criteria**
- [ ] 4 tests pass
- [ ] Idempotency test proves identical chunk_ids don't inflate the count
- [ ] The real `chroma_db/` is still empty (`ls chroma_db` fails or is absent) — tests used tmp_path

**Common mistakes**
- Cursor stores `None` for `faq_question` in metadata → chromadb raises. That's what `chroma_safe()` is for; don't skip it.
- Cursor creates the client at import time. P15 depends on import being cheap.

**Commit:** `feat: persistent Chroma store with metadata coercion and StoreMissing contract`

### As built (P7, P8)

`src/store.py`, `src/ingest.py`. Store tests 17, manifest tests 10, full suite 112.

- `chroma_safe()` coerces `None` to `""` and keeps bools as bools. A stringly-typed
  `discover_only` would break every Q5 `where` filter, and `None` makes chromadb raise.
- The client is built on first use, never at import: a bare `import src.store` creates
  no `chroma_db/` and costs no HNSW load. P15 depends on this.
- `require_store()` also rejects a `schema_version` mismatch and names `--force` in the
  message, so a stale store fails loudly instead of mixing schemas (ADR-09).
- `build_manifest(...)` is pure; the manifest shape is tested with no network or Chroma.
- Chunk invariants run over the whole corpus, not per source, so one source's text
  leaking into another's chunk is caught.
- `data/embeddings.txt` is written by S4 alongside `data/chunks.txt`, so the preview
  cannot describe a corpus the store no longer holds.

**P4 fix found while verifying P7.** 18.2% of cleaned blocks (33-37% on official scheme
pages) were exact duplicates. The walk visits every `div`/`span`, so a wrapper and the
element nested inside it both yielded the same paragraph; one HDFC sentence was emitted
7 times, and each copy took its own top-k slot. `clean()` now drops a repeated block
text, keeping the first occurrence, and reports the count in `warnings`. Headings are
exempt (a repeat marks a repeated section) and disclaimers are exempt (the dedicated
pass keeps the *last* copy and matches on a normalised prefix). Corpus: **538 -> 349
chunks**; top-10 for a single query went from 3 copies of 1 chunk to 10 distinct.
All 11 measured facts still present. Duplicate-only dedup: near-duplicates are NOT
collapsed, so the Groww nav menu still competes for slots (see open items).

**Live numbers:** 349 chunks, 384-dim, `mean_norm=1.000000`, 5.3 MB
`chroma_db/chroma.sqlite3`, ingest 67s (NFR-2 < 3 min). Manifest warns 14x, 11 of them
the new duplicate-collapse line.

---

## 11. P8 — Ingest Orchestrator 🤖

**Goal:** `python -m src.ingest` runs S1→S5 end to end and writes a manifest.

**Depends on:** P7
**Files to create:** `src/ingest.py`, `tests/test_ingest_manifest.py`

**Read first:** `architecture.md` §7 (all of S1–S5), §6.2 (manifest schema), §20

### Prompt Block — P8

```
Read architecture.md §7 (S1 through S5) and §6.2, then write src/ingest.py.

CLI:  python -m src.ingest [--force] [--only <source_id>]

main() orchestrates and logs a line per stage:
  S1 LOAD   -> load_registry + fetch_all
  S2 CLEAN  -> clean each RawDoc with status "ok"; warn and skip others
  S3 CHUNK  -> chunk_document per doc; assert_chunk_invariants over the whole
               corpus (not per source) so cross-source bleed is caught
  S4 EMBED  -> src.embedder.encode on all chunk texts in one batched call;
               log sanity_check
  S5 STORE  -> src.store.upsert_chunks
  then write data/ingest_manifest.json per the schema in architecture.md §6.2

Requirements:
- run_id = ISO timestamp
- The manifest must include the model block (embed, dim, max_seq_length) and the
  chunking block (size_wp, overlap_wp, strategy) so a reader can tell what
  produced the store
- Per-source entries: source_id, status, http, bytes, extracted_chars, chunks,
  retrieved_at
- A `warnings` list, e.g. a source whose extracted_chars is below
  CONFIG.MIN_EXTRACTED_CHARS
- Also write data/chunks.txt
- Exit code 0 on success, 1 if zero chunks were produced at all
- --only <source_id> restricts to one source (useful for iterating)
- --force deletes the collection before writing
- NEVER let a single source's failure abort the run. Only a total failure of the
  pipeline exits non-zero.
- Print a final summary: sources ok / short / failed, total chunks, total time.

tests/test_ingest_manifest.py: test the manifest dict shape produced by a
pure `build_manifest(...)` helper, with fake inputs, no network, no Chroma.
```

**Verify**
```bash
rm -rf chroma_db
python -m src.ingest
echo "exit=$?"
python -c "
import json; m = json.load(open('data/ingest_manifest.json'))
print(m['collection']); print(m['model']); print(m['chunking'])
print('warnings:', m['warnings'])"
ls -la data/chunks.txt
```

**Acceptance criteria**
- [ ] `exit=0`
- [ ] `collection.count_after` equals the number of entries summed
- [ ] `warnings` is a list (possibly empty) — and you have *read* it. A non-empty list means R1 is biting; go back to P3.
- [ ] `data/chunks.txt` regenerated
- [ ] Total wall time < 3 min (NFR-2)

**Common mistakes**
- Cursor makes any source failure fatal. Explicitly forbidden — architecture.md §7 S1.
- Cursor leaves the manifest out or makes it optional.

**Commit:** `feat: ingest CLI running S1-S5 with manifest and chunks.txt output`

---

## 12. P9 — Guardrails 🤖

**Goal:** PII scanning, intent classification, and the fixed refusal templates. **The safety core of the system.**

**Depends on:** P8
**Files to create:** `src/guardrails.py`, `tests/test_guardrails.py`

**Read first:** `architecture.md` §9.1 (PII table), §9.2 (templates — copy the strings verbatim), §8 Q1–Q2, §11 (classifier prompt)

### Prompt Block — P9

```
Read architecture.md §9.1, §9.2 and §8 Q1-Q2, then write src/guardrails.py.
This module is the safety core. Be exhaustive and conservative.

1. PII_PATTERNS: a list of (label, compiled_regex) for exactly these labels —
   pan, aadhaar, account, demat, ifsc, email, phone_in, otp — with the regexes
   from architecture.md §9.1. `otp` is contextual: a 4-6 digit number only
   counts when the surrounding ~30 characters contain otp|code|pin|verification.
   Write a module docstring stating the deliberate false-positive trade-off:
   blocking a harmless question costs one message, leaking PII is unacceptable.

2. PIIHit(frozen dataclass): label, span_start, span_end, masked. `masked` is a
   fixed-length placeholder that does NOT reveal the value or its length.

3. scan_pii(text) -> PIIHit | None   — first match wins.
   scrub_pii(text) -> str           — replaces every match with "[redacted]".
   The scrubbed string must never contain the original digits.

4. Intent(frozen dataclass): kind, layer, matched, confidence
   kind in {PII, ADVICE, OUT_OF_SCOPE, FACTUAL}

5. `ADVICE_PATTERNS`: compiled regexes for should i, which is better, is it
   good, worth it, recommend, suggest, can i sell, best fund, portfolio,
   allocate, worth buying, is it safe, which one should.

6. `PERFORMANCE_PATTERNS`: returns, performance, CAGR, how much will i earn,
   how much can i make, outperform, compare performance, expected return.
   Used for the performance short-circuit (FR-5.3).

7. classify(query, *, use_llm=True) -> Intent, with STRICT precedence:
     1. scan_pii hit                          -> PII
     2. ADVICE_PATTERNS match                 -> ADVICE   (layer="rule")
     3. PERFORMANCE_PATTERNS match            -> ADVICE   (layer="rule",
                                                    with matched=["performance"])
     4. OFF_CORPUS names (Parag, Axis, Mirae, Kotak, SBI, ICICI, Nifty, Sensex,
        gold, debt fund, etc.)                 -> OUT_OF_SCOPE (layer="rule")
     5. use_llm and not yet decided           -> one Groq call, the classifier
                                                    prompt from architecture.md §11
     6. default                               -> FACTUAL
   Document in a docstring that ADVICE MUST be checked before OUT_OF_SCOPE,
   with the reason: "Should I buy HDFC ELSS?" contains a corpus scheme AND an
   advice request, and must refuse as advice, not answer as a scheme question.

8. The refusal constants REFUSAL_PII, REFUSAL_ADVICE, REFUSAL_OUT_OF_SCOPE,
   REFUSAL_PERFORMANCE, REFUSAL_NO_GROUNDING — copy the strings VERBATIM from
   architecture.md §9.2, including the markdown link syntax and the
   {link}/{factsheet_url} placeholders. Add a `render(template, **kw)` helper
   that fills placeholders and raises on an unknown key.

9. `redact_for_trace(query, pii_hit) -> str` so debug traces can never leak.

10. tests/test_guardrails.py — this file matters more than most:
    - 10 PII fixtures, one per label, each asserted to be caught
    - 6 safe fixtures asserted NOT to trip (e.g. "expense ratio of HDFC Large
      Cap", "18 months", "0.52%", "Nifty 50 TRI") — this guards against the
      account pattern over-firing
    - scrub_pii removes a PAN and the output contains no substring of it
    - precedence: "Should I buy HDFC ELSS?" -> ADVICE, not OUT_OF_SCOPE, not
      FACTUAL
    - precedence: a PAN in an otherwise factual question -> PII
    - "What are the returns of HDFC Large Cap?" -> ADVICE with
      matched containing "performance"
    - every REFUSAL_* constant is a str, non-empty, and contains no digits that
      look like a financial figure
```

**Verify**
```bash
python -m pytest tests/test_guardrails.py -v
python -c "
from src.guardrails import classify, REFUSAL_ADVICE
for q in ['expense ratio of HDFC Large Cap','Should I buy HDFC ELSS?',
          'What are the returns of HDFC Small Cap?','my PAN is ABCDE1234F',
          'Is Parag Parag Flexi Cap good?','What is the exit load?']:
    i = classify(q, use_llm=False); print(f'{i.kind:13} {i.layer:5} {q}')"
```

**Acceptance criteria**
- [ ] All guardrail tests pass — **no exceptions, no skips**
- [ ] The 6 classification probes print exactly:
  ```
  FACTUAL      rule  expense ratio of HDFC Large Cap
  ADVICE       rule  Should I buy HDFC ELSS?
  ADVICE       rule  What are the returns of HDFC Small Cap?
  PII          rule  my PAN is ABCDE1234F
  OUT_OF_SCOPE rule  Is Parag Parag Flexi Cap good?
  FACTUAL      rule  What is the exit load?
  ```
  If any line differs, **stop and fix the precedence.**
- [ ] The 6 safe fixtures pass — an over-eager account pattern blocks real questions

**Common mistakes**
- Cursor puts OUT_OF_SCOPE before ADVICE. Breaks the "Should I buy HDFC ELSS?" case, which is one of the three manual demo checks in PRD §8.3.
- Cursor paraphrases the refusal constants instead of copying them verbatim. The brief specifies a "polite, facts-only message"; the exact text is reviewed.
- Cursor's `account` pattern `\b\d+\b` blocks "0.52" and every fee question. The 6 safe fixtures exist precisely for this.

**Commit:** `feat: PII scan, layered intent classifier, fixed refusal templates`

### As built (P9)

`src/guardrails.py`, 92 tests, full suite 239. The six acceptance probes print
exactly the specified `kind`/`layer` pairs.

- `PII_PATTERNS` is ordered most-specific-first, because "first match wins" and
  a 16-digit demat number would otherwise be reported as a 9-18 digit account
  number. The order in architecture.md 9.1's table cannot be used as-is.
- The `otp` context window is `[^0-9]{0,25}`, not `\W{0,20}`: a `\W` window cannot
  cross the letters in "the otp **is** 448213".
- `account` is 9-18 digits, so a 9-digit order number is blocked. That is
  documented over-blocking, not an oversight, and a test now pins it so the
  behaviour is a decision rather than a surprise.
- A rule-layer `FACTUAL` reports `layer="rule"`. `layer="default"` is reserved
  for the LLM fallback, where no layer actually decided. Collapsing the two made
  the probes print `default` and hid whether the rules had run.
- `_classify_llm` fails safe to `FACTUAL` on a missing key, missing `openai`
  package or transport error. It is a mitigation (R8), not a gate: a classifier
  that crashes must not become a denial of service.
- `render()` raises on unknown keys as well as missing ones. `str.format` ignores
  extra kwargs silently, so a caller could pass a URL the template never uses and
  believe it had been applied.

**Advice-pattern gaps found by running P9 against P10's boundary cases.** The
keyword list in the phase brief is a floor, and two recommendation requests
phrased themselves around it. Both reached the model as `FACTUAL` and were
answered, which is a C3 violation:

| query | before | after |
|---|---|---|
| `is HDFC ELSS a good fund` | FACTUAL, **grounded at 0.749** | ADVICE |
| `best stocks to buy tomorrow` | FACTUAL, grounded at 0.354 | ADVICE |
| `which fund should I invest in` | FACTUAL | ADVICE |

Added: `good (fund|scheme|investment|option|choice)`, `best ... buy|invest|fund|scheme`,
`which (fund|scheme|mutual fund|one)`, `worth (it|buying|investing)`. A companion
test asserts the six real questions these could have swallowed are still FACTUAL.

This corrects a claim made in the P10 note below, which assumed P9 would catch
`best stocks to buy tomorrow`. It did not, until these patterns were added.

---

## 13. P10 — Retriever 🤖

**Goal:** Q3–Q7. Query → grounded hits, or an honest failure.

**Depends on:** P9
**Files to create:** `src/retriever.py`, `tests/test_retriever.py`

**Read first:** `architecture.md` §8 Q3–Q7, §10 (retrieval decision table)

### Prompt Block — P10

```
Read architecture.md §8 Q3 through Q7 and §10, then write src/retriever.py.

1. `SCHEME_ALIASES`: dict mapping lowercased alias substrings to
   scheme_category, per architecture.md §8 Q3:
     "large cap"->large_cap, "flexi cap"->flexi_cap, "hdfc equity"->flexi_cap,
     "elss"->elss, "tax saver"->elss, "small cap"->small_cap,
     "balanced advantage"->hybrid
   `resolve_scheme(query) -> str | None` does longest-alias-first substring
   matching on the lowercased query, so "small cap" wins over a bare "cap".

2. `mMR(cands, k, lam)` — implement the exact MMR pseudocode from
   architecture.md §8 Q6 in pure numpy. No extra model, no network.
   cands is a list of Hit. similarity is cosine between embedding vectors.

3. `retrieve(query, *, debug=False) -> RetrievalResult` with
   RetrievalResult(hits, filtered_by, max_score, grounded, candidates,
                   after_mmr)
   Steps:
     a. qvec = src.embedder.encode([query], is_query=True)[0]
     b. scheme_id = resolve_scheme(query)
     c. where = {"discover_only": False}                    # ALWAYS, I1/C1
        if scheme_id: where = {"$and": [where, {"scheme_category": scheme_id}]}
        filtered_by = scheme_id or None
     d. res = src.store.query_store(qvec, where=where,
                                    n_results=CONFIG.RETRIEVE_POOL)
        RETRIEVE_POOL is 20, not TOP_K — the pool is the MMR candidate set
     e. hits = mmr(res, k=CONFIG.TOP_K, lam=CONFIG.MMR_LAMBDA)
     f. GROUNDING GATE: max_score = max(h.score for h in hits) or 0.0
        if max_score < CONFIG.GROUNDING_THRESHOLD:
           - check whether any of the PRE-MMR pool candidates clears the
             threshold; if so promote the best one to the top of `hits`
             (guards against one junk chunk shadowing good ones)
           - grounded = False
        else: grounded = True
     g. return the result. This function does NOT decide what to do about
        ungrounded results — that is Q7's caller (the pipeline). It reports.

4. A module docstring noting that CONFIG.GROUNDING_THRESHOLD is UNTUNED and
   calibrated in P17, and that τ is only meaningful because embeddings are
   L2-normalised (ADR-08).

5. tests/test_retriever.py: a fake store via monkeypatch.
   - resolve_scheme alias tests including longest-match precedence
   - mmr returns exactly k items, no duplicates, from a synthetic 20-item pool
   - mmr prefers a diverse set over 5 near-identical items
   - retrieve builds a where clause containing discover_only False ALWAYS,
     even with no scheme in the query
   - retrieve reports grounded=False when all scores are below threshold
   - retrieve promotes a pre-MMR candidate that clears the threshold when the
     top-1 does not
```

**Verify**
```bash
python -m pytest tests/test_retriever.py -v
python -c "
from src.retriever import retrieve
for q in ['expense ratio of HDFC Large Cap','exit load on HDFC Flexi Cap',
          'lock-in period HDFC ELSS','benchmark of Balanced Advantage Fund',
          'quantum physics entanglement']:
    r = retrieve(q)
    print(f'{q[:42]:44} scheme={str(r.filtered_by):10} max={r.max_score:.3f} grounded={r.grounded}')
    for h in r.hits[:2]:
        print(f'    {h.score:.3f} {h.metadata[\"section\"][:30]:32} {h.text[:60]}')"
```

**Acceptance criteria**
- [ ] Retriever tests pass
- [ ] All 4 in-corpus queries print `grounded=True`
- [ ] The out-of-corpus query either prints `grounded=False` **or** is flagged in P17 for τ calibration. Either way you now know where τ sits.
- [ ] The printed `where` always includes `discover_only: False`

**Common mistakes**
- Cursor uses `n_results=CONFIG.TOP_K`, making MMR a no-op. The pool must be 20.
- Cursor drops the `discover_only=False` filter when no scheme is resolved, so aggregator chunks leak into retrieval. They must be retrievable-but-never-citable only via the Q9 citation check; architecture.md §8 Q5 makes the filter unconditional.
- Cursor raises on an empty result set instead of returning `grounded=False`.

**Commit:** `feat: retriever with scheme filter, MMR re-rank, grounding gate`

### As built (P10)

`src/retriever.py`, 35 tests, full suite 147. P9 is still unbuilt, but P10 imports
nothing from it -- the alias table is specified inline -- so the phase stands alone.

- `where` always carries `{"discover_only": False}`, wrapped in `$and` with the
  scheme filter when one resolves. Dropping it in the scheme branch is the
  documented mistake, and there is a test for exactly that.
- Pool is `RETRIEVE_POOL=20` against `TOP_K=5`; a test asserts 20 > 5, since
  fetching only k makes MMR a no-op.
- Empty pool returns `grounded=False` rather than raising.
- `resolve_scheme` sorts by `(-len(alias), position)`, so the longest surface form
  wins and "small cap vs large cap" resolves to the subject the reader led with.

**Two findings, both P17 inputs.**

1. **The Q6 MMR pseudocode has an inverted penalty.** It subtracts
   `1 - cos_sim`, so an item *identical* to something already selected scores a
   penalty of 0 and the most novel item is penalised hardest -- the opposite of
   the section's own acceptance test. Measured on 5 identical + 4 unrelated
   chunks: the written formula returns all 5 identical chunks; standard MMR
   (Carbonell & Goldstein) returns 5 distinct. This implementation penalises
   similarity directly. The architecture snippet should be corrected to match;
   it is left as-is only so the deviation stays visible in review.

2. **The Q7 promote branch is unreachable, and tau=0.35 does not separate.**
   MMR's first pick is always the max-relevance item, so if any pool candidate
   clears tau the selected top-1 does too and `max_score` already passes. The
   guard defends a future MMR, not this one; its test stubs `mmr` to reach it.

   Measured over 10 in-corpus and 6 out-of-corpus queries:

   | | min | median | max |
   |---|---|---|---|
   | in-corpus (10) | **0.311** | 0.652 | 0.814 |
   | out-of-corpus (6) | 0.020 | 0.169 | **0.354** |

   The classes overlap by 0.044, so no single threshold separates this set.
   The two boundary cases are informative rather than arbitrary:

   - "direct vs regular plan" scores 0.311 and would be refused. The corpus has
     the phrase only inside Groww nav chrome, which `discover_only=False` filters
     out, so this is closer to a real corpus gap than a threshold error.
   - "best stocks to buy tomorrow" scores 0.354 and would be **answered**,
     matching "About HDFC Small Cap Fund" on investment vocabulary alone. The
     retriever is not the right place to catch it: P9's ADVICE layer refuses it
     before Q3. That defence is currently unbuilt.

   tau=0.35 also sits below the in-corpus median by a wide margin, so it is
   permissive rather than strict. P17 should plot these distributions over the
   full golden set before fixing a value.

**Model drift, found while smoke-testing the Groq key.** `architecture.md` Q8 and
the config default both pin `llama-3.1-8b-instant`. That model no longer exists on
Groq -- a valid key returns `404 model_not_found`, which is easy to misread as a
bad key because auth succeeds and only the model is rejected.

Models actually reachable from the tested key:

| model | `response_format=json_object` | note |
|---|---|---|
| `openai/gpt-oss-120b` | works, but needs `max_tokens` 1024 | reasoning model: 220 is consumed before any JSON is emitted |
| `openai/gpt-oss-20b` | fails at 220 | same reason |
| `qwen/qwen3.8-27b` | **works at the spec's 220** | set as the default |

`qwen/qwen3.8-27b` is the default because Q8 depends on JSON mode and
`LLM_MAX_TOKENS=220` is a real budget, not a suggestion. Verified end to end:
741 prompt tokens in, valid JSON out, 1.2s, and the answer was verbatim from the
retrieved chunk (1.00% within 1 year, NIL after) with no invented figures.

---

## 14. P11 — Prompts + Generator 🤖🤝

**Goal:** The Groq call, in JSON mode, with the prompt text in one auditable file.

**Depends on:** P10
**Files to create:** `src/prompts.py`, `src/generator.py`, `tests/test_generator.py`

**Read first:** `architecture.md` §11 (both prompt texts — copy verbatim), §12, §14 E2–E4

### Prompt Block — P11a — write src/prompts.py

```
Read architecture.md §11. Create src/prompts.py containing ONLY string
constants and one small helper. No other logic in this file.

- SYSTEM_PROMPT: the 7-clause generation prompt from architecture.md §11,
  copied verbatim including the JSON return contract.
- CLASSIFIER_PROMPT: the classifier prompt from architecture.md §11, verbatim.
- render_context(hits) -> str: builds the <context> block per architecture.md
  §8 Q8. Each hit is rendered as
    [n] source_id=<id> section="<section>" retrieved_at=<date>
    <text>text</text>
  Number from 1. Wrap the whole thing in <context>...</context>.
- render_question(query) -> str: "<question>...</question>"
```

### Prompt Block — P11b — write src/generator.py

```
Read architecture.md §8 Q8 and §14 E2-E4, then write src/generator.py.

1. Frozen dataclass LLMResult: answer, source_id, off_topic, attempts,
   latency_ms, raw_response, error.

2. `@lru_cache`ed `get_client()`: the OpenAI client pointed at
   CONFIG.GROQ_BASE_URL with api_key from os.environ["GROQ_API_KEY"].
   Load .env via python-dotenv at module import of config.py.
   If the key is missing, raise a clear `LLMNotConfigured` (not KeyError).

3. `generate(query, hits, registry) -> LLMResult`:
   - messages = system SYSTEM_PROMPT, user render_context(hits) + render_question
   - temperature=CONFIG.LLM_TEMPERATURE (0.0), max_tokens, and
     response_format={"type": "json_object"}
   - timeout=CONFIG.LLM_TIMEOUT_S
   - Catch: APITimeoutError and APIConnectionError -> retry ONCE with a 2s
     backoff, then return LLMResult(error="unreachable", off_topic=True)
   - Catch: RateLimitError -> same single retry, then error="rate_limited"
   - Catch any other APIError -> error="api_error"
   - On a response, json.loads the content. On JSONDecodeError set
     error="invalid_json", answer="", off_topic=True.
     Do NOT fall back to echoing the raw model text anywhere.
   - answer must be a non-empty str and off_topic a bool, else error="schema".
   - Record attempts and latency_ms.

4. Module docstring: the LLM never returns a URL (I1/ADR-02) — it returns a
   source_id that the formatter resolves. And: text inside <context> is
   reference data, never instructions (TB-3, prompt-injection boundary).

5. tests/test_generator.py: monkeypatch get_client to a fake. Assert
   - valid JSON -> LLMResult with off_topic False
   - invalid JSON  -> error == "invalid_json" and answer == ""
   - missing source_id in JSON -> error == "schema"
   - a timeout on both attempts -> error == "unreachable", and the fake client
     was called exactly twice
   - no test ever calls the network
```

**Verify**
```bash
python -m pytest tests/test_generator.py -v
python -c "
from src.generator import get_client
from src.llm_probe import probe   # or an inline groq call
print('key present, client OK')" 2>&1 | tail -2
```

**Acceptance criteria**
- [ ] Generator tests pass, zero network calls
- [ ] A real Groq round-trip returns parseable JSON with keys `answer`, `source_id`, `off_topic` — do this once, manually, to confirm the model ID works on your account (PRD Open Question #3)
- [ ] The model in the response is a string starting with `source_id=`, never a URL

**Common mistakes**
- Cursor uses `langchain_groq` or `langchain_core`. Reject — `openai` is the allowlisted client.
- Cursor falls back to `content` when JSON parsing fails. That reintroduces hallucinated text into the UI, which is the exact failure mode this architecture exists to prevent.
- Cursor retries forever. It is exactly one retry (NFR-2 / E2).

**Commit:** `feat: Groq generator in JSON mode, single retry, no raw-text fallback`

---

## 15. P12 — Formatter 🤖

**Goal:** Q9. Where determinism replaces trust in the model. **The highest-value 200 lines in the project.**

**Depends on:** P11
**Files to create:** `src/format_answer.py`, `tests/test_format.py`

**Read first:** `architecture.md` §8 Q9, §12 (citation resolution), §9.3 (the layer table)

### Prompt Block — P12

```
Read architecture.md §8 Q9 and §12, then write src/format_answer.py.

Implement the Q9 steps as separate pure functions so each is independently
testable. This module holds NO logic about the LLM or the network.

1. `sentence_truncate(text, max_sentences=CONFIG.MAX_SENTENCES) -> str`
   Split on sentence terminators (. ! ?) followed by whitespace, respecting
   decimal points and common abbreviations (p.a., Rs., No., Dr., Mr., vs.),
   take the first max_sentences, rejoin. Preserve inline markdown.
   If the text has fewer sentences, return it unchanged.

2. `normalize_number(tok) -> str` — strip commas, currency symbols, a trailing
   "%", a trailing ".*", and leading zeros are PRESERVED. So "0.52%", "0.52",
   "Rs. 0.52" all normalise to the same comparison form.
   Write it as a small, obviously-correct function. This is a security check
   and clever regexes here are a liability.

3. `numbers_supported(answer, chunk_text) -> bool`
   Extract every numeric token from the answer. For each, normalise it and
   check it appears in the normalised chunk text. Return False if ANY answer
   number is unsupported. This is the FR-5.5 / A1 numeric guard — the single
   most important defence against a hallucinated fee.

4. `performance_violation(text) -> bool` — True if text matches
   PERFORMANCE_PATTERNS from guardrails, or contains a "%" together with any of
   return/return%/CAGR/annualised. Reuse the patterns; do not redefine.

5. `finalize(llm: LLMResult, hits, registry) -> Answer` — the Q9 sequence,
   in this exact order, returning on the first failure:
     a. llm.error or llm.off_topic or not llm.answer -> NO_GROUNDING
     b. text = sentence_truncate(llm.answer)
     c. if performance_violation(text) -> return the PERFORMANCE refusal,
        with factsheet_url resolved from the registry by the scheme of the
        top hit. Do NOT emit a citation to the model.
     d. row = registry.resolve(llm.source_id)
        - if row is None or not row.enabled -> NO_GROUNDING
        - if row.discover_only is True -> NO_GROUNDING (aggregator never citable)
     e. cited_text = the text of the hit whose source_id == llm.source_id
        - if not numbers_supported(text, cited_text) -> NO_GROUNDING
          (architecture.md §8 Q9 step 6)
     f. text = guardrails.scrub_pii(text)
     g. append, verbatim:
          \n\nSource: [<row url title>](<row.url>)
          \nLast updated from sources: <row.retrieved_at or hit retrieved_at>
     h. sources_consulted = len({hit.source_id for hit in hits})
     i. return Answer(status="ANSWERED", ...)
   Also: if `hits` is empty or the top hit failed the grounding gate, short
   circuit at (a) with NO_GROUNDING.

6. `Answer` frozen dataclass per architecture.md §6.5: query, status, text,
   citation, sources_consulted, latency_ms, trace.
   `Citation` frozen dataclass: title, url, retrieved_at.

7. NO_GROUNDING text comes from guardrails.REFUSAL_NO_GROUNDING rendered with
   the official page URL for the resolved scheme, or a generic HDFC index link
   if no scheme was resolved. Never a model-authored string.

8. tests/test_format.py:
   - sentence_truncate: 5 sentences -> 3; decimals not split; "p.a." not
     split; 2 sentences unchanged
   - normalize_number: the three forms above collide
   - numbers_supported: True for "0.52%" against a chunk containing "0.52%";
     False for "0.53%" against the same chunk
   - finalize with an unknown source_id -> NO_GROUNDING
   - finalize with a discover_only source_id -> NO_GROUNDING   (I1/C1)
   - finalize with an unsupported number -> NO_GROUNDING       (A1)
   - finalize with a clean answer -> status ANSWERED, exactly one markdown
     link in the output, and the output contains "Last updated from sources:"
   - finalize where the answer contains a PAN -> output contains no PAN
     substring
   - a performance-violating answer -> PERFORMANCE status, and the output
     contains no "%" figure
```

**Verify**
```bash
python -m pytest tests/test_format.py -v
```

**Acceptance criteria**
- [ ] All 9 tests pass
- [ ] `discover_only` and `unknown source_id` and `unsupported number` all three produce `NO_GROUNDING`, never an answer
- [ ] Exactly one markdown link `[..](..)` in an ANSWERED payload
- [ ] Every `NO_GROUNDING` path produces a model-free string

**Common mistakes**
- Cursor reorders (e) before (d) and crashes on a `None` row. Order is load-bearing.
- Cursor's `numbers_supported` does a substring test on the raw text, so "0.52" matches inside "10.52" and the guard never fires. Use the normaliser.
- Cursor appends the citation *before* the PII scrub, so a PAN in the URL survives.
- Cursor lets the model supply the link text. It must come from the registry.

**Commit:** `feat: deterministic formatter — citation resolution, numeric guard, length limit`

---

## 16. P13 — Pipeline (Q1–Q9) 🤖

**Goal:** One function, `pipeline.answer`, that the UI, the CLI and the eval all call. I8 requires it.

**Depends on:** P12
**Files to create:** `src/pipeline.py`, `tests/test_pipeline.py`

**Read first:** `architecture.md` §8 (the full Q1–Q9 flow), §16 (the trace format), §14 (the whole error matrix)

### Prompt Block — P13

```
Read architecture.md §8 (Q1 through Q9), §14 and §16, then write
src/pipeline.py.

`answer(query: str, *, debug: bool = False) -> Answer` implements Q1-Q9 in
order. This is the ONLY entry point for the app, the CLI and the eval. Keep it
readable: a linear function with a per-stage comment marker, not a class with
injected collaborators. Use a small `_Stage` helper to record latency per stage.

Q1  PII scan. On a hit: return REFUSED_PII immediately. Do not embed, do not log
    the query, do not call the network. In debug mode the trace shows
    {"pii": {"hit": "<label>"}} and nothing else. (I3)
Q2  guardrails.classify. ADVICE -> REFUSED_ADVICE. OUT_OF_SCOPE ->
    REFUSED_OUT_OF_SCOPE. Pass use_llm=True, but the PII path must already have
    returned. On a classifier API failure, fall back to the rule layer's answer
    rather than raising.
    Also short-circuit PERFORMANCE here to REFUSAL_PERFORMANCE.
Q3  retriever.resolve_scheme — record it in the trace.
Q4-Q7 retriever.retrieve. If not grounded -> NO_GROUNDING.
Q8  generator.generate. On error -> status ERROR, and STILL return the
    retrieved hits in the trace so the demo can show the retrieval half
    working without the LLM (architecture.md §14 E2).
Q9  format_answer.finalize.

Wrap Q8 in try/except for any unexpected exception: never let a traceback
escape answer() (I9).

Every stage appends its latency to Answer.latency_ms, keyed by stage name.
When debug=True, populate Answer.trace with the exact structure in
architecture.md §16, including the retrieval hit list with rank, score,
source_id, section, doc_type, authority, chars and a text preview.

Do NOT import Streamlit. Do NOT read environment variables beyond what config
already does.
```

**Verify**
```bash
python -m pytest tests/test_pipeline.py -v
python -c "
from src.pipeline import answer
for q in ['What is the expense ratio of HDFC Large Cap?','Should I buy HDFC ELSS?',
          'my pan is ABCDE1234F','What are the returns of HDFC Large Cap?',
          'What is the price of Bitcoin?']:
    a = answer(q, debug=True)
    print(f'{a.status:20} {str(a.latency_ms):45} {q}')
    print('   ', (a.text or '')[:110].replace(chr(10),' '))"
```

**Acceptance criteria**
- [ ] Tests pass
- [ ] 5 probes produce `ANSWERED`, `REFUSED_ADVICE`, `REFUSED_PII`, `REFUSED_PERFORMANCE` (or `REFUSED_ADVICE` with `performance` matched), `REFUSED_OUT_OF_SCOPE` respectively
- [ ] `latency_ms` is a non-empty dict with per-stage keys
- [ ] The PII probe's trace contains no PAN substring
- [ ] End-to-end < 5 s (NFR-2)

**Common mistakes**
- Cursor logs the raw query somewhere for debugging. That is a C2 violation. Only redacted forms.
- Cursor retries Q8 inside the pipeline on top of the generator's own retry, multiplying latency against NFR-2.
- Cursor returns a string instead of an `Answer` on refusal paths. Refusals are first-class `Answer` objects with a `status` (architecture.md §6.5).

**Commit:** `feat: Q1-Q9 pipeline as the single entry point with per-stage trace`

---

## 17. P14 — CLI + Debug View 🤖

**Goal:** A terminal harness to exercise the whole pipeline and see the trace. This is what you will use to debug at 2 a.m.

**Depends on:** P13
**Files to create:** `src/cli.py`

**Read first:** `architecture.md` §16, §7.3 (the inspectability spirit)

### Prompt Block — P14

```
Read architecture.md §16, then write src/cli.py.

Usage:
  python -m src.cli                     # interactive REPL, keeps history in-process
  python -m src.cli "exit load on HDFC Large Cap"      # one-shot
  python -m src.cli --debug "expense ratio"            # full trace
  python -m src.cli --json "exit load"                  # machine-readable, for scripting

Behaviour:
- Catch StoreMissing and print the friendly "run python -m src.ingest first"
  message. Never a traceback (I9).
- Catch any other exception, print a one-line friendly message, and offer the
  traceback only behind an explicit --traceback flag.
- Default output per turn:
    [status] answer text
             Source: [title](url)
             Last updated from sources: date
             (latency: pii 1ms, embed 14ms, retrieve 22ms, llm 1180ms, post 1ms)
- --debug additionally prints, in this order: the PII check result, the intent
  classification with its layer and matched patterns, the resolved scheme
  filter, the full candidate count, the grounding threshold and max score, a
  table of the final hits (rank, score, source_id, section, doc_type, authority,
  preview), the LLM model and latency, and the post-checks (sentence count,
  citation resolved, numbers supported, truncated, pii scrubbed).
- Redact any PII in --debug output using guardrails.redact_for_trace.
- --json prints a single JSON object with status, text, citation, latency_ms
  and (only with --debug) trace.
- In interactive mode, "exit"/"quit"/Ctrl-C exits cleanly. Persist the in-process
  history to st.session_state-equivalent only; do NOT write chat logs to disk
  (C2).
```

**Verify**
```bash
python -m src.cli --debug "What is the exit load on HDFC Large Cap?"
python -m src.cli "Should I buy HDFC ELSS?"
mv chroma_db /tmp/cd_bak && python -m src.cli "exit load" ; mv /tmp/cd_bak chroma_db
```

**Acceptance criteria**
- [ ] `--debug` shows all 7 trace sections for a real query
- [ ] The moved-away-store case prints the friendly "run `python -m src.ingest`" message and exits 0 — no traceback
- [ ] The advice query prints the refusal and makes no Groq call (check latency: `llm` key absent or ~0)
- [ ] `--json` output parses with `json.loads`

**Common mistakes**
- Cursor writes a chat transcript to `chat_log.jsonl`. Reject — C2. Session history stays in-process only.
- Cursor's debug view dumps raw chunk text without a preview limit, producing 400-line output.

**Commit:** `feat: CLI with REPL, one-shot, and full decision-trace debug view`

---

## 18. P15 — Streamlit UI 🤖

**Goal:** The tiny UI from PRD §6 FR-6. Presentation only — no business logic (I8).

**Depends on:** P13
**Files to create:** `app.py`

**Read first:** `PRD.md` §5 FR-6, `architecture.md` §4 (module responsibilities), §20 (the MiniLM warm-up note)

### Prompt Block — P15

```
Read PRD.md FR-6 and architecture.md §4, then write app.py. Streamlit, single
file, presentation only. It must import ONLY from src.pipeline and
src.config — never from retriever, generator, or format_answer (I8).

Layout:
1. st.set_page_config(page_title="Mutual Fund FAQ Assistant", layout="centered")
2. At module import (not inside the handler), call src.store.get_collection() in a
   try/except so the MiniLM/Chroma warm-up is absorbed by the startup screen
   rather than the first question. If StoreMissing is raised, render
   "Run `python -m src.ingest` first" with the command, and st.stop().
3. Title: "Mutual Fund FAQ Assistant"
   Welcome line: "Hi! I answer facts about 5 HDFC Mutual Fund schemes using
   official sources only." Then a line naming the 5 schemes and categories.
4. Exactly 3 clickable example-question buttons (st.button), one each from:
   fees, ELSS lock-in, statements. Clicking sets the text in the chat input.
5. st.chat_input for the question.
6. Each turn renders: the user message, then the assistant message with
   st.markdown, unsafe_allow_html=False. Status-specific styling: a refusal gets
   an info box, NO_GROUNDING gets a warning box, ANSWERED gets the answer plus
   the citation and last-updated lines. For REFUSED_PII, show no citation and
   no echo of the input.
7. Directly under the input, always visible: **"Facts-only. No investment
   advice."** (D6 deliverable — this exact string must be in the file.)
8. Sidebar: an expander "How this answer was produced" rendering the trace from
   Answer.trace when debug is on — a small table of hits with rank, score,
   section, and source_id, plus the per-stage latency. And an expander listing
   the sources consulted with their URLs.
9. No analytics, no telemetry, no third-party trackers (NFR-4).
10. Wrap the answer() call in try/except: on an unexpected exception render a
    friendly message, never a traceback (I9).
```

**Verify**
```bash
streamlit run app.py
# then, in the browser:
#  - the 3 chips appear
#  - "Facts-only. No investment advice." is visible without scrolling
#  - click a chip -> an answer with a working Source link appears
#  - type "Should I buy HDFC ELSS?" -> the refusal renders, no numbers
#  - type a PAN -> blocked, nothing echoed
#  - the sidebar expander shows hits with scores
```

**Acceptance criteria**
- [ ] All 6 browser checks above pass
- [ ] The first question after startup is not noticeably slower than the second
- [ ] `grep -n "Facts-only. No investment advice." app.py` matches
- [ ] No import in `app.py` outside `src.pipeline`, `src.config`, and `src.store` (the warm-up exception)

**Common mistakes**
- Cursor puts `answer()` logic in `app.py` for convenience. Reject — I8 exists so the eval measures the same code the demo shows.
- Cursor uses `st.write(answer.text)` instead of markdown, so the citation renders as a bare URL. Use `st.markdown`.
- Cursor forgets the disclaimer, which is a graded deliverable (D6).

**Commit:** `feat: Streamlit UI with disclaimer, example chips, and trace sidebar`

---

## 18b. As built (P11–P13, P15)

Implemented after P7–P10. Suite is 290 passing; the UI is verified with a real
headless browser, not by assertion in prose.

**Files added**

| File | Phase | Purpose |
|---|---|---|
| `src/prompts.py` | P11a | `SYSTEM_PROMPT`, `CLASSIFIER_PROMPT`, `render_context`, `render_question` |
| `src/generator.py` | P11b | `LLMResult`, `get_client` (lru_cache), `generate` — JSON mode, one retry |
| `src/format_answer.py` | P12 | Q9 gates: `sentence_truncate`, `normalize_number`, `numbers_supported`, `performance_violation`, `finalize`, `Answer`, `Citation` |
| `src/pipeline.py` | P13 | `answer(query, *, debug=False)` — the single entry point |
| `app.py` | P15 | Streamlit UI, presentation only |

**Deviations from the prompt blocks, and why**

1. **`finalize` checks every chunk from the cited source, not the first.** The
   model returns a `source_id`, not a chunk id, and retrieval routinely returns
   several chunks from one source. Taking only the first match refused a correct
   answer: a verified "1.03%" became `NO_GROUNDING` while the figure sat
   plainly in the second chunk of the same source. Regression test added.
2. **`strip_model_urls` added to Q9.** The prompt tells the model never to emit
   a link, but during testing it emitted `https://evil.example.com` inside an
   otherwise correct answer, and it reached the UI body right after a valid
   registry citation, where it read like a second source. I1 says a fabricated
   URL cannot reach the user because the model never types one — that was only
   true because the model happened to obey. It is now enforced.
3. **URLs for the sidebar travel in the trace.** I8 limits `app.py` to
   `src.pipeline`, `src.config` and `src.store`, so the hit trace carries `url`
   and `title` rather than `app.py` importing the registry.
4. **`generate` takes `registry` and ignores it.** Kept for the spec's signature
   and for the CLI's future use; it is unused today.
5. **Two bugs the tests caught that would have shipped silently.**
   Canonicalising a fact label *before* `_merge` orphans the fact, because
   `_merge` keys on the original label text — the fact vanishes and the block
   reverts to two plain paragraphs. Since the canonicalisation was reverted this
   is moot, but `test_ter_expansion_survives_the_merge` documents the trap.
   Separately, a coverage checker that normalises the probe but not the corpus
   reports rows with empty cells (`"|  |"`) as lost data.

**Grounding behaviour observed in the browser.** The numeric guard is the strict
thing: any figure in the answer absent from the cited source becomes
`NO_GROUNDING`. That is the intended direction, and it is why the ELSS TER miss
seen in `eval/recall_check.py` surfaces as an honest refusal rather than a
confident wrong answer.

**Performance (NFR-2).** Cold process: first question ~5.3 s, of which ~4.2 s is
the MiniLM load. Warm: 0.8–2.0 s. `app.py` calls `get_collection()` at module
import so the warm-up lands on the startup screen, per §20.

**Browser acceptance (headless Chromium, 9/9).** 3 chips render; the D6
disclaimer is visible without scrolling; a chip click yields an answer with a
real `hdfcfund.com` anchor and a `Last updated from sources:` line; "Should I
buy HDFC ELSS?" refuses; a PAN is refused and never echoed by the assistant;
the sidebar trace table shows rank and score.

**Not done:** P14 (CLI) and P16 (eval harness). P11–P13 and P15 are complete.


## 18c. As built (P14, P16, P17a) and two open findings

P14, P16 and P17a are built. **P17b is blocked at the human stop gate** and P18
is blocked behind it. Two findings changed the plan, so they are recorded here
rather than in a commit message.

### Built

| File | Phase | Purpose |
|---|---|---|
| `src/cli.py` | P14 | REPL, one-shot, `--debug` (7 trace sections), `--json`, `--traceback` |
| `eval/golden_qa.json` | P16a | 25 labelled cases, every `expected_facts` value grepped out of `data/chunks.txt` |
| `eval/golden_qa.py` | P16a | Read-only loader, so the runner never rewrites the golden set |
| `eval/run_eval.py` | P16b/P17a | PRD §8.2 metrics, link checker, `report.md`, `--sweep` |
| `sample_qa.md` | P18a | generated, currently stale — see the warning at its top |

### Bug found by the golden set: an honest refusal was reported as a network error

`eval/golden_qa.json` q25 ("how many days to settle redemptions?") is absent
from the corpus. The model declined correctly with `off_topic: true` and an
empty answer. `generator.py` scored the empty answer as a **schema error**, the
pipeline returned `ERROR` / "I couldn't reach the language model", and
`format_answer`'s `NO_GROUNDING` branch was unreachable. An out-of-corpus
question was shown to the user as a model outage.

Fixed in `src/generator.py`: `off_topic: true` is now a successful generation,
and any answer text accompanying it is discarded. Four regression tests added.
One pre-existing test (`test_off_topic_true_is_preserved`) asserted
`error == "schema"` — it encoded the bug and was inverted. Suite: 294 passing.

### Two harness bugs found by the same run

Both were in the measuring instrument, not the system, and both are worth
recording because the golden set is now the thing future accuracy claims rest on.

1. `FIGURE_RE` matched any digit, so the refusal "I only have documents for
   **5** HDFC Mutual Fund schemes" was reported as containing a financial
   figure. It matches currency, decimals, percentages and 3+ digit numbers now.
2. `no_model_url` was applied to refusals, which carry a hardcoded
   `investor.gov.in` SEBI link (`src/guardrails.py:128`). It is a curated
   constant, not model output; the check now applies only to ANSWERED answers.

The golden set itself was wrong once: q20 (a non-HDFC fund) was labelled
`NO_GROUNDING`, but the pipeline identifies the foreign scheme and refuses
before retrieval. `REFUSED_OUT_OF_SCOPE` is the correct, more precise label.

### Finding 1 — the grounding threshold has no usable knee

`python eval/run_eval.py --sweep`, 25 cases:

| group | n | min | mean | max |
|---|---|---|---|---|
| in_corpus (ANSWERED) | 19 | 0.6463 | 0.7658 | 0.8596 |
| true_out_of_corpus | **1** | 0.5680 | — | — |
| guardrailed_upstream (advice/PII/non-HDFC) | 5 | 0.6436 | 0.7176 | 0.8064 |

| tau | in-corpus recall | out-of-corpus gated | in-corpus cases lost |
|---|---|---|---|
| 0.20–0.55 | 1.000 | 0.00 | — |
| **0.60** | **1.000** | **1.00** | — |
| 0.65 | 0.947 | 1.00 | q03 |
| 0.70 | 0.895 | 1.00 | q03, q17 |
| 0.75 | 0.474 | 1.00 | 10 cases |

The spec's premise — "pick the knee" — does not hold here. The two
distributions overlap almost completely: the highest-scoring advice/PII case
(0.8064) outranks 15 of the 19 legitimate questions. MiniLM cosine similarity
between any financial question and any financial chunk is simply high, so tau
has almost no discriminative power in the 0.20–0.55 range the spec suggested.

Only **one** case actually exercises the gate. Advice, PII and non-HDFC funds
are all resolved upstream by Q1/Q2 and never reach it. So `tau = 0.60` is the
only value that separates anything, and it is tuned on **n = 1** with a margin of
0.078 between 0.5680 and 0.6463. That is precisely the overfitting P17 warns
against, so the decision is left to a human.

Current `tau = 0.35` costs nothing measurable: in-corpus recall is already 1.00
and the gate never fires. The real safety mechanisms are Q1 (PII), Q2 (intent),
the model's own honest decline, and the numeric support guard.

### Finding 2 — the free-tier model cannot reliably serve the real prompt

Chasing the two remaining golden-set failures exposed an infrastructure
ceiling. Both are honest refusals from real retrieval gaps, not bugs:

- **q04** (Flexi Cap TER 0.77): none of the top 5 hits contain the TER figure.
  It is buried in a mega-chunk that never wins retrieval. Same class as the
  ELSS TER miss at rank 36/349.
- **q19** (capital-gains statement): all 5 hits are `large_cap` scheme chunks;
  the `hdfc_request_statement_guide` nav shell never ranks at all. This
  confirms that source should be demoted, as flagged in P7–P10.

Then Groq started returning `429 rate_limited` on calls that had been
succeeding minutes earlier. The sweep isolates it exactly:

| payload | prompt_tokens | result |
|---|---|---|
| no context | 40 | OK |
| 1 chunk | 108 | OK |
| 5 chunks, raw | ~1,100 | **429** |
| 5 chunks, real `render_context` | ~838 | OK, then **429** once the window drained |

This is a **token-per-minute** limit, not a request-count limit, and it sits
right at the size of the production prompt. Consequences:

- Accuracy numbers are not reproducible under load. An earlier clean run scored
  23/25 (92%); a throttled run scored 5/25. `run_eval.py` now classifies these
  as `infra_error` and reports `None` rather than `0` for unmeasurable metrics,
  and `--delay` paces the run.
- `generator.py` had a 2s retry backoff, shorter than the rate-limit window, so
  a throttled run burned both attempts in ~4s and reported ERROR. Rate limits now
  get an 8s single wait; transient connection errors keep 2s. Still exactly one
  retry (NFR-2).
- 1,354 of the 3,353 context characters are navigation boilerplate
  (`## Investment Strategy... TER TER The To...`). Sending 3 hits instead of 5
  would cut the prompt 838 → 565 tokens. That is a `TOP_K` change, which P17
  reserves for a human decision, so it is not applied unilaterally.

Under throttling the pipeline degrades to `ERROR` and never to a wrong answer.
That is the safety property working, but it makes the system unusable for a
demo while throttled, which matters for R9.

### What is not done

P17b (apply a human-chosen tau / lambda / k) and P18 (`README.md`) are both
blocked at the stop gate. `sample_qa.md` is generated but stale.


## 19. P16 — Eval Harness 🤖🤝

**Goal:** Objective measurement against PRD §8.2. This is what turns claims into evidence.

**Depends on:** P14
**Files to create:** `eval/golden_qa.json`, `eval/run_eval.py`, `eval/report_template.md`

**Read first:** `PRD.md` §8.1 and §8.2 (the metrics — copy them exactly), `architecture.md` §18

### Prompt Block — P16a — author the golden set 🤝

```
Read PRD.md §8.1. Create eval/golden_qa.json with ~20 labelled queries:

  {
    "id": "q01",
    "query": "What is the expense ratio of HDFC Large Cap Fund?",
    "expected_kind": "ANSWERED",
    "expected_facts": ["0.52"],        // the numbers that MUST appear
    "expected_source_id": "hdfc_large_cap_scheme",
    "forbidden_terms": ["outperform", "returns are", "CAGR"],
    "note": "Direct-Growth plan only"
  }

Coverage, all mandatory:
- 5 schemes x (expense ratio, exit load, minimum SIP) = 15
- HDFC ELSS lock-in
- 1 riskometer question
- 1 benchmark question (HDFC Balanced Advantage)
- 1 capital-gains-statement-download question
- 1 OUT_OF_SCOPE (a non-HDFC fund)
- 2 ADVICE ("Should I buy...", "Which is better...")
- 2 PII (a PAN, an email)
- 1 out-of-corpus fact question that SHOULD hit NO_GROUNDING

The expected_facts values MUST be read out of the actual ingested chunks in
data/chunks.txt. Do not invent them from memory — grep the chunks file and copy
the figures that are really there. If a figure is not in the corpus, set
expected_kind to NO_GROUNDING and note why.
```

### Prompt Block — P16b — the runner

```
Write eval/run_eval.py. It must:

1. Load eval/golden_qa.json, load the registry, require_store().
2. For each case, call src.pipeline.answer(query) — the same entry point the UI
   uses (I8).
3. Assert the metrics in PRD §8.2 and report each as PASS/FAIL:
   - expected_kind matches the returned status
   - every string in expected_facts appears in the answer text
   - an ANSWERED answer contains EXACTLY ONE markdown link [..](http..)
   - that link's URL matches the registry URL for the expected source_id
   - the answer is <= 3 sentences
   - no term in forbidden_terms appears (case-insensitive)
   - a refusal contains no digits that look like financial figures
4. LINK CHECKER: for every row in data/sources.csv, issue a HEAD request with a
   10s timeout and report status. Print a "DEAD LINKS" section listing any
   non-2xx. This is the R9 pre-demo check.
5. Output: a per-case table, then a metrics summary with the PRD §8.2 numbers,
   then the pass rate. Also write eval/report.md.
6. --json for machine-readable output.
7. Do not modify golden_qa.json. Do not special-case any query id.
```

**Verify**
```bash
python eval/run_eval.py
cat eval/report.md
```

**Acceptance criteria**
- [ ] All 20 cases run
- [ ] Every metric line matches a PRD §8.2 row verbatim
- [ ] The link checker reports zero dead official links, or lists the dead ones for a human to fix
- [ ] The failure list is non-empty before P17 — if everything passes first try, the golden set is too easy and the measurement is worthless
- [ ] `expected_facts` were grepped out of `data/chunks.txt`, not written from memory

**Common mistakes**
- Cursor marks `expected_facts` as whatever the current system outputs. That is a test that can never fail and is worse than no test. **Facts must come from the source corpus.**
- Cursor omits the link checker. It is the R9 mitigation.
- Cursor hardcodes the answer text instead of a substring.

**Commit:** `test: golden Q&A set of 20 with metrics runner and link checker`

---

## 20. P17 — Tune τ, k, λ ⛔🤝

> **Stop gate.** The grounding threshold is the one number in the system with no principled default. Do not leave it at 0.35 and do not let Cursor pick it for you. A human decides, with data in front of them.

**Goal:** Calibrate `GROUNDING_THRESHOLD`, and confirm `MMR_LAMBDA` and `TOP_K`.

**Depends on:** P16
**Files to modify:** `src/config.py`, `docs/chunking_proposal.md` (append a calibration section)

### Prompt Block — P17a — measure

```
Add a `--sweep` flag to eval/run_eval.py that, for the 20 golden cases:

1. For each case, records the max retrieval similarity from
   src.retriever.retrieve(query) BEFORE the grounding gate is applied. Use
   src.store directly so the gate does not short-circuit the measurement.
2. Labels each case in_corpus (expected_kind == ANSWERED) or
   out_of_corpus (everything else).
3. Prints the full sorted list of (label, score, query) and a summary of
   min/mean/max for each label.
4. Sweeps tau over [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50] and for each
   value reports: in-corpus recall (fraction of ANSWERED cases that survive
   the gate) and out-of-corpus precision (fraction of non-ANSWERED cases
   correctly gated). Print it as a table.
5. Does NOT write to config.py. It only reports.
```

**Verify**
```bash
python eval/run_eval.py --sweep
```

### Human decision ⛔

```
Read the sweep table and choose tau as the knee: the lowest value where
out-of-corpus cases are gated without starving in-corpus cases.

Record in docs/chunking_proposal.md, appended:
  - the full score list
  - the chosen tau and the one-line reason
  - the resulting in-corpus recall and out-of-corpus precision

Then confirm MMR_LAMBDA (0.7) and TOP_K (5) by re-running the eval with
lambda 0.5 and 0.9, and with TOP_K 3 and 8. Keep whichever combination
maximises the PRD §8.2 numbers. These are brief-fixed defaults, so changing
them needs a note justifying the deviation.
```

### Prompt Block — P17b — apply

```
Apply the human-chosen values to src/config.py. Remove the "UNTUNED" comment
from GROUNDING_THRESHOLD and replace it with a one-line comment recording the
calibration date and the achieved recall/precision. Add a comment to any
changed TOP_K or MMR_LAMBDA stating the measured justification.

Re-run: python -m pytest -q  &&  python eval/run_eval.py
Both must be green.
```

**Acceptance criteria**
- [ ] The chosen τ is recorded with data, not intuition
- [ ] In-corpus recall ≥ 0.9 and out-of-corpus precision ≥ 0.8, or the shortfall is documented and accepted
- [ ] Full test suite green; eval metrics match PRD §8.2
- [ ] `GROUNDING_THRESHOLD` no longer says UNTUNED

**Common mistakes**
- Cursor picks τ by maximising eval pass rate on 20 cases. That overfits 20 cases; pick the knee and accept a slightly lower score.
- Cursor raises τ until everything passes, starving real questions. The in-corpus recall column is what stops this — check it.

**Commit:** `chore: calibrate grounding threshold, k and lambda from golden-set sweep`

---

## 21. P18 — Deliverables 🤖🤝

**Goal:** Every item in PRD §10 closed. This is what is actually graded.

**Depends on:** P17
**Files to create/modify:** `README.md`, `sample_qa.md`, `.gitignore` check, `data/sources.csv` (final)

**Read first:** `PRD.md` §10 (D1–D8), §13 (the known limits to state verbatim), §11 (milestones)

### Prompt Block — P18a — sample_qa.md

```
Read PRD.md §10 D5. Regenerate sample_qa.md by RUNNING the system, not by
writing it by hand.

Produce 10 Q&A entries covering: all 5 schemes, every required fact category
from PRD §4.4 (expense ratio, exit load, minimum SIP, ELSS lock-in,
riskometer, benchmark, statements), plus at least one ADVICE refusal and one
NO_GROUNDING.

For each entry include:
  ### Q<n>. <the question>
  **Status:** ANSWERED | REFUSED_ADVICE | NO_GROUNDING
  **Answer:** <the exact text the system produced>
  **Source:** <the citation>
  **Last updated from sources:** <date>
  **Retrieved context (top hit):** score, source_id, section, first ~200 chars

Generate it by calling src.pipeline.answer with debug=True and formatting the
output. Include a header noting the ingest date and the model IDs used.
```

### Prompt Block — P18b — README.md

```
Write README.md. Required sections, in this order:

1. What it is — 3 sentences. Facts-only MF FAQ assistant for 5 HDFC schemes,
   RAG over public official pages, every answer cited.
2. Scope — the AMC and the 5 schemes with categories, Direct-Growth only.
   State the official-vs-aggregator decision (PRD §4.3) plainly: official AMC /
   AMFI pages are cited; Groww was used only to locate them.
3. The exact disclaimer string used in the UI (D6), quoted.
4. Setup — the exact commands from architecture.md §21, plus the note that the
   first run downloads the MiniLM model and needs internet.
5. How to run — ingest, CLI, streamlit, eval.
6. Architecture — a short version of architecture.md §3 and §8, with a link to
   architecture.md. Include the chunking parameters and the calibrated
   grounding threshold.
7. Sample Q&A — a table of 5 queries and their answers, linking sample_qa.md.
8. Evaluation — the PRD §8.2 metrics table with the ACTUAL measured numbers
   from eval/report.md, and a pointer to the full report.
9. Known limits — the 8 items from PRD §13, verbatim, plus the 8
   architectural limits from architecture.md §23 that actually apply.
10. Deliverables checklist — D1-D8 with where each one lives.
11. Citation — the source list, from data/sources.csv.

Do not claim anything the eval did not measure. If a metric was not measured,
say so.
```

### Prompt Block — P18c — final audit

```
Run this final audit and report the results honestly, including failures:

1. python -m pytest -q                      # all tests
2. python eval/run_eval.py                  # metrics + link checker
3. git status                               # must be clean
4. git check-ignore .env chroma_db data/raw # all three ignored
5. grep -rn "GROQ_API_KEY" --include=*.py   # must only appear in src/config.py
   and src/generator.py, never a literal key
6. grep -rn "langchain\|llama_index\|langgraph" .   # must be empty
7. grep -c "http" data/chunks.txt          # every chunk has its URL footer
8. Confirm data/chunks.txt is committed and data/raw/ is not
9. Confirm README.md quotes the exact disclaimer string that appears in app.py
10. Print the PRD §10 deliverable table with each row marked done or open

Report anything that fails. Do not fix silently — list it for a human.
```

**Acceptance criteria**
- [ ] `sample_qa.md` has 10 entries, all machine-generated, with statuses
- [ ] `README.md` has all 11 sections; the eval numbers are real
- [ ] Test suite green; eval metrics meet PRD §8.2
- [ ] Final audit reports 0 failures
- [ ] `data/sources.csv` has `retrieved_at` and `fetch_status` populated for every enabled row

**Common mistakes**
- Cursor writes `sample_qa.md` by hand, inventing answers. It must be generated from a real run.
- README claims 100% citation accuracy without running the eval.
- `.gitignore` misses `data/raw/`, committing fetched pages that may contain PII.

**Commit:** `docs: README, generated sample Q&A, final deliverable audit`

---

## 22. Cross-Cutting Checklist

Apply at every phase boundary.

**Before starting a phase**
- [ ] Previous phase's Verify command is green
- [ ] Previous phase is committed
- [ ] `git status` is clean
- [ ] You have read the `Read first` sections for this phase

**While a phase is in progress**
- [ ] No library outside the §2.3 allowlist has appeared in `requirements.txt`
- [ ] No new tunable has been hardcoded outside `src/config.py` (I6)
- [ ] No `langchain` / `llama_index` / second embedding model has appeared (I10, I2)
- [ ] No stack trace can reach a user-facing surface (I9)
- [ ] No PII is written anywhere (I7)

**Before committing a phase**
- [ ] Verify command passes
- [ ] All acceptance criteria met
- [ ] `git status` shows only in-scope files
- [ ] Any new invariant is added to §2.1 if it's genuinely global
- [ ] Commit message matches the phase's stated message

**At the end of P17 (the only real stop gate besides P2)**
- [ ] τ is calibrated with recorded data
- [ ] Eval numbers are real
- [ ] README does not overclaim

---

## 23. Quick Reference — Files by Phase

| Phase | Creates | Deletes |
|-------|---------|---------|
| P0 | `.gitignore`, `requirements.txt`, `.env.example`, `src/__init__.py`, `src/config.py`, `tests/__init__.py`, `tests/conftest.py` | — |
| P1 | `src/sources.py`, `data/sources.csv`, `tests/test_sources.py` | — |
| P2 | `data/raw/*`, `docs/chunking_proposal.md` | — |
| P3 | `src/loaders.py`, `tests/test_loaders.py` | `fetch_probe.py` |
| P4 | `src/cleaner.py`, `tests/test_cleaner.py` | — |
| P5 | `src/chunker.py`, `tests/test_chunker.py`, `data/chunks.txt` | — |
| P6 | `src/embedder.py`, `tests/test_embedder.py` | — |
| P7 | `src/store.py`, `tests/test_store.py` | — |
| P8 | `src/ingest.py`, `tests/test_ingest_manifest.py` | — |
| P9 | `src/guardrails.py`, `tests/test_guardrails.py` | — |
| P10 | `src/retriever.py`, `tests/test_retriever.py` | — |
| P11 | `src/prompts.py`, `src/generator.py`, `tests/test_generator.py` | — |
| P12 | `src/format_answer.py`, `tests/test_format.py` | — |
| P13 | `src/pipeline.py`, `tests/test_pipeline.py` | — |
| P14 | `src/cli.py` | — |
| P15 | `app.py` | — |
| P16 | `eval/golden_qa.json`, `eval/run_eval.py`, `eval/report.md` | — |
| P17 | `docs/chunking_proposal.md` (append), `src/config.py` (edit) | — |
| P18 | `README.md`, `sample_qa.md` | — |

---

## 24. If a Phase Goes Wrong

| Symptom | Most likely cause | Fix in phase |
|---------|------------------|--------------|
| Answers cite the wrong scheme's fee | Scheme alias not matching; `where` filter absent | P10 |
| A fee table is missing from every answer | Table coverage failure at chunk time; rows dropped by the cleaner | P4, P5 |
| `mean_norm` ≠ 1.0 | `normalize_embeddings=False` | P6 |
| `chromadb` rejects metadata | `None`/list/dict in metadata; `chroma_safe` bypassed | P7 |
| Chunk tails are unretrievable | Chunk over 256 word-pieces (ADR-01) | P5 |
| "Should I buy HDFC ELSS?" gets an answer | OUT_OF_SCOPE checked before ADVICE | P9 |
| A PAN is echoed back in the answer | PII scrub runs after the citation is appended | P12 |
| A hallucinated link appears | The formatter is letting the model supply a URL | P12 |
| Every answer is NO_GROUNDING | τ set too high, or the corpus has no relevant chunks | P17, then P2 |
| The app is slow on first question | `get_collection()` not called at import | P15 |
| A stack trace appears in the UI | An uncaught exception path | P13, P15 |
| The eval passes on the first run | Golden set was written from model output | P16 |
| A demo question hits a rate limit | No retry headroom; no cached transcript | P11, P18 |
