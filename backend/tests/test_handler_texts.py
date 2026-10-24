# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Handler texts and label mappings follow the survey's source language; fixed texts come from locales/."""
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers.single_choice import SingleChoiceHandler  # noqa: E402
from handlers.multi_choice import MultiChoiceHandler  # noqa: E402
from handlers.ranking import RankingHandler  # noqa: E402


@pytest.fixture
def source(monkeypatch):
    def set_lang(lang):
        cfg = {"settings": {"survey_metadata": {"source_language": lang, "languages": ["de", "en", "it"]}}}
        monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(config=cfg)))
    return set_lang


def single(lang_src):
    opts = {"de": ["Ja", "Nein"], "en": ["Yes", "No"], "it": ["Sì", "No"]}
    return SingleChoiceHandler({"id": "q", "columns": ["q"], "label": {"de": "Frage", "en": "Question"},
                                "options": opts})


def test_german_source_text_unchanged(source):
    source("de")
    h = single("de")
    counts = pd.Series({"Ja": 3, "Nein": 2})
    assert h.generate_description_text(counts, 5, "de") == 'Die häufigste Antwort war "Ja" mit 3 Nennungen (60.0%).'
    assert h.generate_description_text(counts, 5, "en") == 'The most common answer was "Yes" with 3 mentions (60.0%).'


def test_english_source_maps_from_english_labels(source):
    source("en")
    h = single("en")
    assert h._get_option_labels("de") == {"Yes": "Ja", "No": "Nein"}
    assert h._get_option_labels("en") == {}
    counts = pd.Series({"Yes": 3, "No": 2})
    assert h.generate_description_text(counts, 5, "de") == 'Die häufigste Antwort war "Ja" mit 3 Nennungen (60.0%).'
    assert h.get_title("it") == "Question"                     # fallback to the source language, not German


def test_italian_texts_and_other_handlers(source):
    source("de")
    mc = MultiChoiceHandler({"id": "m", "columns": ["a", "b"], "item_labels": {"de": ["A", "B"], "it": ["Ai", "Bi"]}})
    out = mc.generate_description_text({"A": 4, "B": 1}, 5, "it")
    assert out.startswith("Le opzioni scelte più spesso sono state:") and '"Ai" (4, 80%)' in out
    rk = RankingHandler({"id": "r", "columns": ["r1"], "item_labels": {"de": ["X"]}})
    assert "il rango 1 riceve 9 punti" in rk.get_score_explanation(9, "it")
    assert "Rang 2 erhält 8 Punkte" in rk.get_score_explanation(9, "de")


def test_yes_values_one_rule_for_handler_and_statistics(source):
    """English data without binary_values: 'Yes' counts in the handler and in the test variables alike."""
    from handlers.multi_choice_binary import MultiChoiceBinaryHandler
    from utils.analysis_variables import build_variables
    from utils.binary_values import yes_mask
    source("en")
    df = pd.DataFrame({"a": ["Yes", "No", "Yes", None], "b": ["No", "No", "Yes", "No"]})
    qcfg = {"id": "q", "handler": "multi_choice_binary", "columns": ["a", "b"],
            "item_labels": {"en": ["A", "B"]}, "label": {"en": "Q"}}
    result = MultiChoiceBinaryHandler(qcfg).analyze(df, lang="en")
    assert sorted(result.data.values()) == [1, 2]
    assert set(result.metadata["detailed_data"][next(iter(result.data))]) == {"Yes", "No"}  # labels in the source language
    cfg = {"settings": {"survey_metadata": {"source_language": "en"}}, "question_groups": {"q": qcfg}}
    variables = build_variables(cfg, df, ["q"])["q"]
    assert sorted(int(v.values.fillna(0).sum()) for v in variables) == [1, 2]
    # a configured value counts strictly; other spellings only if it matches nothing
    assert yes_mask(pd.Series(["oui", "Ja", "x"]), "x").tolist() == [False, False, True]
    assert yes_mask(pd.Series(["Ja", "nein"]), "Y").tolist() == [True, False]
