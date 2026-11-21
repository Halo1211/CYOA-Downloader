"""Atomic archive metadata, sharing the mirror's files rather than caching bodies.

Only explicit interrupted-archive resume reads old records. A normal download
starts a new generation. SQLite transactions fence late metadata writers; file
hashes, request context and exact URLs guard reuse. This is not a remote update
check: route records describe a verified local snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from ..core.atomic_io import interprocess_file_lock
from ..core.cancellation import _raise_if_cancelled
from ..core.paths import _safe_archive_join


def _checkpoint_root() -> Path:
    return Path.home() / ".cyoa_downloader" / "archive-checkpoints"


def _legacy_path(folder: str) -> Path:
    return Path(_safe_archive_join(folder, "_audit/archive-checkpoints.sqlite3", decode_url=False))


def _migrate_checkpoint(folder: str, destination: str) -> None:
    """Import a legacy DB atomically; remove only its owned file and empty parent."""
    legacy = _legacy_path(folder)
    if not legacy.is_file() or Path(destination).exists():
        return
    with interprocess_file_lock(destination, timeout=.1):
        if Path(destination).exists():
            return
        temporary = Path(destination + "." + uuid.uuid4().hex + ".tmp")
        source = sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True, timeout=.05)
        try:
            # Reject unrelated/corrupt databases instead of deleting their files.
            source.execute("SELECT generation, context FROM owner WHERE id=1").fetchone()
            source.execute("SELECT kind, url, data FROM records LIMIT 1").fetchone()
            deadline = time.monotonic() + .25

            def progress(status, _remaining, _total):
                _raise_if_cancelled()
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() > deadline:
                    raise sqlite3.OperationalError("Legacy checkpoint is busy")

            target = sqlite3.connect(temporary)
            try:
                source.backup(target, pages=128, progress=progress, sleep=.01)
            finally:
                target.close()
            os.replace(temporary, destination)
        finally:
            source.close()
            temporary.unlink(missing_ok=True)
        try:
            legacy.unlink()
            legacy.parent.rmdir()
        except OSError:
            pass  # Busy legacy files and unrelated directory contents are retained.


@lru_cache(maxsize=1)
def _windows_file_info_api():
    import ctypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ("creation", "access", "write", "change")] + [("attributes", ctypes.c_ulong)]

    api = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    api.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)
    api.restype = ctypes.c_int
    return api, BasicInfo


def file_identity(path: str) -> tuple:
    stat = os.stat(path)
    changed = stat.st_ctime_ns
    if os.name == "nt":
        # Python 3.12's Windows st_ctime is creation time. NTFS ChangeTime
        # detects same-size edits even when an editor restores LastWriteTime.
        import ctypes
        import msvcrt
        api, info_type = _windows_file_info_api()
        info = info_type()
        with open(path, "rb") as handle:
            changed = (info.change if api(msvcrt.get_osfhandle(handle.fileno()), 0,
                                         ctypes.byref(info), ctypes.sizeof(info)) else time.monotonic_ns())
    return stat.st_size, stat.st_mtime_ns, changed, stat.st_ino


class ArchiveCheckpoint:
    @staticmethod
    def path_for(folder: str) -> str:
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(folder)))
        identity = hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()
        return str(_checkpoint_root() / (identity + ".sqlite3"))

    @classmethod
    def exists(cls, folder: str) -> bool:
        return Path(cls.path_for(folder)).is_file() or _legacy_path(folder).is_file()

    @classmethod
    def relocate(cls, source: str, destination: str) -> bool:
        """Follow an application-owned completed staging move, after files move."""
        old, new = cls.path_for(source), cls.path_for(destination)
        if old == new or not Path(old).is_file():
            return False
        try:
            with interprocess_file_lock(old, timeout=.1), interprocess_file_lock(new, timeout=.1):
                os.replace(old, new)
                db = sqlite3.connect(new, timeout=.05)
                try:
                    with db:
                        db.execute("UPDATE archive SET folder=? WHERE id=1",
                                   (os.path.realpath(os.path.abspath(destination)),))
                finally:
                    db.close()
            return True
        except (OSError, sqlite3.Error):
            return False  # Metadata must not turn a completed file move into a failed job.

    @classmethod
    def interrupted(cls, root: Path):
        """Find private recovery records within this output tree, without writes."""
        root = root.resolve()
        for path in _checkpoint_root().glob("*.sqlite3"):
            try:
                db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.05)
                try:
                    row = db.execute("SELECT folder, source FROM archive WHERE id=1").fetchone()
                finally:
                    db.close()
                if not row or not all(isinstance(value, str) and value for value in row):
                    continue
                folder, source = Path(row[0]).resolve(), row[1]
                if (folder.is_relative_to(root) and folder.is_dir()
                        and Path(cls.path_for(str(folder))) == path
                        and source.startswith(("http://", "https://"))
                        and not (folder / "archive_manifest.json").exists()):
                    yield folder, source
            except (OSError, sqlite3.Error, ValueError):
                continue

    def __init__(self, folder: str, context: str, *, resume: bool = False, source_url: str = ""):
        self.folder = os.path.realpath(os.path.abspath(folder))
        self.path = self.path_for(self.folder)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        _migrate_checkpoint(self.folder, self.path)
        self.generation = uuid.uuid4().hex
        self.context = context
        self._lock = threading.RLock()
        self._verified = {}
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, generation TEXT, context TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, url TEXT, data TEXT, PRIMARY KEY(kind,url))")
            db.execute("CREATE TABLE IF NOT EXISTS archive (id INTEGER PRIMARY KEY, source TEXT, folder TEXT)")
            if "folder" not in {row[1] for row in db.execute("PRAGMA table_info(archive)")}:
                db.execute("ALTER TABLE archive ADD COLUMN folder TEXT")
            previous = db.execute("SELECT context FROM owner WHERE id=1").fetchone()
            self.compatible = bool(resume and previous and previous[0] == context)
            if not self.compatible:
                db.execute("DELETE FROM records")
            db.execute("INSERT OR REPLACE INTO owner VALUES (1,?,?)", (self.generation, context))
            if not source_url and self.compatible:
                row = db.execute("SELECT source FROM archive WHERE id=1").fetchone()
                source_url = row[0] if row else ""
            db.execute("INSERT OR REPLACE INTO archive VALUES (1,?,?)", (source_url, self.folder))

    @staticmethod
    def source(path: str) -> str:
        """Inspect recovery provenance without taking ownership or creating a DB."""
        try:
            db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)
            try:
                row = db.execute("SELECT source FROM archive WHERE id=1").fetchone()
                return row[0] if row and isinstance(row[0], str) and row[0].startswith(("http://", "https://")) else ""
            finally:
                db.close()
        except (OSError, sqlite3.Error, ValueError):
            return ""

    @contextmanager
    def _connect(self):
        # Connections are short lived and never shared between worker threads.
        # Optional metadata never waits through a long SQLite busy timeout.
        # Callers retain normal download/failure behavior when the DB is busy.
        db = sqlite3.connect(self.path, timeout=0.05)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identity(path: str) -> tuple[int, int, int, int]:
        return file_identity(path)

    @staticmethod
    def digest(path: str) -> str:
        result = hashlib.sha256()
        with open(path, "rb") as stream:
            while block := stream.read(64 * 1024):
                _raise_if_cancelled()
                result.update(block)
        return result.hexdigest()

    def put(self, category: str, url: str, path: str, **metadata) -> bool:
        _raise_if_cancelled()
        relative = os.path.relpath(path, self.folder).replace("\\", "/")
        safe = _safe_archive_join(self.folder, relative, decode_url=False)
        before = self.identity(safe)
        digest = self.digest(safe)
        if self.identity(safe) != before:
            return False
        data = dict(metadata, local=relative, size=before[0], sha256=digest)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT generation FROM owner WHERE id=1").fetchone()
            if not owner or owner[0] != self.generation:
                return False
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (category, url, json.dumps(data)))
            self._verified[(category, url)] = (before, digest)
        return True

    def get(self, kind: str, url: str) -> dict | None:
        _raise_if_cancelled()
        with self._lock, self._connect() as db:
            owner = db.execute("SELECT generation FROM owner WHERE id=1").fetchone()
            if not owner or owner[0] != self.generation:
                return None
            row = db.execute("SELECT data FROM records WHERE kind=? AND url=?", (kind, url)).fetchone()
            if not row:
                return None
            try:
                data = json.loads(row[0])
                path = _safe_archive_join(self.folder, data["local"], decode_url=False)
                before = self.identity(path)
                if before[0] != data["size"] or not Path(path).is_file():
                    return None
                verified = self._verified.get((kind, url))
                if verified != (before, data["sha256"]):
                    if self.digest(path) != data["sha256"] or self.identity(path) != before:
                        return None
                    self._verified[(kind, url)] = (before, data["sha256"])
                return dict(data, path=path)
            except (OSError, ValueError, KeyError, TypeError):
                return None

    def urls(self, kind: str) -> list[str]:
        with self._lock, self._connect() as db:
            return [row[0] for row in db.execute("SELECT url FROM records WHERE kind=?", (kind,))]
