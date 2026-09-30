"""S5 - Store. ChromaDB persistence, metadata coercion, and the store contract.

chroma metadata accepts only `str | int | float | bool`. `None`, lists and dicts
are rejected outright, and a bool is coerced to the *string* "True" unless the
caller is explicit. Both failure modes are silent-looking: a rejected upsert
raises far from its cause, and a stringly-typed bool breaks every `where` filter
downstream in Q5. So `chroma_safe()` is the single coercion point and every
metadata dict goes through it.

`require_store()` exists so the app can render "Run `python -m src.ingest` first"
instead of answering from an empty collection, which would otherwise look like
"no good match" and produce a confident no-answer (C7).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache, wraps

import chromadb
from chromadb.errors import NotFoundError

from src.chunker import Chunk
from src.config import CONFIG

_client = None
_collection = None


def _reopen_if_stale(fn):
    """Reopen the collection once if the cached handle no longer resolves.

    A long-lived process (the Streamlit app, an interactive CLI session) caches
    the collection handle in `_collection`. If a rebuild drops and recreates the
    collection while that process is alive, the cached handle points at a
    deleted collection and every subsequent query raises NotFoundError - the app
    showed "Something went wrong handling that question (NotFoundError)" for
    every question, including ones with a perfect chunk waiting. Reopening once
    recovers transparently, and costs nothing in the steady state because the
    happy path is untouched.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotFoundError:
            reset_caches()
            return fn(*args, **kwargs)

    return wrapper


class StoreMissing(RuntimeError):
    """The vector store is absent, empty, or built by a different schema."""


def chroma_dir():
    """Seam for the store location. CONFIG is frozen, so tests redirect this
    rather than the attribute."""
    return CONFIG.CHROMA_DIR


@lru_cache(maxsize=1)
def get_client() -> "chromadb.ClientAPI":
    path = chroma_dir()
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def get_collection():
    global _collection
    if _collection is None:
        _collection = get_client().get_or_create_collection(
            name=CONFIG.COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "schema_version": CONFIG.COLLECTION_SCHEMA_VERSION,
            },
        )
    return _collection


def reset_caches() -> None:
    """Drop the memoised client and collection. Tests call this; ingest does not."""
    global _collection
    _collection = None
    get_client.cache_clear()


def chroma_safe(chunk: Chunk) -> dict:
    """Coerce a Chunk to Chroma's allowed metadata types.

    None becomes "". Bools stay bools. Everything else is str/int/float already.
    """
    metadata = {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "source_title": chunk.source_title,
        "url": chunk.url,
        "authority": chunk.authority,
        "discover_only": chunk.discover_only,
        "scheme": chunk.scheme,
        "scheme_category": chunk.scheme_category,
        "doc_type": chunk.doc_type,
        "section": chunk.section,
        "faq_question": chunk.faq_question,
        "chunk_index": chunk.chunk_index,
        "token_count": chunk.token_count,
        "retrieved_at": chunk.retrieved_at,
        "block_kind": chunk.block_kind,
    }
    coerced: dict = {}
    for key, value in metadata.items():
        if value is None:
            coerced[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            coerced[key] = value
        else:
            coerced[key] = str(value)
    return coerced


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    text: str
    metadata: dict
    score: float
    embedding: list | None = None


@_reopen_if_stale
def count() -> int:
    return get_collection().count()


def drop_collection() -> None:
    """Delete and recreate. Backs --force, and the ADR-09 rebuild path."""
    global _collection
    client = get_client()
    try:
        client.delete_collection(name=CONFIG.COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
    get_collection()


@_reopen_if_stale
def upsert_chunks(chunks: list, vectors, *, force: bool = False) -> int:
    """Idempotent by chunk_id (ADR-09): re-running leaves count() unchanged."""
    if not chunks:
        return 0
    if force:
        drop_collection()

    collection = get_collection()
    collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        embeddings=[list(map(float, vector)) for vector in vectors],
        metadatas=[chroma_safe(chunk) for chunk in chunks],
    )

    actual = collection.count()
    if actual < len(chunks):
        raise StoreMissing(
            f"count mismatch after upsert: {actual} < {len(chunks)}"
        )
    return actual


@_reopen_if_stale
def query_store(qvec, *, where: dict | None = None, n_results: int = 8) -> list:
    """Nearest-neighbour search. Returns Hit objects with score and embedding."""
    collection = get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[list(map(float, qvec))],
        n_results=min(n_results, collection.count()),
        where=where or None,
        include=["documents", "metadatas", "distances", "embeddings"],
    )

    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    embeddings = (result.get("embeddings") or [[]])[0]

    hits: list = []
    for position, chunk_id in enumerate(ids):
        hits.append(
            Hit(
                chunk_id=chunk_id,
                text=documents[position] if position < len(documents) else "",
                metadata=metadatas[position] if position < len(metadatas) else {},
                # Chroma returns cosine *distance*; lower is better. score is
                # similarity so callers can compare it against tau directly.
                score=1.0 - float(distances[position]) if position < len(distances) else 0.0,
                embedding=(
                    list(embeddings[position]) if position < len(embeddings) else None
                ),
            )
        )
    return hits


def require_store() -> None:
    """Raise StoreMissing unless a usable, current store exists (C7)."""
    if not chroma_dir().exists():
        raise StoreMissing(
            f"No vector store at {chroma_dir()}. Run `python -m src.ingest` first."
        )
    try:
        collection = get_collection()
    except Exception as error:
        raise StoreMissing(
            f"Collection {CONFIG.COLLECTION_NAME!r} is unreadable ({error}). "
            "Run `python -m src.ingest` first."
        ) from error

    total = collection.count()
    if total == 0:
        raise StoreMissing(
            f"Collection {CONFIG.COLLECTION_NAME!r} is empty. "
            "Run `python -m src.ingest` first."
        )

    version = (collection.metadata or {}).get("schema_version")
    if version != CONFIG.COLLECTION_SCHEMA_VERSION:
        raise StoreMissing(
            f"Store schema_version={version} but this build expects "
            f"{CONFIG.COLLECTION_SCHEMA_VERSION}. Re-run `python -m src.ingest --force` "
            "to rebuild (ADR-09)."
        )
