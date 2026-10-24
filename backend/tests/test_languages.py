# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Language settings and the shared fallback order for multilingual texts."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import languages as L  # noqa: E402


def cfg(**meta):
    return {"settings": {"survey_metadata": meta}}


def test_defaults():
    # without languages, the output is the source language only
    assert L.get_source_language({}) == "de"
    assert L.get_output_languages({}) == ["de"]
    assert L.get_output_languages(cfg(source_language="en")) == ["en"]


def test_configured_languages_are_normalised():
    assert L.get_output_languages(cfg(languages=["IT", "de", "it", " en "])) == ["it", "de", "en"]
    assert L.get_output_languages(cfg(languages="en")) == ["en"]


def test_unsupported_source_language_is_an_error():
    with pytest.raises(L.LanguageConfigError):
        L.get_source_language(cfg(source_language="it"))


@pytest.mark.parametrize("value, lang, src, expected", [
    ({"de": "Ja", "en": "Yes"}, "en", "de", "Yes"),
    ({"de": "Ja", "en": "Yes"}, "it", "de", "Ja"),        # missing -> source
    ({"en": "Yes"}, "de", "en", "Yes"),                    # English survey: no German text
    ({"de": "", "fr": "Oui"}, "de", "de", "Oui"),          # empty treated as missing
    ("plain", "it", "de", "plain"),
    (None, "de", "de", ""),
])
def test_localized_fallback(value, lang, src, expected):
    assert L.localized(value, lang, src) == expected


def test_language_names_cover_configurable_languages():
    assert L.language_name("it") == "Italian"
    assert L.language_name("xx") == "xx"


def test_validation_warns_for_language_without_catalog():
    warnings = L.validate_language_settings(cfg(languages=["de", "es", "xx"]))
    assert any("'es'" in w for w in warnings) and any("'xx'" in w for w in warnings)
    assert not any("'de'" in w for w in warnings)


def test_text_falls_back_to_english(tmp_path, monkeypatch):
    (tmp_path / "en.yaml").write_text("report:\n  title: 'Results {year}'\n", encoding="utf-8")
    (tmp_path / "it.yaml").write_text("report: {}\n", encoding="utf-8")
    monkeypatch.setattr(L, "LOCALES_DIR", tmp_path)
    L._catalog.cache_clear()
    try:
        assert L.text("report.title", "it", year=2026) == "Results 2026"
        with pytest.raises(KeyError):
            L.text("report.missing", "it")
    finally:
        L._catalog.cache_clear()
