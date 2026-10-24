# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of utils/segmentation.py and utils/segmented_analysis.py.

Verifies:
1. the Segmenter with any dimensions
2. SEGMENT_TYPES and SEGMENT_LABELS of the SegmentedAnalyzer are dynamic properties
3. there is no country-specific wrapper any more
4. _analyze_segment_specific_clusters iterates over all dimensions with
   enable_specific_clusters=True
5. the SEGMENT_LABELS output has the expected format
6. bucketing (numeric_buckets, value_mapping) works in the pipeline

Run: python tests/suite_segmentation.py
"""

import sys
import os
import types
import importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# heavy stubs before anything else
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

# utils package anew, so its __init__ does not run
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg

# charts stub
charts_stub = types.ModuleType('utils.charts')
charts_stub.ChartGenerator = type('CG', (), {
    'DEFAULT_COLORS': {'positive': '#5cb85c', 'negative': '#d9534f', 'neutral': '#999'}
})
sys.modules['utils.charts'] = charts_stub


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# load the modules in order (manually, as utils/__init__ is bypassed)
_load('utils.summary_schema',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'summary_schema.py'))
_load('utils.numeric_coerce',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'segment_resolver.py'))
_load('utils.segmentation',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'segmentation.py'))

import pandas as pd

from utils.segmentation import (
    Segmenter, SegmentInfo, SegmentSelector,
    SegmentationManager, create_segmenter,
)
from utils.segment_resolver import SegmentResolver


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


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


def assert_raises(label, exc_type, callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except exc_type:
        assert_true(label, True)
        return
    except Exception as e:
        assert_true(f"{label}: unexpected {type(e).__name__}: {e}", False)
        return
    assert_true(f"{label}: expected {exc_type.__name__}, no error", False)


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} not in {container!r}"))


# ==========================================================================
# Setup: Test-Configurations
# ==========================================================================

# classic configuration (three classic dimensions)
CLASSIC_CONFIG = {
    'settings': {
        'survey_metadata': {'source_language': 'de', 'languages': ['de', 'fr', 'en']},
        'segmentation': {
            'dimensions': [
                {
                    'id': 'country',
                    'label': {'de': 'Länder', 'fr': 'Pays', 'en': 'Countries'},
                    'source_question': 'qcountry',
                    'bucketing': {'type': 'identity'},
                    'enable_specific_clusters': True,
                },
                {
                    'id': 'institution_type',
                    'label': {'de': 'Hochschultyp', 'fr': 'Type d\'institution', 'en': 'Institution Type'},
                    'source_question': 'qtype',
                    'bucketing': {'type': 'identity'},
                },
                {
                    'id': 'size',
                    'label': {'de': 'Hochschulgröße', 'fr': 'Taille', 'en': 'Size'},
                    'source_question': 'qsize',
                    'bucketing': {'type': 'identity'},
                },
            ],
        },
    },
    'question_groups': {
        'qcountry': {'columns': ['Land?']},
        'qtype':    {'columns': ['Hochschultyp?']},
        'qsize':    {'columns': ['Studierende?']},
    },
}


# a versatile survey with any number of dimensions + bucketing
COURSE_CONFIG = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {
                    'id': 'country',
                    'label': {'de': 'Land'},
                    'source_question': 'qcountry',
                    'bucketing': {'type': 'identity'},
                },
                {
                    'id': 'bundesland',
                    'label': {'de': 'Bundesland'},
                    'source_question': 'qbl',
                    'bucketing': {'type': 'identity'},
                },
                {
                    'id': 'org_size',
                    'label': {'de': 'Größenklasse'},
                    'source_question': 'qcount',
                    'bucketing': {
                        'type': 'numeric_buckets',
                        'buckets': [
                            {'label': {'de': 'klein'}, 'max': 10},
                            {'label': {'de': 'mittel'}, 'min': 10, 'max': 50},
                            {'label': {'de': 'groß'}, 'min': 50},
                        ],
                        'unmapped_strategy': {'type': 'drop'},
                    },
                },
            ],
        },
    },
    'question_groups': {
        'qcountry': {'columns': ['Land?']},
        'qbl':      {'columns': ['Bundesland?']},
        'qcount':   {'columns': ['Anzahl?']},
    },
}


# demo DataFrame for the classic configuration
df_classic = pd.DataFrame({
    'Land?':         ['Deutschland', 'Österreich', 'Schweiz',
                       'Deutschland', 'Deutschland', 'Schweiz',
                       'Deutschland', 'Österreich', 'Deutschland', 'Schweiz'],
    'Hochschultyp?': ['Universität', 'FH', 'Universität',
                       'FH', 'FH', 'Universität',
                       'Universität', 'FH', 'Pädagogische HS', 'Universität'],
    'Studierende?':  ['groß', 'klein', 'mittel',
                       'mittel', 'klein', 'groß',
                       'mittel', 'klein', 'klein', 'mittel'],
})

# demo DataFrame for the example survey
df_course = pd.DataFrame({
    'Land?':       ['Deutschland'] * 8 + ['Österreich'] * 2,
    'Bundesland?': ['Bayern', 'NRW', 'Berlin', 'Bayern',
                    'NRW', 'Sachsen', 'Bayern', 'Berlin', '', ''],
    'Anzahl?':     ['5', '15', '60', '8', '20', '3', '100', '12', '25', '40'],
})


# ==========================================================================
# 1. Segmenter with configurable dimensions
# ==========================================================================

print("=" * 60)
print("Segmenter — classic configuration")
print("=" * 60)

seg = Segmenter(CLASSIC_CONFIG)
assert_eq("3 segment types", seg.list_segment_types(),
          ['country', 'institution_type', 'size'])
assert_in("country in the types", 'country', seg.list_segment_types())

# get_segment_column returns __segment_<id>__
col = seg.get_segment_column('country')
assert_eq("Spaltenname country", col, '__segment_country__')

# test the materialisation explicitly
df_mat = seg.materialize(df_classic)
assert_in("__segment_country__ in df_mat", '__segment_country__', df_mat.columns)
assert_eq("values materialised correctly",
          list(df_mat['__segment_country__']),
          list(df_classic['Land?']))

# get_available_values
values = seg.get_available_values(df_classic, 'country')
assert_eq("country values (default order by frequency)",
          set(values), {'Deutschland', 'Österreich', 'Schweiz'})

# filter_data
filtered = seg.filter_data(df_classic, 'country', 'Deutschland')
assert_eq("Filter: 5 DE", len(filtered), 5)

# unknown dimension -> no crash, an empty result
assert_eq("unknown dimension: empty values",
          seg.get_available_values(df_classic, 'unknown'), [])
assert_eq("unknown dimension: no filter",
          len(seg.filter_data(df_classic, 'unknown', 'x')), len(df_classic))


# ==========================================================================
# 2. Example survey with bucketing (numeric_buckets): a completely new dimension
# ==========================================================================

print()
print("=" * 60)
print("Segmenter — example survey with numeric_buckets")
print("=" * 60)

seg_course = Segmenter(COURSE_CONFIG)
assert_eq("3 dimensions (country, bundesland, org_size)",
          seg_course.list_segment_types(),
          ['country', 'bundesland', 'org_size'])

df_mat_course = seg_course.materialize(df_course)

# numeric_buckets: the values should be "klein"/"mittel"/"groß"
size_values = list(df_mat_course['__segment_org_size__'].dropna())
assert_eq("bucketing: count 5 -> klein", df_mat_course['__segment_org_size__'].iloc[0], 'klein')
assert_eq("bucketing: count 15 -> mittel", df_mat_course['__segment_org_size__'].iloc[1], 'mittel')
assert_eq("bucketing: count 60 -> groß", df_mat_course['__segment_org_size__'].iloc[2], 'groß')

# unmapped_strategy: empty values become NA with the identity bucketing of Bundesland
bl_na_count = df_mat_course['__segment_bundesland__'].isna().sum()
assert_eq("Bundesland: 2 NAs (empty strings)", bl_na_count, 2)

# the filter works with bucket values too
filtered = seg_course.filter_data(df_course, 'org_size', 'klein')
assert_eq("filter org_size=klein: 3 rows", len(filtered), 3)


# ==========================================================================
# 3. SegmentInfo with warning thresholds
# ==========================================================================

print()
print("=" * 60)
print("SegmentInfo — warnings with a small n")
print("=" * 60)

info = seg.get_segment_info(df_classic, 'country', 'Österreich')
assert_eq("AT: n=2", info.n, 2)
assert_true("AT: warning set", info.warning is not None)
assert_eq("AT: show_results=False (n < 5)", info.show_results, False)

info_de = seg.get_segment_info(df_classic, 'country', 'Deutschland')
assert_eq("DE: n=5", info_de.n, 5)
assert_true("DE: warning n<10", info_de.warning is not None and info_de.warning.startswith('n < '))
assert_eq("DE: show_results=True", info_de.show_results, True)


# ==========================================================================
# 4. SegmentSelector
# ==========================================================================

print()
print("=" * 60)
print("SegmentSelector")
print("=" * 60)

selector = SegmentSelector(seg)
opts = selector.get_filter_options(df_classic, include_all=True)
assert_in("country in opts", 'country', opts)
assert_in("All as the first option", 'All', opts['country'])

formatted = selector.format_option_with_n(df_classic, 'country', 'Deutschland')
assert_eq("format with n", formatted, 'Deutschland (n=5)')


# ==========================================================================
# 5. SegmentationManager (Label-API)
# ==========================================================================

print()
print("=" * 60)
print("SegmentationManager — Label-Lookup")
print("=" * 60)

mgr = SegmentationManager(CLASSIC_CONFIG)
# apply with a German label
info_via_label = mgr.apply_segment(df_classic, 'Länder', 'Deutschland')
assert_eq("apply_segment via label DE", info_via_label.n, 5)
assert_eq("filter_type mapped from the label", info_via_label.filter_type, 'country')

# apply with another label
info_via_label_en = mgr.apply_segment(df_classic, 'Countries', 'Deutschland')
assert_eq("apply_segment via label EN", info_via_label_en.n, 5)


# ==========================================================================
# 6. Only the complete configuration; configuration errors are reported
# ==========================================================================

print()
print("=" * 60)
print("Segmenter(settings_dict) is rejected instead of silently having no segments")
print("=" * 60)

assert_raises("Segmenter(settings) without question_groups", ValueError,
              Segmenter, {'segmentation': CLASSIC_CONFIG['settings']['segmentation']})


# ==========================================================================
# 7. Empty configuration -> no segmentation, no crash
# ==========================================================================

print()
print("=" * 60)
print("edge: survey without a segmentation block")
print("=" * 60)

empty_config = {'settings': {}, 'question_groups': {}}
seg_empty = Segmenter(empty_config)
assert_eq("empty configuration: no dimension ids", seg_empty.list_segment_types(), [])
assert_eq("empty configuration: get_available_values empty",
          seg_empty.get_available_values(df_classic, 'country'), [])

# get_segment_info with None/None works (segment_type='all')
info_all = seg_empty.get_segment_info(df_classic)
assert_eq("get_segment_info without a filter: filter_type=all",
          info_all.filter_type, 'all')
assert_eq("get_segment_info without a filter: n=10", info_all.n, 10)


# ==========================================================================
# 8. SegmentedAnalyzer: properties are dynamic, not fixed
# ==========================================================================

print()
print("=" * 60)
print("SegmentedAnalyzer — dynamic SEGMENT_TYPES/SEGMENT_LABELS")
print("=" * 60)

# SegmentedAnalyzer imports a handler loader (handlers.get_handler); it is
# stubbed, not because the loader is used, but so the import of
# utils.segmented_analysis does not fail.
handlers_stub = types.ModuleType('handlers')
def _fake_get_handler(*a, **kw):
    return None
handlers_stub.get_handler = _fake_get_handler
sys.modules['handlers'] = handlers_stub

handlers_base_stub = types.ModuleType('handlers.base')
class _AR:
    pass
handlers_base_stub.AnalysisResult = _AR
sys.modules['handlers.base'] = handlers_base_stub

# now load segmented_analysis
sa = _load('utils.segmented_analysis',
           os.path.join(os.path.dirname(__file__), '..', 'utils', 'segmented_analysis.py'))

from utils.segmented_analysis import SegmentedAnalyzer

# classic configuration: 3 dimensions
analyzer = SegmentedAnalyzer(CLASSIC_CONFIG, df_classic)
assert_eq("SEGMENT_TYPES dynamic (classic: 3 dimensions)",
          analyzer.SEGMENT_TYPES,
          ['country', 'institution_type', 'size'])

# SEGMENT_LABELS: format "Nach <label>"
labels = analyzer.SEGMENT_LABELS
assert_in("country in SEGMENT_LABELS", 'country', labels)
assert_eq("Country-Label DE", labels['country']['de'], 'Nach Länder')
assert_eq("Country-Label FR", labels['country']['fr'], 'Par Pays')
assert_eq("Country-Label EN", labels['country']['en'], 'By Countries')
assert_eq("InstitutionType-Label DE",
          labels['institution_type']['de'], 'Nach Hochschultyp')

# example survey: 3 other dimensions
analyzer_course = SegmentedAnalyzer(COURSE_CONFIG, df_course)
assert_eq("SEGMENT_TYPES (Beispielumfrage)",
          analyzer_course.SEGMENT_TYPES,
          ['country', 'bundesland', 'org_size'])
assert_eq("Bundesland label DE",
          analyzer_course.SEGMENT_LABELS['bundesland']['de'],
          'Nach Bundesland')

# empty -> empty properties
analyzer_empty = SegmentedAnalyzer(empty_config, df_classic)
assert_eq("SEGMENT_TYPES leer", analyzer_empty.SEGMENT_TYPES, [])
assert_eq("SEGMENT_LABELS empty", analyzer_empty.SEGMENT_LABELS, {})


# ==========================================================================
# 9. _segment_label() fallback
# ==========================================================================

print()
print("=" * 60)
print("_segment_label() — fallback for an unknown id")
print("=" * 60)

# known id, all languages
assert_eq("_segment_label country DE",
          analyzer._segment_label('country', 'de'), 'Nach Länder')
assert_eq("_segment_label country FR",
          analyzer._segment_label('country', 'fr'), 'Par Pays')

# unknown id -> the id itself
assert_eq("_segment_label unknown",
          analyzer._segment_label('does_not_exist', 'de'),
          'does_not_exist')


# ==========================================================================
# 10. No compatibility wrappers for country-specific clusters
# ==========================================================================

class _StubHandler:
    def analyze(self, df, **kwargs):
        # simulate "no cluster generated"
        from types import SimpleNamespace
        return SimpleNamespace(n=0, metadata={})

assert_true("_analyze_segment_specific_clusters exists",
            hasattr(analyzer, '_analyze_segment_specific_clusters'))
assert_true("country-specific cluster wrapper removed",
            not hasattr(analyzer, '_analyze_segment_value_clusters'))


# ==========================================================================
# 11. enable_specific_clusters: only enabled dimensions are processed
# ==========================================================================

print()
print("=" * 60)
print("enable_specific_clusters Iteration")
print("=" * 60)

# in CLASSIC_CONFIG only country is enabled, institution_type and size are not
active_dims = [
    d for d in analyzer.segmenter._resolver.list_dimensions()
    if d.enable_specific_clusters
]
assert_eq("only 1 dimension enabled (classic)", len(active_dims), 1)
assert_eq("enabled dimension: country", active_dims[0].id, 'country')

# in COURSE_CONFIG: no dimension enabled
active_dims_course = [
    d for d in analyzer_course.segmenter._resolver.list_dimensions()
    if d.enable_specific_clusters
]
assert_eq("none enabled (example survey)", len(active_dims_course), 0)


# test of the iteration: _analyze_segment_specific_clusters runs and returns
# an empty result with n<5 or without enabled dimensions
results_course = analyzer_course._analyze_segment_specific_clusters(
    'q1', {}, _StubHandler(), 'de'
)
assert_eq("no enabled dimensions -> empty", results_course, [])


# ==========================================================================
# 12. Materialisation in __init__: df has segment columns after construction
# ==========================================================================

print()
print("=" * 60)
print("__init__ materialises the DataFrame once")
print("=" * 60)

assert_in("__segment_country__ after init",
          '__segment_country__', analyzer.df.columns)
assert_in("__segment_institution_type__",
          '__segment_institution_type__', analyzer.df.columns)
assert_in("__segment_size__",
          '__segment_size__', analyzer.df.columns)

# the original df stays unchanged
assert_true("original df unchanged (no segment columns)",
            not any(c.startswith('__segment_') for c in df_classic.columns))


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
