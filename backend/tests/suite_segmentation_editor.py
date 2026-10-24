# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the segmentation editor.

Verifies:
1. services/segmentation_editor.py: data models (DimensionDetail round trip)
2. services/segmentation_editor.py: list_dimensions
3. services/segmentation_editor.py: validate_dimension
4. services/segmentation_editor.py: save_dimension (add/update with a snapshot)
5. services/segmentation_editor.py: delete_dimension
6. services/segmentation_editor.py: interface helpers (table conversions)
7. app.py: the Setup tab has the segmentation sub-tab
8. app.py: all interface components and handlers present

Run: python tests/suite_segmentation_editor.py
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


if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

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


# stub core
TEMP_SURVEY_DIR = None
core_stub = types.ModuleType('core')
core_stub.get_active_config_dir = lambda: TEMP_SURVEY_DIR
core_stub.load_config = lambda: None
sys.modules['core'] = core_stub


services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg

config_io = _load('services.config_io',
                   os.path.join(ROOT, 'services', 'config_io.py'))
seg_editor = _load('services.segmentation_editor',
                    os.path.join(ROOT, 'services', 'segmentation_editor.py'))


import yaml


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
        self.themes = kw.get('themes', {})
        self.pending_reexport = False
        self.pending_reexport_reasons = []
    def mark_reexport_needed(self, r):
        self.pending_reexport = True


def make_config_with_dims():
    return {
        'settings': {
            'segmentation': {
                'dimensions': [
                    {
                        'id': 'country',
                        'label': {'de': 'Land'},
                        'source_question': 'q_country',
                        'bucketing': {'type': 'identity'},
                    },
                    {
                        'id': 'size',
                        'label': {'de': 'Größe'},
                        'source_question': 'q_size',
                        'bucketing': {
                            'type': 'numeric_buckets',
                            'buckets': [
                                {'label': {'de': 'klein'}, 'max': 10},
                                {'label': {'de': 'mittel'}, 'min': 10, 'max': 50},
                                {'label': {'de': 'groß'}, 'min': 50},
                            ],
                        },
                    },
                ],
            },
        },
        'question_groups': {
            'q_country': {'label': {'de': 'Land?'}, 'handler': 'single_choice'},
            'q_size': {'label': {'de': 'Größe?'}, 'handler': 'numeric'},
        },
    }


# ==========================================================================
# 1. DimensionDetail roundtrip
# ==========================================================================

print("=" * 60)
print("1. DimensionDetail from_dict / to_dict")
print("=" * 60)

# Identity
d_in = {'id': 'land', 'label': {'de': 'Land'},
        'source_question': 'q1', 'bucketing': {'type': 'identity'}}
d = seg_editor.DimensionDetail.from_dict(d_in)
assert_eq("identity: id", d.id, 'land')
assert_eq("identity: bucketing_type", d.bucketing_type, 'identity')
out = d.to_dict()
assert_eq("identity: roundtrip 'type'", out['bucketing']['type'], 'identity')

# value_mapping
d_in = {'id': 'reg', 'label': {'de': 'Region'},
        'source_question': 'q2',
        'bucketing': {
            'type': 'value_mapping',
            'mapping': {'DE': 'Deutschland', 'AT': 'Österreich'},
        }}
d = seg_editor.DimensionDetail.from_dict(d_in)
assert_eq("vm: bucketing_type", d.bucketing_type, 'value_mapping')
assert_eq("vm: mapping loaded", d.value_mapping, {'DE': 'Deutschland', 'AT': 'Österreich'})
out = d.to_dict()
assert_eq("vm: roundtrip mapping",
          out['bucketing']['mapping'],
          {'DE': 'Deutschland', 'AT': 'Österreich'})

# numeric_buckets
d_in = {'id': 'size', 'label': {'de': 'Größe'},
        'source_question': 'q3',
        'bucketing': {
            'type': 'numeric_buckets',
            'buckets': [
                {'label': {'de': 'klein'}, 'max': 10},
                {'label': {'de': 'groß'}, 'min': 50},
            ],
        }}
