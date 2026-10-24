# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Qualtrics adapter (decision 3.6.12): QSF + CSV with ImportId row."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.structure_qualtrics import qualtrics_structure, read_qualtrics_csv  # noqa: E402
from utils.survey_structure import build_config  # noqa: E402


def sq(qid, tag, qtype, sel, text, choices=None, answers=None, sub=None, lang=None, recode=None):
    p = {"QuestionID": qid, "DataExportTag": tag, "QuestionType": qtype, "Selector": sel,
         "QuestionText": f"<p>{text}</p>"}
    if sub:
        p["SubSelector"] = sub
    if choices:
        p["Choices"] = {str(i + 1): {"Display": c} for i, c in enumerate(choices)}
        p["ChoiceOrder"] = [str(i + 1) for i in range(len(choices))]
    if answers:
        p["Answers"] = {str(i + 1): {"Display": a} for i, a in enumerate(answers)}
        p["AnswerOrder"] = [str(i + 1) for i in range(len(answers))]
    if lang:
        p["Language"] = lang
    if recode:
        p["RecodeValues"] = recode
    return {"Element": "SQ", "PrimaryAttribute": qid, "Payload": p}


QSF = {"SurveyEntry": {"SurveyID": "SV_1", "SurveyName": "Campus", "SurveyLanguage": "EN"},
       "SurveyElements": [
           {"Element": "BL", "Payload": [{"ID": "BL_1", "Description": "Main", "BlockElements": [
               {"Type": "Question", "QuestionID": q} for q in ("QID1", "QID2", "QID3", "QID4", "QID5", "QID6", "QID7")]}]},
           sq("QID1", "Q1", "MC", "SAVR", "Your role?", ["Staff", "Student"],
              lang={"DE": {"QuestionText": "Ihre Rolle?", "Choices": {"1": {"Display": "Personal"}, "2": {"Display": "Studium"}}}},
              recode={"1": "1", "2": "2"}),
           sq("QID2", "Q2", "MC", "MAVR", "Services used", ["Library", "Canteen"]),
           sq("QID3", "Q3", "Matrix", "Likert", "Rate", ["Speed", "Price"], ["good", "bad"], sub="SingleAnswer"),
           sq("QID4", "Q4", "TE", "ML", "Comments"),
           sq("QID5", "Q5", "RO", "DND", "Rank these", ["A", "B"]),
           sq("QID6", "Q6", "DB", "TB", "Thank you"),
           sq("QID7", "Q7", "Matrix", "TE", "Texts per row", ["r1"], ["c1"]),
       ]}

CSV = [
    ["ResponseId", "Q1", "Q2", "Q3_1", "Q3_2", "Q4", "Q5_1", "Q5_2"],
    ["Response ID", "Your role?", "Services used", "Rate - Speed", "Rate - Price", "Comments", "Rank - A", "Rank - B"],
    ['{"ImportId":"_recordId"}', '{"ImportId":"QID1"}', '{"ImportId":"QID2"}', '{"ImportId":"QID3_1"}',
     '{"ImportId":"QID3_2"}', '{"ImportId":"QID4_TEXT"}', '{"ImportId":"QID5_1"}', '{"ImportId":"QID5_2"}'],
    ["R_1", "1", "Library,Canteen", "good", "bad", "More rooms", "1", "2"],
    ["R_2", "2", "Canteen", "bad", "bad", "", "2", "1"],
    ["R_3", "1", "", "good", "good", "Longer hours", "1", "2"],
]


def files(tmp_path):
    import csv
    path = tmp_path / "export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(CSV)
    return path


def test_header_rows_dropped_and_import_ids_read(tmp_path):
    data, ids, texts = read_qualtrics_csv(str(files(tmp_path)))
    assert len(data) == 3 and ids["Q3_2"] == "QID3_2" and texts["Q1"] == "Your role?"


