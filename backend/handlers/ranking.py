# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Ranking handler for weighted ranking questions.

Processes questions in which participants sort items by priority (e.g. ranks
1-9). The analysis is weighted.
"""

import logging
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt

from .base import MultiColumnHandler, AnalysisResult

logger = logging.getLogger(__name__)


class RankingHandler(MultiColumnHandler):
    """
    Handler for ranking questions.
    
    Participants order items by priority. The analysis is weighted: rank 1
    gets the most points.
    """
    
    handler_type = "ranking"
    supported_charts = ["bar_weighted", "rank_table"]
    
    def __init__(self, config: dict):
        """Initialises the RankingHandler."""
        super().__init__(config)
    
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
        Analyses a ranking question.
        
        LimeSurvey format: the columns are ranks (Rank 1, Rank 2, ...), the
        values are the item names on that rank.
        
        Weighting:
        - with n ranks: rank 1 = n points, rank 2 = n-1 points, ..., rank n = 1 point
        
        Args:
            df: DataFrame with the survey data
            lang: language
        
        Returns:
            AnalysisResult with weighted scores
        """
        existing_cols, missing_cols = self.validate_columns(df)
        
        if not existing_cols:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=[f"No columns found: {missing_cols}"]
            )
        
        # filter the valid answers (at least one column filled)
        valid_df = self.filter_valid_responses(df, existing_cols)
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # number of ranks for the weighting
        num_ranks = len(existing_cols)
        
        # compute the weighted scores
        # the columns are ranks (rank 1, 2, 3...), the values are item names
        scores = {}
        rank_distribution = {}  # item_name -> {rank: count}
        
        for col_idx, col in enumerate(existing_cols):
            # rank from the column index (0 = rank 1, 1 = rank 2, etc.)
            rank = col_idx + 1
            # points for this rank: higher rank = more points
            points = num_ranks - rank + 1
            
            # the values in this column are item names
            for item_name in valid_df[col].dropna():
                if pd.isna(item_name) or str(item_name).strip() == '':
                    continue
                    
                item_name = str(item_name).strip()
                
                # add the score
                scores[item_name] = scores.get(item_name, 0) + points
                
                # track the rank distribution
                if item_name not in rank_distribution:
                    rank_distribution[item_name] = {}
                rank_distribution[item_name][rank] = rank_distribution[item_name].get(rank, 0) + 1
        
        if not scores:
            return AnalysisResult(
                question_id=self.question_id,
                n=n,
                data={},
                warnings=["No ranking data found"]
            )
        
        # sort by score (descending)
        sorted_scores = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
        
        # compute the average rank
        avg_ranks = {}
        for item_name, rank_counts in rank_distribution.items():
            total_ranks = sum(r * c for r, c in rank_counts.items())
            total_count = sum(rank_counts.values())
            if total_count > 0:
                avg_ranks[item_name] = round(total_ranks / total_count, 2)
        
        # maximum possible score for the normalisation:
        # if every participant ranks all items: n * num_ranks points for rank 1
        max_possible_score = n * num_ranks
        
        # percentages (relative to the maximum score)
        percentages = {}
        for item, score in sorted_scores.items():
            percentages[item] = round(score / max_possible_score * 100, 1) if max_possible_score > 0 else 0
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=sorted_scores,
            percentages=percentages,
            description=self.generate_description_text(sorted_scores, avg_ranks, lang),
            metadata={
                "columns": existing_cols,
                "num_ranks": num_ranks,
                "rank_distribution": rank_distribution,
                "average_ranks": avg_ranks,
                "max_possible_score": max_possible_score
            }
        )
    
    def generate_description_text(
        self,
        scores: dict,
        avg_ranks: dict,
        lang: str
    ) -> str:
        """Writes a description text with translated values."""
        if not scores:
            return ""
        
        # get the translation mapping
        item_labels = self._get_item_labels(lang)
        
        # top 3 items (translated)
        top_items_src = list(scores.keys())[:3]
        top_items = [item_labels.get(item, item) for item in top_items_src]
        from utils.languages import text
        return text("handler.ranking.top", lang, items=", ".join(f'"{item}"' for item in top_items))
    
    def get_score_explanation(self, num_ranks: int, lang: str = "de") -> str:
        """
        Writes an explanation of how the weighted score is computed.
        
        Args:
            num_ranks: number of ranks (e.g. 9 for a top-9 ranking)
            lang: language
            
        Returns:
            explanation text
        """
        from utils.languages import text
        return text("handler.ranking.score_explanation", lang, n=num_ranks, n_minus_1=num_ranks - 1)
    
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
            chart_type: chart type (bar_weighted or rank_table)
            lang: language
        
        Returns:
            matplotlib figure
        """
        from utils.charts import ChartGenerator
        from utils.languages import text
        
        generator = ChartGenerator(kwargs.get('colors'))
        title = self.get_title(lang)
        colors = self.get_colors()
        
        # translated labels for the chart
        item_labels = self._get_item_labels(lang)
        if item_labels:
            translated_data = {
                item_labels.get(k, k): v for k, v in result.data.items()
            }
        else:
            translated_data = result.data
        
        if chart_type == "rank_table":
            return self._create_rank_table(result, title, lang)
        else:
            # default: weighted bars
            return generator.bar_horizontal(
                data=translated_data,
                title=title,
                colors=colors,
                lang=lang,
                show_percentages=False,  # points; a share of the maximum reads like a share of persons
                xlabel=self._get_score_label(lang),
                preserve_order=True,  # keep the ranking order
                n_info=text(
                    "chart.ranking", lang, k=result.metadata.get("num_ranks", len(self.columns)), n=result.n),
            )
    
    def _create_rank_table(
        self,
        result: AnalysisResult,
        title: str,
        lang: str
    ) -> plt.Figure:
        """Creates a rank table as a figure."""
        fig, ax = plt.subplots(figsize=(10, max(6, len(result.data) * 0.5)))
        ax.axis('off')
        
        # prepare the table data
        headers = self._get_table_headers(lang)
        
        # translated labels
        item_labels = self._get_item_labels(lang)
        
        avg_ranks = result.metadata.get("average_ranks", {})
        rows = []
        for idx, (item, score) in enumerate(result.data.items(), 1):
            translated_item = item_labels.get(item, item) if item_labels else item
            avg_rank = avg_ranks.get(item, "-")
            pct = result.percentages.get(item, 0) if result.percentages else 0
            rows.append([idx, translated_item, f"{score:,}", f"{avg_rank}", f"{pct}%"])
        
        # create the table
        table = ax.table(
            cellText=rows,
            colLabels=headers,
            loc='center',
            cellLoc='left'
        )
        
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.2, 1.5)
        
        # format the header
        for i in range(len(headers)):
            table[(0, i)].set_facecolor('#4472C4')
            table[(0, i)].set_text_props(color='white', fontweight='bold')
        
        # Alternating row colors
        for i in range(1, len(rows) + 1):
            color = '#f0f0f0' if i % 2 == 0 else 'white'
            for j in range(len(headers)):
                table[(i, j)].set_facecolor(color)
        
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        
        plt.tight_layout()
        return fig
    
    def _get_table_headers(self, lang: str) -> list:
        """The table headers in the chosen language."""
        from utils.languages import catalog_value
        return list(catalog_value("handler.ranking.table_headers", lang))
    
    def _get_score_label(self, lang: str) -> str:
        """The score label in the chosen language."""
        from utils.languages import catalog_value
        return catalog_value("handler.ranking.score_label", lang)
