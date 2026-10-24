# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Net Promoter Score for a single choice on the 0-10 scale (``nps: true`` in
the question; Qualtrics NPS questions get it on import).

Promoters answer 9 or 10, passives 7 or 8, detractors 0 to 6; the NPS is the
share of promoters minus the share of detractors (-100 to +100). Answers that
are no number from 0 to 10 ("don't know") are left out and counted.
"""
from typing import Dict, Optional


def _score(label) -> Optional[int]:
    try:
        value = float(str(label).strip().replace(",", "."))
    except ValueError:
        return None
    return int(value) if value.is_integer() and 0 <= value <= 10 else None


def compute(counts: Dict[str, int]) -> Optional[dict]:
    """``{answer: count}`` -> NPS figures; None without answers on the scale."""
    groups = {"promoters": 0, "passives": 0, "detractors": 0}
    excluded = 0
    for label, count in counts.items():
        score = _score(label)
        if score is None:
            excluded += int(count)
        elif score >= 9:
            groups["promoters"] += int(count)
        elif score >= 7:
            groups["passives"] += int(count)
        else:
            groups["detractors"] += int(count)
    n = sum(groups.values())
    if n == 0:
        return None
    shares = {k: round(v / n * 100, 1) for k, v in groups.items()}
    return {"n": n, "n_excluded": excluded, **{f"n_{k}": v for k, v in groups.items()}, **shares,
            # from the counts, not from the rounded shares (66.7 - 33.3 would give 33.4)
            "nps": round((groups["promoters"] - groups["detractors"]) / n * 100, 1)}
