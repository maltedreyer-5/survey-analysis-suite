# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Answers read as numbers by a typed import count like the configured texts.

A CSV or Excel import with type detection gives answer codes such as 0-10
as integers, or as floats (10.0) when the column has gaps; configured options
are texts ("10"). Handlers, statistics and the dashboard keys must treat them
alike (they did not: the configured points stayed at 0 and the counts went
under extra number keys; the dashboard export dropped the question).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import get_handler  # noqa: E402
from utils.analysis_variables import build_variables  # noqa: E402
from utils.answer_values import answer_text  # noqa: E402

OPTIONS = [str(i) for i in range(11)]
QCONFIG = {"handler": "single_choice", "columns": ["nps"], "label": {"de": "NPS"},
           "nps": True, "ordinal": True, "options": {"de": OPTIONS}}
CONFIG = {"settings": {"survey_metadata": {"source_language": "de"}},
          "question_groups": {"rec": QCONFIG}}
VALUES = [10, 9, 9, 7, 3, 0, 10, 8]


def frames():
    as_text = pd.DataFrame({"nps": [str(v) for v in VALUES] + [None]})
    as_int = pd.DataFrame({"nps": VALUES + [None]})          # float64 with NaN
    as_int64 = pd.DataFrame({"nps": VALUES})
    return as_text, as_int, as_int64


def test_answer_text():
    assert [answer_text(v) for v in (10, 10.0, np.int64(3), 3.5, True, "x")] == \
        ["10", "10", "3", "3.5", "True", "x"]
    assert answer_text(None) is None and np.isnan(answer_text(float("nan")))


def test_single_choice_counts_numbers_like_texts():
    as_text, as_float, as_int64 = frames()
    expected = dict(get_handler("single_choice", "rec", QCONFIG).analyze(as_text, lang="de").data)
    assert set(expected) == set(OPTIONS)
    for df in (as_float, as_int64.iloc[:len(VALUES)]):
        r = get_handler("single_choice", "rec", QCONFIG).analyze(df, lang="de")
        assert dict(r.data) == expected
    assert get_handler("single_choice", "rec", QCONFIG).analyze(as_float, lang="de").metadata["nps"] == \
        get_handler("single_choice", "rec", QCONFIG).analyze(as_text, lang="de").metadata["nps"]


def test_statistics_variable_keeps_number_answers():
    as_text, as_float, _ = frames()
    v_text = build_variables(CONFIG, as_text, ["rec"])["rec"][0]
    v_float = build_variables(CONFIG, as_float, ["rec"])["rec"][0]
    assert v_float.values.tolist()[:len(VALUES)] == v_text.values.tolist()[:len(VALUES)]
    assert int(v_float.values.notna().sum()) == len(VALUES)


def test_numeric_handler_keeps_numbers():
    cfg = {"handler": "numeric", "columns": ["age"], "label": {"de": "Alter"}}
    r = get_handler("numeric", "age", cfg).analyze(pd.DataFrame({"age": [20, 30, None]}), lang="de")
    assert r.n == 2
