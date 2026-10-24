# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: maintaining the segmentation dimensions.

CRUD operations for settings.segmentation.dimensions in the configuration
of the active survey. Writes with snapshots through config_io.

Data model (in the YAML):

    settings:
      segmentation:
        dimensions:
          - id: role
            label:
              en: Role
            source_question: q_role
            bucketing:
              type: identity      # | value_mapping | numeric_buckets
              # with value_mapping:
              # mapping:
              #   "S": "Student"
              #   "T": "Teaching and research"
              # with numeric_buckets:
              # buckets:
              #   - {label: {en: small}, max: 10}
              #   - {label: {en: medium}, min: 10, max: 50}
              #   - {label: {en: large}, min: 50}
              # unmapped_strategy:
              #   type: passthrough | drop | bucket
              #   bucket_label: {en: ...}  # only for type=bucket
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Data models (for the interface, NOT identical to utils/segment_resolver.py)
# =============================================================================

@dataclass
class DimensionSummary:
    """Compact view of a dimension for the list."""
    id: str
    label_source: str
    source_question: str
    bucketing_type: str
    bucket_count: Optional[int] = None  # with value_mapping/numeric_buckets
    
    def display_row(self) -> List[Any]:
        """Format for gr.Dataframe."""
        if self.bucket_count is not None:
            bucket_str = f"{self.bucketing_type} ({self.bucket_count} buckets)"
        else:
            bucket_str = self.bucketing_type
        return [self.id, self.label_source, self.source_question, bucket_str]


# Keys of a dimension the editor edits; everything else (order, description,
# value_labels, future keys) is kept unchanged when a dimension is saved.
_EDITED_KEYS = {'id', 'label', 'source_question', 'source_column', 'bucketing',
                'enable_specific_clusters', 'specific_clusters_min_n'}


