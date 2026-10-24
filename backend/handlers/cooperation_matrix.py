# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Cooperation matrix handler for questions on cooperation partners.

Processes matrix questions on the importance of several cooperation partners
with a rating in several steps.
"""

import logging
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from .base import MultiColumnHandler, AnalysisResult
from utils.languages import text as _text

logger = logging.getLogger(__name__)


class CooperationMatrixHandler(MultiColumnHandler):
    """
    Handler for cooperation partner matrix questions.
    
    These questions rate the importance of several partners
    (e.g. "very important", "important", "less important", "unimportant").
    """
    
    handler_type = "cooperation_matrix"
    supported_charts = ["stacked_bar_100pct", "heatmap", "bar_grouped"]
    
    # default importance scale
    # ascending, as the importance score expects (first = lowest weight, last =
    # highest); it was descending, which turned "no cooperation" into the most
    # important answer whenever a question had no scale of its own
    DEFAULT_SCALE = [
        "Keine Kooperation",
        "Unwichtig",
        "Weniger wichtig",
        "Wichtig",
        "Sehr wichtig",
    ]
    
    # default colours (from positive to negative)
    DEFAULT_COLORS = {
        "Sehr wichtig": "#2E7D32",      # dark green
        "Wichtig": "#66BB6A",            # light green
        "Weniger wichtig": "#FFCA28",    # yellow
        "Unwichtig": "#EF5350",          # light red
        "Keine Kooperation": "#9E9E9E"   # grey
    }
    
    def __init__(self, config: dict):
        """Initialises the CooperationMatrixHandler."""
        super().__init__(config)
        
        # scale from the configuration or the default
        # (plain lists as well as multilingual dicts)
        scale_config = config.get("scale") or config.get("scale_values")
        
        if scale_config:
            if isinstance(scale_config, dict):
                # Per-language configuration: the data use the source language
                self.scale = scale_config.get(self.source_language, self.DEFAULT_SCALE)
                self.scale_translations = scale_config
            else:
                self.scale = scale_config
                self.scale_translations = None
        else:
            self.scale = self.DEFAULT_SCALE
            self.scale_translations = None
        
        # colours from the configuration or the default
        self.colors = config.get("scale_colors", self.DEFAULT_COLORS)
        
        logger.info(f"CooperationMatrixHandler init: scale={self.scale}")
    
    def get_scale_for_lang(self, lang: str) -> list:
        """The scale in the requested language."""
        if self.scale_translations and lang in self.scale_translations:
            return self.scale_translations[lang]
        return self.scale
    
    def get_scale_translation_map(self, lang: str) -> dict:
        """A mapping from the scale in the source language to the target language."""
        if not self.scale_translations or lang == self.source_language:
            return {v: v for v in self.scale}
        
        target_scale = self.scale_translations.get(lang, self.scale)
        # mapping by position
        return {de_val: target_scale[i] if i < len(target_scale) else de_val 
                for i, de_val in enumerate(self.scale)}
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de"
    ) -> AnalysisResult:
        """
        Analyses a cooperation partner matrix question.
        
        Args:
            df: DataFrame with the survey data
            lang: language
        
        Returns:
            AnalysisResult with a pivot table and percentages
        """
        existing_cols, missing_cols = self.validate_columns(df)
        
        logger.info(f"CooperationMatrix analyze: {len(existing_cols)} columns found, {len(missing_cols)} missing")
        
        if not existing_cols:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=[f"No columns found: {missing_cols}"]
            )
        
        # valid answers
        valid_df = self.filter_valid_responses(df, existing_cols)
        n = len(valid_df)
        
        logger.info(f"CooperationMatrix: {n} valid answers")
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # Answers outside the scale are not counted: say so instead of showing zeros
        outside = set()
        for col in existing_cols:
            outside |= {str(v).strip() for v in valid_df[col].dropna().unique()} - set(self.scale)
        warnings = [f"Values outside the scale not counted: {sorted(outside)[:6]}"] if outside else []
        
        # build the matrix with counts
        matrix_data = {}
        for col in existing_cols:
            item_name = self.extract_item_name(col)
            value_counts = valid_df[col].value_counts()
            
            logger.debug(f"  column {item_name}: {dict(value_counts)}")
            
            # consider only values of the scale
            counts = {}
            for scale_val in self.scale:
                counts[scale_val] = int(value_counts.get(scale_val, 0))
            
            matrix_data[item_name] = counts
        
        logger.info(f"CooperationMatrix: matrix data created with {len(matrix_data)} items")
        
        # create the DataFrame
        matrix_df = pd.DataFrame(matrix_data).T
        
        # column order by the scale
        matrix_df = matrix_df.reindex(columns=self.scale, fill_value=0)
        
        logger.info(f"CooperationMatrix matrix_df shape: {matrix_df.shape}, columns: {list(matrix_df.columns)}")
        
        # are all values 0?
        total_sum = matrix_df.sum().sum()
        if total_sum == 0:
            logger.warning(f"CooperationMatrix: all values are 0! Check the scale values.")
            # show the actual values in the columns
            for col in existing_cols[:2]:  # first 2 columns
                unique_vals = valid_df[col].unique()
                logger.warning(f"  column '{col[:50]}...' has the values: {list(unique_vals)[:10]}")
        
        # create the DataFrame
        matrix_df = pd.DataFrame(matrix_data).T
        
        # column order by the scale
        matrix_df = matrix_df.reindex(columns=self.scale, fill_value=0)
        
        # compute the percentage matrix
        percent_df = matrix_df.div(matrix_df.sum(axis=1), axis=0) * 100
        percent_df = percent_df.round(1)
        
        # compute the "importance score" (weighted average)
        importance_scores = self._calculate_importance_scores(matrix_df)
        
        # sort by importance
        sorted_items = importance_scores.sort_values(ascending=False).index.tolist()
        matrix_df = matrix_df.reindex(sorted_items)
        percent_df = percent_df.reindex(sorted_items)
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=matrix_df.to_dict(),
            percentages=percent_df.to_dict(),
            warnings=warnings,
            description=self._generate_description(
                matrix_df, importance_scores, lang
            ),
            metadata={
                "columns": existing_cols,
                "scale": self.scale,
                "matrix_df": matrix_df,
                "percent_df": percent_df,
                "importance_scores": importance_scores.to_dict(),
                "item_order": sorted_items
            }
        )
    
    def _calculate_importance_scores(
        self,
        matrix_df: pd.DataFrame
    ) -> pd.Series:
        """
        Computes an importance score per partner.
        
        The weighting follows the order of the scale:
        - first element = 0 (lowest value)
        - last element = len(scale)-1 (highest value)
        """
        # dynamic weights from the scale
        weights = {val: idx for idx, val in enumerate(self.scale)}
        
        logger.debug(f"Importance weights: {weights}")
        
        # weighted sum
        scores = pd.Series(index=matrix_df.index, dtype=float)
        
        for item in matrix_df.index:
            weighted_sum = 0
            total_count = 0
            
            for scale_val, weight in weights.items():
                if scale_val in matrix_df.columns:
                    count = matrix_df.loc[item, scale_val]
                    weighted_sum += count * weight
                    total_count += count
            
            if total_count > 0:
                scores[item] = weighted_sum / total_count
            else:
                scores[item] = 0
        
        return scores
    
    def _generate_description(
        self,
        matrix_df: pd.DataFrame,
        importance_scores: pd.Series,
        lang: str
    ) -> str:
        """Writes a description text."""
        if matrix_df.empty:
            return ""
        
        # Top-Partner
        top_partner = importance_scores.idxmax()
        top_score = importance_scores.max()
        
        # percentage of the two upper scale values for the top partners
        # (the last two entries of the scale are the highest/most important)
        if len(self.scale) >= 2:
            top_values = self.scale[-2:]  # the two highest values
            important_count = sum(
                matrix_df.loc[top_partner, val] 
                for val in top_values 
                if val in matrix_df.columns
            )
            total = matrix_df.loc[top_partner].sum()
            important_pct = (important_count / total * 100) if total > 0 else 0
        else:
            important_pct = 0
        
        from utils.languages import text
        return text("handler.cooperation_matrix.top", lang, partner=top_partner, score=top_score, pct=important_pct)
    
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
        title = self.get_title(lang)
        
        if chart_type == "heatmap":
            return self._create_heatmap(result, title, lang)
        elif chart_type == "bar_grouped":
            return self._create_grouped_bars(result, title, lang)
        else:
            # Default: Stacked Bar 100%
            return self._create_stacked_bar(result, title, lang)
    
    def _create_stacked_bar(
        self,
        result: AnalysisResult,
        title: str,
        lang: str
    ) -> plt.Figure:
        """Creates a 100% stacked bar chart."""
        percent_df = result.metadata.get("percent_df")
        
        if percent_df is None or percent_df.empty:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.text(0.5, 0.5, _text('chart.no_data', lang), ha='center', va='center')
            return fig
        
        # create the figure
        fig, ax = plt.subplots(figsize=(12, max(6, len(percent_df) * 0.6)))
        
        # translation map for the labels
        translation_map = self.get_scale_translation_map(lang)
        
        # colours of the scale, index-based for robustness:
        # gradient from grey (no cooperation) over yellow to green (high importance)
        default_color_palette = [
            '#9E9E9E',  # grey: no/low importance
            '#FFCA28',  # yellow
            '#66BB6A',  # light green
            '#2E7D32',  # dark green: high importance
        ]
        
        # assign the colours for the current scale
        n_scale = len(self.scale)
        if n_scale <= len(default_color_palette):
            colors = default_color_palette[:n_scale]
        else:
            # more scale values than colours: interpolate or repeat
            colors = []
            for i in range(n_scale):
                idx = int(i * (len(default_color_palette) - 1) / max(n_scale - 1, 1))
                colors.append(default_color_palette[idx])
        
        # prefer explicit colours of the configuration
        if hasattr(self, 'colors') and self.colors:
            colors = [self.colors.get(val, colors[i] if i < len(colors) else '#999999') 
                     for i, val in enumerate(self.scale)]
        
        # Stacked horizontal bars
        left = np.zeros(len(percent_df))
        
        for idx, (scale_val, color) in enumerate(zip(self.scale, colors)):
            if scale_val in percent_df.columns:
                values = percent_df[scale_val].values
                # translated label for the legend
                translated_label = translation_map.get(scale_val, scale_val)
                bars = ax.barh(
                    range(len(percent_df)),
                    values,
                    left=left,
                    label=translated_label,
                    color=color,
                    edgecolor='white',
                    linewidth=0.5
                )
                
                # show the values (if > 5%)
                for i, (val, l) in enumerate(zip(values, left)):
                    if val > 5:
                        ax.text(
                            l + val/2,
                            i,
                            f"{val:.0f}%",
                            ha='center',
                            va='center',
                            fontsize=8,
                            color='white' if color in ['#2E7D32', '#EF5350'] else 'black'
                        )
                
                left += values
        
        # y axis with the partner names
        ax.set_yticks(range(len(percent_df)))
        ax.set_yticklabels(percent_df.index)
        
        # axis formatting
        ax.set_xlim(0, 100)
        from utils.languages import text
        ax.set_xlabel(text("handler.cooperation_matrix.axis_percent", lang))
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        # legend
        ax.legend(
            loc='upper center',
            bbox_to_anchor=(0.5, -0.05),
            ncol=min(len(self.scale), 5),
            fontsize=9
        )
        
        # Styling
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        return fig
    
    def _create_heatmap(
        self,
        result: AnalysisResult,
        title: str,
        lang: str
    ) -> plt.Figure:
        """Creates a heatmap."""
        percent_df = result.metadata.get("percent_df")
        
        if percent_df is None or percent_df.empty:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.text(0.5, 0.5, _text('chart.no_data', lang), ha='center', va='center')
            return fig
        
        fig, ax = plt.subplots(figsize=(12, max(6, len(percent_df) * 0.6)))
        
        # translation map for the labels
        translation_map = self.get_scale_translation_map(lang)
        translated_columns = [translation_map.get(col, col) for col in percent_df.columns]
        
        # heatmap data
        data = percent_df.values
        
        # Colormap
        cmap = plt.cm.RdYlGn_r  # red-yellow-green (reversed)
        
        # create the heatmap
        im = ax.imshow(data, cmap=cmap, aspect='auto', vmin=0, vmax=100)
        
        # axes with translated labels
        ax.set_xticks(range(len(percent_df.columns)))
        ax.set_xticklabels(translated_columns, rotation=45, ha='right')
        ax.set_yticks(range(len(percent_df)))
        ax.set_yticklabels(percent_df.index)
        
        # values in the cells
        for i in range(len(percent_df)):
            for j in range(len(percent_df.columns)):
                val = data[i, j]
                color = 'white' if val > 50 else 'black'
                ax.text(j, i, f"{val:.0f}%", ha='center', va='center', 
                       color=color, fontsize=9)
        
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        # Colorbar
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        from utils.languages import text
        cbar.set_label(text("handler.cooperation_matrix.axis_percent", lang))
        
        plt.tight_layout()
        return fig
    
    def _create_grouped_bars(
        self,
        result: AnalysisResult,
        title: str,
        lang: str
    ) -> plt.Figure:
        """Creates grouped bars (focus on the highest scale values)."""
        percent_df = result.metadata.get("percent_df")
        
        if percent_df is None or percent_df.empty:
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.text(0.5, 0.5, _text('chart.no_data', lang), ha='center', va='center')
            return fig
        
        fig, ax = plt.subplots(figsize=(12, max(6, len(percent_df) * 0.6)))
        
        # combine the two highest scale values
        top_values = self.scale[-2:] if len(self.scale) >= 2 else self.scale[-1:]
        
        important = pd.Series(index=percent_df.index, dtype=float)
        for item in percent_df.index:
            val = sum(
                percent_df.loc[item, scale_val] 
                for scale_val in top_values 
                if scale_val in percent_df.columns
            )
            important[item] = val
        
        # sort by importance (already in the result)
        important = important.reindex(percent_df.index)
        
        # horizontal bars
        colors = ['#66BB6A'] * len(important)
        
        bars = ax.barh(
            range(len(important)),
            important.values,
            color=colors,
            edgecolor='white'
        )
        
        # values
        for i, val in enumerate(important.values):
            ax.text(val + 1, i, f"{val:.0f}%", va='center', fontsize=10)
        
        # y axis
        ax.set_yticks(range(len(important)))
        ax.set_yticklabels(important.index)
        
        # formatting, translated labels
        translation_map = self.get_scale_translation_map(lang)
        translated_top = [translation_map.get(v, v) for v in top_values]
        top_label = " + ".join(f"'{v}'" for v in translated_top)
        
        from utils.languages import text
        xlabel = text("handler.cooperation_matrix.axis_share", lang, values=top_label)
        ax.set_xlabel(xlabel)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        return fig
