# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Statistical associations between survey questions, from data to text.

The chain, each step checkable:

1. Variables (utils/analysis_variables.py): every question becomes one or
   more variables with a defined meaning and base (per option, per item,
   per cluster); respondents who were not asked and evasive answers are
   excluded.
2. Tests (utils/association.py): every variable of one question against
   every variable of the other, with a test suited to the measurement
   levels; tables with too small expected counts are not used.
3. Family: all tests of a run form one family; p values are adjusted with
   Benjamini-Hochberg. A finding needs q < alpha and at least a weak effect.
4. Selection: every question pair is tested; themes only group the
   results. Per question pair the strongest findings (max_per_pair), per
   theme the strongest question pairs (max_per_theme), between themes and
   for pairs outside any theme ("general") max_cross_theme each.
5. Facts: test, effect, q, n and the pattern in the data, written from fixed
   texts (locales/) in every output language. They are shown with every
   finding, so each statement can be checked.
6. Texts: the LLM words a description and, separately, a possible reading
   from the facts, in the survey's source language; other languages come
   from the translation service. Texts are cached per finding and facts
   (text_store), so an unchanged analysis gives the same texts.

The method record (CorrelationReport.method) states what was done in this
run: settings, number of tests, tests not possible and why, corrections.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from utils.analysis_variables import AnalysisVariable, build_variables, cluster_memberships_from_results
from utils.association import MIN_GROUP, MIN_N, STRENGTHS, AssociationResult, adjust, assess_pair
from utils.languages import get_output_languages, get_source_language, localized

logger = logging.getLogger(__name__)

DEFAULTS = {
    "alpha": 0.05,
    "min_strength": "weak",
    "max_per_pair": 3,
    "max_per_theme": 5,
    "max_cross_theme": 10,
}
MEASURE_NOTES = ("rank_position", "scale_index", "cluster_mentioned")


def analysis_settings(config: Optional[dict]) -> dict:
    """settings.analysis.correlations over the defaults."""
    own = ((((config or {}).get("settings") or {}).get("analysis") or {}).get("correlations") or {})
    return {**DEFAULTS, **{k: v for k, v in own.items() if k in DEFAULTS}}


# ---------------------------------------------------------------------------
# Facts in words (fixed texts from locales/)
# ---------------------------------------------------------------------------

def _t(key: str, lang: str, **fields) -> str:
    from utils.languages import text
    return text(key, lang, **fields)


def fmt_number(value: float, lang: str, digits: int = 2) -> str:
    s = f"{value:.{digits}f}"
    return s.replace(".", ",") if lang in ("de", "fr", "it") else s


def fmt_q(q: Optional[float], lang: str) -> str:
    if q is None:
        return "–"
    return "< " + fmt_number(0.001, lang, 3) if q < 0.001 else fmt_number(q, lang, 3)


def variable_label(var: AnalysisVariable, lang: str, src: str) -> str:
    label = var.label(lang, src)
    if var.measure in MEASURE_NOTES:
        label += f" ({_t(f'analysis.measure.{var.measure}', lang)})"
    return label


def _category(var: AnalysisVariable, value: Any, lang: str, src: str) -> str:
    return f"{variable_label(var, lang, src)} = {var.category_label(value, lang, src)}"


def finding_facts(result: AssociationResult, lang: str, src: str) -> List[str]:
    """Checkable statements about one finding, in ``lang``."""
    a, b = result.var_a, result.var_b
    digits = 3 if result.effect_measure == "epsilon_squared" else 2
    facts = [_t("analysis.fact.stats", lang,
                test=_t(f"analysis.test.{result.test}", lang),
                measure=_t(f"analysis.effect_measure.{result.effect_measure}", lang),
                effect=fmt_number(result.effect, lang, digits),
                strength=_t(f"analysis.strength.{result.strength}", lang),
                q=fmt_q(result.q_value, lang), n=result.n)]
    for item in result.pattern:
        if item["kind"] == "cell":
            facts.append(_t("analysis.fact.cell", lang,
                            a=_category(a, item["a"], lang, src), b=_category(b, item["b"], lang, src),
                            group_n=item["group_n"],
                            share=fmt_number(100 * item["share_in_group"], lang, 0),
                            overall=fmt_number(100 * item["share_overall"], lang, 0)))
        elif item["kind"] == "direction":
            key = "direction_positive" if item["sign"] > 0 else "direction_negative"
            facts.append(_t(f"analysis.fact.{key}", lang, a=variable_label(a, lang, src),
                            b=variable_label(b, lang, src)))
        elif item["kind"] == "group_median":
            group_var = a if item["variable"] == a.id else b
            num_var = b if group_var is a else a
            facts.append(_t("analysis.fact.median", lang, num=variable_label(num_var, lang, src),
                            group=group_var.category_label(item["group"], lang, src),
                            median=fmt_number(item["median"], lang, 1),
                            overall=fmt_number(item["overall_median"], lang, 1),
                            group_n=item["group_n"]))
    return facts


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class CorrelationFinding:
    result: AssociationResult
    description: Dict[str, str] = field(default_factory=dict)
    reading: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.result.key()

    def to_dict(self, languages: List[str], src: str) -> dict:
        r = self.result
        return {
            "variable_1": {**r.var_a.describe(), "label": {l: variable_label(r.var_a, l, src) for l in languages}},
            "variable_2": {**r.var_b.describe(), "label": {l: variable_label(r.var_b, l, src) for l in languages}},
            "test": r.test, "effect_measure": r.effect_measure,
            "coefficient": round(r.effect, 4), "p_value": r.p_value, "q_value": r.q_value,
            "strength": r.strength, "n": r.n,
            "pattern": r.pattern, "dropped_categories": r.dropped_categories,
            "facts": {l: finding_facts(r, l, src) for l in languages},
            "description": self.description, "reading": self.reading,
        }


