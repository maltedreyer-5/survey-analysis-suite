# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Statistics module of the survey analysis tool: significance tests and
statistical analyses.
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class SignificanceResult:
    """Result of a significance test."""
    test_name: str
    statistic: float
    p_value: float
    is_significant: bool
    significance_level: float
    degrees_of_freedom: Optional[int] = None
    effect_size: Optional[float] = None
    interpretation: Optional[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class StatisticalAnalyzer:
    """Runs statistical analyses and significance tests."""
    
    def __init__(self, significance_level: float = 0.05, min_expected: float = 5):
        """
        Initialises the StatisticalAnalyzer.
        
        Args:
            significance_level: p value threshold for significance
            min_expected: minimum expected frequency for chi-square
        """
        self.significance_level = significance_level
        self.min_expected = min_expected
    
    def chi_square_test(
        self,
        observed: Dict[str, Dict[str, int]],
        row_labels: List[str] = None,
        col_labels: List[str] = None
    ) -> SignificanceResult:
        """
        Runs a chi-square test of independence.
        
        Args:
            observed: dictionary {segment: {category: count}}
                     e.g. {"Student": {"Yes": 45, "No": 55}, "Staff": {...}}
            row_labels: optional row labels (segments)
            col_labels: optional column labels (categories)
        
        Returns:
            SignificanceResult with the test results
        """
        warnings = []
        
        try:
            # build the contingency table
            if row_labels is None:
                row_labels = list(observed.keys())
            
            if col_labels is None:
                col_labels = set()
                for seg_data in observed.values():
                    col_labels.update(seg_data.keys())
                col_labels = sorted(col_labels)
            
            # build the matrix
            matrix = []
            for row in row_labels:
                row_data = observed.get(row, {})
                matrix.append([row_data.get(col, 0) for col in col_labels])
            
            observed_array = np.array(matrix)
            
            # check for valid data
            if observed_array.sum() == 0:
                return SignificanceResult(
                    test_name="Chi-square",
                    statistic=0.0,
                    p_value=1.0,
                    is_significant=False,
                    significance_level=self.significance_level,
                    interpretation="No data for the test",
                    warnings=["No valid data"]
                )
            
            # compute the expected frequencies
            row_totals = observed_array.sum(axis=1)
            col_totals = observed_array.sum(axis=0)
            total = observed_array.sum()
            
            expected = np.outer(row_totals, col_totals) / total
            
            # warning for low expected frequencies
            low_expected = (expected < self.min_expected).sum()
            if low_expected > 0:
                pct_low = (low_expected / expected.size) * 100
                warnings.append(
                    f"{low_expected} cells ({pct_low:.0f}%) have an expected frequency < {self.min_expected}"
                )
            
            # run the chi-square test
            chi2, p_value, dof, expected_freq = stats.chi2_contingency(observed_array)
            
            # effect size (Cramér's V)
            n = observed_array.sum()
            min_dim = min(observed_array.shape[0] - 1, observed_array.shape[1] - 1)
            if min_dim > 0 and n > 0:
                cramers_v = np.sqrt(chi2 / (n * min_dim))
            else:
                cramers_v = 0.0
            
            # Interpretation
            is_significant = p_value < self.significance_level
            
            if is_significant:
                if cramers_v < 0.1:
                    effect_desc = "weak"
                elif cramers_v < 0.3:
                    effect_desc = "medium"
                else:
                    effect_desc = "strong"
                interpretation = f"Significant association (p={p_value:.4f}) with {effect_desc} effect size (V={cramers_v:.3f})"
            else:
                interpretation = f"No significant association (p={p_value:.4f})"
            
            return SignificanceResult(
                test_name="Chi-square",
                statistic=chi2,
                p_value=p_value,
                is_significant=is_significant,
                significance_level=self.significance_level,
                degrees_of_freedom=dof,
                effect_size=cramers_v,
                interpretation=interpretation,
                warnings=warnings
            )
            
        except Exception as e:
            logger.error(f"Chi-square test failed: {e}")
            return SignificanceResult(
                test_name="Chi-square",
                statistic=0.0,
                p_value=1.0,
                is_significant=False,
                significance_level=self.significance_level,
                interpretation=f"Test failed: {str(e)}",
                warnings=[str(e)]
            )
    
    def compare_segments(
        self,
        segment_results: Dict[str, Dict],
        use_counts: bool = True
    ) -> SignificanceResult:
        """
        Compares segments for significant differences.
        
        Args:
            segment_results: dictionary {segment: {'result': AnalysisResult or dict, 'n': int}}
            use_counts: True for absolute numbers, False for percentages
        
        Returns:
            SignificanceResult
        """
        # extract the data
        observed = {}
        
        for seg_name, seg_data in segment_results.items():
            result = seg_data.get('result')
            if result is None:
                continue
            
            # objects as well as dicts
            if hasattr(result, 'data'):
                data = result.data
            elif isinstance(result, dict):
                data = result.get('data', {})
            else:
                continue
            
            if isinstance(data, dict):
                if use_counts:
                    observed[seg_name] = {k: int(v) for k, v in data.items()}
                else:
                    # convert percentages to counts (approximately)
                    n = seg_data.get('n', 100)
                    observed[seg_name] = {k: int(v * n / 100) for k, v in data.items()}
        
        if len(observed) < 2:
            return SignificanceResult(
                test_name="Chi-square",
                statistic=0.0,
                p_value=1.0,
                is_significant=False,
                significance_level=self.significance_level,
                interpretation="At least 2 segments are needed for a comparison",
                warnings=["Too few segments"]
            )
        
        return self.chi_square_test(observed)
    
    def pairwise_comparisons(
        self,
        segment_results: Dict[str, Dict]
    ) -> Dict[Tuple[str, str], SignificanceResult]:
        """
        Runs pairwise comparisons between all segments.
        
        Args:
            segment_results: dictionary {segment: {'result': ..., 'n': int}}
        
        Returns:
            dictionary {(segment1, segment2): SignificanceResult}
        """
        segments = list(segment_results.keys())
        results = {}
        
        for i, seg1 in enumerate(segments):
            for seg2 in segments[i+1:]:
                pair_data = {
                    seg1: segment_results[seg1],
                    seg2: segment_results[seg2]
                }
                result = self.compare_segments(pair_data)
                results[(seg1, seg2)] = result
        
        return results


# =============================================================================
# Numeric tests: Kruskal-Wallis for comparisons of central tendency
# =============================================================================

def kruskal_wallis_test(
    segment_values: Dict[str, List[float]],
    significance_level: float = 0.05,
    min_per_group: int = 5,
) -> SignificanceResult:
    """
    Runs a Kruskal-Wallis test of numeric data between >=2 segments.
    
    Kruskal-Wallis is the non-parametric alternative to the one-way ANOVA: it
    tests whether the samples come from the same distribution. Chosen because:
    - survey data is often not normally distributed (right-skewed distributions
      of counts, percentages etc.)
    - robust against outliers
    - works for any number of segments >= 2
    
    The effect size is estimated as epsilon squared (the eta squared equivalent
    for KW, interpretable like Cramér's V):
        epsilon^2 = (H - k + 1) / (n - k)
    
    Args:
        segment_values: {segment_name: [floats]}, the raw values per segment.
        significance_level: p value threshold.
        min_per_group: minimum n per segment for a sensible test.
    
    Returns:
        SignificanceResult with:
        - statistic: H statistic
        - p_value: p value (NaN if the test is not possible)
        - is_significant: p < alpha
        - degrees_of_freedom: k - 1
        - effect_size: epsilon squared
        - interpretation: the effect category
        - warnings: in edge cases
    """
    warnings: List[str] = []
    
    # filter: drop segments with too little data
    valid_segments = {
        name: [float(v) for v in values if not pd.isna(v)]
        for name, values in segment_values.items()
    }
    valid_segments = {
        name: values for name, values in valid_segments.items()
        if len(values) >= min_per_group
    }
    
    n_groups = len(valid_segments)
    
    if n_groups < 2:
        warnings.append(
            f"Kruskal-Wallis: only {n_groups} segment(s) with n >= {min_per_group}, "
            f"no test possible"
        )
        return SignificanceResult(
            test_name='kruskal_wallis',
            statistic=0.0,
            p_value=float('nan'),
            is_significant=False,
            significance_level=significance_level,
            degrees_of_freedom=0,
            effect_size=0.0,
            interpretation='not testable',
            warnings=warnings,
        )
    
    # are all values identical? (the test would return NaN)
    all_values = [v for vs in valid_segments.values() for v in vs]
    if len(set(all_values)) == 1:
        warnings.append("All values identical, no test possible")
        return SignificanceResult(
            test_name='kruskal_wallis',
            statistic=0.0,
            p_value=1.0,  # no difference, p=1
            is_significant=False,
            significance_level=significance_level,
            degrees_of_freedom=n_groups - 1,
            effect_size=0.0,
            interpretation='no variation',
            warnings=warnings,
        )
    
    # Test durchfuehren
    try:
        groups_list = list(valid_segments.values())
        h_stat, p_value = stats.kruskal(*groups_list)
    except Exception as e:
        warnings.append(f"Test failed: {e}")
        return SignificanceResult(
            test_name='kruskal_wallis',
            statistic=0.0,
            p_value=float('nan'),
            is_significant=False,
            significance_level=significance_level,
            warnings=warnings,
        )
    
    # Effektstaerke: epsilon^2
    n_total = len(all_values)
    if n_total > n_groups:
        epsilon_sq = max(0.0, (h_stat - n_groups + 1) / (n_total - n_groups))
    else:
        epsilon_sq = 0.0
    
    # interpretation analogous to Cohen's eta^2 conventions
    if epsilon_sq < 0.01:
        interpretation = 'negligible'
    elif epsilon_sq < 0.06:
        interpretation = 'weak'
    elif epsilon_sq < 0.14:
        interpretation = 'medium'
    else:
        interpretation = 'strong'
    
    return SignificanceResult(
        test_name='kruskal_wallis',
        statistic=float(h_stat),
        p_value=float(p_value),
        is_significant=p_value < significance_level,
        significance_level=significance_level,
        degrees_of_freedom=n_groups - 1,
        effect_size=float(epsilon_sq),
        interpretation=interpretation,
        warnings=warnings,
    )


def run_numeric_segment_significance(
    segment_values: Dict[str, List[float]],
    significance_level: float = 0.05,
    min_per_group: int = 5,
) -> Dict:
    """
    Wrapper for significance tests of numeric data between segments.
    
    Args:
        segment_values: {segment_name: [floats]}
        significance_level: p value threshold.
        min_per_group: minimum n per segment.
    
    Returns:
        dict with 'overall' and 'summary'. (Pairwise comparisons are not offered
        here; a Mann-Whitney U test per pair would be the next step.)
    """
    overall = kruskal_wallis_test(
        segment_values,
        significance_level=significance_level,
        min_per_group=min_per_group,
    )
    
    return {
        'overall': overall,
        'summary': {
            'any_significant': overall.is_significant,
            'significance_level': significance_level,
            'test': 'kruskal_wallis',
            'n_groups': len([
                name for name, values in segment_values.items()
                if len([v for v in values if not pd.isna(v)]) >= min_per_group
            ]),
        },
    }
