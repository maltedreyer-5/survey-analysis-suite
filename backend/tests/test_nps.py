# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Net Promoter Score."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import get_handler  # noqa: E402
from utils.nps import compute  # noqa: E402


def test_groups_and_score():
    m = compute({"10": 3, "9": 2, "8": 2, "7": 1, "6": 1, "0": 1, "weiß nicht": 4})
    assert (m["n"], m["n_excluded"], m["n_promoters"], m["n_passives"], m["n_detractors"]) == (10, 4, 5, 3, 2)
    assert m["promoters"] == 50.0 and m["detractors"] == 20.0 and m["nps"] == 30.0
    assert compute({"weiß nicht": 2}) is None


def test_single_choice_with_nps_and_order():
    df = pd.DataFrame({"r": ["10", "9", "3", "7", "10", None]})
    cfg = {"id": "r", "columns": ["r"], "nps": True, "ordinal": True, "options": {"de": [str(i) for i in range(11)]}}
    r = get_handler("single_choice", "r", cfg).analyze(df, lang="de")
    assert r.metadata["nps"]["nps"] == 40.0 and list(r.data)[:2] == ["0", "1"] and r.data["0"] == 0   # whole scale
    assert r.metadata["scale_metrics"]["mean"] == 7.8                 # numbers as answers: their own value


def test_qualtrics_nps_question_and_word(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_structure_qualtrics import sq
    from utils.structure_qualtrics import qualtrics_structure
    from utils.survey_structure import build_config
    qsf = {"SurveyEntry": {"SurveyID": "SV", "SurveyLanguage": "EN"},
           "SurveyElements": [sq("QID1", "Q1", "MC", "NPS", "Recommend?", [str(i) for i in range(11)])]}
    data = pd.DataFrame({"Q1": ["10", "9", "3"]})
    data, structure = qualtrics_structure(qsf, data, {"Q1": "QID1"})
    q = build_config(structure)[0]["question_groups"]["q1"]
    assert q["nps"] is True and q["ordinal"] is True
    r = get_handler("single_choice", "q1", q).analyze(data, lang="en")
    assert round(r.metadata["nps"]["nps"], 1) == 33.3
