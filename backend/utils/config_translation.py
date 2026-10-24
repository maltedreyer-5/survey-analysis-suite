# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Translation of the labels of a survey configuration.

A survey usually brings its texts in the source language only. This module fills the
missing output languages of question texts, answer options, matrix rows and scales,
named scales and segmentation dimensions:

- every list is translated as a whole (all answer options of a question, all points
  of a scale) together with the question text, the meaning of the scale (``context``
  of a named scale) and a note on the order of the points, so short single terms are
  never translated without their context;
- labels written in the configuration are never overwritten, only missing languages
  are filled;
- the translations are kept in the translation memory (scope: question id,
  ``scale:<name>`` or ``dimension:<id>``) and corrected there (translation table of
  the import tab).

``fill_config_labels(config, service, use_llm=False)`` fills from the memory only
(cheap, used when the configuration is loaded); ``use_llm=True`` translates what is
still missing (pipeline step).
"""

import copy
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

LIST_KEYS = ("options", "item_labels", "row_labels")      # {lang: [...]} lists of a question
TEXT_KEYS = ("label", "full_question", "help")             # {lang: text} of a question


@dataclass
class LabelUnit:
    """Texts that are translated together, and where their translations go."""
    scope: str
    kind: str
    texts: List[str]
    question_text: str
    notes: str
    apply: Callable[[str, List[str]], None]      # (lang, translated texts) -> fills the configuration
    missing: Callable[[str], bool]                # lang -> whether the configuration lacks this language


def _src_list(value, src: str) -> List[str]:
    if isinstance(value, dict):
        v = value.get(src)
        return list(v) if isinstance(v, list) else []
    return list(value) if isinstance(value, list) else []


def _list_missing(holder: dict, key: str, n: int) -> Callable[[str], bool]:
    def missing(lang):
        value = holder.get(key)
        return not (isinstance(value, dict) and isinstance(value.get(lang), list) and len(value[lang]) == n)
    return missing


def _list_apply(holder: dict, key: str, src: str) -> Callable[[str, List[str]], None]:
    def apply(lang, translated):
        value = holder.get(key)
        if not isinstance(value, dict):
            holder[key] = value = {src: list(value or [])}
        value[lang] = list(translated)
    return apply


def _text_units(holder: dict, keys, src: str, scope: str, kind: str, question_text: str) -> List[LabelUnit]:
    present = [k for k in keys if isinstance(holder.get(k), dict) and holder[k].get(src)
               or isinstance(holder.get(k), str) and holder[k].strip()]
    if not present:
        return []
    texts = [holder[k][src] if isinstance(holder[k], dict) else holder[k] for k in present]

    def missing(lang):
        return any(not (isinstance(holder.get(k), dict) and holder[k].get(lang)) for k in present)

    def apply(lang, translated):
        for k, t in zip(present, translated):
            if not isinstance(holder.get(k), dict):
                holder[k] = {src: holder.get(k)}
            if not holder[k].get(lang):
                holder[k][lang] = t
    return [LabelUnit(scope, kind, texts, question_text, "", apply, missing)]


def label_units(config: dict) -> List[LabelUnit]:
    """All translation units of a configuration (expanded: named scales already resolved)."""
    from utils.languages import get_source_language, localized
    from utils.translation_service import prompt_catalog
    src = get_source_language(config)
    notes = prompt_catalog(src).get("translate_batch", {})
    scale_note, list_note = notes.get("scale_note", ""), notes.get("list_note", "")
    units: List[LabelUnit] = []
    scales = ((config.get("settings") or {}).get("scales") or {})

    # named scales: translated once, then filled into every question that uses them
    users: Dict[str, List[dict]] = {}
    for qid, q in (config.get("question_groups") or {}).items():
        if isinstance(q, dict) and q.get("scale_name") in scales:
            users.setdefault(q["scale_name"], []).append(q)
    for name, qs in users.items():
        scale = scales[name]
        holder_key = "options" if qs[0].get("handler") == "single_choice" else "scale"
        texts = _src_list(qs[0].get(holder_key), src)
        if not texts:
            continue
        note = scale_note.replace("{count}", str(len(texts))).replace("{meaning}", str(scale.get("context") or "-"))

        def apply(lang, translated, qs=qs):
            for q in qs:
                key = "options" if q.get("handler") == "single_choice" else "scale"
                _list_apply(q, key, src)(lang, translated)

        def missing(lang, qs=qs):
            return any(_list_missing(q, "options" if q.get("handler") == "single_choice" else "scale",
                                     len(texts))(lang) for q in qs)
        units.append(LabelUnit(f"scale:{name}", "scale", texts, "", note, apply, missing))

    for qid, q in (config.get("question_groups") or {}).items():
        if not isinstance(q, dict):
            continue
        question_text = str(localized(q.get("full_question") or q.get("label") or {}, src, src) or "")
        units += _text_units(q, TEXT_KEYS, src, qid, "question", "")
        for key in LIST_KEYS + ("scale",):
            if q.get("scale_name") in scales and key in ("options", "scale"):
                continue        # filled from the named scale
            texts = _src_list(q.get(key), src)
            if not texts:
                continue
            kind = "scale" if key == "scale" else "option"
            note = (scale_note.replace("{count}", str(len(texts))).replace("{meaning}", "-") if kind == "scale"
                    else list_note.replace("{count}", str(len(texts))))
            units.append(LabelUnit(qid, kind, texts, question_text, note,
                                   _list_apply(q, key, src), _list_missing(q, key, len(texts))))

    dims = ((config.get("settings") or {}).get("segmentation") or {}).get("dimensions") or []
    for d in dims if isinstance(dims, list) else []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        scope = f"dimension:{d['id']}"
        units += _text_units(d, ("label",), src, scope, "question", "")
        bucketing = d.get("bucketing") or {}
        buckets = [b for b in (bucketing.get("buckets") or []) if isinstance(b, dict)]
        texts = [localized(b.get("label") or {}, src, src) for b in buckets]
        if buckets and all(texts):
            def apply_b(lang, translated, buckets=buckets):
                for b, t in zip(buckets, translated):
                    if not isinstance(b.get("label"), dict):
                        b["label"] = {src: b.get("label")}
                    b["label"].setdefault(lang, t)

            def missing_b(lang, buckets=buckets):
                return any(not (isinstance(b.get("label"), dict) and b["label"].get(lang)) for b in buckets)
            units.append(LabelUnit(scope, "segment_value", texts, "", "", apply_b, missing_b))
        # value_mapping targets without labels become value_labels
        targets = list(dict.fromkeys(str(v) for v in (bucketing.get("mapping") or {}).values() if v))
        explicit = d.get("value_labels") or {}
        targets = [t for t in targets if t not in explicit]
        if targets:
            def apply_v(lang, translated, d=d, targets=targets):
                labels = d.setdefault("value_labels", {})
                for t, tr in zip(targets, translated):
                    entry = labels.setdefault(t, {src: t})
                    if isinstance(entry, dict):
                        entry.setdefault(lang, tr)

            def missing_v(lang, d=d, targets=targets):
                labels = d.get("value_labels") or {}
                return any(not (isinstance(labels.get(t), dict) and labels[t].get(lang)) for t in targets)
            units.append(LabelUnit(scope, "segment_value", targets, "", "", apply_v, missing_v))
    return units


def fill_config_labels(config: Optional[dict], service=None, use_llm: bool = False,
                       languages: Optional[List[str]] = None, progress=None) -> tuple:
    """
    A copy of the configuration with the missing output languages filled.

    Args:
        config: configuration (named scales already expanded).
        service: TranslationService (memory and LLM); None = the one of the app.
        use_llm: translate what the memory does not have (otherwise memory only).
        languages: target languages; default: the output languages without the source.
        progress: optional callback(fraction, text).

    Returns:
        (config, stats) with stats {"units", "filled", "translated", "missing"}.
    """
    stats = {"units": 0, "filled": 0, "translated": 0, "missing": 0}
    if not config:
        return config, stats
    from utils.languages import get_output_languages, get_source_language
    from utils.translation_service import TranslationContext, get_translation_service
    src = get_source_language(config)
    targets = [l for l in (languages or get_output_languages(config)) if l != src]
    if not targets:
        return config, stats
    config = copy.deepcopy(config)
    if service is None:
        service = get_translation_service() if use_llm else _memory_only_service(config)
    units = label_units(config)
    stats["units"] = len(units)
    todo = [(u, lang) for u in units for lang in targets if u.missing(lang)]
    for i, (unit, lang) in enumerate(todo):
        if progress:
            progress(i / max(1, len(todo)), f"{unit.scope} → {lang}")
        context = TranslationContext.from_config(config, kind=unit.kind, scope=unit.scope,
                                                 question_text=unit.question_text, notes=unit.notes)
        cached = [service._memory_get(t, src, lang, unit.scope) for t in unit.texts]
        if all(c is not None for c in cached):
            unit.apply(lang, cached)
            stats["filled"] += 1
            continue
        if not use_llm:
            stats["missing"] += 1
            continue
        translated, ok = service.translate_many_with_status(unit.texts, lang, context)
        if all(ok):
            unit.apply(lang, translated)
            stats["translated"] += 1
        else:
            stats["missing"] += 1
    return config, stats


class _MemoryOnly:
    """Reads the translation memory; never calls an LLM."""
    def __init__(self, db):
        self.db = db

    def _memory_get(self, text, src, target_lang, scope):
        if self.db is None:
            return None
        try:
            return self.db.get_translation(text, src, target_lang, scope=scope)
        except Exception as e:
            logger.warning(f"Translation memory not readable: {e}")
            return None


def _memory_only_service(config):
    try:
        from core import state
        return _MemoryOnly(state.db)
    except Exception:
        return _MemoryOnly(None)
