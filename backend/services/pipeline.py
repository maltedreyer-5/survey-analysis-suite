# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: pipeline operations.

The pipeline functions behind the interface:
- run_full_pipeline         (complete pipeline)
- regenerate_summaries      (regenerate the summaries only)
- reexport_after_workbench  (re-export after Workbench changes)
- batch_extract_items       (item extraction for all free-text questions)

They delegate to export.pipeline and take `state` and optionally
`progress` as arguments; they return a (status text, further outputs) tuple.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


# adapter that turns a plain progress callable into the signature the
# export functions expect (gradio.Progress or compatible)
class _ProgressAdapter:
    """Turns a callable(fraction, desc) into a gradio-compatible object."""
    
    def __init__(self, callback: Optional[Callable[[float, str], None]]):
        self._cb = callback
    
    def __call__(self, fraction, desc=""):
        if self._cb:
            self._cb(fraction, desc)
    
    def tqdm(self, iterable, **kw):
        # compatibility with gradio.Progress.tqdm
        return iterable


def run_full_pipeline(
    state,
    include_item_extraction: bool = True,
    include_theme_analysis: bool = True,
    deep_theme_analysis: bool = False,
    include_segment_comparison: bool = True,
    include_summaries: bool = True,
    include_word_export: bool = True,
    include_dashboard_export: bool = True,
    include_translations: bool = True,
    languages: Optional[List[str]] = None,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, str]:
    """
    Runs the complete analysis (from item extraction to the Word export).
    
    Args:
        state: AppState (passed on).
        include_*: flags switching single steps on.
        languages: list of languages, default: the output languages of the survey.
        progress: optional callback (fraction, desc).
    
    Returns:
        (status text, output path).
    """
    from batch_export import run_full_pipeline as _run
    
    return _run(
        include_translations=include_translations,
        include_item_extraction=include_item_extraction,
        include_theme_analysis=include_theme_analysis,
        include_segment_comparison=include_segment_comparison,
        include_summaries=include_summaries,
        include_word_export=include_word_export,
        deep_theme_analysis=deep_theme_analysis,
        languages=languages or None,  # None: the survey's output languages
        progress=_ProgressAdapter(progress),
    )