@dataclass
class QuestionPairCorrelation:
    question_1_id: str
    question_2_id: str
    question_1_label: Dict[str, str]
    question_2_label: Dict[str, str]
    findings: List[CorrelationFinding]

    @property
    def best(self) -> CorrelationFinding:
        return self.findings[0]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.question_1_id, self.question_2_id)

    def to_dict(self, languages: List[str], src: str) -> dict:
        best = self.best.result
        return {
            "id": f"corr_{self.question_1_id}_{self.question_2_id}",
            "question_1": {"id": self.question_1_id, "label": self.question_1_label},
            "question_2": {"id": self.question_2_id, "label": self.question_2_label},
            # strongest finding, for overviews (heatmap, network)
            "correlation": {
                "coefficient": round(abs(best.effect), 4), "effect_measure": best.effect_measure,
                "test": best.test, "p_value": best.p_value, "q_value": best.q_value,
                "strength": best.strength, "n": best.n, "is_significant": True,
                "interpretation": self.best.description, "reading": self.best.reading,
            },
            "findings": [f.to_dict(languages, src) for f in self.findings],
        }


@dataclass
class ThemeCorrelationSummary:
    theme_id: str
    theme_label: Dict[str, str]
    theme_icon: str
    questions: List[str]
    pairs: List[QuestionPairCorrelation]
    theme_insight: Dict[str, str] = field(default_factory=dict)

    def to_dict(self, languages: List[str], src: str) -> dict:
        return {"theme_id": self.theme_id, "label": self.theme_label, "icon": self.theme_icon,
                "questions_in_theme": self.questions,
                "correlations": [p.to_dict(languages, src) for p in self.pairs],
                "theme_insight": self.theme_insight}


