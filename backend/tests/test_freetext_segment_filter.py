# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""A segment-filtered DataFrame must restrict extracted items, not only raw answers."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import get_handler  # noqa: E402
from utils.persistence import SurveyDatabase as Database  # noqa: E402

COL = "Q_open"


@pytest.fixture
def db_with_items(tmp_path):
    df = pd.DataFrame({"country": ["FI"] * 2 + ["DE"] * 8,
                       COL: [f"answer {i}" for i in range(10)]})
    db = Database(tmp_path / "t.db")
    project_id = db.create_project("p")
    import_id = db.save_import(project_id, df, filename="x.csv")
    with db._connection() as conn:
        rows = conn.execute("SELECT id, row_index FROM raw_responses WHERE import_id=?",
                            (import_id,)).fetchall()
        for rid, row_index in rows:
            for k in range(2):  # two extracted items per answer
                conn.execute(
                    "INSERT INTO response_items (import_id, row_index, question_id, column_name,"
                    " item_index, original_text, extracted_text, extraction_method)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    # row_index is the data row, as the writers store it
                    (import_id, row_index, "q_open", COL, k, "t", f"item {row_index}.{k}", "llm"))
    return df, db, import_id


def run(df, db, import_id):
    h = get_handler("freetext", "q_open", {"handler": "freetext", "columns": [COL]})
    seen = {}
    h._perform_clustering = lambda items_df, lang: seen.setdefault("items", len(items_df))
    h._enrich_clusters_with_response_ids = lambda: None
    h._build_result = lambda n, column, lang, mode: (seen.setdefault("n", n), MagicMock(metadata={}))[1]
    h.analyze(df, lang="de", db=db, import_id=import_id, use_cached_clustering=False)
    return seen


def test_segment_filter_restricts_items(db_with_items):
    df, db, import_id = db_with_items
    seen = run(df[df.country == "FI"], db, import_id)
    assert seen == {"items": 4, "n": 2}


def test_unfiltered_dataframe_keeps_all_items(db_with_items):
    df, db, import_id = db_with_items
    assert run(df, db, import_id) == {"items": 20, "n": 10}
