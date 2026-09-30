"""P7 - S5 store. Every test uses tmp_path; the real chroma_db/ is never touched."""

from __future__ import annotations

import numpy as np
import pytest

from src import store
from src.chunker import Chunk
from src.config import CONFIG
from src.embedder import embed_texts
from src.store import (
    Hit,
    StoreMissing,
    chroma_safe,
    count,
    query_store,
    require_store,
    upsert_chunks,
)


def _chunk(index: int, text: str = "Min SIP: ₹ 100", **overrides) -> Chunk:
    fields = {
        "chunk_id": f"id{index:032d}",
        "text": text,
        "source_id": "unit",
        "source_title": "HDFC Large Cap Fund - scheme_page",
        "url": "https://www.hdfcfund.com/unit",
        "authority": "official",
        "discover_only": False,
        "scheme": "HDFC Large Cap Fund",
        "scheme_category": "large_cap",
        "doc_type": "scheme_page",
        "section": "Fund Facts",
        "faq_question": "",
        "chunk_index": index,
        "token_count": len(text.split()),
        "char_start": 0,
        "char_end": len(text),
        "retrieved_at": "2026-09-29",
        "block_kind": "fact",
    }
    fields.update(overrides)
    return Chunk(**fields)


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """A throwaway CHROMA_DIR. CONFIG is frozen, so patch the store's view."""
    monkeypatch.setattr(store, "chroma_dir", lambda: tmp_path / "chroma_db")
    store.reset_caches()
    yield
    store.reset_caches()


class TestUpsert:
    def test_upsert_then_count(self, store_env):
        chunks = [_chunk(i) for i in range(3)]
        vectors = embed_texts([c.text for c in chunks])
        assert upsert_chunks(chunks, vectors) == 3
        assert count() == 3

    def test_upsert_is_idempotent(self, store_env):
        """ADR-09. Identical chunk_ids must overwrite, not accumulate."""
        chunks = [_chunk(i) for i in range(3)]
        vectors = embed_texts([c.text for c in chunks])
        upsert_chunks(chunks, vectors)
        upsert_chunks(chunks, vectors)
        assert count() == 3

    def test_force_rebuilds_from_scratch(self, store_env):
        old = [_chunk(i, text=f"old text {i}") for i in range(5)]
        upsert_chunks(old, embed_texts([c.text for c in old]))
        assert count() == 5
        new = [_chunk(i) for i in range(2)]
        assert upsert_chunks(new, embed_texts([c.text for c in new]), force=True) == 2
        assert count() == 2

    def test_persistence_survives_a_fresh_client(self, store_env):
        chunks = [_chunk(i) for i in range(3)]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        store.reset_caches()  # drops the memoised client entirely
        assert count() == 3


class TestChromaSafe:
    def test_none_becomes_empty_string(self):
        metadata = chroma_safe(_chunk(0, faq_question=None))
        assert metadata["faq_question"] == ""

    def test_bools_stay_bools_not_strings(self):
        """A stringly-typed bool breaks every `where` filter in Q5."""
        metadata = chroma_safe(_chunk(0, discover_only=True))
        assert metadata["discover_only"] is True
        assert not isinstance(metadata["discover_only"], str)

    def test_integers_stay_integers(self):
        assert isinstance(chroma_safe(_chunk(7))["chunk_index"], int)

    def test_every_value_is_an_allowed_type(self):
        allowed = (str, int, float, bool)
        for value in chroma_safe(_chunk(0, faq_question=None)).values():
            assert isinstance(value, allowed), f"{value!r} is not a legal chroma value"

    def test_all_three_doc_types_are_stored(self):
        """Q5 pre-filters on these; a missing key fails the query, not the upsert."""
        metadata = chroma_safe(_chunk(0))
        for key in ("source_id", "scheme_category", "doc_type", "discover_only", "authority"):
            assert key in metadata


class TestQuery:
    def test_nearest_neighbour_is_found(self, store_env):
        chunks = [
            _chunk(0, text="The minimum SIP for HDFC Large Cap Fund is ₹ 100"),
            _chunk(1, text="Monsoon flooding closed the coastal highway for two days"),
            _chunk(2, text="Expense ratio of the Large Cap Fund is 1.03 percent"),
        ]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        qvec = embed_texts(["what is the minimum SIP amount"])[0]
        hits = query_store(qvec, n_results=3)
        assert isinstance(hits[0], Hit)
        assert "minimum SIP" in hits[0].text
        assert hits[0].score >= hits[-1].score

    def test_hits_carry_metadata_and_embedding(self, store_env):
        chunks = [_chunk(0)]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        hit = query_store(embed_texts(["min sip"])[0], n_results=1)[0]
        assert hit.metadata["source_id"] == "unit"
        assert hit.embedding is not None and len(hit.embedding) == CONFIG.EMBED_DIM

    def test_where_filter_restricts_results(self, store_env):
        chunks = [
            _chunk(0, text="official page text", source_id="official_one", discover_only=False),
            _chunk(1, text="aggregator page text", source_id="groww_one", discover_only=True),
        ]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        qvec = embed_texts(["page text"])[0]
        hits = query_store(qvec, where={"discover_only": False}, n_results=5)
        assert {h.metadata["source_id"] for h in hits} == {"official_one"}

    def test_empty_store_returns_no_hits_rather_than_raising(self, store_env):
        assert query_store(np.zeros(CONFIG.EMBED_DIM, dtype=np.float32), n_results=3) == []


class TestRequireStore:
    def test_missing_directory_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "chroma_dir", lambda: tmp_path / "absent")
        store.reset_caches()
        with pytest.raises(StoreMissing, match="python -m src.ingest"):
            require_store()
        store.reset_caches()

    def test_empty_collection_raises(self, store_env):
        with pytest.raises(StoreMissing, match="empty"):
            require_store()

    def test_populated_store_passes(self, store_env):
        chunks = [_chunk(0)]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        require_store()  # must not raise

    def test_schema_version_mismatch_demands_a_rebuild(self, store_env):
        chunks = [_chunk(0)]
        upsert_chunks(chunks, embed_texts([c.text for c in chunks]))
        collection = store.get_collection()
        collection.modify(metadata={"schema_version": 999})
        with pytest.raises(StoreMissing, match="--force"):
            require_store()
