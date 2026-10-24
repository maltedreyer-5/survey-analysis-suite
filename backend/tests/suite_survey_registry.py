# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the multi-survey directory structure.

Verifies:
1. SurveyRegistry.discover() finds valid surveys
2. the required fields of survey.yaml are checked
3. discovery skips invalid entries gracefully
4. set_active() / get_active() work as expected
5. load_questions/documents/diagram_labels load the right file of the
   active survey
7. sorting and path conventions
8. the example survey ai_in_higher_education_de is syntactically correct and
   loads completely

Run: python tests/suite_survey_registry.py
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


if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')

# Survey-Registry laden
_load('utils.survey_registry',
      os.path.join(ROOT, 'utils', 'survey_registry.py'))

from utils.survey_registry import (
    SurveyRegistry, SurveyMeta,
    SURVEY_META_FILE, QUESTIONS_FILE,
    DOCUMENTS_FILE, DIAGRAM_LABELS_FILE,
)
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
        RESULTS.append(('FAIL', f"{label}: {item!r} not in {container!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


def assert_raises(label, exc_type, callable_):
    global PASS, FAIL
    try:
        callable_()
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: no exception, expected {exc_type.__name__}"))
    except exc_type:
        PASS += 1
        RESULTS.append(('OK', label))
    except Exception as e:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected {exc_type.__name__}, kam {type(e).__name__}: {e}"))


def make_survey(parent: Path, sid: str, title='Test', has_documents=False,
                has_diagram_labels=False, has_meta=True, has_questions=True):
    """Helper: creates a test survey under parent/sid/."""
    d = parent / sid
    d.mkdir(parents=True, exist_ok=True)
    
    if has_meta:
        meta = {
            'id': sid,
            'title': {'de': title},
            'description': {'de': f'Beschreibung von {sid}'},
            'version': '1.0',
            'languages': ['de'],
        }
        with (d / SURVEY_META_FILE).open('w', encoding='utf-8') as f:
            yaml.safe_dump(meta, f, allow_unicode=True)
    
    if has_questions:
        q = {
            'settings': {'segmentation': {'dimensions': []}},
            'question_groups': {
                f'q1_{sid}': {
                    'id': f'q1_{sid}',
                    'columns': ['Test-Spalte'],
                    'type': 'single_choice',
                    'handler': 'single_choice',
                },
            },
        }
        with (d / QUESTIONS_FILE).open('w', encoding='utf-8') as f:
            yaml.safe_dump(q, f, allow_unicode=True)
    
    if has_documents:
        with (d / DOCUMENTS_FILE).open('w', encoding='utf-8') as f:
            yaml.safe_dump({'docs': {}}, f)
    
    if has_diagram_labels:
        with (d / DIAGRAM_LABELS_FILE).open('w', encoding='utf-8') as f:
            yaml.safe_dump({'labels': {}}, f)
    
    return d


# ==========================================================================
# 1. discover finds valid surveys
# ==========================================================================

print("=" * 60)
print("1. Discovery — gueltige Surveys")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    
    make_survey(tmp, 'survey_a', title='Survey A')
    make_survey(tmp, 'survey_b', title='Survey B', has_documents=True)
    make_survey(tmp, 'survey_c', title='Survey C', has_diagram_labels=True)
    
    reg = SurveyRegistry(tmp)
    found = reg.discover()
    
    assert_eq("3 Surveys discovert", len(found), 3)
    ids = sorted(s.id for s in found)
    assert_eq("ids sorted", ids, ['survey_a', 'survey_b', 'survey_c'])
    
    # the paths are set correctly
    a = reg.get_survey('survey_a')
    assert_true("survey_a hat questions_path", a.questions_path is not None)
    assert_eq("survey_a questions_path correct",
              a.questions_path.name, QUESTIONS_FILE)
    
    # optional files
    b = reg.get_survey('survey_b')
    assert_true("survey_b hat documents_path", b.has_documents())
    assert_true("survey_a has NO documents_path",
                not a.has_documents())
    
    c = reg.get_survey('survey_c')
    assert_true("survey_c hat diagram_labels", c.has_diagram_labels())


# ==========================================================================
# 2. survey.yaml-Pflichtfelder
# ==========================================================================

print()
print("=" * 60)
print("2. Validation of the required fields")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    
    # valid
    make_survey(tmp, 'good')
    # no survey.yaml
    d = tmp / 'no_meta'
    d.mkdir()
    (d / QUESTIONS_FILE).write_text('{}', encoding='utf-8')
    # survey.yaml without id
    d2 = tmp / 'no_id'
    d2.mkdir()
    with (d2 / SURVEY_META_FILE).open('w', encoding='utf-8') as f:
        yaml.safe_dump({'title': {'de': 'No ID'}}, f)
    (d2 / QUESTIONS_FILE).write_text('{}', encoding='utf-8')
    # with survey.yaml but without questions.yaml
    d3 = tmp / 'no_questions'
    d3.mkdir()
    with (d3 / SURVEY_META_FILE).open('w', encoding='utf-8') as f:
        yaml.safe_dump({'id': 'no_questions', 'title': {'de': 'No Q'}}, f)
    
    reg = SurveyRegistry(tmp)
    found = reg.discover()
    
    assert_eq("only 'good' is registered", len(found), 1)
    assert_eq("correct id", found[0].id, 'good')


# ==========================================================================
# 3. Hidden directories are ignored
# ==========================================================================

print()
print("=" * 60)
print("3. Hidden / helper directories")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    
    make_survey(tmp, 'visible')
    make_survey(tmp, '.hidden')        # hidden
    make_survey(tmp, '_private')       # private/Hilfsverzeichnis
    
    reg = SurveyRegistry(tmp)
    found = reg.discover()
    
    assert_eq("only 'visible' registered", len(found), 1)
    assert_eq("ID 'visible'", found[0].id, 'visible')


# ==========================================================================
# 4. set_active / get_active
# ==========================================================================

print()
print("=" * 60)
print("4. Managing the active survey")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    make_survey(tmp, 'a')
    make_survey(tmp, 'b')
    
    reg = SurveyRegistry(tmp)
    reg.discover()
    
    assert_eq("initially: get_active() is None",
              reg.get_active(), None)
    assert_eq("initially: get_active_id() is None",
              reg.get_active_id(), None)
    
    # set
    meta = reg.set_active('a')
    assert_eq("set_active returns SurveyMeta",
              meta.id, 'a')
    assert_eq("get_active() is now 'a'",
              reg.get_active().id, 'a')
    assert_eq("get_active_id() is 'a'",
              reg.get_active_id(), 'a')
    
    # switch
    reg.set_active('b')
    assert_eq("switch to 'b'", reg.get_active_id(), 'b')
    
    # Ungueltiger ID
    assert_raises("set_active with an unknown id -> KeyError",
                  KeyError, lambda: reg.set_active('does_not_exist'))


# ==========================================================================
# 5. load_questions / load_documents / load_diagram_labels
# ==========================================================================

print()
print("=" * 60)
print("5. Loading the configuration of the active survey")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    make_survey(tmp, 'a', has_documents=True, has_diagram_labels=True)
    
    reg = SurveyRegistry(tmp)
    reg.discover()
    reg.set_active('a')
    
    qcfg = reg.load_questions()
    assert_in("questions loaded", 'question_groups', qcfg)
    assert_in("question_groups contain q1_a",
              'q1_a', qcfg['question_groups'])
    
    docs = reg.load_documents()
    assert_in("documents loaded", 'docs', docs)
    
    labels = reg.load_diagram_labels()
    assert_in("diagram_labels loaded", 'labels', labels)
    
    # without an active survey -> ValueError
    reg2 = SurveyRegistry(tmp)
    reg2.discover()
    assert_raises("load_questions without an active survey -> ValueError",
                  ValueError, lambda: reg2.load_questions())
    
    # with an explicit id
    qcfg = reg2.load_questions('a')
    assert_in("an explicit id loads correctly",
              'q1_a', qcfg['question_groups'])


# ==========================================================================
# 6. Optional configurations are None when missing
# ==========================================================================

print()
print("=" * 60)
print("6. Optional configurations")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    make_survey(tmp, 'minimal')  # without documents.yaml/diagram_labels.yaml
    
    reg = SurveyRegistry(tmp)
    reg.discover()
    reg.set_active('minimal')
    
    assert_eq("documents missing -> None",
              reg.load_documents(), None)
    assert_eq("diagram_labels missing -> None",
              reg.load_diagram_labels(), None)


# ==========================================================================
# 8. SurveyMeta helper methods
# ==========================================================================

print()
print("=" * 60)
print("8. SurveyMeta-Helpers")
print("=" * 60)

meta = SurveyMeta(
    id='test',
    title={'de': 'Titel DE', 'en': 'Title EN'},
    description={'de': 'Beschreibung'},
)

assert_eq("get_title('de')", meta.get_title('de'), 'Titel DE')
assert_eq("get_title('en')", meta.get_title('en'), 'Title EN')
assert_eq("get_title('fr') -> fallback DE",
          meta.get_title('fr'), 'Titel DE')

assert_eq("get_description('de')",
          meta.get_description('de'), 'Beschreibung')
assert_eq("get_description('fr') -> fallback DE",
          meta.get_description('fr'), 'Beschreibung')

# without a description -> empty string
meta2 = SurveyMeta(id='x', title={'de': 'X'}, description={})
assert_eq("empty description -> ''",
          meta2.get_description('de'), '')


# ==========================================================================
# 9. survey.yaml with a string title (not a dict)
# ==========================================================================

print()
print("=" * 60)
print("9. survey.yaml with a string instead of a dict")
print("=" * 60)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    d = tmp / 'shortform'
    d.mkdir()
    
    with (d / SURVEY_META_FILE).open('w', encoding='utf-8') as f:
        f.write("id: shortform\ntitle: Kurzform-Survey\ndescription: Mit Strings\n")
    (d / QUESTIONS_FILE).write_text('{}', encoding='utf-8')
    
    reg = SurveyRegistry(tmp)
    reg.discover()
    meta = reg.get_survey('shortform')
    
    assert_true("short-form survey registered", meta is not None)
    if meta:
        assert_eq("title normalised as a dict",
                  meta.get_title('de'), 'Kurzform-Survey')
        assert_eq("description normalised as a dict",
                  meta.get_description('de'), 'Mit Strings')


# ==========================================================================
# 10. The example survey ai_in_higher_education_de is valid (examples/generate.py)
# ==========================================================================

print()
print("=" * 60)
print("10. Beispielumfrage ai_in_higher_education_de")
print("=" * 60)

surveys_dir = Path(ROOT) / 'surveys'
if not surveys_dir.exists():
    RESULTS.append(('FAIL', "no surveys/ directory found"))
    FAIL += 1
else:
    reg = SurveyRegistry(surveys_dir)
    reg.discover()
    
    example = reg.get_survey('ai_in_higher_education_de')
    assert_true("example survey registered", example is not None)
    
    if example:
        assert_eq("example survey: id correct", example.id, 'ai_in_higher_education_de')
        assert_in("example survey: title (DE)", 'KI', example.get_title('de'))
        
        # the configuration loads
        reg.set_active('ai_in_higher_education_de')
        qcfg = reg.load_questions()
        assert_in("Beispielumfrage: questions hat question_groups", 'question_groups', qcfg)
        
        # one number question (age); the other types occur once each
        numeric_count = sum(1 for q in qcfg['question_groups'].values() if q.get('handler') == 'numeric')
        assert_eq("Beispielumfrage: 1 Zahlenfrage", numeric_count, 1)
        
        llm_ctx = qcfg.get('settings', {}).get('llm_context', '')
        assert_true("example survey: llm_context set", len(llm_ctx) > 50)


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
