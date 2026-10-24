# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
LLM summaries of the question results.

Generates a configurable number of text blocks (sections) per question. The
sections are resolved by the SchemaResolver (see utils/summary_schema.py) and can
be overridden per survey, per handler type or per question. The default schema
has three sections:
1. description of the results (factual, descriptive)
2. interpretation (context, notable points)
3. segment differences (only with a segmentation; without significant
   differences a fixed sentence from locales/ instead of an LLM text)

The prompts come from prompts/<source language>.yaml; the texts are written in
the source language of the survey and then translated into the other output
languages. All texts are cached in the database (table question_summaries).

Storage: save_summary_to_db / load_summary_from_db keep schema_id and the
sections as JSON (sections_json); there are no other columns for the texts.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple

import pandas as pd

from utils.languages import get_source_language, localized

# schema module
from utils.summary_schema import (
    SchemaResolver, SummarySchema, Section,
    DEFAULT_THREE_SECTIONS_SCHEMA,
    is_default_summary,
)

logger = logging.getLogger(__name__)


def _is_data_empty(data) -> bool:
    """Whether the data is empty; works with dict, DataFrame and None."""
    if data is None:
        return True
    if isinstance(data, dict):
        return len(data) == 0
    if hasattr(data, 'empty'):  # pandas DataFrame/Series
        return data.empty
    return not data


# =============================================================================
# DATENKLASSEN
# =============================================================================

@dataclass
class QuestionSummary:
    """
    Summary of a question in one language.
    
    The sections are held in a dict whose keys are the section ids of the
    resolved SummarySchema. The sections of the default schema
    (description/interpretation/segments_text) can also be read and set as
    properties.
    
    Attributes:
        question_id: question id from question_groups.
        lang: language code.
        sections: {section_id: text}. The order of the entries follows the
            schema, but may differ after translations (iteration in the old
            order of the entries).
        schema_id: id of the schema this summary was created with; stored in
            the database, so it is clear which sections to expect on loading.
    """
    question_id: str
    lang: str
    sections: Dict[str, str] = field(default_factory=dict)
    schema_id: str = ''
    
    def __init__(self,
                 question_id: str,
                 lang: str,
                 sections: Optional[Dict[str, str]] = None,
                 schema_id: str = ''):
        self.question_id = question_id
        self.lang = lang
        self.schema_id = schema_id
        self.sections = dict(sections) if sections else {}

    
    # ----- Sections of the default schema as properties -----
    
    @property
    def description(self) -> str:
        return self.sections.get('description', '')
    
    @description.setter
    def description(self, value: str) -> None:
        self.sections['description'] = value or ''
    
    @property
    def interpretation(self) -> str:
        return self.sections.get('interpretation', '')
    
    @interpretation.setter
    def interpretation(self, value: str) -> None:
        self.sections['interpretation'] = value or ''
    
    @property
    def segments_text(self) -> str:
        return self.sections.get('segments_text', '')
    
    @segments_text.setter
    def segments_text(self, value: str) -> None:
        self.sections['segments_text'] = value or ''
    
    # ----- methods -----
    
    def to_dict(self) -> dict:
        """
        Serialisierung (sections as stored in sections_json).
        """
        return {
            'question_id': self.question_id,
            'lang': self.lang,
            'schema_id': self.schema_id,
            'sections': dict(self.sections),
        }
    
    def has_content(self) -> bool:
        """True if at least one section has a non-empty text."""
        return any(v.strip() for v in self.sections.values())
    
    def get_section(self, section_id: str) -> str:
        """Convenience: gets a section with an empty string as default."""
        return self.sections.get(section_id, '')
    
    def set_section(self, section_id: str, text: str) -> None:
        """Convenience: sets a section."""
        self.sections[section_id] = text or ''


@dataclass
class SegmentSignificance:
    """
    Test result of one segmentation dimension for a question (utils/segment_tests.py).

    state: 'differs' (at least one significant finding), 'no_difference'
    (tested, nothing significant) or 'not_testable' (too few cases).
    """
    segment_type: str           # dimension id
    segment_label: str          # label of the dimension in the source language
    is_significant: bool
    p_value: float              # smallest adjusted p (q) of the dimension
    cramers_v: float            # effect of the strongest finding (see effect_measure)
    effect_desc: str            # strength of the strongest finding
    segment_values: Dict[str, Dict] = field(default_factory=dict)
    n_per_segment: Dict[str, int] = field(default_factory=dict)
    state: str = ""
    findings: List[Any] = field(default_factory=list)   # significant AssociationResults
    effect_measure: str = "cramers_v"

    def __post_init__(self):
        if not self.state:
            self.state = "differs" if self.is_significant else "no_difference"


# =============================================================================
# SYSTEM PROMPT (from the schema and the prompt catalog of the source language)
# =============================================================================

def _p(key: str, lang: str, **fields) -> str:
    """Prompt text from prompts/<lang>.yaml."""
    from utils import prompts
    return prompts.prompt(key, lang, **fields)


def _length_hint_text(hint: Optional[str], lang: str = 'de') -> str:
    """Length hint of a section for the prompt ('' without one)."""
    if hint not in ('short', 'medium', 'long'):
        return ''
    return _p(f'summary.length.{hint}', lang)


