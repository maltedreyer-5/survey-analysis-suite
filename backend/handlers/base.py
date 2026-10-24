# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Base handler class of the survey analysis tool: the interface of all question handlers.

With the schema concept (see utils/summary_schema.py):
- default_summary_schema(): class method returning the default schema of the
  handler type. Subclasses can override it to offer their own sections (e.g.
  NumericHandler: distribution / central_tendency / outliers).
- build_summary_context(): creates the condition dict for
  Section.applicable_sections().
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# imported at run time to avoid circular imports
# (utils.summary_schema imports nothing from handlers)

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Standardised result of an analysis."""
    question_id: str
    n: int
    data: dict | pd.DataFrame
    percentages: Optional[dict] = None
    description: str = ""
    warnings: list[str] = field(default_factory=list)
    clusters: Optional[list[dict]] = None
    additional_content: Optional[dict] = None
    metadata: dict = field(default_factory=dict)


def blanks_to_nan(df: pd.DataFrame, columns) -> pd.DataFrame:
    """
    Empty and whitespace-only cells of ``columns`` as missing (NaN). They mean
    "no answer" in every export format (SPSS writes empty strings, CSV cells
    may hold a blank), but would otherwise count as an answer.
    """
    cols = [c for c in (columns or []) if c in df.columns
            and (pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c]))]
    if not cols:
        return df
    blank = df[cols].apply(lambda s: s.map(lambda v: isinstance(v, str) and v.strip() == ""))
    if not blank.values.any():
        return df
    df = df.copy()
    for c in cols:
        df.loc[blank[c], c] = np.nan
    return df


from utils.answer_values import answer_text  # noqa: E402


def answers_as_text(df: pd.DataFrame, columns) -> pd.DataFrame:
    """``columns`` with every non-text answer as text (answer_text); a copy if anything changes."""
    cols = [c for c in (columns or []) if c in df.columns
            and df[c].map(lambda v: v is not None and not isinstance(v, str) and pd.notna(v)).any()]
    if not cols:
        return df
    df = df.copy()
    for c in cols:
        df[c] = df[c].map(answer_text).astype(object)
    return df


class BaseHandler(ABC):
    """
    Abstract base class of the question handlers.
    
    Every handler processes one question type and offers:
    - analysis/aggregation of the data
    - charts of the results
    - export support
    """
    
    # handler type (overridden in subclasses)
    handler_type: str = "base"
    
    # supported chart types
    supported_charts: list[str] = []
    
    # Answers are categories compared with configured texts (options, scales,
    # labels): numbers from a typed import are turned into those texts first.
    # Handlers that compute with the values themselves set this to False.
    answers_as_text: bool = True
    
    def __init_subclass__(cls, **kwargs):
        """
        Every handler's analyze sees blank cells as missing (blanks_to_nan)
        and, unless ``answers_as_text`` is False, answers as text (answers_as_text).
        """
        super().__init_subclass__(**kwargs)
        analyze = cls.__dict__.get("analyze")
        if analyze is not None and not getattr(analyze, "_blanks_as_missing", False):
            import functools

            @functools.wraps(analyze)
            def wrapped(self, df, *args, **kw):
                df = blanks_to_nan(df, self.columns)
                if self.answers_as_text:
                    df = answers_as_text(df, self.columns)
                return analyze(self, df, *args, **kw)
            wrapped._blanks_as_missing = True
            cls.analyze = wrapped

    def __init__(self, config: dict):
        """
        Initialises the handler.
        
        Args:
            config: configuration of the question group from questions.yaml
        """
        self.config = config
        self.question_id = config.get("id", "unknown")
        self.columns = config.get("columns", [])
        self.label = config.get("label", {})
    
    @abstractmethod
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Runs the analysis.
        
        Args:
            df: DataFrame with the survey data
            lang: language of the labels
        
        Returns:
            AnalysisResult with all results
        """
        pass
    
    @abstractmethod
    def create_chart(
        self,
        result: AnalysisResult,
        chart_type: str,
        lang: str = "de",
        **kwargs
    ) -> plt.Figure:
        """
        Creates a chart.
        
        Args:
            result: analysis result
            chart_type: requested chart type
            lang: language of the labels
            **kwargs: additional parameters
        
        Returns:
            matplotlib figure
        """
        pass
    
    def get_available_charts(self) -> list[str]:
        """The available chart types."""
        return self.config.get("charts", self.supported_charts)
    
    def get_default_chart(self) -> str:
        """The default chart type."""
        default = self.config.get("default_chart")
        if default:
            return default
        charts = self.get_available_charts()
        return charts[0] if charts else "bar_horizontal"
    
    @property
    def source_language(self) -> str:
        """
        Language of the survey's data and configuration texts (answer values,
        option and item labels as they appear in the data). Taken from the
        loaded survey; outside the app the default source language.
        """
        from utils.prompts import source_language
        return source_language()

    def labels_map(self, key: str, lang: str) -> dict:
        """
        Mapping from the labels in the source language (as they appear in the
        data) to the labels in ``lang`` for a per-language list in the
        question configuration (``options``, ``item_labels``, ``row_labels``).
        Empty if ``lang`` is the source language, the lists are missing or
        their lengths differ (logged).
        """
        import logging
        values = self.config.get(key) or {}
        if not isinstance(values, dict):
            return {}
        src = self.source_language
        src_labels = values.get(src, [])
        target = values.get(lang, src_labels)
        if lang == src or not src_labels:
            return {}
        if len(src_labels) != len(target):
            logging.getLogger(__name__).warning(
                f"{self.question_id}: {key}.{src} ({len(src_labels)}) != {key}.{lang} ({len(target)})")
            return {}
        return dict(zip(src_labels, target))

    def get_title(self, lang: str = "de") -> str:
        """The title in the requested language."""
        if isinstance(self.label, dict):
            from utils.languages import localized
            return str(localized(self.label, lang, self.source_language) or self.question_id)
        return str(self.label)
    
    # ------------------------------------------------------------------
    # Summary schema concept (see utils/summary_schema.py)
    # ------------------------------------------------------------------
    
    @classmethod
    def default_summary_schema(cls):
        """
        The default schema of the LLM question summaries of this handler type.
        
        The default implementation returns a schema with the three default
        sections (description / interpretation / segments_text), so the
        handlers without a schema of their own share the same sections.
        
        Subclasses can override it to offer handler-specific sections. Example
        for a NumericHandler:
        
            @classmethod
            def default_summary_schema(cls):
                from utils.summary_schema import SummarySchema, Section
                return SummarySchema(
                    schema_id='numeric_default',
                    handler_type='numeric',
                    sections=[
                        Section(id='central_tendency', label={...}, ...),
                        Section(id='distribution',     label={...}, ...),
                        Section(id='outliers',         label={...}, conditional='has_outliers'),
                        Section(id='segments',         label={...}, conditional='has_segmentation'),
                    ],
                )
        
        The schema id of default schemas follows the convention
        '{handler_type}_default', so the database tells which handler created a
        stored summary.
        """
        from utils.summary_schema import SummarySchema, DEFAULT_THREE_SECTIONS_SCHEMA
        return SummarySchema(
            schema_id=f'{cls.handler_type}_default',
            handler_type=cls.handler_type,
            # The three standard sections (description, interpretation,
            # segment differences) unless a handler defines its own schema.
            sections=list(DEFAULT_THREE_SECTIONS_SCHEMA.sections),
            description=(
                f"Default schema for handler '{cls.handler_type}' — "
                f"the three standard sections."
            ),
        )
    
    def build_summary_context(self,
                              result: 'AnalysisResult',
                              segment_dimension_count: int = 0,
                              has_significant_segments: bool = False) -> dict:
        """
        Builds the condition dict for Section.applicable_sections().
        
        Used by question_summary to decide, before the LLM summary, which
        sections are relevant at all. Subclasses can override it to add
        handler-specific flags (e.g. NumericHandler sets 'has_outliers').
        
        Args:
            result: AnalysisResult from analyze().
            segment_dimension_count: number of active segmentation dimensions.
            has_significant_segments: was at least one significant segment
                difference found?
        
        Returns:
            dict with condition flags, for example:
                {
                    'has_segmentation': True,
                    'has_segmentation_significant': False,
                    'has_clusters': True,
                    'n_categories': 4,
                    'n': 47,
                }
        """
        ctx = {
            'has_segmentation': segment_dimension_count > 0,
            'has_segmentation_significant': has_significant_segments,
            'n': result.n,
        }
        
        # cluster information (mainly relevant for free text, but generic)
        if result.clusters:
            ctx['has_clusters'] = True
            ctx['n_clusters'] = len(result.clusters)
        else:
            ctx['has_clusters'] = False
            ctx['n_clusters'] = 0
        
        # number of categories from result.data, if possible
        if isinstance(result.data, dict):
            ctx['n_categories'] = len(result.data)
        elif isinstance(result.data, pd.DataFrame):
            ctx['n_categories'] = len(result.data)
        else:
            ctx['n_categories'] = 0
        
        return ctx
    
    def get_colors(self) -> dict[str, str]:
        """
        The configured colours.
        
        Automatic colours for known patterns:
        - JA/Yes/Oui -> green
        - NEIN/No/Non -> red
        """
        colors = {}
        
        # option colours from the configuration
        if "option_colors" in self.config:
            colors.update(self.config["option_colors"])
        
        # scale colours from the configuration
        if "scale_colors" in self.config:
            colors.update(self.config["scale_colors"])
        
        # automatic colours for YES/NO if not configured
        positive_keywords = ["ja", "yes", "oui", "stimme zu", "agree", "d'accord", 
                           "trifft zu", "applies", "s'applique", "vorhanden", "exists"]
        negative_keywords = ["nein", "no", "non", "stimme nicht zu", "disagree", 
                           "pas d'accord", "trifft nicht zu", "does not apply", 
                           "nicht vorhanden", "does not exist"]
        
        # colours for known answers (if not in the configuration already)
        from utils.charts import ChartGenerator
        positive_color = ChartGenerator.DEFAULT_COLORS['positive']  # green
        negative_color = ChartGenerator.DEFAULT_COLORS['negative']  # red
        
        # check the options from the configuration
        options = self.config.get('options', {})
        src_options = options.get(self.source_language, []) if isinstance(options, dict) else []
        
        # the whole answer must be one of the words: a part of a word ("no" in
        # "Don't know", "Economics") gives no colour
        for opt in src_options:
            opt_lower = opt.lower().strip()
            if opt not in colors:
                if opt_lower in positive_keywords:
                    colors[opt] = positive_color
                elif opt_lower in negative_keywords:
                    colors[opt] = negative_color
        
        return colors
    
    def validate_columns(self, df: pd.DataFrame) -> tuple[list[str], list[str]]:
        """
        Validates whether the required columns are present.
        
        Args:
            df: DataFrame
        
        Returns:
            tuple (present columns, missing columns)
        """
        existing = [col for col in self.columns if col in df.columns]
        missing = [col for col in self.columns if col not in df.columns]
        return existing, missing
    
    def filter_valid_responses(
        self,
        df: pd.DataFrame,
        columns: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """
        Filters the rows with valid answers.
        
        Args:
            df: DataFrame
            columns: columns to check (default: self.columns)
        
        Returns:
            filtered DataFrame
        """
        if columns is None:
            columns = self.columns
        
        # use only existing columns
        existing = [col for col in columns if col in df.columns]
        
        if not existing:
            return df
        
        # rows with at least one valid answer
        mask = df[existing].notna().any(axis=1)
        return df[mask].copy()
    
    def count_valid_responses(
        self,
        df: pd.DataFrame,
        columns: Optional[list[str]] = None
    ) -> int:
        """
        Counts the valid answers.
        
        Args:
            df: DataFrame
            columns: columns to check
        
        Returns:
            number of valid answers
        """
        filtered = self.filter_valid_responses(df, columns)
        return len(filtered)
    
    def extract_item_name(self, column_name: str) -> str:
        """
        Extracts the item name from a column name.
        
        Example: "Usefulness [Chatbots (e.g. ChatGPT)]"
                  -> "Chatbots (e.g. ChatGPT)"
        
        Args:
            column_name: full column name
        
        Returns:
            extracted item name
        """
        # Configured item name per column (configurations generated from LimeSurvey,
        # whose code-headed exports name the column "G01Q01[SQ001]")
        labels = self.config.get("column_labels") or {}
        if column_name in labels:
            return labels[column_name]
        if "[" in column_name and "]" in column_name:
            start = column_name.index("[") + 1
            end = column_name.rindex("]")
            return column_name[start:end]
        # Columns without the item in their name (SPSS "m1", "m2"; hand-made
        # configurations): the label at the same position in the per-column
        # label list of the source language, if the lists line up
        key = self.per_column_labels
        lists = self.config.get(key) if key else None
        if isinstance(lists, dict) and column_name in self.columns:
            src_list = lists.get(self.source_language) or []
            if len(src_list) == len(self.columns):
                return src_list[self.columns.index(column_name)]
        return column_name
    
    # Configuration list that holds one label per column (by position), if any
    per_column_labels: Optional[str] = None
    
    def generate_description(
        self,
        result: AnalysisResult,
        lang: str = "de"
    ) -> str:
        """
        Writes a textual description of the results.
        
        Args:
            result: analysis result
            lang: language
        
        Returns:
            description text
        """
        # default implementation, can be overridden in subclasses
        return ""
    
    def prepare_export_data(
        self,
        result: AnalysisResult
    ) -> dict:
        """
        Prepares the data for the export.
        
        Args:
            result: analysis result
        
        Returns:
            dictionary ready for the export
        """
        return {
            "question_id": result.question_id,
            "n": result.n,
            "data": result.data,
            "percentages": result.percentages,
            "description": result.description,
            "metadata": result.metadata
        }


class SingleColumnHandler(BaseHandler):
    """Base for handlers with a single column."""
    
    def get_column(self) -> Optional[str]:
        """The (first) column."""
        if self.columns:
            return self.columns[0]
        return None


class MultiColumnHandler(BaseHandler):
    """Base for handlers with several columns (matrix questions)."""
    
    def get_item_labels(self) -> list[str]:
        """The item labels from the column names."""
        return [self.extract_item_name(col) for col in self.columns]
