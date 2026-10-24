# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Persistence of the survey analysis tool: stores data, analysis results and
session state in SQLite.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Schema version of the database. There are no migrations: a database written
# by another schema version is refused at start-up (check_database_version),
# never changed; such a survey is imported again into a new database.
SCHEMA_VERSION = 2


def get_db_path() -> Path:
    """
    Path of the database the application has open (core.state.db), else the
    configured one (core.DB_PATH). Used by functions that query it with sqlite3 directly.
    """
    from core import DB_PATH, state  # lazy: core imports this module
    db = getattr(state, "db", None)
    return Path(db.db_path) if db is not None else Path(DB_PATH)


class IncompatibleDatabaseError(RuntimeError):
    """The database file was written by another schema version."""


def check_database_version(db_path) -> None:
    """
    Refuse a database of another schema version, before anything is written
    to it. A new or empty file is fine (the schema is created). There is no
    migration: the message names the file so it can be moved aside and the
    survey imported again.
    """
    db_path = Path(db_path)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return
        version = None
        if "meta" in tables:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = row[0] if row else None
    finally:
        conn.close()
    if str(version) != str(SCHEMA_VERSION):
        raise IncompatibleDatabaseError(
            f"The database {db_path} was written by another version of this tool "
            f"(schema version {version or 'unknown'}, expected {SCHEMA_VERSION}). It is not "
            f"migrated. Move the file aside (or set SURVEY_DATA_DIR to another directory) "
            f"and import the survey again."
        )


def _numpy_safe_json_default(obj):
    """
    JSON serialisation helper for numpy types and DataFrames, used as the
    default parameter of json.dumps.
    
    IMPORTANT: NaN and Infinity become None, as they are no valid JSON values.
    """
    import math
    
    # check NaN and Infinity first
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict()
    if isinstance(obj, pd.Series):
        return obj.to_dict()
    if pd.isna(obj):
        return None
    # fallback for unknown types
    return str(obj)


