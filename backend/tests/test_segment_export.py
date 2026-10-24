# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Per-segment and cross-filter results come from the configured dimensions."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from export import segment_context, segments  # noqa: E402

CONFIG = {
    "settings": {
        "survey_metadata": {"source_language": "de", "languages": ["de", "en", "it"]},
        "sample_warnings": {"minimum_threshold": 5},
        "segmentation": {
            "dimensions": [
                {"id": "region", "label": {"de": "Region", "en": "Region"},
                 "source_question": "q_region", "order": ["Süd", "Nord"]},
                {"id": "role", "label": {"de": "Rolle"}, "source_column": "ROLE",
                 "value_labels": {"Lehre": {"en": "Teaching", "it": "Insegnamento"}}},
            ],
            "cross_filters": [["role", "region"]],
            "cross_filter_min_n": 3,
        },
    },
    "question_groups": {
        "q_region": {"handler": "single_choice", "columns": ["REGION"],
                     "options": {"de": ["Nord", "Süd"], "en": ["North", "South"]}},
        "q_sat": {"handler": "single_choice", "columns": ["SAT"]},
    },
}


@pytest.fixture
def survey(monkeypatch):
    df = pd.DataFrame({
        "REGION": ["Nord"] * 6 + ["Süd"] * 4,
        "ROLE": ["Lehre"] * 3 + ["Verwaltung"] * 3 + ["Lehre"] * 2 + ["Verwaltung"] * 2,
        "SAT": ["Ja", "Nein"] * 5,
    })
    monkeypatch.setattr(core.state, "config", CONFIG)
    monkeypatch.setattr(core.state, "df", df)
    segment_context.reset_context()
    yield df
    segment_context.reset_context()


def test_segment_metadata_keys_labels_and_order(survey):
    meta = segment_context.get_context().segments_metadata()
    assert [v["key"] for v in meta["region"]["values"]] == ["sued", "nord"]   # configured order
    sued = meta["region"]["values"][0]["label"]
    assert sued == {"de": "Süd", "en": "South", "it": "Süd"}  # options aligned; it falls back
    lehre = next(v for v in meta["role"]["values"] if v["key"] == "lehre")
    assert lehre["label"]["it"] == "Insegnamento"


def test_single_dimension_results_use_materialised_segments(survey):
    res = segments._load_segmented_results("q_sat", "region")
    assert set(res) == {"nord", "sued"}
    assert res["nord"]["n"] == 6 and res["sued"]["n"] == 4


def test_cross_filters_follow_configuration_and_threshold(survey):
    assert segments._get_segment_combinations() == [("region", "role")]
    res = segments._load_cross_segment_results("q_sat", ("region", "role"))
    # Süd|Lehre and Süd|Verwaltung have n=2 < cross_filter_min_n=3
    assert set(res) == {"nord|lehre", "nord|verwaltung"}
