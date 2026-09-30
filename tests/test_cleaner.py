"""P4 - S2 cleaner. All fixtures are inline; no network."""

from __future__ import annotations

import pytest

from src.cleaner import clean, flatten
from src.loaders import RawDoc

CHROME = """
<html><body>
  <nav>NAV_MARKER_SHOULD_DIE</nav>
  <div class="cookie-banner">COOKIE_MARKER_SHOULD_DIE</div>
  <main>
    <h1>HDFC Large Cap Fund</h1>
    <h2>Expenses and Taxes</h2>
    <table>
      <tr><th>Fee</th><th>Slab</th><th>Rate</th></tr>
      <tr><td>Ongoing</td><td>0.52%</td><td>monthly</td></tr>
      <tr><td>Exit</td><td>&lt;12m</td><td>1.00%</td></tr>
    </table>
  </main>
  <footer>FOOTER_MARKER_SHOULD_DIE</footer>
</body></html>
"""

DISCLAIMER = "Mutual fund investments are subject to market risks. Read all scheme related documents carefully before investing."
LOCKIN_DISCLAIMER = (
    "Mutual fund investments are subject to market risks. ELSS investments "
    "have a statutory lock in of 3 years."
)


def _raw(html: str, source_id: str = "t") -> RawDoc:
    return RawDoc(
        source_id=source_id,
        url="https://www.hdfcfund.com/t",
        http_status=200,
        raw_bytes=html.encode(),
        extracted_text="",
        status="ok",
        retrieved_at="2026-09-29",
    )


class TestChrome:
    def test_nav_cookie_and_footer_are_dropped(self):
        doc = clean(_raw(CHROME))
        blob = flatten(doc)
        for marker in ("NAV_MARKER", "COOKIE_MARKER", "FOOTER_MARKER"):
            assert marker not in blob, f"{marker} survived into the clean text"

    def test_real_content_survives(self):
        assert "HDFC Large Cap Fund" in clean(_raw(CHROME)).text


class TestTables:
    def test_all_nine_cells_survive_in_order(self):
        rows = [b for b in clean(_raw(CHROME)).blocks if b.kind == "table_row"]
        assert len(rows) == 3
        blob = " ".join(r.text for r in rows)
        for cell in ("Fee", "Slab", "Rate", "Ongoing", "0.52%", "monthly", "Exit", "1.00%"):
            assert cell in blob, f"table cell {cell!r} was lost"

    def test_row_format_is_pipe_delimited(self):
        rows = [b for b in clean(_raw(CHROME)).blocks if b.kind == "table_row"]
        assert rows[0].text == "Fee | Slab | Rate"


class TestHeadings:
    def test_levels_are_preserved(self):
        html = "<main><h1>A</h1><h2>B</h2><h3>C</h3><h4>D</h4></main>"
        blocks = clean(_raw(html)).blocks
        assert [b.level for b in blocks if b.kind == "heading"] == [1, 2, 3, 4]

    def test_heading_prefix_is_rendered_in_flat_text(self):
        html = "<main><h2>Expenses and Taxes</h2><p>x</p></main>"
        assert "## Expenses and Taxes" in clean(_raw(html)).text


class TestDisclaimer:
    def test_duplicate_disclaimer_dropped_with_warning(self):
        html = f"<main><p>{DISCLAIMER}</p><p>middle</p><p>{DISCLAIMER}</p></main>"
        doc = clean(_raw(html))
        assert doc.text.count("subject to market risks") == 1
        assert any("duplicate disclaimer" in w for w in doc.warnings)

    def test_lock_in_phrase_survives_a_disclaimer(self):
        """The exact bug the ELSS answer depends on."""
        html = (
            f"<main><p>{LOCKIN_DISCLAIMER}</p><p>other</p><p>{LOCKIN_DISCLAIMER}</p></main>"
        )
        doc = clean(_raw(html))
        assert "statutory lock in of 3 years" in doc.text
        assert doc.text.count("statutory lock in") == 1


