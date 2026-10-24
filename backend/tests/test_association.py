# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Variable model and association tests. The first tests are the defects found
in the review (session 2) turned into regression tests.
"""
import math
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from utils.analysis_variables import build_variables  # noqa: E402
from utils.association import (  # noqa: E402
    adjust, benjamini_hochberg, bias_corrected_v, strength_label, assess_pair,
)

RNG = np.random.default_rng(1)


def _cfg(groups, **settings):
    return {"settings": {"survey_metadata": {"source_language": "de", "languages": ["de"]}, **settings},
            "question_groups": groups}


def test_filter_question_does_not_create_association():
    """Follow-up question shown only after 'Ja', choices random: no association may appear."""
    n = 200
    q1 = RNG.choice(["Ja", "Nein"], n)
    df = pd.DataFrame({"q1": q1})
    for k in range(4):
        df[f"q2_{k}"] = np.where((q1 == "Ja") & (RNG.random(n) < 0.5), "Ja", None)
    df.loc[(q1 == "Ja") & df[[f"q2_{k}" for k in range(4)]].isna().all(axis=1), "q2_0"] = "Ja"
    cfg = _cfg({"q1": {"handler": "single_choice", "columns": ["q1"]},
                "q2": {"handler": "multi_choice", "columns": [f"q2_{k}" for k in range(4)]}})
    v = build_variables(cfg, df)
    for option in v["q2"]:
        assert option.n == int((q1 == "Ja").sum())          # base: only those who were asked
        result = assess_pair(v["q1"][0], option)
        assert not result.testable or not result.is_significant()


def test_yes_no_matrix_counts_yes_per_item():
    """A 'Nein' is not a selection: the respondents spread over the categories."""
    df = pd.DataFrame({f"m{k}": RNG.choice(["Ja", "Nein"], 100) for k in range(3)})
    cfg = _cfg({"m": {"handler": "multi_choice_binary", "columns": ["m0", "m1", "m2"],
                      "binary_values": {"positive": "Ja", "negative": "Nein"}}})
    items = build_variables(cfg, df)["m"]
    assert len(items) == 3 and all(i.level == "binary" for i in items)
    assert items[0].values.sum() == (df["m0"] == "Ja").sum()


def test_multi_choice_group_difference_is_detected():
    """Group B chooses every option four times as often: the per-option test finds it."""
    n = 60
    seg = np.array(["A"] * n + ["B"] * n)
    probs = {"A": [0.2, 0.15, 0.1, 0.05], "B": [0.8, 0.6, 0.4, 0.2]}
    df = pd.DataFrame({"seg": seg})
    for k in range(4):
        df[f"o{k}"] = [("x" if RNG.random() < probs[s][k] else None) for s in seg]
    df.loc[df[[f"o{k}" for k in range(4)]].isna().all(axis=1), "o3"] = "x"
    cfg = _cfg({"seg": {"handler": "single_choice", "columns": ["seg"]},
                "mc": {"handler": "multi_choice", "columns": [f"o{k}" for k in range(4)]}})
    v = build_variables(cfg, df)
    results = [assess_pair(v["seg"][0], opt) for opt in v["mc"]]
    adjust(results)
    assert results[0].is_significant() and results[1].is_significant()
    cell = results[0].pattern[0]
    assert cell["a"] == "B" and cell["b"] == 1.0 and cell["share_in_group"] > cell["share_overall"]


def test_likert_items_are_ordinal_with_direction():
    n = 120
    x = RNG.integers(1, 6, n)
    y = np.clip(x + RNG.integers(-1, 2, n), 1, 5)
    scale = ["gar nicht", "eher nicht", "teils", "eher ja", "voll"]
    df = pd.DataFrame({"a": [scale[i - 1] for i in x], "b": [scale[i - 1] for i in y],
                       "c": ["keine Angabe"] * 10 + [scale[i - 1] for i in y[10:]]})
    cfg = _cfg({"l": {"handler": "matrix_likert", "columns": ["a", "b", "c"], "scale": {"de": scale}}})
    items = build_variables(cfg, df)["l"]
    assert [i.level for i in items] == ["ordinal"] * 3          # no mean unless scale_index is set
    assert items[2].n == n - 10                                # evasive answers excluded
    result = assess_pair(items[0], items[1])
    assert result.test == "kendall_tau_b" and result.effect > 0.5
    assert result.pattern[0] == {"kind": "direction", "sign": 1}


def test_scale_index_only_when_configured():
    df = pd.DataFrame({"a": ["teils"] * 30, "b": ["voll"] * 30})
    cfg = _cfg({"l": {"handler": "matrix_likert", "columns": ["a", "b"], "scale_index": True,
                      "scale": {"de": ["gar nicht", "teils", "voll"]}}})
    items = build_variables(cfg, df)["l"]
    assert items[-1].measure == "scale_index" and items[-1].values.iloc[0] == 2.5


def test_small_two_by_two_uses_fisher():
    df = pd.DataFrame({"a": ["x"] * 22 + ["y"] * 6, "b": ["u"] * 20 + ["v"] * 2 + ["u"] * 1 + ["v"] * 5})
    cfg = _cfg({"a": {"handler": "single_choice", "columns": ["a"]},
                "b": {"handler": "single_choice", "columns": ["b"]}})
    v = build_variables(cfg, df)
    assert assess_pair(v["a"][0], v["b"][0]).test == "fisher"


def test_numeric_against_groups_uses_kruskal_with_medians():
    g = np.array(["klein"] * 30 + ["groß"] * 30)
    val = np.concatenate([RNG.normal(10, 2, 30), RNG.normal(20, 2, 30)])
    df = pd.DataFrame({"g": g, "v": val})
    cfg = _cfg({"g": {"handler": "single_choice", "columns": ["g"]},
                "v": {"handler": "numeric", "columns": ["v"]}})
    v = build_variables(cfg, df)
    r = assess_pair(v["g"][0], v["v"][0])
    assert r.test == "kruskal_wallis" and r.strength == "strong"
    assert r.pattern[0]["group"] == "groß"


def test_not_testable_is_reported_not_dropped():
    df = pd.DataFrame({"a": ["x"] * 10, "b": ["u"] * 10})
    cfg = _cfg({"a": {"handler": "single_choice", "columns": ["a"]},
                "b": {"handler": "single_choice", "columns": ["b"]}})
    v = build_variables(cfg, df)
    r = assess_pair(v["a"][0], v["b"][0])
    assert not r.testable and r.reason == "too_few_cases"


def test_benjamini_hochberg_known_values():
    q = benjamini_hochberg([0.01, 0.04, 0.03, 0.005, float("nan")])
    assert [round(x, 4) for x in q[:4]] == [0.02, 0.04, 0.04, 0.02] and math.isnan(q[4])


def test_v_bias_correction_and_strength():
    assert bias_corrected_v(10.0, 100, 2, 2) < math.sqrt(10.0 / 100)
    assert strength_label("cramers_v", 0.25, (3, 3)) == "medium"      # .3/sqrt(2) = .21
    assert strength_label("cramers_v", 0.25, (2, 2)) == "weak"


def test_freetext_clusters_all_memberships():
    df = pd.DataFrame(index=range(40))
    clusters = {"f": {"answered": set(range(30)), "clusters": [
        {"label": {"de": "Preise"}, "respondents": set(range(0, 20))},
        {"label": {"de": "Zeiten"}, "respondents": set(range(10, 30))}]}}
    cfg = _cfg({"f": {"handler": "freetext", "columns": []}})
    v = build_variables(cfg, df, cluster_memberships=clusters)["f"]
    assert v[0].values.sum() == 20 and v[1].values.sum() == 20      # overlap counted in both
    assert v[0].n == 30                                              # base: those with a clustered answer


def test_ranking_columns_are_ranks_values_are_items():
    """LimeSurvey ranking: column = rank, value = item (not item columns)."""
    items = ["Cloud", "Open Source", "Daten"]
    rows = [["Cloud", "Daten", None]] * 20 + [["Open Source", "Cloud", "Daten"]] * 15 + [[None] * 3] * 5
    df = pd.DataFrame(rows, columns=["r1", "r2", "r3"])
    cfg = _cfg({"rk": {"handler": "ranking", "columns": ["r1", "r2", "r3"],
                       "item_labels": {"de": items, "en": ["Cloud", "Open source", "Data"]}}})
    v = {x.item_label["de"]: x for x in build_variables(cfg, df)["rk"]}
    assert set(v) == set(items)
    assert v["Cloud"].values.value_counts().to_dict() == {1.0: 20, 2.0: 15}
    assert v["Open Source"].values.value_counts().to_dict() == {4.0: 20, 1.0: 15}   # not ranked = k + 1
    assert v["Daten"].n == 35 and v["Daten"].item_label["en"] == "Data"


def test_yes_no_matrix_base_includes_no_answers():
    """Base as in the handler: everybody who answered an item, also with 'Nein' only."""
    df = pd.DataFrame({"i1": ["Nein"] * 10 + ["Ja"] * 10 + [None] * 5, "i2": ["Nein"] * 25})
    cfg = _cfg({"m": {"handler": "multi_choice_binary", "columns": ["i1", "i2"],
                      "binary_values": {"positive": "Ja", "negative": "Nein"}}})
    i1 = build_variables(cfg, df)["m"][0]
    assert i1.n == 25 and i1.values.sum() == 10


def _seg_cfg(groups):
    cfg = _cfg(groups)
    cfg["settings"]["segmentation"] = {"dimensions": [
        {"id": "grp", "label": {"de": "Gruppe"}, "source_question": "seg", "bucketing": {"type": "identity"}}]}
    return cfg


def test_summary_segment_tests_find_option_differences_and_states():
    from utils.question_summary import compute_segment_significance, segment_fixed_text
    n = 60
    seg = np.array(["A"] * n + ["B"] * n)
    probs = {"A": [0.2, 0.15], "B": [0.8, 0.6]}
    df = pd.DataFrame({"seg": seg})
    for k in range(2):
        df[f"o{k}"] = [("x" if RNG.random() < probs[s][k] else None) for s in seg]
    df.loc[df[["o0", "o1"]].isna().all(axis=1), "o1"] = "x"
    cfg = _seg_cfg({"seg": {"handler": "single_choice", "columns": ["seg"]},
                    "mc": {"handler": "multi_choice", "columns": ["o0", "o1"]}})
    sig = compute_segment_significance("mc", "multi_choice", cfg["question_groups"]["mc"], df, cfg)
    assert sig[0].state == "differs" and sig[0].findings
    small = df.head(12)
    sig_small = compute_segment_significance("mc", "multi_choice", cfg["question_groups"]["mc"], small, cfg)
    assert sig_small[0].state == "not_testable"
    assert "reichen die Fallzahlen nicht aus" in segment_fixed_text(sig_small, "de")
