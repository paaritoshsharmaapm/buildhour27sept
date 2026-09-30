# Sample Q&A

> **WARNING - this file is stale and must be regenerated.**
> It was generated while the Groq free tier was rate limiting (HTTP 429), so 10 of
> the 11 entries below record `ERROR` / "couldn't reach the language model"
> instead of real answers. The pipeline behaved correctly under throttling - it
> declined rather than inventing an answer - but these entries are not evidence
> of answer quality and should not be quoted.
>
> Regenerate once the token-per-minute window has reset:
>
> ```bash
> python -m src.ingest --force
> ./eval/regen_sample_qa.py          # see implementation.md P18a
> python -m pytest -q && python eval/run_eval.py
> ```
>
> Root cause and the fix under consideration are recorded in
> `implementation.md` §18c: the free tier's token-per-minute budget is exhausted
> by a ~838-token prompt, and 1,354 of the 3,353 context characters are
> navigation boilerplate.

Generated 2026-09-30 by running `src.pipeline.answer` with `debug=True`, not written by hand. Corpus ingested . Model `qwen/qwen3.8-27b`, embeddings `sentence-transformers/all-MiniLM-L6-v2`, grounding threshold `0.35`.

Ingest with `python -m src.ingest --force` after any change to the corpus.

---

### Q1. What is the expense ratio of HDFC Large Cap Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.7356` from `hdfc_large_cap_scheme`, section "HDFC Large Cap Fund"

> # HDFC Large Cap Fund HDFC Equity DIRECT REGULAR Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct (retrieved 2026-09-29)

---

### Q2. What is the minimum SIP for HDFC Large Cap Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.656` from `hdfc_large_cap_scheme`, section "About HDFC Large Cap Fund"

> ## About HDFC Large Cap Fund value of securities of a scheme minus liabilities divided by the total number of outstanding units on a given date. NA AUM (31/08/2

---

### Q3. What is the exit load on HDFC Flexi Cap Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.8397` from `hdfc_flexi_cap_scheme`, section "Exit Load"

> ## Exit Load In respect of each purchase / switch-in of Units, an Exit Load of 1.00% is payable if Units are redeemed / switched-out within 1 year from the date

---

### Q4. What is the expense ratio of HDFC ELSS Tax Saver Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.7496` from `hdfc_elss_scheme`, section "HDFC ELSS - Tax Saver Fund"

> # HDFC ELSS - Tax Saver Fund HDFC Tax Saver DIRECT REGULAR Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-elss-tax-saver-fund/direct (retrieved 2026

---

### Q5. What is the minimum SIP for HDFC ELSS Tax Saver Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.7339` from `hdfc_elss_scheme`, section "FAQs"

> ### 2. Can I invest in SIP and Lump Sum of the HDFC ELSS Tax Saver? Yes, SIP as well as Lump Sum investments are permitted with the minimum application amount b

---

### Q6. What is the lock-in period for HDFC ELSS Tax Saver Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.8472` from `hdfc_elss_scheme`, section "About HDFC ELSS - Tax Saver Fund"

> ## About HDFC ELSS - Tax Saver Fund Disclaimer Including Additional Expenses and Goods and Service Tax on Management Fees, if any. Click here to view the Total 

---

### Q7. What is the expense ratio of HDFC Small Cap Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.738` from `hdfc_small_cap_scheme`, section "FAQs"

> ### What is the minimum investment in HDFC Small Cap Fund? Rs.100/- and any amount thereafter Source: https://www.hdfcfund.com/explore/mutual-funds/hdfc-small-c

---

### Q8. What is the riskometer level of HDFC Balanced Advantage Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.7288` from `hdfc_balanced_scheme`, section "About HDFC Balanced Advantage Fund"

> ## About HDFC Balanced Advantage Fund An open ended Balanced Advantage Fund. This Fund aims to provide long term capital appreciation/income from a mix of equit

---

### Q9. What is the benchmark index of HDFC Balanced Advantage Fund?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.8596` from `hdfc_balanced_scheme`, section "About HDFC Balanced Advantage Fund"

> ## About HDFC Balanced Advantage Fund Benchmark A benchmark is a standard market index used to compare a Fund"s performance. It helps investors know whether the

---

### Q10. Should I buy HDFC ELSS Tax Saver Fund?

**Status:** REFUSED_ADVICE

**Answer:** I'm a facts-only assistant, so I can't tell you what to buy or sell. I can share factual details like expense ratio, exit load, lock-in, or benchmark. For guidance on choosing a scheme, see [SEBI Mutual Fund Basics](https://www.investor.gov.in/).

**Last updated from sources:** n/a

**Retrieved context (top hit):** none — REFUSED_ADVICE

---

### Q11. How many days does HDFC Large Cap Fund take to settle redemption proceeds?

**Status:** ERROR

**Answer:** I couldn't reach the language model. Your question was retrieved successfully — see the sources below.

**Last updated from sources:** n/a

**Retrieved context (top hit):** score `0.568` from `hdfc_large_cap_scheme`, section "FAQs"

> ### How to Invest in HDFC Large Cap Fund? Investors can apply for their transactions requests either offline or electronically using the relevant application / 

---

## Regenerating

```bash
./.venv/bin/python -m src.cli --debug "your question here"
```
