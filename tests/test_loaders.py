"""P3 - S1 loader. No test here touches the network."""

from __future__ import annotations

import pytest
import requests

from src import loaders
from src.loaders import (
    RawDoc,
    SourceFetchError,
    fetch_all,
    fetch_source,
    scrub_pii,
)
from src.sources import SourceRow

GOOD_HTML = (
    b"<html><body><main>"
    + b"<h2>Fund Facts</h2><p>Riskometer Very High</p><p>Expense Ratio 1.03%</p>"
    + (b"<p>Filler text to clear the minimum extracted length threshold.</p>" * 60)
    + b"</main></body></html>"
)
THIN_HTML = b"<html><body><main><p>Too short to be useful.</p></main></body></html>"


def _row(**overrides) -> SourceRow:
    defaults = {
        "source_id": "unit_test_source",
        "url": "https://www.hdfcfund.com/example",
        "authority": "official",
        "discover_only": False,
        "scheme": "HDFC Large Cap Fund",
        "scheme_category": "large_cap",
        "doc_type": "scheme_page",
        "enabled": True,
    }
    return SourceRow(**{**defaults, **overrides})


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the cache so tests never read or write the real snapshots."""
    target = tmp_path / "raw"
    monkeypatch.setattr(loaders, "raw_dir", lambda: target)
    return target


def _patch_get(monkeypatch, *, body=GOOD_HTML, status=200, error=None):
    """Replace requests.get with a fake; returns the list of call URLs."""

    calls: list = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if error is not None:
            raise error
        response = requests.Response()
        response.status_code = status
        response._content = body
        response.url = url
        return response

    monkeypatch.setattr(loaders.requests, "get", fake_get)
    return calls


class TestStatus:
    def test_200_with_good_text_is_ok(self, monkeypatch):
        _patch_get(monkeypatch)
        doc = fetch_source(_row())
        assert doc.status == "ok"
        assert doc.http_status == 200
        assert "1.03%" in doc.extracted_text
        assert doc.retrieved_at and doc.error is None

    def test_200_with_thin_text_is_short_not_failed(self, monkeypatch):
        """A JS-rendered shell must be reported, never silently accepted."""
        _patch_get(monkeypatch, body=THIN_HTML)
        doc = fetch_source(_row())
        assert doc.status == "short"
        assert "JS-rendered" in doc.error
        assert doc.raw_bytes, "the raw response must still be cached for debugging"

    def test_connection_error_is_failed_and_never_raises(self, monkeypatch):
        _patch_get(monkeypatch, error=requests.ConnectionError("dns go boom"))
        doc = fetch_source(_row())
        assert doc.status == "failed"
        assert doc.extracted_text == ""
        assert "dns go boom" in doc.error or "http_get" in doc.error

    def test_missing_url_is_a_programmer_error(self):
        with pytest.raises(SourceFetchError):
            fetch_source(_row(url=""))


class TestTransportTiering:
    def test_403_on_requests_escalates_to_curl(self, monkeypatch):
        """ADR-12. The AMC refuses requests but serves curl."""
        _patch_get(monkeypatch, status=403)
        seen: list = {}

        def fake_curl(url):
            seen["url"] = url
            return 200, GOOD_HTML

        monkeypatch.setattr(loaders, "_via_curl", fake_curl)
        doc = fetch_source(_row())
        assert doc.status == "ok"
        assert seen["url"] == "https://www.hdfcfund.com/example"

    def test_no_curl_available_degrades_to_blocked(self, monkeypatch):
        _patch_get(monkeypatch, status=403)
        monkeypatch.setattr(loaders, "_via_curl", lambda url: ("failed", b""))
        doc = fetch_source(_row())
        assert doc.status in {"blocked", "failed"}


class TestPiiScrub:
    def test_fake_pan_is_removed(self):
        text = "Contact for KYC: ABCDE1234F and support@hdfcfund.com"
        clean, labels = scrub_pii(text)
        assert "ABCDE1234F" not in clean
        assert "support@hdfcfund.com" not in clean
        assert "[redacted]" in clean
        assert set(labels) == {"pan", "email"}

    def test_10_digit_mobile_is_removed(self):
        clean, labels = scrub_pii("Call 9876543210 for help")
        assert "9876543210" not in clean
        assert "phone_in" in labels

    def test_float_fractions_are_not_mistaken_for_pii(self, monkeypatch):
        """A 12-digit float tail matches the Aadhaar shape. Guarded in PII_PATTERNS."""
        clean, labels = scrub_pii("Return 3y 53.830423188357 and 90.670247196002")
        assert "53.830423188357" in clean
        assert "90.670247196002" in clean
        assert "aadhaar" not in labels

    def test_ordinary_fund_facts_survive_the_scrub(self):
        """The scrub must not eat the numbers the demo is built to answer with."""
        text = (
            "Expense Ratio 1.03% Min SIP 100 Exit Load 1.00% "
            "AUM 75463 Cr NAV 145.2300 Lock-in 3 years"
        )
        clean, _ = scrub_pii(text)
        for fact in ("1.03%", "100", "1.00%", "75463", "145.2300", "3 years"):
            assert fact in clean, f"scrub destroyed {fact!r}"

    def test_otp_only_fires_next_to_a_verification_word(self):
        with_word, _ = scrub_pii("Your OTP 4417 has expired")
        assert "4417" not in with_word
        without_word, _ = scrub_pii("The year 2024 was a good year")
        assert "2024" in without_word

    def test_labels_reach_the_rawdoc(self, monkeypatch):
        html = GOOD_HTML.replace(b"<main>", b"<main><p>PAN ABCDE1234F</p>")
        _patch_get(monkeypatch, body=html)
        doc = fetch_source(_row())
        assert "pan" in doc.pii_labels
        assert "ABCDE1234F" not in doc.extracted_text


class TestCache:
    def test_second_call_is_served_from_cache(self, monkeypatch):
        calls = _patch_get(monkeypatch)
        first = fetch_source(_row())
        second = fetch_source(_row())
        assert len(calls) == 1, "the second call must not hit the network"
        assert second.status == "cached"
        assert second.extracted_text == first.extracted_text
        assert second.http_status == 0

    def test_force_bypasses_the_cache(self, monkeypatch):
        calls = _patch_get(monkeypatch)
        fetch_source(_row())
        fetch_source(_row(), force=True)
        assert len(calls) == 2

    def test_raw_snapshot_is_written_before_extraction(self, monkeypatch):
        _patch_get(monkeypatch)
        fetch_source(_row())
        assert (loaders.raw_dir() / "unit_test_source.html").exists()


class TestFetchAll:
    def test_one_bad_url_does_not_abort_the_run(self, monkeypatch):
        """E11/E12. A dead source must not take the whole ingest down."""

        def fake_get(url, **kwargs):
            if "bad" in url:
                raise requests.ConnectionError("nope")
            response = requests.Response()
            response.status_code = 200
            response._content = GOOD_HTML
            response.url = url
            return response

        monkeypatch.setattr(loaders.requests, "get", fake_get)
        registry = loaders.Registry(
            rows={
                "good": _row(source_id="good", url="https://www.hdfcfund.com/good"),
                "bad": _row(source_id="bad", url="https://www.hdfcfund.com/bad"),
            }
        )
        results = fetch_all(registry, delay_s=0)
        assert [doc.source_id for doc in results] == ["good", "bad"]
        assert {doc.status for doc in results} == {"ok", "failed"}

    def test_disabled_rows_are_skipped(self, monkeypatch):
        calls = _patch_get(monkeypatch)
        registry = loaders.Registry(
            rows={"off": _row(source_id="off", enabled=False, url="https://x.test/a")}
        )
        assert fetch_all(registry, delay_s=0) == []
        assert calls == []
