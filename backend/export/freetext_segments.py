# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Segment breakdown of free-text clusters for the dashboard export.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from core import state
from .json_utils import _slugify
from .segment_context import get_context
from .segments import _make_cross_segment_key
from .result_format import _format_result_for_dashboard

logger = logging.getLogger(__name__)



def _in_source(value) -> str:
    """A cluster name or description in the source language (plain strings unchanged)."""
    from utils.languages import localized
    from .keys import _source_language
    src = _source_language()
    return str(localized(value, src, src) or "") if isinstance(value, dict) else str(value or "")


def _examples_per_language(originals: list, translated: dict, languages: List[str], limit: int = 5) -> list:
    """[{lang: text}] for up to ``limit`` examples; a missing translation shows the original."""
    from .keys import _source_language
    src = _source_language()
    langs = list(dict.fromkeys([src] + list(languages)))
    out = []
    for i, original in enumerate(list(originals)[:limit]):
        entry = {}
        for lang in langs:
            options = (translated or {}).get(lang) or []
            entry[lang] = options[i] if lang != src and i < len(options) and options[i] else original
        out.append(entry)
    return out


def _per_language(value, languages: List[str]) -> dict:
    """{lang: text} for the source language and ``languages``."""
    from utils.languages import localized
    from .keys import _source_language
    src = _source_language()
    langs = list(dict.fromkeys([src] + list(languages)))
    if not isinstance(value, dict):
        return {lang: value or "" for lang in langs}
    return {lang: str(localized(value, lang, src) or "") for lang in langs}

