# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Handler for several numbers of one question: a number matrix
(LimeSurvey ``:``, Qualtrics matrix with constant sum), several number fields
(LimeSurvey ``K``) and constant sums (Qualtrics ``CS``).

Per field (column) the statistics of utils/numeric_coerce plus NumericHandler:
n, mean, median, standard deviation, quartiles, minimum, maximum. With
``constant_sum: <total>`` in the configuration the sums per respondent are
checked (share of respondents whose fields add up to the total, with a
tolerance of 0.5 % of the total) and the mean share of each field in the
total is reported.

Configuration::

    handler: numeric_matrix
    columns: [Q5_1, Q5_2, Q5_3]
    item_labels: {de: [...], en: [...]}   # optional, by position
    constant_sum: 100                      # optional
    numeric: {unit: "%", decimals: 1}      # optional, as for numeric
"""
from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from handlers.base import AnalysisResult, MultiColumnHandler
from handlers.numeric import NumericHandler
from utils.numeric_coerce import coerce_series

logger = logging.getLogger(__name__)


class NumericMatrixHandler(MultiColumnHandler):
    """Several numbers per respondent: statistics per field, optional constant-sum check."""
    per_column_labels = "item_labels"   # one label per column, by position (extract_item_name)

    answers_as_text = False  # computes with the values (numbers, dates)
    handler_type = "numeric_matrix"
    supported_charts = ["bar_horizontal"]

    def _field_handler(self, column: str) -> NumericHandler:
        return NumericHandler({**self.config, "id": f"{self.question_id}:{column}", "columns": [column],
                               "handler": "numeric"})

    def item_label(self, column: str, lang: str) -> str:
        labels = self.config.get("item_labels") or {}
        if isinstance(labels, dict):
            src = self.source_language
            src_list = labels.get(src) or []
            target = labels.get(lang) or src_list
            if column in self.columns:
                i = self.columns.index(column)
                if i < len(target) and target[i]:
                    return target[i]
                if i < len(src_list) and src_list[i]:
                    return src_list[i]
        return self.extract_item_name(column)

    def analyze(self, df: pd.DataFrame, lang: str = "de", **kwargs) -> AnalysisResult:
        existing = [c for c in self.columns if c in df.columns]
        missing = [c for c in self.columns if c not in df.columns]
        if not existing:
            return AnalysisResult(question_id=self.question_id, n=0, data={},
                                  warnings=[f"No columns found: {missing}"])
        values = pd.DataFrame({c: coerce_series(df[c]) for c in existing})
        answered = values.notna().any(axis=1)
        stats, data, fields, by_column = {}, {}, {}, {}
        for col in existing:
            valid = values[col].dropna()
            if valid.empty:
                continue
            result = self._field_handler(col)._compute_stats(valid, int(answered.sum() - len(valid)))
            label = self.item_label(col, lang)
            stats[label] = result
            data[label] = result.to_summary_dict()
            fields[self.field_key(col)] = result.to_summary_dict()
            by_column[col] = result
        n = int(answered.sum())
        warnings = [f"Columns missing: {missing}"] if missing else []
        # fields: statistics by language-independent key (dashboard, see field_key)
        metadata = {"item_stats": stats, "columns": existing, "fields": fields, "stats_by_column": by_column}
        total = self.config.get("constant_sum")
        if total not in (None, ""):
            metadata["constant_sum"] = self._constant_sum(values[answered], float(total), lang)
            if metadata["constant_sum"]["share_matching"] < 90:
                warnings.append(f"Only {metadata['constant_sum']['share_matching']:.0f}% of the respondents reach "
                                f"the sum {total:g}")
        return AnalysisResult(question_id=self.question_id, n=n, data=data, warnings=warnings,
                              description=self.generate_description_text(data, n, lang), metadata=metadata)

    def _constant_sum(self, values: pd.DataFrame, total: float, lang: str) -> dict:
        sums = values.fillna(0).sum(axis=1)
        tolerance = abs(total) * 0.005
        matching = (sums - total).abs() <= tolerance
        shares = {self.item_label(c, lang): float((values[c].fillna(0) / total * 100).mean()) for c in values.columns}
        return {"total": total, "n_respondents": int(len(values)), "n_matching": int(matching.sum()),
                "share_matching": float(matching.mean() * 100) if len(values) else 0.0,
                "mean_share_of_total": shares,
                "mean_share_by_key": {self.field_key(c): float((values[c].fillna(0) / total * 100).mean())
                                      for c in values.columns},
                "mean_share_by_column": {c: float((values[c].fillna(0) / total * 100).mean()) for c in values.columns}}

    def report_metadata(self, result: AnalysisResult, lang: str) -> dict:
        """Statistics and constant sum with the field labels of ``lang`` (Word report)."""
        meta = result.metadata or {}
        by_column = meta.get("stats_by_column") or {}
        out = {"item_stats": {self.item_label(c, lang): st for c, st in by_column.items()}}
        cs = meta.get("constant_sum")
        if cs:
            out["constant_sum"] = {**cs, "mean_share_of_total": {self.item_label(c, lang): v for c, v in
                                                                 (cs.get("mean_share_by_column") or {}).items()}}
        return out

    def field_key(self, column: str) -> str:
        """Language-independent key of a field: its position in the configured columns."""
        return f"f{self.columns.index(column)}"

    def generate_description_text(self, data: Dict[str, dict], n: int, lang: str = "de") -> str:
        if not data:
            return ""
        from utils.languages import text
        top = max(data.items(), key=lambda kv: kv[1].get("mean", float("-inf")))
        decimals = int((self.config.get("numeric") or {}).get("decimals", 1))
        return text("handler.numeric_matrix.top", lang, item=top[0], mean=f"{top[1]['mean']:.{decimals}f}", n=n)

    def create_chart(self, result: AnalysisResult, chart_type: str = "bar_horizontal", lang: str = "de", **kwargs):
        import matplotlib.pyplot as plt
        from utils.languages import text
        keys = list(result.data.keys())
        means = [result.data[k].get("mean", 0) for k in keys]
        # the result is keyed by the labels of the source language: the chart shows the
        # label of each field in the language of the report
        by_source = {self.item_label(c, self.source_language): c for c in self.columns}
        labels = [self.item_label(by_source[k], lang) if k in by_source else k for k in keys]
        from utils.charts import ChartGenerator, house_style
        fig, ax = plt.subplots(figsize=(10, max(3, 0.6 * len(labels) + 1.5)))
        bars = ax.barh(labels[::-1], means[::-1], color=ChartGenerator.DEFAULT_COLORS['primary'], height=0.7)
        for bar, value in zip(bars, means[::-1]):
            ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f" {value:.1f}", va='center', fontsize=10)
        ax.set_xlabel(text("handler.numeric_matrix.axis_mean", lang))
        house_style(fig, ax, self.get_title(lang), text("chart.base", lang, n=result.n))
        return fig
