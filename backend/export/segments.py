# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Per-segment and cross-filter results for the dashboard export.
"""

import logging
from typing import List, Tuple


from core import state
from .segment_context import get_context
from .result_format import _format_result_for_dashboard

logger = logging.getLogger(__name__)


# === KREUZFILTER ===



def _source_lang() -> str:
    from utils.languages import get_source_language
    return get_source_language(state.config)

def _get_segment_combinations() -> List[Tuple[str, ...]]:
    """Configured cross filters (settings.segmentation.cross_filters), sorted ids."""
    return list(get_context().resolver.cross_filters)


def _make_cross_segment_key(segment_names: Tuple[str, ...]) -> str:
    """
    Builds the results key of a segment combination.
    
    Args:
        segment_names: tuple of the segment names
    
    Returns:
        key such as "by_role_area"
    """
    # sort alphabetically (should be sorted already, but to be safe)
    sorted_names = sorted(segment_names)
    return "by_" + "_".join(sorted_names)


def _make_cross_value_key(segment_values: Tuple[str, ...], segment_types: Tuple[str, ...]) -> str:
    """Value key of a combination, e.g. "deutschland|universitaet"."""
    ctx = get_context()
    return "|".join(ctx.key(dim, str(v)) for dim, v in zip(segment_types, segment_values))


def _load_cross_segment_results(
    qid: str,
    segment_combo: Tuple[str, ...],
    progress_callback=None
) -> dict:
    """
    Computes the cross filter results of a segment combination.
    
    Args:
        qid: question id
        segment_combo: tuple of the segment names, e.g. ('role', 'area')
        progress_callback: optional callback(message) for the progress display
    
    Returns:
        dict with the results per combination, e.g. {"Student|STEM": {...}}
    """
    results = {}
    
    if state.config is None or state.df is None:
        logger.warning(f"No configuration or DataFrame for the cross filter {segment_combo}")
        return {}
    
    ctx = get_context()
    min_n = ctx.resolver.cross_filter_min_n
    columns = [ctx.column(seg_name) for seg_name in segment_combo]
    
    # question configuration and handler
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    handler_type = qconfig.get('handler', 'single_choice')
    
    # Rows with a value in every dimension of the combination
    df_clean = ctx.frame.dropna(subset=columns)
    
    if len(df_clean) == 0:
        logger.warning(f"No valid data for the cross filter {segment_combo}")
        return {}
    
    # group
    try:
        grouped = df_clean.groupby(columns, dropna=True)
    except Exception as e:
        logger.warning(f"Grouping failed for {segment_combo}: {e}")
        return {}
    
    combo_key = _make_cross_segment_key(segment_combo)
    total_groups = len(grouped)
    processed = 0
    
    for group_values, group_df in grouped:
        processed += 1
        
        # group_values may be a single value or a tuple
        if not isinstance(group_values, tuple):
            group_values = (group_values,)
        
        # check the minimum n
        if len(group_df) < min_n:
            continue
        
        # build the value keys (with stable keys)
        value_key = _make_cross_value_key(group_values, segment_combo)
        
        if progress_callback and processed % 10 == 0:
            progress_callback(f"Kreuzfilter {combo_key}: {processed}/{total_groups}")
        
        try:
            # handler for the computation
            from handlers import get_handler
            handler = get_handler(handler_type, qid, qconfig)
            
            if not handler:
                logger.warning(f"No handler for {qid} ({handler_type}) - skipping the cross filter calculation")
                continue
            
            if handler_type == 'freetext':
                result = handler.analyze(
                    group_df,
                    lang=ctx.source_language,
                    db=state.db,
                    import_id=state.import_id,
                    use_cached_clustering=True
                )
            else:
                result = handler.analyze(group_df, lang=ctx.source_language)
            
            if result and hasattr(result, 'n') and result.n >= min_n:
                # convert the result to a dict
                result_dict = {
                    'n': result.n,
                    'data': {},
                    'percentages': {},
                    'metadata': result.metadata if hasattr(result, 'metadata') else {}
                }
                
                # extract the data
                if hasattr(result, 'data'):
                    if hasattr(result.data, 'to_dict'):
                        result_dict['data'] = result.data.to_dict()
                    elif isinstance(result.data, dict):
                        result_dict['data'] = result.data
                
                # extract the percentages
                if hasattr(result, 'percentages'):
                    if hasattr(result.percentages, 'to_dict'):
                        result_dict['percentages'] = result.percentages.to_dict()
                    elif isinstance(result.percentages, dict):
                        result_dict['percentages'] = result.percentages
                
                # format
                formatted = _format_result_for_dashboard(result_dict, handler_type)
                results[value_key] = formatted
                
        except Exception as e:
            # IMPORTANT: log errors properly for debugging
            logger.warning(f"Cross filter calculation failed for {qid}/{value_key}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    if len(results) == 0:
        logger.warning(f"Cross filter {combo_key} for {qid}: NO results (of {total_groups} groups, min_n={min_n})")
    else:
        logger.info(f"Cross filter {combo_key} for {qid}: {len(results)} combinations (of {total_groups} groups)")
    
    return results


def _load_segmented_results(qid: str, seg_key: str, progress_callback=None) -> dict:
    """
    Loads or computes segmented results.
    
    IMPORTANT: always computes afresh, as the database results may be
    outdated or incomplete.
    
    Args:
        qid: question id
        seg_key: segment key (e.g. 'role')
        progress_callback: optional callback(message) for the progress display
    """
    results = {}
    
    # check the configuration and the DataFrame
    if state.config is None or state.df is None:
        logger.warning(f"No configuration or DataFrame for the segment {seg_key}")
        return {}
    
    ctx = get_context()
    if seg_key not in ctx.dimension_ids():
        logger.warning(f"Segment {seg_key} is not configured")
        return {}
    column = ctx.column(seg_key)
    value_counts = ctx.counts(seg_key)
    segment_values = ctx.values(seg_key)
    
    logger.info(f"Segment {seg_key} for {qid}: {len(segment_values)} values found")
    for val, count in sorted(value_counts.items(), key=lambda x: -x[1])[:10]:
        logger.info(f"  - '{val}': {count} entries")
    
    if not segment_values:
        logger.warning(f"No segment values for {seg_key}")
        return {}
    
    # handler and configuration of the question
    qconfig = state.config.get('question_groups', {}).get(qid, {})
    handler_type = qconfig.get('handler', 'single_choice')
    
    # compute the analysis for every segment value
    for i, seg_value in enumerate(segment_values):
        if progress_callback:
            progress_callback(f"Berechne {seg_key}: {seg_value} ({i+1}/{len(segment_values)})")
        
        try:
            # ALWAYS compute afresh, ignore the database cache
            result_data = _calculate_segment_result(qid, qconfig, seg_key, column, seg_value)
            
            if result_data and result_data.get('n', 0) > 0:
                formatted = _format_result_for_dashboard(result_data, handler_type)
                
                stable_key = ctx.key(seg_key, seg_value)
                results[stable_key] = formatted
                logger.debug(f"  {seg_key}={seg_value} -> {stable_key}: n={result_data.get('n', 0)}")
            else:
                logger.debug(f"  {seg_key}={seg_value}: no data or n=0")
                
        except Exception as e:
            logger.warning(f"Error in the segment {seg_key}/{seg_value} for {qid}: {e}")
    
    logger.info(f"Segment {seg_key} for {qid}: {len(results)} results exported")
    if results:
        logger.info(f"  segments (keys): {list(results.keys())}")
    
    return results


def _calculate_segment_result(qid: str, qconfig: dict, seg_key: str, column: str, seg_value: str) -> dict:
    """
    Computes a segment result by filtering and analysing the data.
    
    Args:
        qid: question id
        qconfig: question configuration
        seg_key: segment key (e.g. 'role'), or 'all' for the overall result
        column: column name for the filtering (None for the overall result)
        seg_value: value filtered by (None for the overall result)
    
    Returns:
        dictionary with the analysis result, or None
    """
    from handlers import get_handler
    
    handler_type = qconfig.get('handler', 'single_choice')
    handler = get_handler(handler_type, qid, qconfig)
    
    if not handler:
        logger.warning(f"No handler for {qid} ({handler_type})")
        return None
    
    if state.df is None:
        logger.warning(f"DataFrame not available for {qid}")
        return None
    ctx = get_context()
    data = ctx.frame  # state.df plus materialised segment columns, same index
    
    # overall result or filtered result?
    if seg_key == "all" or column is None or seg_value is None:
        # overall result, no filtering
        filtered_df = data.copy()
        n_filtered = len(filtered_df)
        logger.debug(f"Overall result for {qid}: {n_filtered} rows")
    else:
        # filtered result
        if column not in data.columns:
            logger.warning(f"Column '{column}' not available for {qid}")
            return None
        
        filtered_df = data[data[column] == seg_value].copy()
        n_filtered = len(filtered_df)
        
        logger.debug(f"Segment {seg_key}='{seg_value}': {n_filtered} of {len(data)} rows")
        
        if n_filtered == 0:
            return None
    
    # run the analysis
    try:
        if handler_type == 'freetext':
            result = handler.analyze(
                filtered_df,
                lang=_source_lang(),
                db=state.db,
                import_id=state.import_id,
                use_cached_clustering=False  # IMPORTANT: compute afresh for correct answer_ids
            )
        else:
            result = handler.analyze(filtered_df, lang=_source_lang())
    except Exception as e:
        logger.warning(f"Analysis of {qid} failed: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None
    
    if not result:
        logger.debug(f"The handler returned no result for {qid}")
        return None
    
    # convert the result to a dict:
    # copy the metadata and convert DataFrames
    metadata = dict(result.metadata) if hasattr(result, 'metadata') and result.metadata else {}
    if 'percentages_df' in metadata and hasattr(metadata['percentages_df'], 'to_dict'):
        metadata['percentages_df'] = metadata['percentages_df'].to_dict()
    
    result_dict = {
        'n': result.n if hasattr(result, 'n') else n_filtered,
        'data': {},
        'percentages': {},
        'metadata': metadata
    }
    
    # extract the data (may be a DataFrame for a matrix)
    if hasattr(result, 'data'):
        if hasattr(result.data, 'to_dict'):
            # DataFrame -> {column: {index: value}}
            result_dict['data'] = result.data.to_dict()
        elif isinstance(result.data, dict):
            result_dict['data'] = result.data
    
    # extract the percentages (DataFrame or dict)
    if hasattr(result, 'percentages'):
        if hasattr(result.percentages, 'to_dict'):
            # DataFrame -> {column: {index: value}}
            result_dict['percentages'] = result.percentages.to_dict()
        elif isinstance(result.percentages, dict):
            result_dict['percentages'] = result.percentages
    
    # matrix: use percentages_df from the metadata if present
    if handler_type in ['matrix_likert', 'cooperation_matrix']:
        pct_df = result_dict['metadata'].get('percentages_df')
        if pct_df is not None and hasattr(pct_df, 'to_dict'):
            result_dict['percentages'] = pct_df.to_dict()
    
    # store in the database for caching (ALL results, including "all")
    if state.db and state.import_id:
        try:
            state.db.save_analysis_result(
                import_id=state.import_id,
                question_id=qid,
                handler_type=handler_type,
                result=result_dict,
                n=result_dict['n'],
                segment_type=seg_key,
                segment_value=str(seg_value).strip() if seg_value else None
            )
        except Exception as e:
            logger.debug(f"Could not store the result in the database: {e}")
    
    return result_dict
