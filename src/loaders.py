"""S1 - Load. Fetch a source, extract readable text, scrub PII, cache to disk.

Two findings from the P2 corpus inspection shape this module, both recorded in
architecture.md as ADRs:

ADR-11  The primary extractor is BeautifulSoup on `<main>`. trafilatura was the
        original choice and it loses ~88% of each page while dropping the
        expense ratio entirely, which would leave the corpus unable to answer
        the most-asked question. trafilatura survives only as a fallback.

ADR-12  Fetching tiers by *transport* as well as by extractor. hdfcfund.com is
        behind Akamai, which fingerprints the TLS handshake: `requests` is
        refused with 403 whatever headers it sends, while system `curl` is
        served 200 with the same headers. Both then burst-throttle.

No network failure is allowed to raise. One dead URL must never abort a run
(architecture.md §14 E11/E12).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
import trafilatura
from bs4 import BeautifulSoup

from src.config import CONFIG
from src.sources import Registry, SourceRow

# PII patterns, from architecture.md §9.1. Duplicated here rather than imported
# so that loaders does not depend on guardrails, which is built in P9; P9 must
# consolidate these into one module.
#
# `(?<![\d.,])` appears on every digit-run pattern and is load-bearing. Return
# tables carry floats like 53.830423188357, whose 12-digit fraction matches the
# Aadhaar shape and whose 11-digit tail matches a mobile number. Redacting those
# would corrupt the performance data, so a real identifier must not sit against
# a decimal point, a comma separator, or another digit.
PII_PATTERNS: tuple = (
    ("pan", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("aadhaar", re.compile(r"(?<![\d.,])[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?![\d])")),
    ("demat", re.compile(r"(?<![\d.,])\d{16}(?![\d])")),
    ("account", re.compile(r"(?<![\d.,])\d{9,18}(?![\d])")),
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("email", re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("phone_in", re.compile(r"(?<![\d.,])(?:\+91[\s-]?)?[6-9]\d{9}\b")),
    # A bare 4-6 digit number is only PII next to a verification word, otherwise
    # every fee slab and every year in a date would be redacted.
    ("otp", re.compile(r"\b\d{4,6}\b", re.IGNORECASE)),
)

OTP_CONTEXT = re.compile(r"otp|code|pin|verification", re.IGNORECASE)
REDACTION = "[redacted]"

STATUS_OK = "ok"
STATUS_SHORT = "short"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
STATUS_CACHED = "cached"


class SourceFetchError(Exception):
    """Programmer error only. Never raised for a network or HTTP problem."""


@dataclass(frozen=True)
class RawDoc:
    source_id: str
    url: str
    http_status: int
    raw_bytes: bytes
    extracted_text: str
    status: str
    retrieved_at: str
    error: str | None = None
    pii_labels: tuple = ()


# --- PII ------------------------------------------------------------------


def scan_pii(text: str) -> list[str]:
    """Labels of every PII category present, in pattern order, deduplicated."""
    labels: list[str] = []
    for label, pattern in PII_PATTERNS:
        if label == "otp":
            hits = [
                match
                for match in pattern.finditer(text)
                if OTP_CONTEXT.search(text[max(0, match.start() - 30) : match.end() + 30])
            ]
        else:
            hits = list(pattern.finditer(text))
        if hits and label not in labels:
            labels.append(label)
    return labels


def scrub_pii(text: str) -> tuple:
    """Replace every PII match with REDACTION. Returns (clean_text, labels)."""
    labels = scan_pii(text)
    if not labels:
        return text, []

    # Collect spans, longest first, so a PAN inside a longer email is not
    # partially rewritten before the email pattern gets its turn.
    spans: list[tuple[int, int]] = []
    for label, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            if label == "otp" and not OTP_CONTEXT.search(
                text[max(0, match.start() - 30) : match.end() + 30]
            ):
                continue
            spans.append((match.start(), match.end()))
    spans.sort(key=lambda span: (span[0], -span[1]))

    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append(REDACTION)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), labels


# --- extraction ------------------------------------------------------------


def extract_text(html: bytes) -> tuple:
    """Return (text, extractor). BeautifulSoup on `<main>` first (ADR-11)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    main = soup.select_one("main") or soup.body or soup
    text = main.get_text(separator="\n", strip=True)
    if len(text) >= CONFIG.MIN_EXTRACTED_CHARS:
        return text, "beautifulsoup"

    # Fallback for pages without a usable <main>. favor_recall keeps more
    # content than the default, at the cost of some boilerplate.
    recalled = (
        trafilatura.extract(
            html, include_tables=True, favor_recall=True
        )
        or ""
    )
    return (recalled, "trafilatura") if len(recalled) > len(text) else (text, "beautifulsoup")


# --- transports ------------------------------------------------------------


def _via_requests(url: str) -> tuple:
    try:
        response = requests.get(
            url, timeout=30, headers=CONFIG.BROWSER_HEADERS
        )
    except requests.RequestException:
        return STATUS_FAILED, b""
    status = STATUS_BLOCKED if response.status_code == 403 else response.status_code
    return status, response.content


def _via_curl(url: str) -> tuple:
    if shutil.which("curl") is None:
        return STATUS_FAILED, b""
    command = ["curl", "-sS", "--compressed", "-L", "--max-time", "30"]
    for name, value in CONFIG.BROWSER_HEADERS.items():
        command += ["-H", f"{name}: {value}"]
    command += ["-w", "\n%{http_code}", url]
    result = subprocess.run(command, capture_output=True)
    body, _, raw_status = result.stdout.rpartition(b"\n")
    try:
        status = int(raw_status)
    except ValueError:
        return STATUS_FAILED, b""
    return (STATUS_BLOCKED if status == 403 else status), body