def regenerate_summaries(
    state,
    include_word: bool = True,
    languages: Optional[List[str]] = None,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, str]:
    """
    Deletes the summary cache in the database and regenerates all summaries.
    
    Optionally also updates the Word export.
    
    Args:
        state: AppState.
        include_word: if True, the Word export is created again as well.
        languages: languages.
        progress: optional callback.
    
    Returns:
        (status text with cache information, output path).
    """
    from batch_export import run_full_pipeline as _run
    from core import DB_PATH
    
    # 1. clear the cache — only for the active import; summaries of other
    # surveys stay untouched.
    cache_msg = ""
    if getattr(state, 'import_id', None) is None:
        return "❌ No data imported, nothing to regenerate.", ""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(
            "DELETE FROM question_summaries WHERE import_id = ?",
            (state.import_id,),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        cache_msg = f"✅ {deleted} cached summaries deleted"
    except Exception as e:
        cache_msg = f"⚠️ Clearing the cache failed: {e}"
    
    # 2. run the pipeline with the summaries only (+ optionally Word)
    status, output_path = _run(
        include_translations=False,
        include_item_extraction=False,
        include_theme_analysis=False,
        include_correlations=False,
        include_segment_comparison=False,
        include_summaries=True,
        include_word_export=include_word,
        languages=languages or None,  # None: the survey's output languages
        progress=_ProgressAdapter(progress),
    )
    
    return f"{cache_msg}\n\n{status}", output_path


def reexport_after_workbench(
    state,
    languages: Optional[List[str]] = None,
    reassign_items: bool = True,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, str]:
    """
    Re-export after changes in the Workbench (clusters, items, etc.).
    
    Args:
        state: AppState.
        languages: languages of the export.
        reassign_items: whether the LLM assigns the items to the new clusters.
        progress: optional callback.
    
    Returns:
        (status text, output path).
    """
    from batch_export import reexport_after_workbench as _reexport
    
    return _reexport(
        languages=languages or None,  # None: the survey's output languages
        include_word_export=True,
        include_dashboard_export=True,
        retranslate_changed_clusters=True,
        reassign_items=reassign_items,
        progress=_ProgressAdapter(progress),
    )


def batch_extract_items(
    state,
    force_llm: bool = False,
    force_reextract: bool = False,
    thematic_mode: bool = False,
    validate: bool = False,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, str]:
    """
    Extracts items for all free-text questions.
    
    Args:
        state: AppState.
        force_llm: use the LLM even if the LLM mode is off by default.
        force_reextract: delete existing items and extract again.
        thematic_mode: thematic instead of purely syntactic splitting.
        validate: let the LLM check the extraction against the original.
        progress: optional callback.
    
    Returns:
        (Markdown status, JSON statistics string).
    """
    from utils.llm import get_llm_client
    from utils.item_extractor import ItemExtractor
    from services.item_writer import text_columns, extract_and_store_items
    
    if state.df is None:
        return "❌ No data loaded", "{}"
    if state.db is None or state.import_id is None:
        return "❌ No database connection", "{}"
    if state.config is None:
        return "❌ No configuration loaded", "{}"
    
    # collect the free-text questions
    freetext_questions = []
    for qid, qconfig in state.config.get('question_groups', {}).items():
        if qconfig.get('handler') == 'freetext':
            columns = qconfig.get('columns', [])
            if columns:
                freetext_questions.append({
                    'qid': qid,
                    'label': _src_text(qconfig.get('label', {}), None, qid),
                    'columns': text_columns(qconfig, state.df),
                    'configured': columns,
                })
    
    if not freetext_questions:
        return "❌ No free-text questions found", "{}"
    
    total = len(freetext_questions)
    mode_text = "thematic" if thematic_mode else "syntactic"
    validate_text = " + quality check" if validate else ""
    
    llm_client = get_llm_client()
    connection_ok, _ = llm_client.test_connection()
    
    if force_llm and not connection_ok:
        return "❌ LLM not available", "{}"
    
    stats = {
        'questions': 0, 'items': 0, 'responses': 0, 'skipped': 0,
        'retries': 0, 'details': {}, 'mode': mode_text + validate_text,
    }
    errors = []
    
    for i, q_info in enumerate(freetext_questions):
        qid = q_info['qid']
        columns = q_info['columns']
        
        if progress:
            progress((i + 1) / total, f"[{i+1}/{total}] {qid}...")
        
        if not columns:
            errors.append(f"{qid}: column not found")
            continue
        
        # Resume-Modus
        if not force_reextract:
            # Resume only if every text field has items; a field without
            # items (e.g. extracted before all fields were covered) is redone
            existing_items = state.db.get_items_for_question(
                state.import_id, qid, None, with_metadata=False,
            )
            if existing_items and not all(
                    state.db.has_extraction(state.import_id, qid, c) for c in columns):
                existing_items = []
            if existing_items and len(existing_items) > 0:
                stats['skipped'] += 1
                stats['details'][qid] = {
                    'responses': 0,
                    'items': len(existing_items),
                    'from_cache': True,
                }
                continue
        
        try:
            extractor = ItemExtractor(
                llm_client=llm_client if connection_ok else None,
                db=state.db,
                question_context=q_info['label'],
                thematic_mode=thematic_mode,
            )
            
            ex = extract_and_store_items(
                extractor, state.db, state.import_id, qid, state.df, columns,
                force_llm=(force_llm and connection_ok), validate=validate,
            )
            items_count = ex['items']
            if ex['retries']:
                stats['retries'] = stats.get('retries', 0) + ex['retries']
            
            stats['questions'] += 1
            stats['responses'] += ex['answers']
            stats['items'] += items_count
            stats['details'][qid] = {
                'responses': ex['answers'],
                'respondents': ex['respondents'],
                'items': items_count,
            }
        except Exception as e:
            errors.append(f"{qid}: {str(e)[:40]}")
    
    # build the result text
    llm_status = "🤖 LLM" if connection_ok else "📋 Rule-based"
    result_text = (
        f"✅ **{stats['questions'] + stats['skipped']}/{total}** questions processed "
        f"({llm_status})\n\n"
    )
    if stats['skipped'] > 0:
        result_text += (
            f"   ↳ {stats['skipped']} from the cache, "
            f"{stats['questions']} newly extracted\n\n"
        )
    result_text += (
        f"📝 **{stats['items']}** items from "
        f"**{stats['responses']}** answers\n\n"
    )
    for qid, detail in stats['details'].items():
        cache_marker = " (cache)" if detail.get('from_cache') else ""
        result_text += f"- {qid}: {detail['items']} items{cache_marker}\n"
    if errors:
        result_text += f"\n⚠️ Errors: {', '.join(errors[:3])}"
    
    return result_text, json.dumps(stats, indent=2)
