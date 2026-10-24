# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Segment resolver module of the survey analysis tool.

Resolves the survey-specific segmentation dimensions and adds one column per
dimension to the data DataFrame.

Main ideas:
- A dimension refers to its data source by a question id
  (question_groups.<id>), not by the column name, so the configuration is
  robust against renamed survey columns.
- Three bucketing strategies cover the cases that occur in practice:
    * identity         - the answer value is the segment value (1:1)
    * value_mapping    - several answers -> one segment class (m:n)
    * numeric_buckets  - numeric answer -> classes with bounds
- Values not covered by the bucketing are handled uniformly through
  unmapped_strategy: passthrough / drop / bucket.

Example:

    from utils.segment_resolver import SegmentResolver

    resolver = SegmentResolver(survey_config=state.config)
    df_enriched = resolver.materialize(df_raw)
    # df_enriched has the additional columns __segment_role__,
    # __segment_area__, etc.

    for dim in resolver.list_dimensions():
        col = resolver.segment_column(dim.id)
        print(dim.label['en'], df_enriched[col].value_counts())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from utils.numeric_coerce import coerce_series, is_numeric_column
from utils.languages import get_source_language, localized

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEGMENT_COLUMN_PREFIX = '__segment_'
SEGMENT_COLUMN_SUFFIX = '__'

# Thresholds; each can be overridden in settings.segmentation.
DEFAULT_MIN_N = 5                     # privacy; configured as sample_warnings.minimum_threshold
DEFAULT_SPECIFIC_CLUSTERS_MIN_N = 15  # segment-specific free-text clustering
DEFAULT_CROSS_FILTER_MIN_N = 5        # cross-filter combinations
MAX_CROSS_FILTER_DIMENSIONS = 3

# sentinel values for unmapped_strategy='drop' and values that cannot be assigned
DROPPED_VALUE = pd.NA   # values with the strategy 'drop' become NA

