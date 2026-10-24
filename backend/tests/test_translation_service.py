# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Single translation path: context package, batching, memory, failure handling."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.persistence import SurveyDatabase  # noqa: E402
from utils.translation_service import TranslationContext, TranslationService  # noqa: E402

CONFIG = {"settings": {
    "survey_metadata": {"survey_title": {"de": "Campus-Umfrage"}, "source_language": "de"},
    "llm_context": "Zufriedenheit mit Campus-Diensten",
    "glossary": {"Mensa": {"en": "cafeteria", "it": "mensa"}},
    "keep_terms": ["Moodle"],
}}


class FakeLLM:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def complete(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt, prompt))
        if self.answer is not None:
            return self.answer
        texts = json.loads(prompt[prompt.index("["):prompt.rindex("]") + 1])
        return "```json\n" + json.dumps([f"<{t}>" for t in texts], ensure_ascii=False) + "\n```"


def service(llm, db=None, config=CONFIG):
    return TranslationService(llm, config, db)


def test_context_package_reaches_the_prompt_and_options_go_in_one_call():
    llm = FakeLLM()
    ctx = TranslationContext.from_config(CONFIG, kind="option", question_text="Wie oft nutzen Sie die Mensa?",
                                         scope="q_mensa")
    out = service(llm).translate_many(["täglich", "nie", "", "täglich"], "en", ctx)
    assert out == ["<täglich>", "<nie>", "", "<täglich>"]
    assert len(llm.calls) == 1  # one call, duplicates sent once
    system, user = llm.calls[0]
    for expected in ("Campus-Umfrage", "Zufriedenheit mit Campus-Diensten", "Antwortoptionen einer Frage",
                     "Wie oft nutzen Sie die Mensa?", "Mensa → cafeteria", "Moodle", "Englisch"):
        assert expected in system + user, expected


def test_memory_with_scope_and_manual_corrections(tmp_path):
    db = SurveyDatabase(tmp_path / "t.db")
    db.save_translation("Betrieb", "de", "en", "company (checked)", scope="q1", is_manual=True)
    llm = FakeLLM()
    svc = service(llm, db)
    assert svc.translate("Betrieb", "en", TranslationContext(scope="q1")) == "company (checked)"
    assert llm.calls == []
    assert svc.translate("Betrieb", "en", TranslationContext(scope="q2")) == "<Betrieb>"
    assert db.get_translation("Betrieb", "de", "en", scope="q2") == "<Betrieb>"


def test_source_language_needs_no_translation_and_english_catalog_is_used():
    llm = FakeLLM()
    en_cfg = {"settings": {"survey_metadata": {"source_language": "en"}}}
    svc = service(llm, config=en_cfg)
    assert svc.translate("Daily", "en") == "Daily" and llm.calls == []
    svc.translate("Daily", "it")
    system, user = llm.calls[0]
    assert "into Italian" in system and "Italian" in user and "Zielsprache" not in system


@pytest.mark.parametrize("answer", ["not json", '["only one"]', '{"a": 1}'])
def test_unusable_answers_leave_texts_untranslated_and_unstored(tmp_path, answer):
    db = SurveyDatabase(tmp_path / "t.db")
    out = service(FakeLLM(answer), db).translate_many(["eins", "zwei"], "en", TranslationContext(scope="q"))
    assert out == ["eins", "zwei"]
    assert db.get_translation("eins", "de", "en", scope="q") is None


def test_unknown_source_language_is_left_to_the_llm_and_status_reported(tmp_path):
    """Answers of uncertain language: one path, the prompt says the language is unknown, no glossary."""
    from utils.translation_service import AUTO_SOURCE
    llm = FakeLLM()
    text, ok = service(llm).translate_with_status("Mensa trop chère", "de", source_lang=AUTO_SOURCE)
    assert ok and text == "<Mensa trop chère>"
    system, user = llm.calls[0]
    assert "unbekannt" in user and "Mensa → cafeteria" not in user
    failing = FakeLLM(answer="kaputt")
    text, ok = service(failing).translate_with_status("Mensa trop chère", "de", source_lang=AUTO_SOURCE)
    assert not ok and text == "Mensa trop chère"


def test_response_translator_auto_path_uses_the_service(monkeypatch):
    import types
    from utils.response_translator import ResponseTranslator
    monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(config=CONFIG)))
    llm = FakeLLM()
    translator = ResponseTranslator(llm=llm)
    assert translator.translate_text("Mensa trop chère", "uncertain", "de") == ("<Mensa trop chère>", True)
    assert "unbekannt" in llm.calls[0][1] and "Campus-Umfrage" in llm.calls[0][1]
    assert translator.translate_text("Mensa trop chère", "uncertain", "de") == ("<Mensa trop chère>", True)
    assert len(llm.calls) == 1                                     # cached
    broken = ResponseTranslator(llm=FakeLLM(answer="kaputt"))
    assert broken.translate_text("Mensa trop chère", "auto", "de") == ("Mensa trop chère", False)
