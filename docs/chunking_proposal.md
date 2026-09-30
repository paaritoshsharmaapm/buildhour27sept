# Chunking Strategy Proposal — Corpus Inspection Report

**Phase:** P2 (the stop gate required by `Problemstatement.txt`)
**Date:** 2026-09-29
**Inputs:** `data/sources.csv`, snapshots in `data/raw/`
**Purpose:** Satisfy the brief's instruction to *"inspect the data and propose a strategy, say why it suits this data, and specify chunk size, overlap, and what metadata each chunk keeps"* — **before** `src/chunker.py` is written.

---

## 1. What was fetched

Eleven sources probed, ten reachable, six citable. All official pages were retrieved during a single successful window; `hdfcfund.com` throttles aggressively (see §3.1).

| source_id | HTTP | Bytes | `<main>` chars | Authority | Citable |
|---|---|---|---|---|---|
| `hdfc_large_cap_scheme` | 200 | 167,951 | 5,435 | official | ✅ |
| `hdfc_flexi_cap_scheme` | 200 | 185,460 | 7,555 | official | ✅ |
| `hdfc_elss_scheme` | 200 | 169,351 | 6,049 | official | ✅ |
| `hdfc_small_cap_scheme` | 200 | 185,579 | 7,367 | official | ✅ |
| `hdfc_balanced_scheme` | 200 | 180,746 | 6,605 | official | ✅ |
| `hdfc_request_statement_guide` | 200 | 129,908 | 2,084 | official | ✅ |
| `hdfc_large_cap_groww` | 200 | 453,887 | 17,664 | aggregator | ❌ `discover_only` |
| `hdfc_flexi_cap_groww` | 200 | 494,198 | 19,518 | aggregator | ❌ |
| `hdfc_small_cap_groww` | 200 | 496,876 | 19,831 | aggregator | ❌ |
| `hdfc_balanced_groww` | 200 | 815,651 | 43,484 | aggregator | ❌ |
| `hdfc_elss_groww` | 200 | — | — | aggregator | ❌ (PRD URL was 404) |

Estimated corpus: **≈8,000 word-pieces of usable text → roughly 45 chunks at the size chosen in §5.** That is a small corpus. Losing a single fee table is proportionally a much bigger loss here than it would be in a large corpus, which is why §5.4 makes table coverage a hard assertion rather than a nice-to-have.

---

## 2. The facts we can now answer

Read out of the snapshots, not from memory. These seed the P16 golden set.

| Scheme | Min SIP | Riskometer | TER | Exit load |
|---|---|---|---|---|
| HDFC Large Cap Fund (Direct) | ₹100 | Very High | 1.03% | 1.00% |
| HDFC Flexi Cap Fund (Direct) | ₹100 | Very High | 0.77% | 1.00% |
| HDFC ELSS Tax Saver Fund (Direct) | ₹500 | Very High | 1.21% | **NIL** |
| HDFC Small Cap Fund (Direct) | ₹100 | Very High | 0.78% | 1.00% |
| HDFC Balanced Advantage Fund (Direct) | ₹100 | Very High | 0.78% | **15% of the units redeemed** |

Every fact category in PRD §4.4 is answerable from the corpus: expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and the capital-gains statement procedure.

**ELSS lock-in** is a verbatim sentence in the About section:
> *"An Open-ended Equity Linked Savings Scheme with a statutory lock in of 3 years and tax benefit."*

**Capital-gains statement** is a list item on the guide page:
> *"Account statement - Click here · Dividend statement - Click here · Exit load statement - Click here · **Capital Gains Statement - Click here** · Consolidated account statement - Click here"*

⚠️ **Balanced Advantage's exit load is not a percentage.** It is *"15% of the units (the limit)"* — a units-based formulation. Any numeric guard must not assume `%` (P12, and it is called out in §8.1).

⚠️ The words "lock-in" appear on **all five** pages, not just ELSS, mostly in product-suitability boilerplate. Only ELSS carries the statutory three-year lock-in. A naive keyword search would produce four wrong answers, so lock-in must be resolved through the scheme filter (Q3) plus section context, never through keyword matching alone.

---

## 3. Findings that change the design

P2 exists to catch these before code is written. Six did.

### 3.1 The AMC domain in the PRD does not exist — `architecture.md` ADR-04 needs correcting

