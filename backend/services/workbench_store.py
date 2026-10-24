# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Workbench functions of the survey analysis tool: manual editing of clusters,
items and assignments.
"""

import json
import logging
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import gradio as gr
import pandas as pd

from core import state
from utils.persistence import merge_translations, translations_from_flat, translations_to_flat
from services.ui_values import ALL
from services.ui_tables import ITEMS, SIMILAR_ITEMS, cluster_editor_table

# Value of the "apply" column of the normalisation table; any of APPLY_NO skips a group
APPLY_YES = "Yes"
APPLY_NO = ("nein", "n", "no", "false", "0", "")

logger = logging.getLogger(__name__)


# =============================================================================
# Database helpers
# =============================================================================

def _get_db():
    """Returns the database instance."""
    if state.db is None:
        raise ValueError("No database connected")
    return state.db


def _get_import_id() -> int:
    """Returns the current import id."""
    if state.import_id is None:
        raise ValueError("No import active")
    return state.import_id


# =============================================================================
# Managing clusters
# =============================================================================

def get_freetext_questions() -> list[str]:
    """
    Returns all free-text questions.
    
    Returns:
        list of question ids
    """
    if state.config is None:
        return []
    
    freetext_questions = []
    for qid, qconfig in state.config.get('question_groups', {}).items():
        if qconfig.get('type') == 'freetext':
            freetext_questions.append(qid)
    
    return freetext_questions


def get_clusters_for_question(question_id: str) -> list[dict]:
    """
    Loads all clusters of a free-text question.
    
    Reads freetext_clusters first (edited clusters); if empty, extracts them
    from result_json and stores them in freetext_clusters. Also updates the
    cluster_id in response_items.
    
    Args:
        question_id: id of the free-text question
    
    Returns:
        list of cluster dicts with all fields
    """
    db = _get_db()
    import_id = _get_import_id()
    
    logger.info(f"get_clusters_for_question: {question_id}, import_id={import_id}")
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # first find the analysis_id and result_json
        cursor.execute("""
            SELECT id, result_json FROM analysis_results 
            WHERE import_id = ? AND question_id = ? AND segment_type = 'all'
            ORDER BY analyzed_at DESC LIMIT 1
        """, (import_id, question_id))
        
        row = cursor.fetchone()
        if not row:
            logger.warning(f"No analysis found for {question_id}")
            return []
        
        analysis_id = row[0]
        result_json = row[1]
        
        logger.info(f"Analysis found: id={analysis_id}, result_json length={len(result_json) if result_json else 0}")
        
        # are there clusters in freetext_clusters (for THIS analysis_id)?
        cursor.execute("""
            SELECT COUNT(*) FROM freetext_clusters WHERE analysis_id = ?
        """, (analysis_id,))
        cluster_count = cursor.fetchone()[0]
        
        logger.info(f"Existing clusters for analysis_id={analysis_id}: {cluster_count}")
        
        # no clusters for this analysis_id: extract them from result_json and store them
        if cluster_count == 0 and result_json:
            try:
                result_data = json.loads(result_json)
                
                logger.info(f"result_data keys: {result_data.keys()}")
                
                # extract the clusters from the possible sources, in this order:
                #   1. metadata.cluster_details (most reliable),
                #   2. clusters (if it is a dict/list, not a string),
                #   3. data (fallback)
                
                cluster_details = []
                
                # option 1: metadata.cluster_details (preferred, holds all details)
                metadata = result_data.get('metadata', {})
                if metadata.get('cluster_details'):
                    cluster_details = metadata.get('cluster_details', [])
                    logger.info(f"Clusters from 'metadata.cluster_details': {len(cluster_details)}")
                
                # option 2: directly under 'clusters' (only if it is a list/dict, not a string)
                if not cluster_details and result_data.get('clusters'):
                    clusters_raw = result_data.get('clusters')
                    if isinstance(clusters_raw, (list, dict)) and not isinstance(clusters_raw, str):
                        # a dict with a 'clusters' key (sometimes nested)
                        if isinstance(clusters_raw, dict) and 'clusters' in clusters_raw:
                            cluster_details = clusters_raw.get('clusters', [])
                        elif isinstance(clusters_raw, list):
                            cluster_details = clusters_raw
                        logger.info(f"Clusters from the 'clusters' key: {len(cluster_details)}")
                    else:
                        logger.info(f"'clusters' is no valid format: {type(clusters_raw)}")
                
                logger.info(f"cluster_details count: {len(cluster_details)}")
                
                if cluster_details:
                    logger.info(f"First cluster: {list(cluster_details[0].keys()) if isinstance(cluster_details[0], dict) else 'N/A'}")
                
                # fallback: directly from data (cluster names as keys)
                if not cluster_details and result_data.get('data'):
                    data = result_data.get('data', {})
                    if isinstance(data, dict):
                        logger.info(f"Fallback: creating clusters from data, keys: {list(data.keys())}")
                        cluster_details = [
                            {'name': name, 'count': _count_of(count), 'description': '', 'examples': [], 'answer_ids': []}
                            for name, count in data.items()
                        ]
                
                # delete the old clusters of this question_id (from earlier analyses)
                cursor.execute("""
                    DELETE FROM freetext_clusters 
                    WHERE analysis_id IN (
                        SELECT id FROM analysis_results 
                        WHERE import_id = ? AND question_id = ? AND id != ?
                    )
                """, (import_id, question_id, analysis_id))
                deleted_old = cursor.rowcount
                if deleted_old > 0:
                    logger.info(f"Old clusters deleted: {deleted_old}")
                
                # store the clusters in the table and update the item assignment
                from utils import prompts
                from utils.languages import localized
                src = prompts.source_language()
                for idx, cluster_data in enumerate(cluster_details):
                    # the name may be a string or a dict with language versions
                    name_raw = cluster_data.get('name', f'Cluster {idx+1}')
                    if isinstance(name_raw, dict):
                        # {lang: text}; the source language goes into cluster_name
                        name_source = localized(name_raw, src, src) or f'Cluster {idx+1}'
                        name_fr = name_raw.get('fr', '') if src != 'fr' else ''
                        name_en = name_raw.get('en', '') if src != 'en' else ''
                    else:
                        # name as plain text in the source language
                        name_source = name_raw
                        name_fr = cluster_data.get('name_fr', '')
                        name_en = cluster_data.get('name_en', '')
                    
                    # the description may be a dict too
                    desc_raw = cluster_data.get('description', '')
                    if isinstance(desc_raw, dict):
                        description_source = localized(desc_raw, src, src) or ''
                        description_fr = desc_raw.get('fr', '') if src != 'fr' else ''
                        description_en = desc_raw.get('en', '') if src != 'en' else ''
                    else:
                        description_source = desc_raw
                        description_fr = cluster_data.get('description_fr', '')
                        description_en = cluster_data.get('description_en', '')
                    
                    answer_ids = cluster_data.get('answer_ids', [])
                    
                    # examples: originals and translations
                    examples = cluster_data.get('examples', [])
                    examples_translated = cluster_data.get('examples_translated', {})
                    # store the examples as JSON (with translations)
                    # {lang: [...]}: originals under the source language, translations under theirs
                    examples_data = {src: examples,
                                     **{k: v for k, v in (examples_translated or {}).items() if k != src and v}}
                    
                    logger.info(f"Storing cluster '{name_source}' with {len(answer_ids)} answer_ids, "
                               f"examples per language: {({k: len(v) for k, v in examples_data.items()})}")
                    
                    cursor.execute("""
                        INSERT INTO freetext_clusters (
                            analysis_id, cluster_name, description_source, translations_json,
                            response_count, response_ids_json, examples_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        analysis_id,
                        name_source,
                        description_source,
                        translations_from_flat({
                            'name_fr': name_fr, 'name_en': name_en,
                            'description_fr': description_fr, 'description_en': description_en,
                            # name/description may be plain text or {lang: text}; only dicts carry further languages
                            **{f"name_{k}": v for k, v in _as_dict(cluster_data.get('name')).items()
                               if k not in (src, 'fr', 'en')},
                            **{f"description_{k}": v for k, v in _as_dict(cluster_data.get('description')).items()
                               if k not in (src, 'fr', 'en')},
                        }),
                        cluster_data.get('count', 0),
                        json.dumps(answer_ids),
                        json.dumps(examples_data)  # now with all languages
                    ))
                    
                    cluster_id = cursor.lastrowid
                    
                    # assign the items to this cluster
                    if answer_ids:
                        # first remove the old cluster_id for this question
                        if idx == 0:  # only for the first cluster
                            cursor.execute("""
                                UPDATE response_items 
                                SET cluster_id = NULL
                                WHERE import_id = ? AND question_id = ?
                            """, (import_id, question_id))
                            logger.info(f"  → old cluster_ids reset")
                        
                        placeholders = ','.join('?' * len(answer_ids))
                        cursor.execute(f"""
                            UPDATE response_items 
                            SET cluster_id = ?
                            WHERE import_id = ? AND question_id = ? AND id IN ({placeholders})
                        """, [cluster_id, import_id, question_id] + answer_ids)
                        updated = cursor.rowcount
                        logger.info(f"  → {updated} items assigned (of {len(answer_ids)} IDs)")
                
                conn.commit()
                logger.info(f"Clusters for {question_id} created from result_json: {len(cluster_details)} clusters")
                
                # update the cluster counts from response_items
                _update_cluster_counts(question_id)
                
            except (json.JSONDecodeError, Exception) as e:
                logger.exception(f"Could not extract the clusters from result_json: {e}")
        
        # now load the clusters from the table
        cursor.execute("""
            SELECT 
                id, cluster_name, translations_json, NULL,
                description_source, NULL, NULL,
                response_count, response_ids_json, examples_json,
                is_manual, updated_at
            FROM freetext_clusters
            WHERE analysis_id = ?
            ORDER BY response_count DESC
        """, (analysis_id,))
        
        clusters = []
        from utils import prompts as _prompts
        src = _prompts.source_language()
        for row in cursor.fetchall():
            # Examples: {lang: [...]}, originals under the source language
            examples_raw = json.loads(row[9]) if row[9] else {}
            examples_src = examples_raw.get(src) or []
            examples_by_lang = {k: v for k, v in examples_raw.items() if v}
            
            translated = translations_to_flat(row[2])
            # Texts per language: the source columns under the source language,
            # stored translations under the others. The keys name_<lang> /
            # description_<lang> / examples_<lang> mean that language; for a
            # for a German survey, 'name_de' is the source text itself
            names = {src: row[1] or '', **{k[5:]: v for k, v in translated.items()
                                           if k.startswith('name_') and k[5:] != src}}
            descriptions = {src: row[4] or '', **{k[12:]: v for k, v in translated.items()
                                                  if k.startswith('description_') and k[12:] != src}}
            per_lang = {}
            for lang in set(names) | set(descriptions) | set(examples_by_lang) | {'de', 'fr', 'en'}:
                per_lang[f'name_{lang}'] = names.get(lang, '')
                per_lang[f'description_{lang}'] = descriptions.get(lang, '')
                per_lang[f'examples_{lang}'] = examples_by_lang.get(lang, [])
            clusters.append({
                **per_lang,
                'name_source': row[1] or '',
                'description_source': row[4] or '',
                'names': names,
                'descriptions': descriptions,
                'examples_by_lang': examples_by_lang,
                'translations_flat': translated,
                'id': row[0],
                'count': row[7] or 0,
                'item_ids': json.loads(row[8]) if row[8] else [],
                'examples': examples_src,  # originals (source language)
                'is_manual': bool(row[10]) if row[10] is not None else False,
                'updated_at': row[11]
            })
        
        logger.info(f"Returning {len(clusters)} clusters")
        return clusters


