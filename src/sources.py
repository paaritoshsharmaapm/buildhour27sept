"""The source registry: the authority table for every citation in the system.

`format_answer` (P12) resolves a model-supplied `source_id` to a URL through
this registry, and the model itself never types a URL (architecture.md §12,
invariant I1). That is only safe because the registry is validated here, in
code, rather than by reviewer diligence: the host allowlist and the frozen
scheme list are the C1 and R7 controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.config import CONFIG

# PRD §4.2 freezes the corpus at one AMC and five schemes. These are the
# plan-level names; the "Direct - Growth" suffix is carried by the URL and the
# official page, not by the scheme key, so that one filter matches every
# question asked about the scheme.
#
# "HDFC Flexi Cap Fund" is the live name on hdfcfund.com. The PRD and the
# supplied Groww URLs say "HDFC Equity (Flexi Cap) Fund" and
# "hdfc-equity-fund", which is the retired name and now 404s. Verified during
# the P2 corpus inspection.
SCHEMES: tuple = (
    "HDFC Large Cap Fund",
    "HDFC Flexi Cap Fund",
    "HDFC ELSS Tax Saver Fund",
    "HDFC Small Cap Fund",
    "HDFC Balanced Advantage Fund",
)

# One AMC-wide document class is permitted: investor-service pages such as the
# statement-request guide answer "how do I download my capital-gains statement",
# which applies to all five schemes and belongs to none of them. This is a
# single whitelisted escape hatch, not an opening of the scope: a sixth
# *fund* still fails RULE_SCHEME (R7).
GENERAL_SERVICES_SCHEME = "HDFC Mutual Fund (Investor Services)"
ALLOWED_SCHEMES: tuple = SCHEMES + (GENERAL_SERVICES_SCHEME,)

AUTHORITIES: tuple = ("official", "aggregator")

DOC_TYPES: tuple = (
    "scheme_page",
    "factsheet",
    "fee_charges",
    "kim",
    "sid",
    "guide",
    "faq",
)

CSV_COLUMNS: tuple = (
    "source_id",
    "url",
    "authority",
    "scheme",
    "scheme_category",
    "doc_type",
    "discover_only",
    "enabled",
    "retrieved_at",
    "fetch_status",
    "notes",
)

# Rule names appear verbatim in RegistryError messages so the tests can assert
# on which invariant broke, not merely that something did.
RULE_UNIQUE_ID = "unique source_id"
RULE_HOST = "allowed host"
RULE_AUTHORITY = "allowed authority"
RULE_AGGREGATOR = "aggregator must be discover_only"
RULE_SCHEME = "known scheme"
RULE_DOC_TYPE = "known doc_type"


class RegistryError(ValueError):
    """Raised when data/sources.csv violates a corpus invariant."""


@dataclass(frozen=True)
class SourceRow:
    """One row of data/sources.csv."""

    source_id: str
    url: str
    authority: str
    discover_only: bool
    scheme: str
    scheme_category: str
    doc_type: str
    enabled: bool
    retrieved_at: str = ""
    fetch_status: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Registry:
    """Validated sources, keyed by source_id."""

    rows: dict[str, SourceRow]

    def resolve(self, source_id: str) -> SourceRow | None:
        return self.rows.get(source_id)

    def citable_ids(self) -> set[str]:
        """Rows a generated answer is allowed to cite: enabled and official.

        Aggregators are retrievable but never citable, which is how
        PRD §4.3 ("Groww for discovery, official for citation") is enforced
        rather than merely intended.
        """
        return {
            source_id
            for source_id, row in self.rows.items()
            if row.enabled and not row.discover_only
        }

    def citable_for(self, scheme_category: str) -> list[SourceRow]:
        return [
            row
            for row in self.rows.values()
            if row.enabled
            and not row.discover_only
            and row.scheme_category == scheme_category
        ]

    def factsheet_url(self, scheme_category: str) -> str | None:
        candidates = self.citable_for(scheme_category)
        for row in candidates:
            if row.doc_type == "factsheet":
                return row.url
        return candidates[0].url if candidates else None

    def official_page_url(self, scheme_category: str) -> str | None:
        candidates = self.citable_for(scheme_category)
        for row in candidates:
            if row.doc_type == "scheme_page":
                return row.url
        return candidates[0].url if candidates else None


def _fail(row: SourceRow, rule: str, detail: str) -> None:
    raise RegistryError(
        f"source_id={row.source_id!r} violates [{rule}]: {detail}"
    )


def _as_bool(value: str, source_id: str, column: str) -> bool:
    normalised = str(value).strip().lower()
    if normalised in {"true", "yes", "1"}:
        return True
    if normalised in {"false", "no", "0", ""}:
        return False
    raise RegistryError(
        f"source_id={source_id!r} violates [parseable {column}]: "
        f"{value!r} is not a boolean"
    )


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def validate_registry(rows: list[SourceRow]) -> dict[str, SourceRow]:
    """Return rows keyed by source_id, or raise RegistryError on the first breach.

    A soft failure here is how a third-party blog ends up in the corpus, so
    every rule raises rather than warns.
    """
    indexed: dict[str, SourceRow] = {}

    for row in rows:
        if row.source_id in indexed:
            _fail(row, RULE_UNIQUE_ID, "source_id appears more than once")

        host = _host_of(row.url)
        if host not in CONFIG.ALLOWED_HOSTS:
            _fail(
                row,
                RULE_HOST,
                f"host {host!r} is not in CONFIG.ALLOWED_HOSTS",
            )

        if row.authority not in AUTHORITIES:
            _fail(
                row,
                RULE_AUTHORITY,
                f"{row.authority!r} is not one of {AUTHORITIES}",
            )

        if row.authority == "aggregator" and not row.discover_only:
            _fail(
                row,
                RULE_AGGREGATOR,
                "aggregator sources must be discover_only=true so they can "
                "never be cited",
            )

        if row.scheme not in ALLOWED_SCHEMES:
            _fail(
                row,
                RULE_SCHEME,
                f"{row.scheme!r} is not one of the five corpus schemes "
                f"or the investor-services document class",
            )

        if row.doc_type not in DOC_TYPES:
            _fail(
                row,
                RULE_DOC_TYPE,
                f"{row.doc_type!r} is not one of {DOC_TYPES}",
            )

        indexed[row.source_id] = row

    return indexed


def _row_from_mapping(record: dict, position: int) -> SourceRow:
    missing = [column for column in CSV_COLUMNS if column not in record]
    if missing:
        raise RegistryError(
            f"row {position} is missing column(s) {missing}; "
            f"expected header {list(CSV_COLUMNS)}"
        )

    source_id = str(record["source_id"]).strip()
    if not source_id:
        raise RegistryError(f"row {position} has an empty source_id")

    def field(name: str) -> str:
        return str(record[name]).strip()

    return SourceRow(
        source_id=source_id,
        url=field("url"),
        authority=field("authority"),
        discover_only=_as_bool(record["discover_only"], source_id, "discover_only"),
        scheme=field("scheme"),
        scheme_category=field("scheme_category"),
        doc_type=field("doc_type"),
        enabled=_as_bool(record["enabled"], source_id, "enabled"),
        retrieved_at=field("retrieved_at"),
        fetch_status=field("fetch_status"),
        notes=field("notes"),
    )


def load_registry(path: Path | str | None = None) -> Registry:
    """Read and validate the source registry.

    `keep_default_na=False` matters: an empty cell must become an empty string,
    not NaN, or every unfilled `retrieved_at` would be a float in a str field.
    """
    csv_path = Path(path) if path is not None else CONFIG.SOURCES_CSV
    frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    unexpected = [column for column in frame.columns if column not in CSV_COLUMNS]
    if unexpected:
        raise RegistryError(
            f"{csv_path} has unexpected column(s) {unexpected}; "
            f"expected header {list(CSV_COLUMNS)}"
        )

    rows = [
        _row_from_mapping(record, position)
        for position, record in enumerate(frame.to_dict("records"), start=2)
    ]
    return Registry(rows=validate_registry(rows))
