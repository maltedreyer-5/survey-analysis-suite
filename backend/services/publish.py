# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: publication documents.

Wrapper around utils.document_builder.DocumentBuilder for build_all,
build_update and clear_translations.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional
from services.ui_values import ALL

logger = logging.getLogger(__name__)


def build_all(
    state,
    languages: List[str],
    output_dir: Path,
    progress: Optional[Callable[[float, str], None]] = None,
) -> str:
    """
    Creates all publication documents in the requested languages.
    
    Args:
        state: AppState (not used directly, but the convention).
        languages: list of languages.
        output_dir: output directory of the documents.
        progress: optional callback (fraction, desc).
    
    Returns:
        status text with the list of the created files.
    """
    try:
        from utils.document_builder import DocumentBuilder
        builder = DocumentBuilder()
        
        def prog_cb(frac, msg=""):
            if progress:
                progress(frac, msg)
        
        results = builder.build_all(
            languages=languages,
            output_dir=str(output_dir),
            progress_callback=prog_cb,
        )
        
        lines = ["✅ Publication documents created:", ""]
        for _doc_id, paths in results.items():
            for p in paths:
                lines.append(f"  📄 {p.name}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"build_all failed: {e}", exc_info=True)
        return f"❌ Error: {e}"


def build_update(
    state,
    languages: List[str],
    output_dir: Path,
    progress: Optional[Callable[[float, str], None]] = None,
) -> str:
    """
    Updates only the changed publication documents.
    
    Also returns a list of outdated translations.
    """
    try:
        from utils.document_builder import DocumentBuilder
        builder = DocumentBuilder()
        
        def prog_cb(frac, msg=""):
            if progress:
                progress(frac, msg)
        
        results = builder.build_update(
            languages=languages,
            output_dir=str(output_dir),
            progress_callback=prog_cb,
        )
        
        lines = ["✅ Update finished:", ""]
        total = sum(len(paths) for paths in results.values())
        lines.append(f"  {total} documents up to date")
        
        stale = builder.get_stale_translations()
        if stale:
            lines.append("")
            lines.append("⚠️ Outdated translations:")
            for doc_id, langs in stale.items():
                for lang, count in langs.items():
                    lines.append(f"  {doc_id} ({lang}): {count} paragraphs")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"build_update failed: {e}", exc_info=True)
        return f"❌ Error: {e}"


def clear_translations(state, doc_filter: str, lang_filter: str) -> str:
    """
    Deletes the translations of a document or a language.
    
    Args:
        doc_filter: document id, or ALL for all.
        lang_filter: language code, or ALL.
    """
    try:
        from utils.document_builder import DocumentBuilder
        builder = DocumentBuilder()
        doc_id = None if doc_filter in (ALL, None, "") else doc_filter
        lang = None if lang_filter in (ALL, None, "") else lang_filter
        deleted = builder.clear_translations(doc_id, lang)
        return f"🗑️ {deleted} translations deleted"
    except Exception as e:
        logger.error(f"clear_translations failed: {e}", exc_info=True)
        return f"❌ Error: {e}"