def _enrich_freetext_question(
    question_data: dict,
    result: dict,
    qconfig: dict,
    languages: List[str]
) -> dict:
    """Adds the cluster details, with segmented data, to a free-text question."""
    # analysis mode from the metadata or the configuration
    metadata = result.get('metadata', {})
    analysis_mode = metadata.get('analysis_mode', 'items')
    if analysis_mode == 'items':
        # fallback: check the configuration
        clustering_mode = qconfig.get('clustering', {}).get('mode', 'auto')
        if clustering_mode == 'raw':
            analysis_mode = 'raw'
    
    question_data["analysis_mode"] = analysis_mode
    question_data["clusters"] = []
    
    # cluster details from the metadata
    cluster_details = metadata.get('cluster_details', [])
    
    
    # load the clusters from the database for multilingual labels
    if state.db and state.import_id:
        from services.workbench_store import get_clusters_for_question
        db_clusters = get_clusters_for_question(question_data["id"])
        
        logger.info(f"Database mode for {question_data['id']}: {len(db_clusters)} clusters loaded from the database")
        
        # calculate the segmented cluster data
        segmented_data = _calculate_segmented_cluster_data(
            question_data["id"], db_clusters, analysis_mode
        )
        
        if segmented_data:
            sample_cluster_id = list(segmented_data.keys())[0] if segmented_data else None
            sample_results = segmented_data.get(sample_cluster_id, {})
            logger.info(f"  Segmented data calculated: {len(segmented_data)} clusters, example keys: {list(sample_results.keys())[:5]}")
        else:
            logger.warning(f"  No segmented data calculated for {question_data['id']}")
        
        for cluster in db_clusters:
            cluster_id = cluster.get('id')
            cluster_seg_data = segmented_data.get(cluster_id, {})
            
            # debug: show whether the cluster id was found in segmented_data
            if cluster_id in segmented_data:
                logger.info(f"  Cluster {cluster_id}: {len(cluster_seg_data)} Segment-Keys: {list(cluster_seg_data.keys())[:5]}")
            else:
                logger.warning(f"  Cluster {cluster_id}: NOT found in segmented_data!")
                logger.warning(f"    Available keys in segmented_data: {list(segmented_data.keys())[:5]}")
            
            # fallback: without segmented data, calculate at least "all"
            if not cluster_seg_data:
                count = cluster.get('count', len(cluster.get('item_ids', [])))
                total_n = result.get('n', count)
                pct = round(count / total_n * 100, 1) if total_n > 0 else 0
                cluster_seg_data = {
                    "all": {
                        "count": count,
                        "percentage": pct,
                        "n": total_n
                    }
                }
                logger.warning(f"  Cluster {cluster_id}: fallback to 'all' (no segmentation)")
            
            # add segment information to the examples (originals in the source language)
            enriched_examples = _enrich_examples_with_segments(
                cluster.get('examples', [])[:5],
                cluster.get('item_ids', []),
                question_data["id"],
                analysis_mode,
            )
            
            # Examples per language: [{lang: text}], the original where a translation is missing
            multilingual_examples = []
            for i, example_entry in enumerate(_examples_per_language(
                    cluster.get('examples', []), cluster.get('examples_by_lang', {}), languages)):
                # add segment information if present
                if i < len(enriched_examples) and enriched_examples[i].get('segment'):
                    example_entry["segment"] = enriched_examples[i]["segment"]
                multilingual_examples.append(example_entry)
            
            cluster_entry = {
                "id": _slugify(cluster.get('name_source', '')),
                "label": _per_language(cluster.get('names') or {}, languages),
                "description": _per_language(cluster.get('descriptions') or {}, languages),
                "results": cluster_seg_data,
                "examples": multilingual_examples
            }
            question_data["clusters"].append(cluster_entry)
    
    # fallback: from the result metadata (without segmentation)
    if not question_data["clusters"] and cluster_details:
        logger.info(f"Fallback mode for {question_data['id']}: {len(cluster_details)} clusters from the metadata")
        
        # calculate segmented data in the fallback too, if possible:
        # a pseudo cluster structure for _calculate_segmented_cluster_data
        pseudo_clusters = []
        for cd in cluster_details:
            name = cd.get('name', '')
            pseudo_clusters.append({
                'id': _slugify(_in_source(name)),
                'item_ids': cd.get('answer_ids', [])
            })
        
        # calculate segmented data if possible
        segmented_data = {}
        if pseudo_clusters and state.df is not None:
            try:
                segmented_data = _calculate_segmented_cluster_data(
                    question_data["id"], pseudo_clusters, 'raw'
                )
                logger.info(f"Fallback segmentation calculated for {len(pseudo_clusters)} clusters")
            except Exception as e:
                logger.warning(f"Fallback segmentation failed: {e}")
        
        for cd in cluster_details:
            # name and description may be dicts
            name = cd.get('name', '')
            description = cd.get('description', '')
            
            # Name and description in every output language; a missing
            # language falls back to the source language (localized)
            label = _per_language(name, languages)
            desc = _per_language(description, languages)
            slug_name = _in_source(name)
            
            cluster_id = _slugify(slug_name)
            
            # use the segmented data if present, otherwise only all
            cluster_seg_data = segmented_data.get(cluster_id, {})
            if not cluster_seg_data:
                # fallback: only all
                count = cd.get('count', 0)
                total_n = result.get('n', count)
                pct = round(count / total_n * 100, 1) if total_n > 0 else 0
                cluster_seg_data = {
                    "all": {
                        "count": count,
                        "percentage": pct,
                        "n": total_n
                    }
                }
            
            # multilingual examples in the compact format
            multilingual_examples = _examples_per_language(
                cd.get('examples', []), cd.get('examples_translated') or {}, languages)
            
            cluster_entry = {
                "id": cluster_id,
                "label": label,
                "description": desc,
                "results": cluster_seg_data,
                "examples": multilingual_examples
            }
            question_data["clusters"].append(cluster_entry)
    
    _fill_segment_results(question_data, result, qconfig, analysis_mode)
    return question_data


