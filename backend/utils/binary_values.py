# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Which answer values mean "yes" / "chosen" in checkbox and yes/no columns.

A question may configure its values (``binary_values: {positive: ..., negative: ...}``).
Without that configuration the values below count as "yes": numeric and
boolean codes and the yes-words of the supported languages. The handlers and
the statistical variables (utils/analysis_variables.py) use this one rule, so
counts and tests rest on the same base.
"""
from typing import Optional

import pandas as pd

AFFIRMATIVE_VALUES = ("1", "true", "ja", "yes", "oui", "sì", "si", "y", "j")


def configured_positive(qconfig: dict) -> Optional[str]:
    value = (qconfig.get("binary_values") or {}).get("positive")
    return None if value in (None, "") else str(value).strip()


def configured_negative(qconfig: dict) -> Optional[str]:
    value = (qconfig.get("binary_values") or {}).get("negative")
    return None if value in (None, "") else str(value).strip()


def is_affirmative(value) -> bool:
    """A value of the language-independent "yes" set (case-insensitive; 1, 1.0 and True included)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value).strip().lower() in AFFIRMATIVE_VALUES


def chosen_mask(df: pd.DataFrame, column: str, config: dict) -> pd.Series:
    """
    True where the option of ``column`` of a yes/no multiple choice is chosen: a "yes"
    by ``yes_mask``, or, for a column listed in ``text_item_columns`` (the text field
    of "other" in survey tools without an "other" checkbox), any text.
    """
    if column in (config.get("text_item_columns") or []):
        return df[column].map(lambda v: pd.notna(v) and str(v).strip() != "").astype(bool)
    return yes_mask(df[column], configured_positive(config))


def yes_mask(series: pd.Series, positive: Optional[str]) -> pd.Series:
    """
    True where a column says "yes". With a configured positive value only that
    value counts; if it matches nothing in the column (other spelling or coding
    in the data), the language-independent set is used instead.
    """
    if positive is not None:
        mask = series.map(lambda v: pd.notna(v) and str(v).strip() == positive)
        if mask.any():
            return mask.astype(bool)
    return series.map(is_affirmative).astype(bool)
