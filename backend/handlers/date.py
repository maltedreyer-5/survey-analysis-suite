# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Handler for dates: LimeSurvey ``D``, SPSS date formats,
Qualtrics text entry with date validation.

Reads dates in the forms exports use (ISO ``2026-03-15``, with time;
``15.03.2026``; ``03/15/2026`` as month/day/year, the form of US exports;
date objects from SPSS). Reports the number of valid and invalid values,
the first, last and median date, and the distribution over periods: days
for a span of up to 31 days, months up to two years, years beyond
(``date: {granularity: day|month|year}`` sets it). Period keys are
language-independent (``2026-03-15``, ``2026-03``, ``2026``); the dashboard
and the reports format them per language.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

import pandas as pd

from handlers.base import AnalysisResult, SingleColumnHandler

logger = logging.getLogger(__name__)

_DOTTED = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{2,4}")
_SLASHED = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}")


def parse_dates(series: pd.Series, dayfirst_slash: bool = False) -> pd.Series:
    """Values -> Timestamps (NaT where no date). Missing stays NaT."""
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for idx, value in series.items():
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        if isinstance(value, (pd.Timestamp,)) or hasattr(value, "year"):
            try:
                out[idx] = pd.Timestamp(value)
            except (ValueError, TypeError):
                pass
            continue
        text = str(value).strip()
        if not text:
            continue
        try:
            if _DOTTED.match(text):
                out[idx] = pd.to_datetime(text, dayfirst=True)
            elif _SLASHED.match(text):
                out[idx] = pd.to_datetime(text, dayfirst=dayfirst_slash)
            else:
                out[idx] = pd.to_datetime(text, format="ISO8601")
        except (ValueError, TypeError, OverflowError):
            pass
    return out


def period_key(ts: pd.Timestamp, granularity: str) -> str:
    return ts.strftime({"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[granularity])


class DateHandler(SingleColumnHandler):
    """Dates: distribution over days, months or years; range and median."""

    answers_as_text = False  # computes with the values (numbers, dates)
    handler_type = "date"
    supported_charts = ["bar_vertical"]

    def _settings(self) -> dict:
        return self.config.get("date") or {}

    def granularity(self, first: pd.Timestamp, last: pd.Timestamp) -> str:
        configured = self._settings().get("granularity")
        if configured in ("day", "month", "year"):
            return configured
        span = (last - first).days
        return "day" if span <= 31 else ("month" if span <= 731 else "year")

    def analyze(self, df: pd.DataFrame, lang: str = "de", **kwargs) -> AnalysisResult:
        col = self.get_column()
        if not col or col not in df.columns:
            return AnalysisResult(question_id=self.question_id, n=0, data={},
                                  warnings=[f"Column '{col}' not in the data"])
        given = df[col].notna()
        dates = parse_dates(df[col], bool(self._settings().get("dayfirst_slash", False)))
        valid = dates.dropna()
        n_invalid = int(given.sum()) - len(valid)
        warnings = [f"{n_invalid} values are not a date"] if n_invalid else []
        if valid.empty:
            return AnalysisResult(question_id=self.question_id, n=0, data={}, warnings=warnings or ["No data"])
        first, last = valid.min(), valid.max()
        gran = self.granularity(first, last)
        counts = valid.map(lambda ts: period_key(ts, gran)).value_counts()
        # every period between first and last, also empty ones, in time order
        periods = pd.period_range(first, last, freq={"day": "D", "month": "M", "year": "Y"}[gran])
        keys = [period_key(p.to_timestamp(), gran) for p in periods]
        data = {k: int(counts.get(k, 0)) for k in keys}
        n = len(valid)
        percentages = {k: round(v / n * 100, 1) for k, v in data.items()}
        median = valid.sort_values().iloc[(n - 1) // 2]
        metadata = {"granularity": gran, "first": first.strftime("%Y-%m-%d"), "last": last.strftime("%Y-%m-%d"),
                    "median": median.strftime("%Y-%m-%d"), "n_invalid": n_invalid}
        result = AnalysisResult(question_id=self.question_id, n=n, data=data, percentages=percentages,
                                warnings=warnings, metadata=metadata)
        result.description = self.generate_description_text(result, lang)
        return result

    def generate_description_text(self, result: AnalysisResult, lang: str = "de") -> str:
        from utils.languages import text
        m = result.metadata
        peak = max(result.data.items(), key=lambda kv: kv[1])[0] if result.data else ""
        return text("handler.date.summary", lang, n=result.n, first=format_date(m["first"], lang),
                    last=format_date(m["last"], lang), median=format_date(m["median"], lang),
                    peak=format_period(peak, m["granularity"], lang))

    def create_chart(self, result: AnalysisResult, chart_type: str = "bar_vertical", lang: str = "de", **kwargs):
        import matplotlib.pyplot as plt
        from utils.languages import text
        gran = result.metadata.get("granularity", "month")
        labels = [format_period(k, gran, lang) for k in result.data]
        from utils.charts import ChartGenerator, house_style
        fig, ax = plt.subplots(figsize=(max(8, 0.45 * len(labels) + 2), 5))
        ax.bar(labels, list(result.data.values()), color=ChartGenerator.DEFAULT_COLORS['primary'], width=0.7)
        ax.set_ylabel(text("handler.date.axis_count", lang))
        if len(labels) > 8:
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
        house_style(fig, ax, self.get_title(lang), text("chart.base", lang, n=result.n))
        return fig




def format_date(iso: str, lang: str) -> str:
    """2026-03-15 -> 15.03.2026 (de), 15/03/2026 (fr, it), 2026-03-15 (en, ISO)."""
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return {"de": f"{d}.{m}.{y}", "fr": f"{d}/{m}/{y}", "it": f"{d}/{m}/{y}"}.get(lang, iso)


def format_period(key: str, granularity: str, lang: Optional[str]) -> str:
    if granularity == "month" and len(key) == 7:
        y, m = key.split("-")
        from utils.languages import catalog_value
        return f"{catalog_value('date.months', lang or 'en')[int(m) - 1]} {y}"
    if granularity == "day":
        return format_date(key, lang)
    return key


def period_labels(keys, granularity: str, languages) -> Dict[str, Dict[str, str]]:
    return {k: {lang: format_period(k, granularity, lang) for lang in languages} for k in keys}
