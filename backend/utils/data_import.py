# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Data import module of the survey analysis tool: processes CSV files with
language detection and translation.
"""

import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import pandas as pd
import langdetect
from langdetect import detect_langs

from .llm import get_llm_client

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    """Result of the data import."""
    data: pd.DataFrame
    total_responses: int
    language_distribution: dict[str, int]
    columns: list[str]
    missing_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DataImporter:
    """Imports and processes survey data from CSV."""
    
    def __init__(self, freetext_columns: Optional[list[str]] = None):
        """
        Initialises the DataImporter.
        
        Args:
            freetext_columns: list of the columns with free-text answers
        """
        self.freetext_columns = freetext_columns or []
        self.llm = get_llm_client()
    
    def load_csv(
        self,
        filepath: str | Path,
        delimiter: str = ";",
        encoding: str = "utf-8"
    ) -> pd.DataFrame:
        """
        Loads a CSV file.
        
        Args:
            filepath: path of the CSV file
            delimiter: separator (default: semicolon)
            encoding: character encoding
        
        Returns:
            DataFrame with the data
        """
        filepath = Path(filepath)
        
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        
        # try several encodings
        encodings_to_try = [encoding, "utf-8-sig", "latin-1", "cp1252"]
        
        for enc in encodings_to_try:
            try:
                df = pd.read_csv(filepath, delimiter=delimiter, encoding=enc)
                logger.info(f"CSV loaded with the encoding: {enc}")
                return df
            except UnicodeDecodeError:
                continue
        
        raise ValueError(f"Could not read the file with any known encoding: {filepath}")
    
    def detect_language(self, text: str) -> str:
        """
        Detects the language of a text.
        
        Args:
            text: the text to analyse
        
        Returns:
            language code (de, fr, en, it); for short or unrecognisable text the
            survey's source language
        """
        from utils.prompts import source_language
        default = source_language()
        if not text or not isinstance(text, str) or len(text.strip()) < 10:
            return default  # short or empty text: source language
        
        try:
            # use langdetect
            detected = detect_langs(text)
            if detected:
                lang = detected[0].lang
                # mapping to our languages:
                # languages with fixed texts (locales/); others count as the source language
                return lang if lang in ("de", "fr", "en", "it") else default
        except langdetect.lang_detect_exception.LangDetectException:
            pass
        
        return default
    
    def detect_response_language(self, row: pd.Series, freetext_cols: list[str]) -> str:
        """
        Detects the language of an answer from its free-text fields.
        
        Args:
            row: a data row
            freetext_cols: list of the free-text columns
        
        Returns:
            language code
        """
        # collect all free texts
        texts = []
        for col in freetext_cols:
            if col in row.index:
                val = row[col]
                if pd.notna(val) and isinstance(val, str) and len(val.strip()) > 10:
                    texts.append(val)
        
        if not texts:
            return "de"
        
        # analyse the longest text
        combined_text = " ".join(texts)
        return self.detect_language(combined_text)
    
    def add_language_column(
        self,
        df: pd.DataFrame,
        language_column: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Adds a language column or uses an existing one.
        
        Args:
            df: DataFrame
            language_column: name of an existing language column (optional)
        
        Returns:
            DataFrame with the language column "_language"
        """
        if language_column and language_column in df.columns:
            # use the existing column
            df["_language"] = df[language_column].apply(
                lambda x: "fr" if "fr" in str(x).lower() else (
                    "en" if "en" in str(x).lower() else "de"
                )
            )
        else:
            # detect the language automatically
            logger.info("Detecting the language of every answer...")
            df["_language"] = df.apply(
                lambda row: self.detect_response_language(row, self.freetext_columns),
                axis=1
            )
        
        return df
    
    def translate_freetext(
        self,
        df: pd.DataFrame,
        target_lang: str = "de",
        progress_callback: Optional[callable] = None
    ) -> pd.DataFrame:
        """
        Translates free-text answers in other languages into the target language.
        
        Args:
            df: DataFrame with the _language column
            target_lang: target language (default: de)
            progress_callback: callback for the progress display
        
        Returns:
            DataFrame with translated texts (the original is kept)
        """
        # find the rows to translate
        non_target = df[df["_language"] != target_lang]
        
        if len(non_target) == 0:
            logger.info("All answers are in the target language already.")
            return df
        
        logger.info(f"Translating {len(non_target)} answers into {target_lang}...")
        
        # create the translation columns
        for col in self.freetext_columns:
            if col not in df.columns:
                continue
            
            # create the column of the original
            original_col = f"{col}_original"
            if original_col not in df.columns:
                df[original_col] = df[col]
            
            # collect the texts to translate
            for idx in non_target.index:
                source_lang = df.at[idx, "_language"]
                text = df.at[idx, col]
                
                if pd.notna(text) and isinstance(text, str) and len(text.strip()) > 0:
                    try:
                        from core import state
                        from utils.translation_service import TranslationContext, get_translation_service
                        svc = get_translation_service(self.llm)
                        translated = svc.translate(
                            text, target_lang,
                            TranslationContext.from_config(state.config, kind="answer", scope=col),
                            source_lang=source_lang)
                        df.at[idx, col] = translated
                        df.at[idx, original_col] = text  # keep the original
                    except Exception as e:
                        logger.warning(f"Translation failed for row {idx}: {e}")
                
                if progress_callback:
                    progress_callback()
        
        return df
    
    def validate_columns(
        self,
        df: pd.DataFrame,
        expected_columns: list[str]
    ) -> tuple[list[str], list[str]]:
        """
        Validates whether the expected columns are present.
        
        Args:
            df: DataFrame
            expected_columns: list of the expected columns
        
        Returns:
            tuple (present columns, missing columns)
        """
        existing = [col for col in expected_columns if col in df.columns]
        missing = [col for col in expected_columns if col not in df.columns]
        
        return existing, missing
    
    def import_survey(
        self,
        filepath: str | Path,
        expected_columns: Optional[list[str]] = None,
        language_column: Optional[str] = None,
        translate: bool = True,
        delimiter: str = ";",
        progress_callback: Optional[callable] = None
    ) -> ImportResult:
        """
        Complete import of a survey CSV.
        
        Args:
            filepath: path of the CSV file
            expected_columns: list of the expected columns (optional)
            language_column: name of the language column (optional)
            translate: whether to translate automatically
            delimiter: CSV separator
            progress_callback: callback for the progress display
        
        Returns:
            ImportResult with all data and meta information
        """
        warnings = []
        
        # load the CSV
        df = self.load_csv(filepath, delimiter=delimiter)
        logger.info(f"Loaded: {len(df)} rows, {len(df.columns)} columns")
        
        # validate the columns
        missing_columns = []
        if expected_columns:
            _, missing_columns = self.validate_columns(df, expected_columns)
            if missing_columns:
                warnings.append(f"Missing columns: {', '.join(missing_columns[:5])}...")
        
        # detect the language
        df = self.add_language_column(df, language_column)
        
        # compute the language distribution
        lang_dist = df["_language"].value_counts().to_dict()
        logger.info(f"Sprachverteilung: {lang_dist}")
        
        # translate
        if translate and self.freetext_columns:
            df = self.translate_freetext(df, progress_callback=progress_callback)
        
        return ImportResult(
            data=df,
            total_responses=len(df),
            language_distribution=lang_dist,
            columns=list(df.columns),
            missing_columns=missing_columns,
            warnings=warnings
        )


def load_survey_data(
    filepath: str | Path,
    config: dict,
    translate: bool = True,
    progress_callback: Optional[callable] = None
) -> ImportResult:
    """
    Convenience function to load survey data.
    
    Args:
        filepath: path of the CSV file
        config: configuration dictionary from questions.yaml
        translate: whether to translate
        progress_callback: progress callback
    
    Returns:
        ImportResult
    """
    # extract the free-text columns from the configuration
    freetext_columns = []
    for group in config.get("question_groups", {}).values():
        if group.get("type") == "freetext":
            freetext_columns.extend(group.get("columns", []))
    
    # collect all expected columns
    expected_columns = []
    for group in config.get("question_groups", {}).values():
        expected_columns.extend(group.get("columns", []))
    
    # add the segmentation columns
    seg_config = config.get("settings", {}).get("segmentation", {})
    for seg in seg_config.values():
        if "column" in seg:
            expected_columns.append(seg["column"])
    
    importer = DataImporter(freetext_columns=freetext_columns)
    return importer.import_survey(
        filepath,
        expected_columns=expected_columns,
        translate=translate,
        progress_callback=progress_callback
    )
