# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Texts of generated reports: title rule and clusters in the report language."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document  # noqa: E402
from export.pipeline import report_title  # noqa: E402
from utils.export_word import WordExporter  # noqa: E402


def state_with(titles):
    return SimpleNamespace(config={"settings": {"survey_metadata": {"survey_title": titles}}})


def test_report_title_rule():
    st = state_with({"de": "Studierendenbefragung", "en": "Student survey"})
    assert report_title(st, "en", "  My title ") == "My title"          # entered title wins
    assert report_title(st, "en") == "Student survey"                   # survey title in the language
    assert report_title(st, "it") in ("Studierendenbefragung", "Student survey")   # source language (fallback)
    empty = state_with({})
    assert [report_title(empty, lang) for lang in ("de", "en", "fr", "it")] == \
        ["Umfrage-Auswertung", "Survey report", "Rapport d'enquête", "Rapporto dell'indagine"]


def test_segment_cluster_details_are_in_the_report_language(tmp_path):
    doc = Document()
    result = SimpleNamespace(n=12, metadata={"cluster_details": [
        {"name": {"en": "Costs", "de": "Kosten"}, "description": {"en": "Too expensive", "de": "Zu teuer"}, "count": 5}]})
    WordExporter(tmp_path)._add_segment_theme_details(doc, "role", {"student": {"result": result, "n": 12}}, "en")
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Costs" in text and "Too expensive" in text
    assert "{'" not in text and "Kosten" not in text
