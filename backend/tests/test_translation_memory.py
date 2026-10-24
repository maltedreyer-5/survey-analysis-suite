# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Translation memory: scope and precedence of manual corrections."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.persistence import SurveyDatabase  # noqa: E402


def test_scoped_entry_wins_and_general_is_fallback(tmp_path):
    db = SurveyDatabase(tmp_path / "t.db")
    db.save_translation("Betrieb", "de", "en", "operations")
    db.save_translation("Betrieb", "de", "en", "company", scope="q_employer")
    assert db.get_translation("Betrieb", "de", "en", scope="q_employer") == "company"
    assert db.get_translation("Betrieb", "de", "en", scope="q_it") == "operations"
    assert db.get_translation("Betrieb", "de", "en") == "operations"


def test_automatic_translation_never_replaces_manual_correction(tmp_path):
    db = SurveyDatabase(tmp_path / "t.db")
    db.save_translation("Ja", "de", "en", "Yes (checked)", is_manual=True)
    assert db.save_translation("Ja", "de", "en", "Yes") is False
    assert db.get_translation("Ja", "de", "en") == "Yes (checked)"
    assert db.save_translation("Ja", "de", "en", "Yes!", is_manual=True) is True
    assert db.get_translation("Ja", "de", "en") == "Yes!"