class SurveyDatabase:
    """SQLite database of the survey analysis results."""
    
    def __init__(self, db_path: str | Path):
        """
        Initialises the database connection.
        
        Args:
            db_path: path of the SQLite file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        check_database_version(self.db_path)
        self._init_database()
    
    @contextmanager
    def _connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_database(self):
        """Creates the database schema."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # meta table for the schema version
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # projects (for several surveys/years)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # import sessions (every data import)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    filename TEXT,
                    total_responses INTEGER,
                    columns_json TEXT,
                    language_distribution_json TEXT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    survey_id TEXT NOT NULL DEFAULT 'default',
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_imports_survey
                ON imports(survey_id)
            """)
            
            # raw data (original answers)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS raw_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    row_index INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    detected_language TEXT,
                    FOREIGN KEY (import_id) REFERENCES imports(id),
                    UNIQUE(import_id, row_index)
                )
            """)
            
            # analysis results per question group
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    segment_type TEXT DEFAULT 'all',
                    segment_value TEXT,
                    handler_type TEXT NOT NULL,
                    n INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    analysis_mode TEXT DEFAULT 'raw',
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (import_id) REFERENCES imports(id),
                    UNIQUE(import_id, question_id, segment_type, segment_value)
                )
            """)
            
            # free-text clusters
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS freetext_clusters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id INTEGER NOT NULL,
                    cluster_name TEXT NOT NULL,       -- source language
                    description_source TEXT,              -- source language
                    translations_json TEXT,           -- {lang: {name, description}}
                    response_count INTEGER,
                    response_ids_json TEXT,
                    examples_json TEXT,
                    is_manual BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP,
                    updated_by TEXT,
                    FOREIGN KEY (analysis_id) REFERENCES analysis_results(id)
                )
            """)
            
            # translations (cache)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    -- '' = valid everywhere; otherwise e.g. a question id, so the
                    -- same text can be translated differently in another context
                    scope TEXT NOT NULL DEFAULT '',
                    -- manual corrections are never overwritten automatically
                    is_manual INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source_text, source_lang, target_lang, scope)
                )
            """)
            
            # theme definitions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    theme_id TEXT NOT NULL,
                    label_source TEXT,           -- source language
                    translations_json TEXT,  -- {lang: {label}}
                    question_keywords TEXT,  -- JSON array
                    answer_keywords TEXT,    -- JSON array
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(project_id, theme_id),
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)
            
            # theme assignments (question to theme)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS theme_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    theme_id TEXT NOT NULL,
                    question_id TEXT,
                    keyword TEXT,
                    source TEXT,  -- 'question', 'answer', 'manual'
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)
            
            # =========================================================
            # Item extraction tables
            # =========================================================
            
            # single items extracted from free-text answers
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS response_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    row_index INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    item_index INTEGER NOT NULL,
                    original_text TEXT,
                    extracted_text TEXT NOT NULL,
                    normalized_text TEXT,
                    extraction_method TEXT NOT NULL,
                    extraction_confidence REAL DEFAULT 1.0,
                    llm_reasoning TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    -- Workbench columns
                    is_deleted BOOLEAN DEFAULT FALSE,
                    deleted_at TIMESTAMP,
                    edited_at TIMESTAMP,
                    original_extracted_text TEXT,
                    cluster_id INTEGER REFERENCES freetext_clusters(id),
                    -- row_index is the data row of the import (raw_responses.row_index),
                    -- not raw_responses.id: every writer stores the row, every reader joins
                    -- on (import_id, row_index); hence no foreign key on raw_responses.id
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
                    UNIQUE(import_id, row_index, question_id, column_name, item_index)
                )
            """)
            
            # extraction jobs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS extraction_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    total_responses INTEGER DEFAULT 0,
                    processed_responses INTEGER DEFAULT 0,
                    total_items_extracted INTEGER DEFAULT 0,
                    method_stats_json TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
                    UNIQUE(import_id, question_id, column_name)
                )
            """)
            
            # =========================================================
            # Workbench tables
            # =========================================================
            
            # Item-Bearbeitungshistorie
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS item_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    source_item_ids TEXT NOT NULL,
                    target_item_ids TEXT,
                    result_text TEXT,
                    details_json TEXT,
                    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    performed_by TEXT,
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
                )
            """)
            
            # Item-Normalisierungen
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS item_normalizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    variant_text TEXT NOT NULL,
                    canonical_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by TEXT,
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
                    UNIQUE(import_id, question_id, variant_text)
                )
            """)
            
            # =========================================================
            # Indexes for performance
            # =========================================================
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_analysis_import 
                ON analysis_results(import_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_analysis_question 
                ON analysis_results(question_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_translations_source 
                ON translations(source_text, source_lang, target_lang)
            """)
            
            # Items are joined to their response by (import_id, row_index)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_raw_import_row
                ON raw_responses(import_id, row_index)
            """)
            
            # indexes for the item extraction
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_import_question 
                ON response_items(import_id, question_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_response 
                ON response_items(row_index)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_column 
                ON response_items(import_id, column_name)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_import 
                ON extraction_jobs(import_id)
            """)
            
            # Workbench indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_deleted 
                ON response_items(is_deleted)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_cluster 
                ON response_items(cluster_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_import_question 
                ON item_history(import_id, question_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_normalizations_question 
                ON item_normalizations(import_id, question_id)
            """)
            
            # question summaries (written by the LLM)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS question_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    lang TEXT NOT NULL DEFAULT 'de',
                    schema_id TEXT DEFAULT 'default_three_sections',
                    sections_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
                    UNIQUE(import_id, question_id, lang)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_summaries_import_question
                ON question_summaries(import_id, question_id)
            """)
            
            # translation cache of the publication documents
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    section_file TEXT NOT NULL,
                    paragraph_index INTEGER NOT NULL,
                    source_lang TEXT NOT NULL DEFAULT 'de',
                    target_lang TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    UNIQUE(document_id, section_file, paragraph_index, source_lang, target_lang)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_doc_trans_lookup
                ON document_translations(document_id, target_lang)
            """)
            
            # v9: generated texts of statistical findings (see utils/analysis_texts.py)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_texts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    facts_hash TEXT NOT NULL,
                    texts_json TEXT NOT NULL,
                    is_manual INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
                    UNIQUE(import_id, kind, item_key)
                )
            """)
            
            # store the schema version
            cursor.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION))
            )
    
    # =========================================================================
    # PROJECT MANAGEMENT
    # =========================================================================
    
    def create_project(self, name: str, description: str = "") -> int:
        """Creates a new project."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO projects (name, description) VALUES (?, ?)",
                (name, description)
            )
            return cursor.lastrowid
    
    def get_project(self, project_id: int) -> Optional[Dict]:
        """Loads a project."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_project_by_name(self, name: str) -> Optional[Dict]:
        """Loads a project by name."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def list_projects(self) -> List[Dict]:
        """Lists all projects."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            return [dict(row) for row in cursor.fetchall()]
    
    def update_project_timestamp(self, project_id: int):
        """Updates the timestamp of a project."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id,)
            )
    
    # =========================================================================
    # DATA IMPORT
    # =========================================================================
    
    def save_import(
        self,
        project_id: int,
        df: pd.DataFrame,
        filename: str = "",
        language_distribution: Dict[str, int] = None,
        survey_id: str = 'default'
    ) -> int:
        """
        Stores a data import.
        
        Args:
            project_id: project id
            df: DataFrame with the raw data
            filename: original file name
            language_distribution: distribution of the languages
            survey_id: id of the survey the import belongs to
                (default: 'default' if no survey is active).
        
        Returns:
            import id
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # store the import metadata
            cursor.execute("""
                INSERT INTO imports (
                    project_id, filename, total_responses, 
                    columns_json, language_distribution_json, survey_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                project_id,
                filename,
                len(df),
                json.dumps(list(df.columns)),
                json.dumps(language_distribution or {}),
                survey_id or 'default'
            ))
            import_id = cursor.lastrowid
            
            # store the raw data row by row
            for idx, row in df.iterrows():
                cursor.execute("""
                    INSERT INTO raw_responses (import_id, row_index, data_json)
                    VALUES (?, ?, ?)
                """, (import_id, idx, json.dumps(row.to_dict(), default=_numpy_safe_json_default)))
            
            # update the project timestamp
            cursor.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id,)
            )
            
            logger.info(f"Import stored: {len(df)} rows, ID={import_id}")
            return import_id
    
    def load_import(self, import_id: int) -> Optional[pd.DataFrame]:
        """
        Loads an import as a DataFrame.
        
        Args:
            import_id: import id
        
        Returns:
            DataFrame or None
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # load the raw data
            cursor.execute("""
                SELECT row_index, data_json 
                FROM raw_responses 
                WHERE import_id = ?
                ORDER BY row_index
            """, (import_id,))
            
            rows = cursor.fetchall()
            if not rows:
                return None
            
            data = [json.loads(row['data_json']) for row in rows]
            return pd.DataFrame(data)
    
    def get_latest_import(self, project_id: int) -> Optional[Dict]:
        """Returns the newest import of a project."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM imports 
                WHERE project_id = ?
                ORDER BY imported_at DESC
                LIMIT 1
            """, (project_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_latest_import_for_survey(
        self,
        survey_id: str,
        project_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Returns the newest import of a survey.
        
        Args:
            survey_id: filter by survey.
            project_id: optional additional filter.
        
        Returns:
            dict with the import data, or None.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            if project_id is not None:
                cursor.execute("""
                    SELECT * FROM imports
                    WHERE survey_id = ? AND project_id = ?
                    ORDER BY imported_at DESC
                    LIMIT 1
                """, (survey_id, project_id))
            else:
                cursor.execute("""
                    SELECT * FROM imports
                    WHERE survey_id = ?
                    ORDER BY imported_at DESC
                    LIMIT 1
                """, (survey_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_imports_for_survey(
        self,
        survey_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """
        Lists all imports of a survey, newest first.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            sql = """
                SELECT * FROM imports
                WHERE survey_id = ?
                ORDER BY imported_at DESC
            """
            if limit:
                sql += " LIMIT ?"
                cursor.execute(sql, (survey_id, limit))
            else:
                cursor.execute(sql, (survey_id,))
            return [dict(r) for r in cursor.fetchall()]
    
    def count_imports_per_survey(self) -> Dict[str, int]:
        """
        {survey_id: count} over all imports.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT survey_id, COUNT(*) AS n
                FROM imports
                GROUP BY survey_id
                ORDER BY n DESC
            """)
            return {row['survey_id']: row['n'] for row in cursor.fetchall()}
    
    # =========================================================================
    # ANALYSIS RESULTS
    # =========================================================================
    
    def save_analysis_result(
        self,
        import_id: int,
        question_id: str,
        handler_type: str,
        result: Dict[str, Any],
        n: int,
        segment_type: str = "all",
        segment_value: str = None
    ) -> int:
        """
        Stores an analysis result.
        
        Args:
            import_id: import id
            question_id: question group id
            handler_type: handler type
            result: result dictionary
            n: sample size
            segment_type: segmentation type
            segment_value: segmentation value
        
        Returns:
            analysis id
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # delete the existing result (update)
            cursor.execute("""
                DELETE FROM analysis_results 
                WHERE import_id = ? AND question_id = ? 
                AND segment_type = ? AND (segment_value = ? OR (segment_value IS NULL AND ? IS NULL))
            """, (import_id, question_id, segment_type, segment_value, segment_value))
            
            # store the new result
            cursor.execute("""
                INSERT INTO analysis_results (
                    import_id, question_id, segment_type, segment_value,
                    handler_type, n, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                import_id, question_id, segment_type, segment_value,
                handler_type, n, json.dumps(result, default=_numpy_safe_json_default)
            ))
            
            return cursor.lastrowid
    
    def get_analysis_result(
        self,
        import_id: int,
        question_id: str,
        segment_type: str = "all",
        segment_value: str = None
    ) -> Optional[Dict]:
        """Loads an analysis result."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM analysis_results
                WHERE import_id = ? AND question_id = ?
                AND segment_type = ? AND (segment_value = ? OR (segment_value IS NULL AND ? IS NULL))
            """, (import_id, question_id, segment_type, segment_value, segment_value))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            result = dict(row)
            result['result'] = json.loads(result['result_json'])
            return result
    
    def get_all_analysis_results(self, import_id: int) -> List[Dict]:
        """Loads all analysis results of an import."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM analysis_results
                WHERE import_id = ?
                ORDER BY question_id, segment_type
            """, (import_id,))
            
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['result'] = json.loads(result['result_json'])
                results.append(result)
            return results
    
    def has_analysis(
        self,
        import_id: int,
        question_id: str,
        segment_type: str = "all",
        segment_value: str = None
    ) -> bool:
        """Checks whether an analysis exists."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM analysis_results
                WHERE import_id = ? AND question_id = ?
                AND segment_type = ? AND (segment_value = ? OR (segment_value IS NULL AND ? IS NULL))
                LIMIT 1
            """, (import_id, question_id, segment_type, segment_value, segment_value))
            return cursor.fetchone() is not None
    
    # =========================================================================
    # FREE-TEXT CLUSTERS
    # =========================================================================
    
    
    # =========================================================================
    # TRANSLATION CACHE
    # =========================================================================
    
    def get_translation(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        scope: str = ''
    ) -> Optional[str]:
        """
        Cached translation; an entry for ``scope`` wins over the general one
        ('' = valid everywhere).
        """
        with self._connection() as conn:
            row = conn.execute("""
                SELECT translated_text FROM translations
                WHERE source_text = ? AND source_lang = ? AND target_lang = ?
                  AND scope IN (?, '')
                ORDER BY (scope = ?) DESC, is_manual DESC
                LIMIT 1
            """, (source_text, source_lang, target_lang, scope, scope)).fetchone()
            return row['translated_text'] if row else None
    
    def save_translation(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        translated_text: str,
        scope: str = '',
        is_manual: bool = False
    ) -> bool:
        """
        Store a translation. An automatic translation never replaces a manual
        correction for the same text, language pair and scope.

        Returns False if the entry was kept because it is a manual correction.
        """
        with self._connection() as conn:
            existing = conn.execute("""
                SELECT is_manual FROM translations
                WHERE source_text = ? AND source_lang = ? AND target_lang = ? AND scope = ?
            """, (source_text, source_lang, target_lang, scope)).fetchone()
            if existing and existing['is_manual'] and not is_manual:
                return False
            conn.execute("""
                INSERT INTO translations
                    (source_text, source_lang, target_lang, translated_text, scope, is_manual)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_text, source_lang, target_lang, scope) DO UPDATE SET
                    translated_text = excluded.translated_text,
                    is_manual = excluded.is_manual
            """, (source_text, source_lang, target_lang, translated_text, scope, int(is_manual)))
            return True
    
    def get_translation_stats(self) -> Dict[str, int]:
        """Statistics of the translation cache."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM translations")
            total = cursor.fetchone()['count']
            
            cursor.execute("""
                SELECT source_lang || '->' || target_lang as pair, COUNT(*) as count
                FROM translations GROUP BY pair
            """)
            pairs = {row['pair']: row['count'] for row in cursor.fetchall()}
            
            return {'total': total, 'pairs': pairs}
    
    # =========================================================================
    # THEMES
    # =========================================================================
    
    def save_theme_mapping(
        self,
        project_id: int,
        theme_id: str,
        question_id: str = None,
        keyword: str = None,
        source: str = "manual"
    ):
        """Stores a theme assignment."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO theme_mappings (project_id, theme_id, question_id, keyword, source)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, theme_id, question_id, keyword, source))
    
    def get_theme_mappings(self, project_id: int) -> Dict[str, List[str]]:
        """Loads all theme assignments of a project."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT theme_id, question_id FROM theme_mappings
                WHERE project_id = ? AND question_id IS NOT NULL
            """, (project_id,))
            
            mappings = {}
            for row in cursor.fetchall():
                theme = row['theme_id']
                if theme not in mappings:
                    mappings[theme] = []
                mappings[theme].append(row['question_id'])
            return mappings
    
    def save_theme(
        self,
        project_id: int,
        theme_id: str,
        label_source: str = "",
        label_fr: str = "",
        label_en: str = "",
        question_keywords: list = None,
        answer_keywords: list = None,
        labels: Optional[Dict[str, str]] = None
    ):
        """
        Stores or updates a theme definition.
        
        label_source holds the source-language label; every
        other language goes to translations_json. ``labels`` may give labels
        for any language, e.g. {'it': '...'}.
        
        Args:
            project_id: project id
            theme_id: theme id (e.g. 'ai', 'data_protection')
            label_source/fr/en: labels in the languages
            question_keywords: list of question keywords
            answer_keywords: list of answer keywords
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            q_kw_json = json.dumps(question_keywords or [], ensure_ascii=False)
            a_kw_json = json.dumps(answer_keywords or [], ensure_ascii=False)
            
            from utils.prompts import source_language
            src = source_language()
            flat = {'label_fr': label_fr, 'label_en': label_en}
            flat.update({f"label_{k}": v for k, v in (labels or {}).items()})
            flat.pop(f"label_{src}", None)   # the source label is the column label_source
            translations = translations_from_flat(flat, fields=("label",))
            cursor.execute("""
                INSERT INTO themes (project_id, theme_id, label_source, translations_json,
                                   question_keywords, answer_keywords, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id, theme_id) DO UPDATE SET
                    label_source = excluded.label_source,
                    translations_json = excluded.translations_json,
                    question_keywords = excluded.question_keywords,
                    answer_keywords = excluded.answer_keywords,
                    updated_at = CURRENT_TIMESTAMP
            """, (project_id, theme_id, label_source, translations, q_kw_json, a_kw_json))
    
    def get_themes(self, project_id: int) -> Dict[str, Dict]:
        """
        Loads all theme definitions of a project.
        
        Returns:
            dict theme_id -> {label: {lang: text}, question_keywords: [], answer_keywords: []}
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT theme_id, label_source, translations_json,
                       question_keywords, answer_keywords
                FROM themes
                WHERE project_id = ?
            """, (project_id,))
            
            from utils.prompts import source_language
            src = source_language()
            themes = {}
            for row in cursor.fetchall():
                theme_id = row['theme_id']
                themes[theme_id] = {
                    'id': theme_id,
                    # source label (column label_source) under the
                    # source language, translations under theirs
                    'label': {
                        src: row['label_source'] or theme_id,
                        **{k[len('label_'):]: v for k, v in translations_to_flat(
                            row['translations_json'], fields=("label",)).items() if v},
                    },
                    'question_keywords': json.loads(row['question_keywords'] or '[]'),
                    'answer_keywords': json.loads(row['answer_keywords'] or '[]')
                }
            return themes
    
    def delete_theme(self, project_id: int, theme_id: str):
        """Deletes a theme and all its mappings."""
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM themes WHERE project_id = ? AND theme_id = ?",
                (project_id, theme_id)
            )
            cursor.execute(
                "DELETE FROM theme_mappings WHERE project_id = ? AND theme_id = ?",
                (project_id, theme_id)
            )
    
    # =========================================================================
    # RESPONSE ITEMS (item extraction)
    # =========================================================================
    
    def save_response_item(
        self,
        import_id: int,
        row_index: int,
        question_id: str,
        column_name: str,
        item_index: int,
        original_text: str,
        extracted_text: str,
        extraction_method: str,
        extraction_confidence: float = 1.0,
        llm_reasoning: str = None
    ) -> int:
        """
        Stores an extracted item.
        
        Args:
            import_id: import id
            row_index: data row of the response in the import (raw_responses.row_index,
                the DataFrame index at import time), not raw_responses.id
            question_id: question id
            column_name: original column name
            item_index: index of the item (1, 2, 3...)
            original_text: full text of the original answer
            extracted_text: extracted item
            extraction_method: method used
            extraction_confidence: confidence value (0-1)
            llm_reasoning: reasoning of the LLM (optional)
        
        Returns:
            item id
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO response_items (
                    import_id, row_index, question_id, column_name,
                    item_index, original_text, extracted_text,
                    extraction_method, extraction_confidence, llm_reasoning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                import_id, row_index, question_id, column_name,
                item_index, original_text, extracted_text,
                extraction_method, extraction_confidence, llm_reasoning
            ))
            return cursor.lastrowid
    
    def get_items_for_question(
        self,
        import_id: int,
        question_id: str,
        column_name: str = None,
        with_metadata: bool = True
    ) -> List[Dict]:
        """
        Loads all items of a question.
        
        Args:
            import_id: import id
            question_id: question id
            column_name: optional: only the items of this column
            with_metadata: include the response metadata
        
        Returns:
            list of item dictionaries
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            if with_metadata:
                # response_items.row_index is the data row of the import
                # (raw_responses.row_index), see the table definition
                query = """
                    SELECT 
                        i.id as item_id,
                        i.row_index,
                        i.question_id,
                        i.column_name,
                        i.item_index,
                        i.original_text,
                        i.extracted_text,
                        i.extraction_method,
                        i.extraction_confidence,
                        i.llm_reasoning,
                        i.created_at,
                        r.data_json,
                        r.row_index AS raw_row_index
                    FROM response_items i
                    LEFT JOIN raw_responses r ON i.row_index = r.row_index AND r.import_id = i.import_id
                    WHERE i.import_id = ? AND i.question_id = ?
                """
                params = [import_id, question_id]
                
                if column_name:
                    query += " AND i.column_name = ?"
                    params.append(column_name)
                
                query += " ORDER BY i.row_index, i.item_index"
            else:
                query = """
                    SELECT * FROM response_items
                    WHERE import_id = ? AND question_id = ?
                """
                params = [import_id, question_id]
                
                if column_name:
                    query += " AND column_name = ?"
                    params.append(column_name)
                
                query += " ORDER BY row_index, item_index"
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_items_with_segments(
        self,
        import_id: int,
        question_id: str,
        segment_column: str = None,
        segment_value: str = None
    ) -> List[Dict]:
        """
        Loads items, optionally for one segment.
        
        Args:
            import_id: import id
            question_id: question id
            segment_column: JSON path of the segment column (e.g. "role")
            segment_value: segment value wanted (e.g. "Student")
        
        Returns:
            list of item dictionaries with metadata
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT 
                    i.id as item_id,
                    i.extracted_text,
                    i.extraction_method,
                    i.extraction_confidence,
                    i.row_index,
                    r.data_json,
                    r.row_index AS raw_row_index
                FROM response_items i
                JOIN raw_responses r ON i.row_index = r.row_index AND r.import_id = i.import_id
                WHERE i.import_id = ? AND i.question_id = ?
            """
            params = [import_id, question_id]
            
            if segment_column and segment_value:
                # SQLite JSON extraction
                query += f" AND json_extract(r.data_json, '$.{segment_column}') = ?"
                params.append(segment_value)
            
            query += " ORDER BY r.row_index, i.item_index"
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_item_stats(
        self,
        import_id: int,
        question_id: str,
        column_name: str = None
    ) -> Dict[str, Any]:
        """
        Statistics of the extracted items.
        
        Returns:
            dictionary with statistics
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            where_clause = "WHERE import_id = ? AND question_id = ?"
            params = [import_id, question_id]
            
            if column_name:
                where_clause += " AND column_name = ?"
                params.append(column_name)
            
            # overall statistics
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as total_items,
                    COUNT(DISTINCT row_index) as unique_responses,
                    AVG(extraction_confidence) as avg_confidence
                FROM response_items
                {where_clause}
            """, params)
            
            row = cursor.fetchone()
            total_items = row['total_items'] or 0
            unique_responses = row['unique_responses'] or 0
            avg_confidence = row['avg_confidence'] or 0.0
            
            # statistics per method
            cursor.execute(f"""
                SELECT 
                    extraction_method,
                    COUNT(*) as count,
                    AVG(extraction_confidence) as avg_conf
                FROM response_items
                {where_clause}
                GROUP BY extraction_method
            """, params)
            
            methods = {}
            for row in cursor.fetchall():
                methods[row['extraction_method']] = {
                    'count': row['count'],
                    'avg_confidence': round(row['avg_conf'], 3)
                }
            
            return {
                'total_items': total_items,
                'unique_responses': unique_responses,
                'avg_items_per_response': round(total_items / unique_responses, 2) if unique_responses else 0,
                'avg_confidence': round(avg_confidence, 3),
                'methods': methods
            }
    
    def has_extraction(
        self,
        import_id: int,
        question_id: str,
        column_name: str = None
    ) -> bool:
        """Checks whether items exist for a question."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT 1 FROM response_items
                WHERE import_id = ? AND question_id = ?
            """
            params = [import_id, question_id]
            
            if column_name:
                query += " AND column_name = ?"
                params.append(column_name)
            
            query += " LIMIT 1"
            
            cursor.execute(query, params)
            return cursor.fetchone() is not None
    
    def delete_items(
        self,
        import_id: int,
        question_id: str,
        column_name: str = None
    ):
        """
        Deletes the items of a question (for a new extraction).
        
        Args:
            import_id: import id
            question_id: question id
            column_name: optional: only the items of this column
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            if column_name:
                cursor.execute("""
                    DELETE FROM response_items
                    WHERE import_id = ? AND question_id = ? AND column_name = ?
                """, (import_id, question_id, column_name))
            else:
                cursor.execute("""
                    DELETE FROM response_items
                    WHERE import_id = ? AND question_id = ?
                """, (import_id, question_id))
            
            logger.info(f"Items deleted: {cursor.rowcount} entries")
    
    def get_raw_responses(self, import_id: int) -> List[Dict]:
        """
        Loads all raw responses of an import.
        
        Args:
            import_id: import id
        
        Returns:
            list of response dictionaries
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, row_index, data_json, detected_language
                FROM raw_responses
                WHERE import_id = ?
                ORDER BY row_index
            """, (import_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # EXTRACTION JOBS
    # =========================================================================
    
    def create_extraction_job(
        self,
        import_id: int,
        question_id: str,
        column_name: str
    ) -> int:
        """
        Creates a new extraction job.
        
        Args:
            import_id: import id
            question_id: question id
            column_name: column name
        
        Returns:
            job id
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            
            # delete the previous job if present
            cursor.execute("""
                DELETE FROM extraction_jobs
                WHERE import_id = ? AND question_id = ? AND column_name = ?
            """, (import_id, question_id, column_name))
            
            cursor.execute("""
                INSERT INTO extraction_jobs (import_id, question_id, column_name, status)
                VALUES (?, ?, ?, 'pending')
            """, (import_id, question_id, column_name))
            
            return cursor.lastrowid
    
    def update_extraction_job(
        self,
        job_id: int,
        status: str = None,
        total: int = None,
        processed: int = None,
        items_extracted: int = None,
        method_stats: Dict = None,
        error: str = None
    ):
        """
        Updates an extraction job.
        
        Args:
            job_id: job id
            status: new status
            total: total number of responses
            processed: responses processed
            items_extracted: items extracted
            method_stats: statistics per method
            error: error message
        """
        updates = []
        params = []
        
        if status:
            updates.append("status = ?")
            params.append(status)
            if status == 'running':
                updates.append("started_at = CURRENT_TIMESTAMP")
            elif status in ('completed', 'failed'):
                updates.append("completed_at = CURRENT_TIMESTAMP")
        
        if total is not None:
            updates.append("total_responses = ?")
            params.append(total)
        
        if processed is not None:
            updates.append("processed_responses = ?")
            params.append(processed)
        
        if items_extracted is not None:
            updates.append("total_items_extracted = ?")
            params.append(items_extracted)
        
        if method_stats is not None:
            updates.append("method_stats_json = ?")
            params.append(json.dumps(method_stats))
        
        if error:
            updates.append("error_message = ?")
            params.append(error)
        
        if not updates:
            return
        
        params.append(job_id)
        
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE extraction_jobs SET {', '.join(updates)} WHERE id = ?",
                params
            )
    
    def get_extraction_job(
        self,
        import_id: int,
        question_id: str,
        column_name: str = None
    ) -> Optional[Dict]:
        """Loads an extraction job."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            if column_name:
                cursor.execute("""
                    SELECT * FROM extraction_jobs
                    WHERE import_id = ? AND question_id = ? AND column_name = ?
                """, (import_id, question_id, column_name))
            else:
                cursor.execute("""
                    SELECT * FROM extraction_jobs
                    WHERE import_id = ? AND question_id = ?
                    ORDER BY id DESC LIMIT 1
                """, (import_id, question_id))
            
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get('method_stats_json'):
                    result['method_stats'] = json.loads(result['method_stats_json'])
                return result
            return None
    
    def get_extraction_status(self, import_id: int) -> List[Dict]:
        """
        Status of all extraction jobs of an import.
        
        Args:
            import_id: import id
        
        Returns:
            list of job status dictionaries
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    j.*,
                    (SELECT COUNT(*) FROM response_items i 
                     WHERE i.import_id = j.import_id 
                     AND i.question_id = j.question_id
                     AND i.column_name = j.column_name) as actual_items
                FROM extraction_jobs j
                WHERE j.import_id = ?
                ORDER BY j.question_id, j.column_name
            """, (import_id,))
            
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get('method_stats_json'):
                    result['method_stats'] = json.loads(result['method_stats_json'])
                results.append(result)
            return results
    
    # =========================================================================
    # STATISTICS
    # =========================================================================
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Returns database statistics."""
        with self._connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # base tables
            for table in ['projects', 'imports', 'raw_responses', 
                         'analysis_results', 'translations']:
                cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                stats[table] = cursor.fetchone()['count']
            
            # item extraction tables (if present)
            try:
                cursor.execute("SELECT COUNT(*) as count FROM response_items")
                stats['response_items'] = cursor.fetchone()['count']
            except:
                stats['response_items'] = 0
            
            try:
                cursor.execute("SELECT COUNT(*) as count FROM extraction_jobs")
                stats['extraction_jobs'] = cursor.fetchone()['count']
            except:
                stats['extraction_jobs'] = 0
            
            # file size
            stats['file_size_mb'] = self.db_path.stat().st_size / (1024 * 1024)
            
            # Schema-Version
            cursor.execute("SELECT value FROM meta WHERE key = 'schema_version'")
            row = cursor.fetchone()
            stats['schema_version'] = int(row['value']) if row else None
            
            return stats


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

_default_db: Optional[SurveyDatabase] = None


def get_database(db_path: str | Path = None) -> SurveyDatabase:
    """
    Returns the default database instance.
    
    Args:
        db_path: optional path (relevant only for the first call)
    
    Returns:
        SurveyDatabase instance
    """
    global _default_db
    if _default_db is None:
        if db_path is None:
            db_path = Path(__file__).parent.parent / "outputs" / "survey_analyzer.db"
        _default_db = SurveyDatabase(db_path)
    return _default_db


# =============================================================================
# Translations of cluster and theme texts (schema v8)
# =============================================================================
# translations_json holds texts in output languages other than the source
# language: {"fr": {"name": "...", "description": "..."}, "it": {...}}.
# Callers work with flat keys such as name_fr or description_it.

def translations_from_flat(flat: dict, fields=("name", "description")) -> Optional[str]:
    """{'name_fr': 'x', 'description_it': 'y'} -> JSON for translations_json."""
    data: Dict[str, Dict[str, str]] = {}
    for key, value in (flat or {}).items():
        if not value:
            continue
        for field_name in fields:
            prefix = f"{field_name}_"
            if key.startswith(prefix) and len(key) - len(prefix) in (2, 3):
                data.setdefault(key[len(prefix):], {})[field_name] = value
    return json.dumps(data, ensure_ascii=False) if data else None


def translations_to_flat(raw: Optional[str], fields=("name", "description")) -> dict:
    """JSON from translations_json -> {'name_fr': 'x', ...} for every stored language."""
    if not raw:
        return {}
    flat = {}
    for lang, values in json.loads(raw).items():
        for field_name in fields:
            if values.get(field_name):
                flat[f"{field_name}_{lang}"] = values[field_name]
    return flat


def merge_translations(raw: Optional[str], flat_updates: dict,
                       fields=("name", "description")) -> Optional[str]:
    """Apply flat updates (None = unchanged, '' = remove) to stored JSON."""
    data = json.loads(raw) if raw else {}
    for key, value in flat_updates.items():
        if value is None:
            continue
        for field_name in fields:
            prefix = f"{field_name}_"
            if key.startswith(prefix):
                lang = key[len(prefix):]
                if value:
                    data.setdefault(lang, {})[field_name] = value
                else:
                    data.get(lang, {}).pop(field_name, None)
                    if lang in data and not data[lang]:
                        del data[lang]
    return json.dumps(data, ensure_ascii=False) if data else None