def update_cluster(
    cluster_id: int,
    name_fr: str = None,
    name_en: str = None,
    description_fr: str = None,
    description_en: str = None,
    translations: Optional[dict] = None,
    name_source: str = None,
    description_source: str = None,
) -> bool:
    """
    Updates a cluster.
    
    Args:
        cluster_id: database id of the cluster
        name_source / description_source: texts in the survey's source language
            (columns cluster_name / description_source).
        name_fr, name_en, description_fr, description_en, translations:
            stored translations (``{'name_it': ...}``); None = unchanged,
            '' removes the translation
    
    Returns:
        True on success
    """
    db = _get_db()
    
    # build the UPDATE statement dynamically
    updates = []
    params = []
    
    if name_source is not None:
        updates.append("cluster_name = ?")
        params.append(name_source)
    flat_updates = {
        'name_fr': name_fr, 'name_en': name_en,
        'description_fr': description_fr, 'description_en': description_en,
        **(translations or {}),   # further languages, e.g. {'name_it': '...'}
    }
    if description_source is not None:
        updates.append("description_source = ?")
        params.append(description_source)
    if any(v is not None for v in flat_updates.values()):
        with db._connection() as conn:
            row = conn.execute(
                "SELECT translations_json FROM freetext_clusters WHERE id = ?", (cluster_id,)
            ).fetchone()
        updates.append("translations_json = ?")
        params.append(merge_translations(row[0] if row else None, flat_updates))
    
    if not updates:
        return True  # nothing to do
    
    # add the timestamp and the manual flag
    updates.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    updates.append("is_manual = ?")
    params.append(True)
    
    params.append(cluster_id)
    
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE freetext_clusters SET {', '.join(updates)} WHERE id = ?",
            params
        )
        
        logger.info(f"Cluster {cluster_id} updated")
        return cursor.rowcount > 0


