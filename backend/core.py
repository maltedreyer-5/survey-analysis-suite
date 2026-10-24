# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Core module of the survey analysis tool: AppState, configuration and session handling.

Several surveys can be installed side by side, each in a directory
`surveys/<id>/`. The active survey is managed by the survey registry
(utils/survey_registry.py).

Setting the active survey:
- environment variable: SURVEY_ID=ai_in_higher_education_de
- in code: set_active_survey('<id>')
"""

import logging
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List

import yaml
import pandas as pd

from utils.persistence import SurveyDatabase, get_database
from utils.persistence import check_database_version, SCHEMA_VERSION
from utils import set_label_replacements
from utils.survey_registry import (
    SurveyRegistry,
)

logger = logging.getLogger(__name__)

# paths
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"          # files shared by all surveys (glossary, documents, ...)
SURVEYS_DIR = BASE_DIR / "surveys"        # multi-survey path
# Runtime data (database, caches) and outputs; SURVEY_DATA_DIR / SURVEY_OUTPUT_DIR
# move them elsewhere (installations, tests, checks), default inside backend/
DATA_DIR = Path(os.environ.get("SURVEY_DATA_DIR") or BASE_DIR / "data")
OUTPUT_DIR = Path(os.environ.get("SURVEY_OUTPUT_DIR") or BASE_DIR / "outputs")
DB_PATH = DATA_DIR / "survey_data.db"

# create the directories
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# A database of another schema version is refused, not migrated
check_database_version(DB_PATH)


# =============================================================================
# Survey registry bootstrap
# =============================================================================
# Importing the module initialises the registry and discovers the surveys.
# Choice of the active survey, in this order:
#   1. environment variable SURVEY_ID, if set and registered
#   2. first registered survey under surveys/
# =============================================================================

_registry = SurveyRegistry(SURVEYS_DIR)
_registry.discover()


def _select_initial_active_survey() -> None:
    """Sets the initial active survey when the module starts."""
    env_id = os.environ.get('SURVEY_ID', '').strip()
    if env_id and _registry.has_survey(env_id):
        _registry.set_active(env_id)
        return
    if env_id:
        logger.warning(
            f"SURVEY_ID='{env_id}' not in the registry — fallback. "
            f"Available: {_registry.survey_ids()}"
        )
    
    survey_ids = _registry.survey_ids()
    if not survey_ids:
        logger.warning("No surveys registered — the configuration stays empty")
        return
    _registry.set_active(survey_ids[0])


_select_initial_active_survey()


def get_registry() -> SurveyRegistry:
    """Returns the global survey registry."""
    return _registry


def set_active_survey(survey_id: str) -> None:
    """
    Switches the active survey.
    
    Note: the existing AppState (loaded data and analyses) is NOT reset
    automatically. The caller has to reload state.config with load_config()
    after the switch.
    
    Raises:
        KeyError: if the survey is not registered.
    """
    _registry.set_active(survey_id)


def get_active_survey_id() -> Optional[str]:
    """ID of the active survey, or None if none is set."""
    return _registry.get_active_id()


def get_active_config_dir() -> Path:
    """
    Configuration directory of the active survey.
    
    For an active survey this is its directory surveys/<id>/.
    Without an active survey: `config/` (shared files only, no questions.yaml).
    """
    meta = _registry.get_active()
    if meta is None or meta.directory is None:
        return CONFIG_DIR
    return meta.directory


@dataclass
class AppState:
    """Global application state."""
    df: Optional[pd.DataFrame] = None
    config: Optional[dict] = None
    # Result of services.config_validation for the loaded configuration
    config_errors: List[str] = field(default_factory=list)
    config_warnings: List[str] = field(default_factory=list)
    analysis_results: Dict[str, Any] = field(default_factory=dict)
    current_segment: Optional[str] = None  # segmentation key (e.g. "role")
    segment_column: Optional[str] = None   # actual column name
    segment_filter: Optional[pd.Series] = None
    
    # Persistierung
    db: Optional[SurveyDatabase] = None
    project_id: Optional[int] = None
    import_id: Optional[int] = None
    project_name: str = "default"
    
    # themes for the dashboard export
    themes: Dict[str, Any] = field(default_factory=dict)
    
    # segment-specific cluster comparison
    clusters_by_value: Optional[dict] = None
    
    # Re-export status
    # Set to True when changes in the Workbench or the themes are saved that
    # need a re-export. Kept across tab switches until a re-export runs or the
    # banner is dismissed explicitly.
    pending_reexport: bool = False
    pending_reexport_reasons: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if self.analysis_results is None:
            self.analysis_results = {}
        # initialise the database
        self.db = get_database(DB_PATH)
        # create/load the default project
        self._init_project()
    
    def _init_project(self):
        """Initialises or loads the default project."""
        project = self.db.get_project_by_name(self.project_name)
        if project:
            self.project_id = project['id']
            # load the last import of the active survey
            last_import = self.db.get_latest_import_for_survey(get_active_survey_id() or 'default', self.project_id)
            if last_import:
                self.import_id = last_import['id']
                logger.info(f"Project '{self.project_name}' loaded (import #{self.import_id})")
        else:
            self.project_id = self.db.create_project(self.project_name, "Default project")
            logger.info(f"New project '{self.project_name}' created")
    
    def reset(self):
        """Resets the state."""
        self.df = None
        self.analysis_results = {}
        self.current_segment = None
        self.segment_column = None
        self.segment_filter = None
        self.clusters_by_value = None
        self.pending_reexport = False
        self.pending_reexport_reasons = []
    
    def mark_reexport_needed(self, reason: str) -> None:
        """
        Marks that a re-export is needed.
        
        Called by the save operations of the Workbench and the themes.
        Several calls accumulate the reasons; the banner shows up to
        three of them and counts the rest.
        """
        self.pending_reexport = True
        if reason and reason not in self.pending_reexport_reasons:
            self.pending_reexport_reasons.append(reason)
    
    def clear_reexport_flag(self) -> None:
        """Clears the re-export flag (e.g. after a successful re-export)."""
        self.pending_reexport = False
        self.pending_reexport_reasons = []
    
    def get_filtered_df(self) -> pd.DataFrame:
        """Returns the filtered DataFrame."""
        if self.df is None:
            return pd.DataFrame()
        if self.segment_filter is not None:
            return self.df[self.segment_filter]
        return self.df
    
    def get_db_info(self) -> dict:
        """Returns information about the database."""
        if self.db is None:
            return {"status": "not_initialized"}
        
        try:
            return {
                "status": "connected",
                "path": str(DB_PATH),
                "project_id": self.project_id,
                "import_id": self.import_id,
                "schema_version": SCHEMA_VERSION
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# global state instance
state = AppState()


def load_config() -> dict:
    """Loads the YAML configuration of the active survey."""
    config_path = get_active_config_dir() / "questions.yaml"
    try:
        logger.info(f"Loading the configuration from: {config_path}")
        logger.info(f"File exists: {config_path.exists()}")
        if get_active_survey_id():
            logger.info(f"Active survey: {get_active_survey_id()}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
            # references to named scales (settings.scales) become the embedded
            # structures the analysis reads; the file itself stays as written
            from utils.scales import expand_named_scales
            config = expand_named_scales(raw_config)
            # missing output languages of the labels from the translation memory
            # (no LLM call here; the pipeline translates what is still missing)
            try:
                from utils.config_translation import fill_config_labels
                config, label_stats = fill_config_labels(config)
                if label_stats["filled"]:
                    logger.info(f"Configuration labels from the translation memory: {label_stats['filled']} lists")
            except Exception as e:
                logger.warning(f"Configuration labels not filled from the translation memory: {e}")
            state.config = config
            
            # Debug-Info
            if config:
                qgroups = config.get('question_groups', {})
                logger.info(f"Configuration loaded: {len(qgroups)} question groups")
                logger.info(f"Question group ids: {list(qgroups.keys())}")
                
                # load the label replacements and set them globally
                label_replacements = config.get('settings', {}).get('label_replacements', {})
                if label_replacements:
                    set_label_replacements(label_replacements)
                    logger.info(f"Label replacements loaded: {len(label_replacements)} entries")
            else:
                logger.warning("The configuration is empty or None!")
            
            from services.config_validation import validate_survey_config
            state.config_errors, state.config_warnings = validate_survey_config(raw_config)
            for msg in state.config_errors:
                logger.error(f"Configuration error: {msg}")
            for msg in state.config_warnings:
                logger.warning(f"Configuration warning: {msg}")
            
            return config
    except Exception as e:
        logger.error(f"Error loading the configuration: {e}", exc_info=True)
        return {}


def save_config(yaml_content: str) -> str:
    """Saves the configuration of the active survey."""
    config_path = get_active_config_dir() / "questions.yaml"
    try:
        # validate the YAML
        config = yaml.safe_load(yaml_content)
        
        # save, after a snapshot of the previous version
        from services.config_io import write_active_config_text_with_snapshot
        write_active_config_text_with_snapshot(yaml_content, reason='yaml_editor')
        
        # reload
        state.config = config
        
        # update the label replacements
        label_replacements = config.get('settings', {}).get('label_replacements', {})
        set_label_replacements(label_replacements)
        
        return "✅ Configuration saved"
    except yaml.YAMLError as e:
        return f"❌ YAML error: {e}"
    except Exception as e:
        return f"❌ Error saving: {e}"


def _latest_import_of_active_survey():
    return state.db.get_latest_import_for_survey(get_active_survey_id() or 'default', state.project_id)


def restore_last_session() -> Tuple[str, Optional[pd.DataFrame]]:
    """Restores the last session completely."""
    import json
    
    try:
        if state.db is None or state.project_id is None:
            return "No database connection", None
        
        # load the last import of the active survey (imports of other surveys share the database)
        last_import = _latest_import_of_active_survey()
        
        if not last_import:
            return "No previous imports of the active survey found", None
        
        state.import_id = last_import['id']
        
        # load the data
        df = state.db.load_import(last_import['id'])
        
        if df is None or df.empty:
            return "No data in the last import", None
        
        state.df = df
        
        # load the configuration (if not loaded yet)
        if state.config is None:
            load_config()
        
        # === LOAD THE ANALYSIS RESULTS ===
        analysis_count = 0
        try:
            all_results = state.db.get_all_analysis_results(state.import_id)
            
            for result_row in all_results:
                # only overall results (segment_type="all") in state.analysis_results
                if result_row.get('segment_type') == 'all':
                    qid = result_row.get('question_id')
                    result_data = result_row.get('result')
                    
                    if qid and result_data:
                        state.analysis_results[qid] = result_data
                        analysis_count += 1
        except Exception as e:
            logger.warning(f"Could not load the analysis results: {e}")
        
        # === LOAD THE THEMES ===
        themes_count = 0
        try:
            db_themes = state.db.get_themes(state.project_id)
            if db_themes:
                state.themes = db_themes
                themes_count = len(db_themes)
        except Exception as e:
            logger.warning(f"Could not load the themes: {e}")
        
        # === ITEM-EXTRAKTIONS-STATUS ===
        extraction_count = 0
        try:
            extractions = state.db.get_extraction_status(state.import_id)
            if extractions:
                extraction_count = len(extractions)
        except Exception:
            pass
        
        # === STATUS MESSAGE ===
        msg_lines = [
            f"✅ Session restored",
            f"",
            f"📁 Import: {last_import.get('filename', 'unknown')}",
            f"📅 Date: {last_import.get('imported_at', 'unknown')[:16]}",
            f"📊 Rows: {len(df)}",
        ]
        
        if analysis_count > 0:
            msg_lines.append(f"✓ {analysis_count} analysis results loaded")
        
        if themes_count > 0:
            msg_lines.append(f"✓ {themes_count} themes loaded")
        
        if extraction_count > 0:
            msg_lines.append(f"✓ {extraction_count} questions with item extractions")
        
        return "\n".join(msg_lines), df
            
    except Exception as e:
        logger.error(f"Error restoring: {e}", exc_info=True)
        return f"❌ Error: {e}", None


def get_database_status() -> str:
    """Returns the database status as a formatted string."""
    try:
        if state.db is None:
            return "📂 No database connection"
        
        lines = [
            "### 📊 Database status",
            f"Schema version: {SCHEMA_VERSION}",
            f"Project: {state.project_name} (ID: {state.project_id})",
            f"Current import: {state.import_id or 'none'}",
        ]
        
        # show the last import of the active survey
        if state.project_id:
            last_import = _latest_import_of_active_survey()
            if last_import:
                lines.append("")
                lines.append("### Last import:")
                lines.append(f"- **{last_import.get('filename', 'unknown')}**")
                lines.append(f"- {last_import.get('total_responses', 0)} rows")
                lines.append(f"- {last_import.get('imported_at', 'unknown')[:16]}")
        
        # extraction status
        if state.import_id:
            try:
                extractions = state.db.get_extraction_status(state.import_id)
                if extractions:
                    lines.append("")
                    lines.append("### Item extractions:")
                    for ext in extractions[:5]:
                        lines.append(
                            f"- {ext['question_id']}: {ext['item_count']} items "
                            f"({ext['method']})"
                        )
            except Exception:
                pass  # ignore if get_extraction_status does not exist
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Database status error: {e}", exc_info=True)
        return f"❌ Database error: {e}"


def get_config_yaml() -> str:
    """Returns the current YAML content of the active survey."""
    config_path = get_active_config_dir() / "questions.yaml"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading the configuration: {e}")
        return f"# Error loading: {e}"
