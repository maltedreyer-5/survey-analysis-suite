# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Adapter: Qualtrics survey file (.qsf, JSON) and Qualtrics CSV export ->
data frame and utils/survey_structure.SurveyStructure (decision 3.6.12).

The QSF holds the questions as ``SurveyElements`` with ``Element == "SQ"``;
their ``Payload`` gives ``QuestionID``, ``DataExportTag``, ``QuestionType``,
``Selector``/``SubSelector``, ``QuestionText``, ``Choices``/``ChoiceOrder``,
``Answers``/``AnswerOrder`` (matrix scale), ``RecodeValues`` and translations
under ``Language``. The QSF format is not officially documented and changes
now and then; unknown types are reported, never guessed.

The CSV export starts with three header rows: column name, question text,
``{"ImportId":"QID5_1"}``. The ImportId ties every column to its question
and choice, so columns are found without comparing texts. Exports with
answer codes are mapped to the choice texts (``RecodeValues``).

Prepared in the data, because the handlers expect it:
- multiple choice exported in one column (``A,B``): one yes/no column per
  choice (``<column>__<choice id>``);
- rank order (one column per choice holding its rank): one column per rank
  holding the choice text (``<tag>__rank<k>``).
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from utils.survey_structure import Choice, Item, StructGroup, StructQuestion, SurveyStructure, other_text_question

SINGLE_SELECTORS = {"SAVR", "SAHR", "SACOL", "DL", "SB", "NPS"}
MULTI_SELECTORS = {"MAVR", "MAHR", "MACOL", "MSB"}
NO_DATA_TYPES = {"DB", "Timing", "Meta", "Captcha"}
# Types whose export layout is not documented well enough to read them reliably;
# reported until a real export shows the layout
UNKNOWN_STRUCTURE = {
    "PGR": "Pick, Group & Rank: export layout not reliably readable without an example",
    "HeatMap": "Heat map: click coordinates, no answer values",
    "HotSpot": "Hot spot: areas not reliably readable without an example",
    "DD": "Drill-down: export layout not reliably readable without an example",
    "FileUpload": "File upload: no answers to analyze",
    "Draw": "Signature/drawing: no answers to analyze",
}


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).replace("&nbsp;", " ").strip()


def _lang(code: str) -> str:
    return str(code or "").split("-")[0].split("_")[0].lower()


def read_qsf(path_or_text) -> dict:
    if isinstance(path_or_text, dict):
        return path_or_text
    text = path_or_text
    if not str(text).lstrip().startswith("{"):
        with open(path_or_text, encoding="utf-8-sig") as f:
            text = f.read()
    return json.loads(text)


