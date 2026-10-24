# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of persistence (CREATE TABLE), question_summary (compute_segment_significance)
and export_word (section renderer).

Verifies:
1. CREATE TABLE question_summaries has schema_id and sections_json
2. compute_segment_significance uses the SegmentResolver
3. _add_question_summary in export_word iterates over the sections
4. the default-schema path keeps the previous heading order exactly
5. custom sections are rendered with their section ids as headings
6. render_summary_with_schema works with any schema

Run: python tests/suite_persistence_and_export.py
"""

import sys
import os
import types
import importlib.util
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


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


# stub charts and docx: the actual Excel/Word output is not tested here
ch = types.ModuleType('utils.charts')
ch.ChartGenerator = type('CG', (), {
    'DEFAULT_COLORS': {'positive': '#5cb85c', 'negative': '#d9534f', 'neutral': '#999'}
})
sys.modules['utils.charts'] = ch

# load the modules
_load('utils.summary_schema',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'summary_schema.py'))
_load('utils.numeric_coerce',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'segment_resolver.py'))
_load('utils.segmentation',
      os.path.join(os.path.dirname(__file__), '..', 'utils', 'segmentation.py'))


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
        a = repr(actual)[:200]
        e = repr(expected)[:200]
        RESULTS.append(('FAIL', f"{label}: expected {e}, got {a}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} not in {container!r}"))


# ==========================================================================
# 1. persistence.py: CREATE TABLE holds schema_id and sections_json
# ==========================================================================

print("=" * 60)
print("persistence.py — CREATE TABLE question_summaries")
print("=" * 60)

# static reading of the CREATE statement
persistence_path = os.path.join(os.path.dirname(__file__), '..', 'utils', 'persistence.py')
with open(persistence_path, encoding='utf-8') as f:
    p_code = f.read()

# find CREATE TABLE question_summaries
import re
m = re.search(
    r'CREATE TABLE IF NOT EXISTS question_summaries\s*\((.*?)\)\s*"""',
    p_code, re.DOTALL,
)
assert_true("CREATE TABLE question_summaries found", m is not None)
if m:
    create_body = m.group(1)
    assert_in("schema_id column defined", 'schema_id', create_body)
    assert_in("sections_json column defined", 'sections_json', create_body)
    # no mirror columns: the texts live in sections_json only
    assert_true("description column removed", 'description' not in create_body)
    assert_true("interpretation column removed", 'interpretation' not in create_body)
    assert_true("segments_text column removed", 'segments_text' not in create_body)


# functional test: the actual table creation; a minimal stub of the database
# class path runs CREATE TABLE, then PRAGMA shows the columns.

with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db_path = f.name

# extract the CREATE statement from the source and run it against a real database
m = re.search(
    r'(CREATE TABLE IF NOT EXISTS question_summaries.*?\))(\s*"""|\s*\)\s*\)|\s*\))',
    p_code, re.DOTALL,
)

# simpler: extract with a coarser regex and run it
m = re.search(
    r'cursor\.execute\(\s*"""\s*(CREATE TABLE IF NOT EXISTS question_summaries.*?)\s*"""',
    p_code, re.DOTALL,
)
if m:
    create_sql = m.group(1)
    
    # the imports table is needed as the FK target
    imports_create = '''
        CREATE TABLE imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, name TEXT, created_at TIMESTAMP
        )
    '''
    
    conn = sqlite3.connect(db_path)
    conn.execute(imports_create)
    conn.execute(create_sql)
    conn.commit()
    
    # check the columns
    cursor = conn.execute("PRAGMA table_info(question_summaries)")
    cols = {row[1]: row[2] for row in cursor.fetchall()}
    
    assert_in("column schema_id exists in the database", 'schema_id', cols)
    assert_in("column sections_json exists in the database", 'sections_json', cols)
    assert_true("description column removed in the database", 'description' not in cols)
    
    # check the default values
    cursor = conn.execute("PRAGMA table_info(question_summaries)")
    defaults = {row[1]: row[4] for row in cursor.fetchall()}
    assert_eq("schema_id Default", defaults.get('schema_id'), "'default_three_sections'")
    assert_eq("sections_json Default", defaults.get('sections_json'), "'{}'")
    
    # test insert with the columns
    conn.execute(
        "INSERT INTO imports (id, name) VALUES (1, 'test')"
    )
    conn.execute(
        """INSERT INTO question_summaries
           (import_id, question_id, lang, schema_id, sections_json)
           VALUES (?, ?, ?, ?, ?)""",
        (1, 'q1', 'de', 'numeric_default',
         '{"central_tendency": "X", "distribution": "Y"}'),
    )
    conn.commit()
    
    row = conn.execute(
        "SELECT schema_id, sections_json FROM question_summaries WHERE question_id='q1'"
    ).fetchone()
    assert_eq("insert: schema_id stored", row[0], 'numeric_default')
    assert_in("insert: sections_json stored", 'central_tendency', row[1])
    
    conn.close()
    os.unlink(db_path)


