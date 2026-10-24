# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Full analysis pipeline and re-export after manual corrections in the workbench.
"""

import logging
import json
from pathlib import Path
from typing import List, Tuple
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt
import gradio as gr

from core import state, load_config, OUTPUT_DIR
from handlers import get_handler
from utils.export_word import WordExporter
from .question_texts import name_in_source, report_extras, stored_cluster_texts, stored_examples
from .dashboard import export_dashboard_data
from .insights import export_segment_cluster_comparison, export_segment_insights
from .json_utils import NumpyJSONEncoder, sanitize_for_json

from .reports import _dimension_of_question

logger = logging.getLogger(__name__)



def _source_lang() -> str:
    from utils.languages import get_source_language
    return get_source_language(state.config)

def run_full_pipeline(
    include_translations: bool = True,
    include_item_extraction: bool = True,
    include_theme_analysis: bool = True,
    include_correlations: bool = True,
    include_segment_comparison: bool = True,
    include_word_export: bool = True,
    include_summaries: bool = True,
    deep_theme_analysis: bool = False,
    languages: List[str] = None,
    progress=gr.Progress()
) -> Tuple[str, str]:
    """
    Runs the complete analysis pipeline:
    1. translations (optional)
    2. item extraction for free-text questions (optional)
    3. analyses of all questions (with clustering)
    4. theme analysis and assignment (optional)
    5. correlation analysis (optional)
    6. segment comparison (optional)
    7. LLM summaries per question (optional)
    8. Word export in all languages (optional)
    9. dashboard export (JSON)
    
    Args:
        include_translations: translate answers in other languages into the
            source language of the survey
        include_item_extraction: item extraction for free text
        include_theme_analysis: run the theme analysis
        include_correlations: run the correlation analysis
        include_segment_comparison: segment comparison (segment-specific themes and
            tested group differences)
        include_word_export: create the Word reports
        include_summaries: generate LLM summaries per question
        deep_theme_analysis: deep analysis (many LLM calls) instead of the fast one
        languages: export languages (default: the output languages of the survey)
        progress: Gradio progress tracker
    
    Returns:
        tuple (status text, export directory path)
    """
    # languages from the configuration, or the default
    if languages is None:
        # try to load from the configuration
        if state.config:
            config_languages = state.config.get('settings', {}).get('survey_metadata', {}).get('languages', None)
            if config_languages and isinstance(config_languages, list):
                languages = config_languages
                logger.info(f"Languages loaded from the configuration: {languages}")
            else:
                from utils.languages import get_output_languages
                languages = get_output_languages(state.config)
        else:
            from utils.languages import get_output_languages
            languages = get_output_languages(state.config)
    
    # === CHECKS ===
    progress(0, desc="Checking prerequisites...")
    
    if state.df is None:
        return "❌ No data imported. Upload a file first.", ""
    
    if state.config is None:
        load_config()
    
    if not state.config:
        return "❌ No configuration loaded.", ""
    
    if state.db is None or state.import_id is None:
        return "❌ No database connection.", ""
    
    # timestamp of the export folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pipeline_dir = OUTPUT_DIR / f"full_pipeline_{timestamp}"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    
    status_lines = ["🚀 Pipeline started", ""]
    errors = []
    
    # count the LLM usage of this run (written at the end: llm_usage.json)
    from utils.llm import start_usage_run
    start_usage_run()
    
    # actual number of steps, depending on the options
    # steps: 1. translations (optional), 2. item extraction (optional),
    #        3. analyses, 4. themes (optional), 5. correlations (optional),
    #        6. segment comparison (optional), 7. summaries (optional),
    #        8. Word export (optional), 9. dashboard export
    actual_steps = 2  # analyses + dashboard are always included
    if include_translations:
        actual_steps += 1
    if include_item_extraction:
        actual_steps += 1
    if include_theme_analysis:
        actual_steps += 1
    if include_correlations:
        actual_steps += 1
    if include_segment_comparison:
        actual_steps += 1
    if include_summaries:
        actual_steps += 1
    if include_word_export:
        actual_steps += 1
    
    current_step = 0
    
    def get_progress(step: int, sub_progress: float = 0.0) -> float:
        """Computes the overall progress (0.0 to 1.0)."""
        base = (step - 1) / actual_steps
        sub = sub_progress / actual_steps
        return min(base + sub, 0.99)  # never quite 100% until the end
    
    # === STEP 1: TRANSLATIONS ===
    if include_translations:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: translations...")
        
        try:
            from utils.response_translator import translate_responses
            from utils.languages import get_source_language
            from utils.llm import get_llm_client
            
            llm = get_llm_client()
            llm_ok, _ = llm.test_connection()
            
            if llm_ok:
                # labels of the configuration (question texts, options, scales,
                # segment values) into the output languages, each list with its context
                from utils.config_translation import fill_config_labels
                state.config, label_stats = fill_config_labels(
                    state.config, use_llm=True,
                    progress=lambda frac, msg: progress(get_progress(current_step, frac * 0.3),
                                                        f"Step {current_step}/{actual_steps}: labels {msg}"))
                status_lines.append(
                    f"✅ Configuration labels: {label_stats['translated']} lists translated, "
                    f"{label_stats['filled']} from the translation memory, {label_stats['missing']} without translation")
                state.df, trans_stats = translate_responses(
                    df=state.df,
                    config=state.config,
                    # translate everything into the source language of the survey
                    target_lang=get_source_language(state.config),
                    db=state.db,
                    import_id=state.import_id,
                    progress_callback=lambda cur, total, msg: progress(
                        get_progress(current_step, cur/total if total > 0 else 0),
                        f"Step {current_step}/{actual_steps}: {msg}"
                    )
                )
                
                translated = trans_stats.get('translated', 0)
                cached = trans_stats.get('from_cache', 0)  # key of the translated count
                skipped = trans_stats.get('skipped', 0)
                status_lines.append(f"✅ Translations: {translated} new, {cached} from the cache, {skipped} skipped (already in the target language)")
                logger.info(f"Pipeline: translations finished - {translated} new, {cached} cached, {skipped} skipped")
            else:
                status_lines.append("⚠️ Translations: LLM not available, skipped")
                
        except Exception as e:
            error_msg = f"Translations failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}")
    else:
        status_lines.append("⏭️ Translations skipped")
    
    # === STEP: ITEM EXTRACTION ===
    if include_item_extraction:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: item extraction...")
        
        try:
            from services.item_extraction import run_all_extractions
            
            # item extraction for all free-text questions
            # thematic = different topics -> separate items
            # validate = the LLM checks the quality + retry if the extraction is too abstract
            extraction_status, extraction_stats = run_all_extractions(
                force_llm=False,
                thematic_mode=True,
                validate=True,
                progress=lambda p, desc: progress(
                    get_progress(current_step, p), 
                    f"Step {current_step}/{actual_steps}: {desc}"
                )
            )
            
            stats = json.loads(extraction_stats) if extraction_stats != "{}" else {}
            total_items = stats.get("total_items", 0)
            
            status_lines.append(f"✅ Item extraction: {total_items} items extracted")
            logger.info(f"Pipeline: item extraction finished - {total_items} items")
            
        except Exception as e:
            error_msg = f"Item extraction failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}")
    else:
        status_lines.append("⏭️ Item extraction skipped")
    
    # === STEP: ANALYSES ===
    current_step += 1
    progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: running analyses...")
    
    try:
        # analyse all questions (with clustering for free text)
        question_groups = state.config.get('question_groups', {})
        total_questions = len(question_groups)
        analyzed = 0
        
        for i, (qid, qconfig) in enumerate(question_groups.items()):
            progress(
                get_progress(current_step, i / total_questions if total_questions > 0 else 0),
                f"Step {current_step}/{actual_steps}: analyzing {qid}..."
            )
            
            handler_type = qconfig.get('handler', 'single_choice')
            handler = get_handler(handler_type, qid, qconfig)
            
            if handler:
                try:
                    if handler_type == 'freetext':
                        # free text: analyze() with db and import_id for item mode
                        result = handler.analyze(
                            state.df,
                            lang=_source_lang(),
                            db=state.db,
                            import_id=state.import_id
                        )
                    else:
                        # results are kept in the source language; each report and
                        # export takes its own language from the configuration
                        result = handler.analyze(state.df, lang=_source_lang())
                    
                    state.analysis_results[qid] = result
                    
                    # IMPORTANT: store in the database as well!
                    if state.db and state.import_id and result is not None:
                        try:
                            # safe conversion of DataFrames
                            data_val = {}
                            if hasattr(result, 'data') and result.data is not None:
                                if hasattr(result.data, 'to_dict'):
                                    data_val = result.data.to_dict()
                                elif isinstance(result.data, dict):
                                    data_val = result.data
                            
                            pct_val = {}
                            if hasattr(result, 'percentages') and result.percentages is not None:
                                if hasattr(result.percentages, 'to_dict'):
                                    pct_val = result.percentages.to_dict()
                                elif isinstance(result.percentages, dict):
                                    pct_val = result.percentages
                            
                            # copy the metadata and convert DataFrames
                            metadata_val = {}
                            if hasattr(result, 'metadata') and result.metadata:
                                metadata_val = dict(result.metadata)
                                # percentages_df is often a DataFrame
                                if 'percentages_df' in metadata_val:
                                    if hasattr(metadata_val['percentages_df'], 'to_dict'):
                                        metadata_val['percentages_df'] = metadata_val['percentages_df'].to_dict()
                            
                            result_dict = {
                                'n': result.n if hasattr(result, 'n') else 0,
                                'data': data_val,
                                'percentages': pct_val,
                                'description': result.description if hasattr(result, 'description') else '',
                                'warnings': result.warnings if hasattr(result, 'warnings') else [],
                                # do NOT store result.clusters: it is a Python object and does not
                                # serialise correctly. The cluster details are in metadata.cluster_details.
                                'metadata': metadata_val
                            }
                            state.db.save_analysis_result(
                                import_id=state.import_id,
                                question_id=qid,
                                handler_type=handler_type,
                                result=result_dict,
                                n=result.n if hasattr(result, 'n') else 0,
                                segment_type="all",
                                segment_value=None
                            )
                        except Exception as db_e:
                            logger.warning(f"Could not store the analysis of {qid} in the database: {db_e}")
                    
                    analyzed += 1
                except Exception as e:
                    logger.warning(f"Analysis of {qid} failed: {e}")
        
        status_lines.append(f"✅ Analyses: {analyzed}/{total_questions} questions analyzed")
        logger.info(f"Pipeline: analyses finished - {analyzed}/{total_questions}")
        
    except Exception as e:
        error_msg = f"Analyses failed: {e}"
        errors.append(error_msg)
        status_lines.append(f"⚠️ {error_msg}")
        logger.error(f"Pipeline: {error_msg}")
    
    # === STEP: THEME ANALYSIS ===
    if include_theme_analysis:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: theme analysis...")
        
        try:
            from themes.theme_analyzer import ThemeAnalyzer
            
            theme_analyzer = ThemeAnalyzer(state.config, llm_client=None, output_dir=OUTPUT_DIR)
            
            # do all themes have explicit question assignments already?
            all_have_questions = all(
                len(theme.questions) > 0 
                for theme in theme_analyzer.themes.values()
            )
            
            if all_have_questions and not deep_theme_analysis:
                # FAST PATH: use the predefined assignments directly
                total_questions_assigned = set()
                for theme in theme_analyzer.themes.values():
                    total_questions_assigned.update(theme.questions)
                
                # synchronise into state.themes
                state.themes = {
                    tid: theme.to_dict()
                    for tid, theme in theme_analyzer.themes.items()
                }
                
                status_lines.append(
                    f"✅ Themes: {len(theme_analyzer.themes)} themes with "
                    f"{len(total_questions_assigned)} predefined question assignments"
                )
                logger.info(f"Pipeline: using the predefined theme assignments (no LLM call needed)")
            else:
                # ANALYSIS PATH: keyword matching or deep analysis
                from utils.llm import get_llm_client
                llm_client = get_llm_client()
                theme_analyzer.llm = llm_client
                
                total_themes = len(theme_analyzer.themes)
                themes_analyzed = 0
                total_questions_assigned = set()
                
                for i, theme_id in enumerate(theme_analyzer.themes.keys()):
                    progress(
                        get_progress(current_step, i / total_themes if total_themes > 0 else 0),
                        f"Step {current_step}/{actual_steps}: theme {theme_id}..."
                    )
                    
                    if deep_theme_analysis:
                        # deep analysis, the LLM checks every cluster
                        result = theme_analyzer.deep_analyze_theme(theme_id)
                    else:
                        # fast analysis with keyword matching
                        result = theme_analyzer.extract_keywords_for_theme(
                            theme_id, use_llm=True, check_data=True
                        )
                    
                    matched = result.get("matched_questions", result.get("assigned_questions", []))
                    total_questions_assigned.update(matched)
                    themes_analyzed += 1
                
                # synchronise into state.themes
                state.themes = {
                    tid: theme.to_dict()
                    for tid, theme in theme_analyzer.themes.items()
                }
                
                analysis_type = "deep" if deep_theme_analysis else "quick"
                status_lines.append(
                    f"✅ Theme analysis ({analysis_type}): {themes_analyzed} themes, "
                    f"{len(total_questions_assigned)} questions assigned"
                )
                logger.info(f"Pipeline: theme analysis finished")
            
        except Exception as e:
            error_msg = f"Theme analysis failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}")
    else:
        status_lines.append("⏭️ Theme analysis skipped")
        
        # load the themes from the configuration anyway, for the correlation analysis!
        if not state.themes:
            try:
                from themes.theme_analyzer import ThemeAnalyzer
                theme_analyzer = ThemeAnalyzer(
                    config=state.config,
                    llm_client=None,
                    config_dir=Path(__file__).parent / "config"
                )
                state.themes = {
                    tid: theme.to_dict()
                    for tid, theme in theme_analyzer.themes.items()
                }
                if state.themes:
                    status_lines.append(f"   (themes loaded from the configuration: {len(state.themes)})")
            except Exception as e:
                logger.warning(f"Could not load the themes from the configuration: {e}")
    
    # === STEP: CORRELATION ANALYSIS ===
    theme_correlations = {}
    cross_theme_correlations = []
    correlation_report = None
    
    if include_correlations:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: correlation analysis...")
        
        try:
            from utils.correlation import run_for_state
            from utils.llm import get_llm_client
            
            correlation_report = run_for_state(state, languages, get_llm_client())
            theme_correlations = correlation_report.themes
            cross_theme_correlations = correlation_report.cross
            total_corr = len(correlation_report.pairs())
            status_lines.append(f"✅ Correlation analysis: {total_corr} question pairs with significant associations")
            logger.info(f"Pipeline: correlation analysis finished - {total_corr} question pairs")
            
            # JSON export of the correlations (ALWAYS created, even if empty)
            corr_json = correlation_report.to_json(state.config, len(state.df), languages)
            corr_filepath = pipeline_dir / "correlations.json"
            with open(corr_filepath, 'w', encoding='utf-8') as f:
                json.dump(sanitize_for_json(corr_json), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
            
        except Exception as e:
            error_msg = f"Correlation analysis failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}", exc_info=True)
    else:
        status_lines.append("⏭️ Correlation analysis skipped")
    
    # === STEP: SEGMENT COMPARISON ===
    # Segment-specific clusters (descriptive, per group) and tested differences
    # between groups. The segment-specific clusters are cached for this run, so
    # the dashboard export below uses the same clusters without new LLM calls.
    segment_clusters_data = None
    segment_differences = None
    from .freetext_segments import begin_specific_cluster_cache, end_specific_cluster_cache
    begin_specific_cluster_cache()
    if include_segment_comparison:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: segment comparison...")
        
        try:
            # 1. Segment-specific clusters of free-text questions
            segment_clusters_data = export_segment_cluster_comparison(
                export_dir=pipeline_dir,
                languages=languages,
                max_clusters=5,
                progress_callback=lambda p, msg: progress(get_progress(current_step, p * 0.5), msg)
            )
            questions_with_clusters = segment_clusters_data.get('summary', {}).get('questions_with_clusters', 0)
            freetext_total = segment_clusters_data.get('summary', {}).get('total_freetext_questions', 0)
            status_lines.append(f"✅ Segment-specific themes: {questions_with_clusters} question/dimension pairs "
                                f"({freetext_total} free-text questions)")
            state.clusters_by_value = segment_clusters_data
        except Exception as e:
            error_msg = f"Segment-specific themes failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}", exc_info=True)
        
        try:
            # 2. Tested differences between groups, all questions
            segment_differences, insights_data = export_segment_insights(
                export_dir=pipeline_dir,
                languages=languages,
                min_difference=15.0,
                progress_callback=lambda p, msg: progress(get_progress(current_step, 0.5 + p * 0.5), msg)
            )
            summary = insights_data.get('summary', {})
            status_lines.append(f"✅ Group differences: {summary.get('questions_with_differences', 0)} of "
                                f"{summary.get('questions_tested', 0)} questions with statistically significant differences")
            for err in insights_data.get('method', {}).get('errors', []):
                status_lines.append(f"⚠️ Group differences {err['question_id']}: {err['error']}")
        except Exception as e:
            error_msg = f"Group differences failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}", exc_info=True)
    else:
        status_lines.append("⏭️ Segment comparison skipped")
    
    # === STEP: LLM SUMMARIES ===
    all_summaries = {}  # {qid: {lang: QuestionSummary}}
    if include_summaries:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: generating summaries...")
        
        try:
            from utils.question_summary import generate_all_summaries
            
            # build the survey context for the LLM prompt
            survey_metadata = state.config.get('settings', {}).get('survey_metadata', {})
            # Survey title in the source language as context line of the summary
            # prompts; the general context comes from settings.llm_context
            from utils.languages import get_source_language, localized
            _src = get_source_language(state.config)
            survey_context = str(localized(survey_metadata.get('survey_title', {}), _src, _src, default='') or '')
            if not survey_context:
                survey_context = str(localized(state.config.get('survey_info', {}).get('title', {}), _src, _src,
                                               default='') or '')
            
            all_summaries = generate_all_summaries(
                config=state.config,
                analysis_results=state.analysis_results,
                df=state.df,
                db=state.db,
                import_id=state.import_id,
                languages=languages,
                progress_callback=lambda cur, total, msg: progress(
                    get_progress(current_step, cur / total if total > 0 else 0),
                    f"Step {current_step}/{actual_steps}: {msg}"
                ),
                llm_client=None,  # created internally
                survey_context=survey_context
            )
            
            cached_count = sum(1 for qid in all_summaries if all_summaries[qid])
            status_lines.append(f"✅ Summaries: {cached_count} questions (source language + translations)")
            logger.info(f"Pipeline: summaries finished - {cached_count} questions")
            
        except Exception as e:
            error_msg = f"Summaries failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}", exc_info=True)
    else:
        status_lines.append("⏭️ Summaries skipped")
    
    # === STEP: WORD EXPORT ===
    if include_word_export:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: Word export...")
        
        try:
            exported_reports = _write_word_reports(
                state, languages, pipeline_dir, timestamp,
                report_progress=lambda fraction, lang: progress(
                    get_progress(current_step, fraction), f"Step {current_step}/{actual_steps}: Word export ({lang})..."),
                all_summaries=all_summaries, correlation_report=correlation_report,
                segment_differences=segment_differences, segment_clusters_data=segment_clusters_data)
            status_lines.append(f"✅ Word export: {len(exported_reports)} reports ({', '.join(languages)})")
            logger.info(f"Pipeline: Word export finished - {exported_reports}")
        
        except Exception as e:
            error_msg = f"Word export failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Pipeline: {error_msg}", exc_info=True)
    else:
        status_lines.append("⏭️ Word export skipped")
    
    # === STEP: DASHBOARD EXPORT ===
    current_step += 1
    progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: dashboard export...")
    
    try:
        # dashboard export directly into the pipeline folder
        dashboard_status, dashboard_path = export_dashboard_data(
            include_segments=True,
            languages=languages,
            output_dir=pipeline_dir,  # write directly into the pipeline folder
            progress=lambda p, desc: progress(
                get_progress(current_step, p),
                f"Step {current_step}/{actual_steps}: {desc}"
            )
        )
        
        status_lines.append(f"✅ Dashboard export: dashboard_data.json, themes.json, correlations.json")
        logger.info(f"Pipeline: dashboard export finished")
        
    except Exception as e:
        error_msg = f"Dashboard export failed: {e}"
        errors.append(error_msg)
        status_lines.append(f"⚠️ {error_msg}")
        logger.error(f"Pipeline: {error_msg}", exc_info=True)
    
    end_specific_cluster_cache()
    
    # === FINISH ===
    progress(1.0, desc="Pipeline finished!")
    
    status_lines.append("")
    status_lines.append("=" * 40)
    
    if errors:
        status_lines.append(f"⚠️ {len(errors)} errors occurred")
    else:
        status_lines.append("✅ Pipeline finished successfully!")
    
    status_lines.append("")
    status_lines.append(f"📁 All files in: {pipeline_dir}")
    
    # list the files
    status_lines.append("")
    status_lines.append("Exported files:")
    for f in sorted(pipeline_dir.glob("*")):
        size_kb = f.stat().st_size / 1024
        status_lines.append(f"  • {f.name} ({size_kb:.1f} KB)")
    
    # LLM usage of this run: llm_usage.json, data/llm_usage.jsonl, log, status
    usage_summary = _record_llm_usage("pipeline", pipeline_dir, state)
    if usage_summary:
        status_lines += ["", usage_summary]
    
    return "\n".join(status_lines), str(pipeline_dir)


def reexport_after_workbench(
    languages: List[str] = None,
    include_word_export: bool = True,
    include_dashboard_export: bool = True,
    retranslate_changed_clusters: bool = True,
    reassign_items: bool = True,
    progress=gr.Progress()
) -> Tuple[str, str]:
    """
    Re-export after Workbench changes.
    
    Loads all data from the database (with the Workbench changes of the
    clusters), reassigns the items of changed clusters and creates the Word
    and dashboard exports again.
    
    Args:
        languages: export languages (default: the output languages of the survey)
        include_word_export: create the Word reports
        include_dashboard_export: create the dashboard JSON
        retranslate_changed_clusters: translate changed clusters again
        reassign_items: reassign the items of changed clusters (LLM call)
        progress: Gradio progress tracker
    
    Returns:
        tuple (status text, export directory path)
    """
    if languages is None:
        from utils.languages import get_output_languages
        languages = get_output_languages(state.config)
    
    # count the LLM usage of this run (written at the end: llm_usage.json)
    from utils.llm import start_usage_run
    start_usage_run()
    
    status_lines = []
    errors = []
    
    # === CHECKS ===
    progress(0, desc="Checking prerequisites...")
    
    if state.df is None:
        return "❌ No data loaded. Import a file first.", ""
    
    # export directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pipeline_dir = OUTPUT_DIR / f"reexport_{timestamp}"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    
    status_lines.append("🔄 Re-export after Workbench changes")
    status_lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    status_lines.append("")
    
    actual_steps = sum([
        1,  # load the data
        1,  # reassignment (if enabled)
        include_word_export,
        include_dashboard_export
    ])
    current_step = 0
    
    def get_progress(step, sub_progress=0):
        return (step + sub_progress) / actual_steps if actual_steps > 0 else 0
    
    # === STEP 1: load the data from the database and find changed clusters ===
    current_step += 1
    progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: loading data from the database...")
    
    changed_clusters_by_question = {}
    
    try:
        from services.workbench_store import load_all_analyses_from_db, get_clusters_for_question
        
        # load all analyses
        loaded_analyses = load_all_analyses_from_db()
        
        if not loaded_analyses:
            return "❌ No analyses found in the database. Run the pipeline first.", ""
        
        # transfer to state.analysis_results
        for qid, analysis_data in loaded_analyses.items():
            # load the clusters from the database (with the Workbench changes)
            clusters = get_clusters_for_question(qid)
            if clusters:
                analysis_data['clusters'] = clusters
            
            state.analysis_results[qid] = analysis_data
        
        status_lines.append(f"✅ {len(loaded_analyses)} analyses loaded from the database")
        
        # IMPORTANT: load the themes for the correlations!
        if not state.themes:
            try:
                from themes.theme_analyzer import ThemeAnalyzer
                theme_analyzer = ThemeAnalyzer(
                    config=state.config,
                    llm_client=None,  # no LLM needed for loading
                    config_dir=Path(__file__).parent / "config"
                )
                # load the themes from YAML/configuration
                state.themes = {
                    tid: theme.to_dict()
                    for tid, theme in theme_analyzer.themes.items()
                }
                if state.themes:
                    status_lines.append(f"✅ {len(state.themes)} themes loaded from the configuration")
                else:
                    status_lines.append("⚠️ No theme configuration found")
            except Exception as theme_err:
                logger.warning(f"Loading the themes failed: {theme_err}")
                status_lines.append(f"⚠️ Themes could not be loaded: {theme_err}")
        
        # identify the changed clusters
        changed_clusters_by_question = _find_changed_clusters()
        if changed_clusters_by_question:
            total_changed = sum(len(v) for v in changed_clusters_by_question.values())
            status_lines.append(f"🔍 {total_changed} changed clusters found in {len(changed_clusters_by_question)} questions")
        
    except Exception as e:
        error_msg = f"Loading from the database failed: {e}"
        errors.append(error_msg)
        status_lines.append(f"⚠️ {error_msg}")
        logger.error(f"Re-Export: {error_msg}")
    
    # === STEP 2: reassign the items (if clusters changed) ===
    current_step += 1
    if reassign_items and changed_clusters_by_question:
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: reassigning items...")
        
        try:
            total_questions = len(changed_clusters_by_question)
            total_reassigned = 0
            
            for i, (qid, clusters) in enumerate(changed_clusters_by_question.items()):
                progress(
                    get_progress(current_step, i / total_questions),
                    f"Step {current_step}/{actual_steps}: reassignment {qid}..."
                )
                
                stats = _reassign_items_to_clusters(qid)
                total_reassigned += stats.get('items_reassigned', 0)
                
                if stats.get('error'):
                    errors.append(f"Reassignment {qid}: {stats['error']}")
            
            status_lines.append(f"✅ {total_reassigned} items reassigned in {total_questions} questions")
            
            # reload the clusters after the reassignment
            for qid in changed_clusters_by_question.keys():
                clusters = get_clusters_for_question(qid)
                if clusters and qid in state.analysis_results:
                    state.analysis_results[qid]['clusters'] = clusters
            
        except Exception as e:
            error_msg = f"Item reassignment failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
            logger.error(f"Re-Export: {error_msg}")
    else:
        status_lines.append("⏭️ No reassignment needed (no changed clusters)")
    
    # === STEP 3: update the translations ===
    if retranslate_changed_clusters and changed_clusters_by_question:
        progress(get_progress(current_step, 0.8), desc="Translating changed clusters...")
        
        try:
            _retranslate_clusters(changed_clusters_by_question, progress, current_step, actual_steps)
            status_lines.append(f"✅ Cluster translations updated")
        except Exception as e:
            error_msg = f"Translation failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
    
    # === STEP 3b: associations and group comparison, from the corrected clusters ===
    # As in the full pipeline: group differences of free-text questions depend on the
    # clusters, so they are tested again; the texts of unchanged findings come from the
    # store. Segment-specific clusters are formed once and shared with the dashboard export.
    from .freetext_segments import begin_specific_cluster_cache, end_specific_cluster_cache
    begin_specific_cluster_cache()
    correlation_report = segment_differences = segment_clusters_data = None
    try:
        from utils.correlation import run_for_state
        from utils.llm import get_llm_client
        correlation_report = run_for_state(state, languages, get_llm_client())
        corr_json = correlation_report.to_json(state.config, len(state.df), languages)
        with open(pipeline_dir / "correlations.json", 'w', encoding='utf-8') as f:
            json.dump(sanitize_for_json(corr_json), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
        status_lines.append(f"✅ Associations: {len(correlation_report.pairs())} question pairs")
    except Exception as e:
        errors.append(f"Correlation analysis failed: {e}")
        status_lines.append(f"⚠️ Correlation analysis failed: {e}")
        logger.error(f"Re-export: correlation analysis failed: {e}", exc_info=True)
    try:
        segment_clusters_data = export_segment_cluster_comparison(
            export_dir=pipeline_dir, languages=languages, max_clusters=5)
        state.clusters_by_value = segment_clusters_data
    except Exception as e:
        errors.append(f"Segment-specific themes failed: {e}")
        status_lines.append(f"⚠️ Segment-specific themes failed: {e}")
        logger.error(f"Re-export: segment-specific themes failed: {e}", exc_info=True)
    try:
        segment_differences, insights_data = export_segment_insights(
            export_dir=pipeline_dir, languages=languages, min_difference=15.0)
        summary = insights_data.get('summary', {})
        status_lines.append(f"✅ Group differences: {summary.get('questions_with_differences', 0)} of "
                            f"{summary.get('questions_tested', 0)} questions with statistically significant differences")
    except Exception as e:
        errors.append(f"Group differences failed: {e}")
        status_lines.append(f"⚠️ Group differences failed: {e}")
        logger.error(f"Re-export: group differences failed: {e}", exc_info=True)

    # === STEP 4: Word export ===
    if include_word_export:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: Word export...")
        
        try:
            # the same Word reports as the full pipeline (summaries come from the cache)
            exported_reports = _write_word_reports(
                state, languages, pipeline_dir, timestamp,
                report_progress=lambda fraction, lang: progress(
                    get_progress(current_step, fraction), f"Step {current_step}/{actual_steps}: Word export ({lang})..."),
                all_summaries=_cached_summaries(state, languages), correlation_report=correlation_report,
                segment_differences=segment_differences, segment_clusters_data=segment_clusters_data)
            if len(exported_reports) != len(languages):
                raise RuntimeError(f"{len(exported_reports)} of {len(languages)} reports written")
            status_lines.append(f"✅ Word export: {len(exported_reports)} reports ({', '.join(languages)})")
            
        except Exception as e:
            error_msg = f"Word export failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
    
    # === STEP 5: dashboard export ===
    if include_dashboard_export:
        current_step += 1
        progress(get_progress(current_step), desc=f"Step {current_step}/{actual_steps}: dashboard export...")
        
        try:
            def dashboard_progress(p, desc=""):
                progress(
                    get_progress(current_step, p),
                    f"Step {current_step}/{actual_steps}: {desc}"
                )
            
            _status, dashboard_path = export_dashboard_data(
                include_segments=True,
                languages=languages,
                output_dir=pipeline_dir,
                progress=lambda p, desc="": dashboard_progress(p, desc),
            )
            if not dashboard_path:
                raise RuntimeError(_status[-200:])
            
            status_lines.append(f"✅ Dashboard export: dashboard_data.json, themes.json, correlations.json")
            
        except Exception as e:
            error_msg = f"Dashboard export failed: {e}"
            errors.append(error_msg)
            status_lines.append(f"⚠️ {error_msg}")
    
    end_specific_cluster_cache()
    
    # === FINISH ===
    progress(1.0, desc="Re-export finished")
    
    if errors:
        status_lines.append("")
        status_lines.append(f"⚠️ {len(errors)} errors occurred")
    else:
        status_lines.append("")
        status_lines.append("✅ Re-export finished successfully!")
    
    status_lines.append("")
    status_lines.append(f"📁 All files in: {pipeline_dir}")
    
    # list the files
    status_lines.append("")
    status_lines.append("Exported files:")
    for f in sorted(pipeline_dir.glob("*")):
        size_kb = f.stat().st_size / 1024
        status_lines.append(f"  • {f.name} ({size_kb:.1f} KB)")
    
    # LLM usage of this run: llm_usage.json, data/llm_usage.jsonl, log, status
    usage_summary = _record_llm_usage("reexport", pipeline_dir, state)
    if usage_summary:
        status_lines += ["", usage_summary]
    
    return "\n".join(status_lines), str(pipeline_dir)


def _find_changed_clusters() -> dict:
    """
    Finds clusters changed in the Workbench.
    
    Returns:
        dict question_id -> list of changed cluster dicts
    """
    from utils.persistence import get_db_path
    import sqlite3
    
    changed_by_question = {}
    
    try:
        db_path = get_db_path()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # clusters changed manually
            cursor.execute("""
                SELECT 
                    fc.id, fc.cluster_name, fc.description_source, fc.translations_json,
                    fc.is_manual, fc.updated_at,
                    ar.question_id
                FROM freetext_clusters fc
                JOIN analysis_results ar ON fc.analysis_id = ar.id
                WHERE fc.is_manual = 1
            """)
            
            for row in cursor.fetchall():
                qid = row['question_id']
                if qid not in changed_by_question:
                    changed_by_question[qid] = []
                changed_by_question[qid].append(dict(row))
            
    except Exception as e:
        logger.warning(f"Error searching for changed clusters: {e}")
    
    return changed_by_question


def _reassign_items_to_clusters(question_id: str, progress_callback=None) -> dict:
    """
    Reassigns items/answers based on the current cluster definitions.
    
    The LLM gets:
    - all current cluster names and descriptions
    - all items/answers of the question
    
    and assigns every answer to the best-fitting cluster.
    
    Args:
        question_id: question id
        progress_callback: optional callback for progress updates
    
    Returns:
        dict with statistics
    """
    from utils.persistence import get_db_path
    from utils.llm import get_llm_client
    from services.workbench_store import get_clusters_for_question, get_items_for_question, _update_cluster_counts
    import sqlite3
    
    stats = {
        'question_id': question_id,
        'items_processed': 0,
        'items_reassigned': 0,
        'clusters_updated': 0
    }
    
    try:
        # 1. load the current clusters
        clusters = get_clusters_for_question(question_id)
        if not clusters:
            logger.warning(f"No clusters found for {question_id}")
            return stats
        
        # 2. load the items/answers
        items, total = get_items_for_question(question_id, page_size=1000)
        if not items:
            logger.warning(f"No items found for {question_id}")
            return stats
        
        stats['items_processed'] = len(items)
        
        # 3. build the LLM prompt
        llm = get_llm_client()
        
        # cluster definitions
        cluster_defs = []
        for i, c in enumerate(clusters, 1):
            name = c.get('cluster_name', c.get('name', f'Cluster {i}'))
            desc = c.get('description_source', c.get('description', ''))
            cluster_defs.append(f"{i}. **{name}**" + (f": {desc}" if desc else ""))
        
        clusters_text = "\n".join(cluster_defs)
        
        # items text (in batches if there are too many)
        batch_size = 100
        all_assignments = []
        
        for batch_start in range(0, len(items), batch_size):
            batch_items = items[batch_start:batch_start + batch_size]
            
            items_text = "\n".join(
                f"[{item['id']}] {item.get('extracted_text', item.get('text', ''))}"
                for item in batch_items
            )
            
            from utils import prompts
            prompt = prompts.prompt('cluster_assignment.prompt', prompts.source_language(state.config),
                                    clusters=clusters_text, items=items_text)

            try:
                result = llm.complete_json(prompt, temperature=0.3, max_tokens=4000)
                if isinstance(result, dict):
                    assignments = result.get('assignments', [])
                else:
                    logger.warning(f"The LLM returned no dict for batch {batch_start} but {type(result).__name__}")
                    assignments = []
                all_assignments.extend(assignments)
            except Exception as e:
                logger.error(f"LLM error in batch {batch_start}: {e}")
        
        # 4. update the assignments in the database
        db_path = get_db_path()
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # cluster id mapping (index -> db_id)
            cluster_id_map = {i+1: c['id'] for i, c in enumerate(clusters)}
            
            for assignment in all_assignments:
                item_id = assignment.get('item_id')
                cluster_idx = assignment.get('cluster_index')
                
                if item_id and cluster_idx and cluster_idx in cluster_id_map:
                    new_cluster_id = cluster_id_map[cluster_idx]
                    
                    # change needed?
                    cursor.execute(
                        "SELECT cluster_id FROM response_items WHERE id = ?",
                        (item_id,)
                    )
                    row = cursor.fetchone()
                    old_cluster_id = row[0] if row else None
                    
                    if old_cluster_id != new_cluster_id:
                        cursor.execute(
                            "UPDATE response_items SET cluster_id = ? WHERE id = ?",
                            (new_cluster_id, item_id)
                        )
                        stats['items_reassigned'] += 1
            
            conn.commit()
        
        # 5. update the cluster counts
        _update_cluster_counts(question_id)
        stats['clusters_updated'] = len(clusters)
        
        # 6. reset is_manual (changes processed)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for cluster in clusters:
                cursor.execute("""
                    UPDATE freetext_clusters 
                    SET is_manual = 0, updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), cluster['id']))
            conn.commit()
        
        logger.info(f"Reassignment for {question_id}: {stats['items_reassigned']} of {stats['items_processed']} items reassigned")
        
    except Exception as e:
        logger.error(f"Reassignment of {question_id} failed: {e}")
        stats['error'] = str(e)
    
    return stats


