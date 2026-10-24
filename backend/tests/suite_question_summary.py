# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of utils/question_summary.py.

Verifies:
1. QuestionSummary with dict sections + properties
2. the system prompt from the prompt catalog of the source language (de/en), for all schemas
3. _parse_summary_response with dynamic expected_ids
4. translate_summary iterates over the sections
5. save/load with the database structure (sections_json)
6. schema resolver integration in generate_all_summaries

Run: python tests/suite_question_summary.py
"""

import sys
import os
import types
import json
import importlib.util
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Heavy imports stubben
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

# set up the utils package anew, so its __init__ does not run
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
sys.modules['utils'] = utils_pkg

# stub for utils.charts
charts_stub = types.ModuleType('utils.charts')
charts_stub.ChartGenerator = type('CG', (), {
    'DEFAULT_COLORS': {'positive': '#5cb85c', 'negative': '#d9534f', 'neutral': '#999'}
})
sys.modules['utils.charts'] = charts_stub


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_module('utils.summary_schema',
             os.path.join(os.path.dirname(__file__), '..', 'utils', 'summary_schema.py'))
_load_module('utils.numeric_coerce',
             os.path.join(os.path.dirname(__file__), '..', 'utils', 'numeric_coerce.py'))

# now load the actual module
qs_module = _load_module('utils.question_summary',
                          os.path.join(os.path.dirname(__file__), '..', 'utils', 'question_summary.py'))

from utils.question_summary import (
    QuestionSummary, SegmentSignificance,
    build_system_prompt,
    _parse_summary_response, translate_summary,
    save_summary_to_db, load_summary_from_db,
    no_significant_differences_text,
)
from utils.summary_schema import (
    Section, SummarySchema, SchemaResolver,
    DEFAULT_THREE_SECTIONS_SCHEMA,
)


# ==========================================================================
# Helpers
# ==========================================================================

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
        actual_repr    = repr(actual)[:200]
        expected_repr  = repr(expected)[:200]
        RESULTS.append(('FAIL', f"{label}: expected {expected_repr}, got {actual_repr}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


# ==========================================================================
# 1. QuestionSummary with dict sections + properties
# ==========================================================================

print("=" * 60)
print("QuestionSummary: dict-based + default-schema properties")
print("=" * 60)

# construction without sections
qsum = QuestionSummary(question_id='q1', lang='de')
assert_eq("default sections leer", qsum.sections, {})
assert_eq("default schema_id empty", qsum.schema_id, '')
assert_true("has_content false when empty", not qsum.has_content())

# Property-Setter -> Dict
qsum.description = "Test-Beschreibung"
assert_eq("setter -> dict", qsum.sections, {'description': 'Test-Beschreibung'})
assert_eq("getter works", qsum.description, 'Test-Beschreibung')

# direct dict access
qsum2 = QuestionSummary(question_id='q2', lang='de',
                         sections={'description': 'A', 'interpretation': 'B', 'segments_text': 'C'})
assert_eq("description property", qsum2.description, 'A')
assert_eq("interpretation property", qsum2.interpretation, 'B')
assert_eq("segments_text property", qsum2.segments_text, 'C')
assert_true("has_content true", qsum2.has_content())

# custom sections (e.g. for numeric)
qsum3 = QuestionSummary(question_id='q3', lang='de',
                         sections={'central_tendency': 'X', 'distribution': 'Y'})
assert_eq("custom sections readable", qsum3.get_section('central_tendency'), 'X')
assert_eq("default schema property on a section that does not exist", qsum3.description, '')

# to_dict
d = qsum2.to_dict()
assert_eq("to_dict question_id", d['question_id'], 'q2')
assert_eq("to_dict sections", d['sections'], {'description': 'A', 'interpretation': 'B', 'segments_text': 'C'})
assert_true("to_dict without mirror fields", 'description' not in d)


# ==========================================================================
# 2./3. build_system_prompt: one prompt for all schemas, per source language
# ==========================================================================

print()
print("=" * 60)
print("build_system_prompt() — catalog of the source language")
print("=" * 60)

prompt_de = build_system_prompt(DEFAULT_THREE_SECTIONS_SCHEMA, llm_context="Campus-Dienste", lang='de')
prompt_en = build_system_prompt(DEFAULT_THREE_SECTIONS_SCHEMA, llm_context="Campus services", lang='en')
assert_true("de: answer language named", 'Schreibe alle Texte auf Deutsch' in prompt_de)
assert_true("en: answer language named", 'Write all texts in English' in prompt_en)
assert_true("de: context from llm_context", 'Campus-Dienste' in prompt_de)
assert_true("en: context from llm_context", 'Campus services' in prompt_en)
assert_true("de: section ids", all(s in prompt_de for s in ('description', 'interpretation', 'segments_text')))
assert_true("en: instruction in English", 'Factual description of the results' in prompt_en)
assert_true("de: instruction in German", 'Sachliche Beschreibung der Ergebnisse' in prompt_de)
assert_true("no fixed survey context any more",
            all(w not in prompt_de for w in ('Hochschul-IT', 'Hochschultyp', 'DACH', 'CDO')))
assert_true("style rules from the earlier default prompt",
            'ABWERTENDE FORMULIERUNGEN' in prompt_de and 'neutral frequency descriptions' in prompt_en)
assert_true("without llm_context: placeholder text",
            'keine weiteren Angaben' in build_system_prompt(DEFAULT_THREE_SECTIONS_SCHEMA, lang='de'))

# own schema with a structured context
custom_schema = SummarySchema(
    schema_id='custom',
    handler_type='numeric',
    sections=[
        Section(id='central_tendency', label={'de': 'Lage'},
                prompt_instruction="Mittelwert und Median in Prosa.",
                length_hint='short'),
        Section(id='distribution', label={'de': 'Verteilung'},
                prompt_instruction={'de': "Form der Verteilung.", 'en': "Shape of the distribution."}),
    ],
)
prompt_dyn = build_system_prompt(custom_schema, llm_context={
    'domain': 'Campus-Dienste',
    'region': 'Hessen',
    'audience': 'Hochschulleitungen',
}, lang='de')
assert_true("dyn-prompt enthaelt domain", 'Campus-Dienste' in prompt_dyn)
assert_true("dyn prompt contains region", 'Hessen' in prompt_dyn)
assert_true("dyn-prompt enthaelt audience", 'Hochschulleitungen' in prompt_dyn)
assert_true("dyn-prompt enthaelt section ids",
            'central_tendency' in prompt_dyn and 'distribution' in prompt_dyn)
assert_true("dyn-prompt enthaelt prompt_instruction (Text)",
            'Mittelwert und Median' in prompt_dyn)
assert_true("dyn prompt chooses the instruction of the source language (dict)",
            'Form der Verteilung' in prompt_dyn and 'Shape of' not in prompt_dyn)
assert_true("en: instruction of the source language (dict)",
            'Shape of the distribution' in build_system_prompt(custom_schema, lang='en'))


# ==========================================================================
# 5. _parse_summary_response: dynamic section ids
# ==========================================================================

print()
print("=" * 60)
print("_parse_summary_response()")
print("=" * 60)

# default schema
parsed = _parse_summary_response('{"description": "A", "interpretation": "B", "segments_text": "C"}')
assert_eq("default schema: all 3 fields", parsed,
          {'description': 'A', 'interpretation': 'B', 'segments_text': 'C'})

# with Markdown wrappers
parsed = _parse_summary_response('```json\n{"description": "X", "interpretation": "Y", "segments_text": "Z"}\n```')
assert_eq("markdown wrapper removed", parsed['description'], 'X')

# Custom expected_ids
parsed = _parse_summary_response(
    '{"central_tendency": "Mittelwert 5.2", "distribution": "rechtsschief", "extra": "ignored"}',
    expected_ids=['central_tendency', 'distribution'],
)
assert_eq("custom: only the expected ids", set(parsed.keys()),
          {'central_tendency', 'distribution'})
assert_eq("custom: central_tendency", parsed['central_tendency'], 'Mittelwert 5.2')
assert_true("custom: extra field ignored", 'extra' not in parsed)

# missing section -> empty string
parsed = _parse_summary_response(
    '{"central_tendency": "X"}',
    expected_ids=['central_tendency', 'distribution', 'outliers'],
)
assert_eq("missing sections empty", parsed['outliers'], '')
assert_eq("missing sections empty 2", parsed['distribution'], '')

# corrupt JSON
parsed = _parse_summary_response('not json at all')
assert_eq("corrupt -> all empty", parsed,
          {'description': '', 'interpretation': '', 'segments_text': ''})

# JSON with newlines in the string (regex test)
parsed = _parse_summary_response('Hier ist die Antwort:\n```json\n{"description": "Eine\\nMehrzeile", "interpretation": "B", "segments_text": "C"}\n```\nFertig.')
assert_eq("markdown with prefix/suffix", parsed['description'], 'Eine\nMehrzeile')


# ==========================================================================
# 6. translate_summary iterates over the sections
# ==========================================================================

print()
print("=" * 60)
print("translate_summary()")
print("=" * 60)

class FakeLLM:
    """Answers the single translation path (complete() with a JSON array)."""
    def __init__(self):
        self.calls = []
    def complete(self, prompt, system_prompt=None, **kw):
        import json as _json, re as _re
        texts = _json.loads(prompt[prompt.index("["):prompt.rindex("]") + 1])
        tgt = {"Französisch": "fr", "Englisch": "en", "French": "fr", "English": "en"}[
            _re.search(r"Zielsprache: (\w+)|into (\w+)", prompt).group(1) or
            _re.search(r"Zielsprache: (\w+)|into (\w+)", prompt).group(2)]
        for t in texts:
            self.calls.append((t, "de", tgt))
        return _json.dumps([f"[{tgt}] {t}" for t in texts], ensure_ascii=False)


# default schema with three sections
fake = FakeLLM()
src = QuestionSummary(question_id='q1', lang='de',
                       sections={'description': 'Beschr.',
                                 'interpretation': 'Inter.',
                                 'segments_text': 'Seg.'},
                       schema_id='default_three_sections')
tr = translate_summary(src, 'fr', llm_client=fake)
assert_eq("3 LLM-Calls", len(fake.calls), 3)
assert_eq("translated description", tr.description, '[fr] Beschr.')
assert_eq("translated interpretation", tr.interpretation, '[fr] Inter.')
assert_eq("translated segments_text", tr.segments_text, '[fr] Seg.')
assert_eq("schema_id taken over", tr.schema_id, 'default_three_sections')
assert_eq("target lang", tr.lang, 'fr')

# custom schema with 5 sections
fake2 = FakeLLM()
src2 = QuestionSummary(question_id='q2', lang='de',
                        sections={'a': 'Aa', 'b': 'Bb', 'c': 'Cc', 'd': '', 'e': 'Ee'},
                        schema_id='numeric_default')
tr2 = translate_summary(src2, 'en', llm_client=fake2)
assert_eq("only 4 non-empty translations (a,b,c,e)", len(fake2.calls), 4)
assert_eq("a translated", tr2.sections['a'], '[en] Aa')
assert_eq("d stays empty", tr2.sections['d'], '')
assert_eq("e translated", tr2.sections['e'], '[en] Ee')

# special case: the fixed sentence "no significant differences" does not go
# to the LLM, the dimensions appear with their labels in the target language
_seg_cfg = {'settings': {'segmentation': {'dimensions': [
    {'id': 'region', 'label': {'de': 'Region', 'en': 'Region'}, 'source_question': 'q_region',
     'bucketing': {'type': 'identity'}},
    {'id': 'groesse', 'label': {'de': 'Größe', 'en': 'Size'}, 'source_question': 'q_size',
     'bucketing': {'type': 'identity'}},
]}}, 'question_groups': {'q_region': {'label': {'de': 'Region'}}, 'q_size': {'label': {'de': 'Größe'}}}}
qs_module._app_config_and_db = lambda: (_seg_cfg, None)
fake3 = FakeLLM()
src3 = QuestionSummary(question_id='q3', lang='de',
                        sections={'description': 'X', 'interpretation': 'Y',
                                  'segments_text': no_significant_differences_text(['Region', 'Größe'], 'de')})
tr3 = translate_summary(src3, 'en', llm_client=fake3)
assert_eq("only 2 LLM calls (segments has a standard translation)", len(fake3.calls), 2)
assert_eq("segments_text prepared translation",
          tr3.segments_text,
          no_significant_differences_text(['Region', 'Size'], 'en'))
assert_true("fixed sentence names the dimensions", 'Region, Size' in tr3.segments_text)


# ==========================================================================
# 7. save/load of summaries
# ==========================================================================

print()
print("=" * 60)
print("save/load Summary")
print("=" * 60)


class FakeDB:
    """Minimal database stub for tests."""
    def __init__(self, path):
        self.path = path
    def _connection(self):
        return _ConnectionWrapper(self.path)


class _ConnectionWrapper:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
    def __enter__(self):
        return self.conn
    def __exit__(self, *args):
        self.conn.commit()
        self.conn.close()


summary = QuestionSummary(question_id='q1', lang='de',
                          sections={'description': 'A', 'interpretation': 'B',
                                    'segments_text': 'C'},
                          schema_id='single_choice_default')
summary_custom = QuestionSummary(question_id='q2', lang='de',
                                 sections={'central_tendency': 'X', 'distribution': 'Y'},
                                 schema_id='numeric_default')

# Table structure (schema_id + sections_json)
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db_path_new = f.name

conn = sqlite3.connect(db_path_new)
conn.execute("""
    CREATE TABLE question_summaries (
        import_id INTEGER, question_id TEXT, lang TEXT,
        schema_id TEXT, sections_json TEXT,
        PRIMARY KEY (import_id, question_id, lang)
    )
