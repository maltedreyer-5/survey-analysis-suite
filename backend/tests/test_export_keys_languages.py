# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Dashboard export: option keys and labels from the source language, every output language."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export.keys import _build_options_with_keys, _create_source_to_key_map, labels_per_language  # noqa: E402


@pytest.fixture
def source(monkeypatch):
    def set_lang(lang, languages):
        cfg = {"settings": {"survey_metadata": {"source_language": lang, "languages": languages}}}
        monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(config=cfg)))
    return set_lang


def test_english_source_with_italian_output(source):
    source("en", ["en", "de", "it"])
    q = {"options": {"en": ["Very good", "Poor"], "de": ["Sehr gut", "Schlecht"], "it": ["Molto buono"]}}
    opts = _build_options_with_keys(q)
    assert opts[0]["label"] == {"en": "Very good", "de": "Sehr gut", "it": "Molto buono"}
    assert opts[1]["label"]["it"] == "Poor"                          # shorter list: source label stays
    assert _create_source_to_key_map(opts) == {"Very good": opts[0]["key"], "Poor": opts[1]["key"]}


def test_german_source_unchanged_for_configured_languages(source):
    source("de", ["de", "fr", "en"])
    q = {"options": {"de": ["Ja", "Nein"], "fr": ["Oui", "Non"], "en": ["Yes", "No"]}}
    assert [o["label"] for o in _build_options_with_keys(q)] == [
        {"de": "Ja", "fr": "Oui", "en": "Yes"}, {"de": "Nein", "fr": "Non", "en": "No"}]


def test_plain_list_is_source_language():
    assert labels_per_language(["Alpha"], ["de", "en"], src="en") == [{"en": "Alpha", "de": "Alpha"}]


def test_question_and_cluster_texts_fall_back_to_the_source_language(source):
    from export.question_texts import help_text, question_text, stored_cluster_texts
    source("en", ["en", "de", "it"])
    q = {"label": {"en": "Parking", "de": "Parken"}, "full_question": {"en": "How is parking?"},
         "help": {"en": "Campus only"}}
    assert question_text(q, "de", "q") == "How is parking?"        # full question preferred, source language
    assert question_text({"label": {"en": "Parking", "de": "Parken"}}, "de", "q") == "Parken"
    assert help_text(q, "it") == "Campus only"
    # English survey: the source text and a German translation stay apart
    stored = {"name_de": "Too expensive", "name_source": "Too expensive", "description_source": "",
              "translations_flat": {"name_de": "Zu teuer", "name_it": "Troppo caro"}}
    texts = stored_cluster_texts(stored, ["en", "de", "it"])
    assert texts["name"] == {"en": "Too expensive", "de": "Zu teuer", "it": "Troppo caro"}


def test_word_fixed_texts_fall_back_to_english_and_config_texts_to_the_source(source):
    from utils.export_word import _config_text, _fixed_text
    source("en", ["en", "de", "it"])
    assert _fixed_text({"de": "Seite", "fr": "Page", "en": "Page"}, "it") == "Page"     # not German
    assert _config_text({"en": "Parking", "de": "Parken"}, "it") == "Parking"           # source language
    assert _config_text({"en": "Parking", "de": "Parken"}, "de") == "Parken"
    assert _config_text("", "de", "q1") == "q1"


def test_word_fixed_texts_come_from_the_catalogs_with_italian():
    from utils.export_word import _fixed_text, _word
    assert _fixed_text(_word("toc"), "it") == "Indice"
    assert _fixed_text(_word("toc"), "de") == "Inhaltsverzeichnis"                    # unchanged
    assert _fixed_text(_word("generated_on", date="1.2.2026"), "fr") == "Généré le 1.2.2026"
    assert _fixed_text(_word("question_types"), "it")["ranking"] == "Classifica / Priorità"
    assert list(_fixed_text(_word("footer"), "en")) == ["Page ", " of "]


def test_section_and_ranking_labels_have_italian():
    from handlers.ranking import RankingHandler
    from utils.summary_schema import DEFAULT_THREE_SECTIONS_SCHEMA
    labels = {s.id: s.label for s in DEFAULT_THREE_SECTIONS_SCHEMA.sections}
    assert labels["description"]["it"] == "Descrizione dei risultati"
    assert labels["description"]["de"] == "Beschreibung der Ergebnisse"
    rk = RankingHandler({"id": "r", "columns": ["r1"]})
    assert rk._get_table_headers("it")[0] == "Rango" and rk._get_score_label("de") == "Gewichteter Score"


def test_plain_cluster_names_stored_under_the_source_language(source):
    from export.result_format import _src
    source("en", ["en", "de"])
    assert _src() == "en"