d = seg_editor.DimensionDetail.from_dict(d_in)
assert_eq("nb: bucketing_type", d.bucketing_type, 'numeric_buckets')
assert_eq("nb: 2 buckets", len(d.numeric_buckets), 2)
assert_eq("nb: bucket1 label", d.numeric_buckets[0]['label_source'], 'klein')
assert_eq("nb: bucket1 max", d.numeric_buckets[0]['max'], 10)
out = d.to_dict()
assert_eq("nb: roundtrip 2 buckets",
          len(out['bucketing']['buckets']), 2)

# unmapped_strategy as bucket
d_in = {'id': 'x', 'label': {'de': 'X'}, 'source_question': 'q1',
        'bucketing': {
            'type': 'value_mapping',
            'mapping': {'a': 'A'},
            'unmapped_strategy': {'type': 'bucket',
                                    'bucket_label': {'de': 'Sonstige'}},
        }}
d = seg_editor.DimensionDetail.from_dict(d_in)
assert_eq("unmapped type", d.unmapped_type, 'bucket')
assert_eq("unmapped label", d.unmapped_bucket_label, 'Sonstige')
out = d.to_dict()
us = out['bucketing']['unmapped_strategy']
assert_eq("unmapped roundtrip type", us['type'], 'bucket')
assert_eq("unmapped roundtrip label",
          us['bucket_label'], {'de': 'Sonstige'})


# ==========================================================================
# 2. list_dimensions
# ==========================================================================

print()
print("=" * 60)
print("2. list_dimensions")
print("=" * 60)

assert_eq("no state -> []",
          seg_editor.list_dimensions(None), [])
assert_eq("empty state -> []",
          seg_editor.list_dimensions(StubState()), [])

state = StubState(config=make_config_with_dims())
summaries = seg_editor.list_dimensions(state)
assert_eq("2 dimensions", len(summaries), 2)
ids = sorted(s.id for s in summaries)
assert_eq("IDs", ids, ['country', 'size'])

# size hat 3 Buckets
size_summary = next(s for s in summaries if s.id == 'size')
assert_eq("size: bucketing_type", size_summary.bucketing_type, 'numeric_buckets')
assert_eq("size: bucket_count", size_summary.bucket_count, 3)


# ==========================================================================
# 3. get_dimension_detail
# ==========================================================================

print()
print("=" * 60)
print("3. get_dimension_detail")
print("=" * 60)

detail = seg_editor.get_dimension_detail(state, 'country')
assert_true("country detail present", detail is not None)
if detail:
    assert_eq("country: source_question",
              detail.source_question, 'q_country')

assert_eq("unknown id -> None",
          seg_editor.get_dimension_detail(state, 'nope'), None)


# ==========================================================================
# 4. validate_dimension
# ==========================================================================

print()
print("=" * 60)
print("4. validate_dimension")
print("=" * 60)

# empty id
d = seg_editor.DimensionDetail(id='', label={'de': 'X'},
                                  source_question='q_country')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("empty id: invalid", ok, False)
assert_true("error contains 'ID'", any('ID' in e for e in errs))

# invalid characters
d = seg_editor.DimensionDetail(id='hat leerzeichen',
                                  label={'de': 'X'},
                                  source_question='q_country')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("invalid id characters", ok, False)

# the source question does not exist
d = seg_editor.DimensionDetail(id='neu', label={'de': 'X'},
                                  source_question='q_unknown')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("unknown source question", ok, False)

# value_mapping without a mapping
d = seg_editor.DimensionDetail(id='neu', label={'de': 'X'},
                                  source_question='q_country',
                                  bucketing_type='value_mapping')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("vm without mapping", ok, False)

# numeric_buckets with an invalid bucket
d = seg_editor.DimensionDetail(id='neu', label={'de': 'X'},
                                  source_question='q_country',
                                  bucketing_type='numeric_buckets')
d.numeric_buckets = [{'label_source': 'b', 'min': 100, 'max': 50}]  # min>=max
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("nb with min>=max", ok, False)

# bucket strategy without a label
d = seg_editor.DimensionDetail(id='neu', label={'de': 'X'},
                                  source_question='q_country',
                                  unmapped_type='bucket',
                                  unmapped_bucket_label='')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("unmapped=bucket without a label", ok, False)

