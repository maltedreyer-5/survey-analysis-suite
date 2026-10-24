# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Matrix/Likert handler for matrix questions with a rating scale.
"""

import logging

import pandas as pd
import matplotlib.pyplot as plt

from .base import MultiColumnHandler, AnalysisResult
from utils.charts import ChartGenerator

logger = logging.getLogger(__name__)


class MatrixLikertHandler(MultiColumnHandler):
    """Handler for matrix/Likert questions."""
    per_column_labels = "row_labels"   # one label per column, by position (extract_item_name)
    
    handler_type = "matrix_likert"
    supported_charts = ["stacked_bar_100pct", "diverging_bar", "heatmap"]
    
    def _get_scale(self, lang: str = "de") -> list:
        """The scale in the requested language."""
        scale_config = self.config.get("scale", [])
        
        # scale per language: scale.de, scale.fr, scale.en
        if isinstance(scale_config, dict):
            from utils.languages import localized
            return list(localized(scale_config, lang, self.source_language, default=[]) or [])
        
        # scale as a plain list
        return scale_config
    
    def _get_row_labels(self, lang: str = "de") -> dict:
        """The row labels in the requested language as a mapping."""
        import logging
        logger = logging.getLogger(__name__)
        
        row_labels = self.config.get("row_labels", {})
        
        if not row_labels:
            logger.debug(f"{self.question_id}: no row_labels configured")
            return {}
        
        # row_labels.<source language> -> row_labels.<lang>
        mapping = self.labels_map("row_labels", lang)
        if mapping:
            logger.debug(f"{self.question_id}: {lang} translation for {len(mapping)} rows")
        return mapping
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Analyses a matrix/Likert question.
        
        Several items are rated on the same scale (e.g. very good to very poor).
        
        Args:
            df: DataFrame with the survey data
            lang: language
        
        Returns:
            AnalysisResult
        """
        existing_cols, missing_cols = self.validate_columns(df)
        
        if not existing_cols:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data=pd.DataFrame(),
                warnings=[f"No columns found: {missing_cols}"]
            )
        
        # Scale of the source language (the data hold its values); a copy, so
        # values outside the scale never change the configuration
        from utils import prompts
        scale = [str(v).strip() for v in (self._get_scale(prompts.source_language()) or [])]
        
        # valid answers
        valid_df = self.filter_valid_responses(df, existing_cols)
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data=pd.DataFrame(),
                warnings=["No valid answers"]
            )
        
        # debug: check the actual values in the data
        actual_values = set()
        for col in existing_cols:
            actual_values.update(valid_df[col].dropna().unique())
        
        logger.info(f"Configured scale: {scale}")
        logger.info(f"Actual values in the data: {actual_values}")
        
        # warn if the scale values do not match
        warnings = []
        if scale and actual_values:
            missing_in_data = set(scale) - actual_values
            extra_in_data = actual_values - set(scale)
            
            if missing_in_data:
                warnings.append(f"Scale values not found in the data: {missing_in_data}")
                logger.warning(f"Scale values not in the data: {missing_in_data}")
            
            if extra_in_data:
                # Not part of the scale (e.g. "no answer"): counted separately,
                # never part of the scale shares
                logger.info(f"{self.question_id}: values outside the scale: {extra_in_data}")
        
        # no scale defined: derive it from the data
        if not scale and actual_values:
            scale = sorted([str(v) for v in actual_values if v and str(v).strip()])
            logger.info(f"Scale derived from the data: {scale}")
        
        if not scale:
            return AnalysisResult(
                question_id=self.question_id,
                n=n,
                data=pd.DataFrame(),
                warnings=["No scale values found, neither in the configuration nor in the data"]
            )
        
        # build the pivot table: items x scale values
        result_data = {}
        
        for col in existing_cols:
            item_name = self.extract_item_name(col)
            counts = valid_df[col].value_counts()
            
            # for every scale value
            item_data = {}
            for scale_val in scale:
                item_data[scale_val] = counts.get(scale_val, 0)
            
            result_data[item_name] = item_data
        
        # create the DataFrame
        matrix_df = pd.DataFrame(result_data).T
        # Answers outside the scale per item (shown separately in charts)
        excluded = {}
        for col in existing_cols:
            counts = valid_df[col].dropna().map(lambda v: str(v).strip()).value_counts()
            extra = {v: int(c) for v, c in counts.items() if v and v not in scale}
            if extra:
                excluded[self.extract_item_name(col)] = extra
        
        # debug: check the matrix content
        logger.info(f"Matrix DataFrame:\n{matrix_df}")
        
        # is the matrix empty?
        if matrix_df.empty or matrix_df.sum().sum() == 0:
            warnings.append("No data found for the scale; the values may not match")
            return AnalysisResult(
                question_id=self.question_id,
                n=n,
                data=matrix_df,
                warnings=warnings
            )
        
        # sort by the first scale value
        if scale:
            first_scale = scale[0]
            if first_scale in matrix_df.columns:
                matrix_df = matrix_df.sort_values(first_scale, ascending=False)
        
        # calculate the percentages (guarded against division by zero)
        row_sums = matrix_df.sum(axis=1)
        row_sums = row_sums.replace(0, 1)  # replace 0 by 1 to avoid a division by zero
        matrix_pct = matrix_df.div(row_sums, axis=0) * 100
        matrix_pct = matrix_pct.round(1)
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=matrix_df,
            percentages=matrix_pct.to_dict(),
            description=self.generate_description_text(matrix_df, scale, lang),
            warnings=warnings if warnings else None,
            metadata={
                "columns": existing_cols,
                "scale": scale,
                "percentages_df": matrix_pct,
                "excluded": excluded,
                "scale_metrics": self._scale_metrics(matrix_df, scale),
            }
        )
    
    def _scale_metrics(self, matrix_df: pd.DataFrame, scale: list) -> dict:
        """Mean, median, top/bottom box per row (utils/scale_metrics.py); empty if not a scale."""
        from utils.scale_metrics import compute
        out = {}
        for row in matrix_df.index:
            metrics = compute({str(k): int(v) for k, v in matrix_df.loc[row].items()}, [str(x) for x in scale],
                              self.config)
            if metrics:
                out[str(row)] = metrics
        return out
    
    def generate_description_text(
        self,
        matrix_df: pd.DataFrame,
        scale: list[str],
        lang: str
    ) -> str:
        """Writes a description text with translated values."""
        if matrix_df.empty or not scale:
            return ""
        
        # Item with the highest share of the first scale value (scale in the source language)
        first_scale_src = scale[0]
        if first_scale_src not in matrix_df.columns:
            return ""
        
        # are all values 0?
        if matrix_df[first_scale_src].sum() == 0:
            return ""
        
        top_item_src = matrix_df[first_scale_src].idxmax()
        top_value = matrix_df.loc[top_item_src, first_scale_src]
        total = matrix_df.loc[top_item_src].sum()
        
        # avoid a division by zero
        if total == 0:
            return ""
        
        top_pct = top_value / total * 100
        
        # translate the scale and the row labels
        scale_translated = self._get_scale(lang)
        row_label_map = self._get_row_labels(lang)
        
        # the translated first scale value
        scale_src = self._get_scale(self.source_language)
        if scale_src and scale_translated and len(scale_src) == len(scale_translated):
            scale_map = dict(zip(scale_src, scale_translated))
            first_scale = scale_map.get(first_scale_src, first_scale_src)
        else:
            first_scale = first_scale_src
        
        # translate the row label
        top_item = row_label_map.get(top_item_src, top_item_src)
        from utils.languages import text
        return text("handler.matrix_likert.top", lang, scale=first_scale, item=top_item, pct=top_pct)
    
    def create_chart(
        self,
        result: AnalysisResult,
        chart_type: str,
        lang: str = "de",
        **kwargs
    ) -> plt.Figure:
        """
        Creates a chart with translated labels.
        
        Args:
            result: analysis result
            chart_type: chart type
            lang: language
        
        Returns:
            matplotlib figure
        """
        generator = ChartGenerator(kwargs.get('colors'))
        title = self.get_title(lang)
        
        from utils import prompts
        from utils.languages import text
        src = prompts.source_language()
        scale_src = list(result.metadata.get("scale", []))
        scale_lang = self._get_scale(lang) or []
        # translation by position on the scale
        if len(scale_lang) == len(scale_src):
            col_rename = dict(zip(scale_src, scale_lang))
        else:
            col_rename = {}
        categories = [col_rename.get(c, c) for c in scale_src]
        row_label_map = self._get_row_labels(lang)
        data_for_chart = result.data[[c for c in scale_src if c in result.data.columns]].rename(columns=col_rename)
        if row_label_map:
            data_for_chart = data_for_chart.rename(index=row_label_map)
        
        # answers outside the scale: share of all answers per item, grey bar
        excluded_counts = result.metadata.get("excluded") or {}
        excluded_pct, excluded_values, excluded_total = {}, set(), 0
        for item, extra in excluded_counts.items():
            on_scale = float(result.data.loc[item].sum()) if item in result.data.index else 0.0
            n_extra = sum(extra.values())
            excluded_values.update(extra)
            excluded_total += n_extra
            if on_scale + n_extra:
                excluded_pct[row_label_map.get(item, item) if row_label_map else item] = 100 * n_extra / (on_scale + n_extra)
        note = None
        if excluded_total:
            note = text("chart.excluded", lang, values=", ".join(sorted(excluded_values)),
                        count=excluded_total)
        options = dict(title=title, categories=categories, lang=lang, excluded=excluded_pct,
                       excluded_label=text("chart.excluded_bar", lang), note=note,
                       positive_first=self.config.get("scale_positive_first", True))
        if chart_type == "diverging_bar" and len(categories) >= 2:
            return generator.diverging_bar(data=data_for_chart, neutral_label=text("chart.neutral_split", lang),
                                           **options)
        # colours by polarity or position on the scale, evasive answers light grey
        # (utils/chart_colors.py); colours configured per option win
        from utils.chart_colors import scale_colours
        colours = {col_rename.get(k, k): c for k, c in scale_colours(scale_src, self.config).items()}
        colours.update(self.get_colors())
        return generator.stacked_bar_100pct(data=data_for_chart, colors=colours, **options)