@dataclass
class DimensionDetail:
    """
    Complete dimension for the editor.

    Labels are edited in the source language of the survey; labels in other
    languages, and keys the editor does not show, survive a save unchanged.
    """
    id: str
    label: Dict[str, str] = field(default_factory=lambda: {'de': ''})
    source_question: str = ''
    source_column: str = ''          # alternative to source_question
    bucketing_type: str = 'identity'
    enable_specific_clusters: bool = False
    specific_clusters_min_n: Optional[int] = None  # None: survey default
    source_language: str = 'de'
    
    # Bei value_mapping: roh -> bucket-label
    value_mapping: Dict[str, str] = field(default_factory=dict)
    
    # with numeric_buckets: list of {label_source, min, max, label_all}
    # (label_source = label in the source language)
    numeric_buckets: List[Dict[str, Any]] = field(default_factory=list)
    
    # Unmapped-Strategy
    unmapped_type: str = 'passthrough'  # passthrough | drop | bucket
    unmapped_bucket_label: str = ''     # only for type=bucket
    unmapped_bucket_label_all: Dict[str, str] = field(default_factory=dict)
    
    # Keys not edited here, written back unchanged
    extra: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: dict, source_language: str = 'de') -> 'DimensionDetail':
        """Built from a YAML dict."""
        src = source_language
        d = cls(
            id=data.get('id', ''),
            label=data.get('label', {src: ''}),
            source_question=data.get('source_question', '') or '',
            source_column=data.get('source_column', '') or '',
            enable_specific_clusters=bool(data.get('enable_specific_clusters', False)),
            specific_clusters_min_n=data.get('specific_clusters_min_n'),
            source_language=src,
            extra={k: v for k, v in data.items() if k not in _EDITED_KEYS},
        )
        if isinstance(d.label, str):
            d.label = {src: d.label}
        
        bucketing = data.get('bucketing', {}) or {}
        d.bucketing_type = bucketing.get('type', 'identity')
        
        if d.bucketing_type == 'value_mapping':
            d.value_mapping = dict(bucketing.get('mapping') or {})
        
        if d.bucketing_type == 'numeric_buckets':
            for b in bucketing.get('buckets') or []:
                if isinstance(b, dict):
                    label = b.get('label', {})
                    label_all = {src: label} if isinstance(label, str) else dict(label or {})
                    d.numeric_buckets.append({
                        'label_source': label_all.get(src, ''),
                        'label_all': label_all,
                        'min': b.get('min'),
                        'max': b.get('max'),
                    })
        
        unmapped = bucketing.get('unmapped_strategy') or {}
        if isinstance(unmapped, str):
            d.unmapped_type = unmapped
        elif isinstance(unmapped, dict):
            d.unmapped_type = unmapped.get('type', 'passthrough')
            ubl = unmapped.get('bucket_label', '')
            if isinstance(ubl, dict):
                d.unmapped_bucket_label_all = dict(ubl)
                d.unmapped_bucket_label = ubl.get(src, '')
            else:
                d.unmapped_bucket_label = str(ubl) if ubl else ''
        
        return d
    
    def to_dict(self) -> dict:
        """Converts into the YAML format."""
        src = self.source_language
        result = dict(self.extra)  # unedited keys first, edited keys take precedence
        result['id'] = self.id
        result['label'] = dict(self.label) if self.label else {src: self.id}
        if self.source_column:
            result['source_column'] = self.source_column
        else:
            result['source_question'] = self.source_question
        result['bucketing'] = self._build_bucketing_dict()
        if self.enable_specific_clusters:
            result['enable_specific_clusters'] = True
            if self.specific_clusters_min_n:
                result['specific_clusters_min_n'] = int(self.specific_clusters_min_n)
        return result
    
    def _build_bucketing_dict(self) -> dict:
        src = self.source_language
        bucketing: Dict[str, Any] = {'type': self.bucketing_type}
        
        if self.bucketing_type == 'value_mapping':
            if self.value_mapping:
                bucketing['mapping'] = dict(self.value_mapping)
        elif self.bucketing_type == 'numeric_buckets':
            buckets = []
            for b in self.numeric_buckets:
                label = dict(b.get('label_all') or {})
                label[src] = b.get('label_source', '')
                bucket: Dict[str, Any] = {'label': label}
                if b.get('min') is not None:
                    bucket['min'] = float(b['min'])
                if b.get('max') is not None:
                    bucket['max'] = float(b['max'])
                buckets.append(bucket)
            if buckets:
                bucketing['buckets'] = buckets
        
        # write the unmapped strategy only if it is not the default
        if self.unmapped_type != 'passthrough' or self.unmapped_bucket_label:
            us: Dict[str, Any] = {'type': self.unmapped_type}
            if self.unmapped_type == 'bucket' and self.unmapped_bucket_label:
                us['bucket_label'] = {**self.unmapped_bucket_label_all, src: self.unmapped_bucket_label}
            bucketing['unmapped_strategy'] = us
        
        return bucketing


@dataclass
class DimensionPreview:
    """Distribution preview of a dimension on the current data."""
    dimension_id: str
    rows: List[Tuple[str, int]] = field(default_factory=list)  # [(bucket_label, n)]
    n_total: int = 0
    n_unmapped: int = 0
    error: Optional[str] = None


def _check_full_config(config: dict) -> Optional[str]:
    """Error message if the configuration would be invalid as a whole
    (e.g. a cross filter referencing a deleted dimension), else None."""
    from services.config_validation import validate_survey_config
    errors, _ = validate_survey_config(config)
    return "; ".join(errors) if errors else None


def _source_language(state) -> str:
    from utils.languages import get_source_language
    try:
        return get_source_language(getattr(state, 'config', None))
    except ValueError:
        return 'de'


# =============================================================================
# 1. List of all dimensions
# =============================================================================

