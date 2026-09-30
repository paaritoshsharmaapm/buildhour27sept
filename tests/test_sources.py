"""Registry invariants. Each of the six rules gets a fixture that must fail."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.config import CONFIG
from src.sources import (
    CSV_COLUMNS,
    RULE_AGGREGATOR,
    RULE_AUTHORITY,
    RULE_DOC_TYPE,
    RULE_HOST,
    RULE_SCHEME,
    RULE_UNIQUE_ID,
    RegistryError,
    load_registry,
)

# A row that satisfies every invariant, so each test can break exactly one
# thing. The shipped registry contains only aggregator rows, so a valid
# official row has to be constructed here.
VALID_OFFICIAL = {
    "source_id": "hdfc_large_cap_scheme",
    "url": "https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct",
    "authority": "official",
    "scheme": "HDFC Large Cap Fund",
    "scheme_category": "large_cap",
    "doc_type": "scheme_page",
    "discover_only": "false",
    "enabled": "true",
    "retrieved_at": "",
    "fetch_status": "",
    "notes": "",
}


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_shipped_registry_is_valid() -> None:
    registry = load_registry(CONFIG.SOURCES_CSV)
    assert len(registry.rows) == 11
    assert "hdfc_large_cap_groww" in registry.rows
    assert "hdfc_large_cap_scheme" in registry.rows


def test_only_official_rows_are_citable() -> None:
    """The C1 mechanism, now with official rows present.

    Every aggregator row must be excluded, so no generated answer can ever
    cite Groww (PRD §4.3). The five scheme pages and the statement guide are
    the only citable sources.
    """
    registry = load_registry(CONFIG.SOURCES_CSV)
    citable = registry.citable_ids()

    assert len(citable) == 6
    assert not any(sid.endswith("_groww") for sid in citable)
    assert all(registry.resolve(sid).authority == "official" for sid in citable)
    for scheme in ("large_cap", "flexi_cap", "elss", "small_cap", "hybrid"):
        assert any(
            row.scheme_category == scheme
            for sid in citable
            for row in [registry.resolve(sid)]
        )


def test_registry_host_allowlist_matches_the_live_amc_domain() -> None:
    """hdfcmutualfund.com does not resolve; the real AMC domain is hdfcfund.com.

    Regression guard for the P2 discovery: if someone "corrects" ALLOWED_HOSTS
    back to the domain the PRD assumed, this fails.
    """
    assert "hdfcfund.com" in CONFIG.ALLOWED_HOSTS
    assert "hdfcmutualfund.com" not in CONFIG.ALLOWED_HOSTS


def test_rejects_duplicate_source_id(tmp_path: Path) -> None:
    duplicate = dict(VALID_OFFICIAL, url="https://www.hdfcmutualfund.com/other")
    path = write_csv(tmp_path / "dup.csv", [VALID_OFFICIAL, duplicate])
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_UNIQUE_ID in message


def test_rejects_disallowed_host(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "host.csv",
        [dict(VALID_OFFICIAL, url="https://randomblog.example.com/hdfc")],
    )
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_HOST in message
    assert "randomblog.example.com" in message


def test_rejects_unknown_authority(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "auth.csv", [dict(VALID_OFFICIAL, authority="blog")])
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_AUTHORITY in message


def test_rejects_citable_aggregator(tmp_path: Path) -> None:
    """An aggregator marked citable is the exact failure PRD §4.3 forbids."""
    path = write_csv(
        tmp_path / "agg.csv",
        [dict(VALID_OFFICIAL, authority="aggregator", discover_only="false")],
    )
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_AGGREGATOR in message


def test_rejects_unknown_scheme(tmp_path: Path) -> None:
    """Scope-creep guard (R7): a sixth scheme must fail loudly, not be indexed."""
    path = write_csv(
        tmp_path / "scheme.csv", [dict(VALID_OFFICIAL, scheme="Parag Parag Flexi Cap")]
    )
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_SCHEME in message
    assert "Parag Parag Flexi Cap" in message


def test_rejects_unknown_doc_type(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "doctype.csv", [dict(VALID_OFFICIAL, doc_type="blog_post")])
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    message = str(excinfo.value)
    assert "hdfc_large_cap_scheme" in message
    assert RULE_DOC_TYPE in message


def test_rejects_unparseable_boolean(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "bool.csv", [dict(VALID_OFFICIAL, enabled="yes-please")])
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    assert "hdfc_large_cap_scheme" in str(excinfo.value)
    assert "enabled" in str(excinfo.value)


def test_rejects_unexpected_column(tmp_path: Path) -> None:
    # Written by hand: DictWriter silently drops keys absent from fieldnames,
    # which would make this test pass for the wrong reason.
    path = tmp_path / "cols.csv"
    header = ",".join(list(CSV_COLUMNS) + ["secret"]) + "\n"
    body = ",".join(str(VALID_OFFICIAL[column]) for column in CSV_COLUMNS) + ",x\n"
    path.write_text(header + body, encoding="utf-8")
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path)
    assert "secret" in str(excinfo.value)


def test_resolve_and_citable_ids(tmp_path: Path) -> None:
    factsheet = dict(
        VALID_OFFICIAL,
        source_id="hdfc_large_cap_factsheet",
        doc_type="factsheet",
    )
    disabled = dict(VALID_OFFICIAL, source_id="hdfc_off", enabled="false")
    path = write_csv(tmp_path / "mixed.csv", [VALID_OFFICIAL, factsheet, disabled])
    registry = load_registry(path)

    assert registry.resolve("hdfc_large_cap_scheme") is not None
    assert registry.resolve("does_not_exist") is None
    assert registry.citable_ids() == {"hdfc_large_cap_scheme", "hdfc_large_cap_factsheet"}


def test_url_lookups_prefer_the_right_document_type(tmp_path: Path) -> None:
    page = dict(VALID_OFFICIAL, source_id="hdfc_page", doc_type="scheme_page")
    factsheet = dict(
        VALID_OFFICIAL,
        source_id="hdfc_fs",
        doc_type="factsheet",
        url="https://www.hdfcfund.com/documents/large-cap-factsheet",
    )
    guide = dict(VALID_OFFICIAL, source_id="hdfc_guide", doc_type="guide")
    path = write_csv(tmp_path / "docs.csv", [page, factsheet, guide])
    registry = load_registry(path)

    assert registry.factsheet_url("large_cap") == factsheet["url"]
    assert registry.official_page_url("large_cap") == page["url"]


def test_url_lookups_return_none_for_unknown_category(tmp_path: Path) -> None:
    registry = load_registry(write_csv(tmp_path / "one.csv", [VALID_OFFICIAL]))
    assert registry.factsheet_url("elss") is None
    assert registry.official_page_url("elss") is None


def test_factsheet_url_never_returns_a_discover_only_source(tmp_path: Path) -> None:
    """Refusal templates interpolate this URL, so it must be official-only."""
    aggregator = dict(
        VALID_OFFICIAL,
        source_id="agg_fs",
        authority="aggregator",
        discover_only="true",
        doc_type="factsheet",
    )
    official_page = dict(VALID_OFFICIAL, source_id="off_page", doc_type="scheme_page")
    path = write_csv(tmp_path / "agg_fs.csv", [aggregator, official_page])
    registry = load_registry(path)

    assert registry.factsheet_url("large_cap") == official_page["url"]
    assert registry.official_page_url("large_cap") == official_page["url"]
