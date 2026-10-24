# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Matrix questions by segment in the Word report: small multiples per row and/or mean per
segment, chosen with matrix_segment_chart (question, else settings; default both)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from docx import Document  # noqa: E402
from services.config_validation import validate_survey_config  # noqa: E402
from utils import export_structured as es  # noqa: E402

SCALE = ["Very good", "Good", "Poor", "Very poor"]
# {segment: {option: {row: percent}}} as the matrix handler returns them
DATA = {seg: {opt: {"Library": p, "Canteen": 100 - p - 10 if i == 0 else p}
              for i, (opt, p) in enumerate(zip(SCALE, shares))}
        for seg, shares in {"Students": [40, 30, 20, 10], "Staff": [10, 20, 30, 40]}.items()}


@pytest.mark.parametrize("mode, figures", [(None, 2), ("both", 2), ("small_multiples", 1), ("mean", 1), ("none", 0)])
def test_mode_decides_the_figures(tmp_path, monkeypatch, mode, figures):
    monkeypatch.setattr(core.state, "config", {"settings": {"matrix_segment_chart": mode} if mode else {}})
    exp = es.StructuredReportExporter(tmp_path)
    doc = Document()
    exp._add_matrix_segment_charts(doc, "How do you rate", DATA, "Role", "en",
                                   {"scale": SCALE, "scale_order": "descending"})
    assert len(doc.inline_shapes) == figures


def test_question_overrides_settings_and_unknown_values_are_reported():
    assert es.matrix_segment_chart_mode({"matrix_segment_chart": "mean"}, {"matrix_segment_chart": "none"}) == "mean"
    assert es.matrix_segment_chart_mode({}, {"matrix_segment_chart": "bars"}) == "both"
    cfg = {"settings": {"matrix_segment_chart": "bars"},
           "question_groups": {"q": {"handler": "matrix_likert", "columns": ["a"], "matrix_segment_chart": "x"}}}
    errors, warnings = validate_survey_config(cfg)
    assert sum("matrix_segment_chart" in w for w in warnings) == 2
