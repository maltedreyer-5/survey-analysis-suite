# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Comparisons between segments for the pipeline export.

- ``segment_clusters.json``: segment-specific clusters per dimension with
  ``enable_specific_clusters``. Every group large enough is clustered on its
  own by the free-text handler (the same path as the dashboard export,
  export/freetext_segments._specific_clusters), so counts and shares are
  counted, not estimated. Since every group has its own topics, the file
  describes each group; it makes no comparison statement.
- ``segment_insights.json``: tested differences between the groups of every
  dimension for every question (utils/segment_findings.py): facts, a
  description and, set apart, a possible reading.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core import state
from .json_utils import NumpyJSONEncoder, sanitize_for_json

logger = logging.getLogger(__name__)


def _answered(frame, columns) -> int:
    texts = frame[columns].apply(lambda col: col.map(lambda v: isinstance(v, str) and v.strip() != ""))
    return int(texts.any(axis=1).sum())


def _group_clusters(formatted: dict, max_clusters: int) -> List[dict]:
    """Clusters of one group for the comparison view: counted respondents, share of n."""
    n = formatted.get("n") or 0
    clusters = []
    for c in formatted.get("clusters") or []:
        count = int(c.get("unique_respondents", c.get("count", 0)) or 0)
        clusters.append({
            "name": c.get("name") or {},
            "description": c.get("description") or {},
            "count": count,
            "percentage": round(100 * count / n, 1) if n else 0.0,
            "examples": c.get("examples") or [],
        })
    clusters.sort(key=lambda c: -c["count"])
    return clusters[:max_clusters]


def export_segment_cluster_comparison(
    export_dir: Optional[Path],
    languages: List[str] = None,
    min_responses: int = 5,
    max_clusters: int = 5,
    progress_callback=None,
) -> dict:
    """
    Writes segment_clusters.json (segment-specific clusters per dimension).

    ``min_responses`` is not used for dimensions: each dimension has its own
    threshold (``specific_clusters_min_n``). ``max_clusters`` limits the
    clusters listed per group (largest first).
    """
    from utils.languages import get_output_languages, get_source_language, localized
    from .freetext_segments import _specific_clusters
    from .segment_context import get_context
    if languages is None:
        languages = get_output_languages(state.config)
    export_data = {
        "$schema": "segment_clusters_v3",
        "generated_at": datetime.now().isoformat(),
        "parameters": {"max_clusters_per_group": max_clusters, "languages": languages},
        "questions": {},
    }
    if state.df is None or state.config is None:
        logger.warning("Segment-specific clusters: no data")
        return export_data
    ctx = get_context()
    dims = [d for d in ctx.dimension_ids() if ctx.resolver.specific_clusters_threshold(d) is not None]
    if not dims:
        logger.info("Segment-specific clusters: no dimension with enable_specific_clusters")
        return export_data
    export_data["dimensions"] = {}
    src = get_source_language(state.config)
    freetext = [(qid, q) for qid, q in (state.config.get("question_groups") or {}).items()
                if q.get("handler") == "freetext"]
    with_clusters = 0
    for i, (qid, qconfig) in enumerate(freetext):
        if progress_callback:
            progress_callback(i / max(1, len(freetext)), f"Segmenteigene Cluster {qid}...")
        columns = [c for c in qconfig.get("columns", []) if c in ctx.frame.columns]
        if not columns:
            continue
        label = qconfig.get("label") or {src: qid}
        for dim in dims:
            threshold = ctx.resolver.specific_clusters_threshold(dim)
            groups = {}
            for value in ctx.values(dim):
                if _answered(ctx.subset(dim, value), columns) < threshold:
                    continue
                formatted = _specific_clusters(qid, qconfig, dim, value)
                if not formatted:
                    continue
                groups[ctx.key(dim, value)] = {
                    "segment_value_label": ctx.value_labels(dim, value),
                    "total_responses": formatted.get("n", 0),
                    "clusters": _group_clusters(formatted, max_clusters),
                }
            if groups:
                dim_data = export_data["dimensions"].setdefault(dim, {"label": ctx.label(dim), "questions": {}})
                dim_data["questions"][qid] = {
                    "question_id": qid,
                    "question_label": {l: str(localized(label, l, src) or qid) for l in languages},
                    "clusters_by_value": groups,
                    "metadata": {"min_responses_per_group": threshold, "max_clusters_per_group": max_clusters},
                }
                with_clusters += 1
    export_data["summary"] = {"questions_with_clusters": with_clusters, "total_freetext_questions": len(freetext)}
    if export_dir:
        with open(Path(export_dir) / "segment_clusters.json", "w", encoding="utf-8") as f:
            json.dump(sanitize_for_json(export_data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        logger.info(f"Segment-specific clusters exported: {with_clusters} question/dimension pairs")
    return export_data


def export_segment_insights(
    export_dir: Optional[Path],
    languages: List[str] = None,
    min_difference: float = 15.0,
    progress_callback=None,
    llm_client="auto",
):
    """
    Tested differences between groups (utils/segment_findings.py) as
    segment_insights.json. Returns the report (for the Word export) and the
    exported data: ``(report, data)``.
    """
    from utils.languages import get_output_languages
    from utils.segment_findings import run_for_state
    from .dashboard import _available_llm_client
    if languages is None:
        languages = get_output_languages(state.config)
    if llm_client == "auto":
        llm_client = _available_llm_client()

    def progress(i, total, msg):
        if progress_callback:
            progress_callback(i / max(1, total), msg)

    report = run_for_state(state, languages, llm_client, min_difference, progress)
    data = report.to_json(state.config, languages)
    if export_dir:
        with open(Path(export_dir) / "segment_insights.json", "w", encoding="utf-8") as f:
            json.dump(sanitize_for_json(data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        logger.info(f"Group differences exported: {data['summary']['questions_with_differences']} "
                    f"of {data['summary']['questions_tested']} questions with differences")
    return report, data
