# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Stored texts of statistical findings (table analysis_texts, schema v9).

A finding (e.g. one association) gets a description and a possible reading
from the LLM. They are stored per import, kind and finding together with a
hash of the facts and prompts they were written from:

- same facts, same prompts: the stored texts are used again, so repeated
  exports give the same wording and cost no LLM calls;
- changed facts: new texts are written, since the old ones may no longer fit;
- texts marked as manual (corrected by a person) are never overwritten. If
  their facts have changed, they are still used and a warning is logged, so
  the correction is not lost silently.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AnalysisTextStore:
    def __init__(self, db, import_id: int, kind: str):
        self.db = db
        self.import_id = import_id
        self.kind = kind

    def get(self, item_key: str, facts_hash: str) -> Optional[dict]:
        with self.db._connection() as conn:
            row = conn.execute(
                "SELECT facts_hash, texts_json, is_manual FROM analysis_texts "
                "WHERE import_id = ? AND kind = ? AND item_key = ?",
                (self.import_id, self.kind, item_key)).fetchone()
        if not row:
            return None
        stored_hash, texts, manual = row[0], json.loads(row[1]), bool(row[2])
        if stored_hash == facts_hash:
            return texts
        if manual:
            logger.warning(f"Manually corrected text of {self.kind} {item_key} no longer matches "
                           f"the current facts; it is kept, please check.")
            return texts
        return None

    def put(self, item_key: str, facts_hash: str, description: Dict[str, str],
            reading: Dict[str, str], manual: bool = False) -> None:
        texts = json.dumps({"description": description, "reading": reading}, ensure_ascii=False)
        with self.db._connection() as conn:
            row = conn.execute(
                "SELECT is_manual FROM analysis_texts WHERE import_id = ? AND kind = ? AND item_key = ?",
                (self.import_id, self.kind, item_key)).fetchone()
            if row and row[0] and not manual:
                return  # a person's correction wins
            conn.execute(
                "INSERT INTO analysis_texts (import_id, kind, item_key, facts_hash, texts_json, is_manual, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(import_id, kind, item_key) DO UPDATE SET facts_hash = excluded.facts_hash, "
                "texts_json = excluded.texts_json, is_manual = excluded.is_manual, updated_at = CURRENT_TIMESTAMP",
                (self.import_id, self.kind, item_key, facts_hash, texts, int(manual)))
