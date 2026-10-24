# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Validation of a survey configuration when it is loaded.

Errors make the configuration unusable for analysis (e.g. an unsupported
segmentation format); warnings point to settings that work but may not do
what was intended (e.g. an output language without a text catalog). Both
are kept on the application state so the UI can show them instead of
failing later in the middle of an analysis.
"""
from typing import List, Tuple

from utils.languages import LanguageConfigError, validate_language_settings
from utils.segment_resolver import SegmentResolver


def validate_survey_config(config: dict) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for a questions.yaml as written (named scales not yet expanded)."""
    errors: List[str] = []
    warnings: List[str] = []
    if not config:
        return ["The configuration is empty"], warnings
    try:
        warnings.extend(validate_language_settings(config))
    except LanguageConfigError as e:
        errors.append(str(e))
        return errors, warnings  # the segment check needs a valid source language
    # named scales: checked on the configuration as written, then expanded for the
    # remaining checks (they see the same structures as the analysis)
    from utils.scales import validate_scales, expand_named_scales
    scale_errors, scale_warnings = validate_scales(config)
    errors.extend(scale_errors)
    warnings.extend(scale_warnings)
    config = expand_named_scales(config)
    try:
        resolver = SegmentResolver(config)
        warnings.extend(resolver.validation_warnings())
    except (ValueError, KeyError, TypeError) as e:
        errors.append(f"Segmentation: {e}")
    settings = config.get("settings") or {}
    for place, value in (("settings.survey_metadata.llm_context", (settings.get("survey_metadata") or {}).get("llm_context")),
                         ("llm_context (top level)", config.get("llm_context"))):
        if value:
            warnings.append(f"{place} is not read; the context belongs in settings.llm_context")
    mode = (config.get("settings") or {}).get("matrix_segment_chart")
    if mode is not None and mode not in ("both", "small_multiples", "mean", "none"):
        warnings.append(f"settings.matrix_segment_chart '{mode}' unknown (both, small_multiples, mean, none); 'both' is used")
    question_errors, question_warnings = _validate_questions(config)
    errors.extend(question_errors)
    warnings.extend(question_warnings)
    return errors, warnings


# LimeSurvey types whose answers the existing handlers would evaluate wrongly
# (types 4-6); reported until the dedicated handlers exist.
# Type K (several number fields) is covered by the check of numeric questions
# with several columns.
_LSS_TYPES_NOT_SUPPORTED = {
    ":": "numeric matrix (would be analyzed as a Likert matrix)",
    ";": "text matrix (would be analyzed as a Likert matrix)",
    "D": "date (no handler)",
}


# New question types whose dashboard and Word display are not finished yet
# remove an entry once its display exists
_HANDLERS_WITHOUT_DISPLAY: dict = {}


# Handler that evaluates a LimeSurvey type correctly (then no hint)
_LSS_TYPE_HANDLER = {":": "numeric_matrix", ";": "freetext", "D": "date"}


def _validate_questions(config: dict) -> Tuple[List[str], List[str]]:
    """
    Questions the analysis would skip or evaluate wrongly without saying so:
    an unknown handler (the question is skipped), a numeric question with
    several columns (only the first is evaluated) and LimeSurvey types the
    handlers do not support yet.
    """
    from handlers import get_handler_info
    known = set(get_handler_info())
    errors: List[str] = []
    warnings: List[str] = []
    for qid, q in (config.get("question_groups") or {}).items():
        if not isinstance(q, dict):
            continue
        handler = q.get("handler") or q.get("type")
        if handler and handler not in known:
            errors.append(f"Question {qid}: unknown handler '{handler}' (the question would be skipped); "
                          f"known: {', '.join(sorted(known))}")
        if handler in _HANDLERS_WITHOUT_DISPLAY:
            warnings.append(f"Question {qid}: question type '{handler}' is analyzed but has no display of its own "
                            f"in the dashboard and the Word report yet ({_HANDLERS_WITHOUT_DISPLAY[handler]})")
        if q.get("ordinal") and handler == "single_choice" and not (q.get("scale_order") or q.get("scale_values")):
            order = q.get("sort_order") or q.get("options") or []
            if isinstance(order, dict):
                order = next(iter(order.values()), [])
            from utils.scale_metrics import direction, scale_points
            points = scale_points([str(o) for o in order], q)
            if len(points) >= 2 and direction(points, q) is None:
                warnings.append(f"Question {qid}: direction of the scale unclear; without scale_order (ascending/descending) "
                                f"no scale metrics (mean, top/bottom box)")
        if q.get("other_of") and q["other_of"] not in (config.get("question_groups") or {}):
            warnings.append(f"Question {qid}: other_of refers to the unknown question '{q['other_of']}'")
        mode = q.get("matrix_segment_chart")
        if mode is not None and mode not in ("both", "small_multiples", "mean", "none"):
            warnings.append(f"Question {qid}: matrix_segment_chart '{mode}' unknown (both, small_multiples, mean, none); "
                            f"'both' is used")
        if handler == "numeric" and len(q.get("columns") or []) > 1:
            errors.append(f"Question {qid}: numeric with {len(q['columns'])} columns; only "
                          f"'{q['columns'][0]}' would be analyzed. Create one question per column.")
        lss = q.get("lss_metadata") or {}
        lss_type = lss.get("type") or lss.get("lss_type")
        if lss_type in _LSS_TYPES_NOT_SUPPORTED and handler != _LSS_TYPE_HANDLER.get(lss_type):
            warnings.append(f"Question {qid}: LimeSurvey type '{lss_type}': "
                            f"{_LSS_TYPES_NOT_SUPPORTED[lss_type]}; check the results.")
    return errors, warnings