@dataclass
class CorrelationReport:
    themes: Dict[str, ThemeCorrelationSummary]
    cross: List[Tuple[QuestionPairCorrelation, str, str]]
    method: dict
    theme_labels: Dict[str, Dict[str, str]]

    def pairs(self) -> List[QuestionPairCorrelation]:
        seen, out = set(), []
        for summary in self.themes.values():
            for p in summary.pairs:
                if p.key not in seen:
                    seen.add(p.key)
                    out.append(p)
        for p, _, _ in self.cross:
            if p.key not in seen:
                seen.add(p.key)
                out.append(p)
        return out

    def to_json(self, config: dict, total_responses: int, languages: List[str]) -> dict:
        src = get_source_language(config)
        meta = ((config.get("settings") or {}).get("survey_metadata") or {})
        reported = self.pairs()
        strengths = [p.best.result.strength for p in reported]
        return {
            "$schema": "correlations_schema_v2",
            "generated_at": datetime.now().isoformat(),
            "survey_metadata": {"survey_year": meta.get("survey_year"),
                                "survey_title": meta.get("survey_title"),
                                "total_responses": total_responses},
            "method": self.method,
            "summary": {
                "question_pairs_tested": self.method.get("question_pairs", 0),
                "variable_pairs_tested": self.method.get("variable_pairs_tested", 0),
                "significant_correlations_found": len(reported),
                "strong_correlations": strengths.count("strong"),
                "medium_correlations": strengths.count("medium"),
                "weak_correlations": strengths.count("weak"),
            },
            "theme_correlations": {tid: s.to_dict(languages, src) for tid, s in self.themes.items()},
            "cross_theme_correlations": {
                "label": {l: _t("correlations.cross_theme", l) for l in languages},
                "correlations": [
                    {**p.to_dict(languages, src), "themes_involved": [t1, t2],
                     "theme_1": {"id": t1, "label": self.theme_labels.get(t1, {src: t1})},
                     "theme_2": {"id": t2, "label": self.theme_labels.get(t2, {src: t2})}}
                    for p, t1, t2 in self.cross
                ],
            },
        }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class CorrelationAnalyzer:
    """Runs steps 1–6 of the module docstring for one survey."""

    def __init__(self, config: dict, df: pd.DataFrame, llm_client=None,
                 cluster_memberships: Optional[Dict[str, Dict[str, Any]]] = None,
                 text_store=None, db=None):
        self.config = config
        self.df = df
        self.llm = llm_client
        self.cluster_memberships = cluster_memberships or {}
        self.text_store = text_store
        self.db = db
        self.settings = analysis_settings(config)
        self.src = get_source_language(config)

    # -- steps 1–4 ------------------------------------------------------------

    def run(self, themes: Optional[Dict[str, dict]] = None, languages: Optional[List[str]] = None,
            progress=None) -> CorrelationReport:
        languages = list(languages or get_output_languages(self.config))
        themes = {tid: t for tid, t in (themes or {}).items() if len(t.get("questions") or []) >= 2}
        groups = self.config.get("question_groups") or {}
        qids = list(groups)  # what is tested does not depend on the theme assignment
        variables = build_variables(self.config, self.df, qids, self.cluster_memberships)
        qids = [q for q in qids if variables.get(q)]

        results: List[AssociationResult] = []
        question_pairs = list(combinations(qids, 2))
        for i, (q1, q2) in enumerate(question_pairs):
            if progress and i % 20 == 0:
                progress(i, len(question_pairs), f"Associations {i + 1}/{len(question_pairs)}")
            for va in variables[q1]:
                for vb in variables[q2]:
                    results.append(assess_pair(va, vb))
        adjust(results)  # one family: every test of this run

        min_rank = STRENGTHS.index(self.settings["min_strength"])
        alpha = self.settings["alpha"]
        by_pair: Dict[Tuple[str, str], List[AssociationResult]] = {}
        for r in results:
            if r.is_significant(alpha) and STRENGTHS.index(r.strength) >= min_rank:
                by_pair.setdefault((r.var_a.question_id, r.var_b.question_id), []).append(r)

        def rank(r: AssociationResult):
            return (-STRENGTHS.index(r.strength), r.q_value, -abs(r.effect))

        pairs: Dict[Tuple[str, str], QuestionPairCorrelation] = {}
        for (q1, q2), found in by_pair.items():
            found.sort(key=rank)
            pairs[(q1, q2)] = QuestionPairCorrelation(
                q1, q2, self._label(q1), self._label(q2),
                [CorrelationFinding(r) for r in found[: self.settings["max_per_pair"]]])
        ordered = sorted(pairs.values(), key=lambda p: rank(p.best.result))

        summaries: Dict[str, ThemeCorrelationSummary] = {}
        cross: List[Tuple[QuestionPairCorrelation, str, str]] = []
        general: List[QuestionPairCorrelation] = []
        theme_labels = {tid: t.get("label") or {self.src: tid} for tid, t in themes.items()}
        members = {tid: set(t.get("questions") or []) for tid, t in themes.items()}
        inside: Dict[str, List[QuestionPairCorrelation]] = {tid: [] for tid in themes}
        for p in ordered:
            common = [tid for tid, qs in members.items() if p.question_1_id in qs and p.question_2_id in qs]
            if common:
                for tid in common:
                    inside[tid].append(p)
                continue
            t1 = next((tid for tid, qs in members.items() if p.question_1_id in qs), None)
            t2 = next((tid for tid, qs in members.items() if p.question_2_id in qs), None)
            if t1 and t2:
                cross.append((p, t1, t2))
            else:
                general.append(p)  # at least one question in no theme
        for tid, pairs_in in inside.items():
            if pairs_in:
                summaries[tid] = ThemeCorrelationSummary(
                    tid, theme_labels[tid], themes[tid].get("icon", "📊"),
                    [q for q in qids if q in members[tid]], pairs_in[: self.settings["max_per_theme"]])
        cross = cross[: self.settings["max_cross_theme"]]
        if general:
            label = {l: _t("correlations.general", l) for l in languages}
            theme_labels["general"] = label
            summaries["general"] = ThemeCorrelationSummary(
                "general", label, "📊", qids, general[: self.settings["max_cross_theme"]])

        method = self._method_record(results, len(question_pairs), len(qids), summaries, cross)
        report = CorrelationReport(summaries, cross, method, theme_labels)
        self._write_texts(report, languages)
        return report

    def _label(self, qid: str) -> Dict[str, str]:
        label = ((self.config.get("question_groups") or {}).get(qid) or {}).get("label")
        return label if isinstance(label, dict) else {self.src: str(label or qid)}

    def _method_record(self, results, n_question_pairs, n_questions, summaries, cross) -> dict:
        not_testable: Dict[str, int] = {}
        tests: Dict[str, int] = {}
        for r in results:
            if r.testable:
                tests[r.test] = tests.get(r.test, 0) + 1
            else:
                not_testable[r.reason] = not_testable.get(r.reason, 0) + 1
        alpha = self.settings["alpha"]
        reported = {p.key for s in summaries.values() for p in s.pairs} | {p.key for p, _, _ in cross}
        return {
            **self.settings,
            "correction": "benjamini_hochberg",
            "family": "all variable pairs tested in this run",
            "questions": n_questions,
            "question_pairs": n_question_pairs,
            "variable_pairs_tested": sum(tests.values()),
            "tests": tests,
            "not_testable": not_testable,
            "significant_unadjusted": sum(1 for r in results if r.testable and r.p_value < alpha),
            "significant_adjusted": sum(1 for r in results if r.is_significant(alpha)),
            "question_pairs_reported": len(reported),
            "min_cases": MIN_N,
            "min_group": MIN_GROUP,
        }

    # -- steps 5–6: texts -------------------------------------------------------

    def _question_text(self, qid: str) -> str:
        q = (self.config.get("question_groups") or {}).get(qid) or {}
        return str(localized(q.get("full_question") or q.get("label") or {}, self.src, self.src) or qid)

    def _text_writer(self):
        from utils.finding_texts import FindingTextWriter
        return FindingTextWriter(
            self.config, self.llm, "correlation",
            facts_of=lambda f: finding_facts(f.result, self.src, self.src),
            fields_of=lambda f: {"question_1": self._question_text(f.result.var_a.question_id),
                                 "question_2": self._question_text(f.result.var_b.question_id)},
            store=self.text_store, db=self.db, label="association")

    def _write_texts(self, report: CorrelationReport, languages: List[str]) -> None:
        findings = [f for p in report.pairs() for f in p.findings]
        if not findings:
            return
        self._text_writer().write(findings, languages)
        for summary in report.themes.values():
            summary.theme_insight = self._theme_insight(summary, languages)

    def _theme_insight(self, summary: ThemeCorrelationSummary, languages: List[str]) -> Dict[str, str]:
        """Summary of a theme from the descriptions of its strongest findings (stored, see finding_texts)."""
        from utils.finding_texts import write_summary
        lines = [f.description.get(self.src) for p in summary.pairs for f in p.findings[:1]
                 if f.description.get(self.src)]
        theme = str(localized(summary.theme_label, self.src, self.src) or summary.theme_id)
        return write_summary(self.config, self.llm, key=f"theme:{summary.theme_id}", lines=lines,
                             languages=languages, system_key="correlation.theme_system",
                             user_key="correlation.theme_user", system_fields={"theme": theme},
                             store=self.text_store, db=self.db, label="theme summary")