def create_cluster(
    question_id: str,
    name_source: str,
    name_fr: str = None,
    name_en: str = None,
    description_source: str = None
) -> int:
    """
    Creates a new (empty) cluster.
    
    Args:
        question_id: question id
        name_source: name in the source language
        name_fr: French name (optional)
        name_en: English name (optional)
        description_source: description in the source language (optional)
    
    Returns:
        id of the new cluster
    """
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # find the analysis id
        cursor.execute("""
            SELECT id FROM analysis_results 
            WHERE import_id = ? AND question_id = ? AND segment_type = 'all'
            ORDER BY analyzed_at DESC LIMIT 1
        """, (import_id, question_id))
        
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"No analysis found for {question_id}")
        
        analysis_id = row[0]
        
        # create the new cluster
        cursor.execute("""
            INSERT INTO freetext_clusters 
            (analysis_id, cluster_name, translations_json,
             description_source, response_count, is_manual, updated_at)
            VALUES (?, ?, ?, ?, 0, TRUE, ?)
        """, (
            analysis_id, name_source,
            translations_from_flat({'name_fr': name_fr, 'name_en': name_en}),
            description_source,
            datetime.now().isoformat()
        ))
        
        new_id = cursor.lastrowid
        logger.info(f"New cluster created: {new_id} ({name_source})")
        return new_id


def delete_cluster(cluster_id: int, reassign_to: int = None) -> bool:
    """
    Deletes a cluster.
    
    Args:
        cluster_id: cluster to delete
        reassign_to: optional: move the items to this cluster
    
    Returns:
        True on success
    """
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # move the items if wanted
        if reassign_to:
            cursor.execute("""
                UPDATE response_items SET cluster_id = ? 
                WHERE cluster_id = ?
            """, (reassign_to, cluster_id))
        else:
            # leave the items without a cluster
            cursor.execute("""
                UPDATE response_items SET cluster_id = NULL 
                WHERE cluster_id = ?
            """, (cluster_id,))
        
        # delete the cluster
        cursor.execute("DELETE FROM freetext_clusters WHERE id = ?", (cluster_id,))
        
        logger.info(f"Cluster {cluster_id} deleted")
        return cursor.rowcount > 0


# =============================================================================
# Editing items
# =============================================================================

