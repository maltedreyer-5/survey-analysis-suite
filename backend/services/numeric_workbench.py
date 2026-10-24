# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: Workbench operations for numeric questions.

Functions behind the numeric clean-up sub-tab of the Workbench:

1. list_numeric_questions(state): all numeric questions with label, column, range
2. get_numeric_overview(state, qid): statistics and invalid values of a question
3. update_valid_range(state, qid, min, max): writes a new range into questions.yaml
   through config_io (with a snapshot)

It deliberately uses the same helper as the NumericHandler
(`utils.numeric_coerce.coerce_series`), so the Workbench behaves like the analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


# =============================================================================
# Data models
# =============================================================================

@dataclass
class InvalidValue:
    """An invalid or out-of-range raw answer of a numeric question."""
    row_index: int          # df.index of this answer
    raw_value: str            # raw text as entered
    parsed_value: Optional[float]  # after coercion; None if not parsable
    status: str               # 'non_numeric' | 'out_of_range_low' | 'out_of_range_high'
    
    def status_label(self) -> str:
        labels = {
            'non_numeric': '❌ not numeric',
            'out_of_range_low': '⚠️ below min',
            'out_of_range_high': '⚠️ above max',
        }
        return labels.get(self.status, self.status)


@dataclass
class NumericQuestionInfo:
    """Compact description of a numeric question for the dropdown."""
    qid: str
    label: str
    column: str
    unit: str
    min_value: Optional[float]
    max_value: Optional[float]
    
    def display_choice(self, lang: str = 'de') -> str:
        """Text for the dropdown: '<qid> — <label> (<unit>, <range>)'."""
        unit_part = f" [{self.unit}]" if self.unit else ""
        if self.min_value is not None and self.max_value is not None:
            range_part = f" ({self.min_value}–{self.max_value})"
        else:
            range_part = " (range not set)"
        return f"{self.qid} — {self.label}{unit_part}{range_part}"


@dataclass
class NumericOverview:
    """Complete information on a numeric question for the clean-up tab."""
    qid: str
    label: str
    column: str
    unit: str
    min_value: Optional[float]
    max_value: Optional[float]
    n_total: int
    n_valid: int
    n_invalid: int
    invalid_values: List[InvalidValue] = field(default_factory=list)
    error: Optional[str] = None  # if the question cannot be analysed at all


# =============================================================================
# 1. List of the numeric questions
# =============================================================================

def list_numeric_questions(state, lang: str = 'de') -> List[NumericQuestionInfo]:
    """
    Lists all questions with handler == 'numeric' of the active configuration.
    """
    if state is None or state.config is None:
        return []
    
    qg = state.config.get('question_groups', {}) or {}
    result = []
    for qid, qcfg in qg.items():
        if qcfg.get('handler') != 'numeric':
            continue
        
        label = ''
        lbl = qcfg.get('label')
        if isinstance(lbl, dict):
            label = _src_text(lbl, lang, qid) or qid
        elif isinstance(lbl, str):
            label = lbl
        else:
            label = qid
        
        columns = qcfg.get('columns', []) or []
        column = columns[0] if columns else ''
        
        numeric_cfg = qcfg.get('numeric', {}) or {}
        unit = numeric_cfg.get('unit', '')
        valid_range = numeric_cfg.get('valid_range', {}) or {}
        v_min = valid_range.get('min')
        v_max = valid_range.get('max')
        
        result.append(NumericQuestionInfo(
            qid=qid,
            label=label,
            column=column,
            unit=unit,
            min_value=v_min,
            max_value=v_max,
        ))
    
    return result


def find_question_by_choice(state, choice: str, lang: str = 'de'
                              ) -> Optional[NumericQuestionInfo]:
    """
    Finds a numeric question from a dropdown choice string.
    
    Accepts both the full display text and just the qid.
    """
    if not choice:
        return None
    
    qid = choice.split(" — ")[0].strip() if " — " in choice else choice.strip()
    
    for info in list_numeric_questions(state, lang=lang):
        if info.qid == qid:
            return info
    return None


# =============================================================================
# 2. Detail view of a question
# =============================================================================

