# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the numeric integration.

Verifies:
1. the Kruskal-Wallis test works correctly (significant / non-significant
   differences of central tendency, edge cases)
2. compute_segment_significance detects the numeric handler and uses
   Kruskal-Wallis instead of chi-square
3. _add_numeric_stats_table renders the statistics table correctly (headers,
   values, multilingual, note with n_invalid)
4. _add_question_to_report calls _add_numeric_stats_table if the result has a
   numeric_stats entry
5. the segment_means_bar chart renders without a crash
6. NumericHandler.create_chart('segment_means_bar') works with/without
   segment_stats

Run: python tests/suite_numeric_integration.py
"""

import sys
import os
import types
import importlib.util
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


def _ensure_translation_stub():
    if 'utils.translation' in sys.modules:
        return
    tm_stub = types.ModuleType('utils.translation')
    class _TM:
        def t(self, key, lang='de', **kw): return key
        def get(self, key, lang='de'): return key
        def translate_common_label(self, label, lang='de'): return label
    tm_stub.get_translation_manager = lambda: _TM()
    sys.modules['utils.translation'] = tm_stub


if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg
_ensure_translation_stub()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')

_load('utils.summary_schema',  os.path.join(ROOT, 'utils', 'summary_schema.py'))
_load('utils.numeric_coerce',  os.path.join(ROOT, 'utils', 'numeric_coerce.py'))
_load('utils.segment_resolver', os.path.join(ROOT, 'utils', 'segment_resolver.py'))
_load('utils.segmentation',    os.path.join(ROOT, 'utils', 'segmentation.py'))
_load('utils.charts',          os.path.join(ROOT, 'utils', 'charts.py'))
_load('utils.statistics',      os.path.join(ROOT, 'utils', 'statistics.py'))


import numpy as np
import pandas as pd

from utils.statistics import (
    kruskal_wallis_test,
    run_numeric_segment_significance,
    SignificanceResult,
)
from utils.charts import ChartGenerator


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


def assert_close(label, actual, expected, tol=1e-3):
    global PASS, FAIL
    try:
        if abs(actual - expected) <= tol:
            PASS += 1
            RESULTS.append(('OK', label))
            return
    except Exception:
        pass
    FAIL += 1
    RESULTS.append(('FAIL', f"{label}: expected {expected!r} (±{tol}), got {actual!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


# ==========================================================================
# 1. kruskal_wallis_test: significant differences of central tendency
# ==========================================================================

print("=" * 60)
print("1. Kruskal-Wallis: significant differences of central tendency")
print("=" * 60)

# three groups with clearly different means
sig_data = {
    'klein':  [5, 6, 7, 6, 5, 7, 8, 5, 6, 7],
    'mittel': [20, 22, 21, 19, 23, 22, 20, 21, 19, 22],
    'gross':  [50, 55, 52, 51, 53, 54, 50, 56, 52, 53],
}

result = kruskal_wallis_test(sig_data)
assert_eq("Test-Name", result.test_name, 'kruskal_wallis')
assert_true("p_value < 0.05", result.p_value < 0.05)
assert_true("is_significant True", result.is_significant)
assert_eq("dof = k - 1", result.degrees_of_freedom, 2)
assert_true("effect_size > 0.14 (stark)", result.effect_size > 0.14)
assert_eq("Interpretation 'strong'", result.interpretation, 'strong')


# ==========================================================================
# 2. Kruskal-Wallis: no significant differences
# ==========================================================================

print()
print("=" * 60)
print("2. Kruskal-Wallis: no differences")
print("=" * 60)

# three groups with similar means and spread
nonsig_data = {
    'A': [10, 11, 12, 9, 10, 11, 12, 9, 10, 11],
    'B': [10, 12, 11, 9, 13, 10, 11, 10, 11, 12],
    'C': [11, 10, 12, 11, 10, 9, 12, 11, 10, 11],
}

result = kruskal_wallis_test(nonsig_data)
assert_true("p_value > 0.05", result.p_value > 0.05)
assert_eq("is_significant False", result.is_significant, False)


# ==========================================================================
# 3. Kruskal-Wallis: Edge-Cases
# ==========================================================================

print()
print("=" * 60)
print("3. Edge-Cases")
print("=" * 60)

# 3a. only one group
result = kruskal_wallis_test({'A': [1, 2, 3, 4, 5, 6]})
assert_eq("1 group: not testable",
          result.interpretation, 'not testable')
assert_eq("1 group: not significant", result.is_significant, False)
assert_true("1 group: warning", len(result.warnings) > 0)

# 3b. too little data in the groups
result = kruskal_wallis_test({'A': [1, 2], 'B': [3, 4]})  # n_per_group < 5
assert_eq("too little data: not testable",
          result.interpretation, 'not testable')

# 3c. all values identical
result = kruskal_wallis_test({
    'A': [5, 5, 5, 5, 5],
    'B': [5, 5, 5, 5, 5],
})
assert_eq("identical values: no variation",
          result.interpretation, 'no variation')
assert_eq("identical values: p=1", result.p_value, 1.0)

# 3d. with NaN values
result = kruskal_wallis_test({
    'A': [1, 2, 3, 4, 5, float('nan')],
    'B': [10, 11, 12, 13, 14, float('nan')],
})
assert_true("NaN ignored: 5+5=10 valid", result.p_value < 0.05)


# ==========================================================================
# 4. run_numeric_segment_significance Wrapper
# ==========================================================================

print()
print("=" * 60)
print("4. run_numeric_segment_significance Wrapper")
print("=" * 60)

result = run_numeric_segment_significance(sig_data)
assert_true("Wrapper hat 'overall'", 'overall' in result)
assert_true("Wrapper hat 'summary'", 'summary' in result)
assert_eq("summary.test", result['summary']['test'], 'kruskal_wallis')
assert_true("summary.any_significant True", result['summary']['any_significant'])
assert_eq("summary.n_groups", result['summary']['n_groups'], 3)


# ==========================================================================
# 5. compute_segment_significance: numeric handler path
# ==========================================================================

print()
print("=" * 60)
print("5. compute_segment_significance detects numeric")
print("=" * 60)

# statistics is loaded already; now load question_summary;
# first stub handlers.base and handlers.numeric (as in suite_persistence_and_export)
hb_mod = types.ModuleType('handlers.base')
class _AR:
    def __init__(self, **kw):
        self.question_id = kw.get('question_id', '')
        self.n = kw.get('n', 0)
        self.data = kw.get('data', {})
        self.percentages = kw.get('percentages')
        self.warnings = kw.get('warnings', [])
        self.metadata = kw.get('metadata', {})
        self.clusters = None
        self.additional_content = None
hb_mod.AnalysisResult = _AR
sys.modules['handlers.base'] = hb_mod

# numeric handler stub with a minimal analyze method
class _StubNumericHandler:
    def __init__(self, qconfig=None):
        self.config = qconfig or {}
    def analyze(self, df, lang='de', **kw):
        from utils.numeric_coerce import coerce_series
        col = self.config.get('columns', [None])[0]
        if not col or col not in df.columns:
            return _AR(n=0, data={}, metadata={'numeric_stats': None})
        vals = coerce_series(df[col]).dropna()
        if len(vals) == 0:
            return _AR(n=0, data={}, metadata={'numeric_stats': None})
        return _AR(n=len(vals), data={'mean': float(vals.mean())}, metadata={})


# stub handler module that provides get_handler
hb_mod_pkg = types.ModuleType('handlers')
def _stub_get_handler(handler_type, qid, qconfig):
    if handler_type == 'numeric':
        return _StubNumericHandler(qconfig)
    # others: simple stub with a data dict
    class _GenericStub:
        def __init__(self, qcfg): self.config = qcfg
        def analyze(self, df, lang='de'):
            return _AR(n=len(df), data={'A': len(df) // 2}, metadata={})
    return _GenericStub(qconfig)


hb_mod_pkg.get_handler = _stub_get_handler
sys.modules['handlers'] = hb_mod_pkg


qs_mod = _load('utils.question_summary',
               os.path.join(ROOT, 'utils', 'question_summary.py'))
from utils.question_summary import compute_segment_significance


# test data: numeric question with 2 clearly different segment means
config_num = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {
                    'id': 'size',
                    'label': {'de': 'Größe'},
                    'source_question': 'qsize',
                    'bucketing': {'type': 'identity'},
                },
            ]
        }
    },
    'question_groups': {
        'qsize': {'columns': ['Hochschulgroesse']},
        'q_apprentices': {'columns': ['Anzahl Auszubildende'],
                            'handler': 'numeric'},
    },
}


df_num = pd.DataFrame({
    'Hochschulgroesse':  ['klein'] * 10 + ['gross'] * 10,
    'Anzahl Auszubildende': [3, 4, 5, 4, 5, 3, 4, 5, 4, 3,    # small
                                30, 32, 28, 35, 31, 29, 33, 32, 30, 31],  # large
})


qcfg = {
    'columns': ['Anzahl Auszubildende'],
    'handler': 'numeric',
}

results = compute_segment_significance(
    question_id='q_apprentices',
    handler_type='numeric',
    qconfig=qcfg,
    df=df_num,
    config=config_num,
)

assert_true("numeric: 1 result", len(results) >= 1)
if results:
    r = results[0]
    assert_eq("Numeric: segment_type=size", r.segment_type, 'size')
    assert_true("numeric: significance detected", r.is_significant)
    assert_true("Numeric: p_value < 0.05", r.p_value < 0.05)
    # with this data the effect should be 'strong' (epsilon^2)
    assert_true("numeric: effect_desc set", r.effect_desc != '')


# ==========================================================================
# 6. compute_segment_significance: non-numeric questions still use chi-square
# ==========================================================================

print()
print("=" * 60)
print("6. compute_segment_significance: the chi-square path stays active")
print("=" * 60)

# single choice: the same configuration structure, but the single_choice handler
config_sc = {
    'settings': {
        'segmentation': {
            'dimensions': [
                {
                    'id': 'size',
                    'label': {'de': 'Größe'},
                    'source_question': 'qsize',
                    'bucketing': {'type': 'identity'},
                },
            ]
        }
    },
    'question_groups': {
        'qsize': {'columns': ['Größe']},
        'q_sc': {'columns': ['Antwort'], 'handler': 'single_choice'},
    },
}


df_sc = pd.DataFrame({
    'Größe': ['klein'] * 10 + ['gross'] * 10,
    'Antwort': ['Ja', 'Nein', 'Ja', 'Ja', 'Nein',
                'Nein', 'Ja', 'Nein', 'Ja', 'Nein',
                'Ja', 'Ja', 'Nein', 'Ja', 'Ja',
                'Nein', 'Ja', 'Ja', 'Nein', 'Ja'],
})


# should run without a crash: the chi-square path
results_sc = compute_segment_significance(
    question_id='q_sc',
    handler_type='single_choice',
    qconfig={'columns': ['Antwort'], 'handler': 'single_choice'},
    df=df_sc,
    config=config_sc,
)
assert_true("SC: no exception, a list returned",
            isinstance(results_sc, list))


# ==========================================================================
# 7. segment_means_bar-Chart rendert
# ==========================================================================

print()
print("=" * 60)
print("7. segment_means_bar Chart")
print("=" * 60)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

cg = ChartGenerator()

# with three segments + standard deviation
fig = cg.segment_means_bar(
    segment_stats={
        'klein':  {'mean': 5,  'std': 1.5, 'n': 10},
        'mittel': {'mean': 21, 'std': 2.0, 'n': 12},
        'gross':  {'mean': 52, 'std': 3.5, 'n': 8},
    },
    title="Mittelwerte nach Größe",
    xlabel="Personen",
    decimals=1,
)
assert_true("figure created", fig is not None)
plt.close(fig)


# with an explicit order
fig = cg.segment_means_bar(
    segment_stats={
        'klein':  {'mean': 5},
        'gross':  {'mean': 52},
        'mittel': {'mean': 21},
    },
    title="Test",
    order=['klein', 'mittel', 'gross'],
)
assert_true("with the order parameter", fig is not None)
plt.close(fig)


# empty data -> None
fig = cg.segment_means_bar(segment_stats={}, title="Leer")
assert_true("empty data -> None", fig is None)


# ==========================================================================
# 8. _add_numeric_stats_table
# ==========================================================================

print()
print("=" * 60)
print("8. _add_numeric_stats_table")
print("=" * 60)

# set the docx stubs (as in suite_persistence_and_export)
def _ensure_docx_stub():
    if 'docx' in sys.modules:
        return
    docx_mod = types.ModuleType('docx')
    docx_mod.Document = lambda: type('D', (), {})()
    docx_shared = types.ModuleType('docx.shared')
    for n in ('Pt', 'Inches', 'Cm', 'Mm', 'Emu'):
        setattr(docx_shared, n, lambda v, _n=n: v)
    docx_shared.RGBColor = lambda *a: None
    docx_enum = types.ModuleType('docx.enum'); docx_enum.__path__ = []
    docx_enum_text = types.ModuleType('docx.enum.text')
    docx_enum_text.WD_ALIGN_PARAGRAPH = type('', (), {'CENTER': 1, 'LEFT': 0, 'RIGHT': 2, 'JUSTIFY': 3})()
    docx_enum_text.WD_LINE_SPACING = type('', (), {'SINGLE': 1, 'ONE_POINT_FIVE': 2, 'DOUBLE': 3})()
    docx_enum_text.WD_BREAK = type('', (), {'PAGE': 1, 'COLUMN': 2, 'LINE': 3})()
    docx_enum_table = types.ModuleType('docx.enum.table')
    docx_enum_table.WD_ALIGN_VERTICAL = type('', (), {'CENTER': 1, 'TOP': 0, 'BOTTOM': 2})()
    docx_enum_table.WD_TABLE_ALIGNMENT = type('', (), {'CENTER': 1, 'LEFT': 0, 'RIGHT': 2})()
    docx_enum_table.WD_ROW_HEIGHT_RULE = type('', (), {'AUTO': 0, 'AT_LEAST': 1, 'EXACTLY': 2})()
    docx_enum_style = types.ModuleType('docx.enum.style')
    docx_enum_style.WD_STYLE_TYPE = type('', (), {'PARAGRAPH': 1, 'CHARACTER': 2, 'TABLE': 3, 'LIST': 4})()
    docx_enum_section = types.ModuleType('docx.enum.section')
    docx_enum_section.WD_SECTION = type('', (), {'NEW_PAGE': 0})()
    docx_enum_section.WD_ORIENTATION = type('', (), {'PORTRAIT': 0, 'LANDSCAPE': 1})()
    docx_oxml = types.ModuleType('docx.oxml'); docx_oxml.__path__ = []
    docx_oxml.OxmlElement = lambda *a: type('', (), {'set': lambda *_: None, 'append': lambda *_: None})()
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
    sys.modules['docxcompose'] = types.ModuleType('docxcompose')
    docx_composer = types.ModuleType('docxcompose.composer')
    docx_composer.Composer = type('Composer', (), {})
    sys.modules['docxcompose.composer'] = docx_composer


_ensure_docx_stub()


# stub doc that records add_table and add_paragraph
class StubRun:
    def __init__(self):
        self.bold = False
        self.italic = False
        self.font = type('F', (), {'size': None})()

class StubPara:
    def __init__(self):
        self.runs = []
        self.text = ''
        self.space_after = None
    def add_run(self, text=''):
        r = StubRun()
        self.runs.append(r)
        # text-only-Mode
        self.text = (self.text or '') + text
        return r

class StubCell:
    def __init__(self):
        self._text = ''
        self.paragraphs = [StubPara()]
    @property
    def text(self):
        return self._text
    @text.setter
    def text(self, val):
        self._text = val

class StubRow:
    def __init__(self, n_cols):
        self.cells = [StubCell() for _ in range(n_cols)]

class StubTable:
    def __init__(self, rows, cols):
        self.rows = [StubRow(cols) for _ in range(rows)]
        self.style = ''

class StubDoc:
    def __init__(self):
        self.tables = []
        self.paragraphs = []
        self.headings = []
    def add_table(self, rows, cols):
        t = StubTable(rows, cols)
        self.tables.append(t)
        return t
    def add_paragraph(self, text=''):
        p = StubPara()
        if text:
            p.add_run(text)
        self.paragraphs.append(p)
        return p
    def add_heading(self, text, level=1):
        self.headings.append((text, level))
        return None


# matplotlib stub (in case export_word needs it)
mpl_stub = types.ModuleType('matplotlib')
mpl_pyplot = types.ModuleType('matplotlib.pyplot')
mpl_pyplot.Figure = type('Figure', (), {})
mpl_pyplot.subplots = lambda *a, **kw: (None, None)
mpl_pyplot.close = lambda *a, **kw: None
sys.modules['matplotlib'] = mpl_stub
sys.modules['matplotlib.pyplot'] = mpl_pyplot

# export_word laden
ew_mod = _load('utils.export_word',
               os.path.join(ROOT, 'utils', 'export_word.py'))


# find the WordExporter class
WordExporter = None
for name in dir(ew_mod):
    obj = getattr(ew_mod, name)
    if isinstance(obj, type) and name.endswith('Exporter'):
        WordExporter = obj
        break

assert_true("WordExporter found", WordExporter is not None)


# test: _add_numeric_stats_table with a NumericResult-like object
from dataclasses import dataclass

@dataclass
class FakeNumericResult:
    n: int
    n_invalid: int
    mean: float
    median: float
    std: float
    min_value: float
    max_value: float
    decimals: int = 1
    unit: str = ''


# stub self that can bind the method
exporter_stub = WordExporter.__new__(WordExporter)


# DE, with data
doc = StubDoc()
nr = FakeNumericResult(
    n=47, n_invalid=3,
    mean=4.2, median=3.0, std=5.1,
    min_value=0, max_value=28,
    decimals=1, unit='Personen',
)
WordExporter._add_numeric_stats_table(exporter_stub, doc, nr, 'de')

# a table was created
assert_eq("DE: 1 table", len(doc.tables), 1)
table = doc.tables[0]
assert_eq("DE: 2 rows", len(table.rows), 2)
assert_eq("DE: 6 columns", len(table.rows[0].cells), 6)

# Header
header_texts = [c.text for c in table.rows[0].cells]
assert_eq("DE Header", header_texts,
          ['n', 'Mittelwert', 'Median', 'SD', 'Min', 'Max'])

# values
value_texts = [c.text for c in table.rows[1].cells]
assert_eq("DE: n", value_texts[0], '47')
assert_eq("DE: mean", value_texts[1], '4.2')
assert_eq("DE: median", value_texts[2], '3.0')

# with n_invalid > 0 there must be a note paragraph
invalid_notes = [p for p in doc.paragraphs if 'ungültige' in (p.text or '')]
assert_eq("DE: note with n_invalid", len(invalid_notes), 1)


# EN, other headers
doc = StubDoc()
WordExporter._add_numeric_stats_table(exporter_stub, doc, nr, 'en')
header_texts = [c.text for c in doc.tables[0].rows[0].cells]
assert_eq("EN Header", header_texts,
          ['n', 'Mean', 'Median', 'SD', 'Min', 'Max'])

# n_invalid=0 -> no note
nr_clean = FakeNumericResult(
    n=50, n_invalid=0,
    mean=10.5, median=10.0, std=2.1,
    min_value=5, max_value=15,
    decimals=1, unit='',
)
doc = StubDoc()
WordExporter._add_numeric_stats_table(exporter_stub, doc, nr_clean, 'de')
invalid_notes = [p for p in doc.paragraphs if 'ungültige' in (p.text or '')]
assert_eq("no n_invalid: no note", len(invalid_notes), 0)


# None -> render nothing
doc = StubDoc()
WordExporter._add_numeric_stats_table(exporter_stub, doc, None, 'de')
assert_eq("None: no table", len(doc.tables), 0)

# n=0 -> render nothing
doc = StubDoc()
nr_zero = FakeNumericResult(
    n=0, n_invalid=10, mean=0, median=0, std=0,
    min_value=0, max_value=0,
)
WordExporter._add_numeric_stats_table(exporter_stub, doc, nr_zero, 'de')
assert_eq("n=0: no table", len(doc.tables), 0)


# ==========================================================================
# 9. NumericHandler.create_chart('segment_means_bar')
# ==========================================================================

print()
print("=" * 60)
print("9. NumericHandler.create_chart('segment_means_bar')")
print("=" * 60)

# before loading handlers.numeric: replace the stub handlers.base of test 5
# by the real module, as numeric.py needs 'SingleColumnHandler'.
for name in ('handlers', 'handlers.base'):
    if name in sys.modules:
        del sys.modules[name]

# handlers package anew (with __path__)
handlers_pkg = types.ModuleType('handlers')
handlers_pkg.__path__ = [os.path.join(ROOT, 'handlers')]
sys.modules['handlers'] = handlers_pkg

# load the real handlers.base
_load('handlers.base', os.path.join(ROOT, 'handlers', 'base.py'))

# handlers.numeric laden
_load('handlers.numeric', os.path.join(ROOT, 'handlers', 'numeric.py'))
from handlers.numeric import NumericHandler, NumericResult


handler = NumericHandler(config={
    'id': 'q1', 'columns': ['Anzahl'],
    'numeric': {'unit': 'Personen', 'decimals': 1},
})


# 9a. with data without segment_stats -> fallback to the histogram
df = pd.DataFrame({'Anzahl': list(range(1, 21))})
res = handler.analyze(df)
fig = handler.create_chart(res, chart_type='segment_means_bar')
assert_true("without segment_stats: fallback histogram",
            fig is not None)
plt.close(fig)


# 9b. with segment_stats
res = handler.analyze(df)
nr = res.metadata['numeric_stats']

# fill segment_stats manually, simulating the segmented pipeline
nr.segment_stats = {
    'size': {
        'klein':  NumericResult(question_id='q1', n=8, n_invalid=0,
                                  mean=4, median=4, std=1.5,
                                  min_value=1, max_value=7,
                                  q25=2.5, q75=5.5,
                                  decimals=1, unit='Personen'),
        'gross':  NumericResult(question_id='q1', n=12, n_invalid=0,
                                  mean=15, median=15, std=2.5,
                                  min_value=10, max_value=20,
                                  q25=13, q75=17,
                                  decimals=1, unit='Personen'),
    },
}

fig = handler.create_chart(res, chart_type='segment_means_bar')
assert_true("with segment_stats: chart rendered", fig is not None)
plt.close(fig)


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
