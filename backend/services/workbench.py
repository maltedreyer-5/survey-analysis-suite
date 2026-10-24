# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: Workbench operations, preparing all free-text questions.

The fine-grained editor functions (load_cluster_editor, save_cluster_changes,
load_items_table, ...) live in services/workbench_store.py; this service layer
only orchestrates the frequent collective actions.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


def get_freetext_questions(state) -> List[str]:
    """
    The IDs of all free-text questions of the active configuration.
    """
    if not state.config:
        return []
    return [
        qid for qid, qconfig in state.config.get('question_groups', {}).items()
        if qconfig.get('handler') == 'freetext'
    ]


def prepare_all_freetext(state) -> str:
    """
    Runs item extraction and analysis for all free-text questions.
    
    Called by the "Prepare everything" button of the Workbench.
    
    Args:
        state: AppState with df, config, db, import_id.
    
    Returns:
        status text with the results per question.
    
    Side effects:
        - the database tables response_items and analysis_results are filled
        - state.analysis_results is NOT changed (the Workbench works directly on
          the database; the interface state is updated by a separate refresh)
    """
    if state.df is None:
        return "❌ No data imported"
    if state.config is None:
        return "❌ No configuration loaded"
    
    questions = get_freetext_questions(state)
    if not questions:
        return "❌ No free-text questions in the configuration"
    
    # Lazy imports: the core module is initialised only once the pre-checks
    # have passed.
    # (These two names were imported from core, which never defined them:
    # the button failed with an ImportError before doing anything.)
    from services.item_extraction import run_item_extraction
    from services.workbench_store import get_clusters_for_question
    from handlers import get_handler
    from utils.prompts import source_language
    
    logger.info(f"prepare_all_freetext: {len(questions)} questions: {questions}")
    results = []
    
    for qid in questions:
        try:
            # 1. item extraction (first!)
            logger.info(f"Starting the extraction for {qid}")
            extraction_result, _stats = run_item_extraction(qid, force_llm=False)
            if extraction_result.startswith("✅"):
                results.append(f"✓ {qid}: extraction OK")
            else:
                results.append(f"⚠ {qid}: {extraction_result}")
            
            # 2. analysis of the extracted items
            logger.info(f"Starting the analysis for {qid}")
            qconfig = state.config.get('question_groups', {}).get(qid, {})
            handler = get_handler('freetext', qid, qconfig)
            
            if handler:
                result = handler.analyze(
                    state.df,
                    lang=source_language(),
                    db=state.db,
                    import_id=state.import_id,
                    force_items=True,
                )
                logger.info(
                    f"Analysis result: n={result.n}, "
                    f"metadata keys={result.metadata.keys() if result.metadata else 'None'}"
                )
                
                # build the result dict (DataFrame to dict)
                if hasattr(result, '__dataclass_fields__'):
                    metadata_val = {}
                    if result.metadata:
                        metadata_val = dict(result.metadata)
                        if 'percentages_df' in metadata_val and \
                                hasattr(metadata_val['percentages_df'], 'to_dict'):
                            metadata_val['percentages_df'] = (
                                metadata_val['percentages_df'].to_dict()
                            )
                    
                    result_dict = {
                        'question_id': result.question_id,
                        'n': result.n,
                        'data': (result.data.to_dict()
                                 if hasattr(result.data, 'to_dict') else result.data),
                        'percentages': (result.percentages.to_dict()
                                        if hasattr(result.percentages, 'to_dict')
                                        else result.percentages),
                        'description': result.description,
                        'clusters': result.clusters,
                        'metadata': metadata_val,
                    }
                else:
                    result_dict = result
                
                if state.db and state.import_id:
                    analysis_id = state.db.save_analysis_result(
                        state.import_id, qid, 'freetext',
                        result_dict,
                        result.n if hasattr(result, 'n') else 0,
                    )
                    logger.info(f"Analysis stored with id={analysis_id}")
                    
                    clusters = get_clusters_for_question(qid)
                    results.append(
                        f"✓ {qid}: analysis OK "
                        f"({result.n} items, {len(clusters)} clusters)"
                    )
        except Exception as e:
            results.append(f"✗ {qid}: {str(e)}")
            logger.exception(f"Error in {qid}")
    
    return "Done!\n" + "\n".join(results)
