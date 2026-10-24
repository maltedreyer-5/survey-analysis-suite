# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: workflow status for the overview tab.

Data about the current state of the active survey:
- Is data imported?
- How many questions are configured?
- How many questions are analysed?
- How many themes are defined?
- What is the next sensible step?

All functions return plain data (dicts/strings), no interface coupling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Data model
# =============================================================================

@dataclass
class WorkflowStep:
    """Status of one workflow step."""
    id: str
    label: str
    icon: str  # '✓' (done) | '⏳' (in progress) | '○' (pending)
    detail: str  # additional information
    
    @property
    def done(self) -> bool:
        return self.icon == '✓'


@dataclass
class WorkflowStatus:
    """Overall status of the analysis of the current survey."""
    survey_id: Optional[str]
    survey_title: str
    steps: List[WorkflowStep] = field(default_factory=list)
    next_action: Optional[Dict] = None  # {'tab': 'pipeline', 'label': 'Start the pipeline'}
    recent_activity: List[str] = field(default_factory=list)
    config_errors: List[str] = field(default_factory=list)
    config_warnings: List[str] = field(default_factory=list)
    
    def all_done(self) -> bool:
        return all(s.done for s in self.steps)
    
    def first_incomplete(self) -> Optional[WorkflowStep]:
        for s in self.steps:
            if not s.done:
                return s
        return None


# =============================================================================
# Status collector
# =============================================================================

def get_workflow_status(state) -> WorkflowStatus:
    """
    Collects the current status of the active survey.
    
    Args:
        state: AppState with df, config, db, import_id, analysis_results.
    
    Returns:
        WorkflowStatus with the steps and a recommendation for the next step.
    """
    survey_id, survey_title = _get_survey_info()
    status = WorkflowStatus(survey_id=survey_id, survey_title=survey_title)
    
    # step 1: data imported?
    n_rows = 0
    if state.df is not None:
        n_rows = len(state.df)
    status.steps.append(WorkflowStep(
        id='import',
        label='Data imported',
        icon='✓' if n_rows > 0 else '○',
        detail=f"{n_rows} responses" if n_rows > 0 else 'No data yet',
    ))
    
    # step 2: configuration present?
    n_questions = 0
    n_numeric = 0
    n_freetext = 0
    if state.config:
        qg = state.config.get('question_groups', {})
        n_questions = len(qg)
        for qcfg in qg.values():
            handler = qcfg.get('handler')
            if handler == 'numeric':
                n_numeric += 1
            elif handler == 'freetext':
                n_freetext += 1
    
    config_detail = ''
    if n_questions > 0:
        config_detail = f"{n_questions} question groups"
        if n_numeric > 0:
            config_detail += f" ({n_numeric} numeric)"
    else:
        config_detail = 'No configuration yet'
    
    status.steps.append(WorkflowStep(
        id='config',
        label='Configuration',
        icon='✓' if n_questions > 0 else '○',
        detail=config_detail,
    ))
    
    # step 3: item extraction (only with free-text questions)
    n_extracted = 0
    if state.db and state.import_id and n_freetext > 0:
        try:
            with state.db._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT COUNT(DISTINCT question_id) FROM response_items
                       WHERE import_id = ?""",
                    (state.import_id,),
                )
                row = cursor.fetchone()
                n_extracted = row[0] if row else 0
        except Exception as e:
            logger.debug(f"Could not count the response_items: {e}")
    
    if n_freetext > 0:
        status.steps.append(WorkflowStep(
            id='extraction',
            label='Item extraction',
            icon='✓' if n_extracted >= n_freetext and n_extracted > 0 else '○',
            detail=f"{n_extracted}/{n_freetext} free-text questions",
        ))
    
    # step 4: analysis
    n_analyzed = 0
    if state.db and state.import_id and n_questions > 0:
        try:
            with state.db._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT COUNT(DISTINCT question_id) FROM analysis_results
                       WHERE import_id = ?""",
                    (state.import_id,),
                )
                row = cursor.fetchone()
                n_analyzed = row[0] if row else 0
        except Exception as e:
            logger.debug(f"Could not count the analysis_results: {e}")
    elif state.analysis_results:
        n_analyzed = len(state.analysis_results)
    
    status.steps.append(WorkflowStep(
        id='analysis',
        label='Analysis',
        icon='✓' if n_analyzed >= n_questions and n_analyzed > 0 else '○',
        detail=f"{n_analyzed}/{n_questions} questions analyzed",
    ))
    
    # step 5: themes
    n_themes = len(state.themes or {})
    status.steps.append(WorkflowStep(
        id='themes',
        label='Cross-cutting themes',
        icon='✓' if n_themes > 0 else '○',
        detail=f"{n_themes} themes defined" if n_themes > 0 else 'Optional',
    ))
    
    # step 6: export
    n_exports = _count_recent_exports()
    status.steps.append(WorkflowStep(
        id='export',
        label='Export',
        icon='✓' if n_exports > 0 else '○',
        detail=f"{n_exports} files in the output" if n_exports else 'No results yet',
    ))
    
    # recommend the next step
    incomplete = status.first_incomplete()
    if incomplete:
        status.next_action = _next_action_for(incomplete.id)
    
    # recent activity (last imports/exports)
    status.recent_activity = _get_recent_activity(state)
    status.config_errors = list(getattr(state, 'config_errors', []) or [])
    status.config_warnings = list(getattr(state, 'config_warnings', []) or [])
    
    return status


