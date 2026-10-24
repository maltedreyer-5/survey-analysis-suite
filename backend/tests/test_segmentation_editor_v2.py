# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Segmentation editor: no silent loss of settings, cross filters and thresholds."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services import segmentation_editor as ed  # noqa: E402

CONFIG = {
    "settings": {"segmentation": {"dimensions": [
        {"id": "region", "label": {"de": "Region", "en": "Region"}, "source_question": "q_region",
         "order": ["Süd", "Nord"], "enable_specific_clusters": True, "description": {"de": "Wo"},
         "value_labels": {"Süd": {"en": "South"}}},
        {"id": "size", "label": {"de": "Größe"}, "source_question": "q_size",
         "bucketing": {"type": "numeric_buckets", "buckets": [
             {"label": {"de": "klein", "en": "small"}, "max": 50},
             {"label": {"de": "groß", "en": "large"}, "min": 50}]}},
    ]}},
    "question_groups": {"q_region": {"handler": "single_choice", "columns": ["R"]},
                        "q_size": {"handler": "numeric", "columns": ["S"]}},
}


@pytest.fixture
def survey(tmp_path, monkeypatch):
    (tmp_path / "questions.yaml").write_text(yaml.safe_dump(CONFIG, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(core, "get_active_config_dir", lambda: tmp_path)
    monkeypatch.setattr(core.state, "config", yaml.safe_load((tmp_path / "questions.yaml").read_text()))
    monkeypatch.setattr(core, "load_config", lambda *a, **k: None)
    return tmp_path


def saved(d):
    return yaml.safe_load((d / "questions.yaml").read_text(encoding="utf-8"))["settings"]["segmentation"]


def test_round_trip_keeps_every_key_and_all_label_languages():
    for dim in CONFIG["settings"]["segmentation"]["dimensions"]:
        expected = {"bucketing": {"type": "identity"}, **dim}  # the editor writes the default explicitly
        assert ed.DimensionDetail.from_dict(dim).to_dict() == expected


def test_saving_an_edited_dimension_keeps_unedited_settings(survey):
    detail = ed.get_dimension_detail(core.state, "region")
    detail.label["de"] = "Landesteil"
    ok, msg = ed.save_dimension(core.state, detail, is_new=False)
    assert ok, msg
    region = saved(survey)["dimensions"][0]
    assert region["label"] == {"de": "Landesteil", "en": "Region"}
    assert region["order"] == ["Süd", "Nord"] and region["enable_specific_clusters"] is True
    assert region["value_labels"] == {"Süd": {"en": "South"}}


def test_cross_filters_and_thresholds(survey):
    assert ed.cross_filter_choices(core.state) == ["region + size"]
    ok, msg = ed.save_segmentation_settings(core.state, ["region + size"], 20, 8)
    assert ok, msg
    seg = saved(survey)
    assert seg["cross_filters"] == [["region", "size"]]
    assert (seg["specific_clusters_min_n"], seg["cross_filter_min_n"]) == (20, 8)


def test_invalid_results_are_refused_before_writing(survey):
    before = (survey / "questions.yaml").read_text(encoding="utf-8")
    ok, msg = ed.save_segmentation_settings(core.state, ["region + nowhere"], 15, 5)
    assert not ok and "invalid" in msg
    assert (survey / "questions.yaml").read_text(encoding="utf-8") == before
    # deleting a dimension that a cross filter references is refused as well
    core.state.config = yaml.safe_load(before)
    ed.save_segmentation_settings(core.state, ["region + size"], 15, 5)
    core.state.config = yaml.safe_load((survey / "questions.yaml").read_text(encoding="utf-8"))
    ok, msg = ed.delete_dimension(core.state, "size")
    assert not ok and "invalid" in msg
