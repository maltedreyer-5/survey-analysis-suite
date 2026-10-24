#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
#
# All checks a change must pass ("checked" in CONTRIBUTING.md). Run from anywhere:
#   bash scripts/check.sh
# Needs the Python environment of backend/requirements.txt plus pytest, and Node.js.
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
failed=()
step() { echo; echo "== $1"; }
run() { "$@" || failed+=("$*"); }

step "Python syntax"
run $PY -m compileall -q backend examples dashboard/build_offline.py scripts
step "Import smoke test"
run $PY scripts/import_smoke.py
step "Data and configuration files, runtime state, licence headers"
run $PY scripts/check_files.py
step "pytest"
(cd backend && $PY -m pytest -q tests/) || failed+=("pytest")
step "Script suites"
run $PY scripts/run_suites.py
step "Dashboard tests"
for t in dashboard/tests/*.test.js; do run node "$t"; done
step "Documentation links"
run $PY scripts/check_doc_links.py

echo
if [ ${#failed[@]} -eq 0 ]; then echo "ALL CHECKS PASSED"; exit 0; fi
echo "FAILED:"; printf '  %s\n' "${failed[@]}"; exit 1