def get_items_for_question(
    question_id: str,
    cluster_id: int = None,
    search_text: str = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 50
) -> tuple[list[dict], int]:
    """
    Loads items with pagination and filters.
    
    Args:
        question_id: question id
        cluster_id: optional: only the items of this cluster
        search_text: optional: text search
        include_deleted: show deleted items too
        page: page number (1-based)
        page_size: items per page
    
    Returns:
        (list of item dicts, total count)
    """
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # check which columns exist
        cursor.execute("PRAGMA table_info(response_items)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        
        has_is_deleted = 'is_deleted' in existing_columns
        has_cluster_id = 'cluster_id' in existing_columns
        has_edited_at = 'edited_at' in existing_columns
        
        # Basis-Query
        where_clauses = ["ri.import_id = ?", "ri.question_id = ?"]
        params = [import_id, question_id]
        
        if has_is_deleted and not include_deleted:
            where_clauses.append("(ri.is_deleted IS NULL OR ri.is_deleted = FALSE)")
        
        if has_cluster_id and cluster_id is not None:
            where_clauses.append("ri.cluster_id = ?")
            params.append(cluster_id)
        
        if search_text:
            where_clauses.append("(ri.extracted_text LIKE ? OR ri.original_text LIKE ?)")
            params.extend([f"%{search_text}%", f"%{search_text}%"])
        
        where_sql = " AND ".join(where_clauses)
        
        # total count
        cursor.execute(
            f"SELECT COUNT(*) FROM response_items ri WHERE {where_sql}",
            params
        )
        total_count = cursor.fetchone()[0]
        
        # load the items, choosing the columns dynamically
        if has_cluster_id:
            select_cols = """
                ri.id, ri.original_text, ri.extracted_text, ri.normalized_text,
                ri.cluster_id, fc.cluster_name
            """
            join_clause = "LEFT JOIN freetext_clusters fc ON ri.cluster_id = fc.id"
        else:
            select_cols = """
                ri.id, ri.original_text, ri.extracted_text, ri.normalized_text,
                NULL as cluster_id, NULL as cluster_name
            """
            join_clause = ""
        
        if has_is_deleted:
            select_cols += ", ri.is_deleted"
        else:
            select_cols += ", NULL as is_deleted"
            
        if has_edited_at:
            select_cols += ", ri.edited_at"
        else:
            select_cols += ", NULL as edited_at"
        
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT {select_cols}
            FROM response_items ri
            {join_clause}
            WHERE {where_sql}
            ORDER BY ri.id
            LIMIT ? OFFSET ?
        """, params + [page_size, offset])
        
        items = []
        for row in cursor.fetchall():
            items.append({
                'id': row[0],
                'original_text': row[1] or '',
                'extracted_text': row[2] or '',
                'normalized_text': row[3] or '',
                'cluster_id': row[4],
                'cluster_name': row[5] or '(no cluster)',
                'is_deleted': bool(row[6]) if row[6] is not None else False,
                'edited_at': row[7]
            })
        
        return items, total_count


def update_item_text(item_id: int, new_text: str) -> bool:
    """
    Updates the extracted text of an item.
    Keeps the original as a backup on the first edit (if the column exists).
    
    Args:
        item_id: item id
        new_text: new text
    
    Returns:
        True on success
    """
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # check which columns exist
        cursor.execute("PRAGMA table_info(response_items)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        
        has_original_extracted = 'original_extracted_text' in existing_columns
        has_edited_at = 'edited_at' in existing_columns
        
        # load the current text
        cursor.execute(
            "SELECT extracted_text FROM response_items WHERE id = ?",
            (item_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        
        current_text = row[0]
        
        # build the update
        if has_original_extracted and has_edited_at:
            # is there a backup already?
            cursor.execute(
                "SELECT original_extracted_text FROM response_items WHERE id = ?",
                (item_id,)
            )
            backup = cursor.fetchone()[0]
            
            if backup is None:
                cursor.execute("""
                    UPDATE response_items 
                    SET extracted_text = ?, original_extracted_text = ?, edited_at = ?
                    WHERE id = ?
                """, (new_text, current_text, datetime.now().isoformat(), item_id))
            else:
                cursor.execute("""
                    UPDATE response_items 
                    SET extracted_text = ?, edited_at = ?
                    WHERE id = ?
                """, (new_text, datetime.now().isoformat(), item_id))
        else:
            # simple update without backup columns
            cursor.execute("""
                UPDATE response_items 
                SET extracted_text = ?
                WHERE id = ?
            """, (new_text, item_id))
        
        logger.info(f"Item {item_id} updated: '{current_text}' → '{new_text}'")
        return True


def delete_items(item_ids: list[int], hard_delete: bool = False) -> int:
    """
    Deletes items (soft or hard delete).
    
    Args:
        item_ids: ids of the items to delete
        hard_delete: True = delete for good, False = soft delete
    
    Returns:
        number of deleted items
    """
    if not item_ids:
        return 0
    
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(item_ids))
        
        if hard_delete:
            cursor.execute(
                f"DELETE FROM response_items WHERE id IN ({placeholders})",
                item_ids
            )
        else:
            # check whether the columns exist
            cursor.execute("PRAGMA table_info(response_items)")
            existing_columns = {row[1] for row in cursor.fetchall()}
            
            if 'is_deleted' in existing_columns and 'deleted_at' in existing_columns:
                cursor.execute(f"""
                    UPDATE response_items 
                    SET is_deleted = TRUE, deleted_at = ?
                    WHERE id IN ({placeholders})
                """, [datetime.now().isoformat()] + item_ids)
            else:
                # fallback: hard delete if a soft delete is not possible
                cursor.execute(
                    f"DELETE FROM response_items WHERE id IN ({placeholders})",
                    item_ids
                )
        
        count = cursor.rowcount
        logger.info(f"{count} items {'deleted' if hard_delete else 'marked as deleted'}")
        return count


def restore_items(item_ids: list[int]) -> int:
    """
    Restores soft-deleted items.
    
    Args:
        item_ids: ids of the items to restore
    
    Returns:
        number of restored items
    """
    if not item_ids:
        return 0
    
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # check whether the columns exist
        cursor.execute("PRAGMA table_info(response_items)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        
        if 'is_deleted' not in existing_columns:
            logger.warning("restore_items: the column is_deleted does not exist")
            return 0
        
        placeholders = ','.join('?' * len(item_ids))
        cursor.execute(f"""
            UPDATE response_items 
            SET is_deleted = FALSE, deleted_at = NULL
            WHERE id IN ({placeholders})
        """, item_ids)
        
        count = cursor.rowcount
        logger.info(f"{count} items restored")
        return count


# =============================================================================
# Merging/splitting items
# =============================================================================

def merge_items(
    item_ids: list[int],
    merged_text: str,
    question_id: str
) -> int:
    """
    Merges several items.
    
    Args:
        item_ids: ids of the items to merge
        merged_text: text of the merged item
        question_id: question id (for the history)
    
    Returns:
        id of the new item (or of the first item if done in place)
    """
    if len(item_ids) < 2:
        raise ValueError("At least 2 items are needed to merge")
    
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # keep and update the first item
        first_id = item_ids[0]
        other_ids = item_ids[1:]
        
        # update the first item
        cursor.execute("""
            UPDATE response_items 
            SET extracted_text = ?, edited_at = ?
            WHERE id = ?
        """, (merged_text, datetime.now().isoformat(), first_id))
        
        # mark the other items as deleted
        placeholders = ','.join('?' * len(other_ids))
        cursor.execute(f"""
            UPDATE response_items 
            SET is_deleted = TRUE, deleted_at = ?
            WHERE id IN ({placeholders})
        """, [datetime.now().isoformat()] + other_ids)
        
        # store the history
        cursor.execute("""
            INSERT INTO item_history 
            (import_id, question_id, action_type, source_item_ids, target_item_ids, result_text)
            VALUES (?, ?, 'merge', ?, ?, ?)
        """, (
            import_id, question_id,
            json.dumps(item_ids),
            json.dumps([first_id]),
            merged_text
        ))
        
        logger.info(f"Items {item_ids} merged into '{merged_text}'")
        return first_id


def split_item(
    item_id: int,
    new_texts: list[str],
    question_id: str
) -> list[int]:
    """
    Splits an item into several.
    
    Args:
        item_id: id of the item to split
        new_texts: list of the new texts
        question_id: question id (for the history)
    
    Returns:
        ids of the new items
    """
    if len(new_texts) < 2:
        raise ValueError("At least 2 texts are needed to split")
    
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # Original-Item laden
        cursor.execute("""
            SELECT row_index, column_name, original_text, cluster_id
            FROM response_items WHERE id = ?
        """, (item_id,))
        
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Item {item_id} not found")
        
        row_index, column_name, original_text, cluster_id = row
        
        # mark the original as deleted
        cursor.execute("""
            UPDATE response_items 
            SET is_deleted = TRUE, deleted_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), item_id))
        
        # create the new items
        new_ids = []
        for i, text in enumerate(new_texts):
            cursor.execute("""
                INSERT INTO response_items 
                (import_id, row_index, question_id, column_name, item_index,
                 original_text, extracted_text, extraction_method, cluster_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manual_split', ?)
            """, (
                import_id, row_index, question_id, column_name,
                1000 + i,  # high index to avoid conflicts
                original_text, text.strip(), cluster_id
            ))
            new_ids.append(cursor.lastrowid)
        
        # store the history
        cursor.execute("""
            INSERT INTO item_history 
            (import_id, question_id, action_type, source_item_ids, target_item_ids, result_text)
            VALUES (?, ?, 'split', ?, ?, ?)
        """, (
            import_id, question_id,
            json.dumps([item_id]),
            json.dumps(new_ids),
            '; '.join(new_texts)
        ))
        
        logger.info(f"Item {item_id} split into {len(new_texts)} items: {new_ids}")
        return new_ids