def _get_item_response_mapping(question_id: str) -> dict:
    """
    Mapping item id -> data row (DataFrame index) for the active import.

    The item ids in the clusters are primary keys of response_items; their
    row_index is the data row of the import (raw_responses.row_index, see
    the table definition in utils/persistence.py). The join also checks that
    the row exists in this import. Items of other imports never match.

    Returns:
        Dict: {item_id: row_index}
    """
    if not state.db or not state.import_id:
        logger.warning(f"_get_item_response_mapping: database not available (db={state.db is not None}, import_id={state.import_id})")
        return {}

    mapping = {}
    try:
        with state.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ri.id, rr.row_index
                FROM response_items ri
                JOIN raw_responses rr ON ri.row_index = rr.row_index AND ri.import_id = rr.import_id
                WHERE ri.import_id = ? AND ri.question_id = ?
            """, (state.import_id, question_id))
            for item_id, row_index in cursor.fetchall():
                mapping[item_id] = row_index

            cursor.execute("""
                SELECT COUNT(*) FROM response_items WHERE import_id = ? AND question_id = ?
            """, (state.import_id, question_id))
            total = cursor.fetchone()[0]
        if len(mapping) < total:
            logger.warning(f"_get_item_response_mapping: {total - len(mapping)} of {total} items "
                           f"of {question_id} without a data row in the import {state.import_id}")
        else:
            logger.info(f"_get_item_response_mapping: {len(mapping)} entries for {question_id}")
    except Exception as e:
        logger.warning(f"Error loading the item-response mapping: {e}", exc_info=True)

    return mapping



# =============================================================================
# Segment breakdown of free-text clusters
# =============================================================================
#
# Core (always): the global clusters are counted within each segment, so the
# categories are the same in every segment and can be compared. A segment is
# exported only when it reaches the privacy threshold (single dimensions:
# sample_warnings.minimum_threshold; combinations: cross_filter_min_n).
#
# Optional (per dimension, enable_specific_clusters): the answers of a
# segment are clustered on their own, to see which topics a segment raises
# in its own terms. Only for segments of at least specific_clusters_min_n
# respondents; stored as "specific_clusters" next to the core result.


class _FieldRows:
    """item id -> row for questions with several text fields."""

    def __init__(self, factor: int):
        self.factor = factor

    def get(self, item_id, default=None):
        return int(item_id) // self.factor if isinstance(item_id, int) else default


def _cluster_row_positions(question_id: str, clusters: List[dict],
                           analysis_mode: str) -> Dict[Any, Set[int]]:
    """Row positions (respondents) per cluster id."""
    frame = get_context().frame
    n_rows = 0 if frame is None else len(frame)
    if analysis_mode == 'raw':
        to_row = None  # item ids are row positions already
    elif analysis_mode == 'fields':
        # several text fields: item id = row * FIELD_ID_FACTOR + field (handlers/freetext.py)
        from handlers.freetext import FIELD_ID_FACTOR
        to_row = _FieldRows(FIELD_ID_FACTOR)
    else:
        to_row = _get_item_response_mapping(question_id)  # item id -> row_index
    rows = {}
    for cluster in clusters:
        positions = set()
        for item_id in cluster.get('item_ids', []) or []:
            pos = item_id if to_row is None else to_row.get(item_id)
            if isinstance(pos, int) and 0 <= pos < n_rows:
                positions.add(pos)
        rows[cluster.get('id')] = positions
    return rows


def _segment_groups(ctx) -> List[Tuple[str, Tuple[str, ...], int]]:
    """(result key, dimensions, minimum n) for every configured breakdown."""
    groups = [(f"by_{d}", (d,), ctx.resolver.min_n) for d in ctx.dimension_ids()]
    groups += [(_make_cross_segment_key(combo), combo, ctx.resolver.cross_filter_min_n)
               for combo in ctx.resolver.cross_filters]
    return groups


def _segment_rows(ctx, answered: Set[int], dims: Tuple[str, ...]) -> Dict[str, Set[int]]:
    """Value key -> answered row positions, for one breakdown."""
    frame = ctx.frame
    positions = sorted(answered)
    sub = frame.iloc[positions][[ctx.column(d) for d in dims]]
    out: Dict[str, Set[int]] = {}
    for pos, values in zip(positions, sub.itertuples(index=False, name=None)):
        if any(pd.isna(v) for v in values):
            continue
        key = "|".join(ctx.key(d, str(v)) for d, v in zip(dims, values))
        out.setdefault(key, set()).add(pos)
    return out


def _calculate_segmented_cluster_data(question_id: str, clusters: List[dict],
                                      analysis_mode: str = "items") -> dict:
    """
    Per cluster: {"all": {count, percentage, n}, "by_<dim>": {value_key: {...}}, ...}.

    count = respondents with at least one item in the cluster, n = respondents
    with at least one clustered item in the segment.
    """
    ctx = get_context()
    if ctx.frame is None or not clusters:
        return {}
    rows = _cluster_row_positions(question_id, clusters, analysis_mode)
    answered = set().union(*rows.values()) if rows else set()
    if not answered:
        logger.warning(f"{question_id}: no assignment of clusters to respondents")
        return {}

    def cell(count, n):
        return {"count": count, "percentage": round(count / n * 100, 1) if n else 0, "n": n}

    result = {cid: {"all": cell(len(r), len(answered))} for cid, r in rows.items()}
    for result_key, dims, min_n in _segment_groups(ctx):
        for value_key, seg_rows in _segment_rows(ctx, answered, dims).items():
            if len(seg_rows) < min_n:
                continue  # not exported at all, not only hidden
            for cid, r in rows.items():
                result[cid].setdefault(result_key, {})[value_key] = cell(len(r & seg_rows), len(seg_rows))
    return result


def _enrich_examples_with_segments(examples: List[str], item_ids: list,
                                   question_id: str, analysis_mode: str) -> List[dict]:
    """Attach the segment value keys of the respondent to each example."""
    ctx = get_context()
    if ctx.frame is None or not examples:
        return [{"text": ex, "segment": {}} for ex in examples]
    rows = _cluster_row_positions(question_id, [{"id": 0, "item_ids": item_ids[:len(examples)]}],
                                  analysis_mode)[0]
    ordered = sorted(rows)
    out = []
    for i, text in enumerate(examples):
        segment = {}
        if i < len(ordered):
            row = ctx.frame.iloc[ordered[i]]
            for dim in ctx.dimension_ids():
                value = row[ctx.column(dim)]
                if not pd.isna(value):
                    segment[dim] = ctx.key(dim, str(value))
        out.append({"text": text, "segment": segment})
    return out


def _fill_segment_results(question_data: dict, result, qconfig: dict, analysis_mode: str) -> None:
    """
    results.by_* for a free-text question: the global clusters counted per
    segment (same shape the dashboard uses for the overall result, with
    percentages keyed by cluster id), plus segment-specific clusters where
    enabled and large enough.
    """
    ctx = get_context()
    clusters = question_data.get("clusters") or []
    if not clusters:
        return
    analysis_mode = question_data.get("analysis_mode", analysis_mode)
    for result_key, dims, _min_n in _segment_groups(ctx):
        entries: Dict[str, dict] = {}
        for c in clusters:
            for value_key, cell in (c.get("results", {}).get(result_key) or {}).items():
                e = entries.setdefault(value_key, {
                    "n": cell["n"], "n_responses": cell["n"], "analysis_mode": analysis_mode,
                    "clustering": "global",
                    "cluster_counts_by_id": {}, "cluster_percentages_by_id": {},
                })
                e["cluster_counts_by_id"][c["id"]] = cell["count"]
                e["cluster_percentages_by_id"][c["id"]] = cell["percentage"]
        if len(dims) == 1:
            threshold = ctx.resolver.specific_clusters_threshold(dims[0])
            if threshold is not None:
                for value in ctx.values(dims[0]):
                    value_key = ctx.key(dims[0], value)
                    if value_key in entries and entries[value_key]["n"] >= threshold:
                        specific = _specific_clusters(question_data["id"], qconfig, dims[0], value)
                        if specific:
                            entries[value_key]["specific_clusters"] = specific
        question_data["results"][result_key] = entries


# Segment-specific clusters of the running pipeline. The pipeline needs them
# twice (segment_clusters.json and the dashboard export); without this cache
# each segment would be clustered by the LLM twice, with possibly different
# results in the two files. Only active between begin/end of one pipeline
# run, so a later export clusters afresh.
_SPECIFIC_CACHE: Optional[Dict[tuple, Optional[dict]]] = None


def begin_specific_cluster_cache() -> None:
    global _SPECIFIC_CACHE
    _SPECIFIC_CACHE = {}


def end_specific_cluster_cache() -> None:
    global _SPECIFIC_CACHE
    _SPECIFIC_CACHE = None


def _specific_clusters(qid: str, qconfig: dict, dim_id: str, value: str) -> Optional[dict]:
    """Cluster the answers of one segment on their own (LLM, counted by the handler)."""
    if _SPECIFIC_CACHE is None:
        return _cluster_segment(qid, qconfig, dim_id, value)
    import json
    key = (state.import_id, id(state.df), qid, dim_id, value,
           json.dumps(qconfig, sort_keys=True, default=str))
    if key not in _SPECIFIC_CACHE:
        _SPECIFIC_CACHE[key] = _cluster_segment(qid, qconfig, dim_id, value)
    return _SPECIFIC_CACHE[key]


def _cluster_segment(qid: str, qconfig: dict, dim_id: str, value: str) -> Optional[dict]:
    from handlers import get_handler
    ctx = get_context()
    handler = get_handler('freetext', qid, qconfig)
    if handler is None:
        return None
    try:
        res = handler.analyze(ctx.subset(dim_id, value), lang=ctx.source_language,
                              db=state.db, import_id=state.import_id,
                              use_cached_clustering=False)
    except Exception as e:
        logger.warning(f"Segment-specific clusters {qid}/{dim_id}={value} failed: {e}")
        return None
    formatted = _format_result_for_dashboard(
        {"n": res.n, "data": res.data if isinstance(res.data, dict) else {},
         "percentages": {}, "metadata": res.metadata or {}}, 'freetext')
    formatted["clustering"] = "segment"
    return formatted
