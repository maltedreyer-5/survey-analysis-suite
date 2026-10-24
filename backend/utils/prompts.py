# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Prompt catalogs per source language.

Every prompt the tool sends to the LLM lives in ``prompts/<lang>.yaml``, one
catalog per supported source language (``de``, ``en``). The source language
of the survey decides which catalog is used; prompts state the answer
language explicitly, so generated texts come back in the source language
and reach other output languages only through the translation service.

Placeholders are ``{name}`` with a plain identifier. Only those are
replaced, so JSON examples in a prompt need no escaping. A placeholder
without a value is a programming error and raises, rather than reaching the
LLM as literal text.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from utils.languages import (
    DEFAULT_SOURCE_LANGUAGE, SUPPORTED_SOURCE_LANGUAGES, get_source_language,
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@lru_cache(maxsize=None)
def catalog(source_language: str) -> dict:
    """The whole catalog for a source language."""
    if source_language not in SUPPORTED_SOURCE_LANGUAGES:
        raise ValueError(f"no prompt catalog for source language '{source_language}'")
    with open(PROMPTS_DIR / f"{source_language}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get(key: str, source_language: str) -> Any:
    """Entry ``key`` (dotted path, e.g. ``"clustering.system"``) of a catalog."""
    node: Any = catalog(source_language)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"prompt '{key}' missing in prompts/{source_language}.yaml")
        node = node[part]
    return node


def placeholders(template: str) -> set:
    return set(PLACEHOLDER.findall(template))


def render(template: str, **fields: Any) -> str:
    """Fill the ``{name}`` placeholders of a template."""
    missing = placeholders(template) - set(fields)
    if missing:
        raise KeyError(f"prompt placeholders without value: {sorted(missing)}")
    return PLACEHOLDER.sub(lambda m: str(fields[m.group(1)]), template)


def prompt(key: str, source_language: str, **fields: Any) -> str:
    """Rendered prompt ``key`` for a source language."""
    template = get(key, source_language)
    if not isinstance(template, str):
        raise TypeError(f"prompt '{key}' is not a text")
    return render(template, **fields)


def current_config() -> Optional[dict]:
    """Configuration of the loaded survey, or None outside the app."""
    # Only if the app has loaded core already: importing it here would
    # create directories and the database as a side effect.
    import sys
    core = sys.modules.get("core")
    state = getattr(core, "state", None)
    return getattr(state, "config", None)


def source_language(config: Optional[dict] = None) -> str:
    """Source language of ``config``, or of the loaded survey if none is given."""
    if config is None:
        config = current_config()
    return get_source_language(config) if config is not None else DEFAULT_SOURCE_LANGUAGE


def context_text(config: Optional[dict] = None, source_lang: Optional[str] = None) -> str:
    """
    ``settings.llm_context`` as text for a prompt; ``-`` if not set.

    The setting is a text; the structured form with ``domain``, ``region`` and
    ``audience`` is accepted as well and written out as one line.
    """
    if config is None:
        config = current_config()
    settings = (config or {}).get("settings") or {}
    # one place only; older places are reported by services/config_validation.py
    value = settings.get("llm_context")
    if isinstance(value, dict):
        lang = source_lang or source_language(config)
        parts = [str(value[k]) for k in ("domain", "region", "audience") if value.get(k)]
        if not parts:
            from utils.languages import localized
            parts = [str(localized(value, lang, lang) or "")]
        value = "; ".join(p for p in parts if p)
    value = str(value or "").strip()
    return value or "-"
