# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Imports every module of the backend (without starting the web interface)."""
import importlib
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
# keep the database and outputs of this check out of the working tree
tmp = tempfile.mkdtemp()
os.environ.setdefault("SURVEY_DATA_DIR", tmp)
os.environ.setdefault("SURVEY_OUTPUT_DIR", tmp)

failed = []
modules = sorted(
    ".".join(p.relative_to(BACKEND).with_suffix("").parts)
    for p in BACKEND.rglob("*.py")
    if "tests" not in p.parts and p.name != "__init__.py" and ".venv" not in p.parts
)
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as e:  # report every module, not only the first failure
        failed.append(f"{name}: {type(e).__name__}: {e}")
print(f"imported {len(modules) - len(failed)}/{len(modules)} modules")
for f in failed:
    print("  failed:", f)
sys.exit(1 if failed else 0)
