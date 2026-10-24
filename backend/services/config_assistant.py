# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: configuration assistant with LLM suggestions.

Four suggestion functions that use the LLM to maintain the questions.yaml
of a survey:

1. suggest_llm_context(state) -> str
   creates/improves settings.llm_context.

2. suggest_segmentation_dimensions(state) -> List[Dict]
   finds questions suitable as segmentation dimensions.

3. suggest_numeric_fields(state) -> Dict[qid, Dict]
   finds numeric questions and suggests unit/valid_range.

4. suggest_schema_overrides(state) -> Dict[qid, Dict]
   suggests question-specific changes of the summary schema.

All functions return suggestions as plain data. The caller applies them to
the configuration with `apply_suggestions(...)`.

Behaviour:
- without an LLM, every suggestion function returns an empty list/dict with
  a reason in the status text.
- existing fields are NEVER overwritten unless the caller passes
  scope='all' (default is 'unconfigured').
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Prompts come from the catalog of the survey's source language
# (prompts/<lang>.yaml, section config_assistant); labels are read in that
# language as well, so a survey in English is described to the LLM in English.

def _src(state) -> str:
    from utils.languages import get_source_language
    return get_source_language(getattr(state, 'config', None))


def _p(state, key: str, **fields) -> str:
    from utils import prompts
    return prompts.prompt(f"config_assistant.{key}", _src(state), **fields)


def _text(state, value) -> str:
    """A multilingual configuration text in the source language."""
    from utils.languages import localized
    if isinstance(value, dict):
        src = _src(state)
        return str(localized(value, src, src) or '')
    return str(value or '')


def _option_count(qcfg: dict) -> int:
    """Number of answer options (``options`` is a list or a dict per language)."""
    options = qcfg.get('options') or []
    if isinstance(options, dict):
        options = next((v for v in options.values() if v), [])
    return len(options)


# =============================================================================
# Data model of the suggestions
# =============================================================================

@dataclass
class Suggestion:
    """
    A single suggestion of the assistant.
    
    Attributes:
        category: 'llm_context' | 'segmentation' | 'numeric' | 'schema'
        target: path in the configuration, e.g. 'settings.llm_context' or
                'question_groups.<qid>.numeric'
        action: 'add' | 'replace' | 'remove'
        new_value: suggested value (type depends on the category)
        reason: reason written by the LLM
        confidence: 0..1, how sure the suggestion is (self-assessment of the LLM)
    """
    category: str
    target: str
    action: str
    new_value: Any
    reason: str = ''
    confidence: float = 0.5


# =============================================================================
# Helpers: active survey + LLM
# =============================================================================

def _get_llm():
    """Gets the LLM client and checks the connection; returns a (client, ok) tuple."""
    from utils.llm import get_llm_client
    client = get_llm_client()
    ok, _msg = client.test_connection()
    return client, ok


def _get_question_groups(state) -> dict:
    """state.config.question_groups or an empty dict."""
    if state is None or state.config is None:
        return {}
    return state.config.get('question_groups', {}) or {}


def _get_settings(state) -> dict:
    """state.config.settings or an empty dict."""
    if state is None or state.config is None:
        return {}
    return state.config.get('settings', {}) or {}


def _is_configured(qcfg: dict, category: str) -> bool:
    """
    Checks whether a question is already configured for a category.
    
    Used by scope='unconfigured', so nothing is overwritten.
    """
    if category == 'numeric':
        return bool(qcfg.get('numeric')) and qcfg.get('handler') == 'numeric'
    if category == 'schema':
        return bool(qcfg.get('summary_schema_override'))
    return False


# =============================================================================
# 1. LLM-Context
# =============================================================================



