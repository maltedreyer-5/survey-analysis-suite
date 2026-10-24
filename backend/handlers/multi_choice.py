# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Multi-choice handler for multiple-choice questions.
"""

import logging
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt

from .base import MultiColumnHandler, AnalysisResult
from utils.charts import ChartGenerator

logger = logging.getLogger(__name__)


class MultiChoiceHandler(MultiColumnHandler):
    """Handler for multiple-choice questions."""
    per_column_labels = "item_labels"   # one label per column, by position (extract_item_name)
    
    handler_type = "multi_choice"
    supported_charts = ["bar_horizontal"]
    
    def _get_item_labels(self, lang: str = "de") -> dict:
        """A mapping from the item labels in the source language to the target language."""
        import logging
        logger = logging.getLogger(__name__)
        
        item_labels = self.config.get("item_labels", {})
        
        if not item_labels or not isinstance(item_labels, dict):
            logger.debug(f"{self.question_id}: no item_labels configured")
            return {}
        
        # item_labels.<source language> -> item_labels.<lang>
        mapping = self.labels_map("item_labels", lang)
        if mapping:
            logger.debug(f"{self.question_id}: {lang} translation for {len(mapping)} items")
        return mapping
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Analyses a multiple-choice question.
        
        Each option of a multiple-choice question can be chosen independently.
        The percentages refer to the number of respondents (not the sum of
        the mentions).
        
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
                data={},
                warnings=[f"No columns found: {missing_cols}"]
            )
        
        # valid answers: at least one choice
        valid_df = self.filter_valid_responses(df, existing_cols)
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # count the mentions per option
        counts = {}
        for col in existing_cols:
            item_name = self.extract_item_name(col)
            # count non-empty values (with checkboxes often "Yes" or the option text)
            count = valid_df[col].notna().sum()
            # Alternative: values meaning "yes" (utils/binary_values.py)
            if count == 0:
                from utils.binary_values import is_affirmative
                count = int(valid_df[col].map(is_affirmative).sum())
            counts[item_name] = count
        
        # sort by frequency
        counts = dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
        
        # percentages (base: number of respondents)
        percentages = {k: round(v / n * 100, 1) for k, v in counts.items()}
        
        # handle the other field
        other_col = self.config.get("other_column")
        additional_content = None
        
        if other_col and other_col in df.columns:
            other_values = df[other_col].dropna().tolist()
            other_values = [str(v) for v in other_values if str(v).strip()]
            if other_values:
                additional_content = {"sonstiges": other_values}
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=counts,
            percentages=percentages,
            description=self.generate_description_text(counts, n, lang),
            additional_content=additional_content,
            metadata={"columns": existing_cols}
        )
    
    def generate_description_text(
        self,
        counts: dict,
        n: int,
        lang: str
    ) -> str:
        """Writes a description text with translated values."""
        if not counts:
            return ""
        
        # get the translation mapping
        item_labels = self._get_item_labels(lang)
        
        # top 3 options (translated)
        top_items = list(counts.items())[:3]
        
        from utils.languages import text
        items_text = ", ".join(
            text("handler.multi_choice.item", lang, label=item_labels.get(item, item), count=count, pct=count / n * 100)
            for item, count in top_items)
        return text("handler.multi_choice.top", lang, items=items_text)
    
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
            chart_type: chart type
            lang: language
        
        Returns:
            matplotlib figure
        """
        generator = ChartGenerator(kwargs.get('colors'))
        title = self.get_title(lang)
        
        # translated labels for the chart
        item_labels = self._get_item_labels(lang)
        if item_labels:
            translated_data = {
                item_labels.get(k, k): v for k, v in result.data.items()
            }
        else:
            translated_data = result.data
        
        # Share of respondents (base: at least one selection), shown on the axis
        from utils.languages import text
        return generator.bar_horizontal(
            data=translated_data,
            title=title,
            xlabel=text("chart.axis.pct_resp", lang),
            total=result.n,
            colors=self.get_colors(),
            lang=lang,
            value_mode="percent",
            n_info=text("chart.base_mc", lang, n=result.n),
        )
