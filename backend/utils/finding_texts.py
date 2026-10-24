# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Description and possible reading of statistical findings.

One path for every kind of finding (associations between questions in
utils/correlation.py, differences between segments in
utils/segment_findings.py):

1. The LLM writes two texts once, in the source language, from the facts the
   code computed: a factual description and, set apart, a possible reading.
   It never sees raw data, only the facts.
2. The translation service carries both texts into the other output
   languages (one call per language for all findings of a run).
3. The texts are stored with a hash of their facts and prompts
   (utils/analysis_texts.py). Same facts and prompts: the stored texts are
   used again. A text corrected by a person is never overwritten.

Summaries over several findings (a theme of associations, a dimension of
group differences) take the same path through ``write_summary``: written
from the descriptions only, stored, a manual correction wins.

Without an LLM a finding keeps its facts and gets no texts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Dict, List, Sequence

logger = logging.getLogger(__name__)


def parse_json(response: str) -> dict:
    """The JSON object in an LLM answer (code fences and surrounding text tolerated)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (response or "").strip())
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]) if start >= 0 and end > start else {}


class FindingTextWriter:
    """
    Writes ``description`` and ``reading`` ({lang: text}) onto finding objects.

    A finding object needs the attributes ``key`` (stable id within its kind),
    ``description`` and ``reading`` (dicts, filled here). The prompts are
    ``<prefix>.system`` (placeholder ``{context}``) and ``<prefix>.user``
    (placeholder ``{facts}`` plus the fields returned by ``fields_of``).
    """

    def __init__(self, config: dict, llm_client, prompt_prefix: str, *,
                 facts_of: Callable[[Any], List[str]],
                 fields_of: Callable[[Any], Dict[str, str]],
                 store=None, db=None, temperature: float = 0.3, max_tokens: int = 600,
                 label: str = "finding"):
        from utils.languages import get_source_language
        self.config = config
        self.llm = llm_client
        self.prefix = prompt_prefix
        self.facts_of = facts_of
        self.fields_of = fields_of
        self.store = store
        self.db = db
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.label = label
        self.src = get_source_language(config)

    def facts_hash(self, finding) -> str:
        from utils import prompts
        payload = json.dumps({
            "facts": self.facts_of(finding),
            "prompt": [prompts.get(f"{self.prefix}.system", self.src),
                       prompts.get(f"{self.prefix}.user", self.src)],
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def write(self, findings: Sequence[Any], languages: Sequence[str]) -> None:
        for finding in findings:
            self._source_texts(finding)
        self._translate(list(findings), languages)

    # -- source language -------------------------------------------------------

    def _source_texts(self, finding) -> None:
        facts_hash = self.facts_hash(finding)
        stored = self.store.get(finding.key, facts_hash) if self.store else None
        if stored:
            finding.description, finding.reading = dict(stored["description"]), dict(stored["reading"])
            return
        description = reading = ""
        if self.llm is not None:
            from utils import prompts
            system = prompts.prompt(f"{self.prefix}.system", self.src,
                                    context=prompts.context_text(self.config, self.src))
            user = prompts.prompt(f"{self.prefix}.user", self.src,
                                  facts="\n".join(f"- {x}" for x in self.facts_of(finding)),
                                  **self.fields_of(finding))
            try:
                parsed = parse_json(self.llm.complete(prompt=user, system_prompt=system,
                                                      temperature=self.temperature,
                                                      max_tokens=self.max_tokens))
                description = str(parsed.get("description") or "").strip()
                reading = str(parsed.get("reading") or "").strip()
            except Exception as e:
                logger.warning(f"Text for {self.label} {finding.key} not created: {e}")
        finding.description = {self.src: description} if description else {}
        finding.reading = {self.src: reading} if reading else {}
        if self.store and description:
            self.store.put(finding.key, facts_hash, finding.description, finding.reading)

    # -- other output languages ------------------------------------------------

    def _translate(self, findings: List[Any], languages: Sequence[str]) -> None:
        if self.llm is None or not findings:
            return
        from utils.translation_service import TranslationContext, TranslationService
        service = TranslationService(self.llm, self.config, self.db)
        context = TranslationContext.from_config(self.config, kind="report")
        for lang in languages:
            if lang == self.src:
                continue
            todo = [(f, name) for f in findings for name in ("description", "reading")
                    if getattr(f, name).get(self.src) and not getattr(f, name).get(lang)]
            if not todo:
                continue
            texts = [getattr(f, name)[self.src] for f, name in todo]
            try:
                translated = service.translate_many(texts, lang, context)
            except Exception as e:
                logger.warning(f"Translation ({self.label}) into {lang} failed: {e}")
                continue
            for (f, name), original, new in zip(todo, texts, translated):
                if new and new != original:
                    getattr(f, name)[lang] = new
            if self.store:
                for f in {id(f): f for f, _ in todo}.values():
                    self.store.put(f.key, self.facts_hash(f), f.description, f.reading)


def write_summary(config: dict, llm_client, *, key: str, lines: List[str], languages: Sequence[str],
                  system_key: str, user_key: str, system_fields: Dict[str, str],
                  store=None, db=None, temperature: float = 0.3, max_tokens: int = 400,
                  label: str = "summary") -> Dict[str, str]:
    """
    A summary over several findings ({lang: text}), written from their
    descriptions only (``lines``, source language): no new figures, no
    reading. Stored like the findings themselves (under ``key`` in the same
    kind), so repeated exports reuse it and a manual correction wins.

    The system prompt gets ``{context}`` plus ``system_fields``; the user
    prompt gets the lines as ``{findings}``.
    """
    from utils import prompts
    from utils.languages import get_source_language
    src = get_source_language(config)
    if not lines:
        return {}
    payload = json.dumps({"facts": list(lines), "fields": system_fields,
                          "prompt": [prompts.get(system_key, src), prompts.get(user_key, src)]},
                         ensure_ascii=False, sort_keys=True)
    facts_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    stored = store.get(key, facts_hash) if store else None
    out: Dict[str, str] = dict(stored["description"]) if stored else {}
    if not out.get(src):
        if llm_client is None:
            return out
        try:
            text = llm_client.complete(
                prompt=prompts.prompt(user_key, src, findings="\n".join(f"- {x}" for x in lines)),
                system_prompt=prompts.prompt(system_key, src, context=prompts.context_text(config, src),
                                             **system_fields),
                temperature=temperature, max_tokens=max_tokens).strip()
        except Exception as e:
            logger.warning(f"{label} {key} not created: {e}")
            return out
        if not text:
            return out
        out = {src: text}
        if store:
            store.put(key, facts_hash, out, {})
    missing = [lang for lang in languages if lang != src and not out.get(lang)]
    if missing and llm_client is not None:
        from utils.translation_service import TranslationContext, TranslationService
        service = TranslationService(llm_client, config, db)
        context = TranslationContext.from_config(config, kind="report")
        for lang in missing:
            try:
                new = service.translate_many([out[src]], lang, context)[0]
            except Exception as e:
                logger.warning(f"Translation ({label}) into {lang} failed: {e}")
                continue
            if new and new != out[src]:
                out[lang] = new
        if store:
            store.put(key, facts_hash, out, {})
    return out
