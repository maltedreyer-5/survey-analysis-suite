# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Scale metrics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.scale_metrics import compute  # noqa: E402

ORDER = ["Stimme voll zu", "Stimme zu", "Neutral", "Stimme nicht zu", "Stimme gar nicht zu", "Weiß nicht"]


def test_descending_scale_detected_dont_know_excluded():
    counts = {"Stimme voll zu": 4, "Stimme zu": 2, "Neutral": 2, "Stimme nicht zu": 1, "Stimme gar nicht zu": 1, "Weiß nicht": 5}
    m = compute(counts, ORDER)
    assert m["n"] == 10 and m["n_excluded"] == 5
    assert m["mean"] == 3.7 and m["median"] == 4.0                    # 5 = "Stimme voll zu"
    assert m["top_box"] == 60.0 and m["bottom_box"] == 20.0
    assert m["top_labels"] == ["Stimme zu", "Stimme voll zu"]


def test_configured_values_and_order():
    order = ["schlecht", "mittel", "gut"]
    counts = {"schlecht": 1, "mittel": 1, "gut": 2}
    assert compute(counts, order) is None                             # direction unknown: no metrics
    assert compute(counts, order, {"scale_order": "ascending"})["mean"] == 2.25
    assert compute({"1": 1, "5": 1}, ["1", "2", "3", "4", "5"])["mean"] == 3.0   # numbers tell the direction
    assert compute({"0": 1, "10": 1}, [str(i) for i in range(11)])["mean"] == 5.0  # and are the values
    assert compute(counts, order, {"scale_order": "descending"})["mean"] == 1.75
    assert compute(counts, order, {"scale_values": [0, 50, 100]})["mean"] == 62.5
    assert compute(counts, order, {"scale_metrics": False}) is None
    assert compute({"x": 1}, ["x"]) is None


def test_handlers_report_scale_metrics():
    import pandas as pd
    from handlers import get_handler
    opts = {"de": ["sehr gut", "gut", "schlecht", "Weiß nicht"]}
    df = pd.DataFrame({"x": ["sehr gut", "gut", "gut", "schlecht", "Weiß nicht"]})
    r = get_handler("single_choice", "s", {"id": "s", "columns": ["x"], "options": opts, "ordinal": True,
                                           "scale_order": "descending"}).analyze(df, lang="de")
    assert r.metadata["scale_metrics"]["n"] == 4 and r.metadata["scale_metrics"]["mean"] == 2.0
    nominal = get_handler("single_choice", "s", {"id": "s", "columns": ["x"], "options": opts}).analyze(df, lang="de")
    assert "scale_metrics" not in nominal.metadata
    m = pd.DataFrame({"Q [A]": ["gut", "gut", "schlecht"], "Q [B]": ["schlecht", "schlecht", "Weiß nicht"]})
    rm = get_handler("matrix_likert", "m", {"id": "m", "columns": ["Q [A]", "Q [B]"],
                                            "scale": {"de": ["schlecht", "gut", "Weiß nicht"]},
                                            "scale_order": "ascending"}).analyze(m, lang="de")
    sm = rm.metadata["scale_metrics"]
    assert sm["A"]["top_box"] == 66.7 and sm["B"]["n"] == 2 and sm["B"]["n_excluded"] == 1