def list_dimensions(state, lang: str = 'de') -> List[DimensionSummary]:
    """The dimensions configured in state.config."""
    if state is None or state.config is None:
        return []
    
    settings = state.config.get('settings') or {}
    seg = settings.get('segmentation') or {}
    dims_raw = seg.get('dimensions') or []
    
    result = []
    for d in dims_raw:
        if not isinstance(d, dict):
            continue
        det = DimensionDetail.from_dict(d, _source_language(state))
        
        bucket_count = None
        if det.bucketing_type == 'value_mapping':
            bucket_count = len(det.value_mapping)
        elif det.bucketing_type == 'numeric_buckets':
            bucket_count = len(det.numeric_buckets)
        
        label_source = ''
        if isinstance(det.label, dict):
            label_source = det.label.get(lang) or det.label.get(det.source_language) or det.id
        else:
            label_source = str(det.label) or det.id
        
        result.append(DimensionSummary(
            id=det.id,
            label_source=label_source,
            source_question=det.source_question or f"(column) {det.source_column}",
            bucketing_type=det.bucketing_type,
            bucket_count=bucket_count,
        ))
    
    return result


def get_dimension_detail(state, dim_id: str) -> Optional[DimensionDetail]:
    """The complete configuration of a dimension for the editor."""
    if state is None or state.config is None or not dim_id:
        return None
    
    settings = state.config.get('settings') or {}
    seg = settings.get('segmentation') or {}
    dims_raw = seg.get('dimensions') or []
    
    for d in dims_raw:
        if isinstance(d, dict) and d.get('id') == dim_id:
            return DimensionDetail.from_dict(d, _source_language(state))
    return None


# =============================================================================
# 2. Validation
# =============================================================================

def validate_dimension(state, detail: DimensionDetail,
                        is_new: bool = False) -> Tuple[bool, List[str]]:
    """
    Checks whether a dimension can be saved.
    
    Returns:
        (ok, error_messages)
    """
    errors = []
    
    if not detail.id or not detail.id.strip():
        errors.append("The ID must not be empty.")
    elif not all(c.isalnum() or c in '_-' for c in detail.id):
        errors.append("The ID may only contain letters, digits, '_' and '-'.")
    
    src = detail.source_language
    label_src = (detail.label.get(src, '') if isinstance(detail.label, dict)
                 else str(detail.label))
    if not label_src.strip():
        errors.append(f"Label ({src.upper()}) must not be empty.")
    
    if detail.source_question and detail.source_column:
        errors.append("Give either a source question or a source column, not both.")
    elif detail.source_column:
        if state is not None and getattr(state, 'df', None) is not None \
                and detail.source_column not in state.df.columns:
            errors.append(f"Column '{detail.source_column}' is not in the data.")
    elif not detail.source_question:
        errors.append("A source question or a source column is required.")
    elif state is not None and state.config is not None:
        qg = state.config.get('question_groups') or {}
        if detail.source_question not in qg:
            errors.append(
                f"Source question '{detail.source_question}' is not in the "
                f"configuration."
            )
    
    if detail.specific_clusters_min_n not in (None, '', 0):
        try:
            if int(detail.specific_clusters_min_n) < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("The minimum size for segment-specific clusters must be a whole number ≥ 1.")
    
    if detail.bucketing_type not in ('identity', 'value_mapping', 'numeric_buckets'):
        errors.append(
            f"Invalid bucketing type '{detail.bucketing_type}'. "
            f"Allowed: identity, value_mapping, numeric_buckets."
        )
    
    if detail.bucketing_type == 'value_mapping':
        if not detail.value_mapping:
            errors.append(
                "With 'value_mapping', at least one mapping entry "
                "is required."
            )
    
    if detail.bucketing_type == 'numeric_buckets':
        if not detail.numeric_buckets:
            errors.append(
                "With 'numeric_buckets', at least one bucket "
                "must be defined."
            )
        else:
            for i, b in enumerate(detail.numeric_buckets):
                if not b.get('label_source', '').strip():
                    errors.append(f"Bucket #{i+1}: label missing.")
                if b.get('min') is None and b.get('max') is None:
                    errors.append(
                        f"Bucket #{i+1}: min and max must not both "
                        f"be empty."
                    )
                if (b.get('min') is not None and b.get('max') is not None
                        and b['min'] >= b['max']):
                    errors.append(
                        f"Bucket #{i+1}: min ({b['min']}) must be smaller "
                        f"than max ({b['max']})."
                    )
    
    if detail.unmapped_type == 'bucket' and not detail.unmapped_bucket_label.strip():
        errors.append(
            "With unmapped_strategy='bucket', a bucket_label "
            "is required."
        )
    
    # id conflict
    if is_new and state is not None:
        existing = list_dimensions(state)
        if any(d.id == detail.id for d in existing):
            errors.append(f"A dimension with ID '{detail.id}' already exists.")
    
    return len(errors) == 0, errors