# Valid case
d = seg_editor.DimensionDetail(id='neu', label={'de': 'Neu'},
                                  source_question='q_country',
                                  bucketing_type='identity')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("Valid case", ok, True)

# is_new=True, but the id exists already
d = seg_editor.DimensionDetail(id='country', label={'de': 'Doppelt'},
                                  source_question='q_country',
                                  bucketing_type='identity')
ok, errs = seg_editor.validate_dimension(state, d, is_new=True)
assert_eq("is_new + existing id", ok, False)

# is_new=False, the id exists -> ok (update)
ok, errs = seg_editor.validate_dimension(state, d, is_new=False)
assert_eq("is_new=False + existing id -> ok", ok, True)


# ==========================================================================
# 5. save_dimension + delete_dimension
# ==========================================================================

print()
print("=" * 60)
print("5. save_dimension + delete")
print("=" * 60)

TEMP_SURVEY_DIR = Path(tempfile.mkdtemp(prefix='l14b_test_'))
core_stub.get_active_config_dir = lambda: TEMP_SURVEY_DIR

try:
    # write the initial configuration
    config = make_config_with_dims()
    config_io.write_active_config_with_snapshot(config)
    state.config = config
    
    # add a new dimension
    new_d = seg_editor.DimensionDetail(
        id='neu_dim',
        label={'de': 'Neue Dimension'},
        source_question='q_country',
        bucketing_type='value_mapping',
        value_mapping={'A': 'Alpha', 'B': 'Beta'},
    )
    ok, status = seg_editor.save_dimension(state, new_d, is_new=True)
    assert_eq("save new: successful", ok, True)
    assert_in("save new: status enthaelt success", "✅", status)
    
    # check in the file
    cfg_after = config_io.load_active_config()
    dims_after = cfg_after['settings']['segmentation']['dimensions']
    assert_eq("3 dimensions after add", len(dims_after), 3)
    new_in_file = next((d for d in dims_after if d['id'] == 'neu_dim'), None)
    assert_true("neu_dim in the file", new_in_file is not None)
    if new_in_file:
        assert_eq("mapping written",
                  new_in_file['bucketing']['mapping'],
                  {'A': 'Alpha', 'B': 'Beta'})
    
    # a snapshot was taken
    snapshots = config_io.list_snapshots()
    assert_true("snapshot present", len(snapshots) >= 1)
    
    # update an existing dimension
    update_d = seg_editor.DimensionDetail(
        id='country',
        label={'de': 'Land (geändert)'},
        source_question='q_country',
        bucketing_type='identity',
    )
    ok, status = seg_editor.save_dimension(state, update_d, is_new=False)
    assert_eq("update: successful", ok, True)
    
    cfg_after = config_io.load_active_config()
    dims_after = cfg_after['settings']['segmentation']['dimensions']
    segment_value_after = next(d for d in dims_after if d['id'] == 'country')
    assert_eq("label updated", segment_value_after['label']['de'], 'Land (geändert)')
    
    # delete
    ok, status = seg_editor.delete_dimension(state, 'neu_dim')
    assert_eq("delete: successful", ok, True)
    cfg_after = config_io.load_active_config()
    dims_after = cfg_after['settings']['segmentation']['dimensions']
    assert_eq("2 dimensions after delete", len(dims_after), 2)
    assert_true("neu_dim gone",
                not any(d['id'] == 'neu_dim' for d in dims_after))
    
    # delete a dimension that does not exist
    ok, status = seg_editor.delete_dimension(state, 'gibts_nicht')
    assert_eq("delete unknown: failure", ok, False)
    
    # save with a validation error -> failure
    invalid = seg_editor.DimensionDetail(id='', label={'de': 'X'},
                                            source_question='q_country')
    ok, status = seg_editor.save_dimension(state, invalid, is_new=True)
    assert_eq("save with invalid -> failure", ok, False)
    assert_in("status enthaelt 'Validation'", "Validation", status)
    
finally:
    shutil.rmtree(TEMP_SURVEY_DIR, ignore_errors=True)


# ==========================================================================
# 6. UI-Helper: table-conversions
# ==========================================================================

