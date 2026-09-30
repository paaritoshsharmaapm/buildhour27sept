"""P5 - S3 chunker. Synthetic CleanDocs only; no network."""

from __future__ import annotations

import pytest

from src.chunker import (
    ChunkInvariantError,
    assert_chunk_invariants,
    block_covered,
    chunk_document,
    chunk_id_for,
    write_chunks_txt,
)
from src.cleaner import Block, CleanDoc
from src.config import CONFIG
from src.sources import SourceRow

ROW = SourceRow(
    source_id="unit",
    url="https://www.hdfcfund.com/unit",
    authority="official",
    discover_only=False,
    scheme="HDFC Large Cap Fund",
    scheme_category="large_cap",
    doc_type="scheme_page",
    enabled=True,
)


def _doc(blocks, retrieved_at: str = "2026-09-29") -> CleanDoc:
    lines = [
        f"{'#' * b.level} {b.text}" if b.kind == "heading" else b.text for b in blocks
    ]
    return CleanDoc(
        source_id="unit",
        url=ROW.url,
        blocks=list(blocks),
        text="\n".join(lines),
        warnings=[],
        retrieved_at=retrieved_at,
    )


def test_heading_boundaries_produce_separate_chunks():
    blocks = [
        Block("heading", "Expenses and Taxes", 2),
        Block("paragraph", "Ongoing charges 0.52% of NAV."),
        Block("heading", "Fund Managers", 2),
        Block("paragraph", "Mr Rahul Baijal."),
    ]
    chunks = chunk_document(_doc(blocks), ROW)
    assert len(chunks) == 2
    assert chunks[0].section == "Expenses and Taxes"
    assert chunks[1].section == "Fund Managers"


def test_a_table_row_is_never_split():
    row = "Exit load | 1.00% if redeemed within 12 months | Nil after 18 months"
    blocks = [Block("heading", "Exit Load", 2), Block("table_row", row)]
    chunks = chunk_document(_doc(blocks), ROW)
    assert row in "\n".join(c.text for c in chunks)
    for chunk in chunks:
        assert row in chunk.text


def test_a_fact_is_atomic():
    """ADR-14. A split fact leaves a bare number with no label."""
    blocks = [
        Block("heading", "Fund Facts", 2),
        Block("fact", "Min SIP: ₹ 500"),
        Block("fact", "TER: 1.21"),
    ]
    chunks = chunk_document(_doc(blocks), ROW)
    for fact in ("Min SIP: ₹ 500", "TER: 1.21"):
        holders = [c for c in chunks if fact in c.text]
        assert len(holders) == 1, f"{fact} appeared in {len(holders)} chunks"


def test_facts_are_their_own_chunk_kind():
    blocks = [Block("heading", "Fund Facts", 2), Block("fact", "Min SIP: ₹ 500")]
    chunks = chunk_document(_doc(blocks), ROW)
    assert any(c.block_kind == "fact" for c in chunks)


def test_a_question_heading_becomes_an_faq_chunk():
    blocks = [
        Block("heading", "Who should invest in this fund?", 3),
        Block("paragraph", "Investors seeking long-term capital appreciation."),
        Block("heading", "How to invest?", 3),
        Block("paragraph", "Through the AMC website or RTA."),
    ]
    chunks = chunk_document(_doc(blocks), ROW)
    faq = [c for c in chunks if c.faq_question]
    assert faq, "no chunk captured a faq_question"
    assert "Who should invest" in faq[0].faq_question
    assert "long-term capital appreciation" in faq[0].text


def test_faq_question_is_never_none():
    blocks = [Block("heading", "Plain section", 2), Block("paragraph", "text")]
    for chunk in chunk_document(_doc(blocks), ROW):
        assert isinstance(chunk.faq_question, str)


def test_overlap_is_carried_into_the_next_chunk():
    filler = " ".join(f"w{i}" for i in range(600))
    blocks = [Block("heading", "Long Section", 2), Block("paragraph", filler)]
    chunks = chunk_document(_doc(blocks), ROW)
    assert len(chunks) > 1
    first_tail = chunks[0].text.splitlines()[-2:]
    assert any(line in chunks[1].text for line in first_tail)


