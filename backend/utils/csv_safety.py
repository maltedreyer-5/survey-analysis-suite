# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Protection against CSV formula injection.

Exported CSV files contain free-text answers written by survey respondents.
Spreadsheet programs execute cells that start with =, +, -, @, tab or
carriage return as formulas, so such text cells are prefixed with an
apostrophe. Numbers are written unchanged.
"""
import csv
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Return value unchanged unless it is text a spreadsheet would evaluate."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


class _SafeWriter:
    def __init__(self, writer):
        self._writer = writer

    def writerow(self, row):
        return self._writer.writerow([safe_cell(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def safe_csv_writer(f, **kwargs) -> _SafeWriter:
    """Drop-in replacement for csv.writer that neutralises formula cells."""
    return _SafeWriter(csv.writer(f, **kwargs))


def safe_dataframe(df):
    """Copy of df with text cells and index labels neutralised."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or str(out[col].dtype) in ("string", "str"):
            out[col] = out[col].map(safe_cell)
    out.index = out.index.map(safe_cell)
    out.columns = [safe_cell(c) for c in out.columns]
    return out
