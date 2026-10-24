# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Re-clustering with given categories (workbench): prompt from the catalog, catch-all category."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import services.workbench_store as ws  # noqa: E402
import utils.llm as llm_module  # noqa: E402


class FakeLLM:
    def __init__(self, assignments):
        self.assignments = assignments
        self.prompts = []

    def complete(self, prompt, system_prompt=None, **kw):
        self.prompts.append(prompt)
        return json.dumps({"assignments": self.assignments, "new_categories": []})


def _run(monkeypatch, assignments, allow_new):
    items = [{"id": 1, "extracted_text": "Mensa zu teuer"}, {"id": 2, "extracted_text": "WLAN fällt aus"}]
    monkeypatch.setattr(ws, "get_items_for_question", lambda qid, page_size=500: (items, len(items)))
    fake = FakeLLM(assignments)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda config=None: fake)
    applied = {}
    monkeypatch.setattr(ws, "apply_recluster_result",
                        lambda q, a, new, existing: applied.update(new=new, existing=existing))
    result = ws.recluster_with_categories("q1", ["Essen"], allow_new_categories=allow_new)
    return result, applied, fake.prompts[0]


def test_recluster_runs_and_uses_catalog(monkeypatch):
    result, applied, prompt = _run(monkeypatch, [{"item_id": 1, "category": "Essen"},
                                                 {"item_id": 2, "category": "Netz"}], allow_new=True)
    assert result["stats"] == {"Essen": 1, "Netz": 1}
    assert "Mensa zu teuer" in prompt and "1. Essen" in prompt
    assert "auf Deutsch" in prompt


def test_catch_all_category_is_created(monkeypatch):
    result, applied, prompt = _run(monkeypatch, [{"item_id": 1, "category": "Essen"},
                                                 {"item_id": 2, "category": "Sonstiges"}], allow_new=False)
    assert "„Sonstiges“" in prompt
    assert applied["new"] == ["Sonstiges"]