`PRD.md` §4.3 names `hdfcmutualfund.com` as the official host. **It does not resolve.** The real domain is `hdfcfund.com`.

- `hdfcmutualfund.com` → DNS failure
- `www.hdfcfund.com` → 96.17.182.157, live

Live scheme URL pattern: `https://www.hdfcfund.com/explore/mutual-funds/<slug>/direct`

`Config.ALLOWED_HOSTS` has been updated and a regression test added (`test_registry_host_allowlist_matches_the_live_amc_domain`).

### 3.2 The Flexi Cap scheme has been renamed — the PRD's scheme names are stale

PRD §4.2 says "HDFC Equity (Flexi Cap) Fund" at `.../hdfc-equity-fund-direct-growth`. The live site serves **"HDFC Flexi Cap Fund"** at `.../hdfc-flexi-cap-fund/direct`. The old name is retired.

`SCHEMES` now reads `"HDFC Flexi Cap Fund"`. The alias table in P10 must map both `flexi cap` and `hdfc equity` to it, so a user typing the old name still resolves.

The ELSS Groww URL in the PRD is also 404 — the live slug inserts "plan":
`hdfc-elss-tax-saver-fund-direct-plan-growth`.

### 3.3 `trafilatura` is the wrong extractor for these pages — the biggest finding

`architecture.md` §7 S1 specifies `trafilatura.extract(...)`. Measured on `hdfc_large_cap_scheme.html`:

| Extractor | Chars | "expense ratio" present |
|---|---|---|
| `trafilatura` `favor_precision` | **1,233** | ❌ **0 occurrences** |
| `trafilatura` `favor_recall` | 1,335 | ❌ 0 occurrences |
| BeautifulSoup on `<main>` | **10,109** | ✅ 3 occurrences |

trafilatura discards **~88% of the page**, and it drops the expense ratio entirely — on all five scheme pages. Had P3 been built to spec, the corpus would have contained no expense-ratio facts at all, and every "what is the expense ratio of X?" question would have fallen through the grounding gate to "I don't have that in my sources."

`favor_recall` does not rescue it. `<main>` versus `<body>` differs by only ~7 characters once nav/header/footer are removed, so `<main>` is the right container and costs nothing.

**Decision:** BeautifulSoup on `<main>` is the primary extractor. trafilatura is demoted to an optional secondary signal. Recorded as **ADR-11**.

*(Also: architecture.md §7 S1 spells the parameter `favour_precision`. The real name is `favor_precision` — one `u`. The spec as written raises `TypeError`.)*

### 3.4 The AMC is behind Akamai bot protection — ingestion cannot rely on the network

Twice-fetched, identical headers, different results:

| Attempt | Client | Result |
|---|---|---|
| First of a burst | curl, full browser headers | **200**, 167 KB |
| Immediately after | curl, same headers | 403 "Access Denied" |
| 75 s later | python `requests`, same headers | 403 |
| Repeated 4× over ~5 min | `requests` and curl | 403 throughout |

This is **not** IP banning and **not** header ordering — curl worked and then stopped working from the same IP, and header order was ruled out experimentally. It is TLS/JA3 fingerprinting combined with burst throttling: roughly one successful request per cooldown window.

**Consequences, both requiring architecture changes:**

1. **Tiered fetch must add a transport axis.** `requests` first, system `curl` subprocess as fallback, then the documented "give up and warn" path. The tiering in architecture.md §7 S1 was by *extractor*; it now needs to be by *transport* as well.
2. **`data/raw/` must be committed, not gitignored.** The corpus is a snapshot of a site that will not reliably re-serve it. With `data/raw/` ignored, a clean clone has no corpus and the demo cannot be rebuilt on demo day — a direct R9 risk. This is a deliberate change to `.gitignore` and **needs a human decision**, since the guide page contains a toll-free number that the P3 PII scrub must redact before anything is committed.

For the record, the five scheme pages plus the guide were captured during the successful window and are preserved in `data/raw/`.

### 3.5 Key facts are label/value pairs with no heading above them

This is the finding that most directly shapes chunking.

The facts a user asks for render as **consecutive bare text lines**, sandwiched inside an unrelated section:

```
Returns since inception
14.19%
Inception Date
01/01/2013
Riskometer
Very High
Min SIP
₹ 500
```

