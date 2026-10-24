# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of summary_schema, numeric_coerce and segment_resolver.

Run:  python tests/suite_core_modules.py
"""

import sys
import os
import types
import importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# heavy stubs before anything else, so the test also runs in the full app
# environment without the httpx/langdetect packages installed.
def _stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


_stub('httpx',
      Client=lambda *a, **kw: None,
      HTTPStatusError=Exception,
      TimeoutException=Exception,
      RequestError=Exception)
_stub('langdetect', detect=lambda x: 'de')

# utils package anew, so its __init__.py is skipped
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# load the modules directly (without the utils/__init__ imports)
_load('utils.summary_schema',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'summary_schema.py'))
_load('utils.numeric_coerce',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'segment_resolver.py'))

import numpy as np
import pandas as pd

# Modules under test
from utils.summary_schema import (
    Section, SummarySchema, SegmentFilter, SchemaResolver,
    DEFAULT_THREE_SECTIONS_SCHEMA, GENERIC_FALLBACK_SCHEMA,
    is_default_summary,
)
from utils.numeric_coerce import (
    coerce_to_float, coerce_series, coerce_stats_summary, is_numeric_column,
)
from utils.segment_resolver import (
    Bucket, BucketingStrategy, Dimension, UnmappedStrategy,
    SegmentResolver, is_materialized, list_segment_columns,
)


# ===========================================================================
# Helpers
# ===========================================================================

PASS = 0
FAIL = 0
RESULTS = []


def assert_eq(label, actual, expected):
    global PASS, FAIL
    if actual == expected or (
        isinstance(actual, float) and isinstance(expected, float)
        and (np.isnan(actual) and np.isnan(expected))
    ):
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected {expected!r}, got {actual!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


def assert_raises(test_label, exc_type, callable_, *args, **kwargs):
    global PASS, FAIL
    try:
        callable_(*args, **kwargs)
        FAIL += 1
        RESULTS.append(('FAIL', f"{test_label}: expected {exc_type.__name__}, no error"))
    except exc_type:
        PASS += 1
        RESULTS.append(('OK', test_label))
    except Exception as e:
        FAIL += 1
        RESULTS.append(('FAIL', f"{test_label}: expected {exc_type.__name__}, got {type(e).__name__}: {e}"))


# ===========================================================================
# numeric_coerce Tests
# ===========================================================================

print("=" * 60)
print("numeric_coerce")
print("=" * 60)

# numeric inputs
assert_eq("int -> float", coerce_to_float(42), 42.0)
assert_eq("float", coerce_to_float(3.14), 3.14)
assert_eq("None -> NaN", coerce_to_float(None), np.nan)
assert_eq("'' -> NaN", coerce_to_float(""), np.nan)
assert_eq("'   ' -> NaN", coerce_to_float("   "), np.nan)
assert_eq("'abc' -> NaN", coerce_to_float("abc"), np.nan)

# comma vs. dot
assert_eq("'3.14'", coerce_to_float("3.14"), 3.14)
assert_eq("'3,14'", coerce_to_float("3,14"), 3.14)

# Tausendertrenner
assert_eq("'1.234,56' (de)", coerce_to_float("1.234,56"), 1234.56)
assert_eq("'1,234.56' (en)", coerce_to_float("1,234.56"), 1234.56)
assert_eq("'1234'", coerce_to_float("1234"), 1234.0)

# Whitespace
assert_eq("'  42  '", coerce_to_float("  42  "), 42.0)
assert_eq("'1 234' (with spaces)", coerce_to_float("1 234"), 1234.0)

# Suffix
assert_eq("'15%'", coerce_to_float("15%"), 15.0)
assert_eq("'7 stueck'", coerce_to_float("7 stueck"), 7.0)

# Praefix
assert_eq("'ca. 50'", coerce_to_float("ca. 50"), 50.0)
assert_eq("'~ 100'", coerce_to_float("~ 100"), 100.0)

# Series
s = pd.Series(["3,14", "42", "", None, "ca. 100", "abc"])
res = coerce_series(s)
assert_eq("series len", len(res), 6)
assert_eq("series[0]", res.iloc[0], 3.14)
assert_eq("series[1]", res.iloc[1], 42.0)
assert_true("series[2] NaN", np.isnan(res.iloc[2]))
assert_true("series[3] NaN", np.isnan(res.iloc[3]))
assert_eq("series[4]", res.iloc[4], 100.0)
assert_true("series[5] NaN", np.isnan(res.iloc[5]))

# Stats summary
stats = coerce_stats_summary(s)
assert_eq("stats n_valid", stats['n_valid'], 3)
assert_eq("stats n_invalid", stats['n_invalid'], 3)

# is_numeric_column
assert_true("numeric col detected", is_numeric_column(pd.Series([1, 2, 3, 4])))
assert_true("string-numeric col detected", is_numeric_column(pd.Series(["1", "2", "3"])))
assert_true("mixed col not numeric", not is_numeric_column(pd.Series(["abc", "def", "1"])))


# ===========================================================================
# summary_schema Tests
# ===========================================================================

print()
print("=" * 60)
print("summary_schema")
print("=" * 60)

# section validation
assert_raises("section without id", ValueError,
              Section, id="", label={'de': 'X'}, prompt_instruction="x")
assert_raises("section without label", ValueError,
              Section, id="x", label={'de': ''}, prompt_instruction="x")
# English surveys need no German label
assert_eq("section only with label.en", Section(id="x", label={'en': 'X'}, prompt_instruction="x").get_label('de'), 'X')

# SegmentFilter
sf = SegmentFilter(include=['country'])
assert_true("SegmentFilter applies country", sf.applies_to('country', ['country', 'size']))
assert_true("SegmentFilter excludes size", not sf.applies_to('size', ['country', 'size']))

# SummarySchema validation
assert_raises("schema with duplicate section ids", ValueError,
              SummarySchema,
              schema_id='dup', handler_type='x',
              sections=[
                  Section(id='a', label={'de': 'A'}, prompt_instruction='x'),
                  Section(id='a', label={'de': 'A2'}, prompt_instruction='x'),
              ])

# applicable_sections
schema = SummarySchema(
    schema_id='t', handler_type='x',
    sections=[
        Section(id='always', label={'de': 'A'}, prompt_instruction='x'),
        Section(id='only_seg', label={'de': 'B'}, prompt_instruction='x',
                conditional='has_segmentation'),
        Section(id='only_many', label={'de': 'C'}, prompt_instruction='x',
                conditional='n_categories>3'),
    ],
)
applicable = schema.applicable_sections({'has_segmentation': True, 'n_categories': 2})
assert_eq("applicable: only seg", [s.id for s in applicable], ['always', 'only_seg'])

applicable = schema.applicable_sections({'has_segmentation': False, 'n_categories': 5})
assert_eq("applicable: only many categories", [s.id for s in applicable], ['always', 'only_many'])

# Negation
neg_schema = SummarySchema(
    schema_id='t2', handler_type='x',
    sections=[
        Section(id='no_outliers', label={'de': 'A'}, prompt_instruction='x',
                conditional='!has_outliers'),
    ],
)
applicable = neg_schema.applicable_sections({'has_outliers': True})
assert_eq("negation: excluded", [s.id for s in applicable], [])
applicable = neg_schema.applicable_sections({'has_outliers': False})
assert_eq("negation: included", [s.id for s in applicable], ['no_outliers'])

# reproduction of the default schema
assert_eq("default schema schema_id", DEFAULT_THREE_SECTIONS_SCHEMA.schema_id, 'default_three_sections')
assert_eq("default schema section count", len(DEFAULT_THREE_SECTIONS_SCHEMA.sections), 3)
assert_eq("default schema ids",
          [s.id for s in DEFAULT_THREE_SECTIONS_SCHEMA.sections],
          ['description', 'interpretation', 'segments_text'])

# is_legacy_summary
assert_true("default schema detection true",
            is_default_summary({'description': 'x', 'interpretation': 'y'}))
assert_true("default schema detection false (extra key)",
            not is_default_summary({'description': 'x', 'distribution': 'y'}))
assert_true("default schema detection false (empty)",
            not is_default_summary({}))


# SchemaResolver: all four resolution levels
class _StubHandler:
    @classmethod
    def default_summary_schema(cls):
        return SummarySchema(
            schema_id='stub_default',
            handler_type='stub',
            sections=[Section(id='x', label={'de': 'X'}, prompt_instruction='x')],
        )

# level 4: generic fallback (no handler known)
r = SchemaResolver(survey_config={}, handler_registry={}, survey_id='test')
schema = r.resolve(question_id='q1', handler_type='unknown_type')
assert_eq("fallback schema_id", schema.schema_id, 'generic_fallback')

# level 3: handler default
r = SchemaResolver(survey_config={}, handler_registry={'stub': _StubHandler}, survey_id='test')
schema = r.resolve(question_id='q1', handler_type='stub')
assert_eq("handler default", schema.schema_id, 'stub_default')

# level 2: survey override
config = {
    'settings': {
        'summary_schema_overrides': {
            'stub': {
                'sections': [
                    {'id': 'survey_only', 'label': {'de': 'S'},
                     'prompt_instruction': 'x'},
                ],
            }
        }
    },
    'question_groups': {'q1': {}},
}
r = SchemaResolver(survey_config=config, handler_registry={'stub': _StubHandler},
                   survey_id='test')
schema = r.resolve(question_id='q1', handler_type='stub')
assert_eq("survey override schema_id", schema.schema_id, 'survey:test:stub')
assert_eq("survey override section", [s.id for s in schema.sections], ['survey_only'])

# level 1: question override
config['question_groups']['q1'] = {
    'summary_schema_override': {
        'sections': [
            {'id': 'question_only', 'label': {'de': 'Q'},
             'prompt_instruction': 'x'},
        ],
    }
}
r = SchemaResolver(survey_config=config, handler_registry={'stub': _StubHandler},
                   survey_id='test')
schema = r.resolve(question_id='q1', handler_type='stub')
assert_eq("question override wins",
          [s.id for s in schema.sections], ['question_only'])

# cache behaviour
schema_a = r.resolve(question_id='q1', handler_type='stub')
schema_b = r.resolve(question_id='q1', handler_type='stub')
assert_true("cache returns same instance", schema_a is schema_b)


# ===========================================================================
# segment_resolver Tests
# ===========================================================================

print()
print("=" * 60)
print("segment_resolver")
print("=" * 60)

# bucket validation
assert_raises("bucket both None", ValueError,
              Bucket, label={'de': 'x'}, min=None, max=None)
assert_raises("Bucket min >= max", ValueError,
              Bucket, label={'de': 'x'}, min=10, max=5)

b = Bucket(label={'de': 'mid'}, min=10, max=20)
assert_true("Bucket contains 15", b.contains(15.0))
assert_true("bucket excl. max (20)", not b.contains(20.0))
assert_true("bucket incl. min (10)", b.contains(10.0))
assert_true("Bucket NaN", not b.contains(float('nan')))

# Bucket-Ueberlappung
assert_raises("Buckets ueberlappen", ValueError,
              BucketingStrategy,
              type='numeric_buckets',
              buckets=[
                  Bucket(label={'de': 'a'}, max=10),
                  Bucket(label={'de': 'b'}, min=8, max=20),
              ])

# dimension validation
assert_raises("Dimension.id with spaces", ValueError,
              Dimension, id='bad id', label={'de': 'X'},
              source_question='q', bucketing=BucketingStrategy(type='identity'))

# UnmappedStrategy
assert_raises("UnmappedStrategy.type ungueltig", ValueError,
              UnmappedStrategy, type='wat')

us = UnmappedStrategy(type='bucket')
assert_eq("UnmappedStrategy default bucket label", us.bucket_label['de'], 'Sonstige')


# Materialize: identity
config_identity = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'country', 'label': {'de': 'Land'},
                 'source_question': 'qcountry',
                 'bucketing': {'type': 'identity'}},
            ]
        }
    },
    'question_groups': {
        'qcountry': {'columns': ['Welches Land?']},
    },
}
df = pd.DataFrame({'Welches Land?': ['DE', 'AT', 'CH', None, '']})
sr = SegmentResolver(config_identity)
out = sr.materialize(df)
assert_true("identity: column created", '__segment_country__' in out.columns)
assert_eq("identity: DE", out['__segment_country__'].iloc[0], 'DE')
assert_true("identity: NA for None",
            pd.isna(out['__segment_country__'].iloc[3]))
assert_true("identity: NA for ''",
            pd.isna(out['__segment_country__'].iloc[4]))


# materialize: value_mapping with unmapped passthrough
config_mapping = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'group', 'label': {'de': 'Gruppe'},
                 'source_question': 'qtype',
                 'bucketing': {
                     'type': 'value_mapping',
                     'mapping': {
                         'Universität': 'Universitäre',
                         'Pädagogische Hochschule': 'Universitäre',
                         'FH': 'Anwendung',
                     },
                     'unmapped_strategy': {'type': 'passthrough'},
                 }},
            ]
        }
    },
    'question_groups': {
        'qtype': {'columns': ['Hochschultyp'],
                   'options': {'de': ['Universität', 'Pädagogische Hochschule',
                                       'FH', 'Sonstiges']}},
    },
}
df = pd.DataFrame({
    'Hochschultyp': ['Universität', 'FH', 'Pädagogische Hochschule', 'Sonstiges', None]
})
sr = SegmentResolver(config_mapping)
out = sr.materialize(df)
col = '__segment_group__'
assert_eq("mapping: Univ", out[col].iloc[0], 'Universitäre')
assert_eq("mapping: FH", out[col].iloc[1], 'Anwendung')
assert_eq("mapping: päd -> Univ", out[col].iloc[2], 'Universitäre')
assert_eq("mapping: passthrough", out[col].iloc[3], 'Sonstiges')
assert_true("mapping: None -> NA", pd.isna(out[col].iloc[4]))


# materialize: value_mapping with unmapped drop
config_mapping['settings']['segmentation']['dimensions'][0]['bucketing']['unmapped_strategy'] = {
    'type': 'drop'
}
sr = SegmentResolver(config_mapping)
out = sr.materialize(df)
col = '__segment_group__'
assert_true("mapping/drop: Sonstiges -> NA", pd.isna(out[col].iloc[3]))


# materialize: value_mapping with unmapped bucket
config_mapping['settings']['segmentation']['dimensions'][0]['bucketing']['unmapped_strategy'] = {
    'type': 'bucket',
    'bucket': 'Andere'
}
sr = SegmentResolver(config_mapping)
out = sr.materialize(df)
col = '__segment_group__'
assert_eq("mapping/bucket: Sonstiges -> Andere", out[col].iloc[3], 'Andere')


# Materialize: numeric_buckets
config_num = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'size_class', 'label': {'de': 'Größe'},
                 'source_question': 'qsize',
                 'bucketing': {
                     'type': 'numeric_buckets',
                     'buckets': [
                         {'label': {'de': 'klein'}, 'max': 10},
                         {'label': {'de': 'mittel'}, 'min': 10, 'max': 50},
                         {'label': {'de': 'groß'}, 'min': 50},
                     ],
                     'unmapped_strategy': {'type': 'drop'},
                 }},
            ]
        }
    },
    'question_groups': {
        'qsize': {'columns': ['Anzahl']},
    },
}
df = pd.DataFrame({'Anzahl': ['5', '15', '100', '1.234,5', None, 'abc']})
sr = SegmentResolver(config_num)
out = sr.materialize(df)
col = '__segment_size_class__'
assert_eq("num: 5 -> klein", out[col].iloc[0], 'klein')
assert_eq("num: 15 -> mittel", out[col].iloc[1], 'mittel')
assert_eq("num: 100 -> groß", out[col].iloc[2], 'groß')
assert_eq("num: 1234.5 -> groß", out[col].iloc[3], 'groß')
assert_true("num: None -> NA", pd.isna(out[col].iloc[4]))
assert_true("num: 'abc' -> NA (drop)", pd.isna(out[col].iloc[5]))


# edge case: empty configuration
sr_empty = SegmentResolver({})
assert_eq("empty configuration: no dimensions", len(sr_empty.list_dimensions()), 0)
out = sr_empty.materialize(pd.DataFrame({'x': [1, 2, 3]}))
assert_eq("empty configuration: no __segment columns", len(list_segment_columns(out)), 0)


# Edge: Ungueltige source_question
config_bad = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'x', 'label': {'de': 'X'},
                 'source_question': 'does_not_exist',
                 'bucketing': {'type': 'identity'}},
            ]
        }
    },
    'question_groups': {'q1': {'columns': ['Col']}},
}
assert_raises("ungueltige source_question", ValueError,
              SegmentResolver, config_bad)


# edge case: duplicate ids
config_dup = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'x', 'label': {'de': 'X'}, 'source_question': 'q1',
                 'bucketing': {'type': 'identity'}},
                {'id': 'x', 'label': {'de': 'X2'}, 'source_question': 'q1',
                 'bucketing': {'type': 'identity'}},
            ]
        }
    },
    'question_groups': {'q1': {'columns': ['c1']}},
}
assert_raises("duplicate dimension ids", ValueError, SegmentResolver, config_dup)


# the earlier format (settings.segmentation.<id> with column) is rejected
per_key_config = {
    'settings': {'segmentation': {'country': {'column': 'Land?', 'label': {'de': 'Land'}}}},
    'question_groups': {'country': {'columns': ['Land?']}},
}
assert_raises("earlier format rejected", ValueError, SegmentResolver, per_key_config)

ordered_config = {
    'settings': {'segmentation': {'dimensions': [
        {'id': 'country', 'label': {'de': 'Land', 'fr': 'Pays', 'en': 'Country'},
         'source_question': 'country', 'order': ['DE', 'AT', 'CH']},
    ]}},
    'question_groups': {'country': {'columns': ['Land?']}},
}

# get_segment_values with an order
df = pd.DataFrame({'Land?': ['CH', 'DE', 'AT', 'DE', 'CH', 'DE']})
sr = SegmentResolver(ordered_config)
out = sr.materialize(df)
values = sr.get_segment_values(out, 'country')
assert_eq("ordered values", values, ['DE', 'AT', 'CH'])  # gemaess order

# Helpers
assert_true("is_materialized true", is_materialized(out))
assert_true("is_materialized false", not is_materialized(df))


# ===========================================================================
# Summary
# ===========================================================================

print()
print("=" * 60)
print(f"TOTAL: {PASS} OK, {FAIL} FAIL")
print("=" * 60)
if FAIL > 0:
    print()
    for status, msg in RESULTS:
        if status == 'FAIL':
            print(f"  [{status}] {msg}")
    sys.exit(1)
print("All tests passed.")
