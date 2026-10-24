# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Translation of survey answers: translates free-text answers into the target
language (the source language of the survey) for a consistent analysis.
"""

import logging
import re
from typing import Optional, Tuple, List, Dict
from pathlib import Path

import pandas as pd

from .llm import get_llm_client, LLMClient

logger = logging.getLogger(__name__)


class ResponseTranslator:
    """Translates free-text answers into the target language."""
    
    # language codes
    LANG_MAP = {
        'de': 'Deutsch',
        'fr': 'Französisch', 
        'en': 'Englisch'
    }
    
    def __init__(self, db=None, import_id: int = None, llm: LLMClient = None):
        """
        Initialises the ResponseTranslator.
        
        Args:
            db: database connection for caching
            import_id: import id for the assignment
            llm: LLM client (or None to create one automatically)
        """
        self.db = db
        self.import_id = import_id
        self.llm = llm or get_llm_client()
        self._cache = {}  # In-Memory Cache
    
    def detect_language(self, text: str) -> str:
        """
        Detects the language of a text with a hybrid method.
        
        1. keyword-based detection (fast, for clear cases)
        2. langdetect as a fallback (more precise in difficult cases)
        
        Args:
            text: text to analyse
        
        Returns:
            language code ('de', 'fr', 'en' or 'unknown')
        """
        if not text or not text.strip():
            return 'unknown'
        
        text_lower = text.lower()
        words = set(text_lower.replace('-', ' ').replace(',', ' ').replace('.', ' ').split())
        
        # French indicators (words and phrases)
        french_words = {
            # articles and pronouns
            "le", "la", "les", "un", "une", "des", "du", "au", "aux",
            "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
            "ce", "cette", "ces", "mon", "ma", "mes", "ton", "ta", "tes",
            # verbs
            "est", "sont", "être", "avoir", "fait", "faire", "peut", "peuvent",
            "doit", "doivent", "veut", "vont", "été", "était", "étaient",
            # prepositions and conjunctions
            "et", "ou", "mais", "donc", "car", "ni", "que", "qui",
            "dans", "sur", "sous", "avec", "sans", "pour", "par", "entre",
            "depuis", "vers", "chez", "selon", "durant", "pendant",
            # adverbs
            "très", "plus", "moins", "bien", "aussi", "encore", "toujours",
            "jamais", "souvent", "parfois", "déjà", "encore",
            # typical nouns
            "établissement", "université", "recherche", "données", "système",
            "développement", "gestion", "mise", "place", "niveau",
            "traitement", "documents", "bureautique", "informatique",
            "numérique", "logiciel", "réseau", "sécurité"
        }
        
        french_phrases = [
            "l'", "d'", "qu'", "n'", "c'est", "j'ai", "il y a",
            "c'est", "n'est", "s'est", "qu'il", "qu'elle",
            "mise en", "afin de", "grâce à"
        ]
        
        # German indicators
        german_words = {
            # articles and pronouns
            "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
            "ich", "du", "er", "sie", "es", "wir", "ihr",
            "mein", "dein", "sein", "unser", "euer", "kein",
            # verbs
            "ist", "sind", "wird", "werden", "hat", "haben", "kann", "können",
            "muss", "müssen", "soll", "sollen", "wurde", "wurden", "war", "waren",
            # prepositions and conjunctions
            "und", "oder", "aber", "sowie", "jedoch", "denn", "weil", "dass",
            "für", "mit", "bei", "von", "auf", "aus", "nach", "zu", "zur", "zum",
            "über", "unter", "zwischen", "durch", "gegen", "ohne", "während",
            # adverbs
            "auch", "noch", "schon", "bereits", "immer", "nie", "oft",
            "sehr", "mehr", "weniger", "hier", "dort", "jetzt", "dann",
            # typical nouns and adjectives
            "hochschule", "universität", "forschung", "daten", "system",
            "entwicklung", "verwaltung", "einführung", "umsetzung",
            "neue", "neuen", "neuer", "wichtig", "wichtige",
            "digital", "digitale", "digitaler", "digitalen",
            # typical endings (as whole words)
            "automatisierung", "digitalisierung", "optimierung", "implementierung",
            "sicherheit", "verfügbarkeit", "nachhaltigkeit"
        }
        
        # English indicators
        english_words = {
            # articles and pronouns
            "the", "a", "an", "this", "that", "these", "those",
            "i", "you", "he", "she", "it", "we", "they",
            "my", "your", "his", "her", "its", "our", "their",
            # verbs
            "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did",
            "will", "would", "can", "could", "should", "may", "might",
            # prepositions and conjunctions
            "and", "or", "but", "if", "then", "because", "although",
            "for", "with", "from", "to", "at", "by", "about",
            "into", "through", "during", "before", "after", "between",
            # adverbs
            "also", "still", "already", "always", "never", "often",
            "very", "more", "less", "here", "there", "now", "then",
            # typical words
            "university", "research", "data", "system", "management",
            "development", "implementation", "security", "cloud",
            "software", "network", "service", "services"
        }
        
        # count the matching words
        french_count = len(words & french_words)
        german_count = len(words & german_words)
        english_count = len(words & english_words)
        
        # count French phrases separately
        for phrase in french_phrases:
            if phrase in text_lower:
                french_count += 2
        
        # special French characters (weighted strongly)
        french_chars = ['é', 'è', 'ê', 'ë', 'à', 'â', 'ù', 'û', 'ç', 'œ', 'î', 'ï', 'ô']
        french_char_count = sum(1 for c in text if c in french_chars)
        if french_char_count > 0:
            french_count += french_char_count * 2 + 2
        
        # special German characters (weighted strongly)
        german_chars = ['ä', 'ö', 'ü', 'ß']
        german_char_count = sum(1 for c in text if c in german_chars)
        if german_char_count > 0:
            german_count += german_char_count * 2 + 2
        
        # check German word endings (typical of German)
        german_endings = ['ung', 'keit', 'heit', 'schaft', 'lich', 'isch']
        for word in words:
            for ending in german_endings:
                if word.endswith(ending) and len(word) > len(ending) + 2:
                    german_count += 1
        
        # logging for debugging
        logger.debug(f"Language detection: '{text[:50]}...' -> DE:{german_count}, FR:{french_count}, EN:{english_count}")
        
        # decision with a clear priority
        max_count = max(french_count, german_count, english_count)
        
        # uncertain detection (few indicators): langdetect as a fallback
        if max_count < 2 or (max_count < 3 and len(text) > 50):
            try:
                from langdetect import detect, detect_langs
                # langdetect for a better detection in difficult cases
                detected_langs = detect_langs(text)
                if detected_langs:
                    top_lang = detected_langs[0]
                    if top_lang.prob > 0.7:  # only with high confidence
                        lang_code = top_lang.lang
                        if lang_code in ('de', 'fr', 'en', 'it'):
                            logger.debug(f"langdetect: {lang_code} (prob={top_lang.prob:.2f})")
                            return lang_code
                    elif top_lang.prob > 0.5:
                        # medium confidence: mark as 'uncertain'
                        logger.debug(f"langdetect uncertain: {top_lang.lang} (prob={top_lang.prob:.2f})")
                        return 'uncertain'
            except Exception as e:
                logger.debug(f"langdetect fallback failed: {e}")
        
        if max_count == 0:
            # no indicators found: mark as uncertain
            return 'uncertain'
        
        if french_count == max_count and french_count > german_count:
            return 'fr'
        elif english_count == max_count and english_count > german_count and english_count > french_count:
            return 'en'
        else:
            # German wins, or a tie
            return 'de'
    
    def translate_text(
        self,
        text: str,
        source_lang: str,
        target_lang: str = 'de'
    ) -> Tuple[str, bool]:
        """
        Translates a text.
        
        Args:
            text: text to translate
            source_lang: source language ('auto' = the LLM detects it)
            target_lang: target language
        
        Returns:
            tuple (translated text, success: True if translated/cached)
        """
        if not text or not text.strip():
            return text, True
        
        if source_lang == target_lang:
            return text, True
        
        # 'auto' or 'uncertain': the LLM with a special prompt
        if source_lang in ('auto', 'uncertain'):
            return self._translate_with_auto_detect(text, target_lang)
        
        # check the cache
        cache_key = f"{source_lang}:{target_lang}:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key], True
        
        # check the database cache
        if self.db:
            try:
                cached = self.db.get_translation(text, source_lang, target_lang)
                if cached:
                    self._cache[cache_key] = cached
                    return cached, True
            except Exception:
                pass
        
        # LLM translation through the single translation path; it stores the
        # result in the translation memory itself
        try:
            from core import state
            from utils.translation_service import TranslationContext, TranslationService
            svc = TranslationService(self.llm, state.config, self.db)
            translated = svc.translate(
                text, target_lang, TranslationContext.from_config(state.config, kind="answer"),
                source_lang=source_lang)
            if translated == text:
                return text, False  # not translated (LLM failure is logged by the service)
            self._cache[cache_key] = translated
            return translated, True
            
        except Exception as e:
            logger.error(f"LLM translation failed: {e}")
            return text, False  # report the error!
    
    def _translate_with_auto_detect(self, text: str, target_lang: str) -> Tuple[str, bool]:
        """
        Translates a text; the LLM detects the source language itself.
        
        Args:
            text: text to translate
            target_lang: target language
        
        Returns:
            tuple (translated text, success)
        """
        cache_key = f"auto:{target_lang}:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key], True
        # Through the single translation path, with the language left to the
        # LLM (AUTO_SOURCE); it keeps texts already in the target language.
        try:
            from core import state
            from utils.translation_service import AUTO_SOURCE, TranslationContext, TranslationService
            svc = TranslationService(self.llm, state.config, self.db)
            translated, ok = svc.translate_with_status(
                text, target_lang, TranslationContext.from_config(state.config, kind="answer"),
                source_lang=AUTO_SOURCE)
        except Exception as e:
            logger.error(f"Auto-translate failed: {e}")
            return text, False
        if ok:
            self._cache[cache_key] = translated
        return translated, ok
    
    def translate_dataframe_column(
        self,
        df: pd.DataFrame,
        column: str,
        target_lang: str = 'de',
        progress_callback=None,
        force_uncertain: bool = True
    ) -> Tuple[pd.DataFrame, Dict]:
        """
        Translates a column of the DataFrame.
        
        Args:
            df: DataFrame
            column: column name
            target_lang: target language
            progress_callback: optional callback(current, total, message)
            force_uncertain: translate even with an uncertain language detection
        
        Returns:
            tuple (DataFrame with the new column, statistics dict)
        """
        if column not in df.columns:
            return df, {'error': f"Column '{column}' not found"}
        
        stats = {
            'total': 0,
            'translated': 0,
            'skipped': 0,
            'from_cache': 0,
            'errors': 0,
            'by_source_lang': {'de': 0, 'fr': 0, 'en': 0, 'unknown': 0}
        }
        
        # new column for the translated values
        translated_column = f"{column}_translated_{target_lang}"
        df[translated_column] = df[column]
        
        # collect the distinct non-empty values
        unique_values = df[column].dropna().unique()
        total = len(unique_values)
        
        translation_map = {}
        
        for i, value in enumerate(unique_values):
            if progress_callback and i % 10 == 0:
                progress_callback(i, total, f"Translating... ({i}/{total})")
            
            value_str = str(value).strip()
            if not value_str:
                continue
            
            stats['total'] += 1
            
            # detect the language
            source_lang = self.detect_language(value_str)
            stats['by_source_lang'][source_lang] = stats['by_source_lang'].get(source_lang, 0) + 1
            
            # uncertain detection: let the LLM decide
            if source_lang == 'uncertain' and force_uncertain:
                # the LLM detects the language itself
                source_lang = 'auto'  # special value for "the LLM decides"
                stats['uncertain_forced'] = stats.get('uncertain_forced', 0) + 1
            
            if source_lang == target_lang:
                stats['skipped'] += 1
                translation_map[value] = value_str
                continue
            
            # already cached? (BEFORE calling translate_text!)
            cache_key = f"{source_lang}:{target_lang}:{value_str}"
            was_in_memory_cache = cache_key in self._cache
            was_in_db_cache = False
            if not was_in_memory_cache and self.db:
                try:
                    was_in_db_cache = self.db.get_translation(value_str, source_lang, target_lang) is not None
                except Exception:
                    pass
            
            # translate (returns a tuple)
            translated, success = self.translate_text(value_str, source_lang, target_lang)
            translation_map[value] = translated
            
            if not success:
                stats['errors'] += 1
            elif was_in_memory_cache or was_in_db_cache:
                stats['from_cache'] += 1
            else:
                stats['translated'] += 1
        
        # apply the translations
        df[translated_column] = df[column].map(lambda x: translation_map.get(x, x))
        
        if progress_callback:
            progress_callback(total, total, "Translation finished")
        
        return df, stats
    
    def translate_all_freetext_columns(
        self,
        df: pd.DataFrame,
        config: dict,
        target_lang: str = 'de',
        progress_callback=None
    ) -> Tuple[pd.DataFrame, Dict]:
        """
        Translates all free-text columns.
        
        Args:
            df: DataFrame
            config: configuration with question_groups
            target_lang: target language
            progress_callback: optional callback(current, total, message)
        
        Returns:
            tuple (DataFrame with the translated columns, overall statistics)
        """
        question_groups = config.get('question_groups', {})
        
        # 1. free-text columns defined explicitly in the configuration
        freetext_columns = []
        for qid, qconfig in question_groups.items():
            if qconfig.get('handler') == 'freetext':
                columns = qconfig.get('columns', [])
                freetext_columns.extend(columns)
        
        # 2. check all columns that could contain free text
        for col in df.columns:
            if col in freetext_columns:
                continue
            
            # skip obvious non-text columns
            skip_patterns = [
                '_translated_',  # columns translated already
                '_id', '_ID',    # id columns
                'timestamp', 'date', 'zeit',  # Datumsspalten
            ]
            if any(pattern in col for pattern in skip_patterns):
                continue
            
            # does the column contain text?
            sample = df[col].dropna()
            if len(sample) == 0:
                continue
            
            # only string columns
            if not pd.api.types.is_string_dtype(sample) and not pd.api.types.is_object_dtype(sample):
                continue
            
            # convert to string for the analysis
            try:
                sample_str = sample.astype(str)
            except:
                continue
            
            # categorical data (few distinct values)?
            n_unique = sample_str.nunique()
            n_total = len(sample_str)
            
            # categorical columns typically have < 20 distinct values or < 5% distinct
            if n_unique < 20 and n_unique / max(n_total, 1) < 0.05:
                # probably categorical, but check whether it could be French
                has_french = False
                for val in sample_str.head(20):
                    if any(c in str(val) for c in ['é', 'è', 'ê', 'à', 'ç', 'ù', 'î', 'ô']):
                        has_french = True
                        break
                    if any(phrase in str(val).lower() for phrase in ["l'", "d'", "qu'", "n'"]):
                        has_french = True
                        break
                
                if not has_french:
                    continue
            
            # check for free-text indicators:
            # - longer texts (> 10 characters on average)
            # - or French characters
            # - or typical free-text patterns
            avg_len = sample_str.str.len().mean()
            has_french_chars = sample_str.str.contains(r'[éèêëàâùûçœîïô]', regex=True).any()
            has_newlines = sample_str.str.contains(r'\n', regex=True).any()
            has_sentences = sample_str.str.contains(r'[.!?]', regex=True).sum() > len(sample_str) * 0.1
            
            # add the column if it looks like free text
            if avg_len > 10 or has_french_chars or has_newlines or has_sentences:
                freetext_columns.append(col)
                logger.debug(f"Free-text column detected: {col} (avg_len={avg_len:.1f}, french={has_french_chars})")
        
        # deduplicate and sort
        freetext_columns = list(dict.fromkeys(freetext_columns))
        
        logger.info(f"Translating {len(freetext_columns)} columns: {freetext_columns[:5]}...")
        
        total_columns = len(freetext_columns)
        total_stats = {
            'columns_processed': 0,
            'columns_found': total_columns,
            'total_values': 0,
            'translated': 0,
            'skipped': 0,
            'from_cache': 0,
            'errors': 0,
            'by_source_lang': {'de': 0, 'fr': 0, 'en': 0, 'unknown': 0}
        }
        
        for i, column in enumerate(freetext_columns):
            if progress_callback:
                progress_callback(i, total_columns, f"Column: {column[:50]}...")
            
            if column not in df.columns:
                continue
            
            df, col_stats = self.translate_dataframe_column(
                df, column, target_lang
            )
            
            total_stats['columns_processed'] += 1
            total_stats['total_values'] += col_stats.get('total', 0)
            total_stats['translated'] += col_stats.get('translated', 0)
            total_stats['skipped'] += col_stats.get('skipped', 0)
            total_stats['from_cache'] += col_stats.get('from_cache', 0)
            total_stats['errors'] += col_stats.get('errors', 0)
            
            for lang, count in col_stats.get('by_source_lang', {}).items():
                total_stats['by_source_lang'][lang] = \
                    total_stats['by_source_lang'].get(lang, 0) + count
        
        if progress_callback:
            progress_callback(total_columns, total_columns, "All columns translated")
        
        return df, total_stats


def translate_responses(
    df: pd.DataFrame,
    config: dict,
    target_lang: str = 'de',
    db=None,
    import_id: int = None,
    progress_callback=None
) -> Tuple[pd.DataFrame, Dict]:
    """
    Main function: translates all free-text answers.
    
    Args:
        df: DataFrame with the survey data
        config: configuration
        target_lang: target language
        db: database connection
        import_id: import id
        progress_callback: progress callback
    
    Returns:
        tuple (translated DataFrame, statistics)
    """
    translator = ResponseTranslator(db=db, import_id=import_id)
    return translator.translate_all_freetext_columns(
        df, config, target_lang, progress_callback
    )