def _context_for_prompt(llm_context: Any, lang: str) -> str:
    """Survey context for the system prompt: settings.llm_context as text or structured."""
    from utils import prompts
    if llm_context is None:
        text = prompts.context_text(None, lang)
    else:
        text = prompts.context_text({'settings': {'llm_context': llm_context}}, lang)
    return text if text != '-' else _p('summary.no_context', lang)


def build_system_prompt(schema: SummarySchema,
                        llm_context: Optional[Any] = None,
                        lang: Optional[str] = None) -> str:
    """
    System prompt for a schema, in the source language of the survey.

    Args:
        schema: resolved SummarySchema.
        llm_context: settings.llm_context (text or dict with domain/region/
            audience); None = from the loaded survey.
        lang: source language; None = that of the loaded survey.
    """
    from utils import prompts
    lang = lang or prompts.source_language()
    section_lines = []
    json_lines = []
    for s in schema.sections:
        length = _length_hint_text(s.length_hint, lang)
        section_lines.append(_p(
            'summary.section_line', lang, id=s.id, instruction=s.instruction(lang),
            length=_p('summary.length_format', lang, length=length) if length else '',
            optional='' if s.required else _p('summary.optional_marker', lang),
        ))
        json_lines.append(f'    "{s.id}": {json.dumps(s.instruction(lang), ensure_ascii=False)}')
    return _p(
        'summary.system', lang,
        context=_context_for_prompt(llm_context, lang),
        sections="\n".join(section_lines),
        json_format="{\n" + ",\n".join(json_lines) + "\n}",
    )


# =============================================================================
# PROMPT BUILDER PER QUESTION TYPE
# =============================================================================
# The data sections are part of the prompt and are therefore in the
# language of the prompt catalog (source language of the survey).

def _src_lang() -> str:
    from utils import prompts
    return prompts.source_language()


