# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
JSON serialisation helpers for exported files.
"""

import logging
import json

import numpy as np
import pandas as pd



logger = logging.getLogger(__name__)


class NumpyJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that converts numpy types to native Python types.
    
    This prevents errors such as:
    - "Object of type bool_ is not JSON serializable"
    - "Object of type int64 is not JSON serializable"
    - NaN values become null (NaN is no valid JSON value)
    """
    def default(self, obj):
        import math
        
        # catch NaN and Infinity (MUST come before the isinstance checks!)
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        if isinstance(obj, np.floating):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            # replace NaN in arrays as well
            return [None if (isinstance(x, (float, np.floating)) and (np.isnan(x) if hasattr(np, 'isnan') else False)) else x for x in obj.tolist()]
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if pd.isna(obj):
            return None
        return super().default(obj)


def sanitize_for_json(obj):
    """
    Replaces NaN, Infinity and other invalid JSON values recursively with None.
    
    IMPORTANT: apply this function to all data BEFORE handing it to
    json.dump(), because the JSONEncoder does not catch every NaN case.
    
    Args:
        obj: any Python object (dict, list, float, etc.)
    
    Returns:
        the cleaned object with None instead of NaN/Infinity
    """
    import math
    
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(sanitize_for_json(item) for item in obj)
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())
    elif pd.isna(obj):
        return None
    else:
        return obj


def _slugify(text: str) -> str:
    """Converts text into a URL-friendly slug."""
    import re
    # replace umlauts
    replacements = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
        'Ä': 'ae', 'Ö': 'oe', 'Ü': 'ue'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # lower case, only alphanumerics and hyphens
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    
    return text or 'unknown'
