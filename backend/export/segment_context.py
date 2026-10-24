# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Segment access for all exports.

Every export that breaks results down by segment goes through this module,
so dimensions, segment values, value keys and labels are derived the same
way everywhere: from the SegmentResolver and the data with materialised
segment columns.

Value keys are stable, language-independent identifiers derived from the
segment value in the source language; the dashboard uses them in its data
and in shareable URLs. Value labels per output language come from, in this
order: ``value_labels`` of the dimension, bucket labels, the answer options
of the source question (lists aligned by position across languages), and
finally the value itself.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core import state
from utils.languages import get_output_languages, get_source_language, localized
from utils.segment_resolver import SegmentResolver

logger = logging.getLogger(__name__)


def value_key(value: str) -> str:
    """Stable key of a segment value, e.g. 'North / East' -> 'north_east'."""
    text = str(value).strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return slug or "value"


@dataclass
class SegmentContext:
    resolver: SegmentResolver
    frame: Optional[pd.DataFrame]
    source_language: str
    languages: List[str]
    _keys: Dict[Tuple[str, str], str] = field(default_factory=dict)

    # -- dimensions ---------------------------------------------------------

    def dimension_ids(self) -> List[str]:
        return self.resolver.dimension_ids()

    def column(self, dim_id: str) -> str:
        return self.resolver.segment_column(dim_id)

    def label(self, dim_id: str) -> Dict[str, str]:
        dim = self.resolver.get_dimension(dim_id)
        return {lang: dim.get_label(lang) for lang in self.languages}

    # -- values ---------------------------------------------------------------

    def values(self, dim_id: str) -> List[str]:
        """Segment values present in the data, in configured order."""
        if self.frame is None:
            return []
        return self.resolver.get_segment_values(self.frame, dim_id)

    def counts(self, dim_id: str) -> Dict[str, int]:
        if self.frame is None:
            return {}
        return self.frame[self.column(dim_id)].dropna().value_counts().to_dict()

    def mask(self, dim_id: str, value: str) -> pd.Series:
        return self.frame[self.column(dim_id)] == value

    def subset(self, dim_id: str, value: str) -> pd.DataFrame:
        return self.frame[self.mask(dim_id, value)]

    def key(self, dim_id: str, value: str) -> str:
        """Stable key; values that collide on the same slug get a suffix."""
        cache_key = (dim_id, value)
        if cache_key not in self._keys:
            base = value_key(value)
            taken = {k for (d, _), k in self._keys.items() if d == dim_id}
            key, i = base, 2
            while key in taken:
                key, i = f"{base}_{i}", i + 1
            self._keys[cache_key] = key
        return self._keys[cache_key]

    def value_labels(self, dim_id: str, value: str) -> Dict[str, str]:
        dim = self.resolver.get_dimension(dim_id)
        explicit = (dim.value_labels or {}).get(value)
        if explicit:
            return {lang: localized(explicit, lang, self.source_language, value)
                    for lang in self.languages}
        for bucket in dim.bucketing.buckets or []:
            if bucket.get_label(self.source_language, self.source_language) == value:
                return {lang: bucket.get_label(lang, self.source_language)
                        for lang in self.languages}
        options = self._options(dim)
        source_opts = options.get(self.source_language) or []
        if value in source_opts:
            idx = source_opts.index(value)
            labels = {}
            for lang in self.languages:
                opts = options.get(lang) or []
                labels[lang] = opts[idx] if idx < len(opts) and opts[idx] else value
            return labels
        return {lang: value for lang in self.languages}

    def _options(self, dim) -> Dict[str, List[str]]:
        if not dim.source_question:
            return {}
        qcfg = self.resolver.config.get("question_groups", {}).get(dim.source_question, {})
        opts = qcfg.get("options") or {}
        return opts if isinstance(opts, dict) else {}

    # -- dashboard metadata -----------------------------------------------------

    def segments_metadata(self) -> dict:
        """The ``segments`` block of dashboard_data.json."""
        out = {}
        for dim_id in self.dimension_ids():
            dim = self.resolver.get_dimension(dim_id)
            out[dim_id] = {
                "label": self.label(dim_id),
                # lets the dashboard leave the source question out of profile content
                "source_question": dim.source_question,
                "values": [
                    {"key": self.key(dim_id, v), "label": self.value_labels(dim_id, v)}
                    for v in self.values(dim_id)
                ],
            }
        return out


_cache: Dict[str, object] = {"sig": None, "ctx": None}


def get_context() -> SegmentContext:
    """
    Segment context for the current configuration and data.

    Rebuilt whenever the configuration or the DataFrame object changes;
    materialising once per export keeps per-segment loops cheap.
    """
    sig = (id(state.config), id(state.df), None if state.df is None else len(state.df))
    if _cache["sig"] != sig or _cache["ctx"] is None:
        config = state.config or {}
        resolver = SegmentResolver(config)
        frame = resolver.materialize(state.df) if state.df is not None else None
        _cache["ctx"] = SegmentContext(
            resolver=resolver,
            frame=frame,
            source_language=get_source_language(config),
            languages=get_output_languages(config),
        )
        _cache["sig"] = sig
    return _cache["ctx"]  # type: ignore[return-value]


def reset_context() -> None:
    _cache["sig"] = None
    _cache["ctx"] = None
