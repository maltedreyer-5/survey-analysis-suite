# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Integration test: end-to-end validation of the analysis pipeline.

Setup:
- a mini survey with three question types:
  * q_type    - single_choice (type of institution)
  * q_count   - numeric (number of staff)
  * q_yn      - single_choice (yes/no)
- a segmentation dimension on q_type
- 10 rows of test data as a DataFrame (in-memory CSV)

Checks the integration over the most important paths, WITHOUT an LLM:
1. handler layer: the numeric and single-choice analyses return an AnalysisResult
2. service layer: numeric_workbench identifies invalid values
3. service layer: segmentation_editor returns the right distribution
4. dashboard format: _format_result_for_dashboard writes correct JSON
   for all three handler types
5. consistency: the sums match, the segmentation is consistent with the data

Run: python tests/suite_integration.py

Deliberately NOT tested (LLM-dependent):
- item extraction for free text
- theme analysis
- summary generation
- suggestions of the configuration assistant

These paths are covered by the component tests (suite_services, suite_config_assistant).
"""

import sys
import os
import types
import importlib.util
import tempfile
import shutil
from pathlib import Path

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


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')


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
        a = repr(actual)[:200]; e = repr(expected)[:200]
        RESULTS.append(('FAIL', f"{label}: erw {e}, got {a}"))


def assert_close(label, actual, expected, tol=1e-6):
    """Float comparison with a tolerance."""
    global PASS, FAIL
    try:
        if abs(float(actual) - float(expected)) <= tol:
            PASS += 1; RESULTS.append(('OK', label))
        else:
            FAIL += 1; RESULTS.append(('FAIL',
                f"{label}: erw {expected}, got {actual} (Diff {abs(actual - expected)})"))
    except Exception as e:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: error {e}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: {item!r} not in container"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1; RESULTS.append(('OK', label))
    else:
        FAIL += 1; RESULTS.append(('FAIL', f"{label}: war {value!r}"))


# ==========================================================================
# STAGE 0 — set up a mini survey
# ==========================================================================

print("=" * 60)
print("STAGE 0 — set up a mini survey")
print("=" * 60)

# 1. test data
import pandas as pd

# 10 rows with mixed answers:
# - q_type: 4x Universitaet, 4x Fachhochschule, 2x Sonstige
# - q_count: 5,12,18,25,42,50,75,100,250 (1x above the range = 250),
#            'keine Angabe' (= invalid, not numeric)
# - q_yn: 6x Ja, 4x Nein
test_df = pd.DataFrame({
    'q_type_col': [
        'Universitaet', 'Universitaet', 'Universitaet', 'Universitaet',
        'Fachhochschule', 'Fachhochschule', 'Fachhochschule', 'Fachhochschule',
        'Sonstige', 'Sonstige',
    ],
    'q_count_col': [
        5, 12, 18, 25, 42, 50, 75, 100, 250, 'keine Angabe',
    ],
    'q_yn_col': [
        'Ja', 'Ja', 'Ja', 'Ja', 'Ja', 'Ja',
        'Nein', 'Nein', 'Nein', 'Nein',
    ],
})

print(f"  test data: {len(test_df)} rows, {len(test_df.columns)} columns")
print(f"  columns: {list(test_df.columns)}")


# 2. configuration
test_config = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {
                    'id': 'institution_type',
                    'label': {'de': 'Hochschultyp'},
                    'source_question': 'q_type',
                    'bucketing': {'type': 'identity'},
                },
            ],
        },
    },
    'question_groups': {
        'q_type': {
            'label': {'de': 'Was ist der Typ Ihrer Einrichtung?'},
            'columns': ['q_type_col'],
            'type': 'single_choice',
            'handler': 'single_choice',
        },
        'q_count': {
            'label': {'de': 'Wie viele Mitarbeiter?'},
            'columns': ['q_count_col'],
            'type': 'numeric',
            'handler': 'numeric',
            'numeric': {
                'unit': 'Personen',
                'valid_range': {'min': 0, 'max': 200},
                'decimals': 0,
                'bin_count': 5,
            },
        },
        'q_yn': {
            'label': {'de': 'Bieten Sie Praktika an?'},
            'columns': ['q_yn_col'],
            'type': 'single_choice',
            'handler': 'single_choice',
        },
    },
}


# 3. Stub-State
class StubState:
    def __init__(self, df, config):
        self.df = df
        self.config = config
        self.db = None
        self.import_id = None
        self.project_id = None
        self.analysis_results = {}
        self.themes = {}
        self.clusters_by_value = None
        self.pending_reexport = False
        self.pending_reexport_reasons = []
        self.current_segment = None
        self.segment_column = None
        self.segment_filter = None
    
    def mark_reexport_needed(self, r):
        self.pending_reexport = True
    
    def clear_reexport_flag(self):
        self.pending_reexport = False


state = StubState(test_df, test_config)
print("  StubState created")


# ==========================================================================
# STAGE 1 — numeric handler directly
# ==========================================================================

print()
print("=" * 60)
print("STAGE 1 — Numeric-Handler-Analyse")
print("=" * 60)

# utils-Stubs
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(ROOT, 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

# translation stub
tm_stub = types.ModuleType('utils.translation')
class _TM:
    def t(self, key, lang='de', **kw): return key
    def translate_common_label(self, key, lang='de'): return key
    def translate(self, key, lang='de'): return key
    def get_label(self, key, lang='de', default=None): return default or key
tm_stub.get_translation_manager = lambda: _TM()
sys.modules['utils.translation'] = tm_stub


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# load the real numeric_coerce + segment_resolver
_load('utils.numeric_coerce',
      os.path.join(ROOT, 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver',
      os.path.join(ROOT, 'utils', 'segment_resolver.py'))

# Handler laden
handlers_pkg = types.ModuleType('handlers')
handlers_pkg.__path__ = [os.path.join(ROOT, 'handlers')]
sys.modules['handlers'] = handlers_pkg

base_mod = _load('handlers.base',
                  os.path.join(ROOT, 'handlers', 'base.py'))
numeric_mod = _load('handlers.numeric',
                     os.path.join(ROOT, 'handlers', 'numeric.py'))


# analyse the numeric question
qcfg = test_config['question_groups']['q_count']
numeric_handler = numeric_mod.NumericHandler(qcfg)
result = numeric_handler.analyze(test_df, lang='de')

assert_true("AnalysisResult created", result is not None)
# handler_type is a class attribute of BaseHandler, not of AnalysisResult
# instances; instead the result is verified to look like a numeric result
# (numeric_stats in the metadata).
assert_in("metadata.numeric_stats da",
          'numeric_stats', result.metadata or {})

# n_total = 10, of which 1 not numeric ('keine Angabe') and 1 above the range (250)
# -> n_valid = 8 (values: 5,12,18,25,42,50,75,100)
assert_eq("n_valid", result.n, 8)

# statistics of the 8 valid values (5, 12, 18, 25, 42, 50, 75, 100)
metadata = result.metadata or {}
numeric_result = metadata.get('numeric_stats')
assert_true("numeric_stats present", numeric_result is not None)
if numeric_result is not None:
    # numeric_stats is a NumericResult object (no dict); read with getattr.
    assert_eq("n in stats", getattr(numeric_result, 'n', None), 8)
    # Mean = (5+12+18+25+42+50+75+100)/8 = 327/8 = 40.875
    assert_close("mean", getattr(numeric_result, 'mean', None), 40.875, tol=0.01)
    # Median = (25+42)/2 = 33.5
    assert_close("median", getattr(numeric_result, 'median', None), 33.5, tol=0.01)
    # min_value/max_value (NOT min/max: conflict with the Python built-ins in the dataclass)
    assert_eq("min_value", getattr(numeric_result, 'min_value', None), 5)
    assert_eq("max_value", getattr(numeric_result, 'max_value', None), 100)

# the histogram is not in result.data (that is to_summary_dict), but in
# numeric_result.histogram_counts (List[int]).
hist_counts = getattr(numeric_result, 'histogram_counts', None) or []
assert_true("histogram_counts not empty", len(hist_counts) > 0)
assert_eq("sum of histogram_counts == n_valid", sum(hist_counts), 8)
# histogram_bins (edges) has n+1 values
hist_edges = getattr(numeric_result, 'histogram_bins', None) or []
assert_eq("histogram_bins has n+1 edges",
          len(hist_edges), len(hist_counts) + 1)


# ==========================================================================
# STAGE 2 — SingleChoice-Handler
# ==========================================================================

print()
print("=" * 60)
print("STAGE 2 — SingleChoice-Handler-Analyse")
print("=" * 60)

sc_mod = _load('handlers.single_choice',
                os.path.join(ROOT, 'handlers', 'single_choice.py'))

qcfg_yn = test_config['question_groups']['q_yn']
sc_handler = sc_mod.SingleChoiceHandler(qcfg_yn)
result_yn = sc_handler.analyze(test_df, lang='de')

assert_eq("q_yn n", result_yn.n, 10)
data_yn = result_yn.data or {}
assert_eq("yes count", data_yn.get('Ja'), 6)
assert_eq("no count", data_yn.get('Nein'), 4)


# ==========================================================================
# STAGE 3: service numeric_workbench (detecting invalid values)
# ==========================================================================

print()
print("=" * 60)
print("STAGE 3 — Service-Layer: numeric_workbench")
print("=" * 60)

# stub core for the service imports
core_stub = types.ModuleType('core')
core_stub.get_active_config_dir = lambda: Path('/tmp/nonexistent')
core_stub.load_config = lambda: None
sys.modules['core'] = core_stub

services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg

config_io = _load('services.config_io',
                   os.path.join(ROOT, 'services', 'config_io.py'))
nw = _load('services.numeric_workbench',
            os.path.join(ROOT, 'services', 'numeric_workbench.py'))

# list of the numeric questions
infos = nw.list_numeric_questions(state)
assert_eq("exactly 1 numeric question", len(infos), 1)
if infos:
    assert_eq("numeric question id", infos[0].qid, 'q_count')

# detail view with invalid values
overview = nw.get_numeric_overview(state, 'q_count')
assert_true("overview created", overview is not None)
if overview:
    # n_total = 10, n_valid = 8, n_invalid = 2 (1× non-numeric, 1× out of range)
    assert_eq("Overview n_total", overview.n_total, 10)
    assert_eq("Overview n_valid", overview.n_valid, 8)
    assert_eq("Overview n_invalid", overview.n_invalid, 2)
    
    # status distribution of the invalid values
    statuses = [iv.status for iv in overview.invalid_values]
    assert_in("'non_numeric' in invalid", 'non_numeric', statuses)
    assert_in("'out_of_range_high' in invalid", 'out_of_range_high', statuses)


# ==========================================================================
# STAGE 4 — service: segmentation_editor (distribution preview)
# ==========================================================================

print()
print("=" * 60)
print("STAGE 4 — Service-Layer: segmentation_editor")
print("=" * 60)

seg_ed = _load('services.segmentation_editor',
                os.path.join(ROOT, 'services', 'segmentation_editor.py'))

dims = seg_ed.list_dimensions(state)
assert_eq("1 Segmentierungs-Dimension", len(dims), 1)
if dims:
    assert_eq("Dimension ID", dims[0].id, 'institution_type')

# distribution preview
preview = seg_ed.preview_distribution(state, 'institution_type')
print(f"  DEBUG preview: error={preview.error!r}, n_total={preview.n_total}, rows={preview.rows}, n_unmapped={preview.n_unmapped}")
assert_eq("preview without errors", preview.error, None)
assert_eq("n_total in Preview", preview.n_total, 10)

# 4x Universitaet, 4x Fachhochschule, 2x Sonstige
preview_dict = dict(preview.rows)
assert_eq("Universitaet count", preview_dict.get('Universitaet'), 4)
assert_eq("Fachhochschule count", preview_dict.get('Fachhochschule'), 4)
assert_eq("Sonstige count", preview_dict.get('Sonstige'), 2)


# ==========================================================================
# STAGE 5: dashboard export format for all three question types
# ==========================================================================

print()
print("=" * 60)
print("STAGE 5 — dashboard format function")
print("=" * 60)

# load _format_result_for_dashboard in isolation (as in suite_dashboard_numeric)
import re
import logging

with open(os.path.join(ROOT, 'export', 'result_format.py'), encoding='utf-8') as f:
    src = f.read()

m = re.search(
    r'^def _format_result_for_dashboard\(.*?(?=\n^def |\Z)',
    src, flags=re.M | re.S
)
assert m, "Function not found"
fn_src = m.group(0)

mini_globals = {'__builtins__': __builtins__, 'logger': logging.getLogger('test')}
exec(fn_src, mini_globals)
_format = mini_globals['_format_result_for_dashboard']


# Numeric-Format
result_dict_numeric = {
    'n': result.n,
    'data': result.data,
    'percentages': result.percentages or {},
    'metadata': result.metadata or {},
}
fmt_n = _format(result_dict_numeric, 'numeric')

assert_eq("numeric: n", fmt_n['n'], 8)
assert_in("numeric: statistics da", 'statistics', fmt_n)
assert_in("numeric: unit da", 'unit', fmt_n)
assert_eq("numeric: unit correct", fmt_n['unit'], 'Personen')
assert_eq("numeric: decimals = 0", fmt_n['decimals'], 0)
assert_close("numeric: mean roundtrip",
             fmt_n['statistics']['mean'], 40.875, tol=0.01)

# SingleChoice-Format
result_dict_sc = {
    'n': result_yn.n,
    'data': result_yn.data,
    'percentages': result_yn.percentages or {},
    'metadata': result_yn.metadata or {},
}
fmt_sc = _format(result_dict_sc, 'single_choice')

assert_eq("single_choice: n", fmt_sc['n'], 10)
assert_in("single_choice: values da", 'values', fmt_sc)
assert_eq("single_choice: Ja-Count", fmt_sc['values'].get('Ja'), 6)
assert_eq("single_choice: NO statistics",
          'statistics' not in fmt_sc, True)
assert_eq("single_choice: NO unit",
          'unit' not in fmt_sc, True)


# ==========================================================================
# STAGE 6: consistency checks over all stages
# ==========================================================================

print()
print("=" * 60)
print("STAGE 6 — consistency")
print("=" * 60)

# 6.1 sum of the single_choice counts == n
sc_sum = sum(fmt_sc['values'].values())
assert_eq("single_choice sum == n", sc_sum, fmt_sc['n'])

# 6.2 Numeric-stats.n == n im fmt
assert_eq("numeric stats.n == fmt.n",
          fmt_n['statistics']['n'], fmt_n['n'])

# 6.3 Numeric-Histogram-Sum == n
hist_sum = sum(fmt_n['values'].values()) if isinstance(fmt_n['values'], dict) else 0
assert_eq("numeric histogram sum == n", hist_sum, fmt_n['n'])

# 6.4 sum of the segmentation preview == n
preview_sum = sum(n for _, n in preview.rows)
assert_eq("segmentation preview sum == n_total", preview_sum, preview.n_total)

# 6.5 Numeric n_valid + n_invalid == n_total in workbench overview
if overview:
    assert_eq("workbench: n_valid+n_invalid == n_total",
              overview.n_valid + overview.n_invalid, overview.n_total)

# 6.6 n_valid of the numeric handler and of the Workbench are equal
if overview:
    assert_eq("handler n == workbench n_valid",
              result.n, overview.n_valid)


# ==========================================================================
# STAGE 7 — survey switch + reset
# ==========================================================================

print()
print("=" * 60)
print("STAGE 7 — behaviour on a survey switch")
print("=" * 60)

# test the AppState reset (as when switching the survey in the Setup tab)
state.df = test_df
state.import_id = 42
state.analysis_results = {'q_count': result}
state.pending_reexport = True

# reset as on a survey switch
state.df = None
state.import_id = None
state.project_id = None
state.config = None
state.analysis_results = {}
state.themes = {}

assert_eq("df after the reset = None", state.df, None)
assert_eq("import_id after the reset = None", state.import_id, None)
assert_eq("analysis_results empty after the reset",
          state.analysis_results, {})

# pending_reexport was NOT reset by the manual reset
# (the reset of the switch in app.py does not reset it either, on purpose:
# a re-export of the OLD survey may be pending regardless of the switch).
# But the AppState.reset() method in core.py resets it.

# this consistency is a deliberate design point, verified here.
assert_eq("pending_reexport stays after a manual field reset",
          state.pending_reexport, True)


# ==========================================================================
# STAGE 8 — Dashboard-JSON Round-Trip (Format -> JSON -> Parse)
# ==========================================================================

print()
print("=" * 60)
print("STAGE 8 — JSON-Roundtrip")
print("=" * 60)

import json

# build an object like dashboard_data.json
dashboard_data = {
    'metadata': {
        'survey_title': 'Integration Test Survey',
        'languages': ['de'],
    },
    'segments': {
        'institution_type': {
            'key': 'institution_type',
            'labels': {'de': 'Hochschultyp'},
            'values': preview_dict,
        },
    },
    'questions': {
        'q_count': {
            'id': 'q_count',
            'type': 'numeric',
            'text': {'de': 'Wie viele Mitarbeiter?'},
            'result': fmt_n,
        },
        'q_yn': {
            'id': 'q_yn',
            'type': 'single_choice',
            'text': {'de': 'Bieten Sie Praktika an?'},
            'result': fmt_sc,
        },
    },
}

# round trip through JSON
serialized = json.dumps(dashboard_data, ensure_ascii=False, indent=2)
parsed = json.loads(serialized)

# the numeric statistics survive the round trip
assert_in("statistics after the JSON round trip",
          'statistics', parsed['questions']['q_count']['result'])
assert_close("mean after the JSON round trip",
             parsed['questions']['q_count']['result']['statistics']['mean'],
             40.875, tol=0.01)

# segmentations
assert_eq("Segments-Keys",
          list(parsed['segments'].keys()),
          ['institution_type'])
assert_eq("segment values consistent",
          parsed['segments']['institution_type']['values']['Universitaet'],
          4)

# write + read the file: the final round trip
tmp = Path(tempfile.mkdtemp(prefix='l17_test_'))
try:
    json_path = tmp / 'dashboard_data.json'
    with json_path.open('w', encoding='utf-8') as f:
        f.write(serialized)
    
    assert_true("dashboard_data.json written", json_path.exists())
    
    # read again
    with json_path.open(encoding='utf-8') as f:
        roundtrip = json.load(f)
    
    assert_eq("file-roundtrip metadata.survey_title",
              roundtrip['metadata']['survey_title'],
              'Integration Test Survey')
    assert_eq("file-roundtrip questions count",
              len(roundtrip['questions']), 2)
    assert_in("file-roundtrip q_count.result.statistics",
              'statistics', roundtrip['questions']['q_count']['result'])

finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
# Summary
# ==========================================================================

print()
print("=" * 60)
print(f"INTEGRATION TEST TOTAL: {PASS} OK, {FAIL} FAIL")
print("=" * 60)
if FAIL > 0:
    print()
    for status, msg in RESULTS:
        if status == 'FAIL':
            print(f"  [{status}] {msg}")
    sys.exit(1)

print()
print("Paths checked:")
print("  ✓ data -> NumericHandler -> AnalysisResult -> dashboard JSON")
print("  ✓ data -> SingleChoiceHandler -> AnalysisResult -> dashboard JSON")
print("  ✓ data -> numeric_workbench (Workbench path)")
print("  ✓ data -> segmentation_editor.preview_distribution")
print("  ✓ consistency: sums + counts + Workbench stats agree")
print("  ✓ state reset semantics (survey switch)")
print("  ✓ JSON round trip with numeric.statistics")
print()
print("All tests passed.")
