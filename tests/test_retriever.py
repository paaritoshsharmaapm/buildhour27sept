"""P10 - Q3-Q7 retriever. A fake store, so no Chroma and no network."""

from __future__ import annotations

import numpy as np
import pytest

from src import retriever
from src.config import CONFIG
from src.retriever import RetrievalResult, mmr, resolve_scheme, retrieve
from src.store import Hit

RNG = np.random.default_rng(0)


def _unit(vector) -> list:
    array = np.asarray(vector, dtype=np.float32)
    return (array / np.linalg.norm(array)).tolist()


def _hit(index: int, score: float, vector=None, **metadata) -> Hit:
    base = {
        "chunk_id": f"id{index:032d}",
        "text": f"chunk {index} text",
        "source_id": "unit",
        "authority": "official",
        "discover_only": False,
        "scheme_category": "large_cap",
        "doc_type": "scheme_page",
        "section": "Fund Facts",
        "block_kind": "fact",
    }
    base.update(metadata)
    return Hit(
        chunk_id=base["chunk_id"],
        text=base["text"],
        metadata=base,
        score=score,
        embedding=_unit(RNG.normal(size=CONFIG.EMBED_DIM) if vector is None else vector),
    )


def _pool(n: int = 20, **metadata) -> list:
    return [_hit(i, 0.9 - i * 0.01, **metadata) for i in range(n)]


@pytest.fixture
def fake_store(monkeypatch):
    """Capture the where-clause the retriever builds and return a canned pool."""
    seen: dict = {}

    def _fake_query(qvec, *, where=None, n_results=8):
        seen["where"] = where
        seen["n_results"] = n_results
        return seen.get("pool", [])

    monkeypatch.setattr(retriever, "query_store", _fake_query)
    return seen


class TestResolveScheme:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("expense ratio of HDFC Large Cap", "large_cap"),
            ("exit load on HDFC Flexi Cap", "flexi_cap"),
            ("lock-in period HDFC ELSS", "elss"),
            ("what about HDFC Tax Saver Fund", "elss"),
            ("Small Cap Fund facts", "small_cap"),
            ("benchmark of Balanced Advantage Fund", "hybrid"),
            ("HDFC Equity fund name", "flexi_cap"),
        ],
    )
    def test_aliases(self, query, expected):
        assert resolve_scheme(query) == expected

    def test_case_insensitive(self):
        assert resolve_scheme("LARGE CAP Fund") == "large_cap"

    def test_unrelated_query_resolves_to_none(self):
        assert resolve_scheme("what is a mutual fund") is None

    def test_longest_alias_beats_a_shorter_substring(self):
        """'balanced advantage' must not be captured as anything shorter."""
        assert resolve_scheme("Balanced Advantage Fund") == "hybrid"

    def test_tie_breaks_on_earliest_position(self):
        assert resolve_scheme("small cap versus large cap") == "small_cap"

    def test_specific_beat_generic_when_query_leads_with_specific(self):
        assert resolve_scheme("HDFC ELSS tax saver lock in") == "elss"


class TestMMR:
    def test_returns_exactly_k(self):
        assert len(mmr(_pool(20), k=5, lam=0.7)) == 5

    def test_returns_k_even_with_a_tiny_pool(self):
        assert len(mmr(_pool(2), k=5, lam=0.7)) == 2

    def test_no_duplicate_chunk_ids(self):
        chosen = mmr(_pool(20), k=8, lam=0.7)
        assert len({hit.chunk_id for hit in chosen}) == len(chosen)

    def test_prefers_diversity_over_five_near_identical_items(self):
        """Five copies of one vector plus unrelated items: lambda=0.7 must not
        return all five, or the context handed to the model is one fact repeated."""
        same = _unit(RNG.normal(size=CONFIG.EMBED_DIM))
        twins = [_hit(i, 0.80, vector=same) for i in range(5)]
        others = [_hit(10 + i, 0.55 + i * 0.01) for i in range(4)]
        chosen = mmr(twins + others, k=5, lam=0.7)
        assert len({tuple(hit.embedding) for hit in chosen}) == 5

    def test_high_lambda_leans_on_relevance(self):
        same = _unit(RNG.normal(size=CONFIG.EMBED_DIM))
        twins = [_hit(i, 0.80, vector=same) for i in range(5)]
        others = [_hit(10 + i, 0.60) for i in range(4)]
        chosen = mmr(twins + others, k=3, lam=0.99)
        assert sum(1 for hit in chosen if tuple(hit.embedding) == tuple(same)) > 1

    def test_empty_pool_returns_empty(self):
        assert mmr([], k=5, lam=0.7) == []

    def test_tolerates_hits_without_embeddings(self):
        pool = [
            Hit(chunk_id=f"c{i}", text="t", metadata={}, score=0.5 - i * 0.1, embedding=None)
            for i in range(4)
        ]
        assert len(mmr(pool, k=3, lam=0.7)) == 3

    def test_equal_candidates_do_not_collapse(self):
        """Frozen dataclass equality means remove-by-value would drop the wrong
        element. Identical scores and vectors must still yield distinct picks."""
        same = _unit(RNG.normal(size=CONFIG.EMBED_DIM))
        pool = [_hit(i, 0.5, vector=same) for i in range(5)]
        chosen = mmr(pool, k=5, lam=0.7)
        assert len(chosen) == 5


