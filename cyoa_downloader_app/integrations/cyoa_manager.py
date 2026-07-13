"""CYOA Manager SQLite library integration.

Phase 27 moves the real implementation out of legacy.py while preserving
the old function names and return semantics.
"""

from __future__ import annotations

import json as _json
import os
import sqlite3
import sys
import uuid as _uuid
from typing import Dict, List, Optional

from ..config.settings import _load_settings
from ..logging_setup import logger


# DB schema (library_projects table):
#   id TEXT PK, name, description, cover_image, source_url,
#   file_path TEXT,   ← absolute path to project.json on disk
#   viewer_preference TEXT,  ← "icc-plus", "icc-original", etc.
#   favorite INT, exclude_from_perk_index INT,
#   date_added TEXT, tags_json TEXT

_CYOA_MANAGER_DB_CANDIDATES = [
    # Windows (portable — next to the .exe)
    # We can't know the exe path, so we check common install locations
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                 "CYOA Manager", "save", "library.sqlite3"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                 "cyoa-manager", "save", "library.sqlite3"),
    # macOS
    os.path.expanduser("~/Library/Application Support/CYOA Manager/save/library.sqlite3"),
    # Linux
    os.path.expanduser("~/.local/share/cyoa-manager/save/library.sqlite3"),
    os.path.expanduser("~/.local/share/CYOA Manager/save/library.sqlite3"),
]


def _find_cyoa_manager_db() -> Optional[str]:
    """Auto-detect CYOA Manager library.sqlite3. Returns path or None."""
    for p in _CYOA_MANAGER_DB_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def _cyoa_manager_viewer_pref(mode: str) -> str:
    """Map our download mode to CYOA Manager viewer ID."""
    mode_lower = (mode or "").lower()
    if "icc_remix" in mode_lower or "remix" in mode_lower:
        return "icc-remix"
    # icc-original = ICC Original (MeanDelay), icc2-plus = ICC2 Plus (Wahaha303)
    return "icc2-plus"   # default: best modern viewer


def add_to_cyoa_manager(
    project_json_path: str,
    name: str = "",
    source_url: str = "",
    description: str = "",
    tags: Optional[list] = None,
    viewer_preference: str = "",
    db_path: Optional[str] = None,
) -> Optional[bool]:
    """
    Register a downloaded project.json in CYOA Manager's SQLite library.

    Returns True on success, False on failure, and None when the project is
    already registered (legacy behavior).
    """
    import sqlite3 as _sql, uuid as _uuid_local, json as _json_local

    # Resolve paths
    abs_path = os.path.abspath(project_json_path)
    if not os.path.exists(abs_path):
        logger.error(f"CYOA Manager: project.json not found: {abs_path}")
        return False

    if db_path is None:
        s = _load_settings()
        custom = s.get("cyoa_mgr_db_path", "").strip()
        db_path = (custom if custom and os.path.exists(custom)
                   else _find_cyoa_manager_db())
    if db_path is None:
        logger.warning(
            "CYOA Manager: library.sqlite3 not found.\n"
            "  Ensure CYOA Manager is installed, or specify the DB path manually."
        )
        return False

    project_id   = str(_uuid_local.uuid4())
    display_name = name or os.path.splitext(os.path.basename(abs_path))[0]
    date_added   = __import__("datetime").datetime.now().isoformat()
    tags_json    = _json_local.dumps(tags or [])

    try:
        con = _sql.connect(db_path)
        # finally guarantees close on every path, including
        # the exception path which previously leaked the connection.
        try:
            cur = con.cursor()

            # Ensure schema exists (CYOA Manager creates it on first launch)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS library_projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    cover_image TEXT,
                    source_url TEXT,
                    file_path TEXT NOT NULL DEFAULT '',
                    viewer_preference TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    exclude_from_perk_index INTEGER NOT NULL DEFAULT 0,
                    date_added TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                )
            """)

            # Check if this file_path is already registered
            cur.execute("SELECT id FROM library_projects WHERE file_path = ?", (abs_path,))
            existing = cur.fetchone()
            if existing:
                logger.info(
                    f"CYOA Manager: '{display_name}' already in library "
                    f"(id={existing[0]}) — skipping duplicate."
                )
                return None   # None = already exists (not True=added, not False=error)

            cur.execute("""
                INSERT INTO library_projects
                    (id, name, description, cover_image, source_url, file_path,
                     viewer_preference, favorite, exclude_from_perk_index,
                     date_added, tags_json)
                VALUES (?,?,?,NULL,?,?,?,0,0,?,?)
            """, (
                project_id, display_name, description or "",
                source_url or "", abs_path,
                viewer_preference or "icc2-plus",
                date_added, tags_json,
            ))
            con.commit()

            logger.info(
                f"✓ Added to CYOA Manager: '{display_name}'\n"
                f"  file: {abs_path}\n"
                f"  db:   {db_path}"
            )
            return True
        finally:
            con.close()

    except Exception as e:
        logger.error(f"CYOA Manager: DB write failed — {e}")
        return False


def _scan_for_cyoa_manager_db() -> List[str]:
    """
    Scan common locations for CYOA Manager portable installs.
    The portable version stores save/library.sqlite3 next to the exe,
    so we search common download/install directories on Windows.
    """
    found = []
    # Check standard candidates first
    for p in _CYOA_MANAGER_DB_CANDIDATES:
        if p and os.path.exists(p):
            found.append(p)

    # Windows: scan Desktop, Downloads, Program Files for save/library.sqlite3
    if sys.platform == "win32":
        scan_roots = [
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Downloads"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "CYOA Manager"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "CYOA Manager"),
        ]
        for root in scan_roots:
            candidate = os.path.join(root, "save", "library.sqlite3")
            if os.path.exists(candidate) and candidate not in found:
                found.append(candidate)
    return found


def _list_cyoa_manager_projects(db_path: str = "") -> List[Dict[str, str]]:
    """Read CYOA Manager library and return list of projects with URLs.

    Each entry: {id, name, source_url, file_path, date_added, viewer_preference}
    Only returns entries that have a *source_url* (we can re-download them).
    """
    if not db_path:
        db_path = _find_cyoa_manager_db()
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, name, source_url, file_path, date_added, viewer_preference "
                "FROM library_projects ORDER BY name COLLATE NOCASE"
            ).fetchall()
            return [
                {k: (r[k] or "") for k in r.keys()}
                for r in rows if r["source_url"]
            ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"CYOA Manager list: {e}")
        return []


__all__ = [
    "_CYOA_MANAGER_DB_CANDIDATES", "_find_cyoa_manager_db",
    "_cyoa_manager_viewer_pref", "add_to_cyoa_manager",
    "_scan_for_cyoa_manager_db", "_list_cyoa_manager_projects",
]
