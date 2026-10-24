# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Workbench: items are moved between clusters through the item table, clusters are
deleted with a target for their items; counts follow, unknown names change nothing."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services import workbench_store as wb  # noqa: E402
from services.ui_tables import ITEMS  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = SurveyDatabase(tmp_path / "t.db")
    pid = db.create_project("p")
    imp = db.save_import(pid, pd.DataFrame({"q1": ["costs", "price", "privacy"]}), filename="a.csv")
    with db._connection() as conn:
        conn.execute("INSERT INTO analysis_results (import_id, question_id, handler_type, segment_type, n, result_json) "
                     "VALUES (?, 'q1', 'freetext', 'all', 3, '{}')", (imp,))
    monkeypatch.setattr(core.state, "db", db)
    monkeypatch.setattr(core.state, "import_id", imp)
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {"source_language": "en"}}})
    ids = [db.save_response_item(imp, i, "q1", "q1", 0, t, t, "rule") for i, t in enumerate(["costs", "price", "privacy"])]
    a, b = wb.create_cluster("q1", "Costs"), wb.create_cluster("q1", "Privacy")
    wb.reassign_items_to_cluster(ids[:2], a, "q1"); wb.reassign_items_to_cluster(ids[2:], b, "q1")
    return db, ids, a, b


def table():
    return wb.load_items_table("q1")


def clusters_of(db):
    with db._connection() as conn:
        return dict(conn.execute("SELECT id, cluster_id FROM response_items").fetchall())


def counts():
    return {c["name_source"]: c.get("count") for c in wb.get_clusters_for_question("q1")}


def test_move_and_edit_through_the_table(setup):
    db, ids, a, b = setup
    df = ITEMS.from_ui(table())
    df.loc[df["id"] == ids[0], "cluster"] = "privacy"                 # by name, case does not matter
    df.loc[df["id"] == ids[1], "extracted"] = "high price"
    msg = wb.save_items_table(ITEMS.to_ui(df.to_dict("records")), "q1")
    assert msg == "✅ 1 items updated, 1 moved", msg
    assert clusters_of(db) == {ids[0]: b, ids[1]: a, ids[2]: b}
    assert counts() == {"Costs": 1, "Privacy": 2}


def test_unknown_cluster_changes_nothing(setup):
    db, ids, a, b = setup
    df = ITEMS.from_ui(table())
    df.loc[df["id"] == ids[0], "cluster"] = "Prices"
    df.loc[df["id"] == ids[1], "extracted"] = "changed"
    msg = wb.save_items_table(ITEMS.to_ui(df.to_dict("records")), "q1")
    assert msg.startswith("❌ Unknown cluster: Prices") and "Valid names: Costs, Privacy" in msg
    assert clusters_of(db) == {ids[0]: a, ids[1]: a, ids[2]: b}
    assert [i["extracted_text"] for i in wb.get_items_for_question("q1")[0] if i["id"] == ids[1]] == ["price"]


def test_empty_cluster_removes_the_assignment(setup):
    db, ids, a, b = setup
    df = ITEMS.from_ui(table())
    df.loc[df["id"] == ids[2], "cluster"] = ""
    assert wb.save_items_table(ITEMS.to_ui(df.to_dict("records")), "q1") == "✅ 0 items updated, 1 moved"
    assert clusters_of(db)[ids[2]] is None


def test_delete_cluster_moves_its_items(setup):
    db, ids, a, b = setup
    assert wb.delete_cluster_by_name("q1", "Costs", "Privacy") == "✅ Cluster 'Costs' deleted; its items are moved to 'Privacy'"
    assert clusters_of(db) == {ids[0]: b, ids[1]: b, ids[2]: b} and counts() == {"Privacy": 3}
    assert wb.delete_cluster_by_name("q1", "Privacy", "Privacy").startswith("❌")
    assert wb.delete_cluster_by_name("q1", "Nope").startswith("❌ No cluster")
    assert wb.delete_cluster_by_name("q1", "Privacy", wb.NO_CLUSTER).endswith("now without a cluster")
    assert set(clusters_of(db).values()) == {None}
