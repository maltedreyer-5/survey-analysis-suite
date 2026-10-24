# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Export of the JSON files read by the dashboard.
"""

import logging
import json
from pathlib import Path
from typing import List, Tuple
from datetime import datetime

import pandas as pd
import gradio as gr

from core import state, load_config, OUTPUT_DIR
from .freetext_segments import _enrich_freetext_question, _get_item_response_mapping
from .json_utils import NumpyJSONEncoder, sanitize_for_json
from .keys import _build_options_with_keys, _convert_all_result_keys, _convert_matrix_result_keys, _create_source_to_key_map, _create_option_key, _get_translator, _limit_keywords
from .result_format import _format_result_for_dashboard
from .segment_context import get_context
from .segments import _calculate_segment_result, _get_segment_combinations, _load_cross_segment_results, _load_segmented_results, _make_cross_segment_key

logger = logging.getLogger(__name__)


# =============================================================================
# DASHBOARD-EXPORT
# =============================================================================

def _active_survey_id():
    try:
        from core import get_active_survey_id
        return get_active_survey_id()
    except Exception:
        return None


def export_dashboard_data(
    include_segments: bool = True,
    languages: List[str] = None,
    output_dir: Path = None,
    progress=gr.Progress()
) -> Tuple[str, str]:
    """
    Exports all data for the dashboard in JSON format.
    
    The dashboard is independent of the analysis app and needs:
    - dashboard_data.json: all analysis results
    - themes.json: theme assignments
    - correlations.json: statistical associations
    
    Args:
        include_segments: whether segmented analyses are included
        languages: export languages (default: all)
        output_dir: target directory (optional, otherwise a new one is created)
        progress: Gradio progress tracker
    
    Returns:
        tuple (status text, path of the export directory)
    """
    try:
        if state.df is None:
            return "❌ No data imported", ""
        
        if state.config is None:
            load_config()
        
        if not state.config:
            return "❌ No configuration loaded", ""
        
        # normalise the languages (list or None)
        if languages is None or len(languages) == 0:
            from utils.languages import get_output_languages
            languages = get_output_languages(state.config)
        
        # call the progress function safely
        def safe_progress(val, desc=""):
            try:
                progress(val, desc=desc)
            except:
                pass  # ignore progress errors
        
        safe_progress(0, "Starting the dashboard export...")
        
        # export directory: the given one, or a new one
        if output_dir is not None:
            export_dir = Path(output_dir)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_dir = OUTPUT_DIR / f"dashboard_export_{timestamp}"
        
        export_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Dashboard export to: {export_dir}")
        
        # metadata from the configuration
        survey_metadata = state.config.get('settings', {}).get('survey_metadata', {})
        
        # survey title from the configuration
        # (no default: a missing title stays empty instead of naming another survey)
        survey_title = survey_metadata.get('survey_title', {})
        
        # survey year from the configuration; empty if not given
        survey_year = survey_metadata.get('survey_year')
        
        # number of responses from the DataFrame
        total_responses = len(state.df) if state.df is not None else 0
        
        # use the import metadata if available
        if state.db and state.import_id:
            import_info = state.db.get_latest_import(state.project_id)
            if import_info:
                total_responses = import_info.get('total_responses', total_responses)
                # file name for the logging
                filename = import_info.get('filename', '')
                if filename:
                    logger.info(f"Export based on the import: {filename}")
        
        # collect all data
        dashboard_data = {
            "metadata": {
                "survey_id": _active_survey_id(),
                "survey_title": survey_title,
                "survey_year": survey_year,
                # start page, summary view and contact (all optional, per language
                # except contact): settings.survey_metadata
                **{k: survey_metadata[k] for k in ("survey_subtitle", "start_image", "publication_url",
                                                    "survey_period", "about", "summary", "contact", "links")
                   if survey_metadata.get(k)},
                "total_responses": total_responses,
                "export_date": datetime.now().isoformat(),
                # Data format version (contract with the dashboard), not the
                # application version. 2.0: segment keys derived from values,
                # configured cross filters, thresholds and languages below.
                "version": "2.0",
                "n": total_responses,
                "source_language": get_context().source_language,
                # Only the languages this export contains (configured order first:
                # the first one is the dashboard default). Announcing a configured
                # but not exported language would offer it with fallback texts.
                "languages": ([l for l in get_context().languages if l in languages]
                              + [l for l in languages if l not in get_context().languages]),
                "min_n": get_context().resolver.min_n,
                "warning_n": int(((state.config or {}).get('settings', {})
                                  .get('sample_warnings', {}) or {}).get('warning_threshold', 10)),
                "cross_filter_support": bool(get_context().resolver.cross_filters),
                "cross_filter_min_n": get_context().resolver.cross_filter_min_n,
                "cross_filters": [list(c) for c in get_context().resolver.cross_filters],
            },
            "segments": _build_segments_metadata(),
            "questions": {}
        }
        
        themes_data = {
            "metadata": {
                "version": "1.0",
                "created": datetime.now().strftime("%Y-%m-%d")
            },
            "themes": {}
        }
        
        # questions from the configuration
        question_groups = state.config.get('question_groups', {})
        total_questions = len(question_groups)
        exported_questions = 0
        warnings = []
        
        safe_progress(0.1, "Exporting questions...")
        
        # progress callback for the segment computation
        current_progress = [0.1]  # mutable for the closure
        def segment_progress_callback(message):
            # update the progress with the segment information
            safe_progress(current_progress[0], message)
        
        for i, (qid, qconfig) in enumerate(question_groups.items()):
            current_progress[0] = 0.1 + (i / total_questions) * 0.7
            safe_progress(current_progress[0], f"Exporting: {qid}")
            
            try:
                question_data = _export_question_for_dashboard(
                    qid, qconfig, languages, include_segments,
                    progress_callback=segment_progress_callback if include_segments else None
                )
                
                if question_data:
                    dashboard_data["questions"][qid] = question_data
                    exported_questions += 1
                else:
                    handler_type = qconfig.get('handler', qconfig.get('type', 'unknown'))
                    warnings.append(f"{qid} ({handler_type}): no data; check the analysis logs")
                    logger.warning(f"Export of {qid} ({handler_type}) returned no data")
                    
            except Exception as e:
                warnings.append(f"{qid}: {str(e)}")
                logger.exception(f"Error exporting {qid}")
        
        safe_progress(0.8, "Exporting themes...")
        
        # export the themes
        themes_data["themes"] = _export_themes_for_dashboard(languages)
        themes_data["metadata"]["total_themes"] = len(themes_data["themes"])
        
        safe_progress(0.85, "Calculating correlations...")
        
        # compute and export the correlations
        correlations_data = _export_correlations_for_dashboard(languages, progress)
        
        safe_progress(0.95, "Writing files...")
        
        # write the files
        dashboard_file = export_dir / "dashboard_data.json"
        themes_file = export_dir / "themes.json"
        correlations_file = export_dir / "correlations.json"
        
        with open(dashboard_file, 'w', encoding='utf-8') as f:
            json.dump(sanitize_for_json(dashboard_data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        
        with open(themes_file, 'w', encoding='utf-8') as f:
            json.dump(sanitize_for_json(themes_data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        
        with open(correlations_file, 'w', encoding='utf-8') as f:
            json.dump(sanitize_for_json(correlations_data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        
        # Question order and groups of the survey (settings.report_groups); the
        # dashboard lists questions in this order ("all questions" view)
        with open(export_dir / "survey_structure.json", 'w', encoding='utf-8') as f:
            json.dump(sanitize_for_json(_survey_structure(languages)), f, ensure_ascii=False, indent=2,
                      cls=NumpyJSONEncoder)
        
        safe_progress(1.0, "Done!")
        
        # correlation statistics
        corr_count = correlations_data.get('summary', {}).get('significant_correlations_found', 0)
        
        # status message
        meta = dashboard_data.get('metadata', {})
        from utils.languages import localized
        from .keys import _source_language
        survey_title = localized(meta.get('survey_title', {}), _source_language(), _source_language(), default='') or '–'
        status_lines = [
            "✅ Dashboard export successful!",
            "",
            f"📋 Survey: {survey_title}",
            f"👥 Respondents: {meta.get('total_responses', 0)}",
            f"📊 {exported_questions} of {total_questions} questions exported",
            f"🏷️ {len(themes_data['themes'])} themes exported",
            f"🔗 {corr_count} significant correlations",
            f"🌐 Languages: {', '.join(languages)}",
        ]
        
        if include_segments:
            seg_keys = list(dashboard_data.get('segments', {}).keys())
            status_lines.append(f"📈 Segments: {', '.join([f'by_{k}' for k in seg_keys])}")
            
            # show the segment values (key-based)
            for seg_key, seg_data in dashboard_data.get('segments', {}).items():
                values = seg_data.get('values', [])
                # extract the keys from the {key, label} objects
                value_keys = [v.get('key', str(v)) if isinstance(v, dict) else str(v) for v in values[:3]]
                status_lines.append(f"   - {seg_key}: {len(values)} values ({', '.join(value_keys)}{'...' if len(values) > 3 else ''})")
            
            # configured cross filters
            cross_filter_keys = ["by_" + "_".join(combo) for combo in get_context().resolver.cross_filters]
            status_lines.append(f"🔀 Cross filters: {', '.join(cross_filter_keys) or '-'}")
            status_lines.append(f"   - minimum n: {get_context().resolver.cross_filter_min_n}")
        
        if warnings:
            status_lines.append("")
            status_lines.append(f"⚠️ {len(warnings)} warnings:")
            for w in warnings[:5]:
                status_lines.append(f"   - {w}")
            if len(warnings) > 5:
                status_lines.append(f"   ... and {len(warnings) - 5} more")
        
        status_lines.append("")
        status_lines.append("📁 Exported files:")
        status_lines.append(f"   - {dashboard_file}")
        status_lines.append(f"   - {themes_file}")
        status_lines.append(f"   - {correlations_file}")
        
        return "\n".join(status_lines), str(export_dir)
        
    except Exception as e:
        logger.exception("Dashboard export error")
        return f"❌ Export error: {e}", ""


def _build_segments_metadata() -> dict:
    """
    Segment metadata for the dashboard:
    {dim_id: {"label": {lang: ...}, "values": [{"key": ..., "label": {lang: ...}}]}}
    """
    if state.config is None:
        logger.warning("No configuration for segments")
        return {}
    return get_context().segments_metadata()


def _export_question_for_dashboard(
    qid: str,
    qconfig: dict,
    languages: List[str],
    include_segments: bool,
    progress_callback=None
) -> dict:
    """
    Exports a single question for the dashboard.
    
    Args:
        qid: question id
        qconfig: question configuration
        languages: languages to export
        include_segments: whether segments are included
        progress_callback: optional callback(message) for the progress display
    
    Returns:
        dictionary with the question data for the dashboard
    """
    handler_type = qconfig.get('handler', qconfig.get('type', 'single_choice'))
    
    # base structure
    question_data = {
        "id": qid,
        "type": handler_type,
        "text": {},       # short title (label)
        "full_text": {},  # full question text (full_question)
        "help": {},       # explanation of the question
        "results": {}
    }
    
    # Short title, full question and help in all languages; a missing language
    # falls back to the source language (localized), never to a fixed one
    from utils.languages import localized
    from .keys import _source_language
    src = _source_language()
    for field, key in (("text", "label"), ("full_text", "full_question"), ("help", "help")):
        value = qconfig.get(key, {})
        for lang in languages:
            text_value = localized(value, lang, src, default="") if value else ""
            if field == "full_text" and not text_value:
                text_value = question_data["text"].get(lang, '')  # fall back to label
            if text_value or field == "full_text":
                question_data[field][lang] = text_value
    
    # load the analysis result from the state or the database
    result = None
    force_recalculate = False
    
    # first try the state
    if qid in state.analysis_results:
        result = state.analysis_results[qid]
        logger.info(f"  {qid}: result loaded from state.analysis_results")
    
    # then the database (overall result, segment_type="all")
    if result is None and state.db and state.import_id:
        db_result = state.db.get_analysis_result(state.import_id, qid, segment_type="all")
        if db_result:
            result = db_result.get('result_json')
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                    logger.info(f"  {qid}: result loaded from the database")
                except json.JSONDecodeError:
                    result = None
    
    # log if no result was found
    if result is None:
        logger.info(f"  {qid}: no result in state.analysis_results or the database - calculating on the fly")
    
    # free text: can the answer_ids in cluster_details be mapped?
    # NOT in raw mode: there the answer_ids are DataFrame indices, no item mapping needed
    analysis_mode = "items"  # Default
    if result and handler_type == 'freetext' and state.db and state.import_id:
        logger.info(f"  {qid}: validating the cluster_details...")
        # result may be an AnalysisResult object or a dict
        if hasattr(result, 'metadata'):
            # AnalysisResult object
            metadata = result.metadata if result.metadata else {}
            logger.info(f"  {qid}: result is an AnalysisResult, metadata keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'N/A'}")
        elif isinstance(result, dict):
            metadata = result.get('metadata', {})
            logger.info(f"  {qid}: result is a dict, metadata keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'N/A'}")
        else:
            metadata = {}
            logger.info(f"  {qid}: result is {type(result)}, no metadata")
        
        # analysis mode from the metadata or the configuration
        analysis_mode = (metadata.get('analysis_mode', 'items') if isinstance(metadata, dict) else 'items')
        if analysis_mode == 'items':
            # fallback: check the configuration
            clustering_mode = qconfig.get('clustering', {}).get('mode', 'auto')
            if clustering_mode == 'raw':
                analysis_mode = 'raw'
        
        cluster_details = metadata.get('cluster_details', []) if isinstance(metadata, dict) else []
        
        if cluster_details and analysis_mode != 'raw':
            # collect ALL answer_ids of all clusters
            all_answer_ids = []
            for cd in cluster_details:
                if isinstance(cd, dict):
                    all_answer_ids.extend(cd.get('answer_ids', []))
            
            if all_answer_ids:
                # how many ids exist in response_items?
                item_to_response = _get_item_response_mapping(qid)
                
                if item_to_response:
                    matched = sum(1 for aid in all_answer_ids 
                                  if aid in item_to_response 
                                  or str(aid) in item_to_response 
                                  or (isinstance(aid, str) and aid.isdigit() and int(aid) in item_to_response))
                    
                    match_ratio = matched / len(all_answer_ids) if all_answer_ids else 0
                    
                    # if fewer than 80% match, force a recalculation
                    # (50% was too low: wrong data was accepted)
                    if match_ratio < 0.8:
                        logger.warning(f"  {qid}: only {matched}/{len(all_answer_ids)} ({match_ratio:.0%}) answer_ids match - forcing a recalculation")
                        logger.warning(f"    example answer_ids: {all_answer_ids[:5]}")
                        logger.warning(f"    the mapping has {len(item_to_response)} entries, example keys: {list(item_to_response.keys())[:5]}")
                        force_recalculate = True
                        result = None
                        # remove from the state too, so it is calculated afresh
                        if qid in state.analysis_results:
                            del state.analysis_results[qid]
                    else:
                        logger.info(f"  {qid}: {matched}/{len(all_answer_ids)} ({match_ratio:.0%}) answer_ids match - OK")
                else:
                    logger.warning(f"  {qid}: no item_to_response mapping - skipping the validation")
            else:
                logger.warning(f"  {qid}: cluster_details present ({len(cluster_details)} clusters) but no answer_ids - forcing a recalculation")
                force_recalculate = True
                result = None
                if qid in state.analysis_results:
                    del state.analysis_results[qid]
        elif cluster_details and analysis_mode == 'raw':
            # raw mode: the answer_ids are DataFrame indices, no item validation needed
            logger.info(f"  {qid}: raw mode with {len(cluster_details)} clusters - validation skipped (answer_ids = DataFrame indices)")
        else:
            # no cluster_details: free-text questions should have them
            logger.warning(f"  {qid}: no cluster_details in the metadata - forcing a recalculation")
            force_recalculate = True
            result = None
            if qid in state.analysis_results:
                del state.analysis_results[qid]
    
    # no result or a forced recalculation: calculate on the fly
    if result is None or force_recalculate:
        reason = "recalculation forced (answer_ids do not match)" if force_recalculate else "not in state.analysis_results and not in the database"
        logger.info(f"Calculating the result of {qid} on the fly ({reason})")
        try:
            result = _calculate_segment_result(qid, qconfig, "all", None, None)
            if result:
                logger.info(f"  On-the-fly calculation of {qid} successful: n={result.get('n', 'unknown')}")
                
                # IMPORTANT: store the result in the database too, for later use (e.g. loading clusters)
                if state.db and state.import_id:
                    try:
                        state.db.save_analysis_result(
                            import_id=state.import_id,
                            question_id=qid,
                            handler_type=handler_type,
                            result=result,
                            n=result.get('n', 0),
                            segment_type="all",
                            segment_value=None
                        )
                        logger.info(f"  On-the-fly result of {qid} stored in the database")
                    except Exception as e:
                        logger.warning(f"  Could not store the on-the-fly result in the database: {e}")
                
                # cache in the state too, for later calls
                state.analysis_results[qid] = result
                        
        except Exception as e:
            logger.warning(f"On-the-fly calculation of {qid} failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    if result is None:
        logger.warning(f"No result available for {qid} (handler={handler_type})")
        return None
    
    # convert the result to a dict if needed
    if hasattr(result, '__dataclass_fields__'):
        # convert DataFrame to dict
        data = result.data
        if hasattr(data, 'to_dict'):
            data = data.to_dict()
        
        percentages = result.percentages
        if hasattr(percentages, 'to_dict'):
            percentages = percentages.to_dict()
        elif percentages is None:
            percentages = {}
        
        # copy the metadata and convert DataFrames
        metadata = dict(result.metadata) if result.metadata else {}
        if 'percentages_df' in metadata and hasattr(metadata['percentages_df'], 'to_dict'):
            metadata['percentages_df'] = metadata['percentages_df'].to_dict()
        
        result = {
            'n': result.n,
            'data': data,
            'percentages': percentages,
            'metadata': metadata
            # do NOT store the clusters: the cluster details are in metadata.cluster_details
        }
    
    # IMPORTANT: make sure the result is in the database
    # (get_clusters_for_question needs it for free text, and it keeps things consistent)
    if state.db and state.import_id and isinstance(result, dict):
        db_check = state.db.get_analysis_result(state.import_id, qid, segment_type="all")
        if not db_check:
            try:
                state.db.save_analysis_result(
                    import_id=state.import_id,
                    question_id=qid,
                    handler_type=handler_type,
                    result=result,
                    n=result.get('n', 0),
                    segment_type="all",
                    segment_value=None
                )
                logger.info(f"  {qid}: result stored in the database (was only in the memory cache)")
            except Exception as e:
                logger.warning(f"  {qid}: could not store the result in the database: {e}")
    
    # overall result
    question_data["results"]["all"] = _format_result_for_dashboard(result, handler_type)
    
    # segmented results, also without a database
    if include_segments:
        dim_ids = get_context().dimension_ids()
        logger.info(f"Exporting the segments of {qid}: {dim_ids}")
        for seg_key in dim_ids:
            result_key = f"by_{seg_key}"
            if handler_type == 'freetext':
                # Filled by _enrich_freetext_question from the global clusters
                # (counted per segment), see export.freetext_segments.
                question_data["results"][result_key] = {}
                continue
            seg_results = _load_segmented_results(qid, seg_key, progress_callback)
            # ALWAYS add the segment key, even if empty (the dashboard expects it)
            question_data["results"][result_key] = seg_results or {}
            if seg_results:
                logger.info(f"  {qid}: {result_key} = {len(seg_results)} values")
            else:
                logger.warning(f"  {qid}: {result_key} = EMPTY (no data calculated)")
        
        # === CROSS FILTERS (2-way and 3-way combinations) ===
        # only for structured questions (not for free text)
        if handler_type != 'freetext':
            logger.info(f"Calculating the cross filters of {qid} (handler={handler_type})...")
            cross_filter_count = 0
            
            for segment_combo in _get_segment_combinations():
                cross_key = _make_cross_segment_key(segment_combo)
                logger.debug(f"  {qid}: calculating {cross_key} for the combination {segment_combo}...")
                try:
                    cross_results = _load_cross_segment_results(qid, segment_combo, progress_callback)
                    
                    if cross_results:
                        question_data["results"][cross_key] = cross_results
                        cross_filter_count += len(cross_results)
                        logger.info(f"  {qid}: {cross_key} = {len(cross_results)} combinations")
                    else:
                        # empty dict for missing cross filters
                        question_data["results"][cross_key] = {}
                        # IMPORTANT: log empty results too
                        logger.warning(f"  {qid}: {cross_key} = EMPTY (no combinations with n>={get_context().resolver.cross_filter_min_n})")
                except Exception as e:
                    logger.warning(f"  {qid}: {cross_key} error: {e}")
                    question_data["results"][cross_key] = {}
            
            logger.info(f"  {qid}: cross filters finished - {cross_filter_count} combinations in total")
        else:
            logger.info(f"  {qid}: cross filters skipped (free-text question)")
    
    # type-specific extensions
    if handler_type == 'freetext':
        question_data = _enrich_freetext_question(question_data, result, qconfig, languages)
    elif handler_type in ['single_choice', 'multi_choice', 'multi_choice_binary', 'ranking']:
        question_data = _enrich_choice_question(question_data, qconfig, languages)
    elif handler_type in ['matrix_likert', 'cooperation_matrix']:
        question_data = _enrich_matrix_question(question_data, qconfig, languages)
    elif handler_type == 'numeric_matrix':
        question_data = _enrich_numeric_matrix_question(question_data, qconfig, languages)
    elif handler_type == 'date':
        # month names per language for the period keys of all results
        from handlers.date import period_labels
        keys, gran = set(), None
        for res in (question_data.get("results") or {}).values():
            for r in ([res] if "values" in res else list(res.values())):
                if isinstance(r, dict):
                    keys |= set((r.get("values") or {}).keys())
                    gran = gran or (r.get("date") or {}).get("granularity")
        question_data["period_labels"] = period_labels(sorted(keys), gran or "month", languages)
    
    # ranking questions: add the score explanation
    if handler_type == 'ranking':
        from handlers.ranking import RankingHandler
        handler = RankingHandler(qconfig)
        
        # num_ranks from the metadata or the configuration
        num_ranks = 9  # Default
        if result:
            metadata = result.metadata if hasattr(result, 'metadata') else (result.get('metadata', {}) if isinstance(result, dict) else {})
            num_ranks = metadata.get('num_ranks', 9) if isinstance(metadata, dict) else 9
        
        # score explanation in all languages
        question_data["score_explanation"] = {}
        for lang in languages:
            question_data["score_explanation"][lang] = handler.get_score_explanation(num_ranks, lang)
    
    return question_data


def _enrich_choice_question(
    question_data: dict,
    qconfig: dict,
    languages: List[str]
) -> dict:
    """
    Adds the options in all languages to a choice question.
    
    Format with stable keys:
    - options: [{key: "...", label: {de: "...", fr: "...", en: "..."}}]
    
    Supports:
    - single_choice: options = {de: [...], fr: [...], en: [...]}
    - multi_choice: options = {de: [...], fr: [...], en: [...]}
    - multi_choice_binary: item_labels = {de: [...], fr: [...], en: [...]}
    """
    # options from 'options' or 'item_labels' (for multi_choice_binary)
    options_config = qconfig.get('options', {}) or qconfig.get('item_labels', {})
    
    # list of {key, label}
    options_with_keys = _build_options_with_keys(qconfig, languages)
    question_data["options"] = options_with_keys
    # ordered scale: charts keep the configured order instead of sorting by frequency
    question_data["ordinal"] = bool(qconfig.get('ordinal') or qconfig.get('sort_order'))
    
    # mapping for the result conversion
    source_to_key = _create_source_to_key_map(options_with_keys)
    
    # convert the result keys (from labels in the source language to stable keys)
    if "results" in question_data:
        _convert_all_result_keys(question_data["results"], source_to_key)
    
    # colours if defined (option_colors or colors)
    colors = qconfig.get('option_colors', {}) or qconfig.get('colors', {})
    if colors:
        # colours with keys as well
        colors_with_keys = {}
        for de_label, color in colors.items():
            key = source_to_key.get(de_label, _create_option_key(de_label))
            colors_with_keys[key] = color
        question_data["colors"] = colors_with_keys
    
    return question_data


def _survey_structure(languages: List[str]) -> dict:
    """Groups of the survey in their order with their questions (settings.report_groups)."""
    from utils.languages import localized
    from .keys import _source_language
    src = _source_language()
    groups_cfg = ((state.config or {}).get("settings") or {}).get("report_groups") or {}
    known = set((state.config or {}).get("question_groups") or {})
    groups = []
    for gid, g in sorted(groups_cfg.items(), key=lambda kv: (kv[1] or {}).get("order", 0)):
        g = g or {}
        groups.append({"id": gid, "order": g.get("order", 0),
                       "label": {lang: str(localized(g.get("label") or {}, lang, src, default=gid) or gid)
                                 for lang in languages},
                       "questions": [q for q in (g.get("questions") or []) if q in known]})
    return {"metadata": {"source": "settings.report_groups"}, "groups": groups}


def _enrich_numeric_matrix_question(question_data: dict, qconfig: dict, languages: List[str]) -> dict:
    """
    Fields of a numeric_matrix question: key f<i> (position in ``columns``),
    label per language from ``item_labels`` (else the item name of the column),
    plus unit, decimals and the constant sum, if configured.
    """
    from handlers.numeric_matrix import NumericMatrixHandler
    from .keys import _source_language, labels_per_language
    src = _source_language()
    handler = NumericMatrixHandler({**qconfig, "id": question_data.get("id", "")})
    labels = labels_per_language(qconfig.get("item_labels") or {}, languages, src)
    items = []
    for i, col in enumerate(qconfig.get("columns") or []):
        label = labels[i] if i < len(labels) else {}
        name = handler.extract_item_name(col)
        items.append({"key": f"f{i}", "label": {lang: label.get(lang) or label.get(src) or name
                                                for lang in dict.fromkeys([src] + list(languages))}})
    question_data["items"] = items
    numeric = qconfig.get("numeric") or {}
    question_data["unit"] = numeric.get("unit", "")
    question_data["decimals"] = int(numeric.get("decimals", 1))
    if qconfig.get("constant_sum") not in (None, ""):
        question_data["constant_sum"] = float(qconfig["constant_sum"])
    return question_data


def _enrich_matrix_question(
    question_data: dict,
    qconfig: dict,
    languages: List[str]
) -> dict:
    """
    Adds the rows and the scale to a matrix question.
    
    Format with stable keys:
    - scale: [{key, label: {lang: text}}]
    - rows: [{key, label: {lang: text}}]
    - items: {row_key: {label: {lang: text}}}
    """
    from .keys import _source_language, labels_per_language
    src = _source_language()
    
    # === SCALE and ROWS: keys from the labels in the source language ===
    scale_with_keys, scale_src_to_key = [], {}
    polarity = qconfig.get('option_polarity') or {}   # from a named scale
    for label in labels_per_language(qconfig.get('scale', {}), languages, src):
        key = _create_option_key(label[src])
        scale_src_to_key[label[src]] = key
        entry = {"key": key, "label": label}
        if polarity.get(label[src]):
            entry["polarity"] = polarity[label[src]]
        scale_with_keys.append(entry)
    question_data["scale"] = scale_with_keys
    
    rows_with_keys, row_src_to_key = [], {}
    for label in labels_per_language(qconfig.get('row_labels', {}), languages, src):
        key = _create_option_key(label[src])
        row_src_to_key[label[src]] = key
        rows_with_keys.append({"key": key, "label": label})
    question_data["rows"] = rows_with_keys
    
    # === ITEMS: key-based format for the dashboard ===
    question_data["items"] = {}
    for row_entry in rows_with_keys:
        question_data["items"][row_entry["key"]] = {
            "label": row_entry["label"]
        }
    
    # === RESULTS: convert the keys ===
    if "results" in question_data:
        _convert_matrix_result_keys(question_data["results"], row_src_to_key, scale_src_to_key)
    
    return question_data


def _export_themes_for_dashboard(languages: List[str]) -> dict:
    """
    Exports the themes for the dashboard from state.themes.
    
    Per specification:
    - label: {de, fr, en}
    - description: {de, fr, en} (optional)
    - icon: string (emoji)
    - questions: string[] (question ids)
    - order: number
    """
    themes = {}
    from .keys import _source_language
    _src = _source_language()
    
    # default icons of the themes
    default_icons = {
        "governance": "🏛️",
        "trends": "📈",
        "security": "🔒",
        "ki": "🤖",
        "ai": "🤖",
        "cloud": "☁️",
        "infrastructure": "🖥️",
        "strategy": "🎯",
        "cooperation": "🤝",
        "digital_sovereignty": "🛡️",
        "budget": "💰",
        "services": "⚙️"
    }
    
    # use the themes from state.themes (synchronised by the ThemeAnalyzer)
    if state.themes:
        order_counter = 1
        for theme_id, theme_data in state.themes.items():
            # determine the icon
            icon = theme_data.get("icon", "")
            if not icon:
                # try to derive the icon from the id
                for key, emoji in default_icons.items():
                    if key in theme_id.lower():
                        icon = emoji
                        break
                if not icon:
                    icon = "📊"  # Default
            
            themes[theme_id] = {
                "id": theme_id,
                "label": theme_data.get("label", {lang: theme_id.replace('_', ' ').title() for lang in languages}),
                "description": theme_data.get("description", {lang: "" for lang in languages}),
                "icon": icon,
                "keywords": _limit_keywords(theme_data.get("keywords_translated", {
                    _src: theme_data.get("question_keywords", theme_data.get("keywords", []))
                }), max_per_lang=10),
                "questions": theme_data.get("questions", []),
                "order": theme_data.get("order", order_counter)
            }
            order_counter += 1
        return themes
    
    # fallback: load from the database
    if not state.db or not state.import_id:
        return themes
    
    try:
        with state.db._connection() as conn:
            cursor = conn.cursor()
            
            # find all distinct theme_ids
            cursor.execute("""
                SELECT DISTINCT theme_id FROM theme_mappings
                WHERE project_id = (SELECT project_id FROM imports WHERE id = ?)
            """, (state.import_id,))
            
            theme_ids = [row[0] for row in cursor.fetchall()]
            
            order_counter = 1
            for theme_id in theme_ids:
                # keywords of this theme
                cursor.execute("""
                    SELECT keyword, question_id FROM theme_mappings
                    WHERE theme_id = ? AND project_id = (
                        SELECT project_id FROM imports WHERE id = ?
                    )
                """, (theme_id, state.import_id))
                
                rows = cursor.fetchall()
                keywords = [r[0] for r in rows if r[0]]
                question_ids = list(set(r[1] for r in rows if r[1]))
                
                # determine the icon
                icon = "📊"
                for key, emoji in default_icons.items():
                    if key in theme_id.lower():
                        icon = emoji
                        break
                
                themes[theme_id] = {
                    "id": theme_id,
                    "label": {lang: theme_id.replace('_', ' ').title() for lang in languages},
                    "description": {lang: "" for lang in languages},
                    "icon": icon,
                    # keywords stay in the source language (not translated)
                    "keywords": _limit_keywords({_src: keywords[:10]}, max_per_lang=10),
                    "questions": question_ids,
                    "order": order_counter
                }
                order_counter += 1
                
    except Exception as e:
        logger.warning(f"Error loading the themes: {e}")
    
    return themes


def _export_correlations_for_dashboard(languages: List[str], progress=None) -> dict:
    """
    correlations.json for the dashboard (method see utils/correlation.py).

    The structure is always written, empty if there is no data; the LLM is
    optional (without it the findings carry their facts but no texts).
    """
    from utils.correlation import CorrelationReport, run_for_state
    empty = CorrelationReport({}, [], {}, {})
    if state.df is None or state.config is None:
        logger.warning("Correlation export: no data or configuration")
        return empty.to_json(state.config or {}, 0, languages)
    try:
        report = run_for_state(state, languages, _available_llm_client())
        data = report.to_json(state.config, len(state.df), languages)
        logger.info(f"Korrelationsexport: {data['summary']['significant_correlations_found']} Fragenpaare")
        return data
    except Exception as e:
        logger.error(f"Correlation export failed: {e}", exc_info=True)
        return empty.to_json(state.config, len(state.df), languages)


def _available_llm_client():
    """LLM client if the endpoint answers, else None (texts are then left out)."""
    try:
        from utils.llm import get_llm_client
        client = get_llm_client()
        return client if client.test_connection()[0] else None
    except Exception as e:
        logger.info(f"No LLM for the texts of the associations: {e}")
        return None