# default bucket label if bucketing.unmapped_strategy.bucket is used without
# a name
DEFAULT_UNMAPPED_BUCKET_LABEL = {
    'de': 'Sonstige', 'fr': 'Autres', 'en': 'Other', 'it': 'Altro',
}
DEFAULT_OUT_OF_RANGE_LABEL = {
    'de': 'außerhalb der Klassifikation',
    'fr': 'hors classification',
    'en': 'out of range',
    'it': 'fuori classificazione',
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class UnmappedStrategy:
    """
    Strategy for values the bucketing does not cover.

    value_mapping: answers without a mapping entry.
    numeric_buckets: values outside all bucket bounds, or NaN.
    identity: not relevant (every value is passed through).

    Attributes:
        type: 'passthrough' (the value stays), 'drop' (the respondent is left out
            for this dimension), 'bucket' (sorted into a collecting bucket).
        bucket_label: with type='bucket' the label in several languages; if not
            given, DEFAULT_UNMAPPED_BUCKET_LABEL or DEFAULT_OUT_OF_RANGE_LABEL is used.
    """
    type: str = 'passthrough'  # 'passthrough' | 'drop' | 'bucket'
    bucket_label: Optional[Dict[str, str]] = None

    def __post_init__(self):
        if self.type not in ('passthrough', 'drop', 'bucket'):
            raise ValueError(
                f"UnmappedStrategy.type must be 'passthrough', 'drop' or 'bucket', "
                f"was: '{self.type}'"
            )
        if self.type == 'bucket' and not self.bucket_label:
            self.bucket_label = dict(DEFAULT_UNMAPPED_BUCKET_LABEL)


@dataclass
class Bucket:
    """
    A class of numeric_buckets.

    Half-open interval [min, max). min=None means 'unbounded below',
    max=None means 'unbounded above'.

    Examples:
        Bucket(label={'en': 'small'}, max=10)          -> values < 10
        Bucket(label={'en': 'medium'}, min=10, max=50) -> 10 <= values < 50
        Bucket(label={'en': 'large'}, min=50)          -> values >= 50
    """
    label: Dict[str, str]
    min: Optional[float] = None
    max: Optional[float] = None

    def __post_init__(self):
        if isinstance(self.label, str):
            # a plain label is the same text in every language; stored under the
            # source language, from which every other language falls back
            from utils.prompts import source_language
            self.label = {source_language(): self.label}
        if not any(self.label.values()):
            raise ValueError("Bucket: label missing")
        name = localized(self.label, 'en')
        if self.min is None and self.max is None:
            raise ValueError(
                f"Bucket '{name}': min and max must not both be None"
            )
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError(
                f"Bucket '{name}': min ({self.min}) >= max ({self.max})"
            )

    def contains(self, value: float) -> bool:
        if pd.isna(value):
            return False
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value >= self.max:
            return False
        return True

    def get_label(self, lang: str, source_lang: str = 'de') -> str:
        return localized(self.label, lang, source_lang)


@dataclass
class BucketingStrategy:
    """
    Strategy to turn answer values into segment values.
    """
    type: str  # 'identity' | 'value_mapping' | 'numeric_buckets'
    mapping: Optional[Dict[str, str]] = None
    buckets: Optional[List[Bucket]] = None
    unmapped_strategy: UnmappedStrategy = field(default_factory=UnmappedStrategy)

    def __post_init__(self):
        if self.type not in ('identity', 'value_mapping', 'numeric_buckets'):
            raise ValueError(
                f"BucketingStrategy.type must be 'identity', 'value_mapping' or "
                f"'numeric_buckets', was: '{self.type}'"
            )
        if self.type == 'value_mapping' and not self.mapping:
            raise ValueError("BucketingStrategy 'value_mapping': mapping is required")
        if self.type == 'numeric_buckets' and not self.buckets:
            raise ValueError("BucketingStrategy 'numeric_buckets': buckets is required")

        # Bucket-Ueberlappung pruefen
        if self.type == 'numeric_buckets':
            self._validate_buckets()

    def _validate_buckets(self) -> None:
        """Checks that the buckets do not overlap."""
        # sort for the comparison
        bs = sorted(self.buckets, key=lambda b: (b.min if b.min is not None else float('-inf')))
        for i in range(len(bs) - 1):
            a, b = bs[i], bs[i + 1]
            a_max = a.max if a.max is not None else float('inf')
            b_min = b.min if b.min is not None else float('-inf')
            if a_max > b_min:
                raise ValueError(
                    f"Buckets overlap: '{localized(a.label, 'en')}' (max={a.max}) "
                    f"and '{localized(b.label, 'en')}' (min={b.min})"
                )


@dataclass
class Dimension:
    """
    A segmentation dimension.
    """
    id: str
    label: Dict[str, str]
    source_question: Optional[str]  # question id from question_groups ...
    bucketing: BucketingStrategy
    order: Optional[List[str]] = None
    enable_specific_clusters: bool = False
    description: Optional[Dict[str, str]] = None
    # ... or a data column that is not configured as a question
    source_column: Optional[str] = None
    # None: use settings.segmentation.specific_clusters_min_n
    specific_clusters_min_n: Optional[int] = None
    source_language: str = 'de'
    # Optional translations of segment values: {value: {lang: label}}
    value_labels: Optional[Dict[str, Dict[str, str]]] = None

    def __post_init__(self):
        if not self.id:
            raise ValueError("Dimension.id must not be empty")
        if not self.id.replace('_', '').isalnum():
            raise ValueError(
                f"Dimension.id '{self.id}' may only contain letters, digits and '_' "
                f"(it is used as a column name)"
            )
        if bool(self.source_question) == bool(self.source_column):
            raise ValueError(
                f"Dimension '{self.id}': give exactly one of source_question "
                f"or source_column"
            )
        if isinstance(self.label, str):
            self.label = {self.source_language: self.label}
        if not (self.label or {}).get(self.source_language):
            raise ValueError(
                f"Dimension '{self.id}': label.{self.source_language} "
                f"(source language) is required"
            )

    def get_label(self, lang: str) -> str:
        return localized(self.label, lang, self.source_language, default=self.id)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class SegmentResolver:
    """
    Loads the segmentation dimensions from a survey configuration and
    materialises them as additional columns of the DataFrame.
    """

    def __init__(self, survey_config: Dict[str, Any]):
        """
        Args:
            survey_config: loaded questions.yaml as a dict.
        """
        self.config = survey_config or {}
        self.source_language = get_source_language(self.config)
        self._dimensions: List[Dimension] = []
        self._validation_warnings: List[str] = []
        self.min_n: int = DEFAULT_MIN_N
        self.specific_clusters_min_n: int = DEFAULT_SPECIFIC_CLUSTERS_MIN_N
        self.cross_filter_min_n: int = DEFAULT_CROSS_FILTER_MIN_N
        self.cross_filters: List[Tuple[str, ...]] = []
        self._load_and_validate()

    # -- Public API -------------------------------------------------------

    def list_dimensions(self) -> List[Dimension]:
        """All active dimensions in the configured order."""
        return list(self._dimensions)

    def get_dimension(self, dim_id: str) -> Optional[Dimension]:
        for d in self._dimensions:
            if d.id == dim_id:
                return d
        return None

    def dimension_ids(self) -> List[str]:
        return [d.id for d in self._dimensions]

    def segment_column(self, dim_id: str) -> str:
        """Name of the materialised column of a dimension."""
        return f"{SEGMENT_COLUMN_PREFIX}{dim_id}{SEGMENT_COLUMN_SUFFIX}"

    def specific_clusters_threshold(self, dim_id: str) -> Optional[int]:
        """
        Minimum segment size for segment-specific free-text clusters, or
        None when the dimension does not enable them.
        """
        dim = self.get_dimension(dim_id)
        if dim is None or not dim.enable_specific_clusters:
            return None
        if dim.specific_clusters_min_n is not None:
            return dim.specific_clusters_min_n
        return self.specific_clusters_min_n

    def source_column_of(self, dim: Dimension) -> str:
        """Data column a dimension is computed from."""
        if dim.source_column:
            return dim.source_column
        qcfg = self.config.get('question_groups', {}).get(dim.source_question, {})
        cols = qcfg.get('columns', [])
        if not cols:
            raise ValueError(
                f"Dimension '{dim.id}': source question '{dim.source_question}' "
                f"has no columns configured"
            )
        return cols[0]

    def validation_warnings(self) -> List[str]:
        """Warnings of the validation step (not fatal)."""
        return list(self._validation_warnings)

    def materialize(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds one column per dimension to the DataFrame.

        Column name: __segment_<dim_id>__

        Values: the segment label (string), or pd.NA with the drop strategy.

        Returns:
            a copy of the DataFrame with the additional columns; the original
            df stays unchanged.
        """
        if df is None or len(df) == 0:
            logger.debug("materialize(): empty DataFrame, nothing to do")
            return df.copy() if df is not None else df

        out = df.copy()
        for dim in self._dimensions:
            col_name = self.segment_column(dim.id)
            try:
                out[col_name] = self._compute_segment_values(dim, df)
            except Exception as e:
                logger.error(
                    f"Materialisation of the dimension '{dim.id}' failed: {e}",
                    exc_info=True,
                )
                # on errors: fill the column with NA (the dimension is effectively inactive)
                out[col_name] = pd.Series([pd.NA] * len(df), index=df.index, dtype='object')
        return out

    def get_segment_values(self,
                            df_materialized: pd.DataFrame,
                            dim_id: str,
                            include_dropped: bool = False) -> List[str]:
        """
        The segment values that actually occur in the data, in the configured
        order (or by frequency).

        Args:
            df_materialized: a DataFrame enriched by materialize() already.
            dim_id: id of the dimension.
            include_dropped: whether NA values (drop strategy) are included as 'NA'.

        Returns:
            list of segment values.
        """
        dim = self.get_dimension(dim_id)
        if dim is None:
            return []

        col = self.segment_column(dim_id)
        if col not in df_materialized.columns:
            return []

        series = df_materialized[col]
        if not include_dropped:
            series = series.dropna()

        present = set(series.unique())

        if dim.order:
            ordered = [v for v in dim.order if v in present]
            unordered = sorted(present - set(dim.order))
            return ordered + unordered

        # sort by frequency, descending
        counts = series.value_counts()
        return list(counts.index)

    # -- Loading & Validation --------------------------------------------

    def _load_and_validate(self) -> None:
        """Reads the segmentation configuration and validates it."""
        seg = self.config.get('settings', {}).get('segmentation', {}) or {}

        per_key = [k for k, v in seg.items()
                  if k == 'variables' or (isinstance(v, dict) and 'column' in v)]
        if per_key:
            raise ValueError(
                "Unsupported segmentation format: settings.segmentation."
                f"{per_key[0]} uses a per-key format with 'column', which is not supported. "
                "Define segments as a list under settings.segmentation.dimensions "
                "(id, label, source_question or source_column, bucketing)."
            )

        # The privacy threshold already exists as sample_warnings.minimum_threshold.
        sw = self.config.get('settings', {}).get('sample_warnings', {}) or {}
        self.min_n = int(sw.get('minimum_threshold', DEFAULT_MIN_N))

        for key, attr in (('specific_clusters_min_n', 'specific_clusters_min_n'),
                          ('cross_filter_min_n', 'cross_filter_min_n')):
            if key in seg:
                value = seg[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ValueError(f"settings.segmentation.{key} must be a whole number >= 1")
                setattr(self, attr, value)

        for i, dim_data in enumerate(seg.get('dimensions') or []):
            try:
                dim = self._build_dimension(dim_data, index=i)
                self._dimensions.append(dim)
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(
                    f"settings.segmentation.dimensions[{i}]: {e}"
                ) from e

        # unique ids
        ids = [d.id for d in self._dimensions]
        if len(ids) != len(set(ids)):
            dups = [i for i in ids if ids.count(i) > 1]
            raise ValueError(f"Duplicate dimension IDs: {set(dups)}")

        # the source question must exist in question_groups
        qgs = self.config.get('question_groups', {}) or {}
        for d in self._dimensions:
            if d.source_question and d.source_question not in qgs:
                raise ValueError(
                    f"Dimension '{d.id}': source_question='{d.source_question}' "
                    f"does not exist in question_groups"
                )

        # value_mapping: check for typos
        for d in self._dimensions:
            if d.bucketing.type == 'value_mapping' and d.source_question:
                self._validate_value_mapping(d, qgs)

        self.cross_filters = self._parse_cross_filters(seg.get('cross_filters'))

        logger.info(
            f"SegmentResolver loaded: {len(self._dimensions)} dimension(s) "
            f"({', '.join(d.id for d in self._dimensions) if self._dimensions else '(none)'})"
        )
        for w in self._validation_warnings:
            logger.warning(f"Segmentation validation: {w}")

    def _build_dimension(self, data: Dict[str, Any], index: int) -> Dimension:
        """Builds a dimension from the YAML format."""
        if 'id' not in data:
            raise KeyError(f"dimensions[{index}]: 'id' missing")
        min_n = data.get('specific_clusters_min_n')
        if min_n is not None and (not isinstance(min_n, int) or isinstance(min_n, bool) or min_n < 1):
            raise ValueError("specific_clusters_min_n must be a whole number >= 1")

        bucketing_data = data.get('bucketing') or {'type': 'identity'}
        bucketing = self._build_bucketing(bucketing_data)

        return Dimension(
            id=data['id'],
            label=data.get('label', {}),
            source_question=data.get('source_question'),
            source_column=data.get('source_column'),
            bucketing=bucketing,
            order=data.get('order'),
            enable_specific_clusters=bool(data.get('enable_specific_clusters', False)),
            specific_clusters_min_n=min_n,
            description=data.get('description'),
            source_language=self.source_language,
            value_labels=data.get('value_labels'),
        )

    def _parse_cross_filters(self, raw) -> List[Tuple[str, ...]]:
        """
        Cross filters reference dimensions defined above; each entry is a
        list of 2 or 3 dimension ids. Stored in canonical (sorted) order,
        which is also the order used in result keys.
        """
        if not raw:
            return []
        if not isinstance(raw, list):
            raise ValueError("settings.segmentation.cross_filters must be a list")
        known = set(self.dimension_ids())
        result: List[Tuple[str, ...]] = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, list) or not all(isinstance(x, str) for x in entry):
                raise ValueError(f"cross_filters[{i}]: list of dimension IDs expected")
            if not 2 <= len(entry) <= MAX_CROSS_FILTER_DIMENSIONS:
                raise ValueError(
                    f"cross_filters[{i}]: 2 to {MAX_CROSS_FILTER_DIMENSIONS} dimensions expected"
                )
            unknown = [x for x in entry if x not in known]
            if unknown:
                raise ValueError(
                    f"cross_filters[{i}]: unknown dimension(s) {unknown}; "
                    f"defined are {sorted(known)}"
                )
            if len(set(entry)) != len(entry):
                raise ValueError(f"cross_filters[{i}]: dimension repeated")
            key = tuple(sorted(entry))
            if key in result:
                raise ValueError(f"cross_filters[{i}]: combination repeated")
            result.append(key)
        return result

    def _build_bucketing(self, data: Dict[str, Any]) -> BucketingStrategy:
        bt = data.get('type', 'identity')

        # unmapped_strategy
        us_data = data.get('unmapped_strategy') or {}
        if isinstance(us_data, str):
            us = UnmappedStrategy(type=us_data)
        else:
            # convenience: resolve the short form 'bucket: <name>' BEFORE UnmappedStrategy
            # is instantiated (otherwise its default applies already).
            bucket_label = us_data.get('bucket_label')
            if not bucket_label and 'bucket' in us_data:
                name = us_data['bucket']
                bucket_label = {'de': name, 'fr': name, 'en': name, 'it': name}
            us = UnmappedStrategy(
                type=us_data.get('type', 'passthrough'),
                bucket_label=bucket_label,
            )

        # buckets with numeric_buckets
        buckets = None
        if bt == 'numeric_buckets':
            buckets = []
            for i, b_data in enumerate(data.get('buckets') or []):
                buckets.append(Bucket(
                    label=b_data.get('label', {}),
                    min=b_data.get('min'),
                    max=b_data.get('max'),
                ))

        return BucketingStrategy(
            type=bt,
            mapping=data.get('mapping'),
            buckets=buckets,
            unmapped_strategy=us,
        )

    def _validate_value_mapping(self,
                                 dim: Dimension,
                                 qgs: Dict[str, Any]) -> None:
        """
        Checks whether the value_mapping keys exist among the answer options of
        the source question. A typo -> warning, not an error.
        """
        qcfg = qgs.get(dim.source_question, {})
        opts = localized(qcfg.get('options', {}), self.source_language, self.source_language) or []
        if not opts:
            return  # without known options there is nothing to check

        unknown = [k for k in dim.bucketing.mapping.keys() if k not in opts]
        if unknown:
            self._validation_warnings.append(
                f"Dimension '{dim.id}': value_mapping has {len(unknown)} "
                f"keys that are not among the answer options of the question "
                f"'{dim.source_question}': {unknown[:3]}"
                + (f" (and {len(unknown) - 3} more)" if len(unknown) > 3 else "")
            )

    # -- materialisation -----------------------------------------------

    def _compute_segment_values(self,
                                 dim: Dimension,
                                 df: pd.DataFrame) -> pd.Series:
        """Applies the bucketing of a dimension to the DataFrame."""
        col = self.source_column_of(dim)
        if col not in df.columns:
            # sentinel column: all NA
            logger.warning(
                f"Dimension '{dim.id}': column '{col}' not in the DataFrame; "
                f"all values become NA"
            )
            return pd.Series([pd.NA] * len(df), index=df.index, dtype='object')

        source_series = df[col]

        if dim.bucketing.type == 'identity':
            return self._apply_identity(source_series, dim)
        elif dim.bucketing.type == 'value_mapping':
            return self._apply_value_mapping(source_series, dim)
        elif dim.bucketing.type == 'numeric_buckets':
            return self._apply_numeric_buckets(source_series, dim)
        else:
            raise ValueError(f"Unknown bucketing.type: {dim.bucketing.type}")

    def _apply_identity(self,
                         source: pd.Series,
                         dim: Dimension) -> pd.Series:
        """Identity: value -> value. Only empty values become NA."""
        out = source.astype('object').where(source.notna() & (source.astype(str).str.strip() != ''),
                                              other=pd.NA)
        return out

    def _apply_value_mapping(self,
                              source: pd.Series,
                              dim: Dimension) -> pd.Series:
        """value_mapping: value -> mapped value; unmapped values by the strategy."""
        src = self.source_language
        mapping = dim.bucketing.mapping or {}
        us = dim.bucketing.unmapped_strategy

        def transform(v):
            if pd.isna(v):
                return pd.NA
            sv = str(v).strip()
            if not sv:
                return pd.NA
            if sv in mapping:
                return mapping[sv]
            # not in the mapping
            if us.type == 'passthrough':
                return sv
            if us.type == 'drop':
                return pd.NA
            if us.type == 'bucket':
                return localized(us.bucket_label, src, src, DEFAULT_UNMAPPED_BUCKET_LABEL[src])
            return sv  # fallback

        return source.apply(transform).astype('object')

    def _apply_numeric_buckets(self,
                                source: pd.Series,
                                dim: Dimension) -> pd.Series:
        """numeric_buckets: value -> class label."""
        src = self.source_language
        coerced = coerce_series(source)
        buckets = dim.bucketing.buckets or []
        us = dim.bucketing.unmapped_strategy

        def assign(v):
            if pd.isna(v):
                # NaN -> unmapped
                if us.type == 'drop':
                    return pd.NA
                if us.type == 'bucket':
                    return localized(us.bucket_label, src, src, DEFAULT_OUT_OF_RANGE_LABEL[src])
                # passthrough makes no sense with NaN -> like drop
                return pd.NA
            for b in buckets:
                if b.contains(v):
                    return b.get_label(src, src)
            # outside all buckets
            if us.type == 'drop':
                return pd.NA
            if us.type == 'bucket':
                return localized(us.bucket_label, src, src, DEFAULT_OUT_OF_RANGE_LABEL[src])
            # passthrough with a numeric value: return it as a string
            return str(v)

        return coerced.apply(assign).astype('object')


# ---------------------------------------------------------------------------
# Convenience-Helpers
# ---------------------------------------------------------------------------

def is_materialized(df: pd.DataFrame) -> bool:
    """Heuristic: were segment columns attached to this DataFrame already?"""
    return any(c.startswith(SEGMENT_COLUMN_PREFIX) and c.endswith(SEGMENT_COLUMN_SUFFIX)
               for c in df.columns)


def list_segment_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns
            if c.startswith(SEGMENT_COLUMN_PREFIX) and c.endswith(SEGMENT_COLUMN_SUFFIX)]