def get_numeric_overview(state, qid: str, lang: str = 'de'
                           ) -> Optional[NumericOverview]:
    """
    A complete overview of a numeric question.
    
    Args:
        state: AppState with df and config.
        qid: question id.
        lang: language of the labels.
    
    Returns:
        NumericOverview, or None if the question does not exist or is
        not numeric.
    """
    if state is None or state.config is None:
        return None
    
    qg = state.config.get('question_groups', {}) or {}
    qcfg = qg.get(qid)
    if qcfg is None or qcfg.get('handler') != 'numeric':
        return None
    
    info = next(
        (i for i in list_numeric_questions(state, lang=lang) if i.qid == qid),
        None,
    )
    if info is None:
        return None
    
    overview = NumericOverview(
        qid=info.qid,
        label=info.label,
        column=info.column,
        unit=info.unit,
        min_value=info.min_value,
        max_value=info.max_value,
        n_total=0,
        n_valid=0,
        n_invalid=0,
    )
    
    if state.df is None:
        overview.error = "No data imported"
        return overview
    
    if not info.column:
        overview.error = "No column configured"
        return overview
    
    if info.column not in state.df.columns:
        overview.error = f"Column '{info.column}' not in the data"
        return overview
    
    # lazy import: utils.numeric_coerce is used here, but the service must
    # not crash without the module
    try:
        from utils.numeric_coerce import coerce_series
    except ImportError as e:
        overview.error = f"numeric_coerce not available: {e}"
        return overview
    
    raw_series = state.df[info.column]
    coerced = coerce_series(raw_series)
    
    overview.n_total = len(raw_series)
    
    # check per row whether it is valid
    invalid_list: List[InvalidValue] = []
    valid_count = 0
    
    for idx in raw_series.index:
        raw = raw_series[idx]
        # NaN inputs (real missing values) do NOT count as invalid,
        # as in the handler
        if _is_missing(raw):
            continue
        
        parsed = coerced[idx]
        # 'NaN after coercion' counts as not numeric
        try:
            import math
            is_parsed_nan = parsed is None or (
                isinstance(parsed, float) and math.isnan(parsed)
            )
        except Exception:
            is_parsed_nan = parsed is None
        
        if is_parsed_nan:
            invalid_list.append(InvalidValue(
                row_index=int(idx) if not isinstance(idx, int) else idx,
                raw_value=str(raw),
                parsed_value=None,
                status='non_numeric',
            ))
            continue
        
        # Range-Pruefung
        if info.min_value is not None and parsed < info.min_value:
            invalid_list.append(InvalidValue(
                row_index=int(idx) if not isinstance(idx, int) else idx,
                raw_value=str(raw),
                parsed_value=float(parsed),
                status='out_of_range_low',
            ))
            continue
        
        if info.max_value is not None and parsed > info.max_value:
            invalid_list.append(InvalidValue(
                row_index=int(idx) if not isinstance(idx, int) else idx,
                raw_value=str(raw),
                parsed_value=float(parsed),
                status='out_of_range_high',
            ))
            continue
        
        valid_count += 1
    
    overview.n_valid = valid_count
    overview.n_invalid = len(invalid_list)
    overview.invalid_values = invalid_list
    return overview


def _is_missing(val) -> bool:
    """Whether a value counts as 'missing' (NaN/None/empty)."""
    if val is None:
        return True
    try:
        import math
        if isinstance(val, float) and math.isnan(val):
            return True
    except Exception:
        pass
    if isinstance(val, str) and val.strip() == '':
        return True
    return False


# =============================================================================
# 3. Updating the range
# =============================================================================