def suggest_llm_context(state, max_questions: int = 15) -> Optional[Suggestion]:
    """
    Suggests settings.llm_context.
    
    Args:
        state: AppState with a loaded configuration.
        max_questions: how many questions the LLM gets as context.
    
    Returns:
        a Suggestion, or None on errors or without an LLM.
    """
    client, ok = _get_llm()
    if not ok:
        return None
    
    question_groups = _get_question_groups(state)
    if not question_groups:
        return None
    
    # collect example questions (label, and help text if any)
    samples = []
    for i, (qid, qcfg) in enumerate(question_groups.items()):
        if i >= max_questions:
            break
        label = _text(state, qcfg.get('label', ''))
        help_text = _text(state, qcfg.get('help', '')) if qcfg.get('help') else ''
        line = f"- {qid}: {label}"
        if help_text:
            line += f"  ({help_text})"
        samples.append(line)
    
    prompt = _p(state, 'llm_context', questions='\n'.join(samples))
    
    try:
        response = client.complete(
            prompt=prompt,
            system_prompt=_p(state, 'system'),
            max_tokens=500,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning(f"LLM call for llm_context failed: {e}")
        return None
    
    text = response.strip()
    if not text:
        return None
    
    existing = _get_settings(state).get('llm_context', '')
    action = 'replace' if existing else 'add'
    
    return Suggestion(
        category='llm_context',
        target='settings.llm_context',
        action=action,
        new_value=text,
        reason="Created from the example answers of the survey.",
        confidence=0.7,
    )


# =============================================================================
# 2. Segmentation dimensions
# =============================================================================



def suggest_segmentation_dimensions(state) -> List[Suggestion]:
    """
    Suggests segmentation dimensions.
    
    Returns:
        list of Suggestions (one per suggested dimension).
    """
    client, ok = _get_llm()
    if not ok:
        return []
    
    question_groups = _get_question_groups(state)
    if not question_groups:
        return []
    
    # collect the questions with handler type and answer options
    samples = []
    for qid, qcfg in question_groups.items():
        label = _text(state, qcfg.get('label', ''))
        handler = qcfg.get('handler', 'unknown')
        option_count = _option_count(qcfg)
        line = f"- {qid}: {label} (handler={handler}"
        if option_count:
            line += ", " + _p(state, 'options_count', count=option_count)
        line += ")"
        samples.append(line)
    
    prompt = _p(state, 'segmentation', questions='\n'.join(samples))
    
    try:
        result = client.complete_json(
            prompt=prompt,
            system_prompt=_p(state, 'system_json'),
            max_tokens=1500,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"LLM call for the segmentation failed: {e}")
        return []
    
    if not isinstance(result, list):
        logger.warning(f"Expected a list, got: {type(result).__name__}")
        return []
    
    suggestions = []
    existing_dimensions = _get_settings(state).get('segmentation', {}).get('dimensions', [])
    existing_ids = {d.get('id') for d in existing_dimensions if isinstance(d, dict)}
    
    for entry in result:
        if not isinstance(entry, dict):
            continue
        dim_id = entry.get('id')
        if not dim_id or dim_id in existing_ids:
            continue
        source_qid = entry.get('source_question')
        if not source_qid or source_qid not in question_groups:
            continue
        
        # build the dimension dict (in the format the SegmentResolver expects)
        dim = {
            'id': dim_id,
            # The catalog prompt asks for "label" (in the source language)
            'label': {_src(state): entry.get('label') or dim_id},
            'source_question': source_qid,
            'bucketing': {'type': entry.get('bucketing', 'identity')},
        }
        
        suggestions.append(Suggestion(
            category='segmentation',
            target=f'settings.segmentation.dimensions[+]',
            action='add',
            new_value=dim,
            reason=entry.get('reason', ''),
            confidence=float(entry.get('confidence', 0.5)),
        ))
    
    return suggestions


# =============================================================================
# 3. numeric fields
# =============================================================================



def _get_sample_values(state, qid: str, qcfg: dict, max_samples: int = 5) -> List[str]:
    """Sample answers of a question, if df is available."""
    if state is None or state.df is None:
        return []
    columns = qcfg.get('columns', [])
    if not columns:
        return []
    col = columns[0]
    if col not in state.df.columns:
        return []
    
    # the first few non-NA values
    samples = []
    for val in state.df[col].dropna().head(max_samples):
        s = str(val).strip()
        if s and len(s) < 50:
            samples.append(s)
    return samples


def suggest_numeric_fields(state, scope: str = 'unconfigured'
                             ) -> List[Suggestion]:
    """
    Suggests numeric fields.
    
    Args:
        state: AppState with config (and optionally df for sample values).
        scope: 'unconfigured' (default) or 'all'.
    
    Returns:
        list of Suggestions.
    """
    client, ok = _get_llm()
    if not ok:
        return []
    
    question_groups = _get_question_groups(state)
    if not question_groups:
        return []
    
    # filter the candidates
    candidates = {}
    for qid, qcfg in question_groups.items():
        if scope == 'unconfigured' and _is_configured(qcfg, 'numeric'):
            continue
        # numeric already (without its settings), or a number by the structure file
        lss = qcfg.get('lss_metadata') or {}
        is_numeric_hint = (qcfg.get('handler') == 'numeric' or lss.get('type') == 'N'
                           or lss.get('handler_type') == 'numeric')
        # no numeric hint and already another handler -> skip
        if not is_numeric_hint and qcfg.get('handler') not in (None, '', 'freetext'):
            continue
        candidates[qid] = qcfg
    
    if not candidates:
        return []
    
    samples = []
    for qid, qcfg in candidates.items():
        label = _text(state, qcfg.get('label', ''))
        sample_values = _get_sample_values(state, qid, qcfg)
        line = f"- {qid}: {label}"
        if sample_values:
            line += " (" + _p(state, 'samples', samples=', '.join(sample_values)) + ")"
        samples.append(line)
    
    prompt = _p(state, 'numeric', questions='\n'.join(samples))
    
    try:
        result = client.complete_json(
            prompt=prompt,
            system_prompt=_p(state, 'system_json'),
            max_tokens=1500,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"LLM call for numeric failed: {e}")
        return []
    
    if not isinstance(result, dict):
        logger.warning(f"Expected a dict, got: {type(result).__name__}")
        return []
    
    suggestions = []
    for qid, entry in result.items():
        if qid not in candidates or not isinstance(entry, dict):
            continue
        
        # build the numeric block
        numeric_cfg = {
            'unit': entry.get('unit', ''),
            'valid_range': entry.get('valid_range', {}),
            'decimals': int(entry.get('decimals', 0)),
            'bin_count': 10,  # default
        }
        
        # we suggest a patch that sets several fields at once:
        patch = {
            'type': 'numeric',
            'handler': 'numeric',
            'charts': ['histogram', 'box_plot'],
            'default_chart': 'histogram',
            'numeric': numeric_cfg,
        }
        
        suggestions.append(Suggestion(
            category='numeric',
            target=f'question_groups.{qid}',
            action='replace',
            new_value=patch,
            reason=entry.get('reason', ''),
            confidence=float(entry.get('confidence', 0.5)),
        ))
    
    return suggestions


# =============================================================================
# 4. Schema overrides per question
# =============================================================================



def suggest_schema_overrides(state, scope: str = 'unconfigured'
                               ) -> List[Suggestion]:
    """
    Suggests question-specific changes of the summary schema.
    
    Note: this function is conservative; usually no overrides are needed.
    """
    client, ok = _get_llm()
    if not ok:
        return []
    
    question_groups = _get_question_groups(state)
    if not question_groups:
        return []
    
    # candidates: all questions without their own schema (or all, depending on scope)
    candidates = {}
    for qid, qcfg in question_groups.items():
        # numeric questions have their own default schema: skip
        if qcfg.get('handler') == 'numeric':
            continue
        if scope == 'unconfigured' and _is_configured(qcfg, 'schema'):
            continue
        candidates[qid] = qcfg
    
    if not candidates:
        return []
    
    samples = []
    for qid, qcfg in candidates.items():
        label = _text(state, qcfg.get('label', ''))
        handler = qcfg.get('handler', 'unknown')
        samples.append(f"- {qid}: {label} (handler={handler})")
    
    prompt = _p(state, 'schema_overrides', questions='\n'.join(samples[:30]))  # first 30 only, to save tokens
    
    try:
        result = client.complete_json(
            prompt=prompt,
            system_prompt=_p(state, 'system_json'),
            max_tokens=1500,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning(f"LLM call for the schema overrides failed: {e}")
        return []
    
    if not isinstance(result, dict):
        return []
    
    from utils.summary_schema import builtin_sections
    known = builtin_sections()
    suggestions = []
    for qid, entry in result.items():
        if qid not in candidates or not isinstance(entry, dict):
            continue
        confidence = float(entry.get('confidence', 0.0))
        if confidence < 0.6:
            continue
        # Only built-in sections, named by id; the resolver fills in their
        # instruction (utils/summary_schema.builtin_sections). Unknown ids from
        # the LLM are dropped rather than written as empty sections.
        sections = [sid for sid in (entry.get('sections') or []) if isinstance(sid, str) and sid in known]
        dropped = [sid for sid in (entry.get('sections') or []) if sid not in sections]
        if dropped:
            logger.info(f"Schema suggestion {qid}: unknown sections dropped: {dropped}")
        if not sections:
            continue
        suggestions.append(Suggestion(
            category='schema',
            target=f'question_groups.{qid}.summary_schema_override',
            action='replace',
            new_value={'sections': [{'id': sid} for sid in sections]},
            reason=entry.get('reason', ''),
            confidence=confidence,
        ))
    
    return suggestions


# =============================================================================
# Applying the suggestions to the configuration
# =============================================================================

def apply_suggestions(config: dict, suggestions: List[Suggestion]) -> dict:
    """
    Applies a list of suggestions to a configuration.
    
    Args:
        config: the starting configuration (a modified copy is made).
        suggestions: the suggestions to apply.
    
    Returns:
        the new (modified) copy of the configuration; the original is unchanged.
    
    Note: deep copy, so callers keep the original configuration
    (e.g. to show a diff).
    """
    import copy
    new_config = copy.deepcopy(config)
    
    for s in suggestions:
        try:
            _apply_single_suggestion(new_config, s)
        except Exception as e:
            logger.warning(f"Suggestion failed [{s.target}]: {e}")
    
    return new_config


def _apply_single_suggestion(config: dict, s: Suggestion) -> None:
    """Applies a single suggestion in place."""
    if s.category == 'llm_context':
        config.setdefault('settings', {})
        config['settings']['llm_context'] = s.new_value
        return
    
    if s.category == 'segmentation':
        settings = config.setdefault('settings', {})
        seg = settings.setdefault('segmentation', {})
        dims = seg.setdefault('dimensions', [])
        # existence check
        existing_ids = {d.get('id') for d in dims if isinstance(d, dict)}
        new_dim = s.new_value
        if new_dim.get('id') not in existing_ids:
            dims.append(new_dim)
        return
    
    if s.category == 'numeric':
        # target: 'question_groups.<qid>'
        parts = s.target.split('.')
        if len(parts) != 2 or parts[0] != 'question_groups':
            return
        qid = parts[1]
        qg = config.setdefault('question_groups', {})
        if qid not in qg:
            return
        # patch is a dict that goes into qcfg
        for key, value in s.new_value.items():
            qg[qid][key] = value
        # remove the clustering block (it was for free text)
        qg[qid].pop('clustering', None)
        return
    
    if s.category == 'schema':
        # target: 'question_groups.<qid>.summary_schema_override' (read by utils/summary_schema.SchemaResolver)
        parts = s.target.split('.')
        if len(parts) != 3 or parts[0] != 'question_groups':
            return
        qid = parts[1]
        qg = config.setdefault('question_groups', {})
        if qid not in qg:
            return
        qg[qid]['summary_schema_override'] = s.new_value
        return


# =============================================================================
# Wrapper: create and collect the suggestions
# =============================================================================

def generate_all_suggestions(state, scope: str = 'unconfigured'
                                ) -> List[Suggestion]:
    """
    Creates all suggestions at once, for the "Create suggestions" button.
    
    Args:
        state: AppState.
        scope: 'unconfigured' (default) or 'all'.
    
    Returns:
        list of all suggestions, by category.
    """
    suggestions = []
    
    # 1. llm_context
    s = suggest_llm_context(state)
    if s:
        # with scope=unconfigured only if still empty
        existing = _get_settings(state).get('llm_context', '')
        if not (scope == 'unconfigured' and existing):
            suggestions.append(s)
    
    # 2. segmentation
    suggestions.extend(suggest_segmentation_dimensions(state))
    
    # 3. Numeric
    suggestions.extend(suggest_numeric_fields(state, scope=scope))
    
    # 4. Schema-Overrides
    suggestions.extend(suggest_schema_overrides(state, scope=scope))
    
    return suggestions
