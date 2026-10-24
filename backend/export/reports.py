# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Word/CSV report exports, overall and segmented.
"""

import logging
import json
from typing import List, Tuple
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt
import gradio as gr

from core import state, load_config, OUTPUT_DIR
from handlers import get_handler
from utils.export_word import WordExporter
from utils.export_data import DataExporter
from utils.export_structured import StructuredReportExporter
from utils.csv_safety import safe_csv_writer
from .json_utils import NumpyJSONEncoder, sanitize_for_json
from utils.languages import catalog_value

logger = logging.getLogger(__name__)


def export_results(
    question_choice: str,
    export_format: List[str],
    languages: List[str]
) -> str:
    """
    Exports the analysis results of a single question.
    
    Args:
        question_choice: chosen question
        export_format: list of the export formats
        languages: list of the languages
    
    Returns:
        status message with the file paths
    """
    if not question_choice:
        return "No question selected"
    
    if not export_format:
        return "No export format selected"
    
    if not languages:
        return "No language selected"
    
    qid = question_choice.split(":")[0].strip()
    
    if qid not in state.analysis_results:
        return "No analysis results yet. Analyze first."
    
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    result = _as_analysis_result(qid, qconfig, state.analysis_results[qid])
    
    # convert the AnalysisResult to a dict for the export
    if hasattr(result, '__dataclass_fields__'):
        result_dict = {
            'question_id': result.question_id,
            'n': result.n,
            'data': result.data.to_dict() if isinstance(result.data, pd.DataFrame) else result.data,
            'percentages': result.percentages,
            'description': result.description,
            'warnings': result.warnings,
            'clusters': result.clusters,
            'additional_content': result.additional_content,
            'metadata': result.metadata
        }
    elif isinstance(result, dict):
        result_dict = result
    else:
        result_dict = {"data": result, "n": 0}
    
    exported_files = []
    
    logger.info(f"Export started: format={export_format}, languages={languages}")
    
    try:
        if any("Word" in fmt for fmt in export_format):
            logger.info("Running the Word export...")
            word_exporter = WordExporter(OUTPUT_DIR)
            for lang in languages:
                filepath = word_exporter.export_question(
                    question_id=qid,
                    result=result,
                    config=qconfig,
                    lang=lang
                )
                exported_files.append(str(filepath))
                logger.info(f"Word exported: {filepath}")
        
        if any("JSON" in fmt for fmt in export_format):
            logger.info("Running the JSON export...")
            data_exporter = DataExporter(OUTPUT_DIR)
            files = data_exporter.export_question_result(
                question_id=qid,
                question_config=qconfig,
                analysis_result=result_dict,
                formats=['json']
            )
            exported_files.extend(str(f) for f in files.values())
        
        if any("CSV" in fmt for fmt in export_format):
            logger.info("Running the CSV export...")
            data_exporter = DataExporter(OUTPUT_DIR)
            files = data_exporter.export_question_result(
                question_id=qid,
                question_config=qconfig,
                analysis_result=result_dict,
                formats=['csv']
            )
            exported_files.extend(str(f) for f in files.values())
        
        if any("PNG" in fmt for fmt in export_format):
            logger.info("Running the PNG export...")
            handler = get_handler(
                qconfig.get('handler', 'single_choice'), 
                qid, 
                qconfig
            )
            if handler:
                for lang in languages:
                    default_chart = qconfig.get('default_chart', 'bar_horizontal')
                    fig = handler.create_chart(result, default_chart, lang=lang)
                    
                    filename = f"{qid}_{default_chart}_{lang}.png"
                    filepath = OUTPUT_DIR / filename
                    fig.savefig(filepath, dpi=300, bbox_inches='tight')
                    plt.close(fig)
                    exported_files.append(str(filepath))
        
        return f"✅ Export successful!\n\nFiles:\n" + "\n".join(
            f"- {f}" for f in exported_files
        )
        
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        return f"❌ Export error: {e}"


def analyze_all_and_export_segmented_report(
    lang: str,
    report_title: str,
    export_formats: List[str],
    force_recalculate: bool = False,
    progress=gr.Progress()
) -> Tuple[str, str]:
    """
    Runs all analyses with the segmentations and creates a complete report.
    
    Structure of the report (structured Word report):
    - per question group: the overall result and the charts per segment dimension
    - appendix A: detailed tables with significance
    - appendix B: free-text clusters with examples
    
    Args:
        lang: language of the report
        report_title: title of the report
        export_formats: list of the export formats (Word, JSON, CSV)
        force_recalculate: recalculate all analyses (otherwise resume mode)
        progress: Gradio progress tracker
    
    Returns:
        tuple (status text, path of the file)
    """
    from services.question_analysis import analyze_all_questions_with_segments
    
    if state.df is None:
        return "❌ No data imported", ""
    
    if state.config is None:
        load_config()
    
    if not state.config:
        return "❌ No configuration loaded", ""
    
    if not export_formats:
        return "❌ No export format selected", ""
    
    from utils.llm import start_usage_run
    start_usage_run()   # LLM usage of this run, recorded at the end
    
    question_groups = state.config.get('question_groups', {})
    # the configured segmentation dimensions (ids); settings.segmentation also holds
    # cross_filters and thresholds, which are no dimensions
    from export.segment_context import get_context
    dim_ids = get_context().dimension_ids()
    
    if not question_groups:
        return "❌ No question groups in the configuration", ""
    
    total_questions = len(question_groups)
    n_segments = len(dim_ids)
    
    # estimated number of analyses: overall + (questions x segments x values)
    # rough estimate: ~4 values per segment on average
    estimated_total = total_questions * (1 + n_segments * 4)
    
    mode_text = "recalculation" if force_recalculate else "resume mode (using the cache)"
    progress(0, desc=f"Starting analyses ({mode_text})...")
    
    errors = []
    exported_files = []
    
    # progress callback for analyze_all_questions_with_segments
    def progress_callback(current, total, message):
        # the first 80% for the analysis
        pct = (current / total) * 0.8 if total > 0 else 0
        progress(pct, desc=message)
    
    try:
        # === ANALYSIS ===
        logger.info(f"Starting the segmented analysis of {total_questions} questions (force_recalculate={force_recalculate})")
        
        all_results = analyze_all_questions_with_segments(
            lang=lang,
            progress_callback=progress_callback,
            skip_existing=not force_recalculate
        )
        
        # collect the statistics
        n_successful = sum(1 for r in all_results.values() if 'error' not in r)
        n_errors = sum(1 for r in all_results.values() if 'error' in r)
        n_cached = sum(1 for r in all_results.values() if r.get('from_cache', False))
        n_calculated = n_successful - n_cached
        
        for qid, data in all_results.items():
            if 'error' in data:
                errors.append(f"{qid}: {data['error']}")
        
        progress(0.85, desc="Exporting data...")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        
        # === WORD EXPORT ===
        if any("Word" in fmt for fmt in export_formats):
            progress(0.87, desc="Creating the Word report...")
            
            subtitle = datetime.now().strftime("%B %Y")
            
            # structured export
            try:
                structured_exporter = StructuredReportExporter(OUTPUT_DIR)
                filepath = structured_exporter.export_report(
                    all_results=all_results,
                    config=state.config,
                    filename=f"survey_report_{timestamp}",
                    title=_report_title(state, lang, report_title),
                    subtitle=subtitle,
                    lang=lang,
                    include_small_multiples=True,
                    include_grouped_segments=True,
                    n_cluster_examples=5
                )
                exported_files.append(str(filepath))
                logger.info(f"Structured report created: {filepath}")
            except Exception as e:
                logger.warning(f"Structured export failed, using the older exporter: {e}")
                # fallback to the older exporter
                word_exporter = WordExporter(OUTPUT_DIR)
                filepath = word_exporter.export_segmented_report(
                    all_results=all_results,
                    filename=f"survey_report_{timestamp}",
                    title=_report_title(state, lang, report_title),
                    subtitle=subtitle,
                    lang=lang,
                    config=state.config
                )
                exported_files.append(str(filepath))
                logger.info(f"Word report (older exporter) created: {filepath}")
        
        # === JSON EXPORT ===
        if any("JSON" in fmt for fmt in export_formats):
            progress(0.92, desc="Creating the JSON export...")
            
            json_data = {
                'meta': {
                    'title': report_title,
                    'language': lang,
                    'generated_at': datetime.now().isoformat(),
                    'total_questions': len(all_results),
                    'segmentation_variables': list(dim_ids)
                },
                'questions': {}
            }
            
            for qid, q_data in all_results.items():
                if 'error' in q_data:
                    json_data['questions'][qid] = {'error': q_data['error']}
                    continue
                
                total_result = q_data.get('total')
                
                q_export = {
                    'question_text': q_data.get('question_text', qid),
                    'handler_type': q_data.get('handler_type'),
                    'total': None,
                    'by_segment': {}
                }
                
                # overall result
                if total_result and hasattr(total_result, 'data'):
                    q_export['total'] = {
                        'n': total_result.n,
                        'data': total_result.data if isinstance(total_result.data, dict) else {},
                        'percentages': total_result.percentages if hasattr(total_result, 'percentages') else {}
                    }
                
                # segmented results
                for seg_key, seg_data in q_data.get('by_segment', {}).items():
                    q_export['by_segment'][seg_key] = {
                        'label': seg_data.get('label'),
                        'segments': {}
                    }
                    
                    for seg_value, value_data in seg_data.get('segments', {}).items():
                        result = value_data.get('result')
                        if result and hasattr(result, 'data'):
                            q_export['by_segment'][seg_key]['segments'][seg_value] = {
                                'n': value_data.get('n', 0),
                                'warning': value_data.get('warning'),
                                'data': result.data if isinstance(result.data, dict) else {},
                                'percentages': result.percentages if hasattr(result, 'percentages') else {}
                            }
                
                json_data['questions'][qid] = q_export
            
            json_filepath = OUTPUT_DIR / f"survey_segmented_{timestamp}_{lang}.json"
            with open(json_filepath, 'w', encoding='utf-8') as f:
                json.dump(sanitize_for_json(json_data), f, ensure_ascii=False, indent=2, cls=NumpyJSONEncoder)
            exported_files.append(str(json_filepath))
        
        # === CSV EXPORT ===
        if any("CSV" in fmt for fmt in export_formats):
            progress(0.95, desc="Creating the CSV export...")
            
            csv_dir = OUTPUT_DIR / f"csv_segmented_{timestamp}"
            csv_dir.mkdir(exist_ok=True)
            
            # overview
            overview_path = csv_dir / f"overview_{lang}.csv"
            with open(overview_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = safe_csv_writer(f, delimiter=';')
                writer.writerow(catalog_value('word.csv_overview_headers', lang))
                
                for qid, q_data in all_results.items():
                    if 'error' in q_data:
                        continue
                    total_result = q_data.get('total')
                    n = total_result.n if total_result else 0
                    writer.writerow([
                        qid,
                        q_data.get('question_text', qid),
                        q_data.get('handler_type', ''),
                        n
                    ])
            
            # comparison tables per segment
            for seg_key in dim_ids:
                seg_path = csv_dir / f"comparison_{seg_key}_{lang}.csv"
                
                with open(seg_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = safe_csv_writer(f, delimiter=';')
                    
                    # collect all segment values
                    all_seg_values = set()
                    for q_data in all_results.values():
                        if 'error' in q_data:
                            continue
                        seg_data = q_data.get('by_segment', {}).get(seg_key, {})
                        all_seg_values.update(seg_data.get('segments', {}).keys())
                    
                    seg_values = sorted(all_seg_values)
                    
                    # Header
                    header = list(catalog_value('word.csv_comparison_headers', lang)) + [f"{v}" for v in seg_values]
                    writer.writerow(header)
                    
                    # data
                    for qid, q_data in all_results.items():
                        if 'error' in q_data:
                            continue
                        
                        seg_data = q_data.get('by_segment', {}).get(seg_key, {})
                        segments = seg_data.get('segments', {})
                        
                        # collect all answer keys
                        answer_keys = set()
                        for v_data in segments.values():
                            result = v_data.get('result')
                            if result and hasattr(result, 'data') and isinstance(result.data, dict):
                                answer_keys.update(result.data.keys())
                        
                        for answer in sorted(answer_keys):
                            row = [q_data.get('question_text', qid)[:50], answer]
                            for seg_value in seg_values:
                                v_data = segments.get(seg_value, {})
                                result = v_data.get('result')
                                if result and hasattr(result, 'percentages') and result.percentages:
                                    pct = result.percentages.get(answer, 0)
                                    row.append(f"{pct}%")
                                else:
                                    row.append("")
                            writer.writerow(row)
            
            exported_files.append(str(csv_dir))
        
        progress(1.0, desc="Done!")
        
        # === STATUS ===
        status_lines = [
            f"✅ Complete export by segment finished!",
            f"",
            f"📊 {n_successful} questions processed",
        ]
        
        if n_cached > 0:
            status_lines.append(f"   ↳ {n_cached} loaded from the cache, {n_calculated} newly calculated")
        
        status_lines.append(f"📈 Segmentation by: {', '.join(dim_ids)}")
        
        if errors:
            status_lines.append(f"")
            status_lines.append(f"⚠️ {len(errors)} errors:")
            for err in errors[:5]:
                status_lines.append(f"   - {err}")
            if len(errors) > 5:
                status_lines.append(f"   ... and {len(errors) - 5} more")
        
        status_lines.append(f"")
        status_lines.append(f"📁 Exported files:")
        for f in exported_files:
            status_lines.append(f"   - {f}")
        
        # writes into OUTPUT_DIR directly (no directory per run): only the running record in the data directory
        from export.pipeline import _record_llm_usage
        usage_summary = _record_llm_usage("segmented_export", None, state)
        if usage_summary:
            status_lines += ["", usage_summary]
        
        return "\n".join(status_lines), "\n".join(exported_files)
        
    except Exception as e:
        logger.error(f"Segmented export error: {e}", exc_info=True)
        return f"❌ Export error: {e}", ""


def _dimension_of_question(qid: str):
    """Id of the segment dimension whose source question is ``qid``, if any."""
    from .segment_context import get_context
    try:
        resolver = get_context().resolver
    except Exception:
        return None
    for dim_id in resolver.dimension_ids():
        if resolver.get_dimension(dim_id).source_question == qid:
            return dim_id
    return None


def _report_title(state, lang, entered):
    from export.pipeline import report_title
    return report_title(state, lang, entered)


def _as_analysis_result(qid: str, qconfig: dict, result):
    """
    An analysis result as an AnalysisResult. Results restored from the database are
    plain dicts, from which the handlers cannot draw: questions without LLM work are
    recalculated (fast), free text is rebuilt from the stored fields.
    """
    from handlers.base import AnalysisResult
    if hasattr(result, '__dataclass_fields__'):
        return result
    handler_type = qconfig.get('handler', '')
    if handler_type != 'freetext' and state.df is not None:
        from handlers import get_handler
        handler = get_handler(handler_type, qid, qconfig)
        if handler is not None:
            from utils.languages import get_source_language
            return handler.analyze(state.df, lang=get_source_language(state.config))
    result = result or {}
    return AnalysisResult(question_id=qid, n=result.get('n', 0), data=result.get('data', {}),
                          percentages=result.get('percentages'), description=result.get('description', ''),
                          warnings=result.get('warnings') or [], metadata=result.get('metadata') or {})