# =============================================================================
# Changing the cluster assignment
# =============================================================================

def reassign_items_to_cluster(
    item_ids: list[int],
    target_cluster_id: int,
    question_id: str
) -> int:
    """
    Moves items to another cluster.
    
    Args:
        item_ids: ids of the items
        target_cluster_id: target cluster (None = no cluster)
        question_id: question id (for the history)
    
    Returns:
        number of moved items
    """
    if not item_ids:
        return 0
    
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(item_ids))
        cursor.execute(f"""
            UPDATE response_items 
            SET cluster_id = ?
            WHERE id IN ({placeholders})
        """, [target_cluster_id] + item_ids)
        
        count = cursor.rowcount
        
        # store the history
        cursor.execute("""
            INSERT INTO item_history 
            (import_id, question_id, action_type, source_item_ids, details_json)
            VALUES (?, ?, 'reassign', ?, ?)
        """, (
            import_id, question_id,
            json.dumps(item_ids),
            json.dumps({'target_cluster_id': target_cluster_id})
        ))
        
        logger.info(f"{count} items moved to cluster {target_cluster_id}")
        return count


def get_unassigned_items(question_id: str) -> list[dict]:
    """
    Loads all items without a cluster.
    
    Args:
        question_id: question id
    
    Returns:
        list of item dicts
    """
    items, _ = get_items_for_question(
        question_id,
        cluster_id=None,
        page_size=1000
    )
    return [i for i in items if i['cluster_id'] is None]


# =============================================================================
# Translations
# =============================================================================

def get_translations(
    source_lang: str = "de",
    target_lang: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 50
) -> tuple[list[dict], int]:
    """
    Loads translations with pagination.
    
    Args:
        source_lang: source language
        target_lang: target language (None = all)
        search: search text
        page: page number
        page_size: entries per page
    
    Returns:
        (list of translation dicts, total count)
    """
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        where_clauses = ["source_lang = ?"]
        params = [source_lang]
        
        if target_lang:
            where_clauses.append("target_lang = ?")
            params.append(target_lang)
        
        if search:
            where_clauses.append("(source_text LIKE ? OR translated_text LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        
        where_sql = " AND ".join(where_clauses)
        
        # total count
        cursor.execute(
            f"SELECT COUNT(*) FROM translations WHERE {where_sql}",
            params
        )
        total_count = cursor.fetchone()[0]
        
        # load the data
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT id, source_text, source_lang, target_lang, translated_text, created_at
            FROM translations
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset])
        
        translations = []
        for row in cursor.fetchall():
            translations.append({
                'id': row[0],
                'source_text': row[1],
                'source_lang': row[2],
                'target_lang': row[3],
                'translated_text': row[4],
                'created_at': row[5]
            })
        
        return translations, total_count


def update_translation(translation_id: int, new_text: str) -> bool:
    """
    Corrects a translation.
    
    Args:
        translation_id: id of the translation
        new_text: corrected text
    
    Returns:
        True on success
    """
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE translations SET translated_text = ?, is_manual = 1 WHERE id = ?",
            (new_text, translation_id)
        )
        return cursor.rowcount > 0


def delete_translations(translation_ids: list[int]) -> int:
    """
    Deletes translations from the cache.
    
    Args:
        translation_ids: ids of the translations to delete
    
    Returns:
        number of deleted entries
    """
    if not translation_ids:
        return 0
    
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        placeholders = ','.join('?' * len(translation_ids))
        cursor.execute(
            f"DELETE FROM translations WHERE id IN ({placeholders})",
            translation_ids
        )
        return cursor.rowcount


# =============================================================================
# Reclustering with given categories
# =============================================================================

