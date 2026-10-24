# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Conversion of a handler result into the dashboard result format.
"""

import logging




logger = logging.getLogger(__name__)


def _src() -> str:
    """Source language of the loaded survey: plain cluster texts are written in it."""
    from utils.prompts import source_language
    return source_language()


def _format_result_for_dashboard(result: dict, handler_type: str) -> dict:
    """
    Formats an analysis result for the dashboard.
    
    Per specification:
    - single_choice/multi_choice: {n, values, percentages}
    - freetext: {n, n_items, n_responses, cluster_counts, cluster_percentages}
    - matrix_likert: {n, by_row: {row_name: {n, values, percentages}}}
    - ranking: {n, values, percentages}
    """
    formatted = {
        "n": result.get('n', 0)
    }
    
    data = result.get('data', {})
    percentages = result.get('percentages', {})
    metadata = result.get('metadata', {})
    
    if handler_type == 'freetext':
        # free text: cluster counts and percentages
        # n = number of respondents (persons)
        # n_items = number of single items/mentions
        # n_responses = number of respondents (= n)
        analysis_mode = metadata.get('analysis_mode', 'raw')
        formatted["n"] = result.get('n', 0)
        formatted["n_items"] = metadata.get('total_items', result.get('n', 0))
        formatted["n_responses"] = metadata.get('unique_responses', 0) or result.get('n', 0)
        formatted["analysis_mode"] = analysis_mode
        formatted["avg_items_per_response"] = metadata.get('avg_items_per_response', 0)
        formatted["cluster_counts"] = data if isinstance(data, dict) else {}
        formatted["cluster_percentages"] = percentages if isinstance(percentages, dict) else {}
        
        # cluster details with count and unique_respondents for the dashboard
        cluster_details = metadata.get('cluster_details', [])
        if cluster_details:
            formatted["clusters"] = []
            for cd in cluster_details:
                name = cd.get('name', '')
                if isinstance(name, dict):
                    cluster_entry = {"name": name}
                else:
                    cluster_entry = {"name": {_src(): name}}
                
                desc = cd.get('description', '')
                if isinstance(desc, dict):
                    cluster_entry["description"] = desc
                else:
                    cluster_entry["description"] = {_src(): desc}
                
                cluster_entry["count"] = cd.get('count', 0)
                cluster_entry["unique_respondents"] = cd.get('unique_respondents', cd.get('count', 0))
                cluster_entry["examples"] = cd.get('examples', [])
                cluster_entry["examples_translated"] = cd.get('examples_translated', {})
                formatted["clusters"].append(cluster_entry)
        
    elif handler_type in ['matrix_likert', 'cooperation_matrix']:
        # matrix: by_row format with results per row;
        # the dashboard also expects the "items" format with mean values;
        # is the by_row format present already?
        by_row = result.get('by_row', {})
        
        logger.debug(f"matrix_likert Format-Debug: by_row={bool(by_row)}, data type={type(data)}, pct type={type(percentages)}")
        if data:
            first_data_key = list(data.keys())[0] if data else None
            logger.debug(f"  data keys: {list(data.keys())[:3]}, first value type: {type(data.get(first_data_key)) if first_data_key else None}")
        
        if by_row:
            # in the right format already
            formatted["by_row"] = by_row
            # also as "items" for the dashboard
            formatted["items"] = by_row
        elif data and percentages:
            # convert from the flat format to by_row
            # data = {row_name: count} or {scale_value: {row_name: count}}
            # percentages = {row_name: pct} or {scale_value: {row_name: pct}}
            
            # nested (scale -> row -> value)?
            first_key = list(percentages.keys())[0] if percentages else None
            if first_key and isinstance(percentages.get(first_key), dict):
                # Format B: percentages = {Scale: {Row: pct}}
                # Konvertiere zu by_row = {Row: {percentages: {Scale: pct}}}
                # rows in the order of the result (the handler sorts them); a set
                # would give a different order in every run
                rows = {}
                for scale_vals in percentages.values():
                    if isinstance(scale_vals, dict):
                        rows.update(dict.fromkeys(scale_vals.keys()))
                
                formatted["by_row"] = {}
                formatted["items"] = {}  # dashboard format
                
                # scale from the metadata (order of the columns)
                scale = result.get('metadata', {}).get('scale', [])
                
                for row in rows:
                    row_percentages = {}
                    row_values = {}
                    row_total = 0
                    
                    for scale_idx, scale_val in enumerate(scale if scale else percentages.keys()):
                        scale_data_pct = percentages.get(scale_val, {})
                        scale_data_val = data.get(scale_val, {}) if isinstance(data, dict) else {}
                        
                        if isinstance(scale_data_pct, dict) and row in scale_data_pct:
                            row_percentages[scale_val] = scale_data_pct[row]
                        if isinstance(scale_data_val, dict) and row in scale_data_val:
                            val = scale_data_val[row]
                            row_values[scale_val] = val
                            row_total += val
                    
                    # the mean of a row comes from the scale metrics (utils/scale_metrics,
                    # direction from scale_order), attached below as row_data["scale_metrics"]
                    row_data = {
                        "n": row_total or formatted["n"],
                        "values": row_values,
                        "distribution": row_values,  # alias for the dashboard
                        "percentages": row_percentages
                    }
                    formatted["by_row"][row] = row_data
                    formatted["items"][row] = row_data
            else:
                # simple format: data/percentages are flat
                formatted["values"] = data
                formatted["percentages"] = percentages
        else:
            formatted["values"] = data
            formatted["percentages"] = percentages
    elif handler_type == 'date':
        # counts per period (keys 2026-03-15 / 2026-03 / 2026, in time order); range and median
        formatted["values"] = dict(data) if isinstance(data, dict) else {}
        formatted["percentages"] = dict(result.get('percentages') or {}) if isinstance(result, dict) else \
            dict(getattr(result, 'percentages', None) or {})
        formatted["date"] = {k: metadata.get(k) for k in ("granularity", "first", "last", "median", "n_invalid")}
    elif handler_type == 'numeric_matrix':
        # Statistics per field by key f<i> (position in the configured columns);
        # labels per language are on the question (export/dashboard.py)
        formatted["fields"] = metadata.get('fields') or {}
        cs = metadata.get('constant_sum')
        if cs:
            formatted["constant_sum"] = {k: cs.get(k) for k in ("total", "n_respondents", "n_matching",
                                                                 "share_matching")}
            formatted["constant_sum"]["mean_share"] = cs.get("mean_share_by_key") or {}
    elif handler_type == 'numeric':
        # numeric: histogram + statistics (mean, median, std, quartiles, outliers).
        #
        # Source: NumericHandler.analyze() returns
        #   result.data       = NumericResult.to_summary_dict() (NOT the histogram!)
        #   result.metadata['numeric_stats']
        #                     = NumericResult dataclass instance with:
        #                       .histogram_bins   (List[float] -- bin edges, n+1 values)
        #                       .histogram_counts (List[int]   -- bin counts, n values)
        #                       .mean, .median, .std, .q25, .q75
        #                       .min_value, .max_value
        #                       .outliers, .decimals, .unit
        #
        # `values` / `percentages` are built from it as a bin label -> count/% dict,
        # so the dashboard can render the histogram. `data` is ignored on purpose
        # (it is to_summary_dict, no histogram). An already serialised dict (e.g. from
        # results cached in the database) is accepted too.
        
        numeric_stats = metadata.get('numeric_stats')
        
        # NumericResult getter, robust for a dataclass and a dict
        def _ns_get(field, default=None):
            if numeric_stats is None:
                return default
            if isinstance(numeric_stats, dict):
                return numeric_stats.get(field, default)
            return getattr(numeric_stats, field, default)
        
        if numeric_stats is not None:
            # field name mapping (NumericResult vs JSON):
            #   NumericResult.min_value -> JSON.min
            #   NumericResult.max_value -> JSON.max
            min_val = _ns_get('min')
            if min_val is None:
                min_val = _ns_get('min_value')
            max_val = _ns_get('max')
            if max_val is None:
                max_val = _ns_get('max_value')
            
            formatted["statistics"] = {
                "n":         _ns_get('n'),
                "n_invalid": _ns_get('n_invalid', 0),
                "mean":      _ns_get('mean'),
                "median":    _ns_get('median'),
                "std":       _ns_get('std'),
                "min":       min_val,
                "max":       max_val,
                "q25":       _ns_get('q25'),
                "q75":       _ns_get('q75'),
                "outliers":  list(_ns_get('outliers') or []),
            }
            
            ns_decimals = _ns_get('decimals')
            ns_unit = _ns_get('unit')
        else:
            ns_decimals = None
            ns_unit = None
        
        # unit + decimals: first metadata, then numeric_stats, then the default
        formatted["unit"] = metadata.get('unit') or ns_unit or ''
        formatted["decimals"] = (
            metadata.get('decimals')
            if metadata.get('decimals') is not None
            else (ns_decimals if ns_decimals is not None else 0)
        )
        
        # build the histogram, either from metadata.histogram_bins (if it is a list
        # of dicts already) or from numeric_result.bins+counts.
        histogram_bins_list = []
        values_dict: Dict[str, int] = {}
        percentages_dict: Dict[str, float] = {}
        
        # path A: metadata.histogram_bins is a list of {label, min, max, count}
        # dicts already (e.g. from an earlier export).
        bins_in_metadata = metadata.get('histogram_bins')
        if isinstance(bins_in_metadata, list) and bins_in_metadata and \
           all(isinstance(b, dict) for b in bins_in_metadata):
            histogram_bins_list = bins_in_metadata
        
        # path B: built from NumericResult.histogram_bins (edges, n+1) +
        # histogram_counts (n).
        elif numeric_stats is not None:
            ns_bins_edges = list(_ns_get('histogram_bins') or [])
            ns_counts = list(_ns_get('histogram_counts') or [])
            
            if (ns_bins_edges and ns_counts
                    and len(ns_bins_edges) == len(ns_counts) + 1):
                # decimals of the bin label formatting
                label_decimals = formatted["decimals"]
                
                for i, count in enumerate(ns_counts):
                    edge_min = ns_bins_edges[i]
                    edge_max = ns_bins_edges[i + 1]
                    # label in the format "min–max" (en dash)
                    if label_decimals == 0:
                        label = f"{edge_min:.0f}–{edge_max:.0f}"
                    else:
                        fmt_str = f"{{:.{label_decimals}f}}–{{:.{label_decimals}f}}"
                        label = fmt_str.format(edge_min, edge_max)
                    
                    histogram_bins_list.append({
                        "label": label,
                        "min": float(edge_min),
                        "max": float(edge_max),
                        "count": int(count),
                    })
        
        # build the values/percentages dict from histogram_bins_list;
        # values and percentages have the same keys (bin labels), the lean format of
        # the default bar renderer.
        n_total = sum(b.get('count', 0) for b in histogram_bins_list)
        for b in histogram_bins_list:
            label = b.get('label', '')
            count = int(b.get('count', 0))
            if not label:
                continue
            values_dict[label] = count
            if n_total > 0:
                percentages_dict[label] = round(100.0 * count / n_total, 2)
            else:
                percentages_dict[label] = 0.0
        
        formatted["values"] = values_dict
        formatted["percentages"] = percentages_dict
        if histogram_bins_list:
            formatted["histogram_bins"] = histogram_bins_list
        
    else:
        # single_choice, multi_choice, ranking: Standard-Format
        formatted["values"] = data if isinstance(data, dict) else {}
        formatted["percentages"] = percentages if isinstance(percentages, dict) else {}
    
    # scale metrics: single choice as a whole, matrix per row (travels with the
    # row through the key conversion of export/keys.py)
    if isinstance(metadata, dict) and metadata.get('nps') and handler_type == 'single_choice':
        formatted["nps"] = metadata['nps']
    metrics = metadata.get('scale_metrics') if isinstance(metadata, dict) else None
    if metrics:
        if handler_type == 'single_choice':
            formatted["scale_metrics"] = metrics
        elif handler_type in ('matrix_likert', 'matrix'):
            for key in ("by_row", "items"):
                for row, row_data in (formatted.get(key) or {}).items():
                    if isinstance(row_data, dict) and str(row) in metrics:
                        row_data["scale_metrics"] = metrics[str(row)]
    return formatted
