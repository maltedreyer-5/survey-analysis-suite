# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Labels of the configuration are translated into the output languages, each list as a
whole with its context; labels written in the configuration are never overwritten."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.config_translation import fill_config_labels  # noqa: E402
from utils.scales import expand_named_scales  # noqa: E402


class StubService:
    def __init__(self, memory=None):
        self.memory = dict(memory or {})
        self.calls = []

    def _memory_get(self, text, src, lang, scope):
        return self.memory.get((text, lang, scope))

    def translate_many_with_status(self, texts, lang, context):
        self.calls.append((list(texts), lang, context))
        out = [f"[{lang}] {t}" for t in texts]
        for t, o in zip(texts, out):
            self.memory[(t, lang, context.scope)] = o
        return out, [True] * len(texts)


def config():
    return expand_named_scales({
        "settings": {"survey_metadata": {"source_language": "de", "languages": ["de", "en", "it"]},
                     "scales": {"sat": {"context": "Zufriedenheit, von hoch nach niedrig", "direction": "descending",
                                        "points": [{"label": "Sehr zufrieden"}, {"label": "Unzufrieden"}]}},
                     "segmentation": {"dimensions": [{"id": "role", "label": {"de": "Rolle"}, "source_question": "role",
                                                       "bucketing": {"type": "value_mapping",
                                                                     "mapping": {"Studium": "Studierende"}}}]}},
        "question_groups": {
            "role": {"handler": "single_choice", "columns": ["role"], "label": {"de": "Ihre Rolle", "en": "Your role"},
                     "options": {"de": ["Studium", "Lehre"], "en": ["Study", "Teaching"]}},
            "q1": {"handler": "single_choice", "columns": ["q1"], "label": {"de": "Zufrieden?"}, "scale": "sat"},
            "m1": {"handler": "matrix_likert", "columns": ["m1_a"], "label": {"de": "Wie zufrieden sind Sie mit"},
                   "row_labels": {"de": ["der Mensa"]}, "scale": "sat"}}})


def test_lists_are_translated_as_a_whole_with_their_context():
    svc = StubService()
    out, stats = fill_config_labels(config(), service=svc, use_llm=True)
    calls = {(c[2].scope, c[2].kind, c[1]): c for c in svc.calls}
    scale = calls[("scale:sat", "scale", "it")]
    assert scale[0] == ["Sehr zufrieden", "Unzufrieden"]              # all points in one request
    assert "Zufriedenheit, von hoch nach niedrig" in scale[2].notes and "2" in scale[2].notes
    rows = calls[("m1", "option", "it")]
    assert rows[2].question_text == "Wie zufrieden sind Sie mit"       # the question goes with its rows
    q = out["question_groups"]
    assert q["q1"]["options"]["it"] == q["m1"]["scale"]["it"] == ["[it] Sehr zufrieden", "[it] Unzufrieden"]
    assert not any(c[2].scope in ("q1", "m1") and c[2].kind == "scale" for c in svc.calls)  # the named scale once
    assert out["settings"]["segmentation"]["dimensions"][0]["value_labels"]["Studierende"]["en"] == "[en] Studierende"
    assert stats["translated"] > 0 and stats["missing"] == 0


def test_labels_of_the_configuration_are_kept():
    svc = StubService()
    out, _ = fill_config_labels(config(), service=svc, use_llm=True)
    role = out["question_groups"]["role"]
    assert role["options"]["en"] == ["Study", "Teaching"] and role["label"]["en"] == "Your role"
    assert role["options"]["it"] == ["[it] Studium", "[it] Lehre"]
    assert not any(c[2].scope == "role" and c[1] == "en" for c in svc.calls)


def test_loading_fills_from_the_memory_only():
    first = StubService()
    fill_config_labels(config(), service=first, use_llm=True)
    again = StubService(first.memory)
    out, stats = fill_config_labels(config(), service=again, use_llm=False)
    assert again.calls == [] and stats["filled"] > 0 and stats["missing"] == 0
    assert out["question_groups"]["q1"]["options"]["en"] == ["[en] Sehr zufrieden", "[en] Unzufrieden"]
    empty = StubService()
    out2, stats2 = fill_config_labels(config(), service=empty, use_llm=False)
    assert empty.calls == [] and stats2["missing"] > 0 and "it" not in out2["question_groups"]["q1"]["options"]