# ==========================================================================
# 2. compute_segment_significance: SegmentResolver path
# ==========================================================================

print()
print("=" * 60)
print("compute_segment_significance — SegmentResolver-Iteration")
print("=" * 60)

# stubs for handler/statistics
hb = types.ModuleType('handlers')
hbb = types.ModuleType('handlers.base')

class _StubAnalysisResult:
    def __init__(self, n=10, data=None):
        self.n = n
        self.data = data or {}

hbb.AnalysisResult = _StubAnalysisResult


class _StubHandler:
    def analyze(self, df, lang='de'):
        # returns data based on the number of rows
        return _StubAnalysisResult(n=len(df), data={'A': len(df) // 2, 'B': len(df) - len(df) // 2})


def _stub_get_handler(handler_type, qid, qconfig):
    return _StubHandler()


hb.get_handler = _stub_get_handler
sys.modules['handlers'] = hb
sys.modules['handlers.base'] = hbb


# statistics stubben
class _StubOverall:
    p_value = 0.01
    is_significant = True
    effect_size = 0.25


def _stub_run_segment_tests(wrapped):
    return {'overall': _StubOverall()}


_stub('utils.statistics', run_segment_significance_tests=_stub_run_segment_tests)


# now load question_summary
qs_module = _load(
    'utils.question_summary',
    os.path.join(os.path.dirname(__file__), '..', 'utils', 'question_summary.py'),
)
from utils.question_summary import compute_segment_significance


import pandas as pd

# test configuration with a free dimension
config_custom = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {
                    'id': 'bundesland',  # benutzerdefinierter Dimension-Name
                    'label': {'de': 'Bundesland'},
                    'source_question': 'qbl',
                    'bucketing': {'type': 'identity'},
                },
                {
                    'id': 'org_size',
                    'label': {'de': 'Größenklasse'},
                    'source_question': 'qsize',
                    'bucketing': {
                        'type': 'numeric_buckets',
                        'buckets': [
                            {'label': {'de': 'klein'}, 'max': 100},
                            {'label': {'de': 'groß'}, 'min': 100},
                        ],
                    },
                },
            ]
        }
    },
    'question_groups': {
        'qbl': {'columns': ['Bundesland?']},
        'qsize': {'columns': ['Studierende']},
    },
}


df_custom = pd.DataFrame({
    'Bundesland?': ['Bayern'] * 10 + ['NRW'] * 10 + ['Berlin'] * 8,
    'Studierende': ['50'] * 14 + ['200'] * 14,
    # further columns the question itself may need
    'AnswerCol': ['Ja', 'Nein'] * 14,
})


# qconfig for an "AnswerCol" question
qcfg = {'columns': ['AnswerCol'], 'handler': 'single_choice'}

results = compute_segment_significance(
    question_id='q_test',
    handler_type='single_choice',
    qconfig=qcfg,
    df=df_custom,
    config=config_custom,
)

assert_true("result list returned", isinstance(results, list))
# with this data and the stub statistics function 2 dimensions should be
# marked significant
assert_eq("2 dimensions processed", len(results), 2)

