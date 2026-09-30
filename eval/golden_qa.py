"""Loader for the golden Q&A set. Kept separate so run_eval.py reads the JSON
as data and never mutates it — the golden set is the fixed point of the whole
evaluation, and P16 explicitly forbids special-casing any query id.
"""

from __future__ import annotations

import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_qa.json"


def load_golden() -> list:
    cases = json.loads(GOLDEN_PATH.read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("golden_qa.json must be a non-empty list of cases")
    for case in cases:
        for field in ("id", "query", "expected_kind"):
            if field not in case:
                raise ValueError(f"case {case.get('id', '?')} is missing {field!r}")
    return cases
