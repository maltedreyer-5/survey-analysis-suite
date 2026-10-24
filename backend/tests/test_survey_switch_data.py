# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Several surveys share one database: an import after switching is stored, and restoring
the last session loads the import of the active survey, not the newest of all."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services.surveys import create_survey  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402
from utils.survey_registry import SurveyRegistry  # noqa: E402


@pytest.fixture
def two_surveys(tmp_path, monkeypatch):
    reg = SurveyRegistry(tmp_path / "surveys")
    (tmp_path / "surveys").mkdir()
    reg.discover()
    monkeypatch.setattr(core, "_registry", reg)
    monkeypatch.setattr(core, "SURVEYS_DIR", tmp_path / "surveys")
    for attr in ("config", "df", "import_id", "project_id", "analysis_results", "db", "config_errors", "config_warnings"):
        if hasattr(core.state, attr):
            monkeypatch.setattr(core.state, attr, getattr(core.state, attr))
    db = SurveyDatabase(str(tmp_path / "t.db"))
    core.state.db = db
    core.state.project_id = db.create_project("default", "")
    assert create_survey("survey_a", "A", "en")[0] and create_survey("survey_b", "B", "en")[0]
    return db


def test_restore_loads_the_import_of_the_active_survey(two_surveys):
    db = two_surveys
    a = db.save_import(core.state.project_id, pd.DataFrame({"x": ["a1", "a2"]}), filename="a.csv", survey_id="survey_a")
    b = db.save_import(core.state.project_id, pd.DataFrame({"x": ["b1", "b2", "b3"]}), filename="b.csv", survey_id="survey_b")
    core.set_active_survey("survey_a")
    status, df = core.restore_last_session()
    assert core.state.import_id == a and list(df["x"]) == ["a1", "a2"], status   # not the newer import of survey_b
    core.set_active_survey("survey_b")
    status, df = core.restore_last_session()
    assert core.state.import_id == b and len(df) == 3


def test_no_import_of_the_active_survey(two_surveys):
    db = two_surveys
    db.save_import(core.state.project_id, pd.DataFrame({"x": ["a"]}), filename="a.csv", survey_id="survey_a")
    core.set_active_survey("survey_b")
    status, df = core.restore_last_session()
    assert df is None and "active survey" in status


def test_switching_keeps_the_project():
    """The switch handler of the interface must not drop the project (imports would only stay in memory)."""
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    start = src.index("def switch_active_survey(choice):")
    body = src[start:src.index("def create_new_survey(", start)]
    assert "state.project_id = None" not in body