# the dimension ids in the results are those of the configuration
seg_types = {r.segment_type for r in results}
assert_in("bundesland found", 'bundesland', seg_types)
assert_in("org_size found", 'org_size', seg_types)
# fixed names should NOT occur
assert_true("no fixed 'country' dimension",
            'country' not in seg_types)
assert_true("no 'institution_type'", 'institution_type' not in seg_types)

# the labels come from the dimension, not from a fixed SEGMENT_LABELS
bl_result = next(r for r in results if r.segment_type == 'bundesland')
assert_eq("Bundesland label from the configuration", bl_result.segment_label, 'Bundesland')


# the classic configuration (three classic dimensions) must still work
config_classic = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {'id': 'country', 'label': {'de': 'Länder'},
                 'source_question': 'qcountry', 'bucketing': {'type': 'identity'}},
                {'id': 'institution_type', 'label': {'de': 'Hochschultyp'},
                 'source_question': 'qtype', 'bucketing': {'type': 'identity'}},
                {'id': 'size', 'label': {'de': 'Hochschulgröße'},
                 'source_question': 'qsize', 'bucketing': {'type': 'identity'}},
            ]
        }
    },
    'question_groups': {
        'qcountry': {'columns': ['Land?']},
        'qtype': {'columns': ['Typ?']},
        'qsize': {'columns': ['Größe?']},
    },
}

df_classic = pd.DataFrame({
    'Land?':       ['DE'] * 12 + ['CH'] * 8,
    'Typ?':        ['Uni'] * 14 + ['FH'] * 6,
    'Größe?':      ['groß'] * 11 + ['klein'] * 9,
    'AnswerCol':   ['Ja', 'Nein'] * 10,
})

results_classic = compute_segment_significance(
    question_id='q_classic',
    handler_type='single_choice',
    qconfig={'columns': ['AnswerCol'], 'handler': 'single_choice'},
    df=df_classic,
    config=config_classic,
)

assert_eq("classic: 3 dimensions processed", len(results_classic), 3)
seg_types_classic = {r.segment_type for r in results_classic}
assert_eq("classic: dimension ids",
          seg_types_classic, {'country', 'institution_type', 'size'})

# labels: from the dim.label dicts
segment_value_result = next(r for r in results_classic if r.segment_type == 'country')
assert_eq("classic: country label", segment_value_result.segment_label, 'Länder')


# edge case: empty segmentation in the configuration
config_empty = {'settings': {}, 'question_groups': {}}
results_empty = compute_segment_significance(
    'q', 'single_choice', {}, df_classic, config_empty,
)
assert_eq("empty configuration -> empty list", results_empty, [])


# ==========================================================================
# 3. _add_question_summary: iterativer Renderer
# ==========================================================================

print()
print("=" * 60)
print("_add_question_summary — iterativer Renderer")
print("=" * 60)

# the two helper methods are tested; instantiating subclasses of
# WordExporter would need docx, which is not installed here, so the method
# function is imported directly and called on a stub self.