# =============================================================================
# 3. writing (add / update / delete)
# =============================================================================

def save_dimension(state, detail: DimensionDetail, is_new: bool
                    ) -> Tuple[bool, str]:
    """
    Saves a dimension (new or updated) in the questions.yaml.
    
    Args:
        state: AppState, reloaded after a successful write.
        detail: complete DimensionDetail.
        is_new: True to add, False to update.
    
    Returns:
        (ok, status_text).
    """
    ok, errors = validate_dimension(state, detail, is_new=is_new)
    if not ok:
        return False, "❌ Validation error:\n" + "\n".join(
            f"  • {e}" for e in errors
        )
    
    try:
        from services.config_io import (
            load_active_config, write_active_config_with_snapshot,
        )
    except ImportError as e:
        return False, f"config_io not available: {e}"
    
    try:
        config = load_active_config()
    except Exception as e:
        return False, f"Error reading the configuration: {e}"
    
    settings = config.setdefault('settings', {})
    seg = settings.setdefault('segmentation', {})
    dims = seg.setdefault('dimensions', [])
    
    new_dim = detail.to_dict()
    
    if is_new:
        dims.append(new_dim)
        action = "added"
    else:
        # find and replace
        replaced = False
        for i, d in enumerate(dims):
            if isinstance(d, dict) and d.get('id') == detail.id:
                dims[i] = new_dim
                replaced = True
                break
        if not replaced:
            return False, f"❌ Dimension '{detail.id}' to update not found."
        action = "updated"
    
    problem = _check_full_config(config)
    if problem:
        return False, f"❌ Not saved, the configuration would be invalid: {problem}"
    try:
        write_active_config_with_snapshot(
            config, reason=f'segdim_{detail.id}'
        )
    except Exception as e:
        logger.exception("Write error")
        return False, f"❌ Write error: {e}"
    
    # reload the state
    if state is not None:
        try:
            from core import load_config as core_load_config
            core_load_config()
        except Exception as e:
            logger.warning(f"State reload failed: {e}")
    
    return True, (
        f"✅ Dimension '{detail.id}' {action}. "
        f"A snapshot of the previous configuration was taken."
    )


def delete_dimension(state, dim_id: str) -> Tuple[bool, str]:
    """Deletes a dimension from the configuration."""
    if not dim_id:
        return False, "❌ No dimension ID given."
    
    try:
        from services.config_io import (
            load_active_config, write_active_config_with_snapshot,
        )
    except ImportError as e:
        return False, f"config_io not available: {e}"
    
    try:
        config = load_active_config()
    except Exception as e:
        return False, f"Error reading the configuration: {e}"
    
    settings = config.get('settings') or {}
    seg = settings.get('segmentation') or {}
    dims = seg.get('dimensions') or []
    
    new_dims = [d for d in dims
                if not (isinstance(d, dict) and d.get('id') == dim_id)]
    
    if len(new_dims) == len(dims):
        return False, f"❌ Dimension '{dim_id}' not found."
    
    seg['dimensions'] = new_dims
    
    problem = _check_full_config(config)
    if problem:
        return False, f"❌ Not saved, the configuration would be invalid: {problem}"
    try:
        write_active_config_with_snapshot(
            config, reason=f'segdim_delete_{dim_id}'
        )
    except Exception as e:
        return False, f"❌ Write error: {e}"
    
    if state is not None:
        try:
            from core import load_config as core_load_config
            core_load_config()
        except Exception as e:
            logger.warning(f"State reload failed: {e}")
    
    return True, (
        f"✅ Dimension '{dim_id}' deleted. "
        f"A snapshot of the previous configuration was taken."
    )


# =============================================================================
# 4. Distribution preview on the current data
# =============================================================================

