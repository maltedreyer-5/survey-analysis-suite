# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the schema methods of handlers/base.py.

Verifies:
- BaseHandler has default_summary_schema() and build_summary_context()
- all handlers inherit the default schema
- the default schemas are valid and keep the previous behaviour
- the SchemaResolver finds the defaults through handler_registry

Run:  python tests/suite_handler_schemas.py
"""

import sys
import os
import types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# stub the heavy imports BEFORE the handler modules are loaded.
# utils/__init__ imports a number of heavy dependencies; the test stubs all
# that are not needed for its logic.
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
_stub('langdetect',
      detect=lambda x: 'de',
      DetectorFactory=type('DF', (), {'seed': 0})())
_stub('langdetect.lang_detect_exception',
      LangDetectException=Exception)


# if more are missing: skip utils/__init__, load utils.summary_schema and
# utils.charts directly
if 'utils' in sys.modules:
    del sys.modules['utils']

# stub of the utils package (prevents the imports of its __init__.py)
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg

# stub utils.charts manually (imported transitively by the handlers)
charts_stub = types.ModuleType('utils.charts')
charts_stub.ChartGenerator = type('ChartGenerator', (), {
    'DEFAULT_COLORS': {'positive': '#5cb85c', 'negative': '#d9534f', 'neutral': '#999'}
})
charts_stub.create_chart = lambda *a, **kw: None
sys.modules['utils.charts'] = charts_stub

# load utils.summary_schema and utils.numeric_coerce directly from the file system
import importlib.util
for mod_name in ['summary_schema', 'numeric_coerce']:
    spec = importlib.util.spec_from_file_location(
        f'utils.{mod_name}',
        os.path.join(os.path.dirname(__file__), '..', 'utils', f'{mod_name}.py')
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f'utils.{mod_name}'] = mod
    spec.loader.exec_module(mod)

import pandas as pd

PASS = 0
FAIL = 0
RESULTS = []


def assert_eq(label, actual, expected):
    global PASS, FAIL
    if actual == expected:
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


# ===========================================================================
# 1. BaseHandler has the schema methods
# ===========================================================================

print("=" * 60)
print("BaseHandler — schema methods")
print("=" * 60)

from handlers.base import BaseHandler, AnalysisResult

assert_true("default_summary_schema present",
            hasattr(BaseHandler, 'default_summary_schema'))
assert_true("default_summary_schema is a classmethod",
            isinstance(getattr(BaseHandler, '__dict__', {}).get('default_summary_schema'), classmethod))
assert_true("build_summary_context present",
            hasattr(BaseHandler, 'build_summary_context'))


# ===========================================================================
# 2. BaseHandler.default_summary_schema() returns the default schema
# ===========================================================================

print()
print("=" * 60)
print("BaseHandler.default_summary_schema()")
print("=" * 60)

schema = BaseHandler.default_summary_schema()
assert_eq("schema.handler_type", schema.handler_type, "base")
assert_eq("schema.schema_id", schema.schema_id, "base_default")
assert_eq("number of sections", len(schema.sections), 3)
assert_eq("order of the section ids",
          [s.id for s in schema.sections],
          ['description', 'interpretation', 'segments_text'])

# the section texts must match the default schema
from utils.summary_schema import DEFAULT_THREE_SECTIONS_SCHEMA

assert_eq("description label DE",
          schema.sections[0].label['de'],
          DEFAULT_THREE_SECTIONS_SCHEMA.sections[0].label['de'])
assert_eq("description prompt_instruction",
          schema.sections[0].prompt_instruction,
          DEFAULT_THREE_SECTIONS_SCHEMA.sections[0].prompt_instruction)
assert_eq("interpretation label DE",
          schema.sections[1].label['de'],
          DEFAULT_THREE_SECTIONS_SCHEMA.sections[1].label['de'])
assert_eq("segments_text conditional",
          schema.sections[2].conditional,
          'has_segmentation')


# ===========================================================================
# 3. All handlers inherit the default
# ===========================================================================

print()
print("=" * 60)
print("handler classes: default_summary_schema()")
print("=" * 60)

# import the handler classes (the stubs are set above)
from handlers.single_choice       import SingleChoiceHandler
from handlers.multi_choice        import MultiChoiceHandler
from handlers.multi_choice_binary import MultiChoiceBinaryHandler
from handlers.freetext            import FreetextHandler
from handlers.cooperation_matrix  import CooperationMatrixHandler
from handlers.matrix_likert       import MatrixLikertHandler
from handlers.ranking             import RankingHandler

HANDLER_CLASSES = {
    'single_choice':       SingleChoiceHandler,
    'multi_choice':        MultiChoiceHandler,
    'multi_choice_binary': MultiChoiceBinaryHandler,
    'freetext':            FreetextHandler,
    'cooperation_matrix':  CooperationMatrixHandler,
    'matrix_likert':       MatrixLikertHandler,
    'ranking':             RankingHandler,
}

DEFAULT_IDS = ['description', 'interpretation', 'segments_text']

for name, cls in HANDLER_CLASSES.items():
    schema = cls.default_summary_schema()
    
    # the schema really belongs to the handler type
    assert_eq(f"{name}: schema.handler_type", schema.handler_type, name)
    assert_eq(f"{name}: schema.schema_id", schema.schema_id, f"{name}_default")
    
    # compatible with the default: the same 3 section ids in the same order
    assert_eq(f"{name}: 3 sections", len(schema.sections), 3)
    assert_eq(f"{name}: section ids", [s.id for s in schema.sections], DEFAULT_IDS)
    
    # at least a DE label
    assert_true(f"{name}: section[0] label DE",
                bool(schema.sections[0].label.get('de')))


# ===========================================================================
# 4. the SchemaResolver finds the defaults through the handler registry
# ===========================================================================

print()
print("=" * 60)
print("SchemaResolver with the handler registry")
print("=" * 60)

from utils.summary_schema import SchemaResolver

resolver = SchemaResolver(
    survey_config={'question_groups': {'q1': {}, 'q2': {}}},
    handler_registry=HANDLER_CLASSES,
    survey_id='test',
)

for name in HANDLER_CLASSES.keys():
    schema = resolver.resolve(question_id='q1', handler_type=name)
    assert_eq(f"resolver: {name} -> {name}_default",
              schema.schema_id, f"{name}_default")

# a survey override wins over the handler default
config_with_override = {
    'settings': {
        'summary_schema_overrides': {
            'single_choice': {
                'sections': [
                    {'id': 'custom', 'label': {'de': 'Custom'},
                     'prompt_instruction': 'x'},
                ],
            }
        }
    },
    'question_groups': {'q1': {}},
}
resolver2 = SchemaResolver(
    survey_config=config_with_override,
    handler_registry=HANDLER_CLASSES,
    survey_id='test',
)
schema = resolver2.resolve(question_id='q1', handler_type='single_choice')
assert_eq("survey override wins", schema.schema_id, 'survey:test:single_choice')
assert_eq("override section count", len(schema.sections), 1)

# other handler types keep their default
schema2 = resolver2.resolve(question_id='q1', handler_type='freetext')
assert_eq("freetext stays default", schema2.schema_id, 'freetext_default')


# ===========================================================================
# 5. build_summary_context() works
# ===========================================================================

print()
print("=" * 60)
print("build_summary_context()")
print("=" * 60)

# Handler instanziieren
handler = SingleChoiceHandler(config={'id': 'q1', 'columns': ['x']})

result_basic = AnalysisResult(
    question_id='q1', n=42,
    data={'A': 20, 'B': 15, 'C': 7},
)
ctx = handler.build_summary_context(
    result_basic,
    segment_dimension_count=3,
    has_significant_segments=True,
)
assert_eq("ctx has_segmentation", ctx['has_segmentation'], True)
assert_eq("ctx has_segmentation_significant", ctx['has_segmentation_significant'], True)
assert_eq("ctx n", ctx['n'], 42)
assert_eq("ctx n_categories", ctx['n_categories'], 3)
assert_eq("ctx has_clusters (no clusters)", ctx['has_clusters'], False)

result_with_clusters = AnalysisResult(
    question_id='q1', n=10, data={},
    clusters=[{'id': 1, 'label': 'A'}, {'id': 2, 'label': 'B'}],
)
ctx2 = handler.build_summary_context(result_with_clusters)
assert_eq("ctx2 has_segmentation (default 0)", ctx2['has_segmentation'], False)
assert_eq("ctx2 has_clusters", ctx2['has_clusters'], True)
assert_eq("ctx2 n_clusters", ctx2['n_clusters'], 2)


# ===========================================================================
# 6. The conditions are evaluated with the context
# ===========================================================================

print()
print("=" * 60)
print("Schema + Context Integration")
print("=" * 60)

# with has_segmentation=False, segments_text must not be in applicable_sections
schema = SingleChoiceHandler.default_summary_schema()
applicable = schema.applicable_sections({
    'has_segmentation': False, 'has_segmentation_significant': False,
})
assert_eq("without segmentation: only 2 sections",
          [s.id for s in applicable], ['description', 'interpretation'])

applicable = schema.applicable_sections({
    'has_segmentation': True, 'has_segmentation_significant': True,
})
assert_eq("with segmentation: all 3 sections",
          [s.id for s in applicable], DEFAULT_IDS)


# ===========================================================================
# 7. End-to-end: handler -> default_summary_schema -> build_summary_context
#    -> applicable_sections, the full pipeline
# ===========================================================================

print()
print("=" * 60)
print("E2E: Handler-Pipeline")
print("=" * 60)

handler = FreetextHandler(config={'id': 'q1', 'columns': ['x']})
schema = handler.__class__.default_summary_schema()

result = AnalysisResult(
    question_id='q1', n=50,
    data={},
    clusters=[{'id': 1, 'label': 'A'}, {'id': 2, 'label': 'B'}, {'id': 3, 'label': 'C'}],
)
ctx = handler.build_summary_context(
    result, segment_dimension_count=2, has_significant_segments=False,
)
applicable = schema.applicable_sections(ctx)

assert_eq("freetext with segments (not significant): all 3 sections",
          [s.id for s in applicable], DEFAULT_IDS)

# a stricter condition (only on significance) can be set by a survey
# override, but the default is 'has_segmentation', not
# 'has_segmentation_significant'.


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