def _retranslate_clusters(clusters_by_question: dict, progress, current_step: int, actual_steps: int):
    """
    Translates changed cluster names and descriptions.
    
    Args:
        clusters_by_question: dict question_id -> cluster_list
        progress: Gradio progress
        current_step: current pipeline step
        actual_steps: total number of steps
    """
    from utils.persistence import get_db_path
    from utils.llm import get_llm_client
    import sqlite3
    
    all_clusters = []
    for qid, cluster_list in clusters_by_question.items():
        all_clusters.extend({**c, '_qid': qid} for c in cluster_list)
    
    if not all_clusters:
        return
    
    try:
        llm = get_llm_client()
        db_path = get_db_path()
        
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            from utils.languages import get_output_languages, get_source_language
            from utils.persistence import merge_translations, translations_to_flat
            from utils.translation_service import context_for_question, get_translation_service
            translator = get_translation_service(llm)
            src = get_source_language(state.config)
            targets = [lang for lang in get_output_languages(state.config) if lang != src]
            
            for i, cluster in enumerate(all_clusters):
                cluster_id = cluster['id']
                name_src = cluster['cluster_name']
                desc_src = cluster['description_source'] or ''  # source language
                stored = translations_to_flat(cluster.get('translations_json'))
                
                # Translate what is missing in any output language; existing
                # (possibly manually corrected) translations stay untouched.
                # Name and description together, with the question as context
                updates = {}
                ctx = context_for_question(state.config, cluster.get('_qid', ''), 'cluster_name')
                for lang in targets:
                    missing = [(f, t) for f, t in (('name', name_src), ('description', desc_src))
                               if t and not stored.get(f"{f}_{lang}")]
                    if not missing:
                        continue
                    translated = translator.translate_many([t for _, t in missing], lang, ctx)
                    for (field_name, original), new in zip(missing, translated):
                        if new and new != original:
                            updates[f"{field_name}_{lang}"] = new
                if updates:
                    cursor.execute(
                        "UPDATE freetext_clusters SET translations_json = ? WHERE id = ?",
                        (merge_translations(cluster.get('translations_json'), updates), cluster_id),
                    )
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Cluster translation failed: {e}")


