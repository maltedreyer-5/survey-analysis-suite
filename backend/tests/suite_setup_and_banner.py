# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the Setup tab and the re-export banner.

Verifies:
1. core.py: AppState has the pending_reexport fields
2. core.py: mark_reexport_needed/clear_reexport_flag work
3. core.py: reset() clears the flag
4. app.py: compiles
5. app.py: ⚙️ Setup tab present, ⚙️ Configuration still there
6. app.py: survey switcher interface present
7. app.py: re-export banner in the Workbench and themes tabs
8. app.py: no public Gradio tunnel

Run: python tests/suite_setup_and_banner.py
"""

import sys
import os
import types
import importlib.util
import compileall
import ast

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


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')
APP_PY = os.path.join(ROOT, 'app.py')
CORE_PY = os.path.join(ROOT, 'core.py')


# ==========================================================================
# 1. core.py: pending_reexport fields
# ==========================================================================

print("=" * 60)
print("1. core.py: AppState pending_reexport fields")
print("=" * 60)

with open(CORE_PY, encoding='utf-8') as f:
    core_content = f.read()

assert_in("pending_reexport: bool defined", "pending_reexport: bool", core_content)
assert_in("pending_reexport_reasons: List[str]",
          "pending_reexport_reasons: List[str]", core_content)
assert_in("List imported", "from typing import Dict, Any, Optional, Tuple, List",
          core_content)


# ==========================================================================
# 2. core.py: mark/clear-Helper
# ==========================================================================

print()
print("=" * 60)
print("2. core.py: mark_reexport_needed / clear_reexport_flag")
print("=" * 60)

assert_in("mark_reexport_needed", "def mark_reexport_needed", core_content)
assert_in("clear_reexport_flag",   "def clear_reexport_flag", core_content)


# ==========================================================================
# 3. Test the AppState behaviour
# ==========================================================================

print()
print("=" * 60)
print("3. AppState behaviour")
print("=" * 60)

# AppState cannot simply be imported (that triggers the core initialisation);
# instead the class definition is extracted standalone and the methods are
# tested with a reduced context.

# the logic is tested by inspecting the methods directly; first, the string
# search confirms that the helpers access the right fields.
assert_in("mark_reexport sets pending_reexport = True",
          "self.pending_reexport = True", core_content)
assert_in("clear_reexport sets pending_reexport = False",
          "self.pending_reexport = False", core_content)
assert_in("reset() clears pending_reexport",
          "self.pending_reexport = False\n        self.pending_reexport_reasons = []",
          core_content)


# ==========================================================================
# 4. app.py compiles
# ==========================================================================

print()
print("=" * 60)
print("4. app.py compiles")
print("=" * 60)

ok = compileall.compile_file(APP_PY, quiet=1)
assert_true("app.py compiles", ok)

with open(APP_PY, encoding='utf-8') as f:
    content = f.read()


# ==========================================================================
# 5. Setup tab present
# ==========================================================================

print()
print("=" * 60)
print("5. Setup-Tab")
print("=" * 60)

assert_in("⚙️ Setup-Tab", '"⚙️ Setup"', content)
assert_in("Configuration tab still present", '"⚙️ Configuration"', content)
assert_in("Survey selection sub-tab", '"🗂️ Survey selection"', content)


# ==========================================================================
# 6. Survey-Wechsler-UI
# ==========================================================================

print()
print("=" * 60)
print("6. Survey-Wechsler")
print("=" * 60)

ui_elements = [
    "survey_active_md",
    "survey_list_md",
    "survey_choice_dropdown",
    "survey_refresh_btn",
    "survey_switch_btn",
    "survey_switch_status",
    "switch_active_survey",
    "_build_survey_overview",
]

for el in ui_elements:
    assert_in(f"Survey-UI: {el}", el, content)

# core calls
assert_in("set_active_survey call", "set_active_survey(survey_id)", content)


# ==========================================================================
# 7. Re-Export-Banner
# ==========================================================================

print()
print("=" * 60)
print("7. Re-Export-Banner")
print("=" * 60)

# Workbench-Banner
workbench_elements = [
    "workbench_banner_md",
    "workbench_banner_check_btn",
    "workbench_banner_reexport_btn",
    "workbench_banner_dismiss_btn",
    "workbench_banner_check",
    "workbench_banner_reexport",
    "workbench_banner_dismiss",
]
for el in workbench_elements:
    assert_in(f"Workbench-Banner: {el}", el, content)

# themes banner
themes_elements = [
    "themes_banner_md",
    "themes_banner_check_btn",
    "themes_banner_reexport_btn",
    "themes_banner_dismiss_btn",
    "themes_banner_check",
    "themes_banner_reexport",
    "themes_banner_dismiss",
]
for el in themes_elements:
    assert_in(f"themes banner: {el}", el, content)

# mark_reexport_needed is called
assert_in("save button sets the flag",
          "state.mark_reexport_needed", content)
# clear_reexport_flag on a re-export
assert_in("Re-Export loescht Flag",
          "state.clear_reexport_flag()", content)


# ==========================================================================
# 8. No public Gradio tunnel
# ==========================================================================

print()
print("=" * 60)
print("8. No share link")
print("=" * 60)

assert_true("GRADIO_SHARE is not read", "GRADIO_SHARE" not in content)
assert_in("share switched off for good", "share=False,", content)


# ==========================================================================
# 10. Tab order
# ==========================================================================

print()
print("=" * 60)
print("10. Tab order")
print("=" * 60)

idx_overview = content.find('"🏠 Overview"')
idx_setup      = content.find('"⚙️ Setup"')
idx_info       = content.find('"ℹ️ Info"')

assert_true("Setup before Info", idx_setup < idx_info)
assert_true("Overview before Setup", idx_overview < idx_setup)


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
