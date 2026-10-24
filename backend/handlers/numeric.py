# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Numeric handler for numeric questions.

Processes questions such as LimeSurvey type N (numerical input) and analyses
quantitative answers (e.g. "How old are you?") with descriptive statistics.

Delivers:
- standard statistics: n, mean, median, std, min, max, q25, q75
- histogram with a configurable number of bins
- box plot with outlier detection (IQR-based)
- segmented analysis: statistics per segment + Kruskal-Wallis test
- default summary schema with 4 sections:
  * central_tendency (location and spread)
  * distribution (shape of the distribution)
  * outliers (notable values), conditional on has_outliers
  * segments_text (segment differences), conditional

Interface:
- inherits from SingleColumnHandler like all other handlers.
- returns an AnalysisResult with data={statistics dict}, percentages=None.
- result.metadata['numeric_stats'] additionally holds a NumericResult object
  for the detailed tables of the Word export.

Configuration in questions.yaml:

    question_groups:
      age:
        handler: numeric
        columns: ["age"]
        numeric:
          unit: "years"                   # for the axis label
          valid_range: {min: 16, max: 99} # everything outside -> invalid
          bin_count: 10                   # for the histogram
          decimals: 0                     # display format (number of decimals)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from .base import SingleColumnHandler, AnalysisResult
from utils.charts import ChartGenerator
from utils.languages import catalog_texts
from utils.numeric_coerce import coerce_series

logger = logging.getLogger(__name__)


# =============================================================================
# Data model
# =============================================================================

@dataclass
class NumericResult:
    """
    Result of a numeric analysis, overall as well as per segment.
    
    Delivered in AnalysisResult.metadata['numeric_stats'], so the Word exporter
    can render statistics tables and the LLM summary can fill the
    location/spread/outlier sections.
    """
    question_id: str
    n: int                           # number of valid values
    n_invalid: int                   # not numeric / out of range
    mean: float
    median: float
    std: float
    min_value: float
    max_value: float
    q25: float
    q75: float
    histogram_bins: List[float] = field(default_factory=list)
    histogram_counts: List[int] = field(default_factory=list)
    outliers: List[float] = field(default_factory=list)
    segment_stats: Dict[str, Dict[str, 'NumericResult']] = field(default_factory=dict)
    significance: Optional[Dict[str, Any]] = None  # {dim_id: {'h': ..., 'p_value': ...}}
    
    # configuration, needed for the formatting in the Word export
    decimals: int = 1
    unit: str = ""
    
    def has_outliers(self) -> bool:
        return len(self.outliers) > 0
    
    def has_segmentation(self) -> bool:
        return any(self.segment_stats.values())
    
    def has_significant_segments(self) -> bool:
        if not self.significance:
            return False
        return any(
            sig.get('is_significant', False)
            for sig in self.significance.values()
        )
    
    def to_summary_dict(self) -> dict:
        """Compact statistics summary for Word tables / LLM prompts."""
        return {
            'n': self.n,
            'mean': round(self.mean, self.decimals),
            'median': round(self.median, self.decimals),
            'std': round(self.std, self.decimals),
            'min': round(self.min_value, self.decimals),
            'max': round(self.max_value, self.decimals),
            'q25': round(self.q25, self.decimals),
            'q75': round(self.q75, self.decimals),
        }
    
    def format_value(self, value: float) -> str:
        """Formats a single value with its unit."""
        if pd.isna(value):
            return '—'
        formatted = f"{value:.{self.decimals}f}"
        if self.unit:
            formatted = f"{formatted} {self.unit}"
        return formatted


# =============================================================================
# Handler
# =============================================================================

