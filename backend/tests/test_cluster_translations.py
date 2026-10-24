# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Cluster texts in any output language are stored and read back (schema v8)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services import workbench_store as wb  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = SurveyDatabase(tmp_path / "t.db")
    pid = database.create_project("p")
    import pandas as pd
    imp = database.save_import(pid, pd.DataFrame({"x": [1]}), filename="a.csv")
    with database._connection() as conn:
        conn.execute("INSERT INTO analysis_results (import_id, question_id, handler_type, segment_type, n, result_json) VALUES (?, 'q1', 'freetext', 'all', 1, '{}')", (imp,))
    monkeypatch.setattr(core.state, "db", database)
    monkeypatch.setattr(core.state, "import_id", imp)
    return database


def stored(db, cid):
    with db._connection() as conn:
        return conn.execute("SELECT cluster_name, translations_json FROM freetext_clusters WHERE id=?",
                            (cid,)).fetchone()


def test_create_and_update_any_language(db):
    cid = wb.create_cluster("q1", "Kosten", name_fr="Coûts", name_en="Costs")
    assert wb.update_cluster(cid, translations={"name_it": "Costi", "description_it": "Troppo caro"})
    assert wb.update_cluster(cid, name_fr="Frais")
    import json
    data = json.loads(stored(db, cid)[1])
    assert data == {"fr": {"name": "Frais"}, "en": {"name": "Costs"},
                    "it": {"name": "Costi", "description": "Troppo caro"}}
    # empty string removes a translation
    wb.update_cluster(cid, translations={"description_it": ""})
    assert "description" not in json.loads(stored(db, cid)[1])["it"]


def test_english_survey_keeps_source_and_german_translation_apart(db, monkeypatch):
    """For a non-German source the source text and a German translation stay separate."""
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {
        "source_language": "en", "languages": ["en", "de", "it"]}}})
    cid = wb.create_cluster("q1", "Too expensive")
    wb.update_cluster(cid, translations={"name_de": "Zu teuer", "name_it": "Troppo caro"})
    c = next(c for c in wb.get_clusters_for_question("q1") if c["id"] == cid)
    assert c["name_source"] == "Too expensive" and c["name_en"] == "Too expensive"
    assert c["name_de"] == "Zu teuer" and c["names"]["it"] == "Troppo caro"
    # editor: one name column per language, source first; saving writes source and translations
    df = wb.load_cluster_editor("q1")
    assert list(df.columns) == ["ID", "Name (EN)", "Name (DE)", "Name (IT)", "Description (EN)", "Items"]
    df.loc[df["ID"] == cid, "Name (IT)"] = "Caro"
    df.loc[df["ID"] == cid, "Name (EN)"] = "Expensive"
    assert wb.save_cluster_changes(df, "q1").startswith("✅")
    c = next(c for c in wb.get_clusters_for_question("q1") if c["id"] == cid)
    assert c["name_source"] == "Expensive" and c["names"]["it"] == "Caro" and c["names"]["de"] == "Zu teuer"
    assert wb.source_name_column() == "Name (EN)"


def test_german_survey_editor_unchanged_layout(db, monkeypatch):
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {"source_language": "de",
                                                                                "languages": ["de", "fr", "en"]}}})
    wb.create_cluster("q1", "Kosten", name_fr="Coûts", name_en="Costs")
    df = wb.load_cluster_editor("q1")
    assert list(df.columns) == ["ID", "Name (DE)", "Name (FR)", "Name (EN)", "Description (DE)", "Items"]
    assert df.iloc[0]["Name (FR)"] == "Coûts"


def test_theme_labels_keyed_by_the_source_language(db, monkeypatch):
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {"source_language": "en"}}})
    pid = db.create_project("themes")
    db.save_theme(pid, "food", label_source="Food", labels={"en": "Food", "de": "Essen", "it": "Cibo"})
    label = db.get_themes(pid)["food"]["label"]
    assert label == {"en": "Food", "de": "Essen", "it": "Cibo"}   # no English text under 'de'
