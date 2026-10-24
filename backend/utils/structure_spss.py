# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Adapter: SPSS data file (.sav) -> data frame with answer texts and
utils/survey_structure.SurveyStructure.

Most survey tools export SPSS (EvaSys, SoSci Survey, Unipark, SurveyMonkey,
QuestionPro, Qualtrics, LimeSurvey). The file carries variable labels
(question texts), value labels (answer options) and, from some tools,
multiple-response sets. It carries one language and no explicit question
type, so the structure is derived:

1. Multiple-response sets of the dichotomy kind -> multiple choice; the
   counted value is the "yes" value.
2. Consecutive variables with the same name stem and the same value labels
   form one question: with two labels (chosen / not chosen) a multiple
   choice, with more a matrix (rows = variables, scale = labels in code order).
3. A variable with value labels -> single choice (options in code order).
4. Numbers without labels -> numeric; date and time formats -> date
   (reported); strings -> free text, or single choice when they
   repeat a few values.

Answer codes with a label are replaced by the label in the data, so the
handlers see the answer texts. Read with pyreadstat (Apache-2.0).
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils.binary_values import is_affirmative
from utils.survey_structure import Choice, Item, StructQuestion, SurveyStructure

_CHOSEN = re.compile(r"(ausgew[aä]hlt|gew[aä]hlt|angekreuzt|selected|checked|chosen|coch[ée]|selezionat)", re.I)
_NOT = re.compile(r"\b(nicht|not|non|pas|un)", re.I)
_DATE_FORMATS = ("DATE", "ADATE", "EDATE", "SDATE", "JDATE", "DATETIME", "TIME", "DTIME", "WKDAY", "MONTH", "QYR", "MOYR", "WKYR")


def _stem(name: str) -> str:
    """Name stem of a variable (Q4_1, Q4r2, Q4.3, Q4[SQ1] -> Q4)."""
    m = re.match(r"^(.*?)(?:[_.\[]|r(?=\d))\w*\]?$", name)
    return m.group(1) if m and m.group(1) else name


def _common_prefix(texts: List[str]) -> str:
    if not texts:
        return ""
    prefix = os.path.commonprefix(texts)
    if len(prefix) >= min(len(t) for t in texts):
        return prefix.strip()
    # cut back to a separator ("Question - item", "Question: item", "Question [item]"),
    # else to a word boundary
    for seps in ((" - ", " – ", ": ", "? ", " ["), (" ",)):
        cut = max(prefix.rfind(sep) for sep in seps)
        if cut > 0:
            return prefix[:cut + 1].strip(" -–:[")
    return ""


def _rest(text: str, prefix: str) -> str:
    rest = text[len(prefix):] if prefix and text.startswith(prefix) else text
    return rest.strip(" -–:[]") or text


def _positive_label(labels: Dict) -> Tuple[object, str]:
    """The value of a two-label set that means 'chosen'."""
    items = sorted(labels.items(), key=lambda kv: kv[0])
    for code, text in items:
        if (_CHOSEN.search(str(text)) and not _NOT.search(str(text))) or is_affirmative(str(text)):
            return code, str(text)
    return items[-1][0], str(items[-1][1])


def read_spss(path: str, language: Optional[str] = None) -> Tuple[pd.DataFrame, SurveyStructure]:
    import pyreadstat
    raw, meta = pyreadstat.read_sav(path)
    return spss_structure(raw, meta, language)