def test_structure_config_and_prepared_data(tmp_path):
    data, ids, _ = read_qualtrics_csv(str(files(tmp_path)))
    data, structure = qualtrics_structure(QSF, data, ids)
    config, report = build_config(structure)
    g = config["question_groups"]
    assert config["settings"]["survey_metadata"]["languages"] == ["en", "de"]
    assert g["q1"]["options"] == {"en": ["Staff", "Student"], "de": ["Personal", "Studium"]}
    assert list(data["Q1"]) == ["Staff", "Student", "Staff"]                       # codes -> texts
    assert g["q2"]["binary_values"] == {"positive": "1"} and g["q2"]["item_labels"]["en"] == ["Library", "Canteen"]
    import pandas as pd
    second = list(data[g["q2"]["columns"][1]])
    assert second[:2] == ["1", "1"] and pd.isna(second[2])                       # skipped question stays empty
    assert g["q3"]["row_labels"]["en"] == ["Speed", "Price"] and g["q3"]["scale"]["en"] == ["good", "bad"]
    assert g["q4"]["columns"] == ["Q4"]
    assert g["q5"]["columns"] == ["Q5__rank1", "Q5__rank2"] and list(data["Q5__rank1"]) == ["A", "B", "A"]
    assert "q6" not in g and report["unmatched_columns"] == ["ResponseId"]
    assert "Q7_1_1" in report["missing"]                # text matrix: one free text per cell; columns absent here
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    assert dict(MultiChoiceBinaryHandler(g["q2"]).analyze(data, lang="en").data) == {"Canteen": 2, "Library": 1}
    from services.config_validation import validate_survey_config
    assert validate_survey_config(config)[0] == []


