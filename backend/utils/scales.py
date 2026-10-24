# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Named scales and orders.

A scale or an order is defined once under ``settings.scales`` and can be used by
several questions and segmentation dimensions:

    settings:
      scales:
        satisfaction_4:
          context: "satisfaction, from the most to the least satisfied"
          direction: descending           # first point = highest; or ascending
          points:
            - {key: very_satisfied, label: {en: "Very satisfied"}, polarity: positive}
            - {key: rather_satisfied, label: {en: "Rather satisfied"}, polarity: positive}
            - {key: rather_dissatisfied, label: {en: "Rather dissatisfied"}, polarity: negative}
            - {key: very_dissatisfied, label: {en: "Very dissatisfied"}, polarity: negative}

    question_groups:
      satisfaction:
        handler: single_choice
        scale: satisfaction_4             # instead of options/ordinal/scale_order
      rating:
        handler: matrix_likert
        scale: satisfaction_4             # instead of an embedded scale list

    settings.segmentation.dimensions:
      - id: age_group
        order: age_groups                 # instead of a list of values

Embedded lists stay valid; a reference is an additional way. ``expand_named_scales``
turns the references into the embedded structures the handlers, exports and reports
already read, so nothing downstream needs to know about named scales. The file is
never rewritten: only the configuration in memory is expanded.

``context`` describes the meaning of the scale for the translation of its labels
(short labels are translated with this context and the position of each point).
``polarity`` (positive, neutral, negative) decides the colours in the dashboard.
"""

import copy
import re
from typing import Dict, List, Optional, Tuple

POLARITIES = ("positive", "neutral", "negative")
DIRECTIONS = ("ascending", "descending")
# handlers whose answer options are the points of the scale
OPTION_HANDLERS = ("single_choice",)
# handlers with an embedded ``scale`` (the columns of a matrix)
SCALE_HANDLERS = ("matrix_likert", "cooperation_matrix")


def _source_language(config: dict) -> str:
    from utils.languages import get_source_language
    return get_source_language(config)


def _point_key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(label).lower()).strip("_")
    return key or "point"


def scale_definitions(config: Optional[dict]) -> Dict[str, dict]:
    """The named scales of a configuration (settings.scales), {} if none."""
    scales = ((config or {}).get("settings") or {}).get("scales") or {}
    return scales if isinstance(scales, dict) else {}


def normalized_points(scale: dict, source_lang: str) -> List[dict]:
    """
    The points of a scale as [{key, label: {lang: text}, polarity}] in their order.

    A label given as a plain string is the label in the source language.
    """
    points = []
    for p in (scale or {}).get("points") or []:
        if isinstance(p, str):
            p = {"label": p}
        if not isinstance(p, dict):
            continue
        label = p.get("label")
        if isinstance(label, str):
            label = {source_lang: label}
        label = {k: v for k, v in (label or {}).items() if isinstance(v, str) and v.strip()}
        source = label.get(source_lang) or next(iter(label.values()), "")
        points.append({
            "key": str(p.get("key") or _point_key(source)),
            "label": label,
            "polarity": p.get("polarity"),
        })
    return points


def _labels_by_language(points: List[dict], source_lang: str) -> Dict[str, List[str]]:
    """{lang: [label of each point]} for every language that labels all points."""
    langs = {lang for p in points for lang in p["label"]}
    out = {}
    for lang in sorted(langs):
        if all(lang in p["label"] for p in points):
            out[lang] = [p["label"][lang] for p in points]
    if source_lang not in out:
        out[source_lang] = [p["label"].get(source_lang) or next(iter(p["label"].values()), p["key"]) for p in points]
    return out


def validate_scales(config: Optional[dict]) -> Tuple[List[str], List[str]]:
    """(errors, warnings) of the scale definitions and the references to them."""
    errors, warnings = [], []
    scales = scale_definitions(config)
    src = _source_language(config or {})
    for name, scale in scales.items():
        if not isinstance(scale, dict):
            errors.append(f"settings.scales.{name}: a scale is a mapping with points")
            continue
        points = normalized_points(scale, src)
        if not points:
            errors.append(f"settings.scales.{name}: no points")
        direction = scale.get("direction")
        if direction is not None and direction not in DIRECTIONS:
            errors.append(f"settings.scales.{name}.direction: '{direction}' is not one of {', '.join(DIRECTIONS)}")
        keys = [p["key"] for p in points]
        if len(set(keys)) != len(keys):
            errors.append(f"settings.scales.{name}: the keys of the points are not unique")
        for p in points:
            if p["polarity"] is not None and p["polarity"] not in POLARITIES:
                errors.append(f"settings.scales.{name}: polarity '{p['polarity']}' of '{p['key']}' "
                              f"is not one of {', '.join(POLARITIES)}")
            if src not in p["label"]:
                errors.append(f"settings.scales.{name}: point '{p['key']}' has no label in the source language '{src}'")
        if not scale.get("context"):
            warnings.append(f"settings.scales.{name}: no context; the labels are translated without knowing "
                            f"what the scale measures")
    for qid, q in ((config or {}).get("question_groups") or {}).items():
        ref = (q or {}).get("scale")
        if isinstance(ref, str):
            if ref not in scales:
                errors.append(f"{qid}: scale '{ref}' is not defined under settings.scales")
            elif q.get("handler") not in OPTION_HANDLERS + SCALE_HANDLERS:
                errors.append(f"{qid}: a named scale is not supported for the handler '{q.get('handler')}'")
            elif q.get("handler") in OPTION_HANDLERS and q.get("options"):
                errors.append(f"{qid}: both scale '{ref}' and options are given; use one of them")
    dims = (((config or {}).get("settings") or {}).get("segmentation") or {}).get("dimensions") or []
    for d in dims if isinstance(dims, list) else []:
        ref = (d or {}).get("order")
        if isinstance(ref, str) and ref not in scales:
            errors.append(f"segmentation dimension '{d.get('id')}': order '{ref}' is not defined under settings.scales")
    return errors, warnings


def expand_named_scales(config: Optional[dict]) -> Optional[dict]:
    """
    A copy of the configuration with every reference to a named scale replaced by the
    embedded structures (options/scale, ordinal, scale_order, option_polarity; the order
    of a dimension). Unknown references are left as they are (validate_scales reports them).
    """
    scales = scale_definitions(config)
    if not config or not scales:
        return config
    config = copy.deepcopy(config)
    src = _source_language(config)
    for qid, q in (config.get("question_groups") or {}).items():
        ref = (q or {}).get("scale")
        if not isinstance(ref, str) or ref not in scales:
            continue
        scale = scales[ref]
        points = normalized_points(scale, src)
        labels = _labels_by_language(points, src)
        handler = q.get("handler")
        if handler in OPTION_HANDLERS:
            del q["scale"]
            q["options"] = labels
            q.setdefault("ordinal", True)
        elif handler in SCALE_HANDLERS:
            q["scale"] = labels
        else:
            continue
        if scale.get("direction"):
            q.setdefault("scale_order", scale["direction"])
        polarity = {p["label"][src]: p["polarity"] for p in points if p["polarity"] and src in p["label"]}
        if polarity:
            q["option_polarity"] = polarity
        q["scale_name"] = ref
    dims = ((config.get("settings") or {}).get("segmentation") or {}).get("dimensions") or []
    for d in dims if isinstance(dims, list) else []:
        ref = (d or {}).get("order")
        if isinstance(ref, str) and ref in scales:
            d["order"] = [p["label"].get(src, p["key"]) for p in normalized_points(scales[ref], src)]
            d["order_scale"] = ref
    return config