# =============================================================================
# Helper
# =============================================================================

def _get_survey_info() -> tuple:
    """(survey_id, title) of the active survey."""
    try:
        from core import get_active_survey_id, get_registry
        sid = get_active_survey_id()
        if sid:
            meta = get_registry().get_survey(sid)
            if meta:
                return sid, meta.get_title('de')
        return sid, sid or 'No active survey'
    except Exception as e:
        logger.debug(f"Could not determine the survey info: {e}")
        return None, 'Unknown survey'


def _count_recent_exports() -> int:
    """Counts files in OUTPUT_DIR (heuristic: number of direct children)."""
    try:
        from core import OUTPUT_DIR
        from pathlib import Path
        if not Path(OUTPUT_DIR).exists():
            return 0
        return sum(1 for _ in Path(OUTPUT_DIR).iterdir())
    except Exception:
        return 0


def _next_action_for(step_id: str) -> Optional[Dict]:
    """Recommends an interface action for the next incomplete step."""
    actions = {
        'import':     {'tab': 'import',     'label': '📥 Import data'},
        'config':     {'tab': 'config',     'label': '🛠️ Load a configuration'},
        'extraction': {'tab': 'pipeline',   'label': '▶️ Start item extraction'},
        'analysis':   {'tab': 'pipeline',   'label': '▶️ Start the pipeline'},
        'themes':     {'tab': 'themes',     'label': '🏷️ Define themes'},
        'export':     {'tab': 'pipeline',   'label': '▶️ Start the pipeline'},
    }
    return actions.get(step_id)