def _record_llm_usage(run: str, output_dir, state) -> str:
    """LLM usage of a run (see utils.llm.finish_usage_run); the data directory keeps one line per run."""
    from utils.llm import finish_usage_run
    try:
        from core import DATA_DIR, get_registry
        survey_id = get_registry().get_active_id() or ""
    except Exception:
        DATA_DIR, survey_id = None, ""
    return finish_usage_run(run, output_dir=output_dir, data_dir=DATA_DIR, survey_id=survey_id)


def report_title(state, lang: str, entered: str = "") -> str:
    """
    Title of a report in `lang`: the title entered for the export, else the survey
    title (settings.survey_metadata.survey_title) in `lang` or the source language,
    else the default title of the catalogs (locales, word.report_title).
    """
    if entered and entered.strip():
        return entered.strip()
    from utils.languages import localized, text
    from utils.prompts import source_language
    titles = ((state.config or {}).get("settings", {}).get("survey_metadata", {}) or {}).get("survey_title")
    if isinstance(titles, dict):
        found = localized(titles, lang, source_language(), default="")
        if found:
            return found
    elif isinstance(titles, str) and titles.strip():
        return titles.strip()
    return text("word.report_title", lang)

def _write_word_reports(state, languages, pipeline_dir, timestamp, report_progress=lambda fraction, lang: None,
                        all_summaries=None, correlation_report=None, segment_differences=None,
                        segment_clusters_data=None) -> list:
    """
    Word report per language into pipeline_dir (report_<lang>_<timestamp>.docx), as written
    by the full pipeline and the re-export. The optional parts (summaries, correlations,
    group differences, segment-specific topics) are added when given. Returns the file names.
    """
    all_summaries = all_summaries or {}
    word_exporter = WordExporter(pipeline_dir)
    exported_reports = []

    for lang in languages:
        report_progress(languages.index(lang) / len(languages), lang)

        # collect the results for the report (PER LANGUAGE)
        results_for_report = []
        for qid, qconfig in state.config.get('question_groups', {}).items():
            if qid in state.analysis_results:
                # results loaded from the database are dicts: as AnalysisResult
                # (recalculated without LLM work; free text from the stored fields)
                from export.reports import _as_analysis_result
                result = _as_analysis_result(qid, qconfig, state.analysis_results[qid])
                handler_type = qconfig.get('handler', 'single_choice')
                handler = get_handler(handler_type, qid, qconfig)

                if handler and result:
                    try:
                        # create the chart in the right language
                        fig = None
                        try:
                            fig = handler.create_chart(result, qconfig.get('default_chart', 'bar_horizontal'), lang=lang)
                        except Exception as chart_err:
                            logger.warning(f"Chart creation failed for {qid}/{lang}: {chart_err}")

                        # question text in the right language:
                        # prefer full_question (full text), then label (short title)
                        from export.question_texts import question_text as _question_text
                        question_text = _question_text(qconfig, lang, qid)

                        # help text (explanation) in the right language
                        from export.question_texts import help_text as _help_text
                        help_text = _help_text(qconfig, lang)

                        # regenerate the description in the right language
                        description = ""
                        if hasattr(handler, 'generate_description_text') and hasattr(result, 'data'):
                            try:
                                description = handler.generate_description_text(result.data, result.n, lang)
                            except:
                                pass

                        # score explanation for ranking questions
                        score_explanation = None
                        if handler_type == 'ranking' and hasattr(result, 'metadata'):
                            num_ranks = result.metadata.get('num_ranks', 9) if result.metadata else 9
                            score_explanation = handler.get_score_explanation(num_ranks, lang)

                        # load the cluster details from the database (for the current counts)
                        cluster_details = []
                        if handler_type == 'freetext':
                            try:
                                from services.workbench_store import get_clusters_for_question
                                db_clusters = get_clusters_for_question(qid)

                                # translated examples from the metadata (if present)
                                metadata_clusters = result.metadata.get('cluster_details', []) if hasattr(result, 'metadata') and result.metadata else []
                                metadata_by_name = {
                                    name_in_source(cd.get('name', '')): cd
                                    for cd in metadata_clusters
                                }

                                if db_clusters:
                                    for c in db_clusters:
                                        # translated examples and unique_respondents from the metadata
                                        meta_cluster = metadata_by_name.get(c.get('name_source', c.get('name_de', '')), {})
                                        examples_translated = meta_cluster.get('examples_translated', {})

                                        # unique_respondents: preferably from the metadata (correct),
                                        # fallback to the database count
                                        unique_resp = meta_cluster.get('unique_respondents', 0)
                                        db_count = c.get('count', 0)

                                        cluster_details.append({
                                            **stored_cluster_texts(c, languages),
                                            'count': db_count,
                                            'unique_respondents': unique_resp if unique_resp > 0 else db_count,
                                            'examples': c.get('examples', []),
                                            'examples_translated': stored_examples(c, examples_translated),
                                            'answer_ids': c.get('item_ids', [])
                                        })
                            except Exception as e:
                                logger.debug(f"Clusters from the database for {qid}: {e}")
                                cluster_details = result.metadata.get('cluster_details', []) if hasattr(result, 'metadata') else []

                        # unique_respondents of free-text questions
                        unique_respondents = None
                        if handler_type == 'freetext' and hasattr(result, 'metadata') and result.metadata:
                            unique_respondents = result.metadata.get('unique_responses', result.n)

                        # Source question of a segment dimension: add a distribution table
                        segment_table_data = None
                        segment_table_dimension = _dimension_of_question(qid)
                        if segment_table_dimension and hasattr(result, 'data') and result.data:
                            segment_table_data = sorted(result.data.items(), key=lambda x: x[1], reverse=True)

                        results_for_report.append({
                            'question_id': qid,
                            'question_text': question_text,
                            'help_text': help_text,
                            'fig': fig,
                            'description': description,
                            'score_explanation': score_explanation,
                            'n': result.n if hasattr(result, 'n') else None,
                            'unique_respondents': unique_respondents,
                            'n_info': f"n={result.n}" if hasattr(result, 'n') else "",
                            'cluster_details': cluster_details,
                            'segment_table_data': segment_table_data,
                            'segment_table_dimension': segment_table_dimension,
                            'summary': all_summaries.get(qid, {}).get(lang, None),
                            **report_extras(handler, result, lang)
                        })

                        # explicit logging for debugging
                        has_summary = all_summaries.get(qid, {}).get(lang) is not None
                        has_chart = fig is not None
                        logger.debug(
                            f"Report entry: qid={qid}, n={result.n if hasattr(result, 'n') else 'N/A'}, "
                            f"chart={'✓' if has_chart else '✗'}, summary={'✓' if has_summary else '✗'}"
                        )
                        if not has_summary and not has_chart:
                            logger.warning(f"Question {qid}/{lang}: neither a chart nor a summary")
                    except Exception as e:
                        logger.warning(f"Question {qid}/{lang} could not be added to the report: {e}")

        if results_for_report:
            # Debug: Log all questions in the list
            logger.info(f"results_for_report for {lang}: {len(results_for_report)} questions")
            for idx, r in enumerate(results_for_report):
                logger.debug(f"  {idx}: qid={r.get('question_id')}, text='{r.get('question_text', '')[:50]}...', n={r.get('n')}")

            survey_title = report_title(state, lang)

            # report groups from the configuration
            report_groups = state.config.get('settings', {}).get('report_groups', {})

            doc = word_exporter.create_full_report(
                results=results_for_report,
                title=survey_title,
                subtitle=datetime.now().strftime("%B %Y"),
                lang=lang,
                report_groups=report_groups,
                config=state.config
            )

            # add the correlation section (if any)
            if correlation_report is not None and correlation_report.pairs():
                word_exporter.add_correlation_section(doc, correlation_report, state.config, lang)

            # Tested differences between groups
            if segment_differences is not None:
                word_exporter.add_group_differences_section(doc, segment_differences, state.config, lang)

            # Segment-specific topics, one descriptive section per dimension
            for dim_data in (segment_clusters_data or {}).get('dimensions', {}).values():
                if dim_data.get('questions'):
                    word_exporter.add_segment_comparison_section(
                        doc=doc,
                        segment_clusters_data={'questions': dim_data['questions'],
                                               'dimension_label': dim_data.get('label', {})},
                        lang=lang
                    )

            # add the overview of the questions as an appendix
            word_exporter.add_survey_overview_section(
                doc=doc,
                config=state.config,
                lang=lang
            )

            filename = f"report_{lang}_{timestamp}.docx"
            filepath = pipeline_dir / filename
            doc.save(filepath)
            exported_reports.append(filename)
            logger.info(f"Word report created: {filename}")

            # close the figures
            for r in results_for_report:
                if r.get('fig'):
                    plt.close(r['fig'])

    return exported_reports


def _cached_summaries(state, languages) -> dict:
    """Summaries stored in the database ({qid: {lang: QuestionSummary}}), for reports without regenerating them."""
    from utils.question_summary import load_summary_from_db
    out = {}
    if not (state.db and state.import_id):
        return out
    for qid in (state.config or {}).get("question_groups", {}):
        for lang in languages:
            summary = load_summary_from_db(state.db, state.import_id, qid, lang)
            if summary:
                out.setdefault(qid, {})[lang] = summary
    return out
