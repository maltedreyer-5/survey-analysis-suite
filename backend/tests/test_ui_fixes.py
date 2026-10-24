# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Fixes found by operating every button of the interface (17./18.10.)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import workbench_store as wb  # noqa: E402
from utils import document_builder as db  # noqa: E402


def test_cluster_fields_may_be_text_or_dict_and_counts_dicts():
    assert wb._as_dict({"de": "x"}) == {"de": "x"} and wb._as_dict("x") == {}
    assert wb._count_of(5) == 5 and wb._count_of({"count": 7, "pct": 2.0}) == 7 and wb._count_of(None) == 0


def test_document_builder_paths_do_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                       # anywhere: the defaults stay absolute
    b = db.DocumentBuilder()
    assert b.config_path.is_absolute() and b.config_path.name == "documents.yaml"
    assert b.db_path.is_absolute()


def test_needs_rebuild_handles_section_groups(tmp_path):
    b = db.DocumentBuilder.__new__(db.DocumentBuilder)
    b.config_path = tmp_path / "documents.yaml"; b.config_path.write_text("x")
    b.source_dir = tmp_path
    (tmp_path / "a.md").write_text("a"); (tmp_path / "b.md").write_text("b")
    b.config = {"shared_sections": {}, "documents": {"d": {"sections": [
        "a.md", {"group": {"title": {"en": "G"}, "sections": ["b.md"]}}]}}}
    out = tmp_path / "d.docx"; out.write_text("doc")
    import os, time
    old = time.time() - 100
    os.utime(b.config_path, (old, old)); os.utime(tmp_path / "a.md", (old, old)); os.utime(tmp_path / "b.md", (old, old))
    assert b._needs_rebuild("d", "en", out) is False
    os.utime(tmp_path / "b.md", None)                 # a file inside the group changes
    assert b._needs_rebuild("d", "en", out) is True