def test_import_with_qsf_and_qualtrics_csv_without_qsf(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import yaml
    import services.data_import as di
    from core import state
    monkeypatch.setattr(di, "get_active_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(state, "db", None)
    monkeypatch.setattr(state, "project_id", None)
    qsf = tmp_path / "survey.qsf"
    qsf.write_text(json.dumps(QSF), encoding="utf-8")
    msg, df = di.import_data(SimpleNamespace(name=str(files(tmp_path))), lss_file=SimpleNamespace(name=str(qsf)))
    assert len(df) == 3 and "created from the QUALTRICS file" in msg
    written = yaml.safe_load((tmp_path / "cfg" / "questions.yaml").read_text(encoding="utf-8"))
    assert {"q1", "q2", "q3", "q4", "q5"} <= set(written["question_groups"])
    # without the QSF the two extra header rows are still not taken for answers
    msg, df = di.import_data(SimpleNamespace(name=str(files(tmp_path))))
    assert len(df) == 3 and "Qualtrics" in msg


def test_side_by_side_matrix_multi_answer_loops_and_excel():
    import pandas as pd
    from utils.structure_qualtrics import split_qualtrics_header
    sbs = {"Element": "SQ", "Payload": {
        "QuestionID": "QID8", "DataExportTag": "Q8", "QuestionType": "SBS", "Selector": "SBSMatrix",
        "QuestionText": "Services", "AdditionalQuestions": {
            "1": {"QuestionType": "Matrix", "Selector": "Likert", "SubSelector": "SingleAnswer", "QuestionText": "Quality",
                  "Choices": {"1": {"Display": "Library"}}, "ChoiceOrder": ["1"],
                  "Answers": {"1": {"Display": "good"}, "2": {"Display": "bad"}}, "AnswerOrder": ["1", "2"]},
            "2": {"QuestionType": "Matrix", "Selector": "Likert", "SubSelector": "SingleAnswer", "QuestionText": "Use",
                  "Choices": {"1": {"Display": "Library"}}, "ChoiceOrder": ["1"],
                  "Answers": {"1": {"Display": "often"}, "2": {"Display": "rarely"}}, "AnswerOrder": ["1", "2"]}}}}
    mma = sq("QID9", "Q9", "Matrix", "Likert", "Devices per place", ["Home", "Office"], ["Laptop", "Phone"],
             sub="MultipleAnswer")
    loop_q = sq("QID10", "Q10", "TE", "SL", "Name")
    qsf = {"SurveyEntry": {"SurveyID": "SV_2", "SurveyLanguage": "EN"}, "SurveyElements": [sbs, mma, loop_q]}
    raw = pd.DataFrame([
        ["Services - Quality - Library", "Services - Use - Library", "Home - Laptop", "Home - Phone",
         "Office - Laptop", "Office - Phone", "1 Name", "2 Name"],
        ['{"ImportId":"QID8#1_1"}', '{"ImportId":"QID8#2_1"}', '{"ImportId":"QID9_1_1"}', '{"ImportId":"QID9_1_2"}',
         '{"ImportId":"QID9_2_1"}', '{"ImportId":"QID9_2_2"}', '{"ImportId":"1_QID10_TEXT"}', '{"ImportId":"2_QID10_TEXT"}'],
        ["good", "often", "Laptop", "", "", "Phone", "a", "b"],
        ["bad", "rarely", "", "", "Laptop", "", "c", "d"]],
        columns=["Q8#1_1", "Q8#2_1", "Q9_1_1", "Q9_1_2", "Q9_2_1", "Q9_2_2", "1_Q10", "2_Q10"])
    data, ids, _ = split_qualtrics_header(raw)                       # as read from an Excel export
    assert len(data) == 2 and ids["Q9_2_1"] == "QID9_2_1"
    data, structure = qualtrics_structure(qsf, data, ids)
    config, report = build_config(structure)
    g = config["question_groups"]
    assert g["q8_1"]["label"]["en"] == "Services – Quality" and g["q8_1"]["scale"]["en"] == ["good", "bad"]
    assert g["q8_2"]["columns"] == ["Q8#2_1"]
    assert g["q9_1"]["label"]["en"] == "Devices per place – Home" and g["q9_1"]["binary_values"] == {"positive": "1"}
    home = list(data[g["q9_1"]["columns"][0]])
    assert home[0] == "1" and pd.isna(home[1])                     # row "Home": second respondent chose nothing
    assert "loop:Q10" in report["skipped"]


def test_constant_sum_slider_and_matrix_constant_sum():
    import pandas as pd
    cs = sq("QID11", "Q11", "CS", "VRTL", "Split 100 points", ["A", "B"])
    cs["Payload"]["Validation"] = {"Settings": {"Total": "100"}}
    slider = sq("QID12", "Q12", "Slider", "HSLIDER", "Rate", ["Speed"])
    mcs = sq("QID13", "Q13", "Matrix", "CS", "Hours", ["Mon"], ["Office", "Home"], sub="Horizontal")
    qsf = {"SurveyEntry": {"SurveyID": "SV_3", "SurveyLanguage": "EN"}, "SurveyElements": [cs, slider, mcs]}
    data = pd.DataFrame({"Q11_1": ["60", "50"], "Q11_2": ["40", "50"], "Q12_1": ["7", "9"],
                         "Q13_1_1": ["5", "3"], "Q13_1_2": ["2", "4"]})
    ids = {c: "QID" + c[1:] for c in data.columns}
    data, structure = qualtrics_structure(qsf, data, ids)
    g = build_config(structure)[0]["question_groups"]
    assert g["q11"]["handler"] == "numeric_matrix" and g["q11"]["constant_sum"] == 100.0
    assert g["q12"]["handler"] == "numeric_matrix" and g["q12"]["columns"] == ["Q12_1"]
    assert g["q13"]["columns"] == ["Q13_1_1", "Q13_1_2"] and g["q13"]["item_labels"]["en"] == ["Mon – Office", "Mon – Home"]


def test_text_matrix_per_row():
    import pandas as pd
    te = sq("QID14", "Q14", "Matrix", "TE", "Feedback", ["Library", "Canteen"], ["Good", "Bad"])
    qsf = {"SurveyEntry": {"SurveyID": "SV_4", "SurveyLanguage": "EN"}, "SurveyElements": [te]}
    data = pd.DataFrame({"Q14_1_1": ["quiet"], "Q14_1_2": ["cold"], "Q14_2_1": ["tasty"], "Q14_2_2": [""]})
    data, structure = qualtrics_structure(qsf, data, {c: "QID" + c[1:] for c in data.columns})
    g = build_config(structure)[0]["question_groups"]
    assert g["q14_1_1"]["handler"] == "freetext" and g["q14_1_1"]["columns"] == ["Q14_1_1"]
    assert g["q14_2_2"]["label"]["en"] == "Feedback – Canteen – Bad"


def test_choice_text_entry_becomes_linked_free_text():
    import pandas as pd
    mc = sq("QID20", "Q20", "MC", "SAVR", "Browser", ["Firefox", "Other"])
    qsf = {"SurveyEntry": {"SurveyID": "SV_5", "SurveyLanguage": "EN"}, "SurveyElements": [mc]}
    data = pd.DataFrame({"Q20": ["Firefox", "Other"], "Q20_2_TEXT": ["", "Brave"]})
    data, structure = qualtrics_structure(qsf, data, {"Q20": "QID20", "Q20_2_TEXT": "QID20_2_TEXT"})
    config, report = build_config(structure)
    g = config["question_groups"]
    assert g["q20_other"]["columns"] == ["Q20_2_TEXT"] and g["q20_other"]["other_of"] == "q20"
    assert g["q20_other"]["label"]["en"] == "Browser – Other" and report["unmatched_columns"] == []
