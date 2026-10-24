# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Texts of a question and of its clusters for the report exports, in one
language with fallback to the source language (never to a fixed language).

Used by the Word export of the pipeline (export/pipeline.py) and the report
export (export/reports.py), which built the same texts each on their own.
"""
from typing import Dict, List, Optional

from utils.languages import localized


def _src() -> str:
    from utils.prompts import source_language
    return source_language()


def question_text(qconfig: dict, lang: str, qid: str) -> str:
    """Full question if configured, else the short title, else the id."""
    src = _src()
    for key in ("full_question", "label"):
        value = qconfig.get(key)
        if value:
            found = localized(value, lang, src, default="")
            if found:
                return str(found)
    return str(qid)


def help_text(qconfig: dict, lang: str) -> str:
    value = qconfig.get("help")
    return str(localized(value, lang, _src(), default="") or "") if value else ""


def name_in_source(value) -> str:
    src = _src()
    return str(localized(value, src, src, default="") or "") if isinstance(value, dict) else str(value or "")


def stored_cluster_texts(cluster: dict, languages: List[str]) -> Dict[str, Dict[str, str]]:
    """
    Name and description of a cluster from the workbench store
    (services/workbench_store.get_clusters_for_question) as {lang: text}:
    the source text under the source language, the stored translations
    under the others.
    """
    src = _src()
    flat = cluster.get("translations_flat") or {}
    out = {}
    for field in ("name", "description"):
        texts = {src: cluster.get(f"{field}_source", cluster.get(f"{field}_de", "")) or ""}
        for lang in languages:
            if lang != src:
                texts[lang] = flat.get(f"{field}_{lang}", "") or ""
        out[field] = texts
    return out


def stored_examples(cluster: dict, translated: Optional[dict]) -> Dict[str, list]:
    """Examples per language: translated ones from the analysis metadata, else the originals (source language)."""
    return translated if translated else {_src(): cluster.get("examples", [])}


def report_extras(handler, result, lang: str) -> dict:
    """
    Statistics the Word report shows as tables, in ``lang``: ``numeric_stats``
    for numeric questions, ``metadata`` (statistics per field, constant sum)
    for numeric_matrix questions. Empty for other types.
    """
    meta = getattr(result, "metadata", None) or {}
    extras = {}
    # description of the handler in the report language (shown when there is no
    # LLM summary); free text keeps its cluster sections instead
    if getattr(handler, "handler_type", "") != "freetext" and not hasattr(handler, "clustering_result"):
        try:
            from core import state
            if state.df is not None:
                extras["description_text"] = handler.analyze(state.df, lang=lang).description or ""
        except Exception:
            pass
    if meta.get("numeric_stats") is not None:
        extras["numeric_stats"] = meta["numeric_stats"]
    if hasattr(handler, "report_metadata") and meta.get("stats_by_column"):
        extras["metadata"] = handler.report_metadata(result, lang)
    if meta.get("nps"):
        extras["nps"] = meta["nps"]
    metrics = meta.get("scale_metrics")
    if metrics:
        if "n" in metrics:                      # single choice: one set of metrics
            extras["scale_metrics"] = {"": metrics}
        else:                                   # matrix: per row, row labels in the report language
            mapping = handler.labels_map("row_labels", lang) if hasattr(handler, "labels_map") else {}
            extras["scale_metrics"] = {mapping.get(row, row): m for row, m in metrics.items()}
    return extras
