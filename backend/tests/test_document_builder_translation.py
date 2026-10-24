# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Document builder: translations through the single translation path, source language configurable."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.document_builder import DocumentBuilder  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402


class FakeLLM:
    def __init__(self, broken=False):
        self.calls, self.broken = [], broken

    def complete(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt or "", prompt))
        if self.broken:
            return "kaputt"
        texts = json.loads(prompt[prompt.index("\n[") + 1:prompt.rindex("]") + 1])
        return json.dumps([f"<{t}>" for t in texts], ensure_ascii=False)


@pytest.fixture
def builder(tmp_path, monkeypatch):
    def make(source_language="de", llm=None, survey_lang="de"):
        cfg = tmp_path / "documents.yaml"
        cfg.write_text(f'source_dir: "docs"\nsource_language: "{source_language}"\ndocuments: {{}}\n', encoding="utf-8")
        db = tmp_path / "s.db"
        SurveyDatabase(db)
        config = {"settings": {"survey_metadata": {"source_language": survey_lang, "survey_title": {survey_lang: "Campus"}},
                               "llm_context": "Campus-Dienste"}}
        monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(config=config)))
        import utils.llm as llm_mod
        monkeypatch.setattr(llm_mod, "get_llm_client", lambda *a, **k: llm)
        return DocumentBuilder(config_path=str(cfg), db_path=str(db))
    return make


def test_paragraph_table_heading_through_the_service_with_kinds(builder, monkeypatch):
    import utils.llm as llm_mod
    llm = FakeLLM()
    b = builder(llm=llm)
    assert b._translate_paragraph("Die Mensa ist gut.", "en", "d", "f.md", 0) == "<Die Mensa ist gut.>"
    table = "| A | B |\n| --- | --- |\n| x | y |"
    llm_table = FakeLLM()
    llm_table.complete = lambda prompt, system_prompt=None, **kw: (llm.calls.append((system_prompt, prompt))
                                                                   or json.dumps([table.replace("x", "z")]))
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda *a, **k: llm_table)
    assert b._translate_table(table, "en", "d", "f.md", 1) == table.replace("x", "z")
    monkeypatch.setattr(llm_mod, "get_llm_client", lambda *a, **k: llm)
    assert b._translate_heading("Methoden", "en", "d", "f.md", 2) == "<Methoden>"
    users = [u for _, u in llm.calls]
    assert "Absatz aus einer Dokumentation" in users[0] and "Campus-Dienste" in users[0]
    assert "Markdown-Tabelle" in users[1] and "Kapitelüberschrift" in users[2]
    # cached per position: no new call
    n = len(llm.calls)
    assert b._translate_paragraph("Die Mensa ist gut.", "en", "d", "f.md", 0) == "<Die Mensa ist gut.>"
    assert len(llm.calls) == n


def test_failure_keeps_original_and_is_not_cached(builder):
    b = builder(llm=FakeLLM(broken=True))
    assert b._translate_paragraph("Die Mensa ist gut.", "en", "d", "f.md", 0) == "Die Mensa ist gut."
    assert b._get_cached_translation("d", "f.md", 0, "en", b._text_hash("Die Mensa ist gut.")) is None


def test_source_language_from_configuration(builder):
    llm = FakeLLM()
    b = builder(source_language="en", llm=llm, survey_lang="en")
    assert b.source_language == "en"
    b._translate_paragraph("The canteen is good.", "de", "d", "f.md", 0)
    assert "Source language: English" in llm.calls[0][1] or "English" in llm.calls[0][1]
    assert b._translate_paragraph("Same language.", "en", "d", "f.md", 1) == "Same language."
