# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Tests of the configuration assistant and the overview.

Verifies:
1. config_io: load/write with snapshots, rotation, restore
2. config_assistant: suggestions without an LLM (graceful), apply_suggestions
3. survey_status: building and rendering the WorkflowStatus

Run: python tests/suite_config_assistant.py
"""

import sys
import os
import types
import importlib.util
import tempfile
import shutil
import time
from pathlib import Path

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


# utils-Stubs
if 'utils' in sys.modules:
    del sys.modules['utils']
utils_pkg = types.ModuleType('utils')
utils_pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'utils')]
utils_pkg.set_label_replacements = lambda *a, **kw: None
sys.modules['utils'] = utils_pkg

# Translation stub
tm_stub = types.ModuleType('utils.translation')
class _TM:
    def t(self, key, lang='de', **kw): return key
tm_stub.get_translation_manager = lambda: _TM()
sys.modules['utils.translation'] = tm_stub


HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Services laden
services_pkg = types.ModuleType('services')
services_pkg.__path__ = [os.path.join(ROOT, 'services')]
sys.modules['services'] = services_pkg


# 'core' must be stubbed, as config_io imports it
core_stub = types.ModuleType('core')
TEMP_SURVEY_DIR = None  # set per test
def _get_active_config_dir_stub():
    return TEMP_SURVEY_DIR
core_stub.get_active_config_dir = _get_active_config_dir_stub
core_stub.get_active_survey_id = lambda: 'test_survey'
core_stub.get_registry = lambda: type('R', (), {
    'get_survey': lambda self, sid: type('M', (), {'get_title': lambda self, lang='de': 'Test Survey'})()
})()
core_stub.OUTPUT_DIR = '/tmp/nonexistent_outputs_for_tests'
sys.modules['core'] = core_stub


config_io = _load(
    'services.config_io',
    os.path.join(ROOT, 'services', 'config_io.py')
)
config_assistant = _load(
    'services.config_assistant',
    os.path.join(ROOT, 'services', 'config_assistant.py')
)
survey_status = _load(
    'services.survey_status',
    os.path.join(ROOT, 'services', 'survey_status.py')
)


import yaml
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
        a = repr(actual)[:200]
        e = repr(expected)[:200]
        RESULTS.append(('FAIL', f"{label}: expected {e}, got {a}"))


def assert_in(label, item, container):
    global PASS, FAIL
    if item in container:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: {item!r} not in {container!r}"))


def assert_true(label, value):
    global PASS, FAIL
    if value:
        PASS += 1
        RESULTS.append(('OK', label))
    else:
        FAIL += 1
        RESULTS.append(('FAIL', f"{label}: expected truthy, got {value!r}"))


# ==========================================================================
# Test setup helpers
# ==========================================================================

def with_temp_survey():
    """Creates a temporary survey folder and activates it."""
    global TEMP_SURVEY_DIR
    tmpdir = tempfile.mkdtemp(prefix='l12_test_')
    TEMP_SURVEY_DIR = Path(tmpdir)
    return Path(tmpdir)


def cleanup_temp_survey(tmpdir):
    shutil.rmtree(tmpdir, ignore_errors=True)


# ==========================================================================
# 1. config_io: load + write_with_snapshot
# ==========================================================================

print("=" * 60)
print("1. config_io: load + write_with_snapshot")
print("=" * 60)

tmp = with_temp_survey()
try:
    # 1.1 initial write (the file does not exist)
    initial = {
        'settings': {'segmentation': {'dimensions': []}},
        'question_groups': {'q1': {'columns': ['c'], 'handler': 'single_choice'}},
    }
    
    config_io.write_active_config_with_snapshot(initial)
    questions_path = tmp / 'questions.yaml'
    assert_true("file was created", questions_path.exists())
    
    # 1.2 read
    cfg = config_io.load_active_config()
    assert_eq("configuration can be read",
              cfg['question_groups']['q1']['handler'], 'single_choice')
    
    # 1.3 write with a snapshot
    cfg['settings']['llm_context'] = 'Geänderter Text'
    time.sleep(0.05)  # so the snapshot gets another timestamp
    config_io.write_active_config_with_snapshot(cfg, reason='test_change')
    
    snapshots_dir = tmp / config_io.SNAPSHOT_DIR_NAME
    assert_true("snapshot directory exists", snapshots_dir.exists())
    
    # there should be at least 1 snapshot
    snaps = list(snapshots_dir.glob('questions_*.yaml'))
    assert_eq("1 snapshot after 1 rewrite", len(snaps), 1)
    assert_in("reason in the file name", 'test_change', snaps[0].name)
    
    # 1.4 the file contains the changed text
    cfg2 = config_io.load_active_config()
    assert_eq("new llm_context stored",
              cfg2['settings']['llm_context'], 'Geänderter Text')
    
finally:
    cleanup_temp_survey(tmp)


# ==========================================================================
# 2. config_io: header comments are kept
# ==========================================================================

print()
print("=" * 60)
print("2. config_io: header comments kept")
print("=" * 60)

tmp = with_temp_survey()
try:
    # write the initial file with a header
    questions_path = tmp / 'questions.yaml'
    with questions_path.open('w', encoding='utf-8') as f:
        f.write('# Mein Header-Kommentar\n')
        f.write('# Zweite Zeile\n')
        f.write('\n')
        yaml.safe_dump({'settings': {}, 'question_groups': {}}, f, allow_unicode=True)
    
    # load the configuration, modify it, write it back
    cfg = config_io.load_active_config()
    cfg['settings']['llm_context'] = 'X'
    config_io.write_active_config_with_snapshot(cfg)
    
    # the header should be kept
    written = questions_path.read_text(encoding='utf-8')
    assert_in("header line 1 kept", '# Mein Header-Kommentar', written)
    assert_in("header line 2 kept", '# Zweite Zeile', written)
    
finally:
    cleanup_temp_survey(tmp)


# ==========================================================================
# 3. config_io: Snapshot-Rotation (>10 -> aelteste geloescht)
# ==========================================================================

print()
print("=" * 60)
print("3. config_io: Snapshot-Rotation")
print("=" * 60)

tmp = with_temp_survey()
try:
    # write 12 snapshots
    cfg = {'settings': {}, 'question_groups': {}}
    config_io.write_active_config_with_snapshot(cfg)  # initial
    for i in range(12):
        cfg['settings']['counter'] = i
        time.sleep(0.02)
        config_io.write_active_config_with_snapshot(cfg, reason=f'iter_{i}')
    
    snapshots_dir = tmp / config_io.SNAPSHOT_DIR_NAME
    snaps = list(snapshots_dir.glob('questions_*.yaml'))
    
    assert_true(
        f"Maximal MAX_SNAPSHOTS Snapshots ({len(snaps)} <= {config_io.MAX_SNAPSHOTS})",
        len(snaps) <= config_io.MAX_SNAPSHOTS
    )
    
finally:
    cleanup_temp_survey(tmp)


# ==========================================================================
# 4. config_io: list_snapshots + restore_snapshot
# ==========================================================================

print()
print("=" * 60)
print("4. config_io: list_snapshots + restore")
print("=" * 60)

tmp = with_temp_survey()
try:
    # initial write with the "original" value
    cfg = {'settings': {'mode': 'original'}, 'question_groups': {}}
    config_io.write_active_config_with_snapshot(cfg)
    
    # second version
    cfg['settings']['mode'] = 'modified'
    time.sleep(0.05)
    config_io.write_active_config_with_snapshot(cfg, reason='change_to_modified')
    
    # third version
    cfg['settings']['mode'] = 'final'
    time.sleep(0.05)
    config_io.write_active_config_with_snapshot(cfg, reason='change_to_final')
    
    # Snapshots listen
    snapshots = config_io.list_snapshots()
    assert_true(f"at least 2 snapshots present ({len(snapshots)})", len(snapshots) >= 2)
    
    # the current file has 'final'
    cur = config_io.load_active_config()
    assert_eq("current: 'final'", cur['settings']['mode'], 'final')
    
    # snapshot order (newest first):
    #   snapshots[0] = snapshot of 'modified' (before writing 'final')
    #   snapshots[1] = snapshot of 'original' (before writing 'modified')
    # the newest snapshot is restored -> 'modified'
    newest_snap = snapshots[0][0]
    config_io.restore_snapshot(newest_snap)
    
    cur = config_io.load_active_config()
    assert_eq("Nach restore: 'modified'", cur['settings']['mode'], 'modified')
    
    # restore itself takes a pre-restore snapshot
    snapshots_after = config_io.list_snapshots()
    pre_restore = [p for p, _ in snapshots_after if 'before_restore' in p.name]
    assert_true("pre-restore snapshot present", len(pre_restore) >= 1)
    
finally:
    cleanup_temp_survey(tmp)


# ==========================================================================
# 5. config_assistant: without an LLM -> empty list, no crash
# ==========================================================================

print()
print("=" * 60)
print("5. config_assistant: without an LLM connection")
print("=" * 60)

# stub the LLM client with a failure
class StubLLMFail:
    def test_connection(self):
        return False, "no LLM available"
    def complete(self, *a, **kw):
        raise RuntimeError("should not be called")
    def complete_json(self, *a, **kw):
        raise RuntimeError("should not be called")


llm_module = sys.modules.get('utils.llm') or types.ModuleType('utils.llm')
llm_module.get_llm_client = lambda config=None: StubLLMFail()
sys.modules['utils.llm'] = llm_module


class StubState:
    def __init__(self, **kw):
        self.df = kw.get('df')
        self.config = kw.get('config')
        self.db = kw.get('db')
        self.import_id = kw.get('import_id')
        self.analysis_results = kw.get('analysis_results', {})
        self.themes = kw.get('themes', {})
        self.project_id = kw.get('project_id')


state_no_llm = StubState(config={
    'settings': {},
    'question_groups': {'q1': {'label': {'de': 'Frage 1'}, 'handler': 'freetext'}},
})

s = config_assistant.suggest_llm_context(state_no_llm)
assert_eq("suggest_llm_context without an LLM -> None", s, None)

dims = config_assistant.suggest_segmentation_dimensions(state_no_llm)
assert_eq("suggest_segmentation without an LLM -> []", dims, [])

nums = config_assistant.suggest_numeric_fields(state_no_llm)
assert_eq("suggest_numeric without an LLM -> []", nums, [])

ovs = config_assistant.suggest_schema_overrides(state_no_llm)
assert_eq("suggest_schema_overrides without an LLM -> []", ovs, [])

all_s = config_assistant.generate_all_suggestions(state_no_llm)
assert_eq("generate_all without an LLM -> []", all_s, [])


# ==========================================================================
# 6. config_assistant: with an LLM mock
# ==========================================================================

print()
print("=" * 60)
print("6. config_assistant: with an LLM mock")
print("=" * 60)


class StubLLMOk:
    """Returns predictable answers."""
    def __init__(self):
        self.complete_calls = 0
        self.complete_json_calls = 0
        self._json_responses = []
        self._text_responses = []
    
    def test_connection(self):
        return True, "ok"
    
    def queue_text(self, text):
        self._text_responses.append(text)
    
    def queue_json(self, obj):
        self._json_responses.append(obj)
    
    def complete(self, prompt, system_prompt=None, **kw):
        self.complete_calls += 1
        if self._text_responses:
            return self._text_responses.pop(0)
        return "Default-Text"
    
    def complete_json(self, prompt, system_prompt=None, **kw):
        self.complete_json_calls += 1
        if self._json_responses:
            return self._json_responses.pop(0)
        return {}


stub_llm = StubLLMOk()
llm_module.get_llm_client = lambda config=None: stub_llm


# 6.1 suggest_llm_context
state6 = StubState(config={
    'settings': {},
    'question_groups': {
        'q1': {'label': {'de': 'Wie viele Mitarbeiter?'}, 'handler': 'freetext'},
        'q2': {'label': {'de': 'In welchem Land?'}, 'handler': 'single_choice'},
    },
})

stub_llm.queue_text("Eine Umfrage über Hochschul-IT in Deutschland und Österreich.")
s = config_assistant.suggest_llm_context(state6)
assert_true("LLM context suggestion created", s is not None)
if s:
    assert_eq("category", s.category, 'llm_context')
    assert_eq("Target", s.target, 'settings.llm_context')
    assert_eq("Action 'add' (war leer)", s.action, 'add')
    assert_in("text contains a context hint", "Hochschul-IT", s.new_value)


# 6.2 suggest_segmentation_dimensions
stub_llm.queue_json([
    {
        "id": "country",
        "label_de": "Land",
        "source_question": "q2",
        "bucketing": "identity",
        "reason": "Klassische demografische Frage",
        "confidence": 0.9,
    }
])
dims = config_assistant.suggest_segmentation_dimensions(state6)
assert_eq("1 dimension suggested", len(dims), 1)
if dims:
    d = dims[0]
    assert_eq("Dim category", d.category, 'segmentation')
    assert_eq("Dim ID", d.new_value['id'], 'country')
    assert_eq("source question found", d.new_value['source_question'], 'q2')
    assert_eq("confidence passed on", d.confidence, 0.9)


# 6.3 suggest_segmentation: existing dimensions are skipped
state6b = StubState(config={
    'settings': {
        'segmentation': {
            'dimensions': [{'id': 'country'}],  # exists already
        },
    },
    'question_groups': {'q2': {'label': {'de': 'Land?'}, 'handler': 'single_choice'}},
})
stub_llm.queue_json([
    {
        "id": "country",
        "label_de": "Land",
        "source_question": "q2",
        "bucketing": "identity",
        "confidence": 0.9,
    }
])
dims = config_assistant.suggest_segmentation_dimensions(state6b)
assert_eq("an existing dimension is skipped", len(dims), 0)


# 6.4 suggest_numeric_fields
state6c = StubState(
    df=pd.DataFrame({'col': [10, 20, 30, 5]}),
    config={
        'settings': {},
        'question_groups': {
            'q_count': {
                'label': {'de': 'Wie viele?'},
                'columns': ['col'],
                'handler': 'freetext',
                'lss_metadata': {'handler_type': 'numeric'},
            },
        },
    },
)
stub_llm.queue_json({
    "q_count": {
        "unit": "Personen",
        "valid_range": {"min": 0, "max": 100},
        "decimals": 0,
        "reason": "Antworten sind eindeutig Zahlen",
        "confidence": 0.95,
    }
})
nums = config_assistant.suggest_numeric_fields(state6c)
assert_eq("1 numeric suggestion", len(nums), 1)
if nums:
    n = nums[0]
    assert_eq("Numeric category", n.category, 'numeric')
    assert_eq("Numeric Patch hat handler=numeric",
              n.new_value['handler'], 'numeric')
    assert_eq("Numeric Patch hat unit",
              n.new_value['numeric']['unit'], 'Personen')
    assert_eq("Numeric Patch hat valid_range",
              n.new_value['numeric']['valid_range'],
              {'min': 0, 'max': 100})


# 6.5 suggest_schema_overrides: low confidence -> ignored
state6d = StubState(config={
    'settings': {},
    'question_groups': {
        'q_simple': {'label': {'de': 'Geschlecht?'}, 'handler': 'single_choice'},
    },
})
stub_llm.queue_json({
    "q_simple": {
        "schema_id": "minimal",
        "sections": ["description"],
        "reason": "Sehr einfache Frage",
        "confidence": 0.4,  # too low
    }
})
ovs = config_assistant.suggest_schema_overrides(state6d)
assert_eq("schema override with confidence < 0.6 is dropped",
          len(ovs), 0)

# 6.6 with a higher confidence
stub_llm.queue_json({
    "q_simple": {
        "schema_id": "minimal",
        "sections": ["description"],
        "confidence": 0.85,
    }
})
ovs = config_assistant.suggest_schema_overrides(state6d)
assert_eq("schema override with confidence >= 0.6 accepted", len(ovs), 1)


# ==========================================================================
# 7. apply_suggestions
# ==========================================================================

print()
print("=" * 60)
print("7. apply_suggestions")
print("=" * 60)

base_config = {
    'settings': {},
    'question_groups': {
        'q1': {'label': {'de': 'X'}, 'handler': 'freetext'},
        'q2': {'label': {'de': 'Y'}, 'handler': 'single_choice'},
    },
}

# put together 4 suggestions
sugs = [
    config_assistant.Suggestion(
        category='llm_context',
        target='settings.llm_context',
        action='add',
        new_value='Mein Kontext',
        confidence=1.0,
    ),
    config_assistant.Suggestion(
        category='segmentation',
        target='settings.segmentation.dimensions[+]',
        action='add',
        new_value={'id': 'land', 'label': {'de': 'Land'}, 'source_question': 'q2',
                   'bucketing': {'type': 'identity'}},
        confidence=0.9,
    ),
    config_assistant.Suggestion(
        category='numeric',
        target='question_groups.q1',
        action='replace',
        new_value={
            'type': 'numeric', 'handler': 'numeric',
            'numeric': {'unit': 'X', 'valid_range': {'min': 0, 'max': 100}, 'decimals': 0},
        },
        confidence=0.9,
    ),
    config_assistant.Suggestion(
        category='schema',
        target='question_groups.q2.summary_schema_override',
        action='replace',
        new_value={'sections': [{'id': 'description'}]},
        confidence=0.8,
    ),
]


new_cfg = config_assistant.apply_suggestions(base_config, sugs)

# Original unveraendert?
assert_eq("Original-Config unveraendert", base_config['settings'], {})

# the new configuration has all 4 changes
assert_eq("llm_context set",
          new_cfg['settings']['llm_context'], 'Mein Kontext')
assert_eq("Dimension hinzugefuegt",
          new_cfg['settings']['segmentation']['dimensions'][0]['id'], 'land')
assert_eq("q1 is numeric now",
          new_cfg['question_groups']['q1']['handler'], 'numeric')
assert_in("q1 hat numeric-Block",
          'numeric', new_cfg['question_groups']['q1'])
# Written where utils/summary_schema.SchemaResolver reads it (was 'summary_schema', never read)
assert_eq("q2 has a schema override",
          new_cfg['question_groups']['q2']['summary_schema_override']['sections'], [{'id': 'description'}])


# ==========================================================================
# 8. survey_status: WorkflowStatus
# ==========================================================================

print()
print("=" * 60)
print("8. survey_status: WorkflowStatus")
print("=" * 60)


# 8.1 empty state
status = survey_status.get_workflow_status(StubState())
assert_true("Status hat steps", len(status.steps) >= 4)
assert_eq("first step: import",
          status.steps[0].id, 'import')
assert_eq("import still open",
          status.steps[0].icon, '○')
assert_true("next_action points to the import",
            status.next_action and status.next_action['tab'] == 'import')


# 8.2 state with data and configuration, without analysis
state_partial = StubState(
    df=pd.DataFrame({'a': [1, 2, 3]}),
    config={
        'question_groups': {
            'q1': {'handler': 'single_choice'},
            'q2': {'handler': 'numeric'},
            'q3': {'handler': 'freetext'},
        },
    },
)
status = survey_status.get_workflow_status(state_partial)
import_step = next((s for s in status.steps if s.id == 'import'), None)
assert_eq("Import done", import_step.icon, '✓')
config_step = next((s for s in status.steps if s.id == 'config'), None)
assert_eq("Config done", config_step.icon, '✓')
analysis_step = next((s for s in status.steps if s.id == 'analysis'), None)
assert_eq("analysis open", analysis_step.icon, '○')

# next_action -> analysis or item extraction
nxt = status.next_action
assert_true("next_action set", nxt is not None)


# 8.3 status with analysis results
state_full = StubState(
    df=pd.DataFrame({'a': [1, 2, 3]}),
    config={
        'question_groups': {
            'q1': {'handler': 'single_choice'},
        },
    },
    analysis_results={'q1': {'data': {}}},
)
status = survey_status.get_workflow_status(state_full)
analysis_step = next((s for s in status.steps if s.id == 'analysis'), None)
assert_eq("Analyse done (1/1)", analysis_step.icon, '✓')


# ==========================================================================
# 9. survey_status: render_status_markdown
# ==========================================================================

print()
print("=" * 60)
print("9. survey_status: Markdown-Rendering")
print("=" * 60)

md = survey_status.render_status_markdown(status)
assert_in("Markdown enthaelt 'Active survey'", "Active survey", md)
assert_in("Markdown enthaelt Workflow-Status", "Workflow status", md)
# the step indicator '✓' occurs in the Markdown
assert_in("Markdown enthaelt step indicator", "✓", md)


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
