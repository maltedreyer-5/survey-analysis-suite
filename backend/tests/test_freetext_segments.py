# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Free-text clusters per segment: global clusters counted per segment (core),
segment-specific clusters only where enabled and large enough."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from export import freetext_segments as fs, segment_context  # noqa: E402

CONFIG = {
    "settings": {
        "survey_metadata": {"source_language": "de", "languages": ["de", "en"]},
        "sample_warnings": {"minimum_threshold": 3},
        "segmentation": {
            "specific_clusters_min_n": 4,
            "dimensions": [
                {"id": "region", "label": {"de": "Region"}, "source_column": "REGION",
                 "enable_specific_clusters": True},
                {"id": "role", "label": {"de": "Rolle"}, "source_column": "ROLE"},
            ],
            "cross_filters": [["region", "role"]],
            "cross_filter_min_n": 3,
        },
    },
    "question_groups": {"q_open": {"handler": "freetext", "columns": ["OPEN"]}},
}
# rows 0-5 Nord, 6-8 Süd, 9 Ost (n=1, below threshold)
REGION = ["Nord"] * 6 + ["Süd"] * 3 + ["Ost"]
ROLE = ["A", "A", "A", "B", "B", "B", "A", "A", "A", "B"]
CLUSTERS = [{"id": "kosten", "item_ids": [0, 1, 2, 6, 9]},
            {"id": "zeit", "item_ids": [3, 4, 5, 6, 7, 8]}]


@pytest.fixture
def survey(monkeypatch):
    df = pd.DataFrame({"REGION": REGION, "ROLE": ROLE, "OPEN": ["x"] * 10})
    monkeypatch.setattr(core.state, "config", CONFIG)
    monkeypatch.setattr(core.state, "df", df)
    segment_context.reset_context()
    yield
    segment_context.reset_context()


def test_global_clusters_counted_per_segment(survey):
    data = fs._calculate_segmented_cluster_data("q_open", CLUSTERS, "raw")
    assert data["kosten"]["all"] == {"count": 5, "percentage": 50.0, "n": 10}
    assert data["kosten"]["by_region"]["nord"] == {"count": 3, "percentage": 50.0, "n": 6}
    assert data["zeit"]["by_region"]["sued"] == {"count": 3, "percentage": 100.0, "n": 3}
    # Ost has n=1 < minimum_threshold: not exported
    assert "ost" not in data["kosten"]["by_region"]
    # cross filter from configuration, combination key and threshold
    assert set(data["zeit"]["by_region_role"]) == {"nord|a", "nord|b", "sued|a"}


def test_items_mode_maps_items_to_respondents(survey, monkeypatch):
    # item ids 100+ belong to rows via the DB mapping
    monkeypatch.setattr(fs, "_get_item_response_mapping", lambda qid: {100 + i: i for i in range(10)})
    clusters = [{"id": "kosten", "item_ids": [100, 101, 101, 106]}]  # two items of row 1
    data = fs._calculate_segmented_cluster_data("q_open", clusters, "items")
    assert data["kosten"]["all"]["count"] == 3


def test_segment_results_and_specific_clusters(survey, monkeypatch):
    calls = []

    def fake_specific(qid, qconfig, dim, value):
        calls.append((dim, value))
        return {"n": 6, "clustering": "segment"}
    monkeypatch.setattr(fs, "_specific_clusters", fake_specific)
    seg = fs._calculate_segmented_cluster_data("q_open", CLUSTERS, "raw")
    q = {"id": "q_open", "analysis_mode": "raw", "results": {},
         "clusters": [{"id": c["id"], "results": seg[c["id"]]} for c in CLUSTERS]}
    fs._fill_segment_results(q, None, CONFIG["question_groups"]["q_open"], "raw")
    nord = q["results"]["by_region"]["nord"]
    assert nord["cluster_percentages_by_id"] == {"kosten": 50.0, "zeit": 50.0}
    assert nord["clustering"] == "global" and nord["n"] == 6
    # only Nord reaches specific_clusters_min_n=4; role has them disabled
    assert calls == [("region", "Nord")]
    assert "specific_clusters" in nord and "specific_clusters" not in q["results"]["by_region"]["sued"]
    assert "specific_clusters" not in q["results"]["by_role"]["a"]
