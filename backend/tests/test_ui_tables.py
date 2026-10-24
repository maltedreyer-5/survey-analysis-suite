# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Editable interface tables: the code works with column keys, the labels can
change (e.g. be translated) without changing what is read back."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import translations, ui_tables  # noqa: E402
from services.ui_tables import UiTable  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


def test_round_trip_by_position_whatever_the_labels():
    t = UiTable([("id", "ID"), ("text", "Text")])
    shown = t.to_ui([{"id": 1, "text": "a"}])
    assert list(shown.columns) == ["ID", "Text"]
    shown.columns = ["Kennung", "Wortlaut"]                 # labels changed (translated)
    back = t.from_ui(shown)
    assert back.to_dict("records") == [{"id": 1, "text": "a"}]


def test_wrong_number_of_columns_is_an_error():
    with pytest.raises(ValueError):
        UiTable([("id", "ID")]).from_ui(pd.DataFrame({"a": [1], "b": [2]}))


@pytest.mark.parametrize("labels", [None, ["Id", "Source", "Language", "Translation"]])
def test_translation_table_saves_edits_with_any_labels(tmp_path, monkeypatch, labels):
    if labels:
        monkeypatch.setattr(ui_tables.TRANSLATIONS, "columns",
                            list(zip(ui_tables.TRANSLATIONS.keys, labels)))
    db = SurveyDatabase(tmp_path / "t.db")
    db.save_translation("Kosten", "de", "en", "Costs")
    state = SimpleNamespace(db=db)
    shown = translations.load_translations_from_db(state, "en")
    assert list(shown.columns) == ui_tables.TRANSLATIONS.labels
    shown.iloc[0, 3] = "Expenses"                           # the user edits the translation cell
    assert translations.save_translations_to_db(state, shown, "en").startswith("✅")
    assert db.get_translation("Kosten", "de", "en") == "Expenses"


def test_similar_items_table_round_trip_with_translated_headers(tmp_path, monkeypatch):
    from services import workbench_store as wb
    monkeypatch.setattr(wb, "find_similar_items", lambda qid, thr: [
        {"variants": ["Kosten zu hoch", "kosten zu hoch!"], "counts": [3, 1], "total_count": 4,
         "suggested_canonical": "Kosten zu hoch"}])
    shown = wb.load_similar_items_table("q1", 0.85)
    assert list(shown.columns) == ui_tables.SIMILAR_ITEMS.labels
    shown.columns = ["Group", "Variants", "Frequency", "Suggestion", "Apply"]   # translated headers
    row = ui_tables.SIMILAR_ITEMS.from_ui(shown).iloc[0]
    assert row["suggestion"] == "Kosten zu hoch" and row["apply"] == wb.APPLY_YES
    assert '"kosten zu hoch!" (1x)' in row["variants"]
    assert "no" in wb.APPLY_NO and "nein" in wb.APPLY_NO         # skipping works in both languages
