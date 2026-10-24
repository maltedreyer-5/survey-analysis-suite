# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: create a new survey.

A survey is a directory under ``surveys/`` with a ``survey.yaml`` and a
``questions.yaml``. ``create_survey`` writes both with the title and the languages;
the questions come from the import afterwards (from the structure file of the survey
tool, or inferred from the data) and are refined in the Configuration tab and the
Config assistant.
"""
import re
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

SURVEY_ID = re.compile(r"^[a-z][a-z0-9_]{1,62}$")

QUESTIONS_HEADER = """# Configuration of the survey (see docs/configuration.md).
# The questions are created by the first import: from the structure file of the
# survey tool (LimeSurvey .lss, Qualtrics .qsf, SPSS .sav), or inferred from the data.
"""


def create_survey(survey_id: str, title: str, source_language: str,
                  languages: Optional[List[str]] = None, surveys_dir: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Creates ``surveys/<survey_id>/`` with ``survey.yaml`` and ``questions.yaml`` and
    registers it. Returns ``(ok, message)``; nothing is written if a check fails.
    """
    from utils.languages import SUPPORTED_SOURCE_LANGUAGES
    import core
    survey_id = (survey_id or "").strip()
    title = (title or "").strip()
    if not SURVEY_ID.match(survey_id):
        return False, ("❌ The id must start with a lower-case letter and contain only lower-case letters, "
                       "digits and underscores (2 to 63 characters), e.g. staff_survey_2026.")
    if not title:
        return False, "❌ Give the survey a title."
    if source_language not in SUPPORTED_SOURCE_LANGUAGES:
        return False, f"❌ The source language must be one of {', '.join(SUPPORTED_SOURCE_LANGUAGES)}."
    registry = core.get_registry()
    if survey_id in registry.survey_ids():
        return False, f"❌ A survey with the id '{survey_id}' exists already."
    base = Path(surveys_dir) if surveys_dir else Path(core.SURVEYS_DIR)
    directory = base / survey_id
    if directory.exists():
        return False, f"❌ The directory {directory} exists already."
    langs = [source_language] + [l for l in (languages or []) if l and l != source_language]

    directory.mkdir(parents=True)
    survey = {"id": survey_id, "title": {source_language: title}, "description": {source_language: ""},
              "version": "1.0", "languages": langs}
    (directory / "survey.yaml").write_text(yaml.safe_dump(survey, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
    questions = {"settings": {"survey_metadata": {"survey_title": {source_language: title},
                                                  "source_language": source_language, "languages": langs}},
                 "question_groups": {}}
    (directory / "questions.yaml").write_text(
        QUESTIONS_HEADER + yaml.safe_dump(questions, allow_unicode=True, sort_keys=False), encoding="utf-8")
    registry.discover()
    if survey_id not in registry.survey_ids():
        return False, f"❌ The survey was written to {directory} but could not be registered."
    return True, (f"✅ Survey '{survey_id}' created in {directory}.\n"
                  f"Next: import the answers in the Import tab, together with the structure file of the "
                  f"survey tool if you have one.")
