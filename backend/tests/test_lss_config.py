# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Configuration generated from a LimeSurvey structure (decision 3.6.11)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.lss_config import build_config  # noqa: E402
from utils.lss_parser import AnswerOption, Question, QuestionGroup, QuestionText, SurveyData  # noqa: E402


def q(qid, title, qtype, text, answers=(), subs=(), order=0):
    question = Question(qid=qid, title=title, type=qtype, group_id="1", order=order,
                        texts=QuestionText(question=text))
    question.answers = [AnswerOption(code=c, texts=t, sort_order=i) for i, (c, t) in enumerate(answers)]
    question.subquestions = [Question(qid=f"{qid}{i}", title=st, type="subquestion", group_id="", order=i,
                                      texts=QuestionText(question=tt)) for i, (st, tt) in enumerate(subs)]
    return question


def survey():
    qs = [
        q("1", "Q1", "L", {"en": "Your role?", "de": "Ihre Rolle?"},
          answers=[("A1", {"en": "Staff", "de": "Personal"}), ("A2", {"en": "Student", "de": "Studium"})], order=1),
        q("2", "Q2", "M", {"en": "Services used", "de": "Genutzte Dienste"},
          subs=[("SQ1", {"en": "Library", "de": "Bibliothek"}), ("SQ2", {"en": "Canteen", "de": "Mensa"})], order=2),
        q("3", "Q3", "K", {"en": "How many staff?"},
          subs=[("F", {"en": "full time"}), ("P", {"en": "part time"})], order=3),
        q("4", "Q4", "F", {"en": "Rate"}, answers=[("1", {"en": "good"}), ("2", {"en": "bad"})],
          subs=[("R1", {"en": "Speed"}), ("R2", {"en": "Price"})], order=4),
        q("5", "Q5", "T", {"en": "Comments"}, order=5),
        q("6", "Q6", "D", {"en": "Start date"}, order=6),
        q("7", "Q7", ";", {"en": "Texts"}, order=7),
        q("8", "Q8", "X", {"en": "Thank you"}, order=8),
    ]
    return SurveyData(survey_id="123", languages=["en", "de"],
                      groups={"1": QuestionGroup(gid="1", order=1, names={"en": "Main"})},
                      questions={x.title: x for x in qs}, questions_by_qid={x.qid: x for x in qs})


def test_code_headed_export():
    columns = ["id", "Q1", "Q2[SQ1]", "Q2[SQ2]", "Q3[F]", "Q3[P]", "Q4[R1]", "Q4[R2]", "Q5", "Q6"]
    config, report = build_config(survey(), columns)
    g = config["question_groups"]
    assert config["settings"]["survey_metadata"]["source_language"] == "en"
    assert g["q1"]["handler"] == "single_choice" and g["q1"]["options"]["de"] == ["Personal", "Studium"]
    assert g["q2"]["handler"] == "multi_choice_binary" and g["q2"]["columns"] == ["Q2[SQ1]", "Q2[SQ2]"]
    assert g["q2"]["column_labels"] == {"Q2[SQ1]": "Library", "Q2[SQ2]": "Canteen"}
    # K: several number fields in one numeric_matrix question
    assert g["q3"]["handler"] == "numeric_matrix" and g["q3"]["columns"] == ["Q3[F]", "Q3[P]"]
    assert g["q3"]["item_labels"]["en"] == ["full time", "part time"] and "constant_sum" not in g["q3"]
    assert g["q4"]["row_labels"]["en"] == ["Speed", "Price"] and g["q4"]["scale"]["en"] == ["good", "bad"]
    assert g["q5"]["handler"] == "freetext"
    assert g["q6"]["handler"] == "date" and g["q6"]["columns"] == ["Q6"] and "q8" not in g   # date
    assert "Q7" in report["skipped"]                    # text matrix without rows/columns: reported
    assert report["unmatched_columns"] == ["id"]
    assert config["settings"]["report_groups"]["group_1"]["questions"][:2] == ["q1", "q2"]


