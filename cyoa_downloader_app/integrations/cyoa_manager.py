"""CYOA Manager SQLite library integration.

Phase 27 moves the real implementation out of legacy.py while preserving
the old function names and return semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from ..config.settings import _load_settings
from ..core.archive import validate_zip_archive
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
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "CYOA Manager", "save", "library.sqlite3"),
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


def _find_cyoa_manager_db() -> str | None:
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
    tags: list | None = None,
    viewer_preference: str = "",
    db_path: str | None = None,
) -> bool | None:
    """
    Register a downloaded project.json in CYOA Manager's SQLite library.

    Returns True on success, False on failure, and None when the project is
    already registered (legacy behavior).
    """
    import json as _json_local
    import sqlite3 as _sql
    import uuid as _uuid_local

    # Resolve paths
    abs_path = os.path.abspath(project_json_path)
    if not os.path.exists(abs_path):
        logger.error(f"CYOA Manager: project.json not found: {abs_path}")
        return False
    try:
        with open(abs_path, encoding="utf-8") as project_file:
            project_data = _json_local.load(project_file)
        if not isinstance(project_data, dict) or not isinstance(project_data.get("rows"), list):
            logger.error("CYOA Manager: expected ICC project JSON with rows: %s", abs_path)
            return False
    except (OSError, UnicodeError, ValueError) as exc:
        logger.error("CYOA Manager: invalid project JSON %s: %s", abs_path, exc)
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
    version = project_data.get("version")
    is_plus = (
        isinstance(version, str)
        and len(version.split(".")) >= 2
        and all(segment.isdigit() for segment in version.split("."))
        and isinstance(project_data.get("styling"), dict)
    )
    selected_viewer = viewer_preference or ("icc2-plus" if is_plus else "icc-original")
    parsed_source = urlparse(source_url)
    project_json_url = (
        source_url if parsed_source.scheme in ("http", "https")
        and parsed_source.path.lower().endswith(".json") else None
    )

    try:
        con = _sql.connect(db_path)
        # finally guarantees close on every path, including
        # the exception path which previously leaked the connection.
        try:
            cur = con.cursor()

            # Ensure schema exists (CYOA Manager creates it on first launch)
            cur.execute("""CREATE TABLE IF NOT EXISTS library_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS library_projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    cover_image TEXT,
                    source_url TEXT,
                    project_json_url TEXT,
                    file_path TEXT NOT NULL DEFAULT '',
                    viewer_preference TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    exclude_from_perk_index INTEGER NOT NULL DEFAULT 0,
                    date_added TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]'
                )
            """)
            columns = {row[1] for row in cur.execute("PRAGMA table_info(library_projects)")}
            if "project_json_url" not in columns:
                cur.execute("ALTER TABLE library_projects ADD COLUMN project_json_url TEXT")

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
                    (id, name, description, cover_image, source_url, project_json_url, file_path,
                     viewer_preference, favorite, exclude_from_perk_index,
                     date_added, tags_json)
                VALUES (?,?,?,NULL,?,?,?,?,0,0,?,?)
            """, (
                project_id, display_name, description or "",
                source_url or "", project_json_url, abs_path,
                selected_viewer,
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

    except (OSError, TypeError, ValueError, sqlite3.Error) as e:
        logger.error(f"CYOA Manager: DB write failed — {e}")
        return False


def add_archive_to_cyoa_manager(
    archive_path: str, *, name: str = "", source_url: str = "",
    db_path: str | None = None,
) -> bool | None:
    """Extract a ZIP beside the archive because Manager registers JSON paths, not ZIPs."""
    archive = Path(archive_path).resolve()
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        return False
    digest = hashlib.sha256()
    try:
        with archive.open("rb") as source_bytes:
            for chunk in iter(lambda: source_bytes.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.error("CYOA Manager: cannot read ZIP for import: %s", exc)
        return False
    source_hash = digest.hexdigest()
    destination = archive.with_name(archive.stem + "_manager")
    created = False
    try:
        if destination.exists() and (
            not destination.is_dir()
            or destination.is_symlink()
            or not (destination / ".cyoa_source_sha256").is_file()
            or (destination / ".cyoa_source_sha256").read_text(encoding="ascii").strip() != source_hash
        ):
            destination = archive.with_name(archive.stem + "_manager_" + source_hash[:12])
        if destination.exists() and (
            not destination.is_dir()
            or destination.is_symlink()
            or not (destination / ".cyoa_source_sha256").is_file()
            or (destination / ".cyoa_source_sha256").read_text(encoding="ascii").strip() != source_hash
        ):
            raise ValueError("existing CYOA Manager folder does not match ZIP")
        if not destination.exists():
            validate_zip_archive(
                archive,
                max_members=20000,
                max_member_size=1024 * 1024 * 1024,
                max_total_size=4 * 1024**3,
                max_ratio=250.0,
            )
            destination.mkdir()
            created = True
            with zipfile.ZipFile(archive) as source:
                members = [member for member in source.infolist() if not member.is_dir()]
                if len(members) > 20000 or sum(m.file_size for m in members) > 4 * 1024**3:
                    raise ValueError("archive exceeds CYOA Manager import limit")
                for member in members:
                    relative = Path(member.filename.replace("\\", "/"))
                    target = (destination / relative).resolve()
                    if (
                        relative.is_absolute() or ".." in relative.parts
                        or not target.is_relative_to(destination)
                        or (member.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("unsafe archive member")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as reader, open(target, "wb") as writer:
                        shutil.copyfileobj(reader, writer)
            (destination / ".cyoa_source_sha256").write_text(source_hash, encoding="ascii")
        projects = sorted(destination.rglob("project.json"), key=lambda path: len(path.parts))
        if not projects:
            raise ValueError("project.json missing from archive")
        return add_to_cyoa_manager(
            str(projects[0]), name=name or archive.stem,
            source_url=source_url, db_path=db_path,
        )
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        logger.error("CYOA Manager: ZIP import failed: %s", exc)
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        return False


def _scan_for_cyoa_manager_db() -> list[str]:
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


def _list_cyoa_manager_projects(db_path: str = "") -> list[dict[str, str]]:
    """Read URL and local-file projects from a CYOA Manager library."""
    if not db_path:
        db_path = _find_cyoa_manager_db()
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(library_projects)")}
            wanted = ("id", "name", "source_url", "project_json_url", "file_path",
                      "date_added", "viewer_preference")
            rows = conn.execute(
                "SELECT " + ", ".join(
                    column if column in columns else f"'' AS {column}" for column in wanted
                ) + " "
                "FROM library_projects ORDER BY name COLLATE NOCASE"
            ).fetchall()
            projects = [{k: (row[k] or "") for k in wanted} for row in rows]
            for project in projects:
                if not project["source_url"]:
                    project["source_url"] = project["project_json_url"]
            return projects
        finally:
            conn.close()
    except (OSError, TypeError, ValueError, sqlite3.Error) as e:
        logger.warning(f"CYOA Manager list: {e}")
        return []


def prepare_cyoa_manager_serve_folder(project_json_path: str) -> str:
    """Make a Manager JSON entry playable by our local Serve without network access.

    Existing offline folders are served in place. JSON-only entries get a cached
    offline viewer with neighboring images/audio copied into it.
    """
    project = Path(project_json_path).resolve(strict=True)
    if not project.is_file() or project.suffix.lower() not in {".json", ".txt"}:
        raise ValueError("CYOA Manager entry is not a project JSON file")
    project_text = project.read_text(encoding="utf-8-sig")
    project_data = json.loads(project_text)
    if not isinstance(project_data, dict) or not isinstance(project_data.get("rows"), list):
        raise TypeError("CYOA Manager project must contain ICC rows")
    if (project.parent / "index.html").is_file():
        return str(project.parent)

    from .offline_viewers.injector import _apply_offline_viewer
    from .offline_viewers.registry import (
        _VIEWERS_DIR,
        _auto_register_bundled_viewers,
        _safe_viewer_archive_name,
        get_viewer_for_site,
    )

    _auto_register_bundled_viewers()
    viewer = get_viewer_for_site("", project_data=project_data)
    if viewer is None:
        raise ValueError("No compatible offline ICC viewer is registered for this project")

    asset_dirs = {
        name: str(project.parent / name)
        for name in ("images", "audio", "assets", "media", "videos", "fonts", "img", "backgrounds")
        if (project.parent / name).is_dir() and not (project.parent / name).is_symlink()
    }
    media_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg",
                      ".mp3", ".ogg", ".wav", ".m4a", ".mp4", ".webm", ".woff", ".woff2", ".ttf"}
    root_media = [entry for entry in project.parent.iterdir()
                  if entry.is_file() and not entry.is_symlink() and entry.suffix.lower() in media_suffixes]
    signature = hashlib.sha256(project_text.encode("utf-8"))
    signature.update(json.dumps(viewer, sort_keys=True, default=str).encode("utf-8"))
    archive_name = _safe_viewer_archive_name(viewer.get("zip_filename", ""))
    if archive_name:
        archive = Path(_VIEWERS_DIR) / archive_name
        if archive.is_file() and not archive.is_symlink():
            archive_stat = archive.stat()
            signature.update(f"{archive_stat.st_size}:{archive_stat.st_mtime_ns}".encode())
    for name, source in sorted(asset_dirs.items()):
        for asset in sorted(Path(source).rglob("*")):
            if asset.is_file() and not asset.is_symlink():
                stat = asset.stat()
                signature.update(f"{name}/{asset.relative_to(source)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    for asset in sorted(root_media):
        stat = asset.stat()
        signature.update(f"{asset.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    cache_key = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:16]
    cache_root = Path(tempfile.gettempdir()) / "cyoa-downloader-manager-preview" / cache_key / signature.hexdigest()[:16]
    cached = cache_root / "manager_offline" / "index.html"
    if cached.is_file():
        cached_json = cached.parent / "project.json"
        if not cached_json.is_file():
            cached_json.write_text(project_text, encoding="utf-8")
        return str(cached.parent)
    cache_root.mkdir(parents=True, exist_ok=True)
    # Keep assets within the project cache. A global cache can substitute an
    # identically named remote asset from another CYOA into this preview.
    shared_assets = cache_root.parent / "shared-source-assets"
    index = _apply_offline_viewer(
        str(cache_root), project_text, viewer, file_name="manager",
        asset_source_dirs=asset_dirs, preserved_asset_cache_dir=str(shared_assets),
    )
    if not index:
        raise ValueError("Unable to build a local viewer for the CYOA Manager project")
    for asset in root_media:
        target = Path(index).parent / asset.name
        if not target.exists():
            shutil.copy2(asset, target)
    localized_assets = Path(index).parent / "__source_assets__"
    if localized_assets.is_dir():
        shutil.copytree(localized_assets, shared_assets, dirs_exist_ok=True)
    return str(Path(index).parent)


__all__ = [
    "_CYOA_MANAGER_DB_CANDIDATES",
    "_cyoa_manager_viewer_pref",
    "_find_cyoa_manager_db",
    "_list_cyoa_manager_projects",
    "_scan_for_cyoa_manager_db",
    "add_archive_to_cyoa_manager",
    "add_to_cyoa_manager",
    "prepare_cyoa_manager_serve_folder",
]
