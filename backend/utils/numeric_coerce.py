# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Numeric value conversion of the survey analysis tool.

Shared by:
- the numeric handler (statistics of numeric answers)
- the segment resolver (numeric_buckets bucketing)

Both paths must coerce identically, so a question can be analysed as numeric
and used as a segmentation source without the two producing different values.

Handles:
- comma vs. dot as decimal separator ("3,14" -> 3.14, "3.14" -> 3.14)
- empty strings, None, NaN -> NaN
- strings with spaces ("  42 ") -> 42.0
- thousands separators ("1.234,56" -> 1234.56 ; "1,234.56" -> 1234.56)
- percentages ("15%") -> 15.0 (not 0.15: the percent suffix is cut off,
  because surveys usually mean the number itself)
- prefixes such as "ca. " or "ungefähr " are logged and ignored
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# regex for typical prefixes and units (cut off before parsing)
_PREFIX_RE = re.compile(
    r'^(?:ca\.?|circa|ungefaehr|ungefähr|etwa|rund|approx\.?|appr\.?|~)\s*',
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(
    r'\s*(?:%|prozent|percent|stueck|stück|stk\.?|pieces?|pcs?\.?|ea\.?|persons?|personen)\s*$',
    re.IGNORECASE,
)


def coerce_to_float(value) -> float:
    """
    Converts a single value into a float.

    Returns:
        float, or np.nan if not parsable.

    Examples:
        coerce_to_float("3,14")     -> 3.14
        coerce_to_float("1.234,56") -> 1234.56
        coerce_to_float("15%")      -> 15.0
        coerce_to_float("ca. 50")   -> 50.0
        coerce_to_float(None)       -> nan
        coerce_to_float("")         -> nan
        coerce_to_float("abc")      -> nan
    """
    # numeric already?
    if isinstance(value, (int, float, np.integer, np.floating)):
        if isinstance(value, float) and np.isnan(value):
            return np.nan
        return float(value)

    # None / NaN-like
    if value is None:
        return np.nan
    if pd.isna(value):
        return np.nan

    # string processing
    s = str(value).strip()
    if not s:
        return np.nan

    # cut off the prefix/suffix
    s = _PREFIX_RE.sub('', s)
    s = _SUFFIX_RE.sub('', s)
    s = s.strip()
    if not s:
        return np.nan

    # detect thousands separators + decimal separator heuristically
    has_dot = '.' in s
    has_comma = ',' in s

    if has_dot and has_comma:
        # which character comes last? That is the decimal separator.
        if s.rfind(',') > s.rfind('.'):
            # format 1.234,56 -> remove the dots, replace the comma by a dot
            s = s.replace('.', '').replace(',', '.')
        else:
            # format 1,234.56 -> remove the commas
            s = s.replace(',', '')
    elif has_comma and not has_dot:
        # only a comma: read as decimal separator if there is at most 1 comma
        if s.count(',') == 1:
            s = s.replace(',', '.')
        else:
            # several commas => probably thousands separators
            s = s.replace(',', '')
    # only a dot: stays unchanged

    # remove whitespace inside the number ("1 234")
    s = re.sub(r'\s+', '', s)

    try:
        return float(s)
    except (ValueError, TypeError):
        return np.nan


def coerce_series(series: pd.Series) -> pd.Series:
    """
    Applies coerce_to_float element-wise to a Series.

    Returns:
        Series of dtype float64; values that cannot be parsed are NaN.
    """
    if series is None or len(series) == 0:
        return pd.Series([], dtype='float64')

    # fast path for columns that are numeric already
    if pd.api.types.is_numeric_dtype(series):
        return series.astype('float64')

    return series.apply(coerce_to_float).astype('float64')


def coerce_iterable(values: Iterable) -> List[float]:
    """coerce_to_float for any iterable; returns a Python list."""
    return [coerce_to_float(v) for v in values]


def coerce_stats_summary(series: pd.Series) -> dict:
    """
    Convenience: applies coerce_series and returns n, n_invalid, n_total.

    Helps to check beforehand whether a column is numeric at all.
    """
    n_total = len(series)
    if n_total == 0:
        return {'n_total': 0, 'n_valid': 0, 'n_invalid': 0, 'fraction_valid': 0.0}

    coerced = coerce_series(series)
    n_valid = int(coerced.notna().sum())
    n_invalid = n_total - n_valid
    return {
        'n_total': n_total,
        'n_valid': n_valid,
        'n_invalid': n_invalid,
        'fraction_valid': n_valid / n_total if n_total else 0.0,
    }


def is_numeric_column(series: pd.Series, min_valid_fraction: float = 0.5) -> bool:
    """
    Heuristic: is this column 'essentially numeric'?

    Used by the segment resolver in its validation step to check whether a
    question marked as numeric_bucket source really contains parsable values.

    Args:
        series: Pandas Series to check.
        min_valid_fraction: share of parsable values among the non-empty rows
            from which the column counts as numeric. The default 0.5 is
            permissive enough for typical surveys with typos.

    Returns:
        True if a sufficient share coerces to floats.
    """
    if pd.api.types.is_numeric_dtype(series):
        return True

    non_empty = series.dropna()
    non_empty = non_empty[non_empty.astype(str).str.strip() != '']
    if len(non_empty) == 0:
        return False

    coerced = coerce_series(non_empty)
    fraction = coerced.notna().sum() / len(non_empty)
    return fraction >= min_valid_fraction