def update_valid_range(
    state,
    qid: str,
    min_value: Optional[float],
    max_value: Optional[float],
) -> Tuple[bool, str]:
    """
    Writes a new valid_range into the questions.yaml.
    
    Uses config_io for the snapshot.
    
    Args:
        state: AppState (the configuration is reloaded with load_config
            after a successful write).
        qid: question id.
        min_value: new minimum, or None for no limit.
        max_value: new maximum, or None for no limit.
    
    Returns:
        (success, status_text)
    """
    # load the configuration through config_io (not from state: we work on
    # the file version, which is written back)
    try:
        from services.config_io import (
            load_active_config, write_active_config_with_snapshot,
        )
    except ImportError as e:
        return False, f"config_io not available: {e}"
    
    try:
        config = load_active_config()
    except FileNotFoundError as e:
        return False, f"Configuration file not found: {e}"
    except Exception as e:
        logger.exception("Error loading the configuration")
        return False, f"Error reading the configuration: {e}"
    
    qg = config.get('question_groups', {}) or {}
    qcfg = qg.get(qid)
    if qcfg is None:
        return False, f"Question '{qid}' is not in the configuration"
    if qcfg.get('handler') != 'numeric':
        return False, f"Question '{qid}' does not use the numeric handler"
    
    # make sure the numeric block exists
    numeric_cfg = qcfg.setdefault('numeric', {})
    valid_range = numeric_cfg.setdefault('valid_range', {})
    
    if min_value is None:
        valid_range.pop('min', None)
    else:
        valid_range['min'] = float(min_value)
    
    if max_value is None:
        valid_range.pop('max', None)
    else:
        valid_range['max'] = float(max_value)
    
    # if both are gone, remove the valid_range block entirely
    if not valid_range:
        numeric_cfg.pop('valid_range', None)
    
    # Plausibilitaet
    if (min_value is not None and max_value is not None
            and min_value >= max_value):
        return False, (
            f"Min ({min_value}) must be smaller than max ({max_value})."
        )
    
    # write with a snapshot
    try:
        write_active_config_with_snapshot(
            config, reason=f'numeric_range_{qid}'
        )
    except Exception as e:
        logger.exception("Error writing the configuration")
        return False, f"Write error: {e}"
    
    # reload the state, so the change is visible at once
    if state is not None:
        try:
            from core import load_config as core_load_config
            core_load_config()
        except Exception as e:
            logger.warning(f"State reload failed: {e}")
    
    return True, (
        f"✅ Range of '{qid}' updated: "
        f"min={min_value if min_value is not None else 'no limit'}, "
        f"max={max_value if max_value is not None else 'no limit'}. "
        f"A snapshot of the previous configuration was taken."
    )


# =============================================================================
# 4. Helpers for the interface
# =============================================================================

def render_overview_summary(overview: NumericOverview) -> str:
    """
    Renders the statistics summary as a Markdown string.
    
    Shown in the interface as gr.Markdown.
    """
    if overview is None:
        return "_No question selected._"
    
    if overview.error:
        return f"⚠️ {overview.error}"
    
    lines = [f"### {overview.label}"]
    lines.append(f"_Column: `{overview.column}`_  |  _Question ID: `{overview.qid}`_")
    lines.append("")
    
    range_str = ""
    if overview.min_value is not None and overview.max_value is not None:
        range_str = f"{overview.min_value} – {overview.max_value}"
    elif overview.min_value is not None:
        range_str = f"≥ {overview.min_value}"
    elif overview.max_value is not None:
        range_str = f"≤ {overview.max_value}"
    else:
        range_str = "_no range set_"
    
    unit_str = f" {overview.unit}" if overview.unit else ""
    
    lines.append(f"**Current range:** {range_str}{unit_str}")
    lines.append("")
    lines.append(
        f"**Values:** {overview.n_total} total, "
        f"{overview.n_valid} valid, "
        f"{overview.n_invalid} invalid"
    )
    
    return "\n".join(lines)


def invalid_values_to_table_rows(overview: NumericOverview) -> List[List]:
    """
    Turns overview.invalid_values into a format gr.Dataframe understands.
    
    Columns: index (row_index) | raw value | parsed | status
    """
    if overview is None or not overview.invalid_values:
        return []
    
    rows = []
    for inv in overview.invalid_values:
        parsed_str = (f"{inv.parsed_value:.4f}"
                      if inv.parsed_value is not None else "—")
        rows.append([
            inv.row_index,
            inv.raw_value,
            parsed_str,
            inv.status_label(),
        ])
    return rows
