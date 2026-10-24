# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Colours of the report charts: ordered scales by polarity or position, evasive answers
light grey, NPS by group; yes/no colours only for whole yes/no answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.chart_colors import EXCLUDED, NEGATIVE, NEUTRAL, POSITIVE, scale_colours  # noqa: E402
from handlers.single_choice import SingleChoiceHandler  # noqa: E402


def test_polarity_of_a_named_scale():
    q = {"ordinal": True, "scale_order": "descending",
         "option_polarity": {"Very satisfied": "positive", "Rather satisfied": "positive",
                             "Rather dissatisfied": "negative", "Very dissatisfied": "negative"}}
    c = scale_colours(["Very satisfied", "Rather satisfied", "Rather dissatisfied", "Very dissatisfied", "Don't know"], q)
    assert c["Very satisfied"] == POSITIVE and c["Very dissatisfied"] == NEGATIVE
    assert c["Rather satisfied"] not in (POSITIVE, NEGATIVE) and c["Don't know"] == EXCLUDED


def test_position_without_polarity_follows_the_direction():
    up = scale_colours(["1", "2", "3", "4", "5"], {"ordinal": True, "scale_order": "ascending"})
    assert up["5"] == POSITIVE and up["1"] == NEGATIVE and up["3"] == NEUTRAL
    down = scale_colours(["Very good", "Good", "Poor", "Very poor"], {"handler": "matrix_likert", "scale_order": "descending"})
    assert down["Very good"] == POSITIVE and down["Very poor"] == NEGATIVE


def test_nps_groups_and_nominal_questions():
    c = scale_colours([str(i) for i in range(11)], {"nps": True})
    assert {c["0"], c["6"]} == {NEGATIVE} and {c["7"], c["8"]} == {NEUTRAL} and {c["9"], c["10"]} == {POSITIVE}
    assert scale_colours(["Student", "Staff"], {"handler": "single_choice"}) == {}


def test_yes_no_colours_only_for_whole_answers(monkeypatch):
    h = SingleChoiceHandler({"id": "q", "handler": "single_choice", "columns": ["q"],
                             "options": {"de": ["Ja", "Nein", "Weiß nicht", "Technologie", "Japan"]}})
    monkeypatch.setattr(SingleChoiceHandler, "source_language", property(lambda self: "de"))
    colors = h.get_colors()
    assert set(colors) == {"Ja", "Nein"}   # "Technologie" contains "no", "Japan" contains "ja"
