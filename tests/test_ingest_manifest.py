"""P8 - manifest shape. build_manifest is pure, so this needs no network or Chroma."""

from __future__ import annotations

import json

from src.config import CONFIG
from src.ingest import build_manifest

SOURCES = [
    {
        "source_id": "hdfc_large_cap_scheme",
        "status": "ok",
        "http": 200,
        "bytes": 167951,
        "extracted_chars": 5472,
        "chunks": 31,
        "retrieved_at": "2026-09-29",
    },
    {
        "source_id": "hdfc_elss_scheme",
        "status": "ok",
        "http": 200,
        "bytes": 169351,
        "extracted_chars": 6093,
        "chunks": 12,
        "retrieved_at": "2026-09-29",
    },
]


def _manifest(**overrides) -> dict:
    kwargs = {
        "run_id": "2026-09-29T14:03:11Z",
        "force": False,
        "collection_name": "mf_faq_chunks",
        "count_before": 0,
        "count_after": 43,
        "sources": SOURCES,
        "warnings": ["hdfc_elss_sid: extracted_chars=812 below threshold 2000"],
    }
    kwargs.update(overrides)
    return build_manifest(**kwargs)


class TestShape:
    def test_top_level_keys(self):
        assert set(_manifest()) == {
            "run_id", "force", "model", "chunking", "collection", "sources", "warnings",
        }

    def test_model_block_says_what_produced_the_vectors(self):
        model = _manifest()["model"]
        assert model["embed"] == CONFIG.EMBED_MODEL
        assert model["dim"] == CONFIG.EMBED_DIM
        assert model["max_seq_length"] == CONFIG.EMBED_MAX_SEQ

    def test_chunking_block_records_the_strategy(self):
        chunking = _manifest()["chunking"]
        assert chunking["size_wp"] == CONFIG.CHUNK_SIZE_WP
        assert chunking["overlap_wp"] == CONFIG.CHUNK_OVERLAP_WP
        assert chunking["strategy"]

    def test_collection_block_tracks_before_and_after(self):
        collection = _manifest()["collection"]
        assert collection["name"] == "mf_faq_chunks"
        assert collection["count_before"] == 0
        assert collection["count_after"] == 43

    def test_every_source_entry_has_the_required_fields(self):
        required = {
            "source_id", "status", "http", "bytes",
            "extracted_chars", "chunks", "retrieved_at",
        }
        for entry in _manifest()["sources"]:
            assert set(entry) == required

    def test_warnings_is_always_a_list(self):
        assert _manifest(warnings=[])["warnings"] == []
        assert isinstance(_manifest()["warnings"], list)

    def test_run_id_is_an_iso_utc_string(self):
        assert _manifest()["run_id"] == "2026-09-29T14:03:11Z"
        assert _manifest()["run_id"].endswith("Z")

    def test_force_is_carried_through(self):
        assert _manifest(force=True)["force"] is True

    def test_manifest_is_json_serialisable(self):
        """The file is the deliverable; if it cannot be dumped it cannot be read."""
        text = json.dumps(_manifest())
        assert json.loads(text)["collection"]["count_after"] == 43

    def test_count_after_can_exceed_the_sum_of_per_source_chunks(self):
        """Re-ingest upserts by chunk_id, so a partial re-run leaves earlier
        chunks in place. The total is cumulative and must not be read as
        'this run produced these'."""
        manifest = _manifest(count_before=17, count_after=60)
        assert manifest["collection"]["count_after"] > sum(
            e["chunks"] for e in manifest["sources"]
        )
