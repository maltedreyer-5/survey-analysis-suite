# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Conversion of answer labels into stable, language-independent keys.

The labels in the data are those of the survey's source language; keys are
derived from them, and the per-language labels of the configuration are
attached for every output language.
"""

import logging
from typing import List




logger = logging.getLogger(__name__)


# =============================================================================
# HELPER FUNCTIONS FOR STABLE KEY CONVERSION
# =============================================================================

def _get_translator():
    """Cached TranslationManager instance."""
    if not hasattr(_get_translator, '_instance'):
        from utils.translation import TranslationManager
        _get_translator._instance = TranslationManager()
    return _get_translator._instance


def _create_option_key(source_label: str) -> str:
    """
    Builds a stable key from an answer label in the source language.
    
    Args:
        source_label: e.g. "Data protection concerns..." 
        
    Returns:
        key, e.g. "data_protection_concerns"
    """
    import re
    
    # shorten long labels
    label = str(source_label)  # answer codes may arrive as numbers
    label = label[:50] if len(label) > 50 else label
    
    # remove special characters, convert to lower case
    slug = re.sub(r'[^a-zäöüß0-9]+', '_', label.lower())
    
    # replace umlauts
    slug = slug.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    
    # Bereinige
    slug = re.sub(r'_+', '_', slug).strip('_')
    
    return slug or 'option'


def _limit_keywords(keywords: dict, max_per_lang: int = 10) -> dict:
    """
    Limits the number of keywords per language.
    
    Args:
        keywords: dict with language keys and lists of keywords
        max_per_lang: maximum number per language
        
    Returns:
        dict with limited lists
    """
    if not isinstance(keywords, dict):
        return {}
    
    return {
        lang: (kws[:max_per_lang] if isinstance(kws, list) else [])
        for lang, kws in keywords.items()
    }


def _source_language() -> str:
    from utils.prompts import source_language
    return source_language()


def labels_per_language(lists, languages: List[str], src: str = None) -> List[dict]:
    """
    One label dict per position of the source-language list.

    ``lists`` is a per-language configuration (``{lang: [...]}``) or a plain
    list in the source language. For every output language the configured
    label at the same position is used; where the list is missing or shorter,
    a known common label is translated (utils/translation.translate_common_label),
    otherwise the source label stays.
    """
    src = src or _source_language()
    translator = _get_translator()
    if isinstance(lists, dict):
        src_list = list(lists.get(src) or [])
    else:
        src_list, lists = list(lists or []), {}
    out = []
    for i, src_label in enumerate(src_list):
        label = {src: src_label}
        for lang in languages:
            if lang == src:
                continue
            target = lists.get(lang) or []
            label[lang] = target[i] if i < len(target) else translator.translate_common_label(src_label, lang)
        out.append(label)
    return out


def _build_options_with_keys(qconfig: dict, languages: List[str] = None) -> List[dict]:
    """
    Builds the list of options with stable keys and multilingual labels.
    
    Args:
        qconfig: question configuration
        languages: output languages (default: those of the loaded survey)
        
    Returns:
        list of {key, label: {lang: text}} (with polarity if the options come from
        a named scale that defines it); the key comes from the label in the
        source language.
    """
    if languages is None:
        from utils.languages import get_output_languages
        from utils.prompts import current_config
        languages = get_output_languages(current_config())
    
    # multi_choice_binary: item_labels instead of options
    options_config = qconfig.get('options', {}) or qconfig.get('item_labels', {})
    
    # format 1: structured with keys already
    if isinstance(options_config, list) and options_config and isinstance(options_config[0], dict) and 'key' in options_config[0]:
        return options_config
    
    # Format 2: {lang: [...]}; Format 3: plain list in the source language
    if isinstance(options_config, (dict, list)):
        src = _source_language()
        polarity = qconfig.get('option_polarity') or {}   # from a named scale
        out = []
        for label in labels_per_language(options_config, languages, src):
            entry = {"key": _create_option_key(label[src]), "label": label}
            if polarity.get(label[src]):
                entry["polarity"] = polarity[label[src]]
            out.append(entry)
        return out
    
    return []


def _create_source_to_key_map(options_with_keys: List[dict]) -> dict:
    """
    Mapping from the labels in the source language (as in the data) to keys.
    
    Args:
        options_with_keys: list of {key, label: {de, fr, en}}
        
    Returns:
        dict: {"label in the source language": "key", ...}
    """
    src = _source_language()
    mapping = {}
    for opt in options_with_keys:
        src_label = opt.get('label', {}).get(src, '')
        key = opt.get('key', '')
        if src_label and key:
            mapping[src_label] = key
    return mapping


def _convert_option_values_to_keys(values: dict, source_to_key: dict) -> dict:
    """
    Converts answer labels in the source language in results into stable keys.
    
    Args:
        values: dict with labels as keys (e.g. {"Ja": 50, "Nein": 30})
        source_to_key: mapping from labels to keys
        
    Returns:
        dict with stable keys (e.g. {"yes": 50, "no": 30})
    """
    if not values or not source_to_key:
        return values
    
    converted = {}
    for source_label, value in values.items():
        key = source_to_key.get(source_label)
        if key:
            converted[key] = value
        else:
            # fallback: build a key
            converted[_create_option_key(source_label)] = value
    
    return converted


def _convert_all_result_keys(results: dict, source_to_key: dict) -> None:
    """
    Converts all labels in the source language in results into stable keys.
    Modifies the dict in place.
    
    Args:
        results: the results dict
        source_to_key: mapping from labels to keys
    """
    if not results or not source_to_key:
        return
    
    for result_key, result_data in results.items():
        if not isinstance(result_data, dict):
            continue
        
        # convert values and percentages
        if "values" in result_data and isinstance(result_data["values"], dict):
            result_data["values"] = _convert_option_values_to_keys(result_data["values"], source_to_key)
        
        if "percentages" in result_data and isinstance(result_data["percentages"], dict):
            result_data["percentages"] = _convert_option_values_to_keys(result_data["percentages"], source_to_key)
        
        # for nested structures (e.g. segment results)
        if result_key.startswith("by_"):
            for seg_value, seg_data in result_data.items():
                if isinstance(seg_data, dict):
                    if "values" in seg_data:
                        seg_data["values"] = _convert_option_values_to_keys(seg_data["values"], source_to_key)
                    if "percentages" in seg_data:
                        seg_data["percentages"] = _convert_option_values_to_keys(seg_data["percentages"], source_to_key)


def _convert_matrix_result_keys(results: dict, row_source_to_key: dict, scale_source_to_key: dict) -> None:
    """
    Converts labels in matrix results into stable keys.
    
    Args:
        results: the results dict
        row_source_to_key: mapping from row labels to keys
        scale_source_to_key: mapping from scale labels to keys
    """
    if not results:
        return
    
    for result_key, result_data in results.items():
        if not isinstance(result_data, dict):
            continue
        
        # convert by_row / items
        for items_key in ["by_row", "items"]:
            if items_key in result_data and isinstance(result_data[items_key], dict):
                old_items = result_data[items_key]
                new_items = {}
                
                for source_row, row_data in old_items.items():
                    row_key = row_source_to_key.get(source_row, _create_option_key(source_row))
                    
                    if isinstance(row_data, dict):
                        # Konvertiere scale-Keys in values/percentages/distribution
                        new_row_data = row_data.copy()
                        
                        for data_key in ["values", "percentages", "distribution"]:
                            if data_key in new_row_data and isinstance(new_row_data[data_key], dict):
                                new_row_data[data_key] = {
                                    scale_source_to_key.get(k, _create_option_key(k)): v
                                    for k, v in new_row_data[data_key].items()
                                }
                        
                        new_items[row_key] = new_row_data
                    else:
                        new_items[row_key] = row_data
                
                result_data[items_key] = new_items
        
        # for segment results (by_role etc.)
        if result_key.startswith("by_"):
            for seg_value, seg_data in result_data.items():
                if isinstance(seg_data, dict):
                    for items_key in ["by_row", "items"]:
                        if items_key in seg_data and isinstance(seg_data[items_key], dict):
                            old_items = seg_data[items_key]
                            new_items = {}
                            
                            for source_row, row_data in old_items.items():
                                row_key = row_source_to_key.get(source_row, _create_option_key(source_row))
                                
                                if isinstance(row_data, dict):
                                    new_row_data = row_data.copy()
                                    for data_key in ["values", "percentages", "distribution"]:
                                        if data_key in new_row_data and isinstance(new_row_data[data_key], dict):
                                            new_row_data[data_key] = {
                                                scale_source_to_key.get(k, _create_option_key(k)): v
                                                for k, v in new_row_data[data_key].items()
                                            }
                                    new_items[row_key] = new_row_data
                                else:
                                    new_items[row_key] = row_data
                            
                            seg_data[items_key] = new_items
