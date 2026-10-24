# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""A new survey: created in the Setup tab, its first configuration from the import
(inferred from the data when there is no structure file), always with a snapshot."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from utils.survey_registry import SurveyRegistry  # noqa: E402
from services.surveys import create_survey  # noqa: E402
from utils.structure_data import infer_structure  # noqa: E402
from utils.survey_structure import build_config  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "data"


@pytest.fixture
def surveys(tmp_path, monkeypatch):
    reg = SurveyRegistry(tmp_path)
    reg.discover()
    monkeypatch.setattr(core, "_registry", reg)
    monkeypatch.setattr(core, "SURVEYS_DIR", tmp_path)
    # the import changes the application state: restored after the test
    for attr in ("config", "df", "import_id", "project_id", "analysis_results", "config_errors", "config_warnings"):
        if hasattr(core.state, attr):
            monkeypatch.setattr(core.state, attr, getattr(core.state, attr))
    return tmp_path


def test_create_survey_writes_both_files_and_registers(surveys):
    ok, msg = create_survey("staff_2026", "Staff survey 2026", "en", ["de", "en", "it"])
    assert ok, msg
    s = yaml.safe_load((surveys / "staff_2026" / "survey.yaml").read_text())
    q = yaml.safe_load((surveys / "staff_2026" / "questions.yaml").read_text())
    assert s["id"] == "staff_2026" and s["languages"] == ["en", "de", "it"]
    assert q["settings"]["survey_metadata"] == {"survey_title": {"en": "Staff survey 2026"},
                                                "source_language": "en", "languages": ["en", "de", "it"]}
    assert "staff_2026" in core.get_registry().survey_ids()


@pytest.mark.parametrize("sid,title,src,expected", [
    ("Staff", "T", "en", "must start with a lower-case letter"),
    ("s", "T", "en", "must start with a lower-case letter"),
    ("staff", "", "en", "title"),
    ("staff", "T", "fr", "source language"),
])
def test_create_survey_checks_its_input(surveys, sid, title, src, expected):
    ok, msg = create_survey(sid, title, src)
    assert not ok and expected in msg and not any(surveys.iterdir())


def test_create_survey_refuses_an_existing_id(surveys):
    assert create_survey("staff", "T", "en")[0]
    ok, msg = create_survey("staff", "T", "en")
    assert not ok and "exists already" in msg


@pytest.mark.parametrize("lang", ["en", "de"])
def test_configuration_inferred_from_the_example_data_matches_the_written_one(lang):
    df = pd.read_csv(EXAMPLES / f"ai_in_higher_education_{lang}.csv", dtype=str)
    structure, notes = infer_structure(df, lang)
    inferred = build_config(structure)[0]["question_groups"]
    from utils.scales import expand_named_scales
    written = expand_named_scales(yaml.safe_load(
        (Path(core.SURVEYS_DIR) / f"ai_in_higher_education_{lang}" / "questions.yaml").read_text()))["question_groups"]
    by_columns = {tuple(q["columns"]): q["handler"] for q in written.values()}
    for qid, q in inferred.items():
        if tuple(q["columns"]) in by_columns:
            assert q["handler"] == by_columns[tuple(q["columns"])], qid
    assert set(by_columns) <= {tuple(q["columns"]) for q in inferred.values()}   # every written question found
    assert inferred["nps"].get("nps") and inferred["budget"].get("constant_sum") == 100.0
    assert inferred["used_other"]["other_of"] == "used"
    assert any("technical columns left out: id" in n for n in notes)
    assert any(n.startswith("rate: scale points listed by frequency") for n in notes)


def test_technical_columns_and_grouping():
    df = pd.DataFrame({"submitdate": ["2026-01-01"] * 4, "token": list("abcd"),
                       "q1[SQ001]": ["Y", "", "Y", ""], "q1[SQ002]": ["", "Y", "Y", ""], "q1[other]": ["", "x", "", ""],
                       "q2": ["very long answer with many words here", "another long sentence of text",
                              "a third answer that is long", "and the last one written out"]})
    cfg = build_config(infer_structure(df, "en")[0])[0]["question_groups"]
    assert set(cfg) == {"q1", "q1_other", "q2"}
    assert cfg["q1"]["handler"] == "multi_choice_binary" and cfg["q1"]["columns"] == ["q1[SQ001]", "q1[SQ002]"]
    assert cfg["q2"]["handler"] == "freetext"


def test_import_without_structure_file_writes_a_configuration_with_snapshot(surveys, monkeypatch):
    from services import data_import
    assert create_survey("csv_only", "From CSV", "en")[0]
    core.set_active_survey("csv_only")
    monkeypatch.setattr(core.state, "db", None)
    csv = EXAMPLES / "ai_in_higher_education_en.csv"
    msg, df = data_import.import_data(SimpleNamespace(name=str(csv)))
    assert df is not None, msg
    assert "Configuration created from the data: 14 questions" in msg and "inferred from the data" in msg
    q = yaml.safe_load((surveys / "csv_only" / "questions.yaml").read_text())
    assert len(q["question_groups"]) == 14
    assert q["settings"]["survey_metadata"]["survey_title"] == {"en": "From CSV"}   # settings of the survey kept
    assert list((surveys / "csv_only" / "snapshots").glob("*.yaml"))                 # previous version kept
    # a second import does not overwrite the configuration
    msg2, _ = data_import.import_data(SimpleNamespace(name=str(csv)))
    assert "Configuration created" not in msg2
