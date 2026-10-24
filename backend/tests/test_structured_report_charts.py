# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Charts by group of the complete export: the same order and colours as the overall
chart, no comparison of a question with the groups of its own answers, numeric
questions by median and quartiles."""
import sys
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from utils.charts import ChartGenerator  # noqa: E402
from utils import export_structured as es  # noqa: E402

SCALE = {"en": ["Very good", "Good", "Poor", "Don't know"], "de": ["Sehr gut", "Gut", "Schlecht", "Weiß nicht"]}


def test_category_order_configured_numeric_or_by_share():
    seq = ChartGenerator._category_sequence
    assert seq({"Poor", "Very good", "Good"}, {}, ["Very good", "Good", "Poor"]) == ["Very good", "Good", "Poor"]
    assert seq({"10", "2", "0", "1"}, {}) == ["0", "1", "2", "10"]
    data = {"A": {"x": 10, "y": 70}, "B": {"x": 20, "y": 50}}
    assert seq({"x", "y"}, data) == ["y", "x"]


def test_scale_colours_in_the_language_of_the_report(monkeypatch):
    monkeypatch.setattr(es, "_src", lambda: "en")
    rep = es.StructuredReportExporter.__new__(es.StructuredReportExporter)
    q = {"handler": "matrix_likert", "scale": SCALE, "scale_order": "descending"}
    en, de = rep._scale_colours(q, "en"), rep._scale_colours(q, "de")
    assert set(de) == set(SCALE["de"]) and de["Sehr gut"] == en["Very good"] and de["Weiß nicht"] == en["Don't know"]
    assert rep._scale_colours({"handler": "single_choice", "options": {"en": ["A", "B"]}}, "en") == {}


def test_dimension_source(monkeypatch):
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {"source_language": "en"},
        "segmentation": {"dimensions": [
            {"id": "role", "label": {"en": "Role"}, "source_question": "role", "bucketing": {"type": "identity"}},
            {"id": "site", "label": {"en": "Site"}, "source_column": "site", "bucketing": {"type": "identity"}}]}},
        "question_groups": {"role": {"handler": "single_choice", "columns": ["role"]}}})
    assert es._dimension_source("role") == "role"
    assert es._dimension_source("site") is None


def test_numeric_question_by_group_is_a_median_chart(monkeypatch):
    rep = es.StructuredReportExporter.__new__(es.StructuredReportExporter)
    rep.chart_gen = ChartGenerator()
    drawn = {}
    monkeypatch.setattr(rep.chart_gen, "segment_means_bar",
                        lambda **kw: drawn.update(kw) or None)
    segments = {"Students": {"result": SimpleNamespace(data={"median": 24, "q25": 21, "q75": 30, "n": 144}), "n": 144},
                "Staff": {"result": SimpleNamespace(data={"median": 44, "q25": 35, "q75": 52, "n": 96}), "n": 96}}
    rep._add_numeric_segment_chart(None, "Age", segments, "Role", "en", {"numeric": {"unit": "years", "decimals": 0}})
    assert drawn["segment_stats"]["Students"] == {"mean": 24, "low": 21, "high": 30, "n": 144}
    assert drawn["xlabel"] == "years" and "Median" in drawn["note"]
