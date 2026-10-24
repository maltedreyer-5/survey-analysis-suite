# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Rendered charts say what the data say: axis titles, ticks, order, legend,
sums of bars, notes.
"""
import pathlib
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def german_survey(monkeypatch):
    state = types.SimpleNamespace(config={"settings": {"survey_metadata": {
        "source_language": "de", "languages": ["de", "en"]}}}, db=None)
    monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=state))


def _bar_sums(ax):
    sums = {}
    for p in ax.patches:
        y = round(p.get_y() + p.get_height() / 2, 2)
        sums[y] = sums.get(y, 0) + abs(p.get_width())
    return sorted(round(v) for v in sums.values())


def _legend(ax):
    return [t.get_text() for t in ax.get_legend().get_texts()]


def _figure_text(fig):
    return " ".join(t.get_text() for t in fig.texts)


LIKERT_SCALE = {"de": ["Zunahme", "Unverändert", "Abnahme"], "en": ["Increase", "Unchanged", "Decrease"]}


@pytest.mark.parametrize("chart", ["stacked_bar_100pct", "diverging_bar"])
def test_likert_answers_outside_scale_are_separate(chart):
    from handlers.matrix_likert import MatrixLikertHandler
    df = pd.DataFrame({"KI": ["Zunahme"] * 30 + ["Abnahme"] * 10 + ["keine Angabe"] * 20,
                       "Cloud": ["Unverändert"] * 40 + ["Zunahme"] * 20})
    cfg = {"id": "rel", "columns": ["KI", "Cloud"], "scale": LIKERT_SCALE, "label": {"de": "Relevanz"}}
    h = MatrixLikertHandler(cfg)
    r = h.analyze(df)
    assert list(r.data.columns) == LIKERT_SCALE["de"] and cfg["scale"]["de"] == LIKERT_SCALE["de"]
    fig = h.create_chart(r, chart, lang="en")
    ax = fig.axes[0]
    assert _bar_sums(ax) == [100, 133]                    # 100 % scale + 33 % grey for KI
    legend = _legend(ax)
    assert any("Decrease" in x for x in legend) and "not on the scale" in legend
    assert not any("keine Angabe" == x for x in legend)
    assert "keine Angabe" in _figure_text(fig)
    assert all(not t.get_text().startswith(("-", "−")) for t in ax.get_xticklabels())


def test_single_choice_keeps_configured_order_for_ordered_scales():
    from handlers.single_choice import SingleChoiceHandler
    cfg = {"id": "size", "columns": ["size"], "ordinal": True,
           "options": {"de": ["klein", "mittel", "groß"], "en": ["small", "medium", "large"]}}
    df = pd.DataFrame({"size": ["groß"] * 30 + ["klein"] * 5 + ["mittel"] * 12})
    h = SingleChoiceHandler(cfg)
    r = h.analyze(df)
    assert list(r.data) == ["klein", "mittel", "groß"]
    fig = h.create_chart(r, "bar_horizontal", lang="en")
    assert [t.get_text() for t in fig.axes[0].get_yticklabels()] == ["small", "medium", "large"]


def test_multi_choice_axis_is_percent_of_respondents_with_base():
    from handlers.multi_choice import MultiChoiceHandler
    df = pd.DataFrame({"o1": ["x"] * 40 + [None] * 10, "o2": ["x"] * 10 + [None] * 40})
    h = MultiChoiceHandler({"id": "mc", "columns": ["o1", "o2"]})
    fig = h.create_chart(h.analyze(df), "bar_horizontal", lang="de")
    ax = fig.axes[0]
    assert ax.get_xlabel() == "% der Befragten"
    assert max(p.get_width() for p in ax.patches) == pytest.approx(100)
    assert [t.get_text() for t in ax.texts] == ["100% (40)", "25% (10)"]
    assert "n = 40" in _figure_text(fig)


def test_yes_no_matrix_shows_share_of_yes():
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    df = pd.DataFrame({"i1": ["Ja"] * 30 + ["Nein"] * 20, "i2": ["Nein"] * 45 + ["Ja"] * 5})
    h = MultiChoiceBinaryHandler({"id": "mb", "columns": ["i1", "i2"],
                                  "binary_values": {"positive": "Ja", "negative": "Nein"}})
    fig = h.create_chart(h.analyze(df), "bar_horizontal", lang="de")
    assert fig.axes[0].get_xlabel() == "% Ja" and "n = 50" in _figure_text(fig)


def test_ranking_shows_points_explained():
    from handlers.ranking import RankingHandler
    df = pd.DataFrame({"r1": ["A"] * 10 + ["B"] * 5, "r2": ["B"] * 10 + ["A"] * 5})
    h = RankingHandler({"id": "rk", "columns": ["r1", "r2"]})
    fig = h.create_chart(h.analyze(df), "bar_horizontal", lang="de")
    assert all("%" not in t.get_text() for t in fig.axes[0].texts)
    assert "Rang 1 = 2 Punkte" in _figure_text(fig)


def test_grouped_chart_uses_category_order():
    from utils.charts import ChartGenerator
    data = {"A": {"groß": 10, "klein": 50, "mittel": 40}, "B": {"groß": 30, "klein": 30, "mittel": 40}}
    fig = ChartGenerator().bar_grouped(data, "T", category_order=["klein", "mittel", "groß"])
    assert [t.get_text() for t in fig.axes[0].get_yticklabels()] == ["klein", "mittel", "groß"]


def test_label_replacement_merges_instead_of_overwriting(monkeypatch):
    import utils
    monkeypatch.setattr(utils, "_label_replacements", {"Uni": "Universität"}, raising=False)
    merged = utils.apply_label_replacements_to_dict({"Uni": 3, "Universität": 4})
    assert merged == {"Universität": 7}


def test_segment_medians_chart_names_dimension_and_quartiles():
    from utils.charts import ChartGenerator
    stats = {"klein": {"mean": 10, "low": 8, "high": 13, "n": 20}, "groß": {"mean": 20, "low": 15, "high": 30, "n": 20}}
    fig = ChartGenerator().segment_means_bar(stats, "Personal – Größe", note="Median je Gruppe; Gruppen: Größe.")
    assert "Median" in _figure_text(fig)
