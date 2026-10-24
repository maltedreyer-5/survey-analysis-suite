# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Every internal link of the published Markdown files resolves (file and anchor).
Anchors as on GitHub: the heading in lower case, characters other than letters, digits, spaces, - and _ removed, spaces as '-'."""
import json
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_files import files as published_files   # the same file list with and without git

files = [ROOT / f for f in published_files() if f.endswith(".md")]
if not files:
    print("no Markdown files found"); sys.exit(1)
def anchors(path):
    out, seen = set(), {}
    in_code = False
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("```"): in_code = not in_code; continue
        m = None if in_code else re.match(r"#{1,6}\s+(.*)", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            n = seen.get(slug, 0); seen[slug] = n + 1
            out.add(slug if n == 0 else f"{slug}-{n}")
    return out
problems, checked = [], 0
for f in files:
    text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target) or target.startswith("<"): continue   # external, mailto
        path, _, anchor = target.partition("#")
        dest = (f.parent / path).resolve() if path else f
        checked += 1
        if not dest.exists(): problems.append(f"{f.relative_to(ROOT)}: {target} (file missing)"); continue
        if anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            problems.append(f"{f.relative_to(ROOT)}: {target} (anchor missing)")
print(json.dumps({"files": len(files), "links": checked, "problems": problems}, ensure_ascii=False))
sys.exit(1 if problems else 0)