def _get_recent_activity(state) -> List[str]:
    """
    The last few activities as formatted strings.
    
    Currently from the database:
    - last import (import_id, time, number of rows)
    - last update of the analyses
    - last change of the themes
    """
    activities = []
    
    if not state.db:
        return activities
    
    # last import, for the active survey if possible
    try:
        active_survey_id = None
        try:
            from core import get_active_survey_id
            active_survey_id = get_active_survey_id()
        except Exception:
            pass
        
        with state.db._connection() as conn:
            cursor = conn.cursor()
            if active_survey_id:
                cursor.execute(
                    """SELECT id, imported_at as created_at,
                              total_responses as n_rows, survey_id
                       FROM imports
                       WHERE survey_id = ?
                       ORDER BY imported_at DESC LIMIT 1""",
                    (active_survey_id,)
                )
            else:
                cursor.execute(
                    """SELECT id, imported_at as created_at,
                              total_responses as n_rows, survey_id
                       FROM imports
                       ORDER BY imported_at DESC LIMIT 1"""
                )
            row = cursor.fetchone()
            if row:
                survey_marker = ""
                if active_survey_id and row['survey_id']:
                    survey_marker = f" [{row['survey_id']}]"
                activities.append(
                    f"Import {_format_relative_time(row['created_at'])} ago "
                    f"→ {row['n_rows']} rows{survey_marker}"
                )
    except Exception as e:
        logger.debug(f"Could not query the imports: {e}")
    
    # last analysis
    try:
        with state.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT MAX(created_at) AS last_at FROM analysis_results"""
            )
            row = cursor.fetchone()
            if row and row['last_at']:
                activities.append(
                    f"Last analysis {_format_relative_time(row['last_at'])} ago"
                )
    except Exception as e:
        logger.debug(f"Could not query the analysis_results: {e}")
    
    return activities


def _format_relative_time(timestamp_str) -> str:
    """Formats a database timestamp as '5 min', '2 h', '3 days'."""
    try:
        # SQLite timestamps come in different forms
        if isinstance(timestamp_str, str):
            try:
                ts = datetime.fromisoformat(timestamp_str)
            except ValueError:
                ts = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        else:
            return 'an unknown time'
        
        delta = datetime.now() - ts
        total_seconds = delta.total_seconds()
        
        if total_seconds < 60:
            return f"{int(total_seconds)} s"
        elif total_seconds < 3600:
            return f"{int(total_seconds / 60)} min"
        elif total_seconds < 86400:
            return f"{int(total_seconds / 3600)} h"
        else:
            return f"{int(total_seconds / 86400)} days"
    except Exception:
        return 'an unknown time'


# =============================================================================
# Status text renderer (for a simple display)
# =============================================================================

def render_status_markdown(status: WorkflowStatus) -> str:
    """
    Renders WorkflowStatus as a Markdown string for a Gradio Markdown component.
    """
    lines = []
    lines.append(f"### Active survey: **{status.survey_title}**")
    if status.survey_id:
        lines.append(f"_(`{status.survey_id}`)_")
    lines.append('')
    if status.config_errors:
        lines.append('#### ❌ Configuration has errors')
        lines.append('Analyses are possible only after the errors are fixed in the Setup tab.')
        for e in status.config_errors:
            lines.append(f"- {e}")
        lines.append('')
    if status.config_warnings:
        lines.append('#### ⚠️ Notes on the configuration')
        for w in status.config_warnings:
            lines.append(f"- {w}")
        lines.append('')
    lines.append('#### Workflow status')
    lines.append('')
    for step in status.steps:
        lines.append(f"- {step.icon} **{step.label}** — {step.detail}")
    lines.append('')
    
    if status.next_action:
        lines.append(f"**Next step:** {status.next_action['label']}")
        lines.append('')
    elif status.all_done():
        lines.append("**✅ All steps done.**")
        lines.append('')
    
    if status.recent_activity:
        lines.append('#### Recent activity')
        for a in status.recent_activity:
            lines.append(f"- {a}")
    
    return '\n'.join(lines)


def get_imports_per_survey(state) -> Dict[str, int]:
    """
    {survey_id: count} of all imports, over all surveys.
    
    Shown in the Setup tab, so the user sees which surveys have data.
    """
    if not state or not state.db:
        return {}
    
    try:
        return state.db.count_imports_per_survey()
    except Exception as e:
        logger.debug(f"Could not determine the import counts: {e}")
        return {}


def render_imports_per_survey_markdown(state) -> str:
    """Renders the imports per survey as Markdown."""
    counts = get_imports_per_survey(state)
    if not counts:
        return "_No imports in the database._"
    
    # add the survey title, if the registry is available
    titles = {}
    try:
        from core import get_registry
        for meta in get_registry().list_surveys():
            titles[meta.id] = meta.get_title('de')
    except Exception:
        pass
    
    lines = ["**Imports per survey:**", ""]
    for survey_id, count in sorted(counts.items(),
                                     key=lambda x: x[1], reverse=True):
        title = titles.get(survey_id)
        if title:
            lines.append(f"- `{survey_id}` ({title}): **{count}** import(s)")
        else:
            lines.append(f"- `{survey_id}`: **{count}** import(s)")
    return '\n'.join(lines)
