# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
LLM integration of the survey analysis tool, through an OpenAI-compatible
chat completions API (e.g. OpenAI, vLLM).

Configuration through environment variables:
- LLM_BASE_URL: API base URL (default: http://localhost:8000/v1)
- LLM_API_KEY: API key (default: empty)
- LLM_MODEL: model name (default: default)
- LLM_MAX_TOKENS: max. tokens (default: 4096)
- LLM_MAX_TOKENS_PARAM: name of the token limit in the request
  (default: max_completion_tokens; older servers: max_tokens)
- LLM_TEMPERATURE: a temperature is sent at all only if set, and exactly this
  value. Current reasoning models reject any temperature or allow only their
  default.
- LLM_TASK_TEMPERATURES: true/false (default: false). If true, the task-specific
  values in the code apply instead of LLM_TEMPERATURE (e.g. 0.3 for
  descriptions, rising values for retries). Only useful if the model accepts any
  temperature; works only together with LLM_TEMPERATURE.
- LLM_EXTRA_PARAMS: JSON object merged into every request,
  e.g. {"reasoning_effort": "low"} or
  {"chat_template_kwargs": {"enable_thinking": false}}
- LLM_TIMEOUT: timeout in seconds (default: 300)
"""

import json
import logging
import os
import re
from typing import Optional, Union
from dataclasses import MISSING, asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

import httpx
from .languages import language_name

logger = logging.getLogger(__name__)


@dataclass
class LLMUsageStats:
    """
    Usage of the LLM in one run. Token counts come from the `usage` field of the
    API answer; for answers without it, characters / 4 is added as an estimate
    (tokens_estimated counts those calls).
    """
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_input_chars: int = 0
    total_output_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: int = 0  # successful calls without `usage` in the answer
    by_task: dict = field(default_factory=dict)  # task -> {calls, failed, prompt_tokens, completion_tokens}

    def record_call(self, input_text: str, output_text: str, success: bool = True,
                    usage: Optional[dict] = None, task: Optional[str] = None):
        """Records one LLM call (usage: the API's usage field, if any; task: see caller_task)."""
        self.total_calls += 1
        t = self.by_task.setdefault(task or caller_task(),
                                    {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0})
        t["calls"] += 1
        if not success:
            self.failed_calls += 1
            t["failed"] += 1
            return
        prompt_before, completion_before = self.prompt_tokens, self.completion_tokens
        self.successful_calls += 1
        input_len = len(input_text) if input_text else 0
        output_len = len(output_text) if output_text else 0
        self.total_input_chars += input_len
        self.total_output_chars += output_len
        usage = usage if isinstance(usage, dict) else {}
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int):
            self.prompt_tokens += prompt
            self.completion_tokens += completion
        else:
            self.prompt_tokens += input_len // 4
            self.completion_tokens += output_len // 4
            self.tokens_estimated += 1
        t["prompt_tokens"] += self.prompt_tokens - prompt_before
        t["completion_tokens"] += self.completion_tokens - completion_before

    def to_dict(self) -> dict:
        d = asdict(self)
        d["by_task"] = dict(sorted(self.by_task.items(), key=lambda kv: -kv[1]["calls"]))
        return d

    def get_summary(self) -> str:
        """Short summary for the status of a run."""
        estimated = (f", {self.tokens_estimated} call(s) estimated from characters"
                     if self.tokens_estimated else "")
        return (
            f"📊 **LLM usage:**\n"
            f"  • Calls: {self.total_calls} ({self.successful_calls} successful, {self.failed_calls} failed)\n"
            f"  • Tokens: {self.prompt_tokens:,} prompt, {self.completion_tokens:,} completion"
            f"{estimated}\n"
            f"  • Characters: {self.total_input_chars:,} in, {self.total_output_chars:,} out"
            + "".join(f"\n  • {task}: {v['calls']} calls, {v['prompt_tokens'] + v['completion_tokens']:,} tokens"
                      + (f" ({v['failed']} failed)" if v["failed"] else "")
                      for task, v in sorted(self.by_task.items(), key=lambda kv: -kv[1]["calls"]))
        )

    def reset(self):
        """Resets all counters."""
        for f in fields(self):
            setattr(self, f.name, f.default_factory() if f.default_factory is not MISSING else f.default)


# Task of an LLM call, from the function that asked for it (first caller outside the client's
# request methods): by module, or by function name inside utils.llm. Unknown callers: "other:<module>".
_TASK_BY_MODULE = {
    "export.pipeline": "cluster_assignment",
    "services.config_assistant": "config_assistant",
    "services.workbench_store": "clustering",
    "themes.theme_analyzer": "themes",
    "utils.cluster_examples": "cluster_examples",
    "utils.finding_texts": "findings",
    "utils.item_extractor": "item_extraction",
    "utils.question_summary": "summary",
    "utils.translation_service": "translation",
}
_TASK_BY_LLM_FUNCTION = {"cluster_responses": "clustering", "_split_dominant_clusters": "clustering",
                         "test_connection": "connection_test"}
_REQUEST_METHODS = {"complete", "complete_json", "complete_async", "record_call", "caller_task", "<lambda>"}


def caller_task() -> str:
    """The task of the current LLM call, derived from the call stack (see _TASK_BY_MODULE)."""
    import sys
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        func = frame.f_code.co_name
        if module.endswith("utils.llm") or module == "llm":
            if func in _TASK_BY_LLM_FUNCTION:
                return _TASK_BY_LLM_FUNCTION[func]
            if func not in _REQUEST_METHODS and not func.startswith("_request"):
                return f"other:{func}"
        else:
            return _TASK_BY_MODULE.get(module, f"other:{module}")
        frame = frame.f_back
    return "other"


def start_usage_run() -> None:
    """Starts counting the LLM usage of a run (pipeline, re-export, export)."""
    try:
        get_llm_client().reset_stats()
    except Exception as e:  # no LLM configured: nothing to count
        logger.debug(f"LLM usage not reset: {e}")


def finish_usage_run(run: str, output_dir=None, data_dir=None, survey_id: str = "") -> str:
    """
    Records the LLM usage of the finished run: as `llm_usage.json` in the
    output directory of the run, as a line in `<data_dir>/llm_usage.jsonl`
    (one line per run, kept across runs) and in the log. Returns the summary
    for the status ("" when the LLM was not used).
    """
    try:
        client = get_llm_client()
        stats = client.get_stats()
    except Exception as e:
        logger.debug(f"LLM usage not available: {e}")
        return ""
    if stats.total_calls == 0:
        return ""
    record = {
        "run": run,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "survey_id": survey_id,
        "model": client.config.model,
        "base_url": client.config.base_url,
        **stats.to_dict(),
    }
    try:
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            (Path(output_dir) / "llm_usage.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if data_dir:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(data_dir) / "llm_usage.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"LLM usage could not be written: {e}")
    logger.info(f"LLM usage ({run}): {stats.total_calls} calls, {stats.failed_calls} failed, "
                f"{stats.prompt_tokens} prompt / {stats.completion_tokens} completion tokens")
    return stats.get_summary()


_MAX_TOKENS_PARAMS = ("max_completion_tokens", "max_tokens")
# Status codes worth retrying; any other 4xx is a request the server will
# never accept (unknown parameter, bad model name, auth), so retrying only
# delays the error message.
_RETRYABLE_STATUS = {408, 409, 425, 429}


def _optional_float_env(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


def _max_tokens_param_env() -> str:
    raw = os.environ.get("LLM_MAX_TOKENS_PARAM", "").strip() or "max_completion_tokens"
    if raw not in _MAX_TOKENS_PARAMS:
        raise ValueError(
            f"LLM_MAX_TOKENS_PARAM must be one of {_MAX_TOKENS_PARAMS}, got {raw!r}"
        )
    return raw


def _extra_params_env() -> dict:
    raw = os.environ.get("LLM_EXTRA_PARAMS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM_EXTRA_PARAMS is not valid JSON: {e}") from e
    if not isinstance(value, dict):
        raise ValueError("LLM_EXTRA_PARAMS must be a JSON object")
    return value


def _strip_thinking(text: str) -> str:
    """
    Remove model reasoning that some servers leave inside the answer text.

    - Everything up to the last </think> is reasoning. This also covers
      templates that put the opening <think> into the prompt, so only the
      closing tag appears in the output.
    - An opening <think> without a closing tag means the model stopped while
      still thinking: there is no answer.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    elif "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


class LLMRequestError(Exception):
    """The server rejected the request; repeating it will not help."""


class LLMNoAnswerError(ValueError):
    """The server answered, but without usable answer text.

    Caused by configuration (token budget, thinking mode), so repeating the
    identical request only costs time and tokens.
    """


@dataclass
class LLMConfig:
    """Configuration of the OpenAI-compatible API."""
    base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("LLM_API_KEY", "")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL", "default")
    )
    max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("LLM_MAX_TOKENS", "4096"))
    )
    # None means: never send a temperature. Current reasoning models reject
    # any value other than their default, so sending one is opt-in.
    temperature: Optional[float] = field(
        default_factory=lambda: _optional_float_env("LLM_TEMPERATURE")
    )
    # Task-specific temperatures from the code replace LLM_TEMPERATURE only on
    # request: models that accept exactly one temperature fail otherwise.
    task_temperatures: bool = field(
        default_factory=lambda: _bool_env("LLM_TASK_TEMPERATURES", False)
    )
    max_tokens_param: str = field(
        default_factory=lambda: _max_tokens_param_env()
    )
    extra_params: dict = field(
        default_factory=lambda: _extra_params_env()
    )
    timeout: float = field(
        default_factory=lambda: float(os.environ.get("LLM_TIMEOUT", "300"))
    )
    verify_ssl: bool = field(
        default_factory=lambda: os.environ.get("LLM_VERIFY_SSL", "true").lower() != "false"
    )


class LLMClient:
    """Client for an OpenAI-compatible chat completions API."""
    
    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        
        # make the SSL verification configurable
        if self.config.verify_ssl:
            self.client = httpx.Client(timeout=self.config.timeout)
        else:
            import ssl
            logger.warning("⚠️ SSL verification disabled (LLM_VERIFY_SSL=false)")
            self.client = httpx.Client(timeout=self.config.timeout, verify=False)
        
        self._connection_tested = False
        self._connection_ok = False
        self._connection_error = None
        
        # Nutzungsstatistik
        self.stats = LLMUsageStats()
    
    def get_stats(self) -> LLMUsageStats:
        """Returns the current usage statistics."""
        return self.stats
    
    def reset_stats(self):
        """Resets the usage statistics."""
        self.stats.reset()
    
    def test_connection(self) -> tuple[bool, str]:
        """
        Tests the LLM connection with a minimal request.
        
        Returns:
            tuple (success, message)
        """
        if self._connection_tested:
            return self._connection_ok, self._connection_error or "OK"
        
        self._connection_tested = True
        
        try:
            url = f"{self.config.base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            
            payload = self._build_payload(
                [{"role": "user", "content": "Hi"}], max_tokens=16
            )
            
            logger.info(f"Testing the LLM connection: {url} (model: {self.config.model})")
            
            response = self.client.post(url, headers=headers, json=payload, timeout=30)
            # the test is a real call: it counts in the usage of the run
            try:
                usage = response.json().get("usage") if response.status_code == 200 else None
            except ValueError:
                usage = None
            self.stats.record_call("Hi", "", success=response.status_code == 200, usage=usage,
                                   task="connection_test")
            
            if response.status_code == 200:
                self._connection_ok = True
                self._connection_error = f"LLM OK (model: {self.config.model})"
                logger.info(self._connection_error)
                return True, self._connection_error
            else:
                self._connection_error = f"LLM error: {response.status_code} - {response.text[:200]}"
                logger.warning(self._connection_error)
                return False, self._connection_error
                
        except Exception as e:
            self._connection_error = f"LLM connection error: {str(e)}"
            logger.warning(self._connection_error)
            return False, self._connection_error
    
    def complete(
        self, 
        prompt: str, 
        system_prompt: Optional[str] = None, 
        **kwargs
    ) -> str:
        """
        Runs a chat completion with retries.
        
        Args:
            prompt: the user prompt
            system_prompt: optional system prompt
            **kwargs: max_tokens, temperature, max_retries (default: 5)
        
        Returns:
            the generated answer as a string
        """
        max_retries = kwargs.pop('max_retries', 5)
        retry_delay = 2  # seconds between attempts
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # input length for the statistics
        input_text = (system_prompt or "") + prompt
        
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        
        payload = self._build_payload(
            messages,
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            temperature=kwargs.get("temperature"),
        )
        
        url = f"{self.config.base_url}/chat/completions"
        
        logger.debug(f"LLM Request: {url}, model={self.config.model}, prompt_len={len(prompt)}")
        
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.post(
                    url, headers=headers, json=payload, timeout=self.config.timeout
                )
                if response.status_code >= 400:
                    if 400 <= response.status_code < 500 and response.status_code not in _RETRYABLE_STATUS:
                        raise LLMRequestError(
                            f"LLM server rejected the request (HTTP {response.status_code}): "
                            f"{response.text[:300]}"
                        )
                    response.raise_for_status()
                
                data = response.json()
                content = self._extract_content(data)
                
                logger.debug(f"LLM Response: {len(content)} chars (attempt {attempt})")
                
                # record the statistics (only for the successful attempt), with the token counts of the API
                self.stats.record_call(input_text, content, success=True, usage=data.get("usage"))
                
                return content
            
            except (LLMRequestError, LLMNoAnswerError):
                self.stats.record_call(input_text, "", success=False)
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"LLM request error (attempt {attempt}/{max_retries}): {e}")
                
                if attempt < max_retries:
                    import time
                    wait_time = retry_delay * attempt  # linearly growing wait
                    logger.info(f"Waiting {wait_time}s before the next attempt...")
                    time.sleep(wait_time)
        
        # all attempts failed: record the statistics
        self.stats.record_call(input_text, "", success=False)
        logger.error(f"LLM request failed after {max_retries} attempts: {last_error}")
        raise last_error
    
    def _build_payload(self, messages: list, max_tokens: int,
                       temperature: Optional[float] = None) -> dict:
        """
        Assemble a chat-completions request body.

        A temperature is only sent when LLM_TEMPERATURE is configured, and
        then exactly that value. The task-specific value passed by the caller
        replaces it only with LLM_TASK_TEMPERATURES=true. Extra parameters
        from LLM_EXTRA_PARAMS are merged in but can never replace model or
        messages.
        """
        payload = {
            "model": self.config.model,
            "messages": messages,
            self.config.max_tokens_param: max_tokens,
        }
        if self.config.temperature is not None:
            use_task_value = self.config.task_temperatures and temperature is not None
            payload["temperature"] = temperature if use_task_value else self.config.temperature
        for key, value in self.config.extra_params.items():
            if key not in ("model", "messages"):
                payload[key] = value
        return payload
    
    @staticmethod
    def _extract_content(data: dict) -> str:
        """
        Return the answer text of a chat-completions response.

        Raises ValueError with an actionable message when the model produced
        no answer, most often because a reasoning model spent the whole token
        budget on thinking.
        """
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(
                f"The LLM answer has no 'choices': {str(data)[:200]}"
            )
        choice = choices[0]
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason", "?")
        content = _strip_thinking(message.get("content") or "")
        if content:
            return content
        if finish_reason == "length":
            raise LLMNoAnswerError(
                "LLM returned no answer before reaching the token limit "
                "(finish_reason=length). Increase LLM_MAX_TOKENS or reduce the "
                "model's reasoning, e.g. via LLM_EXTRA_PARAMS."
            )
        # A reasoning field without an answer is the model's thinking, not a
        # result; using it would hand unparseable or misleading text to the
        # caller. Typical cause: thinking mode is on and the server returns
        # the answer only in the reasoning field.
        has_reasoning = bool(message.get("reasoning_content") or message.get("reasoning"))
        hint = (
            " The response contains only reasoning; disable thinking, e.g. "
            'LLM_EXTRA_PARAMS={"chat_template_kwargs": {"enable_thinking": false}}.'
            if has_reasoning else ""
        )
        raise LLMNoAnswerError(
            f"LLM returned no answer (finish_reason={finish_reason}).{hint}"
        )
    
    def _detect_example_language(self, text: str) -> str:
        """
        Detects the language of an example text.
        
        A simple but effective heuristic based on:
        - characteristic characters (é, è, ä, ö, etc.)
        - frequent function words
        
        Args:
            text: text to analyse
        
        Returns:
            language code ('de', 'fr', 'en' or 'unknown')
        """
        if not text or len(text.strip()) < 3:
            return self._survey_source_language()  # too short to tell
        
        text_lower = text.lower()
        
        # French indicators (weighted strongly)
        french_chars = set('éèêëàâùûçœîïô')
        french_char_count = sum(1 for c in text if c in french_chars)
        
        french_words = {
            "le", "la", "les", "un", "une", "des", "du", "au", "aux",
            "de", "et", "ou", "est", "sont", "dans", "sur", "pour", "par",
            "avec", "sans", "plus", "que", "qui", "ce", "cette", "ces",
            "nous", "vous", "ils", "elles", "être", "avoir", "fait",
            "cas", "droits", "douane", "gouvernementale", "souveraineté",
            "numérique", "tensions", "internationales"
        }
        
        french_patterns = ["l'", "d'", "n'", "s'", "c'", "qu'", "j'"]
        
        # German indicators
        german_chars = set('äöüß')
        german_char_count = sum(1 for c in text if c in german_chars)
        
        german_words = {
            "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
            "und", "oder", "ist", "sind", "wird", "werden", "hat", "haben",
            "für", "mit", "bei", "von", "auf", "aus", "nach", "zu", "zur",
            "auch", "noch", "schon", "bereits", "sowie", "jedoch"
        }
        
        # English indicators
        english_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "and", "or", "but", "for", "with", "from",
            "to", "at", "by", "this", "that", "which", "who", "what"
        }
        
        # extract the words
        words = set(text_lower.replace('-', ' ').replace(',', ' ').replace('.', ' ').replace(':', ' ').split())
        
        # compute the scores
        french_score = french_char_count * 3  # weight French characters strongly
        german_score = german_char_count * 3
        english_score = 0
        
        # count the word matches
        french_score += len(words & french_words) * 2
        german_score += len(words & german_words) * 2
        english_score += len(words & english_words) * 2
        
        # French patterns (apostrophes)
        for pattern in french_patterns:
            if pattern in text_lower:
                french_score += 3
        
        logger.debug(f"Language scores for '{text[:40]}...': DE={german_score}, FR={french_score}, EN={english_score}")
        
        # decision
        if french_score > german_score and french_score > english_score:
            return 'fr'
        elif english_score > german_score and english_score > french_score:
            return 'en'
        elif german_score > 0:
            return 'de'
        else:
            # Uncertain: assume the survey's source language
            return self._survey_source_language()
    
    async def complete_async(
        self, 
        user_prompt: str, 
        system_prompt: Optional[str] = None, 
        **kwargs
    ) -> str:
        """Async wrapper (calls complete)."""
        return self.complete(user_prompt, system_prompt, **kwargs)
    
    
    @staticmethod
    def _survey_source_language() -> str:
        """Source language of the loaded survey (de if none is loaded)."""
        try:
            from core import state
            from utils.languages import get_source_language
            return get_source_language(state.config)
        except Exception:
            return 'de'

    def complete_json(
        self, 
        prompt: str, 
        system_prompt: Optional[str] = None, 
        **kwargs
    ) -> Union[dict, list]:
        """
        Completion with JSON parsing.
        
        Returns:
            the parsed JSON as dict or list
        """
        response = self.complete(prompt, system_prompt, **kwargs)
        
        # debug: show the start and end of the answer
        logger.debug(f"LLM raw response (first 300 characters): {response[:300]}")
        logger.debug(f"LLM raw response (last 200 characters): ...{response[-200:]}")
        
        # the whole answer as JSON (also inside a Markdown code block)
        text = response.strip()
        fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # JSON inside other text: the bracket that comes first decides whether a list or
        # an object is meant (a list of objects must not be read as its first object)
        first_obj, first_arr = response.find('{'), response.find('[')
        patterns = [r'\{[\s\S]*\}', r'\[[\s\S]*\]']
        if first_arr != -1 and (first_obj == -1 or first_arr < first_obj):
            patterns.reverse()
        for pattern in patterns:
            match = re.search(pattern, response)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass   # not parsable: try the next form, then the repair below
        
        # repair attempt for truncated arrays:
        # starts with [ but is no valid JSON array
        if '[' in response:
            array_start = response.find('[')
            partial_array = response[array_start:]
            
            # try several repairs (best first)
            repair_attempts = []
            
            # 1. ends with ] but does not parse: try a clean-up
            if partial_array.rstrip().endswith(']'):
                # maybe a missing comma or quotation mark
                repair_attempts.append(partial_array)
            
            # 2. remove everything after the last complete string and close
            last_quote = partial_array.rfind('"')
            if last_quote > 0:
                # is the string complete (even number of quotation marks up to there)?
                before_last = partial_array[:last_quote+1]
                # count the quotation marks (ignoring escaped ones)
                quote_count = len(re.findall(r'(?<!\\)"', before_last))
                if quote_count % 2 == 0:  # even = complete
                    repair_attempts.append(before_last + ']')
                else:
                    # incomplete string: find the previous complete element
                    last_comma = before_last.rfind(',')
                    if last_comma > 0:
                        repair_attempts.append(before_last[:last_comma] + ']')
            
            # 3. close with "] if a string is open
            repair_attempts.append(partial_array.rstrip() + '"]')
            
            # 4. simply close with ]
            repair_attempts.append(partial_array.rstrip() + ']')
            
            # 5. remove the last element completely
            last_comma = partial_array.rfind(',')
            if last_comma > 0:
                repair_attempts.append(partial_array[:last_comma] + ']')
            
            for repaired in repair_attempts:
                try:
                    result = json.loads(repaired)
                    if isinstance(result, list) and len(result) > 0:
                        logger.info(f"JSON array repaired: {len(result)} elements")
                        return result
                except json.JSONDecodeError:
                    continue
            
            # last resort: extract all complete strings
            string_pattern = r'"(?:[^"\\]|\\.)*"'
            matches = re.findall(string_pattern, partial_array)
            if matches:
                extracted = []
                for m in matches:
                    try:
                        extracted.append(json.loads(m))
                    except:
                        extracted.append(m[1:-1])
                if extracted:
                    logger.info(f"Extracted {len(extracted)} strings from incomplete JSON")
                    return extracted
        
        # fallback: empty dict
        logger.warning(f"JSON parsing failed: {response[:100]}")
        return {}
    
    def cluster_responses(
        self,
        question: str,
        responses: list[dict],
        min_cluster_size: int = 2,
        max_clusters: int = 10,
        hint: str = None,
        max_cluster_pct: int = None
    ) -> dict:
        """
        Clusters survey answers with the LLM.
        
        Args:
            question: question text for context
            responses: list of dicts with 'id' and 'text'
            min_cluster_size: minimum cluster size
            max_clusters: maximum number of clusters
            hint: optional question-specific differentiation hint
            max_cluster_pct: optional max. share of a cluster in %, None = dynamic default
        
        Returns:
            dict with 'clusters', each cluster with:
            - name: dict {language: text}; the source language from the LLM, the
              other output languages through the TranslationService
            - description: dict like name
            - answer_ids: list[int]
            - examples: list[str]
        """
        # dynamic minimum number of clusters by the number of answers
        n_responses = len(responses)
        if n_responses >= 100:
            min_clusters = 5
        elif n_responses >= 50:
            min_clusters = 4
        elif n_responses >= 20:
            min_clusters = 3
        elif n_responses >= 10:
            min_clusters = 2
        else:
            min_clusters = 1
        
        
        # maximum share of a cluster (dynamic by the number of answers):
        # these thresholds prevent extreme concentration (e.g. 80%+), but allow a
        # natural dominance of single topics (up to ~50%).
        # Can be overridden per question in questions.yaml.
        if max_cluster_pct is not None:
            # configured explicitly: use this value
            pass
        elif n_responses >= 100:
            max_cluster_pct = 50
        elif n_responses >= 50:
            max_cluster_pct = 55
        elif n_responses >= 20:
            max_cluster_pct = 60
        else:
            max_cluster_pct = 70
        
        # format the answers
        answers_text = "\n".join(
            f"[{r['id']}] {r['text']}" 
            for r in responses
        )
        
        # hint for question-specific differentiation
        hint_text = self._clustering_prompt('hint', hint=hint) if hint else ""
        
        prompt = self._clustering_prompt('user', question=question, hint=hint_text, total=n_responses,
                                         answers=answers_text, min_clusters=min_clusters)
        system_prompt = self._clustering_prompt(
            'system', max_clusters=max_clusters, min_clusters=min_clusters,
            min_size=min_cluster_size, total=n_responses, max_pct=max_cluster_pct)
        
        try:
            logger.info(f"Clustering request: {n_responses} answers, {min_clusters}-{max_clusters} clusters, max_pct={max_cluster_pct}%" + (" (with hint)" if hint else ""))
            
            # at most 3 attempts
            for attempt in range(3):
                result = self.complete_json(
                    prompt,
                    system_prompt,
                    temperature=0.6 + (attempt * 0.15),  # higher temperature on the retry
                    max_tokens=8192
                )
                
                # debug: what did the LLM return?
                logger.debug(f"LLM clustering result (type: {type(result).__name__}): {str(result)[:500]}...")
                
                # validation and post-processing
                if not isinstance(result, dict):
                    logger.warning(f"The LLM returned no dict but {type(result).__name__}: {str(result)[:200]}")
                    result = {"clusters": []}
                elif "clusters" not in result:
                    logger.warning(f"The LLM result contains no 'clusters': {list(result.keys())}")
                    result = {"clusters": []}
                
                # are there clusters with ids?
                n_clusters_found = len(result.get("clusters", []))
                total_ids_in_clusters = sum(len(c.get("answer_ids", [])) for c in result.get("clusters", []))
                
                if total_ids_in_clusters > 0 and n_clusters_found >= min_clusters:
                    logger.info(f"Clustering attempt {attempt + 1}: {total_ids_in_clusters}/{n_responses} IDs in {n_clusters_found} clusters ✓")
                    break  # success!
                elif total_ids_in_clusters > 0 and n_clusters_found < min_clusters:
                    logger.warning(
                        f"Clustering attempt {attempt + 1}: only {n_clusters_found} clusters "
                        f"(minimum: {min_clusters}), {total_ids_in_clusters} IDs – retrying with an explicit hint..."
                    )
                    if attempt < 2:
                        # with only 1 cluster the problem is usually that all answers belong to the
                        # same TOPIC but differ in APPROACH/DEGREE; so ask explicitly for
                        # differentiation along other dimensions.
                        if n_clusters_found <= 1:
                            hint_retry = self._clustering_prompt('retry_hint', hint=hint) if hint else ""
                            prompt = self._clustering_prompt(
                                'retry_single', question=question, hint=hint_retry, total=n_responses,
                                answers=answers_text, min_clusters=min_clusters, max_clusters=max_clusters)
                        else:
                            prompt = self._clustering_prompt(
                                'retry_few', question=question, found=n_clusters_found, total=n_responses,
                                answers=answers_text, min_clusters=min_clusters, max_clusters=max_clusters)
                    else:
                        # last attempt failed: accept what we have
                        logger.warning(f"Clustering: accepting {n_clusters_found} clusters after 3 attempts")
                        break
                else:
                    logger.warning(f"Clustering attempt {attempt + 1}: no IDs found in the clusters, retrying...")
                    if attempt < 2:
                        prompt = self._clustering_prompt(
                            'retry_empty', question=question, total=n_responses, answers=answers_text,
                            min_clusters=min_clusters, max_clusters=max_clusters)
            
            # =================================================================
            # DOMINANCE CHECK: split clusters that are too large
            # =================================================================
            result = self._split_dominant_clusters(
                result, question, responses, max_cluster_pct, max_clusters, hint
            )
            
            # extract examples from the answers if missing
            id_to_text = {r['id']: r['text'] for r in responses}
            
            # Names and descriptions are texts in the source language here;
            # other output languages are added at the end (_translate_cluster_texts).
            for cluster in result.get("clusters", []):
                cluster["name"] = self._name_text(cluster.get("name", ""))
                cluster["description"] = self._name_text(cluster.get("description", ""))
            
            # detect vague cluster names and warn
            vague_patterns = [
                'sonstig', 'weitere', 'allgemein', 'verschieden', 
                'andere', 'übrig', 'misc', 'other', 'general'
            ]
            
            for cluster in result.get("clusters", []):
                cluster_name_str = cluster["name"].lower()
                
                # check for vague names
                is_vague = any(pattern in cluster_name_str for pattern in vague_patterns)
                if is_vague:
                    logger.warning(f"Vague cluster name detected: '{cluster_name_str}' - should be more specific")
                    cluster["_warning"] = "Vager Sammelcluster"
                
                if "examples" not in cluster or not cluster["examples"]:
                    # generate examples from answer_ids
                    examples = []
                    for aid in cluster.get("answer_ids", [])[:3]:
                        if aid in id_to_text:
                            examples.append(id_to_text[aid])
                    cluster["examples"] = examples
            
            # =================================================================
            # DUPLICATE DETECTION: merge similar clusters
            # =================================================================
            clusters = result.get("clusters", [])
            if len(clusters) >= 2:
                merged_indices = set()
                for i in range(len(clusters)):
                    if i in merged_indices:
                        continue
                    name_i_str = self._name_text(clusters[i].get("name")).lower()
                    # extract keywords (words with 4+ characters)
                    words_i = set(w for w in re.split(r'[\s,\-/&]+', name_i_str) if len(w) >= 4)
                    
                    for j in range(i + 1, len(clusters)):
                        if j in merged_indices:
                            continue
                        name_j_str = self._name_text(clusters[j].get("name")).lower()
                        words_j = set(w for w in re.split(r'[\s,\-/&]+', name_j_str) if len(w) >= 4)
                        
                        # check the overlap: >= 50% common keywords
                        if words_i and words_j:
                            overlap = words_i & words_j
                            smaller_set = min(len(words_i), len(words_j))
                            if smaller_set > 0 and len(overlap) / smaller_set >= 0.5:
                                # merge the smaller into the larger
                                ids_i = len(clusters[i].get("answer_ids", []))
                                ids_j = len(clusters[j].get("answer_ids", []))
                                larger, smaller = (i, j) if ids_i >= ids_j else (j, i)
                                
                                logger.warning(
                                    f"Duplicate cluster detected: '{name_j_str}' → "
                                    f"merged into '{name_i_str}' "
                                    f"(overlap: {overlap})"
                                )
                                clusters[larger]["answer_ids"].extend(
                                    clusters[smaller].get("answer_ids", [])
                                )
                                merged_indices.add(smaller)
                
                if merged_indices:
                    result["clusters"] = [c for i, c in enumerate(clusters) if i not in merged_indices]
                    logger.info(f"Duplicate clean-up: {len(merged_indices)} clusters merged, "
                               f"{len(result['clusters'])} clusters remain")
            
            # translate the examples into all output languages (with language detection!)
            logger.info("Translating the cluster examples into all languages...")
            
            # Examples are respondents' own words, possibly in several
            # languages: translate them into every output language, batched
            # per cluster and detected language, with the question as context.
            from core import state
            from utils.languages import get_output_languages
            from utils.translation_service import TranslationContext, TranslationService
            out_langs = get_output_languages(state.config)
            translator = TranslationService(self, state.config, state.db)
            context = TranslationContext.from_config(state.config, kind="answer",
                                                     question_text=str(question or ""))
            
            for ci, cluster in enumerate(result.get("clusters", [])):
                examples = [e if isinstance(e, str) else "" for e in cluster.get("examples", [])]
                translated = {lang: list(examples) for lang in out_langs}
                by_lang: dict = {}
                for ei, example in enumerate(examples):
                    if example.strip():
                        by_lang.setdefault(self._detect_example_language(example), []).append(ei)
                for src_lang, idxs in by_lang.items():
                    texts = [examples[i] for i in idxs]
                    for lang in out_langs:
                        if lang == src_lang:
                            continue
                        try:
                            results = translator.translate_many(
                                texts, lang, context,
                                source_lang=None if src_lang in (None, "auto", "unknown") else src_lang)
                        except Exception as e:
                            logger.warning(f"Example translation failed: {e}")
                            continue
                        for i, new in zip(idxs, results):
                            translated[lang][i] = new
                cluster["examples_translated"] = translated
            
            # count the translated examples
            total_examples = sum(len(c.get("examples", [])) for c in result.get("clusters", []))
            logger.info(f"Examples translated: {total_examples} texts in {', '.join(out_langs)}")
            
            # === COMPLETENESS VALIDATION ===
            # were all ids assigned? assign the missing ones
            all_response_ids = set(r['id'] for r in responses)
            assigned_ids = set()
            for cluster in result.get("clusters", []):
                assigned_ids.update(cluster.get("answer_ids", []))
            
            missing_ids = all_response_ids - assigned_ids
            duplicate_ids = []
            
            # check for duplicates
            seen_ids = set()
            for cluster in result.get("clusters", []):
                for aid in cluster.get("answer_ids", []):
                    if aid in seen_ids:
                        duplicate_ids.append(aid)
                    seen_ids.add(aid)
            
            if duplicate_ids:
                logger.warning(f"Clustering: {len(duplicate_ids)} IDs assigned twice - cleaning up")
                # keep the first assignment (= the LLM's intent):
                # deduplicate within each cluster, then keep the first assignment across clusters.
                seen_ids = set()
                for cluster in result.get("clusters", []):
                    unique_ids = []
                    for aid in cluster.get("answer_ids", []):
                        if aid not in seen_ids:
                            unique_ids.append(aid)
                            seen_ids.add(aid)
                    cluster["answer_ids"] = unique_ids
            
            if missing_ids:
                logger.warning(f"Clustering: {len(missing_ids)} of {len(all_response_ids)} IDs not assigned - distributing them")
                
                # distribute the missing ids PROPORTIONALLY to the existing cluster sizes,
                # which keeps the natural distribution created by the LLM.
                if result.get("clusters"):
                    clusters = result["clusters"]
                    total_in_clusters = sum(len(c.get("answer_ids", [])) for c in clusters)
                    
                    if total_in_clusters > 0:
                        # weighted distribution by cluster size
                        missing_list = list(missing_ids)
                        for i, missing_id in enumerate(missing_list):
                            # proportional: position in the cumulative size vector
                            cumulative = 0
                            target_pos = (i % total_in_clusters)
                            target_cluster = clusters[-1]  # fallback
                            for c in clusters:
                                cumulative += len(c.get("answer_ids", []))
                                if target_pos < cumulative:
                                    target_cluster = c
                                    break
                            target_cluster["answer_ids"].append(missing_id)
                    else:
                        # all clusters empty: distribute evenly
                        missing_list = list(missing_ids)
                        for i, missing_id in enumerate(missing_list):
                            clusters[i % len(clusters)]["answer_ids"].append(missing_id)
                    
                    logger.info(f"Clustering: {len(missing_ids)} missing IDs distributed proportionally")
                else:
                    # no cluster present: create a new one
                    result["clusters"] = [{
                        "name": self._fixed_text("clustering.other_mentions", out_langs),
                        "description": self._fixed_text("clustering.auto_assigned", out_langs),
                        "answer_ids": list(missing_ids),
                        "examples": [id_to_text.get(mid, "") for mid in list(missing_ids)[:3]],
                        "examples_translated": {lang: [] for lang in out_langs}
                    }]
            
            # final statistics
            final_assigned = sum(len(c.get("answer_ids", [])) for c in result.get("clusters", []))
            logger.info(f"Clustering finished: {final_assigned}/{len(responses)} IDs in {len(result.get('clusters', []))} clusters")
            
            # log the distribution
            for c in result.get("clusters", []):
                c_name_str = self._name_text(c.get("name", ""))
                c_count = len(c.get("answer_ids", []))
                c_pct = c_count / len(responses) * 100 if responses else 0
                logger.info(f"  Cluster '{c_name_str[:50]}': {c_count} ({c_pct:.1f}%)")
            
            # === REMOVE EMPTY CLUSTERS ===
            # after the duplicate clean-up and the redistribution of ids, clusters with
            # 0 assignments may remain
            clusters_before = len(result.get("clusters", []))
            result["clusters"] = [
                c for c in result.get("clusters", [])
                if len(c.get("answer_ids", [])) > 0
            ]
            clusters_removed = clusters_before - len(result["clusters"])
            if clusters_removed > 0:
                logger.info(f"Clustering: {clusters_removed} empty clusters removed, {len(result['clusters'])} remain")
            
            # === MERGE CLUSTERS THAT ARE TOO SMALL ===
            # singletons (1 item) go into the largest cluster.
            # min_cluster_size from the configuration is a hint in the prompt to the LLM;
            # here only the hard minimum (2) is enforced.
            hard_min = 2
            if len(result.get("clusters", [])) > 1:
                large_clusters = [
                    c for c in result["clusters"]
                    if len(c.get("answer_ids", [])) >= hard_min
                ]
                small_clusters = [
                    c for c in result["clusters"]
                    if len(c.get("answer_ids", [])) < hard_min
                ]
                if small_clusters and large_clusters:
                    largest = max(large_clusters, key=lambda c: len(c.get("answer_ids", [])))
                    merged_count = 0
                    for sc in small_clusters:
                        sc_ids = sc.get("answer_ids", [])
                        sc_name_str = self._name_text(sc.get("name", ""))
                        largest["answer_ids"].extend(sc_ids)
                        merged_count += len(sc_ids)
                        logger.info(
                            f"Clustering: singleton '{sc_name_str}' ({len(sc_ids)} IDs) "
                            f"→ merged into the largest cluster"
                        )
                    # keep only clusters with >= hard_min
                    result["clusters"] = large_clusters
                    logger.info(
                        f"Clustering: {len(small_clusters)} singleton clusters "
                        f"({merged_count} IDs) merged, "
                        f"{len(result['clusters'])} clusters remain"
                    )
            
            # Names and descriptions into every output language, through the
            # single translation path (glossary, translation memory, manual
            # corrections take precedence)
            self._translate_cluster_texts(result.get("clusters", []), question, translator, out_langs)
            
            return result
            
        except Exception as e:
            logger.error(f"Clustering error: {e}")
            # fallback: all in one cluster
            try:
                from core import state
                from utils.languages import get_output_languages
                out_langs = get_output_languages(state.config)
            except Exception:
                out_langs = [self._survey_source_language()]
            return {
                "clusters": [{
                    "name": self._fixed_text("clustering.all_responses", out_langs),
                    "description": self._fixed_text("clustering.failed", out_langs),
                    "answer_ids": [r['id'] for r in responses],
                    "examples": [r['text'] for r in responses[:3]],
                    "examples_translated": {lang: [r['text'] for r in responses[:3]] for lang in out_langs},
                }]
            }
    
    # -- helpers for clustering -------------------------------------------------
    
    def _clustering_prompt(self, key: str, **fields) -> str:
        """Prompt clustering.<key> from the catalog of the survey's source language."""
        from utils import prompts
        return prompts.prompt(f"clustering.{key}", self._survey_source_language(), **fields)
    
    def _name_text(self, value) -> str:
        """Cluster name or description as text in the source language."""
        if isinstance(value, dict):
            from utils.languages import localized
            src = self._survey_source_language()
            return str(localized(value, src, src) or "")
        return "" if value is None else str(value)
    
    def _fixed_text(self, key: str, langs) -> dict:
        """Fixed text from locales/ in the source language and every output language."""
        from utils.languages import text
        src = self._survey_source_language()
        return {lang: text(key, lang) for lang in dict.fromkeys([src, *langs])}
    
    def _translate_cluster_texts(self, clusters: list, question: str, translator, out_langs) -> None:
        """
        Turn the source-language names and descriptions into {lang: text}.

        All names of a question are translated together (consistent
        terminology), then all descriptions. A target language is only added
        where the translation differs from the source text, so a failed
        translation is retried later by the export instead of being stored
        as if it were one.
        """
        from utils.translation_service import TranslationContext
        src = self._survey_source_language()
        todo = [c for c in clusters if not isinstance(c.get("name"), dict)]
        for field_name, kind in (("name", "cluster_name"), ("description", "cluster_description")):
            texts = [self._name_text(c.get(field_name)) for c in todo]
            values = [{src: text} for text in texts]
            context = TranslationContext.from_config(translator.config, kind=kind,
                                                     question_text=str(question or ""))
            for lang in out_langs:
                if lang == src or not any(texts):
                    continue
                try:
                    translated = translator.translate_many(texts, lang, context)
                except Exception as e:
                    logger.warning(f"Translation of the cluster {field_name}s into {lang} failed: {e}")
                    continue
                for value, original, new in zip(values, texts, translated):
                    if new and new != original:
                        value[lang] = new
            for cluster, value in zip(todo, values):
                cluster[field_name] = value
    
    def _split_dominant_clusters(
        self, result: dict, question: str, responses: list[dict],
        max_cluster_pct: int, max_clusters: int, hint: str = None
    ) -> dict:
        """
        Checks whether a cluster is too dominant (>max_cluster_pct% of all ids)
        and splits it by sub-clustering if needed.
        
        Args:
            result: clustering result so far
            question: original question text
            responses: all answers with 'id' and 'text'
            max_cluster_pct: maximum share of a cluster in percent
            max_clusters: maximum total number of clusters
        
        Returns:
            the updated result dict
        """
        clusters = result.get("clusters", [])
        if not clusters:
            return result
        
        total_ids = sum(len(c.get("answer_ids", [])) for c in clusters)
        if total_ids == 0:
            return result
        
        threshold = total_ids * max_cluster_pct / 100
        id_to_text = {r['id']: r['text'] for r in responses}
        
        # find dominant clusters
        dominant_indices = []
        for i, cluster in enumerate(clusters):
            n_ids = len(cluster.get("answer_ids", []))
            pct = n_ids / total_ids * 100
            if n_ids > threshold and n_ids >= 10:
                name_str = self._name_text(cluster.get("name")) or f"Cluster {i}"
                logger.warning(
                    f"Dominant cluster detected: '{name_str}' "
                    f"with {n_ids}/{total_ids} mentions ({pct:.0f}%) – starting sub-clustering"
                )
                dominant_indices.append(i)
        
        if not dominant_indices:
            return result
        
        # split only the largest dominant cluster (to avoid fragmentation)
        dom_idx = max(dominant_indices, key=lambda i: len(clusters[i].get("answer_ids", [])))
        dom_cluster = clusters[dom_idx]
        dom_ids = dom_cluster.get("answer_ids", [])
        dom_name_str = self._name_text(dom_cluster.get("name"))
        
        # how many sub-clusters?
        remaining_slots = max_clusters - len(clusters) + 1  # +1 because the dominant one is replaced
        n_sub = min(max(3, len(dom_ids) // 20), remaining_slots, 6)
        
        # sub-clustering only for the answers in the dominant cluster
        sub_responses = []
        for aid in dom_ids:
            if aid in id_to_text:
                sub_responses.append({"id": aid, "text": id_to_text[aid]})
        
        if len(sub_responses) < 6:
            logger.info("Dominant cluster too small for sub-clustering, skipping")
            return result
        
        sub_answers_text = "\n".join(f"[{r['id']}] {r['text']}" for r in sub_responses)
        
        # is this the ONLY cluster? Then a stronger differentiation prompt is needed
        is_single_cluster = (len(clusters) == 1)
        
        # hint for question-specific differentiation
        hint_sub = self._clustering_prompt('split_hint', hint=hint) if hint else ""
        
        sub_prompt = self._clustering_prompt(
            'split_single' if is_single_cluster else 'split_dominant',
            total=len(sub_responses), question=question, cluster=dom_name_str, hint=hint_sub,
            answers=sub_answers_text, n_min=n_sub, n_max=min(n_sub + 2, 6))
        sub_system = self._clustering_prompt('split_system')
        
        try:
            # single cluster: 2 attempts with rising temperature
            # dominant cluster: also 2 attempts (the JSON parsing may fail)
            max_sub_attempts = 2
            
            for sub_attempt in range(max_sub_attempts):
                sub_temp = 0.5 + (sub_attempt * 0.2)
                sub_result = self.complete_json(
                    sub_prompt, sub_system,
                    temperature=sub_temp,
                    max_tokens=8192
                )
                
                # complete_json may return a list instead of a dict
                # (with incomplete JSON / fallback parsing)
                if isinstance(sub_result, list):
                    # does the list contain cluster dicts?
                    if sub_result and isinstance(sub_result[0], dict) and "answer_ids" in sub_result[0]:
                        sub_clusters = sub_result
                    else:
                        logger.warning(f"Sub-clustering: unexpected list format (attempt {sub_attempt + 1}), retrying...")
                        sub_clusters = []
                elif isinstance(sub_result, dict):
                    sub_clusters = sub_result.get("clusters", [])
                else:
                    sub_clusters = []
                
                if len(sub_clusters) >= 2:
                    # validation: do the sub-clusters contain ids?
                    sub_total = sum(len(c.get("answer_ids", [])) for c in sub_clusters)
                    if sub_total > 0:
                        logger.info(
                            f"Sub-clustering successful (attempt {sub_attempt + 1}): "
                            f"'{dom_name_str}' → {len(sub_clusters)} sub-clusters ({sub_total} IDs)"
                        )
                        
                        # replace the dominant cluster by the sub-clusters
                        new_clusters = []
                        for i, c in enumerate(clusters):
                            if i == dom_idx:
                                new_clusters.extend(sub_clusters)
                            else:
                                new_clusters.append(c)
                        
                        result["clusters"] = new_clusters
                        result["_sub_clustered"] = True
                        result["_sub_clustered_from"] = dom_name_str
                        return result
                
                if sub_attempt < max_sub_attempts - 1:
                    logger.warning(
                        f"Sub-clustering attempt {sub_attempt + 1}: only {len(sub_clusters)} clusters, "
                        f"retrying with temperature {sub_temp + 0.2:.1f}"
                    )
            
            logger.warning("Sub-clustering produced fewer than 2 clusters, keeping the original")
            
        except Exception as e:
            logger.warning(f"Sub-clustering failed: {e}, keeping the original")
        
        return result
    
    def close(self):
        """Closes the HTTP client."""
        self.client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# singleton instance
_default_client: Optional[LLMClient] = None


def get_llm_client(config: Optional[LLMConfig] = None) -> LLMClient:
    """
    Returns the default LLM client instance.
    
    Returns:
        LLMClient instance
    """
    global _default_client
    if _default_client is None:
        _default_client = LLMClient(config)
    return _default_client


def reset_llm_client():
    """Resets the LLM client (for tests or a new configuration)."""
    global _default_client
    if _default_client:
        _default_client.close()
    _default_client = None
