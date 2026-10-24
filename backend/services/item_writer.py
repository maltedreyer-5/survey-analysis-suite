# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Writing extracted free-text items: one writer for the pipeline, the
item-extraction tab and the workbench preparation. Free of the user
interface (no gradio), like the rest of the service layer.
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


def text_columns(qconfig: dict, df) -> List[str]:
    """Configured text fields of a free-text question that are present in the data."""
    return [c for c in (qconfig.get('columns') or []) if c in df.columns]


LLM_METHODS = ('llm', 'llm_fallback', 'llm_error', 'llm_thematic', 'llm_retry')


def extract_and_store_items(extractor, db, import_id: int, qid: str, df, columns: List[str],
                            force_llm: bool = False, validate: bool = False,
                            on_answer=None) -> dict:
    """
    Extract the items of one free-text question from all its text fields and
    store them, replacing earlier items of these fields.

    Every filled field of a respondent is one answer ("up to three reasons"
    as three fields, a form); its items are stored under the data row
    (response_items.row_index = DataFrame index = raw_responses.row_index),
    so that readers find the respondent of each item. The one writer for the
    pipeline, the item-extraction tab and the workbench preparation.

    on_answer(n): optional callback with the number of answers done so far.

    Returns counts: answers (filled fields), respondents, items, methods,
    llm_calls, llm_errors, retries.
    """
    stats = {'answers': 0, 'respondents': 0, 'items': 0, 'methods': {},
             'llm_calls': 0, 'llm_errors': 0, 'retries': 0}
    rows = set()
    for column in columns:
        db.delete_items(import_id, qid, column)
        for idx, row in df[df[column].notna()].iterrows():
            if on_answer:
                on_answer(stats['answers'])
            original = str(row[column])
            try:
                result = extractor.extract_items_sync(original, force_llm=force_llm, validate=validate)
            except Exception as e:
                logger.warning(f"Extraction failed for {qid} row {idx}: {e}")
                result = None
                if 'llm' in str(e).lower():
                    stats['llm_errors'] += 1
            if result and result.method in LLM_METHODS:
                stats['llm_calls'] += 2 if 'retry' in result.method else 1
            if result and 'retry' in result.method:
                stats['retries'] += 1
            if result and result.items:
                items, method = result.items, result.method
            else:
                items, method = ([original.strip()] if original.strip() else []), 'fallback'
            stats['methods'][method] = stats['methods'].get(method, 0) + 1
            stats['answers'] += 1
            for item_idx, item in enumerate(items):
                if item.strip():
                    db.save_response_item(
                        import_id=import_id,
                        row_index=int(idx),
                        question_id=qid,
                        column_name=column,
                        item_index=item_idx,
                        original_text=original,
                        extracted_text=item.strip(),
                        extraction_method=method,
                        extraction_confidence=result.confidence if result else 0.5,
                    )
                    stats['items'] += 1
                    rows.add(idx)
    stats['respondents'] = len(rows)
    return stats
