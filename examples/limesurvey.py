# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
The example surveys as a LimeSurvey export: a survey structure file (``.lss``) and the
responses (``.csv``) as LimeSurvey 6 writes them with the option "answer codes".

They are the material for the tutorial "from the survey tool" and for the end-to-end
check of that path: imported into a new survey, they must give the same results as
the example survey with its hand-written configuration.

Every question type of the examples maps to a LimeSurvey type: list (L), multiple
choice with "other" (M), array (F), ranking (R), multiple numerical input with a
constant sum (K), numerical input (N), date (D), long free text (T), multiple short
text (Q). The structure file has two languages (the language of the survey first),
the response file the system columns of LimeSurvey (id, submitdate, lastpage,
startlanguage, seed) and answer codes instead of answer texts.

Called by examples/generate.py; writes examples/limesurvey/.
"""
import csv
from pathlib import Path
from typing import Dict, List

GROUPS = {"de": ("Zu Ihrer Person", "KI an der Hochschule"), "en": ("About you", "AI at the university")}
OTHER_LANG = {"de": "en", "en": "de"}
SURVEY_ID = {"de": "481516", "en": "234223"}


def _cdata(value) -> str:
    text = "" if value is None else str(value)
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _table(name: str, fields: List[str], rows: List[Dict]) -> str:
    out = [f" <{name}>", "  <fields>"] + [f"   <fieldname>{f}</fieldname>" for f in fields] + ["  </fields>", "  <rows>"]
    for r in rows:
        out.append("   <row>" + "".join(f"<{f}>{_cdata(r.get(f, ''))}</{f}>" for f in fields) + "</row>")
    out += ["  </rows>", f" </{name}>"]
    return "\n".join(out)


def _questions(T: Dict[str, dict], lang: str):
    """[(code, type, group index, {lang: text}, answers [{lang: text}], subquestions [{lang: text}], attributes)]."""
    a, b = lang, OTHER_LANG[lang]
    both = lambda key: {a: T[a][key], b: T[b][key]}
    pair = lambda key, i: {a: T[a][key][i], b: T[b][key][i]}
    lists = lambda key, i: [{a: x, b: y} for x, y in zip(T[a][key][i], T[b][key][i])]
    sites = [{a: x, b: y} for x, y in zip(T[a]["sites"], T[b]["sites"])]
    return [
        ("role", "L", 0, pair("role", 0), lists("role", 1), [], {}),
        ("faculty", "L", 0, pair("faculty", 0), lists("faculty", 1), [], {}),
        ("age", "N", 0, pair("age", 0), [], [], {}),
        ("site", "L", 0, both("site"), sites, [], {}),
        ("sat", "L", 1, pair("sat", 0), lists("sat", 1), [], {}),
        ("nps", "L", 1, both("nps"), [{a: str(i), b: str(i)} for i in range(11)], [], {}),
        ("used", "M", 1, pair("used", 0), [], lists("used", 1)[:-1], {}),          # "other" as the other option
        ("rate", "F", 1, pair("rate", 0), lists("rate", 2), lists("rate", 1), {}),
        ("rank", "R", 1, pair("rank", 0), lists("rank", 1), [], {"max_answers": "3"}),
        ("budget", "K", 1, pair("budget", 0), [], lists("budget", 1), {"equals_num_value": "100"}),
        ("visit", "D", 1, both("visit"), [], [], {}),
        ("improve", "T", 1, both("improve"), [], [], {}),
        ("reasons", "Q", 1, both("reasons"), [], [{a: f"{i}.", b: f"{i}."} for i in (1, 2, 3)], {}),
    ]


def build_lss(T: Dict[str, dict], lang: str) -> str:
    """The structure file of the survey in ``lang`` (with the other language as second language)."""
    other = OTHER_LANG[lang]
    langs = [lang, other]
    sid = SURVEY_ID[lang]
    groups, group_l10ns, questions, subquestions, question_l10ns = [], [], [], [], []
    answers, answer_l10ns, attributes = [], [], []
    for gi in range(2):
        groups.append({"gid": gi + 1, "sid": sid, "group_order": gi, "randomization_group": "", "grelevance": ""})
        for l in langs:
            group_l10ns.append({"id": len(group_l10ns) + 1, "gid": gi + 1, "group_name": GROUPS[l][gi], "description": "", "language": l})
    qid = 0
    aid = 0
    for order, (code, qtype, gi, texts, opts, subs, attrs) in enumerate(_questions(T, lang)):
        qid += 1
        parent = qid
        questions.append({"qid": parent, "parent_qid": 0, "sid": sid, "gid": gi + 1, "type": qtype, "title": code,
                          "other": "Y" if code == "used" else "N", "mandatory": "N", "question_order": order,
                          "scale_id": 0, "same_default": 0, "relevance": "1"})
        for l in langs:
            question_l10ns.append({"id": len(question_l10ns) + 1, "qid": parent, "question": texts[l], "help": "", "language": l})
        for si, sub in enumerate(subs):
            qid += 1
            subquestions.append({"qid": qid, "parent_qid": parent, "sid": sid, "gid": gi + 1, "type": "T",
                                 "title": f"SQ{si + 1:03d}", "other": "N", "mandatory": "", "question_order": si + 1,
                                 "scale_id": 0, "same_default": 0, "relevance": "1"})
            for l in langs:
                question_l10ns.append({"id": len(question_l10ns) + 1, "qid": qid, "question": sub[l], "help": "", "language": l})
        for oi, opt in enumerate(opts):
            aid += 1
            code_ = str(oi) if code == "nps" else f"A{oi + 1}"
            answers.append({"aid": aid, "qid": parent, "code": code_, "sortorder": oi + 1, "assessment_value": 0, "scale_id": 0})
            for l in langs:
                answer_l10ns.append({"id": len(answer_l10ns) + 1, "aid": aid, "answer": opt[l], "language": l})
        for name, value in attrs.items():
            attributes.append({"qid": parent, "attribute": name, "value": value, "language": ""})
    surveys = [{"sid": sid, "language": lang, "additional_languages": other, "format": "G", "datestamp": "Y"}]
    settings = [{"surveyls_survey_id": sid, "surveyls_language": l, "surveyls_title": T[l]["title"],
                 "surveyls_description": T[l]["description"]} for l in langs]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<document>",
             " <LimeSurveyDocType>Survey</LimeSurveyDocType>", " <DBVersion>624</DBVersion>",
             " <languages>" + "".join(f"<language>{l}</language>" for l in langs) + "</languages>",
             _table("answers", ["aid", "qid", "code", "sortorder", "assessment_value", "scale_id"], answers),
             _table("answer_l10ns", ["id", "aid", "answer", "language"], answer_l10ns),
             _table("groups", ["gid", "sid", "group_order", "randomization_group", "grelevance"], groups),
             _table("group_l10ns", ["id", "gid", "group_name", "description", "language"], group_l10ns),
             _table("questions", ["qid", "parent_qid", "sid", "gid", "type", "title", "other", "mandatory",
                                  "question_order", "scale_id", "same_default", "relevance"], questions),
             _table("subquestions", ["qid", "parent_qid", "sid", "gid", "type", "title", "other", "mandatory",
                                     "question_order", "scale_id", "same_default", "relevance"], subquestions),
             _table("question_l10ns", ["id", "qid", "question", "help", "language"], question_l10ns),
             _table("question_attributes", ["qid", "attribute", "value", "language"], attributes),
             _table("surveys", ["sid", "language", "additional_languages", "format", "datestamp"], surveys),
             _table("surveys_languagesettings", ["surveyls_survey_id", "surveyls_language", "surveyls_title",
                                                 "surveyls_description"], settings),
             "</document>", ""]
    return "\n".join(parts)


def responses(T: Dict[str, dict], lang: str, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """The rows of the example data as LimeSurvey writes them (answer codes, LimeSurvey column names)."""
    t = T[lang]
    code = lambda options, value: "" if not value else f"A{options.index(value) + 1}"
    out = []
    for i, r in enumerate(rows, start=1):
        row = {"id": str(i), "submitdate": f"{r['visit'] or '2026-06-01'} 12:00:00", "lastpage": "2",
               "startlanguage": lang, "seed": str(100000 + i * 7919 % 900000)}
        row["role"] = code(t["role"][1], r["role"])
        row["faculty"] = code(t["faculty"][1], r["faculty"])
        row["age"] = r["age"]
        row["site"] = code(t["sites"], r["site"])
        row["sat"] = code(t["sat"][1], r["sat"])
        row["nps"] = r["nps"]
        for k in range(4):
            row[f"used[SQ{k + 1:03d}]"] = "Y" if r[f"used_{k}"] == t["yes"] else ""
        row["used[other]"] = r["used_other"] if r["used_4"] == t["yes"] else ""
        for k in range(3):
            row[f"rate[SQ{k + 1:03d}]"] = code(t["rate"][2], r[f"rate_{k}"])
        for k in range(3):
            row[f"rank[{k + 1}]"] = code(t["rank"][1], r[f"rank_{k}"])
        for k in range(3):
            row[f"budget[SQ{k + 1:03d}]"] = r[f"budget_{k}"]
        row["visit"] = f"{r['visit']} 00:00:00" if r["visit"] else ""
        row["improve"] = r["improve"]
        for k in range(3):
            row[f"reasons[SQ{k + 1:03d}]"] = r[f"reason_{k}"]
        out.append(row)
    return out


def write(T: Dict[str, dict], lang: str, data_csv: Path, out_dir: Path) -> List[Path]:
    """Writes ``<survey>.lss`` and ``<survey>_responses.csv`` from the example data file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = data_csv.stem
    with open(data_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    lss = out_dir / f"{name}.lss"
    lss.write_text(build_lss(T, lang), encoding="utf-8")
    resp = responses(T, lang, rows)
    out = out_dir / f"{name}_responses.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(resp[0]), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(resp)
    return [lss, out]
