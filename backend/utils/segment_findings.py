# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Differences between segments, per question and dimension, as statistical findings.

The tests are those of utils/segment_tests.run_segment_tests: every answer
option, item, free-text topic (global clusters) or numeric value of a
question against the groups of every segmentation dimension, one
Benjamini-Hochberg family per question. The segment part of the LLM
summaries uses the same tests, so both say the same.

Selection per dimension:

- significant (q < alpha) with at least the configured minimum strength,
  strongest first (the rule of ``DimensionTests.significant``);
- for shares (an option chosen, an item answered "yes", a topic mentioned)
  the gap between the highest and the lowest group share must reach
  ``min_difference`` percentage points. A significant but smaller gap is
  counted (``small_differences``) and stated in a fixed sentence, not listed,
  because a few points rarely matter for a report reader even with many
  cases;
- at most ``MAX_FINDINGS_PER_DIMENSION`` findings.

A dimension with at least ``SUMMARY_MIN_FINDINGS`` listed findings gets a
summary written from their descriptions only (no new figures, no reading),
like the theme summary of the associations; it is stored the same way.

Every listed finding carries its facts (computed here, in every output
language), a description and a possible reading set apart (written by the
LLM from the facts only, see utils/finding_texts.py). Dimensions without a
listed finding get a fixed sentence from locales/: tested without
difference, too few cases, or only small differences.

Segment-specific clusters (``enable_specific_clusters``) are separate
clusterings per group and cannot be tested against each other; they are
shown descriptively elsewhere and do not appear here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.association import MIN_GROUP, AssociationResult
from utils.languages import get_output_languages, get_source_language, localized, text

logger = logging.getLogger(__name__)

DEFAULT_MIN_DIFFERENCE = 15.0      # percentage points between highest and lowest group share
MAX_FINDINGS_PER_DIMENSION = 3     # as in the segment part of the summaries
SUMMARY_MIN_FINDINGS = 2          # a summary of a single finding would only repeat it
TEXT_KIND = "segment_difference"   # kind in the table analysis_texts


def group_values(result: AssociationResult) -> List[Dict[str, Any]]:
    """
    Per group of the segment: n and the share (binary variables) or the
    median (numeric variables), on the respondents the test used. Groups
    below the minimum size are left out, as in the test.
    """
    seg, var = result.var_a, result.var_b
    joint = pd.concat([seg.values.rename("g"), var.values.rename("v")], axis=1).dropna()
    out = []
    for value, rows in joint.groupby("g"):
        if len(rows) < MIN_GROUP:
            continue
        entry: Dict[str, Any] = {"value": value, "n": int(len(rows))}
        if var.level == "binary":
            entry["share"] = float(100 * (rows["v"] == 1).mean())
        elif var.level == "numeric":
            entry["median"] = float(rows["v"].median())
        else:
            continue
        out.append(entry)
    key = "share" if var.level == "binary" else "median"
    return sorted(out, key=lambda e: -e.get(key, 0))


def share_gap(groups: List[Dict[str, Any]]) -> Optional[float]:
    shares = [g["share"] for g in groups if "share" in g]
    return max(shares) - min(shares) if len(shares) >= 2 else None


@dataclass
class SegmentFinding:
    question_id: str
    dimension_id: str
    result: AssociationResult
    groups: List[Dict[str, Any]] = field(default_factory=list)
    description: Dict[str, str] = field(default_factory=dict)
    reading: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.question_id}|{self.dimension_id}|{self.result.var_b.id}"

    def facts(self, lang: str, src: str) -> List[str]:
        from utils.correlation import finding_facts, fmt_number, variable_label
        facts = finding_facts(self.result, lang, src)
        shares = [g for g in self.groups if "share" in g]
        if shares:
            values = ", ".join(text("analysis.fact.group_share", lang,
                                    group=self.result.var_a.category_label(g["value"], lang, src),
                                    share=fmt_number(g["share"], lang, 0), n=g["n"]) for g in shares)
            facts.append(text("analysis.fact.group_shares", lang,
                              variable=variable_label(self.result.var_b, lang, src), values=values))
        return facts

    def to_dict(self, languages: List[str], src: str) -> dict:
        from utils.correlation import variable_label
        r = self.result
        return {
            "variable": {**r.var_b.describe(), "label": {l: variable_label(r.var_b, l, src) for l in languages}},
            "test": r.test, "effect_measure": r.effect_measure, "coefficient": round(r.effect, 4),
            "p_value": r.p_value, "q_value": r.q_value, "strength": r.strength, "n": r.n,
            "pattern": r.pattern, "dropped_categories": r.dropped_categories,
            "groups": [{**{k: v for k, v in g.items() if k != "value"}, "value": str(g["value"]),
                        "label": {l: r.var_a.category_label(g["value"], l, src) for l in languages}}
                       for g in self.groups],
            "facts": {l: self.facts(l, src) for l in languages},
            "description": self.description, "reading": self.reading,
        }


