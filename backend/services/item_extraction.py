# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Item extraction module of the survey analysis tool: extracting items from
free-text answers.
"""

import logging
from typing import List, Tuple

import gradio as gr

from core import state, load_config
from utils.item_extractor import RuleBasedExtractor, ExtractionResult
from services.item_writer import text_columns, extract_and_store_items  # noqa: F401

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


def get_freetext_questions() -> List[str]:
    """Returns the list of free-text questions."""
    if state.config is None:
        load_config()
    
    if not state.config:
        return []
    
    questions = []
    for qid, qconfig in state.config.get('question_groups', {}).items():
        if qconfig.get('handler') == 'freetext':
            label = _src_text(qconfig.get('label', {}), None, qid)
            columns = qconfig.get('columns', [])
            col_info = f" ({len(columns)} field{'s' if len(columns) > 1 else ''})" if columns else ""
            questions.append(f"{qid}: {label}{col_info}")
    return questions


def get_extraction_status_text() -> str:
    """Returns the status of all extractions."""
    if state.db is None or state.import_id is None:
        return "No data imported"
    
    try:
        status_list = state.db.get_extraction_status(state.import_id)
        
        if not status_list:
            return "No extractions yet"
        
        lines = ["### Extraction status\n"]
        
        for ext in status_list:
            lines.append(
                f"**{ext['question_id']}** ({ext['column'][:30]}...)\n"
                f"- Method: {ext['method']}\n"
                f"- Items: {ext['item_count']}\n"
                f"- Date: {ext['extracted_at'][:16]}\n"
            )
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Status error: {e}", exc_info=True)
        return f"Error: {e}"


def preview_item_extraction(
    question_choice: str,
    force_llm: bool = False
) -> Tuple[str, str]:
    """
    Shows a preview of the item extraction.
    
    Args:
        question_choice: chosen question
        force_llm: force the LLM instead of the rules
    
    Returns:
        tuple (preview table, status text)
    """
    if not question_choice:
        return "", "No question selected"
    
    if state.df is None:
        return "", "No data loaded"
    
    qid = question_choice.split(":")[0].strip()
    
    if state.config is None:
        load_config()
    
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    if not qconfig:
        return "", f"No configuration for {qid}"
    
    columns = qconfig.get('columns', [])
    if not columns:
        return "", "No columns configured"
    
    column = columns[0]
    if column not in state.df.columns:
        return "", f"Column not found: {column}"
    
    try:
        # get the LLM client and test the connection
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
        connection_ok, connection_msg = llm_client.test_connection()
        
        if force_llm and not connection_ok:
            return "", f"❌ LLM not available: {connection_msg}\n\nCheck LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env."
        
        # create the preview (at most 10 rows)
        preview_df = state.df[state.df[column].notna()].head(10)
        
        # question context for the LLM
        question_text = _src_text(qconfig.get('label', {}), None, qid)
        
        # create the extractor: ItemExtractor with LLM support if available
        if connection_ok:
            from utils.item_extractor import ItemExtractor
            extractor = ItemExtractor(
                llm_client=llm_client,
                question_context=question_text
            )
            use_item_extractor = True
        else:
            extractor = RuleBasedExtractor()
            use_item_extractor = False
        
        preview_lines = ["| Original | Extracted items |", "|----------|------------------|"]
        
        extraction_stats = {'rule': 0, 'llm': 0, 'single': 0, 'multi': 0}
        
        for _, row in preview_df.iterrows():
            full_text = str(row[column])
            
            # run the extraction
            if use_item_extractor:
                logger.info(f"Extrahiere: {full_text[:60]}...")
                result = extractor.extract_items_sync(full_text, force_llm=force_llm)
                logger.info(f"  -> {len(result.items)} Items, method={result.method}")
            else:
                result = extractor.extract(full_text)
            
            # evaluate the ExtractionResult
            if result and result.items:
                items = result.items
                
                # statistics
                if 'llm' in result.method:
                    extraction_stats['llm'] += 1
                else:
                    extraction_stats['rule'] += 1
                if len(items) > 1:
                    extraction_stats['multi'] += 1
                else:
                    extraction_stats['single'] += 1
                
                # do NOT shorten the items, show them completely
                items_str = " • ".join(items)
                # limit only the total length for the display
                if len(items_str) > 200:
                    items_str = items_str[:197] + "..."
                # escape pipe characters
                items_str = items_str.replace("|", "/")
            else:
                items_str = "(no items)"
            
            # shorten the original for the display
            original_display = full_text[:100] + "..." if len(full_text) > 100 else full_text
            original_display = original_display.replace("\n", " ↵ ").replace("|", "/")
            
            preview_lines.append(f"| {original_display} | {items_str} |")
        
        # statistics
        total = len(state.df[state.df[column].notna()])
        
        if force_llm and connection_ok:
            method_info = f"🤖 LLM extraction ({connection_msg})"
        elif connection_ok:
            method_info = f"📋 Hybrid (rules + LLM where needed)"
        else:
            method_info = f"📋 Rules only ({connection_msg})"
        
        # detailed statistics
        stats_info = f"Preview: {extraction_stats['rule']} rule, {extraction_stats['llm']} LLM | {extraction_stats['multi']} multi, {extraction_stats['single']} single"
        
        status = (
            f"✅ Preview for {total} answers\n"
            f"Column: {column}\n"
            f"Method: {method_info}\n"
            f"{stats_info}"
        )
        
        return "\n".join(preview_lines), status
        
    except Exception as e:
        logger.error(f"Preview error: {e}", exc_info=True)
        return "", f"❌ Error: {e}"


def run_item_extraction(
    question_choice: str,
    force_llm: bool = False
) -> Tuple[str, str]:
    """
    Runs the item extraction.
    
    Uses the full ItemExtractor, which:
    - extracts with rules where possible
    - uses the LLM for complex/uncertain cases
    - always uses the LLM with force_llm=True
    
    Args:
        question_choice: chosen question
        force_llm: force the LLM instead of the rules
    
    Returns:
        tuple (status text, JSON with statistics)
    """
    if not question_choice:
        return "No question selected", "{}"
    
    if state.df is None:
        return "No data loaded", "{}"
    
    if state.db is None or state.import_id is None:
        return "No database connection", "{}"
    
    qid = question_choice.split(":")[0].strip()
    
    if state.config is None:
        load_config()
    
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    if not qconfig:
        return f"No configuration for {qid}", "{}"
    
    columns = qconfig.get('columns', [])
    if not columns:
        return "No columns configured", "{}"
    
    columns = text_columns(qconfig, state.df)
    if not columns:
        return f"Column not found: {', '.join(qconfig.get('columns', []))}", "{}"
    
    try:
        # get the LLM client and test the connection
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
        
        logger.info(f"LLM-Config: base_url={llm_client.config.base_url}, model={llm_client.config.model}")
        
        # test the connection
        connection_ok, connection_msg = llm_client.test_connection()
        logger.info(f"LLM-Verbindungstest: ok={connection_ok}, msg={connection_msg}")
        
        if force_llm and not connection_ok:
            return f"❌ LLM not available: {connection_msg}\n\nCheck LLM_BASE_URL and LLM_MODEL in .env.", "{}"
        
        if not connection_ok:
            logger.warning(f"LLM not available: {connection_msg} - using the rule-based extraction only")
        
        # question context for a better LLM extraction
        question_text = _src_text(qconfig.get('label', {}), None, qid)
        
        # create the ItemExtractor (rules plus LLM)
        from utils.item_extractor import ItemExtractor
        extractor = ItemExtractor(
            llm_client=llm_client if connection_ok else None,
            db=state.db,
            question_context=question_text
        )
        
        effective_force_llm = force_llm and connection_ok
        logger.info(f"Starting the extraction for {qid}, fields {columns} (force_llm={force_llm}, connection_ok={connection_ok}, effective={effective_force_llm})")
        ex = extract_and_store_items(extractor, state.db, state.import_id, qid, state.df, columns,
                                     force_llm=effective_force_llm)
        n_answers = ex['answers']
        total_items = ex['items']
        methods_count = ex['methods']
        llm_calls = ex['llm_calls']
        llm_errors = ex['llm_errors']
        
        # statistics
        stats = {
            'question_id': qid,
            'columns': columns,
            'responses': n_answers,
            'respondents': ex['respondents'],
            'items_extracted': total_items,
            'avg_items_per_response': round(total_items / n_answers, 2) if n_answers > 0 else 0,
            'methods': methods_count,
            'force_llm': force_llm,
            'llm_calls': llm_calls,
            'llm_errors': llm_errors
        }
        
        import json
        
        # format the distribution of methods
        methods_str = "\n".join(
            f"- {method}: {count} ({round(count/n_answers*100, 1)}%)" 
            for method, count in sorted(methods_count.items(), key=lambda x: -x[1])
        )
        
        if force_llm and connection_ok:
            llm_info = f"🤖 LLM mode\n   {llm_calls} LLM calls"
            if llm_errors > 0:
                llm_info += f", {llm_errors} errors"
        elif connection_ok:
            llm_info = f"📋 Hybrid mode (rules + LLM where needed)\n   {llm_calls} LLM calls"
        else:
            llm_info = "📋 Rules only (LLM not available)"
        
        return (
            f"✅ Extraction finished!\n\n"
            f"{llm_info}\n\n"
            f"📊 {n_answers} answers processed\n"
            f"📝 {total_items} items extracted\n"
            f"📈 Ø {stats['avg_items_per_response']} items/answer\n\n"
            f"### Methods:\n{methods_str}",
            json.dumps(stats, indent=2)
        )
        
    except Exception as e:
        logger.error(f"Extraction error: {e}", exc_info=True)
        return f"❌ Error: {e}", "{}"


def run_all_extractions(
    force_llm: bool = False,
    thematic_mode: bool = True,
    validate: bool = True,
    progress=gr.Progress()
) -> Tuple[str, str]:
    """
    Runs the item extraction for ALL free-text questions.
    
    Args:
        force_llm: force the LLM instead of the rules
        thematic_mode: thematic splitting (different topics -> separate items, details kept)
        validate: LLM validation plus a retry if the extraction is too abstract
        progress: Gradio progress tracker
    
    Returns:
        tuple (status text, JSON with the overall statistics)
    """
    import json
    
    if state.df is None:
        return "❌ No data loaded", "{}"
    
    if state.db is None or state.import_id is None:
        return "❌ No database connection", "{}"
    
    if state.config is None:
        load_config()
    
    if not state.config:
        return "❌ No configuration loaded", "{}"
    
    # collect all free-text questions (only those in item mode)
    freetext_questions = []
    skipped_raw = []
    for qid, qconfig in state.config.get('question_groups', {}).items():
        if qconfig.get('handler') == 'freetext':
            columns = qconfig.get('columns', [])
            if not columns:
                continue
            # skip questions with clustering.mode='raw': they are clustered as
            # whole answers, not split into items
            clustering_mode = qconfig.get('clustering', {}).get('mode', 'auto')
            if clustering_mode == 'raw':
                skipped_raw.append(qid)
                logger.info(f"Item extraction skipped for {qid} (clustering.mode=raw)")
                continue
            freetext_questions.append({
                'qid': qid,
                'label': _src_text(qconfig.get('label', {}), None, qid),
                'columns': text_columns(qconfig, state.df),
                'config': qconfig
            })
    
    if skipped_raw:
        logger.info(f"Item extraction: {len(skipped_raw)} questions with mode=raw skipped: {skipped_raw}")
    
    if not freetext_questions:
        return "❌ No free-text questions in the configuration", "{}"
    
    total_questions = len(freetext_questions)
    logger.info(f"Starting the batch extraction for {total_questions} free-text questions")
    
    # total number of answers for the progress
    total_responses = 0
    for q_info in freetext_questions:
        for column in q_info['columns']:
            total_responses += int(state.df[column].notna().sum())
    
    progress(0, desc=f"Starting extraction for {total_questions} questions...")
    
    try:
        # get the LLM client and test the connection
        from utils.llm import get_llm_client
        from utils.item_extractor import ItemExtractor
        
        llm_client = get_llm_client()
        connection_ok, connection_msg = llm_client.test_connection()
        
        logger.info(f"LLM connection: ok={connection_ok}, msg={connection_msg}")
        
        if force_llm and not connection_ok:
            return f"❌ LLM not available: {connection_msg}", "{}"
        
        # overall statistics
        total_stats = {
            'questions_processed': 0,
            'questions_failed': 0,
            'total_responses': 0,
            'total_items': 0,
            'llm_calls': 0,
            'by_question': {}
        }
        
        errors = []
        processed_responses = 0
        
        # for each free-text question
        for i, q_info in enumerate(freetext_questions):
            qid = q_info['qid']
            columns = q_info['columns']
            question_text = q_info['label']
            
            progress(
                processed_responses / max(total_responses, 1), 
                desc=f"[{i+1}/{total_questions}] {qid}"
            )
            
            logger.info(f"Extrahiere {qid} ({i+1}/{total_questions})")
            
            if not columns:
                errors.append(f"{qid}: column '{', '.join(q_info['config'].get('columns', []))}' not found")
                total_stats['questions_failed'] += 1
                continue
            
            try:
                # create the ItemExtractor
                extractor = ItemExtractor(
                    llm_client=llm_client if connection_ok else None,
                    db=state.db,
                    question_context=question_text,
                    thematic_mode=thematic_mode
                )
                
                effective_force_llm = force_llm and connection_ok
                n_responses = sum(int(state.df[c].notna().sum()) for c in columns)
                
                def on_answer(done, i=i, qid=qid, n_responses=n_responses):
                    # progress per answer (every 10 answers)
                    if done % 10 == 0:
                        progress(
                            (processed_responses + done) / max(total_responses, 1),
                            desc=f"[{i+1}/{total_questions}] {qid}: {done+1}/{n_responses}"
                        )
                
                ex = extract_and_store_items(extractor, state.db, state.import_id, qid, state.df, columns,
                                             force_llm=effective_force_llm, validate=validate,
                                             on_answer=on_answer)
                items_count = ex['items']
                llm_calls = ex['llm_calls']
                methods_count = ex['methods']
                
                processed_responses += n_responses
                
                # statistics of this question
                total_stats['questions_processed'] += 1
                total_stats['total_responses'] += n_responses
                total_stats['total_items'] += items_count
                total_stats['llm_calls'] += llm_calls
                total_stats['by_question'][qid] = {
                    'responses': n_responses,
                    'respondents': ex['respondents'],
                    'items': items_count,
                    'avg_items': round(items_count / n_responses, 2) if n_responses > 0 else 0,
                    'methods': methods_count,
                    'llm_calls': llm_calls
                }
                
                logger.info(f"  -> {items_count} items from {n_responses} answers")
                
            except Exception as e:
                logger.error(f"Error in {qid}: {e}", exc_info=True)
                errors.append(f"{qid}: {str(e)}")
                total_stats['questions_failed'] += 1
        
        progress(1.0, desc="Done!")
        
        # build the result text
        avg_items = round(total_stats['total_items'] / total_stats['total_responses'], 2) if total_stats['total_responses'] > 0 else 0
        
        if connection_ok:
            llm_info = f"🤖 LLM available ({total_stats['llm_calls']} calls)"
        else:
            llm_info = "📋 Rules only"
        
        result_lines = [
            f"✅ Batch extraction finished!\n",
            f"{llm_info}\n",
            f"📊 **{total_stats['questions_processed']}** questions processed",
            f"📝 **{total_stats['total_items']}** items from **{total_stats['total_responses']}** answers",
            f"📈 Ø **{avg_items}** items/answer\n",
            "### Details per question:"
        ]
        
        for qid, q_stats in total_stats['by_question'].items():
            result_lines.append(f"- **{qid}**: {q_stats['items']} items from {q_stats['responses']} answers (Ø {q_stats['avg_items']})")
        
        if errors:
            result_lines.append(f"\n### ⚠️ Errors ({len(errors)}):")
            for err in errors[:5]:
                result_lines.append(f"- {err}")
            if len(errors) > 5:
                result_lines.append(f"- ... and {len(errors) - 5} more")
        
        return "\n".join(result_lines), json.dumps(total_stats, indent=2)
        
    except Exception as e:
        logger.error(f"Batch extraction error: {e}", exc_info=True)
        return f"❌ Error: {e}", "{}"