There is no `<h2>` for "Riskometer" or "Min SIP". They sit inside the **About** section's prose. A heading-aware chunker that flushes on headings will bury `₹ 500` in the middle of a ~200-token prose chunk that also contains the fund's investment philosophy, and the embedding for *"what is the minimum SIP for HDFC ELSS"* will be dominated by philosophy prose.

The TER value has the same problem in a worse form: the label is inside a tooltip `<div>` and the number is in a **sibling** `<p class="...description">1.03</p>` — separated in the DOM.

**Decision:** the chunker must recognise label/value adjacency and emit those pairs as their own atomic chunks. Specified in §5.3.

### 3.6 The FAQ answers are in the DOM, and headings are well-formed

Good news, and it constrains the chunker positively:

- 16 headings per scheme page in clean `h1 → h2 → h3` order.
- FAQ items are `<h3>` questions — `"1. How to Invest in HDFC ELSS Tax Saver?"` — with the full answer as following prose, **server-rendered**, not deferred to JS. An FAQ question maps almost one-to-one to a user question.
- Exit load renders as a real `<table>` (4 columns, the redemption-period slabs) — e.g. ELSS is `NIL | NIL | NIL | NIL`.

---

## 4. Actual document structure

Per source, from the snapshots:

| Source | `<main>` chars | Headings | Tables | Numeric sections | Boilerplate to strip |
|---|---|---|---|---|---|
| `hdfc_large_cap_scheme` | 5,435 | 15 | 1 | TER, exit load, min SIP, riskometer, benchmark, inception | "OUR VISION / OUR MISSION" block, "Ask Me" widget, cookie text, most-searched-funds carousel |
| `hdfc_flexi_cap_scheme` | 7,555 | 15 | 1 | same | same |
| `hdfc_elss_scheme` | 6,049 | 16 | 1 | same **+ lock-in in About** | same |
| `hdfc_small_cap_scheme` | 7,367 | 15 | 1 | same | same |
| `hdfc_balanced_scheme` | 6,605 | 15 | 1 | same **+ units-based exit load** | same |
| `hdfc_request_statement_guide` | 2,084 | 2 | 0 | none (procedural) | "OUR VISION / OUR MISSION", toll-free numbers, SMS example with a placeholder folio |

Heading names actually present (from the ELSS page, representative of all five):
`h1` HDFC ELSS - Tax Saver Fund · `h2` About · `h2` NAV and Historical Performance · `h2` Benchmark Performance · `h2` Fund Managers · `h2` Portfolio Allocation & Top Holdings · `h2` Downloads · `h2` Exit Load · `h3` Product Labelling · `h3` Benchmark Riskometer · `h2` Product Suitability · `h2` FAQs · `h3` 1./2./3. numbered questions

**Legal disclaimer handling.** The pages carry "MUTUAL FUND INVESTMENTS ARE SUBJECT TO MARKET RISKS" and product-suitability text. In this corpus the disclaimer is **never** the sole home of a required fact — the lock-in sentence is in the About paragraph, not the disclaimer — so the P4 rule (drop a disclaimer block only if a near-duplicate exists elsewhere) drops the footer boilerplate safely. I checked rather than assumed: the ELSS lock-in string does not appear inside any disclaimer block.

---

## 5. The proposed strategy

### 5.1 Granularity: heading-aware recursive splitting, with a label/value rule on top

Chosen because the pages are genuinely sectioned, and because §3.5 shows a pure heading-based split is insufficient.

Rules, in precedence order:

1. **Flush on `h1`/`h2`.** Section boundaries. Keeps "Exit Load" from mixing with "Product Suitability".
2. **Never flush on `h3` alone** if the `h3` is a numbered FAQ question. Instead, an FAQ item becomes **its own chunk of question + answer** — the question text is the best possible embedding for the question the user will type. This is why FAQ items are treated separately rather than by generic `h3` handling.
3. **Label/value pairs are atomic.** If a paragraph matches `^(known label)\n(value)$` for a label in the known-facts set, emit `label: value` as its own chunk. §3.5.
4. **Table rows are atomic and never split.** A row is a unit; rows pack into a chunk until the budget is hit.
5. **Body overflow** flushes at the size budget, carrying overlap forward.

### 5.2 Chunk size and the 256-token arithmetic

