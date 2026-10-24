# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service: configuration I/O with snapshots.

Reads and writes the `questions.yaml` of the active survey safely. Before
every write a snapshot is created in
`<survey_dir>/snapshots/questions_<timestamp>.yaml`. The last N snapshots
are kept, older ones are deleted automatically.

Usage:
    from services.config_io import (
        load_active_config, write_active_config_with_snapshot,
        list_snapshots, restore_snapshot,
    )
    
    cfg = load_active_config()
    cfg['settings']['llm_context'] = 'New text'
    write_active_config_with_snapshot(cfg)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# number of snapshots kept
MAX_SNAPSHOTS = 10

# prefix of the snapshot files
SNAPSHOT_PREFIX = 'questions_'
SNAPSHOT_SUFFIX = '.yaml'

# directory name of the snapshots inside a survey directory
SNAPSHOT_DIR_NAME = 'snapshots'


# =============================================================================
# Resolving directories
# =============================================================================

def get_active_survey_dir() -> Path:
    """
    Directory of the active survey.
    
    Imported lazily from core, so this service can be imported without
    initialising core (e.g. in tests).
    """
    from core import get_active_config_dir
    return Path(get_active_config_dir())


def get_active_questions_path() -> Path:
    """Path of the questions.yaml of the active survey."""
    return get_active_survey_dir() / 'questions.yaml'


def get_snapshots_dir(survey_dir: Optional[Path] = None) -> Path:
    """Directory of the snapshots; created if needed."""
    if survey_dir is None:
        survey_dir = get_active_survey_dir()
    snapshots = survey_dir / SNAPSHOT_DIR_NAME
    snapshots.mkdir(parents=True, exist_ok=True)
    return snapshots


# =============================================================================
# Reading / writing
# =============================================================================

