# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Segmented analysis of the survey analysis tool: analyses per segment and
comparison tables.

The dimensions come from the segmenter (utils/segmentation.py) and thus from
the SegmentResolver (utils/segment_resolver.py): number, ids, labels, buckets,
all from the survey configuration. Segment-specific clusters
(_analyze_segment_specific_clusters) are created for every dimension with
enable_specific_clusters=True.
"""

import logging
from typing import Optional, Any
from dataclasses import dataclass, field

import pandas as pd

from handlers import get_handler
from handlers.base import AnalysisResult
from utils.languages import text as _text
from utils.segmentation import Segmenter, create_segmenter

logger = logging.getLogger(__name__)


def _src_text(value, lang=None, default=""):
    """Configuration text in ``lang`` (default: the source language), falling back to the source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    return localized(value, lang or src, src, default=default)


@dataclass
class SegmentResult:
    """Result of a single segment."""
    segment_type: str  # dimension id from segmentation.dimensions
    segment_value: str  # bucket value (e.g. "Student", "STEM", "under 25")
    n: int
    warning: Optional[str]  # z.B. "n < 10"
    data: dict[str, Any]  # result data (answer -> count)
    percentages: dict[str, float]  # answer -> percent


@dataclass
class ComparisonTable:
    """Comparison table of a segment."""
    segment_type: str  # Dimension-ID
    segment_label: str  # multilingual label, e.g. "By role"
    segments: list[SegmentResult]  # list of the segment results
    row_labels: list[str]  # row labels (answer options)
    
    def to_matrix(self) -> list[list[str]]:
        """
        Converts into a matrix for the Word export.
        
        Returns:
            2D list: [[header], [row1], [row2], ...]
        """
        # header row
        header = [""]
        for seg in self.segments:
            label = seg.segment_value
            if seg.warning:
                label += f" (n={seg.n}, <10)"
            else:
                label += f" (n={seg.n})"
            header.append(label)
        
        # data rows
        rows = [header]
        for row_label in self.row_labels:
            row = [row_label]
            for seg in self.segments:
                pct = seg.percentages.get(row_label, 0) if seg.percentages else 0
                row.append(f"{pct:.0f}%")
            rows.append(row)
        
        return rows


@dataclass
class FreetextClusterComparison:
    """Cluster comparison of free-text questions."""
    cluster_names: list[str]  # names of the clusters
    segment_type: str
    segment_label: str
    segments: list[SegmentResult]  # distribution per segment
    
    def to_matrix(self) -> list[list[str]]:
        """Converts into a matrix for the Word export."""
        header = ["Cluster"]
        for seg in self.segments:
            label = seg.segment_value
            if seg.warning:
                label += f" (n={seg.n}, <10)"
            else:
                label += f" (n={seg.n})"
            header.append(label)
        
        rows = [header]
        for cluster_name in self.cluster_names:
            row = [cluster_name]
            for seg in self.segments:
                pct = seg.percentages.get(cluster_name, 0) if seg.percentages else 0
                row.append(f"{pct:.0f}%")
            rows.append(row)
        
        return rows


@dataclass 
class SegmentedFreetextResult:
    """Result of a segment-specific free-text analysis."""
    segment_type: str
    segment_value: str
    n_items: int
    n_responses: int
    warning: Optional[str]
    clusters: list[dict]  # clusters with name, count, percentage, examples


@dataclass
class QuestionAnalysisComplete:
    """Complete analysis of a question with all segments."""
    question_id: str
    question_text: str
    handler_type: str
    
    # overall result
    total_result: AnalysisResult
    total_chart: Any  # matplotlib Figure
    
    # comparison tables
    comparison_tables: list[ComparisonTable]
    
    # only for free text
    cluster_comparisons: list[FreetextClusterComparison] = field(default_factory=list)
    segment_specific_clusters: list[SegmentedFreetextResult] = field(default_factory=list)


