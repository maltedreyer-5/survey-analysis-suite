# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""date handler: parsing, periods, description, chart."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers import get_handler  # noqa: E402
from handlers.date import format_period, parse_dates  # noqa: E402


def test_parsing_of_export_formats():
    s = pd.Series(["2026-03-15", "2026-03-15 00:00:00", "15.03.2026", "03/15/2026", "kein Datum", None, ""])
    parsed = parse_dates(s)
    assert parsed.dropna().dt.strftime("%Y-%m-%d").tolist() == ["2026-03-15"] * 4
    assert parsed.isna().sum() == 3


def test_months_distribution_with_empty_periods_and_description():
    df = pd.DataFrame({"d": ["2026-01-10", "2026-01-20", "2026-03-05", "2026-04-01", "x", None]})
    h = get_handler("date", "d", {"id": "d", "columns": ["d"], "label": {"de": "Start"}})
    r = h.analyze(df, lang="de")
    assert r.n == 4 and r.metadata["granularity"] == "month"
    assert r.data == {"2026-01": 2, "2026-02": 0, "2026-03": 1, "2026-04": 1}
    assert r.metadata["median"] == "2026-01-20" and r.warnings == ["1 values are not a date"]
    assert r.description.startswith("4 Angaben zwischen 10.01.2026 und 01.04.2026") and "Jan 2026" in r.description
    fig = h.create_chart(r, "bar_vertical", lang="it")
    ax = fig.axes[0]
    assert [round(p.get_height()) for p in ax.patches] == [2, 0, 1, 1]
    assert [t.get_text() for t in ax.get_xticklabels()] == ["gen 2026", "feb 2026", "mar 2026", "apr 2026"]


def test_days_and_years():
    h = get_handler("date", "d", {"id": "d", "columns": ["d"]})
    assert h.analyze(pd.DataFrame({"d": ["2026-01-01", "2026-01-03"]}), lang="en").data == {
        "2026-01-01": 1, "2026-01-02": 0, "2026-01-03": 1}
    assert list(h.analyze(pd.DataFrame({"d": ["2020-05-01", "2026-01-03"]}), lang="en").data)[:2] == ["2020", "2021"]
    assert format_period("2026-03-15", "day", "de") == "15.03.2026"