# load export_word.py, prepare the docx stubs
def _ensure_docx_stub():
    if 'docx' in sys.modules:
        return
    docx_mod = types.ModuleType('docx')
    docx_mod.Document = lambda: type('D', (), {})()
    
    docx_shared = types.ModuleType('docx.shared')
    docx_shared.Pt = lambda v: v
    docx_shared.RGBColor = lambda *a: None
    docx_shared.Inches = lambda v: v
    docx_shared.Cm = lambda v: v
    docx_shared.Mm = lambda v: v
    docx_shared.Emu = lambda v: v
    
    # docx.enum as a package with __path__, so submodules are possible
    docx_enum = types.ModuleType('docx.enum')
    docx_enum.__path__ = []
    
    docx_enum_text = types.ModuleType('docx.enum.text')
    docx_enum_text.WD_ALIGN_PARAGRAPH = type('WAP', (), {
        'CENTER': 1, 'LEFT': 0, 'RIGHT': 2, 'JUSTIFY': 3
    })()
    docx_enum_text.WD_LINE_SPACING = type('WLS', (), {'SINGLE': 1, 'ONE_POINT_FIVE': 2, 'DOUBLE': 3})()
    docx_enum_text.WD_BREAK = type('WB', (), {'PAGE': 1, 'COLUMN': 2, 'LINE': 3})()
    
    docx_enum_table = types.ModuleType('docx.enum.table')
    docx_enum_table.WD_ALIGN_VERTICAL = type('WAV', (), {'CENTER': 1, 'TOP': 0, 'BOTTOM': 2})()
    docx_enum_table.WD_TABLE_ALIGNMENT = type('WTA', (), {'CENTER': 1, 'LEFT': 0, 'RIGHT': 2})()
    docx_enum_table.WD_ROW_HEIGHT_RULE = type('WRH', (), {'AUTO': 0, 'AT_LEAST': 1, 'EXACTLY': 2})()
    
    docx_enum_style = types.ModuleType('docx.enum.style')
    docx_enum_style.WD_STYLE_TYPE = type('WST', (), {
        'PARAGRAPH': 1, 'CHARACTER': 2, 'TABLE': 3, 'LIST': 4
    })()
    
    docx_enum_section = types.ModuleType('docx.enum.section')
    docx_enum_section.WD_SECTION = type('WS', (), {'NEW_PAGE': 0})()
    docx_enum_section.WD_ORIENTATION = type('WO', (), {'PORTRAIT': 0, 'LANDSCAPE': 1})()
    
    docx_oxml = types.ModuleType('docx.oxml')
    docx_oxml.__path__ = []
    docx_oxml.OxmlElement = lambda *a: type('O', (), {
        'set': lambda *_: None, 'append': lambda *_: None
    })()
    docx_oxml_ns = types.ModuleType('docx.oxml.ns')
    docx_oxml_ns.qn = lambda x: x
    
    docx_table_mod = types.ModuleType('docx.table')
    docx_table_mod.Table = type('Table', (), {})
    
    sys.modules['docx'] = docx_mod
    sys.modules['docx.shared'] = docx_shared
    sys.modules['docx.enum'] = docx_enum
    sys.modules['docx.enum.text'] = docx_enum_text
    sys.modules['docx.enum.table'] = docx_enum_table
    sys.modules['docx.enum.style'] = docx_enum_style
    sys.modules['docx.enum.section'] = docx_enum_section
    sys.modules['docx.oxml'] = docx_oxml
    sys.modules['docx.oxml.ns'] = docx_oxml_ns
    sys.modules['docx.table'] = docx_table_mod
    
    # docxcompose
    sys.modules['docxcompose'] = types.ModuleType('docxcompose')
    docx_composer = types.ModuleType('docxcompose.composer')
    docx_composer.Composer = type('Composer', (), {})
    sys.modules['docxcompose.composer'] = docx_composer

_ensure_docx_stub()


# export_word is loaded directly and the helper functions are tested
# statically; instantiating the full class would be too invasive.
ew = _load('utils.export_word',
           os.path.join(os.path.dirname(__file__), '..', 'utils', 'export_word.py'))


# _extract_sections / _order_sections_for_render are static or instance
# methods and can be tested through the class.
# (WordExporter has an __init__ that needs further stubs, so the free-standing
#  logic is tested by direct calls.)

# 1. _extract_sections from a QuestionSummary
qs_mod = sys.modules['utils.question_summary']
QuestionSummary = qs_mod.QuestionSummary

# find WordExporter
WordExporter = None
for name in dir(ew):
    obj = getattr(ew, name)
    if isinstance(obj, type) and name.endswith('Exporter'):
        WordExporter = obj
        break
assert_true("WordExporter class found", WordExporter is not None)