class TestWhereClause:
    def test_discover_only_false_is_always_present(self, fake_store):
        retrieve("what is a mutual fund")
        assert fake_store["where"] == {"discover_only": False}

    def test_scheme_resolved_adds_an_and_filter(self, fake_store):
        retrieve("expense ratio of HDFC Large Cap")
        where = fake_store["where"]
        assert where == {
            "$and": [{"discover_only": False}, {"scheme_category": "large_cap"}]
        }

    def test_discover_only_false_survives_the_and_branch(self, fake_store):
        """The common mistake is dropping the C1 filter once a scheme matches."""
        retrieve("expense ratio of HDFC Large Cap")
        where = fake_store["where"]
        branches = where.get("$and", [])
        assert {"discover_only": False} in branches

    def test_pool_is_larger_than_k(self, fake_store):
        """MMR is a no-op if the pool equals the final k."""
        fake_store["pool"] = _pool(20)
        retrieve("expense ratio of HDFC Large Cap")
        assert fake_store["n_results"] == CONFIG.RETRIEVE_POOL == 20
        assert CONFIG.RETRIEVE_POOL > CONFIG.TOP_K


class TestGroundingGate:
    def test_grounded_when_top_score_clears_tau(self, fake_store):
        fake_store["pool"] = _pool(5)
        result = retrieve("expense ratio of HDFC Large Cap")
        assert result.grounded is True
        assert result.max_score >= CONFIG.GROUNDING_THRESHOLD

    def test_ungrounded_when_everything_is_weak(self, fake_store):
        fake_store["pool"] = [_hit(i, 0.10) for i in range(5)]
        result = retrieve("quantum physics entanglement")
        assert result.grounded is False
        assert result.max_score < CONFIG.GROUNDING_THRESHOLD

    def test_promotes_a_pre_mmr_candidate_that_clears_tau(self, fake_store, monkeypatch):
        """Q7 rescue: a sub-tau top-1 must not hide a candidate that clears tau.

        Driven by stubbing mmr, because through a real pool the branch cannot
        fire: MMR's first pick is always the max-relevance item, so any
        candidate clearing tau is itself selected and max_score already passes.
        The guard defends against a future MMR, not the current one.
        """
        strong = _hit(9, 0.71)
        fake_store["pool"] = [_hit(i, 0.30) for i in range(5)] + [strong]
        monkeypatch.setattr(retriever, "mmr", lambda *a, **k: [_hit(0, 0.30)])
        result = retrieve("expense ratio of HDFC Large Cap")
        assert result.hits[0].chunk_id == strong.chunk_id
        assert result.trace is None  # debug off

    def test_promotion_does_not_flip_grounded(self, fake_store, monkeypatch):
        """The gate must not be talked out of a sub-tau top hit by its own
        rescue path, or NO_GROUNDING becomes unreachable."""
        strong = _hit(9, 0.71)
        fake_store["pool"] = [_hit(i, 0.30) for i in range(5)] + [strong]
        monkeypatch.setattr(retriever, "mmr", lambda *a, **k: [_hit(0, 0.30)])
        result = retrieve("expense ratio of HDFC Large Cap")
        assert result.hits[0].score >= CONFIG.GROUNDING_THRESHOLD
        assert result.grounded is False

    def test_promotion_is_skipped_when_nothing_clears_tau(self, fake_store, monkeypatch):
        fake_store["pool"] = [_hit(i, 0.30) for i in range(5)]
        monkeypatch.setattr(retriever, "mmr", lambda *a, **k: [_hit(0, 0.30)])
        result = retrieve("expense ratio of HDFC Large Cap")
        assert result.hits[0].chunk_id == _hit(0, 0.30).chunk_id
        assert result.grounded is False

    def test_empty_store_is_ungrounded_not_an_exception(self, fake_store):
        fake_store["pool"] = []
        result = retrieve("anything at all")
        assert result.grounded is False
        assert result.hits == []
        assert result.max_score == 0.0
        assert result.candidates == 0


class TestResult:
    def test_counts_are_reported(self, fake_store):
        fake_store["pool"] = _pool(20)
        result = retrieve("expense ratio of HDFC Large Cap")
        assert isinstance(result, RetrievalResult)
        assert result.candidates == 20
        assert result.after_mmr == CONFIG.TOP_K

    def test_filtered_by_reports_the_scheme(self, fake_store):
        fake_store["pool"] = _pool(5)
        assert retrieve("ELSS lock in").filtered_by == "elss"

    def test_filtered_by_is_none_for_a_global_query(self, fake_store):
        fake_store["pool"] = _pool(5)
        assert retrieve("what is a mutual fund").filtered_by is None

    def test_debug_trace_exposes_the_where_clause(self, fake_store):
        fake_store["pool"] = _pool(5)
        result = retrieve("expense ratio of HDFC Large Cap", debug=True)
        branches = result.trace["where"]["$and"]
        assert {"discover_only": False} in branches
        assert result.trace["tau"] == CONFIG.GROUNDING_THRESHOLD

    def test_no_trace_unless_asked(self, fake_store):
        fake_store["pool"] = _pool(5)
        assert retrieve("expense ratio of HDFC Large Cap").trace is None
