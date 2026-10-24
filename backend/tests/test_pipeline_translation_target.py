# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""The translation step of the full pipeline translates answers into the source
language of the survey, so an English survey keeps English answers for the clustering."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from export import pipeline  # noqa: E402
from utils import response_translator  # noqa: E402
import utils.llm as llm  # noqa: E402


@pytest.mark.parametrize("source", ["en", "de"])
def test_translation_step_targets_the_source_language(monkeypatch, tmp_path, source):
    seen = {}

    def fake_translate(df, config, target_lang="de", **kw):
        seen["target"] = target_lang
        raise RuntimeError("stop after the translation step")   # the rest of the pipeline is not needed

    monkeypatch.setattr(response_translator, "translate_responses", fake_translate)
    monkeypatch.setattr(llm, "get_llm_client", lambda: SimpleNamespace(
        test_connection=lambda: (True, "ok"), reset_stats=lambda: None,
        get_stats=lambda: SimpleNamespace(total_calls=0)))
    monkeypatch.setattr(core.state, "df", pd.DataFrame({"q": ["a", "b"]}))
    monkeypatch.setattr(core.state, "config", {"settings": {"survey_metadata": {"source_language": source, "output_languages": [source]}}, "question_groups": {}})
    monkeypatch.setattr(core.state, "db", SimpleNamespace())
    monkeypatch.setattr(core.state, "import_id", 1)
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    out = None
    try:
        out = pipeline.run_full_pipeline(include_item_extraction=False, include_theme_analysis=False,
                                   include_correlations=False, include_segment_comparison=False,
                                   include_word_export=False, include_summaries=False,
                                   languages=[source], progress=lambda *a, **k: None)
    except Exception as e:
        out = repr(e)
    assert seen.get("target") == source, out