def recluster_with_categories(
    question_id: str,
    category_names: list[str],
    allow_new_categories: bool = True,
    dry_run: bool = False
) -> dict:
    """
    Clusters items with given categories.
    
    Args:
        question_id: question whose items are clustered
        category_names: list of the given category names
        allow_new_categories: the LLM may suggest new categories
        dry_run: preview only, do not save
    
    Returns:
        {
            'assignments': [{item_id, item_text, category, confidence}, ...],
            'new_categories': [name, ...],
            'stats': {category: count, ...}
        }
    """
    from utils import prompts
    from utils.llm import get_llm_client
    
    # Items laden
    items, total = get_items_for_question(question_id, page_size=500)
    
    if not items:
        return {'assignments': [], 'new_categories': [], 'stats': {}}
    
    # build the prompt (catalog of the source language)
    lang = prompts.source_language()
    other_category = prompts.prompt('recluster.other_category', lang)
    categories_text = "\n".join(f"{i+1}. {name}" for i, name in enumerate(category_names))
    
    items_text = "\n".join(
        f"[{item['id']}] {item['extracted_text']}"
        for item in items
    )
    
    fallback_rule = (
        prompts.prompt('recluster.rule_other', lang, other=other_category)
        if not allow_new_categories else
        prompts.prompt('recluster.rule_new', lang)
    )
    
    prompt = prompts.prompt('recluster.prompt', lang, categories=categories_text,
                            items=items_text, fallback_rule=fallback_rule)

    # call the LLM
    response = get_llm_client().complete(prompt, temperature=0.6)
    
    # parse the response
    try:
        # extract the JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            result = json.loads(json_match.group())
        else:
            raise ValueError("No JSON found in the answer")
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        logger.error(f"LLM answer: {response}")
        raise ValueError(f"The LLM answer could not be parsed: {e}")
    
    assignments = result.get('assignments', [])
    new_categories = result.get('new_categories', [])
    # Without new categories the prompt allows the catch-all category; it
    # must exist as a cluster, otherwise these items cannot be assigned.
    if not allow_new_categories and other_category not in category_names and any(
            a.get('category') == other_category for a in assignments):
        new_categories = list(new_categories) + [other_category]
    
    # add the item texts for the display
    item_map = {item['id']: item['extracted_text'] for item in items}
    for a in assignments:
        a['item_text'] = item_map.get(a['item_id'], '')
    
    # statistics
    stats = {}
    for a in assignments:
        cat = a['category']
        stats[cat] = stats.get(cat, 0) + 1
    
    # save unless dry_run
    if not dry_run:
        apply_recluster_result(question_id, assignments, new_categories, category_names)
    
    return {
        'assignments': assignments,
        'new_categories': new_categories,
        'stats': stats
    }


def apply_recluster_result(
    question_id: str,
    assignments: list[dict],
    new_categories: list[str],
    existing_categories: list[str]
) -> dict:
    """
    Applies the result of the reclustering.
    
    Args:
        question_id: question id
        assignments: list of {item_id, category}
        new_categories: new category names
        existing_categories: existing category names
    
    Returns:
        statistics of the changes
    """
    db = _get_db()
    import_id = _get_import_id()
    
    # build the cluster map (name -> id)
    clusters = get_clusters_for_question(question_id)
    cluster_map = {c['name_source']: c['id'] for c in clusters}
    
    # create the new categories
    for cat_name in new_categories:
        if cat_name not in cluster_map:
            new_id = create_cluster(question_id, cat_name)
            cluster_map[cat_name] = new_id
    
    # also existing categories that are not clusters yet
    for cat_name in existing_categories:
        if cat_name not in cluster_map:
            new_id = create_cluster(question_id, cat_name)
            cluster_map[cat_name] = new_id
    
    # assign the items
    stats = {'reassigned': 0, 'errors': 0}
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        for a in assignments:
            item_id = a['item_id']
            category = a['category']
            cluster_id = cluster_map.get(category)
            
            if cluster_id:
                cursor.execute(
                    "UPDATE response_items SET cluster_id = ? WHERE id = ?",
                    (cluster_id, item_id)
                )
                if cursor.rowcount > 0:
                    stats['reassigned'] += 1
            else:
                stats['errors'] += 1
                logger.warning(f"Cluster '{category}' not found for item {item_id}")
    
    # update the cluster counts
    _update_cluster_counts(question_id)
    
    logger.info(f"Reclustering applied: {stats}")
    return stats


def _update_cluster_counts(question_id: str):
    """Updates the item counts of all clusters of a question.
    
    Item mode: counts from response_items.
    Raw mode: counts from response_ids_json (there are no response_items).
    """
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # all clusters of the question with response_ids_json
        cursor.execute("""
            SELECT fc.id, fc.response_ids_json
            FROM freetext_clusters fc
            JOIN analysis_results ar ON fc.analysis_id = ar.id
            WHERE ar.import_id = ? AND ar.question_id = ?
        """, (import_id, question_id))
        
        rows = cursor.fetchall()
        
        for row in rows:
            cluster_id = row[0]
            response_ids_json = row[1]
            
            # first try to count from response_items (item mode)
            cursor.execute("""
                SELECT COUNT(*) FROM response_items 
                WHERE cluster_id = ? AND (is_deleted IS NULL OR is_deleted = FALSE)
            """, (cluster_id,))
            count = cursor.fetchone()[0]
            
            # without response_items: fall back to the answer_ids from the JSON (raw mode)
            if count == 0 and response_ids_json:
                try:
                    answer_ids = json.loads(response_ids_json)
                    count = len(answer_ids) if isinstance(answer_ids, list) else 0
                except (json.JSONDecodeError, TypeError):
                    count = 0
            
            cursor.execute(
                "UPDATE freetext_clusters SET response_count = ? WHERE id = ?",
                (count, cluster_id)
            )


# =============================================================================
# Normalising items
# =============================================================================

def find_similar_items(
    question_id: str,
    min_similarity: float = 0.8
) -> list[dict]:
    """
    Finds groups of similar items.
    
    Args:
        question_id: question id
        min_similarity: minimum similarity (0-1)
    
    Returns:
        [
            {
                'variants': ['ChatGPT', 'Chat GPT', 'chatgpt'],
                'counts': [8, 5, 3],
                'suggested_canonical': 'ChatGPT',
                'total_count': 16
            },
            ...
        ]
    """
    # Items laden
    items, _ = get_items_for_question(question_id, page_size=1000)
    
    if not items:
        return []
    
    # count text -> frequency
    text_counts = {}
    for item in items:
        text = item['extracted_text'].strip()
        if text:
            text_counts[text] = text_counts.get(text, 0) + 1
    
    unique_texts = list(text_counts.keys())
    
    # calculate the similarities
    def similarity(a: str, b: str) -> float:
        a_lower, b_lower = a.lower(), b.lower()
        if a_lower == b_lower:
            return 1.0
        return SequenceMatcher(None, a_lower, b_lower).ratio()
    
    # grouping
    groups = []
    used = set()
    
    for i, text_a in enumerate(unique_texts):
        if i in used:
            continue
        
        group = [text_a]
        used.add(i)
        
        for j, text_b in enumerate(unique_texts[i+1:], i+1):
            if j in used:
                continue
            
            if similarity(text_a, text_b) >= min_similarity:
                group.append(text_b)
                used.add(j)
        
        if len(group) > 1:
            # sort by frequency
            group_sorted = sorted(group, key=lambda x: text_counts[x], reverse=True)
            counts = [text_counts[t] for t in group_sorted]
            
            groups.append({
                'variants': group_sorted,
                'counts': counts,
                'suggested_canonical': group_sorted[0],  # the most frequent
                'total_count': sum(counts)
            })
    
    # sort by total frequency
    groups.sort(key=lambda x: x['total_count'], reverse=True)
    
    return groups


