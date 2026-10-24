# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Segmentation configuration: only the dimensions format, validated on load."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.config_validation import validate_survey_config  # noqa: E402
from utils.segment_resolver import SegmentResolver  # noqa: E402
from utils.segmentation import Segmenter  # noqa: E402

QG = {"q_country": {"handler": "single_choice", "columns": ["C"]}}


def config(segmentation, source_language="de", **meta):
    return {"settings": {"survey_metadata": {"source_language": source_language, **meta},
                         "segmentation": segmentation},
            "question_groups": QG}


DIMS = [
    {"id": "country", "label": {"de": "Land"}, "source_question": "q_country",
     "enable_specific_clusters": True},
    {"id": "size", "label": {"de": "Größe"}, "source_column": "staff",
     "bucketing": {"type": "numeric_buckets",
                   "buckets": [{"label": {"de": "klein"}, "max": 50},
                               {"label": {"de": "groß"}, "min": 50}]}},
]


def test_removed_per_key_format_is_rejected_with_guidance():
    per_key = {"country": {"column": "C", "label": {"de": "Land"}}}
    with pytest.raises(ValueError, match="dimensions"):
        SegmentResolver(config(per_key))
    errors, _ = validate_survey_config(config(per_key))
    assert errors and "dimensions" in errors[0]


def test_segmenter_does_not_hide_configuration_errors():
    with pytest.raises(ValueError):
        Segmenter(config({"country": {"column": "C"}}))


def test_source_column_and_source_question():
    r = SegmentResolver(config({"dimensions": DIMS}))
    m = r.materialize(pd.DataFrame({"C": ["DE", "IT"], "staff": [10, 80]}))
    assert list(m[r.segment_column("country")]) == ["DE", "IT"]
    assert list(m[r.segment_column("size")]) == ["klein", "groß"]


def test_exactly_one_source_is_required():
    both = [{**DIMS[0], "source_column": "C"}]
    with pytest.raises(ValueError, match="exactly one"):
        SegmentResolver(config({"dimensions": both}))


def test_specific_cluster_thresholds():
    seg = {"dimensions": [dict(DIMS[0]), dict(DIMS[1])]}
    r = SegmentResolver(config(seg))
    assert r.specific_clusters_threshold("country") == 15      # default
    assert r.specific_clusters_threshold("size") is None       # not enabled
    seg["specific_clusters_min_n"] = 25
    seg["dimensions"][0]["specific_clusters_min_n"] = 30
    r = SegmentResolver(config(seg))
    assert r.specific_clusters_threshold("country") == 30


def test_cross_filters_reference_defined_dimensions():
    r = SegmentResolver(config({"dimensions": DIMS, "cross_filters": [["size", "country"]]}))
    assert r.cross_filters == [("country", "size")]
    for bad, msg in ([["country", "region"]], "unknown"), ([["country"]], "2 to"), \
                    ([["country", "size"], ["size", "country"]], "combination repeated"), \
                    ([["country", "country"]], "dimension repeated"):
        with pytest.raises(ValueError, match=msg):
            SegmentResolver(config({"dimensions": DIMS, "cross_filters": bad}))


def test_privacy_threshold_comes_from_sample_warnings():
    cfg = config({"dimensions": DIMS})
    cfg["settings"]["sample_warnings"] = {"minimum_threshold": 8}
    assert SegmentResolver(cfg).min_n == 8


def test_english_survey_uses_english_labels_as_segment_values():
    dims = [{"id": "size", "label": {"en": "Size"}, "source_column": "staff",
             "bucketing": {"type": "numeric_buckets",
                           "buckets": [{"label": {"en": "small", "de": "klein"}, "max": 50},
                                       {"label": {"en": "large"}, "min": 50}]}}]
    r = SegmentResolver(config({"dimensions": dims}, source_language="en"))
    m = r.materialize(pd.DataFrame({"staff": [10, 80]}))
    assert list(m[r.segment_column("size")]) == ["small", "large"]
    assert r.get_dimension("size").get_label("it") == "Size"
    with pytest.raises(ValueError, match="label.en"):
        SegmentResolver(config({"dimensions": [{**dims[0], "label": {"de": "Größe"}}]},
                               source_language="en"))