# ---------------------------------------------------------------------------
# Entry point for the exports (dashboard, pipeline)
# ---------------------------------------------------------------------------

def load_themes(state) -> Dict[str, dict]:
    """Themes of the loaded survey; from the theme configuration if none are loaded yet."""
    if getattr(state, "themes", None):
        return state.themes
    try:
        from pathlib import Path
        from themes.theme_analyzer import ThemeAnalyzer
        # the themes of the active survey (surveys/<id>/themes.yaml); the shared
        # config/themes.yaml is only a template
        try:
            from core import get_active_config_dir
            config_dir = Path(get_active_config_dir())
        except Exception:
            config_dir = Path(__file__).resolve().parent.parent / "config"
        analyzer = ThemeAnalyzer(config=state.config, llm_client=None, config_dir=config_dir)
        state.themes = {tid: theme.to_dict() for tid, theme in analyzer.themes.items()}
    except Exception as e:
        logger.warning(f"Themes not loaded, associations without themes: {e}")
        state.themes = {}
    return state.themes


def run_for_state(state, languages: Optional[List[str]] = None, llm_client=None, progress=None) -> CorrelationReport:
    """The single path both exports use."""
    from utils.analysis_texts import AnalysisTextStore
    import_id = getattr(state, "import_id", None)
    store = AnalysisTextStore(state.db, import_id, "correlation") if state.db is not None and import_id else None
    analyzer = CorrelationAnalyzer(
        config=state.config, df=state.df, llm_client=llm_client,
        cluster_memberships=cluster_memberships_from_results(state.analysis_results or {}, state.config),
        text_store=store, db=state.db)
    return analyzer.run(load_themes(state), languages, progress)
