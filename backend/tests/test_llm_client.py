# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""LLM client against an OpenAI-style server that accepts only max_completion_tokens and no temperature."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import llm as llm_mod  # noqa: E402
from utils.llm import LLMClient, LLMConfig, LLMRequestError  # noqa: E402


def make_client(monkeypatch, handler, **env):
    for k in ("LLM_TEMPERATURE", "LLM_TASK_TEMPERATURES", "LLM_MAX_TOKENS_PARAM", "LLM_EXTRA_PARAMS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = LLMClient(LLMConfig(base_url="http://llm.test/v1", model="m"))
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def openai_strict(seen):
    """Behaves like current OpenAI reasoning models for the two parameters."""
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'"}})
        if "temperature" in body and body["temperature"] != 1:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    return handler


def test_default_request_is_accepted_by_strict_server(monkeypatch):
    seen = []
    c = make_client(monkeypatch, openai_strict(seen))
    # task-specific temperature as passed throughout the code base
    assert c.complete("hi", temperature=0.3, max_tokens=50) == "ok"
    assert seen[-1]["max_completion_tokens"] == 50
    assert "temperature" not in seen[-1] and "max_tokens" not in seen[-1]


def test_client_error_is_not_retried(monkeypatch):
    seen = []
    c = make_client(monkeypatch, openai_strict(seen), LLM_MAX_TOKENS_PARAM="max_tokens")
    with pytest.raises(LLMRequestError, match="HTTP 400"):
        c.complete("hi")
    assert len(seen) == 1


def test_temperature_is_opt_in_and_only_the_configured_value_is_sent(monkeypatch):
    """A model that accepts exactly one temperature works with every task of the code."""
    seen = []
    c = make_client(monkeypatch, openai_strict(seen), LLM_TEMPERATURE="1")
    assert c.complete("hi") == "ok"
    assert c.complete("hi", temperature=0.3) == "ok"            # task value ignored
    assert c.complete("hi", temperature=0.6 + 2 * 0.15) == "ok"  # retry increase ignored
    assert [b["temperature"] for b in seen] == [1.0, 1.0, 1.0]


def test_task_temperatures_on_request(monkeypatch):
    seen = []
    ok = lambda r: (seen.append(json.loads(r.content)) or httpx.Response(  # noqa: E731
        200, json={"choices": [{"message": {"content": "x"}}]}))
    c = make_client(monkeypatch, ok, LLM_TEMPERATURE="0.7", LLM_TASK_TEMPERATURES="true")
    c.complete("hi", temperature=0.3)
    c.complete("hi")
    assert [b["temperature"] for b in seen] == [0.3, 0.7]
    c = make_client(monkeypatch, ok, LLM_TASK_TEMPERATURES="true")   # without LLM_TEMPERATURE: none
    c.complete("hi", temperature=0.3)
    assert "temperature" not in seen[-1]


def test_extra_params_are_merged_but_cannot_replace_messages(monkeypatch):
    seen = []
    c = make_client(monkeypatch, openai_strict(seen),
                    LLM_EXTRA_PARAMS='{"reasoning_effort": "low", "messages": []}')
    c.complete("hi")
    assert seen[-1]["reasoning_effort"] == "low"
    assert seen[-1]["messages"][-1]["content"] == "hi"


def test_server_errors_are_retried(monkeypatch):
    calls = []
    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})
    c = make_client(monkeypatch, handler)
    assert c.complete("hi") == "late"
    assert len(calls) == 3


def test_token_limit_without_answer_gives_actionable_error(monkeypatch):
    c = make_client(monkeypatch, lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": None, "reasoning": "thinking..."}, "finish_reason": "length"}]}))
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        c.complete("hi", max_retries=1)


@pytest.mark.parametrize("message, expected", [
    ({"content": "<think>plan</think>\nAnswer"}, "Answer"),
    ({"content": "Answer", "reasoning_content": "plan"}, "Answer"),
    # opening tag in the chat template, only the closing tag in the output
    ({"content": "plan ...\n</think>\nAnswer"}, "Answer"),
    ({"content": "<think>a</think>x<think>b</think>\n{\"a\": 1}"}, '{"a": 1}'),
])
def test_thinking_is_removed_from_answer(monkeypatch, message, expected):
    c = make_client(monkeypatch, lambda r: httpx.Response(200, json={
        "choices": [{"message": message, "finish_reason": "stop"}]}))
    assert c.complete("hi") == expected


@pytest.mark.parametrize("message, match", [
    ({"content": "<think>plan</think>"}, "no answer"),
    ({"content": "<think>plan plan plan"}, "no answer"),
    ({"content": None, "reasoning": "plan"}, "enable_thinking"),
    ({"content": None, "reasoning_content": "plan"}, "enable_thinking"),
])
def test_thinking_without_answer_is_an_error(monkeypatch, message, match):
    calls = []
    c = make_client(monkeypatch, lambda r: (calls.append(1), httpx.Response(200, json={
        "choices": [{"message": message, "finish_reason": "stop"}]}))[1])
    with pytest.raises(ValueError, match=match):
        c.complete("hi")
    assert len(calls) == 1  # configuration problem: not retried


def test_invalid_env_values_fail_early(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS_PARAM", "tokens")
    with pytest.raises(ValueError):
        LLMConfig()
    monkeypatch.setenv("LLM_MAX_TOKENS_PARAM", "max_tokens")
    monkeypatch.setenv("LLM_EXTRA_PARAMS", "[1]")
    with pytest.raises(ValueError):
        LLMConfig()
    monkeypatch.setenv("LLM_EXTRA_PARAMS", "")
    monkeypatch.setenv("LLM_TASK_TEMPERATURES", "maybe")
    with pytest.raises(ValueError):
        LLMConfig()
