# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Association between two analysis variables, with the facts a text may state.

Test by measurement level of the two variables (see analysis_variables.py):

=========================  ===========================  ==========================
levels                     test                         effect size
=========================  ===========================  ==========================
ordinal/numeric, both      Kendall's tau-b              tau-b (signed)
numeric × nominal/binary   Kruskal-Wallis               epsilon squared
all other combinations     chi-square (no Yates)        Cramér's V, bias-corrected
                           Fisher's exact test for a    (Bergsma 2013)
                           2×2 table with small
                           expected counts
=========================  ===========================  ==========================

Strength labels follow Cohen: for V the thresholds .1/.3/.5 are divided by
sqrt(min(r, c) - 1); tau-b is converted to Pearson's r (r = sin(pi*tau/2))
before applying .1/.3/.5; epsilon squared uses .01/.06/.14.

A chi-square test whose expected counts are too small (more than 20 % of the
cells below 5, or any below 1) is not used; for a 2×2 table Fisher's exact
test replaces it, otherwise the pair counts as "not testable" and is
reported in the method record, not dropped silently.

P values of a family of tests are adjusted with Benjamini-Hochberg (false
discovery rate); a result counts as significant if its adjusted value (q) is
below alpha.

The pattern of an association is what a reader can check in the data: for
category tables the cells that are clearly over-represented (adjusted
standardized residual > 1.96) with the share within the group against the
overall share; for tau-b the direction; for Kruskal-Wallis the medians of the
groups.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from utils.analysis_variables import AnalysisVariable

ALPHA = 0.05
MIN_N = 20                 # minimum respondents in both variables
MIN_GROUP = 5              # minimum size of a category / group
RESIDUAL_THRESHOLD = 1.96  # adjusted standardized residual for a pattern cell
MAX_PATTERN_CELLS = 3

STRENGTHS = ("negligible", "weak", "medium", "strong")


@dataclass
class AssociationResult:
    var_a: AnalysisVariable
    var_b: AnalysisVariable
    test: str                      # chi2 | fisher | kendall_tau_b | kruskal_wallis | not_testable
    effect_measure: Optional[str]  # cramers_v | kendall_tau_b | epsilon_squared
    effect: float
    p_value: float
    n: int
    strength: str
    statistic: float = 0.0
    df: int = 0
    q_value: Optional[float] = None
    pattern: List[dict] = field(default_factory=list)
    reason: str = ""               # why not testable
    dropped_categories: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def testable(self) -> bool:
        return self.test != "not_testable"

    def is_significant(self, alpha: float = ALPHA) -> bool:
        p = self.q_value if self.q_value is not None else self.p_value
        return self.testable and p is not None and not math.isnan(p) and p < alpha

    def key(self) -> str:
        return f"{self.var_a.id}|{self.var_b.id}"


def strength_label(measure: str, effect: float, table_shape=None) -> str:
    value = abs(effect)
    if measure == "cramers_v":
        k = max(1, min(table_shape) - 1) if table_shape else 1
        limits = [x / math.sqrt(k) for x in (0.1, 0.3, 0.5)]
    elif measure == "kendall_tau_b":
        value = abs(math.sin(math.pi * effect / 2))   # tau-b -> r (Greiner)
        limits = [0.1, 0.3, 0.5]
    else:  # epsilon_squared
        limits = [0.01, 0.06, 0.14]
    for label, limit in zip(STRENGTHS, limits):
        if value < limit:
            return label
    return "strong"


def bias_corrected_v(chi2: float, n: int, r: int, c: int) -> float:
    """Cramér's V with the bias correction of Bergsma (2013)."""
    if n <= 1:
        return 0.0
    phi2 = chi2 / n
    phi2c = max(0.0, phi2 - (r - 1) * (c - 1) / (n - 1))
    rc = r - (r - 1) ** 2 / (n - 1)
    cc = c - (c - 1) ** 2 / (n - 1)
    k = min(rc - 1, cc - 1)
    return math.sqrt(phi2c / k) if k > 0 else 0.0