def test_long_prose_is_sliced_rather_than_overflowing():
    """A 400-word paragraph must not become a 400-word chunk."""
    huge = " ".join(f"word{i}" for i in range(400))
    blocks = [Block("heading", "Too big", 2), Block("paragraph", huge)]
    chunks = chunk_document(_doc(blocks), ROW)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= CONFIG.EMBED_MAX_SEQ


def test_oversized_atomic_block_still_raises():
    """A table row is never split, so nothing can rescue an oversized one.

    This is the case the 256 assertion exists for, and it must stay loud rather
    than silently emitting a chunk whose tail the embedder will drop.
    """
    giant_row = "Exit load | " + " | ".join(f"slab{i} 1.00%" for i in range(120))
    blocks = [Block("heading", "Exit Load", 2), Block("table_row", giant_row)]
    with pytest.raises(ChunkInvariantError, match="ADR-01"):
        chunk_document(_doc(blocks), ROW)


def test_every_chunk_is_inside_the_budget():
    blocks = [
        Block("heading", "Section", 2),
        Block("paragraph", " ".join(f"w{i}" for i in range(600))),
    ]
    for chunk in chunk_document(_doc(blocks), ROW):
        assert chunk.token_count <= CONFIG.EMBED_MAX_SEQ


def test_chunk_id_is_stable_and_deterministic():
    blocks = [Block("heading", "S", 2), Block("paragraph", "body text here")]
    first = chunk_document(_doc(blocks), ROW)
    second = chunk_document(_doc(blocks), ROW)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id == chunk_id_for("unit", 0, first[0].text)


def test_retrieved_at_is_denormalised_onto_every_chunk():
    blocks = [Block("heading", "S", 2), Block("paragraph", "body")]
    for chunk in chunk_document(_doc(blocks, "2026-01-02"), ROW):
        assert chunk.retrieved_at == "2026-01-02"


def test_char_offsets_point_into_the_document_text():
    blocks = [Block("heading", "Expenses", 2), Block("paragraph", "0.52% of NAV")]
    doc = _doc(blocks)
    for chunk in chunk_document(doc, ROW):
        assert 0 <= chunk.char_start < chunk.char_end <= len(doc.text) + 1


