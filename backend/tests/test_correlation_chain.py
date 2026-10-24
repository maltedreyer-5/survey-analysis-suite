# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Correlations from data to text (utils/correlation.py): selection, facts,
texts in the source language, translation, stored texts, export contract.
"""
import json
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from utils.analysis_texts import AnalysisTextStore  # noqa: E402
from utils.correlation import CorrelationAnalyzer, finding_facts  # noqa: E402
from utils.persistence import SurveyDatabase  # noqa: E402

RNG = np.random.default_rng(7)


def survey(lang="de"):
    n = 240
    size = RNG.choice(["klein", "groß"], n)
    # option "Cloud" chosen much more often by large institutions; "Papier" unrelated
    cloud = np.where(RNG.random(n) < np.where(size == "groß", 0.7, 0.2), "x", None)
    paper = np.where(RNG.random(n) < 0.4, "x", None)
    noise = RNG.choice(["a", "b", "c"], n)
    df = pd.DataFrame({"size": size, "cloud": cloud, "paper": paper, "noise": noise})
    df.loc[df[["cloud", "paper"]].isna().all(axis=1), "paper"] = "x"
    labels = {"de": ("Größe", "Werkzeuge", "Zufall"), "en": ("Size", "Tools", "Random")}[lang]
    cfg = {"settings": {"survey_metadata": {"source_language": lang, "languages": [lang, "fr"]},
                        "llm_context": "Campus"},
           "question_groups": {
               "size": {"handler": "single_choice", "columns": ["size"], "label": {lang: labels[0]}},
               "tools": {"handler": "multi_choice", "columns": ["cloud", "paper"], "label": {lang: labels[1]},
                         "item_labels": {lang: ["Cloud", "Papier"]}},
               "noise": {"handler": "single_choice", "columns": ["noise"], "label": {lang: labels[2]}}}}
    return cfg, df


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, prompt, system_prompt=None, **kw):
        self.calls.append((system_prompt or "", prompt))
        if prompt.rstrip().endswith("]"):                       # translation batch
            texts = json.loads(prompt[prompt.rindex("\n[") + 1:])
            return json.dumps([f"[fr] {t}" for t in texts], ensure_ascii=False)
        if '"reading"' in (system_prompt or ""):
            return json.dumps({"description": "Beschreibung.", "reading": "Lesart."})
        return "Themenzusammenfassung."


def test_finds_the_real_association_only():
    cfg, df = survey()
    report = CorrelationAnalyzer(cfg, df).run()
    pairs = report.pairs()
    assert [(p.question_1_id, p.question_2_id) for p in pairs] == [("size", "tools")]
    finding = pairs[0].best
    assert finding.result.var_b.id == "tools::0"                  # Cloud, not Papier
    m = report.method
    assert m["correction"] == "benjamini_hochberg" and m["variable_pairs_tested"] == 5
    assert m["question_pairs"] == 3 and m["question_pairs_reported"] == 1


def test_facts_are_checkable_and_localised():
    cfg, df = survey()
    finding = CorrelationAnalyzer(cfg, df).run().pairs()[0].best
    de = finding_facts(finding.result, "de", "de")
    fr = finding_facts(finding.result, "fr", "de")
    assert de[0].startswith("Chi-Quadrat-Test: Cramér's V =") and "korrigiertes p (q)" in de[0]
    assert "Werkzeuge: Cloud = ja" in de[1] and "%" in de[1]
    assert fr[0].startswith("test du chi carré") and "Werkzeuge: Cloud = oui" in fr[1]


@pytest.mark.parametrize("lang,marker", [("de", "Schreibe auf Deutsch"), ("en", "Write in English")])
def test_texts_in_source_language_then_translated(lang, marker):
    cfg, df = survey(lang)
    llm = FakeLLM()
    report = CorrelationAnalyzer(cfg, df, llm_client=llm).run(languages=[lang, "fr"])
    finding = report.pairs()[0].best
    system, user = llm.calls[0]
    assert marker in system and "Campus" in system
    assert "Fakten:" in user or "Facts:" in user
    assert finding.description == {lang: "Beschreibung.", "fr": "[fr] Beschreibung."}
    assert finding.reading[lang] == "Lesart."


def test_export_contract():
    cfg, df = survey()
    report = CorrelationAnalyzer(cfg, df, llm_client=FakeLLM()).run(languages=["de", "fr"])
    data = report.to_json(cfg, len(df), ["de", "fr"])
    entry = data["theme_correlations"]["general"]["correlations"][0]
    assert entry["correlation"]["effect_measure"] == "cramers_v" and "direction" not in entry["correlation"]
    assert entry["correlation"]["q_value"] is not None
    f = entry["findings"][0]
    assert set(f["facts"]) == {"de", "fr"} and f["reading"]["de"] == "Lesart."
    assert data["method"]["variable_pairs_tested"] == 5
    json.dumps(data)  # serialisable


def _db_with_import(tmp_path):
    db = SurveyDatabase(tmp_path / "s.db")
    with db._connection() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (1, 'p')")
        conn.execute("INSERT INTO imports (id, project_id, filename) VALUES (1, 1, 'f.csv')")
    return db


def test_stored_texts_are_reused_and_manual_ones_kept(tmp_path):
    cfg, df = survey()
    db = _db_with_import(tmp_path)
    store = AnalysisTextStore(db, 1, "correlation")
    first = FakeLLM()
    CorrelationAnalyzer(cfg, df, llm_client=first, text_store=store).run(languages=["de", "fr"])
    second = FakeLLM()
    report = CorrelationAnalyzer(cfg, df, llm_client=second, text_store=store).run(languages=["de", "fr"])
    assert not [c for c in second.calls if '"reading"' in c[0]]    # no new texts written
    finding = report.pairs()[0].best
    store.put(finding.key, "anything", {"de": "Von Hand."}, {"de": ""}, manual=True)
    store.put(finding.key, "other", {"de": "Automatisch."}, {}, manual=False)     # must not overwrite
    assert store.get(finding.key, "changed facts")["description"] == {"de": "Von Hand."}



def test_themes_group_but_do_not_limit_testing():
    cfg, df = survey()
    unrelated = {"t": {"questions": ["other_1", "other_2"], "label": {"de": "Fremd"}}}
    report = CorrelationAnalyzer(cfg, df).run(themes=unrelated)
    assert list(report.themes) == ["general"] and report.pairs()
    themed = {"t": {"questions": ["size", "tools"], "label": {"de": "Ausstattung"}}}
    report = CorrelationAnalyzer(cfg, df).run(themes=themed)
    assert list(report.themes) == ["t"] and report.themes["t"].pairs[0].key == ("size", "tools")


def test_theme_summary_is_stored_and_reused(tmp_path):
    cfg, df = survey()
    db = _db_with_import(tmp_path)
    store = AnalysisTextStore(db, 1, "correlation")
    themes = {"t": {"label": {"de": "Ausstattung"}, "questions": ["size", "tools"]}}
    first = FakeLLM()
    report = CorrelationAnalyzer(cfg, df, llm_client=first, text_store=store, db=db).run(themes, languages=["de", "fr"])
    summary = next(iter(report.themes.values()))
    assert summary.theme_insight == {"de": "Themenzusammenfassung.", "fr": "[fr] Themenzusammenfassung."}
    second = FakeLLM()
    report = CorrelationAnalyzer(cfg, df, llm_client=second, text_store=store, db=db).run(themes, languages=["de", "fr"])
    assert not [c for c in second.calls if "Ausstattung" in c[0]]
    assert next(iter(report.themes.values())).theme_insight["fr"] == "[fr] Themenzusammenfassung."
    store.put("theme:t", "x", {"de": "Von Hand."}, {}, manual=True)
    report = CorrelationAnalyzer(cfg, df, llm_client=FakeLLM(), text_store=store, db=db).run(themes, languages=["de"])
    assert next(iter(report.themes.values())).theme_insight["de"] == "Von Hand."
