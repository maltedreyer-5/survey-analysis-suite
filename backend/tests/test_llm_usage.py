# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""LLM usage of a run: token counts from the API answer (estimate only without
it), written as llm_usage.json, as one line per run and in the status."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import llm  # noqa: E402
from utils.llm import LLMUsageStats  # noqa: E402


def test_reported_tokens_and_estimate():
    s = LLMUsageStats()
    s.record_call("x" * 40, "y" * 8, usage={"prompt_tokens": 11, "completion_tokens": 5})
    s.record_call("x" * 40, "y" * 8)                       # no usage in the answer: estimate
    s.record_call("x", "", success=False)
    assert (s.total_calls, s.successful_calls, s.failed_calls) == (3, 2, 1)
    assert (s.prompt_tokens, s.completion_tokens, s.tokens_estimated) == (11 + 10, 5 + 2, 1)
    assert "1 call(s) estimated" in s.get_summary()
    s.reset()
    assert s.to_dict() == LLMUsageStats().to_dict()


def test_finish_usage_run_writes_file_line_and_summary(tmp_path, monkeypatch):
    client = SimpleNamespace(stats=LLMUsageStats(),
                             config=SimpleNamespace(model="m", base_url="http://x/v1"))
    client.get_stats = lambda: client.stats
    client.reset_stats = client.stats.reset
    monkeypatch.setattr(llm, "get_llm_client", lambda: client)
    llm.start_usage_run()
    assert llm.finish_usage_run("pipeline", tmp_path / "out", tmp_path / "data") == ""   # no call: nothing written
    client.stats.record_call("a", "b", usage={"prompt_tokens": 3, "completion_tokens": 2})
    summary = llm.finish_usage_run("pipeline", tmp_path / "out", tmp_path / "data", survey_id="s1")
    assert "Calls: 1" in summary
    record = json.loads((tmp_path / "out" / "llm_usage.json").read_text())
    assert record["survey_id"] == "s1" and record["prompt_tokens"] == 3 and record["model"] == "m"
    llm.finish_usage_run("reexport", None, tmp_path / "data")
    runs = [json.loads(x)["run"] for x in (tmp_path / "data" / "llm_usage.jsonl").read_text().splitlines()]
    assert runs == ["pipeline", "reexport"]


def test_calls_are_counted_per_task_from_the_caller():
    s = LLMUsageStats()
    # a call made from a module of the translation service and one from an unknown module
    for module in ("utils.translation_service", "some.plugin"):
        g = {"__name__": module, "s": s}
        exec("def ask():\n    s.record_call('in', 'out', usage={'prompt_tokens': 4, 'completion_tokens': 1})\nask()", g)
    s.record_call("Hi", "", task="connection_test")
    assert s.by_task["translation"] == {"calls": 1, "failed": 0, "prompt_tokens": 4, "completion_tokens": 1}
    assert s.by_task["other:some.plugin"]["calls"] == 1 and s.by_task["connection_test"]["calls"] == 1
    assert sum(t["calls"] for t in s.by_task.values()) == s.total_calls
    assert "translation: 1 calls" in s.get_summary()
