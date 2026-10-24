# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Demo data of the dashboard (dashboard/data/*.json and js/*-data.js) from the
English example survey, with the German texts of the German example survey as the
German labels (as a bilingual survey brings them; French and Italian fall back to
English).

Runs the real export with one change: free-text answers are grouped by the
themes the generator wrote them from (examples/generate.py) instead of an
LLM, so the demo is reproducible and needs no LLM. Descriptions, readings and
translations are left empty (no LLM) except for the theme names, which come
from the generator in German and English.

Run from the repository root: python examples/make_demo_dashboard.py
Options: --survey <id> (default ai_in_higher_education_en), --out <directory> (default
dashboard/data; another directory gets the JSON files only).
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "examples"))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

import generate  # noqa: E402
import utils.llm as llm_mod  # noqa: E402


class NoLLM:
    """Stands in for the LLM: no texts; the free-text grouping below replaces clustering."""
    def test_connection(self): return False, "demo"
    def complete(self, *a, **k): raise RuntimeError("no LLM in the demo build")
    def __getattr__(self, name): raise AttributeError(name)


def theme_clustering(self, answers_df, lang):
    from handlers.freetext import ClusterInfo, ClusteringResult
    de, en = generate.T["de"]["texts"], generate.T["en"]["texts"]
    theme_of = {text: (name, i) for i, (name, texts) in enumerate(de.items()) for text in texts}
    theme_of.update({text: (name, i) for i, (name, texts) in enumerate(en.items()) for text in texts})
    names_de, names_en = list(de), list(en)
    groups = {}
    for oid, answer in zip(answers_df["original_index"], answers_df["answer"]):
        _, i = theme_of.get(str(answer).strip(), (None, None))
        groups.setdefault(i, []).append(int(oid))
    clusters = []
    for i, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if i is None:
            name = {"de": "Sonstiges", "en": "Other", "fr": "Autres", "it": "Altro"}
        else:
            name = {"de": names_de[i], "en": names_en[i], "fr": names_en[i], "it": names_en[i]}
        examples = [a for oid, a in zip(answers_df["original_index"], answers_df["answer"]) if int(oid) in ids][:3]
        clusters.append(ClusterInfo(name=name, description={}, answer_ids=ids, examples=examples, count=len(ids)))
    return ClusteringResult(clusters)


def add_german_labels(cfg: dict, de_cfg: dict) -> None:
    """The German texts of the German example survey as the German labels of the English one
    (same questions and dimensions, lists by position)."""
    meta, de_meta = cfg["settings"]["survey_metadata"], de_cfg["settings"]["survey_metadata"]
    for key in ("survey_title", "about"):
        if isinstance(meta.get(key), dict) and isinstance(de_meta.get(key), dict) and de_meta[key].get("de"):
            meta[key]["de"] = de_meta[key]["de"]
    for qid, q in cfg["question_groups"].items():
        dq = de_cfg["question_groups"].get(qid) or {}
        for key in ("label", "full_question", "help"):
            if isinstance(q.get(key), dict) and isinstance(dq.get(key), dict) and dq[key].get("de"):
                q[key]["de"] = dq[key]["de"]
        for key in ("options", "scale", "row_labels", "item_labels"):
            src, de = (q.get(key) or {}), (dq.get(key) or {})
            if isinstance(src, dict) and isinstance(de, dict) and de.get("de") and len(de["de"]) == len(src.get("en") or []):
                src["de"] = list(de["de"])
    de_dims = {d["id"]: d for d in de_cfg["settings"].get("segmentation", {}).get("dimensions", [])}
    for d in cfg["settings"].get("segmentation", {}).get("dimensions", []):
        dd = de_dims.get(d["id"]) or {}
        if isinstance(d.get("label"), dict) and (dd.get("label") or {}).get("de"):
            d["label"]["de"] = dd["label"]["de"]
        b, db = d.get("bucketing") or {}, dd.get("bucketing") or {}
        for x, y in zip(b.get("buckets") or [], db.get("buckets") or []):
            if isinstance(x.get("label"), dict) and (y.get("label") or {}).get("de"):
                x["label"]["de"] = y["label"]["de"]
        targets = list(dict.fromkeys((b.get("mapping") or {}).values()))
        de_targets = list(dict.fromkeys((db.get("mapping") or {}).values()))
        if targets and len(targets) == len(de_targets):
            d["value_labels"] = {t: {"en": t, "de": g} for t, g in zip(targets, de_targets)}


def main():
    from handlers.freetext import FreetextHandler
    FreetextHandler._perform_clustering = theme_clustering
    llm_mod._default_client = NoLLM()
    llm_mod.get_llm_client = lambda *a, **k: llm_mod._default_client

    from core import set_active_survey, state
    from utils.persistence import SurveyDatabase
    import argparse
    args = argparse.ArgumentParser()
    args.add_argument("--survey", default="ai_in_higher_education_en")
    args.add_argument("--out", default=str(ROOT / "dashboard" / "data"))
    opts = args.parse_args()
    sid = opts.survey
    set_active_survey(sid)          # the exports name the survey they belong to
    from utils.scales import expand_named_scales  # named scales as the app expands them on load
    load = lambda s: expand_named_scales(yaml.safe_load(open(ROOT / "backend" / "surveys" / s / "questions.yaml", encoding="utf-8")))
    cfg = load(sid)
    if sid == "ai_in_higher_education_en":
        add_german_labels(cfg, load("ai_in_higher_education_de"))
    df = pd.read_csv(ROOT / "examples" / "data" / f"{sid}.csv", dtype=str, keep_default_na=True)
    tmp = Path(tempfile.mkdtemp())
    db = SurveyDatabase(tmp / "demo.db")
    state.db = db
    state.project_id = db.create_project(sid)
    state.import_id = db.save_import(state.project_id, df, filename=f"{sid}.csv")
    state.df, state.config, state.analysis_results = df, cfg, {}
    langs = cfg["settings"]["survey_metadata"]["languages"]

    from export.dashboard import export_dashboard_data
    from export.insights import export_segment_cluster_comparison, export_segment_insights
    out = tmp / "out"
    out.mkdir()
    export_dashboard_data(include_segments=True, languages=langs, output_dir=out, progress=lambda *a, **k: None)
    export_segment_insights(out, langs, llm_client=None)
    export_segment_cluster_comparison(out, langs)

    target = Path(opts.out)
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.json"):
        old.unlink()
    for f in sorted(out.rglob("*.json")):
        shutil.copy(f, target / f.name)
        print("  ", f.name)
    if target.resolve() == (ROOT / "dashboard" / "data").resolve():
        subprocess.run([sys.executable, str(ROOT / "dashboard" / "build_offline.py")], cwd=ROOT / "dashboard", check=True)


if __name__ == "__main__":
    main()