if WordExporter:
    extract = WordExporter._extract_sections
    
    # from a QuestionSummary
    summary = QuestionSummary(
        question_id='q1', lang='de',
        sections={'description': 'A', 'interpretation': 'B', 'segments_text': 'C'},
    )
    extracted = extract(summary)
    assert_eq("extract from a QuestionSummary",
              extracted, {'description': 'A', 'interpretation': 'B', 'segments_text': 'C'})
    
    # from a dict
    extracted_dict = extract({'description': 'X', 'interpretation': 'Y', 'segments_text': 'Z'})
    assert_eq("extract from a dict",
              extracted_dict, {'description': 'X', 'interpretation': 'Y', 'segments_text': 'Z'})
    
    # from custom sections
    custom_summary = QuestionSummary(
        question_id='q2', lang='de',
        sections={'central_tendency': 'mean=5', 'distribution': 'rechtsschief'},
    )
    extracted_custom = extract(custom_summary)
    assert_eq("extract custom sections",
              extracted_custom,
              {'central_tendency': 'mean=5', 'distribution': 'rechtsschief'})
    
    # from None / unknown
    assert_eq("extract from None", extract(None), {})
    
    # from a dict with nested 'sections'
    nested = {'sections': {'a': 'X', 'b': 'Y'}, 'lang': 'de'}
    assert_eq("extract from a nested dict", extract(nested), {'a': 'X', 'b': 'Y'})


# 2. _order_sections_for_render: default-schema path;
# a stub self is needed, as the method is an instance method
class _StubSelf:
    _DEFAULT_HEADINGS = WordExporter._DEFAULT_HEADINGS if WordExporter else {}
    _DEFAULT_ORDER = WordExporter._DEFAULT_ORDER if WordExporter else []

# called like a method (stubbed self)
order_method = WordExporter._order_sections_for_render
ordered_de = order_method(_StubSelf, {
    'description': 'A',
    'interpretation': 'B',
    'segments_text': 'C',
}, None, 'de')
assert_eq("default schema path: 3 sections in a fixed order",
          [t[0] for t in ordered_de],
          ['description', 'interpretation', 'segments_text'])
assert_eq("default schema path: DE heading description",
          ordered_de[0][2], 'Beschreibung der Ergebnisse')
assert_eq("default schema path: DE heading segments_text",
          ordered_de[2][2], 'Segmentunterschiede')

# FR-Heading
ordered_fr = order_method(_StubSelf, {
    'description': 'A', 'interpretation': 'B', 'segments_text': 'C',
}, None, 'fr')
assert_eq("default schema path: FR heading description",
          ordered_fr[0][2], 'Description des résultats')

# custom path: other section ids
ordered_custom = order_method(_StubSelf, {
    'central_tendency': 'mean=5', 'distribution': 'rechtsschief',
}, None, 'de')
custom_ids = [t[0] for t in ordered_custom]
assert_in("custom path: central_tendency in the ids", 'central_tendency', custom_ids)
assert_in("custom path: distribution in the ids", 'distribution', custom_ids)

# mixed: default + custom -> custom path (no 1:1 subset relation)
ordered_mixed = order_method(_StubSelf, {
    'description': 'A', 'central_tendency': 'X',
}, None, 'de')
mixed_ids = [t[0] for t in ordered_mixed]
assert_eq("mixed: 2 sections", len(mixed_ids), 2)
# default sections keep their headings in the custom path too
desc_section = next(t for t in ordered_mixed if t[0] == 'description')
assert_eq("mixed: description uses the default schema heading",
          desc_section[2], 'Beschreibung der Ergebnisse')


# subset (only 1 default section) -> default-schema path
ordered_subset = order_method(_StubSelf, {
    'interpretation': 'Nur Interp.',
}, None, 'de')
assert_eq("subset default schema: 1 section",
          [t[0] for t in ordered_subset], ['interpretation'])
assert_eq("subset default schema: correct heading",
          ordered_subset[0][2], 'Interpretation')


# ==========================================================================
# 4. render_summary_with_schema — schema-aware variant
# ==========================================================================

print()
print("=" * 60)
print("render_summary_with_schema (schema-aware)")
print("=" * 60)

