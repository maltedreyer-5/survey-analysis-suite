# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Single path for all translations of survey content.

Every translation request carries a context package: survey title, subject
area (``settings.llm_context``), glossary, the kind of text and, where it
exists, the question it belongs to. Texts that belong together (the options
of one question, a cluster name with its description) are sent in one call,
so their terminology stays consistent.

Translations are kept in the translation memory with a scope (usually the
question id): the same text can be translated differently in different
questions, and manual corrections always take precedence.

Prompts come from ``prompts/<source language>.yaml``; the source language
of the survey decides which catalog is used.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from utils.languages import get_source_language, language_name

logger = logging.getLogger(__name__)

BASE_GLOSSARY = Path(__file__).resolve().parent.parent / "config" / "glossary.yaml"
MAX_BATCH = 40  # texts per LLM call; larger groups are split


# Language code for texts whose language is not known (see translate_many)
AUTO_SOURCE = "auto"


def prompt_catalog(source_language: str) -> dict:
    """Prompt catalog of a source language (see utils/prompts.py)."""
    from utils.prompts import catalog
    return catalog(source_language)


@dataclass
class TranslationContext:
    """What the LLM needs to know besides the texts themselves."""
    kind: str = "text"            # question, option, scale, cluster_name, ... (see prompts)
    question_text: str = ""        # the question the texts belong to, in the source language
    scope: str = ""                # translation-memory scope, usually the question id
    survey_title: str = ""
    llm_context: str = ""
    glossary: Dict[str, Dict[str, str]] = field(default_factory=dict)
    keep_terms: List[str] = field(default_factory=list)
    notes: str = ""                # what the texts are (e.g. the meaning and order of a scale)

    @classmethod
    def from_config(cls, config: Optional[dict], **kwargs) -> "TranslationContext":
        """Context with title, subject area, glossary and keep-terms of a survey."""
        settings = (config or {}).get("settings", {}) or {}
        meta = settings.get("survey_metadata", {}) or {}
        src = get_source_language(config)
        title = meta.get("survey_title") or meta.get("title") or ""
        if isinstance(title, dict):
            title = title.get(src) or next(iter(title.values()), "")
        glossary = dict(_base_glossary(src))
        glossary.update(settings.get("glossary") or {})
        return cls(survey_title=str(title), llm_context=str(settings.get("llm_context") or ""),
                   glossary=glossary, keep_terms=list(settings.get("keep_terms") or []), **kwargs)


def _base_glossary(source_language: str) -> Dict[str, Dict[str, str]]:
    """General glossary shipped with the tool; only used for its source language."""
    if not BASE_GLOSSARY.exists():
        return {}
    data = yaml.safe_load(BASE_GLOSSARY.read_text(encoding="utf-8")) or {}
    if data.get("source_language", "de") != source_language:
        return {}
    return data.get("glossary") or {}


