# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""A new theme has one label in the source language; the other output languages are
translated (no fixed set of languages)."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import themes  # noqa: E402


class Service:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def translate_many_with_status(self, texts, lang, context):
        self.calls.append((lang, context.scope))
        return [f"[{lang}] {t}" for t in texts], [self.ok] * len(texts)


def state(langs):
    return SimpleNamespace(config={"settings": {"survey_metadata": {"source_language": "en", "languages": langs}}},
                           db=None, project_id=None, themes={})


def test_labels_in_every_output_language(monkeypatch):
    svc = Service()
    monkeypatch.setattr("utils.translation_service.get_translation_service", lambda llm_client=None: svc)
    labels = themes.theme_labels(state(["en", "de", "it"]), "privacy", "Data protection")
    assert labels == {"en": "Data protection", "de": "[de] Data protection", "it": "[it] Data protection"}
    assert svc.calls == [("de", "theme:privacy"), ("it", "theme:privacy")]


def test_without_translation_the_source_label_stays(monkeypatch):
    monkeypatch.setattr("utils.translation_service.get_translation_service", lambda llm_client=None: Service(ok=False))
    assert themes.theme_labels(state(["en", "fr"]), "t", "Costs") == {"en": "Costs"}


def test_add_theme_stores_the_labels(monkeypatch):
    monkeypatch.setattr("utils.translation_service.get_translation_service", lambda llm_client=None: Service())
    from themes.theme_analyzer import ThemeAnalyzer
    analyzer = ThemeAnalyzer.__new__(ThemeAnalyzer)
    analyzer.themes = {}
    st = state(["en", "it"])
    ok, status, _, choices = themes.add_theme(st, analyzer, "costs", "Costs")
    assert ok and "costs" in choices
    assert analyzer.themes["costs"].label == {"en": "Costs", "it": "[it] Costs"}
    ok, status, _, _ = themes.add_theme(st, analyzer, "", "x")
    assert not ok and status == "⚠️ Give an ID and a label"
