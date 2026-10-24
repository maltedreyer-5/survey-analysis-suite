# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Analysis variables: what exactly a question contributes to a statistical test.

Correlations and segment tests do not work on questions but on variables
derived from them. Each variable has a clear meaning and a clear base (the
respondents it is defined for), so every statement built on it can say what
was measured:

=====================  ==========================================  ========
question type          variable(s)                                 level
=====================  ==========================================  ========
single_choice          the chosen option                           nominal
                       (ordinal if the question sets ordinal: true)
multi_choice           per option: chosen / not chosen             binary
multi_choice_binary    per item: yes / no                          binary
matrix_likert          per item: the rating                        ordinal
                       plus the mean over all items, only if the
                       question sets scale_index: true             numeric
ranking                per item: the rank given (1 = first)        ordinal
freetext               per cluster: mentioned / not mentioned      binary
numeric                the value (per column)                      numeric
=====================  ==========================================  ========

Base: respondents who answered the question (for choice questions at least
one selection, as in the handlers' percentages). Respondents who did not
answer are excluded (NaN), never counted as "none chosen"; otherwise filter
questions produce spurious associations. Evasive answers ("no answer",
"don't know") are excluded as well: the global list
``settings.analysis.missing_values`` (defaults below), replaced per question
by ``missing_values``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

import numpy as np
import pandas as pd

from utils.languages import get_source_language, localized

DEFAULT_MISSING_VALUES = [
    "keine Angabe", "k. A.", "k.A.", "weiß nicht", "weiss nicht", "kann ich nicht beurteilen",
    "no answer", "n/a", "don't know", "do not know", "prefer not to say", "not applicable",
    "pas de réponse", "je ne sais pas", "nessuna risposta", "non so",
]

LEVELS = ("nominal", "binary", "ordinal", "numeric")


@dataclass
class AnalysisVariable:
    """One variable derived from a question; ``values`` is NaN outside its base."""
    id: str
    question_id: str
    level: str
    measure: str                       # what the value means, see MEASURES
    question_label: Dict[str, str]
    item_label: Optional[Dict[str, str]]
    values: pd.Series
    # Ordered categories (value -> multilingual label); None for numeric
    categories: Optional[Dict[Any, Dict[str, str]]] = None
    excluded: Dict[str, int] = field(default_factory=dict)   # e.g. {"missing_values": 4}

    @property
    def n(self) -> int:
        return int(self.values.notna().sum())

    def label(self, lang: str, source_lang: str) -> str:
        q = str(localized(self.question_label, lang, source_lang) or self.question_id)
        if self.item_label:
            return f"{q}: {localized(self.item_label, lang, source_lang)}"
        return q

    def category_label(self, value: Any, lang: str, source_lang: str) -> str:
        if self.categories and value in self.categories:
            return str(localized(self.categories[value], lang, source_lang) or value)
        return str(value)

    def describe(self) -> dict:
        """Definition for exports and the method record (no data)."""
        return {"id": self.id, "question_id": self.question_id, "level": self.level,
                "measure": self.measure, "item_label": self.item_label, "n": self.n,
                "excluded": dict(self.excluded)}


# What a value means; the texts for reports come from locales/ (analysis.measure.*)
MEASURES = ("choice", "option_chosen", "item_yes", "item_rating", "scale_index",
            "rank_position", "cluster_mentioned", "value")


def missing_values_for(config: Optional[dict], qconfig: dict) -> Set[str]:
    """Evasive answers excluded from tests, compared case-insensitively."""
    values = qconfig.get("missing_values")
    if values is None:
        values = (((config or {}).get("settings") or {}).get("analysis") or {}).get("missing_values")
    if values is None:
        values = DEFAULT_MISSING_VALUES
    return {str(v).strip().casefold() for v in values}


from utils.answer_values import answer_text  # noqa: E402  (one text form for answer values)


def _is_missing(value: Any, missing: Set[str]) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return True
    text = str(value).strip()
    return text == "" or text.casefold() in missing


def _labels_by_index(qconfig: dict, key: str, index: int) -> Optional[Dict[str, str]]:
    labels = qconfig.get(key)
    if isinstance(labels, dict):
        out = {lang: vals[index] for lang, vals in labels.items()
               if isinstance(vals, list) and index < len(vals) and vals[index]}
        return out or None
    if isinstance(labels, list) and index < len(labels):
        return {"": labels[index]} if labels[index] else None
    return None


def _item_label(qconfig: dict, column: str, index: int, source_lang: str) -> Dict[str, str]:
    for key in ("item_labels", "row_labels"):
        found = _labels_by_index(qconfig, key, index)
        if found:
            return {source_lang if k == "" else k: v for k, v in found.items()}
    if "[" in column and "]" in column:
        return {source_lang: column[column.index("[") + 1:column.rindex("]")].strip()}
    return {source_lang: column}


def _binary_categories(source_lang: str) -> Dict[Any, Dict[str, str]]:
    from utils.languages import available_catalogs, text
    return {value: {lang: text(f"analysis.binary.{key}", lang) for lang in available_catalogs()}
            for value, key in ((0.0, "no"), (1.0, "yes"))}


def build_variables(
    config: dict,
    df: pd.DataFrame,
    question_ids: Optional[Iterable[str]] = None,
    cluster_memberships: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[AnalysisVariable]]:
    """
    Variables for every supported question (or for ``question_ids``).

    ``cluster_memberships``: for freetext questions,
    ``{qid: {"clusters": [{"label": {lang: name}, "respondents": set(row index)}],
    "answered": set(row index)}}``; freetext questions without it get no variables.
    """
    src = get_source_language(config)
    groups = config.get("question_groups") or {}
    out: Dict[str, List[AnalysisVariable]] = {}
    for qid in (question_ids or groups.keys()):
        qconfig = groups.get(qid) or {}
        built = _build_for_question(qid, qconfig, config, df, src, (cluster_memberships or {}).get(qid))
        if built:
            out[qid] = built
    return out


def _build_for_question(qid, qconfig, config, df, src, clusters) -> List[AnalysisVariable]:
    handler = qconfig.get("handler", "single_choice")
    columns = [c for c in (qconfig.get("columns") or []) if c in df.columns]
    label = qconfig.get("label") or {src: qid}
    if not isinstance(label, dict):
        label = {src: str(label)}
    missing = missing_values_for(config, qconfig)

    if handler == "single_choice" and columns:
        raw = df[columns[0]]
        miss = raw.map(lambda v: _is_missing(v, missing))
        excluded = int((miss & raw.notna() & (raw.astype(str).str.strip() != "")).sum())
        # answer_text: codes read as numbers (10, or 10.0 in a column with gaps)
        # are the texts of the configured options ("10")
        values = raw.where(~miss).map(lambda v: str(answer_text(v)).strip() if pd.notna(v) else v)
        options = qconfig.get("options") or {}
        src_options = options.get(src) if isinstance(options, dict) else options
        categories = {}
        if isinstance(src_options, list):
            for i, opt in enumerate(src_options):
                categories[str(opt).strip()] = _labels_by_index({"o": options}, "o", i) \
                    if isinstance(options, dict) else {src: opt}
        for v in values.dropna().unique():
            categories.setdefault(v, {src: v})
        level = "ordinal" if qconfig.get("ordinal") else "nominal"
        if level == "ordinal" and isinstance(src_options, list):
            rank = {str(o).strip(): i + 1 for i, o in enumerate(src_options)}
            values = values.map(lambda v: rank.get(v, np.nan) if pd.notna(v) else np.nan)
            categories = {rank[k]: lab for k, lab in categories.items() if k in rank}
        return [AnalysisVariable(qid, qid, level, "choice", label, None, values, categories,
                                 {"missing_values": excluded} if excluded else {})]

    if handler in ("multi_choice", "multi_choice_binary") and columns:
        filled = df[columns].apply(lambda s: s.map(lambda v: not _is_missing(v, set())))
        if handler == "multi_choice_binary":
            # yes/no per item; base as in the handler: at least one item answered (yes or no)
            from utils.binary_values import chosen_mask
            chosen = pd.DataFrame({col: chosen_mask(df, col, qconfig) for col in columns}, index=df.index)
            answered = filled.any(axis=1)
            measure = "item_yes"
        else:
            # checkbox per option; base as in the handler: at least one selection
            chosen = filled
            answered = filled.any(axis=1)
            measure = "option_chosen"
        cats = _binary_categories(src)
        return [AnalysisVariable(f"{qid}::{i}", qid, "binary", measure, label,
                                 _item_label(qconfig, col, i, src),
                                 chosen[col].astype(float).where(answered), cats)
                for i, col in enumerate(columns)]

    if handler == "matrix_likert" and columns:
        scale = qconfig.get("scale") or {}
        scale_src = scale.get(src) if isinstance(scale, dict) else scale
        mapping: Dict[str, int] = {}
        if isinstance(scale, dict):
            for vals in scale.values():
                for i, v in enumerate(vals or []):
                    mapping.setdefault(str(v).strip(), i + 1)
        elif isinstance(scale, list):
            mapping = {str(v).strip(): i + 1 for i, v in enumerate(scale)}
        categories = None
        if isinstance(scale_src, list):
            categories = {float(i + 1): (_labels_by_index({"s": scale}, "s", i)
                                         if isinstance(scale, dict) else {src: v})
                          for i, v in enumerate(scale_src)}
        variables = []
        numeric_items = []
        for i, col in enumerate(columns):
            raw = df[col]
            miss = raw.map(lambda v: _is_missing(v, missing))

            def to_number(v):
                if pd.isna(v):
                    return np.nan
                key = str(v).strip()
                if key in mapping:
                    return float(mapping[key])
                try:
                    return float(key)
                except ValueError:
                    return np.nan
            values = raw.where(~miss).map(to_number)
            numeric_items.append(values)
            variables.append(AnalysisVariable(f"{qid}::{i}", qid, "ordinal", "item_rating", label,
                                              _item_label(qconfig, col, i, src), values, categories))
        if qconfig.get("scale_index"):
            mean = pd.concat(numeric_items, axis=1).mean(axis=1)
            variables.append(AnalysisVariable(f"{qid}::index", qid, "numeric", "scale_index",
                                              label, None, mean, None))
        return variables

    if handler == "ranking" and columns:
        # Data format (LimeSurvey): column = rank position 1..k, value = the item.
        # Per item its rank; respondents who ranked something but not this
        # item get k + 1 ("not ranked"), the bottom of their order.
        k = len(columns)
        cells = df[columns].apply(lambda s: s.map(lambda v: None if _is_missing(v, set()) else str(v).strip()))
        answered = cells.notna().any(axis=1)
        labels_cfg = qconfig.get("item_labels") or {}
        src_items = labels_cfg.get(src) if isinstance(labels_cfg, dict) else None
        items = list(src_items or [])
        for col in columns:
            for v in cells[col].dropna().unique():
                if v not in items:
                    items.append(v)
        from utils.languages import available_catalogs, text
        rank_labels = {float(r): {"": str(r)} for r in range(1, k + 1)}
        rank_labels[float(k + 1)] = {lang: text("analysis.not_ranked", lang) for lang in available_catalogs()}
        variables = []
        for item in items:
            rank = pd.Series(np.nan, index=df.index)
            for pos, col in enumerate(columns, start=1):
                rank[cells[col] == item] = float(pos)
            rank = rank.where(~answered | rank.notna(), float(k + 1)).where(answered)
            if rank.notna().sum() == 0:
                continue
            item_label = ({lang: vals[items.index(item)] for lang, vals in labels_cfg.items()
                           if isinstance(vals, list) and src_items and item in src_items
                           and src_items.index(item) < len(vals)}
                          if isinstance(labels_cfg, dict) and src_items and item in src_items else {src: item})
            variables.append(AnalysisVariable(f"{qid}::{items.index(item)}", qid, "ordinal", "rank_position",
                                              label, item_label, rank, rank_labels))
        return variables

    if handler == "date" and columns:
        # a date as a number (days since 1970-01-01): earlier/later between groups, associations
        from handlers.date import parse_dates
        days = parse_dates(df[columns[0]]).map(lambda ts: ts.value / 86_400_000_000_000 if pd.notna(ts) else np.nan)
        return [AnalysisVariable(qid, qid, "numeric", "date", label, None, days.astype(float), None)]
    if handler in ("numeric", "numeric_matrix") and columns:
        return [AnalysisVariable(f"{qid}::{i}" if len(columns) > 1 else qid, qid, "numeric", "value",
                                 label, _item_label(qconfig, col, i, src) if len(columns) > 1 else None,
                                 pd.to_numeric(df[col], errors="coerce"), None)
                for i, col in enumerate(columns)]

    if handler == "freetext" and clusters:
        answered = pd.Series(df.index.isin(list(clusters.get("answered") or [])), index=df.index)
        cats = _binary_categories(src)
        variables = []
        for i, cluster in enumerate(clusters.get("clusters") or []):
            members = pd.Series(df.index.isin(list(cluster["respondents"])), index=df.index)
            variables.append(AnalysisVariable(f"{qid}::c{i}", qid, "binary", "cluster_mentioned", label,
                                              cluster["label"], members.astype(float).where(answered), cats))
        return variables
    return []


def cluster_memberships_from_results(analysis_results: dict, config: dict) -> Dict[str, Dict[str, Any]]:
    """Respondents per cluster for every clustered freetext question (all memberships, not the first)."""
    out = {}
    groups = config.get("question_groups") or {}
    for qid, result in (analysis_results or {}).items():
        if (groups.get(qid) or {}).get("handler") != "freetext":
            continue
        clustering = getattr(result, "clustering_result", None)
        clusters = getattr(clustering, "clusters", None) or []
        entries, answered = [], set()
        for cluster in clusters:
            respondents = set(getattr(cluster, "response_ids", None) or [])
            if not respondents:
                continue
            answered |= respondents
            name = cluster.name if isinstance(cluster.name, dict) else {get_source_language(config): str(cluster.name)}
            entries.append({"label": name, "respondents": respondents})
        if len(entries) >= 2:
            out[qid] = {"clusters": entries, "answered": answered}
    return out
