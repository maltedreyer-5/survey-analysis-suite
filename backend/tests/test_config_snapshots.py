# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Every path that replaces questions.yaml must leave a restorable snapshot."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from services import config_io  # noqa: E402

ORIGINAL = "# hand-written comment\nsettings: {}\nquestion_groups:\n  q1:\n    handler: single_choice\n    columns: [A]\n"


@pytest.fixture
def survey_dir(tmp_path, monkeypatch):
    (tmp_path / "questions.yaml").write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setattr(core, "get_active_config_dir", lambda: tmp_path)
    return tmp_path


def _snapshots(d):
    return sorted((d / "snapshots").glob("*.yaml"))


def test_yaml_editor_save_keeps_snapshot_and_comments(survey_dir):
    new_text = "# edited\nsettings: {llm_context: x}\nquestion_groups: {}\n"
    msg = core.save_config(new_text)
    assert "❌" not in msg
    assert (survey_dir / "questions.yaml").read_text(encoding="utf-8") == new_text
    snaps = _snapshots(survey_dir)
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == ORIGINAL


def test_yaml_editor_rejects_invalid_yaml_without_touching_file(survey_dir):
    core.save_config("settings: [unclosed")
    assert (survey_dir / "questions.yaml").read_text(encoding="utf-8") == ORIGINAL
    assert _snapshots(survey_dir) == []


def test_dict_write_keeps_snapshot(survey_dir):
    config_io.write_active_config_with_snapshot({"settings": {}, "question_groups": {}}, reason="test")
    snaps = _snapshots(survey_dir)
    assert len(snaps) == 1 and snaps[0].read_text(encoding="utf-8") == ORIGINAL
    assert yaml.safe_load((survey_dir / "questions.yaml").read_text(encoding="utf-8"))["question_groups"] == {}
