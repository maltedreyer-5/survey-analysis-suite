# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Survey structure from the data alone (no structure file of the survey tool).

Used when answers are imported without a LimeSurvey, Qualtrics or SPSS structure
and the active survey has no configuration yet. The structure is inferred from the
column names and the values; the configuration that ``survey_structure.build_config``
makes of it is a starting point that has to be checked:

- technical columns (ids, time stamps, tokens, metadata of survey tools) are left out
  and reported;
- columns with a common stem (``q[SQ001]``, ``q_1``, ``q.2``) form one question;
- the kind of a question follows the values: yes/no or one value per column →
  multiple choice; numbers → number or number matrix (with a constant-sum check);
  every row an order of the same items → ranking; long texts → free text; a few
  values shared by all columns → matrix; dates → date; otherwise single choice;
- "other" and comment columns become the free text of their question.

What the data cannot tell is reported in ``notes``: the order of a scale (answer
options are listed by frequency) and whether a single choice is an ordered scale.
"""
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils.survey_structure import Choice, Item, StructQuestion, SurveyStructure, other_text_question

TECHNICAL = re.compile(
    r"(?i)^(id|response_?id|respondent_?id|respid|submitdate|startdate|enddate|datestamp|recordeddate|"
    r"lastpage|startlanguage|userlanguage|seed|token|ipaddr|ipaddress|refurl|interviewtime|grouptime\w*|"
    r"timestamp|zeitstempel|duration.*|progress|finished|status|locationlatitude|locationlongitude|"
    r"distributionchannel|externalreference|recipient\w*)$")
OTHER_SUFFIX = re.compile(r"(?i)^(other|comment|sonstiges|andere|autre|altro)$")
GROUPED = re.compile(r"^(?P<stem>.+?)(?:\[(?P<bracket>[^\]]+)\]|[_.](?P<suffix>[A-Za-z]{0,3}\d+|other|comment|sonstiges|andere|autre|altro))$",
                     re.IGNORECASE)
YES = {"yes", "ja", "oui", "sì", "si", "y", "j", "1", "true", "x", "checked", "ausgewählt", "selected"}
NO = {"no", "nein", "non", "n", "0", "false", "not selected", "nicht ausgewählt", "unchecked"}
MAX_SCALE_VALUES = 10


def _values(series: pd.Series) -> pd.Series:
    s = series.dropna().astype(str).str.strip()
    return s[s != ""]


def _numeric(values: pd.Series) -> Optional[pd.Series]:
    from utils.numeric_coerce import coerce_series
    if values.empty:
        return None
    num = coerce_series(values)
    return num if num.notna().mean() >= 0.9 else None


def _is_date(values: pd.Series) -> bool:
    if values.empty or not values.str.contains(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}").mean() >= 0.9:
        return False
    parsed = pd.to_datetime(values, errors="coerce", dayfirst=values.str.contains(r"^\d{1,2}\.").any())
    return parsed.notna().mean() >= 0.9


def _is_text(values: pd.Series) -> bool:
    if values.empty:
        return False
    words = values.str.split().map(len).mean()
    return (values.str.len().mean() > 40
            or (words >= 3.5 and values.nunique() >= 10)        # sentences rather than options
            or (words >= 3.5 and len(values) >= 3 and values.nunique() / len(values) >= 0.9)   # few, all different
            or (values.nunique() > 20 and values.nunique() / len(values) > 0.5))


def _by_frequency(values: pd.Series) -> List[str]:
    return list(values.value_counts().index)


def _groups(columns: List[str]) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """[(stem, [(column, part)])] in column order; a column without a common stem is its own group."""
    stems: Dict[str, List[Tuple[str, str]]] = {}
    order: List[str] = []
    for col in columns:
        m = GROUPED.match(col)
        stem, part = (m.group("stem"), m.group("bracket") or m.group("suffix")) if m else (col, "")
        if stem not in stems:
            stems[stem] = []
            order.append(stem)
        stems[stem].append((col, part))
    out = []
    for stem in order:
        members = stems[stem]
        if len(members) == 1 and not OTHER_SUFFIX.match(members[0][1] or ""):
            out.append((members[0][0], [(members[0][0], "")]))   # a single column keeps its own name
        else:
            out.append((stem, members))
    return out


def infer_structure(df: pd.DataFrame, source_language: str) -> Tuple[SurveyStructure, List[str]]:
    """``(structure, notes)`` for the data; ``notes`` name what has to be checked by hand."""
    src = source_language
    technical = [c for c in df.columns if TECHNICAL.match(str(c))]
    columns = [c for c in df.columns if c not in technical]
    questions: List[StructQuestion] = []
    notes: List[str] = []
    for stem, members in _groups(columns):
        main = [(c, p) for c, p in members if not OTHER_SUFFIX.match(p or "")]
        others = [c for c, p in members if OTHER_SUFFIX.match(p or "")]
        if not main:           # only an "other" column: an ordinary free text
            main, others = [(others[0], "")], others[1:]
        q = _question(stem, main, df, src, notes)
        questions.append(q)
        if others:
            questions.append(other_text_question(q, others, [src], {src: "other"}))
    if technical:
        notes.append(f"technical columns left out: {', '.join(map(str, technical))}")
    structure = SurveyStructure(source_format="data", survey_id="", languages=[src], questions=questions,
                                unmatched_columns=list(map(str, technical)))
    return structure, notes


def _question(stem: str, members: List[Tuple[str, str]], df: pd.DataFrame, src: str, notes: List[str]) -> StructQuestion:
    texts = {src: str(stem)}
    if len(members) == 1:
        col = members[0][0]
        values = _values(df[col])
        num = _numeric(values)
        if num is not None:
            whole = (num.dropna() % 1 == 0).all()
            distinct = sorted(num.dropna().unique())
            if whole and distinct and min(distinct) >= 0 and max(distinct) <= 10 and len(distinct) <= 11 \
                    and re.search(r"(?i)nps|recommend|empfehl|recommand|raccomand", str(col)):
                return StructQuestion(code=col, kind="single", texts=texts, columns=[col], nps=True, ordinal=True,
                                      choices=[Choice(code=str(int(v)), texts={src: str(int(v))}) for v in range(11)],
                                      source_type="data:nps")
            if whole and len(distinct) <= 7:
                notes.append(f"{col}: whole numbers 1 to {len(distinct)}-like, configured as an ordered single choice; "
                             f"give the meaning of the values (options) or make it numeric")
                return StructQuestion(code=col, kind="single", texts=texts, columns=[col], ordinal=True,
                                      choices=[Choice(code=str(int(v)), texts={src: str(int(v))}) for v in distinct],
                                      source_type="data:codes")
            return StructQuestion(code=col, kind="numeric", texts=texts, columns=[col], source_type="data:number")
        if _is_date(values):
            return StructQuestion(code=col, kind="date", texts=texts, columns=[col], source_type="data:date")
        if _is_text(values):
            return StructQuestion(code=col, kind="text", texts=texts, columns=[col], source_type="data:text")
        choices = _by_frequency(values)
        if len(choices) > 1:
            notes.append(f"{col}: answer options listed by frequency; set their order (or a named scale) if they form a scale")
        return StructQuestion(code=col, kind="single", texts=texts, columns=[col], source_type="data:choice",
                              choices=[Choice(code=v, texts={src: v}) for v in choices])

    cols = [c for c, _ in members]
    items = [Item(code=p or c, texts={src: c}, column=c) for c, p in members]
    per_col = {c: _values(df[c]) for c in cols}
    every = pd.concat(list(per_col.values())) if per_col else pd.Series(dtype=str)
    lowered = set(every.str.lower())
    if lowered and lowered <= (YES | NO):
        positive = next((v for v in _by_frequency(every) if v.lower() in YES), None)
        return StructQuestion(code=stem, kind="multi", texts=texts, items=items, positive_value=positive,
                              source_type="data:yes_no")
    if all(per_col[c].nunique() <= 1 for c in cols) and lowered:
        return StructQuestion(code=stem, kind="multi", texts=texts, items=items, source_type="data:checkboxes")
    if all(_numeric(per_col[c]) is not None for c in cols if not per_col[c].empty):
        constant = _constant_sum(df[cols])
        return StructQuestion(code=stem, kind="numeric_matrix", texts=texts, items=items, constant_sum=constant,
                              source_type="data:numbers")
    if _is_ranking(df[cols]):
        return StructQuestion(code=stem, kind="ranking", texts=texts, columns=cols, source_type="data:ranking",
                              choices=[Choice(code=v, texts={src: v}) for v in _by_frequency(every)])
    if _is_text(every):
        return StructQuestion(code=stem, kind="text", texts=texts, items=items, source_type="data:text")
    if every.nunique() <= MAX_SCALE_VALUES:
        notes.append(f"{stem}: scale points listed by frequency; set their order (or a named scale)")
        return StructQuestion(code=stem, kind="matrix", texts=texts, items=items, source_type="data:matrix",
                              choices=[Choice(code=v, texts={src: v}) for v in _by_frequency(every)])
    return StructQuestion(code=stem, kind="text", texts=texts, items=items, source_type="data:text")


def _constant_sum(block: pd.DataFrame) -> Optional[float]:
    from utils.numeric_coerce import coerce_series
    sums = block.apply(coerce_series).sum(axis=1, min_count=1).dropna().round(6)
    if sums.empty:
        return None
    top = sums.mode().iloc[0]
    return float(top) if (sums == top).mean() >= 0.8 and top > 0 else None


def _is_ranking(block: pd.DataFrame) -> bool:
    rows = block.apply(lambda r: [str(v).strip() for v in r if pd.notna(v) and str(v).strip()], axis=1)
    rows = rows[rows.map(len) >= 2]
    if rows.empty:
        return False
    distinct_rows = rows.map(lambda r: len(set(r)) == len(r)).mean()
    labels = {v for r in rows for v in r}
    return distinct_rows >= 0.95 and len(labels) >= block.shape[1] and len(labels) <= 3 * block.shape[1] \
        and not all(re.fullmatch(r"-?\d+([.,]\d+)?", v) for v in labels)
