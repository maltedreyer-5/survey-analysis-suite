# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Schema version: no migrations; a database of another version is refused untouched."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import persistence  # noqa: E402
from utils.persistence import IncompatibleDatabaseError, SurveyDatabase, check_database_version  # noqa: E402


def version(path):
    return sqlite3.connect(path).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]


def test_fresh_database_carries_the_version(tmp_path):
    db_path = tmp_path / "fresh.db"
    SurveyDatabase(db_path)
    assert version(db_path) == str(persistence.SCHEMA_VERSION)
    check_database_version(db_path)            # opening it again is fine
    SurveyDatabase(db_path)


def test_empty_file_gets_the_schema(tmp_path):
    db_path = tmp_path / "empty.db"
    db_path.touch()
    SurveyDatabase(db_path)
    assert version(db_path) == str(persistence.SCHEMA_VERSION)


@pytest.mark.parametrize("old", ["10", "7", None])
def test_other_version_is_refused_and_left_unchanged(tmp_path, old):
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    if old:
        con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (old,))
    con.execute("CREATE TABLE raw_responses (id INTEGER PRIMARY KEY, data_json TEXT)")
    con.commit(); con.close()
    before = db_path.read_bytes()
    with pytest.raises(IncompatibleDatabaseError, match="import the survey again"):
        SurveyDatabase(db_path)
    assert db_path.read_bytes() == before      # nothing written, nothing migrated


def test_no_foreign_key_from_items_to_raw_response_ids(tmp_path):
    db_path = tmp_path / "fk.db"
    SurveyDatabase(db_path)
    fks = sqlite3.connect(db_path).execute("PRAGMA foreign_key_list(response_items)").fetchall()
    assert not [fk for fk in fks if fk[2] == "raw_responses"]
