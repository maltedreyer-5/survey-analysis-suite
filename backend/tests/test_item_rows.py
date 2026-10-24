# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Extracted items must belong to the respondent they were extracted from.

response_items.row_index holds the data row (raw_responses.row_index) of the
import. Every writer (pipeline, item-extraction tab, workbench preparation) and
every reader (handler item mode, dashboard mapping) must agree on that, also
for the second import of a database and for questions with several text fields.
These tests go through the production writers, not through hand-made rows.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils.llm as llm_mod  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


class NoLLM:
    """Connection test fails: extraction is rule-based only."""
    config = SimpleNamespace(base_url="-", model="-")

    def test_connection(self):
        return False, "offline"


CONFIG = {
    "settings": {"survey_metadata": {"source_language": "de", "languages": ["de"]}},
    "question_groups": {
        "why": {"handler": "freetext", "label": {"de": "Gründe"}, "columns": ["why_1", "why_2"]},
    },
}


def make_df(offset):
    # distinct texts per row and field, so every item identifies its row
    return pd.DataFrame({
        "grp": ["a", "b", "a", "b", "a"],
        "why_1": [f"erster grund {offset + i}" for i in range(5)],
        "why_2": [f"zweiter grund {offset + i}" if i % 2 == 0 else None for i in range(5)],
    })


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda *a, **k: NoLLM())
    db = SurveyDatabase(tmp_path / "t.db")
    project = db.create_project("p")
    first = make_df(0)
    db.save_import(project, first, filename="first.csv")      # occupies raw ids 1..5
    df = make_df(100)
    import_id = db.save_import(project, df, filename="second.csv")
    state = SimpleNamespace(df=df, db=db, import_id=import_id, config=CONFIG)
    return state, df, db, import_id


def check_items(df, db, import_id):
    items = db.get_items_for_question(import_id, "why", None)
    assert items, "no items stored"
    expected = {(row, col) for col in ("why_1", "why_2") for row in df.index if pd.notna(df.at[row, col])}
    seen = set()
    for it in items:
        row = it["row_index"]
        assert row is not None, f"item {it['extracted_text']!r} has no data row"
        assert df.at[row, it["column_name"]] == it["original_text"], (
            f"item {it['extracted_text']!r} linked to row {row}")
        seen.add((row, it["column_name"]))
    assert seen == expected  # every filled field of every respondent, nothing else
    return items


def test_pipeline_writer(setup):
    from services import pipeline
    state, df, db, import_id = setup
    pipeline.batch_extract_items(state)
    check_items(df, db, import_id)


def test_extraction_tab_writers(setup, monkeypatch):
    import services.item_extraction as ie
    state, df, db, import_id = setup
    monkeypatch.setattr(ie, "state", state)
    ie.run_item_extraction("why: Gründe")
    check_items(df, db, import_id)
    ie.run_all_extractions(progress=lambda *a, **k: None)
    check_items(df, db, import_id)


def test_workbench_preparation(setup, monkeypatch):
    import services.item_extraction as ie
    from services import workbench
    import core
    state, df, db, import_id = setup
    monkeypatch.setattr(ie, "state", state)
    for name in ("df", "db", "import_id", "config"):   # the workbench store reads the app state
        monkeypatch.setattr(core.state, name, getattr(state, name))
    status = workbench.prepare_all_freetext(state)
    assert "why" in status and "✗" not in status, status
    check_items(df, db, import_id)


def test_readers_follow_the_data_row(setup, monkeypatch):
    from services import pipeline
    from export import freetext_segments
    from handlers import get_handler
    state, df, db, import_id = setup
    pipeline.batch_extract_items(state)
    items = check_items(df, db, import_id)

    # dashboard export mapping: item id -> data row
    monkeypatch.setattr(freetext_segments, "state", state)
    mapping = freetext_segments._get_item_response_mapping("why")
    assert mapping == {it["item_id"]: it["row_index"] for it in items}

    # handler item mode under a segment filter keeps exactly the items of those rows
    h = get_handler("freetext", "why", CONFIG["question_groups"]["why"])
    seen = {}
    original = h._perform_clustering

    def recording(items_df, lang):
        seen["rows"] = set(h._answers_cache["row_index"])
        return original(items_df, lang)       # no LLM: the fallback cluster
    h._perform_clustering = recording
    group_a = df[df.grp == "a"]
    h.analyze(group_a, lang="de", db=db, import_id=import_id, use_cached_clustering=False)
    assert seen["rows"] == set(group_a.index)


def test_items_of_only_some_fields_are_not_used(setup):
    """Items extracted for the first field only (databases from before all
    fields were covered) must not silently drop the other fields."""
    from handlers import get_handler
    state, df, db, import_id = setup
    for row in df.index:
        db.save_response_item(import_id=import_id, row_index=int(row), question_id="why",
                              column_name="why_1", item_index=0, original_text=df.at[row, "why_1"],
                              extracted_text=df.at[row, "why_1"], extraction_method="rule")
    h = get_handler("freetext", "why", CONFIG["question_groups"]["why"])
    result = h.analyze(df, lang="de", db=db, import_id=import_id, use_cached_clustering=False)
    assert result.metadata.get("analysis_mode") == "fields"
