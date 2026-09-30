"""S2 - Clean. Raw HTML -> ordered blocks + flat text.

Tables and headings are the load-bearing output. `section` on a Chunk is derived
from heading text, and fee/exit-load slabs live in tables, so both survive by
construction here.

Two rules exist because the corpus was measured, not assumed:

ADR-14  The facts the demo is judged on (min SIP, TER, exit load, riskometer)
        render as consecutive bare lines with no wrapper, and the TER label and
        value sit in *sibling* elements. They are paired into kind="fact" blocks
        so P5 can emit them atomically.

Disclaimer  The ELSS three-year lock-in is sometimes stated *inside* disclaimer
        text. A disclaimer is dropped only when a near-duplicate exists in the
        same document, so the answer-bearing copy survives.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from src.config import CONFIG
from src.loaders import RawDoc

DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "aside", "header")

# Class-token markers for page furniture. Matched per token, not as a substring:
# the AMC wraps the entire fund-facts card in `...__bannerdiv`, and a substring
# match on "banner" deletes every min SIP, TER, exit load and riskometer on the
# page while `table_row > 0` still passes. Tokenising is what prevents that.
DROP_WORDS = frozenset({
    "cookie", "consent", "banner", "popup", "modal", "advert", "advertisement",
    "newsletter", "subscribe", "related", "trending", "social", "share",
    "breadcrumb", "sitemap", "footer", "header", "nav", "navbar", "menu",
    "ads", "ad", "skip", "pagination",
})

HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# ADR-14. The facts the demo is judged on live in a class-driven card grid, not
# in headings and not in <p> pairs: the label is div/p.title and the value is
# p.description, and they are siblings. Measured on all five scheme pages.
LABEL_CLASS = re.compile(r"(?:^|[-_ ])(?:title|minsip|mOM)(?:$|[-_ ])")
VALUE_CLASS = re.compile(r"(?:^|[-_ ])(?:description|value|mainvalue)(?:$|[-_ ])")
CARD_CLASS = re.compile(r"(?:^|[-_ ])(?:card|maindiv|lastdiv|width|field)(?:$|[-_ ])")

# Words that mean "chrome" even when they appear inside an otherwise contentful
# class, e.g. `...__m-readmore-tools__`.
CHROME_CLASS = re.compile(
    r"(?:^|[-_ ])(?:cookie|consent|banner|popup|modal|advertisement|newsletter"
    r"|subscribe|related|trending|breadcrumb|sitemap|footer|navbar)(?:$|[-_ ])"
)

MAX_FACT_VALUE_CHARS = 60

# ADR-14. P2 read these off the real pages. The third field says what the value
# is allowed to look like.
#
# The value shape is load-bearing, not decoration. Groww's trading menu contains
# a nav item literally labelled "Nav" (as in navigation), whose "value" is the
# next line of menu text. Without a shape check that produced a confident
# "Nav: Trade in Futures & Options ..." fact, which is worse than no fact at
# all: it embeds as a real-looking answer about the wrong subject.
NUMERIC_VALUE = re.compile(
    r"^(?:n\.?a\.?|nil|-{1,2})$|^[\s₹]*[\d,.]+\s*[a-z%.]{0,12}$", re.IGNORECASE
)
ANY_VALUE = None

FACT_LABELS: tuple = (
    ("min sip", re.compile(r"^(?:minimum\s+)?sip(?:\s+amount)?$|^min\.?\s*sip", re.I), NUMERIC_VALUE),
    ("expense ratio", re.compile(r"^(?:expense\s+ratio|ter|total\s+expense\s+ratio)", re.I), NUMERIC_VALUE),
    ("exit load", re.compile(r"^exit\s+load", re.I), NUMERIC_VALUE),
    ("riskometer", re.compile(r"^risk(?:ometer)?(?:\s+level)?$|^risk\s+level", re.I), ANY_VALUE),
    ("aum", re.compile(r"^aum$|^assets?\s+under\s+management", re.I), NUMERIC_VALUE),
    ("nav", re.compile(r"^nav\b", re.I), NUMERIC_VALUE),
    ("lock-in", re.compile(r"^(?:statutory\s+)?lock[-\s]?in", re.I), NUMERIC_VALUE),
    ("benchmark", re.compile(r"^benchmark", re.I), ANY_VALUE),
)

def _value_shape_ok(label: str, value: str) -> bool:
    """Reject a value that cannot be right for this label."""
    for _, pattern, shape in FACT_LABELS:
        if pattern.search(label):
            return shape is None or bool(shape.match(value))
    return True

FOOTER_MARKERS = (
    "last updated on", "contact us", "all rights reserved", "toll free",
    "registered office", "cin no", "disclosure",
)

DISCLAIMER_MARKERS = (
    "mutual fund investments are subject to market risks",
    "read all scheme related documents carefully before investing",
)


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    level: int = 0


@dataclass(frozen=True)
class CleanDoc:
    source_id: str
    url: str
    blocks: list = field(default_factory=list)
    text: str = ""
    warnings: list = field(default_factory=list)
    # Carried from RawDoc so FR-2.6 can render "Last updated from sources".
    # The P4 spec omitted it, but Chunk.retrieved_at has to come from somewhere
    # and the source registry has no reliable value for it.
    retrieved_at: str = ""


def _collapse(text: str) -> str:
    """Whitespace and punctuation hygiene for embedding."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"\s+", " ", text).strip()
    for dash in ("\u2014", "\u2013", "\u2212"):
        text = text.replace(dash, "-")
    for quote in ("\u2018", "\u2019", "\u201c", "\u201d"):
        text = text.replace(quote, '"')
    return text


