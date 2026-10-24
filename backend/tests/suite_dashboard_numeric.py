# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the numeric support of the web dashboard.

IMPORTANT: this suite uses REAL NumericResult-like dataclass instances, not
dict stubs, so it catches bugs that dict stubs would hide (e.g. an
AttributeError in dict-only code, or a wrong field mapping min_value/max_value
vs min/max).

Verifies:
1. _format_result_for_dashboard with a REAL NumericResult (dataclass):
   - statistics, unit, decimals, histogram_bins correct
   - values as a bin label -> count dict (NOT as a statistics summary!)
   - percentages as a bin label -> percentage dict
2. _format_result_for_dashboard with dict numeric_stats (cached result)
3. robustness: no crash without numeric_stats
4. consistency: values/histogram_bins/percentages have the same keys
   and the counts add up to n
5. the JS files contain the expected functions + switch cases
6. the specification documents the numeric format
"""

import sys
import os
import re
import logging
import types
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _stub(name, **attrs):
    if name in sys.modules: return
    m = types.ModuleType(name)
    for k, v in attrs.items(): setattr(m, k, v)
    sys.modules[name] = m


_stub('httpx', Client=lambda *a, **kw: None,
      HTTPStatusError=Exception, TimeoutException=Exception,
      RequestError=Exception)
_stub('langdetect', detect=lambda x: 'de')

ROOT = os.path.join(os.path.dirname(__file__), '..')

PASS = 0
FAIL = 0
RESULTS = []


def assert_eq(label, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: erw {expected!r}, got {actual!r}"))


def assert_close(label, actual, expected, tol=0.05):
    global PASS, FAIL
    try:
        if abs(float(actual) - float(expected)) <= tol:
            PASS += 1; RESULTS.append(('OK', label))
        else:
            FAIL += 1; RESULTS.append(('FAIL', f"{label}: erw {expected}, got {actual}"))
    except Exception as e:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: {e}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: {item!r} fehlt"))


def assert_not_in(label, item, container):
    global PASS, FAIL
    if item not in container:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: {item!r} should not be present"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: war {value!r}"))


@dataclass
class FakeNumericResult:
    """Replica of the NumericResult dataclass of handlers/numeric.py."""
    question_id: str
    n: int
    n_invalid: int
    mean: float
    median: float
    std: float
    min_value: float
    max_value: float
    q25: float
    q75: float
    histogram_bins: List[float] = field(default_factory=list)
    histogram_counts: List[int] = field(default_factory=list)
    outliers: List[float] = field(default_factory=list)
    decimals: int = 1
    unit: str = ""


# load the format function in isolation
with open(os.path.join(ROOT, 'export', 'result_format.py'), encoding='utf-8') as f:
    src = f.read()
m = re.search(r'^def _format_result_for_dashboard\(.*?(?=\n^def |\Z)',
              src, flags=re.M | re.S)
assert m
fn_src = m.group(0)
mini_globals = {
    '__builtins__': __builtins__,
    'logger': logging.getLogger('test'),
    'Dict': Dict, 'Any': Any, 'Optional': Optional, 'List': List,
}
exec(fn_src, mini_globals)
_format = mini_globals['_format_result_for_dashboard']


# ==========================================================================
# 1. REAL NumericResult object
# ==========================================================================

print("=" * 60)
print("1. with a NumericResult dataclass")
print("=" * 60)

nr = FakeNumericResult(
    question_id='ihk_count', n=8, n_invalid=2,
    mean=40.875, median=33.5, std=33.5,
    min_value=5.0, max_value=100.0, q25=14.25, q75=56.25,
    histogram_bins=[5.0, 24.0, 43.0, 62.0, 81.0, 100.0],
    histogram_counts=[3, 2, 1, 1, 1],
    outliers=[], decimals=0, unit='Mitarbeiter',
)

result_dict = {
    'n': 8,
    'data': {  # to_summary_dict style: must NOT come out as values
        'n': 8, 'mean': 40.875, 'median': 33.5,
        'std': 33.5, 'min': 5, 'max': 100, 'q25': 14.25, 'q75': 56.25
    },
    'percentages': None,
    'metadata': {'numeric_stats': nr, 'n_invalid': 2},
}

formatted = _format(result_dict, 'numeric')

assert_eq("n", formatted['n'], 8)
assert_in("statistics", 'statistics', formatted)
assert_in("unit", 'unit', formatted)
assert_in("decimals", 'decimals', formatted)
assert_in("values", 'values', formatted)
assert_in("percentages", 'percentages', formatted)
assert_in("histogram_bins", 'histogram_bins', formatted)

# main check: values is the HISTOGRAM, not the statistics
values_keys = list(formatted['values'].keys())
print(f"  values keys: {values_keys}")
assert_not_in("'mean' not in values", 'mean', values_keys)
assert_not_in("'median' not in values", 'median', values_keys)
assert_not_in("'std' not in values", 'std', values_keys)
assert_eq("5 Bins", len(formatted['values']), 5)
assert_eq("Sum(values) == n", sum(formatted['values'].values()), 8)
assert_true("values are ints",
            all(isinstance(v, int) for v in formatted['values'].values()))

# histogram_bins
hb = formatted['histogram_bins']
assert_eq("5 histogram_bins", len(hb), 5)
assert_eq("hb[0].label present", 'label' in hb[0], True)
assert_eq("hb[0].count = 3", hb[0]['count'], 3)
assert_eq("hb[0].min = 5.0", hb[0]['min'], 5.0)
assert_eq("hb[0].max = 24.0", hb[0]['max'], 24.0)
assert_eq("Sum(hb.count) == n", sum(b['count'] for b in hb), 8)

# consistency of values/histogram_bins
hb_labels = [b['label'] for b in hb]
assert_eq("values keys == histogram_bins labels", values_keys, hb_labels)

# percentages
pct_sum = sum(formatted['percentages'].values())
assert_close("Sum(percentages) ~= 100", pct_sum, 100.0, tol=0.5)

# statistics
st = formatted['statistics']
assert_eq("stats.n", st['n'], 8)
assert_eq("stats.n_invalid", st['n_invalid'], 2)
assert_close("stats.mean", st['mean'], 40.875)
assert_close("stats.median", st['median'], 33.5)
assert_eq("stats.min (from min_value)", st['min'], 5.0)
assert_eq("stats.max (from max_value)", st['max'], 100.0)
assert_close("stats.q25", st['q25'], 14.25)
assert_close("stats.q75", st['q75'], 56.25)

assert_eq("unit", formatted['unit'], 'Mitarbeiter')
assert_eq("decimals", formatted['decimals'], 0)


# ==========================================================================
# 2. Mit Dict-numeric_stats (cached path)
# ==========================================================================

print()
print("=" * 60)
print("2. with dict numeric_stats")
print("=" * 60)

ns_dict = {
    'n': 8, 'n_invalid': 2,
    'mean': 40.875, 'median': 33.5, 'std': 33.5,
    'min': 5.0, 'max': 100.0,
    'q25': 14.25, 'q75': 56.25,
    'histogram_bins': [5.0, 24.0, 43.0, 62.0, 81.0, 100.0],
    'histogram_counts': [3, 2, 1, 1, 1],
    'outliers': [], 'decimals': 0, 'unit': 'Mitarbeiter',
}

result_d = {
    'n': 8, 'data': {}, 'percentages': None,
    'metadata': {'numeric_stats': ns_dict, 'n_invalid': 2},
}
fmt_d = _format(result_d, 'numeric')

assert_eq("Dict: n", fmt_d['n'], 8)
assert_eq("Dict: 5 bins", len(fmt_d['histogram_bins']), 5)
assert_eq("Dict: Sum(values) = n", sum(fmt_d['values'].values()), 8)
assert_eq("Dict: stats.mean", fmt_d['statistics']['mean'], 40.875)
assert_eq("Dict: unit", fmt_d['unit'], 'Mitarbeiter')


# ==========================================================================
# 3. Robustness: numeric_stats missing
# ==========================================================================

print()
print("=" * 60)
print("3. Robustness: numeric_stats missing")
print("=" * 60)

result_ns = {
    'n': 100, 'data': {'foo': 60}, 'percentages': None,
    'metadata': {'unit': 'Personen', 'decimals': 0},
}
fmt_ns = _format(result_ns, 'numeric')

assert_eq("without stats: n", fmt_ns['n'], 100)
assert_eq("without stats: values empty", fmt_ns['values'], {})
assert_eq("without stats: percentages empty", fmt_ns['percentages'], {})
assert_not_in("without stats: no statistics", 'statistics', fmt_ns)
assert_eq("without stats: unit from the metadata", fmt_ns['unit'], 'Personen')
assert_eq("without stats: decimals from the metadata", fmt_ns['decimals'], 0)


# ==========================================================================
# 4. Regression
# ==========================================================================

print()
print("=" * 60)
print("4. Regression")
print("=" * 60)

f_sc = _format({
    'n': 100, 'data': {'Ja': 70, 'Nein': 30},
    'percentages': {'Ja': 70.0, 'Nein': 30.0}, 'metadata': {},
}, 'single_choice')
assert_eq("sc: n", f_sc['n'], 100)
assert_eq("sc: values", f_sc['values'], {'Ja': 70, 'Nein': 30})
assert_not_in("sc: no statistics", 'statistics', f_sc)
assert_not_in("sc: no unit", 'unit', f_sc)

f_ft = _format({
    'n': 50, 'data': {'A': 20, 'B': 30},
    'percentages': {'A': 40.0, 'B': 60.0},
    'metadata': {'total_items': 75, 'unique_responses': 50},
}, 'freetext')
assert_in("ft: cluster_counts", 'cluster_counts', f_ft)
assert_not_in("ft: no statistics", 'statistics', f_ft)


# ==========================================================================
# 5. JS files
# ==========================================================================

print()
print("=" * 60)
print("5. JS files")
print("=" * 60)

with open(os.path.join(ROOT, '..', 'dashboard', 'js', 'charts.js'),
          encoding='utf-8') as f:
    charts_js = f.read()
assert_in("renderNumericChart def", "function renderNumericChart", charts_js)
assert_in("DE 'Mittelwert'", "Mittelwert", charts_js)
assert_in("FR 'Médiane'", "Médiane", charts_js)
assert_in("EN 'Mean'", "'Mean'", charts_js)

with open(os.path.join(ROOT, '..', 'dashboard', 'js', 'app.js'),
          encoding='utf-8') as f:
    app_js = f.read()
assert_in("switch case 'numeric'", "case 'numeric':", app_js)
assert_in("renderNumericChart call", "renderNumericChart(container", app_js)


# ==========================================================================
# 6. Spec
# ==========================================================================

print()
print("=" * 60)
print("6. Spec")
print("=" * 60)

with open(os.path.join(ROOT, '..', 'docs', 'dashboard-data.md'), encoding='utf-8') as f:
    spec = f.read()
assert_in("numeric section", "#### numeric", spec)
assert_in("question types with numeric", "| `numeric` |", spec)
assert_in("statistics", "statistics", spec)
assert_in("histogram_bins", "histogram_bins", spec)
assert_in("outliers", "outliers", spec)
assert_in("IQR", "IQR", spec)


# ==========================================================================
# Summary
# ==========================================================================

print()
print("=" * 60)
print(f"TOTAL: {PASS} OK, {FAIL} FAIL")
print("=" * 60)
if FAIL > 0:
    print()
    for status, msg in RESULTS:
        if status == 'FAIL':
            print(f"  [{status}] {msg}")
    sys.exit(1)
print("All tests passed.")
