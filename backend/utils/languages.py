# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Languages of a survey and lookup of multilingual texts.

Two settings in ``settings.survey_metadata`` of a survey configuration
decide which languages are involved:

- ``source_language``: language of the responses and of the texts in the
  configuration. Prompts, translation direction and fallbacks follow it.
  Supported: ``de`` and ``en``.
- ``languages``: output languages of reports and dashboard, the first one
  being the default. Any ISO 639-1 code is accepted; content is translated
  by the LLM. Fixed texts (headings, scale labels, UI) come from the
  catalogs in ``locales/``; languages without a catalog fall back to English
  for those texts.

All lookups of multilingual values go through :func:`localized`, so the
fallback order is the same everywhere: requested language, source
language, English, then any non-empty value.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List, Optional

import yaml

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_LANGUAGES = ("de", "en")
DEFAULT_SOURCE_LANGUAGE = "de"
FALLBACK_LANGUAGE = "en"

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

# English names, used in prompts ("Translate into Italian").
LANGUAGE_NAMES = {
    "de": "German", "en": "English", "fr": "French", "it": "Italian",
    "es": "Spanish", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "cs": "Czech", "da": "Danish", "sv": "Swedish", "fi": "Finnish",
    "nb": "Norwegian", "el": "Greek", "hu": "Hungarian", "ro": "Romanian",
    "sk": "Slovak", "sl": "Slovenian", "hr": "Croatian", "bg": "Bulgarian",
    "et": "Estonian", "lv": "Latvian", "lt": "Lithuanian", "ga": "Irish",
    "mt": "Maltese", "lb": "Luxembourgish", "tr": "Turkish", "uk": "Ukrainian",
}

# German names, used in German prompts ("Uebersetze ins Italienische").
LANGUAGE_NAMES_DE = {
    "de": "Deutsch", "en": "Englisch", "fr": "Französisch", "it": "Italienisch",
    "es": "Spanisch", "pt": "Portugiesisch", "nl": "Niederländisch", "pl": "Polnisch",
    "cs": "Tschechisch", "da": "Dänisch", "sv": "Schwedisch", "fi": "Finnisch",
    "nb": "Norwegisch", "el": "Griechisch", "hu": "Ungarisch", "ro": "Rumänisch",
    "sk": "Slowakisch", "sl": "Slowenisch", "hr": "Kroatisch", "bg": "Bulgarisch",
    "et": "Estnisch", "lv": "Lettisch", "lt": "Litauisch", "ga": "Irisch",
    "mt": "Maltesisch", "lb": "Luxemburgisch", "tr": "Türkisch", "uk": "Ukrainisch",
}

# Names in the language itself, used for language selectors.
NATIVE_NAMES = {
    "de": "Deutsch", "en": "English", "fr": "Français", "it": "Italiano",
    "es": "Español", "pt": "Português", "nl": "Nederlands", "pl": "Polski",
}


class LanguageConfigError(ValueError):
    """Invalid language settings in a survey configuration."""


def _metadata(config: Optional[dict]) -> dict:
    return ((config or {}).get("settings") or {}).get("survey_metadata") or {}


def get_source_language(config: Optional[dict]) -> str:
    """Language of responses and configuration texts (``de`` or ``en``)."""
    lang = _metadata(config).get("source_language") or DEFAULT_SOURCE_LANGUAGE
    if lang not in SUPPORTED_SOURCE_LANGUAGES:
        raise LanguageConfigError(
            f"source_language '{lang}' is not supported; "
            f"use one of {', '.join(SUPPORTED_SOURCE_LANGUAGES)}"
        )
    return lang


def get_output_languages(config: Optional[dict]) -> List[str]:
    """Configured output languages; default: the source language only."""
    langs = _metadata(config).get("languages")
    if not langs:
        return [get_source_language(config)]
    if isinstance(langs, str):
        langs = [langs]
    result = []
    for code in langs:
        code = str(code).strip().lower()
        if code and code not in result:
            result.append(code)
    return result


def language_name(code: str, in_lang: str = "en") -> str:
    """
    Name of a language for use in prompts written in ``in_lang`` (en or de).
    Unknown codes are returned as-is.
    """
    table = LANGUAGE_NAMES_DE if in_lang == "de" else LANGUAGE_NAMES
    return table.get(code, code)


def native_name(code: str) -> str:
    return NATIVE_NAMES.get(code, LANGUAGE_NAMES.get(code, code))


def fallback_chain(lang: str, source_lang: str = DEFAULT_SOURCE_LANGUAGE) -> List[str]:
    chain = []
    for code in (lang, source_lang, FALLBACK_LANGUAGE):
        if code and code not in chain:
            chain.append(code)
    return chain


def localized(value: Any, lang: str, source_lang: str = DEFAULT_SOURCE_LANGUAGE,
              default: Any = "") -> Any:
    """
    Pick the text for ``lang`` from a multilingual value.

    ``value`` may be a dict keyed by language code or a plain value, which is
    returned unchanged (configurations may give a single string for all
    languages).
    """
    if not isinstance(value, dict):
        return default if value is None else value
    for code in fallback_chain(lang, source_lang):
        text = value.get(code)
        if text not in (None, "", [], {}):
            return text
    for text in value.values():
        if text not in (None, "", [], {}):
            return text
    return default


@lru_cache(maxsize=None)
def _catalog(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def available_catalogs() -> List[str]:
    return sorted(p.stem for p in LOCALES_DIR.glob("*.yaml"))


def text(key: str, lang: str, **fmt) -> str:
    """
    Fixed text from the catalogs in ``locales/``, e.g. ``text("report.title", "it")``.

    Falls back to English when the language has no catalog or lacks the key.
    A missing key in English as well is a programming error and raises.
    """
    for code in (lang, FALLBACK_LANGUAGE):
        node: Any = _catalog(code)
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node.format(**fmt) if fmt else node
    raise KeyError(f"text key '{key}' missing in locales/{FALLBACK_LANGUAGE}.yaml")


def catalog_value(key: str, lang: str) -> Any:
    """
    Any catalog entry (string, list, mapping) in ``lang``; English if the
    language lacks it. Raises KeyError if English lacks it too.
    """
    for code in (lang, FALLBACK_LANGUAGE):
        node: Any = _catalog(code)
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is not None:
            return node
    raise KeyError(f"catalog key '{key}' missing in locales/{FALLBACK_LANGUAGE}.yaml")


def catalog_texts(key: str) -> dict:
    """{lang: entry} for every catalog language (multilingual label of a fixed text)."""
    return {code: catalog_value(key, code) for code in available_catalogs()}


def validate_language_settings(config: Optional[dict]) -> List[str]:
    """
    Check the language settings; returns warnings, raises on errors.

    Errors: unsupported source language. Warnings: output languages without
    a text catalog (fixed texts will be English there).
    """
    get_source_language(config)
    warnings = []
    catalogs = set(available_catalogs())
    for code in get_output_languages(config):
        if code not in LANGUAGE_NAMES:
            warnings.append(f"Unknown language code '{code}' in languages")
        elif code not in catalogs:
            warnings.append(
                f"No text catalog for '{code}': fixed texts in reports and "
                f"dashboard will be English"
            )
    return warnings


def ordered(langs: Iterable[str], first: str) -> List[str]:
    """``langs`` with ``first`` moved to the front (if present)."""
    langs = list(langs)
    return ([first] if first in langs else []) + [c for c in langs if c != first]
