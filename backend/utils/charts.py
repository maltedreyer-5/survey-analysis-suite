# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Visualisation module of the survey analysis tool: multilingual charts with
matplotlib and plotly.
"""

import logging
from pathlib import Path
from typing import Optional, Dict
from io import BytesIO

import matplotlib
from utils.languages import text as _text
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

from .translation import get_translation_manager

logger = logging.getLogger(__name__)


def _apply_label_replacement(text: str) -> str:
    """Applies the label replacements (imported lazily to avoid a circular import)."""
    try:
        from . import apply_label_replacement
        return apply_label_replacement(text)
    except ImportError:
        return text


def _apply_replacements_to_dict(data: dict) -> dict:
    """Applies the label replacements to dictionary keys."""
    try:
        from . import apply_label_replacements_to_dict
        return apply_label_replacements_to_dict(data)
    except ImportError:
        return data

# matplotlib settings for professional output
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['DejaVu Sans', 'Arial', 'Helvetica'],
    'font.size': 11,           # increased from 10
    'axes.titlesize': 14,      # increased from 12
    'axes.labelsize': 12,      # increased from 10
    'xtick.labelsize': 11,     # increased from 9
    'ytick.labelsize': 11,     # increased from 9
    'legend.fontsize': 10,     # increased from 9
    'figure.titlesize': 16,    # increased from 14
    'figure.dpi': 150,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.1,
})


class ChartGenerator:
    """Creates charts of survey results."""
    
    # default colour palette (desaturated)
    DEFAULT_COLORS = {
        'positive': '#5cb85c',
        'negative': '#d9534f',
        'neutral': '#999999',
        'primary': '#337ab7',
        'secondary': '#5bc0de',
        'palette': [
            '#337ab7', '#5cb85c', '#5bc0de', '#f0ad4e',
            '#d9534f', '#9b59b6', '#1abc9c', '#e74c3c',
            '#3498db', '#2ecc71', '#e67e22', '#95a5a6'
        ]
    }
    
    def __init__(self, colors: Optional[dict] = None):
        """
        Initialises the ChartGenerator.
        
        Args:
            colors: optional colour configuration
        """
        self.colors = {**self.DEFAULT_COLORS, **(colors or {})}
        self.tm = get_translation_manager()
    
    def _get_color(self, key: str, index: int = 0) -> str:
        """
        A colour from the configuration.
        
        Args:
            key: colour key (e.g. 'positive', 'primary') or 'palette' for indexed colours
            index: index of palette colours
            
        Returns:
            hex colour code as a string
        """
        # special case: palette, get the colour by index
        if key == 'palette':
            palette = self.colors.get('palette', self.DEFAULT_COLORS['palette'])
            if isinstance(palette, list) and len(palette) > 0:
                return palette[index % len(palette)]
            return self.DEFAULT_COLORS['palette'][index % len(self.DEFAULT_COLORS['palette'])]
        
        # normal colours (positive, negative, primary, etc.)
        if key in self.colors:
            color = self.colors[key]
            # in case a list was passed by mistake
            if isinstance(color, list):
                return color[index % len(color)] if color else self.DEFAULT_COLORS['primary']
            return color
        
        # fallback: use the palette
        palette = self.colors.get('palette', self.DEFAULT_COLORS['palette'])
        return palette[index % len(palette)]
    
    def bar_horizontal(
        self,
        data: dict[str, int | float],
        title: str,
        xlabel: str = "",
        show_percentages: bool = True,
        total: Optional[int] = None,
        colors: Optional[dict[str, str]] = None,
        lang: str = "de",
        figsize: tuple = (10, 6),
        n_info: Optional[str] = None,
        preserve_order: bool = False,
        value_mode: str = "count",
    ) -> plt.Figure:
        """
        Creates a horizontal bar chart.

        value_mode: "count" draws the counts (label "count (pct %)"); "percent"
        draws the share of ``total`` (label "pct % (count)"), so the axis is
        in percent and an axis title in percent is correct.
        
        Args:
            data: dictionary {label: value}
            title: chart title
            xlabel: x axis label
            show_percentages: whether percentages are shown
            total: total for the percentages
            colors: optional colours per label
            lang: language of the labels
            figsize: size of the figure
            n_info: optional n information (e.g. "n=50" or "120 mentions by 50 persons")
            preserve_order: if True, the order of the data is kept (no sorting)
        
        Returns:
            matplotlib figure
        """
        fig, ax = plt.subplots(figsize=figsize)
        
        # apply the label replacements
        data = _apply_replacements_to_dict(data)
        
        # sort the data or keep the order
        if preserve_order:
            # keep the order of the dictionary
            labels = list(data.keys())
            values = list(data.values())
        else:
            # default: sort by value, descending
            sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
            labels = [item[0] for item in sorted_items]
            values = [item[1] for item in sorted_items]
        
        # assign colours, with validation
        bar_colors = []
        for i, label in enumerate(labels):
            if colors and label in colors:
                color = colors[label]
                # validate the colour value
                if isinstance(color, str) and (color.startswith('#') or color in plt.colormaps() or color in ['red', 'green', 'blue', 'yellow', 'orange', 'purple', 'gray', 'black', 'white']):
                    bar_colors.append(color)
                else:
                    bar_colors.append(self._get_color('palette', i))
            else:
                bar_colors.append(self._get_color('palette', i))
        
        # safety check: bar_colors empty or invalid
        if not bar_colors or len(bar_colors) != len(labels):
            bar_colors = [self._get_color('palette', i) for i in range(len(labels))]
        
        # y positions
        y_pos = np.arange(len(labels))
        
        if total is None:
            total = sum(values)
        plotted = ([v / total * 100 for v in values] if value_mode == "percent" and total
                   else list(values))
        
        # draw the bars
        bars = ax.barh(y_pos, plotted, color=bar_colors, edgecolor='white', height=0.7)
        
        # labels
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.invert_yaxis()  # largest value on top
        
        top = max(plotted) if plotted else 1
        for bar, val, shown in zip(bars, values, plotted):
            if value_mode == "percent" and total:
                text = f'{shown:.0f}% ({val})'
            elif show_percentages and total > 0:
                text = f'{val} ({val / total * 100:.1f}%)'
            else:
                text = str(val)
            if bar.get_width() > top * 0.3:
                ax.text(bar.get_width() - top * 0.02, bar.get_y() + bar.get_height()/2,
                       text, ha='right', va='center', color='white', fontweight='bold')
            else:
                ax.text(bar.get_width() + top * 0.02, bar.get_y() + bar.get_height()/2,
                       text, ha='left', va='center', color='black')
        
        ax.set_xlabel(xlabel)
        ax.set_title(title, fontweight='bold', pad=15)
        ax.set_xlim(0, (max(plotted) if plotted else 1) * 1.15)
        
        # remove the frame
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        
        # show the n information below the chart
        if n_info:
            # more room at the bottom for a longer text
            current_bottom = fig.subplotpars.bottom
            fig.subplots_adjust(bottom=current_bottom + 0.08)
            # text right-aligned below the x axis
            fig.text(0.95, 0.02, n_info, ha='right', va='bottom',
                     fontsize=8, fontstyle='italic', color='gray',
                     wrap=True)
        
        return fig
    
    def _scale_colors(self, n: int, positive_first: bool = True) -> list:
        """Colours by position on the scale: positive end green, negative end red, middle grey."""
        from matplotlib.colors import to_rgb
        picks = []
        for key in ('positive', 'neutral', 'negative'):
            c = self.colors.get(key, self.DEFAULT_COLORS[key])
            picks.append(to_rgb(c if isinstance(c, str) else self.DEFAULT_COLORS[key]))
        pos, neutral, neg = picks
        out = []
        for i in range(n):
            x = i / (n - 1) if n > 1 else 0.5          # 0 = first category
            if not positive_first:
                x = 1 - x
            a, b, w = (pos, neutral, x * 2) if x <= 0.5 else (neutral, neg, (x - 0.5) * 2)
            out.append(tuple((1 - w) * ca + w * cb for ca, cb in zip(a, b)))
        return out

    def _excluded_bars(self, ax, y_pos, items, excluded, start, label):
        """Grey bar for answers outside the scale, to the right of the scale part."""
        values = [float((excluded or {}).get(item, 0.0)) for item in items]
        if not any(values):
            return start
        ax.barh(y_pos, values, left=start, color='#bbbbbb', height=0.7, edgecolor='white', label=label)
        for y, v in zip(y_pos, values):
            if v > 0:
                ax.text(start + v + 1, y, f'{v:.0f}%', va='center', fontsize=8, color='#666666')
        return start + max(values) + 8

    def _note(self, fig, note):
        if note:
            fig.subplots_adjust(bottom=fig.subplotpars.bottom + 0.08)
            fig.text(0.95, 0.02, note, ha='right', va='bottom', fontsize=8,
                     fontstyle='italic', color='gray', wrap=True)

    def stacked_bar_100pct(
        self,
        data: pd.DataFrame,
        title: str,
        categories: list[str],
        colors: Optional[dict[str, str]] = None,
        lang: str = "de",
        figsize: tuple = (12, 8),
        excluded: Optional[Dict[str, float]] = None,
        excluded_label: str = "",
        note: Optional[str] = None,
        positive_first: bool = True,
    ) -> plt.Figure:
        """
        100 % stacked bars per item over the scale ``categories`` (in scale order).

        ``data`` holds counts per item and scale category; answers outside the
        scale (e.g. "no answer") are not part of the 100 % and are drawn as a
        separate grey bar from ``excluded`` ({item: % of all answers}).
        """
        fig, ax = plt.subplots(figsize=figsize)
        data = data.copy()
        data.index = [_apply_label_replacement(str(idx)) for idx in data.index]
        cats = [c for c in categories if c in data.columns]
        data = data[cats]
        totals = data.sum(axis=1).replace(0, np.nan)
        data_pct = data.div(totals, axis=0).fillna(0) * 100
        scale_colors = self._scale_colors(len(categories), positive_first)
        y_pos = np.arange(len(data_pct.index))
        left = np.zeros(len(data_pct.index))
        for i, cat in enumerate(categories):
            if cat not in data_pct.columns:
                continue
            own = (colors or {}).get(cat)
            values = data_pct[cat].values
            bars = ax.barh(y_pos, values, left=left, color=own if isinstance(own, str) else scale_colors[i],
                           label=cat, height=0.7, edgecolor='white', linewidth=0.5)
            for bar, val in zip(bars, values):
                if val > 8:
                    ax.text(bar.get_x() + bar.get_width()/2, bar.get_y() + bar.get_height()/2,
                            f'{val:.0f}%', ha='center', va='center', color='white',
                            fontsize=8, fontweight='bold')
            left += values
        end = self._excluded_bars(ax, y_pos, list(data_pct.index), excluded, 104, excluded_label)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(data_pct.index)
        ax.set_xlim(0, max(100, end))
        ax.set_xticks([0, 20, 40, 60, 80, 100])
        ax.set_xlabel('%')
        ax.set_title(title, fontweight='bold', pad=15)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.08),
                  ncol=min(len(categories) + 1, 6), frameon=False)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        plt.tight_layout()
        self._note(fig, note)
        return fig

    def diverging_bar(
        self,
        data: pd.DataFrame,
        title: str,
        categories: list[str],
        lang: str = "de",
        figsize: tuple = (12, 8),
        excluded: Optional[Dict[str, float]] = None,
        excluded_label: str = "",
        note: Optional[str] = None,
        positive_first: bool = True,
        neutral_label: str = "",
    ) -> plt.Figure:
        """
        Diverging bars over the scale ``categories`` (in scale order).

        The positive half of the scale goes to the right, the negative half to
        the left; a middle category (odd scale) is split half and half around
        zero. All categories are shown; the axis shows shares without sign.
        With ``positive_first`` the first category is the positive end.
        Answers outside the scale: as in stacked_bar_100pct.
        """
        from matplotlib.ticker import FuncFormatter
        fig, ax = plt.subplots(figsize=figsize)
        data = data.copy()
        data.index = [_apply_label_replacement(str(idx)) for idx in data.index]
        cats = [c for c in categories if c in data.columns]
        totals = data[cats].sum(axis=1).replace(0, np.nan)
        pct = data[cats].div(totals, axis=0).fillna(0) * 100
        k = len(cats)
        colors = self._scale_colors(k, positive_first)
        col_of = {c: colors[i] for i, c in enumerate(cats)}
        order = cats if positive_first else list(reversed(cats))     # positive end first
        half = k // 2
        positive, negative = order[:half], order[k - half:]
        middle = order[half] if k % 2 else None
        y_pos = np.arange(len(pct.index))
        right = pct[middle].values / 2 if middle else np.zeros(len(y_pos))
        left = -right.copy()
        if middle:
            ax.barh(y_pos, pct[middle].values, left=left, color=col_of[middle], height=0.6,
                    label=f"{middle} ({neutral_label})" if neutral_label else middle)
        for c in reversed(positive):          # the positive end is outermost
            vals = pct[c].values
            ax.barh(y_pos, vals, left=right, color=col_of[c], height=0.6, label=c)
            for y, v, x in zip(y_pos, vals, right):
                if v > 5:
                    ax.text(x + v / 2, y, f'{v:.0f}%', ha='center', va='center', color='white',
                            fontweight='bold', fontsize=8)
            right = right + vals
        for c in reversed(negative):
            vals = pct[c].values
            ax.barh(y_pos, -vals, left=left, color=col_of[c], height=0.6, label=c)
            for y, v, x in zip(y_pos, vals, left):
                if v > 5:
                    ax.text(x - v / 2, y, f'{v:.0f}%', ha='center', va='center', color='white',
                            fontweight='bold', fontsize=8)
            left = left - vals
        limit = max(float(np.max(right)) if len(right) else 0.0,
                    float(-np.min(left)) if len(left) else 0.0, 10.0) + 5
        end = self._excluded_bars(ax, y_pos, list(pct.index), excluded, limit + 2, excluded_label)
        ax.set_xlim(-limit, max(limit, end))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{abs(v):.0f}'))
        ax.set_yticks(y_pos)
        ax.set_yticklabels(pct.index)
        ax.axvline(0, color='black', linewidth=0.5)
        ax.set_xlabel('%')
        ax.set_title(title, fontweight='bold', pad=15)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.08), ncol=min(k + 1, 6), frameon=False)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        plt.tight_layout()
        self._note(fig, note)
        return fig

    def ranking_chart(
        self,
        data: dict[str, float],
        title: str,
        lang: str = "de",
        figsize: tuple = (10, 6)
    ) -> plt.Figure:
        """
        Creates a ranking chart with weighted points.
        
        Args:
            data: dictionary {item: score}
            title: title
            lang: language
            figsize: size
        
        Returns:
            matplotlib figure
        """
        fig, ax = plt.subplots(figsize=figsize)
        
        # apply the label replacements
        data = _apply_replacements_to_dict(data)
        
        # sort by score
        sorted_items = sorted(data.items(), key=lambda x: x[1], reverse=True)
        labels = [item[0] for item in sorted_items]
        values = [item[1] for item in sorted_items]
        
        y_pos = np.arange(len(labels))
        
        # colour gradient by rank
        colors = plt.cm.Blues(np.linspace(0.8, 0.3, len(values)))
        
        bars = ax.barh(y_pos, values, color=colors, height=0.7, edgecolor='white')
        
        # Rang-Labels
        primary_color = self.colors.get('primary', self.DEFAULT_COLORS['primary'])
        if not isinstance(primary_color, str):
            primary_color = self.DEFAULT_COLORS['primary']
        
        for i, (bar, val) in enumerate(zip(bars, values)):
            # rank
            ax.text(-max(values)*0.08, bar.get_y() + bar.get_height()/2,
                   f'#{i+1}', ha='center', va='center', fontweight='bold',
                   fontsize=10, color=primary_color)
            
            # score
            ax.text(bar.get_width() + max(values)*0.02, bar.get_y() + bar.get_height()/2,
                   f'{val:.1f}', ha='left', va='center', fontsize=9)
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.set_xlabel(_text('chart.weighted_points', lang))
        ax.set_title(title, fontweight='bold', pad=15)
        ax.set_xlim(-max(values)*0.15, max(values)*1.15)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        ax.tick_params(left=False)
        
        plt.tight_layout()
        return fig
    
    def matrix_rows_by_segment(
        self,
        data: dict,
        scale: list,
        title: str,
        segment_label: str = "",
        lang: str = "de",
        colors: Optional[dict] = None,
    ) -> Optional[plt.Figure]:
        """
        Matrix question by segment, small multiples: one panel per matrix row,
        in it one 100 % stacked bar per segment over the scale options.
        data: {segment: {option: {row: percent}}} (percentages of the matrix handler).
        """
        rows = []
        for by_option in data.values():
            for opt in by_option.values():
                for row in (opt or {}):
                    if row not in rows:
                        rows.append(row)
        if not rows or not scale:
            return None
        segments = list(data)
        palette = self.colors.get('palette') or ['#4c78a8']
        ncols = min(2, len(rows))
        nrows = (len(rows) + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols, figsize=(6 * ncols, 0.45 * len(segments) * nrows + 1.6 * nrows + 0.8),
                                 squeeze=False)
        for i, row in enumerate(rows):
            ax = axes[i // ncols][i % ncols]
            left = [0.0] * len(segments)
            for j, opt in enumerate(scale):
                vals = [float(((data[seg].get(opt) or {}).get(row)) or 0) for seg in segments]
                colour = (colors or {}).get(opt) or palette[j % len(palette)]   # the colours of the scale
                ax.barh(segments, vals, left=left, color=colour, label=str(opt), height=0.6)
                left = [a + b for a, b in zip(left, vals)]
            ax.set_xlim(0, 100)
            ax.set_title(str(row)[:70], fontsize=9)
            ax.invert_yaxis()
            ax.tick_params(labelsize=8)
        for k in range(len(rows), nrows * ncols):
            axes[k // ncols][k % ncols].axis('off')
        handles, labels = axes[0][0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='lower center', ncol=min(len(scale), 5), fontsize=8, frameon=False)
        fig.suptitle(title, fontweight='bold', fontsize=10)
        fig.tight_layout(rect=(0, 0.06, 1, 0.95))
        return fig

    def matrix_means_by_segment(
        self,
        means: dict,
        title: str,
        segment_label: str = "",
        lang: str = "de",
    ) -> Optional[plt.Figure]:
        """
        Matrix question by segment: mean on the scale per matrix row (groups) and segment (bars).
        means: {segment: {row: mean}}.
        """
        segments = [s for s in means if means[s]]
        rows = []
        for s in segments:
            for r in means[s]:
                if r not in rows:
                    rows.append(r)
        if not rows or not segments:
            return None
        palette = self.colors.get('palette') or ['#4c78a8']
        fig, ax = plt.subplots(figsize=(10, 0.5 * len(rows) * len(segments) + 1.5))
        height = 0.8 / len(segments)
        for j, seg in enumerate(segments):
            ys = [i + j * height for i in range(len(rows))]
            vals = [means[seg].get(r) for r in rows]
            ax.barh([y for y, v in zip(ys, vals) if v is not None], [v for v in vals if v is not None],
                    height=height, color=palette[j % len(palette)], label=str(seg))
        ax.set_yticks([i + 0.4 - height / 2 for i in range(len(rows))])
        ax.set_yticklabels([str(r)[:60] for r in rows], fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel(_text('chart.mean_on_scale', lang))
        ax.legend(title=segment_label or None, fontsize=8, frameon=False, loc='upper left',
                  bbox_to_anchor=(1.01, 1.0))
        ax.set_title(title, fontweight='bold', pad=12)
        fig.tight_layout()
        return fig

    def bar_grouped(
        self,
        data: dict[str, dict[str, float]],
        title: str,
        segment_label: str = "",
        show_percentages: bool = True,
        lang: str = "de",
        figsize: tuple = (12, 8),
        n_info: Optional[str] = None,
        category_order: Optional[list] = None,
    ) -> plt.Figure:
        """
        Creates a grouped bar chart for segment comparisons.

        category_order: configured order of the categories (e.g. an ordered
        scale); categories not in it follow alphabetically.
        
        Args:
            data: dictionary {segment: {category: percent}}
                  e.g. {"Student": {"Yes": 45.2, "No": 54.8}, "Staff": {...}}
            title: chart title
            segment_label: label of the segmentation (e.g. "Role")
            show_percentages: whether percentages are shown
            lang: language
            figsize: size
            n_info: n information text
        
        Returns:
            matplotlib figure
        """
        fig, ax = plt.subplots(figsize=figsize)
        
        if not data:
            ax.text(0.5, 0.5, _text('chart.no_data', lang), ha='center', va='center')
            return fig
        
        # extract categories and segments
        segments = list(data.keys())
        
        # collect all categories (in a consistent order)
        all_categories = set()
        for seg_data in data.values():
            all_categories.update(seg_data.keys())
        # the same order as the small multiples: configured, else numeric, else by total share
        raw_categories = self._category_sequence(all_categories, data, category_order)
        
        # apply the label replacements
        categories = [_apply_label_replacement(c) for c in raw_categories]
        
        # bar parameters
        n_segments = len(segments)
        n_categories = len(categories)
        bar_width = 0.8 / n_segments
        
        # y positions of the categories
        y_pos = np.arange(n_categories)
        
        # colours of the segments
        segment_colors = [self._get_color('palette', i) for i in range(n_segments)]
        
        # draw the bars of every segment
        for i, (segment, seg_data) in enumerate(data.items()):
            values = [seg_data.get(cat, 0) for cat in raw_categories]
            offset = (i - n_segments/2 + 0.5) * bar_width
            
            bars = ax.barh(
                y_pos + offset, 
                values, 
                bar_width * 0.9,
                label=segment,
                color=segment_colors[i],
                edgecolor='white'
            )
            
            # values at the bars (only if > 5%)
            for bar, val in zip(bars, values):
                if val > 5:
                    ax.text(
                        bar.get_width() + 0.5,
                        bar.get_y() + bar.get_height()/2,
                        f'{val:.0f}%' if show_percentages else f'{val:.0f}',
                        ha='left', va='center', fontsize=8
                    )
        
        # axes
        ax.set_yticks(y_pos)
        ax.set_yticklabels(categories)
        ax.invert_yaxis()
        
        # x axis
        if show_percentages:
            ax.set_xlabel('%')
            ax.set_xlim(0, 105)
        
        ax.set_title(title, fontweight='bold', pad=15)
        
        # legend
        ax.legend(
            title=segment_label,
            loc='upper center',
            bbox_to_anchor=(0.5, -0.08),
            ncol=min(n_segments, 4),
            frameon=False
        )
        
        # Styling
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.axvline(0, color='gray', linewidth=0.5)
        
        plt.tight_layout()
        
        # n information below the chart
        if n_info:
            current_bottom = fig.subplotpars.bottom
            fig.subplots_adjust(bottom=current_bottom + 0.08)
            fig.text(0.95, 0.02, n_info, ha='right', va='bottom',
                     fontsize=8, fontstyle='italic', color='gray',
                     wrap=True)
        
        return fig
    
    @staticmethod
    def _category_sequence(all_categories, segment_results, category_order=None) -> list:
        """Order of the categories of grouped charts, the same in every panel."""
        cats = list(all_categories)
        if category_order:
            ordered = [c for c in category_order if c in all_categories]
            return ordered + sorted((c for c in cats if c not in ordered), key=str)
        try:
            return sorted(cats, key=lambda c: float(str(c).replace(',', '.')))
        except ValueError:
            pass
        totals = {}
        for seg_data in segment_results.values():
            result = seg_data.get('result') if isinstance(seg_data, dict) and 'result' in seg_data else None
            data = (getattr(result, 'percentages', None) if result is not None and not isinstance(result, dict)
                    else (result or {}).get('percentages')) if result is not None else seg_data
            for c, v in (data or {}).items():
                try:
                    totals[c] = totals.get(c, 0) + float(v or 0)
                except (TypeError, ValueError):
                    pass
        return sorted(cats, key=lambda c: (-totals.get(c, 0), str(c)))

    def small_multiples(
        self,
        segment_results: dict,
        title: str,
        segment_label: str = "",
        show_percentages: bool = True,
        lang: str = "de",
        max_cols: int = 3,
        subplot_size: tuple = (4, 3),
        n_info: Optional[str] = None,
        category_order: Optional[list] = None,
        colors: Optional[dict] = None,
    ) -> plt.Figure:
        """
        Creates small multiples: several small charts in a grid.

        category_order: the configured order of the categories (ordered scales); without
        it, numbers in their numeric order and other categories by their total share.
        colors: {category: colour}, e.g. the colours of a scale; else the palette.
        
        Args:
            segment_results: dictionary {segment: {category: percent}}
                  e.g. {"Student": {"Yes": 45.2, "No": 54.8}, "Staff": {...}}
            title: main title
            segment_label: label of the segmentation (e.g. "Role")
            show_percentages: whether percentages are shown
            lang: language
            max_cols: maximum number of columns in the grid
            subplot_size: size of each subplot
            n_info: n information text
        
        Returns:
            matplotlib figure
        """
        if not segment_results:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, _text('chart.no_data', lang), ha='center', va='center')
            return fig
        
        segments = list(segment_results.keys())
        n_segments = len(segments)
        
        # calculate the grid layout
        n_cols = min(n_segments, max_cols)
        n_rows = (n_segments + n_cols - 1) // n_cols
        
        # create the figure
        fig_width = subplot_size[0] * n_cols
        fig_height = subplot_size[1] * n_rows + 1  # extra room for the title
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(fig_width, fig_height))
        
        # make sure axes is a 2D array
        if n_rows == 1 and n_cols == 1:
            axes = np.array([[axes]])
        elif n_rows == 1:
            axes = axes.reshape(1, -1)
        elif n_cols == 1:
            axes = axes.reshape(-1, 1)
        
        # collect all categories for a consistent order
        all_categories = set()
        for seg_data in segment_results.values():
            if isinstance(seg_data, dict) and 'result' in seg_data:
                result = seg_data['result']
                if hasattr(result, 'data'):
                    data = result.data
                else:
                    data = result.get('data', {}) if isinstance(result, dict) else {}
            else:
                data = seg_data
            if isinstance(data, dict):
                all_categories.update(data.keys())
        categories = self._category_sequence(all_categories, segment_results, category_order)
        
        # apply the label replacements
        display_categories = [_apply_label_replacement(str(c)) for c in categories]
        
        # colours: those of the scale where given, else the palette
        colors = [(colors or {}).get(c) or self._get_color('palette', i) for i, c in enumerate(categories)]
        
        # create every subplot
        for idx, (segment, seg_data) in enumerate(segment_results.items()):
            row = idx // n_cols
            col = idx % n_cols
            ax = axes[row, col]
            
            # extract the data
            if isinstance(seg_data, dict) and 'result' in seg_data:
                result = seg_data['result']
                n = seg_data.get('n', 0)
                warning = seg_data.get('warning')
                if hasattr(result, 'percentages'):
                    data = result.percentages or {}
                elif isinstance(result, dict):
                    data = result.get('percentages', result.get('data', {}))
                else:
                    data = {}
            else:
                data = seg_data
                n = sum(data.values()) if isinstance(data, dict) else 0
                warning = None
            
            # values of this segment
            values = [data.get(cat, 0) for cat in categories]
            y_pos = np.arange(len(categories))
            
            # draw the bars
            bars = ax.barh(y_pos, values, color=colors, edgecolor='white')
            
            # show the values
            for bar, val in zip(bars, values):
                if val > 5:
                    ax.text(
                        bar.get_width() + 1,
                        bar.get_y() + bar.get_height()/2,
                        f'{val:.0f}%' if show_percentages else f'{val:.0f}',
                        ha='left', va='center', fontsize=7
                    )
            
            # axes
            ax.set_yticks(y_pos)
            ax.set_yticklabels(display_categories, fontsize=8)
            ax.invert_yaxis()
            ax.set_xlim(0, 105 if show_percentages else max(values) * 1.2 if values else 100)
            
            # title with n and a warning
            subplot_title = segment
            if n:
                subplot_title += f" (n={n})"
            if warning:
                subplot_title += " ⚠️"
            ax.set_title(subplot_title, fontsize=10, fontweight='bold')
            
            # Styling
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.axvline(0, color='gray', linewidth=0.5)
        
        # hide empty subplots
        for idx in range(n_segments, n_rows * n_cols):
            row = idx // n_cols
            col = idx % n_cols
            axes[row, col].set_visible(False)
        
        # main title
        fig.suptitle(title, fontsize=14, fontweight='bold', y=1.02)
        
        plt.tight_layout()
        
        # n information below the chart
        if n_info:
            current_bottom = fig.subplotpars.bottom
            fig.subplots_adjust(bottom=current_bottom + 0.08)
            fig.text(0.95, 0.02, n_info, ha='right', va='bottom', 
                    fontsize=8, fontstyle='italic', color='gray',
                    wrap=True)
        
        return fig
    
    def histogram(
        self,
        bins: list[float],
        counts: list[int],
        title: str,
        xlabel: str = "",
        ylabel: str = "Häufigkeit",
        decimals: int = 1,
        color_key: str = "primary",
    ) -> plt.Figure:
        """
        Histogram of numeric data.
        
        Rendered from the bin edges and counts precomputed by the NumericHandler
        (not from raw values: the bin logic belongs in the handler).
        
        Args:
            bins: bin edges (length n+1 for n bins).
            counts: number of values per bin (length n).
            title: chart title.
            xlabel: x axis label (unit).
            ylabel: y axis label.
            decimals: number of decimals of the x tick labels.
            color_key: colour from self.colors (default 'primary').
        
        Returns:
            matplotlib figure.
        """
        if not bins or not counts or len(counts) == 0:
            logger.warning("histogram: no data")
            return None
        
        fig, ax = plt.subplots(figsize=(10, 5))
        
        # bin centres and widths
        widths = [bins[i + 1] - bins[i] for i in range(len(counts))]
        centers = [bins[i] + widths[i] / 2 for i in range(len(counts))]
        
        color = self._get_color(color_key)
        ax.bar(centers, counts, width=widths, color=color,
               edgecolor='white', linewidth=0.5, align='center')
        
        ax.set_title(title, fontsize=12, fontweight='bold', pad=15)
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        
        # x axis ticks: bin edges, formatted with decimals
        format_str = f"{{:.{decimals}f}}"
        ax.set_xticks(bins)
        ax.set_xticklabels([format_str.format(b) for b in bins],
                           rotation=45, ha='right', fontsize=8)
        
        # y axis: integer ticks
        ax.yaxis.get_major_locator().set_params(integer=True)
        
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        return fig
    
    def box_plot(
        self,
        stats: dict,
        title: str,
        ylabel: str = "",
        outliers: Optional[list[float]] = None,
        color_key: str = "primary",
    ) -> plt.Figure:
        """
        Box plot from precomputed quartiles (min/q25/median/q75/max).
        
        Takes computed statistics instead of raw values. Two advantages:
        - consistent with the numeric handler (statistics computed once in
          analyze(), then reused for the table AND the chart)
        - works in segmented comparisons, where only the aggregated
          statistics per segment exist
        
        Args:
            stats: dict with the keys 'min', 'q25', 'median', 'q75', 'max'.
            title: chart title.
            ylabel: y axis label (unit).
            outliers: optional list of outliers (drawn as points).
            color_key: colour from self.colors.
        
        Returns:
            matplotlib figure.
        """
        required = ('min', 'q25', 'median', 'q75', 'max')
        if not all(k in stats for k in required):
            logger.warning(f"box_plot: stats missing {[k for k in required if k not in stats]}")
            return None
        
        fig, ax = plt.subplots(figsize=(6, 5))
        
        color = self._get_color(color_key)
        
        # manual box plot, as there are no raw values;
        # ax.bxp expects a list of dicts with the statistics
        bxp_data = [{
            'med':       stats['median'],
            'q1':        stats['q25'],
            'q3':        stats['q75'],
            'whislo':    stats['min'],
            'whishi':    stats['max'],
            'fliers':    outliers or [],
            'mean':      stats.get('mean'),
        }]
        
        ax.bxp(
            bxp_data,
            showfliers=bool(outliers),
            showmeans=stats.get('mean') is not None,
            patch_artist=True,
            boxprops=dict(facecolor=color, alpha=0.6, edgecolor='black'),
            medianprops=dict(color='black', linewidth=2),
            whiskerprops=dict(color='black'),
            capprops=dict(color='black'),
            flierprops=dict(marker='o', markerfacecolor=self._get_color('negative'),
                            markersize=6, alpha=0.7),
            meanprops=dict(marker='D', markerfacecolor='white',
                           markeredgecolor='black', markersize=6),
        )
        
        ax.set_title(title, fontsize=12, fontweight='bold', pad=15)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=10)
        ax.set_xticks([])  # only one box, no x tick needed
        
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        return fig
    
    def segment_means_bar(
        self,
        segment_stats: Dict[str, Dict[str, float]],
        title: str,
        xlabel: str = "",
        decimals: int = 1,
        order: Optional[list[str]] = None,
        color_key: str = "primary",
        note: Optional[str] = None,
    ) -> plt.Figure:
        """
        Horizontal bar chart of the means per segment.
        
        Used by the NumericHandler and the segmented pipeline to show mean
        comparisons between dimensions, like bar_horizontal, but with the mean
        and error bars (SD or standard error) instead of counts/percentages.
        
        Args:
            segment_stats: {segment_name: {'mean': float, 'std': float (optional),
                            'n': int (optional)}}
            title: chart title.
            xlabel: x axis label (unit).
            decimals: decimals of the value labels.
            order: optional order of the segments; if None, sorted by mean,
                descending.
            color_key: colour from self.colors.
        
        Returns:
            matplotlib figure.
        """
        if not segment_stats:
            logger.warning("segment_means_bar: no data")
            return None
        
        # determine the order
        if order:
            seg_names = [s for s in order if s in segment_stats]
            # append the rest
            for s in segment_stats:
                if s not in seg_names:
                    seg_names.append(s)
        else:
            seg_names = sorted(
                segment_stats.keys(),
                key=lambda s: segment_stats[s].get('mean', 0),
                reverse=True,
            )
        
        means = [segment_stats[s].get('mean', 0) for s in seg_names]
        stds = [segment_stats[s].get('std', 0) for s in seg_names]
        if all('low' in segment_stats[s] and 'high' in segment_stats[s] for s in seg_names):
            # asymmetric whiskers: lower and upper quartile around the median
            lows = [segment_stats[s]['mean'] - segment_stats[s]['low'] for s in seg_names]
            highs = [segment_stats[s]['high'] - segment_stats[s]['mean'] for s in seg_names]
            stds = [lows, highs]
        ns = [segment_stats[s].get('n', None) for s in seg_names]
        
        # Y-Labels: "Segment (n=X)"
        y_labels = []
        for s, n in zip(seg_names, ns):
            if n is not None:
                y_labels.append(f"{s} (n={n})")
            else:
                y_labels.append(s)
        
        # figure height depends on the number of segments
        fig_height = max(3, 0.6 * len(seg_names) + 1.5)
        fig, ax = plt.subplots(figsize=(10, fig_height))
        
        color = self._get_color(color_key)
        y_pos = np.arange(len(seg_names))
        
        # bars with error bars (standard deviation as indicator)
        bars = ax.barh(
            y_pos, means,
            xerr=stds if np.any(np.asarray(stds) > 0) else None,
            color=color, alpha=0.85,
            edgecolor='white', linewidth=0.5,
            error_kw={'capsize': 4, 'elinewidth': 1, 'alpha': 0.6},
        )
        
        # value labels to the right of the bars
        format_str = f"{{:.{decimals}f}}"
        x_max = max(means) + (float(np.max(stds)) if np.size(stds) else 0.0)
        for i, (bar, mean) in enumerate(zip(bars, means)):
            ax.text(
                bar.get_width() + (float(np.asarray(stds)[..., i].max()) if np.size(stds) else 0) + x_max * 0.01,
                bar.get_y() + bar.get_height() / 2,
                format_str.format(mean),
                va='center', ha='left',
                fontsize=9,
            )
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels(y_labels, fontsize=9)
        ax.invert_yaxis()  # first segment on top
        ax.set_title(title, fontsize=12, fontweight='bold', pad=15)
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=10)
        
        ax.grid(axis='x', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        
        plt.tight_layout()
        self._note(fig, note)
        return fig
    
    def save_figure(
        self,
        fig: plt.Figure,
        filepath: Path,
        formats: list[str] = None,
        close_after: bool = True
    ) -> list[Path]:
        """
        Saves a figure in several formats.
        
        Args:
            fig: matplotlib figure
            filepath: base path (without extension)
            formats: list of formats (default: ["png"])
            close_after: whether the figure is closed after saving (default: True)
        
        Returns:
            list of the saved file paths
        """
        if formats is None:
            formats = ["png"]
        
        saved_files = []
        filepath = Path(filepath)
        
        for fmt in formats:
            output_path = filepath.with_suffix(f".{fmt}")
            fig.savefig(output_path, format=fmt, bbox_inches='tight',
                       facecolor='white', edgecolor='none')
            saved_files.append(output_path)
            logger.info(f"Chart saved: {output_path}")
        
        # close the figure to avoid a memory leak
        if close_after:
            plt.close(fig)
        
        return saved_files
    
    def figure_to_bytes(self, fig: plt.Figure, format: str = "png", close_after: bool = True) -> bytes:
        """
        Converts a figure into bytes.
        
        Args:
            fig: matplotlib figure
            format: output format
            close_after: whether the figure is closed after the conversion (default: True)
        
        Returns:
            bytes of the figure
        """
        buf = BytesIO()
        fig.savefig(buf, format=format, bbox_inches='tight',
                   facecolor='white', edgecolor='none')
        buf.seek(0)
        result = buf.getvalue()
        
        # close the figure to avoid a memory leak
        if close_after:
            plt.close(fig)
        
        return result


def house_style(fig, ax, title: str, n_info: Optional[str] = None) -> None:
    """The style of the report charts: bold title, no top and right axis lines, n below."""
    ax.set_title(title, fontsize=12, fontweight='bold', pad=12)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    fig.tight_layout()
    if n_info:
        fig.subplots_adjust(bottom=fig.subplotpars.bottom + 0.06)
        fig.text(0.95, 0.01, n_info, ha='right', va='bottom', fontsize=8, fontstyle='italic', color='gray')


def create_chart(
    chart_type: str,
    data: dict | pd.DataFrame,
    title: str,
    **kwargs
) -> plt.Figure:
    """
    Factory function to create charts.
    
    Args:
        chart_type: type of the chart
        data: data
        title: title
        **kwargs: further parameters
    
    Returns:
        matplotlib figure
    
    Note:
        The caller should close the figure after use with plt.close(fig)
        or close_figure(fig), to avoid memory leaks.
    """
    generator = ChartGenerator(kwargs.get('colors'))
    
    chart_methods = {
        'bar_horizontal': generator.bar_horizontal,
        'stacked_bar_100pct': generator.stacked_bar_100pct,
        'pie': generator.pie_chart,
        'treemap': generator.treemap,
        'diverging_bar': generator.diverging_bar,
        'bar_weighted': generator.ranking_chart,
    }
    
    if chart_type not in chart_methods:
        raise ValueError(f"Unknown chart type: {chart_type}")
    
    return chart_methods[chart_type](data, title, **kwargs)


def close_figure(fig: plt.Figure) -> None:
    """
    Closes a matplotlib figure to free memory.
    
    Args:
        fig: the figure to close
    """
    if fig is not None:
        plt.close(fig)


def close_all_figures() -> None:
    """Closes all open matplotlib figures."""
    plt.close('all')