def benjamini_hochberg(p_values: Sequence[float]) -> List[float]:
    """Adjusted p values (q); NaN stays NaN and does not count towards the family."""
    p = np.asarray(p_values, dtype=float)
    q = np.full_like(p, np.nan)
    valid = ~np.isnan(p)
    m = int(valid.sum())
    if m == 0:
        return q.tolist()
    order = np.argsort(p[valid])
    ranked = p[valid][order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adjusted = np.empty(m)
    adjusted[order] = np.minimum(ranked, 1.0)
    q[valid] = adjusted
    return q.tolist()


def adjust(results: List[AssociationResult]) -> None:
    """Set q_value on a family of results (in place)."""
    q = benjamini_hochberg([r.p_value if r.testable else np.nan for r in results])
    for result, value in zip(results, q):
        result.q_value = None if np.isnan(value) else float(value)


def _not_testable(a, b, n, reason) -> AssociationResult:
    return AssociationResult(a, b, "not_testable", None, 0.0, float("nan"), n, "negligible", reason=reason)


def assess_pair(a: AnalysisVariable, b: AnalysisVariable) -> AssociationResult:
    """Association of two variables on the respondents both are defined for."""
    joint = pd.concat([a.values.rename("a"), b.values.rename("b")], axis=1).dropna()
    n = len(joint)
    if n < MIN_N:
        return _not_testable(a, b, n, "too_few_cases")
    ordinal = ("ordinal", "numeric")
    if a.level in ordinal and b.level in ordinal:
        return _kendall(a, b, joint)
    if a.level == "numeric" or b.level == "numeric":
        num, cat = (a, b) if a.level == "numeric" else (b, a)
        return _kruskal(a, b, joint, num_col="a" if num is a else "b", cat_col="b" if num is a else "a")
    return _contingency(a, b, joint)


def _kendall(a, b, joint) -> AssociationResult:
    if joint["a"].nunique() < 2 or joint["b"].nunique() < 2:
        return _not_testable(a, b, len(joint), "no_variation")
    tau, p = stats.kendalltau(joint["a"], joint["b"], variant="b")
    if tau is None or np.isnan(tau):
        return _not_testable(a, b, len(joint), "no_variation")
    result = AssociationResult(a, b, "kendall_tau_b", "kendall_tau_b", float(tau), float(p), len(joint),
                               strength_label("kendall_tau_b", tau))
    result.pattern = [{"kind": "direction", "sign": 1 if tau > 0 else -1}]
    # Where few distinct values: the category cells as for a table, for a checkable statement
    if joint["a"].nunique() <= 7 and joint["b"].nunique() <= 7:
        table = pd.crosstab(joint["a"], joint["b"])
        result.pattern += _cell_pattern(table, a, b)
    return result


def _kruskal(a, b, joint, num_col, cat_col) -> AssociationResult:
    from utils.statistics import kruskal_wallis_test
    groups = {k: g[num_col].tolist() for k, g in joint.groupby(cat_col)}
    small = [k for k, g in groups.items() if len(g) < MIN_GROUP]
    groups = {k: g for k, g in groups.items() if len(g) >= MIN_GROUP}
    if len(groups) < 2:
        return _not_testable(a, b, len(joint), "too_few_groups")
    test = kruskal_wallis_test(groups, min_per_group=MIN_GROUP)
    if test.p_value is None or (isinstance(test.p_value, float) and np.isnan(test.p_value)):
        return _not_testable(a, b, len(joint), "no_variation")
    n = sum(len(g) for g in groups.values())
    result = AssociationResult(a, b, "kruskal_wallis", "epsilon_squared", float(test.effect_size or 0.0),
                               float(test.p_value), n, strength_label("epsilon_squared", test.effect_size or 0.0),
                               statistic=float(test.statistic), df=int(test.degrees_of_freedom or 0))
    cat_var = a if cat_col == "a" else b
    medians = sorted(((k, float(np.median(v)), len(v)) for k, v in groups.items()), key=lambda x: -x[1])
    overall = float(np.median([x for v in groups.values() for x in v]))
    result.pattern = [{"kind": "group_median", "variable": cat_var.id, "group": k, "median": m,
                       "overall_median": overall, "group_n": g_n}
                      for k, m, g_n in (medians[0], medians[-1])]
    if small:
        result.dropped_categories[cat_var.id] = [str(k) for k in small]
    return result


def _contingency(a, b, joint) -> AssociationResult:
    table = pd.crosstab(joint["a"], joint["b"])
    dropped = {}
    rows = table.sum(axis=1)
    cols = table.sum(axis=0)
    if (rows < MIN_GROUP).any():
        dropped[a.id] = [str(k) for k in rows[rows < MIN_GROUP].index]
    if (cols < MIN_GROUP).any():
        dropped[b.id] = [str(k) for k in cols[cols < MIN_GROUP].index]
    table = table.loc[rows >= MIN_GROUP, cols >= MIN_GROUP]
    if table.shape[0] < 2 or table.shape[1] < 2:
        res = _not_testable(a, b, int(table.values.sum()), "too_few_categories")
        res.dropped_categories = dropped
        return res
    n = int(table.values.sum())
    chi2, p, dof, expected = stats.chi2_contingency(table.values, correction=False)
    low = (expected < 5).mean()
    test_name = "chi2"
    if low > 0.2 or (expected < 1).any():
        if table.shape == (2, 2):
            _, p = stats.fisher_exact(table.values)
            test_name = "fisher"
        else:
            res = _not_testable(a, b, n, "expected_counts_too_small")
            res.dropped_categories = dropped
            return res
    v = bias_corrected_v(chi2, n, *table.shape)
    result = AssociationResult(a, b, test_name, "cramers_v", v, float(p), n,
                               strength_label("cramers_v", v, table.shape),
                               statistic=float(chi2), df=int(dof), dropped_categories=dropped)
    result.pattern = _cell_pattern(table, a, b)
    return result


def _cell_pattern(table: pd.DataFrame, a: AnalysisVariable, b: AnalysisVariable) -> List[dict]:
    """
    Clearly over-represented cells: share of the column category within the
    row group against its overall share. For a yes/no variable only cells with
    "yes" are reported; the "no" cells mirror them.
    """
    obs = table.values.astype(float)
    n = obs.sum()
    row = obs.sum(axis=1, keepdims=True)
    col = obs.sum(axis=0, keepdims=True)
    exp = row @ col / n
    with np.errstate(divide="ignore", invalid="ignore"):
        resid = (obs - exp) / np.sqrt(exp * (1 - row / n) * (1 - col / n))
    cells = []
    for i in range(obs.shape[0]):
        for j in range(obs.shape[1]):
            r = resid[i, j]
            if (a.level == "binary" and table.index[i] == 0) or (b.level == "binary" and table.columns[j] == 0):
                continue
            if np.isfinite(r) and r > RESIDUAL_THRESHOLD:
                cells.append({
                    "kind": "cell", "a": _plain(table.index[i]), "b": _plain(table.columns[j]),
                    "count": int(obs[i, j]), "group_n": int(row[i, 0]),
                    "share_in_group": float(obs[i, j] / row[i, 0]),
                    "share_overall": float(col[0, j] / n),
                    "residual": float(r),
                })
    cells.sort(key=lambda c: -c["residual"])
    return cells[:MAX_PATTERN_CELLS]


def _plain(value: Any) -> Any:
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value