def preview_distribution(state, dim_id: str) -> DimensionPreview:
    """
    Calculates the distribution of the answers over the buckets of the dimension.
    
    Uses the SegmentResolver, so the preview shows exactly what the
    analysis will do.
    """
    pv = DimensionPreview(dimension_id=dim_id)
    
    if state is None or state.df is None:
        pv.error = "No data imported"
        return pv
    if state.config is None:
        pv.error = "No configuration loaded"
        return pv
    
    detail = get_dimension_detail(state, dim_id)
    if detail is None:
        pv.error = f"Dimension '{dim_id}' not found"
        return pv
    
    # validate the source question
    qg = state.config.get('question_groups') or {}
    qcfg = qg.get(detail.source_question)
    if qcfg is None:
        pv.error = f"Source question '{detail.source_question}' not in the configuration"
        return pv
    
    columns = qcfg.get('columns', []) or []
    if not columns:
        pv.error = f"Source question '{detail.source_question}' has no column"
        return pv
    col = columns[0]
    
    if col not in state.df.columns:
        pv.error = f"Column '{col}' not in the data"
        return pv
    
    pv.n_total = len(state.df)
    
    # calculate the bucket values
    try:
        from utils.segment_resolver import SegmentResolver
    except ImportError as e:
        pv.error = f"segment_resolver not available: {e}"
        return pv
    
    # resolver workflow:
    #   1. SegmentResolver(config): takes only the configuration
    #   2. resolver.materialize(df): adds __segment_<dim_id>__ columns to df
    #   3. read the value_counts from the materialised column
    try:
        resolver = SegmentResolver(state.config)
        df_with_segments = resolver.materialize(state.df)
        seg_col = resolver.segment_column(dim_id)
        if seg_col not in df_with_segments.columns:
            pv.error = f"Segment column '{seg_col}' not in the data after materialize()"
            return pv
        bucket_series = df_with_segments[seg_col]
    except Exception as e:
        pv.error = f"Bucketing error: {e}"
        return pv
    
    # Counts
    counts = bucket_series.value_counts(dropna=False)
    
    rows = []
    n_unmapped = 0
    for bucket_label, n in counts.items():
        # detect NaN safely (also without importing pandas here)
        is_unmapped = (
            bucket_label is None
            or (isinstance(bucket_label, float) and str(bucket_label) == 'nan')
        )
        if is_unmapped:
            n_unmapped += int(n)
            rows.append(("(unmapped)", int(n)))
        else:
            rows.append((str(bucket_label), int(n)))
    
    pv.rows = rows
    pv.n_unmapped = n_unmapped
    return pv


# =============================================================================
# 5. UI-Helper
# =============================================================================

def render_dimension_table(summaries: List[DimensionSummary]) -> List[List[Any]]:
    """gr.Dataframe rows for the list of dimensions."""
    return [s.display_row() for s in summaries]


def get_question_choices_for_dropdown(state) -> List[str]:
    """List of all question ids to choose a source question from."""
    if state is None or state.config is None:
        return []
    qg = state.config.get('question_groups') or {}
    return list(qg.keys())


def value_mapping_to_table(mapping: Dict[str, str]) -> List[List[str]]:
    """Turns the mapping dict into a table."""
    return [[k, v] for k, v in mapping.items()]


def value_mapping_from_table(rows: Any) -> Dict[str, str]:
    """
    Turns editor table rows back into the mapping dict.
    
    Accepts a pandas.DataFrame, a list of lists and None.
    """
    mapping: Dict[str, str] = {}
    if rows is None:
        return mapping
    
    # pandas.DataFrame -> values
    if hasattr(rows, 'values'):
        rows = rows.values.tolist()
    
    for row in rows:
        if not row or len(row) < 2:
            continue
        key = str(row[0] or '').strip()
        val = str(row[1] or '').strip()
        if key and val:
            mapping[key] = val
    return mapping


