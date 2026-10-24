# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Segmentation module of the survey analysis tool.

A simple API to filter a DataFrame by segmentation dimensions. The dimensions
are fully configurable: number, ids, data sources and value mappings are read
from the survey configuration (settings.segmentation) and delegated to the
SegmentResolver (utils/segment_resolver.py). Any number of dimensions, also none.

Classes: Segmenter, SegmentInfo, SegmentSelector, SegmentationManager,
create_segmenter. A segment type the SegmentResolver does not know returns
None or empty lists (no crash).

Calling conventions:
- segment_type is the id of a configured dimension.
- segment_value is the concrete value after bucketing (e.g. "Student", "under 25");
  with identity bucketing it is the raw answer value.
"""

import logging
from typing import Optional, List, Dict
from dataclasses import dataclass

import pandas as pd

from utils.segment_resolver import (
    SegmentResolver, Dimension,
    SEGMENT_COLUMN_PREFIX, SEGMENT_COLUMN_SUFFIX,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data models
# =============================================================================

@dataclass
class SegmentInfo:
    """
    Information on a segment.
    
    Attributes:
        filter_type: dimension id (e.g. "role", "faculty", "all").
        filter_value: filter value (after bucketing), or None for "all".
        n: number of data points in the segment.
        warning: optional warning (e.g. a sample that is too small).
        show_results: whether results should be shown (False with n < minimum_threshold).
    """
    filter_type: str
    filter_value: Optional[str]
    n: int
    warning: Optional[str] = None
    show_results: bool = True


# =============================================================================
# Segmenter
# =============================================================================

class Segmenter:
    """
    Segments the survey data (based on the SegmentResolver).
    """
    
    def __init__(self, config: dict):
        """
        Initialises the segmenter.
        
        Args:
            config: configuration dict in one of two formats:
                - the settings dict: {"segmentation": {...}, "sample_warnings": {...}}
                - the complete configuration: {"settings": {...}, "question_groups": {...}}
                
                Both are detected and the SegmentResolver is instantiated
                accordingly.
        """
        self.config = config
        self.warning_threshold = 10
        self.minimum_threshold = 5
        
        # take the thresholds from the configuration
        # (accepts both configuration structures)
        sw_config = config.get("sample_warnings") or \
                    config.get("settings", {}).get("sample_warnings", {})
        if sw_config:
            self.warning_threshold = sw_config.get("warning_threshold", 10)
            self.minimum_threshold = sw_config.get("minimum_threshold", 5)
        
        # The resolver needs the full configuration (settings and
        # question_groups) to resolve source_question. Configuration errors
        # propagate: silently analysing without segments would produce
        # results that look complete but are not.
        if 'question_groups' not in config and 'settings' not in config:
            raise ValueError(
                "Segmenter expects the full survey configuration "
                "(with 'settings' and 'question_groups')"
            )
        self._resolver = SegmentResolver(config)
    
    # ----- Public API -----
    
    def list_segment_types(self) -> List[str]:
        """
        The ids of the active segmentation dimensions, from the survey configuration.
        """
        if self._resolver is None:
            return []
        return self._resolver.dimension_ids()
    
    def get_segment_label(self, segment_type: str, lang: str = 'de') -> str:
        """Multilingual label of a dimension."""
        if self._resolver is None:
            return segment_type
        dim = self._resolver.get_dimension(segment_type)
        if dim is None:
            return segment_type
        return dim.get_label(lang)
    
    def get_segment_column(self, segment_type: str) -> Optional[str]:
        """
        The column name of a segment type.
        
        Returns the materialised column name (__segment_<id>__) if the dimension
        exists. Callers that use the column themselves should call
        materialize() first.
        
        Args:
            segment_type: dimension id.
        
        Returns:
            column name, or None for an unknown dimension.
        """
        if self._resolver is None:
            return None
        dim = self._resolver.get_dimension(segment_type)
        if dim is None:
            return None
        return self._resolver.segment_column(segment_type)
    
    def get_available_values(
        self,
        df: pd.DataFrame,
        segment_type: str
    ) -> List[str]:
        """
        The values of a dimension that occur in the data.
        
        Order: by Dimension.order, or by frequency (descending) if order is
        not defined.
        
        Args:
            df: DataFrame; materialised automatically if not done yet (idempotent).
            segment_type: dimension id.
        
        Returns:
            list of the segment values (without NA), in the defined order.
        """
        if self._resolver is None:
            return []
        
        df_mat = self._ensure_materialized(df)
        return self._resolver.get_segment_values(df_mat, segment_type)
    
    def filter_data(
        self,
        df: pd.DataFrame,
        segment_type: str,
        segment_value: str
    ) -> pd.DataFrame:
        """
        Filters the DataFrame to a segment value.
        
        Args:
            df: DataFrame.
            segment_type: dimension id.
            segment_value: value after bucketing.
        
        Returns:
            filtered DataFrame (a copy).
        """
        if self._resolver is None:
            logger.warning(f"No resolver available — cannot filter on '{segment_type}'")
            return df.copy()
        
        col = self._resolver.segment_column(segment_type)
        df_mat = self._ensure_materialized(df)
        if col not in df_mat.columns:
            logger.warning(f"Segment column '{col}' not found")
            return df_mat.copy()
        
        return df_mat[df_mat[col] == segment_value].copy()
    
    def get_segment_info(
        self,
        df: pd.DataFrame,
        segment_type: Optional[str] = None,
        segment_value: Optional[str] = None
    ) -> SegmentInfo:
        """
        Information on a segment (n, warnings).
        """
        if segment_type and segment_value:
            filtered_df = self.filter_data(df, segment_type, segment_value)
            n = len(filtered_df)
        else:
            n = len(df)
            segment_type = "all"
            segment_value = None
        
        warning = None
        show_results = True
        
        if n < self.minimum_threshold:
            warning = f"n < {self.minimum_threshold}"   # language-neutral, like the segment results
            show_results = False
        elif n < self.warning_threshold:
            warning = f"n < {self.warning_threshold}"
        
        return SegmentInfo(
            filter_type=segment_type,
            filter_value=segment_value,
            n=n,
            warning=warning,
            show_results=show_results,
        )
    
    def apply_segment(
        self,
        df: pd.DataFrame,
        segment_type: Optional[str] = None,
        segment_value: Optional[str] = None
    ) -> tuple:
        """
        Applies the segmentation and returns information.
        
        Returns:
            tuple (filtered DataFrame, SegmentInfo).
        """
        if segment_type and segment_value:
            filtered_df = self.filter_data(df, segment_type, segment_value)
        else:
            filtered_df = df.copy()
        
        info = self.get_segment_info(df, segment_type, segment_value)
        return filtered_df, info
    
    def get_all_segments_summary(
        self,
        df: pd.DataFrame
    ) -> Dict[str, Dict[str, int]]:
        """
        Overview of all dimensions and the distribution of their values.
        """
        summary = {"all": {"total": len(df)}}
        if self._resolver is None:
            return summary
        
        df_mat = self._ensure_materialized(df)
        for dim in self._resolver.list_dimensions():
            col = self._resolver.segment_column(dim.id)
            if col in df_mat.columns:
                counts = df_mat[col].dropna().value_counts().to_dict()
                summary[dim.id] = counts
        return summary
    
    def materialize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds the segment columns to the DataFrame.
        
        Called automatically, but can also be used explicitly to materialise
        once for later calls.
        """
        if self._resolver is None:
            return df.copy()
        return self._resolver.materialize(df)
    
    # ----- Internal -----
    
    def _ensure_materialized(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        The DataFrame with all segment columns.
        
        If the DataFrame has segment columns already (heuristic: at least one
        column with the prefix __segment_), it is returned unchanged, otherwise
        materialised.
        """
        if self._resolver is None:
            return df
        # materialised already?
        has_seg_cols = any(
            c.startswith(SEGMENT_COLUMN_PREFIX) and c.endswith(SEGMENT_COLUMN_SUFFIX)
            for c in df.columns
        )
        if has_seg_cols:
            return df
        return self._resolver.materialize(df)


# =============================================================================
# SegmentSelector (interface component)
# =============================================================================

class SegmentSelector:
    """Interface component to choose a segment."""
    
    def __init__(self, segmenter: Segmenter):
        self.segmenter = segmenter
    
    def get_filter_options(
        self,
        df: pd.DataFrame,
        include_all: bool = True
    ) -> Dict[str, List[str]]:
        """
        All available filter options, over all active dimensions of the
        survey configuration.
        """
        options = {}
        for seg_type in self.segmenter.list_segment_types():
            values = self.segmenter.get_available_values(df, seg_type)
            if include_all:
                values = ["All"] + values
            if values:
                options[seg_type] = values
        return options
    
    def format_option_with_n(
        self,
        df: pd.DataFrame,
        segment_type: str,
        value: str
    ) -> str:
        """Formats an option with its sample size: 'value (n=123)'."""
        if value == "All":
            return f"All (n={len(df)})"
        info = self.segmenter.get_segment_info(df, segment_type, value)
        return f"{value} (n={info.n})"


# =============================================================================
# Factory + Manager
# =============================================================================

def create_segmenter(config: dict) -> Segmenter:
    """Factory function of the segmenter (complete survey configuration)."""
    return Segmenter(config)


class SegmentationManager:
    """
    Manager class for the segmentation with a label-based API.
    """
    
    def __init__(self, config: dict):
        """
        Args:
            config: complete configuration from questions.yaml.
        """
        self.config = config
        self.segmenter = create_segmenter(config)
        
        # Label -> Dimension-ID Mapping
        self._label_to_type = {}
        if self.segmenter._resolver is not None:
            for dim in self.segmenter._resolver.list_dimensions():
                # Lookup by the id and by the label in every configured language
                self._label_to_type.setdefault(dim.id, dim.id)
                for other_label in (dim.label or {}).values():
                    if other_label and other_label not in self._label_to_type:
                        self._label_to_type[other_label] = dim.id
    
    def apply_segment(
        self,
        df: pd.DataFrame,
        label: str,
        value: str
    ) -> SegmentInfo:
        """Applies a segmentation by label."""
        segment_type = self._label_to_type.get(label, label.lower())
        return self.segmenter.get_segment_info(df, segment_type, value)
    
    def filter_data(
        self,
        df: pd.DataFrame,
        segment_info: SegmentInfo
    ) -> pd.DataFrame:
        """Filters the DataFrame by a SegmentInfo."""
        if segment_info.filter_type == "all" or not segment_info.filter_value:
            return df.copy()
        return self.segmenter.filter_data(
            df, segment_info.filter_type, segment_info.filter_value
        )
    
    def get_filter_options(
        self,
        df: pd.DataFrame
    ) -> Dict[str, List[str]]:
        """All filter options per dimension."""
        selector = SegmentSelector(self.segmenter)
        return selector.get_filter_options(df, include_all=False)
