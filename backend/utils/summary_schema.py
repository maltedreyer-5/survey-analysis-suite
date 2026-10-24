# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Summary schema module of the survey analysis tool.

Defines the data structures and the resolution logic for configurable result
sections per question: a schema per handler type gives the default, which can
be overridden per survey and even per single question.

Resolution order (highest priority first):
    1. question_groups.<qid>.summary_schema_override
    2. settings.summary_schema_overrides.<handler_type>
    3. HandlerClass.default_summary_schema()
    4. GENERIC_FALLBACK_SCHEMA

Example:

    from utils.summary_schema import SchemaResolver

    resolver = SchemaResolver(
        survey_config=state.config,
        handler_registry=HANDLER_CLASSES,  # {'single_choice': SingleChoiceHandler, ...}
    )
    schema = resolver.resolve(question_id='role',
                              handler_type='single_choice')
    for section in schema.applicable_sections(context={'has_segmentation': True}):
        print(section.id, section.label['en'])
"""

from __future__ import annotations
from utils.languages import catalog_texts

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Union

from utils.languages import SUPPORTED_SOURCE_LANGUAGES, localized

logger = logging.getLogger(__name__)


def builtin_instruction(section: str) -> Dict[str, str]:
    """Instruction of a built-in section in every source language (from prompts/<lang>.yaml)."""
    from utils import prompts
    return {lang: prompts.get(f"summary.sections.{section}", lang)
            for lang in SUPPORTED_SOURCE_LANGUAGES}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SegmentFilter:
    """
    Optional restriction of a section to certain segmentation dimensions.

    If neither include nor exclude is set, all dimensions of the active survey
    configuration apply.

    Attributes:
        include: whitelist of dimension ids (all others are ignored).
        exclude: blacklist of dimension ids (all others are considered).

    If both are set, include wins; exclude is logged as a warning.
    """
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None

    def is_active(self) -> bool:
        return bool(self.include) or bool(self.exclude)

    def applies_to(self, dimension_id: str, all_dimensions: List[str]) -> bool:
        """Decides whether the dimension is admitted by the filter."""
        if self.include is not None:
            return dimension_id in self.include
        if self.exclude is not None:
            return dimension_id not in self.exclude
        return True


@dataclass
class Section:
    """
    A single section of a question summary.

    Attributes:
        id: stable section id (e.g. 'description', 'distribution', 'segments_text').
            Used as the JSON key in the LLM answer and as the database key.
        label: multilingual display name for the headings of the Word export.
        prompt_instruction: hint to the LLM on what this section should contain;
            1-3 sentences that describe the content concretely. A text in the
            source language of the survey or a dict per source language
            ({'de': ..., 'en': ...}); the built-in schemas take it from the
            prompt catalogs.
        length_hint: optional style hint 'short' (1-2 sentences), 'medium'
            (3-5 sentences), 'long' (>5 sentences); goes into the system prompt.
        conditional: condition identifier. If the context given at analysis time
            does not meet the condition, the section is skipped. Known values:
                'has_segmentation'             - at least one dimension active
                'has_segmentation_significant' - a significant segment difference
                'has_outliers'                 - the numeric question has IQR outliers
                'has_clusters'                 - the free-text question has clusters
                'n_categories>3'               - more than 3 answer categories
        segment_filter: optional restriction of the dimensions the section
            considers; only useful for sections that talk about segments.
        required: if True, the LLM must deliver this section (otherwise a repair
            attempt); if False, an empty section is acceptable.
    """
    id: str
    label: Dict[str, str]
    prompt_instruction: Union[str, Dict[str, str]]
    length_hint: Optional[str] = None
    conditional: Optional[str] = None
    segment_filter: Optional[SegmentFilter] = None
    required: bool = True

    def __post_init__(self):
        if not self.id:
            raise ValueError("Section.id must not be empty")
        filled = any(self.label.values()) if isinstance(self.label, dict) else bool(self.label)
        if not filled:
            raise ValueError(f"Section '{self.id}': label is required")

    def get_label(self, lang: str) -> str:
        """Label in the requested language, else English, else any."""
        return localized(self.label, lang, lang, default=self.id)

    def instruction(self, source_lang: str) -> str:
        """Prompt instruction in the language of the prompt (the survey's source language)."""
        return localized(self.prompt_instruction, source_lang, source_lang, default='')


@dataclass
class SummarySchema:
    """
    A list of sections underlying a question summary.

    Attributes:
        schema_id: unique identifier of the schema; stored in the database so
            that it is clear which section structure a stored summary has.
            Convention:
                'default_three_sections' - the default schema with three sections
                '<handler_type>_default' - default schema of the handler
                'survey:<survey_id>:<handler_type>' - survey override
                'survey:<survey_id>:question:<qid>' - question override
        handler_type: the handler type the schema is meant for.
        sections: ordered list of the sections.
        description: human-readable description of the schema.
    """
    schema_id: str
    handler_type: str
    sections: List[Section]
    description: str = ""

    def __post_init__(self):
        if not self.schema_id:
            raise ValueError("SummarySchema.schema_id must not be empty")
        if not self.sections:
            raise ValueError(f"SummarySchema '{self.schema_id}': at least one section is required")
        # prevent duplicate section ids
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            dups = [i for i in ids if ids.count(i) > 1]
            raise ValueError(f"SummarySchema '{self.schema_id}': duplicate section IDs: {set(dups)}")

    def section_ids(self) -> List[str]:
        """Ids of all sections in order."""
        return [s.id for s in self.sections]

    def get_section(self, section_id: str) -> Optional[Section]:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None

    def applicable_sections(self, context: Optional[Dict[str, Any]] = None) -> List[Section]:
        """
        Filters sections by their conditional conditions and the context.

        Args:
            context: dict with condition flags, e.g.
                {'has_segmentation': True, 'has_outliers': False, 'n_categories': 5}

        Returns:
            list of the applicable sections in their original order.
        """
        if context is None:
            context = {}

        result = []
        for section in self.sections:
            if section.conditional is None:
                result.append(section)
                continue
            if _evaluate_conditional(section.conditional, context):
                result.append(section)
            else:
                logger.debug(
                    f"Section '{section.id}' skipped (condition '{section.conditional}' false)"
                )
        return result


def _evaluate_conditional(cond: str, context: Dict[str, Any]) -> bool:
    """
    Evaluates a condition expression.

    Supported forms:
        'has_segmentation'              -> bool(context.get('has_segmentation'))
        'has_segmentation_significant'  -> bool(context.get('has_segmentation_significant'))
        'has_outliers'                  -> bool(context.get('has_outliers'))
        'has_clusters'                  -> bool(context.get('has_clusters'))
        'n_categories>3'                -> int(context.get('n_categories', 0)) > 3
        'n_categories>=5'               -> int(context.get('n_categories', 0)) >= 5
        '!has_outliers'                 -> not context.get('has_outliers')

    Unknown condition: True (render when in doubt), plus a warning.
    """
    cond = cond.strip()

    # Negation
    if cond.startswith('!'):
        return not _evaluate_conditional(cond[1:].strip(), context)

    # numeric comparisons
    for op_str, op in [('>=', lambda a, b: a >= b), ('<=', lambda a, b: a <= b),
                        ('>',  lambda a, b: a >  b), ('<',  lambda a, b: a <  b),
                        ('==', lambda a, b: a == b), ('!=', lambda a, b: a != b)]:
        if op_str in cond:
            key, _, raw = cond.partition(op_str)
            try:
                threshold = float(raw.strip())
                value = float(context.get(key.strip(), 0))
                return op(value, threshold)
            except (ValueError, TypeError):
                logger.warning(f"Condition '{cond}' could not be evaluated")
                return True

    # Boolean-Flag
    return bool(context.get(cond, False))


# ---------------------------------------------------------------------------
# Generic fallback schema
# ---------------------------------------------------------------------------

GENERIC_FALLBACK_SCHEMA = SummarySchema(
    schema_id='generic_fallback',
    handler_type='*',
    sections=[
        Section(
            id='summary',
            label=catalog_texts('summary_section.summary'),
            prompt_instruction=builtin_instruction('summary'),
            length_hint='medium',
            required=True,
        ),
    ],
    description="Generic fallback schema when neither the handler nor the survey provides one.",
)


# ---------------------------------------------------------------------------
# Default schema with three sections (the handlers' default)
# ---------------------------------------------------------------------------

DEFAULT_THREE_SECTIONS_SCHEMA = SummarySchema(
    schema_id='default_three_sections',
    handler_type='*',
    sections=[
        Section(
            id='description',
            label=catalog_texts('summary_section.description'),
            prompt_instruction=builtin_instruction('description'),
            length_hint='short',
            required=True,
        ),
        Section(
            id='interpretation',
            label=catalog_texts('summary_section.interpretation'),
            prompt_instruction=builtin_instruction('interpretation'),
            length_hint='medium',
            required=True,
        ),
        Section(
            id='segments_text',
            label=catalog_texts('summary_section.segments_text'),
            prompt_instruction=builtin_instruction('segments_text'),
            length_hint='medium',
            conditional='has_segmentation',
            required=False,
        ),
    ],
    description=(
        "Default schema with three sections: description, interpretation, "
        "segment differences. If no group differs significantly, the code "
        "inserts the fixed sentence from locales/ instead of asking the LLM."
    ),
)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class SchemaResolver:
    """
    Determines the summary schema to apply to a question.

    Resolution in descending priority:
        1. question override from question_groups.<qid>.summary_schema_override
        2. survey override from settings.summary_schema_overrides.<handler_type>
        3. handler default through HandlerClass.default_summary_schema()
        4. generic fallback (GENERIC_FALLBACK_SCHEMA)
    """

    def __init__(self,
                 survey_config: Dict[str, Any],
                 handler_registry: Optional[Dict[str, type]] = None,
                 survey_id: Optional[str] = None):
        """
        Args:
            survey_config: loaded questions.yaml as a dict.
            handler_registry: {handler_type: HandlerClass} to resolve the defaults;
                if None, only survey/question-specific schemas are resolved and
                the generic fallback is used otherwise.
            survey_id: for the schema ids, optional.
        """
        self.config = survey_config or {}
        self.handler_registry = handler_registry or {}
        self.survey_id = survey_id or self.config.get('settings', {}).get(
            'survey_metadata', {}).get('survey_id', 'unknown')

        # Cache: question_id -> SummarySchema
        self._cache: Dict[str, SummarySchema] = {}

    def resolve(self, question_id: str, handler_type: str) -> SummarySchema:
        """
        The resolved schema of a question.

        Args:
            question_id: id from question_groups.
            handler_type: handler key (e.g. 'single_choice').

        Returns:
            SummarySchema, never None.
        """
        cache_key = f"{question_id}|{handler_type}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        schema = (
            self._resolve_question_override(question_id, handler_type)
            or self._resolve_survey_override(handler_type)
            or self._resolve_handler_default(handler_type)
            or GENERIC_FALLBACK_SCHEMA
        )

        self._cache[cache_key] = schema
        logger.debug(f"Schema for '{question_id}' (handler={handler_type}): {schema.schema_id}")
        return schema

    def clear_cache(self) -> None:
        """Invalidates the cache, e.g. after switching the survey."""
        self._cache.clear()

    # -- Helpers ----------------------------------------------------------

    def _resolve_question_override(self,
                                    question_id: str,
                                    handler_type: str) -> Optional[SummarySchema]:
        qgs = self.config.get('question_groups', {}) or {}
        qcfg = qgs.get(question_id)
        if not qcfg:
            return None
        override = qcfg.get('summary_schema_override')
        if not override:
            return None

        schema_id = f"survey:{self.survey_id}:question:{question_id}"
        return self._build_schema_from_dict(
            schema_id=schema_id,
            handler_type=handler_type,
            data=override,
        )

    def _resolve_survey_override(self, handler_type: str) -> Optional[SummarySchema]:
        overrides = (
            self.config.get('settings', {})
            .get('summary_schema_overrides', {})
        )
        if not overrides:
            return None
        data = overrides.get(handler_type)
        if not data:
            return None

        schema_id = f"survey:{self.survey_id}:{handler_type}"
        return self._build_schema_from_dict(
            schema_id=schema_id,
            handler_type=handler_type,
            data=data,
        )

    def _resolve_handler_default(self, handler_type: str) -> Optional[SummarySchema]:
        cls = self.handler_registry.get(handler_type)
        if cls is None:
            return None
        if not hasattr(cls, 'default_summary_schema'):
            return None
        try:
            return cls.default_summary_schema()
        except Exception as e:
            logger.warning(
                f"default_summary_schema() of {cls.__name__} failed: {e}"
            )
            return None

    @staticmethod
    def _build_schema_from_dict(schema_id: str,
                                 handler_type: str,
                                 data: Dict[str, Any]) -> SummarySchema:
        """Builds a SummarySchema from the YAML dict format."""
        sections_data = data.get('sections') or []
        if not sections_data:
            raise ValueError(
                f"Schema '{schema_id}': 'sections' is empty or missing"
            )

        sections: List[Section] = []
        builtin = builtin_sections()
        for sd in sections_data:
            if isinstance(sd, str):
                sd = {'id': sd}
            base = builtin.get(sd.get('id')) if not sd.get('prompt_instruction') else None
            if base is not None:
                # A built-in section named by id: its definition, with any
                # field given in the configuration taking precedence
                sd = {'label': base.label, 'prompt_instruction': base.prompt_instruction,
                      'length_hint': base.length_hint, 'conditional': base.conditional,
                      'required': base.required, **sd}
            sf_data = sd.get('segment_filter')
            sf = None
            if sf_data:
                sf = SegmentFilter(
                    include=sf_data.get('include'),
                    exclude=sf_data.get('exclude'),
                )

            sections.append(Section(
                id=sd['id'],
                label=sd.get('label', {}),
                prompt_instruction=sd.get('prompt_instruction', ''),
                length_hint=sd.get('length_hint'),
                conditional=sd.get('conditional'),
                segment_filter=sf,
                required=bool(sd.get('required', True)),
            ))

        return SummarySchema(
            schema_id=schema_id,
            handler_type=handler_type,
            sections=sections,
            description=data.get('description', ''),
        )


# ---------------------------------------------------------------------------
# Built-in sections, addressable by id in configured schemas
# ---------------------------------------------------------------------------

def builtin_sections() -> Dict[str, "Section"]:
    """
    Sections a configured schema can name by id alone (``- id: description``):
    label, instruction, length and condition then come from here. Used by the
    configuration assistant, whose suggestions name sections only by id.
    """
    return {section.id: section
            for schema in (DEFAULT_THREE_SECTIONS_SCHEMA, GENERIC_FALLBACK_SCHEMA)
            for section in schema.sections}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

DEFAULT_SECTION_IDS: Set[str] = {'description', 'interpretation', 'segments_text'}


def is_default_summary(sections: Dict[str, str]) -> bool:
    """Checks whether a sections dict belongs to the default schema with three sections."""
    return set(sections.keys()).issubset(DEFAULT_SECTION_IDS) and bool(sections)