@dataclass
class DimensionDifferences:
    dimension_id: str
    label: Dict[str, str]
    state: str                                   # differs | no_difference | not_testable
    findings: List[SegmentFinding]
    small_differences: int = 0                   # significant, gap below min_difference
    n_per_segment: Dict[str, int] = field(default_factory=dict)
    tests: int = 0
    not_testable: int = 0
    summary: Dict[str, str] = field(default_factory=dict)

    def statement(self, lang: str, src: str, min_difference: float) -> str:
        """Fixed sentence when nothing is listed for this dimension."""
        if self.findings:
            return ""
        label = str(localized(self.label, lang, src) or self.dimension_id)
        if self.state == "not_testable":
            return text("summary.segments_not_testable", lang, dimensions=label)
        if self.state == "no_difference":
            return text("summary.no_significant_differences", lang, dimensions=label)
        return text("report.group_differences.small_only", lang, dimension=label,
                    points=f"{min_difference:g}")


@dataclass
class QuestionDifferences:
    question_id: str
    label: Dict[str, str]
    dimensions: List[DimensionDifferences]

    @property
    def findings(self) -> List[SegmentFinding]:
        return [f for d in self.dimensions for f in d.findings]


@dataclass
class SegmentDifferenceReport:
    questions: Dict[str, QuestionDifferences]
    min_difference: float
    alpha: float
    min_strength: str
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_json(self, config: dict, languages: List[str]) -> dict:
        from datetime import datetime
        src = get_source_language(config)
        dims = [d for q in self.questions.values() for d in q.dimensions]
        return {
            "$schema": "segment_insights_v2",
            "generated_at": datetime.now().isoformat(),
            "parameters": {"languages": list(languages), "source_language": src,
                           "alpha": self.alpha, "min_strength": self.min_strength,
                           "min_difference_points": self.min_difference,
                           "max_findings_per_dimension": MAX_FINDINGS_PER_DIMENSION,
                           "summary_min_findings": SUMMARY_MIN_FINDINGS},
            "method": {"correction": "benjamini_hochberg", "family": "all tests of one question",
                       "tests": sum(d.tests for d in dims), "not_testable": sum(d.not_testable for d in dims),
                       "questions_tested": len(self.questions),
                       "errors": list(self.errors)},
            "questions": {
                qid: {"label": {l: str(localized(q.label, l, src) or qid) for l in languages},
                      "dimensions": {
                          d.dimension_id: {
                              "label": {l: str(localized(d.label, l, src) or d.dimension_id) for l in languages},
                              "state": d.state, "n_per_segment": d.n_per_segment,
                              "small_differences": d.small_differences,
                              "summary": d.summary,
                              "statement": {l: d.statement(l, src, self.min_difference) for l in languages},
                              "findings": [f.to_dict(languages, src) for f in d.findings],
                          } for d in q.dimensions}}
                for qid, q in self.questions.items()},
            "summary": {"questions_with_differences": sum(1 for q in self.questions.values() if q.findings),
                        "questions_tested": len(self.questions)},
        }


