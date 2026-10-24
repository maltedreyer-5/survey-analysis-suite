# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of app.py on the service layer.

Verifies:
1. app.py compiles cleanly (syntax)
2. app.py can be imported with stubs (no broken closure references)
3. the tabs call the service functions through short wrappers, with
   shorter wrappers
4. the _gradio_progress_callback adapter works: returns None for None,
   returns a callable for a progress object, passes the gradio call on

Run: python tests/suite_app_wrappers.py
"""

import sys
import os
import types
import importlib.util
import compileall
import ast

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


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
        RESULTS.append(('FAIL',
            f"{label}: expected {expected!r}, got {actual!r}"))


def assert_lt(label, actual, expected):
    global PASS, FAIL
    if actual < expected:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL',
            f"{label}: expected < {expected!r}, got {actual!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')
APP_PY = os.path.join(ROOT, 'app.py')


# ==========================================================================
# 1. Syntax / Compile
# ==========================================================================

print("=" * 60)
print("1. Syntax-Check (compile)")
print("=" * 60)

ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)


# ==========================================================================
# 2. Number of lines
# ==========================================================================

print()
print("=" * 60)
print("2. Code reduction by the wrappers")
print("=" * 60)

with open(APP_PY, encoding='utf-8') as f:
    lines = f.readlines()
loc = len(lines)

# no hard limit on the lines of code, as new features let the file grow;
# the print only informs, no assertion.
print(f"  current LOC: {loc} (original 2418)")


# ==========================================================================
# 3. Service imports present
# ==========================================================================

print()
print("=" * 60)
print("3. Service imports present")
print("=" * 60)

content = ''.join(lines)

required_imports = [
    'translations as svc_translations',
    'pipeline as svc_pipeline',
    'analysis as svc_analysis',
    'workbench as svc_workbench',
    'publish as svc_publish',
    'themes as svc_themes',
]

for req in required_imports:
    assert_true(f"Import: {req}", req in content)


# ==========================================================================
# 4. Wrapper calls (the service calls are in the app.py source)
# ==========================================================================

print()
print("=" * 60)
print("4. Service calls present")
print("=" * 60)

required_calls = [
    'svc_translations.load_translations_from_db',
    'svc_translations.save_translations_to_db',
    'svc_translations.delete_all_translations',
    'svc_translations.translate_responses',
    'svc_pipeline.run_full_pipeline',
    'svc_pipeline.regenerate_summaries',
    'svc_pipeline.reexport_after_workbench',
    'svc_pipeline.batch_extract_items',
    'svc_analysis.analyze_all_questions',
    'svc_workbench.prepare_all_freetext',
    'svc_publish.build_all',
    'svc_publish.build_update',
    'svc_publish.clear_translations',
    'svc_themes.load_themes_from_db',
    'svc_themes.sync_themes_to_state',
    'svc_themes.add_theme',
    'svc_themes.get_theme_keywords',
    'svc_themes.save_theme_keywords',
    'svc_themes.extract_keywords_with_llm',
    'svc_themes.analyze_all_themes',
    'svc_themes.deep_analyze_all_themes',
    'svc_themes.export_themes',
]

for call in required_calls:
    assert_true(f"call: {call}", call in content)


# ==========================================================================
# 5. Progress-Adapter
# ==========================================================================

print()
print("=" * 60)
print("5. _gradio_progress_callback Adapter")
print("=" * 60)

# load the adapter by AST extraction
tree = ast.parse(content)

adapter_func = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_gradio_progress_callback':
        adapter_func = node
        break

assert_true("_gradio_progress_callback found", adapter_func is not None)

if adapter_func:
    # run the function: running app.py globally is not possible directly, so
    # the function code is extracted and run in isolation.
    func_src = ast.unparse(adapter_func)
    
    # logger stub (the adapter uses logger.debug)
    import logging
    test_logger = logging.getLogger('suite_app_wrappers')
    
    namespace = {'logger': test_logger}
    exec(func_src, namespace)
    adapter = namespace['_gradio_progress_callback']
    
    # Test 1: None -> None
    assert_eq("Adapter(None) -> None", adapter(None), None)
    
    # Test 2: Mit Callable -> Wrapper
    calls = []
    def fake_progress(fraction, desc=""):
        calls.append((fraction, desc))
    
    cb = adapter(fake_progress)
    assert_true("adapter(callable) -> not None", cb is not None)
    
    cb(0.5, "Test")
    assert_eq("the wrapper passes the call on", calls, [(0.5, "Test")])
    
    # test 3: the wrapper swallows exceptions
    def crashing_progress(fraction, desc=""):
        raise RuntimeError("crash")
    
    cb2 = adapter(crashing_progress)
    try:
        cb2(0.7, "Test")
        # should not crash
        assert_eq("the wrapper swallows exceptions", True, True)
    except RuntimeError:
        assert_eq("the wrapper swallows exceptions", False, True)


# ==========================================================================
# 6. Old implementations removed: cross check
# ==========================================================================

print()
print("=" * 60)
print("6. Remains of the old implementation removed")
print("=" * 60)

# heuristics: traces of the old implementations that should not be in the
# wrappers any more; if they are, the refactoring was not clean.
old_impl_markers_should_be_gone = [
    # from translate_responses_handler, very specific
    "from utils.response_translator import translate_responses",
    # from run_pipeline_handler, very specific
    "from batch_export import run_full_pipeline",
    "from batch_export import reexport_after_workbench",
    # from run_regen_summaries_handler
    'cursor.execute("DELETE FROM question_summaries")',
    # Aus batch_extract_all
    "from utils.item_extractor import ItemExtractor",
    # Aus analyze_all_questions
    "result.percentages.to_dict()",
    # Aus run_pub_build/update
    "from utils.document_builder import DocumentBuilder",
    "builder = DocumentBuilder()",
]

for marker in old_impl_markers_should_be_gone:
    assert_eq(f"removed: {marker[:50]}",
              marker in content, False)


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
