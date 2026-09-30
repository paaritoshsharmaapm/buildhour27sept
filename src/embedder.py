"""S4 - Embed. One model, used for documents and queries alike.

ADR-08: `normalize_embeddings=True` alongside Chroma's `hnsw:space=cosine`.
The two must agree or the grounding threshold stops meaning what it says, so
`assert_normalised()` checks the property rather than trusting the flag.
"""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import CONFIG

_model = None


def get_model() -> SentenceTransformer:
    """Lazily loaded. Importing the package is slow and the pipeline is not
    always run end to end."""
    global _model
    if _model is None:
        _model = SentenceTransformer(CONFIG.EMBED_MODEL)
    return _model


def embed_texts(texts: list) -> "np.ndarray":
    """L2-normalised embeddings, shape (n, EMBED_DIM). Order is preserved."""
    if not texts:
        return np.zeros((0, CONFIG.EMBED_DIM), dtype=np.float32)
    vectors = get_model().encode(
        list(texts),
        batch_size=32,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    assert_normalised(vectors)
    return vectors.astype(np.float32)


def embed_query(query: str) -> "np.ndarray":
    """Same model, same normalization. A query embedded any other way is
    compared against document vectors in a different space and never matches."""
    return embed_texts([query])[0]


def assert_normalised(vectors: "np.ndarray", tolerance: float = 1e-3) -> None:
    norms = np.linalg.norm(vectors, axis=1)
    worst = float(np.max(np.abs(norms - 1.0))) if norms.size else 0.0
    if worst > tolerance:
        raise ValueError(
            f"embeddings are not L2-normalised (max |norm-1| = {worst:.2e}). "
            "ADR-08 requires normalize_embeddings=True to match hnsw:space=cosine; "
            "check that nothing is overriding it (architecture.md §24)."
        )


def write_embeddings_txt(vectors: "np.ndarray", labels: list, path, preview: int = 12) -> None:
    """Human-readable vector dump.

    A full 384-float dump for every chunk is unreadable, so each row shows the
    leading dimensions plus the norm. The norm is the part that actually
    matters: it is what proves ADR-08 holds, and it is impossible to eyeball
    from a wall of decimals.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    norms = np.linalg.norm(vectors, axis=1)
    lines = [
        "=" * 100,
        f"EMBEDDINGS  model={CONFIG.EMBED_MODEL}  dim={vectors.shape[1]}  n={len(vectors)}",
        f"normalized={CONFIG.normalize if hasattr(CONFIG, 'normalize') else True}  "
        f"mean_norm={norms.mean():.6f}  min={norms.min():.6f}  max={norms.max():.6f}",
        f"showing first {preview} of {vectors.shape[1]} dimensions per row",
        "=" * 100,
        "",
    ]
    for vector, label in zip(vectors, labels):
        head = ", ".join(f"{value:+.4f}" for value in vector[:preview])
        lines.append(
            f"{label}\n  [{head}, ...]\n  L2 norm = {np.linalg.norm(vector):.6f}\n"
        )
    path.write_text("\n".join(lines) + "\n")
