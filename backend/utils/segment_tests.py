# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Differences between segments for one question.

Every configured segmentation dimension is tested against every analysis
variable of the question (utils/analysis_variables.py): for a multiple-choice
question each option on its own (share of respondents choosing it), for a
matrix each item, for a numeric question the values. The test follows the
measurement levels (utils/association.py); the segment is nominal.

All tests of one question (dimensions × variables) form one family and are
adjusted with Benjamini-Hochberg. A dimension "differs" if at least one of
its tests has q < alpha and at least a weak effect.

The result distinguishes three states per dimension, so a report never says
"no differences" where nothing could be tested:
- differs:        at least one significant finding
- no_difference:  tested, nothing significant
- not_testable:   too few cases or groups for any test
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.analysis_variables import AnalysisVariable, build_variables
from utils.association import ALPHA, STRENGTHS, AssociationResult, adjust, assess_pair
from utils.languages import get_source_language


@dataclass
class DimensionTests:
    dimension_id: str
    label: Dict[str, str]
    results: List[AssociationResult]
    n_per_segment: Dict[str, int] = field(default_factory=dict)
    alpha: float = ALPHA
    min_strength: str = "weak"

    def significant(self) -> List[AssociationResult]:
        rank = STRENGTHS.index(self.min_strength)
        found = [r for r in self.results
                 if r.is_significant(self.alpha) and STRENGTHS.index(r.strength) >= rank]
        return sorted(found, key=lambda r: (-STRENGTHS.index(r.strength), r.q_value))

    @property
    def state(self) -> str:
        if self.significant():
            return "differs"
        return "no_difference" if any(r.testable for r in self.results) else "not_testable"


@dataclass
class SegmentTests:
    question_id: str
    dimensions: List[DimensionTests]

    def method(self) -> dict:
        tests = [r for d in self.dimensions for r in d.results]
        return {"correction": "benjamini_hochberg", "family": "all tests of this question",
                "tests": sum(1 for r in tests if r.testable),
                "not_testable": sum(1 for r in tests if not r.testable)}


def segment_variable(resolver, dim, df_mat: pd.DataFrame, src: str) -> Optional[AnalysisVariable]:
    """The segment of a dimension as a nominal variable (labels from the configuration)."""
    column = resolver.segment_column(dim.id)
    if column not in df_mat.columns:
        return None
    values = df_mat[column].map(lambda v: None if pd.isna(v) or str(v).strip() == "" else str(v).strip())
    categories: Dict[Any, Dict[str, str]] = {}
    for bucket in (dim.bucketing.buckets or []):
        categories[bucket.get_label(src, src)] = dict(bucket.label)
    for value, labels in (dim.value_labels or {}).items():
        categories[str(value)] = {**categories.get(str(value), {}), **labels}
    for value in values.dropna().unique():
        categories.setdefault(value, {src: value})
    return AnalysisVariable(f"segment:{dim.id}", f"segment:{dim.id}", "nominal", "choice",
                            dict(dim.label), None, values.astype(object), categories)


def run_segment_tests(config: dict, df: pd.DataFrame, question_id: str,
                      cluster_memberships: Optional[dict] = None) -> SegmentTests:
    from utils.correlation import analysis_settings
    from utils.segment_resolver import SegmentResolver
    src = get_source_language(config)
    settings = analysis_settings(config)
    resolver = SegmentResolver(config)
    variables = build_variables(config, df, [question_id], cluster_memberships).get(question_id, [])
    df_mat = resolver.materialize(df)
    dims: List[DimensionTests] = []
    for dim in resolver.list_dimensions():
        if dim.source_question == question_id:
            continue  # a question against its own segmentation says nothing
        seg = segment_variable(resolver, dim, df_mat, src)
        if seg is None:
            continue
        results = [assess_pair(seg, var) for var in variables]
        counts = seg.values.dropna().value_counts()
        dims.append(DimensionTests(dim.id, dict(dim.label), results,
                                   {str(k): int(v) for k, v in counts.items()},
                                   settings["alpha"], settings["min_strength"]))
    adjust([r for d in dims for r in d.results])  # one family per question
    return SegmentTests(question_id, dims)
