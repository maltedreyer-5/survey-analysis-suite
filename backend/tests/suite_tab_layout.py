# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the tab layout: pipeline tab of its own, analysis tab.

Verifies:
1. app.py compiles cleanly
2. the pipeline tab exists (▶️ Pipeline)
3. the analysis tab exists (🔍 Analysis), the old one does not
4. the pipeline interface elements are in their own tab (not in the import tab)
5. the numeric statistics table in the analysis tab
6. the click handlers of the pipeline buttons are connected
7. tab order: Overview -> Config assistant -> Import -> Pipeline -> ...

Run: python tests/suite_tab_layout.py
"""

import sys
import os
import compileall

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
        RESULTS.append(('FAIL', f"{label}: expected {expected!r}, got {actual!r}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} not in container"))


def assert_not_in(label, item, container):
    global PASS, FAIL
    if item not in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} sollte not in container sein"))


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


with open(APP_PY, encoding='utf-8') as f:
    content = f.read()


# ==========================================================================
# 2. Tab labels: the new ones present, the old ones gone
# ==========================================================================

print()
print("=" * 60)
print("2. Tab-Labels")
print("=" * 60)

assert_in("pipeline tab exists", '"▶️ Pipeline"', content)
assert_in("analysis tab exists", '"🔍 Analysis"', content)
assert_not_in("old analysis tab removed", '"📈 Analyse"', content)


# ==========================================================================
# 3. Pipeline interface in its own tab
# ==========================================================================

print()
print("=" * 60)
print("3. Pipeline interface defined only once")
print("=" * 60)

# these components should be defined exactly once, in the pipeline tab.
pipeline_components = [
    'pipeline_btn',
    'regen_summaries_btn',
    'reexport_btn',
    'pipeline_status',
    'pipeline_output_path',
    'pipeline_item_extraction',
    'pipeline_languages',
]

for comp in pipeline_components:
    # definition pattern: variable = gr.<class>(...) at the start of a line,
    # possibly indented; re.findall keeps 'reexport_btn' apart from
    # 'workbench_banner_reexport_btn'.
    import re as _re
    pattern = _re.compile(r'(?:^|\W)' + _re.escape(comp) + r'\s*=\s*gr\.', _re.MULTILINE)
    matches = pattern.findall(content)
    count = len(matches)
    assert_eq(f"{comp}: exactly 1 definition (was {count})", count, 1)


# ==========================================================================
# 4. Pipeline click handlers connected
# ==========================================================================

print()
print("=" * 60)
print("4. Click handlers connected")
print("=" * 60)

clicks = [
    'pipeline_btn.click(',
    'regen_summaries_btn.click(',
    'reexport_btn.click(',
]

for c in clicks:
    assert_in(f"Click: {c}", c, content)


# ==========================================================================
# 5. numeric stats in the analysis tab
# ==========================================================================

print()
print("=" * 60)
print("5. Numeric statistics table")
print("=" * 60)

assert_in("numeric_stats_table defined", "numeric_stats_table = gr.Dataframe", content)
assert_in("analyze_question_with_numeric_stats wrapper", 
          "analyze_question_with_numeric_stats", content)
# the wrapper extracts numeric_stats from the metadata
assert_in("reads numeric_stats from the metadata",
          "metadata.get('numeric_stats')", content)


# ==========================================================================
# 6. The analysis tab has a read-only note
# ==========================================================================

print()
print("=" * 60)
print("6. Read-only note")
print("=" * 60)

assert_in("read-only note in the analysis tab",
          "Read-only view", content)
assert_in("reference to the Workbench",
          "Workbench", content)


# ==========================================================================
# 7. Pipeline tab: note in the import tab
# ==========================================================================

print()
print("=" * 60)
print("7. Note in the import tab")
print("=" * 60)

# the old pipeline accordion header must not be in the import tab any more
assert_not_in("old pipeline accordion header gone",
              '"🚀 Vollständige Pipeline"', 
              # only out of the import tab, the pipeline tab may contain the header;
              # heuristic: it must not stand before 'TAB: PIPELINE'
              content.split('# ===== TAB: PIPELINE =====')[0])


# ==========================================================================
# 8. Tab order
# ==========================================================================

print()
print("=" * 60)
print("8. Tab order")
print("=" * 60)

idx_overview = content.find('"🏠 Overview"')
idx_assistant  = content.find('"🛠️ Config assistant"')
idx_import     = content.find('"📥 Import"')
idx_pipeline   = content.find('"▶️ Pipeline"')
idx_analysis = content.find('"🔍 Analysis"')
idx_workbench  = content.find('"✏️ Workbench"')

# all present
for label, idx in [
    ('Übersicht', idx_overview),
    ('Konfig-Assistent', idx_assistant),
    ('Import', idx_import),
    ('Pipeline', idx_pipeline),
    ('Auswertung', idx_analysis),
    ('Workbench', idx_workbench),
]:
    assert_true(f"{label} tab found", idx > 0)

# order
assert_true("Overview < Config assistant", idx_overview < idx_assistant)
assert_true("Config assistant < Import",     idx_assistant < idx_import)
assert_true("Import < Pipeline",             idx_import < idx_pipeline)
assert_true("Pipeline < Analysis",         idx_pipeline < idx_analysis)
assert_true("Analysis < Workbench",        idx_analysis < idx_workbench)


# ==========================================================================
# 9. Existing wrappers unchanged
# ==========================================================================

print()
print("=" * 60)
print("9. Existing service calls unchanged")
print("=" * 60)

still_present = [
    'svc_pipeline.run_full_pipeline',
    'svc_pipeline.regenerate_summaries',
    'svc_pipeline.reexport_after_workbench',
    'svc_translations.load_translations_from_db',
    'svc_themes.add_theme',
    'svc_config_assistant.suggest_llm_context',
]

for call in still_present:
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
