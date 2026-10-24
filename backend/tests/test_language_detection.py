# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Language detection and fallbacks: Italian stays Italian, defaults follow the source language."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def source(monkeypatch):
    def set_lang(lang):
        cfg = {"settings": {"survey_metadata": {"source_language": lang}}}
        monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(config=cfg)))
    return set_lang


def test_italian_is_not_mapped_to_german(source):
    from utils.data_import import DataImporter
    source("en")
    importer = DataImporter(freetext_columns=[])
    assert importer.detect_language("La mensa è troppo cara e il servizio è molto lento ogni giorno") == "it"
    assert importer.detect_language("kurz") == "en"               # short text: source language, not German


def test_fixed_tables_fall_back_to_english_before_german():
    from utils.translation import _pick
    assert _pick({"de": "Ja", "en": "Yes"}, "it", "x") == "Yes"
    assert _pick({"de": "Ja"}, "it", "x") == "Ja"
    assert _pick({}, "it", "x") == "x"


def test_theme_matching_reads_the_source_language():
    from themes.theme_analyzer import _initial_keywords, _list_in_source, _text_in_source
    cfg = {"settings": {"survey_metadata": {"source_language": "en"}}}
    q = {"label": {"de": "Parken", "en": "Parking"}, "options": {"de": ["Ja"], "en": ["Yes"]}}
    assert _text_in_source(q["label"], cfg) == "Parking"
    assert _list_in_source(q, "options", cfg) == ["Yes"]
    assert _initial_keywords({"keywords": {"de": ["mensa"], "en": ["canteen"]}}, cfg) == ["canteen"]
    assert _initial_keywords({"question_keywords": ["x"]}, cfg) == ["x"]


def test_common_labels_from_the_catalogs_in_every_direction():
    from utils.translation import TranslationManager
    tm = TranslationManager()
    assert tm.translate_common_label("Ja", "fr") == "Oui"
    assert tm.translate_common_label("Sonstiges", "en") == "Other"
    assert tm.translate_common_label("Ja", "it") == "Sì"                  # Italian is new
    assert tm.translate_common_label("Strongly agree", "de") == "Stimme voll zu"   # English source
    assert tm.translate_common_label("Something else", "de") == "Something else"


def test_config_validation_reports_silently_skipped_or_misread_questions():
    from services.config_validation import validate_survey_config
    cfg = {"settings": {"survey_metadata": {"source_language": "de"}},
           "question_groups": {
               "d": {"handler": "calendar", "columns": ["D"]},
               "k": {"handler": "numeric", "columns": ["K1", "K2"]},
               "m": {"handler": "matrix_likert", "columns": ["M1"], "lss_metadata": {"type": ";"}},
               "ok": {"handler": "single_choice", "columns": ["S"]},
               "t": {"handler": "freetext", "columns": ["T1", "T2"]}}}
    errors, warnings = validate_survey_config(cfg)
    assert any("Question d: unknown handler 'calendar'" in e for e in errors)
    assert any("Question k: numeric with 2 columns" in e for e in errors)
    assert not any("Question t" in m for m in errors + warnings)   # several text fields are evaluated
    assert any("Question m: LimeSurvey type ';'" in w for w in warnings)
    assert not any("Question ok" in m for m in errors + warnings)


def test_llm_context_only_from_settings_and_other_places_reported():
    from services.config_validation import validate_survey_config
    from utils.prompts import context_text
    old = {"settings": {"survey_metadata": {"source_language": "de", "llm_context": "Alt"}}, "question_groups": {}}
    assert context_text(old) == "-"
    assert any("the context belongs in settings.llm_context" in w for w in validate_survey_config(old)[1])
    assert context_text({"settings": {"llm_context": "Neu"}}) == "Neu"


def test_output_languages_default_to_the_source_language():
    from utils.languages import get_output_languages
    assert get_output_languages({"settings": {"survey_metadata": {"source_language": "en"}}}) == ["en"]
    assert get_output_languages({"settings": {"survey_metadata": {"source_language": "de"}}}) == ["de"]
    assert get_output_languages({"settings": {"survey_metadata": {"source_language": "de", "languages": ["de", "it"]}}}) == ["de", "it"]
