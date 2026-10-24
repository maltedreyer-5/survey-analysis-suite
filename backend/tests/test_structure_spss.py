# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""SPSS adapter (decision 3.6.12): labels, grouping into questions, config, handlers."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pyreadstat = pytest.importorskip("pyreadstat")

from utils.structure_spss import read_spss, spss_structure  # noqa: E402
from utils.survey_structure import build_config  # noqa: E402


def sav(tmp_path):
    df = pd.DataFrame({
        "role": [1, 2, 1, 2, 1, 1],
        "q4_1": [1, 2, 3, 4, 5, 2], "q4_2": [2, 2, 3, 1, 5, 4],
        "svc_1": [1, 0, 1, 1, 0, 1], "svc_2": [0, 0, 1, 1, 1, 1],
        "staff": [10.0, 20.0, 15.0, 30.0, 12.0, 8.0],
        "comment": ["Parking is too expensive", "", "More study rooms please", "Longer hours", "", "Better wifi"],
    })
    agree = {1: "Strongly disagree", 2: "Disagree", 3: "Neutral", 4: "Agree", 5: "Strongly agree"}
    labels = {"role": "What is your role?", "q4_1": "How satisfied are you with - the library",
              "q4_2": "How satisfied are you with - the canteen",
              "svc_1": "Services used: Library", "svc_2": "Services used: Canteen",
              "staff": "How many staff work in your unit?", "comment": "Any comments?"}
    path = tmp_path / "survey.sav"
    pyreadstat.write_sav(df, str(path), column_labels=[labels[c] for c in df.columns],
                         variable_value_labels={"role": {1: "Staff", 2: "Student"}, "q4_1": agree, "q4_2": agree,
                                                "svc_1": {0: "not selected", 1: "selected"},
                                                "svc_2": {0: "not selected", 1: "selected"}})
    return path


def test_structure_and_config_from_sav(tmp_path):
    data, structure = read_spss(str(sav(tmp_path)), language="en")
    kinds = {q.code: q.kind for q in structure.questions}
    assert kinds == {"role": "single", "q4": "matrix", "svc": "multi", "staff": "numeric", "comment": "text"}
    config, report = build_config(structure)
    g = config["question_groups"]
    assert g["role"]["options"]["en"] == ["Staff", "Student"]
    assert g["q4"]["row_labels"]["en"] == ["the library", "the canteen"]
    assert g["q4"]["scale"]["en"][0] == "Strongly disagree" and g["q4"]["label"]["en"] == "How satisfied are you with"
    assert g["svc"]["binary_values"] == {"positive": "selected"} and g["svc"]["item_labels"]["en"] == ["Library", "Canteen"]
    assert config["settings"]["survey_metadata"]["source_language"] == "en"
    assert data.loc[0, "role"] == "Staff" and data.loc[0, "q4_1"] == "Strongly disagree"   # codes -> labels


def test_handlers_count_from_the_generated_config(tmp_path):
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    from handlers.single_choice import SingleChoiceHandler
    data, structure = read_spss(str(sav(tmp_path)), language="en")
    g = build_config(structure)[0]["question_groups"]
    assert dict(SingleChoiceHandler(g["role"]).analyze(data, lang="en").data) == {"Staff": 4, "Student": 2}
    multi = MultiChoiceBinaryHandler(g["svc"]).analyze(data, lang="en").data
    assert dict(multi) == {"Library": 4, "Canteen": 4}                      # "not selected" is not counted


def test_mr_sets_and_validation():
    from services.config_validation import validate_survey_config
    raw = pd.DataFrame({"a": [1, 0, 1], "b": [1, 1, 0], "when": [1.6e10, 1.6e10, 1.7e10]})
    meta = SimpleNamespace(column_names_to_labels={"a": "Tools: Mail", "b": "Tools: Chat", "when": "Date"},
                           variable_value_labels={}, original_variable_types={"when": "DATETIME20"},
                           mr_sets={"tools": {"is_dichotomy": True, "counted_value": 1, "label": "Tools",
                                              "variable_list": ["a", "b"]}}, column_labels=[])
    data, structure = spss_structure(raw, meta, language="de")
    config, report = build_config(structure)
    assert config["question_groups"]["tools"]["binary_values"] == {"positive": "1"}
    assert config["question_groups"]["when"]["handler"] == "date"            # date
    assert validate_survey_config(config)[0] == []


def test_import_of_sav_writes_config(tmp_path, monkeypatch):
    import yaml
    import services.data_import as di
    from core import state
    monkeypatch.setattr(di, "get_active_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(state, "db", None)
    monkeypatch.setattr(state, "project_id", None)
    msg, df = di.import_data(SimpleNamespace(name=str(sav(tmp_path))))
    assert "SPSS" in msg and "Configuration created from the SPSS file" in msg
    written = yaml.safe_load((tmp_path / "cfg" / "questions.yaml").read_text(encoding="utf-8"))
    assert set(written["question_groups"]) == {"role", "q4", "svc", "staff", "comment"}
    assert df.loc[0, "role"] == "Staff"


def test_spss_ordinal_measure_marks_single_choice():
    raw = pd.DataFrame({"sat": [1, 2, 3], "role": [1, 2, 1]})
    meta = SimpleNamespace(column_names_to_labels={"sat": "Satisfaction", "role": "Role"},
                           variable_value_labels={"sat": {1: "low", 2: "mid", 3: "high"}, "role": {1: "A", 2: "B"}},
                           variable_measure={"sat": "ordinal", "role": "nominal"}, original_variable_types={},
                           mr_sets={}, column_labels=[])
    _, structure = spss_structure(raw, meta, language="en")
    g = build_config(structure)[0]["question_groups"]
    assert g["sat"]["ordinal"] is True and "ordinal" not in g["role"]
