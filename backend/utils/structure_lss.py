# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Adapter: LimeSurvey structure file (.lss, parsed by utils/lss_parser.py) and
the data columns -> utils/survey_structure.SurveyStructure.

Columns are found by question code (``G01Q01``, ``G01Q01[SQ001]``,
``G01Q01_SQ001``) or by question text (``Text``, ``Text [Item]``), the way
LimeSurvey exports them; each column is used once.
"""
from __future__ import annotations

import re
from typing import List, Optional

from utils.survey_structure import Choice, Item, StructGroup, StructQuestion, SurveyStructure, other_text_question

KIND_BY_TYPE = {
    "5": "single", "L": "single", "!": "single", "O": "single", "G": "single", "Y": "single", "I": "single",
    "M": "multi", "P": "multi",
    "S": "text", "T": "text", "U": "text", "Q": "text",
    "R": "ranking",
    "F": "matrix", "A": "matrix", "B": "matrix", "C": "matrix", "E": "matrix", "H": "matrix",
    "N": "numeric", "K": "numeric_matrix",   # K: several number fields (constant sum: equals_num_value)
    ":": "numeric_matrix",
    ";": "text",          # one free-text question per cell
    "D": "date",
    "1": "dual_scale",    # documented limit, reported
}
NO_DATA = {"X", "*", "|"}   # text display, equation, file upload
FIXED_SCALES = {"A": [str(i) for i in range(1, 6)], "B": [str(i) for i in range(1, 11)],
                "5": [str(i) for i in range(1, 6)]}
# Types whose answers LimeSurvey fixes itself; their codes in exports with answer
# codes (texts from locales/, used only when the data hold these codes)
FIXED_CODES = {"Y": [("Y", "answer_yes"), ("N", "answer_no")],
               "C": [("Y", "answer_yes"), ("N", "answer_no"), ("U", "uncertain")],
               "E": [("I", "increased"), ("S", "unchanged"), ("D", "decreased")],
               "G": [("F", "female"), ("M", "male")]}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip().lower()


class Columns:
    """Data columns found by code or (normalised) text; each column is used once."""

    def __init__(self, columns: List[str]):
        self.columns = list(columns)
        self.by_norm = {}
        for col in self.columns:
            self.by_norm.setdefault(_norm(col), col)
        self.used: set = set()

    def take(self, candidates: List[str]) -> Optional[str]:
        for cand in candidates:
            col = cand if cand in self.columns else self.by_norm.get(_norm(cand))
            if col and col not in self.used:
                self.used.add(col)
                return col
        return None

    def unused(self) -> List[str]:
        return [c for c in self.columns if c not in self.used]


def _sub_candidates(q, sq, qtext: str) -> List[str]:
    texts = [t for t in (sq.texts.question or {}).values() if t]
    return ([f"{q.title}[{sq.title}]", f"{q.title}_{sq.title}", f"{q.title}.{sq.title}"]
            + [f"{qtext} [{t}]" for t in texts])


def _other_columns(q, qtext: str, cols: "Columns", languages: List[str]) -> List[str]:
    """
    Text columns of "other" (``Q[other]``, ``Q_other``, "Text [Sonstiges]"),
    of the comment of a list with comment (``Q[comment]``) and of the comments
    per option of a multiple choice with comments (``Q[SQ001comment]``).
    """
    from utils.languages import catalog_texts
    found = []
    other_words = {str(v) for v in catalog_texts("common_label.other").values()} | {"Other", "other"}
    col = cols.take([f"{q.title}[other]", f"{q.title}_other", f"{q.title}[comment]", f"{q.title}_comment"]
                    + [f"{qtext} [{w}]" for w in other_words])
    if col:
        found.append(col)
    if q.type == "P":
        for sub in q.subquestions:
            c = cols.take([f"{q.title}[{sub.title}comment]", f"{q.title}_{sub.title}comment"])
            if c:
                found.append(c)
    return found


def lss_structure(survey_data, data_columns: List[str]) -> SurveyStructure:
    languages = list(survey_data.languages or ["de"])
    src = languages[0]
    cols = Columns(data_columns)
    structure = SurveyStructure("lss", survey_data.survey_id, languages, [],
                                groups=[StructGroup(gid, g.order, dict(g.names))
                                        for gid, g in survey_data.groups.items()])
    ordered = sorted(survey_data.questions.values(),
                     key=lambda q: (getattr(survey_data.groups.get(q.group_id), "order", 0), q.order))
    for q in ordered:
        if q.type in NO_DATA:
            continue
        kind = KIND_BY_TYPE.get(q.type)
        if kind is None:
            structure.skipped[q.title] = f"unknown LimeSurvey type '{q.type}'"
            continue
        qtext = q.texts.question.get(src) or q.title
        sq = StructQuestion(code=q.title, kind=kind, texts=dict(q.texts.question), help=dict(q.texts.help),
                            group=q.group_id, source_type=f"lss:{q.type}")
        sq.choices = [Choice(a.code, dict(a.texts)) for a in sorted(q.answers, key=lambda a: a.sort_order)]
        if not sq.choices and q.type in FIXED_SCALES:
            sq.choices = [Choice(v, {lang: v for lang in languages}) for v in FIXED_SCALES[q.type]]
            sq.ordinal = True
        if not sq.choices and q.type in FIXED_CODES:
            from utils.languages import text
            sq.choices = [Choice(code, {lang: text(f"common_label.{key}", lang) for lang in languages})
                          for code, key in FIXED_CODES[q.type]]
            sq.code_only_choices = True
        if kind == "single" and sq.choices and all(re.fullmatch(r"\d+", c.code) for c in sq.choices):
            codes = sorted(int(c.code) for c in sq.choices)
            if codes == list(range(codes[0], codes[-1] + 1)) and len(codes) >= 3:
                sq.ordinal = True                 # numbered points: an ordered scale
                if codes == list(range(0, 11)) and re.search(r"(?i)recommend|empfehl|recommand|raccomand", qtext):
                    sq.nps = True
        if kind in ("single", "numeric", "date") or (kind == "text" and q.type not in ("Q", ";")):
            col = cols.take([q.title, qtext])
            if col:
                sq.columns = [col]
            else:
                sq.missing.append("column not found")
        elif kind == "ranking":
            for rank in range(1, len(q.answers) + 1):
                col = cols.take([f"{q.title}[{rank}]", f"{q.title}_{rank}", f"{qtext} [{rank}]",
                                 f"{qtext} [Rang {rank}]", f"{qtext} [Rank {rank}]"])
                if col:
                    sq.columns.append(col)
        elif q.type == ";":
            # text per row: each row becomes a free-text question over its cells
            rows = [x for x in q.subquestions if x.scale_id == 0]
            cols_ = [x for x in q.subquestions if x.scale_id == 1]
            if not rows or not cols_:
                structure.skipped[q.title] = "text matrix without rows or columns in the LSS file"
                continue
            # text matrix: one free-text question per cell
            for r in rows:
                r_texts = dict(r.texts.question or {})
                for c in cols_:
                    c_texts = dict(c.texts.question or {})
                    cand = [f"{q.title}[{r.title}_{c.title}]", f"{q.title}_{r.title}_{c.title}"]
                    for lang in languages:
                        rt, ct = r_texts.get(lang), c_texts.get(lang)
                        if rt and ct:
                            cand += [f"{q.texts.question.get(lang, qtext)} [{rt}] [{ct}]",
                                     f"{q.texts.question.get(lang, qtext)} [{rt}][{ct}]"]
                    col = cols.take(cand)
                    cq = StructQuestion(
                        code=f"{q.title}_{r.title}_{c.title}", kind="text", group=q.group_id, source_type="lss:;",
                        texts={lang: f"{q.texts.question.get(lang, qtext)} – {r_texts.get(lang) or r.title} – "
                                     f"{c_texts.get(lang) or c.title}"
                               for lang in languages if q.texts.question.get(lang) or lang == src},
                        columns=[col] if col else [])
                    if not col:
                        cq.missing.append("column not found")
                    structure.questions.append(cq)
            continue
        elif q.type == ":":
            # number matrix: rows (scale 0) x columns (scale 1), one data column per cell
            rows = [x for x in q.subquestions if x.scale_id == 0]
            cols_ = [x for x in q.subquestions if x.scale_id == 1]
            for r in rows:
                for c in cols_:
                    r_texts, c_texts = dict(r.texts.question or {}), dict(c.texts.question or {})
                    cand = [f"{q.title}[{r.title}_{c.title}]", f"{q.title}_{r.title}_{c.title}"]
                    for lang in languages:
                        rt, ct = r_texts.get(lang), c_texts.get(lang)
                        if rt and ct:
                            cand += [f"{q.texts.question.get(lang, qtext)} [{rt}] [{ct}]",
                                     f"{q.texts.question.get(lang, qtext)} [{rt}][{ct}]"]
                    col = cols.take(cand)
                    if not col:
                        sq.missing.append(f"{r.title}_{c.title}")
                    texts = {lang: f"{r_texts.get(lang) or r.title} – {c_texts.get(lang) or c.title}"
                             for lang in languages}
                    sq.items.append(Item(f"{r.title}_{c.title}", texts, col))
        elif kind in ("multi", "matrix", "text", "numbers", "numeric_matrix"):
            for sub in q.subquestions:
                col = cols.take(_sub_candidates(q, sub, qtext))
                if not col:
                    sq.missing.append(sub.title)
                sq.items.append(Item(sub.title, dict(sub.texts.question or {}) or {src: sub.title}, col))
            if q.type == "K" and str(q.attributes.get("equals_num_value", "")).strip():
                try:
                    sq.constant_sum = float(str(q.attributes["equals_num_value"]).replace(",", "."))
                except ValueError:
                    pass   # an expression instead of a number: no sum check
        structure.questions.append(sq)
        # "other, please specify" and comment texts: one linked free-text question
        other_cols = _other_columns(q, qtext, cols, languages)
        if other_cols:
            from utils.languages import catalog_texts
            label = {lang: str(v) for lang, v in catalog_texts("common_label.other").items()}
            if q.type == "M" and other_cols[0] not in [i.column for i in sq.items]:
                # LimeSurvey has no "other" checkbox: the option counts as chosen when its text is filled
                sq.items.append(Item("other", {lang: label.get(lang) or label.get("en", "Other") for lang in languages},
                                     other_cols[0]))
                sq.text_item_columns.append(other_cols[0])
            if q.type in ("O", "P"):
                label = {lang: str(v) for lang, v in catalog_texts("word.comments").items()}
            structure.questions.append(other_text_question(sq, other_cols, languages, label))
    structure.unmatched_columns = cols.unused()
    return structure