def test_text_headed_export_and_handler_item_names():
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    columns = ["Your role?", "Services used [Library]", "Services used [Canteen]"]
    config, report = build_config(survey(), columns)
    g = config["question_groups"]
    assert g["q1"]["columns"] == ["Your role?"] and g["q2"]["columns"] == columns[1:]
    df = pd.DataFrame({columns[1]: ["Yes", "No"], columns[2]: ["Yes", "Yes"]})
    result = MultiChoiceBinaryHandler(g["q2"]).analyze(df, lang="en")
    assert set(result.data) == {"Library", "Canteen"}


def test_generated_config_passes_validation():
    from services.config_validation import validate_survey_config
    config, _ = build_config(survey(), ["Q1", "Q2[SQ1]", "Q2[SQ2]", "Q3[F]", "Q3[P]", "Q4[R1]", "Q4[R2]", "Q5"])
    errors, warnings = validate_survey_config(config)
    assert errors == []


def test_code_named_items_resolved_by_column_labels():
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    config, _ = build_config(survey(), ["Q2[SQ1]", "Q2[SQ2]"])
    df = pd.DataFrame({"Q2[SQ1]": ["Y", ""], "Q2[SQ2]": ["Y", "Y"]})
    result = MultiChoiceBinaryHandler(config["question_groups"]["q2"]).analyze(df, lang="en")
    assert result.data == {"Canteen": 2, "Library": 1} or dict(result.data) == {"Canteen": 2, "Library": 1}


def test_import_writes_generated_config_and_keeps_settings(tmp_path):
    import yaml
    from services.data_import import _write_generated_config
    path = tmp_path / "questions.yaml"
    existing = {"settings": {"llm_context": "Campus"}}
    status = _write_generated_config(survey(), ["Q1", "Q2[SQ1]", "Q2[SQ2]", "Q5", "Q6", "extra"], existing, path)
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["settings"]["llm_context"] == "Campus"
    assert written["settings"]["survey_metadata"]["source_language"] == "en"
    assert set(written["question_groups"]) == {"q1", "q2", "q5", "q6"}   # q6: date
    assert "Q7 not configured" in status and "columns without a question" in status


def test_answer_codes_recoded_and_fixed_types():
    from utils.structure_lss import lss_structure
    from utils.survey_structure import apply_answer_codes, build_config as build
    s = survey()
    extra = [q("9", "Q9", "5", {"en": "Overall"}, order=9), q("10", "Q10", "Y", {"en": "Again?"}, order=10),
             q("11", "Q11", "E", {"en": "Change"}, subs=[("S1", {"en": "Budget"})], order=11)]
    for x in extra:
        s.questions[x.title] = x
    df = pd.DataFrame({"Q1": ["A1", "A2", None], "Q4[R1]": ["1", "2", "1"], "Q4[R2]": ["2", "2", "1"],
                       "Q9": ["4", "5", "3"], "Q10": ["Y", "N", "Y"], "Q11[S1]": ["I", "D", "S"]})
    structure = lss_structure(s, list(df.columns))
    data, recoded = apply_answer_codes(df, structure)
    assert list(data["Q1"])[:2] == ["Staff", "Student"] and list(data["Q4[R1]"]) == ["good", "bad", "good"]
    assert list(data["Q10"]) == ["Yes", "No", "Yes"] and list(data["Q11[S1]"]) == ["Increased", "Decreased", "Unchanged"]
    assert set(recoded) == {"Q1", "Q4", "Q10", "Q11"}            # Q9: codes 1-5 are its texts already
    g = build(structure)[0]["question_groups"]
    assert g["q9"]["options"]["en"] == ["1", "2", "3", "4", "5"] and g["q9"]["ordinal"] is True
    assert g["q11"]["scale"]["de"] == ["Verstärkt", "Nicht verändert", "Gemindert"]   # recoded: scale from catalog
    untouched = lss_structure(s, list(df.columns))                     # exported with full answers
    assert "options" not in build(untouched)[0]["question_groups"]["q10"]