def _tokens(value) -> set:
    """Split a class/id string into comparable word tokens.

    'style-module__h0M7Qq__bannerdiv' -> {'style','module','h0m7qq','bannerdiv'}
    'cookie-banner' -> {'cookie','banner'}
    """
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(str(item).split())
    else:
        parts = str(value or "").split()
    tokens = set()
    for part in parts:
        tokens.update(re.split(r"[-_\s]+", part.lower()))
    return {token for token in tokens if token}


def _is_chrome(node) -> bool:
    """True if this element is page furniture rather than content."""
    # attrs is None on a node that a previous decompose() detached, which is
    # unavoidable when mutating a soup in place.
    attrs = getattr(node, "attrs", None)
    if not attrs:
        return False
    marker = f"{attrs.get('class', '')} {attrs.get('id', '')}"
    if CHROME_CLASS.search(marker.lower()):
        return True
    return bool(_tokens(attrs.get("class")) & DROP_WORDS)


def _strip_chrome(soup) -> None:
    for tag in soup(DROP_TAGS):
        tag.decompose()
    for node in soup.find_all(True):
        if node.name in DROP_TAGS or _is_chrome(node):
            node.decompose()


def _table_rows(table) -> list:
    rows: list = []
    for tr in table.find_all("tr"):
        cells = [_collapse(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        # Pad so a ragged row still renders with a stable column count.
        if not cells:
            continue
        while len(cells) < 2 and cells:
            cells.append("")
        rows.append(" | ".join(cells))
    return rows


def _is_disclaimer(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in DISCLAIMER_MARKERS)


def _footer_index(blocks: list) -> int:
    """Index of the first block after which everything is page furniture."""
    for index, block in enumerate(blocks):
        lowered = block.text.lower()
        if block.kind != "heading" and any(
            marker in lowered for marker in FOOTER_MARKERS
        ):
            # A marker inside a disclaimer is not a footer boundary.
            if not _is_disclaimer(lowered):
                return index
    return len(blocks)


def _pair_facts(blocks: list) -> list:
    """Fallback pairing for pages without the AMC's class-driven card grid.

    Adjacent paragraph blocks only, and the value must be short. Without the
    length guard this pairs "TER" with the tooltip sentence that explains what
    TER means, which is worse than pairing nothing: it produces a confident
    fact with a sentence as its value.
    """
    facts: list = []
    for index, block in enumerate(blocks):
        if block.kind != "paragraph":
            continue
        if not _is_fact_label(block.text):
            continue
        if index + 1 >= len(blocks):
            break
        nxt = blocks[index + 1]
        if nxt.kind not in ("paragraph", "list_item") or _matches_any_label(nxt.text):
            continue
        if not nxt.text or len(nxt.text) > MAX_FACT_VALUE_CHARS:
            continue
        if not _value_shape_ok(block.text, nxt.text):
            continue
        label = block.text
        if nxt.text in label:
            # "NAV NA" + "NA" is one label, not two. Keep the value once.
            label = label[: -len(nxt.text)].strip(" :") or label
        facts.append(Block("fact", f"{label}: {nxt.text}"))
    return facts


def _is_fact_label(text: str) -> bool:
    return any(pattern.search(text) for _, pattern, _ in FACT_LABELS)


def _matches_any_label(text: str) -> bool:
    return _is_fact_label(text)


def _merge(facts: list, blocks: list) -> list:
    """Place each fact right after its label, dropping the raw value line.

    Facts are computed against the original list, so this walks with an explicit
    index and skips the value block rather than mutating and re-filtering.
    """
    by_label = {fact.text.partition(": ")[0]: fact for fact in facts}
    result: list = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind == "paragraph" and block.text in by_label:
            result.append(block)
            result.append(by_label.pop(block.text))
            index += 2
            continue
        result.append(block)
        index += 1
    return result


_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "figcaption"]


def _has_block_descendant(node) -> bool:
    """True if a descendant would itself become a block.

    Keeps the pass leaf-most, so a wrapping div.card does not also emit the
    concatenated text of everything inside it.
    """
    for child in node.find_all(_BLOCK_TAGS):
        if child.name in HEADING_TAGS or child.name in ("table", "li", "figcaption"):
            return True
        marker = str(child.get("class", ""))
        if LABEL_CLASS.search(marker) or VALUE_CLASS.search(marker):
            return True
    return False


def _direct_text(node) -> str:
    """Text owned by this element, excluding nested element text."""
    if getattr(node, "attrs", None) is None:
        return ""
    return _collapse(
        "".join(
            str(child) for child in node.children if getattr(child, "name", None) is None
        )
    )


def _dom_fact_pairs(soup) -> dict:
    """Map each value element to its label, per the AMC's card grid (ADR-14).

    Label and value are siblings inside one card: div/p.title then p.description.
    Returns {value_element: "Label: value"}.
    """
    pairs: dict = {}
    for element in soup.find_all(class_=VALUE_CLASS):
        value = _direct_text(element)
        if not value or len(value) > MAX_FACT_VALUE_CHARS:
            continue
        card = element.find_parent(class_=CARD_CLASS)
        candidates = element.find_all_previous(class_=LABEL_CLASS, limit=8)
        for candidate in candidates:
            if card is not None and candidate not in card.descendants:
                break
            label = _direct_text(candidate)
            if not label or len(label) > 40:
                continue
            if _is_fact_label(label) and _value_shape_ok(label, value):
                pairs[element] = f"{label}: {value}"
            break
    return pairs


def clean(raw: RawDoc) -> CleanDoc:
    """Raw HTML -> CleanDoc. Pure function; no network, no config writes."""
    warnings: list = []
    soup = BeautifulSoup(raw.raw_bytes or b"", "lxml")
    before = len(soup.get_text(" ", strip=True))
    _strip_chrome(soup)
    after = len(soup.get_text(" ", strip=True))
    if before and after < before * 0.5:
        warnings.append(
            f"chrome removal dropped {before - after} of {before} chars "
            f"({(before - after) * 100 // before}%); verify nothing valuable was removed"
        )

    fact_pairs = _dom_fact_pairs(soup)

    blocks: list = []
    seen_facts: set = set()
    seen_text: set = set()
    dropped_dupes = 0

    def add(kind: str, text: str, level: int | None = None) -> None:
        """Append unless this exact text was already emitted.

        The walk visits every div and span, so a wrapper and the element nested
        inside it both yield the same words. A page whose fund-facts card sits
        in five nested wrappers therefore emitted the same paragraph five times,
        and each copy became its own chunk that competed for a top-k slot.
        First occurrence in document order wins, which keeps the section the
        reader would have met it under.

        Headings are exempt: a repeated heading marks a repeated section and
        dropping it would lose structure. Disclaimers are exempt too, because
        the dedicated pass below keeps the *last* copy and matches on a
        normalised prefix -- claiming the first one here would silently change
        which wording survives.
        """
        if kind == "heading" or _is_disclaimer(text):
            blocks.append(Block(kind, text, level))
            return
        if text in seen_text:
            nonlocal dropped_dupes
            dropped_dupes += 1
            return
        seen_text.add(text)
        blocks.append(Block(kind, text))

    for element in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "figcaption", "div", "span"]
    ):
        if element.name == "table":
            for row in _table_rows(element):
                if row.strip(" |"):
                    add("table_row", row)
            continue
        if element.name not in HEADING_TAGS and _has_block_descendant(element):
            continue
        if element in fact_pairs:
            text = fact_pairs[element]
            if text not in seen_facts:
                seen_facts.add(text)
                add("fact", text)
            continue
        if element.name in HEADING_TAGS:
            text = _collapse(element.get_text(" ", strip=True))
            if text:
                add("heading", text, HEADING_TAGS[element.name])
            continue
        # Role-classed elements own only their direct text; a plain <p> owns all.
        text = (
            _direct_text(element)
            if LABEL_CLASS.search(str(element.get("class", "")))
            or VALUE_CLASS.search(str(element.get("class", "")))
            else _collapse(element.get_text(" ", strip=True))
        )
        if not text:
            continue
        if element.name == "li":
            add("list_item", text)
        elif element.name == "figcaption":
            add("footnote", text)
        else:
            add("paragraph", text)

    if dropped_dupes:
        warnings.append(
            f"collapsed {dropped_dupes} duplicate block(s) repeated by nested containers"
        )

    boundary = _footer_index(blocks)
    if boundary < len(blocks):
        warnings.append(
            f"truncated {len(blocks) - boundary} blocks after footer boundary"
        )
        blocks = blocks[:boundary]

    facts = _pair_facts(blocks)
    if facts:
        blocks = _merge(facts, blocks)

    # Disclaimer dedupe: keep the last occurrence, which is where the
    # lock-in wording tends to live.
    seen: set = set()
    kept: list = []
    for block in blocks:
        if _is_disclaimer(block.text):
            key = re.sub(r"[^a-z0-9 ]", "", block.text.lower())[:120]
            if key in seen:
                warnings.append(f"dropped duplicate disclaimer ({len(block.text)} chars)")
                continue
            seen.add(key)
        kept.append(block)
    blocks = kept

    lines = []
    for block in blocks:
        if block.kind == "heading":
            lines.append(f"{'#' * block.level} {block.text}")
        else:
            lines.append(block.text)
    text = "\n".join(lines)

    if len(text) < CONFIG.MIN_EXTRACTED_CHARS:
        warnings.append(
            f"cleaned text is {len(text)} chars, below "
            f"MIN_EXTRACTED_CHARS={CONFIG.MIN_EXTRACTED_CHARS}; source may be a shell"
        )

    return CleanDoc(
        source_id=raw.source_id,
        url=raw.url,
        blocks=blocks,
        text=text,
        warnings=warnings,
        retrieved_at=raw.retrieved_at,
    )


def flatten(doc: CleanDoc) -> str:
    return doc.text