def _build_data_section_single_choice(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for single_choice questions."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    lines = [_p('summary.data.single_choice', lang, n=n), _p('summary.data.results', lang)]
    for category, count in sorted(data.items(), key=lambda x: -x[1]):
        pct = count / n * 100
        lines.append(f"  - {category}: {count} ({pct:.1f}%)")
    
    return "\n".join(lines)


def _build_data_section_multi_choice(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for multi_choice questions."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    lines = [_p('summary.data.multi_choice', lang, n=n), _p('summary.data.results', lang)]
    for category, count in sorted(data.items(), key=lambda x: -x[1]):
        pct = count / n * 100
        lines.append(f"  - {category}: {count} ({pct:.1f}%)")
    
    return "\n".join(lines)


def _build_data_section_multi_choice_binary(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for multi_choice_binary questions."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    percentages = result.percentages if hasattr(result, 'percentages') else result.get('percentages', {})
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    lines = [_p('summary.data.multi_choice_binary', lang, n=n), _p('summary.data.results_yes', lang)]
    
    # data is {category: count_yes} for binary questions
    for category, count in sorted(data.items(), key=lambda x: -x[1]):
        pct = percentages.get(category, count / n * 100 if n > 0 else 0)
        if isinstance(pct, (int, float)):
            lines.append(f"  - {category}: " + _p('summary.data.pct_yes', lang, pct=f"{pct:.1f}"))
        else:
            lines.append(f"  - {category}: " + _p('summary.data.count_yes', lang, count=count))
    
    return "\n".join(lines)


def _build_data_section_matrix_likert(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for matrix_likert questions."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    metadata = result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})
    
    # convert DataFrame -> dict
    if hasattr(data, 'to_dict'):
        data = data.to_dict()
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    lines = [_p('summary.data.matrix_likert', lang, n=n)]
    
    # means from the metadata
    means = metadata.get('means', {}) if metadata else {}
    
    if means:
        lines.append(_p('summary.data.means_ascending', lang))
        for item, mean in sorted(means.items(), key=lambda x: x[1]):
            lines.append(f"  - {item}: {mean:.2f}")
    else:
        lines.append(_p('summary.data.distribution', lang))
        for row_label, row_data in data.items():
            if isinstance(row_data, dict):
                parts = [f"{k}: {v}" for k, v in row_data.items()]
                lines.append(f"  - {row_label}: {', '.join(parts)}")
    
    # scale information from the configuration
    scale_labels = qconfig.get('scale_labels', {})
    if scale_labels:
        scale_info = ", ".join([f"{k}={v}" for k, v in scale_labels.items()])
        lines.append(_p('summary.data.scale', lang, scale=scale_info))
    
    return "\n".join(lines)


def _build_data_section_ranking(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for ranking questions."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    metadata = result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    num_ranks = metadata.get('num_ranks', len(data)) if metadata else len(data)
    
    lines = [
        _p('summary.data.ranking', lang, n=n, ranks=num_ranks),
        _p('summary.data.ranking_scores', lang, ranks=num_ranks),
    ]
    
    for item, score in sorted(data.items(), key=lambda x: -x[1]):
        lines.append(f"  - {item}: " + _p('summary.data.points', lang, score=f"{score:.0f}"))
    
    return "\n".join(lines)


def _build_data_section_freetext(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for freetext questions (clustering)."""
    from utils.languages import localized
    lang = lang or _src_lang()
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    metadata = result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})
    
    if n == 0:
        return ""
    
    cluster_details = metadata.get('cluster_details', []) if metadata else []
    unique_responses = metadata.get('unique_responses', n) if metadata else n
    
    lines = [
        _p('summary.data.freetext', lang),
        _p('summary.data.freetext_counts', lang, respondents=unique_responses, mentions=n,
           clusters=len(cluster_details)),
    ]
    
    # WARNING with a single cluster: the summary LLM must NOT claim that everybody agrees
    if len(cluster_details) == 1:
        lines.append("\n" + _p('summary.data.single_cluster_note', lang))
    
    lines.append(_p('summary.data.clusters', lang))
    
    for cluster in cluster_details:
        name = cluster.get('name', '')
        if isinstance(name, dict):
            name = localized(name, lang, lang, default=str(name))
        
        desc = cluster.get('description', '')
        if isinstance(desc, dict):
            desc = localized(desc, lang, lang, default=str(desc))
        
        count = cluster.get('count', 0)
        unique = cluster.get('unique_respondents', count)
        pct = unique / unique_responses * 100 if unique_responses > 0 else 0
        
        # item mode: distinguish mentions and persons
        if count > 0 and unique > 0 and count != unique:
            lines.append("  - " + _p('summary.data.cluster_items', lang, name=name, count=count,
                                     unique=unique, pct=f"{pct:.1f}", description=desc))
        else:
            lines.append("  - " + _p('summary.data.cluster_plain', lang, name=name, unique=unique,
                                     pct=f"{pct:.1f}", description=desc))
        
        # examples
        examples = cluster.get('examples', [])
        if examples:
            lines.append("    " + _p('summary.data.examples', lang, examples='; '.join(examples[:3])))
    
    return "\n".join(lines)


def _build_data_section_cooperation_matrix(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Builds the data section for cooperation_matrix questions.
    
    The handler returns:
    - data: matrix_df.to_dict() -> {scale value: {partner: count}} (nested)
    - metadata['importance_scores']: {partner: score} (flat)
    - metadata['percent_df']: DataFrame with the percentage distribution
    """
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    metadata = result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    lines = [_p('summary.data.cooperation_matrix', lang, n=n)]
    
    # importance scores from the metadata (primary data source)
    importance_scores = metadata.get('importance_scores', {}) if metadata else {}
    
    if importance_scores:
        lines.append("\n" + _p('summary.data.importance', lang))
        for partner, score in sorted(importance_scores.items(), key=lambda x: -x[1]):
            lines.append(f"  - {partner}: {score:.2f}")
    
    # percentage distribution from the matrix data structure:
    # data is {scale value: {partner: count}} -> transposed to {partner: {scale value: count}}
    if isinstance(data, dict):
        first_val = next(iter(data.values()), None)
        if isinstance(first_val, dict):
            # nested structure: {scale value: {partner: count}}
            partners = set()
            for scale_data in data.values():
                if isinstance(scale_data, dict):
                    partners.update(scale_data.keys())
            
            if partners:
                lines.append("\n" + _p('summary.data.per_partner', lang))
                # sort the partners by importance
                partner_order = sorted(
                    partners,
                    key=lambda p: importance_scores.get(p, 0),
                    reverse=True
                )
                for partner in partner_order:
                    # collect the values per scale value
                    total = sum(
                        scale_data.get(partner, 0)
                        for scale_data in data.values()
                        if isinstance(scale_data, dict)
                    )
                    if total == 0:
                        continue
                    parts = []
                    for scale_val, scale_data in data.items():
                        if isinstance(scale_data, dict):
                            count = scale_data.get(partner, 0)
                            if count > 0:
                                pct = count / total * 100
                                parts.append(f"{scale_val}: {pct:.0f}%")
                    lines.append(f"  {partner}: {', '.join(parts)}")
        elif not importance_scores:
            # flat structure as a fallback: {partner: score}
            lines.append("\n" + _p('summary.data.importance_plain', lang))
            for partner, score in sorted(data.items(), key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0)):
                if isinstance(score, (int, float)):
                    lines.append(f"  - {partner}: {score:.1f}")
    
    # give the scale values
    scale = metadata.get('scale', []) if metadata else []
    if scale:
        lines.append("\n" + _p('summary.data.scale', lang, scale=', '.join(str(s) for s in scale)))
    
    return "\n".join(lines)


def _build_data_section_generic(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Generic fallback builder for unknown handler types.
    
    Detects the data structure:
    - dict of dicts -> matrix/cross table
    - dict of int/float -> simple distribution
    """
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    metadata = result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})
    
    # convert DataFrame -> dict
    if hasattr(data, 'to_dict'):
        data = data.to_dict()
    
    if _is_data_empty(data) or n == 0:
        return ""
    
    # detect the structure
    first_val = next(iter(data.values()), None)
    
    if isinstance(first_val, dict):
        # matrix structure: {column: {row: value}} or {row: {column: value}};
        # 'scale' in the metadata -> a scale matrix
        scale = metadata.get('scale', []) if metadata else []
        
        if scale:
            lines = [_p('summary.data.rating_matrix', lang, n=n),
                     _p('summary.data.scale', lang, scale=', '.join(str(s) for s in scale))]
        else:
            lines = [_p('summary.data.matrix', lang, n=n)]
        
        # collect the items (inner keys)
        all_items = set()
        for col_data in data.values():
            if isinstance(col_data, dict):
                all_items.update(col_data.keys())
        
        lines.append(_p('summary.data.distribution', lang))
        for item in sorted(all_items):
            parts = []
            for col_label, col_data in data.items():
                if isinstance(col_data, dict):
                    count = col_data.get(item, 0)
                    pct = count / n * 100 if n > 0 else 0
                    parts.append(f"{col_label}: {pct:.1f}%")
            lines.append(f"  - {item}: {', '.join(parts)}")
        
        return "\n".join(lines)
    
    elif isinstance(first_val, (int, float)):
        # simple distribution
        lines = [_p('summary.data.generic_distribution', lang, n=n), _p('summary.data.results', lang)]
        for category, count in sorted(data.items(), key=lambda x: -x[1]):
            pct = count / n * 100 if n > 0 else 0
            lines.append(f"  - {category}: {count} ({pct:.1f}%)")
        return "\n".join(lines)
    
    else:
        # unknown structure: raw representation
        lines = [_p('summary.data.other', lang, n=n), _p('summary.data.results', lang)]
        for key, val in data.items():
            lines.append(f"  - {key}: {val}")
        return "\n".join(lines)


# handler type -> builder function
def _build_data_section_numeric_matrix(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Data section of numeric_matrix questions: statistics per field, constant sum if configured."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    meta = (result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})) or {}
    if _is_data_empty(data) or n == 0:
        return ""
    lines = [_p('summary.data.numeric_matrix', lang, n=n), _p('summary.data.numeric_matrix_fields', lang)]
    for label, st in data.items():
        lines.append(f"  - {label}: n={st.get('n')}, {st.get('mean')}, {st.get('median')}, {st.get('std')}, "
                     f"{st.get('min')}, {st.get('max')}")
    cs = meta.get('constant_sum')
    if cs:
        shares = ", ".join(f"{k} {v:.1f}" for k, v in (cs.get('mean_share_of_total') or {}).items())
        lines.append(_p('summary.data.constant_sum', lang, total=f"{cs['total']:g}",
                        share=f"{cs['share_matching']:.0f}", shares=shares))
    return "\n".join(lines)


def _scale_metrics_lines(result: Any, lang: str) -> List[str]:
    """Scale metrics for the data section of the summary prompt."""
    meta = (result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})) or {}
    lines = []
    if meta.get('nps'):
        n = meta['nps']
        lines.append(f"NPS={n['nps']} (promoters {n['promoters']}%, passives {n['passives']}%, "
                     f"detractors {n['detractors']}%, n={n['n']})")
    metrics = meta.get('scale_metrics')
    if not metrics:
        return lines
    rows = {"": metrics} if "n" in metrics else metrics
    lines.append(_p('summary.data.scale_metrics', lang))
    for label, m in rows.items():
        prefix = f"{label}: " if label else ""
        lines.append(f"  - {prefix}mean={m['mean']}, median={m['median']}, top{m['box']}={m['top_box']}%, "
                     f"bottom{m['box']}={m['bottom_box']}%, n={m['n']}, n_outside_scale={m['n_excluded']}")
    return lines


def _build_data_section_date(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
    """Data section of date questions: range, median and counts per period."""
    lang = lang or _src_lang()
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n = result.n if hasattr(result, 'n') else result.get('n', 0)
    meta = (result.metadata if hasattr(result, 'metadata') else result.get('metadata', {})) or {}
    if _is_data_empty(data) or n == 0:
        return ""
    lines = [_p('summary.data.date', lang, n=n, first=meta.get('first'), last=meta.get('last'),
                median=meta.get('median')), _p('summary.data.results', lang)]
    lines += [f"  - {k}: {v}" for k, v in data.items()]
    return "\n".join(lines)


def _with_scale_metrics(builder):
    def build(result: Any, qconfig: dict, lang: Optional[str] = None) -> str:
        text = builder(result, qconfig, lang)
        extra = _scale_metrics_lines(result, lang or _src_lang())
        return "\n".join([text] + extra) if text and extra else text
    return build


DATA_SECTION_BUILDERS = {
    'date': _build_data_section_date,
    'numeric_matrix': _build_data_section_numeric_matrix,
    'single_choice': _with_scale_metrics(_build_data_section_single_choice),
    'multi_choice': _build_data_section_multi_choice,
    'multi_choice_binary': _build_data_section_multi_choice_binary,
    'matrix_likert': _with_scale_metrics(_build_data_section_matrix_likert),
    'ranking': _build_data_section_ranking,
    'freetext': _build_data_section_freetext,
    'cooperation_matrix': _build_data_section_cooperation_matrix,
}


# =============================================================================
# PREPARING THE SEGMENT DATA
# =============================================================================

def no_significant_differences_text(dimension_labels: List[str], lang: str) -> str:
    """
    Fixed sentence for "no significant differences by <dimensions>" (locales/).

    Used instead of an LLM text when segments were tested and none differs
    significantly; it has a fixed translation in every catalog language.
    """
    from utils.languages import text
    return text('summary.no_significant_differences', lang,
                dimensions=", ".join(label for label in dimension_labels if label))


def _build_segment_section(
    segment_significance: List[SegmentSignificance],
    lang: Optional[str] = None,
) -> str:
    """Segment part of the prompt: per dimension its state; for differences the checked facts."""
    lang = lang or _src_lang()
    if not segment_significance:
        return "\n" + _p('summary.segments.none', lang)
    if not any(s.state == "differs" for s in segment_significance):
        return "\n" + _p('summary.segments.none_significant', lang)
    from utils.correlation import finding_facts
    lines = ["\n" + _p('summary.segments.header', lang)]
    for seg in segment_significance:
        key = {"differs": "dimension_differs", "no_difference": "dimension_none",
               "not_testable": "dimension_not_testable"}[seg.state]
        lines.append(_p(f'summary.segments.{key}', lang, label=seg.segment_label))
        for finding in seg.findings[:3]:
            lines.extend(f"  - {fact}" for fact in finding_facts(finding, lang, lang))
    return "\n".join(lines)


def segment_fixed_text(segment_significance: List[SegmentSignificance], lang: str) -> str:
    """
    Fixed text for the segment section when no dimension differs: names the
    tested dimensions without differences and those that could not be tested.
    Empty if a dimension differs (then the LLM writes the section).
    """
    from utils.languages import text
    if not segment_significance or any(s.state == "differs" for s in segment_significance):
        return ""
    none = [s.segment_label for s in segment_significance if s.state == "no_difference"]
    untested = [s.segment_label for s in segment_significance if s.state == "not_testable"]
    parts = []
    if none:
        parts.append(no_significant_differences_text(none, lang))
    if untested:
        parts.append(text('summary.segments_not_testable', lang, dimensions=", ".join(untested)))
    return " ".join(parts)


# =============================================================================
# GENERATING THE SUMMARY
# =============================================================================

def generate_question_summary(
    question_id: str,
    question_text: str,
    handler_type: str,
    result: Any,
    qconfig: dict,
    segment_significance: List[SegmentSignificance] = None,
    survey_context: str = "",
    llm_client=None,
    schema: Optional[SummarySchema] = None,
    llm_context: Optional[Any] = None,
    lang: Optional[str] = None,
) -> QuestionSummary:
    """
    Generates an LLM summary of a question.
    
    The summary is written in the source language of the survey; the other
    languages come from translate_summary().
    
    Args:
        question_id: question id
        question_text: full question text
        handler_type: handler type
        result: analysis result
        qconfig: question configuration
        segment_significance: list of significance test results
        survey_context: optional text context of the survey (title, year, topic)
        llm_client: LLM client
        schema: optional SummarySchema; if None, the default schema with three
            sections. In new code always get it from the resolver.
        llm_context: settings.llm_context (text or dict with
            domain/region/audience); None = from the loaded survey.
        lang: source language; None = that of the loaded survey.
    
    Returns:
        QuestionSummary with the sections of the schema.
    """
    import re
    from utils.languages import localized

    if llm_client is None:
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
    lang = lang or _src_lang()
    
    # determine the schema; the default is the default schema with three sections
    if schema is None:
        schema = SummarySchema(
            schema_id='default_three_sections',
            handler_type=handler_type,
            sections=list(DEFAULT_THREE_SECTIONS_SCHEMA.sections),
        )
    
    # 1. build the data section
    builder = DATA_SECTION_BUILDERS.get(handler_type)
    if not builder:
        logger.info(f"No specific builder for the handler type '{handler_type}', using the generic fallback")
        builder = _build_data_section_generic
    
    data_section = builder(result, qconfig, lang)
    if not data_section:
        logger.warning(f"No data for the summary of {question_id}")
        return QuestionSummary(question_id=question_id, lang=lang, schema_id=schema.schema_id)
    
    # 2. build the segment section
    segment_section = _build_segment_section(segment_significance or [], lang)
    
    # 3. context from the configuration
    help_text = localized(qconfig.get('help') or {}, lang, lang) or ''
    context_parts = []
    if survey_context:
        context_parts.append(_p('summary.context_line', lang, text=survey_context))
    if help_text:
        context_parts.append(_p('summary.help_line', lang, text=help_text))
    
    # 4. determine the applicable sections (conditional filter)
    has_significant = any(s.state == "differs" for s in (segment_significance or []))
    has_segmentation = bool(segment_significance)
    data = result.data if hasattr(result, 'data') else result.get('data', {})
    n_categories = len(data) if isinstance(data, dict) else 0
    
    section_context = {
        'has_segmentation': has_segmentation,
        'has_segmentation_significant': has_significant,
        'n_categories': n_categories,
        'has_clusters': bool(getattr(result, 'clusters', None)),
        'n': result.n if hasattr(result, 'n') else result.get('n', 0),
    }
    applicable_sections = schema.applicable_sections(section_context)
    applicable_ids = [s.id for s in applicable_sections]
    
    # Segments were tested but none differs significantly: the standard
    # segment section gets a fixed sentence instead of an LLM text, so it
    # names the tested dimensions and has a fixed translation.
    fixed_sections = {}
    if 'segments_text' in applicable_ids and has_segmentation and not has_significant:
        fixed = segment_fixed_text(segment_significance, lang)
        if fixed:
            fixed_sections['segments_text'] = fixed
    
    # 5. assemble the prompt parts
    instruction_lines = []
    for s in applicable_sections:
        if s.id in fixed_sections:
            continue
        length = _length_hint_text(s.length_hint, lang)
        instruction_lines.append(_p(
            'summary.section_instruction', lang, id=s.id, instruction=s.instruction(lang),
            length=_p('summary.length_format', lang, length=length) if length else ''))
    
    # sections the LLM leaves empty (condition not met, or a fixed text)
    omitted = [s.id for s in schema.sections if s.id not in applicable_ids or s.id in fixed_sections]
    if omitted:
        instruction_lines.append("\n" + _p('summary.omitted_note', lang, ids=", ".join(omitted)))
    
    # complexity hint by the number of categories (not for numeric questions)
    complexity_hint = ""
    if n_categories and handler_type != 'numeric':
        level = 'few' if n_categories <= 3 else ('several' if n_categories <= 6 else 'many')
        complexity_hint = _p(f'summary.complexity.{level}', lang)
    
    prompt = _p(
        'summary.user', lang,
        question=question_text,
        context_lines="\n".join(context_parts),
        data_section=data_section,
        complexity_hint=complexity_hint,
        segment_section=segment_section.strip("\n"),
        section_instructions="\n".join(instruction_lines).strip("\n"),
    )
    prompt = re.sub(r"\n{3,}", "\n\n", prompt)
    
    # 6. System-Prompt
    system_prompt = build_system_prompt(schema, llm_context, lang)
    
    # 7. LLM-Call
    try:
        response = llm_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.5,
            max_tokens=2000
        )
        
        # parse the JSON, dynamically by the schema ids
        expected_ids = schema.section_ids()  # all of them, including optional/conditional ones
        parsed = _parse_summary_response(response, expected_ids)
        
        # fill the QuestionSummary with all sections of the schema
        # (empty values too, so the structure is clear)
        sections = {sid: fixed_sections.get(sid) or parsed.get(sid, '') for sid in expected_ids}
        return QuestionSummary(
            question_id=question_id,
            lang=lang,
            sections=sections,
            schema_id=schema.schema_id,
        )
        
    except Exception as e:
        logger.error(f"LLM summary failed for {question_id}: {e}")
        return QuestionSummary(
            question_id=question_id, lang=lang,
            schema_id=schema.schema_id,
        )

def _parse_summary_response(response: str,
                             expected_ids: Optional[List[str]] = None) -> dict:
    """
    Parses the LLM answer as JSON.
    
    Args:
        response: raw LLM answer.
        expected_ids: list of the expected section ids; if None, the section
            ids of the default schema ['description', 'interpretation', 'segments_text'].
    
    Returns:
        dict {section_id: text}; missing sections are filled with ''.
    """
    if expected_ids is None:
        expected_ids = ['description', 'interpretation', 'segments_text']
    
    # clean up
    text = response.strip()
    
    # remove Markdown code blocks
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    
    text = text.strip()
    
    def _extract_expected(parsed_obj: dict) -> dict:
        return {sid: (parsed_obj.get(sid) or '') for sid in expected_ids}
    
    try:
        parsed = json.loads(text)
        return _extract_expected(parsed)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error: {e}, response: {text[:200]}")
        
        # fallback: try to extract the JSON from the text:
        # greedy match of the outermost {...} pair with balanced braces
        import re
        # first try the largest {...} match (DOTALL regex)
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return _extract_expected(parsed)
            except json.JSONDecodeError:
                pass
        
        return {sid: '' for sid in expected_ids}


# =============================================================================
# TRANSLATION
# =============================================================================

def translate_summary(
    summary: QuestionSummary,
    target_lang: str,
    llm_client=None
) -> QuestionSummary:
    """
    Translates a summary into another language.
    
    Translates all non-empty sections of the question in one call of the
    TranslationService (one LLM request per question and language instead of
    one per section); works with any schema.
    
    Args:
        summary: summary in the source language.
        target_lang: target language.
        llm_client: LLM client.
    
    Returns:
        translated QuestionSummary with the same schema_id.
    """
    if target_lang == summary.lang:
        return summary
    
    if llm_client is None:
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
    
    translated = QuestionSummary(
        question_id=summary.question_id,
        lang=target_lang,
        schema_id=summary.schema_id,
    )
    
    try:
        config, db = _app_config_and_db()
        pending = []   # (section id, text): all sections of the question in one call
        for section_id, source_text in summary.sections.items():
            if not source_text or not source_text.strip():
                # take empty sections over as they are
                translated.sections[section_id] = source_text or ''
                continue
            # special case: the fixed sentence "no significant differences" has
            # translations in locales/, no LLM call needed
            if section_id == 'segments_text':
                fixed = _translate_no_differences(source_text.strip(), summary.lang, target_lang, config)
                if fixed:
                    translated.sections[section_id] = fixed
                    continue
            translated.sections[section_id] = source_text   # replaced below
            pending.append((section_id, source_text))
        if pending:
            from utils.translation_service import TranslationService, context_for_question
            texts = TranslationService(llm_client, config, db).translate_many(
                [t for _, t in pending], target_lang,
                context_for_question(config, summary.question_id, 'report'), source_lang=summary.lang)
            for (section_id, _), text in zip(pending, texts):
                translated.sections[section_id] = text
    
    except Exception as e:
        logger.error(f"Translation failed for {summary.question_id} → {target_lang}: {e}")
    
    return translated


def _translate_no_differences(source_text: str, source_lang: str, target_lang: str,
                              config: Optional[dict]) -> Optional[str]:
    """
    The fixed "no significant differences" sentence in ``target_lang``, if
    ``source_text`` is that sentence and the target language has a catalog;
    otherwise None (the text then goes through the normal translation).
    """
    from utils.languages import available_catalogs, text
    if target_lang not in available_catalogs():
        return None
    marker = "\x00"
    prefix, _, suffix = text('summary.no_significant_differences', source_lang,
                             dimensions=marker).partition(marker)
    if not (source_text.startswith(prefix) and source_text.endswith(suffix)):
        return None
    middle = source_text[len(prefix):len(source_text) - len(suffix)]
    try:
        from utils.segment_resolver import SegmentResolver
        dims = SegmentResolver(config or {}).list_dimensions()
    except Exception as e:  # configuration errors are reported when the survey is loaded
        logger.debug(f"Fixed segment sentence not assignable ({e}); normal translation")
        return None
    by_label = {d.get_label(source_lang): d for d in dims}
    labels = middle.split(", ")
    if not all(label in by_label for label in labels):
        return None
    return no_significant_differences_text(
        [by_label[label].get_label(target_lang) for label in labels], target_lang)


# =============================================================================
# SIGNIFICANCE TESTS FOR THE SEGMENTS
# =============================================================================

def compute_segment_significance(
    question_id: str,
    handler_type: str,
    qconfig: dict,
    df: pd.DataFrame,
    config: dict,
    cluster_memberships: Optional[dict] = None,
) -> List[SegmentSignificance]:
    """
    Segment differences of a question, one entry per dimension (method see
    utils/segment_tests.py: per option/item, one family per question, BH).
    For a free-text question the topics are tested through
    ``cluster_memberships`` (utils/analysis_variables.cluster_memberships_from_results);
    the differences between groups (utils/segment_findings.py) use the same tests.
    """
    from utils.segment_tests import run_segment_tests
    try:
        tests = run_segment_tests(config, df, question_id, cluster_memberships)
    except (ValueError, KeyError) as e:
        logger.warning(f"[segment significance] {question_id}: segmentation not evaluable: {e}")
        return []
    src = get_source_language(config)
    out = []
    for dim in tests.dimensions:
        found = dim.significant()
        best = found[0] if found else None
        testable = [r for r in dim.results if r.testable and r.q_value is not None]
        out.append(SegmentSignificance(
            segment_type=dim.dimension_id,
            segment_label=str(localized(dim.label, src, src) or dim.dimension_id),
            is_significant=bool(found),
            p_value=min((r.q_value for r in testable), default=float('nan')),
            cramers_v=abs(best.effect) if best else 0.0,
            effect_desc=best.strength if best else "negligible",
            n_per_segment=dim.n_per_segment,
            state=dim.state,
            findings=found,
            effect_measure=(best.effect_measure if best else "cramers_v") or "cramers_v",
        ))
    return out


# =============================================================================
# DB-CACHE
# =============================================================================

def save_summary_to_db(db, import_id: int, summary: QuestionSummary):
    """Stores a summary (schema and sections as JSON)."""
    try:
        with db._connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO question_summaries
                (import_id, question_id, lang, schema_id, sections_json)
                VALUES (?, ?, ?, ?, ?)
            """, (import_id, summary.question_id, summary.lang, summary.schema_id or '',
                  json.dumps(summary.sections, ensure_ascii=False)))
    except Exception as e:
        logger.warning(f"Storing the summary in the cache failed: {e}")


def load_summary_from_db(db, import_id: int, question_id: str, lang: str) -> Optional[QuestionSummary]:
    """Loads a summary; None if there is none or its sections cannot be read."""
    try:
        with db._connection() as conn:
            row = conn.execute("""
                SELECT schema_id, sections_json FROM question_summaries
                WHERE import_id = ? AND question_id = ? AND lang = ?
            """, (import_id, question_id, lang)).fetchone()
    except Exception as e:
        logger.warning(f"Reading the summary cache failed: {e}")
        return None
    if not row or not row[1]:
        return None
    try:
        sections = json.loads(row[1]) or {}
    except json.JSONDecodeError:
        logger.warning(f"sections_json corrupt for {question_id}/{lang}")
        return None
    return QuestionSummary(question_id=question_id, lang=lang, sections=sections,
                           schema_id=row[0] or 'default_three_sections')

def invalidate_summaries(db, import_id: int, question_id: str = None):
    """Invalidates cached summaries."""
    try:
        with db._connection() as conn:
            cursor = conn.cursor()
            if question_id:
                cursor.execute(
                    "DELETE FROM question_summaries WHERE import_id = ? AND question_id = ?",
                    (import_id, question_id)
                )
            else:
                cursor.execute(
                    "DELETE FROM question_summaries WHERE import_id = ?",
                    (import_id,)
                )
    except Exception as e:
        logger.debug(f"Summary invalidation failed: {e}")


# =============================================================================
# MAIN FUNCTION: GENERATING ALL SUMMARIES
# =============================================================================

def generate_all_summaries(
    config: dict,
    analysis_results: dict,
    df,
    db=None,
    import_id: int = None,
    languages: List[str] = None,
    progress_callback=None,
    llm_client=None,
    survey_context: str = "",
    handler_registry: Optional[Dict[str, type]] = None,
    schema_resolver: Optional[SchemaResolver] = None,
) -> Dict[str, Dict[str, QuestionSummary]]:
    """
    Generates summaries for all questions.
    
    Args:
        config: complete configuration (questions.yaml).
        analysis_results: {qid: AnalysisResult}.
        df: DataFrame with the survey data.
        db: database object (for the cache).
        import_id: import id (for the cache).
        languages: languages (default: the output languages of the survey).
        progress_callback: callback(current, total, message).
        llm_client: LLM client.
        survey_context: optional text context (e.g. "Student survey 2026").
        handler_registry: {handler_type: HandlerClass} for resolving the schema;
            if None, an internal default mapping is used.
        schema_resolver: pre-instantiated SchemaResolver; if None, one is
            built from config + handler_registry.
    
    Returns:
        {qid: {lang: QuestionSummary}}.
    """
    from utils import prompts
    from utils.analysis_variables import cluster_memberships_from_results
    from utils.languages import get_output_languages, get_source_language, localized
    src = get_source_language(config)
    if languages is None:
        languages = get_output_languages(config)
    cluster_memberships = cluster_memberships_from_results(analysis_results or {}, config)
    
    if llm_client is None:
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
    
    # handler registry: resolve lazily if not given
    if handler_registry is None:
        handler_registry = _default_handler_registry()
    
    # schema resolver: pre-instantiated or built anew
    if schema_resolver is None:
        schema_resolver = SchemaResolver(
            survey_config=config,
            handler_registry=handler_registry,
        )
    
    # LLM context: settings.llm_context (other places are reported by the configuration check)
    llm_context = prompts.context_text(config, src)
    
    question_groups = config.get('question_groups', {})
    all_summaries = {}
    
    total = len(question_groups)
    current = 0
    
    for qid, qconfig in question_groups.items():
        current += 1
        handler_type = qconfig.get('handler', 'single_choice')
        
        if qid not in analysis_results:
            continue
        
        result = analysis_results[qid]
        if result is None:
            continue
        
        if progress_callback:
            progress_callback(current, total, f"summary: {qid}")
        
        all_summaries[qid] = {}
        
        # 1. check the cache for ALL languages
        all_cached = True
        for lang in languages:
            if db and import_id:
                cached = load_summary_from_db(db, import_id, qid, lang)
                if cached and cached.has_content():
                    all_summaries[qid][lang] = cached
                else:
                    all_cached = False
                    break
            else:
                all_cached = False
                break
        
        if all_cached:
            logger.debug(f"Summary of {qid}: all languages from the cache")
            continue
        
        # 2. resolve the schema of this question
        schema = schema_resolver.resolve(qid, handler_type)
        
        # 3. compute the segment significance tests
        segment_sig = []
        try:
            # Free text: the topics (global clusters) are tested per group, as in
            # utils/segment_findings.py; without clusters there is nothing to test.
            memberships = cluster_memberships if handler_type == 'freetext' else None
            if handler_type != 'freetext' or qid in memberships:
                segment_sig = compute_segment_significance(
                    qid, handler_type, qconfig, df, config, memberships
                )
        except Exception as e:
            logger.warning(f"Segment significance of {qid} failed: {e}")
        
        # 4. generate the summary in the source language
        question_text = (localized(qconfig.get('full_question') or {}, src, src)
                         or localized(qconfig.get('label') or {}, src, src) or qid)
        
        try:
            summary_src = generate_question_summary(
                question_id=qid,
                question_text=question_text,
                handler_type=handler_type,
                result=result,
                qconfig=qconfig,
                segment_significance=segment_sig,
                survey_context=survey_context,
                llm_client=llm_client,
                schema=schema,
                llm_context=llm_context,
                lang=src,
            )
            all_summaries[qid][src] = summary_src
            
            # cache in the database
            if db and import_id:
                save_summary_to_db(db, import_id, summary_src)
                
        except Exception as e:
            logger.error(f"Summary of {qid} failed: {e}")
            continue
        
        # 5. translate
        for lang in languages:
            if lang == src:
                continue
            
            try:
                summary_translated = translate_summary(summary_src, lang, llm_client)
                all_summaries[qid][lang] = summary_translated
                
                # cache in the database
                if db and import_id:
                    save_summary_to_db(db, import_id, summary_translated)
                    
            except Exception as e:
                logger.warning(f"Translation {qid} → {lang} failed: {e}")
    
    logger.info(f"Summaries generated: {len(all_summaries)} questions")
    return all_summaries


def _default_handler_registry() -> Dict[str, type]:
    """Lazy loader of the default handler mapping (avoids circular imports)."""
    try:
        from handlers.single_choice       import SingleChoiceHandler
        from handlers.multi_choice        import MultiChoiceHandler
        from handlers.multi_choice_binary import MultiChoiceBinaryHandler
        from handlers.freetext            import FreetextHandler
        from handlers.cooperation_matrix  import CooperationMatrixHandler
        from handlers.matrix_likert       import MatrixLikertHandler
        from handlers.ranking             import RankingHandler
        return {
            'single_choice':       SingleChoiceHandler,
            'multi_choice':        MultiChoiceHandler,
            'multi_choice_binary': MultiChoiceBinaryHandler,
            'freetext':            FreetextHandler,
            'cooperation_matrix':  CooperationMatrixHandler,
            'matrix_likert':       MatrixLikertHandler,
            'ranking':             RankingHandler,
        }
    except ImportError as e:
        logger.warning(f"Handler registry not fully resolvable: {e}")
        return {}


def _app_config_and_db():
    """Configuration and database of the running app, or (None, None) without one."""
    try:
        from core import state
        return state.config, state.db
    except Exception:
        return None, None