def apply_normalization(
    question_id: str,
    normalizations: list[dict]
) -> int:
    """
    Applies normalisations.
    
    Args:
        question_id: question id
        normalizations: [{variants: [...], canonical: "..."}]
    
    Returns:
        number of normalised items
    """
    db = _get_db()
    import_id = _get_import_id()
    
    total_normalized = 0
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        for norm in normalizations:
            canonical = norm['canonical']
            variants = norm['variants']
            
            for variant in variants:
                if variant == canonical:
                    continue
                
                # store the normalisation
                cursor.execute("""
                    INSERT OR REPLACE INTO item_normalizations 
                    (import_id, question_id, variant_text, canonical_text)
                    VALUES (?, ?, ?, ?)
                """, (import_id, question_id, variant, canonical))
                
                # update the items
                cursor.execute("""
                    UPDATE response_items 
                    SET normalized_text = ?
                    WHERE import_id = ? AND question_id = ? 
                    AND extracted_text = ?
                    AND (is_deleted IS NULL OR is_deleted = FALSE)
                """, (canonical, import_id, question_id, variant))
                
                total_normalized += cursor.rowcount
        
        logger.info(f"{total_normalized} items normalised")
        return total_normalized


def get_normalizations(question_id: str) -> list[dict]:
    """
    Loads the existing normalisations.
    
    Args:
        question_id: question id
    
    Returns:
        list of {variant_text, canonical_text}
    """
    db = _get_db()
    import_id = _get_import_id()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, variant_text, canonical_text, created_at
            FROM item_normalizations
            WHERE import_id = ? AND question_id = ?
            ORDER BY canonical_text, variant_text
        """, (import_id, question_id))
        
        return [
            {
                'id': row[0],
                'variant_text': row[1],
                'canonical_text': row[2],
                'created_at': row[3]
            }
            for row in cursor.fetchall()
        ]


def remove_normalization(normalization_id: int) -> bool:
    """
    Removes a normalisation.
    
    Args:
        normalization_id: id of the normalisation
    
    Returns:
        True on success
    """
    db = _get_db()
    
    with db._connection() as conn:
        cursor = conn.cursor()
        
        # get the variant text
        cursor.execute(
            "SELECT variant_text, question_id FROM item_normalizations WHERE id = ?",
            (normalization_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        
        variant_text, question_id = row
        
        # delete the normalisation
        cursor.execute(
            "DELETE FROM item_normalizations WHERE id = ?",
            (normalization_id,)
        )
        
        # reset normalized_text in the items
        cursor.execute("""
            UPDATE response_items 
            SET normalized_text = NULL
            WHERE question_id = ? AND extracted_text = ?
        """, (question_id, variant_text))
        
        return True


# =============================================================================
# Gradio interface functions
# =============================================================================

def editor_languages() -> list:
    """Source language first, then the other output languages of the loaded survey."""
    from core import state
    from utils import prompts
    from utils.languages import get_output_languages
    src = prompts.source_language()
    return [src] + [lang for lang in get_output_languages(state.config) if lang != src]


def editor_table():
    """Columns of the cluster editor: a name per language, the description in the source language."""
    return cluster_editor_table(editor_languages())


def editor_columns() -> list:
    """Headers of the cluster editor as shown."""
    return editor_table().labels


def source_name_column() -> str:
    """Editor column (as shown) holding the cluster names in the source language."""
    return dict(editor_table().columns)[f'name_{editor_languages()[0]}']


def load_cluster_editor(question_id: str) -> pd.DataFrame:
    """Loads the cluster data for the editor as a DataFrame (one name column per output language)."""
    table = editor_table()
    if not question_id:
        return table.empty()
    
    clusters = get_clusters_for_question(question_id)
    
    if not clusters:
        return table.empty()
    
    langs = editor_languages()
    data = []
    for c in clusters:
        row = {'id': c['id'], f'description_{langs[0]}': c['description_source'], 'items': c['count']}
        for lang in langs:
            row[f'name_{lang}'] = c['names'].get(lang, '')
        data.append(row)
    
    return table.to_ui(data)


def save_cluster_changes(df: pd.DataFrame, question_id: str) -> str:
    """
    Saves the changes of the cluster editor. The column of the source
    language changes the source name; the other language columns change the
    stored translations (an emptied cell removes the translation).
    """
    if df is None or df.empty:
        return "❌ No data to save"
    
    langs = editor_languages()
    src = langs[0]
    try:
        count = 0
        for _, row in editor_table().from_ui(df).iterrows():
            cluster_id = int(row['id'])
            translations = {}
            name_source = None
            description_source = None
            for col in row.index:
                kind, _, lang = col.partition('_')
                if kind not in ('name', 'description') or not lang:
                    continue
                value = row[col] if pd.notna(row[col]) else None
                if lang == src:
                    if kind == 'name':
                        name_source = value
                    else:
                        description_source = value
                else:
                    translations[f'{kind}_{lang}'] = '' if value is None else str(value)
            update_cluster(cluster_id, name_source=name_source, description_source=description_source,
                           translations=translations)
            count += 1
        
        return f"✅ {count} clusters saved"
    except Exception as e:
        logger.error(f"Error saving: {e}")
        return f"❌ Error: {e}"


def load_items_table(
    question_id: str,
    cluster_filter: str = None,
    search_text: str = ""
) -> pd.DataFrame:
    """Loads the items for the editing table."""
    if not question_id:
        return ITEMS.empty()
    
    cluster_id = None
    if cluster_filter and cluster_filter != ALL:
        # cluster id from the name
        clusters = get_clusters_for_question(question_id)
        for c in clusters:
            if c['name_source'] == cluster_filter:
                cluster_id = c['id']
                break
    
    items, total = get_items_for_question(
        question_id,
        cluster_id=cluster_id,
        search_text=search_text if search_text else None,
        page_size=100
    )
    
    if not items:
        return ITEMS.empty()
    
    return ITEMS.to_ui({
        'id': item['id'],
        'original': item['original_text'][:100] + '...' if len(item['original_text']) > 100 else item['original_text'],
        'extracted': item['extracted_text'],
        'cluster': item['cluster_name'],
    } for item in items)


NO_CLUSTER = '(no cluster)'


def save_items_table(df: pd.DataFrame, question_id: str) -> str:
    """
    Saves the item table of the Workbench: changed texts, and items moved to the cluster
    named in the column "Cluster" (an empty cell or "(no cluster)" removes the
    assignment). A manual move is final: it does not mark the clusters as changed, so a
    re-export with reassignment by the language model leaves it as it is. Nothing is
    saved when a cluster name is unknown.
    """
    if df is None or df.empty:
        return "❌ No data to save"
    rows = ITEMS.from_ui(df)
    clusters = get_clusters_for_question(question_id)
    by_name = {str(c['name_source']).strip().casefold(): c['id'] for c in clusters}
    items, _ = get_items_for_question(question_id, page_size=1_000_000)
    current = {it['id']: it for it in items}
    moves, unknown, texts = {}, [], []
    for _, row in rows.iterrows():
        item_id = int(row['id'])
        item = current.get(item_id)
        if item is None:
            continue
        name = '' if pd.isna(row['cluster']) else str(row['cluster']).strip()
        if name in ('', NO_CLUSTER):
            target = None
        elif name.casefold() in by_name:
            target = by_name[name.casefold()]
        else:
            unknown.append(name)
            continue
        if target != item.get('cluster_id'):
            moves.setdefault(target, []).append(item_id)
        new_text = '' if pd.isna(row['extracted']) else str(row['extracted'])
        if new_text != item.get('extracted_text', ''):
            texts.append((item_id, new_text))
    if unknown:
        valid = ', '.join(sorted(str(c['name_source']) for c in clusters))
        return f"❌ Unknown cluster: {', '.join(sorted(set(unknown)))}. Valid names: {valid}. Nothing was saved."
    updated = sum(1 for item_id, text in texts if update_item_text(item_id, text))
    moved = sum(reassign_items_to_cluster(ids, target, question_id) for target, ids in moves.items())
    if moved:
        _update_cluster_counts(question_id)
    return f"✅ {updated} items updated, {moved} moved"


def delete_cluster_by_name(question_id: str, name: str, move_to: str = None) -> str:
    """Deletes the cluster ``name``; its items go to the cluster ``move_to`` or stay without a cluster."""
    clusters = {str(c['name_source']): c['id'] for c in get_clusters_for_question(question_id)}
    if name not in clusters:
        return f"❌ No cluster '{name}'"
    target = None
    if move_to and move_to != NO_CLUSTER:
        if move_to not in clusters or move_to == name:
            return f"❌ Choose another existing cluster for the items (not '{move_to}')"
        target = clusters[move_to]
    delete_cluster(clusters[name], reassign_to=target)
    _update_cluster_counts(question_id)
    where = f"moved to '{move_to}'" if target else "now without a cluster"
    return f"✅ Cluster '{name}' deleted; its items are {where}"


def load_similar_items_table(question_id: str, min_similarity: float) -> pd.DataFrame:
    """Loads similar items for the normalisation."""
    if not question_id:
        return SIMILAR_ITEMS.empty()
    
    groups = find_similar_items(question_id, min_similarity)
    
    if not groups:
        return SIMILAR_ITEMS.empty()
    
    return SIMILAR_ITEMS.to_ui({
        'group': i,
        'variants': ', '.join(f'"{v}" ({c}x)' for v, c in zip(g['variants'], g['counts'])),
        'frequency': g['total_count'],
        'suggestion': g['suggested_canonical'],
        'apply': APPLY_YES,
    } for i, g in enumerate(groups, 1))


def load_all_analyses_from_db() -> dict:
    """
    Loads all analyses from the database.
    
    Returns:
        dict question_id -> analysis_data
    """
    from utils.persistence import get_db_path
    import sqlite3
    
    db_path = get_db_path()
    if not db_path.exists():
        logger.warning("No database found")
        return {}
    
    analyses = {}
    
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # load all analyses (newest per question)
            cursor.execute("""
                SELECT DISTINCT question_id FROM analysis_results
                WHERE segment_type = 'all'
            """)
            
            question_ids = [row['question_id'] for row in cursor.fetchall()]
            
            for qid in question_ids:
                # newest analysis of this question
                cursor.execute("""
                    SELECT id, result_json, analyzed_at
                    FROM analysis_results 
                    WHERE question_id = ? AND segment_type = 'all'
                    ORDER BY analyzed_at DESC LIMIT 1
                """, (qid,))
                
                row = cursor.fetchone()
                if row and row['result_json']:
                    try:
                        analysis_data = json.loads(row['result_json'])
                        analysis_data['_analysis_id'] = row['id']
                        analysis_data['_analyzed_at'] = row['analyzed_at']
                        analyses[qid] = analysis_data
                    except json.JSONDecodeError as e:
                        logger.warning(f"JSON error for {qid}: {e}")
            
            logger.info(f"Analyses loaded from the database: {len(analyses)}")
            
    except Exception as e:
        logger.error(f"Error loading the analyses: {e}")
    
    return analyses


def mark_cluster_as_changed(cluster_id: int):
    """
    Marks a cluster as changed manually.
    
    Called by the Workbench when a cluster is edited.
    
    Args:
        cluster_id: id of the cluster
    """
    from utils.persistence import get_db_path
    import sqlite3
    from datetime import datetime
    
    try:
        db_path = get_db_path()
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE freetext_clusters 
                SET is_manual = 1, updated_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), cluster_id))
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error marking the cluster: {e}")


def _as_dict(value) -> dict:
    """value if it is a dict, else {} (multilingual text or plain text)."""
    return value if isinstance(value, dict) else {}


def _count_of(value) -> int:
    """A count from result data: a number, or a dict with 'count'/'n' (per-cluster details)."""
    if isinstance(value, dict):
        value = value.get('count', value.get('n', 0))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
