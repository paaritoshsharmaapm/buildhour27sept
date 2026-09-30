"""Central configuration. Every tunable in the system lives here (invariant I6).

No logic module may hardcode a threshold, a model name, or a path. The
`GROUNDING_THRESHOLD` value below is a starting guess and is calibrated from
score data in implementation.md P17.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Loaded before the dataclass body evaluates, so GROQ_MODEL can read the env.
load_dotenv()

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


@dataclass(frozen=True)
class Config:
    """Frozen so a phase cannot mutate configuration at runtime."""

    # --- paths -------------------------------------------------------------
    ROOT: Path = Path(__file__).resolve().parent.parent
    SOURCES_CSV: Path = ROOT / "data" / "sources.csv"
    RAW_DIR: Path = ROOT / "data" / "raw"
    CHUNKS_TXT: Path = ROOT / "data" / "chunks.txt"
    EMBEDDINGS_TXT: Path = ROOT / "data" / "embeddings.txt"
    MANIFEST: Path = ROOT / "data" / "ingest_manifest.json"
    CHROMA_DIR: Path = ROOT / "chroma_db"

    # --- models ------------------------------------------------------------
    EMBED_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBED_DIM: int = 384
    # Hard limit of the embedding model. Chunks longer than this are SILENTLY
    # truncated at encode time, making their tails permanently unretrievable.
    # Do not exceed (architecture.md ADR-01, invariant I5).
    EMBED_MAX_SEQ: int = 256
    # The brief fixed llama-3.1-8b-instant, but Groq no longer serves it:
    # a live key returns 404 model_not_found. Verified working with
    # response_format=json_object, which Q8 depends on.
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    # Name, not value. The key stays in the environment; nothing reads it here.
    GROQ_API_KEY_ENV: str = "GROQ_API_KEY"
    LLM_TEMPERATURE: float = 0.0
    LLM_MAX_TOKENS: int = 220
    LLM_TIMEOUT_S: int = 20

    # --- chunking ----------------------------------------------------------
    # 256 total budget minus ~16 for the heading prefix and ~12 for the URL
    # footer (architecture.md §7 S3). Overlap is ~18% of the body.
    CHUNK_SIZE_WP: int = 220
    CHUNK_OVERLAP_WP: int = 40
    MIN_EXTRACTED_CHARS: int = 2000

    # --- retrieval ---------------------------------------------------------
    TOP_K: int = 5
    # Candidate pool for MMR. Fetching only TOP_K makes re-ranking meaningless
    # (architecture.md ADR-06).
    RETRIEVE_POOL: int = 20
    MMR_LAMBDA: float = 0.7
    # Hits handed to the LLM, out of the TOP_K retrieved. 3 of 5 measured at
    # 565 vs 838 prompt tokens, which is the difference between fitting inside
    # the free-tier token-per-minute budget and getting a 429. Retrieval still
    # returns TOP_K, so citation support and the debug trace are unaffected;
    # only the prompt shrinks (P17, human decision).
    CONTEXT_TOP_K: int = 3
    # Calibrated 2026-09-30 from eval/run_eval.py --sweep over 25 golden cases.
    # in-corpus recall 1.00, out-of-corpus gated 0.00 - the gate never fires
    # because MiniLM cosine similarity between any financial question and any
    # financial chunk is high (in-corpus min 0.6463, out-of-corpus max 0.8064),
    # so the distributions overlap and there is no knee. tau 0.60 was the only
    # separating value but is tuned on n=1 with a 0.078 margin, so it was
    # rejected as overfitting. Safety comes from Q1/Q2, the model's own
    # decline, and the numeric support guard - not from this number.
    GROUNDING_THRESHOLD: float = 0.35
    MAX_SENTENCES: int = 3
    # Raised from 2.0 to 6.0 at P3. Akamai throttles to roughly one success per
    # cooldown window, and 2.0s pacing lost most of the official corpus.
    FETCH_DELAY_S: float = 6.0
    # hdfcfund.com sits behind Akamai bot protection. A bare requests.get
    # returns 403 whatever headers it sends; system curl with the same headers
    # is served 200. See ADR-12 for the transport tiering this forces.
    # Bursts of requests without the delay get rate-limited even with headers.
    # A dict is a mutable default, which a dataclass rejects outright, so this
    # is a factory returning a fresh copy.
    BROWSER_HEADERS: dict = field(
        default_factory=lambda: dict(_BROWSER_HEADERS),
    )

    # --- collections and identity -----------------------------------------
    COLLECTION_NAME: str = "mf_faq_chunks"
    # Bump when the chunk metadata schema changes, to force a rebuild rather
    # than silently mixing old and new documents (architecture.md §6.4).
    COLLECTION_SCHEMA_VERSION: int = 2
    # C1 enforcement. Hosts are checked in code, not by convention, so adding
    # one requires a visible edit (architecture.md §6.1 invariant 2).
    # hdfcfund.com is the AMC's real domain; "hdfcmutualfund.com" does not
    # resolve and was an assumption carried by PRD §4.3. See docs/chunking_proposal.md.
    ALLOWED_HOSTS: tuple = (
        "hdfcfund.com",
        "www.hdfcfund.com",
        "files.hdfcfund.com",
        "amfiindia.com",
        "www.amfiindia.com",
        "sebi.gov.in",
        "www.sebi.gov.in",
        "groww.in",
    )


CONFIG = Config()
