# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Analysis module of the survey analysis tool: analysis functions and segmentation.
"""

import logging
from typing import List, Tuple, Optional

import pandas as pd
import matplotlib.pyplot as plt

from core import state, load_config
from handlers import get_handler
from export.segment_context import get_context
from services.ui_values import (ALL, LABEL_ALL_COMPARE, LABEL_NO_SEGMENTATION, NO_SEGMENTATION,
                               choice_all, pairs)

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


def get_segmentation_options() -> list:
    """
    Choices for the segmentation dropdown: (label, dimension id) pairs from
    settings.segmentation.dimensions, preceded by "no segmentation".
    """
    options = [(LABEL_NO_SEGMENTATION, NO_SEGMENTATION)]
    if not state.config or state.df is None:
        return options
    ctx = get_context()
    for dim_id in ctx.dimension_ids():
        options.append((ctx.label(dim_id).get(ctx.source_language, dim_id), dim_id))
    return options


def apply_segmentation(segment_choice: str) -> Tuple[str, int]:
    """
    Applies a segmentation.
    
    Args:
        segment_choice: chosen segmentation (dimension id or NO_SEGMENTATION)
    
    Returns:
        tuple (status message, number of filtered rows)
    """
    if state.df is None:
        return "No data loaded", 0
    
    if segment_choice == NO_SEGMENTATION or not segment_choice:
        state.current_segment = None
        state.segment_filter = None
        return f"Segmentation removed. All {len(state.df)} records active.", len(state.df)
    
    try:
        ctx = get_context()
        dim_id = segment_choice
        if dim_id not in ctx.dimension_ids():
            return f"Unknown segmentation: {dim_id}", 0
        values = ctx.values(dim_id)
        label = ctx.label(dim_id).get(ctx.source_language, dim_id)
        state.current_segment = dim_id
        state.segment_column = ctx.column(dim_id)  # materialised column in ctx.frame
        state.segment_filter = None  # reset the filter: shows the comparison chart
        return (
            f"Segmentation by '{label}' active.\n"
            f"Available values: {', '.join(values[:10])}"
            f"{'...' if len(values) > 10 else ''}\n\n"
            f"ℹ️ 'All' shows the comparison chart, a single value filters.",
            len(state.df)
        )
        
    except Exception as e:
        logger.error(f"Segmentation error: {e}", exc_info=True)
        return f"Error: {e}", 0


def get_segment_values() -> list:
    """
    Choices of the current segmentation as (label, value) pairs: "all
    (comparison)" first, then the values in the configured order.
    """
    if state.current_segment is None or state.df is None:
        return []
    return [choice_all(LABEL_ALL_COMPARE)] + pairs(get_context().values(state.current_segment))


def set_segment_filter(value: str) -> str:
    """
    Sets the segment filter to a value.
    
    Args:
        value: filter value, or ALL
    
    Returns:
        status message
    """
    if state.df is None:
        return "No data loaded"
    
    if value == ALL or not value:
        state.segment_filter = None
        return f"Comparison mode active. Shows a grouped chart for all segment values."
    
    if state.current_segment is None:
        return "No segmentation active"
    
    # Mask on the materialised frame; same index as state.df
    state.segment_filter = get_context().mask(state.current_segment, value)
    count = state.segment_filter.sum()
    
    return f"Filter active: '{value}' ({count} records)"


def get_question_groups() -> List[str]:
    """The configured question groups, with a mark for the analysed ones."""
    if state.config is None:
        load_config()
    
    if not state.config:
        return []
    
    groups = []
    for qid, qconfig in state.config.get('question_groups', {}).items():
        label = _src_text(qconfig.get('label', {}), None, qid)
        handler = qconfig.get('handler', 'unknown')
        # mark questions already analysed
        marker = "✓ " if qid in state.analysis_results else ""
        groups.append(f"{qid}: {marker}{label} [{handler}]")
    
    return groups


def analyze_question(
    question_choice: str,
    lang: str = "de"
) -> Tuple[str, Optional[plt.Figure], str]:
    """
    Analyses a chosen question.
    
    If a segmentation is chosen (e.g. "role") but no specific value,
    a grouped comparison chart is created.
    
    Args:
        question_choice: question chosen in the dropdown
        lang: language
    
    Returns:
        tuple (result text, figure, cluster details)
    """
    if not question_choice:
        return "No question selected", None, ""
    
    if state.df is None:
        return "No data loaded", None, ""
    
    # extract the question id
    qid = question_choice.split(":")[0].strip()
    
    if state.config is None:
        load_config()
    
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    if not qconfig:
        return f"No configuration for {qid}", None, ""
    
    handler_type = qconfig.get('handler', 'single_choice')
    
    try:
        # segment comparison wanted?
        # (a segmentation chosen, but no specific value)
        if state.current_segment and state.segment_filter is None:
            return _analyze_question_segmented(qid, qconfig, handler_type, lang)
        
        # create the handler
        handler = get_handler(handler_type, qid, qconfig)
        if not handler:
            return f"No handler for type '{handler_type}'", None, ""
        
        # use the filtered data
        df = state.get_filtered_df()
        
        # run the analysis
        if handler_type == 'freetext':
            result = handler.analyze(
                df, 
                lang=lang,
                db=state.db,
                import_id=state.import_id
            )
        else:
            result = handler.analyze(df, lang=lang)
        
        # keep the result (in-memory cache)
        state.analysis_results[qid] = result
        
        # IMPORTANT: store it in the database as well, for later exports!
        if state.db and state.import_id:
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
                    if 'percentages_df' in metadata_val:
                        if hasattr(metadata_val['percentages_df'], 'to_dict'):
                            metadata_val['percentages_df'] = metadata_val['percentages_df'].to_dict()
                
                result_dict = {
                    'n': result.n if hasattr(result, 'n') else 0,
                    'data': data_val,
                    'percentages': pct_val,
                    'description': result.description if hasattr(result, 'description') else '',
                    'warnings': result.warnings if hasattr(result, 'warnings') else [],
                    'clusters': result.clusters if hasattr(result, 'clusters') else None,
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
                logger.info(f"Analysis result of {qid} stored in the database")
            except Exception as e:
                logger.warning(f"Could not store the analysis result in the database: {qid}: {e}")
        
        # build the result text
        result_text = [
            f"📊 **{qconfig.get('label', {}).get(lang, qid)}**",
        ]
        
        # add segment information when filtered
        if state.current_segment and state.segment_filter is not None:
            seg_label = get_context().label(state.current_segment).get(lang, state.current_segment)
            result_text.append(f"*Filter: {seg_label}*")
        
        # n display: special format for free text with items
        if handler_type == 'freetext' and result.metadata.get('analysis_mode') in ('items', 'fields'):
            n_items = result.metadata.get('total_items', result.n)
            n_responses = result.metadata.get('unique_responses', result.n)
            if lang == "de":
                result_text.append(f"Insgesamt {n_items} Nennungen von {n_responses} Personen")
            elif lang == "fr":
                result_text.append(f"Au total {n_items} mentions de {n_responses} personnes")
            else:
                result_text.append(f"Total {n_items} mentions from {n_responses} persons")
        else:
            result_text.append(f"n = {result.n}")
        
        result_text.append("")
        
        if result.warnings:
            for w in result.warnings:
                result_text.append(f"⚠️ {w}")
        
        # create the chart with translated labels
        default_chart = qconfig.get('default_chart', 'bar_horizontal')
        fig = _create_translated_chart(handler, result, default_chart, qconfig, lang)
        
        # cluster details (for free text)
        cluster_details = ""
        if handler_type == 'freetext' and result.clusters:
            cluster_details = format_cluster_details(result, lang)
        
        return "\n".join(result_text), fig, cluster_details
        
    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        return f"❌ Analysis error: {e}", None, ""


def _analyze_question_segmented(
    qid: str,
    qconfig: dict,
    handler_type: str,
    lang: str
) -> Tuple[str, Optional[plt.Figure], str]:
    """
    Creates a grouped comparison chart for all segment values.
    
    Args:
        qid: question id
        qconfig: question configuration
        handler_type: handler type
        lang: language
    
    Returns:
        tuple (text, figure, cluster details)
    """
    from utils.charts import ChartGenerator
    
    ctx = get_context()
    seg_key = state.current_segment
    if seg_key not in ctx.dimension_ids():
        return f"Segmentation not found: {seg_key}", None, ""
    seg_column = ctx.column(seg_key)
    seg_label = ctx.label(seg_key).get(lang, seg_key)
    data = ctx.frame  # state.df plus materialised segment columns
    
    # all segment values (configured order)
    segment_values = ctx.values(seg_key)
    
    # create the handler
    handler = get_handler(handler_type, qid, qconfig)
    if not handler:
        return f"No handler for type '{handler_type}'", None, ""
    
    # run the analysis for every segment value
    segment_results = {}
    n_total = 0
    
    for seg_value in segment_values:
        seg_df = data[data[seg_column] == seg_value]
        if len(seg_df) < 5:  # minimum n
            continue
            
        if handler_type == 'freetext':
            result = handler.analyze(seg_df, lang=lang, db=state.db, import_id=state.import_id)
        else:
            result = handler.analyze(seg_df, lang=lang)
        
        if result and result.n > 0:
            # percentages of this segment
            segment_results[str(seg_value)] = {
                'percentages': result.percentages or {},
                'n': result.n
            }
            n_total += result.n
    
    if not segment_results:
        return "Not enough data for a segment comparison (at least n=5 per segment)", None, ""
    
    # data for the grouped chart
    # format: {segment: {category: percent}}
    grouped_data = {}
    for seg_value, seg_result in segment_results.items():
        # translate the labels
        translated_pcts = _translate_data_labels(seg_result['percentages'], qconfig, lang)
        grouped_data[f"{seg_value} (n={seg_result['n']})"] = translated_pcts
    
    # result text
    title = qconfig.get('label', {}).get(lang, qid)
    result_text = [
        f"📊 **{title}**",
        f"*Comparison by {seg_label}*",
        f"n = {n_total} (total)",
        ""
    ]
    
    generator = ChartGenerator()
    from utils.export_structured import is_matrix_data, matrix_means, matrix_scale, matrix_segment_chart_mode
    if is_matrix_data(grouped_data):
        # a matrix has a share per row and category: small multiples per row, or the
        # mean per row and segment (matrix_segment_chart = mean); one figure fits here
        mode = matrix_segment_chart_mode(qconfig, (state.config or {}).get('settings', {}))
        scale = matrix_scale(qconfig, grouped_data, lang)
        means = matrix_means(grouped_data, scale, qconfig) if mode == 'mean' else {}
        if means:
            fig = generator.matrix_means_by_segment(means, title=title, segment_label=seg_label, lang=lang)
        else:
            fig = generator.matrix_rows_by_segment(grouped_data, scale, title=title, segment_label=seg_label, lang=lang)
        return "\n".join(result_text), fig, ""
    # create the grouped bar chart
    fig = generator.bar_grouped(
        category_order=_category_order(qconfig, lang),
        data=grouped_data,
        title=title,
        segment_label=seg_label,
        show_percentages=True,
        lang=lang,
        n_info=f"Total: n={n_total}"
    )
    
    return "\n".join(result_text), fig, ""


def _category_order(qconfig: dict, lang: str) -> list:
    """
    Configured order of answer categories in ``lang`` (options per language),
    used for ordered scales; empty if the question is not marked as ordered.
    """
    if not (qconfig.get('ordinal') or qconfig.get('sort_order') or qconfig.get('handler') == 'matrix_likert'):
        return []
    from utils import prompts
    src = prompts.source_language()
    for key in ('options', 'scale'):
        value = qconfig.get(key)
        if isinstance(value, dict):
            return list(value.get(lang) or value.get(src) or [])
        if isinstance(value, list):
            return list(value)
    return []


def _translate_data_labels(data: dict, qconfig: dict, lang: str) -> dict:
    """
    Translates the labels of a data dictionary.
    
    Args:
        data: dictionary with labels in the source language as keys
        qconfig: question configuration with multilingual options
        lang: target language
    
    Returns:
        dictionary with translated labels
    """
    from utils.prompts import source_language
    src = source_language()
    if lang == src:
        return data
    
    # options from the configuration (labels in the data are in the source language)
    options_source = qconfig.get('options', {}).get(src, [])
    options_target = qconfig.get('options', {}).get(lang, [])
    
    # without translations, return the original
    if not options_source or not options_target or len(options_source) != len(options_target):
        return data
    
    # build the mapping
    translation_map = dict(zip(options_source, options_target))
    
    # translate the labels
    translated = {}
    for key, value in data.items():
        translated_key = translation_map.get(key, key)
        translated[translated_key] = value
    
    return translated


def _create_translated_chart(
    handler,
    result,
    chart_type: str,
    qconfig: dict,
    lang: str
) -> plt.Figure:
    """
    Creates a chart with translated labels.
    
    Args:
        handler: the handler
        result: analysis result
        chart_type: chart type
        qconfig: question configuration
        lang: language
    
    Returns:
        matplotlib figure
    """
    # translate the labels if the language is not the source language
    from utils.prompts import source_language
    if lang != source_language() and result.data:
        # copy of the result with translated labels
        from handlers.base import AnalysisResult
        
        translated_data = _translate_data_labels(result.data, qconfig, lang)
        translated_pcts = _translate_data_labels(result.percentages or {}, qconfig, lang)
        
        translated_result = AnalysisResult(
            question_id=result.question_id,
            n=result.n,
            data=translated_data,
            percentages=translated_pcts,
            description=result.description,
            clusters=result.clusters,
            warnings=result.warnings,
            metadata=result.metadata
        )
        
        return handler.create_chart(translated_result, chart_type, lang=lang)
    
    return handler.create_chart(result, chart_type, lang=lang)


def format_cluster_details(result, lang: str) -> str:
    """Formats the cluster details for the display."""
    if not result.metadata or 'cluster_details' not in result.metadata:
        return ""
    
    lines = ["### Cluster details\n"]
    
    for cluster in result.metadata['cluster_details']:
        name = cluster.get('name', '')
        if isinstance(name, dict):
            name = _src_text(name, lang, str(name))
        unique = cluster.get('unique_respondents', 0)
        count = cluster.get('count', 0)
        
        # consistent format: "X mentions by Y persons" or "X mentions"
        if count > 0 and unique > 0 and count != unique:
            count_text = f"{count} mentions by {unique} persons"
        elif count > 0:
            count_text = f"{count} mentions"
        elif unique > 0:
            count_text = f"{unique} mentions"
        else:
            count_text = ""
        
        heading = f"**{name}** ({count_text})" if count_text else f"**{name}**"
        lines.append(heading)
        desc = cluster.get('description', '')
        if isinstance(desc, dict):
            desc = _src_text(desc, lang, str(desc))
        lines.append(f"_{desc}_\n")
        
        if cluster.get('examples'):
            lines.append("Examples:")
            for ex in cluster['examples'][:3]:
                lines.append(f"- {ex}")
        lines.append("")
    
    return "\n".join(lines)


def get_warning_thresholds() -> dict:
    """The warning thresholds for sample sizes."""
    if state.config is None:
        load_config()
    
    if not state.config:
        return {'warning_threshold': 10, 'minimum_threshold': 5}
    
    return state.config.get('settings', {}).get('sample_warnings', {
        'warning_threshold': 10,
        'minimum_threshold': 5
    })


def analyze_question_by_segments(
    qid: str,
    qconfig: dict,
    lang: str = "de",
    skip_existing: bool = True
) -> dict:
    """
    Analyses a question broken down by all segmentation dimensions.
    
    Args:
        qid: question id
        qconfig: question configuration
        lang: language
        skip_existing: use stored free-text results (resume mode); other
            question types are always recalculated
    
    Returns:
        dict with the structure:
        {
            'total': AnalysisResult,
            'by_segment': {
                'role': {
                    'label': 'Role',
                    'column': '...',
                    'segments': {
                        'Student': {'result': AnalysisResult, 'n': 25, 'warning': None},
                        'Staff': {'result': AnalysisResult, 'n': 8, 'warning': 'n < 10'},
                        ...
                    }
                },
                'faculty': {...},
                ...
            }
        }
    """
    import json
    
    if state.df is None:
        return {}
    
    handler_type = qconfig.get('handler', 'single_choice')
    handler = get_handler(handler_type, qid, qconfig)
    
    if not handler:
        logger.error(f"No handler for {qid}")
        return {}
    
    thresholds = get_warning_thresholds()
    warning_n = thresholds.get('warning_threshold', 10)
    
    result = {
        'question_id': qid,
        'question_text': qconfig.get('label', {}).get(lang, qid),
        'handler_type': handler_type,
        'total': None,
        'by_segment': {}
    }
    
    # The cache saves LLM work: only free text (clustering) is loaded from it. All
    # other question types are recalculated (fast); a stored result is a plain dict,
    # from which the handlers cannot draw their charts.
    use_cache = skip_existing and handler_type == 'freetext'
    
    # 1. overall analysis (check whether already present)
    cached_total = None
    if use_cache and state.db and state.import_id:
        existing = state.db.get_analysis_result(state.import_id, qid, segment_type="all")
        if existing:
            cached_total = existing.get('result_json')
            if isinstance(cached_total, str):
                cached_total = json.loads(cached_total)
    
    if cached_total:
        result['total'] = cached_total
        logger.debug(f"Overall result of {qid} loaded from the cache")
    else:
        try:
            if handler_type == 'freetext':
                total_result = handler.analyze(
                    state.df, 
                    lang=lang,
                    db=state.db,
                    import_id=state.import_id
                )
            else:
                total_result = handler.analyze(state.df, lang=lang)
            
            result['total'] = total_result
            state.analysis_results[qid] = total_result
            
            # store in the database (overall)
            if state.db and state.import_id and total_result:
                _save_result_to_db(qid, handler_type, total_result, segment_type="all", segment_value=None)
            
        except Exception as e:
            logger.error(f"Overall analysis of {qid} failed: {e}")
            return result
    
    # 2. segmented analyses
    ctx = get_context()
    data = ctx.frame
    for seg_key in ctx.dimension_ids():
        seg_column = ctx.column(seg_key)
        seg_label = ctx.label(seg_key).get(lang, seg_key)
        
        result['by_segment'][seg_key] = {
            'label': seg_label,
            'column': seg_column,
            'segments': {}
        }
        
        # distinct values of the segmentation variable
        unique_values = ctx.values(seg_key)
        
        for seg_value in unique_values:
            seg_value_str = str(seg_value)
            
            # segment already in the database?
            cached_segment = None
            if use_cache and state.db and state.import_id:
                existing_seg = state.db.get_analysis_result(
                    state.import_id, qid,
                    segment_type=seg_key,
                    segment_value=seg_value_str
                )
                if existing_seg:
                    cached_segment = existing_seg.get('result_json')
                    if isinstance(cached_segment, str):
                        cached_segment = json.loads(cached_segment)
            
            if cached_segment:
                # load from the cache
                n = cached_segment.get('n', 0)
                warning = f"n < {warning_n}" if n < warning_n else None
                result['by_segment'][seg_key]['segments'][seg_value_str] = {
                    'result': cached_segment,
                    'n': n,
                    'warning': warning,
                    'from_cache': True
                }
                continue
            
            # calculate anew
            mask = data[seg_column] == seg_value
            filtered_df = data[mask].copy()
            n = len(filtered_df)
            
            if n == 0:
                continue
            
            # warning for a small n
            warning = f"n < {warning_n}" if n < warning_n else None
            
            try:
                # the handler needs a new state for free text
                if handler_type == 'freetext':
                    seg_result = handler.analyze(
                        filtered_df, 
                        lang=lang,
                        db=state.db,
                        import_id=state.import_id,
                        use_cached_clustering=False  # cluster again for the segment
                    )
                else:
                    seg_result = handler.analyze(filtered_df, lang=lang)
                
                result['by_segment'][seg_key]['segments'][seg_value_str] = {
                    'result': seg_result,
                    'n': n,
                    'warning': warning
                }
                
                # store in the database (segmented)
                if state.db and state.import_id and seg_result:
                    _save_result_to_db(qid, handler_type, seg_result, segment_type=seg_key, segment_value=seg_value_str)
                
            except Exception as e:
                logger.error(f"Segment analysis of {qid}/{seg_key}/{seg_value} failed: {e}")
    
    return result


def _save_result_to_db(qid: str, handler_type: str, result, segment_type: str, segment_value: str):
    """Stores an analysis result in the database."""
    try:
        # convert the result to a dict
        if hasattr(result, '__dataclass_fields__') or hasattr(result, 'n'):
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
                if 'percentages_df' in metadata_val:
                    if hasattr(metadata_val['percentages_df'], 'to_dict'):
                        metadata_val['percentages_df'] = metadata_val['percentages_df'].to_dict()
            
            result_dict = {
                'n': result.n if hasattr(result, 'n') else 0,
                'data': data_val,
                'percentages': pct_val,
                'description': result.description if hasattr(result, 'description') else '',
                'warnings': result.warnings if hasattr(result, 'warnings') else [],
                'clusters': result.clusters if hasattr(result, 'clusters') else [],
                'metadata': metadata_val
            }
            n = result_dict['n']
        else:
            result_dict = result
            n = result.get('n', 0) if isinstance(result, dict) else 0
        
        state.db.save_analysis_result(
            import_id=state.import_id,
            question_id=qid,
            handler_type=handler_type,
            result=result_dict,
            n=n,
            segment_type=segment_type,
            segment_value=segment_value
        )
    except Exception as e:
        logger.warning(f"Could not store the result in the database: {qid}/{segment_type}/{segment_value}: {e}")


def analyze_all_questions_with_segments(
    lang: str = "de",
    progress_callback=None,
    skip_existing: bool = True
) -> dict:
    """
    Analyses all questions with all segmentations.
    
    Args:
        lang: language
        progress_callback: optional callback(current, total, message)
        skip_existing: resume mode (stored free-text results are used)
    
    Returns:
        dict with all analysis results
    """
    if state.df is None or state.config is None:
        return {}
    
    question_groups = state.config.get('question_groups', {})
    total = len(question_groups)
    
    all_results = {}
    skipped = 0
    
    for i, (qid, qconfig) in enumerate(question_groups.items()):
        if progress_callback:
            progress_callback(i, total, f"Analyzing: {qid}")
        
        # already in the database (resume mode)?
        # IMPORTANT: analyze_question_by_segments is still called, so the segmented
        # results are loaded as well; skip_existing makes it use the cached free-text results.
        
        logger.info(f"Processing {qid} ({i+1}/{total})")
        
        try:
            result = analyze_question_by_segments(qid, qconfig, lang, skip_existing=skip_existing)
            
            # completely from the cache (total and all segments)?
            from_cache = False
            if result.get('total') and isinstance(result['total'], dict):
                # a dict means from the cache (an AnalysisResult would be an object)
                from_cache = True
            
            all_results[qid] = result
            if from_cache:
                skipped += 1
        except Exception as e:
            logger.error(f"Error in {qid}: {e}", exc_info=True)
            all_results[qid] = {'error': str(e)}
    
    if progress_callback:
        msg = "Analysis finished"
        if skipped > 0:
            msg += f" ({skipped} from the cache)"
        progress_callback(total, total, msg)
    
    return all_results


def build_comparison_table(
    segment_results: dict,
    answer_keys: List[str] = None
) -> dict:
    """
    Builds a comparison table from segment results.
    
    Args:
        segment_results: dict segment -> {'result': AnalysisResult, 'n': int, 'warning': str}
        answer_keys: optional list of the answer keys (for a consistent order)
    
    Returns:
        dict with the structure:
        {
            'columns': [
                {'name': 'Student', 'n': 25, 'warning': None},
                {'name': 'Staff', 'n': 8, 'warning': 'n < 10'},
                ...
            ],
            'rows': [
                {
                    'label': 'Chatbots',
                    'values': [
                        {'count': 18, 'percent': 72.0},
                        {'count': 7, 'percent': 87.5},
                        ...
                    ]
                },
                ...
            ]
        }
    """
    if not segment_results:
        return {'columns': [], 'rows': []}
    
    # columns (segments)
    columns = []
    for seg_name, seg_data in segment_results.items():
        columns.append({
            'name': seg_name,
            'n': seg_data.get('n', 0),
            'warning': seg_data.get('warning')
        })
    
    # collect the answer keys (if not given)
    if answer_keys is None:
        answer_keys = set()
        for seg_data in segment_results.values():
            result = seg_data.get('result')
            if result:
                # objects as well as dicts
                if hasattr(result, 'data'):
                    data = result.data
                else:
                    data = result.get('data', {})
                if isinstance(data, dict):
                    answer_keys.update(data.keys())
        answer_keys = sorted(answer_keys, key=str)
    
    # rows (answer options)
    rows = []
    for key in answer_keys:
        row = {
            'label': key,
            'values': []
        }
        
        for seg_name in segment_results.keys():
            seg_data = segment_results.get(seg_name)
            if not seg_data:
                row['values'].append({'count': 0, 'percent': 0})
                continue
                
            result = seg_data.get('result')
            
            if result:
                # objects as well as dicts
                if hasattr(result, 'data'):
                    data = result.data
                    percentages = result.percentages if hasattr(result, 'percentages') else {}
                else:
                    data = result.get('data', {})
                    percentages = result.get('percentages', {})
                
                if data and isinstance(data, dict):
                    count = data.get(key, 0)
                    percent = percentages.get(key, 0) if percentages else 0
                else:
                    count = 0
                    percent = 0
            else:
                count = 0
                percent = 0
            
            row['values'].append({
                'count': count,
                'percent': round(percent, 1) if isinstance(percent, float) else percent
            })
        
        rows.append(row)
    
    return {
        'columns': columns,
        'rows': rows
    }
