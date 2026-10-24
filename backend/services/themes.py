# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: theme management.

Takes the ThemeAnalyzer instance as an explicit argument.

Conventions:
- service functions return plain data (lists, dicts, strings).
- Gradio update objects (gr.update) are created in the interface layer.
- theme_analyzer is always the first argument after state.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _label(label) -> str:
    """Theme label for display in the survey's source language."""
    from utils.languages import localized
    from utils.prompts import source_language
    src = source_language()
    return str(localized(label, src, src, default="") or "") if isinstance(label, dict) else str(label or "")


# =============================================================================
# DB-Sync
# =============================================================================

def load_themes_from_db(state, theme_analyzer) -> int:
    """
    Loads stored themes from the database into the ThemeAnalyzer.
    
    Returns:
        number of themes loaded (0 if the database is not available or empty).
    """
    if not (state.db and state.project_id):
        return 0
    
    try:
        db_themes = state.db.get_themes(state.project_id)
        for theme_id, theme_data in db_themes.items():
            if theme_id not in theme_analyzer.themes:
                theme_analyzer.add_theme(theme_id, _label(theme_data['label']) or theme_id,
                                         labels=theme_data['label'])
            if theme_id in theme_analyzer.themes:
                theme_analyzer.themes[theme_id].question_keywords = (
                    theme_data.get('question_keywords', [])
                )
                theme_analyzer.themes[theme_id].answer_keywords = (
                    theme_data.get('answer_keywords', [])
                )
        logger.info(f"Loaded {len(db_themes)} themes from database")
        return len(db_themes)
    except Exception as e:
        logger.warning(f"Could not load the themes from the database: {e}")
        return 0


def sync_themes_to_state(state, theme_analyzer) -> None:
    """
    Synchronises the themes held by the ThemeAnalyzer with state.themes.
    
    Needed because the dashboard export reads state.themes.
    """
    state.themes = {}
    for theme_id, theme in theme_analyzer.themes.items():
        state.themes[theme_id] = {
            "id": theme_id,
            "label": theme.label,
            "question_keywords": theme.question_keywords,
            "answer_keywords": theme.answer_keywords,
            "questions": theme.questions,
            "stats": theme.stats,
        }


# =============================================================================
# Managing themes
# =============================================================================

def theme_labels(state, theme_id: str, label: str) -> dict:
    """
    The label of a theme in every output language: the given label in the source language,
    the others translated with the translation service (the source label where no
    translation comes back, e.g. without a language model).
    """
    from utils.languages import get_output_languages, get_source_language
    config = state.config or {}
    src = get_source_language(config)
    labels = {src: label}
    targets = [lang for lang in get_output_languages(config) if lang != src]
    if not targets:
        return labels
    try:
        from utils.translation_service import TranslationContext, get_translation_service
        service = get_translation_service()
        context = TranslationContext.from_config(config, kind="question", scope=f"theme:{theme_id}")
        for lang in targets:
            translated, ok = service.translate_many_with_status([label], lang, context)
            if ok and ok[0] and translated[0]:
                labels[lang] = translated[0]
    except Exception as e:
        logger.warning(f"Theme label not translated: {e}")
    return labels


def add_theme(
    state,
    theme_analyzer,
    theme_id: str,
    label: str,
) -> Tuple[bool, str, List[List[Any]], List[str]]:
    """
    Adds a theme with its label in the source language (translated into the other output
    languages) and stores it in the database.
    
    Returns:
        (success, status_text, new_theme_data_rows, new_choices),
        theme_data_rows for the table in the interface, choices for the dropdowns.
    """
    if not theme_id or not label:
        return False, "⚠️ Give an ID and a label", [], []
    
    if theme_id in theme_analyzer.themes:
        return False, f"⚠️ Theme '{theme_id}' already exists", [], []
    
    labels = theme_labels(state, theme_id, label)
    theme_analyzer.add_theme(theme_id, label, labels=labels)
    
    # save in the database
    if state.db and state.project_id:
        try:
            theme = theme_analyzer.themes[theme_id]
            state.db.save_theme(
                project_id=state.project_id,
                theme_id=theme_id,
                label_source=label,
                labels=labels,
                question_keywords=theme.question_keywords,
                answer_keywords=theme.answer_keywords,
            )
        except Exception as e:
            logger.warning(f"Could not store the theme in the database: {e}")
    
    sync_themes_to_state(state, theme_analyzer)
    
    new_data = [
        [t.id, _label(t.label), len(t.question_keywords)]
        for t in theme_analyzer.themes.values()
    ]
    new_choices = list(theme_analyzer.themes.keys())
    
    return True, f"✅ Theme '{theme_id}' added", new_data, new_choices


