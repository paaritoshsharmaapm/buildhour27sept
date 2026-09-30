"""S3 - Chunk. CleanDoc -> Chunks, inside a 256 word-piece budget.

`all-MiniLM-L6-v2` truncates at 256 word-pieces, so anything past that is
invisible to search no matter how good the text is (ADR-01). The budget here is
220 body word-pieces plus a 40 overlap, leaving room for the heading prefix and
the citation footer.

Two block kinds are atomic and are never split:

  table_row  A fee slab split across two chunks answers nothing.
  fact       ADR-14. "Min SIP: ₹ 500" is a single retrievable unit. Splitting
             it leaves a chunk containing a bare "₹ 500" with no label, which
             embeds to noise and can be surfaced as a confident wrong answer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from src.cleaner import Block, CleanDoc
from src.config import CONFIG
from src.sources import SourceRow

# Blocks that must land in one chunk.
ATOMIC_KINDS = frozenset({"table_row", "fact"})

# Labelled metrics a user actually asks about. The scheme pages render these as
# a navigation strip ("...Click here to view the Total Expense Ratio 0.77 Lock in
# A lock-in period is..."), so the figure ends up welded between two unrelated
# nav labels inside a ~1,400-word chunk. Retrieval then ranks that chunk on all
# of it and the figure never wins a top-5 slot on its own. Starting a chunk at
# a metric line gives the figure embedding prominence, which is what actually
# decides the top 5. Deliberately not a rewrite of the text: the corpus wording
# is preserved so the coverage invariants still hold verbatim.
METRIC_LABEL_RE = re.compile(
    r"\b(?:TER|Total Expense Ratio|Min(?:imum)?\s+SIP|Lock[-\s]?in|Riskometer|"
    r"Exit\s+Load|Benchmark|Inception\s+Date)\b",
    re.IGNORECASE,
)


class ChunkInvariantError(RuntimeError):
    """Raised when a chunk set violates S3. Never swallowed by callers."""


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    source_id: str
    source_title: str
    url: str
    authority: str
    discover_only: bool
    scheme: str
    scheme_category: str
    doc_type: str
    section: str
    faq_question: str
    chunk_index: int
    token_count: int
    char_start: int
    char_end: int
    retrieved_at: str
    block_kind: str


def _word_count(text: str) -> int:
    return len(text.split())


def chunk_id_for(source_id: str, chunk_index: int, text: str) -> str:
    """Deterministic across runs, so re-ingest is idempotent (ADR-09)."""
    digest = hashlib.sha256(f"{source_id}|{chunk_index}|{text}".encode())
    return digest.hexdigest()[:32]


def _lines_with_offsets(doc: CleanDoc) -> list:
    """(text, kind, level, char_start, char_end) for every rendered line."""
    lines: list = []
    cursor = 0
    for block in doc.blocks:
        if block.kind == "heading":
            text = f"{'#' * block.level} {block.text}"
        else:
            text = block.text
        lines.append((text, block.kind, block.level, cursor, cursor + len(text)))
        cursor += len(text) + 1  # the joining newline
    return lines


def _is_faq_question(text: str) -> bool:
    stripped = text.lstrip("# ").strip()
    return stripped.endswith("?") and 15 <= len(stripped) <= 200


def _block_kind(kinds: list, faq_question: str = "") -> str:
    if faq_question:
        # Checked first: a FAQ chunk is also full of headings and prose, so
    # without this the kind reads as "section" and FAQ chunks become
        # indistinguishable from ordinary sections in eval slicing.
        return "faq"
    if "fact" in kinds:
        return "fact"
    if "table_row" in kinds:
        return "table"
    if any(kind == "heading" for kind in kinds):
        return "section"
    return "prose"


def _emit(
    buffer: list,
    section_line: str,
    section: str,
    faq_question: str,
    row: SourceRow,
    cfg,
    chunk_index: int,
    retrieved_at: str,
) -> Chunk:
    """Render one chunk. Always self-contained: heading, body, then footer."""
    parts: list = []
    if buffer and buffer[0][1] == "heading":
        parts.append(buffer[0][0])
    elif section_line:
        # A chunk that starts mid-section must still name its section, or the
        # embedding loses the topic context entirely.
        parts.append(section_line)
    parts += [text for text, *_ in buffer if not text.startswith("#")]
    text = "\n".join(parts)
    text = f"{text}\nSource: {row.url} (retrieved {retrieved_at})".strip()

    token_count = _word_count(text)
    if token_count > cfg.EMBED_MAX_SEQ:
        raise ChunkInvariantError(
            f"chunk {chunk_index} of {row.source_id} is {token_count} word-pieces, "
            f"over the {cfg.EMBED_MAX_SEQ} budget (ADR-01); the tail is invisible "
            "to the embedder"
        )

    start = buffer[0][3] if buffer else 0
    end = buffer[-1][4] if buffer else 0
    return Chunk(
        chunk_id=chunk_id_for(row.source_id, chunk_index, text),
        text=text,
        source_id=row.source_id,
        source_title=f"{row.scheme} - {row.doc_type}",
        url=row.url,
        authority=row.authority,
        discover_only=row.discover_only,
        scheme=row.scheme,
        scheme_category=row.scheme_category,
        doc_type=row.doc_type,
        section=section,
        faq_question=faq_question,
        chunk_index=chunk_index,
        token_count=token_count,
        char_start=start,
        char_end=end,
        retrieved_at=retrieved_at,
        block_kind=_block_kind([kind for _, kind, *_ in buffer], faq_question),
    )


def _tail(buffer: list, budget: int) -> list:
    """Trailing lines to carry into the next chunk, bounded by words.

    Returns [] if the final line alone exceeds the overlap budget: carrying it
    would already fill the next chunk, and a line that cannot be split (a table
    row) must not be.
    """
    tail: list = []
    total = 0
    for item in reversed(buffer):
        words = _word_count(item[0])
        if total + words > budget:
            break
        tail.insert(0, item)
        total += words
    return tail if len(tail) < len(buffer) else []


def chunk_document(doc: CleanDoc, row: SourceRow, cfg=CONFIG) -> list:
    """CleanDoc -> Chunks. Never silently drops a block."""
    lines = _lines_with_offsets(doc)
    # A prose line that is too long to ever fit beside the carry is sliced, so
    # the buffer bound below is provable rather than hopeful. Atomic kinds are
    # excluded: a fee row is split at its own cost.
    slice_budget = max(40, cfg.CHUNK_SIZE_WP - cfg.CHUNK_OVERLAP_WP)
    expanded: list = []
    for text, kind, level, start, end in lines:
        words = text.split()
        if kind not in ATOMIC_KINDS and len(words) > slice_budget:
            for offset in range(0, len(words), slice_budget):
                piece = " ".join(words[offset : offset + slice_budget])
                expanded.append((piece, kind, level, start + offset, end))
        else:
            expanded.append((text, kind, level, start, end))
    lines = expanded

    chunks: list = []
    buffer: list = []
    section = ""
    section_line = ""
    faq_question = ""

    def flush() -> None:
        nonlocal buffer, faq_question
        if buffer:
            chunks.append(
                _emit(
                    buffer,
                    section_line,
                    section,
                    faq_question,
                    row,
                    cfg,
                    len(chunks),
                    doc.retrieved_at,
                )
            )
        buffer = []
        faq_question = ""

    for text, kind, level, start, end in lines:
        if kind == "heading":
            # ADR-14: a question heading opens a self-contained FAQ chunk.
            if _is_faq_question(text):
                flush()
                faq_question = text.lstrip("# ").strip()
            elif level <= 2:
                flush()
                section = text.lstrip("# ").strip()
                section_line = text
            buffer.append((text, kind, level, start, end))
            continue

        words = _word_count(text)
        # Start a fresh chunk at a labelled metric so the figure is not buried
        # mid-chunk behind unrelated navigation text. Measured, not assumed:
        # TER was present in five chunks per scheme and still lost every top-5
        # slot, because in each one it sat in the middle of ~1,400 characters.
        # Atomic kinds are excluded on purpose. A table row already stands
        # alone, and flushing on one strands its heading in an empty chunk and
        # separates the row from the section it belongs to (ADR-14).
        if (kind not in ATOMIC_KINDS and buffer
                and METRIC_LABEL_RE.search(text)):
            flush()
        if buffer and sum(_word_count(t) for t, *_ in buffer) + words > cfg.CHUNK_SIZE_WP:
            carried = _tail(buffer, cfg.CHUNK_OVERLAP_WP)
            flush()
            buffer = carried
        buffer.append((text, kind, level, start, end))

    flush()
    return chunks


def assert_chunk_invariants(chunks: list, source_ids: set, blocks_by_source=None) -> None:
    """Raise ChunkInvariantError on the first violation.

    `blocks_by_source` maps source_id -> list[Block]. It is required for the
    coverage checks to mean anything: comparing chunk texts against other chunk
    texts cannot detect a block that was dropped before chunking ever saw it,
    which is exactly the failure the table check exists to catch.
    """
    seen: dict = {}
    for chunk in chunks:
        if chunk.token_count > CONFIG.EMBED_MAX_SEQ:
            raise ChunkInvariantError(
                f"{chunk.source_id}#{chunk.chunk_index}: {chunk.token_count} word-pieces "
                f"exceeds {CONFIG.EMBED_MAX_SEQ} (I5/ADR-01)"
            )
        if chunk.chunk_id in seen:
            raise ChunkInvariantError(
                f"duplicate chunk_id {chunk.chunk_id} on {chunk.source_id} "
                f"and {seen[chunk.chunk_id]}"
            )
        seen[chunk.chunk_id] = chunk.source_id

    produced = {chunk.source_id for chunk in chunks}
    for source_id in source_ids:
        if source_id not in produced:
            raise ChunkInvariantError(f"source {source_id} produced zero chunks")

    # I4/R4: a chunk must never carry another source's url.
    for chunk in chunks:
        foreign = [
            other.url
            for other in chunks
            if other.source_id != chunk.source_id and other.url != chunk.url
            and other.url in chunk.text
        ]
        if foreign:
            raise ChunkInvariantError(
                f"chunk {chunk.chunk_id} of {chunk.source_id} cites {foreign[0]} (I4/R4)"
            )

    if blocks_by_source is None:
        return
    # Every kind is enforced, not just the two atomic ones. Prose is the bulk of
    # the corpus and carries fund-manager bios, objective and strategy text, so
    # a regression that drops it is exactly the "silent data loss" this
    # invariant exists to catch. Floors are 1.00 because measured coverage is
    # 100%: anything lower would permit loss we have no evidence is safe.
    for kind in ("table_row", "fact", "paragraph", "list_item", "footnote"):
        _assert_block_coverage(chunks, blocks_by_source, kind, 1.00)


_WHITESPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def block_covered(text: str, blob: str) -> bool:
    """Is this block's content present in `blob`, allowing for slicing?

    A block longer than the slice budget is deliberately cut across chunks with
    a heading prefix on each piece, so requiring the original contiguous string
    would report a 302-word paragraph as lost when every one of its words is
    present. Therefore:

      block that fits one slice -> must appear contiguously and verbatim
      sliced block            -> its significant words must all appear

    The loose branch is only reachable for blocks already too long to keep
    intact, so it cannot mask a dropped short block.
    """
    if text in blob:  # strict, whitespace-exact: the common case
        return True
    if _norm(text) in _norm(blob):
        # Contiguous but whitespace-irregular, e.g. a row with an empty cell
        # holding "|  |". Normalising both sides is required; normalising only
        # the probe makes the comparison fail and reports present text as lost.
        return True
    budget = max(40, CONFIG.CHUNK_SIZE_WP - CONFIG.CHUNK_OVERLAP_WP)
    if len(text.split()) <= budget:
        return False
    words = [word for word in _norm(text).split() if len(word) > 3]
    if not words:
        return False
    return sum(1 for word in words if word in blob) / len(words) >= 0.99


def _assert_block_coverage(
    chunks: list, blocks_by_source: dict, kind: str, floor: float
) -> None:
    """Every table row / fact must appear verbatim in at least one chunk.

    The table check is the highest-value regression guard in the pipeline: if
    fee slabs are being dropped, answers are wrong. The fact check exists
    because the table check would still pass while every min-SIP and TER value
    was buried, since facts are not table rows.
    """
    by_source: dict = {}
    for chunk in chunks:
        by_source.setdefault(chunk.source_id, []).append(chunk)

    for source_id, blocks in blocks_by_source.items():
        wanted = [block.text for block in blocks if block.kind == kind]
        if not wanted:
            continue
        blob = "\n".join(c.text for c in by_source.get(source_id, []))
        missing = [text for text in wanted if not block_covered(text, blob)]
        coverage = (len(wanted) - len(missing)) / len(wanted)
        if coverage < floor:
            raise ChunkInvariantError(
                f"{kind} coverage for {source_id} is {coverage:.0%} "
                f"({len(missing)} of {len(wanted)} missing), floor is {floor:.0%}"
            )


RULE = "=" * 80
THIN = "-" * 80


def write_chunks_txt(chunks: list, path) -> None:
    """Stable, diffable rendering per architecture.md §17.2."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list = []
    for position, chunk in enumerate(chunks):
        lines += [
            RULE,
            f"CHUNK {position:04d}",
            RULE,
            f"chunk_id      : {chunk.chunk_id}",
            f"source_id     : {chunk.source_id}",
            f"source_title  : {chunk.source_title}",
            f"url           : {chunk.url}",
            f"authority     : {chunk.authority:14} scheme: {chunk.scheme}",
            f"scheme_cat    : {chunk.scheme_category:14} doc_type: {chunk.doc_type}",
            f"section       : {chunk.section}",
            f"faq_question  : {chunk.faq_question}",
            f"block_kind    : {chunk.block_kind}",
            f"chunk_index   : {chunk.chunk_index}   token_count: {chunk.token_count}"
            f"   chars: {chunk.char_start}-{chunk.char_end}",
            f"retrieved_at  : {chunk.retrieved_at}",
            THIN,
            chunk.text,
        ]
    path.write_text("\n".join(lines) + "\n")
