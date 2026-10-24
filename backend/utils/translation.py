# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Translation helpers for generated output: labels of segment values, of common
answers ("Yes", "Other", "Don't know" …) and the fixed texts of the structured Word
report. All texts come from the catalogs locales/*.yaml (common_label, report_doc) and
the survey configuration; there is no separate translation table.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _common_label_index() -> dict:
    """Label text in any catalog language -> concept id of locales/*.yaml common_label (first wins)."""
    global _COMMON_INDEX
    if _COMMON_INDEX is None:
        from .languages import _catalog, available_catalogs
        index = {}
        # German first: ambiguous texts ("Autres", "Other") resolve as in the former table
        for code in sorted(available_catalogs(), key=lambda c: (c != "de", c)):
            for concept, label in (_catalog(code).get("common_label") or {}).items():
                index.setdefault(label, concept)
        _COMMON_INDEX = index
    return _COMMON_INDEX


_COMMON_INDEX = None


def _pick(texts: dict, lang: str, default):
    """Text in ``lang``, else English, else German, else ``default``."""
    for code in (lang, "en", "de"):
        value = texts.get(code)
        if value:
            return value
    return default


class TranslationManager:
    """Labels of segment values and common answers in an output language."""

    def get_segment_label(self, segment_type: str, value: str, lang: str = "de") -> str:
        """
        Label of a segment value in ``lang``, from the segment configuration
        (value_labels, bucket labels, answer options). Unknown dimensions or
        values are returned unchanged.
        """
        from export.segment_context import get_context  # lazy: needs the app state
        try:
            ctx = get_context()
        except Exception:
            return str(value)
        if segment_type not in ctx.dimension_ids():
            return str(value)
        labels = ctx.value_labels(segment_type, str(value))
        return labels.get(lang) or str(value)

    def translate_common_label(self, label: str, lang: str) -> str:
        """A common answer label (locales common_label, any catalog language) in ``lang``; else unchanged."""
        concept = _common_label_index().get(label)
        if concept:
            from .languages import text
            return text(f"common_label.{concept}", lang)
        return label

    def get_category_translation(self, category: str, lang: str, scale_name: str = None) -> str:
        """An answer category in ``lang``: a common answer from the catalogs, else unchanged
        (the configured options and scales are looked up by the caller first)."""
        return self.translate_common_label(category, lang)


_translation_manager: Optional[TranslationManager] = None


def get_translation_manager() -> TranslationManager:
    """The shared TranslationManager."""
    global _translation_manager
    if _translation_manager is None:
        _translation_manager = TranslationManager()
    return _translation_manager


def t_report(key: str, lang: str = "de") -> str:
    """Fixed text of the structured Word report (locales report_doc; dots in key become '_')."""
    from .languages import text
    return text("report_doc." + key.replace(".", "_"), lang)
