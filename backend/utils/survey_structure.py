# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Format-neutral structure of a survey and the configuration built from it
(decision 3.6.12).

Every input format has an adapter that reads its structure file and the
data columns into a ``SurveyStructure``; the columns are assigned to the
questions by the adapter, because only it knows how its format names them:

- utils/structure_lss.py       LimeSurvey structure file (.lss)
- utils/structure_spss.py      SPSS data file (.sav) with its labels
- utils/structure_qualtrics.py Qualtrics survey file (.qsf) and CSV export

``build_config`` turns the structure into a survey configuration
(``question_groups`` and ``settings``) the same way for every format: the
kind of a question decides the handler; texts, options, items and scales
come in every language the format provides, the first language is the
source language. Kinds the handlers cannot evaluate yet are reported, not
configured, so nothing is evaluated wrongly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Kinds of questions (format-neutral) and their handlers
HANDLER_BY_KIND = {
    "single": "single_choice",      # one answer from a list (one column)
    "multi": "multi_choice_binary",  # several answers, one yes/no column per option
    "text": "freetext",             # free text, one or several columns
    "ranking": "ranking",           # one column per rank
    "matrix": "matrix_likert",      # rows (items) rated on one scale
    "numeric": "numeric",           # one number
    "numbers": "numeric",           # several number fields: one numeric question per field
    "numeric_matrix": "numeric_matrix",  # several numbers in one question (fields, matrix, constant sum)
    "date": "date",                 # a date
}
NOT_SUPPORTED = {                    # not supported (reported)
    "text_matrix": "text per row",
    "dual_scale": "dual scale (not supported; documented limit)",
}


@dataclass
class Choice:
    """An answer option (single choice, ranking) or a scale point (matrix)."""
    code: str
    texts: Dict[str, str] = field(default_factory=dict)


@dataclass
class Item:
    """A row of a matrix, an option of a multiple choice, a field of several numbers."""
    code: str
    texts: Dict[str, str] = field(default_factory=dict)
    column: Optional[str] = None


@dataclass
class StructQuestion:
    code: str                                   # code in the source format (becomes the id)
    kind: str                                   # see HANDLER_BY_KIND / NOT_SUPPORTED
    texts: Dict[str, str] = field(default_factory=dict)
    help: Dict[str, str] = field(default_factory=dict)
    choices: List[Choice] = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)   # single, text, ranking: in order
    group: Optional[str] = None
    source_type: str = ""                       # type in the source format, e.g. "lss:L"
    missing: List[str] = field(default_factory=list)   # parts whose column was not found
    positive_value: Optional[str] = None        # multi: the value that means "chosen", if known
    ordinal: bool = False                       # single: the options are ordered (a scale)
    code_only_choices: bool = False             # choices only known as codes of the source tool:
                                                # used when the data hold these codes (then recoded)
    recoded: bool = False                       # answer codes in the data were replaced by texts
    constant_sum: Optional[float] = None        # numeric_matrix: the fields add up to this total
    nps: bool = False                           # single: a Net Promoter Score question (0-10)
    other_of: Optional[str] = None              # text: the "other, please specify" text of this question
    text_item_columns: List[str] = field(default_factory=list)   # multi: items chosen when their text is filled


@dataclass
class StructGroup:
    id: str
    order: int
    texts: Dict[str, str] = field(default_factory=dict)


@dataclass
class SurveyStructure:
    source_format: str                          # lss | spss | qualtrics
    survey_id: str
    languages: List[str]
    questions: List[StructQuestion]
    groups: List[StructGroup] = field(default_factory=list)
    unmatched_columns: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)   # questions the adapter could not read
    title: Dict[str, str] = field(default_factory=dict)


def other_text_question(parent: "StructQuestion", columns: List[str], languages: List[str],
                        label: Dict[str, str]) -> "StructQuestion":
    """
    The free text of an "other, please specify" answer as a question
    of its own, linked to its parent (``other_of``): clustered and
    segmented like every free text, placed right after the parent.
    """
    texts = {lang: f"{parent.texts.get(lang) or parent.texts.get(languages[0], parent.code)} – {label.get(lang) or label.get(languages[0], '')}"
             for lang in languages if parent.texts.get(lang) or lang == languages[0]}
    return StructQuestion(code=f"{parent.code}_other", kind="text", texts=texts, group=parent.group,
                          source_type=parent.source_type, columns=list(columns), other_of=parent.code)


def config_id(code: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(code).lower()).strip("_") or "q"


def _texts(texts: Dict[str, str], languages: List[str]) -> Dict[str, str]:
    return {lang: texts[lang] for lang in languages if texts.get(lang)}


def _lists(entries: List[Dict[str, str]], languages: List[str], src: str) -> Dict[str, List[str]]:
    """[{lang: text}] -> {lang: [text, ...]} (a missing text: the source text)."""
    return {lang: [e.get(lang) or e.get(src) or "" for e in entries] for lang in languages}


def apply_answer_codes(data, structure: SurveyStructure):
    """
    Exports with answer codes instead of answer texts (e.g. LimeSurvey's
    option "answer codes"): a column whose values are all codes of the
    question's choices, and not already its texts, gets the texts of the
    source language. Returns ``(data, recoded question codes)``.
    """
    import pandas as pd
    src = (structure.languages or ["de"])[0]
    data = data.copy()
    recoded = []
    for q in structure.questions:
        if not q.choices:
            continue
        by_code = {str(c.code): (c.texts.get(src) or str(c.code)) for c in q.choices}
        texts = set(by_code.values())
        columns = list(q.columns) + [i.column for i in q.items if i.column and q.kind == "matrix"]
        changed = False
        for col in columns:
            if col not in data.columns:
                continue
            values = data[col].dropna().astype(str).str.strip()
            values = values[values != ""]
            if len(values) and values.isin(by_code.keys()).all() and not values.isin(texts).all():
                data[col] = data[col].map(lambda v: by_code.get(str(v).strip(), v) if pd.notna(v) else v)
                changed = True
        if changed:
            q.recoded = True
            recoded.append(q.code)
    return data, recoded


