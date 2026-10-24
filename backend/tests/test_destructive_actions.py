# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Destructive actions stay within their scope and need confirmation."""
import sqlite3
import sys
import types
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services import pipeline, translations  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


def _db_with_two_imports(tmp_path):
    db = SurveyDatabase(tmp_path / "t.db")
    pid = db.create_project("p")
    a = db.save_import(pid, pd.DataFrame({"x": [1]}), filename="a.csv")
    b = db.save_import(pid, pd.DataFrame({"x": [2]}), filename="b.csv")
    with db._connection() as conn:
        for imp in (a, b):
            conn.execute("INSERT INTO question_summaries (import_id, question_id, lang) VALUES (?,?,?)",
                         (imp, "q1", "de"))
        conn.execute("INSERT INTO translations (source_text, source_lang, target_lang, translated_text)"
                     " VALUES ('Ja','de','en','Yes (corrected)')")
    return db, a, b


def test_regenerate_summaries_only_clears_active_import(tmp_path, monkeypatch):
    db, a, b = _db_with_two_imports(tmp_path)
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "t.db")
    stub = types.ModuleType("batch_export")
    stub.run_full_pipeline = lambda **kw: ("ok", "")
    monkeypatch.setitem(sys.modules, "batch_export", stub)
    state = types.SimpleNamespace(import_id=a, db=db)
    pipeline.regenerate_summaries(state, include_word=False)
    left = sqlite3.connect(tmp_path / "t.db").execute(
        "SELECT import_id FROM question_summaries").fetchall()
    assert left == [(b,)]


def test_translation_delete_requires_confirmation(tmp_path):
    db, a, _ = _db_with_two_imports(tmp_path)
    state = types.SimpleNamespace(db=db)
    status, _ = translations.delete_all_translations(state, "en")
    assert "Not deleted" in status
    count = lambda: sqlite3.connect(tmp_path / "t.db").execute(
        "SELECT COUNT(*) FROM translations").fetchone()[0]
    assert count() == 1
    translations.delete_all_translations(state, "en", confirmed=True)
    assert count() == 0
