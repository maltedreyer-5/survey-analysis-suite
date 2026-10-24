# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Matrix rows in the dashboard export: no fixed +1/0/-1 "mean" for three-step
scales (a rest of one specific survey); the mean of a row comes from the scale
metrics, with the direction from scale_order."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export.result_format import _format_result_for_dashboard  # noqa: E402

SCALE = ["Agree", "Neutral", "Disagree"]


def matrix_result(metrics=None):
    data = {s: {"Library": n, "Canteen": m} for s, n, m in zip(SCALE, (6, 3, 1), (2, 3, 5))}
    pct = {s: {r: v * 10 for r, v in rows.items()} for s, rows in data.items()}
    return {"n": 10, "data": data, "percentages": pct,
            "metadata": {"scale": SCALE, **({"scale_metrics": metrics} if metrics else {})}}


def test_three_step_matrix_has_no_fixed_mean():
    out = _format_result_for_dashboard(matrix_result(), "matrix_likert")
    rows = out["by_row"]
    assert set(rows) == {"Library", "Canteen"}
    assert all("mean" not in r for r in rows.values())


def test_row_mean_comes_from_the_scale_metrics():
    metrics = {"Library": {"mean": 2.5, "median": 3, "n": 10}, "Canteen": {"mean": 1.7, "median": 1, "n": 10}}
    out = _format_result_for_dashboard(matrix_result(metrics), "matrix_likert")
    assert out["by_row"]["Library"]["scale_metrics"]["mean"] == 2.5
    assert "mean" not in out["by_row"]["Library"]
