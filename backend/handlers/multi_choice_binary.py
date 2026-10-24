# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Multi-choice binary handler for yes/no matrix questions.
"""

import logging

import pandas as pd
import matplotlib.pyplot as plt

from .base import MultiColumnHandler, AnalysisResult
from utils.charts import ChartGenerator

logger = logging.getLogger(__name__)


class MultiChoiceBinaryHandler(MultiColumnHandler):
    """Handler for yes/no matrix questions."""
    per_column_labels = "item_labels"   # one label per column, by position (extract_item_name)
    
    handler_type = "multi_choice_binary"
    supported_charts = ["bar_horizontal"]
    
    def _get_item_labels(self, lang: str = "de") -> dict:
        """
        The item labels in the requested language as a mapping.
        
        With a fallback to known labels for terms that are not configured.
        """
        import logging
        from utils.translation import get_translation_manager
        
        logger = logging.getLogger(__name__)
        tm = get_translation_manager()
        
        item_labels = self.config.get("item_labels", {})
        mapping = {}
        
        if item_labels:
            # item_labels.<source language> -> item_labels.<lang>
            mapping = self.labels_map("item_labels", lang)
            if mapping:
                logger.debug(f"{self.question_id}: {lang} translation for {len(mapping)} items")
        
        # fallback dictionary for known labels
        class FallbackDict(dict):
            def __init__(self, base_mapping, translation_manager, target_lang):
                super().__init__(base_mapping)
                self._tm = translation_manager
                self._lang = target_lang
            
            def get(self, key, default=None):
                if key in self:
                    return super().get(key)
                translated = self._tm.translate_common_label(key, self._lang)
                if translated != key:
                    return translated
                return default if default is not None else key
        
        return FallbackDict(mapping, tm, lang)
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Analyses a yes/no matrix question.
        
        The question has several items, each answered with yes or no.
        
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
        
        # binary values from the configuration
        from utils.binary_values import chosen_mask, configured_negative, configured_positive
        from utils.languages import text
        binary_values = self.config.get("binary_values", {})
        positive = configured_positive(self.config)
        src = self.source_language
        positive_val = positive or text("analysis.binary.yes", src).capitalize()
        negative_val = configured_negative(self.config) or text("analysis.binary.no", src).capitalize()
        
        # valid answers
        valid_df = self.filter_valid_responses(df, existing_cols)
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # count the "yes" answers per item
        yes_counts = {}
        for col in existing_cols:
            item_name = self.extract_item_name(col)
            # "chosen" by the one rule of utils/binary_values.py
            yes_counts[item_name] = int(chosen_mask(valid_df, col, self.config).sum())
        
        # sort by frequency
        yes_counts = dict(sorted(yes_counts.items(), key=lambda x: x[1], reverse=True))
        
        # calculate the percentages
        percentages = {k: round(v / n * 100, 1) for k, v in yes_counts.items()}
        
        # detailed data for other visualisations
        detailed_data = {}
        for col in existing_cols:
            item_name = self.extract_item_name(col)
            yes_count = yes_counts.get(item_name, 0)
            no_count = n - yes_count
            detailed_data[item_name] = {
                positive_val: yes_count,
                negative_val: no_count
            }
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=yes_counts,
            percentages=percentages,
            description=self.generate_description_text(yes_counts, n, lang),
            metadata={
                "columns": existing_cols,
                "detailed_data": detailed_data,
                "binary_values": binary_values
            }
        )
    
    def generate_description_text(
        self,
        yes_counts: dict,
        n: int,
        lang: str
    ) -> str:
        """Writes a description text with translated values."""
        if not yes_counts:
            return ""
        
        # get the translation mapping
        item_labels = self._get_item_labels(lang)
        
        # top item (translated)
        top_item_src = max(yes_counts, key=lambda k: yes_counts[k])   # largest, whatever the order
        top_item = item_labels.get(top_item_src, top_item_src)
        top_count = yes_counts[top_item_src]
        top_pct = top_count / n * 100
        from utils.languages import text
        return text("handler.multi_choice_binary.top", lang, item=top_item, count=top_count, pct=top_pct)
    
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
        
        # translate the item labels
        item_label_map = self._get_item_labels(lang)
        
        # data with translated labels
        data_for_chart = {}
        for item, count in result.data.items():
            translated_label = item_label_map.get(item, item)
            data_for_chart[translated_label] = count
        
        # different colours from the palette for different items
        palette = generator.colors.get('palette', ChartGenerator.DEFAULT_COLORS['palette'])
        colors = {}
        for i, item in enumerate(data_for_chart.keys()):
            colors[item] = palette[i % len(palette)]
        
        from utils.languages import text
        from utils.binary_values import configured_positive
        positive = configured_positive(self.config) or text("analysis.binary.yes", lang).capitalize()
        return generator.bar_horizontal(
            data=data_for_chart,
            title=title,
            xlabel=text("chart.axis.pct_yes", lang),
            total=result.n,
            colors=colors,
            lang=lang,
            value_mode="percent",
            n_info=text("chart.base_yes", lang, n=result.n, positive=positive),
        )