class TranslationService:
    """Translate survey texts with context, translation memory and batching."""

    def __init__(self, llm_client, config: Optional[dict] = None, db=None):
        self.llm = llm_client
        self.config = config or {}
        self.db = db
        self.source_language = get_source_language(self.config)
        self.prompts = prompt_catalog(self.source_language)

    # -- public API ---------------------------------------------------------------

    def translate(self, text: str, target_lang: str, context: Optional[TranslationContext] = None,
                  source_lang: Optional[str] = None) -> str:
        return self.translate_many([text], target_lang, context, source_lang)[0]

    def translate_many(self, texts: Sequence[str], target_lang: str,
                       context: Optional[TranslationContext] = None,
                       source_lang: Optional[str] = None) -> List[str]:
        """
        Translate texts that belong together. Empty texts and texts already in
        the target language are returned unchanged. Texts the LLM could not
        translate are returned unchanged and logged, never replaced by
        something else.

        ``source_lang`` may be ``AUTO_SOURCE`` for texts of unknown language
        (e.g. free-text answers where language detection was unsure): the
        LLM then recognises the language itself and returns texts that are
        already in the target language unchanged.
        """
        return self.translate_many_with_status(texts, target_lang, context, source_lang)[0]

    def translate_with_status(self, text: str, target_lang: str, context: Optional[TranslationContext] = None,
                              source_lang: Optional[str] = None) -> Tuple[str, bool]:
        """One text; the flag says whether a translation was delivered (LLM or memory)."""
        texts, ok = self.translate_many_with_status([text], target_lang, context, source_lang)
        return texts[0], ok[0]

    def translate_many_with_status(self, texts: Sequence[str], target_lang: str,
                                   context: Optional[TranslationContext] = None,
                                   source_lang: Optional[str] = None) -> Tuple[List[str], List[bool]]:
        """As translate_many; also returns per text whether it was translated or needed none."""
        context = context or TranslationContext.from_config(self.config)
        # source_lang: language of the texts; default is the survey's source
        # language. Responses in other languages are translated *into* the
        # source language with source_lang set to the detected language.
        src = source_lang or self.source_language
        texts = [t if isinstance(t, str) else ("" if t is None else str(t)) for t in texts]
        if target_lang == src:
            return list(texts), [True] * len(texts)
        result = list(texts)
        ok = [True] * len(texts)
        todo: List[int] = []
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            cached = self._memory_get(text, src, target_lang, context.scope)
            if cached is not None:
                result[i] = cached
            else:
                todo.append(i)
        # unique texts only, in order of appearance
        unique = list(dict.fromkeys(texts[i] for i in todo))
        translated: Dict[str, str] = {}
        for start in range(0, len(unique), MAX_BATCH):
            chunk = unique[start:start + MAX_BATCH]
            translated.update(zip(chunk, self._llm_translate(chunk, src, target_lang, context)))
        for i in todo:
            new = translated.get(texts[i])
            if new:
                result[i] = new
                self._memory_put(texts[i], src, target_lang, new, context.scope)
            else:
                ok[i] = False
        return result, ok

    # -- internals ----------------------------------------------------------------

    def _memory_get(self, text: str, src: str, target_lang: str, scope: str) -> Optional[str]:
        if self.db is None:
            return None
        try:
            return self.db.get_translation(text, src, target_lang, scope=scope)
        except Exception as e:
            logger.warning(f"Translation memory not readable: {e}")
            return None

    def _memory_put(self, text: str, src: str, target_lang: str, translated: str, scope: str) -> None:
        if self.db is None:
            return
        try:
            self.db.save_translation(text, src, target_lang, translated, scope=scope)
        except Exception as e:
            logger.warning(f"Translation memory not writable: {e}")

    def _prompt(self, texts: List[str], src: str, target_lang: str, context: TranslationContext):
        p = self.prompts["translate_batch"]
        in_lang = self.source_language  # language names in the language of the prompt
        # The glossary maps source-language terms; it applies only when
        # translating from the survey's source language.
        glossary_lines = [
            f"- {term} → {values.get(target_lang)}"
            for term, values in context.glossary.items()
            if src == self.source_language and isinstance(values, dict) and values.get(target_lang)
        ]
        question_label = "Question" if in_lang == "en" else "Frage"
        fields = {
            "target_language": language_name(target_lang, in_lang),
            "source_language": (p["unknown_source"] if src == AUTO_SOURCE else language_name(src, in_lang)),
            "survey_title": context.survey_title or "-",
            "llm_context": context.llm_context or "-",
            "kind_description": self.prompts["kinds"].get(context.kind, self.prompts["kinds"]["text"]),
            "question_line": f"{question_label}: {context.question_text}" if context.question_text else "",
            "notes_line": context.notes or "",
            "glossary": "\n".join(glossary_lines) or "-",
            "keep_terms": ", ".join(context.keep_terms) or "-",
            "count": len(texts),
            "texts_json": json.dumps(texts, ensure_ascii=False, indent=0),
        }
        from utils.prompts import render
        return render(p["system"], **fields), render(p["user"], **fields)

    def _llm_translate(self, texts: List[str], src: str, target_lang: str,
                       context: TranslationContext) -> List[Optional[str]]:
        system, user = self._prompt(texts, src, target_lang, context)
        try:
            answer = self.llm.complete(user, system_prompt=system)
            parsed = _parse_json_list(answer)
        except Exception as e:
            logger.warning(f"Translation into {target_lang} failed ({len(texts)} texts): {e}")
            return [None] * len(texts)
        if parsed is None or len(parsed) != len(texts):
            logger.warning(
                f"Translation into {target_lang}: the answer does not match {len(texts)} texts; "
                f"the texts stay untranslated"
            )
            return [None] * len(texts)
        return [p if isinstance(p, str) and p.strip() else None for p in parsed]


def _parse_json_list(answer: str) -> Optional[list]:
    """The JSON array in an LLM answer (tolerates code fences and leading text)."""
    if not answer:
        return None
    start, end = answer.find("["), answer.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(answer[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def get_translation_service(llm_client=None) -> TranslationService:
    """Service for the loaded survey (configuration, database, LLM client of the app)."""
    from core import state  # lazy: the app state is created on import of core
    if llm_client is None:
        from utils.llm import get_llm_client
        llm_client = get_llm_client()
    return TranslationService(llm_client, state.config, state.db)


def context_for_question(config: Optional[dict], qid: str, kind: str) -> TranslationContext:
    """Context for texts that belong to question ``qid`` (scope = question id)."""
    from utils.languages import get_source_language, localized
    qcfg = ((config or {}).get("question_groups") or {}).get(qid, {}) or {}
    src = get_source_language(config)
    question_text = localized(qcfg.get("label") or qcfg.get("text") or {}, src, src) or ""
    return TranslationContext.from_config(config, kind=kind, scope=qid, question_text=str(question_text))
