# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""JSON answers of the language model are read in the form they have (a list stays a list),
and the configuration assistant suggests dimensions and numeric settings."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.llm import LLMClient  # noqa: E402


def client_answering(text):
    c = LLMClient.__new__(LLMClient)
    c.complete = lambda prompt, system_prompt=None, **kw: text
    return c


@pytest.mark.parametrize("answer,expected", [
    ('[{"id": "a"}, {"id": "b"}]', [{"id": "a"}, {"id": "b"}]),
    ('[{"id": "a"}]', [{"id": "a"}]),                                   # one object in a list: still a list
    ('```json\n[{"id": "a"}]\n```', [{"id": "a"}]),
    ('Here you are: [{"id": "a"}] Hope it helps.', [{"id": "a"}]),
    ('{"a": [1, 2]}', {"a": [1, 2]}),
    ('Answer: {"a": [1, 2]} done', {"a": [1, 2]}),
    ('[]', []),
])
def test_json_in_its_own_form(answer, expected):
    assert client_answering(answer).complete_json("p") == expected


def _state(question_groups):
    return SimpleNamespace(config={"settings": {"survey_metadata": {"source_language": "en"}},
                                   "question_groups": question_groups}, df=None)


def test_assistant_suggests_dimensions_from_a_list(monkeypatch):
    from services import config_assistant as ca
    answer = json.dumps([{"id": "role_group", "label": "Role", "source_question": "role", "bucketing": "identity",
                          "reason": "few values", "confidence": 0.9}])
    monkeypatch.setattr(ca, "_get_llm", lambda: (client_answering(answer), True))
    qg = {"role": {"handler": "single_choice", "label": {"en": "Your role"}, "columns": ["role"],
                   "options": {"en": ["Student", "Staff"]}}}
    out = ca.suggest_segmentation_dimensions(_state(qg))
    assert [s.new_value["source_question"] for s in out] == ["role"]


def test_numeric_questions_without_settings_are_candidates(monkeypatch):
    from services import config_assistant as ca
    seen = {}
    def fake(prompt, system_prompt=None, **kw):
        seen["prompt"] = prompt
        return json.dumps({"age": {"unit": "years", "valid_range": {"min": 16, "max": 99}, "decimals": 0,
                                   "reason": "an age", "confidence": 0.9}})
    c = client_answering("")
    c.complete = fake
    monkeypatch.setattr(ca, "_get_llm", lambda: (c, True))
    qg = {"age": {"handler": "numeric", "label": {"en": "Your age"}, "columns": ["age"]},
          "done": {"handler": "numeric", "label": {"en": "Done"}, "columns": ["d"], "numeric": {"unit": "x"}}}
    out = ca.suggest_numeric_fields(_state(qg))
    assert "- age:" in seen["prompt"] and "- done:" not in seen["prompt"]
    assert [s.target for s in out] and "age" in out[0].target