""")
conn.commit()
conn.close()

db_new = FakeDB(db_path_new)

# custom schema in a new database
save_summary_to_db(db_new, import_id=1, summary=summary_custom)
loaded_custom2 = load_summary_from_db(db_new, import_id=1, question_id='q2', lang='de')
assert_true("custom in a new database: loaded", loaded_custom2 is not None)
assert_eq("custom in a new database: all sections",
          loaded_custom2.sections,
          {'central_tendency': 'X', 'distribution': 'Y'})
assert_eq("custom in a new database: schema_id",
          loaded_custom2.schema_id, 'numeric_default')

# a default-schema summary works in a new database too
save_summary_to_db(db_new, import_id=1, summary=summary)
loaded_legacy2 = load_summary_from_db(db_new, import_id=1, question_id='q1', lang='de')
assert_eq("default schema in a new database: all sections",
          loaded_legacy2.sections,
          {'description': 'A', 'interpretation': 'B', 'segments_text': 'C'})

# the texts are stored only in sections_json (no mirror columns)
conn = sqlite3.connect(db_path_new)
row = conn.execute(
    "SELECT sections_json FROM question_summaries WHERE question_id = ? AND lang = ?", ('q1', 'de')
).fetchone()
conn.close()
stored = json.loads(row[0])
assert_eq("sections_json: description", stored.get('description'), 'A')
assert_eq("sections_json: interpretation", stored.get('interpretation'), 'B')
assert_eq("sections_json: segments_text", stored.get('segments_text'), 'C')

os.unlink(db_path_new)


# ==========================================================================
# 8. End-to-end: default schema with three sections
# ==========================================================================

print()
print("=" * 60)
print("E2E: default schema, prompt from the catalog")
print("=" * 60)

# without summary_schema_overrides the default schema applies. Expected:
# - the system prompt from the catalog of the source language
# - the user prompt with data, complexity hint and answer language
# - segments_text without significant differences: a fixed sentence instead of an LLM text

class FakeResultSC:
    """Imitates an AnalysisResult of single_choice."""
    def __init__(self):
        self.question_id = 'library_use'
        self.n = 100
        self.data = {'Ja': 26, 'Nein': 74}
        self.clusters = None
        self.percentages = None
        self.warnings = []
        self.additional_content = None
        self.metadata = {}


fake_llm_calls = []
class CapturingLLM:
    def complete(self, prompt, system_prompt, **kwargs):
        fake_llm_calls.append({'prompt': prompt, 'system': system_prompt, **kwargs})
        return json.dumps({
            'description': 'Drei Viertel der Befragten nutzen die Bibliothek nicht.',
            'interpretation': 'Die Nutzung ist klar in der Minderheit.',
            'segments_text': 'vom LLM, darf nicht uebernommen werden',
        })

from utils.question_summary import generate_question_summary

not_significant = [SegmentSignificance(
    segment_type='region', segment_label='Region', is_significant=False, p_value=0.4,
    cramers_v=0.05, effect_desc='vernachlässigbar', segment_values={}, n_per_segment={})]

res = FakeResultSC()
summary = generate_question_summary(
    question_id='library_use',
    question_text='Nutzen Sie die Bibliothek?',
    handler_type='single_choice',
    result=res,
    qconfig={},
    segment_significance=not_significant,
    llm_client=CapturingLLM(),
    llm_context='Campus-Dienste einer Hochschule',
    lang='de',
    # NO schema argument -> default schema
)

assert_eq("E2E: 1 LLM-Call", len(fake_llm_calls), 1)
call = fake_llm_calls[0]
assert_eq("E2E: system prompt from the catalog",
          call['system'], build_system_prompt(DEFAULT_THREE_SECTIONS_SCHEMA, 'Campus-Dienste einer Hochschule', 'de'))
assert_true("E2E: user prompt contains the complexity hint",
            'HINWEIS: Diese Frage hat nur wenige Kategorien' in call['prompt'])
assert_true("E2E: user prompt contains the data",
            'Fragetyp: Einfachauswahl (n=100)' in call['prompt'] and 'Nein: 74' in call['prompt'])
assert_true("E2E: user prompt names the answer language", 'auf Deutsch' in call['prompt'])
assert_true("E2E: the LLM should leave segments_text empty",
            'NICHT gefüllt werden: segments_text' in call['prompt'])
assert_eq("E2E: schema_id", summary.schema_id, 'default_three_sections')
assert_eq("E2E: language = source language", summary.lang, 'de')
assert_eq("E2E: segments_text fixed sentence with the dimension",
          summary.segments_text, no_significant_differences_text(['Region'], 'de'))
assert_true("E2E: default schema property description works",
            'Bibliothek' in summary.description)

# English survey: English catalog, English data rows, label in the source language
fake_llm_calls.clear()
summary_en = generate_question_summary(
    question_id='library_use', question_text='Do you use the library?',
    handler_type='single_choice', result=FakeResultSC(), qconfig={},
    segment_significance=[], llm_client=CapturingLLM(), llm_context='Campus services', lang='en')
call_en = fake_llm_calls[0]
assert_true("E2E en: data rows in English", 'Question type: single choice (n=100)' in call_en['prompt'])
assert_true("E2E en: Antwortsprache", 'Write all texts in English' in call_en['prompt'])
assert_true("E2E en: no segment data", 'no segment data available' in call_en['prompt'])
assert_true("E2E en: no German text in the prompt",
            all(w not in call_en['prompt'] + call_en['system'] for w in ('Fragetyp', 'Ergebnisse', 'Befragten')))
assert_eq("E2E en: language", summary_en.lang, 'en')


# ==========================================================================
# 9. Custom schema: the schema path takes another code path
# ==========================================================================

print()
print("=" * 60)
print("E2E: custom schema for a numeric question")
print("=" * 60)

fake_llm_calls.clear()

# a schema like the numeric one
numeric_schema = SummarySchema(
    schema_id='numeric_default',
    handler_type='numeric',
    sections=[
        Section(id='central_tendency', label={'de': 'Lage'},
                prompt_instruction="Mittelwert und Median in Prosa zusammenfassen.",
                length_hint='short'),
        Section(id='distribution', label={'de': 'Verteilung'},
                prompt_instruction="Form der Verteilung."),
        Section(id='outliers', label={'de': 'Ausreisser'},
                prompt_instruction="Ausreisser nennen.",
                conditional='has_outliers'),
    ],
)

class CapturingLLM2:
    def complete(self, prompt, system_prompt, **kwargs):
        fake_llm_calls.append({'prompt': prompt, 'system': system_prompt, **kwargs})
        return json.dumps({
            'central_tendency': 'Median liegt bei 12.',
            'distribution': 'Rechtsschiefe Verteilung.',
            'outliers': '',  # empty, because the condition is false
        })

# schema path with an explicit schema parameter
summary2 = generate_question_summary(
    question_id='employee_count',
    question_text='Wie viele Mitarbeitende?',
    handler_type='numeric',
    result=FakeResultSC(),  # data is in the single_choice format, fine for the test
    qconfig={},
    segment_significance=[],
    llm_client=CapturingLLM2(),
    schema=numeric_schema,
    llm_context={'domain': 'Campus-Dienste', 'region': 'Hessen', 'audience': 'Hochschulleitung'},
    lang='de',
)

call2 = fake_llm_calls[0]
assert_true("custom: own sections in the system prompt",
            'Mittelwert und Median in Prosa' in call2['system'])
assert_true("custom: section ids in the prompt",
            'central_tendency' in call2['system'])
assert_eq("custom: schema_id correct",
          summary2.schema_id, 'numeric_default')
assert_eq("custom: sections from the schema",
          set(summary2.sections.keys()),
          {'central_tendency', 'distribution', 'outliers'})
assert_eq("custom: outliers empty (the LLM returned empty)",
          summary2.sections['outliers'], '')


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
