# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Single-choice handler for single-choice questions.
"""

import logging
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt

from .base import SingleColumnHandler, AnalysisResult
from utils.charts import ChartGenerator

logger = logging.getLogger(__name__)


class SingleChoiceHandler(SingleColumnHandler):
    """Handler for single-choice questions."""
    
    handler_type = "single_choice"
    supported_charts = ["bar_horizontal", "pie"]
    
    def _get_option_labels(self, lang: str = "de") -> dict:
        """
        A mapping from the options in the source language to the target language.
        
        Uses:
        1. the explicit options configuration
        2. a fallback to known labels (Other, Yes/No, etc.)
        """
        import logging
        from utils.translation import get_translation_manager
        
        logger = logging.getLogger(__name__)
        tm = get_translation_manager()
        
        options = self.config.get("options", {})
        mapping = {}
        
        if options and isinstance(options, dict):
            # options.<source language> -> options.<lang>
            mapping = self.labels_map("options", lang)
            if mapping:
                logger.debug(f"{self.question_id}: {lang} translation for {len(mapping)} options")
        
        # fallback: translate known labels automatically,
        # used when a label is not in the configuration
        class FallbackDict(dict):
            def __init__(self, base_mapping, translation_manager, target_lang):
                super().__init__(base_mapping)
                self._tm = translation_manager
                self._lang = target_lang
            
            def get(self, key, default=None):
                # first look in the explicit mapping
                if key in self:
                    return super().get(key)
                # then translate known labels
                translated = self._tm.translate_common_label(key, self._lang)
                if translated != key:
                    return translated
                # otherwise the default
                return default if default is not None else key
        
        return FallbackDict(mapping, tm, lang)
    
    def configured_order(self) -> list:
        """
        Order of the options as configured: ``sort_order``, else ``options``
        of the source language (``options`` is a list per language).
        """
        from utils import prompts
        order = self.config.get("sort_order") or self.config.get("options") or []
        if isinstance(order, dict):
            src = prompts.source_language()
            order = order.get(src) or next(iter(order.values()), [])
        return [str(o).strip() for o in order]

    def uses_configured_order(self) -> bool:
        """Ordered scales keep their order in charts; other questions are sorted by frequency."""
        return bool(self.config.get("sort_order") or self.config.get("ordinal"))

    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Analyses a single-choice question.
        
        Args:
            df: DataFrame with the survey data
            lang: language
        
        Returns:
            AnalysisResult
        """
        column = self.get_column()
        
        if not column or column not in df.columns:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=[f"Column not found: {column}"]
            )
        
        # filter the valid answers
        valid_df = df[df[column].notna()].copy()
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # count the frequencies
        counts = valid_df[column].value_counts()
        
        # order from the configuration (sort_order takes precedence over options)
        order_list = self.configured_order()
        
        if order_list:
            # sort by the given order
            ordered_counts = {}
            keep_empty = bool(self.config.get("ordinal"))
            for opt in order_list:
                if opt in counts.index:
                    ordered_counts[opt] = counts[opt]
                elif keep_empty:
                    # an ordered scale keeps its empty points (charts show the whole scale)
                    ordered_counts[opt] = 0
            # add values that are not configured (alphabetically)
            remaining = sorted([val for val in counts.index if val not in ordered_counts])
            for val in remaining:
                ordered_counts[val] = counts[val]
            counts = pd.Series(ordered_counts)
        
        # calculate the percentages
        percentages = (counts / n * 100).round(1).to_dict()
        
        metadata = {"column": column}
        if self.config.get("nps"):
            # Net Promoter Score on the 0-10 scale (utils/nps.py)
            from utils.nps import compute as nps
            figures = nps({k: int(v) for k, v in counts.items()})
            if figures:
                metadata["nps"] = figures
        if self.config.get("ordinal") and order_list:
            # ordered scale: mean, median, top/bottom box (utils/scale_metrics.py)
            from utils.scale_metrics import compute
            metrics = compute({k: int(v) for k, v in counts.items()}, order_list, self.config)
            if metrics:
                metadata["scale_metrics"] = metrics
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=counts.to_dict(),
            percentages=percentages,
            description=self.generate_description_text(counts, n, lang),
            metadata=metadata
        )
    
    def generate_description_text(
        self,
        counts: pd.Series,
        n: int,
        lang: str
    ) -> str:
        """Writes a description text with translated values."""
        if len(counts) == 0:
            return ""
        
        # Most frequent answer (as in the data, source language). The counts may
        # be in scale order (ordinal questions), so pick the largest, not the first.
        by_count = counts.sort_values(ascending=False, kind="stable")
        top_answer_src = by_count.index[0]
        top_count = by_count.iloc[0]
        top_pct = top_count / n * 100
        
        option_labels = self._get_option_labels(lang)
        top_answer = option_labels.get(top_answer_src, top_answer_src)
        from utils.languages import text
        return text("handler.single_choice.top", lang, answer=top_answer, count=top_count, pct=top_pct)
    
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
        # ordered scales and NPS: colours by polarity or position (utils/chart_colors.py);
        # colours configured per option win
        from utils.chart_colors import scale_colours
        colors = {**scale_colours(list(result.data.keys()), self.config), **self.get_colors()}
        
        from utils.languages import text
        n_info = text("chart.base", lang, n=result.n)
        
        # Ordered scales keep the configured order, others are sorted by frequency
        has_fixed_order = self.uses_configured_order()
        
        # translated labels for the chart
        option_labels = self._get_option_labels(lang)
        if option_labels:
            translated_data = {
                option_labels.get(k, k): v for k, v in result.data.items()
            }
            colors = {option_labels.get(k, k): c for k, c in colors.items()}
        else:
            translated_data = result.data
        
        if chart_type == "pie":
            return generator.pie_chart(
                data=translated_data,
                title=title,
                colors=colors,
                lang=lang
            )
        else:
            # default: horizontal bars
            return generator.bar_horizontal(
                data=translated_data,
                title=title,
                colors=colors,
                lang=lang,
                total=result.n,
                n_info=n_info,
                preserve_order=has_fixed_order
            )
