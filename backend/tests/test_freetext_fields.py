# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Free text with several fields: every filled field is a mention of its respondent."""
import sys
import types
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers.freetext import FIELD_ID_FACTOR, ClusterInfo, ClusteringResult, FreetextHandler  # noqa: E402


def fake_clustering(self, answers_df, lang):
    groups = {}
    for oid, ans in zip(answers_df["original_index"], answers_df["answer"]):
        key = "Kosten" if ("teuer" in ans or "Geld" in ans) else "Zeit"
        groups.setdefault(key, []).append(int(oid))
    return ClusteringResult([ClusterInfo(name={"de": k}, description={"de": ""}, answer_ids=v, examples=[], count=len(v))
                             for k, v in groups.items()])


def test_all_fields_pooled_and_counted_per_person(monkeypatch):
    monkeypatch.setitem(sys.modules, "core", types.SimpleNamespace(state=types.SimpleNamespace(
        config={"settings": {"survey_metadata": {"source_language": "de"}}}, df=None, db=None, import_id=None)))
    monkeypatch.setattr(FreetextHandler, "_perform_clustering", fake_clustering)
    df = pd.DataFrame({"g1": ["zu teuer", "keine Zeit", None, "Geld fehlt"],
                       "g2": ["keine Zeit", None, None, "zu teuer"],
                       "g3": [None, None, "", "Zeit fehlt"]})
    h = FreetextHandler({"id": "f", "columns": ["g1", "g2", "g3"]})
    r = h.analyze(df, lang="de")
    assert r.n == 3 and r.metadata["total_items"] == 6 and r.metadata["analysis_mode"] == "fields"
    assert dict(r.data) == {"Zeit": 3, "Kosten": 2}                 # persons per topic, not mentions
    assert "6 Nennungen von 3 Personen" in r.description
    ids = {c.get_name("de"): c.answer_ids for c in h.clustering_result.clusters}
    assert sorted({i // FIELD_ID_FACTOR for i in ids["Kosten"]}) == [0, 3]   # ids decode to the data rows
    from export.freetext_segments import _FieldRows
    assert _FieldRows(FIELD_ID_FACTOR).get(3 * FIELD_ID_FACTOR + 2) == 3