def test_full_answer_export_is_left_alone():
    from utils.structure_lss import lss_structure
    from utils.survey_structure import apply_answer_codes
    df = pd.DataFrame({"Q1": ["Staff", "Student"], "Q4[R1]": ["good", "bad"], "Q4[R2]": ["bad", "bad"]})
    data, recoded = apply_answer_codes(df, lss_structure(survey(), list(df.columns)))
    assert recoded == [] and data.equals(df)


def test_number_matrix_and_constant_sum_from_lss():
    from utils.structure_lss import lss_structure
    from utils.survey_structure import build_config as build
    s = survey()
    k = q("12", "Q12", "K", {"en": "Budget split"}, subs=[("A", {"en": "Staff"}), ("B", {"en": "Material"})], order=12)
    k.attributes = {"equals_num_value": "100"}
    m = q("13", "Q13", ":", {"en": "Hours"}, subs=[("R1", {"en": "Mon"}), ("R2", {"en": "Tue"}),
                                                  ("C1", {"en": "Office"}), ("C2", {"en": "Home"})], order=13)
    m.subquestions[2].scale_id = m.subquestions[3].scale_id = 1
    s.questions.update({"Q12": k, "Q13": m})
    columns = ["Q12[A]", "Q12[B]", "Q13[R1_C1]", "Q13[R1_C2]", "Hours [Tue] [Office]", "Q13[R2_C2]"]
    g = build(lss_structure(s, columns))[0]["question_groups"]
    assert g["q12"]["constant_sum"] == 100.0 and g["q12"]["columns"] == ["Q12[A]", "Q12[B]"]
    assert g["q13"]["handler"] == "numeric_matrix" and g["q13"]["columns"] == columns[2:]
    assert g["q13"]["item_labels"]["en"] == ["Mon – Office", "Mon – Home", "Tue – Office", "Tue – Home"]


def test_text_per_row_from_lss():
    from utils.structure_lss import lss_structure
    from utils.survey_structure import build_config as build
    s = survey()
    t = q("14", "Q14", ";", {"en": "Feedback"}, subs=[("R1", {"en": "Library"}), ("R2", {"en": "Canteen"}),
                                                     ("C1", {"en": "Good"}), ("C2", {"en": "Bad"})], order=14)
    t.subquestions[2].scale_id = t.subquestions[3].scale_id = 1
    s.questions["Q14"] = t
    g = build(lss_structure(s, ["Q14[R1_C1]", "Q14[R1_C2]", "Feedback [Canteen] [Good]", "Q14[R2_C2]"]))[0]["question_groups"]
    # one free-text question per cell
    assert g["q14_r1_c1"]["handler"] == "freetext" and g["q14_r1_c1"]["columns"] == ["Q14[R1_C1]"]
    assert g["q14_r2_c1"]["columns"] == ["Feedback [Canteen] [Good]"]
    assert g["q14_r2_c2"]["label"]["en"] == "Feedback – Canteen – Bad"


def test_other_and_comment_texts_become_linked_free_text():
    from utils.structure_lss import lss_structure
    from utils.survey_structure import build_config as build
    s = survey()
    p = q("15", "Q15", "P", {"en": "Tools"}, subs=[("T1", {"en": "Mail"}), ("T2", {"en": "Chat"})], order=15)
    s.questions["Q15"] = p
    columns = ["Q1", "Q1[other]", "Q15[T1]", "Q15[T2]", "Q15[T1comment]", "Q15[T2comment]"]
    g = build(lss_structure(s, columns))[0]["question_groups"]
    assert g["q1_other"]["handler"] == "freetext" and g["q1_other"]["columns"] == ["Q1[other]"]
    assert g["q1_other"]["other_of"] == "q1" and g["q1_other"]["label"]["en"] == "Your role? – Other"
    assert g["q15_other"]["columns"] == ["Q15[T1comment]", "Q15[T2comment]"]       # several fields: pooled
    assert g["q15_other"]["label"]["en"] == "Tools – Comments"
    keys = list(g)
    assert keys.index("q1_other") == keys.index("q1") + 1                          # right after the parent