def numeric_buckets_to_table(buckets: List[Dict[str, Any]]) -> List[List[Any]]:
    """Turns numeric buckets into table rows."""
    rows = []
    for b in buckets:
        rows.append([
            b.get('label_source', ''),
            b.get('min') if b.get('min') is not None else '',
            b.get('max') if b.get('max') is not None else '',
        ])
    return rows


def numeric_buckets_from_table(rows: Any) -> List[Dict[str, Any]]:
    """Turns table rows into a list of numeric buckets."""
    buckets: List[Dict[str, Any]] = []
    if rows is None:
        return buckets
    
    if hasattr(rows, 'values'):
        rows = rows.values.tolist()
    
    for row in rows:
        if not row or len(row) < 1:
            continue
        label = str(row[0] or '').strip()
        if not label:
            continue
        
        def _opt_float(v):
            if v is None or v == '' or (isinstance(v, float) and str(v) == 'nan'):
                return None
            try:
                return float(str(v).replace(',', '.'))
            except (ValueError, TypeError):
                return None
        
        b = {
            'label_source': label,
            'min': _opt_float(row[1] if len(row) > 1 else None),
            'max': _opt_float(row[2] if len(row) > 2 else None),
        }
        buckets.append(b)
    return buckets


# =============================================================================
# Settings for the whole segmentation: cross filters and thresholds
# =============================================================================

def get_segmentation_settings(state) -> Dict[str, Any]:
    """cross_filters, specific_clusters_min_n and cross_filter_min_n as configured."""
    from utils.segment_resolver import (DEFAULT_CROSS_FILTER_MIN_N,
                                        DEFAULT_SPECIFIC_CLUSTERS_MIN_N)
    seg = ((getattr(state, 'config', None) or {}).get('settings') or {}).get('segmentation') or {}
    return {
        'cross_filters': [list(c) for c in seg.get('cross_filters') or []],
        'specific_clusters_min_n': seg.get('specific_clusters_min_n', DEFAULT_SPECIFIC_CLUSTERS_MIN_N),
        'cross_filter_min_n': seg.get('cross_filter_min_n', DEFAULT_CROSS_FILTER_MIN_N),
    }


def cross_filter_choices(state) -> List[str]:
    """All combinations of 2 or 3 defined dimensions, as 'a + b' strings."""
    from itertools import combinations
    ids = [d.id for d in list_dimensions(state)]
    combos = [c for n in (2, 3) for c in combinations(ids, n)]
    return [" + ".join(c) for c in combos]


def save_segmentation_settings(state, cross_filters: List[str],
                               specific_clusters_min_n: Any,
                               cross_filter_min_n: Any) -> Tuple[bool, str]:
    """
    Save cross filters ('a + b' strings from cross_filter_choices) and the two
    thresholds. Cross filters can only reference defined dimensions; the
    whole configuration is validated before anything is written.
    """
    try:
        from services.config_io import load_active_config, write_active_config_with_snapshot
        config = load_active_config()
    except Exception as e:
        return False, f"❌ Error reading the configuration: {e}"
    seg = config.setdefault('settings', {}).setdefault('segmentation', {})
    try:
        seg['specific_clusters_min_n'] = int(specific_clusters_min_n)
        seg['cross_filter_min_n'] = int(cross_filter_min_n)
    except (TypeError, ValueError):
        return False, "❌ Thresholds must be whole numbers."
    combos = [[part.strip() for part in c.split('+')] for c in (cross_filters or [])]
    if combos:
        seg['cross_filters'] = combos
    else:
        seg.pop('cross_filters', None)
    problem = _check_full_config(config)
    if problem:
        return False, f"❌ Not saved, the configuration would be invalid: {problem}"
    try:
        write_active_config_with_snapshot(config, reason='segmentation_settings')
    except Exception as e:
        logger.exception("Write error")
        return False, f"❌ Write error: {e}"
    try:
        from core import load_config as core_load_config
        core_load_config()
    except Exception as e:
        logger.warning(f"State reload failed: {e}")
    return True, (f"✅ Saved: {len(combos)} cross filters, thresholds "
                  f"{seg['specific_clusters_min_n']} / {seg['cross_filter_min_n']}. "
                  f"Snapshot taken.")