class SegmentDifferenceAnalyzer:
    def __init__(self, config: dict, df: pd.DataFrame, analysis_results: Optional[dict] = None,
                 llm_client=None, text_store=None, db=None,
                 min_difference: float = DEFAULT_MIN_DIFFERENCE):
        from utils.correlation import analysis_settings
        self.config = config
        self.df = df
        self.analysis_results = analysis_results or {}
        self.llm = llm_client
        self.text_store = text_store
        self.db = db
        self.min_difference = float(min_difference)
        self.settings = analysis_settings(config)
        self.src = get_source_language(config)
        self._dim_labels: Dict[str, Dict[str, str]] = {}

    def run(self, languages: Optional[List[str]] = None, progress=None) -> SegmentDifferenceReport:
        from utils.analysis_variables import cluster_memberships_from_results
        from utils.segment_tests import run_segment_tests
        languages = list(languages or get_output_languages(self.config))
        memberships = cluster_memberships_from_results(self.analysis_results, self.config)
        report = SegmentDifferenceReport({}, self.min_difference, self.settings["alpha"],
                                         self.settings["min_strength"])
        groups = self.config.get("question_groups") or {}
        for i, (qid, qconfig) in enumerate(groups.items()):
            if progress:
                progress(i, len(groups), f"Gruppenunterschiede {qid}")
            try:
                tests = run_segment_tests(self.config, self.df, qid, memberships)
            except (ValueError, KeyError) as e:
                # A configuration error is reported, never skipped silently (invariant 2)
                logger.warning(f"Group differences of {qid} not testable: {e}")
                report.errors.append({"question_id": qid, "error": str(e)})
                continue
            if not any(d.results for d in tests.dimensions):
                continue  # no variables (e.g. free text without clusters) or no dimension
            dims = [self._dimension(qid, d) for d in tests.dimensions]
            report.questions[qid] = QuestionDifferences(qid, dict(qconfig.get("label") or {self.src: qid}), dims)
        findings = [f for q in report.questions.values() for f in q.findings]
        if findings:
            self._text_writer().write(findings, languages)
            for question in report.questions.values():
                for dim in question.dimensions:
                    dim.summary = self._summary(question.question_id, dim, languages)
        return report

    def _summary(self, qid: str, dim: "DimensionDifferences", languages: List[str]) -> Dict[str, str]:
        from utils.finding_texts import write_summary
        lines = [f.description[self.src] for f in dim.findings if f.description.get(self.src)]
        if len(lines) < SUMMARY_MIN_FINDINGS:
            return {}
        return write_summary(self.config, self.llm, key=f"summary:{qid}|{dim.dimension_id}", lines=lines,
                             languages=languages, system_key="segment_differences.summary_system",
                             user_key="segment_differences.summary_user",
                             system_fields={"question": self._question_text(qid),
                                            "dimension": self._dimension_label(dim.dimension_id)},
                             store=self.text_store, db=self.db, label="summary of group differences")

    def _dimension(self, qid: str, dim) -> DimensionDifferences:
        self._dim_labels[dim.dimension_id] = dict(dim.label)
        listed, small = [], 0
        for result in dim.significant():
            finding = SegmentFinding(qid, dim.dimension_id, result, group_values(result))
            gap = share_gap(finding.groups)
            if gap is not None and gap < self.min_difference:
                small += 1
                continue
            listed.append(finding)
        return DimensionDifferences(
            dim.dimension_id, dict(dim.label), dim.state, listed[:MAX_FINDINGS_PER_DIMENSION], small,
            dict(dim.n_per_segment),
            tests=sum(1 for r in dim.results if r.testable),
            not_testable=sum(1 for r in dim.results if not r.testable))

    def _question_text(self, qid: str) -> str:
        q = (self.config.get("question_groups") or {}).get(qid) or {}
        return str(localized(q.get("full_question") or q.get("label") or {}, self.src, self.src) or qid)

    def _dimension_label(self, dim_id: str) -> str:
        return str(localized(self._dim_labels.get(dim_id) or {}, self.src, self.src) or dim_id)

    def _text_writer(self):
        from utils.finding_texts import FindingTextWriter
        return FindingTextWriter(
            self.config, self.llm, "segment_differences",
            facts_of=lambda f: f.facts(self.src, self.src),
            fields_of=lambda f: {"question": self._question_text(f.question_id),
                                 "dimension": self._dimension_label(f.dimension_id)},
            store=self.text_store, db=self.db, label="Gruppenunterschied")


def run_for_state(state, languages: Optional[List[str]] = None, llm_client=None,
                  min_difference: float = DEFAULT_MIN_DIFFERENCE, progress=None) -> SegmentDifferenceReport:
    """The single path the exports use (pipeline, Word report, dashboard)."""
    from utils.analysis_texts import AnalysisTextStore
    import_id = getattr(state, "import_id", None)
    store = AnalysisTextStore(state.db, import_id, TEXT_KIND) if state.db is not None and import_id else None
    analyzer = SegmentDifferenceAnalyzer(state.config, state.df, state.analysis_results or {},
                                         llm_client=llm_client, text_store=store, db=state.db,
                                         min_difference=min_difference)
    return analyzer.run(languages, progress)
