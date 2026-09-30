"""P6 - S4 embedder. The model is loaded for real; that is the point."""

from __future__ import annotations

import numpy as np
import pytest

from src.config import CONFIG
from src.embedder import assert_normalised, embed_query, embed_texts, write_embeddings_txt

MINI = "all-MiniLM-L6-v2"


def test_output_shape_and_dtype():
    vectors = embed_texts(["HDFC Large Cap Fund", "HDFC Flexi Cap Fund"])
    assert vectors.shape == (2, CONFIG.EMBED_DIM)
    assert vectors.dtype == np.float32


def test_every_vector_is_l2_normalised():
    """ADR-08. Chroma runs cosine, so a non-unit vector silently rescales tau."""
    vectors = embed_texts([f"chunk {i}" for i in range(8)])
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


def test_assert_normalised_rejects_an_unnormalised_matrix():
    with pytest.raises(ValueError, match="ADR-08"):
        assert_normalised(np.array([[3.0, 4.0]], dtype=np.float32))


def test_empty_input_is_well_formed():
    assert embed_texts([]).shape == (0, CONFIG.EMBED_DIM)


def test_query_and_document_share_one_space():
    """A separately-configured query path is the classic silent failure."""
    query = embed_query("What is the minimum SIP for HDFC Large Cap Fund?")
    docs = embed_texts(["What is the minimum SIP for HDFC Large Cap Fund?"])
    assert np.allclose(query, docs[0], atol=1e-5)


def test_similar_text_scores_higher_than_unrelated_text():
    vectors = embed_texts([
        "The minimum SIP for HDFC Large Cap Fund is ₹ 100",
        "HDFC Flexi Cap Fund charges an expense ratio of 0.77 percent",
        "Monsoon flooding closed the coastal highway for two days",
    ])
    related = float(vectors[0] @ vectors[1])
    unrelated = float(vectors[0] @ vectors[2])
    assert related > unrelated


def test_determinism():
    first = embed_texts(["expense ratio 1.03 percent"])
    second = embed_texts(["expense ratio 1.03 percent"])
    assert np.allclose(first, second)


def test_write_embeddings_txt_is_readable(tmp_path):
    vectors = embed_texts(["alpha text", "beta text"])
    path = tmp_path / "embeddings.txt"
    write_embeddings_txt(vectors, ["chunk 0000", "chunk 0001"], path)
    text = path.read_text()
    assert MINI in text
    assert "L2 norm" in text
    assert "mean_norm=1.000000" in text
    assert "chunk 0000" in text
