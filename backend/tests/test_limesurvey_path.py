# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""The path from the survey tool: the example survey as a LimeSurvey export (structure
file and responses with answer codes, examples/limesurvey/) imported into a new survey
gives the same results as the example survey with its hand-written configuration."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from handlers import get_handler  # noqa: E402
from services import data_import  # noqa: E402
from services.surveys import create_survey  # noqa: E402
from utils.scales import expand_named_scales  # noqa: E402
from utils.survey_registry import SurveyRegistry  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAME = {"role": "role", "faculty": "faculty", "age": "age", "sat": "satisfaction", "nps": "recommend",
        "used": "tools_used", "rate": "rating", "rank": "priorities", "budget": "budget", "visit": "last_training"}


def _norm(x):
    if isinstance(x, pd.DataFrame):
        return json.loads(x.round(6).to_json())
    if isinstance(x, dict):
        return {str(k): _norm(v) for k, v in x.items()}
    return round(x, 6) if isinstance(x, float) else x


@pytest.fixture(params=["en", "de"])
def imported(request, tmp_path, monkeypatch):
    lang = request.param
    reg = SurveyRegistry(tmp_path)
    reg.discover()
    monkeypatch.setattr(core, "_registry", reg)
    monkeypatch.setattr(core, "SURVEYS_DIR", tmp_path)
    for attr in ("config", "df", "import_id", "project_id", "analysis_results", "config_errors", "config_warnings"):
        if hasattr(core.state, attr):
            monkeypatch.setattr(core.state, attr, getattr(core.state, attr))
    monkeypatch.setattr(core.state, "db", None)
    assert create_survey("from_ls", "From LimeSurvey", lang, [])[0]
    core.set_active_survey("from_ls")
    ex = ROOT / "examples" / "limesurvey"
    msg, df = data_import.import_data(SimpleNamespace(name=str(ex / f"ai_in_higher_education_{lang}_responses.csv")),
                                      SimpleNamespace(name=str(ex / f"ai_in_higher_education_{lang}.lss")))
    cfg = yaml.safe_load((tmp_path / "from_ls" / "questions.yaml").read_text())
    hand = expand_named_scales(yaml.safe_load(
        (ROOT / "backend" / "surveys" / f"ai_in_higher_education_{lang}" / "questions.yaml").read_text()))
    data = pd.read_csv(ROOT / "examples" / "data" / f"ai_in_higher_education_{lang}.csv", dtype=str)
    return lang, msg, df, cfg, hand, data


def test_import_report(imported):
    lang, msg, df, cfg, _, _ = imported
    assert "Configuration created from the LSS file: 14 questions" in msg
    assert "Answer codes converted to answer texts" in msg
    assert "5 columns without a question: id, submitdate, lastpage, startlanguage, seed" in msg
    other = {"en": "de", "de": "en"}[lang]
    assert all(set(q["label"]) == {lang, other} for q in cfg["question_groups"].values())   # both languages of the file


def test_same_results_as_the_hand_written_configuration(imported):
    lang, _, df, cfg, hand, data = imported
    for ls_id, hand_id in SAME.items():
        qa = {**cfg["question_groups"][ls_id], "id": ls_id}
        qb = {**hand["question_groups"][hand_id], "id": hand_id}
        if "numeric" in qb:                     # same rounding as the example (decimals: 0)
            qa["numeric"] = qb["numeric"]
        ra = get_handler(qa["handler"], ls_id, qa).analyze(df, lang)
        rb = get_handler(qb["handler"], hand_id, qb).analyze(data, lang)
        assert _norm(ra.data) == _norm(rb.data), ls_id
        if ls_id != "used":
            assert ra.n == rb.n, ls_id


def test_nps_and_other_from_limesurvey(imported):
    lang, _, df, cfg, _, data = imported
    q = cfg["question_groups"]
    assert q["nps"].get("nps") and q["nps"].get("ordinal")              # a 0-10 list asking for a recommendation
    assert q["used"]["text_item_columns"] == ["used[other]"]              # "other" counts as an option
    assert q["used_other"]["other_of"] == "used"                          # and is clustered as free text
    # LimeSurvey writes the same empty value for "not checked" and "not answered": respondents
    # who checked nothing are not in the base of the multiple choice
    nothing = (data[[f"used_{i}" for i in range(5)]] == {"en": "No", "de": "Nein"}[lang]).all(axis=1).sum()
    result = get_handler("multi_choice_binary", "used", {**q["used"], "id": "used"}).analyze(df, lang)
    assert result.n == len(data) - nothing
