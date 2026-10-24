# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the service layer.

Verifies:
1. all service modules can be imported
2. the service functions can be called with a minimal stub state
3. edge cases (no df, no config, no db) return clean error strings
4. the service modules contain NO gradio imports (interface separation)
5. function signatures: all service functions take `state` as their first argument

Run: python tests/suite_services.py
"""

import sys
import os
import types
import importlib.util
import inspect

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


# ==========================================================================
# 1. Service-Module importierbar
# ==========================================================================

print("=" * 60)
print("1. Imports")
print("=" * 60)

# utils-Stubs
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

# stub translation (themes does not use it, but to be safe)
tm_stub = types.ModuleType('utils.translation')
class _TM:
    def t(self, key, lang='de', **kw): return key
    def get(self, key, lang='de'): return key
    def translate_common_label(self, label, lang='de'): return label
tm_stub.get_translation_manager = lambda: _TM()
sys.modules['utils.translation'] = tm_stub


# Services laden
HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')

services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


service_translations = _load(
    'services.translations',
    os.path.join(ROOT, 'services', 'translations.py')
)
service_pipeline = _load(
    'services.pipeline',
    os.path.join(ROOT, 'services', 'pipeline.py')
)
service_analysis = _load(
    'services.analysis',
    os.path.join(ROOT, 'services', 'analysis.py')
)
service_workbench = _load(
    'services.workbench',
    os.path.join(ROOT, 'services', 'workbench.py')
)
service_publish = _load(
    'services.publish',
    os.path.join(ROOT, 'services', 'publish.py')
)
service_themes = _load(
    'services.themes',
    os.path.join(ROOT, 'services', 'themes.py')
)

assert_true("services.translations loaded", service_translations is not None)
assert_true("services.pipeline loaded",     service_pipeline is not None)
assert_true("services.analysis loaded",     service_analysis is not None)
assert_true("services.workbench loaded",    service_workbench is not None)
assert_true("services.publish loaded",      service_publish is not None)
assert_true("services.themes loaded",       service_themes is not None)


# ==========================================================================
# 2. No gradio imports in the services
# ==========================================================================

print()
print("=" * 60)
print("2. No gradio imports in the services (interface separation)")
print("=" * 60)

for service_name in ['translations', 'pipeline', 'analysis', 'workbench',
                      'publish', 'themes', '__init__']:
    path = os.path.join(ROOT, 'services', f'{service_name}.py')
    with open(path, encoding='utf-8') as f:
        content = f.read()
    # find real code imports; docstrings/comments are filtered by parsing:
    # does the identifier really occur as ast.Name or ast.Attribute?
    import ast as _ast
    tree = _ast.parse(content)
    
    has_import = False
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            module = node.module if isinstance(node, _ast.ImportFrom) else None
            if module == 'gradio':
                has_import = True
            for alias in node.names:
                if alias.name == 'gradio':
                    has_import = True
        if isinstance(node, _ast.Attribute):
            if isinstance(node.value, _ast.Name) and node.value.id == 'gr':
                has_import = True
    
    assert_eq(f"services/{service_name}.py: no gradio import",
              has_import, False)


# ==========================================================================
# 3. Stub state for service calls
# ==========================================================================

print()
print("=" * 60)
print("3. Calling service functions with a stub state")
print("=" * 60)


class StubState:
    """Minimal state stub for service tests."""
    def __init__(self, **kw):
        self.df = kw.get('df')
        self.db = kw.get('db')
        self.config = kw.get('config')
        self.import_id = kw.get('import_id')
        self.project_id = kw.get('project_id')
        self.analysis_results = kw.get('analysis_results', {})
        self.themes = kw.get('themes', {})


# 3.1 translations.load_translations_from_db: empty state -> empty DataFrame
state_empty = StubState()
df = service_translations.load_translations_from_db(state_empty)
assert_true("translations.load: empty state -> empty DataFrame",
            len(df) == 0)
assert_eq("translations.load: expected columns",
          list(df.columns),
          ["ID", "Original", "Language", "Translation"])


# 3.2 translations.save: empty state -> error string
result = service_translations.save_translations_to_db(state_empty, None)
assert_in("translations.save: without db -> error", "❌", result)


# 3.3 translations.delete: empty state -> error string
status, df = service_translations.delete_all_translations(state_empty)
assert_in("translations.delete: without db -> error", "❌", status)


# 3.4 translations.translate_responses: without df -> error
status, df = service_translations.translate_responses(state_empty, 'de')
assert_in("translations.translate: without df -> error",
          "❌ No data imported", status)


# 3.5 translations.translate_responses: with df but without config -> error
import pandas as pd
state_with_df = StubState(df=pd.DataFrame({'a': [1, 2]}))
status, df = service_translations.translate_responses(state_with_df, 'de')
assert_in("translations.translate: without config -> error",
          "❌ No configuration", status)


# 3.6 pipeline.batch_extract_items: without df -> error
status, stats = service_pipeline.batch_extract_items(state_empty)
assert_in("pipeline.batch_extract: without df -> error",
          "❌ No data loaded", status)


# 3.7 pipeline.batch_extract_items: with df, without db -> error
status, stats = service_pipeline.batch_extract_items(state_with_df)
assert_in("pipeline.batch_extract: without db -> error",
          "❌ No database connection", status)


# 3.8 pipeline.batch_extract_items: with df + db, without config -> error
class StubDb:
    def get_items_for_question(self, *a, **kw): return []
    def delete_items(self, *a, **kw): pass
    def save_response_item(self, *a, **kw): pass

state_no_config = StubState(df=pd.DataFrame({'a': [1, 2]}),
                              db=StubDb(), import_id=1)
status, stats = service_pipeline.batch_extract_items(state_no_config)
assert_in("pipeline.batch_extract: without config -> error",
          "❌ No configuration", status)


# 3.9 analysis.analyze_all_questions: without df -> error
status, choices = service_analysis.analyze_all_questions(state_empty)
assert_in("analysis.analyze_all: without df -> error",
          "❌ No data", status)
assert_eq("analysis.analyze_all: choices leer", choices, [])


# 3.10 analysis.analyze_all_questions: without config -> error
status, choices = service_analysis.analyze_all_questions(state_with_df)
assert_in("analysis.analyze_all: without config -> error",
          "❌ No configuration", status)


# 3.11 workbench.get_freetext_questions: without config -> []
qs = service_workbench.get_freetext_questions(state_empty)
assert_eq("workbench.get_freetext_questions: without config -> []", qs, [])


# 3.12 workbench.get_freetext_questions: with config
state_cfg = StubState(config={
    'question_groups': {
        'q1': {'handler': 'freetext'},
        'q2': {'handler': 'single_choice'},
        'q3': {'handler': 'freetext'},
    }
})
qs = service_workbench.get_freetext_questions(state_cfg)
assert_eq("workbench.get_freetext_questions: 2 Freitext", sorted(qs), ['q1', 'q3'])


# 3.13 workbench.prepare_all_freetext: without df -> error
status = service_workbench.prepare_all_freetext(state_empty)
assert_in("workbench.prepare: without df -> error",
          "❌ No data", status)


# 3.14 workbench.prepare_all_freetext: without config -> error
status = service_workbench.prepare_all_freetext(state_with_df)
assert_in("workbench.prepare: without config -> error",
          "❌ No configuration", status)


# 3.15 workbench.prepare_all_freetext: without free-text questions -> error
state_no_ft = StubState(df=pd.DataFrame({'a': [1]}),
                          config={'question_groups':
                                   {'q1': {'handler': 'single_choice'}}})
status = service_workbench.prepare_all_freetext(state_no_ft)
assert_in("workbench.prepare: no free-text questions",
          "❌ No free-text questions", status)


# 3.16 themes.add_theme: without id/label -> error
class StubAnalyzer:
    def __init__(self):
        self.themes = {}
    def add_theme(self, tid, label_source, labels=None):
        from types import SimpleNamespace
        self.themes[tid] = SimpleNamespace(
            id=tid, label=dict(labels or {'de': label_source}),
            question_keywords=[], answer_keywords=[],
            questions=[], stats={},
        )

ta = StubAnalyzer()
ok, status, data, choices = service_themes.add_theme(
    state_empty, ta, '', 'kein label'
)
assert_eq("themes.add_theme: without id -> failure", ok, False)
assert_in("themes.add_theme: without id -> status note",
          "Give an ID and a label", status)


# 3.17 themes.add_theme: with id+label -> success
ok, status, data, choices = service_themes.add_theme(
    state_empty, ta, 'tech', 'Technologie'
)
assert_eq("themes.add_theme: success", ok, True)
assert_in("themes.add_theme: created status", "added", status)
assert_in("themes.add_theme: in choices", "tech", choices)


# 3.18 themes.add_theme: duplicate id -> failure
ok, status, _, _ = service_themes.add_theme(
    state_empty, ta, 'tech', 'Technologie'
)
assert_eq("themes.add_theme: duplicate id -> failure", ok, False)
assert_in("themes.add_theme: 'already exists'", "already exists", status)


# 3.19 themes.get_theme_keywords: existing theme
kw = service_themes.get_theme_keywords(ta, 'tech')
assert_eq("themes.get_keywords: empty theme -> ''", kw, '')


# 3.20 themes.get_theme_keywords: unknown theme
kw = service_themes.get_theme_keywords(ta, 'unknown')
assert_eq("themes.get_keywords: unknown -> ''", kw, '')


# 3.21 themes.save_theme_keywords: valid theme
data, status = service_themes.save_theme_keywords(
    state_empty, ta, 'tech', 'cloud, ki, security'
)
assert_in("themes.save_keywords: 3 keywords stored", "3 keywords", status)
assert_eq("themes.save_keywords: keywords set",
          ta.themes['tech'].question_keywords, ['cloud', 'ki', 'security'])


# 3.22 themes.analyze_all_themes: without config -> error
data, status = service_themes.analyze_all_themes(state_empty, ta)
assert_in("themes.analyze_all: without config -> error",
          "No configuration", status)


# 3.23 themes.deep_analyze_all_themes: without config -> error
data, status = service_themes.deep_analyze_all_themes(state_empty, ta)
assert_in("themes.deep_analyze_all: without config -> error",
          "No configuration", status)


# 3.24 themes.sync: empty state, empty analyzer -> state.themes empty
ta_empty = StubAnalyzer()
state_for_sync = StubState()
service_themes.sync_themes_to_state(state_for_sync, ta_empty)
assert_eq("themes.sync: empty analyzer", state_for_sync.themes, {})


# 3.25 themes.sync: one theme -> state.themes has an entry
service_themes.sync_themes_to_state(state_for_sync, ta)  # ta has 'tech'
assert_in("themes.sync: 'tech' in state.themes", 'tech', state_for_sync.themes)


# ==========================================================================
# 4. Check the function signatures
# ==========================================================================

print()
print("=" * 60)
print("4. Signature convention: state as the first argument")
print("=" * 60)

# service functions that take state as their first argument; some helpers
# (like get_theme_keywords) take the analyzer instead, which is fine, as
# they need no state.
state_first_required = [
    (service_translations, 'load_translations_from_db'),
    (service_translations, 'save_translations_to_db'),
    (service_translations, 'delete_all_translations'),
    (service_translations, 'translate_responses'),
    (service_pipeline, 'run_full_pipeline'),
    (service_pipeline, 'regenerate_summaries'),
    (service_pipeline, 'reexport_after_workbench'),
    (service_pipeline, 'batch_extract_items'),
    (service_analysis, 'analyze_all_questions'),
    (service_workbench, 'get_freetext_questions'),
    (service_workbench, 'prepare_all_freetext'),
    (service_publish, 'build_all'),
    (service_publish, 'build_update'),
    (service_publish, 'clear_translations'),
    (service_themes, 'load_themes_from_db'),
    (service_themes, 'sync_themes_to_state'),
    (service_themes, 'add_theme'),
    (service_themes, 'save_theme_keywords'),
    (service_themes, 'extract_keywords_with_llm'),
    (service_themes, 'analyze_all_themes'),
    (service_themes, 'deep_analyze_all_themes'),
]

for mod, fname in state_first_required:
    fn = getattr(mod, fname, None)
    assert_true(f"{mod.__name__}.{fname} exists", fn is not None)
    if fn:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert_true(
            f"{mod.__name__}.{fname}: first argument = 'state'",
            len(params) > 0 and params[0] == 'state'
        )


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
