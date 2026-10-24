# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Runs the script suites backend/tests/suite_*.py and sums up their TOTAL lines.

A suite passes when it exits with 0 and its last TOTAL line reports no failure.
"""
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
ok = fail = 0
bad = []
for suite in sorted((BACKEND / "tests").glob("suite_*.py")):
    proc = subprocess.run([sys.executable, str(suite)], cwd=BACKEND, capture_output=True, text=True, timeout=600)
    totals = re.findall(r"TOTAL: (\d+) OK, (\d+) FAIL", proc.stdout + proc.stderr)
    if totals:
        ok += int(totals[-1][0]); fail += int(totals[-1][1])
    if proc.returncode != 0 or not totals or int(totals[-1][1]):
        bad.append(f"{suite.name} (exit {proc.returncode}, {totals[-1] if totals else 'no TOTAL line'})")
print(f"suites: {ok} OK, {fail} FAIL")
for b in bad:
    print(f"  failed: {b}")
sys.exit(1 if bad else 0)