`all-MiniLM-L6-v2` has `max_seq_length = 256` word-pieces. **Anything longer is silently truncated at encode time** — a fee table in a chunk's tail would be embedded as if it were not there and would become permanently unretrievable. This is ADR-01 and invariant I5.

```
TOTAL BUDGET (word-pieces)                    256   hard model limit
  − heading prefix (## Section name)         ~16
  − source footer (url + retrieved date)     ~12
  − safety margin                             ~8
  ─────────────────────────────────────────────────
  BODY BUDGET                                220   = CONFIG.CHUNK_SIZE_WP
OVERLAP                                       40   = CONFIG.CHUNK_OVERLAP_WP  (~18%)
```

`token_count` is measured as a **whitespace word count**, a safe upper bound per word relative to word-pieces. If the word count is ≤ 256 the real word-piece count is too. This keeps the assertion sound without loading the tokenizer into the chunker.

**Why not the PRD's ~450 tokens.** Two reasons, and the first is fatal: the model truncates at 256. The second is corpus-specific — at 220 word-pieces a scheme page yields ~7 chunks, which keeps each chunk's embedding focused. At 450 the About-section chunk would span the investment philosophy, the TER tooltip, and the min-SIP/Riskometer facts, and §3.5's dilution problem gets worse, not better.

### 5.3 Label/value handling

Recognised labels, emitted as `"<label>: <value>"` chunks:

`Min SIP` · `Riskometer` · `Expense Ratio` / `Total Expense Ratio` · `Exit Load` · `Benchmark` · `Lock in` · `Inception Date` · `Ideal for` · `AUM`

Because the TER label and value are in **sibling elements**, the matcher runs over the flattened line sequence after cleaning, not over the DOM tree.

### 5.4 Hard rules, enforced as assertions

| Rule | Assertion | Guards |
|---|---|---|
| A. No chunk exceeds 256 word-pieces | `assert_chunk_invariants` | ADR-01, I5 |
| B. No chunk spans two `source_id`s | `assert_chunk_invariants` | I4, R4 |
| C. `chunk_id` unique | `assert_chunk_invariants` | ADR-09 |
| D. **≥80% of source table rows appear in ≥1 chunk** | `assert_chunk_invariants` | R3, R4 |
| E. **Every label/value pair in a scheme page is captured** | new check in P5 | §3.5, R3 |

Rule E is new, added because of §3.5. Rule D alone would pass while every min-SIP and riskometer value was lost in prose, since those are not table rows.

### 5.5 Overlap: 40 word-pieces, and only between body chunks

Overlap is applied on **body-overflow flushes only** — never between two sections, never inside a table, never inside a label/value pair or an FAQ item. Those boundaries are semantic, not arbitrary, and overlapping across them duplicates a fee table into two chunks that may then be cited for two different questions, which is worse than losing a few words of prose.

### 5.6 Metadata per chunk, and who consumes it

Fifteen fields, unchanged from architecture.md §6.3. P2 adds the consumers:

| Field | Value observed | Consumed by |
|---|---|---|
| `text` | self-contained: heading + body + URL footer | S4 embed, Q8 LLM context |
| `source_id` | e.g. `hdfc_elss_scheme` | **Q7 citation resolution** — I1/C5 |
| `url` | `www.hdfcfund.com/...` | citation render |
| `authority` | `official` | Q5 `discover_only` filter, C1 |
| `discover_only` | `false` for all citable | Q5 unconditional filter |
| `scheme` / `scheme_category` | `HDFC ELSS Tax Saver Fund` / `elss` | Q3 alias filter, R4 |
| `doc_type` | `scheme_page` / `guide` | Q5 filter |
| `section` | `Exit Load`, `FAQs`, `Min SIP` | Q2 alias hints, debug view, **`faq_question` pairing** |
| `faq_question` | `"3. Can I invest in SIP and Lump Sum…"` | Q5 `doc_type=faq` narrowing, debug view |
| `chunk_index` | 0-based per source | ADR-09 id, re-chunk diff |
| `token_count` | ≤ 256 | Rule A |
| `char_start` / `char_end` | offsets into cleaned text | `chunks.txt` cross-reference |
| `retrieved_at` | `2026-09-29` | "Last updated from sources", FR-2.6 |
| `chunk_id` | `sha256(...)[:32]` | idempotent upsert, MMR identity |