def http_get(url: str) -> tuple:
    """Try each transport in turn. Returns (status, body); never raises.

    Only a 403 escalates to curl. A connection error or a 404 fails identically
    under a different TLS client, so retrying those would just double the
    wall-clock spent on a URL that is already known to be dead. The 403 case is
    the one that genuinely differs: Akamai refuses the requests fingerprint and
    serves the same bytes to curl.
    """
    status, body = _via_requests(url)
    if status == 200:
        return status, body
    if status == STATUS_BLOCKED:
        return _via_curl(url)
    return status, body


# --- cache -----------------------------------------------------------------


def raw_dir() -> Path:
    """Seam for the cache location. CONFIG is frozen, so tests redirect this
    instead of the attribute."""
    return CONFIG.RAW_DIR


def cache_path(source_id: str) -> Path:
    return raw_dir() / f"{source_id}.html"


def _from_cache(row: SourceRow) -> RawDoc | None:
    path = cache_path(row.source_id)
    if not path.exists():
        return None
    raw = path.read_bytes()
    text, _ = extract_text(raw)
    text, labels = scrub_pii(text)
    stamp = date.fromtimestamp(path.stat().st_mtime).isoformat()
    return RawDoc(
        source_id=row.source_id,
        url=row.url,
        http_status=0,
        raw_bytes=raw,
        extracted_text=text,
        status=STATUS_CACHED,
        retrieved_at=stamp,
        error=None,
        pii_labels=tuple(labels),
    )


# --- public API ------------------------------------------------------------


def fetch_source(row: SourceRow, *, force: bool = False) -> RawDoc:
    """Fetch one source, extract, scrub, and cache. Never raises for network."""
    if not row.url or not row.source_id:
        raise SourceFetchError("SourceRow needs both source_id and url")

    if not force:
        cached = _from_cache(row)
        if cached is not None:
            return cached

    raw_dir().mkdir(parents=True, exist_ok=True)
    status, body = http_get(row.url)
    today = date.today().isoformat()

    if status != 200 or not body:
        return RawDoc(
            source_id=row.source_id,
            url=row.url,
            http_status=status if isinstance(status, int) else 0,
            raw_bytes=b"",
            extracted_text="",
            status=STATUS_FAILED if status in (0, STATUS_FAILED) else STATUS_BLOCKED,
            retrieved_at=today,
            error=f"http_get returned {status!r}",
        )

    # C2 forbids storing PII anywhere, so extracted_text is scrubbed before it
    # leaves this function. The raw snapshot is written verbatim on purpose:
    # redacting it would corrupt the markup and make the cached copy useless for
    # re-running extraction after a parser fix. It stays gitignored instead.
    # scrub_snapshots() sanitises the directory in place when it is about to be
    # committed, which is the one case where a mangled copy is acceptable.
    text, _ = extract_text(body)
    text, labels = scrub_pii(text)

    cache_path(row.source_id).write_bytes(body)

    if len(text) < CONFIG.MIN_EXTRACTED_CHARS:
        return RawDoc(
            source_id=row.source_id,
            url=row.url,
            http_status=200,
            raw_bytes=body,
            extracted_text=text,
            status=STATUS_SHORT,
            retrieved_at=today,
            error=(
                f"extracted {len(text)} chars, below "
                f"MIN_EXTRACTED_CHARS={CONFIG.MIN_EXTRACTED_CHARS}; "
                "likely JS-rendered"
            ),
            pii_labels=tuple(labels),
        )

    return RawDoc(
        source_id=row.source_id,
        url=row.url,
        http_status=200,
        raw_bytes=body,
        extracted_text=text,
        status=STATUS_OK,
        retrieved_at=today,
        error=None,
        pii_labels=tuple(labels),
    )


def scrub_snapshots() -> list:
    """Redact PII in the cached snapshots in place, so data/raw/ can be committed.

    Off-line and destructive, hence deliberately not part of fetch_source. Reports
    the source_id and labels of every file it changed.
    """
    changed: list = []
    for path in sorted(raw_dir().glob("*.html")):
        original = path.read_text(errors="replace")
        cleaned, labels = scrub_pii(original)
        if labels and cleaned != original:
            path.write_text(cleaned, encoding="utf-8")
            changed.append((path.stem, labels))
    return changed


def fetch_all(
    registry: Registry, *, force: bool = False, delay_s: float | None = None
) -> list:
    """Fetch every enabled source. Never raises; one bad URL cannot abort a run."""
    delay = CONFIG.FETCH_DELAY_S if delay_s is None else delay_s
    results: list = []
    rows = [row for row in registry.rows.values() if row.enabled]
    for index, row in enumerate(rows):
        if index and delay:
            time.sleep(delay)
        results.append(fetch_source(row, force=force))
    return results


def main() -> int:
    from src.sources import load_registry

    results = fetch_all(load_registry(CONFIG.SOURCES_CSV))
    print(f"{'source_id':30} {'status':9} {'http':>5} {'chars':>7} {'pii':22} note")
    for doc in results:
        note = (doc.error or "")[:44]
        print(
            f"{doc.source_id:30} {doc.status:9} {doc.http_status:>5} "
            f"{len(doc.extracted_text):>7} {','.join(doc.pii_labels) or '-':22} {note}"
        )
    usable = [d for d in results if d.status in (STATUS_OK, STATUS_CACHED)]
    print(f"\nusable: {len(usable)}/{len(results)}")
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
