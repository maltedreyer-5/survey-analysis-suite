# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""Prompt catalogs: both source languages complete and consistent, every key used exists."""
import ast
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import prompts  # noqa: E402


def _leaves(node, prefix=""):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _leaves(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


CATALOGS = {lang: dict(_leaves(prompts.catalog(lang))) for lang in ("de", "en")}


def test_same_keys_in_both_catalogs():
    assert set(CATALOGS["de"]) == set(CATALOGS["en"])


@pytest.mark.parametrize("key", sorted(CATALOGS["de"]))
def test_same_placeholders(key):
    assert prompts.placeholders(CATALOGS["de"][key]) == prompts.placeholders(CATALOGS["en"][key])


def _keys_used_in_code():
    """Literal first arguments of prompts.prompt(...) / prompts.get(...) / _p(...) calls."""
    used = set()
    for path in ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            base = getattr(getattr(func, "value", None), "id", "")
            first = node.args[0]
            if (name in ("prompt", "get") and base == "prompts") or name == "_p":
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    used.add((str(path.relative_to(ROOT)), first.value))
    return used


def test_every_key_used_in_code_exists():
    used = _keys_used_in_code()
    assert used, "no prompt keys found in the code"
    for lang in ("de", "en"):
        for path, key in used:
            prompts.get(key, lang)  # raises KeyError with file context below
    # each catalog key prefix may also be completed at runtime (e.g. summary.data.<type>)


def test_render_is_strict_and_leaves_json_alone():
    assert prompts.render('{"a": 1} {x}', x="y") == '{"a": 1} y'
    with pytest.raises(KeyError):
        prompts.render("{missing}")


def test_prompts_name_answer_language():
    """Prompts that generate text say in which language (the source language)."""
    generating = ["summary.system", "summary.user", "recluster.prompt",
                  "segment_differences.system", "segment_differences.user",
                  "correlation.system", "correlation.user",
                  "themes.theme_keywords", "config_assistant.llm_context",
                  "config_assistant.segmentation", "item_extraction.validation"]
    for key in generating:
        assert "Deutsch" in CATALOGS["de"][key], key
        assert "English" in CATALOGS["en"][key], key


def test_context_text_forms():
    assert prompts.context_text({"settings": {"llm_context": "Campus services"}}) == "Campus services"
    structured = {"settings": {"llm_context": {"domain": "Mensa", "region": "Hessen", "audience": "Leitung"}}}
    assert prompts.context_text(structured) == "Mensa; Hessen; Leitung"
    assert prompts.context_text({"settings": {}}) == "-"
