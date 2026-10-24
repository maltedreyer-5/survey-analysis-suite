# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the numeric clean-up sub-tab of the Workbench.

Verifies:
1. services/numeric_workbench.py: list_numeric_questions, find_question_by_choice
2. services/numeric_workbench.py: get_numeric_overview with invalid values
3. services/numeric_workbench.py: update_valid_range with a snapshot
4. services/numeric_workbench.py: invalid_values_to_table_rows + render_overview_summary
5. app.py: numeric sub-tab in the Workbench
6. app.py: service import
7. app.py: all interface components and handlers present

Run: python tests/suite_numeric_workbench.py
"""

import sys
import os
import types
import importlib.util
import tempfile
import shutil
import compileall
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


# utils-Stubs
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

# Translation stub
tm_stub = types.ModuleType('utils.translation')
class _TM:
    def t(self, key, lang='de', **kw): return key
tm_stub.get_translation_manager = lambda: _TM()
sys.modules['utils.translation'] = tm_stub


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# load the real numeric_coerce
_load('utils.numeric_coerce',
      os.path.join(ROOT, 'utils', 'numeric_coerce.py'))


# stub core (only what numeric_workbench/config_io need)
TEMP_SURVEY_DIR = None
core_stub = types.ModuleType('core')
core_stub.get_active_config_dir = lambda: TEMP_SURVEY_DIR
core_stub.load_config = lambda: None
sys.modules['core'] = core_stub


# services-Package + Module
services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg

config_io = _load('services.config_io',
                   os.path.join(ROOT, 'services', 'config_io.py'))
numeric_workbench = _load('services.numeric_workbench',
                            os.path.join(ROOT, 'services', 'numeric_workbench.py'))


import yaml
import pandas as pd


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
        a = repr(actual)[:200]
        e = repr(expected)[:200]
        RESULTS.append(('FAIL', f"{label}: expected {e}, got {a}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} not in container"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


class StubState:
    def __init__(self, **kw):
        self.df = kw.get('df')
        self.config = kw.get('config')
        self.db = kw.get('db')
        self.import_id = kw.get('import_id')
        self.analysis_results = kw.get('analysis_results', {})
        self.themes = kw.get('themes', {})
        self.project_id = kw.get('project_id')
        self.pending_reexport = False
        self.pending_reexport_reasons = []
    
    def mark_reexport_needed(self, reason):
        self.pending_reexport = True
        if reason and reason not in self.pending_reexport_reasons:
            self.pending_reexport_reasons.append(reason)


# test configuration
def make_config():
    return {
        'settings': {},
        'question_groups': {
            'q_count': {
                'label': {'de': 'Wie viele?'},
                'columns': ['count_col'],
                'type': 'numeric',
                'handler': 'numeric',
                'numeric': {
                    'unit': 'Personen',
                    'valid_range': {'min': 0, 'max': 100},
                    'decimals': 0,
                    'bin_count': 10,
                },
            },
            'q_year': {
                'label': {'de': 'Welches Jahr?'},
                'columns': ['year_col'],
                'type': 'numeric',
                'handler': 'numeric',
                'numeric': {
                    'unit': '',
                    'valid_range': {'min': 1990, 'max': 2026},
                    'decimals': 0,
                    'bin_count': 10,
                },
            },
            'q_text': {
                'label': {'de': 'Freitext-Frage'},
                'columns': ['text_col'],
                'type': 'freetext',
                'handler': 'freetext',
            },
        },
    }


# ==========================================================================
# 1. list_numeric_questions
# ==========================================================================

print("=" * 60)
print("1. list_numeric_questions")
print("=" * 60)

# no state -> []
assert_eq("no state -> []",
          numeric_workbench.list_numeric_questions(None), [])

# empty state -> []
empty_state = StubState()
assert_eq("empty state -> []",
          numeric_workbench.list_numeric_questions(empty_state), [])

# with configuration
state = StubState(config=make_config())
infos = numeric_workbench.list_numeric_questions(state)
assert_eq("exactly 2 numeric questions", len(infos), 2)
qids = sorted(i.qid for i in infos)
assert_eq("ids correct", qids, ['q_count', 'q_year'])

# check the fields
q_count = next(i for i in infos if i.qid == 'q_count')
assert_eq("q_count: label", q_count.label, 'Wie viele?')
assert_eq("q_count: column", q_count.column, 'count_col')
assert_eq("q_count: unit", q_count.unit, 'Personen')
assert_eq("q_count: min", q_count.min_value, 0)
assert_eq("q_count: max", q_count.max_value, 100)

# Display-String
ds = q_count.display_choice()
assert_in("Display-String enthaelt qid", "q_count", ds)
assert_in("Display-String enthaelt unit", "Personen", ds)
assert_in("Display-String enthaelt range", "0–100", ds)


# ==========================================================================
# 2. find_question_by_choice
# ==========================================================================

print()
print("=" * 60)
print("2. find_question_by_choice")
print("=" * 60)

# with a display string
choice = q_count.display_choice()
found = numeric_workbench.find_question_by_choice(state, choice)
assert_true("display string finds the question", found is not None)
if found:
    assert_eq("correct qid found", found.qid, 'q_count')

# only qid
found2 = numeric_workbench.find_question_by_choice(state, 'q_year')
assert_true("bare qid finds the question", found2 is not None)

# not existing
none_found = numeric_workbench.find_question_by_choice(state, 'q_nope')
assert_eq("unknown qid -> None", none_found, None)

# empty choice
none_found2 = numeric_workbench.find_question_by_choice(state, '')
assert_eq("empty choice -> None", none_found2, None)


# ==========================================================================
# 3. get_numeric_overview
# ==========================================================================

print()
print("=" * 60)
print("3. get_numeric_overview")
print("=" * 60)

# DataFrame with real values:
# - 5 valid (5, 10, 50, 80, 99)
# - 2 invalid (-5 below min, 250 above max)
# - 1 not numeric ("ein paar")
# - 1 NaN (real missing value -> not counted as invalid)
df = pd.DataFrame({
    'count_col': [5, 10, 50, 80, 99, -5, 250, 'ein paar', None],
    'year_col': [2020] * 9,
})
state = StubState(config=make_config(), df=df)

overview = numeric_workbench.get_numeric_overview(state, 'q_count')
assert_true("overview created", overview is not None)

if overview:
    assert_eq("qid", overview.qid, 'q_count')
    assert_eq("unit", overview.unit, 'Personen')
    assert_eq("min_value", overview.min_value, 0)
    assert_eq("max_value", overview.max_value, 100)
    
    # n_total: 9 rows
    assert_eq("n_total", overview.n_total, 9)
    # NaN/None does not count as invalid -> 8 values are checked,
    # of which 5 valid, 3 invalid
    assert_eq("n_valid", overview.n_valid, 5)
    assert_eq("n_invalid", overview.n_invalid, 3)
    
    # status per value
    status_counts = {}
    for inv in overview.invalid_values:
        status_counts[inv.status] = status_counts.get(inv.status, 0) + 1
    
    assert_eq("1 'below min'", status_counts.get('out_of_range_low'), 1)
    assert_eq("1 'above max'", status_counts.get('out_of_range_high'), 1)
    assert_eq("1 'not numeric'", status_counts.get('non_numeric'), 1)


# question without df -> error
state_no_df = StubState(config=make_config())
ov2 = numeric_workbench.get_numeric_overview(state_no_df, 'q_count')
assert_true("without df: overview with error",
            ov2 is not None and ov2.error == "No data imported")

# non-numeric question
ov3 = numeric_workbench.get_numeric_overview(state, 'q_text')
assert_eq("free-text question -> None", ov3, None)

# unknown qid
ov4 = numeric_workbench.get_numeric_overview(state, 'q_nope')
assert_eq("unknown qid -> None", ov4, None)


# ==========================================================================
# 4. invalid_values_to_table_rows + render_overview_summary
# ==========================================================================

print()
print("=" * 60)
print("4. UI-Helper: rows + summary")
print("=" * 60)

rows = numeric_workbench.invalid_values_to_table_rows(overview)
assert_eq("3 rows for 3 invalid values", len(rows), 3)
# every row has 4 columns
assert_true("every row has 4 columns",
            all(len(r) == 4 for r in rows))

summary = numeric_workbench.render_overview_summary(overview)
assert_in("Summary enthaelt qid", "q_count", summary)
assert_in("Summary enthaelt unit", "Personen", summary)
assert_in("Summary enthaelt 'valid'", "valid", summary)
assert_in("Summary enthaelt 'invalid'", "invalid", summary)


# render with None
no_summary = numeric_workbench.render_overview_summary(None)
assert_in("None -> note Markdown", "No question", no_summary)


# ==========================================================================
# 5. update_valid_range with a snapshot
# ==========================================================================

print()
print("=" * 60)
print("5. update_valid_range")
print("=" * 60)

# temporary survey directory
TEMP_SURVEY_DIR = Path(tempfile.mkdtemp(prefix='l14_test_'))
core_stub.get_active_config_dir = lambda: TEMP_SURVEY_DIR

try:
    # write the initial configuration
    config = make_config()
    config_io.write_active_config_with_snapshot(config)
    
    # set the state to this configuration
    state.config = config
    
    # successful update
    ok, status = numeric_workbench.update_valid_range(
        state, 'q_count', min_value=0, max_value=200
    )
    assert_eq("update_valid_range successful", ok, True)
    assert_in("Status enthaelt success-icon", "✅", status)
    
    # the file was changed
    new_cfg = config_io.load_active_config()
    assert_eq("new min_value stored",
              new_cfg['question_groups']['q_count']['numeric']['valid_range']['min'],
              0)
    assert_eq("new max_value stored",
              new_cfg['question_groups']['q_count']['numeric']['valid_range']['max'],
              200)
    
    # a snapshot was taken
    snapshots = config_io.list_snapshots()
    assert_true("at least 1 snapshot present", len(snapshots) >= 1)
    
    # remove min (= no limit)
    ok2, _ = numeric_workbench.update_valid_range(
        state, 'q_count', min_value=None, max_value=200
    )
    assert_eq("min=None removes min", ok2, True)
    new_cfg = config_io.load_active_config()
    vr = new_cfg['question_groups']['q_count']['numeric'].get('valid_range', {})
    assert_eq("'min' removed", 'min' in vr, False)
    assert_eq("'max' stays", vr.get('max'), 200)
    
    # remove both -> the valid_range block is gone
    ok3, _ = numeric_workbench.update_valid_range(
        state, 'q_count', min_value=None, max_value=None
    )
    assert_eq("both None successful", ok3, True)
    new_cfg = config_io.load_active_config()
    has_vr = 'valid_range' in new_cfg['question_groups']['q_count'].get('numeric', {})
    assert_eq("valid_range-Block weg", has_vr, False)
    
    # plausibility: min >= max -> error
    ok4, status = numeric_workbench.update_valid_range(
        state, 'q_count', min_value=100, max_value=50
    )
    assert_eq("min >= max -> failure", ok4, False)
    assert_in("error note", "smaller than max", status)
    
    # non-numeric question
    ok5, status = numeric_workbench.update_valid_range(
        state, 'q_text', min_value=0, max_value=100
    )
    assert_eq("non-numeric -> failure", ok5, False)
    
    # unknown qid
    ok6, status = numeric_workbench.update_valid_range(
        state, 'q_nope', min_value=0, max_value=100
    )
    assert_eq("unknown qid -> failure", ok6, False)
    
finally:
    shutil.rmtree(TEMP_SURVEY_DIR, ignore_errors=True)


# ==========================================================================
# 6. app.py: service import + interface components
# ==========================================================================

print()
print("=" * 60)
print("6. app.py")
print("=" * 60)

APP_PY = os.path.join(ROOT, 'app.py')
ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)

with open(APP_PY, encoding='utf-8') as f:
    content = f.read()

# Service-Import
assert_in("Import: numeric_workbench",
          "numeric_workbench as svc_numeric_workbench", content)

# Sub-Tab
assert_in("Sub-Tab '🔢 Numeric'", '"🔢 Numeric"', content)

# interface components
ui_elements = [
    'wb_numeric_question',
    'wb_numeric_refresh_btn',
    'wb_numeric_load_btn',
    'wb_numeric_overview_md',
    'wb_numeric_invalid_table',
    'wb_numeric_min',
    'wb_numeric_max',
    'wb_numeric_save_range_btn',
    'wb_numeric_status',
]
for el in ui_elements:
    assert_in(f"UI: {el}", el, content)

# Handler
assert_in("Handler: wb_numeric_refresh", "def wb_numeric_refresh", content)
assert_in("Handler: wb_numeric_load",    "def wb_numeric_load", content)
assert_in("Handler: wb_numeric_save_range",
          "def wb_numeric_save_range", content)

# service calls
assert_in("call: list_numeric_questions",
          "svc_numeric_workbench.list_numeric_questions", content)
assert_in("call: get_numeric_overview",
          "svc_numeric_workbench.get_numeric_overview", content)
assert_in("call: update_valid_range",
          "svc_numeric_workbench.update_valid_range", content)

# re-export banner trigger after a range change
assert_in("Range-Aenderung -> mark_reexport",
          'mark_reexport_needed(\n                                f"Numeric range',
          content.replace('\\n', '\n'))


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