class NumericHandler(SingleColumnHandler):
    """
    Handler for numeric questions (LimeSurvey type N).
    
    Extends SingleColumnHandler with numeric coercion and statistics.
    """
    
    answers_as_text = False  # computes with the values (numbers, dates)
    handler_type = "numeric"
    supported_charts = ["histogram", "box_plot", "segment_means_bar"]
    
    # ----- Konfigurationszugriff -----
    
    def _get_numeric_config(self) -> dict:
        return self.config.get('numeric', {}) or {}
    
    def _get_unit(self) -> str:
        return self._get_numeric_config().get('unit', '')
    
    def _get_decimals(self) -> int:
        return int(self._get_numeric_config().get('decimals', 1))
    
    def _get_bin_count(self) -> int:
        return int(self._get_numeric_config().get('bin_count', 10))
    
    def _get_valid_range(self) -> Tuple[Optional[float], Optional[float]]:
        vr = self._get_numeric_config().get('valid_range') or {}
        return vr.get('min'), vr.get('max')
    
    # ----- Hauptlogik: analyze() -----
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de",
        **kwargs,  # ignored (e.g. db, import_id of the free-text pipeline)
    ) -> AnalysisResult:
        """
        Analyses a numeric question.
        
        Args:
            df: DataFrame with the survey data.
            lang: language (for labels in the AnalysisResult, not used further at present).
            **kwargs: additional arguments (ignored; compatible with the
                      signature of the free-text handler, which expects db/import_id).
        
        Returns:
            AnalysisResult with:
            - data: dict with summary statistics (mean/median/std/...)
            - n: number of valid values
            - metadata['numeric_stats']: NumericResult object for detailed access
            - warnings: with too little data or a high share of invalid values
        """
        col = self.get_column()
        if not col:
            return AnalysisResult(
                question_id=self.question_id, n=0, data={},
                warnings=["No column configured"],
            )
        
        if col not in df.columns:
            return AnalysisResult(
                question_id=self.question_id, n=0, data={},
                warnings=[f"Column '{col}' not in the data"],
            )
        
        # 1. coerce the raw values (decimal commas, suffixes etc. are handled)
        coerced = coerce_series(df[col])
        
        # 2. apply the range filter, if configured
        v_min, v_max = self._get_valid_range()
        in_range = pd.Series([True] * len(coerced), index=coerced.index)
        if v_min is not None:
            in_range &= (coerced >= v_min) | coerced.isna()
        if v_max is not None:
            in_range &= (coerced <= v_max) | coerced.isna()
        
        # NaN stays NaN; out of range -> marked as NaN
        coerced_filtered = coerced.where(in_range, other=np.nan)
        
        # 3. compute the statistics
        valid = coerced_filtered.dropna()
        n_valid = len(valid)
        # invalid: given but not a number or out of range (an empty cell is no answer, not invalid)
        answered = df[col].notna()
        n_invalid = int(answered.sum()) - n_valid
        n_answered = int(answered.sum())
        
        if n_valid == 0:
            return AnalysisResult(
                question_id=self.question_id, n=0,
                data={'n': 0, 'n_invalid': n_invalid},
                warnings=[f"No valid numeric values (n_invalid={n_invalid})"],
            )
        
        result = self._compute_stats(valid, n_invalid)
        
        # 4. assemble the AnalysisResult
        warnings = []
        if n_invalid > 0:
            invalid_pct = n_invalid / max(1, n_answered) * 100
            if invalid_pct >= 30:
                warnings.append(
                    f"High share of invalid values: {n_invalid}/{n_answered} "
                    f"({invalid_pct:.0f}%)"
                )
            else:
                logger.info(
                    f"{self.question_id}: {n_invalid}/{n_answered} "
                    f"({invalid_pct:.0f}%) invalid values ignored"
                )
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n_valid,
            data=result.to_summary_dict(),
            percentages=None,
            warnings=warnings,
            metadata={
                'numeric_stats': result,
                'n_invalid': n_invalid,
            },
        )
    
    def _compute_stats(self, valid_values: pd.Series, n_invalid: int) -> NumericResult:
        """
        Computes all statistics from an already filtered/coerced Series.
        """
        bin_count = self._get_bin_count()
        decimals = self._get_decimals()
        unit = self._get_unit()
        
        n = len(valid_values)
        mean = float(valid_values.mean())
        median = float(valid_values.median())
        std = float(valid_values.std()) if n > 1 else 0.0
        min_v = float(valid_values.min())
        max_v = float(valid_values.max())
        q25 = float(valid_values.quantile(0.25))
        q75 = float(valid_values.quantile(0.75))
        
        # histogram
        if min_v == max_v:
            # all values equal -> one bin
            hist_bins = [min_v, min_v + 1]
            hist_counts = [n]
        else:
            # numpy.histogram: bin_count Bins
            counts, bins = np.histogram(valid_values, bins=bin_count)
            hist_bins = [float(x) for x in bins]
            hist_counts = [int(c) for c in counts]
        
        # outlier detection (IQR-based: 1.5*IQR rule)
        outliers: List[float] = []
        if n >= 4:  # the IQR needs at least 4 values to make sense
            iqr = q75 - q25
            if iqr > 0:
                lower_bound = q25 - 1.5 * iqr
                upper_bound = q75 + 1.5 * iqr
                outliers = [
                    float(v) for v in valid_values
                    if v < lower_bound or v > upper_bound
                ]
        
        return NumericResult(
            question_id=self.question_id,
            n=n,
            n_invalid=n_invalid,
            mean=mean,
            median=median,
            std=std,
            min_value=min_v,
            max_value=max_v,
            q25=q25,
            q75=q75,
            histogram_bins=hist_bins,
            histogram_counts=hist_counts,
            outliers=outliers,
            decimals=decimals,
            unit=unit,
        )
    
    # ----- segmented analysis -----
    
    def analyze_segmented(
        self,
        df: pd.DataFrame,
        segment_column: str,
        lang: str = "de",
    ) -> Dict[str, NumericResult]:
        """
        Computes a NumericResult per segment value.
        
        Args:
            df: DataFrame, possibly materialised by the SegmentResolver already.
            segment_column: column name (e.g. '__segment_role__' or a direct column).
            lang: language.
        
        Returns:
            {segment_value: NumericResult}, sorted by segment value.
        """
        if segment_column not in df.columns:
            logger.warning(f"Segment column '{segment_column}' not in the DataFrame")
            return {}
        
        col = self.get_column()
        if not col or col not in df.columns:
            return {}
        
        results: Dict[str, NumericResult] = {}
        for seg_value, group in df.groupby(segment_column, dropna=True):
            if pd.isna(seg_value) or str(seg_value).strip() == '':
                continue
            
            sub_result = self.analyze(group, lang=lang)
            if sub_result.n > 0 and 'numeric_stats' in sub_result.metadata:
                results[str(seg_value)] = sub_result.metadata['numeric_stats']
        
        return results
    
    # ----- Charts -----
    
    def create_chart(
        self,
        result: AnalysisResult,
        chart_type: str = "histogram",
        lang: str = "de",
        **kwargs,
    ):
        """
        Creates a chart from an AnalysisResult (numeric).
        
        Returns:
            matplotlib.figure.Figure, or None (on errors / without data).
        """
        if result.n == 0:
            logger.info(f"{self.question_id}: no data for the chart")
            return None
        
        numeric_stats: Optional[NumericResult] = result.metadata.get('numeric_stats')
        if numeric_stats is None:
            logger.warning(f"{self.question_id}: no numeric_stats in the metadata")
            return None
        
        if chart_type not in self.supported_charts:
            logger.warning(
                f"{self.question_id}: chart_type '{chart_type}' not supported; "
                f"available: {self.supported_charts}"
            )
            chart_type = self.supported_charts[0]
        
        chart_gen = ChartGenerator(colors=self.get_colors())
        title = self.get_title(lang)
        
        if chart_type == 'histogram':
            return chart_gen.histogram(
                bins=numeric_stats.histogram_bins,
                counts=numeric_stats.histogram_counts,
                title=title,
                xlabel=self._get_unit() or '',
                ylabel=__import__('utils.languages', fromlist=['text']).text('chart.axis.freq', lang),
                decimals=numeric_stats.decimals,
            )
        elif chart_type == 'box_plot':
            # a box plot needs min/q25/median/q75/max, reconstructed from the
            # statistics
            return chart_gen.box_plot(
                stats=numeric_stats.to_summary_dict(),
                title=title,
                ylabel=self._get_unit() or '',
                outliers=numeric_stats.outliers,
            )
        elif chart_type == 'segment_means_bar':
            # needs segment_stats; without a segmentation no sensible chart
            if not numeric_stats.segment_stats:
                logger.info(
                    f"{self.question_id}: segment_means_bar requested, but "
                    f"no segment_stats — fallback to histogram"
                )
                return chart_gen.histogram(
                    bins=numeric_stats.histogram_bins,
                    counts=numeric_stats.histogram_counts,
                    title=title,
                    xlabel=self._get_unit() or '',
                    decimals=numeric_stats.decimals,
                )
            
            # take the first available dimension (simplification; the pipeline can
            # request a chart per dimension if needed)
            first_dim_id = next(iter(numeric_stats.segment_stats))
            seg_dict = numeric_stats.segment_stats[first_dim_id]
            # medians and quartiles: the segment tests compare distributions
            # (Kruskal-Wallis), not means
            stats_for_chart = {
                seg_value: {'mean': nr.median, 'low': nr.q25, 'high': nr.q75, 'n': nr.n}
                for seg_value, nr in seg_dict.items()
            }
            from utils.languages import localized, text
            from utils.segment_resolver import SegmentResolver
            from utils import prompts
            try:
                dim = SegmentResolver(prompts.current_config() or {}).get_dimension(first_dim_id)
                dim_label = str(localized(dim.label, lang, prompts.source_language()) or first_dim_id)
            except Exception:
                dim_label = first_dim_id
            title = f"{title} – {dim_label}"
            note = text("chart.medians", lang, dimension=dim_label)
            return chart_gen.segment_means_bar(
                note=note,
                segment_stats=stats_for_chart,
                title=title,
                xlabel=self._get_unit() or '',
                decimals=numeric_stats.decimals,
            )
        
        return None
    
    # ----- Default-Summary-Schema -----
    
    @classmethod
    def default_summary_schema(cls):
        """
        Own default schema for numeric questions: 4 sections instead of the default 3.
        
        Sections:
        - central_tendency: location and spread in prose
        - distribution: shape of the distribution / skewness
        - outliers: outliers or implausible values (conditional)
        - segments_text: segment differences (conditional)
        """
        from utils.summary_schema import SummarySchema, Section, builtin_instruction
        return SummarySchema(
            schema_id='numeric_default',
            handler_type='numeric',
            description="Default schema for numeric questions: central tendency / distribution / outliers / segments.",
            sections=[
                Section(
                    id='central_tendency',
                    label=catalog_texts('summary_section.central_tendency'),
                    prompt_instruction=builtin_instruction('central_tendency'),
                    length_hint='short',
                    required=True,
                ),
                Section(
                    id='distribution',
                    label=catalog_texts('summary_section.distribution'),
                    prompt_instruction=builtin_instruction('distribution'),
                    length_hint='short',
                    required=True,
                ),
                Section(
                    id='outliers',
                    label=catalog_texts('summary_section.outliers'),
                    prompt_instruction=builtin_instruction('outliers'),
                    length_hint='short',
                    conditional='has_outliers',
                    required=False,
                ),
                Section(
                    id='segments_text',
                    label=catalog_texts('summary_section.segments_text'),
                    prompt_instruction=builtin_instruction('segments_numeric'),
                    length_hint='medium',
                    conditional='has_segmentation_significant',
                    required=False,
                ),
            ],
        )
    
    # ----- build summary context (for applicable_sections) -----
    
    def build_summary_context(
        self,
        result: AnalysisResult,
        segment_dimension_count: int = 0,
        has_significant_segments: bool = False,
    ) -> dict:
        """
        Extends the base context with numeric flags:
        - has_outliers: True if IQR outliers were found
        """
        ctx = super().build_summary_context(
            result, segment_dimension_count, has_significant_segments
        )
        
        numeric_stats: Optional[NumericResult] = result.metadata.get('numeric_stats')
        if numeric_stats is not None:
            ctx['has_outliers'] = numeric_stats.has_outliers()
            ctx['n_outliers'] = len(numeric_stats.outliers)
        else:
            ctx['has_outliers'] = False
            ctx['n_outliers'] = 0
        
        return ctx
