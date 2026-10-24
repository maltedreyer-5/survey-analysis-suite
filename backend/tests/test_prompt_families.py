# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Each prompt family uses the catalog of the survey's source language and
names the answer language. The source language comes from the loaded survey
(core.state.config); a stand-in module provides it here.
"""
import json
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture
def survey(monkeypatch):
    """Set the loaded survey; returns a function taking the source language."""
    def load(lang, **settings):
        state = types.SimpleNamespace(config={"settings": {
            "survey_metadata": {"source_language": lang, "languages": [lang]}, **settings}}, db=None)
        monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=state))
        return state
    return load


class Recorder:
    """LLM stand-in: records prompts, answers with a fixed text."""
    def __init__(self, answer="{}"):
        self.answer = answer
        self.calls = []

    def complete(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt or kw.get("system") or "", prompt))
        return self.answer

    def complete_json(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt or "", prompt))
        return json.loads(self.answer)


@pytest.mark.parametrize("lang,marker,foreign", [("de", "Sprache der Antwort", "language of the answer"),
                                                 ("en", "language of the answer", "Sprache der Antwort")])
def test_item_extraction(survey, lang, marker, foreign):
    from utils.item_extractor import LLMExtractor
    survey(lang)
    llm = Recorder('{"items": ["a", "b"], "confidence": 0.9}')
    ext = LLMExtractor(llm, question_context="Q?")
    ext.extract_sync("a und b", thematic=False)
    ext.extract_sync("a und b", thematic=True)
    for system, user in llm.calls:
        assert marker in system and foreign not in system
        assert "Q?" in user


class ClusterLLM(Recorder):
    """Answers clustering prompts with fixed clusters and translation prompts with '[lang] text'."""
    def complete(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt or "", prompt))
        if prompt.rstrip().endswith("]") and ("Zielsprache:" in prompt or "into " in prompt):
            texts = json.loads(prompt[prompt.rindex("\n[") + 1:])
            target = prompt.split("Zielsprache: ")[1].split(".")[0] if "Zielsprache:" in prompt \
                else prompt.split(" into ")[1].split(".")[0]
            return json.dumps([f"[{target}] {t}" for t in texts], ensure_ascii=False)
        return json.dumps({"clusters": [
            {"name": "Öffnungszeiten", "description": "Wünsche zu Zeiten", "answer_ids": [1, 2, 3],
             "examples": ["länger offen"]},
            {"name": "Preise", "description": "Kosten", "answer_ids": [4, 5, 6], "examples": ["zu teuer"]},
        ]}, ensure_ascii=False)

    def complete_json(self, prompt, system_prompt=None, **kw):
        return json.loads(self.complete(prompt, system_prompt, **kw))


@pytest.mark.parametrize("lang,other,answer_lang", [("de", "en", "auf Deutsch"), ("en", "de", "in English")])
def test_clustering_source_language_and_translation(survey, lang, other, answer_lang):
    from utils.llm import LLMClient, LLMConfig
    state = survey(lang)
    state.config["settings"]["survey_metadata"]["languages"] = [lang, other, "it"]
    state.config["settings"]["llm_context"] = "Campus"
    llm = ClusterLLM()
    client = LLMClient(LLMConfig(base_url="http://llm.test/v1", model="m"))
    client.complete = llm.complete
    client._detect_example_language = lambda text: lang
    responses = [{"id": i, "text": f"Antwort {i}"} for i in range(1, 7)]
    result = client.cluster_responses("Was fehlt?", responses, max_clusters=5)
    names = [c["name"] for c in result["clusters"]]
    assert names[0][lang] == "Öffnungszeiten"
    assert set(names[0]) == {lang, other, "it"}
    assert names[0]["it"].startswith("[")          # through the translation path
    system, user = llm.calls[0]
    assert answer_lang in system and answer_lang in user
    # names of a question are translated together, with the question as context
    name_calls = [u for s, u in llm.calls if "Öffnungszeiten" in u and "Preise" in u and u.rstrip().endswith("]")]
    assert name_calls and all("Was fehlt?" in u for u in name_calls)


def test_clustering_failure_uses_fixed_texts(survey):
    from utils.llm import LLMClient, LLMConfig
    survey("en").config["settings"]["survey_metadata"]["languages"] = ["en", "it"]
    client = LLMClient(LLMConfig(base_url="http://llm.test/v1", model="m"))

    def broken(*a, **kw):
        raise RuntimeError("down")
    client.complete_json = broken
    result = client.cluster_responses("Q?", [{"id": 1, "text": "x"}])
    assert result["clusters"][0]["name"] == {"en": "All responses", "it": "Tutte le risposte"}


@pytest.mark.parametrize("lang,marker,label", [("de", "Deutsch", "Mensa-Angebot"), ("en", "English", "Canteen offer")])
def test_themes_prompts_from_catalog_with_context(survey, lang, marker, label, tmp_path):
    """Theme keywords and option matching: catalog of the source language, survey context, source labels."""
    from themes.theme_analyzer import ThemeAnalyzer
    state = survey(lang, llm_context="Campus services")
    state.df = None
    state.config["question_groups"] = {"q": {"handler": "single_choice", "columns": ["q"],
                                             "label": {lang: label, "fr": "Offre"},
                                             "options": {lang: ["gut", "schlecht"], "fr": ["bon", "mauvais"]}}}
    llm = Recorder('["x"]')
    analyzer = ThemeAnalyzer(config=state.config, llm_client=llm, config_dir=tmp_path)
    analyzer.add_theme("t1", "Essen", label_en="Food", keywords=["Mensa"])
    analyzer._extract_keywords_llm(["Wie gefällt Ihnen die Mensa?"])
    analyzer.extract_keywords_for_theme("t1", use_llm=True)
    analyzer.deep_analyze_theme("t1")
    assert len(llm.calls) >= 3
    for system, user in llm.calls[:2]:
        assert "Campus services" in system + user and "Hochschul" not in system + user
    assert all(marker in s + u for s, u in llm.calls[:2])          # answer language named
    assert ("Food" if lang == "en" else "Essen") in llm.calls[1][1]  # theme label in the source language
    option_prompt = llm.calls[-1][1]
    assert label in option_prompt and "gut" in option_prompt and "bon" not in option_prompt


@pytest.mark.parametrize("lang,marker,label", [("de", "Deutsch", "Land"), ("en", "English", "Country")])
def test_config_assistant_catalog_and_labels(survey, lang, marker, label, monkeypatch):
    """Assistant prompts in the source language; labels read in that language; 'label' answers accepted."""
    import services.config_assistant as ca
    state = survey(lang)
    state.df = None
    state.config["question_groups"] = {"country": {"handler": "single_choice", "columns": ["c"],
                                                   "label": {lang: label, "fr": "Pays"},
                                                   "options": {lang: ["A", "B", "C"], "fr": ["a", "b", "c"]}}}
    llm = Recorder(json.dumps([{"id": "country", "label": label, "source_question": "country",
                                "bucketing": "identity", "confidence": 0.9}]))
    monkeypatch.setattr(ca, "_get_llm", lambda: (llm, True))
    suggestions = ca.suggest_segmentation_dimensions(state)
    system, user = llm.calls[-1]
    assert marker in user and label in user and "Pays" not in user
    assert ("3 Optionen" if lang == "de" else "3 options") in user       # options per language counted once
    assert suggestions[0].new_value["label"] == {lang: label}
    llm.answer = "Kontext."
    ca.suggest_llm_context(state)
    assert marker in llm.calls[-1][1]


@pytest.mark.parametrize("lang,marker", [("de", "Antworten:"), ("en", "Answers:")])
def test_cluster_examples_catalog_of_source_language(survey, lang, marker):
    """Example selection: prompt of the source language whatever the report language; only shown numbers count."""
    from utils.cluster_examples import ClusterExampleSelector
    survey(lang, llm_context="Campus services")
    llm = Recorder("[2, 60, 1]")
    answers = [f"answer {i}" for i in range(70)]
    picked = ClusterExampleSelector(llm=llm).select_best_examples({lang: "Parking", "fr": "Parking FR"}, "",
                                                                   answers, n_examples=3, lang="fr")
    system, user = llm.calls[0]
    assert marker in user and "Campus services" in user and "Parking FR" not in user
    assert picked == ["answer 1", "answer 0"]          # 60 was never shown to the LLM


def test_schema_suggestion_takes_effect(survey, monkeypatch):
    """Regression: an accepted schema suggestion is written where the resolver reads it, with built-in sections."""
    import services.config_assistant as ca
    from utils.summary_schema import SchemaResolver
    state = survey("de")
    state.df = None
    state.config["question_groups"] = {"q": {"handler": "single_choice", "columns": ["q"], "label": {"de": "Frage"}}}
    llm = Recorder(json.dumps({"q": {"schema_id": "minimal", "sections": ["description", "erfunden"],
                                     "reason": "einfach", "confidence": 0.9}}))
    monkeypatch.setattr(ca, "_get_llm", lambda: (llm, True))
    suggestions = ca.suggest_schema_overrides(state)
    assert "minimal" not in llm.calls[-1][1] and "segments_text" in llm.calls[-1][1]
    assert suggestions[0].target == "question_groups.q.summary_schema_override"
    config = ca.apply_suggestions(state.config, suggestions)
    schema = SchemaResolver(config).resolve("q", "single_choice")
    assert [s.id for s in schema.sections] == ["description"]            # unknown id dropped
    assert schema.sections[0].prompt_instruction["de"]                   # built-in instruction filled in
    assert ca._is_configured(config["question_groups"]["q"], "schema")
