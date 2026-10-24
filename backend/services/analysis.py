# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: analysis of all questions.

The analysis of a single question (analyze_question, apply_segmentation,
get_segment_values, set_segment_filter) is in services/question_analysis.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def analyze_all_questions(
    state,
    lang: str = 'de',
    force_recalculate: bool = False,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, List[str]]:
    """
    Analyses all questions of the active configuration and stores the results
    in state.analysis_results and in the database.
    
    Args:
        state: AppState with df, config, db, import_id, analysis_results.
        lang: language of the label lookup.
        force_recalculate: if True, cached results are ignored; otherwise
            the resume mode applies.
        progress: optional callback (fraction, desc).
    
    Returns:
        (status text, list of the dropdown choices for the interface).
    
    Side effects:
        - state.analysis_results is filled with the results of all questions
        - the database table analysis_results is updated (segment_type='all')
    """
    from handlers import get_handler
    
    if state.df is None:
        return "❌ No data imported", []
    
    if state.config is None:
        return "❌ No configuration loaded", []
    
    question_groups = state.config.get('question_groups', {})
    total = len(question_groups)
    success = 0
    skipped = 0
    errors = []
    
    for i, (qid, qconfig) in enumerate(question_groups.items()):
        if progress:
            progress((i + 1) / total, f"Analyzing {qid}...")
        
        # Resume-Modus
        if not force_recalculate and state.db and state.import_id:
            existing = state.db.get_analysis_result(
                state.import_id, qid, segment_type="all"
            )
            if existing:
                result_data = existing.get('result_json')
                if isinstance(result_data, str):
                    result_data = json.loads(result_data)
                if result_data:
                    state.analysis_results[qid] = result_data
                    skipped += 1
                    success += 1
                    continue
        
        try:
            handler_type = qconfig.get('handler', 'single_choice')
            handler = get_handler(handler_type, qid, qconfig)
            if handler:
                if handler_type == 'freetext':
                    result = handler.analyze(
                        state.df, lang=lang,
                        db=state.db, import_id=state.import_id,
                    )
                else:
                    result = handler.analyze(state.df, lang=lang)
                
                if result:
                    state.analysis_results[qid] = result
                    
                    if state.db and state.import_id:
                        # safe DataFrame-to-dict conversion
                        data_val = (
                            result.data if not hasattr(result.data, 'to_dict')
                            else result.data.to_dict()
                        )
                        pct_val = (
                            result.percentages
                            if not hasattr(result.percentages, 'to_dict')
                            else result.percentages.to_dict()
                        )
                        
                        metadata_val = {}
                        if result.metadata:
                            metadata_val = dict(result.metadata)
                            if 'percentages_df' in metadata_val and \
                                    hasattr(metadata_val['percentages_df'], 'to_dict'):
                                metadata_val['percentages_df'] = (
                                    metadata_val['percentages_df'].to_dict()
                                )
                        
                        result_dict = {
                            'data': data_val,
                            'percentages': pct_val,
                            'description': result.description,
                            'warnings': result.warnings,
                            'clusters': result.clusters,
                            'metadata': metadata_val,
                        }
                        state.db.save_analysis_result(
                            import_id=state.import_id,
                            question_id=qid,
                            handler_type=handler_type,
                            result=result_dict,
                            n=result.n,
                        )
                    success += 1
        except Exception as e:
            logger.error(f"Error analysing {qid}: {e}")
            errors.append(f"{qid}: {str(e)[:50]}")
    
    # build the dropdown choices
    choices = []
    for qid, qconfig in question_groups.items():
        label = qconfig.get('label', {}).get(lang, qid)
        handler = qconfig.get('handler', 'unknown')
        marker = "✓ " if qid in state.analysis_results else ""
        choices.append(f"{qid}: {marker}{label} [{handler}]")
    
    status = f"✅ {success}/{total} questions analyzed"
    if skipped > 0:
        status += f"\n   ↳ {skipped} from the cache, {success - skipped} newly calculated"
    if errors:
        status += f"\n⚠️ {len(errors)} errors: {', '.join(errors[:3])}"
    
    return status, choices
