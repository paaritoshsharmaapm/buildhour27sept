"""Q3-Q7 - retrieval. Query -> grounded hits, or an honest failure.

GROUNDING_THRESHOLD is UNTUNED. It is a starting guess that P17 replaces with a
value fitted to the real score distribution; do not treat 0.35 as validated.
MiniLM cosines for this corpus sit in a narrow band, so tau has to be measured,
not assumed.

tau is only meaningful at all because both sides are L2-normalised (ADR-08):
that is what makes Chroma's cosine distance comparable to a raw dot product.
Un-normalised vectors would silently shift every score and make 0.35 wrong in
a way nothing would flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.config import CONFIG
from src.embedder import embed_query
from src.store import Hit, query_store

# Q3. Substring, lowercased, longest match wins. "tax saver" and "hdfc equity"
# are surface forms the sources actually use; a scheme named in the query is
# what stops a fee question for one scheme being answered from another's page.
SCHEME_ALIASES: dict = {
    "large cap": "large_cap",
    "flexi cap": "flexi_cap",
    "hdfc equity": "flexi_cap",
    "elss": "elss",
    "tax saver": "elss",
    "small cap": "small_cap",
    "balanced advantage": "hybrid",
}


def resolve_scheme(query: str) -> str | None:
    """Alias -> scheme_category, or None for a global search.

    Longest alias first so a specific surface form beats a shorter one that
    happens to be a substring of it. Ties break on earliest position, so
    "small cap vs large cap" resolves to the subject the reader led with.
    """
    lowered = query.lower()
    matches = [
        (-len(alias), lowered.find(alias), alias)
        for alias in SCHEME_ALIASES
        if alias in lowered
    ]
    if not matches:
        return None
    matches.sort()
    return SCHEME_ALIASES[matches[0][2]]


def _unit(vector) -> "np.ndarray | None":
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if not norm:
        return None
    return array / norm


def _cosine(left, right) -> float:
    a, b = _unit(left), _unit(right)
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))


@dataclass(frozen=True)
class RetrievalResult:
    hits: list
    filtered_by: str | None
    max_score: float
    grounded: bool
    candidates: int
    after_mmr: int
    trace: dict | None = field(default=None, compare=False)


def mmr(cands: list, k: int, lam: float) -> list:
    """Maximal Marginal Relevance over the candidate pool (architecture Q6).

    lam * relevance - (1 - lam) * max similarity to anything already selected.
    Pure numpy, no second model.

    **Deviation from architecture.md Q6, deliberate.** The pseudocode there
    subtracts ``1 - cos_sim``, which inverts the penalty: an item identical to
    something already selected scores a penalty of 0, while the most novel item
    in the pool is penalised the most. Measured on a pool of 5 identical chunks
    plus 4 unrelated ones, that version returns all 5 identical chunks -- the
    exact opposite of what MMR is for. The same section's acceptance test
    ("prefers a diverse set over 5 near-identical items") is the real
    intent, so this penalises similarity directly, per Carbonell & Goldstein.

    Pops by index rather than by value: Hit is a frozen dataclass, so
    pool.remove(best) would delete whichever equal element came first. Two
    candidates that compare equal is exactly the case MMR must not mishandle.
    """
    selected: list = []
    pool: list = list(cands)
    while pool and len(selected) < k:
        best_index, best_value = 0, None
        for index, candidate in enumerate(pool):
            if selected:
                redundancy = max(
                    _cosine(candidate.embedding, chosen.embedding)
                    for chosen in selected
                )
            else:
                redundancy = 0.0
            value = lam * candidate.score - (1.0 - lam) * redundancy
            if best_value is None or value > best_value:
                best_index, best_value = index, value
        selected.append(pool.pop(best_index))
    return selected


def retrieve(query: str, *, debug: bool = False) -> RetrievalResult:
    """Q3-Q7. Reports; it does not decide what to do about an ungrounded result.

    That decision belongs to the pipeline, which turns grounded=False into a
    NO_GROUNDING answer. Deciding here would put a policy judgement inside a
    retrieval routine.
    """
    qvec = embed_query(query)
    scheme_id = resolve_scheme(query)

    # C1/I1: unconditional. Aggregator chunks are retrievable but never citable,
    # and this filter is where that is actually enforced. Dropping it when no
    # scheme is named is how Groww nav text ends up in a citable context.
    where: dict = {"discover_only": False}
    if scheme_id:
        where = {"$and": [{"discover_only": False}, {"scheme_category": scheme_id}]}

    pool = query_store(qvec, where=where, n_results=CONFIG.RETRIEVE_POOL)
    hits = mmr(pool, k=CONFIG.TOP_K, lam=CONFIG.MMR_LAMBDA)
    max_score = max((hit.score for hit in hits), default=0.0)

    promoted = False
    if max_score < CONFIG.GROUNDING_THRESHOLD:
        # Q7: the top hit being weak can mean one junk chunk shadowed better
        # ones, so promote the best pre-MMR candidate that does clear tau.
        # grounded stays False: the top hit genuinely was below tau, and the
        # gate must not be talked out of that by its own rescue path.
        better = [hit for hit in pool if hit.score >= CONFIG.GROUNDING_THRESHOLD]
        if better:
            best = max(better, key=lambda hit: hit.score)
            hits = [best] + [hit for hit in hits if hit.chunk_id != best.chunk_id]
            promoted = True

    result = RetrievalResult(
        hits=hits,
        filtered_by=scheme_id,
        max_score=max_score,
        grounded=max_score >= CONFIG.GROUNDING_THRESHOLD,
        candidates=len(pool),
        after_mmr=len(hits),
    )
    if debug:
        object.__setattr__(
            result,
            "trace",
            {
                "where": where,
                "pool": [(hit.chunk_id[:8], round(hit.score, 4)) for hit in pool],
                "promoted": promoted,
                "tau": CONFIG.GROUNDING_THRESHOLD,
            },
        )
    return result