class SegmentedAnalyzer:
    """
    Runs segmented analyses.
    
    Creates for every question:
    - the overall result
    - a comparison table per configured dimension
    - (free text) the cluster distribution per segment
    - (free text) segment-specific clusters for dimensions with
      enable_specific_clusters=True
    """
    
    # the segment types and labels come from the segmenter (survey configuration);
    # these properties return an empty value as long as it is not initialised
    
    @property
    def SEGMENT_TYPES(self) -> list[str]:  # noqa: N802 (Backwards-Compat)
        """List of the active dimension ids in the configured order."""
        if self.segmenter is None:
            return []
        return self.segmenter.list_segment_types()
    
    @property
    def SEGMENT_LABELS(self) -> dict[str, dict[str, str]]:  # noqa: N802
        """
        {dimension_id: {lang: label}}, with the prefix "Nach " (de) / "Par " (fr) /
        "By " (en) before the label of the dimension.
        """
        if self.segmenter is None:
            return {}
        from utils.languages import get_output_languages, get_source_language, text
        # the languages of the analysed survey (this analyzer's configuration)
        config = self.config or {}
        langs = list(dict.fromkeys([get_source_language(config)] + get_output_languages(config)))
        prefixes = {lang: text("report.segment_by", lang, dimension="") for lang in langs}
        labels = {}
        for dim_id in self.segmenter.list_segment_types():
            dim = self.segmenter._resolver.get_dimension(dim_id)
            if dim is None:
                continue
            entry = {}
            for lang in langs:
                base = _src_text(dim.label, lang, dim_id)
                # prefix only if the label has none yet
                if not any(p and base.startswith(p) for p in prefixes.values()):
                    entry[lang] = text("report.segment_by", lang, dimension=base)
                else:
                    entry[lang] = base
            labels[dim_id] = entry
        return labels
    
    def _segment_label(self, segment_type: str, lang: str) -> str:
        """
        The display label of a dimension in a language.
        
        Falls back step by step, finally to the dimension id, so any
        survey-specific dimension works.
        """
        labels = self.SEGMENT_LABELS
        if segment_type in labels:
            return _src_text(labels[segment_type], lang, segment_type)
        return segment_type
    
    def __init__(self, config: dict, df: pd.DataFrame, db=None, import_id=None):
        """
        Initialises the analyzer.
        
        Args:
            config: complete configuration from questions.yaml
            df: DataFrame with the survey data
            db: database connection (for free-text items)
            import_id: import id (for free-text items)
        """
        self.config = config
        self.db = db
        self.import_id = import_id
        self.segmenter = create_segmenter(config)
        sw = (config or {}).get('settings', {}).get('sample_warnings', {}) or {}
        self.warning_threshold = int(sw.get('warning_threshold', 10))
        
        # materialise the segment columns once; afterwards every caller
        # (filter_data, get_available_values etc.) reads the __segment_<id>__
        # columns instead of bucketing again on every call.
        self.df = self.segmenter.materialize(df) if self.segmenter else df
    
    def analyze_question_complete(
        self,
        question_id: str,
        lang: str = "de"
    ) -> QuestionAnalysisComplete:
        """
        Runs the complete analysis of a question.
        
        Args:
            question_id: question id
            lang: language
        
        Returns:
            QuestionAnalysisComplete with all results
        """
        qconfig = self.config.get('question_groups', {}).get(question_id, {})
        handler_type = qconfig.get('handler', 'single_choice')
        question_text = qconfig.get('label', {}).get(lang, question_id)
        
        logger.info(f"Starting the complete analysis of {question_id} ({handler_type})")
        
        # create the handler
        handler = get_handler(handler_type, question_id, qconfig)
        if not handler:
            raise ValueError(f"No handler for {handler_type}")
        
        # 1. overall analysis
        if handler_type == 'freetext':
            total_result = handler.analyze(
                self.df, lang=lang, db=self.db, import_id=self.import_id
            )
        else:
            total_result = handler.analyze(self.df, lang=lang)
        
        # overall chart
        default_chart = qconfig.get('default_chart', 'bar_horizontal')
        total_chart = handler.create_chart(total_result, default_chart, lang=lang)
        
        # 2. comparison tables for every segment
        comparison_tables = []
        for seg_type in self.SEGMENT_TYPES:
            table = self._create_comparison_table(
                question_id, qconfig, handler_type, handler, seg_type, lang
            )
            if table:
                comparison_tables.append(table)
        
        # 3. free-text analyses
        cluster_comparisons = []
        segment_specific_clusters = []
        
        if handler_type == 'freetext' and total_result.clusters:
            # cluster distribution per segment (all dimensions)
            for seg_type in self.SEGMENT_TYPES:
                comparison = self._create_cluster_comparison(
                    question_id, qconfig, total_result.clusters, seg_type, lang
                )
                if comparison:
                    cluster_comparisons.append(comparison)
            
            # segment-specific clusters (all dimensions with enable_specific_clusters=True)
            segment_specific_clusters = self._analyze_segment_specific_clusters(
                question_id, qconfig, handler, lang
            )
        
        return QuestionAnalysisComplete(
            question_id=question_id,
            question_text=question_text,
            handler_type=handler_type,
            total_result=total_result,
            total_chart=total_chart,
            comparison_tables=comparison_tables,
            cluster_comparisons=cluster_comparisons,
            segment_specific_clusters=segment_specific_clusters
        )
    
    def _create_comparison_table(
        self,
        question_id: str,
        qconfig: dict,
        handler_type: str,
        handler,
        segment_type: str,
        lang: str
    ) -> Optional[ComparisonTable]:
        """Creates a comparison table for a segment type."""
        
        # determine the segment values
        segment_values = self.segmenter.get_available_values(self.df, segment_type)
        if not segment_values:
            logger.warning(f"No values for the segment {segment_type}")
            return None
        
        segments = []
        all_row_labels = set()
        
        for value in segment_values:
            # filter the data
            filtered_df = self.segmenter.filter_data(self.df, segment_type, value)
            n = len(filtered_df)
            
            if n == 0:
                continue
            
            # warning for a small n
            warning = f"n < {self.warning_threshold}" if n < self.warning_threshold else None
            
            # analysis on the filtered DataFrame
            try:
                if handler_type == 'freetext':
                    # free text: filter the items from the database
                    result = handler.analyze(
                        filtered_df, lang=lang, db=self.db, import_id=self.import_id
                    )
                else:
                    result = handler.analyze(filtered_df, lang=lang)
                
                # extract the data
                if isinstance(result.data, pd.DataFrame):
                    data = result.data.to_dict()
                elif isinstance(result.data, dict):
                    data = result.data
                else:
                    data = {}
                
                percentages = result.percentages or {}
                
                # collect the row labels
                all_row_labels.update(data.keys())
                
                segments.append(SegmentResult(
                    segment_type=segment_type,
                    segment_value=value,
                    n=n,
                    warning=warning,
                    data=data,
                    percentages=percentages
                ))
                
            except Exception as e:
                logger.warning(f"Error in the analysis of {segment_type}={value}: {e}")
                continue
        
        if not segments:
            return None
        
        # sort the row labels (by overall frequency)
        row_labels = self._sort_row_labels(all_row_labels, segments)
        
        return ComparisonTable(
            segment_type=segment_type,
            segment_label=self._segment_label(segment_type, lang),
            segments=segments,
            row_labels=row_labels
        )
    
    def _sort_row_labels(
        self, 
        labels: set, 
        segments: list[SegmentResult]
    ) -> list[str]:
        """Sorts the row labels by overall frequency."""
        # sum over all segments
        totals = {}
        for label in labels:
            total = sum(seg.data.get(label, 0) for seg in segments)
            totals[label] = total
        
        # sort descending
        return sorted(labels, key=lambda x: totals.get(x, 0), reverse=True)
    
    def _create_cluster_comparison(
        self,
        question_id: str,
        qconfig: dict,
        clusters: list[str],
        segment_type: str,
        lang: str
    ) -> Optional[FreetextClusterComparison]:
        """Creates the cluster distribution per segment."""
        
        segment_values = self.segmenter.get_available_values(self.df, segment_type)
        if not segment_values:
            return None
        
        # extract the cluster names
        cluster_names = clusters if isinstance(clusters, list) else list(clusters)
        
        segments = []
        column = qconfig.get('columns', [None])[0]
        
        for value in segment_values:
            filtered_df = self.segmenter.filter_data(self.df, segment_type, value)
            n = len(filtered_df)
            
            if n == 0:
                continue
            
            warning = f"n < {self.warning_threshold}" if n < self.warning_threshold else None
            
            # cluster distribution of this segment
            # (the actual cluster assignment would have to come from the database;
            # placeholder: even distribution)
            cluster_counts = {name: 0 for name in cluster_names}
            total_items = 0
            
            # load the items of this segment from the database
            if self.db and self.import_id and column:
                # row_indices of the filtered DataFrame
                row_indices = filtered_df.index.tolist()
                items = self.db.get_items_for_question(
                    self.import_id, question_id, column
                )
                
                # filter the items by row_indices
                for item in items:
                    if item.get('row_index') in row_indices:
                        # cluster assignment (if any)
                        cluster = item.get('cluster_name') or _text('word.other', lang)
                        if cluster in cluster_counts:
                            cluster_counts[cluster] += 1
                            total_items += 1
            
            # compute the percentages
            percentages = {}
            if total_items > 0:
                for name, count in cluster_counts.items():
                    percentages[name] = (count / total_items) * 100
            
            segments.append(SegmentResult(
                segment_type=segment_type,
                segment_value=value,
                n=n,
                warning=warning,
                data=cluster_counts,
                percentages=percentages
            ))
        
        if not segments:
            return None
        
        return FreetextClusterComparison(
            cluster_names=cluster_names,
            segment_type=segment_type,
            segment_label=self._segment_label(segment_type, lang),
            segments=segments
        )
    
    def _analyze_segment_specific_clusters(
        self,
        question_id: str,
        qconfig: dict,
        handler,
        lang: str
    ) -> list[SegmentedFreetextResult]:
        """
        Runs the segment-specific cluster analysis.
        
        Iterates over all dimensions with enable_specific_clusters: true in the
        survey configuration, so a survey can create clusters per role and per
        faculty, for instance, or none at all, all by configuration.
        
        Returns:
            list of the cluster results per segment, collected over all enabled
            dimensions.
        """
        results = []
        
        if self.segmenter is None or self.segmenter._resolver is None:
            return results
        
        # all dimensions enabled for segment-specific clusters
        active_dims = [
            d for d in self.segmenter._resolver.list_dimensions()
            if d.enable_specific_clusters
        ]
        
        if not active_dims:
            logger.debug(
                f"No dimension has enable_specific_clusters=True; "
                f"skipping segment-specific clusters for {question_id}"
            )
            return results
        
        for dim in active_dims:
            logger.info(
                f"Segment-specific cluster analysis for the dimension '{dim.id}'"
            )
            
            segment_values = self.segmenter.get_available_values(self.df, dim.id)
            
            for value in segment_values:
                filtered_df = self.segmenter.filter_data(self.df, dim.id, value)
                n = len(filtered_df)
                
                min_n = self.segmenter._resolver.specific_clusters_threshold(dim.id)
                if n < min_n:  # specific_clusters_min_n of the dimension
                    logger.debug(
                        f"  segment {dim.id}={value}: n={n} too small, skipping"
                    )
                    continue
                
                warning = f"n < {self.warning_threshold}" if n < self.warning_threshold else None
                
                try:
                    # own analysis with LLM clustering for this segment
                    result = handler.analyze(
                        filtered_df,
                        lang=lang,
                        db=self.db,
                        import_id=self.import_id,
                        force_new_clusters=True,  # force new clusters
                    )
                    
                    n_items = result.n
                    n_responses = result.metadata.get('unique_responses', n)
                    
                    # extract the cluster details
                    clusters = result.metadata.get('cluster_details', [])
                    
                    results.append(SegmentedFreetextResult(
                        segment_type=dim.id,
                        segment_value=value,
                        n_items=n_items,
                        n_responses=n_responses,
                        warning=warning,
                        clusters=clusters,
                    ))
                    
                    logger.info(
                        f"  Segment {dim.id}={value}: {len(clusters)} Cluster"
                    )
                    
                except Exception as e:
                    logger.warning(
                        f"  error in the segment-specific analysis "
                        f"for {dim.id}={value}: {e}"
                    )
                    continue
        
        return results
    
    def analyze_all_questions(
        self,
        lang: str = "de",
        progress_callback=None
    ) -> list[QuestionAnalysisComplete]:
        """
        Analyses all questions completely.
        
        Args:
            lang: language
            progress_callback: optional callback(current, total, message)
        
        Returns:
            list of all analysis results
        """
        question_groups = self.config.get('question_groups', {})
        total = len(question_groups)
        results = []
        
        for i, (qid, qconfig) in enumerate(question_groups.items()):
            if progress_callback:
                progress_callback(i, total, f"Analyzing: {qid}")
            
            try:
                result = self.analyze_question_complete(qid, lang)
                results.append(result)
                logger.info(f"Analysis finished: {qid}")
                
            except Exception as e:
                logger.error(f"Error analysing {qid}: {e}", exc_info=True)
                continue
        
        if progress_callback:
            progress_callback(total, total, "Done")
        
        return results
