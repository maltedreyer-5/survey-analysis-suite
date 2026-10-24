# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Named scales and orders (settings.scales): defined once, referenced by questions
(``scale: <name>``) and segmentation dimensions (``order: <name>``)."""
import copy
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.scales import expand_named_scales, validate_scales  # noqa: E402
from services.config_validation import validate_survey_config  # noqa: E402

SCALE = {"context": "satisfaction, from the most to the least satisfied", "direction": "descending",
         "points": [{"key": "very", "label": {"de": "Sehr zufrieden", "en": "Very satisfied"}, "polarity": "positive"},
                    {"key": "rather", "label": {"de": "Eher zufrieden"}, "polarity": "positive"},
                    {"key": "not", "label": {"de": "Unzufrieden"}, "polarity": "negative"}]}


def config(**over):
    c = {"settings": {"survey_metadata": {"source_language": "de", "languages": ["de", "en"]},
                      "scales": {"satisfaction": copy.deepcopy(SCALE)},
                      "segmentation": {"dimensions": [{"id": "sat", "source_question": "q1", "order": "satisfaction",
                                                        "bucketing": {"type": "identity"}}]}},
         "question_groups": {"q1": {"handler": "single_choice", "columns": ["q1"], "label": {"de": "Zufrieden?"},
                                    "scale": "satisfaction"},
                             "m1": {"handler": "matrix_likert", "columns": ["m1_a"], "label": {"de": "Matrix"},
                                    "row_labels": {"de": ["Bibliothek"]}, "scale": "satisfaction"}}}
    c.update(over)
    return c


def test_reference_becomes_the_embedded_structures():
    raw = config()
    out = expand_named_scales(raw)
    q, m = out["question_groups"]["q1"], out["question_groups"]["m1"]
    assert q["options"]["de"] == ["Sehr zufrieden", "Eher zufrieden", "Unzufrieden"]
    assert "en" not in q["options"]            # not every point has an English label
    assert q["ordinal"] is True and q["scale_order"] == "descending" and "scale" not in q
    assert q["option_polarity"] == {"Sehr zufrieden": "positive", "Eher zufrieden": "positive", "Unzufrieden": "negative"}
    assert m["scale"]["de"] == ["Sehr zufrieden", "Eher zufrieden", "Unzufrieden"] and m["scale_order"] == "descending"
    assert out["settings"]["segmentation"]["dimensions"][0]["order"] == ["Sehr zufrieden", "Eher zufrieden", "Unzufrieden"]
    assert raw["question_groups"]["q1"]["scale"] == "satisfaction"   # the configuration as written is unchanged


def test_embedded_lists_stay_valid():
    c = config()
    c["question_groups"]["q1"] = {"handler": "single_choice", "columns": ["q1"], "options": {"de": ["Ja", "Nein"]}}
    assert expand_named_scales(c)["question_groups"]["q1"]["options"] == {"de": ["Ja", "Nein"]}


def test_validation_reports_scale_mistakes():
    c = config()
    c["settings"]["scales"]["satisfaction"]["points"][2]["polarity"] = "bad"
    c["settings"]["scales"]["satisfaction"]["direction"] = "up"
    c["question_groups"]["q2"] = {"handler": "single_choice", "columns": ["q2"], "scale": "missing"}
    c["question_groups"]["q3"] = {"handler": "single_choice", "columns": ["q3"], "scale": "satisfaction", "options": {"de": ["x"]}}
    errors, _ = validate_scales(c)
    text = "\n".join(errors)
    assert "polarity 'bad'" in text and "direction: 'up'" in text
    assert "q2: scale 'missing' is not defined" in text and "q3: both scale" in text
    c2 = config(); c2["settings"]["scales"]["satisfaction"]["points"][0]["label"] = {"en": "Only English"}
    assert any("no label in the source language 'de'" in e for e in validate_scales(c2)[0])
    c3 = config(); del c3["settings"]["scales"]["satisfaction"]["context"]
    assert any("no context" in w for w in validate_scales(c3)[1])


def test_valid_configuration_has_no_scale_errors_in_the_full_validation():
    errors, _ = validate_survey_config(config())
    assert not [e for e in errors if "scale" in e.lower()], errors


def test_analysis_and_export_use_the_named_scale():
    from handlers.single_choice import SingleChoiceHandler
    from export.keys import _build_options_with_keys
    import utils.prompts as prompts
    cfg = expand_named_scales(config())
    old = prompts.current_config
    prompts.current_config = lambda: cfg
    try:
        q = cfg["question_groups"]["q1"]
        df = pd.DataFrame({"q1": ["Sehr zufrieden"] * 3 + ["Unzufrieden"]})
        result = SingleChoiceHandler({**q, "id": "q1"}).analyze(df, lang="de")
        assert result.metadata.get("scale_metrics"), "ordinal: scale metrics from the named scale"
        opts = _build_options_with_keys(q, ["de", "en"])
        assert [o.get("polarity") for o in opts] == ["positive", "positive", "negative"]
    finally:
        prompts.current_config = old
