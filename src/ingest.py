"""S1-S5 orchestrator. `python -m src.ingest [--force] [--only <source_id>]`

Runs the whole ingestion path and writes data/ingest_manifest.json so the run is
auditable and the README can quote real numbers.

Two rules that are easy to get wrong and expensive to get wrong:

  A single source's failure never aborts the run. Only a total failure exits
  non-zero. One dead URL must cost you that source, not the whole demo.

  The chunk invariants are asserted across the *whole* corpus, not per source.
  Per-source assertions cannot see one source's URL leaking into another
  source's chunk, which is the I4/R4 failure the check exists for.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np

from src.chunker import ChunkInvariantError, assert_chunk_invariants, chunk_document, write_chunks_txt
from src.cleaner import clean
from src.config import CONFIG
from src.embedder import embed_texts, write_embeddings_txt
from src.loaders import fetch_all
from src.sources import load_registry
from src.store import count, upsert_chunks

USABLE = frozenset({"ok", "cached"})


def _log(stage: str, message: str) -> None:
    print(f"[{stage:6}] {message}", flush=True)


def build_manifest(
    *,
    run_id: str,
    force: bool,
    collection_name: str,
    count_before: int,
    count_after: int,
    sources: list,
    warnings: list,
) -> dict:
    """Pure: takes the collected facts, returns the manifest dict.

    Kept free of I/O so the shape can be tested without a network or a Chroma.
    """
    return {
        "run_id": run_id,
        "force": force,
        "model": {
            "embed": CONFIG.EMBED_MODEL,
            "dim": CONFIG.EMBED_DIM,
            "max_seq_length": CONFIG.EMBED_MAX_SEQ,
        },
        "chunking": {
            "size_wp": CONFIG.CHUNK_SIZE_WP,
            "overlap_wp": CONFIG.CHUNK_OVERLAP_WP,
            "strategy": "heading_aware_recursive",
        },
        "collection": {
            "name": collection_name,
            "count_before": count_before,
            "count_after": count_after,
        },
        "sources": sources,
        "warnings": warnings,
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.ingest")
    parser.add_argument("--force", action="store_true", help="rebuild the collection")
    parser.add_argument("--only", help="ingest a single source_id")
    args = parser.parse_args(argv)

    started = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    warnings: list = []
    entries: list = []
    all_chunks: list = []
    blocks_by: dict = {}
    source_ids: set = set()

    # --- S1 LOAD ---
    registry = load_registry(CONFIG.SOURCES_CSV)
    if args.only:
        row = registry.resolve(args.only)
        if row is None:
            _log("S1", f"unknown source_id {args.only!r}")
            return 1
        registry = type(registry)(rows={args.only: row})
    _log("S1", f"loading {len(registry.rows)} source(s)")
    raw_docs = fetch_all(registry)

    # --- S2 CLEAN ---
    for raw in raw_docs:
        if raw.status not in USABLE:
            _log("S2", f"skip {raw.source_id}: status={raw.status}")
            warnings.append(
                f"{raw.source_id}: fetch status {raw.status}"
                + (f" ({raw.error})" if raw.error else "")
            )
            entries.append(
                {
                    "source_id": raw.source_id,
                    "status": raw.status,
                    "http": raw.http_status,
                    "bytes": len(raw.raw_bytes),
                    "extracted_chars": len(raw.extracted_text),
                    "chunks": 0,
                    "retrieved_at": raw.retrieved_at,
                }
            )
            continue
        try:
            doc = clean(raw)
        except Exception as error:  # never let one source kill the run
            _log("S2", f"clean failed {raw.source_id}: {error}")
            warnings.append(f"{raw.source_id}: clean failed ({error})")
            continue

        for note in doc.warnings:
            warnings.append(f"{raw.source_id}: {note}")
        _log("S2", f"{raw.source_id}: {len(doc.blocks)} blocks, {len(doc.text)} chars")

        # --- S3 CHUNK (per source; invariants are asserted corpus-wide later) ---
        try:
            chunks = chunk_document(doc, registry.resolve(raw.source_id), CONFIG)
        except ChunkInvariantError as error:
            _log("S3", f"chunking failed {raw.source_id}: {error}")
            warnings.append(f"{raw.source_id}: {error}")
            chunks = []

        if chunks:
            all_chunks += chunks
            source_ids.add(raw.source_id)
            blocks_by[raw.source_id] = doc.blocks

        entries.append(
            {
                "source_id": raw.source_id,
                "status": raw.status,
                "http": raw.http_status,
                "bytes": len(raw.raw_bytes),
                "extracted_chars": len(raw.extracted_text),
                "chunks": len(chunks),
                "retrieved_at": raw.retrieved_at,
            }
        )

    if not all_chunks:
        _log("S3", "zero chunks produced; nothing to store")
        return 1

    # Cross-source bleed is only visible here, across the whole corpus.
    try:
        assert_chunk_invariants(all_chunks, source_ids, blocks_by)
    except ChunkInvariantError as error:
        _log("S3", f"INVARIANT VIOLATION: {error}")
        return 1
    _log("S3", f"{len(all_chunks)} chunks, max {max(c.token_count for c in all_chunks)} word-pieces")

    write_chunks_txt(all_chunks, CONFIG.CHUNKS_TXT)
    _log("S3", f"wrote {CONFIG.CHUNKS_TXT}")

    # --- S4 EMBED ---
    vectors = embed_texts([chunk.text for chunk in all_chunks])
    norms = np.linalg.norm(vectors, axis=1)
    _log(
        "S4",
        f"{vectors.shape[0]} vectors, dim={vectors.shape[1]}, "
        f"mean_norm={norms.mean():.6f} (must be 1.0, ADR-08)",
    )
    # Written here, not by a side script, so the preview can never describe a
    # corpus the store no longer holds.
    write_embeddings_txt(
        vectors, [chunk.chunk_id for chunk in all_chunks], CONFIG.EMBEDDINGS_TXT
    )
    _log("S4", f"wrote {CONFIG.EMBEDDINGS_TXT}")

    # --- S5 STORE ---
    count_before = 0 if args.force else count()
    try:
        count_after = upsert_chunks(all_chunks, vectors, force=args.force)
    except Exception as error:
        _log("S5", f"store failed: {error}")
        return 1
    if args.force:
        _log("S5", "collection was dropped and rebuilt")
    _log("S5", f"collection {CONFIG.COLLECTION_NAME}: {count_before} -> {count_after}")

    manifest = build_manifest(
        run_id=run_id,
        force=args.force,
        collection_name=CONFIG.COLLECTION_NAME,
        count_before=count_before,
        count_after=count_after,
        sources=entries,
        warnings=warnings,
    )
    CONFIG.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.MANIFEST.write_text(json.dumps(manifest, indent=2))
    _log("S5", f"wrote {CONFIG.MANIFEST}")

    ok = sum(1 for e in entries if e["status"] in USABLE)
    short = sum(1 for e in entries if e["status"] == "short")
    failed = sum(1 for e in entries if e["status"] in {"failed", "blocked"})
    _log("DONE", f"ok={ok} short={short} failed={failed} chunks={len(all_chunks)} "
                 f"warnings={len(warnings)} elapsed={time.time() - started:.1f}s")
    for note in warnings:
        _log("WARN", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