# does the method exist?
assert_true("render_summary_with_schema present",
            hasattr(WordExporter, 'render_summary_with_schema'))


# the method iterates over the schema and not over the fixed default order;
# a stub doc collects the add_heading calls.
class _StubPara:
    space_after = 0

class _StubDoc:
    def __init__(self):
        self.headings = []
        self.paragraphs = []
    def add_heading(self, text, level):
        self.headings.append((text, level))
    def add_paragraph(self, text=''):
        self.paragraphs.append(text)
        return _StubPara()


# custom schema with its own order
from utils.summary_schema import SummarySchema, Section

schema = SummarySchema(
    schema_id='custom_test',
    handler_type='numeric',
    sections=[
        Section(id='central_tendency',
                label={'de': 'Lage und Streuung', 'fr': 'Tendance', 'en': 'Central tendency'},
                prompt_instruction="x"),
        Section(id='distribution',
                label={'de': 'Verteilungsform', 'fr': 'Distribution', 'en': 'Distribution'},
                prompt_instruction="y"),
        Section(id='outliers',
                label={'de': 'Ausreißer', 'fr': 'Aberrants', 'en': 'Outliers'},
                prompt_instruction="z",
                conditional='has_outliers'),
    ],
)


summary_custom = QuestionSummary(
    question_id='q_num', lang='de', schema_id='custom_test',
    sections={
        'central_tendency': 'Mean=5.2, Median=5',
        'distribution': 'normalverteilt',
        'outliers': '',  # empty
    },
)


# call render through a stubbed self
# (render_summary_with_schema is an instance method and needs a minimal self
# object that knows _extract_sections)
class _RenderableSelf:
    _extract_sections = WordExporter._extract_sections


doc = _StubDoc()
WordExporter.render_summary_with_schema(
    _RenderableSelf, doc, summary_custom, schema, 'de', heading_level=3,
)

# outliers is empty -> not rendered
assert_eq("2 Headings (outliers leer)", len(doc.headings), 2)
assert_eq("heading 1: location and spread",
          doc.headings[0][0], 'Lage und Streuung')
assert_eq("Heading 2: Verteilungsform",
          doc.headings[1][0], 'Verteilungsform')

# the order follows the schema
heading_texts = [h[0] for h in doc.headings]
assert_eq("order as in the schema",
          heading_texts, ['Lage und Streuung', 'Verteilungsform'])


# with FR
doc_fr = _StubDoc()
WordExporter.render_summary_with_schema(
    _RenderableSelf, doc_fr, summary_custom, schema, 'fr', heading_level=3,
)
heading_texts_fr = [h[0] for h in doc_fr.headings]
assert_eq("FR: headings from the schema",
          heading_texts_fr, ['Tendance', 'Distribution'])


# schema with the default section ids -> the previous behaviour
default_schema_obj = qs_mod.DEFAULT_THREE_SECTIONS_SCHEMA \
    if hasattr(qs_mod, 'DEFAULT_THREE_SECTIONS_SCHEMA') \
    else _load('utils.summary_schema',
               os.path.join(os.path.dirname(__file__), '..', 'utils', 'summary_schema.py')
               ).DEFAULT_THREE_SECTIONS_SCHEMA

summary_default = QuestionSummary(
    question_id='q_lg', lang='de', schema_id='default_three_sections',
    sections={
        'description': 'Bei drei Vierteln existiert keine CDO-Position.',
        'interpretation': 'Klare Minderheit.',
        'segments_text': 'Keine signifikanten Unterschiede.',
    },
)


doc_lg = _StubDoc()
WordExporter.render_summary_with_schema(
    _RenderableSelf, doc_lg, summary_default, default_schema_obj, 'de', heading_level=3,
)
default_headings = [h[0] for h in doc_lg.headings]
assert_eq("default schema schema -> 3 expected headings",
          default_headings,
          ['Beschreibung der Ergebnisse', 'Interpretation', 'Segmentunterschiede'])


# ==========================================================================
# Summary
# ==========================================================================

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
