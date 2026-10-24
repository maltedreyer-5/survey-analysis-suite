# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: maintenance of the translation table.

The functions around the translations table of the database: loading,
saving (with user corrections), deleting, and starting an LLM translation
of the answers.

Calling convention:
- the first argument is always `state` (with the attributes `db`, `df`,
  `config`, `import_id`).
- the progress callback is optional and takes (fraction: float, desc: str);
  if None, no progress is reported.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

import pandas as pd
from services.ui_tables import TRANSLATIONS

logger = logging.getLogger(__name__)


# multilingual display labels, shown in the interface as the string
# for source_lang
LANG_LABELS = {'de': 'Deutsch', 'fr': 'Französisch', 'en': 'Englisch'}
LANG_CODES = {v: k for k, v in LANG_LABELS.items()}

EMPTY_TRANSLATIONS_DF = TRANSLATIONS.empty()


def load_translations_from_db(state, target_lang: str = "de") -> pd.DataFrame:
    """
    Loads all translations into a target language from the database.
    
    Args:
        state: AppState with a `db` attribute.
        target_lang: target language.
    
    Returns:
        DataFrame with the columns of ui_tables.TRANSLATIONS.
        On errors: an empty DataFrame.
    """
    if state.db is None:
        return EMPTY_TRANSLATIONS_DF.copy()
    
    try:
        with state.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, source_text, source_lang, translated_text
                FROM translations
                WHERE target_lang = ?
                ORDER BY source_lang, source_text
                LIMIT 500
                """,
                (target_lang,),
            )
            rows = cursor.fetchall()
        
        if not rows:
            return EMPTY_TRANSLATIONS_DF.copy()
        
        return TRANSLATIONS.to_ui({
            "id": row['id'],
            "original": row['source_text'],
            "language": LANG_LABELS.get(row['source_lang'], row['source_lang']),
            "translation": row['translated_text'],
        } for row in rows)
    except Exception as e:
        logger.error(f"Error loading the translations: {e}")
        return EMPTY_TRANSLATIONS_DF.copy()


def save_translations_to_db(state, df: pd.DataFrame,
                             target_lang: str = "de") -> str:
    """
    Saves changed translations from a DataFrame back into the database.
    
    Args:
        state: AppState with a `db` attribute.
        df: the table as shown (columns of ui_tables.TRANSLATIONS, read by position):
            id (refers to translations.id) and the new translation.
        target_lang: not used directly (update by id), but part of the API.
    
    Returns:
        status message.
    """
    if state.db is None:
        return "❌ No database connection"
    
    if df is None or len(df) == 0:
        return "⚠️ No data to save"
    
    try:
        updated_count = 0
        with state.db._connection() as conn:
            cursor = conn.cursor()
            for _, row in TRANSLATIONS.from_ui(df).iterrows():
                trans_id = row.get('id')
                new_translation = row.get('translation')
                if trans_id and new_translation:
                    cursor.execute(
                        "UPDATE translations SET translated_text = ?, is_manual = 1 WHERE id = ?",
                        (new_translation, int(trans_id)),
                    )
                    if cursor.rowcount > 0:
                        updated_count += 1
        return f"✅ {updated_count} translations saved"
    except Exception as e:
        logger.error(f"Error saving: {e}")
        return f"❌ Error: {e}"


def delete_all_translations(state, target_lang: str = "de",
                            confirmed: bool = False
                            ) -> Tuple[str, pd.DataFrame]:
    """
    Deletes all translations into a target language.
    
    The translation cache is shared by all surveys and also holds manual
    corrections, so deletion requires an explicit confirmation.
    
    Returns:
        (status message, empty DataFrame to reset the interface).
    """
    if state.db is None:
        return "❌ No database connection", EMPTY_TRANSLATIONS_DF.copy()
    if not confirmed:
        return (
            "⚠️ Not deleted: confirm first. This deletes all "
            f"translations into '{target_lang}' for all surveys, "
            "including manual corrections.",
            EMPTY_TRANSLATIONS_DF.copy(),
        )
    
    try:
        with state.db._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM translations WHERE target_lang = ?",
                (target_lang,),
            )
            count = cursor.fetchone()[0]
            cursor.execute(
                "DELETE FROM translations WHERE target_lang = ?",
                (target_lang,),
            )
        return f"✅ {count} translations deleted", EMPTY_TRANSLATIONS_DF.copy()
    except Exception as e:
        logger.error(f"Error deleting: {e}")
        return f"❌ Error: {e}", EMPTY_TRANSLATIONS_DF.copy()


def translate_responses(
    state,
    target_lang: str,
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, pd.DataFrame]:
    """
    Starts the LLM translation of all free-text answers.
    
    Args:
        state: AppState; `state.df` is overwritten with the translated values.
        target_lang: target language.
        progress: optional callback (fraction in [0,1], desc: str).
    
    Returns:
        (status message, DataFrame of the translation table after the run).
    
    Side effects:
        - state.df is overwritten with the translated DataFrame
        - entries are created/updated in the database table `translations`
    """
    from utils.response_translator import translate_responses as _translate
    from utils.llm import get_llm_client
    
    if state.df is None:
        return "❌ No data imported", EMPTY_TRANSLATIONS_DF.copy()
    
    if state.config is None:
        # Note: load_config() is optional; the caller can do it. We return an
        # error instead of reloading implicitly.
        return "❌ No configuration loaded", EMPTY_TRANSLATIONS_DF.copy()
    
    if progress:
        progress(0, "Checking the LLM connection...")
    
    llm = get_llm_client()
    llm_ok, llm_msg = llm.test_connection()
    if not llm_ok:
        return (
            f"❌ LLM not available: {llm_msg}\n\n"
            f"→ Check the configuration in .env:\n"
            f"  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL"
        ), EMPTY_TRANSLATIONS_DF.copy()
    
    def _progress_cb(current, total, message):
        if progress:
            progress((current / total) if total > 0 else 0, message)
    
    try:
        state.df, stats = _translate(
            df=state.df,
            config=state.config,
            target_lang=target_lang,
            db=state.db,
            import_id=state.import_id,
            progress_callback=_progress_cb,
        )
        
        # format the status
        by_lang = stats.get('by_source_lang', {})
        errors = stats.get('errors', 0)
        translated = stats.get('translated', 0)
        
        if errors > 0 and translated == 0:
            status_icon = "❌"
            status_text = "Translation failed: is the LLM available?"
        elif errors > 0:
            status_icon = "⚠️"
            status_text = f"Translation partly finished ({errors} errors)"
        else:
            status_icon = "✅"
            status_text = "Translation finished!"
        
        cols_done = stats.get('columns_processed', 0)
        cols_found = stats.get('columns_found', cols_done)
        
        status_lines = [
            f"{status_icon} {status_text}",
            "",
            f"📊 {cols_done}/{cols_found} columns processed",
            f"📝 {stats.get('total_values', 0)} values analyzed",
            "",
            "Detected languages:",
            f"  - German: {by_lang.get('de', 0)}",
            f"  - French: {by_lang.get('fr', 0)}",
            f"  - English: {by_lang.get('en', 0)}",
            "",
            f"🔄 {translated} newly translated",
            f"💾 {stats.get('from_cache', 0)} from the cache",
            f"⏭️ {stats.get('skipped', 0)} skipped (already in the target language)",
        ]
        
        if errors > 0:
            status_lines.append("")
            status_lines.append(f"❌ {errors} translation errors (LLM not reachable?)")
            status_lines.append("   → Check the LLM configuration in .env")
        
        translations_table = load_translations_from_db(state, target_lang)
        return "\n".join(status_lines), translations_table
    
    except Exception as e:
        logger.error(f"Translation failed: {e}", exc_info=True)
        return f"❌ Error: {e}", EMPTY_TRANSLATIONS_DF.copy()