class TestInvariants:
    def _chunks(self):
        blocks = [
            Block("heading", "Expenses", 2),
            Block("table_row", "Ongoing | 0.52% | monthly"),
            Block("fact", "Min SIP: ₹ 100"),
        ]
        return chunk_document(_doc(blocks), ROW), blocks

    def test_a_clean_run_passes(self):
        chunks, blocks = self._chunks()
        assert_chunk_invariants(chunks, {"unit"}, {"unit": blocks})

    def test_dropped_table_row_fails(self):
        """Simulates a chunker that lost a fee slab, with the source intact."""
        chunks, blocks = self._chunks()
        lost = "Ongoing | 0.52% | monthly"
        assert any(lost in c.text for c in chunks)
        broken = [c.__class__(**{**c.__dict__, "text": c.text.replace(lost, "")}) for c in chunks]
        with pytest.raises(ChunkInvariantError, match="table_row coverage"):
            assert_chunk_invariants(broken, {"unit"}, {"unit": blocks})

    def test_dropped_fact_fails_even_when_tables_are_fine(self):
        """The reason fact coverage is a separate check."""
        chunks, blocks = self._chunks()
        lost = "Min SIP: \u20b9 100"
        assert any(lost in c.text for c in chunks)
        broken = [c.__class__(**{**c.__dict__, "text": c.text.replace(lost, "")}) for c in chunks]
        with pytest.raises(ChunkInvariantError, match="fact coverage"):
            assert_chunk_invariants(broken, {"unit"}, {"unit": blocks})

    def _drop(self, chunks, needle):
        return [c.__class__(**{**c.__dict__, "text": c.text.replace(needle, "")})
                for c in chunks]

    def test_dropped_paragraph_fails(self):
        """Prose carries bios and strategy text; losing it must not pass."""
        blocks = [
            Block("heading", "Fund Manager", 2),
            Block("paragraph", "Mr. Dhruv has done B.Com, CA and CFA from Mumbai University."),
        ]
        chunks = chunk_document(_doc(blocks), ROW)
        lost = "B.Com, CA and CFA from Mumbai University"
        assert any(lost in c.text for c in chunks)
        with pytest.raises(ChunkInvariantError, match="paragraph coverage"):
            assert_chunk_invariants(self._drop(chunks, lost), {"unit"}, {"unit": blocks})

    def test_dropped_list_item_fails(self):
        blocks = [
            Block("heading", "Scheme Details", 2),
            Block("list_item", "Benchmark: Nifty 100 TRI"),
        ]
        chunks = chunk_document(_doc(blocks), ROW)
        with pytest.raises(ChunkInvariantError, match="list_item coverage"):
            assert_chunk_invariants(self._drop(chunks, "Nifty 100 TRI"), {"unit"}, {"unit": blocks})

    def test_row_with_empty_cell_is_not_reported_as_lost(self):
        """"1 year | Rs 60,000 | Rs 57,651 |  | -3.91 %" has a double separator.

        Normalising only the probe collapses "|  |" to "| |" and then fails to
        find text that is demonstrably present, which reads as silent data loss.
        """
        blocks = [
            Block("heading", "Over the past", 2),
            Block("table_row", "1 year | \u20b960,000 | \u20b957,651 |  | -3.91 %"),
        ]
        chunks = chunk_document(_doc(blocks), ROW)
        blob = "\n".join(c.text for c in chunks)
        assert block_covered(blocks[1].text, blob)
        assert_chunk_invariants(chunks, {"unit"}, {"unit": blocks})

    def test_sliced_paragraph_is_not_reported_as_lost(self):
        """A block longer than the slice budget is cut across chunks on purpose.

        Its words are all in the corpus, so coverage must pass even though the
        original contiguous string appears nowhere.
        """
        sentence = " ".join(f"clause{i} of the investment strategy" for i in range(300))
        blocks = [Block("heading", "Strategy", 2), Block("paragraph", sentence)]
        chunks = chunk_document(_doc(blocks), ROW)
        assert len(chunks) > 1, "expected the paragraph to be sliced"
        blob = "\n".join(c.text for c in chunks)
        assert sentence not in blob, "slice should have broken contiguity"
        assert block_covered(sentence, blob)
        assert_chunk_invariants(chunks, {"unit"}, {"unit": blocks})

    def test_a_source_with_no_chunks_fails(self):
        chunks, blocks = self._chunks()
        with pytest.raises(ChunkInvariantError, match="zero chunks"):
            assert_chunk_invariants(chunks, {"unit", "missing"}, {"unit": blocks})

    def test_foreign_url_in_a_chunk_fails(self):
        """A chunk must never carry another source's url (I4/R4)."""
        chunks, blocks = self._chunks()
        other = SourceRow(
            source_id="other",
            url="https://groww.in/mutual-funds/other",
            authority="aggregator",
            discover_only=True,
            scheme="HDFC Large Cap Fund",
            scheme_category="large_cap",
            doc_type="aggregator_page",
            enabled=True,
        )
        other_doc = CleanDoc(
            source_id="other", url=other.url,
            blocks=[Block("heading", "Groww", 2), Block("paragraph", "aggregated copy")],
            text="## Groww\naggregated copy", warnings=[], retrieved_at="2026-09-29",
        )
        other_chunks = chunk_document(other_doc, other)
        poisoned = chunks[0].__class__(
            **{**chunks[0].__dict__, "text": chunks[0].text + "\n" + other.url}
        )
        with pytest.raises(ChunkInvariantError, match="I4/R4"):
            assert_chunk_invariants([poisoned] + other_chunks, {"unit", "other"},
                                    {"unit": blocks, "other": other_doc.blocks})

    def test_duplicate_chunk_id_fails(self):
        chunks, blocks = self._chunks()
        with pytest.raises(ChunkInvariantError, match="duplicate chunk_id"):
            assert_chunk_invariants(chunks + [chunks[0]], {"unit"}, {"unit": blocks})


def test_write_chunks_txt_is_stable_and_greppable(tmp_path):
    blocks = [
        Block("heading", "Expenses and Taxes", 2),
        Block("table_row", "Ongoing | 0.52% | monthly"),
        Block("fact", "Min SIP: ₹ 100"),
    ]
    chunks = chunk_document(_doc(blocks), ROW)
    path = tmp_path / "chunks.txt"
    write_chunks_txt(chunks, path)
    write_chunks_txt(chunks, path)

    text = path.read_text()
    assert "Min SIP: ₹ 100" in text
    assert "block_kind" in text and "faq_question" in text
    assert text.count("CHUNK ") == len(chunks)
    assert "=" * 80 in text
