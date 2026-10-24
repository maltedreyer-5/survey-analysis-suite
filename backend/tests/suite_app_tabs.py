# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the Overview and Config assistant tabs of app.py.

Verifies:
1. app.py compiles cleanly
2. the two tabs are defined
3. the service imports (config_io, config_assistant, survey_status) are present
4. the click handler connections are present (generate, apply all, restore)
5. the existing wrappers are unchanged (smoke test)

Run: python tests/suite_app_tabs.py
"""

import sys
import os
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


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')
APP_PY = os.path.join(ROOT, 'app.py')


# ==========================================================================
# 1. Compile-Check
# ==========================================================================

print("=" * 60)
print("1. Compile-Check")
print("=" * 60)

ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)


# ==========================================================================
# 2. Neue Service-Imports
# ==========================================================================

print()
print("=" * 60)
print("2. Service imports")
print("=" * 60)

with open(APP_PY, encoding='utf-8') as f:
    content = f.read()

required_imports = [
    'config_io as svc_config_io',
    'config_assistant as svc_config_assistant',
    'survey_status as svc_survey_status',
]

for req in required_imports:
    assert_true(f"Import: {req}", req in content)


# ==========================================================================
# 3. New tabs present
# ==========================================================================

print()
print("=" * 60)
print("3. New tabs present")
print("=" * 60)

# heuristic: tab labels
assert_in("Overview tab", '"🏠 Overview"', content)
assert_in("Config assistant tab", '"🛠️ Config assistant"', content)


# ==========================================================================
# 4. Service calls in the new tabs
# ==========================================================================

print()
print("=" * 60)
print("4. Service calls in the tabs")
print("=" * 60)

required_calls = [
    'svc_survey_status.render_status_markdown',
    'svc_survey_status.get_workflow_status',
    'svc_config_assistant.suggest_llm_context',
    'svc_config_assistant.suggest_segmentation_dimensions',
    'svc_config_assistant.suggest_numeric_fields',
    'svc_config_assistant.suggest_schema_overrides',
    'svc_config_assistant.generate_all_suggestions',
    'svc_config_assistant.apply_suggestions',
    'svc_config_assistant.Suggestion',
    'svc_config_io.load_active_config',
    'svc_config_io.write_active_config_with_snapshot',
    'svc_config_io.list_snapshots',
    'svc_config_io.restore_snapshot',
]

for call in required_calls:
    assert_true(f"call: {call}", call in content)


# ==========================================================================
# 5. Interface elements present
# ==========================================================================

print()
print("=" * 60)
print("5. Interface elements in the tabs")
print("=" * 60)

# Overview
assert_in("overview_status_md (Markdown)", "overview_status_md", content)
assert_in("overview_refresh_btn", "overview_refresh_btn", content)

# Config assistant
ui_elements = [
    "cfg_assist_category",
    "cfg_assist_scope",
    "cfg_assist_generate_btn",
    "cfg_assist_status",
    "cfg_assist_preview",
    "cfg_assist_state",
    "cfg_assist_apply_all_btn",
    "cfg_assist_clear_btn",
    "cfg_assist_snapshots_md",
    "cfg_assist_refresh_snaps_btn",
    "cfg_assist_restore_btn",
    "cfg_assist_restore_status",
]

for ui in ui_elements:
    assert_in(f"UI-Element: {ui}", ui, content)


# ==========================================================================
# 6. AST: validity of the new click handler connections
# ==========================================================================

print()
print("=" * 60)
print("6. AST: Click-Handler-Verknuepfungen")
print("=" * 60)

tree = ast.parse(content)

# at least 4 .click(...) calls should occur near the new interface variables
click_count = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'click':
            click_count += 1

assert_true(f"at least 30 .click() calls (was: {click_count})",
            click_count >= 30)


# ==========================================================================
# 7. Existing wrappers still there
# ==========================================================================

print()
print("=" * 60)
print("7. Existing wrappers unchanged")
print("=" * 60)

l11_calls = [
    'svc_translations.load_translations_from_db',
    'svc_pipeline.run_full_pipeline',
    'svc_analysis.analyze_all_questions',
    'svc_workbench.prepare_all_freetext',
    'svc_publish.build_all',
    'svc_themes.add_theme',
    '_gradio_progress_callback',
]

for call in l11_calls:
    assert_true(f"service wrapper still present: {call}", call in content)


# ==========================================================================
# 8. Order: Overview is the first tab, Config assistant before Import
# ==========================================================================

print()
print("=" * 60)
print("8. Tab order")
print("=" * 60)

idx_overview = content.find('"🏠 Overview"')
idx_assistant  = content.find('"🛠️ Config assistant"')
idx_import     = content.find('"📥 Import"')

assert_true("Overview tab found", idx_overview > 0)
assert_true("Config assistant tab found", idx_assistant > 0)
assert_true("Import tab found", idx_import > 0)

assert_true("Overview BEFORE Config assistant",
            idx_overview < idx_assistant)
assert_true("Config assistant BEFORE Import",
            idx_assistant < idx_import)


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
