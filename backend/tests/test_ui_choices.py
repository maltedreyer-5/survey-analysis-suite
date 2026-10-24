# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Every choice of a dropdown, radio or checkbox group reaches the browser as a
(label, value) pair. A list of plain strings assigned to `.choices` after the
component was built is not normalised by Gradio: the browser then showed only
the first letter of each entry (survey selection, segmentation editor)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for var in ("SURVEY_DATA_DIR", "SURVEY_OUTPUT_DIR"):
    os.environ.setdefault(var, tempfile.mkdtemp())


def test_all_choices_are_label_value_pairs():
    import app
    config = app.create_interface().get_config_file()
    bad = []
    for comp in config["components"]:
        props = comp.get("props", {})
        if comp.get("type") in ("dropdown", "radio", "checkboxgroup"):
            for choice in props.get("choices") or []:
                if not (isinstance(choice, (list, tuple)) and len(choice) == 2):
                    bad.append((props.get("label"), choice))
    assert not bad, bad[:5]