def spss_structure(raw: pd.DataFrame, meta, language: Optional[str] = None) -> Tuple[pd.DataFrame, SurveyStructure]:
    lang = language or _guess_language(meta)
    labels_of = {k: dict(v) for k, v in (getattr(meta, "variable_value_labels", None) or {}).items()}
    var_label = dict(getattr(meta, "column_names_to_labels", None) or {})
    formats = dict(getattr(meta, "original_variable_types", None) or {})
    measure = dict(getattr(meta, "variable_measure", None) or {})
    structure = SurveyStructure("spss", os.path.basename(getattr(meta, "file_label", "") or "") or "spss",
                                [lang], [])
    used: set = set()

    def text_of(col: str) -> str:
        return str(var_label.get(col) or col)

    # 1. multiple-response sets (dichotomy)
    for name, mr in (getattr(meta, "mr_sets", None) or {}).items():
        variables = [v for v in mr.get("variable_list", []) if v in raw.columns]
        if not variables:
            continue
        if not mr.get("is_dichotomy"):
            structure.skipped[name] = "multiple response set with categories (not dichotomous): not supported"
            continue
        texts = [text_of(v) for v in variables]
        prefix = mr.get("label") or _common_prefix(texts)
        q = StructQuestion(name, "multi", {lang: prefix or name}, source_type="spss:mr_set")
        q.items = [Item(v, {lang: _rest(t, prefix)}, v) for v, t in zip(variables, texts)]
        counted = mr.get("counted_value")
        q.positive_value = str(labels_of.get(variables[0], {}).get(_num(counted), counted))
        structure.questions.append(q)
        used.update(variables)

    # 2.-4. remaining variables in file order
    columns = [c for c in raw.columns if c not in used]
    i = 0
    while i < len(columns):
        col = columns[i]
        labels = labels_of.get(col)
        run = [col]
        if labels:
            j = i + 1
            while j < len(columns) and _stem(columns[j]) == _stem(col) and labels_of.get(columns[j]) == labels:
                run.append(columns[j])
                j += 1
        if len(run) > 1:
            texts = [text_of(c) for c in run]
            prefix = _common_prefix(texts)
            stem = _stem(col)
            if len(labels) == 2:
                q = StructQuestion(stem, "multi", {lang: prefix or stem}, source_type="spss:dichotomies")
                q.positive_value = _positive_label(labels)[1]
            else:
                q = StructQuestion(stem, "matrix", {lang: prefix or stem}, source_type="spss:value_labels")
                q.choices = [Choice(str(k), {lang: str(v)}) for k, v in sorted(labels.items())]
            q.items = [Item(c, {lang: _rest(t, prefix)}, c) for c, t in zip(run, texts)]
            structure.questions.append(q)
            i += len(run)
            continue
        fmt = str(formats.get(col, "")).upper()
        if labels:
            q = StructQuestion(col, "single", {lang: text_of(col)}, source_type="spss:value_labels",
                               choices=[Choice(str(k), {lang: str(v)}) for k, v in sorted(labels.items())],
                               columns=[col], ordinal=measure.get(col) == "ordinal")
        elif fmt.startswith(_DATE_FORMATS):
            q = StructQuestion(col, "date", {lang: text_of(col)}, source_type=f"spss:{fmt}", columns=[col])
        elif pd.api.types.is_numeric_dtype(raw[col]):
            q = StructQuestion(col, "numeric", {lang: text_of(col)}, source_type=f"spss:{fmt or 'numeric'}",
                               columns=[col])
        else:
            values = raw[col].dropna().astype(str).str.strip()
            values = values[values != ""]
            few = len(values) and values.nunique() <= 30 and values.nunique() / len(values) < 0.3
            q = StructQuestion(col, "single" if few else "text", {lang: text_of(col)},
                               source_type="spss:string", columns=[col])
        structure.questions.append(q)
        i += 1

    data = _apply_labels(raw, labels_of)
    return data, structure


def _num(value):
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return value


def _apply_labels(raw: pd.DataFrame, labels_of: Dict[str, Dict]) -> pd.DataFrame:
    """Answer codes with a label -> label (codes without a label stay as they are)."""
    data = raw.copy()
    for col, labels in labels_of.items():
        if col in data.columns and labels:
            mapping = {**{k: v for k, v in labels.items()}, **{_num(k): v for k, v in labels.items()}}
            data[col] = data[col].map(lambda v: mapping.get(_num(v), v) if pd.notna(v) else v)
    return data


def _guess_language(meta) -> str:
    """Language of the labels (langdetect over all labels); the default source language if unsure."""
    from utils.prompts import source_language
    texts = " ".join(str(t) for t in (getattr(meta, "column_labels", None) or []) if t)
    for labels in (getattr(meta, "variable_value_labels", None) or {}).values():
        texts += " " + " ".join(str(t) for t in labels.values())
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        if len(texts) > 40:
            found = detect(texts)
            if found in ("de", "en", "fr", "it"):
                return found
    except Exception:
        pass
    return source_language()