print()
print("=" * 60)
print("6. UI-Helper")
print("=" * 60)

# value_mapping_to_table / from_table
mapping = {'DE': 'Deutschland', 'AT': 'Österreich'}
rows = seg_editor.value_mapping_to_table(mapping)
assert_eq("vm_to_table: 2 rows", len(rows), 2)

back = seg_editor.value_mapping_from_table(rows)
assert_eq("vm roundtrip", back, mapping)

# with pandas
import pandas as pd
df_rows = pd.DataFrame(rows, columns=['k', 'v'])
back2 = seg_editor.value_mapping_from_table(df_rows)
assert_eq("vm from pandas DataFrame", back2, mapping)

# empty rows are filtered
back3 = seg_editor.value_mapping_from_table([['', ''], ['k', 'v']])
assert_eq("vm: empty row filtered", back3, {'k': 'v'})


# numeric_buckets_to_table / from_table
buckets = [
    {'label_source': 'klein', 'min': None, 'max': 10},
    {'label_source': 'groß', 'min': 50, 'max': None},
]
rows = seg_editor.numeric_buckets_to_table(buckets)
assert_eq("nb_to_table: 2 rows", len(rows), 2)
# first row: ['klein', '', 10]
assert_eq("nb_to_table: empty min", rows[0][1], '')

back = seg_editor.numeric_buckets_from_table(rows)
assert_eq("nb roundtrip: 2 buckets", len(back), 2)
assert_eq("nb roundtrip: bucket1 max", back[0]['max'], 10.0)
assert_eq("nb roundtrip: bucket1 min None", back[0]['min'], None)

# comma tolerance
back2 = seg_editor.numeric_buckets_from_table([['x', '1,5', '10']])
assert_eq("nb: comma tolerance", back2[0]['min'], 1.5)


# get_question_choices_for_dropdown
choices = seg_editor.get_question_choices_for_dropdown(state)
assert_in("dropdown: q_country", 'q_country', choices)
assert_in("dropdown: q_size", 'q_size', choices)

# render_dimension_table
summaries = seg_editor.list_dimensions(state)
table = seg_editor.render_dimension_table(summaries)
assert_eq(f"table has {len(summaries)} rows", len(table), len(summaries))
assert_eq("row has 4 columns", len(table[0]), 4)


# ==========================================================================
# 7. app.py: Setup-Sub-Tab
# ==========================================================================

print()
print("=" * 60)
print("7. app.py: UI-Integration")
print("=" * 60)

APP_PY = os.path.join(ROOT, 'app.py')
ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)

with open(APP_PY, encoding='utf-8') as f:
    content = f.read()

# Service-Import
assert_in("Import: segmentation_editor",
          "segmentation_editor as svc_segmentation_editor", content)

# Sub-Tab
assert_in("Sub-Tab '🧭 Segmentation dimensions'",
          '"🧭 Segmentation dimensions"', content)

# interface components
ui_elements = [
    'seg_dim_table',
    'seg_refresh_btn',
    'seg_new_btn',
    'seg_edit_mode',
    'seg_edit_id',
    'seg_edit_label',
    'seg_edit_source',
    'seg_edit_bucketing_type',
    'seg_edit_mapping_table',
    'seg_edit_buckets_table',
    'seg_edit_unmapped_type',
    'seg_save_btn',
    'seg_delete_btn',
    'seg_edit_status',
    'seg_preview_btn',
    'seg_preview_table',
]
for el in ui_elements:
    assert_in(f"UI: {el}", el, content)

# service calls
service_calls = [
    'svc_segmentation_editor.list_dimensions',
    'svc_segmentation_editor.render_dimension_table',
    'svc_segmentation_editor.get_question_choices_for_dropdown',
    'svc_segmentation_editor.get_dimension_detail',
    'svc_segmentation_editor.save_dimension',
    'svc_segmentation_editor.delete_dimension',
    'svc_segmentation_editor.preview_distribution',
    'svc_segmentation_editor.value_mapping_to_table',
    'svc_segmentation_editor.numeric_buckets_to_table',
]
for call in service_calls:
    assert_in(f"call: {call}", call, content)


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
