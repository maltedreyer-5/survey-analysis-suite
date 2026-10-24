# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Survey registry: manages several survey configurations installed side by side.

Every survey is a subdirectory `surveys/<id>/` with this layout:

    surveys/
        <survey_id>/
            survey.yaml          (required: metadata: id, title, description, version)
            questions.yaml       (required: question definitions, settings, segmentation)
            documents.yaml       (optional: configuration of the publication documents)
            diagram_labels.yaml  (optional: labels of the Mermaid diagrams)

Example `survey.yaml`:

    id: ai_in_higher_education_de
    title:
      de: Einsatz von KI an Hochschulen 2026
    description:
      de: Befragung zum Einsatz von KI-Werkzeugen an der Hochschule.
    version: 1.0
    languages: [de]
    source:
      type: limesurvey
      survey_id: 863712

The registry searches the `surveys/` directory for valid survey folders and
offers them through `list_surveys()`.

Without a `surveys/` directory the registry is empty.
"""

from __future__ import annotations

import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


# file name conventions
SURVEY_META_FILE      = 'survey.yaml'
QUESTIONS_FILE        = 'questions.yaml'
DOCUMENTS_FILE        = 'documents.yaml'
DIAGRAM_LABELS_FILE   = 'diagram_labels.yaml'


# =============================================================================
# Data models
# =============================================================================

@dataclass
class SurveyMeta:
    """
    Metadata of a registered survey.
    
    Read from `<survey_dir>/survey.yaml` and enriched with the paths of the
    configuration files.
    """
    id: str
    title: Dict[str, str]            # multilingual display name
    description: Dict[str, str]      # multilingual description
    version: str = '1.0'
    languages: List[str] = field(default_factory=lambda: ['de'])
    source: Optional[dict] = None    # e.g. {'type': 'limesurvey', 'survey_id': '...'}
    
    # paths: set by the registry, not read from YAML
    directory: Optional[Path] = None
    questions_path: Optional[Path] = None
    documents_path: Optional[Path] = None
    diagram_labels_path: Optional[Path] = None
    
    def get_title(self, lang: str = 'de') -> str:
        return _src_text(self.title, lang, self.id) or self.id
    
    def get_description(self, lang: str = 'de') -> str:
        return _src_text(self.description, lang, '') or ''
    
    def has_documents(self) -> bool:
        return self.documents_path is not None and self.documents_path.exists()
    
    def has_diagram_labels(self) -> bool:
        return (self.diagram_labels_path is not None
                and self.diagram_labels_path.exists())


# =============================================================================
# Registry
# =============================================================================

class SurveyRegistry:
    """
    Discovers and manages survey configurations.
    
    Usage:
        registry = SurveyRegistry(Path('/path/to/app/surveys'))
        registry.discover()
        for s in registry.list_surveys():
            print(s.id, s.get_title('en'))
        
        # set the active survey
        registry.set_active('ai_in_higher_education_de')
        config = registry.load_questions()
    """
    
    def __init__(self, surveys_dir: Path):
        """
        Args:
            surveys_dir: root directory of the survey folders.
        """
        self.surveys_dir = Path(surveys_dir)
        self._surveys: Dict[str, SurveyMeta] = {}
        self._active_id: Optional[str] = None
    
    # ----- Discovery -----
    
    def discover(self) -> List[SurveyMeta]:
        """
        Scans the surveys directory and registers all valid surveys.
        
        A survey is valid if:
        - the directory contains a survey.yaml
        - the survey.yaml has an 'id' field
        - the directory contains a questions.yaml
        
        Invalid entries are dropped with a logger warning.
        
        Returns:
            list of the registered SurveyMeta in discovery order.
        """
        self._surveys.clear()
        
        if not self.surveys_dir.exists():
            logger.info(f"Surveys directory not found: {self.surveys_dir}")
            return []
        
        if not self.surveys_dir.is_dir():
            logger.warning(f"Surveys path is not a directory: {self.surveys_dir}")
            return []
        
        for child in sorted(self.surveys_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith('.') or child.name.startswith('_'):
                # skip hidden / helper directories
                continue
            
            try:
                meta = self._load_survey_meta(child)
                if meta is None:
                    continue
                if meta.id in self._surveys:
                    logger.warning(
                        f"Survey id conflict: '{meta.id}' registered already "
                        f"(from {self._surveys[meta.id].directory}); "
                        f"skipping {child}"
                    )
                    continue
                self._surveys[meta.id] = meta
                logger.info(f"Survey registered: {meta.id} ({child})")
            except Exception as e:
                logger.warning(f"Error reading {child}: {e}")
        
        return list(self._surveys.values())
    
    def _load_survey_meta(self, survey_dir: Path) -> Optional[SurveyMeta]:
        """Reads survey.yaml and checks the required files."""
        meta_path = survey_dir / SURVEY_META_FILE
        if not meta_path.exists():
            logger.debug(
                f"No {SURVEY_META_FILE} in {survey_dir}, skipping"
            )
            return None
        
        with meta_path.open(encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        survey_id = data.get('id')
        if not survey_id:
            logger.warning(f"{meta_path}: no 'id' field, skipping")
            return None
        
        questions_path = survey_dir / QUESTIONS_FILE
        if not questions_path.exists():
            logger.warning(
                f"{survey_dir}: no {QUESTIONS_FILE}, skipping"
            )
            return None
        
        # normalise title and description: a string as well as a dict is allowed
        title = data.get('title', survey_id)
        if isinstance(title, str):
            title = {'de': title}
        
        description = data.get('description', '')
        if isinstance(description, str):
            description = {'de': description}
        
        documents_path = survey_dir / DOCUMENTS_FILE
        diagram_labels_path = survey_dir / DIAGRAM_LABELS_FILE
        
        return SurveyMeta(
            id=survey_id,
            title=title,
            description=description,
            version=str(data.get('version', '1.0')),
            languages=data.get('languages', ['de']) or ['de'],
            source=data.get('source'),
            directory=survey_dir,
            questions_path=questions_path,
            documents_path=documents_path if documents_path.exists() else None,
            diagram_labels_path=(diagram_labels_path
                                  if diagram_labels_path.exists() else None),
        )
    
    # ----- Public API -----
    
    def list_surveys(self) -> List[SurveyMeta]:
        return list(self._surveys.values())
    
    def get_survey(self, survey_id: str) -> Optional[SurveyMeta]:
        return self._surveys.get(survey_id)
    
    def has_survey(self, survey_id: str) -> bool:
        return survey_id in self._surveys
    
    def survey_ids(self) -> List[str]:
        return list(self._surveys.keys())
    
    def set_active(self, survey_id: str) -> SurveyMeta:
        """
        Sets the active survey.
        
        Raises:
            KeyError: if the survey is not registered.
        """
        if survey_id not in self._surveys:
            raise KeyError(
                f"Survey '{survey_id}' is not in the registry. "
                f"Available: {list(self._surveys.keys())}"
            )
        self._active_id = survey_id
        logger.info(f"Active survey: {survey_id}")
        return self._surveys[survey_id]
    
    def get_active(self) -> Optional[SurveyMeta]:
        if self._active_id is None:
            return None
        return self._surveys.get(self._active_id)
    
    def get_active_id(self) -> Optional[str]:
        return self._active_id
    
    # ----- loading the configuration of the active survey -----
    
    def load_questions(
        self,
        survey_id: Optional[str] = None,
    ) -> dict:
        """
        Loads the questions.yaml of a survey.
        
        Args:
            survey_id: id of the survey; if None, the active one.
        
        Returns:
            the loaded configuration as a dict.
        
        Raises:
            KeyError: if the survey is not registered.
            FileNotFoundError: if questions.yaml is missing.
        """
        meta = self._resolve_meta(survey_id)
        return self._load_yaml(meta.questions_path)
    
    def load_documents(
        self,
        survey_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Loads documents.yaml if present, otherwise None."""
        meta = self._resolve_meta(survey_id)
        if meta.documents_path is None or not meta.documents_path.exists():
            return None
        return self._load_yaml(meta.documents_path)
    
    def load_diagram_labels(
        self,
        survey_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Loads diagram_labels.yaml if present, otherwise None."""
        meta = self._resolve_meta(survey_id)
        if (meta.diagram_labels_path is None
                or not meta.diagram_labels_path.exists()):
            return None
        return self._load_yaml(meta.diagram_labels_path)
    
    # ----- Private Helpers -----
    
    def _resolve_meta(self, survey_id: Optional[str]) -> SurveyMeta:
        if survey_id is None:
            if self._active_id is None:
                raise ValueError(
                    "No active survey set: call set_active() "
                    "or pass survey_id explicitly."
                )
            survey_id = self._active_id
        
        meta = self._surveys.get(survey_id)
        if meta is None:
            raise KeyError(
                f"Survey '{survey_id}' is not registered. "
                f"Available: {list(self._surveys.keys())}"
            )
        return meta
    
    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        with path.open(encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