def load_active_config() -> dict:
    """
    Loads the questions.yaml of the active survey as a dict.
    
    Raises:
        FileNotFoundError: if the file does not exist.
    """
    path = get_active_questions_path()
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open(encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def snapshot_active_config(reason: str = '', questions_path: Optional[Path] = None) -> Optional[Path]:
    """
    Creates a snapshot of the current questions.yaml of the active survey.

    Called by every write path that replaces the configuration, so a
    mistake can always be undone.

    Returns:
        path of the snapshot, or None if no file exists yet.
    """
    questions_path = Path(questions_path) if questions_path else get_active_questions_path()
    if not questions_path.exists():
        return None
    snapshots_dir = get_snapshots_dir(questions_path.parent)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    # the reason goes into the file name, sanitised
    suffix = ''
    if reason:
        safe_reason = ''.join(
            c if c.isalnum() or c in '-_' else '_'
            for c in reason
        )[:30]
        suffix = f'_{safe_reason}'
    snapshot_path = snapshots_dir / f'{SNAPSHOT_PREFIX}{timestamp}{suffix}{SNAPSHOT_SUFFIX}'
    # copy the file (raw, with comments)
    snapshot_path.write_bytes(questions_path.read_bytes())
    logger.info(f"Snapshot created: {snapshot_path.name}")
    return snapshot_path


def write_active_config_text_with_snapshot(yaml_text: str, reason: str = '') -> Optional[Path]:
    """
    Writes YAML text unchanged (with comments and formatting) into the
    questions.yaml of the active survey, after a snapshot of the previous version.

    The text must be validated beforehand; this function only checks that it
    reads as YAML, and writes nothing otherwise.
    """
    yaml.safe_load(yaml_text)  # raises on invalid YAML, before anything is overwritten
    snapshot_path = snapshot_active_config(reason)
    questions_path = get_active_questions_path()
    questions_path.write_text(yaml_text, encoding='utf-8')
    logger.info(f"Configuration written: {questions_path}")
    _rotate_snapshots(get_snapshots_dir())
    return snapshot_path


def write_active_config_with_snapshot(config: dict, reason: str = '',
                                      questions_path: Optional[Path] = None) -> Path:
    """
    Writes the configuration into the questions.yaml of the active survey,
    after a snapshot of the previous version.
    
    Args:
        config: the complete configuration (overwritten completely).
        reason: optional reason for the snapshot (hinted at in the file name).
        questions_path: the file to write; default: the questions.yaml of the active survey.
    
    Returns:
        path of the created snapshot (or of the configuration file if no
        snapshot was possible, e.g. for an initial configuration).
    
    Side effects:
        - a snapshot of the previous file is created
        - the oldest snapshots are deleted (>= MAX_SNAPSHOTS)
        - header comments of the source file are taken over, if present
    """
    questions_path = Path(questions_path) if questions_path else get_active_questions_path()
    questions_path.parent.mkdir(parents=True, exist_ok=True)
    snapshots_dir = get_snapshots_dir(questions_path.parent)
    
    # 1. snapshot of the previous file (if it exists)
    snapshot_path = snapshot_active_config(reason, questions_path)
    
    # 2. take over the header comments of the old file
    header_block = ''
    if questions_path.exists():
        raw = questions_path.read_text(encoding='utf-8')
        header_lines = []
        for line in raw.splitlines(keepends=True):
            stripped = line.lstrip()
            if stripped.startswith('#') or line.strip() == '':
                header_lines.append(line)
            else:
                break
        header_block = ''.join(header_lines)
    
    # 3. write the new configuration
    with questions_path.open('w', encoding='utf-8') as f:
        if header_block:
            f.write(header_block)
            if not header_block.endswith('\n\n'):
                f.write('\n')
        yaml.safe_dump(
            config, f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    logger.info(f"Configuration written: {questions_path}")
    
    # 4. delete the oldest snapshots
    _rotate_snapshots(snapshots_dir)
    
    return snapshot_path or questions_path


def _rotate_snapshots(snapshots_dir: Path) -> int:
    """
    Deletes the oldest snapshots so that at most MAX_SNAPSHOTS remain.
    
    Returns:
        number of deleted snapshots.
    """
    if not snapshots_dir.exists():
        return 0
    
    snapshots = sorted(
        snapshots_dir.glob(f'{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,  # newest first
    )
    
    deleted = 0
    for old in snapshots[MAX_SNAPSHOTS:]:
        try:
            old.unlink()
            deleted += 1
            logger.debug(f"Snapshot geloescht: {old.name}")
        except OSError as e:
            logger.warning(f"Could not delete the snapshot {old.name}: {e}")
    return deleted


# =============================================================================
# Managing snapshots
# =============================================================================

def list_snapshots(survey_dir: Optional[Path] = None
                    ) -> List[Tuple[Path, datetime]]:
    """
    Lists all snapshots of the active survey, newest first.
    
    Returns:
        list of (path, creation time) tuples.
    """
    snapshots_dir = get_snapshots_dir(survey_dir)
    snapshots = sorted(
        snapshots_dir.glob(f'{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [(p, datetime.fromtimestamp(p.stat().st_mtime)) for p in snapshots]


def restore_snapshot(snapshot_path: Path) -> Path:
    """
    Restores a configuration from a snapshot.
    
    Before the restore, the current file itself is saved as a snapshot
    (reason='before_restore'), so the restore can be undone.
    
    Args:
        snapshot_path: path of the snapshot.
    
    Returns:
        path of the questions.yaml now restored.
    
    Raises:
        FileNotFoundError: if the snapshot does not exist.
    """
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")
    
    questions_path = get_active_questions_path()
    
    # first save the current file itself, so the restore can be undone
    if questions_path.exists():
        snapshots_dir = get_snapshots_dir()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        backup = snapshots_dir / f'{SNAPSHOT_PREFIX}{timestamp}_before_restore{SNAPSHOT_SUFFIX}'
        backup.write_bytes(questions_path.read_bytes())
        logger.info(f"Pre-Restore-Snapshot: {backup.name}")
    
    # restore the snapshot
    questions_path.write_bytes(snapshot_path.read_bytes())
    logger.info(f"Snapshot restored: {snapshot_path.name} -> {questions_path}")
    
    # Aufraeumen
    _rotate_snapshots(get_snapshots_dir())
    
    return questions_path
