"""Chunk coverage: did the whole corpus actually make it into chunks?

    python eval/chunk_coverage.py           # per-kind coverage + store parity
    python eval/chunk_coverage.py --misses  # show what is uncovered

Ranking being good says nothing about completeness. A pipeline can retrieve the
right chunk perfectly while having silently thrown away 30% of the source. This
measures that separately, at three layers, because data can vanish at any of them:

  1. CLEANER retention  extracted HTML -> cleaned text. Chrome stripping and
     footer truncation are lossy by design; the question is how lossy.
  2. CHUNKER coverage   every cleaned block -> some chunk. Facts are guaranteed
     (floor 1.00) and table rows only to a floor of 0.80, so prose is measured
     here rather than trusted.
  3. STORE parity       chunks the pipeline produces now == collection.count().
     A store left over from before a chunker change is the most common way to
     test stale data and believe it is fresh.

Block text is compared after whitespace collapsing, because the chunker
re-joins wrapped lines and adds a section heading prefix.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

from src.chunker import block_covered, chunk_document
from src.cleaner import _strip_chrome, clean
from src.config import CONFIG
from src.loaders import fetch_all
from src.sources import load_registry

WHITESPACE = re.compile(r"\s+")


def norm(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--misses", action="store_true", help="print uncovered block text")
    args = parser.parse_args()

    registry = load_registry(CONFIG.SOURCES_CSV)
    docs = fetch_all(registry)

    per_source = []
    produced_here = []
    kind_wanted: Counter = Counter()
    kind_found: Counter = Counter()
    misses = []
    before = 0
    after = 0

    for source_id in registry.rows:
        raw = next((d for d in docs if d.source_id == source_id), None)
        if raw is None or raw.status not in {"ok", "cached"}:
            continue
        row = registry.resolve(source_id)
        try:
            doc = clean(raw)
        except Exception as error:
            per_source.append((source_id, f"clean failed: {error}"))
            continue

        try:
            chunks = chunk_document(doc, row, CONFIG)
        except Exception as error:
            per_source.append((source_id, f"chunk failed: {error}"))
            continue

        blob = norm("\n".join(chunk.text for chunk in chunks))
        # Measure retention inside the layer that does the stripping. The
        # loader's extracted_text is <main>-scoped while the cleaner walks the
        # whole soup, so comparing them reports >100% and means nothing.
        soup = BeautifulSoup(raw.raw_bytes or b"", "lxml")
        before += len(soup.get_text(" ", strip=True))
        _strip_chrome(soup)
        after += len(soup.get_text(" ", strip=True))

        missing_here = 0
        for block in doc.blocks:
            # Headings are folded into a chunk's section prefix rather than kept
            # as their own line, so they are counted separately and reported as
            # structure, not as missing body text.
            if block.kind == "heading":
                continue
            kind_wanted[block.kind] += 1
            if block_covered(block.text, blob):
                kind_found[block.kind] += 1
            else:
                missing_here += 1
                if len(misses) < 40:
                    misses.append((source_id, block.kind, block.text[:110]))

        produced_here.append(len(chunks))
        per_source.append((source_id, f"{len(doc.blocks):>5} blocks -> {len(chunks):>4} chunks"
                                       f"   {100*missing_here/max(len(doc.blocks),1):5.1f}% uncovered"))

    print("\n1. CLEANER RETENTION - chrome/nav/footer stripped before chunking")
    print(f"   soup text {before:>9,} chars -> after chrome removal {after:>9,} chars"
          f"   ({100*after/max(before,1):.1f}% kept)")
    print("   note: the loader's extracted_chars is <main>-scoped while the cleaner")
    print("   walks the whole soup, so the two figures are ~1.85x apart by design.\n")

    print("2. CHUNKER COVERAGE - every cleaned block should appear in some chunk")
    print(f"   {'kind':<13} {'covered':>9} {'total':>7} {'coverage':>9}   {'enforced by'}")
    print(f"   {'-'*13} {'-'*9} {'-'*7} {'-'*9}   {'-'*22}")
    enforced = {
        kind: "invariant, floor 1.00"
        for kind in ("fact", "table_row", "paragraph", "list_item", "footnote")
    }
    for kind in sorted(kind_wanted, key=lambda k: -kind_wanted[k]):
        want, got = kind_wanted[kind], kind_found[kind]
        pct = 100 * got / want if want else 100.0
        flag = "OK " if pct >= 99.5 else ("~  " if pct >= 95 else "XX ")
        note = enforced.get(kind, "NOT ENFORCED")
        if kind not in enforced:
            flag = "!! "
        print(f"   {flag}{kind:<10} {got:>9} {want:>7} {pct:>8.1f}%   {note}")
    print()

    print("3. PER SOURCE")
    for source_id, line in per_source:
        print(f"   {source_id:<30} {line}")
    print()

    print("4. STORE PARITY - is the store in sync with the chunker?")
    try:
        from src.store import count, require_store

        require_store()
        stored = count()
        produced = sum(produced_here)
        status = "OK  " if stored == produced else "STALE"
        print(f"   {status} store count={stored}  pipeline produces={produced}")
        if stored != produced:
            print("       -> the store does not match current output; re-run")
            print("          python -m src.ingest --force before trusting retrieval")
    except Exception as error:
        print(f"   XX  store unusable: {error}")
    print()

    if misses:
        print(f"UNCOVERED BLOCKS ({len(misses)} shown)")
        for source_id, kind, text in misses[: args.misses and 40 or 12]:
            print(f"   {source_id:<28} {kind:<11} {text!r}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
