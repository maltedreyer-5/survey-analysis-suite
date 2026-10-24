# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Columns of the editable tables of the interface.

Each column is a (key, label) pair: the code works with the keys, the
interface shows the labels. A table edited in the interface comes back with
the labels as column names; from_ui renames them to the keys by position, so
a changed label (for instance a translation) changes nothing in the code.
"""
from typing import Iterable, List, Sequence, Tuple

import pandas as pd


class UiTable:
    def __init__(self, columns: Sequence[Tuple[str, str]]):
        self.columns: List[Tuple[str, str]] = list(columns)

    @property
    def keys(self) -> List[str]:
        return [k for k, _ in self.columns]

    @property
    def labels(self) -> List[str]:
        return [label for _, label in self.columns]

    def empty(self) -> pd.DataFrame:
        return pd.DataFrame(columns=self.labels)

    def to_ui(self, rows: Iterable[dict]) -> pd.DataFrame:
        """Rows keyed by the column keys as a table with the labels as headers."""
        df = pd.DataFrame(list(rows), columns=self.keys)
        df.columns = self.labels
        return df

    def from_ui(self, df: pd.DataFrame) -> pd.DataFrame:
        """A table from the interface with the column keys as names (by position)."""
        if df is None:
            return pd.DataFrame(columns=self.keys)
        if len(df.columns) != len(self.columns):
            raise ValueError(f"table has {len(df.columns)} columns, expected {len(self.columns)}")
        out = df.copy()
        out.columns = self.keys
        return out


# Answer translations (Import tab)
TRANSLATIONS = UiTable([("id", "ID"), ("original", "Original"), ("language", "Language"),
                        ("translation", "Translation")])
# Extracted items (Workbench, Items)
ITEMS = UiTable([("id", "ID"), ("original", "Original"), ("extracted", "Extracted"),
                 ("cluster", "Cluster")])
# Groups of similar items (Workbench, Normalisation)
SIMILAR_ITEMS = UiTable([("group", "Group"), ("variants", "Variants"), ("frequency", "Frequency"),
                         ("suggestion", "Suggestion"), ("apply", "Apply")])


def cluster_editor_table(langs: Sequence[str]) -> UiTable:
    """Cluster editor: a name per language, the description in the source language (langs[0])."""
    return UiTable([("id", "ID")]
                   + [(f"name_{lang}", f"Name ({lang.upper()})") for lang in langs]
                   + [(f"description_{langs[0]}", f"Description ({langs[0].upper()})"), ("items", "Items")])