def build_config(structure: SurveyStructure) -> Tuple[dict, dict]:
    """
    Returns ``(config, report)``; ``report`` has ``matched`` (question ->
    columns), ``skipped`` (question -> reason), ``missing`` (question -> parts
    without column) and ``unmatched_columns``.
    """
    languages = list(structure.languages or ["de"])
    src = languages[0]
    report = {"matched": {}, "skipped": dict(structure.skipped), "missing": {},
              "unmatched_columns": list(structure.unmatched_columns), "source_format": structure.source_format}
    metadata: Dict[str, Any] = {"survey_id": structure.survey_id, "source_language": src,
                                "languages": languages, "source_format": structure.source_format}
    if structure.title:
        metadata["survey_title"] = _texts(structure.title, languages)
    config: Dict[str, Any] = {"settings": {"survey_metadata": metadata, "report_groups": {}},
                              "question_groups": {}}
    groups = config["settings"]["report_groups"]
    for g in sorted(structure.groups, key=lambda g: g.order):
        groups[f"group_{g.id}"] = {"order": g.order, "label": _texts(g.texts, languages), "questions": []}

    for q in structure.questions:
        if q.kind in NOT_SUPPORTED:
            report["skipped"][q.code] = f"{q.source_type or q.kind}: {NOT_SUPPORTED[q.kind]}"
            continue
        if q.kind not in HANDLER_BY_KIND:
            report["skipped"][q.code] = f"{q.source_type or q.kind}: unknown kind '{q.kind}'"
            continue
        if q.missing:
            report["missing"][q.code] = list(q.missing)
        made = _questions_for(q, languages, src)
        if not made:
            report["missing"].setdefault(q.code, ["no columns found"])
            continue
        for qid, qcfg in made:
            config["question_groups"][qid] = qcfg
            if q.group and f"group_{q.group}" in groups:
                groups[f"group_{q.group}"]["questions"].append(qid)
        report["matched"][q.code] = [c for _, cfg in made for c in cfg["columns"]]
    return config, report


def _questions_for(q: StructQuestion, languages: List[str], src: str) -> List[Tuple[str, dict]]:
    qid = config_id(q.code)
    handler = HANDLER_BY_KIND[q.kind]
    label = _texts(q.texts, languages) or {src: q.code}
    base = {"label": label, "full_question": _texts(q.texts, languages),
            "source_metadata": {"format": q.source_type.split(":")[0] if ":" in q.source_type else "",
                                "code": q.code, "type": q.source_type}}
    if q.source_type.startswith("lss:"):    # kept for the LSS paths (config_validation, data_import)
        base["lss_metadata"] = {"title": q.code, "type": q.source_type[4:]}
    if q.help:
        base["help"] = _texts(q.help, languages)

    if q.kind == "numbers":   # one numeric question per field (the handler evaluates one column)
        out = []
        for item in q.items:
            if not item.column:
                continue
            sub = {lang: f"{label.get(lang, q.code)} – {t}" for lang, t in _texts(item.texts, languages).items()}
            sid = f"{qid}_{config_id(item.code)}"
            out.append((sid, {**base, "id": sid, "handler": handler, "type": handler,
                              "label": sub or label, "columns": [item.column]}))
        return out

    item_columns = [i for i in q.items if i.column]
    columns = list(q.columns) if q.kind in ("single", "numeric", "ranking", "date") or (q.kind == "text" and not item_columns) \
        else [i.column for i in item_columns]
    if q.kind == "numeric_matrix" and q.constant_sum is not None:
        base = {**base, "constant_sum": q.constant_sum}
    if not columns:
        return []
    cfg = {**base, "id": qid, "handler": handler, "type": handler, "columns": columns}
    if item_columns and q.kind in ("multi", "matrix", "text", "numeric_matrix"):
        cfg["column_labels"] = {i.column: (i.texts.get(src) or i.code) for i in item_columns}
    choices = [c.texts or {src: c.code} for c in q.choices] if (q.recoded or not q.code_only_choices) else []
    if q.kind == "single" and (q.ordinal or q.nps):
        cfg["ordinal"] = True
    if q.kind == "single" and q.nps:
        cfg["nps"] = True
    if q.other_of:
        cfg["other_of"] = config_id(q.other_of)
    if q.kind == "single" and choices:
        cfg["options"] = _lists(choices, languages, src)
    if q.kind == "ranking" and choices:
        cfg["item_labels"] = _lists(choices, languages, src)
    if q.kind == "multi" and q.positive_value:
        cfg["binary_values"] = {"positive": q.positive_value}
    if q.kind == "multi" and q.text_item_columns:
        cfg["text_item_columns"] = list(q.text_item_columns)
    if q.kind in ("multi", "text", "numeric_matrix") and item_columns:
        cfg["item_labels"] = _lists([i.texts or {src: i.code} for i in item_columns], languages, src)
    if q.kind == "matrix":
        cfg["row_labels"] = _lists([i.texts or {src: i.code} for i in item_columns], languages, src)
        if choices:
            cfg["scale"] = _lists(choices, languages, src)
    return [(qid, cfg)]