def read_qualtrics_csv(path: str) -> Tuple[pd.DataFrame, Dict[str, str], Dict[str, str]]:
    """
    Data of a Qualtrics CSV export without its two extra header rows.
    Returns ``(data, import_ids, question_texts)`` keyed by column name;
    without an ImportId row both dicts are empty and nothing is dropped.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return pd.DataFrame(), {}, {}
    header = rows[0]
    import_ids, texts, skip = {}, {}, 0
    if len(rows) > 2 and any('"ImportId"' in c or "ImportId" in c for c in rows[2]):
        skip = 2
        texts = dict(zip(header, rows[1]))
        for col, cell in zip(header, rows[2]):
            try:
                import_ids[col] = json.loads(cell).get("ImportId", "")
            except (ValueError, AttributeError):
                pass
    data = pd.read_csv(io.StringIO("\n".join(",".join(_q(c) for c in r) for r in [header] + rows[1 + skip:])),
                       dtype=str, keep_default_na=True)
    return data, import_ids, texts


def _q(cell: str) -> str:
    return '"' + str(cell).replace('"', '""') + '"'


def _choices(payload: dict, key: str, order_key: str) -> List[Tuple[str, Dict[str, str]]]:
    raw = payload.get(key) or {}
    order = [str(x) for x in (payload.get(order_key) or list(raw.keys()))]
    return [(cid, {"_": _strip((raw.get(cid) or {}).get("Display", ""))}) for cid in order if cid in raw]


def qualtrics_structure(qsf, data: pd.DataFrame, import_ids: Optional[Dict[str, str]] = None
                        ) -> Tuple[pd.DataFrame, SurveyStructure]:
    qsf = read_qsf(qsf)
    entry = qsf.get("SurveyEntry") or {}
    src = _lang(entry.get("SurveyLanguage") or "en") or "en"
    elements = qsf.get("SurveyElements") or []
    questions = {e["Payload"]["QuestionID"]: e["Payload"] for e in elements
                 if e.get("Element") == "SQ" and isinstance(e.get("Payload"), dict) and e["Payload"].get("QuestionID")}
    languages = [src] + sorted({_lang(k) for p in questions.values() for k in (p.get("Language") or {})} - {src})
    data = data.copy()
    import_ids = dict(import_ids or {})
    by_import = {}
    for col, iid in import_ids.items():
        by_import.setdefault(iid, col)
    used: set = set()

    def col_for(qid: str, tag: str, suffix: Optional[str] = None) -> Optional[str]:
        key = qid if suffix is None else f"{qid}_{suffix}"
        col = by_import.get(key)
        if col is None:   # without ImportId row: column names from the DataExportTag
            name = tag if suffix is None else f"{tag}_{suffix}"
            col = name if name in data.columns else None
        if col and col not in used:
            used.add(col)
            return col
        return None

    structure = SurveyStructure("qualtrics", entry.get("SurveyID", "qualtrics"), languages, [],
                                title={src: entry.get("SurveyName", "")} if entry.get("SurveyName") else {})
    order, groups = _block_order(elements, src)
    structure.groups = groups
    pending = [(qid, questions[qid]) for qid in sorted(questions, key=lambda q: order.get(q, (10 ** 6, 0)))]
    idx = 0
    while idx < len(pending):
        qid, p = pending[idx]
        idx += 1
        qtype, sel, sub = p.get("QuestionType"), p.get("Selector"), p.get("SubSelector")
        tag = p.get("DataExportTag") or qid
        if qtype in NO_DATA_TYPES:
            continue
        if qtype == "SBS":
            # Side by side: each column is a question of its own (AdditionalQuestions),
            # exported as <tag>#<k>_<row>; inserted right after, in their order
            subs = []
            items = (p.get("AdditionalQuestions") or {}).items()
            for k, sp in sorted(items, key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
                sp = dict(sp)
                sp.setdefault("QuestionID", f"{qid}#{k}")
                sp.setdefault("DataExportTag", f"{tag}#{k}")
                head = _strip(p.get("QuestionText"))
                sp["QuestionText"] = f"{head} – {_strip(sp.get('QuestionText'))}" if head else sp.get("QuestionText", "")
                if qid in order:
                    order[sp["QuestionID"]] = order[qid]
                subs.append((sp["QuestionID"], sp))
            pending[idx:idx] = subs
            continue
        texts = {src: _strip(p.get("QuestionText"))}
        trans = {_lang(k): v for k, v in (p.get("Language") or {}).items()}
        for lang, t in trans.items():
            if t.get("QuestionText"):
                texts.setdefault(lang, _strip(t["QuestionText"]))
        choices = _localised(_choices(p, "Choices", "ChoiceOrder"), trans, "Choices", src)
        answers = _localised(_choices(p, "Answers", "AnswerOrder"), trans, "Answers", src)
        source_type = f"qualtrics:{qtype}/{sel}" + (f"/{sub}" if sub else "")
        q = StructQuestion(code=tag, kind="", texts=texts, group=order.get(qid, (None, None))[1] if qid in order else None,
                           source_type=source_type)
        recode = {str(k): str(v) for k, v in (p.get("RecodeValues") or {}).items()}

        if qtype == "MC" and sel in SINGLE_SELECTORS:
            q.kind = "single"
            q.nps = sel == "NPS"      # promoters/passives/detractors and score
            q.choices = [Choice(cid, t) for cid, t in choices]
            col = col_for(qid, tag)
            if col:
                q.columns = [col]
                _decode(data, col, choices, recode, src)
        elif qtype == "MC" and sel in MULTI_SELECTORS:
            q.kind = "multi"
            q.positive_value = "1"     # prepared yes/no columns below
            for cid, t in choices:
                col = col_for(qid, tag, cid)
                q.items.append(Item(cid, t, col))
            if any(i.column for i in q.items):
                _prepare_multi(data, q.items)
            else:
                col = col_for(qid, tag)
                if col:   # one column with the chosen texts, comma-separated -> one column per choice
                    for item in q.items:
                        new = f"{col}__{item.code}"
                        data[new] = _contains_choice(data[col], item.texts.get(src, ""), item.code, recode)
                        item.column = new
        elif qtype == "TE" and sel == "FORM":
            q.kind = "text"
            q.items = [Item(cid, t, col_for(qid, tag, cid)) for cid, t in choices]
        elif qtype == "TE" and str(((p.get("Validation") or {}).get("Settings") or {}).get("ContentType", "")
                                   ).lower() in ("validdate", "date"):
            # text entry validated as a date (place of the setting to be confirmed with a real QSF)
            q.kind = "date"
            col = col_for(qid, tag) or col_for(qid, tag, "TEXT")
            q.columns = [col] if col else []
        elif qtype == "TE":
            q.kind = "text"
            col = col_for(qid, tag) or col_for(qid, tag, "TEXT")
            q.columns = [col] if col else []
        elif qtype == "Matrix" and sub == "MultipleAnswer":
            # several answers per row: one multiple choice per row (columns <tag>_<row>_<answer>)
            for cid, row_texts in choices:
                mq = StructQuestion(code=f"{tag}_{cid}", kind="multi",
                                    texts={lang: f"{t} – {row_texts.get(lang) or row_texts.get(src, cid)}"
                                           for lang, t in texts.items()},
                                    group=q.group, source_type=source_type, positive_value="1")
                mq.items = [Item(aid, at, col_for(qid, tag, f"{cid}_{aid}")) for aid, at in answers]
                _prepare_multi(data, mq.items)
                mq.missing = [i.code for i in mq.items if not i.column]
                structure.questions.append(mq)
            continue
        elif qtype == "Matrix" and sel == "TE":
            # text matrix: one free-text question per cell (<tag>_<row>_<column>)
            for cid, row_texts in choices:
                for aid, at in (answers or [(None, {})]):
                    suffix = f"{cid}_{aid}" if aid is not None else cid
                    cq = StructQuestion(
                        code=f"{tag}_{suffix}", kind="text", group=q.group, source_type=source_type,
                        texts={lang: " – ".join(x for x in (t, row_texts.get(lang) or row_texts.get(src, cid),
                                                           at.get(lang) or at.get(src, "")) if x)
                               for lang, t in texts.items()})
                    col = col_for(qid, tag, suffix)
                    cq.columns = [col] if col else []
                    structure.questions.append(cq)
            continue
        elif qtype == "Matrix" and sel == "CS":
            # constant sum per row: one field per row x column (<tag>_<row>_<column>)
            q.kind = "numeric_matrix"
            for cid, rt in choices:
                for aid, at in answers:
                    texts_ = {lang: f"{rt.get(lang) or rt.get(src, cid)} – {at.get(lang) or at.get(src, aid)}"
                              for lang in languages}
                    q.items.append(Item(f"{cid}_{aid}", texts_, col_for(qid, tag, f"{cid}_{aid}")))
        elif qtype == "Matrix" and sub in ("SingleAnswer", "DL", None) and sel not in ("MaxDiff",):
            q.kind = "matrix"
            q.choices = [Choice(aid, t) for aid, t in answers]
            for cid, t in choices:
                col = col_for(qid, tag, cid)
                if col:
                    _decode(data, col, answers, recode, src)
                q.items.append(Item(cid, t, col))
        elif qtype == "RO":
            q.kind = "ranking"
            q.choices = [Choice(cid, t) for cid, t in choices]
            rank_cols = {cid: col_for(qid, tag, cid) for cid, _ in choices}
            if any(rank_cols.values()):
                for k in range(1, len(choices) + 1):
                    new = f"{tag}__rank{k}"
                    data[new] = _item_at_rank(data, rank_cols, dict(choices), k, src)
                    q.columns.append(new)
        elif qtype in ("Slider", "CS"):
            # sliders and constant sums: one number per choice in one question
            q.kind = "numeric_matrix"
            q.items = [Item(cid, t, col_for(qid, tag, cid)) for cid, t in choices]
            if qtype == "CS":
                q.constant_sum = _constant_sum_total(p)
        else:
            reason = UNKNOWN_STRUCTURE.get(qtype, "not supported")
            structure.skipped[tag] = f"Qualtrics type {source_type}: {reason}"
            continue
        q.missing = [i.code for i in q.items if not i.column]
        structure.questions.append(q)
        # text entry of a choice ("other, please specify": <QID>_<choice>_TEXT)
        if q.kind in ("single", "multi", "ranking"):
            other_cols, label = [], {}
            for cid, t in choices:
                col = col_for(qid, tag, f"{cid}_TEXT")
                if col:
                    other_cols.append(col)
                    label = label or t
            if other_cols:
                structure.questions.append(other_text_question(q, other_cols, languages, label))
    structure.unmatched_columns = [c for c in data.columns if c not in used and "__" not in c]
    # Loop & Merge: the questions of a loop are exported once per pass as <n>_<tag>
    tags = {(p.get("DataExportTag") or qid) for qid, p in questions.items()}
    looped: Dict[str, List[str]] = {}
    for col in structure.unmatched_columns:
        m = re.match(r"^(\d+)_(.+?)(?:_\w+)?$", col)
        if m and (m.group(2) in tags or col[len(m.group(1)) + 1:].split("_")[0] in tags):
            looped.setdefault(col[len(m.group(1)) + 1:].split("_")[0], []).append(col)
    for tag, cols in looped.items():
        structure.skipped[f"loop:{tag}"] = (f"Loop & Merge: {len(cols)} columns ({', '.join(cols[:4])}"
                                            f"{' …' if len(cols) > 4 else ''}): repetitions per loop "
                                            f"are not analyzed yet")
    return data, structure


def _prepare_multi(data: pd.DataFrame, items: List[Item]) -> None:
    """
    One column per option holding its text (or 1) when chosen -> "1"/"0" in a
    new column ``<column>__sel``; respondents who chose none of the options
    stay empty (they did not answer the question).
    """
    cols = [i.column for i in items if i.column]
    if not cols:
        return
    answered = data[cols].apply(
        lambda r: any(str(v).strip() not in ("", "0", "nan") for v in r if pd.notna(v)), axis=1)
    for item in items:
        if item.column:
            new = f"{item.column}__sel"
            data[new] = [("1" if pd.notna(v) and str(v).strip() not in ("", "0") else "0") if a else None
                         for v, a in zip(data[item.column], answered)]
            item.column = new


def split_qualtrics_header(data: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, str], Dict[str, str]]:
    """
    A Qualtrics export read with its first row as header (e.g. from Excel):
    if the second data row holds ImportIds, the first two data rows are
    header rows. Returns ``(data, import_ids, question_texts)``.
    """
    if len(data) < 2 or not any("ImportId" in str(v) for v in data.iloc[1].tolist()):
        return data, {}, {}
    texts = {c: str(v) for c, v in zip(data.columns, data.iloc[0].tolist())}
    import_ids = {}
    for col, cell in zip(data.columns, data.iloc[1].tolist()):
        try:
            import_ids[col] = json.loads(str(cell)).get("ImportId", "")
        except (ValueError, AttributeError):
            pass
    return data.iloc[2:].reset_index(drop=True), import_ids, texts


def _constant_sum_total(payload: dict) -> Optional[float]:
    """
    The total of a constant sum, if the QSF states it. Known places (to be
    confirmed with a real export): ``ChoiceTotal``, ``Validation.Settings``
    with ``Total``/``EqualTo``/``MustTotal``. None: no sum check (it can be
    configured as ``constant_sum``).
    """
    settings = ((payload.get("Validation") or {}).get("Settings") or {})
    for value in (payload.get("ChoiceTotal"), settings.get("Total"), settings.get("EqualTo"),
                  settings.get("MustTotal")):
        try:
            if value not in (None, "", True, False):
                return float(str(value).replace(",", "."))
        except ValueError:
            continue
    return None


def _localised(entries, trans: dict, key: str, src: str):
    """[(id, {"_": text})] -> [(id, {lang: text})] with the translations of the QSF."""
    out = []
    for cid, t in entries:
        texts = {src: t["_"]}
        for lang, tr in trans.items():
            disp = ((tr.get(key) or {}).get(cid) or {}).get("Display")
            if disp:
                texts.setdefault(lang, _strip(disp))
        out.append((cid, texts))
    return out


def _decode(data: pd.DataFrame, col: str, entries, recode: Dict[str, str], src: str) -> None:
    """Exports with answer codes: code (recode value or choice id) -> choice text."""
    texts = {t.get(src, cid) for cid, t in entries}
    by_code = {str(cid): t.get(src, cid) for cid, t in entries}
    # recode values take precedence over choice ids (both can be "10")
    by_code.update({recode[cid]: t.get(src, cid) for cid, t in entries if cid in recode})
    values = data[col].dropna().astype(str).str.strip()
    if len(values) and values.isin(texts).all():
        return   # already the answer texts (e.g. NPS 0-10, scales with numbers as labels)
    if len(values) and values.isin(by_code.keys()).all():
        data[col] = data[col].map(lambda v: by_code.get(str(v).strip(), v) if pd.notna(v) else v)


def _contains_choice(series: pd.Series, text: str, cid: str, recode: Dict[str, str]) -> pd.Series:
    tokens = {text, cid, recode.get(cid, cid)}

    def chosen(v):
        if pd.isna(v) or str(v).strip() == "":
            return None
        value = f",{v},"
        return "1" if any(t and f",{t}," in value for t in tokens) else "0"
    return series.map(chosen)


def _item_at_rank(data: pd.DataFrame, rank_cols: Dict[str, Optional[str]], texts: Dict[str, Dict], k: int,
                  src: str) -> pd.Series:
    def item(row):
        for cid, col in rank_cols.items():
            if col and str(row.get(col, "")).strip() in (str(k), f"{k}.0"):
                return texts[cid].get(src, cid)
        return None
    return data.apply(item, axis=1)


def _block_order(elements, src: str) -> Tuple[Dict[str, Tuple[int, str]], List[StructGroup]]:
    """QuestionID -> (position, block id) from the survey blocks; the blocks as groups."""
    order, groups, pos = {}, [], 0
    for e in elements:
        if e.get("Element") != "BL":
            continue
        payload = e.get("Payload") or {}
        blocks = payload.values() if isinstance(payload, dict) else payload
        for b_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            bid = str(block.get("ID") or b_index)
            groups.append(StructGroup(bid, b_index, {src: block.get("Description", "")}))
            for be in block.get("BlockElements") or []:
                if be.get("Type") == "Question" and be.get("QuestionID"):
                    order[be["QuestionID"]] = (pos, bid)
                    pos += 1
    return order, groups
