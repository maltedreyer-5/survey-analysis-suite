# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Colours of the answers of an ordered scale in the charts of the reports.

One rule for single choices and rating matrices, with the same recognition of evasive
answers and the same direction as the scale metrics (utils/scale_metrics.py):

- NPS (0-10): detractors (0-6) red, passives (7-8) grey, promoters (9-10) green;
- a polarity from a named scale: positive points green, negative red, neutral grey,
  stronger towards the ends of the scale;
- an ordered scale without polarity: by position, from the positive end (green) over
  grey to the negative end (red), in the direction of the scale;
- evasive answers ("don't know", ``scale_exclude``): light grey.

Returns ``{answer: colour}`` in the labels of the source language, or ``{}`` for a
question that is no ordered scale (the charts then keep their palette).
"""
from typing import Dict, List, Sequence

from matplotlib.colors import to_hex, to_rgb

POSITIVE, NEUTRAL, NEGATIVE, EXCLUDED = "#5cb85c", "#999999", "#d9534f", "#d9d9d9"


def _mix(colour: str, weight: float) -> str:
    """The colour mixed with white: weight 1 = the colour, 0 = white."""
    r, g, b = to_rgb(colour)
    return to_hex(tuple(1 - weight * (1 - c) for c in (r, g, b)))


def _shades(colour: str, n: int, strong_first: bool) -> List[str]:
    """n shades of a colour, from strong to light (or the other way)."""
    weights = [1.0 - 0.45 * i / max(1, n - 1) for i in range(n)] if n > 1 else [1.0]
    out = [_mix(colour, w) for w in weights]
    return out if strong_first else out[::-1]


def scale_colours(options: Sequence[str], qconfig: dict) -> Dict[str, str]:
    from utils.scale_metrics import direction, scale_points
    options = [str(o) for o in options]
    if qconfig.get("nps"):
        out = {}
        for o in options:
            try:
                v = float(o.replace(",", "."))
            except ValueError:
                out[o] = EXCLUDED
                continue
            out[o] = NEGATIVE if v <= 6 else (NEUTRAL if v <= 8 else POSITIVE)
        return out
    polarity = qconfig.get("option_polarity") or {}
    ordered = bool(qconfig.get("ordinal") or qconfig.get("scale_order") or polarity
                   or qconfig.get("handler") in ("matrix_likert", "cooperation_matrix"))
    if not ordered:
        return {}
    points = scale_points(options, qconfig)
    out = {o: EXCLUDED for o in options if o not in points}
    if polarity and any(p in polarity for p in points):
        positives = [p for p in points if polarity.get(p) == "positive"]
        negatives = [p for p in points if polarity.get(p) == "negative"]
        # the strongest shade at the end of the scale, lighter towards the middle
        pos_first = not positives or not negatives or points.index(positives[0]) < points.index(negatives[0])
        out.update(zip(positives, _shades(POSITIVE, len(positives), strong_first=pos_first)))
        out.update(zip(negatives, _shades(NEGATIVE, len(negatives), strong_first=not pos_first)))
        out.update({p: NEUTRAL for p in points if p not in out})
        return out
    d = direction(points, qconfig)
    if d is None or len(points) < 2:
        return out
    high_first = d == "descending"
    n = len(points)
    for i, p in enumerate(points):
        x = i / (n - 1)                                  # 0 = first point
        x = x if high_first else 1 - x                   # 0 = the positive end
        if x < 0.5:
            out[p] = _mix(POSITIVE, 1 - 0.9 * x)       # green, lighter towards the middle
        elif x > 0.5:
            out[p] = _mix(NEGATIVE, 1 - 0.9 * (1 - x))
        else:
            out[p] = NEUTRAL
    return out
