"""Test configuration: make the repo root importable and expose shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import CONFIG  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    # `slow` is first used in P6 (real embedding calls) but is registered here
    # so the marker never warns as an unknown mark.
    config.addinivalue_line("markers", "slow: hits the real model or network")


@pytest.fixture
def cfg() -> object:
    return CONFIG