def get_theme_keywords(theme_analyzer, theme_id: str) -> str:
    """The keywords of a theme as a comma-separated string."""
    if not theme_id or theme_id not in theme_analyzer.themes:
        return ""
    theme = theme_analyzer.themes[theme_id]
    return ", ".join(theme.question_keywords)


def save_theme_keywords(
    state, theme_analyzer, theme_id: str, keywords_str: str,
) -> Tuple[List[List[Any]], str]:
    """
    Saves a keyword list edited by the user for a theme.
    
    Returns:
        (new theme table data, status text).
    """
    if not theme_id or theme_id not in theme_analyzer.themes:
        return [], "⚠️ No theme selected"
    
    keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
    theme_analyzer.themes[theme_id].question_keywords = keywords
    
    if state.db and state.project_id:
        try:
            theme = theme_analyzer.themes[theme_id]
            state.db.save_theme(
                project_id=state.project_id,
                theme_id=theme_id,
                label_source=_label(theme.label),     # source-language label
                labels=theme.label,
                question_keywords=keywords,
                answer_keywords=theme.answer_keywords,
            )
        except Exception as e:
            logger.warning(f"Could not store the keywords in the database: {e}")
    
    sync_themes_to_state(state, theme_analyzer)
    
    new_data = [
        [t.id, _label(t.label), len(t.question_keywords)]
        for t in theme_analyzer.themes.values()
    ]
    return new_data, f"✅ {len(keywords)} keywords saved for '{theme_id}'"


# =============================================================================
# LLM keyword extraction (per theme)
# =============================================================================

def extract_keywords_with_llm(
    state, theme_analyzer, theme_id: str,
) -> Tuple[Optional[List[List[Any]]], Optional[str], str]:
    """
    Lets the LLM create keywords for a theme and checks them against the data.
    
    Returns:
        (theme_data_rows | None, keywords_str | None, status_text);
        the first value is None if there is nothing to update (error case).
    """
    if not theme_id or theme_id not in theme_analyzer.themes:
        return None, None, "⚠️ No theme selected"
    if not state.config:
        return None, None, "⚠️ No configuration loaded"
    
    result = theme_analyzer.extract_keywords_for_theme(
        theme_id, use_llm=True, check_data=True,
    )
    
    keywords = result.get("keywords", [])
    matches = result.get("matches", {})
    assigned_questions = result.get("assigned_questions", [])
    
    if not keywords:
        return None, None, "ℹ️ No keywords extracted: is the LLM available?"
    
    matched_kw = [kw for kw in keywords if kw in matches]
    unmatched_kw = [kw for kw in keywords if kw not in matches]
    keywords_str = ", ".join(matched_kw + unmatched_kw)
    
    new_data = [
        [t.id, _label(t.label), len(t.question_keywords)]
        for t in theme_analyzer.themes.values()
    ]
    sync_themes_to_state(state, theme_analyzer)
    
    status_parts = [f"✅ {len(keywords)} keywords created"]
    if matches:
        status_parts.append(f"{len(matches)} with matches in the data")
    if assigned_questions:
        status_parts.append(f"{len(assigned_questions)} questions assigned")
    
    if matches:
        top_matches = sorted(
            matches.items(),
            key=lambda x: x[1]["question_count"] + x[1]["answer_count"],
            reverse=True,
        )[:5]
        status_parts.append("\nTop keywords:")
        for kw, data in top_matches:
            status_parts.append(
                f"  • {kw}: {data['question_count']} questions, "
                f"{data['answer_count']} answers"
            )
    
    return new_data, keywords_str, "\n".join(status_parts)


