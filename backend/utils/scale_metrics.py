# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Scale metrics for ordered answers: single choice with ``ordinal: true`` and
the rows of a Likert matrix.

From the counts per answer and the order of the scale: n on the scale,
mean and median of the scale values, top box and bottom box shares (the
highest / lowest ``box`` points, default 2). Answers outside the scale
("don't know", "no answer", "not applicable") are excluded and counted
separately: by default every answer that is one of these common labels in
any catalog language (locales/*.yaml, common_label), plus the labels in
``scale_exclude``.

Configuration (question)::

    scale_values: [1, 2, 3, 4, 5]     # value per option in option order (default see below)
    scale_order: ascending            # or descending: first option = highest point;
                                      # needed when the labels do not tell (see direction)
    scale_exclude: ["Weiß nicht"]     # further answers outside the scale (source language)
    scale_box: 2                      # size of top/bottom box
    scale_metrics: false              # switch off (e.g. a nominal matrix)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# common_label concepts that are no point on a scale
NON_SCALE_CONCEPTS = ("dont_know", "no_answer", "not_applicable")
# concepts at the high end of a scale: an option list starting with one of them
# (and not ending with one) runs from high to low
HIGH_CONCEPTS = ("strongly_agree", "agree", "very_important", "important", "always", "often", "increased")


def _concept_texts(concepts) -> set:
    from utils.languages import available_catalogs, catalog_value
    out = set()
    for code in available_catalogs():
        for concept in concepts:
            try:
                out.add(str(catalog_value(f"common_label.{concept}", code)).strip().lower())
            except KeyError:
                pass
    return out


def direction(points: Sequence[str], config: dict) -> Optional[str]:
    """
    "ascending" (first point lowest) or "descending", or None if it cannot be
    told: from ``scale_order``; else numbers as labels (1..5); else the common
    labels (a list starting with "strongly agree", "very important" … runs
    downwards, one ending with them upwards). Without a direction no metrics
    are computed: a wrong direction would swap top and bottom box.
    """
    order = str(config.get("scale_order", "")).lower()
    if order in ("ascending", "descending"):
        return order
    try:
        numbers = [float(str(p).replace(",", ".")) for p in points]
        return "ascending" if numbers[0] < numbers[-1] else "descending"
    except ValueError:
        pass
    high = _concept_texts(HIGH_CONCEPTS)
    first, last = str(points[0]).strip().lower(), str(points[-1]).strip().lower()
    if first in high and last not in high:
        return "descending"
    if last in high and first not in high:
        return "ascending"
    return None


def _numeric(points: Sequence[str]) -> bool:
    try:
        [float(str(p).replace(",", ".")) for p in points]
        return True
    except ValueError:
        return False


def non_scale_labels() -> set:
    """Texts of the non-scale concepts in every catalog language."""
    return _concept_texts(NON_SCALE_CONCEPTS)


def scale_points(order: Sequence[str], config: dict) -> List[str]:
    """The options of ``order`` that are points on the scale (non-scale answers removed)."""
    excluded = non_scale_labels() | {str(x).strip().lower() for x in (config.get("scale_exclude") or [])}
    return [o for o in order if str(o).strip().lower() not in excluded]


def compute(counts: Dict[str, int], order: Sequence[str], config: Optional[dict] = None) -> Optional[dict]:
    """
    Metrics for answer counts ``{label: count}`` on the ordered options
    ``order`` (direction: ``scale_values``, ``scale_order`` or the common labels). None if
    fewer than two scale points or no answer on the scale.
    """
    config = config or {}
    if config.get("scale_metrics") is False:
        return None
    points = scale_points(order, config)
    if len(points) < 2:
        return None
    values = config.get("scale_values")
    if isinstance(values, (list, tuple)) and len(values) == len(order):
        value_of = {o: float(v) for o, v in zip(order, values)}
    elif _numeric(points):
        # numbers as answers (1-5, 0-10): their own value
        value_of = {o: float(str(o).replace(",", ".")) for o in points}
    else:
        way = direction(points, config)
        if way is None:
            return None
        k = len(points)
        value_of = {o: float(k - i if way == "descending" else i + 1) for i, o in enumerate(points)}
    n = sum(int(counts.get(p, 0)) for p in points)
    n_excluded = sum(int(v) for k, v in counts.items() if k not in points)
    if n == 0:
        return None
    # expanded values for mean and median
    ordered = sorted(points, key=lambda p: value_of.get(p, 0))
    mean = sum(value_of[p] * int(counts.get(p, 0)) for p in ordered) / n
    cumulative, median = 0, None
    for p in ordered:
        cumulative += int(counts.get(p, 0))
        if median is None and cumulative >= (n + 1) / 2:
            median = value_of[p]
    box = int(config.get("scale_box", 2))
    box = max(1, min(box, len(ordered) // 2))
    top = sum(int(counts.get(p, 0)) for p in ordered[-box:])
    bottom = sum(int(counts.get(p, 0)) for p in ordered[:box])
    return {"n": n, "n_excluded": n_excluded, "mean": round(mean, 2), "median": median,
            "top_box": round(top / n * 100, 1), "bottom_box": round(bottom / n * 100, 1), "box": box,
            "min_value": value_of[ordered[0]], "max_value": value_of[ordered[-1]],
            "top_labels": list(ordered[-box:]), "bottom_labels": list(ordered[:box])}