One addition recommended: **`block_kind`** ∈ `{section, faq, fact, table, prose}`. The cleaner already produces typed blocks (architecture.md §7 S2), so this is free, and it makes rules D and E trivially assertable and the golden set far easier to debug — you can ask "did the label/value rule fire for ELSS?" directly.

---

## 6. Estimated outcome

| Source | Chunks (est.) | Notes |
|---|---|---|
| `hdfc_large_cap_scheme` | ~8 | incl. About, TER fact, exit-load table, 3 FAQ items |
| `hdfc_flexi_cap_scheme` | ~9 | |
| `hdfc_elss_scheme` | ~9 | incl. lock-in chunk — the most valuable chunk in the corpus |
| `hdfc_small_cap_scheme` | ~9 | |
| `hdfc_balanced_scheme` | ~9 | incl. units-based exit-load chunk |
| `hdfc_request_statement_guide` | ~2 | |
| **Total** | **~46** | |

`k = 5` retrieves ~11% of the corpus per query. At this size, `MMR` diversity and the scheme pre-filter do most of the work, and the grounding threshold carries unusual weight: there is little redundancy, so a single mis-ranked chunk means a wrong or refused answer. This reinforces that τ must be calibrated on data in P17, not guessed.

---

## 7. Risks, and what would change this proposal

| Risk | Signal to watch | Response |
|---|---|---|
| Label/value regex misses a label variant | Rule E assertion fails | Add the label; the assertion tells us which source broke |
| TER value moves out of the sibling `<p>` | Rule E fails on that source only | Widen the matcher, do not lower the threshold |
| Section prose still too diffuse | Golden-set expense-ratio queries fail | Lower `CHUNK_SIZE_WP` toward 180, raise overlap to 45 |
| τ too high for a 46-chunk corpus | Most queries return NO_GROUNDING | Recalibrate in P17; do **not** raise k to compensate |
| AMC site changes markup | `MIN_EXTRACTED_CHARS` warning, or table coverage failure | Re-snapshot; the rules fail loudly rather than silently |
| FAQ answers become JS-rendered | `faq` marker count drops to 0 | No HTML-only remedy; would need AMFI or a factsheet PDF — escalate |
| `data/raw/` snapshots go stale | `retrieved_at` ages | Re-fetch is unreliable (§3.4); prefer treating the snapshot as the corpus of record and re-fetching opportunistically |

---

## 8. Decisions needed from the team before P3

1. **Commit `data/raw/`, or not?** §3.4 argues for committing, because the AMC will not reliably re-serve the corpus and a clean clone would otherwise be empty. It needs a human call, and the P3 PII scrub must run before anything is committed — the statement guide contains toll-free numbers.
2. **Add the `curl` transport fallback?** It is outside the §2.3 dependency allowlist in the sense of being a system binary rather than a package, though it adds no Python dependency. Without it, the loader cannot fetch the official corpus at all on this machine.
3. **Raise `FETCH_DELAY_S` from 2.0 to ~6–8?** Empirically required. Currently 2.0 in config; the probe hardcodes 6.
4. **Accept the `block_kind` metadata addition** (§5.6)?
5. **Is the Flexi Cap rename acceptable** as a deviation from PRD §4.2's scheme names? It is a change to a frozen scope item, so it should be recorded rather than assumed.

---

## 9. Appendix — proposed ADRs for architecture.md

| ID | Decision |
|----|----------|
| **ADR-11** | Replace `trafilatura` with BeautifulSoup on `<main>` as the primary extractor. trafilatura loses ~88% of each page and drops the expense ratio entirely, which would leave the corpus unable to answer the most-asked question. |
| **ADR-12** | Add a transport axis to the tiered fetch: `requests` → `curl` subprocess → warn-and-skip. The AMC's Akamai fingerprinting blocks `requests` regardless of headers. |
| **ADR-13** | Correct the official host from `hdfcmutualfund.com` to `hdfcfund.com`, and the Flexi Cap scheme name from "HDFC Equity (Flexi Cap) Fund" to "HDFC Flexi Cap Fund". Both were PRD assumptions that the live site contradicts. |
| **ADR-14** | Add a `block_kind` field to chunk metadata and emit label/value facts as atomic chunks, because the fact values sit in sibling elements with no heading (§3.5). |
