# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of survey-specific imports.

Verifies:
4. utils/persistence.py: column imports.survey_id in CREATE
5. utils/persistence.py: save_import takes survey_id and writes it correctly
6. utils/persistence.py: get_imports_for_survey filters correctly
7. utils/persistence.py: count_imports_per_survey aggregates correctly
8. utils/persistence.py: get_latest_import_for_survey
9. services/data_import.py: the active survey id is passed on to save_import
10. services/survey_status.py: get_imports_per_survey + render helper
11. app.py: compiles + the interface component survey_imports_md is present

Run: python tests/suite_survey_imports.py
"""

import sys
import os
import types
import importlib.util
import tempfile
import shutil
import sqlite3
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


# ==========================================================================
# 3. SurveyDatabase: save_import + get_imports_for_survey
# ==========================================================================

print()
print("=" * 60)
print("3. SurveyDatabase with a survey filter")
print("=" * 60)

# prepare the utils stubs
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(ROOT, 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

# load SurveyDatabase
spec_p = importlib.util.spec_from_file_location(
    'utils.persistence', os.path.join(ROOT, 'utils', 'persistence.py')
)
persistence = importlib.util.module_from_spec(spec_p)
sys.modules['utils.persistence'] = persistence
spec_p.loader.exec_module(persistence)

# create a fresh database (the CREATE statement holds survey_id)
tmpdir = tempfile.mkdtemp(prefix='l15_db_test_')
db_path = Path(tmpdir) / 'test.db'

try:
    db = persistence.SurveyDatabase(db_path)
    
    # create the project
    project_id = db.create_project(name="Test", description="L15-Test")
    
    # store imports for several surveys
    import pandas as pd
    import time
    df1 = pd.DataFrame({'a': [1, 2, 3]})
    df2 = pd.DataFrame({'b': [4, 5]})
    
    id1 = db.save_import(project_id, df1, filename='a1.csv',
                           survey_id='survey_a')
    time.sleep(1.05)  # so imported_at differs
    id2 = db.save_import(project_id, df2, filename='a2.csv',
                           survey_id='survey_a')
    id3 = db.save_import(project_id, df1, filename='survey_b_a.csv',
                           survey_id='survey_b')
    
    assert_true("save_import id1", id1 > 0)
    assert_true("save_import id2", id2 > 0)
    assert_true("save_import id3", id3 > 0)
    
    # default without an explicit survey_id
    id4 = db.save_import(project_id, df1, filename='default.csv')
    
    # get_imports_for_survey
    a_imports = db.get_imports_for_survey('survey_a')
    assert_eq("survey_a: 2 Imports", len(a_imports), 2)
    
    survey_b_imports = db.get_imports_for_survey('survey_b')
    assert_eq("survey_b: 1 Import", len(survey_b_imports), 1)
    
    default_imports = db.get_imports_for_survey('default')
    assert_eq("default: 1 import", len(default_imports), 1)
    
    other_imports = db.get_imports_for_survey('does_not_exist')
    assert_eq("not existing: empty list", other_imports, [])
    
    # sorting: newest first
    if len(a_imports) >= 2:
        # imported_at: newest first, so id2 first
        assert_true("order newest first",
                    a_imports[0]['imported_at'] >= a_imports[1]['imported_at'])
    
    # count_imports_per_survey
    counts = db.count_imports_per_survey()
    assert_eq("survey_a count", counts.get('survey_a'), 2)
    assert_eq("survey_b count", counts.get('survey_b'), 1)
    assert_eq("default count", counts.get('default'), 1)
    
    # get_latest_import_for_survey
    latest_a = db.get_latest_import_for_survey('survey_a')
    assert_true("latest_a present", latest_a is not None)
    if latest_a:
        assert_eq("latest_a is id2 (newest)",
                  latest_a['id'], id2)
    
    latest_unknown = db.get_latest_import_for_survey('unknown_survey')
    assert_eq("latest_unknown: None", latest_unknown, None)
    
    # with a project_id filter
    latest_with_project = db.get_latest_import_for_survey('survey_a', project_id=project_id)
    assert_true("latest with project_id present",
                latest_with_project is not None)

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)


# ==========================================================================
# 4. services/data_import.py: get_active_survey_id is used
# ==========================================================================

print()
print("=" * 60)
print("4. services/data_import.py passes the survey_id on")
print("=" * 60)

dif_path = os.path.join(ROOT, 'services', 'data_import.py')
with open(dif_path, encoding='utf-8') as f:
    dif_content = f.read()

assert_in("Imports get_active_survey_id",
          "from core import get_active_survey_id", dif_content)
assert_in("call get_active_survey_id()",
          "get_active_survey_id()", dif_content)
assert_in("survey_id is passed to save_import",
          "survey_id=survey_id", dif_content)


# ==========================================================================
# 5. services/survey_status.py: statistics per survey
# ==========================================================================

print()
print("=" * 60)
print("5. survey_status: per-survey imports")
print("=" * 60)

tm_stub = types.ModuleType('utils.translation')
class _TM:
    def t(self, key, lang='de', **kw): return key
tm_stub.get_translation_manager = lambda: _TM()
sys.modules['utils.translation'] = tm_stub

# stub core
core_stub = types.ModuleType('core')
core_stub.get_active_survey_id = lambda: 'survey_a'
core_stub.get_registry = lambda: type('R', (), {
    'list_surveys': lambda self: [
        type('M', (), {
            'id': 'survey_a',
            'get_title': lambda self, lang='de': 'Beispielumfrage'
        })()
    ]
})()
core_stub.OUTPUT_DIR = '/tmp/nonexistent'
sys.modules['core'] = core_stub

services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg

spec_s = importlib.util.spec_from_file_location(
    'services.survey_status',
    os.path.join(ROOT, 'services', 'survey_status.py')
)
survey_status = importlib.util.module_from_spec(spec_s)
sys.modules['services.survey_status'] = survey_status
spec_s.loader.exec_module(survey_status)

assert_true("get_imports_per_survey exists",
            hasattr(survey_status, 'get_imports_per_survey'))
assert_true("render_imports_per_survey_markdown exists",
            hasattr(survey_status, 'render_imports_per_survey_markdown'))


# with a stub state without db
class StubState:
    def __init__(self, **kw):
        self.df = kw.get('df')
        self.config = kw.get('config')
        self.db = kw.get('db')
        self.import_id = kw.get('import_id')
        self.analysis_results = kw.get('analysis_results', {})
        self.themes = kw.get('themes', {})

assert_eq("get_imports_per_survey without db -> {}",
          survey_status.get_imports_per_survey(StubState()), {})

# with a stub db
class StubDb:
    def count_imports_per_survey(self):
        return {'survey_a': 5, 'survey_b': 2}
    def _connection(self):
        # not relevant for _get_recent_activity here
        from contextlib import contextmanager
        @contextmanager
        def cm():
            yield None
        return cm()

state_with_db = StubState(db=StubDb())
counts = survey_status.get_imports_per_survey(state_with_db)
assert_eq("counts survey_a", counts.get('survey_a'), 5)
assert_eq("counts survey_b", counts.get('survey_b'), 2)

md = survey_status.render_imports_per_survey_markdown(state_with_db)
assert_in("Markdown enthaelt 'Imports per survey'",
          "Imports per survey", md)
assert_in("Markdown enthaelt survey_a", "survey_a", md)
# title from the registry
assert_in("Markdown enthaelt Title 'Beispielumfrage'",
          "Beispielumfrage", md)
# count
assert_in("Markdown enthaelt count '5'", "**5**", md)


# ==========================================================================
# 6. app.py: interface component
# ==========================================================================

print()
print("=" * 60)
print("6. app.py: survey_imports_md present")
print("=" * 60)

APP_PY = os.path.join(ROOT, 'app.py')
ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)

with open(APP_PY, encoding='utf-8') as f:
    app_content = f.read()

assert_in("survey_imports_md interface component",
          "survey_imports_md", app_content)
assert_in("render_imports_per_survey_markdown is called",
          "render_imports_per_survey_markdown(state)", app_content)


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
