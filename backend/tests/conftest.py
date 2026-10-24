# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Shared test setup: no test may change the surveys of the repository."""
import hashlib
from pathlib import Path

import pytest

SURVEYS = Path(__file__).resolve().parent.parent / "surveys"


def _fingerprint():
    files = sorted(p for p in SURVEYS.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {str(p.relative_to(SURVEYS)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


@pytest.fixture(scope="session", autouse=True)
def surveys_stay_unchanged():
    before = _fingerprint()
    yield
    after = _fingerprint()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"tests changed files under backend/surveys/: {changed}"