# =============================================================================
# Batch theme analysis
# =============================================================================

def analyze_all_themes(
    state, theme_analyzer,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[List[List[Any]], str]:
    """
    Analyses all themes with the LLM and assigns questions.
    
    Returns:
        (theme_data_rows, status_text).
    """
    if not state.config:
        return [], "⚠️ No configuration loaded"
    if not theme_analyzer.themes:
        return [], "⚠️ No themes defined"
    
    total_themes = len(theme_analyzer.themes)
    results = []
    total_questions_assigned = set()
    
    for i, theme_id in enumerate(theme_analyzer.themes.keys()):
        if progress:
            progress((i + 1) / total_themes, f"Analyzing: {theme_id}")
        
        result = theme_analyzer.extract_keywords_for_theme(
            theme_id, use_llm=True, check_data=True,
        )
        keywords = result.get("keywords", [])
        matches = result.get("matches", {})
        assigned = result.get("assigned_questions", [])
        total_questions_assigned.update(assigned)
        
        results.append({
            "theme": theme_id,
            "keywords": len(keywords),
            "matches": len(matches),
            "questions": len(assigned),
        })
    
    state.themes = {
        tid: theme.to_dict()
        for tid, theme in theme_analyzer.themes.items()
    }
    
    new_data = [
        [t.id, _label(t.label), len(t.question_keywords)]
        for t in theme_analyzer.themes.values()
    ]
    
    status_lines = [
        f"✅ {total_themes} themes analyzed",
        f"📊 {len(total_questions_assigned)} questions assigned in total",
        "",
        "Results per theme:",
    ]
    for r in results:
        status_lines.append(
            f"  • {r['theme']}: {r['keywords']} keywords, "
            f"{r['matches']} matches, {r['questions']} questions"
        )
    
    return new_data, "\n".join(status_lines)


def deep_analyze_all_themes(
    state, theme_analyzer,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[List[List[Any]], str]:
    """
    Deep analysis of all themes, the LLM checks every cluster.
    
    Returns:
        (theme_data_rows, status_text).
    """
    if not state.config:
        return [], "⚠️ No configuration loaded"
    if not theme_analyzer.themes:
        return [], "⚠️ No themes defined"
    
    total_themes = len(theme_analyzer.themes)
    all_results = []
    total_llm_calls = 0
    total_questions_assigned = set()
    
    for i, theme_id in enumerate(theme_analyzer.themes.keys()):
        # closure: `i` and `theme_id` are referenced in the sub-callback
        captured_i = i
        captured_id = theme_id
        
        def theme_progress(p, msg, _i=captured_i, _id=captured_id):
            if progress:
                overall = (_i + p) / total_themes
                progress(overall, f"{_id}: {msg}")
        
        result = theme_analyzer.deep_analyze_theme(
            theme_id, progress_callback=theme_progress,
        )
        if "error" not in result:
            all_results.append(result)
            total_llm_calls += result.get("llm_calls", 0)
            total_questions_assigned.update(result.get("matched_questions", []))
    
    state.themes = {
        tid: theme.to_dict()
        for tid, theme in theme_analyzer.themes.items()
    }
    
    new_data = [
        [t.id, _label(t.label), len(t.questions)]
        for t in theme_analyzer.themes.values()
    ]
    
    status_lines = [
        "✅ Deep analysis finished!",
        f"🤖 {total_llm_calls} LLM calls",
        f"📊 {len(total_questions_assigned)} questions assigned in total",
        "",
        "Results per theme:",
    ]
    for r in all_results:
        cluster_count = len(r.get("cluster_matches", []))
        status_lines.append(
            f"  • {r['theme_label']}: "
            f"{len(r['matched_questions'])} questions, "
            f"{cluster_count} cluster matches"
        )
        for match in r.get("cluster_matches", [])[:3]:
            status_lines.append(
                f"      - {match['cluster']} ({match['type']})"
            )
    
    return new_data, "\n".join(status_lines)


def export_themes(theme_analyzer) -> str:
    """Exports the current theme definitions as a file."""
    filepath = theme_analyzer.export_themes()
    return f"✅ Exported: {filepath}"
