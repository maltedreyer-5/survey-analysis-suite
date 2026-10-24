# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the NumericHandler.

Verifies:
1. basic statistics of the NumericHandler (mean, median, std, min/max, q25/q75)
2. robust against decimal commas, empty values, strings, out-of-range values
3. correct histogram bins
4. IQR-based outlier detection
5. edge cases: empty data, all values equal, n=1, only NaN
6. default summary schema with 4 sections
7. build_summary_context sets has_outliers correctly
8. analyze_segmented returns a NumericResult per segment
9. charts: histogram and box_plot render without a crash
10. registry integration: get_handler('numeric', ...) works

Run: python tests/suite_numeric_handler.py
"""

import sys
import os
import types
import importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub('httpx',
      Client=lambda *a, **kw: None,
      HTTPStatusError=Exception,
      TimeoutException=Exception,
      RequestError=Exception)
_stub('langdetect', detect=lambda x: 'de')


# the real app environment is loaded; that also needs utils.translation
def _ensure_translation_stub():
    if 'utils.translation' in sys.modules:
        return
    tm_stub = types.ModuleType('utils.translation')
    class _TM:
        def t(self, key, lang='de', **kw): return key
        def get(self, key, lang='de'): return key
        def translate_common_label(self, label, lang='de'): return label
    tm_stub.get_translation_manager = lambda: _TM()
    sys.modules['utils.translation'] = tm_stub


# utils package anew, so its __init__ is skipped
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg
_ensure_translation_stub()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# load the modules in dependency order
HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')

_load('utils.summary_schema',  os.path.join(ROOT, 'utils', 'summary_schema.py'))
_load('utils.numeric_coerce',  os.path.join(ROOT, 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver', os.path.join(ROOT, 'utils', 'segment_resolver.py'))
_load('utils.segmentation',    os.path.join(ROOT, 'utils', 'segmentation.py'))
_load('utils.charts',          os.path.join(ROOT, 'utils', 'charts.py'))


import numpy as np
import pandas as pd

# load the handler class directly
import handlers.base as hb_mod
from handlers.base import AnalysisResult

_load('handlers.numeric', os.path.join(ROOT, 'handlers', 'numeric.py'))
from handlers.numeric import NumericHandler, NumericResult

from utils.charts import ChartGenerator
from utils.summary_schema import SummarySchema, Section


PASS = 0
FAIL = 0
RESULTS = []


def assert_eq(label, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected {expected!r}, got {actual!r}"))


def assert_close(label, actual, expected, tol=1e-6):
    global PASS, FAIL
    try:
        if abs(actual - expected) <= tol:
            PASS += 1
            RESULTS.append(('OK', label))
            return
    except (TypeError, ValueError):
        pass
    FAIL += 1
    RESULTS.append(('FAIL', f"{label}: expected {expected!r} (±{tol}), got {actual!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


# ==========================================================================
# 1. Basic statistics
# ==========================================================================

print("=" * 60)
print("1. NumericHandler — basic statistics")
print("=" * 60)

handler = NumericHandler(config={
    'id': 'q1',
    'columns': ['Anzahl'],
    'numeric': {'unit': 'Personen', 'decimals': 0, 'bin_count': 5},
})


# values: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
df1 = pd.DataFrame({'Anzahl': list(range(1, 11))})
res = handler.analyze(df1)

assert_eq("n", res.n, 10)
assert_close("mean", res.data['mean'], 6)  # round(5.5, 0) -> 6 (Python banker's rounding)
assert_close("median", res.data['median'], 6)  # round(5.5, 0) -> 6
assert_eq("min", res.data['min'], 1)
assert_eq("max", res.data['max'], 10)
assert_close("q25", res.data['q25'], 3)
assert_close("q75", res.data['q75'], 8)


# numeric stats detail object
ns = res.metadata['numeric_stats']
assert_true("metadata['numeric_stats'] is a NumericResult",
            isinstance(ns, NumericResult))
assert_close("ns.mean (Roh)", ns.mean, 5.5)
assert_close("ns.std (raw)", ns.std, 3.0276503541, tol=0.01)


# ==========================================================================
# 2. robust: German decimal comma, strings, empty values, prefixes
# ==========================================================================

print()
print("=" * 60)
print("2. Robustness: German decimal comma, strings, empty values")
print("=" * 60)

df_robust = pd.DataFrame({'Anzahl': [
    '3,14',          # decimal comma
    '42',            # int as a string
    '',              # empty
    None,            # None
    'ca. 100',       # Praefix
    '15%',           # Suffix
    '1.234,56',      # thousands + comma
    'abc',           # not parsable
    '50',
]})
res = handler.analyze(df_robust)

assert_eq("Robust: n_valid", res.n, 6)
# Since session 3 an empty cell or None is no answer, not an invalid value: only 'abc' counts
assert_eq("robust: n_invalid (only abc; empty and None are missing)", res.metadata['n_invalid'], 1)


# ==========================================================================
# 3. Out-of-Range filtering
# ==========================================================================

print()
print("=" * 60)
print("3. Out-of-Range-Filtering")
print("=" * 60)

handler_ranged = NumericHandler(config={
    'id': 'q2',
    'columns': ['Anzahl'],
    'numeric': {
        'unit': 'Personen',
        'decimals': 0,
        'bin_count': 5,
        'valid_range': {'min': 0, 'max': 100},
    },
})

df_oor = pd.DataFrame({'Anzahl': [10, 20, 50, 150, -5, 80]})  # 150 and -5 out of range
res = handler_ranged.analyze(df_oor)
assert_eq("OOR: n_valid (4 of 6)", res.n, 4)
assert_eq("OOR: n_invalid (2 OOR)", res.metadata['n_invalid'], 2)


# ==========================================================================
# 4. Histogram bins
# ==========================================================================

print()
print("=" * 60)
print("4. Histogram bins")
print("=" * 60)

# bin_count=5 -> 5 Bins
df_hist = pd.DataFrame({'Anzahl': [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]})
res = handler.analyze(df_hist)
ns = res.metadata['numeric_stats']

# with bin_count=5 -> 5 bins, i.e. 6 bin edges
assert_eq("histogram bins: 6 edges (with 5 bins)", len(ns.histogram_bins), 6)
assert_eq("histogram counts: 5 bins", len(ns.histogram_counts), 5)

# sum of the counts = n
assert_eq("histogram counts: sum = n", sum(ns.histogram_counts), len(df_hist))


# ==========================================================================
# 5. Outlier detection (IQR-based)
# ==========================================================================

print()
print("=" * 60)
print("5. IQR-based outliers")
print("=" * 60)

# data with a clear outlier
df_outlier = pd.DataFrame({
    'Anzahl': [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 100]  # 100 is an outlier
})
res = handler.analyze(df_outlier)
ns = res.metadata['numeric_stats']

assert_true("outliers detected",
            100 in ns.outliers or 100.0 in ns.outliers)
assert_true("has_outliers() True", ns.has_outliers())


# without outliers
df_clean = pd.DataFrame({'Anzahl': [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]})
res_clean = handler.analyze(df_clean)
ns_clean = res_clean.metadata['numeric_stats']
assert_true("no outliers with a homogeneous distribution",
            len(ns_clean.outliers) == 0)


# ==========================================================================
# 6. Edge-Cases
# ==========================================================================

print()
print("=" * 60)
print("6. Edge-Cases")
print("=" * 60)

# 6a. empty DataFrame
df_empty = pd.DataFrame({'Anzahl': []})
res = handler.analyze(df_empty)
assert_eq("empty: n=0", res.n, 0)

# 6b. only NaN
df_nan = pd.DataFrame({'Anzahl': [None, None, '', np.nan]})
res = handler.analyze(df_nan)
assert_eq("only NaN: n=0", res.n, 0)
assert_true("only NaN: warnings present", len(res.warnings) > 0)

# 6c. n=1
df_one = pd.DataFrame({'Anzahl': [42]})
res = handler.analyze(df_one)
assert_eq("n=1: n", res.n, 1)
assert_eq("n=1: mean=median=42", res.data['mean'], res.data['median'])
assert_eq("n=1: std=0", res.data['std'], 0)
ns = res.metadata['numeric_stats']
assert_eq("n=1: no outliers", len(ns.outliers), 0)

# 6d. all values equal
df_const = pd.DataFrame({'Anzahl': [50] * 10})
res = handler.analyze(df_const)
assert_eq("constant: mean=50", res.data['mean'], 50)
assert_eq("constant: std=0", res.data['std'], 0)
ns = res.metadata['numeric_stats']
assert_eq("constant: 1 bin", len(ns.histogram_counts), 1)
assert_eq("constant: all values in the bin", ns.histogram_counts[0], 10)

# 6e. column missing
df_no_col = pd.DataFrame({'Andere': [1, 2, 3]})
res = handler.analyze(df_no_col)
assert_eq("column missing: n=0", res.n, 0)
assert_true("column missing: warning",
            any('not in the data' in w for w in res.warnings))

# 6f. column not configured at all
handler_no_col = NumericHandler(config={'id': 'q', 'columns': []})
res = handler_no_col.analyze(pd.DataFrame({'Anzahl': [1, 2, 3]}))
assert_eq("no column configured: n=0", res.n, 0)


# ==========================================================================
# 7. Default-Summary-Schema
# ==========================================================================

print()
print("=" * 60)
print("7. Default-Summary-Schema")
print("=" * 60)

schema = NumericHandler.default_summary_schema()
assert_eq("Schema-ID", schema.schema_id, 'numeric_default')
assert_eq("Handler-Type", schema.handler_type, 'numeric')
assert_eq("4 sections", len(schema.sections),
          4)  # central_tendency, distribution, outliers, segments_text

section_ids = [s.id for s in schema.sections]
assert_eq("section ids in order",
          section_ids,
          ['central_tendency', 'distribution', 'outliers', 'segments_text'])

# Conditional-Flags
outliers_section = schema.get_section('outliers')
assert_eq("outliers conditional", outliers_section.conditional, 'has_outliers')
assert_eq("outliers required=False", outliers_section.required, False)

segments_section = schema.get_section('segments_text')
assert_eq("segments_text conditional",
          segments_section.conditional, 'has_segmentation_significant')


# ==========================================================================
# 8. build_summary_context: has_outliers-Flag
# ==========================================================================

print()
print("=" * 60)
print("8. build_summary_context()")
print("=" * 60)

# with an outlier
df_with_outlier = pd.DataFrame({'Anzahl': [10, 11, 12, 13, 14, 15, 100]})
res = handler.analyze(df_with_outlier)
ctx = handler.build_summary_context(res, segment_dimension_count=2,
                                     has_significant_segments=True)
assert_eq("ctx.has_outliers True", ctx['has_outliers'], True)
assert_true("ctx.n_outliers > 0", ctx['n_outliers'] > 0)
assert_eq("ctx.has_segmentation True", ctx['has_segmentation'], True)
assert_eq("ctx.has_segmentation_significant", ctx['has_segmentation_significant'], True)

# without an outlier
df_clean = pd.DataFrame({'Anzahl': [10, 11, 12, 13, 14]})
res = handler.analyze(df_clean)
ctx = handler.build_summary_context(res)
assert_eq("ctx.has_outliers False", ctx['has_outliers'], False)
assert_eq("ctx.n_outliers 0", ctx['n_outliers'], 0)


# ==========================================================================
# 9. analyze_segmented
# ==========================================================================

print()
print("=" * 60)
print("9. analyze_segmented")
print("=" * 60)

df_seg = pd.DataFrame({
    'Anzahl': [10, 12, 14, 50, 55, 60, 100, 120, 130],
    '__segment_size__': ['klein'] * 3 + ['mittel'] * 3 + ['groß'] * 3,
})

seg_results = handler.analyze_segmented(df_seg, '__segment_size__')
assert_eq("3 segment values", len(seg_results), 3)
assert_true("'klein' as a segment value", 'klein' in seg_results)
assert_close("klein: mean", seg_results['klein'].mean, 12, tol=0.01)
assert_close("groß: mean", seg_results['groß'].mean, 116.66, tol=0.5)


# edge case: NA in the segment column is ignored
df_seg_nan = pd.DataFrame({
    'Anzahl': [10, 20, 30, 40],
    '__segment_x__': ['A', 'A', None, 'B'],
})
seg_results = handler.analyze_segmented(df_seg_nan, '__segment_x__')
assert_eq("NA segments ignored", set(seg_results.keys()), {'A', 'B'})


# ==========================================================================
# 10. Charts: histogram and box plot render
# ==========================================================================

print()
print("=" * 60)
print("10. Charts: histogram + box plot")
print("=" * 60)

# tests that the methods run without an exception, not the exact image.
import matplotlib
matplotlib.use('Agg')  # non-interactive Backend
import matplotlib.pyplot as plt

cg = ChartGenerator()

# histogram
fig = cg.histogram(
    bins=[0, 10, 20, 30, 40, 50],
    counts=[5, 12, 8, 3, 2],
    title="Test-Histogramm",
    xlabel="Personen",
    decimals=0,
)
assert_true("histogram figure created", fig is not None)
plt.close(fig)


# Box-Plot
stats = {'min': 5, 'q25': 15, 'median': 25, 'q75': 40, 'max': 80, 'mean': 28}
fig = cg.box_plot(
    stats=stats,
    title="Test-Boxplot",
    ylabel="Personen",
    outliers=[110, 120],
)
assert_true("box plot figure created", fig is not None)
plt.close(fig)


# box plot without outliers
fig = cg.box_plot(
    stats=stats,
    title="Test ohne Outliers",
    ylabel="Personen",
)
assert_true("box plot without outliers", fig is not None)
plt.close(fig)


# empty histogram -> None
fig = cg.histogram(bins=[], counts=[], title="Leer")
assert_true("empty histogram -> None", fig is None)


# box plot without complete statistics -> None
fig = cg.box_plot(stats={'mean': 5}, title="Unvollstaendig")
assert_true("Box-Plot unvollstaendig -> None", fig is None)


# ==========================================================================
# 11. Handler-Pipeline: create_chart()
# ==========================================================================

print()
print("=" * 60)
print("11. NumericHandler.create_chart()")
print("=" * 60)

df = pd.DataFrame({'Anzahl': list(range(1, 21))})  # 1..20
res = handler.analyze(df)

fig = handler.create_chart(res, chart_type='histogram')
assert_true("create_chart histogram", fig is not None)
plt.close(fig)

fig = handler.create_chart(res, chart_type='box_plot')
assert_true("create_chart box_plot", fig is not None)
plt.close(fig)

# unsupported type -> falls back to the default
fig = handler.create_chart(res, chart_type='unsupported')
assert_true("create_chart fallback for an unknown type", fig is not None)
plt.close(fig)

# empty result -> None
res_empty = handler.analyze(pd.DataFrame({'Anzahl': []}))
fig = handler.create_chart(res_empty, chart_type='histogram')
assert_true("create_chart with n=0 -> None", fig is None)


# ==========================================================================
# 12. Registry-Integration
# ==========================================================================

print()
print("=" * 60)
print("12. Handler-Registry")
print("=" * 60)

# re-import handlers, so __init__.py with NumericHandler applies
if 'handlers' in sys.modules:
    del sys.modules['handlers']
_load('handlers',
      os.path.join(ROOT, 'handlers', '__init__.py'))

from handlers import HANDLER_REGISTRY, get_handler, get_available_handlers, NumericHandler as RegHandler

assert_true("'numeric' in HANDLER_REGISTRY",
            'numeric' in HANDLER_REGISTRY)
assert_true("HANDLER_REGISTRY['numeric'] === NumericHandler",
            HANDLER_REGISTRY['numeric'] is RegHandler)
assert_true("'numeric' in get_available_handlers()",
            'numeric' in get_available_handlers())

# get_handler returns an instance
inst = get_handler('numeric', 'q1', {
    'columns': ['Anzahl'],
    'numeric': {'unit': 'Personen'},
})
assert_true("get_handler('numeric', ...) -> instance",
            inst is not None and isinstance(inst, RegHandler))


# ==========================================================================
# 13. Roundtrip: Pipeline analyze -> Schema -> applicable_sections
# ==========================================================================

print()
print("=" * 60)
print("13. Pipeline: Handler -> Schema -> applicable_sections")
print("=" * 60)

# with an outlier
df_full = pd.DataFrame({'Anzahl': [5, 6, 7, 8, 9, 10, 50]})  # 50 is an outlier
res = handler.analyze(df_full)
ctx = handler.build_summary_context(res, segment_dimension_count=2,
                                     has_significant_segments=True)
schema = NumericHandler.default_summary_schema()

applicable = schema.applicable_sections(ctx)
applicable_ids = [s.id for s in applicable]

assert_eq("pipeline: all 4 sections applicable (with outliers + sig)",
          applicable_ids,
          ['central_tendency', 'distribution', 'outliers', 'segments_text'])


# without outliers, without significant segments
df_clean = pd.DataFrame({'Anzahl': [10, 11, 12, 13, 14]})
res = handler.analyze(df_clean)
ctx = handler.build_summary_context(res)
applicable = schema.applicable_sections(ctx)
applicable_ids = [s.id for s in applicable]

assert_eq("pipeline: only 2 sections applicable (no outlier, no sig)",
          applicable_ids,
          ['central_tendency', 'distribution'])


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
