# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Segmentation in the analysis tab uses the configured dimensions."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from export import segment_context  # noqa: E402
from services import question_analysis as qa  # noqa: E402
from services import ui_values  # noqa: E402

CONFIG = {
    "settings": {"segmentation": {"dimensions": [
        {"id": "size", "label": {"de": "Größe"}, "source_column": "STAFF",
         "bucketing": {"type": "numeric_buckets", "buckets": [
             {"label": {"de": "klein"}, "max": 50}, {"label": {"de": "groß"}, "min": 50}]}}]}},
    "question_groups": {"q": {"handler": "single_choice", "columns": ["Q"]}},
}


@pytest.fixture
def survey(monkeypatch):
    df = pd.DataFrame({"STAFF": [10, 20, 30, 80, 90], "Q": ["Ja", "Nein", "Ja", "Ja", "Ja"]})
    monkeypatch.setattr(core.state, "config", CONFIG)
    monkeypatch.setattr(core.state, "df", df)
    monkeypatch.setattr(core.state, "db", None)
    for attr in ("current_segment", "segment_column", "segment_filter"):
        monkeypatch.setattr(core.state, attr, None)
    segment_context.reset_context()
    yield df
    segment_context.reset_context()


def test_options_filter_and_breakdown(survey):
    # choices are (label, value) pairs; the code acts only on the values
    assert [v for _, v in qa.get_segmentation_options()] == [ui_values.NO_SEGMENTATION, "size"]
    assert qa.get_segmentation_options()[1] == ("Größe", "size")
    status, n = qa.apply_segmentation("size")
    assert "Größe" in status and n == 5
    assert [v for _, v in qa.get_segment_values()] == [ui_values.ALL, "klein", "groß"]
    qa.set_segment_filter(ui_values.ALL)
    assert core.state.segment_filter is None                      # comparison of all values
    qa.set_segment_filter("groß")
    assert list(core.state.get_filtered_df()["STAFF"]) == [80, 90]
    res = qa.analyze_question_by_segments("q", CONFIG["question_groups"]["q"], skip_existing=False)
    segs = res["by_segment"]["size"]["segments"]
    assert list(segs) == ["klein", "groß"] and segs["klein"]["n"] == 3
    qa.apply_segmentation(ui_values.NO_SEGMENTATION)
    assert core.state.current_segment is None


MATRIX = {"handler": "matrix_likert", "columns": ["M1", "M2"], "label": {"de": "Wie bewerten Sie"},
          "scale": {"de": ["Sehr gut", "Gut", "Schlecht"]}, "scale_order": "descending",
          "item_labels": {"de": ["Bibliothek", "Mensa"]}}


@pytest.mark.parametrize("mode", [None, "mean"])
def test_matrix_question_by_segment_draws_its_own_chart(survey, monkeypatch, mode):
    """A matrix question with a segmentation: small multiples (default) or means, no failing grouped bar."""
    df = pd.DataFrame({"STAFF": [10, 20, 30, 40, 15, 25, 80, 90, 70, 60, 85, 95], "Q": ["Ja"] * 12,
                       "M1": ["Sehr gut", "Gut", "Schlecht", "Gut", "Sehr gut", "Gut"] * 2,
                       "M2": ["Gut", "Gut", "Sehr gut", "Schlecht", "Gut", "Schlecht"] * 2})
    cfg = {**CONFIG, "question_groups": {**CONFIG["question_groups"], "m": MATRIX},
           "settings": {**CONFIG["settings"], **({"matrix_segment_chart": mode} if mode else {})}}
    monkeypatch.setattr(core.state, "config", cfg)
    monkeypatch.setattr(core.state, "df", df)
    segment_context.reset_context()
    qa.apply_segmentation("size")
    qa.set_segment_filter(ui_values.ALL)
    out = qa.analyze_question("m: Wie bewerten Sie")
    text, fig = out[0], out[1]
    assert len(out) == 3 and fig is not None, text
    assert "error" not in text.lower()
