# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Checks of the files of the repository:

1. every YAML and JSON file can be read;
2. the configurations of the example surveys have no errors;
3. no runtime state is in the repository (databases, exports, settings, caches);
4. every source file of the project carries the SPDX licence header.

In a git working copy the files are those git would add (tracked and untracked, not
ignored); elsewhere (for example an unpacked archive) all files that the rules of
.gitignore do not exclude.
"""
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}

RUNTIME_STATE = [            # written while the suite runs; never part of the repository
    "*.db", "*.db-journal", "*.sqlite", "*.pyc", ".env", "backend/.env",
    "backend/data/*", "backend/outputs/*", "backend/surveys/*/snapshots/*",
    "*llm_usage.json", "*llm_usage.jsonl", "*/__pycache__/*", ".pytest_cache/*",
]
SPDX_SOURCES = ("*.py", "*.sh", "*.js", "*.html", "*.css")
SPDX_EXEMPT = ["dashboard/vendor/*",                       # third-party files, with their own licences
               "dashboard/js/*-data.js", "dashboard/js/data.js",   # generated data (build_offline.py)
               "dashboard/js/survey-structure.js"]


PUBLISHED_DIRS = {"backend", "dashboard", "docs", "examples", "scripts", ".github"}
PUBLISHED_ROOT_DOCS = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"}


def published(path):
    parts = Path(path).parts
    if len(parts) > 1:
        return parts[0] in PUBLISHED_DIRS
    return not path.endswith(".md") or path in PUBLISHED_ROOT_DOCS


def files():
    return [f for f in _all_files() if published(f)]


def _all_files():
    if (ROOT / ".git").exists():
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split("\n")
        return sorted(f for f in out if f and (ROOT / f).is_file())
    ignored = _gitignore_patterns()
    return sorted(f for f in (str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                              if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts))
                  if not _ignored(f, ignored))


def _gitignore_patterns():
    """The rules of .gitignore (without git: the files git would not add)."""
    path = ROOT / ".gitignore"
    rules = []
    for line in (path.read_text(encoding="utf-8").split("\n") if path.exists() else []):
        line = line.strip()
        if line and not line.startswith("#"):
            rules.append(line)
    return rules


def _ignored(path, rules):
    result = False
    for rule in rules:
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        if pattern.endswith("/"):
            hit = any(fnmatch.fnmatch(path, f"{pattern}*") or fnmatch.fnmatch(path, f"*/{pattern}*")
                      for pattern in (pattern,))
        elif "/" in pattern:
            hit = fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"{pattern}/*")
        else:
            hit = fnmatch.fnmatch(Path(path).name, pattern) or any(fnmatch.fnmatch(part, pattern) for part in Path(path).parts)
        if hit:
            result = not negate
    return result


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def main():
    problems = []
    all_files = files()
    if not all_files:
        print("no files found"); return 1
    for f in all_files:
        p = ROOT / f
        try:
            if f.endswith((".yaml", ".yml")):
                yaml.safe_load(p.read_text(encoding="utf-8"))
            elif f.endswith(".json"):
                json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{f}: not readable ({e})")
        if matches(f, RUNTIME_STATE):
            problems.append(f"{f}: runtime state in the repository")
        if matches(f, SPDX_SOURCES) and not matches(f, SPDX_EXEMPT):
            head = "\n".join(p.read_text(encoding="utf-8", errors="replace").split("\n")[:6])
            if "SPDX-License-Identifier: MIT" not in head:
                problems.append(f"{f}: no SPDX licence header")

    sys.path.insert(0, str(ROOT / "backend"))
    from services.config_validation import validate_survey_config
    for q in sorted((ROOT / "backend" / "surveys").glob("*/questions.yaml")):
        errors, _ = validate_survey_config(yaml.safe_load(q.read_text(encoding="utf-8")))
        problems += [f"{q.relative_to(ROOT)}: {e}" for e in errors]

    print(f"files checked: {len(all_files)}")
    for p in problems:
        print("  problem:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