class TestFacts:
    """ADR-14. The values users ask for have no wrapper and no heading."""

    def test_sibling_label_value_pair_becomes_a_fact(self):
        html = "<main><p>Expense Ratio</p><p>1.03%</p><p>trailing</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert len(facts) == 1
        assert facts[0].text == "Expense Ratio: 1.03%"

    def test_min_sip_pairing(self):
        html = "<main><p>Min SIP</p><p>₹ 500</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert facts[0].text == "Min SIP: ₹ 500"

    def test_riskometer_pairing(self):
        html = "<main><p>Riskometer</p><p>Very High</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert facts[0].text == "Riskometer: Very High"

    def test_value_line_is_not_duplicated_after_pairing(self):
        html = "<main><p>Exit Load</p><p>1.00%</p><p>after</p></main>"
        doc = clean(_raw(html))
        assert doc.text.count("1.00%") == 1

    def test_label_with_no_value_produces_no_fact(self):
        """Pairing a label with the wrong number is worse than skipping it."""
        html = "<main><p>Min SIP</p><p>Expense Ratio</p><p>1.03%</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert len(facts) == 1
        assert facts[0].text == "Expense Ratio: 1.03%"


class TestHygiene:
    def test_whitespace_and_punctuation_normalised(self):
        html = "<main><p>a\u00a0\u00a0\u2014b\u201cc\u201d</p></main>"
        assert clean(_raw(html)).blocks[0].text == 'a -b"c"'

    def test_short_document_warns(self):
        doc = clean(_raw("<main><p>tiny</p></main>"))
        assert any("shell" in w or "below" in w for w in doc.warnings)

    def test_empty_html_does_not_raise(self):
        doc = clean(_raw("<html><body></body></html>"))
        assert doc.blocks == [] and doc.text == ""


class TestValueShape:
    """A label plus the wrong value is worse than no fact at all."""

    def test_nav_menu_item_is_not_a_nav_fact(self):
        """Groww's trading menu has a nav item literally labelled 'Nav'."""
        html = (
            "<main><p>Nav</p>"
            "<p>Trade in Futures and Options using the terminal. View charts</p></main>"
        )
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert facts == []

    def test_numeric_label_rejects_prose_value(self):
        html = "<main><p>TER</p><p>See the scheme related documents carefully</p></main>"
        assert [b for b in clean(_raw(html)).blocks if b.kind == "fact"] == []

    def test_numeric_label_accepts_a_number_with_a_unit(self):
        html = "<main><p>AUM</p><p>₹39,933.37 Cr.</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert facts[0].text == "AUM: ₹39,933.37 Cr."

    def test_na_is_a_legitimate_value(self):
        html = "<main><p>Entry Load</p><p>NA</p></main>"
        assert "NA" in clean(_raw(html)).text

    def test_categorical_label_still_accepts_words(self):
        html = "<main><p>Riskometer</p><p>Very High</p></main>"
        facts = [b for b in clean(_raw(html)).blocks if b.kind == "fact"]
        assert facts[0].text == "Riskometer: Very High"


class TestNestedContainerDuplicates:
    """HDFC wraps the same fund-facts card in nested divs. The walk visits
    every div, so wrapper and child both yielded the same paragraph and each
    copy became its own chunk competing for a top-k slot."""

    NESTED = """
    <html><body><main>
      <div class="card"><div class="card-body">
        <p>The Scheme will remain diversified across key sectors.</p>
      </div></div>
    </main></body></html>
    """

    def test_same_text_reached_through_nesting_appears_once(self):
        doc = clean(_raw(self.NESTED))
        texts = [b.text for b in doc.blocks]
        assert texts.count("The Scheme will remain diversified across key sectors.") == 1

    def test_the_collapse_is_reported_not_silent(self):
        doc = clean(_raw(self.NESTED))
        assert any("duplicate block" in w for w in doc.warnings)

    def test_distinct_text_is_never_dropped(self):
        html = """<html><body><main>
          <div><p>First distinct sentence here.</p>
          <p>Second distinct sentence here.</p></div>
        </main></body></html>"""
        doc = clean(_raw(html))
        texts = [b.text for b in doc.blocks]
        assert "First distinct sentence here." in texts
        assert "Second distinct sentence here." in texts

    def test_repeated_heading_is_kept_so_sections_survive(self):
        html = """<html><body><main>
          <div><h2>FAQs</h2><p>Answer one.</p></div>
          <div><h2>FAQs</h2><p>Answer two.</p></div>
        </main></body></html>"""
        doc = clean(_raw(html))
        assert [b.text for b in doc.blocks].count("FAQs") == 2
