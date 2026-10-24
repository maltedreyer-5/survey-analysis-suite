# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Exported CSV cells must not be executable as spreadsheet formulas."""
import csv
import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.csv_safety import safe_cell, safe_csv_writer, safe_dataframe  # noqa: E402
from utils.export_data import DataExporter  # noqa: E402

DANGEROUS = ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]


def test_text_cells_are_neutralised_numbers_untouched():
    for v in DANGEROUS:
        assert safe_cell(v) == "'" + v
    assert safe_cell("normal text") == "normal text"
    assert safe_cell(-5) == -5 and safe_cell(3.2) == 3.2 and safe_cell(None) is None


def test_writer_neutralises_rows():
    buf = io.StringIO()
    safe_csv_writer(buf, delimiter=";").writerow(["=1+1", 4, "ok"])
    assert next(csv.reader(io.StringIO(buf.getvalue()), delimiter=";")) == ["'=1+1", "4", "ok"]


def test_dataframe_export_neutralises_values_index_and_header(tmp_path):
    df = pd.DataFrame({"=evil": ["=1+1", "fine"], "n": [-1, 2]}, index=["@idx", "ok"])
    out = safe_dataframe(df)
    assert list(out.columns) == ["'=evil", "n"]
    assert list(out.index) == ["'@idx", "ok"]
    assert out.iloc[0, 0] == "'=1+1" and out.iloc[0, 1] == -1
    path = DataExporter(output_dir=tmp_path).export_csv(df, "t")
    text = Path(path).read_text(encoding="utf-8-sig")
    assert "'=1+1" in text and "\n=1+1" not in text and ";=1+1" not in text
