"""
CYOA Downloader — v7.3.9 AI provider options
Features:
  • Parallel image downloads (ThreadPoolExecutor)
  • All image fields: image, backgroundImage, rowBackgroundImage, objectBackgroundImage
  • Font detection + download (Google Fonts + direct woff/ttf/otf)
  • Full website download (viewer HTML/CSS/JS + all assets)
  • Tkinter GUI (auto-launches when run without arguments)
  • All original CLI flags preserved
"""

import sys
import os
import re
import io
import json
import csv
import logging
import base64
import hashlib
import mimetypes
import tempfile
import threading
import time
import uuid
import zipfile
import shutil
import pathlib
import queue as log_queue_module
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse, unquote, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_APP_VERSION = "7.3.9"
_GITHUB_RELEASE_API = ""   # Set to "https://api.github.com/repos/YOUR/REPO/releases/latest" to enable auto-update checks

try:
    import tldextract  # type: ignore
except Exception:
    tldextract = None
from bs4 import BeautifulSoup

try:
    import json5  # type: ignore
except Exception:
    json5 = None

# ─────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────

# ── Image fields ────────────────────────────────────────────────────────────
# Based on audit of ICC Plus v2.9.1 (Svelte 5) and ICC Old Viewer (Vue)
IMAGE_FIELDS: List[str] = [
    "image",               # primary image on choices and rows
    "backgroundImage",     # row/section background
    "rowBackgroundImage",  # row-level background
    "objectBackgroundImage",# object/choice background
    "defaultImage",        # ICC Plus: image shown when choice is NOT selected
    # Additional fields found in the wild
    "bgImage",             # shorthand used by some CYOA creators
    "bg",                  # ultra-short shorthand
    "img",                 # ultra-short shorthand
    "thumbnail",           # preview thumbnail
    "coverImage",          # cover/banner image
    "headerImage",         # header image
    "icon",                # choice icon
    "portrait",            # character portrait
    "avatar",              # character avatar
    "picture",             # generic picture field
]

# ── Audio fields ─────────────────────────────────────────────────────────────
# ICC Plus v2.9.1 audio architecture (from app.js audit):
#   • BGM:  choice/row has bgmId + useAudioURL (bool)
#           if useAudioURL=true  → bgmId is a direct audio URL (mp3/ogg/etc)
#           if useAudioURL=false → bgmId is a YouTube video ID (cannot go offline)
#   • SFX:  app.soundEffects[].audio → direct audio URL or base64 data URI
#           (nested — requires JSON-aware scanning, not simple field regex)
# The regex-based AUDIO_FIELDS below handles simple flat cases;
# the JSON deep-scanner (_deep_scan_project_assets) handles nested/conditional cases.
AUDIO_FIELDS: List[str] = [
    "audio",           # soundEffects[].audio in ICC Plus (direct URL / base64)
    "audioSrc",        # generic alt name used by some custom viewers
    "backgroundMusic", # some custom viewers
    "backgroundAudio", # some custom viewers
    "rowAudio",        # hypothetical row-level audio
    "objectAudio",     # hypothetical object-level audio
    # Expanded: discovered in the wild
    "soundEffect",     # click/hover sound effects
    "sfx",             # shorthand for sound effects
    "bgm",             # background music shorthand
    "ambience",        # ambient audio tracks
    "voiceover",       # narration audio
    "narration",       # narration audio
    "soundFile",       # generic sound file reference
    "audioFile",       # generic audio file reference
    "musicFile",       # music file reference
    "clickSound",      # UI click sounds
    "hoverSound",      # UI hover sounds
    "selectSound",     # selection sound effects
    "musicUrl",        # direct music URL
    "audioUrl",        # direct audio URL
    "soundUrl",        # direct sound URL
    # NOTE: "bgmId" is intentionally excluded here — handled separately
    # by _deep_scan_project_assets() because the same field holds either a
    # YouTube ID (skip) or a direct URL depending on sibling "useAudioURL" field.
]

# Playlist/multi-track audio fields (ICC Plus v2 loadPlaylist support)
BGMLIST_FIELDS: Set[str] = {
    "bgmlist", "bgmplaylist", "bgmtracks", "playlist",
    "audiolist", "musiclist", "bgmqueue",
}

# YouTube URL / ID patterns — handled via yt-dlp
_YOUTUBE_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/',
    re.IGNORECASE,
)
# YouTube video IDs look like: dQw4w9WgXcQ (11 alphanumeric+_-)
_YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')

# SoundCloud — supported by yt-dlp, stored as full URL in bgmId
_SOUNDCLOUD_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?soundcloud\.com/',
    re.IGNORECASE,
)

FONT_EXTENSIONS: Set[str] = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
IMAGE_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico"}
AUDIO_EXTENSIONS: Set[str] = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba"}
VIDEO_EXTENSIONS: Set[str] = {".mp4", ".webm", ".ogv", ".mkv", ".mov", ".m4v"}
SCRIPT_EXTENSIONS: Set[str] = {".js", ".mjs"}
STYLE_EXTENSIONS: Set[str] = {".css"}
TEXT_ASSET_EXTENSIONS: Set[str] = SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | {".html", ".json"}

DEFAULT_WAIT_TIME = 60
DEFAULT_MAX_WORKERS = 4

# ─────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────

logger = logging.getLogger("cyoa_downloader")
_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
logger.setLevel(logging.INFO)
# Prevent duplicate console lines when the module is reloaded or embedded.
logger.propagate = False
_stream_handler = next(
    (h for h in logger.handlers if getattr(h, "_cyoa_console_handler", False)),
    None,
)
if _stream_handler is None:
    _stream_handler = logging.StreamHandler()
    setattr(_stream_handler, "_cyoa_console_handler", True)
    _stream_handler.setFormatter(_formatter)
    logger.addHandler(_stream_handler)
else:
    _stream_handler.setFormatter(_formatter)

# ── File logging — written to <output_dir>/cyoa_downloader.log ────────────
# Initialized lazily when the first download starts so we know output_dir.
_file_handler: Optional[logging.Handler] = None

def setup_file_logging(output_dir: str) -> None:
    """Attach a rotating file handler. Guards against duplicate calls."""
    global _file_handler
    from logging.handlers import RotatingFileHandler
    # Remove ALL existing file/rotating handlers (prevents duplicate log lines)
    for h in logger.handlers[:]:
        if isinstance(h, RotatingFileHandler) or isinstance(h, logging.FileHandler):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    _file_handler = None
    log_path = os.path.join(output_dir, "cyoa_downloader.log")
    try:
        os.makedirs(output_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(_formatter)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
        _file_handler = fh
        logger.info(f"Log file: {log_path}")
    except Exception as e:
        logger.warning(f"Could not create log file at {log_path}: {e}")


wait_time: int = DEFAULT_WAIT_TIME

# Serializes run_download because the legacy pipeline still uses os.chdir().
# This prevents concurrent GUI/batch jobs from changing process cwd at the same time.
_RUN_DOWNLOAD_LOCK = threading.RLock()
_LAST_PREVIEW_FOLDER: Optional[str] = None


class GUILogHandler(logging.Handler):
    def __init__(self, q: log_queue_module.Queue) -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(self.format(record))



def import_queue_items_from_file(file_path: str) -> List[Dict[str, str]]:
    """
    Import batch URLs from txt/csv/xlsx/xls.

    Supported columns (case-insensitive):
      url / link / URL / Link          ← required
      filename / name / output / title ← optional, output filename
      mode                             ← optional, one of: embed zip both
                                         website_zip website_folder
                                         cyoap_vue_zip cyoap_vue_folder

    TXT format:
      https://example.com/cyoa/
      https://example.com/cyoa2/ | MyFilename
    """
    items: List[Dict[str, str]] = []
    if not file_path or not os.path.isfile(file_path):
        return items

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    url      = parts[0] if len(parts) > 0 else ""
                    filename = parts[1] if len(parts) > 1 else ""
                    mode     = parts[2] if len(parts) > 2 else ""
                else:
                    url, filename, mode = line, "", ""
                if url and is_probable_url(url):
                    items.append({"url": url, "filename": filename, "mode": mode})
        return items

    try:
        import pandas as pd  # type: ignore
    except Exception as e:
        logger.warning(f"Batch import needs pandas for {ext}: {e}")
        return items

    try:
        if ext in {".xlsx", ".xls"}:
            df = pd.read_excel(file_path)
        elif ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            logger.warning(f"Unsupported import file: {file_path}")
            return items
    except Exception as e:
        logger.error(f"Failed reading batch file {file_path}: {e}")
        return items

    url_col  = None
    name_col = None
    mode_col = None
    for col in df.columns:
        lowered = str(col).strip().lower()
        if lowered in {"url", "link", "urls", "links"} and url_col is None:
            url_col = col
        if lowered in {"filename", "name", "output", "title", "file"} and name_col is None:
            name_col = col
        if lowered in {"mode", "output_mode", "type"} and mode_col is None:
            mode_col = col

    if url_col is None:
        logger.warning("Batch import: no URL/Link column found.")
        return items

    valid_modes = {
        "embed", "zip", "both",
        "website", "website_zip", "website_folder",
        "pure_website", "pure_website_zip", "pure_website_folder",
        "cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder",
    }

    for _, row in df.iterrows():
        url = "" if pd.isna(row[url_col]) else str(row[url_col]).strip()
        if not url or not is_probable_url(url):
            continue
        filename = ""
        if name_col is not None and not pd.isna(row[name_col]):
            filename = str(row[name_col]).strip()
        mode = ""
        if mode_col is not None and not pd.isna(row[mode_col]):
            raw_mode = str(row[mode_col]).strip().lower().replace("-", "_").replace(" ", "_")
            if raw_mode in valid_modes:
                mode = raw_mode
            else:
                logger.warning(f"Unknown mode '{raw_mode}' for {url} — using GUI/CLI default")
        items.append({"url": url, "filename": filename, "mode": mode})

    return items


def write_failed_url_log(
    failed_items: List[Dict[str, str]],
    output_dir: str,
    filename: str = "failed_urls.txt",
) -> Optional[str]:
    """
    Append failed batch URLs to failed_urls.txt.
    Uses APPEND mode so multiple batch runs accumulate instead of overwriting.
    """
    if not failed_items:
        return None
    target_dir = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path   = os.path.join(target_dir, filename)
    is_new     = not os.path.exists(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# Failed batch URL downloads\n")
            f.write("# Format: url<TAB>error_message\n\n")
        f.write(f"# --- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({len(failed_items)} failed) ---\n")
        for item in failed_items:
            url = item.get("url", "")
            err = item.get("error", "")
            f.write(f"{url}\t{err}\n")
        f.write("\n")

    logger.info(f"Failed URL log saved: {log_path}")
    return log_path








_DEPRECATED_BROKEN_ASSET_REPORT = "broken_assets_report.html"

def _remove_deprecated_broken_asset_report(output_dir: str) -> None:
    """Remove the old HTML broken-asset report if it exists.

    v7.3.9 no longer generates broken_assets_report.html. Failed asset
    details are appended to backup_report.txt when available, or written to
    failed_assets.txt for non-website outputs. This cleanup only prevents stale
    HTML reports from older runs from staying visible in output folders.
    """
    try:
        target_dir = output_dir if output_dir else os.getcwd()
        stale = os.path.join(target_dir, _DEPRECATED_BROKEN_ASSET_REPORT)
        if os.path.exists(stale):
            os.remove(stale)
            logger.info(f"Removed deprecated report: {stale}")
    except Exception as e:
        logger.debug(f"Could not remove deprecated broken asset report: {e}")

def append_asset_failures_to_backup_report(
    failed_items: List[Dict[str, str]],
    report_path: str,
    *,
    source_url: str = "",
    title: str = "Asset Download Failures",
) -> Optional[str]:
    """Append failed asset details to backup_report.txt.

    This is the canonical reporting path from v7.3.9 onward. No separate
    broken_assets_report.html is created.
    """
    if not failed_items or not report_path:
        return None
    try:
        os.makedirs(os.path.dirname(report_path) or os.getcwd(), exist_ok=True)
        generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "",
            "=" * 60,
            title.upper(),
            "=" * 60,
            f"Generated : {generated}",
            f"Source    : {source_url or '-'}",
            f"Total     : {len(failed_items)}",
            "",
        ]
        for i, item in enumerate(failed_items, 1):
            url = item.get("url", "")
            path = item.get("path") or item.get("local") or ""
            err = item.get("error", "")
            kind = item.get("kind", "asset") or "asset"
            lines.append(f"[{i}] {kind}")
            lines.append(f"  Path : {path or '-'}")
            lines.append(f"  URL  : {url or '-'}")
            lines.append(f"  Err  : {err or '-'}")
            lines.append("")
        with open(report_path, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(lines))
        logger.info(f"Asset failure details appended to: {report_path}")
        return report_path
    except Exception as e:
        logger.debug(f"Could not append asset failure details to backup report: {e}")
        return None

def write_failed_assets_log(
    failed_items: List[Dict[str, str]],
    output_dir: str,
    *,
    source_url: str = "",
    title: str = "Asset Download Failures",
    filename: str = "failed_assets.txt",
) -> Optional[str]:
    """Write a plain text failed-assets log for non-website outputs.

    The old HTML report was intentionally removed because it created duplicate
    report files and did not add enough value over backup_report.txt/logs.
    """
    if not failed_items:
        return None
    try:
        target_dir = output_dir if output_dir else os.getcwd()
        os.makedirs(target_dir, exist_ok=True)
        report_path = _safe_join(target_dir, filename, fallback="failed_assets.txt")
        generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            title,
            "=" * len(title),
            f"Generated : {generated}",
            f"Source    : {source_url or '-'}",
            f"Total     : {len(failed_items)}",
            "",
        ]
        for i, item in enumerate(failed_items, 1):
            url = item.get("url", "")
            path = item.get("path") or item.get("local") or ""
            err = item.get("error", "")
            kind = item.get("kind", "asset") or "asset"
            lines.append(f"[{i}] {kind}")
            lines.append(f"  Path : {path or '-'}")
            lines.append(f"  URL  : {url or '-'}")
            lines.append(f"  Err  : {err or '-'}")
            lines.append("")
        pathlib.Path(report_path).write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Failed asset log saved: {report_path}")
        return report_path
    except Exception as e:
        logger.debug(f"Could not write failed asset log: {e}")
        return None

def write_asset_failure_summary(
    failed_items: List[Dict[str, str]],
    output_dir: str,
    *,
    source_url: str = "",
    title: str = "Asset Download Failures",
    filename: str = "failed_assets.txt",
    prefer_single_report: bool = True,
) -> Optional[str]:
    """Write failed asset details without creating an HTML report.

    Preferred behavior:
      1. Append to backup_report.txt when it exists.
      2. Otherwise write failed_assets.txt.
      3. Remove stale broken_assets_report.html from older runs.
    """
    if not failed_items:
        return None
    target_dir = output_dir if output_dir else os.getcwd()
    _remove_deprecated_broken_asset_report(target_dir)
    backup_path = os.path.join(target_dir, "backup_report.txt")
    if prefer_single_report and os.path.exists(backup_path):
        appended = append_asset_failures_to_backup_report(
            failed_items, backup_path, source_url=source_url, title=title
        )
        if appended:
            return appended
    return write_failed_assets_log(
        failed_items, target_dir, source_url=source_url, title=title, filename=filename
    )


def is_probable_url(value: str) -> bool:
    return bool(re.match(r"^https?://", str(value).strip(), re.IGNORECASE))


def _safe_rel_path(value: str, fallback: str = "asset") -> str:
    """Return a sanitized relative path safe for writing inside an output folder."""
    raw = unquote(str(value or "")).replace("\\", "/")
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\x00", "")
    parts: List[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        # Prevent Windows drive names and illegal filename characters.
        part = re.sub(r'^[A-Za-z]:', "_", part)
        part = re.sub(r'[<>:"|?*\x00-\x1f\x7f]', "_", part)
        part = part.strip(". ")
        if part:
            parts.append(part)
    return "/".join(parts) or fallback


def _safe_join(root: str, rel_path: str, fallback: str = "asset") -> str:
    """Join root + rel_path after sanitizing URL-derived asset paths."""
    root_abs = os.path.abspath(root or os.getcwd())
    safe_rel = _safe_rel_path(rel_path, fallback=fallback)
    target = os.path.abspath(os.path.join(root_abs, *safe_rel.split("/")))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise ValueError(f"Unsafe output path rejected: {rel_path!r}")
    return target


def _safe_archive_rel_path(member: str) -> str:
    """Validate archive member path strictly. Reject traversal instead of normalizing it."""
    raw = unquote(str(member or "")).replace("\\", "/").replace("\x00", "")
    if not raw or raw.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    parts: List[str] = []
    for part in raw.split("/"):
        if part in {"", ".", ".."} or part.strip() != part:
            raise ValueError(f"Unsafe archive path rejected: {member!r}")
        if re.match(r"^[A-Za-z]:", part) or any(ch in part for ch in "<>:\"|?*\x00"):
            raise ValueError(f"Unsafe archive path rejected: {member!r}")
        parts.append(part)
    if not parts:
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    return "/".join(parts)


def _safe_archive_join(root: str, member: str) -> str:
    """Join archive member to root only after strict archive-member validation."""
    root_abs = os.path.abspath(root or os.getcwd())
    rel = _safe_archive_rel_path(member)
    target = os.path.abspath(os.path.join(root_abs, *rel.split("/")))
    if target == root_abs or not target.startswith(root_abs + os.sep):
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    return target


def _copytree_merge_safe(src_dir: str, dst_dir: str, *, label: str = "assets") -> int:
    """Copy src_dir into dst_dir without deleting existing user/output folders."""
    if not src_dir or not os.path.isdir(src_dir):
        return 0
    count = 0
    for root, _dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        if rel_root == ".":
            rel_root = ""
        for name in files:
            src_file = os.path.join(root, name)
            rel = os.path.join(rel_root, name).replace("\\", "/")
            dst_file = _safe_join(dst_dir, rel, fallback=name or "asset")
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file)
                count += 1
            except Exception as e:
                logger.debug(f"Copy {label} failed: {src_file} → {dst_file}: {e}")
    return count


def _set_http2_enabled(enabled: bool) -> None:
    """Enable/disable optional HTTP/2 fetches. Falls back to requests if httpx is missing."""
    global _HTTP2_ENABLED
    _HTTP2_ENABLED = bool(enabled)
    if _HTTP2_ENABLED:
        try:
            import httpx  # type: ignore  # noqa: F401
            logger.info("HTTP/2 enabled for deep-scan fetches via httpx.")
        except Exception as e:
            _HTTP2_ENABLED = False
            logger.warning(f"HTTP/2 requested but httpx is unavailable: {e}. Install: pip install httpx[h2]")
    else:
        logger.info("HTTP/2 disabled; using requests.")


def prepare_clean_output_folder(folder: str) -> None:
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)


def _google_sheet_csv_export_url(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        return url
    gid_match = re.search(r"[?&]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"


def import_queue_items_from_source(source: str) -> List[Dict[str, str]]:
    source = (source or "").strip()
    if not source:
        return []
    if os.path.isfile(source):
        return import_queue_items_from_file(source)
    if not is_probable_url(source):
        return []
    url = _google_sheet_csv_export_url(source) if "docs.google.com/spreadsheets" in source else source
    try:
        r = fetch_response(url, timeout=30, extra_headers={"User-Agent": "Mozilla/5.0"}, as_bytes=True)
        if r is None:
            raise RuntimeError("request failed")
        text = _safe_response_text(r)
    except Exception as e:
        logger.error(f"Failed to import remote list: {e}")
        return []

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    items: List[Dict[str, str]] = []

    valid_modes = {
        "embed", "zip", "both",
        "website", "website_zip", "website_folder",
        "pure_website", "pure_website_zip", "pure_website_folder",
        "cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder",
    }

    def add_item(url_value: str, filename_value: str = "", mode_value: str = "") -> None:
        url_value      = (url_value      or "").strip()
        filename_value = (filename_value or "").strip()
        mode_value     = (mode_value     or "").strip().lower().replace("-", "_").replace(" ", "_")
        if url_value.startswith("#"):
            return
        if not is_probable_url(url_value):
            return
        if mode_value and mode_value not in valid_modes:
            logger.warning(f"Unknown mode '{mode_value}' for {url_value} — using default")
            mode_value = ""
        items.append({"url": url_value, "filename": filename_value, "mode": mode_value})

    if any(h in {"url", "link", "urls", "links"} for h in header):
        url_idx  = next((i for i, h in enumerate(header) if h in {"url", "link", "urls", "links"}), 0)
        fn_idx   = next((i for i, h in enumerate(header) if h in {"filename", "name", "output", "title", "file"}), -1)
        mode_idx = next((i for i, h in enumerate(header) if h in {"mode", "output_mode", "type"}), -1)
        for row in rows[1:]:
            if not row:
                continue
            url_v  = row[url_idx]  if url_idx  < len(row) else ""
            fn_v   = row[fn_idx]   if 0 <= fn_idx  < len(row) else ""
            mode_v = row[mode_idx] if 0 <= mode_idx < len(row) else ""
            add_item(url_v, fn_v, mode_v)
    else:
        for row in rows:
            if not row:
                continue
            add_item(
                row[0] if len(row) > 0 else "",
                row[1] if len(row) > 1 else "",
                row[2] if len(row) > 2 else "",
            )
    return items


def format_backup_report_text(
    *,
    start_url: str,
    project_url: str = "",
    project_root: str = "",
    project_aliases: Optional[List[str]] = None,
    downloaded: Optional[List[Dict[str, str]]] = None,
    failed: Optional[List[Dict[str, str]]] = None,
    downloaded_groups: Optional[Dict[str, List[str]]] = None,
    failed_groups: Optional[Dict[str, List[str]]] = None,
    notes: Optional[List[str]] = None,
) -> str:
    project_aliases = sorted(set(project_aliases or []))
    downloaded = downloaded or []
    failed = failed or []
    downloaded_groups = downloaded_groups or {}
    failed_groups = failed_groups or {}
    notes = notes or []

    lines = [
        "============================================================",
        " CYOA Backup Report",
        "============================================================",
        f"Start URL    : {start_url}",
        f"Project URL  : {project_url or '-'}",
        f"Project Root : {project_root or '-'}",
        f"Generated    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Downloaded   : {len(downloaded)}",
        f"Failed       : {len(failed)}",
    ]

    if project_aliases:
        lines.extend(["", "Project aliases:"])
        lines.extend([f"  - {item}" for item in project_aliases])

    if notes:
        lines.extend(["", "Notes:"])
        lines.extend([f"  - {note}" for note in notes])

    if downloaded_groups:
        lines.extend(["", "Downloaded by kind:"])
        for kind in sorted(downloaded_groups):
            files = sorted(set(downloaded_groups[kind]))
            lines.append(f"  [{kind}] {len(files)}")
            lines.extend([f"    ✓ {f}" for f in files])

    if failed_groups:
        lines.extend(["", "Failed by kind:"])
        for kind in sorted(failed_groups):
            files = sorted(set(failed_groups[kind]))
            lines.append(f"  [{kind}] {len(files)}")
            lines.extend([f"    ✗ {f}" for f in files])

    if downloaded:
        lines.extend(["", "Downloaded files:"])
        for item in downloaded:
            lines.append(f"  ✓ {item.get('local', '')}    ← {item.get('url', '')}")

    if failed:
        lines.extend(["", "Failed files:"])
        for item in failed:
            err = item.get("error", "")
            suffix = f"    ({err})" if err else ""
            lines.append(f"  ✗ {item.get('url', '')}{suffix}")

    return "\n".join(lines) + "\n"


def _cyoap_local_path(output_folder: str, remote_url: str) -> str:
    parsed = urlparse(remote_url)
    remote_path = unquote(parsed.path.lstrip("/"))
    if not remote_path or remote_path.endswith("/"):
        remote_path = remote_path + "index.html" if remote_path else "index.html"
    return _safe_join(output_folder, remote_path, fallback="index.html")


def _same_origin(url_a: str, url_b: str) -> bool:
    a = urlparse(url_a)
    b = urlparse(url_b)
    return a.scheme == b.scheme and a.netloc == b.netloc


def _candidate_urls_for_cyoap_asset(base_url: str, value: str, kind: str) -> List[str]:
    value = (value or "").strip()
    if not value or value.startswith("data:"):
        return []
    if is_probable_url(value):
        return [value]

    norm = value.lstrip("/")
    candidates: List[str] = [
        urljoin(base_url, norm),
        urljoin(base_url, quote(norm, safe="/:_-.")),
    ]

    if not norm.startswith("dist/"):
        if kind == "images":
            candidates.extend([
                urljoin(base_url, "dist/images/" + norm),
                urljoin(base_url, "dist/images/" + quote(norm, safe="/:_-.")),
            ])
        else:
            for folder in ("dist/audio/", "dist/media/", "dist/images/", "audio/", "media/"):
                candidates.extend([
                    urljoin(base_url, folder + norm),
                    urljoin(base_url, folder + quote(norm, safe="/:_-.")),
                ])

    dedup: List[str] = []
    seen: Set[str] = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def _scan_cyoap_assets(obj, image_set: Set[str], media_set: Set[str]) -> None:
    if obj is None:
        return
    if isinstance(obj, list):
        for item in obj:
            _scan_cyoap_assets(item, image_set, media_set)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                raw = value.strip()
                if not raw or raw.startswith("data:"):
                    continue
                path = urlparse(raw).path.lower()
                ext = os.path.splitext(path)[1]
                if key in IMAGE_FIELDS or ext in IMAGE_EXTENSIONS:
                    image_set.add(raw)
                elif ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
                    media_set.add(raw)
            else:
                _scan_cyoap_assets(value, image_set, media_set)


def try_download_cyoap_vue_site(
    start_url: str,
    output_folder: str,
    *,
    website_zip_output: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> bool:
    base_url = strip_document_from_url(start_url)
    platform_url = urljoin(base_url, "dist/platform.json")
    list_url = urljoin(base_url, "dist/nodes/list.json")

    session = create_retry_session()
    success_items: List[Dict[str, str]] = []
    failed_items: List[Dict[str, str]] = []
    downloaded_by_kind: Dict[str, List[str]] = {}
    failed_by_kind: Dict[str, List[str]] = {}
    seen_downloads: Set[str] = set()
    seen_lock = threading.Lock()
    image_set: Set[str] = set()
    media_set: Set[str] = set()
    report_lock = threading.Lock()

    def record_success(remote_url: str, local_path: str, kind: str) -> None:
        rel = os.path.relpath(local_path, output_folder).replace("\\", "/")
        with report_lock:
            success_items.append({"url": remote_url, "local": rel, "kind": kind})
            downloaded_by_kind.setdefault(kind, []).append(rel)

    def record_failed(remote_url: str, kind: str, error: str) -> None:
        with report_lock:
            failed_items.append({"url": remote_url, "local": "", "kind": kind, "error": error})
            failed_by_kind.setdefault(kind, []).append(remote_url)

    def fetch_remote(remote_url: str, *, kind: str = "assets", binary: bool = False, referrer: str = ""):
        headers = get_headers_for_url(remote_url) or {"User-Agent": "Mozilla/5.0"}
        if referrer:
            parsed = urlparse(referrer)
            headers.setdefault("Referer", referrer)
            headers.setdefault("Origin", f"{parsed.scheme}://{parsed.netloc}")
        try:
            r = fetch_response(remote_url, timeout=30, extra_headers=headers, as_bytes=True)
            if r is None:
                record_failed(remote_url, kind, "request failed")
                return None
            if r.status_code != 200:
                record_failed(remote_url, kind, f"HTTP {r.status_code}")
                return None
            return r.content if binary else _safe_response_text(r)
        except Exception as e:
            record_failed(remote_url, kind, str(e))
            return None

    platform_text = fetch_remote(platform_url, kind="json", binary=False, referrer=base_url)
    if platform_text is None:
        return False
    try:
        platform_obj = json.loads(platform_text)
    except Exception:
        return False

    list_text = fetch_remote(list_url, kind="json", binary=False, referrer=base_url)
    if list_text is None:
        return False

    logger.info("cyoap_vue detected: using dist/platform.json + dist/nodes/list.json flow")
    prepare_clean_output_folder(output_folder)

    platform_local = _cyoap_local_path(output_folder, platform_url)
    os.makedirs(os.path.dirname(platform_local), exist_ok=True)
    pathlib.Path(platform_local).write_text(platform_text, encoding="utf-8")
    seen_downloads.add(platform_url)
    record_success(platform_url, platform_local, "json")
    _scan_cyoap_assets(platform_obj, image_set, media_set)

    try:
        file_list = json.loads(list_text)
        if not isinstance(file_list, list):
            raise ValueError("list.json is not a list")
    except Exception as e:
        raise RuntimeError(f"cyoap_vue list.json invalid: {e}")

    list_local = _cyoap_local_path(output_folder, list_url)
    os.makedirs(os.path.dirname(list_local), exist_ok=True)
    pathlib.Path(list_local).write_text(list_text, encoding="utf-8")
    seen_downloads.add(list_url)
    record_success(list_url, list_local, "json")

    for fname in file_list:
        if not isinstance(fname, str) or not fname.strip():
            continue
        node_url = urljoin(base_url, "dist/nodes/" + fname.strip())
        text = fetch_remote(node_url, kind="json", binary=False, referrer=list_url)
        if text is None:
            continue
        node_local = _cyoap_local_path(output_folder, node_url)
        os.makedirs(os.path.dirname(node_local), exist_ok=True)
        pathlib.Path(node_local).write_text(text, encoding="utf-8")
        seen_downloads.add(node_url)
        record_success(node_url, node_local, "json")
        try:
            _scan_cyoap_assets(json.loads(text), image_set, media_set)
        except Exception:
            pass

    def _download_one_cyoap_asset(args: Tuple[str, str]) -> None:
        """Download a single cyoap_vue asset (image or media), trying candidate URLs in order."""
        item, bucket = args
        if item.startswith("data:"):
            return
        for remote_url in _candidate_urls_for_cyoap_asset(base_url, item, bucket):
            payload = fetch_remote(remote_url, kind=bucket, binary=True, referrer=base_url)
            if payload is None:
                continue
            local_path = _cyoap_local_path(output_folder, remote_url)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(payload)
            with seen_lock:
                seen_downloads.add(remote_url)
            record_success(remote_url, local_path, bucket)
            return
        record_failed(item, bucket, "asset not found in candidate cyoap_vue locations")

    all_assets: List[Tuple[str, str]] = (
        [(item, "images") for item in sorted(image_set) if not item.startswith("data:")] +
        [(item, "media")  for item in sorted(media_set)  if not item.startswith("data:")]
    )
    if all_assets:
        logger.info(f"Downloading {len(all_assets)} cyoap_vue asset(s) with {max_workers} thread(s)…")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(_download_one_cyoap_asset, all_assets))

    page_text = fetch_remote(start_url, kind="html", binary=False, referrer=base_url)
    if page_text is not None:
        page_local = _cyoap_local_path(output_folder, start_url)
        os.makedirs(os.path.dirname(page_local), exist_ok=True)
        pathlib.Path(page_local).write_text(page_text, encoding="utf-8")
        seen_downloads.add(start_url)
        record_success(start_url, page_local, "html")

        soup = BeautifulSoup(page_text, "html.parser")
        site_assets: List[str] = []
        for tag in soup.find_all("link"):
            href = tag.get("href", "").strip()
            if href:
                full = urljoin(start_url, href)
                if _same_origin(full, base_url):
                    site_assets.append(full)
        for tag in soup.find_all(["script", "img", "audio", "video", "source"]):
            src = tag.get("src", "").strip()
            if src:
                full = urljoin(start_url, src)
                if _same_origin(full, base_url):
                    site_assets.append(full)
        site_assets.append(urljoin(base_url, "favicon.ico"))

        seen_site: Set[str] = set()
        while site_assets:
            remote_url = site_assets.pop(0)
            if remote_url in seen_site:
                continue
            seen_site.add(remote_url)
            path = urlparse(remote_url).path.lower()
            ext = os.path.splitext(path)[1]
            if ext in FONT_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | {".ico"}:
                payload = fetch_remote(remote_url, kind="assets", binary=True, referrer=start_url)
                if payload is None:
                    continue
                local_path = _cyoap_local_path(output_folder, remote_url)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(payload)
                seen_downloads.add(remote_url)
                record_success(remote_url, local_path, "assets")
                continue

            text_payload = fetch_remote(remote_url, kind="assets", binary=False, referrer=start_url)
            if text_payload is None:
                continue
            local_path = _cyoap_local_path(output_folder, remote_url)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            pathlib.Path(local_path).write_text(text_payload, encoding="utf-8")
            seen_downloads.add(remote_url)
            kind = "css" if ext == ".css" else "js" if ext in SCRIPT_EXTENSIONS else "assets"
            record_success(remote_url, local_path, kind)

            if ext == ".css":
                for m in re.finditer(r'@import\s+(?:url\()?["\']?([^"\')\s]+)', text_payload, re.IGNORECASE):
                    child = urljoin(remote_url, m.group(1))
                    if _same_origin(child, base_url):
                        site_assets.append(child)
                for m in re.finditer(r'url\(([^)]+)\)', text_payload, re.IGNORECASE):
                    raw = m.group(1).strip().strip("'\"")
                    if not raw or raw.startswith("data:"):
                        continue
                    child = urljoin(remote_url, raw)
                    if _same_origin(child, base_url):
                        site_assets.append(child)

    report_path = os.path.join(output_folder, "backup_report.txt")
    report_text = format_backup_report_text(
        start_url=start_url,
        project_url=platform_url,
        project_root="dist/platform.json",
        downloaded=success_items,
        failed=failed_items,
        downloaded_groups=downloaded_by_kind,
        failed_groups=failed_by_kind,
        notes=["Engine mode: cyoap_vue", "Downloaded dist/platform.json and dist/nodes/list.json when available."],
    )
    pathlib.Path(report_path).write_text(report_text, encoding="utf-8")
    logger.info("cyoap_vue backup report saved: backup_report.txt")
    if failed_items:
        try:
            write_asset_failure_summary(
                failed_items, output_folder, source_url=start_url,
                title="Broken cyoap_vue Asset Report"
            )
        except Exception as e:
            logger.debug(f"Broken asset report could not be written: {e}")

    if website_zip_output:
        zip_name = os.path.basename(output_folder.rstrip(os.sep)) + ".zip"
        logger.info(f"Zipping → {zip_name}")
        zip_temp_folder(output_folder, zip_name=zip_name)
        shutil.rmtree(output_folder, ignore_errors=True)
    else:
        logger.info(f"Website folder kept: {output_folder}")

    logger.info("cyoap_vue website download complete.")
    return True

# ─────────────────────────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────────────────────────

# ── Cloudflare bypass globals ──────────────────────────────────────────────
use_cloudscraper: bool = False
_ytdlp_enabled:  bool = True   # set False via GUI unchecking "YT Audio" or --no-ytdlp
_HTTP2_ENABLED: bool = False    # optional: use httpx(http2=True) in deep-scan fetches when available

# Cloudflare engine selection.
# off          = never bypass
# auto         = normal request → cloudscraper → FlareSolverr when challenge is detected
# cloudscraper = use cloudscraper session first
# flaresolverr = use FlareSolverr browser solver when needed/selected
_CLOUDFLARE_MODE: str = "auto"
_FLARESOLVERR_URL: str = "http://localhost:8191/v1"
_FLARESOLVERR_SESSION_POLICY: str = "reuse-domain"
_FLARESOLVERR_TIMEOUT: int = 60
_FLARESOLVERR_WAIT_AFTER: int = 3
_FLARESOLVERR_PROXY_MODE: str = "inherit"
_FLARESOLVERR_SESSIONS: Dict[str, str] = {}
_FLARESOLVERR_LOCK = threading.Lock()


# ── Bandwidth throttle ─────────────────────────────────────────────────────
# 0 = unlimited. Set via GUI slider or --bandwidth CLI flag (KB/s).
import time as _time
import threading as _threading
_bandwidth_limit_kbps: float = 0.0
_bw_lock = _threading.Lock()
_bw_last_time: float = 0.0
_bw_bytes_this_window: int = 0

# Speed tracking callback — GUI sets this to record bytes for the speed graph
_gui_speed_cb: Optional[Any] = None   # fn(bytes_downloaded: int) → None

def _throttle_bandwidth(bytes_downloaded: int) -> None:
    """Sleep if necessary to keep download rate ≤ _bandwidth_limit_kbps."""
    global _bw_last_time, _bw_bytes_this_window
    # Record speed for GUI graph (always, even without throttle)
    if _gui_speed_cb is not None:
        try:
            _gui_speed_cb(bytes_downloaded)
        except Exception:
            pass
    limit = _bandwidth_limit_kbps
    if limit <= 0:
        return
    limit_bps = limit * 1024
    with _bw_lock:
        now = _time.monotonic()
        if _bw_last_time == 0.0:
            _bw_last_time = now
        elapsed = now - _bw_last_time
        _bw_bytes_this_window += bytes_downloaded
        expected_time = _bw_bytes_this_window / limit_bps
        sleep_for = expected_time - elapsed
        if sleep_for > 0:
            _time.sleep(sleep_for)
            _bw_last_time = _time.monotonic()
            _bw_bytes_this_window = 0
        elif elapsed > 1.0:
            # Reset window every second
            _bw_last_time = now
            _bw_bytes_this_window = 0

# ── Download history ───────────────────────────────────────────────────────
_HISTORY_FILE = os.path.join(
    os.path.expanduser("~"), ".cyoa_downloader", "download_history.json"
)
_SETTINGS_FILE = os.path.join(
    os.path.expanduser("~"), ".cyoa_downloader", "settings.json"
)
_SETTINGS_DEFAULTS: Dict[str, Any] = {
    "cyoa_mgr_enabled": None,   # None = auto (ON if DB found, OFF otherwise)
    "cyoa_mgr_db_path": "",     # custom DB path override
    "ai_api_key": "",           # Legacy/plain fallback only. Do not store secrets here by default.
    "ai_enabled": False,        # AI assist toggle (on/off)
    "ai_provider": "anthropic", # anthropic/openai/gemini/ollama
    "ai_model": "claude-sonnet-4-6",
    "ai_mode": "auto_fallback", # off/diagnostics/auto_fallback/aggressive_recovery
    "ai_key_storage": "session", # session/env/keyring/plain
    "ai_api_key_anthropic": "",
    "ai_api_key_openai": "",
    "ai_api_key_gemini": "",
    "ollama_url": "http://localhost:11434",
    "ai_max_calls_per_download": 3,
    "ai_max_html_chars": 8000,
    "ai_max_js_chars": 14000,
    "ai_confirm_large_payload": True,
    "language": "id",          # GUI language: id/en
    "http2_enabled": False,    # optional HTTP/2 via httpx for deep scans
    "dns": "",                 # plain DNS IP or DoH endpoint URL
    "bebasdns_variant": "",    # default/security/unfiltered/family when selected
    "gallery_dl_mode": "off",    # off/smart/force. Default off because gallery-dl needs post/gallery URLs
    "cloudflare_mode": "auto",   # off/auto/cloudscraper/flaresolverr
    "flaresolverr_url": "http://localhost:8191/v1",
    "flaresolverr_session_policy": "reuse-domain",  # temporary/reuse-domain/manual
    "flaresolverr_timeout": 60,   # seconds
    "flaresolverr_wait_after": 3, # seconds
    "flaresolverr_proxy_mode": "inherit",  # inherit/none
}

def _load_settings() -> Dict[str, Any]:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            merged = {**_SETTINGS_DEFAULTS, **data}
            # Migration: old versions stored Anthropic keys directly in settings.json.
            # Preserve old behavior only when an old key exists and no explicit storage mode was saved.
            if data.get("ai_api_key") and "ai_key_storage" not in data:
                merged["ai_key_storage"] = "plain"
            return merged
    except Exception:
        pass
    return dict(_SETTINGS_DEFAULTS)

def _save_settings(settings: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save settings: {e}")


# ── AI Assist provider + key storage ───────────────────────────────────────
AI_KEYRING_SERVICE = "cyoa_downloader"
_VALID_AI_KEY_STORAGE = {"session", "env", "keyring", "plain"}
_VALID_AI_MODES = {"off", "diagnostics", "auto_fallback", "aggressive_recovery"}
_VALID_AI_PROVIDERS = {"anthropic", "openai", "gemini", "ollama"}
AI_PROVIDER_LABELS: Dict[str, str] = {
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "ollama": "Ollama / Local",
}
AI_PROVIDER_ENV_VARS: Dict[str, List[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "ollama": [],
}
AI_MODEL_OPTIONS: Dict[str, List[str]] = {
    # Editable recommendations. Providers add/deprecate models over time; users can pass
    # any custom model id via CLI --ai-model. GUI presets use currently documented IDs.
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-5.5", "gpt-5.4", "gpt-4.1-mini"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.1-pro-preview", "gemini-3.5-flash"],
    "ollama": ["llama3.1", "qwen2.5-coder", "mistral", "gemma2"],
}
AI_PROVIDER_DEFAULT_MODEL: Dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.5",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.1",
}
OLLAMA_DEFAULT_URL = "http://localhost:11434"


def _normalize_ai_provider(value: str) -> str:
    v = (value or "anthropic").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "claude": "anthropic",
        "anthropic_claude": "anthropic",
        "open_ai": "openai",
        "gpt": "openai",
        "google": "gemini",
        "google_gemini": "gemini",
        "local": "ollama",
        "ollama_local": "ollama",
    }
    v = aliases.get(v, v)
    return v if v in _VALID_AI_PROVIDERS else "anthropic"


def _ai_provider_label(provider: str) -> str:
    return AI_PROVIDER_LABELS.get(_normalize_ai_provider(provider), provider or "AI")


def _ai_env_vars(provider: Optional[str] = None) -> List[str]:
    return AI_PROVIDER_ENV_VARS.get(_normalize_ai_provider(provider or _get_ai_provider()), [])


def _ai_primary_env_var(provider: Optional[str] = None) -> str:
    vars_ = _ai_env_vars(provider)
    return vars_[0] if vars_ else ""


def _ai_model_options(provider: Optional[str] = None) -> List[str]:
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return list(AI_MODEL_OPTIONS.get(p, []))


def _default_ai_model(provider: Optional[str] = None) -> str:
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return AI_PROVIDER_DEFAULT_MODEL.get(p, "claude-sonnet-4-6")


def _normalize_ai_key_storage(value: str) -> str:
    v = (value or "session").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "environment": "env",
        "environment_variable": "env",
        "os_credential_manager": "keyring",
        "credential_manager": "keyring",
        "os_keyring": "keyring",
        "settings": "plain",
        "settings_json": "plain",
        "plain_settings_json": "plain",
        "session_only": "session",
    }
    v = aliases.get(v, v)
    return v if v in _VALID_AI_KEY_STORAGE else "session"


def _normalize_ai_mode(value: str) -> str:
    v = (value or "auto_fallback").strip().lower().replace("-", "_")
    aliases = {"auto": "auto_fallback", "aggressive": "aggressive_recovery", "diagnostics_only": "diagnostics"}
    v = aliases.get(v, v)
    return v if v in _VALID_AI_MODES else "auto_fallback"



def _ai_provider_needs_key(provider: Optional[str] = None) -> bool:
    """Return True if this provider needs a remote API key."""
    return _normalize_ai_provider(provider or _get_ai_provider()) != "ollama"


def _ai_is_available(api_key: str = "", provider: Optional[str] = None) -> bool:
    """Provider-aware availability check. Ollama/local does not require an API key."""
    p = _normalize_ai_provider(provider or _get_ai_provider())
    return (p == "ollama") or bool((api_key or "").strip())


def _ai_mode_allows(kind: str, mode: Optional[str] = None) -> bool:
    """Map AI Assist mode to concrete behavior.

    kind values:
      - diagnostics: non-mutating viewer diagnostics/logging
      - project_detect: AI can suggest a project.json URL and the app may fetch it
      - asset_scan: AI can suggest extra JS/CSS/image/audio candidates
    """
    m = _normalize_ai_mode(mode or _load_settings().get("ai_mode", "auto_fallback"))
    if m == "off":
        return False
    if m == "diagnostics":
        return kind == "diagnostics"
    if m == "auto_fallback":
        return kind in {"diagnostics", "project_detect"}
    if m == "aggressive_recovery":
        return kind in {"diagnostics", "project_detect", "asset_scan"}
    return False


def _get_ai_int_setting(name: str, default: int, *, min_value: int = 0, max_value: int = 1000000) -> int:
    try:
        val = int(_load_settings().get(name, default) or default)
    except Exception:
        val = default
    return max(min_value, min(max_value, val))


class AIUsageBudget:
    """Small per-download budget so AI Assist cannot call paid APIs repeatedly by accident."""
    def __init__(self, max_calls: Optional[int] = None) -> None:
        self.max_calls = _get_ai_int_setting("ai_max_calls_per_download", 3, min_value=0, max_value=50) if max_calls is None else int(max_calls)
        self.calls = 0

    def can_call(self) -> bool:
        return self.max_calls <= 0 or self.calls < self.max_calls

    def consume(self, label: str = "AI") -> bool:
        if not self.can_call():
            logger.info(f"[{label}] AI call budget exhausted ({self.calls}/{self.max_calls})")
            return False
        self.calls += 1
        return True


def _ai_budget_consume(budget: Optional[AIUsageBudget], label: str) -> bool:
    if budget is None:
        return True
    return budget.consume(label)


def _clear_ai_plain_keys(settings: Optional[Dict[str, Any]] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    """Remove plain-text AI keys from settings. If provider is None, remove all provider keys."""
    st = settings if settings is not None else _load_settings()
    providers = [_normalize_ai_provider(provider)] if provider else list(_VALID_AI_PROVIDERS)
    for p in providers:
        if p != "ollama":
            st[f"ai_api_key_{p}"] = ""
    st["ai_api_key"] = ""  # legacy Anthropic key
    return st


def _sanitize_ai_candidate_url(value: str) -> Optional[str]:
    """Whitelist AI URL/path outputs before urljoin/fetch."""
    v = (value or "").strip().strip('"\'')
    if not v or v.upper() in {"NONE", "NULL", "N/A", "[]"}:
        return None
    if len(v) > 600 or any(ord(ch) < 32 for ch in v):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", v) or v.startswith(("\\", "///")):
        return None
    lower = v.lower()
    if lower.startswith(("javascript:", "file:", "data:", "mailto:", "ftp:", "chrome:", "about:")):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v) and not lower.startswith(("http://", "https://")):
        return None
    if v.startswith("//"):
        host = urlparse("https:" + v).hostname
        return v if host else None
    return v

def _get_ai_provider() -> str:
    st = _load_settings()
    return _normalize_ai_provider(st.get("ai_provider", "anthropic"))


def _get_ai_model(provider: Optional[str] = None) -> str:
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    m = (st.get("ai_model") or "").strip()
    if not m:
        return _default_ai_model(p)
    # If provider changed but the old provider's default model is still saved, move to the new provider default.
    other_defaults = {v for k, v in AI_PROVIDER_DEFAULT_MODEL.items() if k != p}
    return _default_ai_model(p) if m in other_defaults else m


def _plain_ai_key_setting(provider: Optional[str] = None) -> str:
    return f"ai_api_key_{_normalize_ai_provider(provider or _get_ai_provider())}"


def _keyring_username(provider: Optional[str] = None) -> str:
    return f"{_normalize_ai_provider(provider or _get_ai_provider())}_api_key"


def _keyring_module():
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


def _read_ai_key_from_keyring(provider: Optional[str] = None) -> str:
    kr = _keyring_module()
    if kr is None:
        return ""
    user = _keyring_username(provider)
    try:
        val = kr.get_password(AI_KEYRING_SERVICE, user) or ""
        if not val and _normalize_ai_provider(provider or _get_ai_provider()) == "anthropic":
            # Backward compatibility with v7.3.3 keyring username.
            val = kr.get_password(AI_KEYRING_SERVICE, "anthropic_api_key") or ""
        return val
    except Exception as e:
        logger.debug(f"AI keyring read failed: {e}")
        return ""


def _write_ai_key_to_keyring(api_key: str, provider: Optional[str] = None) -> bool:
    kr = _keyring_module()
    if kr is None:
        return False
    user = _keyring_username(provider)
    try:
        if api_key:
            kr.set_password(AI_KEYRING_SERVICE, user, api_key)
        else:
            for username in {user, "anthropic_api_key" if _normalize_ai_provider(provider or _get_ai_provider()) == "anthropic" else user}:
                try:
                    kr.delete_password(AI_KEYRING_SERVICE, username)
                except Exception:
                    pass
        return True
    except Exception as e:
        logger.warning(f"AI keyring write failed: {e}")
        return False


def _mask_secret(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "…" + value[-4:]


def _resolve_ai_api_key(explicit_key: str = "", session_key: str = "", storage: Optional[str] = None,
                        provider: Optional[str] = None) -> str:
    """Resolve a provider-specific AI key without forcing it into settings.json.

    Priority:
      1. explicit_key passed by CLI/caller
      2. storage=session  → in-memory session_key
      3. storage=env      → provider env var, e.g. ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY
      4. storage=keyring  → provider-specific OS credential entry
      5. storage=plain    → provider-specific settings.json fallback

    Ollama/local does not require an API key.
    """
    p = _normalize_ai_provider(provider or _get_ai_provider())
    if p == "ollama":
        return ""
    if explicit_key:
        return explicit_key.strip()
    st = _load_settings()
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    if mode == "session":
        return (session_key or "").strip()
    if mode == "env":
        for env_name in _ai_env_vars(p):
            val = os.environ.get(env_name, "").strip()
            if val:
                return val
        return ""
    if mode == "keyring":
        return _read_ai_key_from_keyring(p).strip()
    if mode == "plain":
        return (st.get(_plain_ai_key_setting(p)) or (st.get("ai_api_key") if p == "anthropic" else "") or "").strip()
    return ""


def _clear_ai_api_key_storage(storage: Optional[str] = None, provider: Optional[str] = None, clear_all: bool = False) -> None:
    """Clear AI API keys.

    clear_all=True removes session-adjacent persistent copies from both plain settings
    and OS Credential Manager for the selected provider. Environment variables cannot
    be removed from inside the process, so they are only reported to the user.
    """
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    if clear_all or mode == "plain":
        _clear_ai_plain_keys(st, p)
        _save_settings(st)
    if clear_all or mode == "keyring":
        _write_ai_key_to_keyring("", p)
    if mode == "env" and not clear_all:
        logger.info("AI key storage is environment-based; unset the environment variable to remove it.")


def _ai_key_status_text(storage: Optional[str] = None, session_key: str = "", provider: Optional[str] = None) -> str:
    st = _load_settings()
    p = _normalize_ai_provider(provider or st.get("ai_provider", "anthropic"))
    if p == "ollama":
        return f"{_ai_provider_label(p)} uses local Ollama and does not need an API key."
    mode = _normalize_ai_key_storage(storage or st.get("ai_key_storage", "session"))
    key = _resolve_ai_api_key(session_key=session_key, storage=mode, provider=p)
    if key:
        src = {"session": "session", "env": "/".join(_ai_env_vars(p)), "keyring": "OS Credential Manager", "plain": "settings.json"}.get(mode, mode)
        return f"{_ai_provider_label(p)} key found via {src}: {_mask_secret(key)}"
    if mode == "env":
        return f"No key found in {' or '.join(_ai_env_vars(p))}"
    if mode == "keyring":
        return "No key found in OS Credential Manager" if _keyring_module() else "keyring package not installed"
    if mode == "plain":
        return "No key saved in settings.json"
    return "Session key not set"
# ── CYOA Manager Integration ───────────────────────────────────────────────
# CYOA Manager (https://github.com/alexncode/CYOA-Manager) stores its library
# in a SQLite database. We can insert downloaded projects directly into it so
# they appear in CYOA Manager without manual import.
#
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

    Parameters
    ----------
    project_json_path : absolute path to the project.json file
    name              : display name in CYOA Manager library
    source_url        : original website URL (shown in card)
    description       : optional description
    tags              : list of tag strings
    viewer_preference : "icc-plus", "icc2-plus", "icc-original" etc.
    db_path           : path to library.sqlite3 (auto-detected if None)

    Returns True on success, False on failure.
    """
    import sqlite3 as _sql, uuid as _uuid, json as _json

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

    project_id   = str(_uuid.uuid4())
    display_name = name or os.path.splitext(os.path.basename(abs_path))[0]
    date_added   = __import__("datetime").datetime.now().isoformat()
    tags_json    = _json.dumps(tags or [])

    try:
        con = _sql.connect(db_path)
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
            con.close()
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
        con.close()

        logger.info(
            f"✓ Added to CYOA Manager: '{display_name}'\n"
            f"  file: {abs_path}\n"
            f"  db:   {db_path}"
        )
        return True

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
    import sqlite3
    if not db_path:
        db_path = _find_cyoa_manager_db()
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, source_url, file_path, date_added, viewer_preference "
            "FROM library_projects ORDER BY name COLLATE NOCASE"
        ).fetchall()
        conn.close()
        return [
            {k: (r[k] or "") for k in r.keys()}
            for r in rows if r["source_url"]
        ]
    except Exception as e:
        logger.warning(f"CYOA Manager list: {e}")
        return []



# ── Offline Viewer Registry ────────────────────────────────────────────────
# Stores uploaded offline viewer ZIPs for use instead of downloading the
# online viewer. Users upload e.g. ICCPlus offline ZIP, ICC offline ZIP, etc.
# Structure:
#   ~/.cyoa_downloader/
#     offline_viewers/
#       viewers.json          ← registry manifest
#       ICCPlus_v2.9.1.zip    ← uploaded viewer ZIP
#       ICC_offline.zip
#       ...

_VIEWERS_DIR      = os.path.join(os.path.expanduser("~"), ".cyoa_downloader", "offline_viewers")
_VIEWERS_MANIFEST = os.path.join(_VIEWERS_DIR, "viewers.json")

# Viewer type tags — used to match a CYOA site to the right viewer
# Detected from: script names, meta tags, HTML patterns in the CYOA site
VIEWER_TYPE_HINTS: Dict[str, List[str]] = {
    # ICC Plus v1.x (New Viewer 1.18.9, Viewer 1.8) — webpack, app.c533aa25.js
    # ICC Plus v2.x — Vite, core.js bootstrap loader
    "icc_plus":  ["app.c533aa25", "chunk-vendors", "ICC+", "icc-plus", "ICCPlus",
                  "core.js", "basePath +", "app.B6d7tc9y", "app_BuGW6RFa",
                  "vite_is_modern_browser"],
    # ICC Remix — template placeholder in HTML
    "icc_remix": ["ICC_PROJECT_DATA_SCRIPT", "icc_remix", "ICCRemix", "app.offline.js"],
    # Om1cr0n — custom viewer by alexncode (CYOA Manager bundled)
    # Loads via fetch("project.json"), Vite build, assets/index-*.js pattern
    "om1cr0n":   ["Om1cr0n", "animation.webp", "Bad End", "color-schema",
                  "assets/index-", "font-sans dark:text-white"],
    "cyoap_vue": ["platform.json", "cyoap", "cyoa_plus"],
    "custom":    [],
}

# ICC Plus marker — exists in ALL ICC Plus versions (v1.x, v2.x).
# The comment block in app.js right before the default state object.
_ICC_MARKER_RE = re.compile(
    r'(/\*!\s*Delete and replace this part[^*]*\*/'
    r'|//\s*Delete and replace this part[^\n]*\n)',
    re.DOTALL | re.IGNORECASE
)


def _load_viewers_manifest() -> Dict[str, Dict]:
    """Load offline viewer registry. Returns {viewer_id: {...metadata}}."""
    try:
        if os.path.exists(_VIEWERS_MANIFEST):
            with open(_VIEWERS_MANIFEST, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_viewers_manifest(manifest: Dict[str, Dict]) -> None:
    """Atomically save offline viewer registry."""
    try:
        os.makedirs(_VIEWERS_DIR, exist_ok=True)
        tmp = _VIEWERS_MANIFEST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _VIEWERS_MANIFEST)
    except Exception as e:
        logger.warning(f"Could not save viewers manifest: {e}")


def register_offline_viewer(
    zip_path: str,
    name: str = "",
    viewer_type: str = "custom",
    description: str = "",
    project_json_path: str = "",
    entry_point: str = "index.html",
) -> Optional[str]:
    """
    Register an offline viewer ZIP or RAR.
    Copies file to _VIEWERS_DIR and saves metadata to manifest.
    Returns viewer_id or None on failure.
    """
    import shutil, zipfile as _zf

    if not os.path.exists(zip_path):
        logger.error(f"Offline viewer file not found: {zip_path}")
        return None

    is_rar = zip_path.lower().endswith(".rar")

    # Validate archive
    try:
        if is_rar:
            import rarfile as _rf
            arc = _rf.RarFile(zip_path)
            names = arc.namelist()
            arc.close()
        else:
            if not _zf.is_zipfile(zip_path):
                logger.error(f"Not a valid ZIP: {zip_path}")
                return None
            with _zf.ZipFile(zip_path) as arc:
                names = arc.namelist()
    except Exception as e:
        logger.error(f"Cannot open archive {zip_path}: {e}")
        return None

    # Auto-detect entry point
    html_files = [n for n in names if n.endswith(".html") and n.count("/") <= 1]
    if not entry_point and html_files:
        entry_point = os.path.basename(html_files[0])

    # Auto-detect viewer type from filenames in archive
    js_files = " ".join(names)
    detected_type = viewer_type
    if detected_type == "custom":
        for vtype, hints in VIEWER_TYPE_HINTS.items():
            if any(h.lower() in js_files.lower() for h in hints):
                detected_type = vtype
                break

    os.makedirs(_VIEWERS_DIR, exist_ok=True)
    viewer_id = os.path.splitext(os.path.basename(zip_path))[0]
    dest      = os.path.join(_VIEWERS_DIR, os.path.basename(zip_path))
    if os.path.abspath(dest) != os.path.abspath(zip_path):
        shutil.copy2(zip_path, dest)

    manifest = _load_viewers_manifest()
    manifest[viewer_id] = {
        "name":              name or viewer_id,
        "zip_filename":      os.path.basename(zip_path),
        "viewer_type":       detected_type,
        "description":       description,
        "entry_point":       entry_point or "index.html",
        "project_json_path": project_json_path,
        "registered_at":     __import__("datetime").datetime.now().isoformat(),
    }
    _save_viewers_manifest(manifest)
    logger.info(f"Offline viewer registered: '{viewer_id}' (type: {detected_type})")
    return viewer_id


def _auto_register_bundled_viewers() -> None:
    """
    Called once at startup: register bundled viewer ZIPs if not yet registered.
    Also extracts LocalViewer and latest Viewer from ICCPlus-main.zip if present.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(_VIEWERS_DIR, exist_ok=True)

    # Handle ICCPlus-main.zip — extract LocalViewer and latest Viewer as separate ZIPs
    iccplus_main = os.path.join(script_dir, "ICCPlus-main.zip")
    if os.path.exists(iccplus_main):
        _extract_iccplus_subviewers(iccplus_main)

    # Plain viewer ZIPs/RARs in the script directory
    bundled = [
        ("ICC_Plus_Viewer_v2_9_1_local.zip", "ICC Plus v2.9.1 (Local)",  "icc_plus"),
        ("ICC_Remix.zip",                     "ICC Remix",                 "icc_remix"),
        ("ICCRemixLocal4.zip",                "ICC Remix Local v4",        "icc_remix"),
        ("Viewer_1_8.rar",                    "ICC Viewer 1.8",            "icc_plus"),
        ("New_Viewer_1_18_9.zip",             "New Viewer 1.18.9",         "icc_plus"),
    ]
    manifest = _load_viewers_manifest()
    for fname, display_name, vtype in bundled:
        vid = os.path.splitext(fname)[0]
        if vid in manifest:
            continue
        src = os.path.join(script_dir, fname)
        if os.path.exists(src):
            register_offline_viewer(src, name=display_name, viewer_type=vtype)


def _extract_iccplus_subviewers(iccplus_zip_path: str) -> None:
    """
    Extract LocalViewer and latest Viewer from ICCPlus-main.zip,
    package each as its own ZIP in _VIEWERS_DIR, and register them.
    LocalViewer is preferred for offline use (simpler, single JS file).
    """
    import zipfile as _zf, io

    manifest = _load_viewers_manifest()

    sub_viewers = [
        # (folder_prefix_in_zip, dest_name, display_name, priority)
        ("ICCPlus-main/LocalViewer/", "ICCPlus_LocalViewer.zip",
         "ICC Plus LocalViewer (recommended)", "icc_plus", "high"),
        ("ICCPlus-main/Viewer/",      "ICCPlus_Viewer_latest.zip",
         "ICC Plus Viewer (latest)",  "icc_plus", "normal"),
    ]

    try:
        with _zf.ZipFile(iccplus_zip_path) as main_zf:
            all_names = main_zf.namelist()

            for prefix, dest_fname, display, vtype, priority in sub_viewers:
                vid = os.path.splitext(dest_fname)[0]
                if vid in manifest:
                    continue

                dest_path = os.path.join(_VIEWERS_DIR, dest_fname)
                members   = [n for n in all_names if n.startswith(prefix) and not n.endswith('/')]

                if not members:
                    continue

                # Re-package as flat ZIP (strip prefix)
                buf = io.BytesIO()
                with _zf.ZipFile(buf, 'w', _zf.ZIP_DEFLATED) as out_zf:
                    for member in members:
                        rel = member[len(prefix):]
                        if rel:
                            data = main_zf.read(member)
                            out_zf.writestr(rel, data)

                with open(dest_path, 'wb') as f:
                    f.write(buf.getvalue())

                register_offline_viewer(
                    dest_path,
                    name=display,
                    viewer_type=vtype,
                )
                logger.info(f"Extracted from ICCPlus-main.zip: {dest_fname} ({len(members)} files)")

    except Exception as e:
        logger.warning(f"Could not process ICCPlus-main.zip: {e}")



def unregister_offline_viewer(viewer_id: str, delete_zip: bool = False) -> bool:
    """Remove a viewer from the registry, optionally delete the ZIP."""
    manifest = _load_viewers_manifest()
    if viewer_id not in manifest:
        return False
    entry = manifest.pop(viewer_id)
    if delete_zip:
        zip_path = os.path.join(_VIEWERS_DIR, entry.get("zip_filename", ""))
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except Exception as e:
            logger.warning(f"Could not delete viewer ZIP: {e}")
    _save_viewers_manifest(manifest)
    logger.info(f"Offline viewer removed: {viewer_id!r}")
    return True


def get_viewer_for_site(html_text: str, mode: str = "auto") -> Optional[Dict]:
    """
    Given the HTML content of a CYOA site and the download mode,
    return the best matching registered offline viewer (or None).

    Scoring priority:
      - ICC Remix site (has {{ICC_PROJECT_DATA_SCRIPT}} or app.offline.js): +20
      - ICC Plus v2 site (core.js / basePath): hints match
      - ICC Plus v1 site (app.c533aa25.js): hints match
      - LocalViewer name bonus: +5
      - Mode type match: +10
    """
    manifest = _load_viewers_manifest()
    if not manifest:
        return None

    html_lower = html_text.lower()
    mode_to_type = {
        "website_zip": "icc_plus", "website_folder": "icc_plus",
        "pure_website_zip": "custom", "pure_website_folder": "custom",
        "cyoap_vue_zip": "cyoap_vue", "cyoap_vue_folder": "cyoap_vue",
    }
    preferred_type = mode_to_type.get(mode, "")

    # Detect ICC Remix sites by template marker
    is_remix = (
        "{{ICC_PROJECT_DATA_SCRIPT}}" in html_text or
        "app.offline.js" in html_lower or
        "iccremix" in html_lower
    )

    scored: List[tuple] = []
    for vid, meta in manifest.items():
        score = 0
        vtype = meta.get("viewer_type", "custom")
        hints = VIEWER_TYPE_HINTS.get(vtype, [])
        for hint in hints:
            if hint.lower() in html_lower:
                score += 1
        # Strong bonus for ICC Remix sites
        if is_remix and vtype == "icc_remix":
            score += 20
        if preferred_type and vtype == preferred_type:
            score += 10
        # LocalViewer is designed for offline — prefer it
        name_lower = meta.get("name", "").lower()
        if "localviewer" in name_lower or "local" in name_lower:
            score += 5
        scored.append((score, vid, meta))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_id, best_meta = scored[0]
    if best_score == 0:
        return None
    return {"id": best_id, **best_meta}



def _build_html_interceptor(data_js: str, size_bytes: int) -> str:
    """Build the HTML fetch/XHR interceptor script tag (fallback when JS embed fails)."""
    return (
        f'<script id="__cyoa_offline_patch__">'
        f'(function(){{'
        f'var D={data_js};'
        f'var R=/project\\.json|data\\.json/i;'
        f'var _f=window.fetch;'
        f'window.fetch=function(u,o){{'
        f'if(R.test(String(u||"")))return Promise.resolve(new Response(JSON.stringify(D),'
        f'{{status:200,headers:{{"Content-Type":"application/json"}}}}));'
        f'return _f?_f.call(this,u,o):Promise.reject(new Error("fetch N/A"));'
        f'}};'
        f'window.__CYOA_OFFLINE__=true;window.__CYOA_DATA__=D;'
        f'document.addEventListener("DOMContentLoaded",function(){{'
        f'var el=document.getElementById("projectSize");'
        f'if(el)el.textContent="{size_bytes}";'
        f'}});'
        f'}})();'
        f'</script>\n'
    )


def _inject_into_head(html: str, script: str) -> str:
    """Insert script as first element inside <head>."""
    for marker in ("<head>", "<HEAD>"):
        idx = html.lower().find(marker.lower())
        if idx != -1:
            end = html.find(">", idx)
            insert_at = (end + 1) if end != -1 else (idx + len(marker))
            return html[:insert_at] + "\n" + script + html[insert_at:]
    return script + "\n" + html


def _unique_folder(base: str) -> str:
    """Return *base* if it does not exist, else base_1, base_2, …"""
    if not os.path.exists(base):
        return base
    counter = 1
    while os.path.exists(f"{base}_{counter}"):
        counter += 1
    candidate = f"{base}_{counter}"
    logger.info(f"Output folder collision: {base!r} exists → using {candidate!r}")
    return candidate


def _apply_offline_viewer(
    output_dir: str,
    project_json_str: str,
    viewer_meta: Dict,
    file_name: str = "project",
    asset_source_dirs: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Extract an offline viewer ZIP into output_dir and inject project data.

    Supports two injection strategies auto-detected from index.html:
      A) Template markers (ICC_Remix): replaces {{ICC_PROJECT_DATA_SCRIPT}},
         {{ICC_PROJECT_SIZE}}, {{ICC_SITE_TITLE}}, {{ICC_FAVICON_TAG}}
      B) Fetch interceptor (ICC_Plus, New_Viewer, Viewer_1.8):
         - Writes project.json to root
         - Injects window.fetch / XHR override as first <head> script so
           CYOA works on file:// without a local server

    Returns path to index.html on success, None on failure.
    """
    import zipfile as _zf, shutil

    zip_filename = viewer_meta.get("zip_filename", "")
    zip_path     = os.path.join(_VIEWERS_DIR, zip_filename)
    entry_point  = viewer_meta.get("entry_point", "index.html")
    is_rar       = zip_path.lower().endswith(".rar")

    if is_rar:
        try:
            import rarfile as _rf
        except ImportError:
            logger.error("rarfile package required for .rar viewers: pip install rarfile")
            return None

    if not os.path.exists(zip_path):
        logger.error(f"Offline viewer ZIP not found: {zip_path}")
        return None

    # ── Extract viewer ────────────────────────────────────────────────
    site_folder = _unique_folder(os.path.join(output_dir, file_name + "_offline"))
    os.makedirs(site_folder, exist_ok=True)

    try:
        if is_rar:
            arc = _rf.RarFile(zip_path)
        else:
            arc = _zf.ZipFile(zip_path)

        with arc:
            members = arc.namelist()
            # Detect if all files are under a single root folder (e.g. "Viewer 1.8/")
            roots = set(m.split("/")[0] for m in members if m.strip("/"))
            strip_prefix = ""
            if len(roots) == 1:
                root_dir = next(iter(roots))
                if all(m.startswith(root_dir + "/") or m == root_dir + "/" for m in members):
                    strip_prefix = root_dir + "/"

            for member in members:
                target_rel = member[len(strip_prefix):] if strip_prefix else member
                if not target_rel or target_rel.endswith("/"):
                    continue
                target_path = _safe_archive_join(site_folder, target_rel)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                data = arc.read(member)
                with open(target_path, "wb") as f:
                    f.write(data)

        logger.info(f"Offline viewer extracted: {site_folder}/")
    except Exception as e:
        logger.error(f"Failed to extract viewer: {e}")
        shutil.rmtree(site_folder, ignore_errors=True)
        return None

    # ── Read index.html ───────────────────────────────────────────────
    index_path = os.path.join(site_folder, entry_point)
    if not os.path.exists(index_path):
        # Try one level deep
        for root, _, files in os.walk(site_folder):
            if entry_point in files:
                index_path = os.path.join(root, entry_point)
                break

    if not os.path.exists(index_path):
        logger.error(f"entry_point '{entry_point}' not found in extracted viewer")
        return None

    try:
        html = pathlib.Path(index_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Cannot read index.html: {e}")
        return None

    # Parse metadata from project JSON
    size_bytes = len(project_json_str.encode("utf-8"))
    try:
        proj_obj = json.loads(project_json_str)
        proj_title = (
            proj_obj.get("app", proj_obj).get("title") or
            proj_obj.get("app", proj_obj).get("name") or
            file_name
        )
    except Exception:
        proj_title = file_name

    data_js = json.dumps(
        json.loads(project_json_str) if project_json_str.strip().startswith("{") else {},
        ensure_ascii=False, separators=(",", ":")
    )

    # ── Strategy A: Template markers (ICC_Remix style) ─────────────────
    if "{{ICC_PROJECT_DATA_SCRIPT}}" in html:
        logger.info("Offline inject: using template strategy (ICC_Remix)")
        # ICC Remix startup reads: window.__CYOA_PROJECT__
        # (also set legacy names for compatibility)
        data_script = (
            f'<script id="__icc_offline_data__">'
            f'window.__CYOA_PROJECT__={data_js};'
            f'window.__ICCPLUS_DATA__={data_js};'
            f'window.__CYOA_DATA__={data_js};'
            f'</script>'
        )
        html = html.replace("{{ICC_PROJECT_DATA_SCRIPT}}", data_script)
        html = html.replace("{{ICC_PROJECT_SIZE}}", str(size_bytes))
        html = html.replace("{{ICC_SITE_TITLE}}", proj_title)
        html = html.replace("{{ICC_FAVICON_TAG}}", "")
        # Also write physical project.json for fallback
        with open(os.path.join(site_folder, "project.json"), "w",
                  encoding="utf-8") as f:
            f.write(project_json_str)

    # ── Strategy B: ICC Plus marker injection (version-agnostic) ──────
    # ICC Plus has a documented injection point in app.js:
    #   /*! Delete and replace this part with your project if you're pasting it in... */
    #   {DEFAULT_STATE}
    # This marker exists in ALL ICC Plus versions — v1.x, v2.x, and future.
    # We replace {DEFAULT_STATE} with the actual project data JSON.
    else:
        # Scan all JS files for ICC Plus marker
        icc_marker_file = None
        icc_marker_js   = None
        for _root, _, _files in os.walk(site_folder):
            for _fname in _files:
                if not _fname.endswith(".js"):
                    continue
                _fp = os.path.join(_root, _fname)
                try:
                    _jt = pathlib.Path(_fp).read_text(encoding="utf-8", errors="replace")
                    if _ICC_MARKER_RE.search(_jt):
                        icc_marker_file = _fp
                        icc_marker_js   = _jt
                        break
                except Exception:
                    pass
            if icc_marker_file:
                break

        if icc_marker_file:
            logger.info(
                f"Offline inject: Strategy B — ICC Plus marker injection "
                f"({os.path.basename(icc_marker_file)})"
            )
            try:
                _proj = json.loads(project_json_str)
                # ICC Plus marker injection point receives the full project state:
                # - v1.18 (app.c533aa25.js): state.app = full flat JSON
                # - v2 (app.B6d7tc9y.js):   state.app = full flat JSON
                # Both read the WHOLE project JSON (rows, backpack, app flags all at root)
                # We inject the full root object, NOT just proj["app"],
                # because "app" sub-key only exists in some export formats.
                _app_js = json.dumps(_proj, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                _app_js = data_js

            # ── Balanced-brace injection ────────────────────────────────
            # Find the exact {DEFAULT_STATE} after the marker using brace counting
            # — far more reliable than a regex on minified JS with nested objects.
            _MARKER_SEARCH = re.compile(
                r'(/\*!\s*Delete and replace this part[^*]*\*/\s*'
                r'|//\s*Delete and replace this part[^\n]*\n)',
                re.DOTALL | re.IGNORECASE
            )
            _m = _MARKER_SEARCH.search(icc_marker_js)
            _injected = False
            if _m:
                _after   = icc_marker_js[_m.end():]
                _brace_i = _after.find('{')
                if _brace_i != -1:
                    _depth = 0; _i = _brace_i
                    while _i < len(_after):
                        if _after[_i] == '{':   _depth += 1
                        elif _after[_i] == '}':
                            _depth -= 1
                            if _depth == 0:
                                _state_end = _i + 1
                                break
                        _i += 1
                    else:
                        _state_end = -1

                    if _state_end != -1:
                        _abs_start = _m.end() + _brace_i
                        _abs_end   = _m.end() + _state_end
                        _patched_js = (
                            icc_marker_js[:_abs_start]
                            + _app_js
                            + icc_marker_js[_abs_end:]
                        )
                        pathlib.Path(icc_marker_file).write_text(_patched_js, encoding="utf-8")
                        logger.info(
                            f"  Marker inject OK: {len(_app_js):,} chars → "
                            f"{os.path.basename(icc_marker_file)} "
                            f"(was {_abs_end - _abs_start:,} chars default state)"
                        )
                        _injected = True

            if not _injected:
                logger.warning("ICC Plus marker found but injection failed — falling to Strategy C")
                icc_marker_file = None

        # ── Strategy C: fetch() patch + prepend data to app.js ──────────
        if not icc_marker_file:
            logger.info("Offline inject: Strategy C — fetch() patch in app.js")

            with open(os.path.join(site_folder, "project.json"), "w",
                      encoding="utf-8") as f:
                f.write(project_json_str)

            _fetch_patterns = [
                re.compile(r'fetch\("project\.json"\)'),
                re.compile(r"fetch\('project\.json'\)"),
                re.compile(r'fetch\("\.\/project\.json"\)'),
            ]
            for _root, _, _files in os.walk(site_folder):
                for _fname in _files:
                    if not _fname.endswith((".js", ".mjs")):
                        continue
                    _fp = os.path.join(_root, _fname)
                    try:
                        _jt = pathlib.Path(_fp).read_text(encoding="utf-8", errors="replace")
                        if not any(p.search(_jt) for p in _fetch_patterns):
                            continue
                        _preamble = f"window.__CYOA_PROJECT__={data_js};\n"
                        _inline   = (
                            'Promise.resolve(new Response('
                            'JSON.stringify(window.__CYOA_PROJECT__),'
                            '{"headers":{"Content-Type":"application/json"}}'
                            '))'
                        )
                        _pj = _jt
                        for _p in _fetch_patterns:
                            _pj = _p.sub(_inline, _pj)
                        pathlib.Path(_fp).write_text(_preamble + _pj, encoding="utf-8")
                        logger.info(f"  fetch() patched: {os.path.relpath(_fp, site_folder)}")
                    except Exception as _e:
                        logger.warning(f"  Cannot patch {_fname}: {_e}")

    # ── Update projectSize div (shared by all strategies) ──────────────
    html = re.sub(
        r'(<div[^>]+id=["\']projectSize["\'][^>]*>)\s*[^<]*',
        rf'\g<1>{size_bytes}',
        html,
    )

    # ── Inject cheat overlay ────────────────────────────────────────────
    # Inspired by CYOA Manager's viewer_overlay.js — floating gear button
    # that opens a panel to modify points, remove requirements, etc.
    # Works by polling window.app.__vue__.$store.state.app every 500ms.
    # Injected just before </body> so it doesn't interfere with app init.
    _CHEAT_OVERLAY = r"""
<style id="__cyoa_cheat_style__">
#__cyoa_gear{position:fixed;right:10px;bottom:10px;width:32px;height:32px;border:none;border-radius:9px;
display:flex;align-items:center;justify-content:center;
background:rgba(30,36,48,0.75);color:rgba(255,255,255,0.85);
backdrop-filter:blur(8px);box-shadow:0 4px 14px rgba(0,0,0,0.3);
cursor:pointer;z-index:2147483647;font-size:18px;transition:background .15s}
#__cyoa_gear:hover{background:rgba(60,70,90,0.92)}
#__cyoa_panel{position:fixed;right:10px;bottom:50px;width:230px;padding:14px;border-radius:12px;
background:rgba(18,22,32,0.94);color:#e8eeff;
box-shadow:0 12px 32px rgba(0,0,0,0.4);z-index:2147483647;
font:12px/1.5 system-ui,sans-serif;display:none}
#__cyoa_panel.open{display:block}
.__cy_title{font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;
color:rgba(150,170,220,.7);margin-bottom:10px}
.__cy_row{display:flex;gap:8px;margin-bottom:8px;align-items:flex-end}
.__cy_lbl{font-size:10px;color:rgba(200,215,245,.6);margin-bottom:3px}
.__cy_sel,.__cy_inp{background:rgba(255,255,255,.07);border:1px solid rgba(120,140,190,.25);
border-radius:7px;color:#f0f4ff;padding:5px 8px;font:inherit;box-sizing:border-box}
.__cy_sel{appearance:auto;flex:1.2}
.__cy_inp{flex:.8;width:0}
.__cy_btn{width:100%;margin-top:6px;background:rgba(255,255,255,.07);
border:1px solid rgba(120,140,190,.25);border-radius:7px;color:#e8eeff;
padding:7px;font:inherit;cursor:pointer;text-align:left;transition:background .12s}
.__cy_btn:hover{background:rgba(255,255,255,.14)}
.__cy_sep{border:none;border-top:1px solid rgba(120,140,190,.15);margin:8px 0}
/* Autoplay unblock banner */
#__cyoa_audio_banner{position:fixed;top:0;left:0;right:0;padding:10px 16px;
background:rgba(16,20,30,0.92);backdrop-filter:blur(6px);
color:#e8eeff;font:13px/1.4 system-ui,sans-serif;
display:flex;align-items:center;gap:12px;z-index:2147483646;
border-bottom:1px solid rgba(99,140,255,.3)}
#__cyoa_audio_banner button{background:#3b82f6;border:none;border-radius:6px;
color:#fff;padding:6px 14px;font:inherit;cursor:pointer;white-space:nowrap}
</style>
<div id="__cyoa_audio_banner" style="display:none">
  <span>🔇 Audio diblokir browser (autoplay policy)</span>
  <button onclick="__cyoaUnblockAudio()">▶ Aktifkan Audio</button>
  <span style="margin-left:auto;cursor:pointer;opacity:.6" onclick="document.getElementById('__cyoa_audio_banner').style.display='none'">✕</span>
</div>
<div id="__cyoa_panel">
<div class="__cy_title">⚡ Cheat Menu</div>
<div class="__cy_row">
<label style="flex:1.2"><div class="__cy_lbl">Point type</div>
<select class="__cy_sel" id="__cyoa_pt_sel"></select></label>
<label style="flex:.8"><div class="__cy_lbl">Value</div>
<input class="__cy_inp" id="__cyoa_pt_val" type="number" step="1"></label>
</div>
<button class="__cy_btn" id="__cyoa_set_pts">💰 Set Points</button>
<hr class="__cy_sep">
<button class="__cy_btn" id="__cyoa_rm_reqs">🔓 Remove All Requirements</button>
<button class="__cy_btn" id="__cyoa_unlim">♾️ Unlimited Choices (all rows)</button>
<button class="__cy_btn" id="__cyoa_sel_all">✅ Select All Choices</button>
<button class="__cy_btn" id="__cyoa_desel_all">☐ Deselect All Choices</button>
</div>
<button id="__cyoa_gear" title="Cheat Menu" aria-label="Cheat Menu">⚙</button>
<script id="__cyoa_cheat_script__">(function(){
var btn=document.getElementById('__cyoa_gear'),
    panel=document.getElementById('__cyoa_panel'),
    ptSel=document.getElementById('__cyoa_pt_sel'),
    ptVal=document.getElementById('__cyoa_pt_val'),
    audioBanner=document.getElementById('__cyoa_audio_banner');
if(!btn||!panel)return;
btn.onclick=function(){panel.classList.toggle('open');if(panel.classList.contains('open'))refresh()};

// ── Autoplay unblock ────────────────────────────────────────────────
// ICC Plus v2 uses No() which calls audio.play() on load.
// If autoplay is blocked, we show a banner so user can explicitly unlock.
var _audioUnblocked=false;
window.__cyoaUnblockAudio=function(){
  _audioUnblocked=true;
  if(audioBanner)audioBanner.style.display='none';
  // Resume any pending audio context
  if(window.AudioContext||window.webkitAudioContext){
    try{var ac=new (window.AudioContext||window.webkitAudioContext)();ac.resume();}catch(e){}
  }
  // Force-play any paused audio elements
  document.querySelectorAll('audio').forEach(function(a){
    if(a.src&&a.paused&&a.readyState>0){a.play().catch(function(){});}
  });
  // Retry ICC Plus bgm
  var app=getApp();
  if(app&&app.bgmId&&window._cyoa_bgm_load){
    try{window._cyoa_bgm_load(app.bgmId);}catch(e){}
  }
};

// Intercept audio play failures and show banner
var _origPlay=HTMLAudioElement.prototype.play;
HTMLAudioElement.prototype.play=function(){
  var self=this, r=_origPlay.call(this);
  if(r&&typeof r.catch==='function'){
    r.catch(function(e){
      var msg=String(e);
      if(!_audioUnblocked&&audioBanner&&
         (msg.indexOf('NotAllowed')!==-1||msg.indexOf('interact')!==-1||msg.indexOf('autoplay')!==-1)){
        audioBanner.style.display='flex';
      }
    });
  }
  return r;
};

function getApp(){try{
var s=window.app&&window.app.__vue__&&window.app.__vue__.$store&&window.app.__vue__.$store.state;
if(s&&s.app&&Array.isArray(s.app.pointTypes))return s.app;
if(window.__pinia){var stores=Object.values(window.__pinia.state.value);
for(var i=0;i<stores.length;i++){if(stores[i]&&Array.isArray(stores[i].pointTypes))return stores[i];}}
}catch(e){}return null;}
function refresh(){var app=getApp();if(!app)return;
var pts=app.pointTypes||[];ptSel.innerHTML='';
pts.forEach(function(p,i){if(!p)return;var o=document.createElement('option');
o.value=i;o.textContent=(p.name||('Point '+i))+' ('+Math.round(p.startingSum||0)+')';ptSel.appendChild(o);});
if(ptSel.options.length>0){var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((pts[idx]||{}).startingSum||0);}}
ptSel.onchange=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0;
ptVal.value=Math.round((app.pointTypes[idx]||{}).startingSum||0);};
document.getElementById('__cyoa_set_pts').onclick=function(){var app=getApp();if(!app)return;
var idx=parseInt(ptSel.value)||0,v=parseFloat(ptVal.value);
if(!isNaN(v)&&app.pointTypes[idx]){app.pointTypes[idx].startingSum=v;refresh();}};
document.getElementById('__cyoa_rm_reqs').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(!r)return;delete r.requireds;
(r.objects||[]).forEach(function(o){if(o)delete o.requireds;});});
this.textContent='✓ Requirements removed';var self=this;setTimeout(function(){self.textContent='🔓 Remove All Requirements';},1500);};
document.getElementById('__cyoa_unlim').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){if(r)r.allowedChoices=0;});
this.textContent='✓ Done';var self=this;setTimeout(function(){self.textContent='♾️ Unlimited Choices (all rows)';},1500);};
document.getElementById('__cyoa_sel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o&&o.id)o.isSelected=true;});});};
document.getElementById('__cyoa_desel_all').onclick=function(){var app=getApp();if(!app)return;
(app.rows||[]).forEach(function(r){(r&&r.objects||[]).forEach(function(o){if(o)o.isSelected=false;});});};
var t=setInterval(function(){if(getApp()){if(panel.classList.contains('open'))refresh();clearInterval(t);}},500);
setTimeout(function(){clearInterval(t);},30000);
})();</script>"""

    if "</body>" in html:
        html = html.replace("</body>", _CHEAT_OVERLAY + "\n</body>", 1)
    elif "</html>" in html:
        html = html.replace("</html>", _CHEAT_OVERLAY + "\n</html>", 1)
    else:
        html += _CHEAT_OVERLAY

    # ── Copy images/ and audio/ folders directly into the offline viewer ───
    # The caller passes temp asset folders explicitly. This avoids copying to or
    # deleting output_dir/images and output_dir/audio, which may belong to another run.
    _asset_sources: Dict[str, str] = dict(asset_source_dirs or {})
    if not _asset_sources:
        # Backward-compatible fallback for older callers only. Never delete roots.
        for _asset_dir_name in ("images", "audio"):
            for _candidate in (
                os.path.join(output_dir, file_name, _asset_dir_name),
                os.path.join(output_dir, _asset_dir_name),
            ):
                if os.path.isdir(_candidate):
                    _asset_sources.setdefault(_asset_dir_name, _candidate)
                    break
    for _asset_dir_name in ("images", "audio"):
        _asset_src_dir = _asset_sources.get(_asset_dir_name, "")
        if os.path.isdir(_asset_src_dir):
            _asset_dst = os.path.join(site_folder, _asset_dir_name)
            _n = _copytree_merge_safe(_asset_src_dir, _asset_dst, label=_asset_dir_name)
            if _n:
                logger.info(
                    f"  Copied {_n} {_asset_dir_name} file(s) → "
                    f"{os.path.relpath(_asset_dst, output_dir)}"
                )

    try:
        pathlib.Path(index_path).write_text(html, encoding="utf-8")
        logger.info(
            f"✓ Offline viewer ready → {os.path.relpath(index_path, output_dir)} "
            f"({size_bytes/1024/1024:.1f} MB data, viewer: '{viewer_meta.get('name','')}') "
            f"— double-click index.html to play offline."
        )
        return index_path
    except Exception as e:
        logger.error(f"Cannot write patched index.html: {e}")
        return None




def _load_history() -> Dict[str, Dict]:
    try:
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def _save_history(history: Dict[str, Dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
        tmp = _HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _HISTORY_FILE)
    except Exception as e:
        logger.debug(f"History save failed: {e}")

def _record_history(url: str, file_name: str, mode: str, success: bool) -> None:
    history = _load_history()
    entry = {
        "last_downloaded": __import__("datetime").datetime.now().isoformat(),
        "file_name":       file_name,
        "filename":        file_name,   # alias for batch update
        "mode":            mode,
        "success":         success,
        "url":             url,
    }
    # Capture server metadata for future batch update checking
    if success:
        try:
            r = fetch_response(url, timeout=8, extra_headers={"User-Agent": "Mozilla/5.0"}, as_bytes=True)
            if r is not None and r.status_code == 200:
                entry["etag"]           = r.headers.get("ETag", "")
                entry["last_modified"]  = r.headers.get("Last-Modified", "")
                entry["content_length"] = r.headers.get("Content-Length", "")
        except Exception:
            pass
    history[url] = entry
    if len(history) > 1000:
        oldest = sorted(history, key=lambda u: history[u].get("last_downloaded", ""))
        for old in oldest[:len(history)-1000]:
            del history[old]
    _save_history(history)

def _check_history(url: str) -> Optional[Dict]:
    """Return history entry if URL was previously downloaded, else None."""
    return _load_history().get(url)

_shared_session = None
_shared_session_cf = None


def _get_shared_session(use_cf: bool = False) -> "requests.Session":
    """Return (and lazily create) the shared requests session.

    When *use_cf* is True the cloudscraper-backed session is returned;
    otherwise a plain retry session is used.  Sessions are re-created
    after proxy or DNS changes (the callers reset the globals to None).
    """
    global _shared_session, _shared_session_cf
    if use_cf:
        if _shared_session_cf is None:
            _shared_session_cf = create_retry_session(use_cloudscraper=True)
        return _shared_session_cf
    if _shared_session is None:
        _shared_session = create_retry_session(use_cloudscraper=False)
    return _shared_session


# ── Domain rate limiter ────────────────────────────────────────────────────
_domain_last_request: Dict[str, float] = {}
_domain_lock = _threading.Lock()
_domain_min_interval: float = 0.3   # 300ms between requests to same domain

# ── D: Per-domain exponential backoff ──────────────────────────────────────
import random as _random
_domain_backoff: Dict[str, float] = {}      # domain → current backoff (seconds)
_domain_fail_count: Dict[str, int] = {}     # domain → consecutive failures
_domain_backoff_lock = _threading.Lock()
_BACKOFF_BASE   = 2.0    # seconds
_BACKOFF_MAX    = 300.0  # 5 min cap
_BACKOFF_JITTER = 0.25   # ±25% jitter

def _domain_throttle(url: str) -> None:
    """Enforce per-domain minimum interval + exponential backoff."""
    try:
        domain = urlparse(url).netloc
        if not domain: return
        with _domain_lock:
            last = _domain_last_request.get(domain, 0)
            # Check if domain is in backoff mode
            backoff = _domain_backoff.get(domain, 0)
            wait = max(_domain_min_interval - (_time.monotonic() - last), backoff)
            if wait > 0:
                _time.sleep(wait)
            _domain_last_request[domain] = _time.monotonic()
    except Exception:
        pass

def _domain_record_success(url: str) -> None:
    """Domain replied OK — halve the backoff."""
    try:
        domain = urlparse(url).netloc
        if not domain: return
        with _domain_backoff_lock:
            if domain in _domain_backoff:
                _domain_backoff[domain] = max(0.0, _domain_backoff[domain] / 2)
                _domain_fail_count[domain] = max(0, _domain_fail_count.get(domain, 0) - 1)
    except Exception:
        pass

def _domain_record_failure(url: str, status: int = 0) -> float:
    """
    Domain failed (429 / 5xx / connection error).
    Double the backoff and return seconds to sleep.
    """
    try:
        domain = urlparse(url).netloc
        if not domain: return 0.0
        with _domain_backoff_lock:
            fails = _domain_fail_count.get(domain, 0) + 1
            _domain_fail_count[domain] = fails
            current = _domain_backoff.get(domain, 0.0)
            if current == 0.0:
                new_backoff = _BACKOFF_BASE
            else:
                new_backoff = min(current * 2, _BACKOFF_MAX)
            # Add jitter: ±25%
            jitter = new_backoff * _BACKOFF_JITTER * (2 * _random.random() - 1)
            new_backoff = max(0.1, new_backoff + jitter)
            _domain_backoff[domain] = new_backoff
            logger.debug(f"Backoff [{domain}] fails={fails} → {new_backoff:.1f}s (status={status})")
            return new_backoff
    except Exception:
        return 0.0

# ── E: Persistent disk image cache ────────────────────────────────────────
import json as _json_cache
import hashlib as _hashlib

_CACHE_DIR  = pathlib.Path.home() / ".cyoa_downloader" / "image_cache"
_CACHE_IDX  = _CACHE_DIR / "index.json"
_cache_index: Dict[str, str] = {}  # url → sha256 hex
_cache_lock  = _threading.Lock()
_cache_loaded = False

def _cache_load() -> None:
    global _cache_loaded
    if _cache_loaded: return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if _CACHE_IDX.exists():
            with open(_CACHE_IDX, encoding="utf-8") as f:
                _cache_index.update(_json_cache.load(f))
        _cache_loaded = True
        logger.debug(f"Image cache: {len(_cache_index)} entries loaded")
    except Exception as e:
        logger.debug(f"Image cache load failed: {e}")

def _cache_get(url: str) -> Optional[bytes]:
    """Return cached bytes for URL if available and valid."""
    _cache_load()
    h = _cache_index.get(url)
    if not h: return None
    fpath = _CACHE_DIR / h[:2] / h
    if fpath.exists():
        try:
            data = fpath.read_bytes()
            # Quick integrity check
            if _hashlib.sha256(data).hexdigest() == h:
                return data
        except Exception:
            pass
    # Cache miss or corrupt — remove entry
    with _cache_lock:
        _cache_index.pop(url, None)
    return None

def _cache_put(url: str, data: bytes) -> None:
    """Store bytes in disk cache, keyed by URL."""
    if not data or len(data) < 64: return  # skip tiny/empty
    try:
        _cache_load()
        h = _hashlib.sha256(data).hexdigest()
        fdir = _CACHE_DIR / h[:2]
        fdir.mkdir(parents=True, exist_ok=True)
        fpath = fdir / h
        if not fpath.exists():
            fpath.write_bytes(data)
        with _cache_lock:
            _cache_index[url] = h
        # Async save index (don't block download thread)
        def _save():
            try:
                with open(_CACHE_IDX, "w", encoding="utf-8") as f:
                    _json_cache.dump(dict(_cache_index), f)
            except Exception: pass
        _threading.Thread(target=_save, daemon=True).start()
    except Exception as e:
        logger.debug(f"Image cache put failed: {e}")

def _cache_stats() -> Dict[str, int]:
    _cache_load()
    return {"entries": len(_cache_index),
            "size_mb": sum(((_CACHE_DIR/h[:2]/h).stat().st_size
                            for h in _cache_index.values()
                            if (_CACHE_DIR/h[:2]/h).exists()), 0) // (1024*1024)}


def _clear_image_cache() -> int:
    """Remove all cached images. Returns number of files deleted."""
    global _cache_index
    count = 0
    try:
        if _CACHE_DIR.exists():
            for item in _CACHE_DIR.iterdir():
                if item.is_dir():
                    for f in item.iterdir():
                        f.unlink(missing_ok=True)
                        count += 1
                    try:
                        item.rmdir()
                    except OSError:
                        pass
            if _CACHE_IDX.exists():
                _CACHE_IDX.unlink()
        _cache_index = {}
        logger.info(f"Image cache cleared — {count} file(s) removed")
    except Exception as e:
        logger.warning(f"Cache clear error: {e}")
    return count


def _send_desktop_notification(title: str, message: str) -> None:
    """Send a desktop notification via plyer (best-effort, non-blocking)."""
    def _do():
        try:
            from plyer import notification  # type: ignore
            notification.notify(
                title=title, message=message[:256],
                timeout=5, app_name="CYOA Downloader",
            )
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _check_for_app_updates() -> Optional[Dict[str, str]]:
    """Check GitHub Releases API for a newer version.

    Returns {"version": ..., "url": ..., "notes": ...} if newer, else None.
    """
    if not _GITHUB_RELEASE_API:
        return None
    try:
        r = fetch_response(_GITHUB_RELEASE_API, timeout=8,
                           extra_headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "CYOA-Downloader"},
                           as_bytes=True)
        if r is None or r.status_code != 200:
            return None
        data = r.json()
        remote_tag = data.get("tag_name", "").lstrip("vV").strip()
        if not remote_tag:
            return None
        def _ver(s):
            return tuple(int(x) for x in s.split(".")[:3] if x.isdigit())
        if _ver(remote_tag) > _ver(_APP_VERSION):
            return {
                "version": remote_tag,
                "url": data.get("html_url", ""),
                "notes": (data.get("body") or "")[:500],
            }
    except Exception:
        pass
    return None


def _batch_check_updates(history: Dict[str, Dict],
                         max_workers: int = 4,
                         progress_cb=None) -> List[Dict]:
    """Check previously downloaded CYOAs for server-side changes.

    Compares stored Content-Length / Last-Modified / ETag against current
    server HEAD response.
    """
    results: List[Dict] = []
    entries = [(url, meta) for url, meta in history.items()
               if meta.get("success")]
    total = len(entries)
    if not total:
        return results

    def _check(args):
        idx, (url, meta) = args
        try:
            r = fetch_response(url, timeout=10, extra_headers={"User-Agent": "Mozilla/5.0"}, as_bytes=True)
            if r is None or r.status_code != 200:
                return {"url": url, "name": meta.get("filename", ""),
                        "status": "unreachable", "reason": f"HTTP {getattr(r, 'status_code', 'request failed')}"}
            old_etag = meta.get("etag", "")
            old_lm   = meta.get("last_modified", "")
            old_cl   = meta.get("content_length", "")
            new_etag = r.headers.get("ETag", "")
            new_lm   = r.headers.get("Last-Modified", "")
            new_cl   = r.headers.get("Content-Length", "")
            changed, reason = False, []
            if old_etag and new_etag and old_etag != new_etag:
                changed = True; reason.append("ETag")
            if old_lm and new_lm and old_lm != new_lm:
                changed = True; reason.append("Last-Modified")
            if old_cl and new_cl and old_cl != new_cl:
                changed = True; reason.append(f"size {old_cl}→{new_cl}")
            return {"url": url, "name": meta.get("filename", ""),
                    "status": "updated" if changed else "current",
                    "reason": ", ".join(reason), "date": meta.get("date", "")}
        except Exception as e:
            return {"url": url, "name": meta.get("filename", ""),
                    "status": "error", "reason": str(e)[:80]}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_check, (i, e)): i for i, e in enumerate(entries)}
        done_n = 0
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                results.append(res)
            done_n += 1
            if progress_cb:
                progress_cb(done_n, total)
    return results



def _extract_single_ai_url(text_value: str) -> Optional[str]:
    """Extract one URL/path from an AI response, rejecting unsafe schemes."""
    if not text_value:
        return None
    raw = text_value.strip().strip('"').strip("'")
    if raw.upper() in {"NONE", "NULL", "N/A", "NO", "NOT FOUND"}:
        return None
    if len(raw) > 600:
        return None
    scheme_match = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*):", raw)
    if scheme_match and scheme_match.group(1).lower() not in {"http", "https"}:
        return None
    pattern = r"(https?://[^\s\"'<>`]+|//[^\s\"'<>`]+|(?:\./|\.\./|/)[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+|[A-Za-z0-9._~/-]+\.(?:json|txt|zip)(?:\?[^\s\"'<>`]*)?)"
    m = re.search(pattern, raw)
    return m.group(1) if m else None

def _ai_detect_project_json(url: str, html_text: str,
                            api_key: str = "", provider: str = "",
                            ai_mode: str = "auto_fallback",
                            budget: Optional[AIUsageBudget] = None) -> Optional[str]:
    """AI-assisted project data locator. Provider-neutral.

    Returns a candidate URL only when AI mode permits recovery and the candidate
    passes strict URL/path sanitization.
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not _ai_mode_allows("project_detect", ai_mode) or not _ai_is_available(api_key, provider):
        return None
    if not _ai_budget_consume(budget, "AI project detect"):
        return None
    html_budget = _get_ai_int_setting("ai_max_html_chars", 8000, min_value=1000, max_value=50000)
    html_sample = html_text[:html_budget]
    result = _ai_call(
        api_key=api_key,
        provider=provider,
        prompt=(
            f"CYOA webpage at {url}.\nHTML (truncated):\n{html_sample}\n\n"
            "Find the URL where project.json data is loaded from. "
            "Look for fetch(), XHR, data-src, or script tags loading CYOA data. "
            "Reply ONLY the URL (absolute or relative). If not found, reply NONE."
        ),
        max_tokens=300,
        label="project detect",
    )
    candidate_raw = _sanitize_ai_candidate_url(_extract_single_ai_url(result or "") or "")
    if candidate_raw:
        candidate = candidate_raw if candidate_raw.startswith(("http://", "https://")) else urljoin(url, candidate_raw)
        logger.info(f"[AI detect] project candidate → {candidate}")
        return candidate
    return None


def _ai_call(api_key: str, prompt: str, max_tokens: int = 1024,
             system: str = "", label: str = "ai", model: str = "",
             provider: str = "") -> Optional[str]:
    """Provider-aware low-level AI call. Returns response text or None.

    Supported providers:
      - anthropic: Anthropic Messages API
      - openai: OpenAI Responses API
      - gemini: Google Gemini generateContent API
      - ollama: local Ollama /api/generate
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    model = (model or _get_ai_model(provider)).strip() or _default_ai_model(provider)
    if provider != "ollama" and not api_key:
        return None
    try:
        session = _get_shared_session(use_cf=False)
        if provider == "anthropic":
            body: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                body["system"] = system
            r = session.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json",
                         "x-api-key": api_key,
                         "anthropic-version": "2023-06-01"},
                json=body,
                timeout=60,
            )
            if r.status_code != 200:
                logger.debug(f"[{label}] Anthropic API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            return "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text").strip() or None

        if provider == "openai":
            input_payload: List[Dict[str, str]] = []
            if system:
                input_payload.append({"role": "system", "content": system})
            input_payload.append({"role": "user", "content": prompt})
            body = {"model": model, "input": input_payload, "max_output_tokens": max_tokens}
            r = session.post(
                "https://api.openai.com/v1/responses",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=60,
            )
            if r.status_code != 200:
                logger.debug(f"[{label}] OpenAI API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            if data.get("output_text"):
                return str(data["output_text"]).strip() or None
            parts: List[str] = []
            for item in data.get("output", []) or []:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("text"):
                        parts.append(str(c.get("text")))
            return "".join(parts).strip() or None

        if provider == "gemini":
            import urllib.parse as _up
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{_up.quote(model, safe='')}:generateContent?key={_up.quote(api_key, safe='')}"
            body: Dict[str, Any] = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            }
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            r = session.post(url, headers={"Content-Type": "application/json"}, json=body, timeout=60)
            if r.status_code != 200:
                logger.debug(f"[{label}] Gemini API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            parts: List[str] = []
            for cand in data.get("candidates", []) or []:
                for part in cand.get("content", {}).get("parts", []) or []:
                    if part.get("text"):
                        parts.append(str(part["text"]))
            return "".join(parts).strip() or None

        if provider == "ollama":
            st = _load_settings()
            base = (st.get("ollama_url") or OLLAMA_DEFAULT_URL).rstrip("/")
            body = {
                "model": model,
                "prompt": (system + "\n\n" if system else "") + prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
            r = session.post(base + "/api/generate", headers={"Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code != 200:
                logger.debug(f"[{label}] Ollama API {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            return str(data.get("response", "")).strip() or None

        logger.warning(f"[{label}] Unsupported AI provider: {provider}")
        return None
    except Exception as e:
        logger.debug(f"[{label}] error: {e}")
    return None


def _ai_analyze_js_for_assets(
    js_files: Dict[str, str],
    base_url: str,
    api_key: str = "",
    provider: str = "",
    ai_mode: str = "aggressive_recovery",
    budget_obj: Optional[AIUsageBudget] = None,
) -> List[str]:
    """AI-assisted JS analysis to discover asset URLs that BFS scan missed.

    Sends truncated JS content to Claude and asks it to find:
    - Image/audio/video URLs or paths
    - Dynamic import() targets
    - Lazy-loaded chunk names
    - Data URLs or fetch() targets
    - Asset arrays, maps, or objects

    Returns list of candidate URLs (absolute).
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not js_files or not _ai_mode_allows("asset_scan", ai_mode) or not _ai_is_available(api_key, provider):
        return []
    if not _ai_budget_consume(budget_obj, "AI asset scan"):
        return []

    # Build a compact JS sample: filename + first N chars of each file
    # Budget is user-configurable from AI settings.
    budget = int(_load_settings().get("ai_max_js_chars", 14000) or 14000)
    per_file = max(800, budget // max(len(js_files), 1))
    samples = []
    for fname, content in js_files.items():
        snippet = content[:per_file]
        samples.append(f"--- {fname} ({len(content)} chars) ---\n{snippet}")
    combined = "\n\n".join(samples)
    if len(combined) > budget:
        combined = combined[:budget]

    system_prompt = (
        "You are an expert JavaScript reverse engineer analyzing CYOA "
        "(Choose Your Own Adventure) web applications. Your task is to "
        "extract asset references from minified JS bundles."
    )
    user_prompt = (
        f"Base URL: {base_url}\n\n"
        f"JS files (truncated):\n{combined}\n\n"
        "Analyze these JS files and extract ALL asset URLs/paths you can find:\n"
        "1. Image paths (.png, .jpg, .jpeg, .webp, .gif, .svg, .avif)\n"
        "2. Audio paths (.mp3, .ogg, .wav, .m4a, .flac, .aac)\n"
        "3. Video paths (.mp4, .webm)\n"
        "4. Dynamic import() or lazy-loaded chunk filenames (.js, .mjs)\n"
        "5. fetch() or XHR target URLs\n"
        "6. CSS file references (.css)\n"
        "7. Font file references (.woff, .woff2, .ttf, .otf)\n"
        "8. JSON data files\n\n"
        "Look for: string literals, template literals, array/object definitions, "
        "concatenated paths, Vite/Webpack chunk maps, asset manifests.\n\n"
        "Reply with ONLY a JSON array of relative or absolute URL strings. "
        "Example: [\"/assets/bg.webp\", \"./chunks/lazy-DxMS06T8.js\", \"music/theme.mp3\"]\n"
        "If nothing found, reply: []"
    )

    result = _ai_call(
        api_key=api_key,
        prompt=user_prompt,
        system=system_prompt,
        max_tokens=2000,
        label="AI asset scan",
        provider=provider,
    )
    if not result:
        return []

    # Parse JSON array from response
    try:
        # Strip markdown fences if present
        cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        candidates = json.loads(cleaned)
        if not isinstance(candidates, list):
            return []
        # Resolve to absolute URLs
        resolved = []
        for c in candidates:
            if not isinstance(c, str) or not c.strip():
                continue
            c = _sanitize_ai_candidate_url(c)
            if not c:
                continue
            # Only accept likely web assets/data endpoints. MIME is validated again at download time.
            path_l = urlparse(c).path.lower()
            if path_l and not path_l.endswith(tuple(IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | FONT_EXTENSIONS | {".json", ".html", ".htm", ".svg"})):
                # Keep extensionless relative fetch targets, but skip obvious non-assets.
                if "." in os.path.basename(path_l):
                    continue
            if c.startswith(("http://", "https://")):
                resolved.append(c)
            else:
                resolved.append(urljoin(base_url, c))
        logger.info(f"[AI asset scan] {len(resolved)} candidate(s) from {len(js_files)} JS file(s)")
        return resolved
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"[AI asset scan] JSON parse error: {e}")
        return []


def _ai_analyze_viewer_logic(
    html_text: str,
    js_samples: Dict[str, str],
    url: str,
    api_key: str = "",
    provider: str = "",
    ai_mode: str = "diagnostics",
    budget_obj: Optional[AIUsageBudget] = None,
) -> Dict[str, Any]:
    """AI-assisted analysis of how a CYOA viewer loads and structures data.

    Returns dict with insights:
    - data_source: how/where the viewer loads CYOA data
    - asset_base: base path for assets
    - viewer_type: detected viewer type
    - suggestions: list of recommended actions
    """
    provider = _normalize_ai_provider(provider or _get_ai_provider())
    if not _ai_mode_allows("diagnostics", ai_mode) or not _ai_is_available(api_key, provider):
        return {}
    if not _ai_budget_consume(budget_obj, "AI viewer analysis"):
        return {}

    # Build compact sample
    html_limit = min(4000, _get_ai_int_setting("ai_max_html_chars", 8000, min_value=1000, max_value=50000))
    js_limit = min(3000, _get_ai_int_setting("ai_max_js_chars", 14000, min_value=1000, max_value=100000))
    html_sample = html_text[:html_limit]
    js_sample_parts = []
    for fname, content in list(js_samples.items())[:3]:
        js_sample_parts.append(f"--- {fname} ---\n{content[:js_limit]}")
    js_combined = "\n\n".join(js_sample_parts)

    result = _ai_call(
        api_key=api_key,
        system=(
            "You are an expert at analyzing CYOA (Choose Your Own Adventure) "
            "web viewers. Analyze the HTML and JS to understand the viewer architecture."
        ),
        prompt=(
            f"URL: {url}\n\nHTML:\n{html_sample}\n\nJS:\n{js_combined}\n\n"
            "Analyze this CYOA viewer and reply ONLY as JSON:\n"
            "{\n"
            '  "data_source": "how the viewer loads CYOA data (fetch, inline, script tag, etc)",\n'
            '  "asset_base": "base URL/path for images and assets",\n'
            '  "viewer_type": "icc_plus|icc_remix|react_custom|vue_custom|other",\n'
            '  "chunk_pattern": "pattern for lazy-loaded JS chunks if any",\n'
            '  "suggestions": ["list of download strategy recommendations"]\n'
            "}"
        ),
        max_tokens=800,
        label="AI viewer analysis",
        provider=provider,
    )
    if not result:
        return {}
    try:
        cleaned = re.sub(r'^```(?:json)?\s*', '', result.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            return {}
        allowed = {"data_source", "asset_base", "viewer_type", "chunk_pattern", "suggestions"}
        clean: Dict[str, Any] = {k: obj.get(k) for k in allowed if k in obj}
        if "suggestions" in clean and not isinstance(clean["suggestions"], list):
            clean["suggestions"] = [str(clean["suggestions"])]
        return clean
    except (json.JSONDecodeError, ValueError):
        return {}


# ── B: Browser cookie session ──────────────────────────────────────────────
def _make_cookie_session(browser: str = "chrome") -> Optional["requests.Session"]:
    """
    Build a requests.Session with cookies from an installed browser.
    Uses browser-cookie3 if available, falls back to Chrome SQLite directly.
    """
    try:
        import browser_cookie3 as _bc
        loaders = {
            "chrome":   _bc.chrome,  "chromium": _bc.chromium,
            "firefox":  _bc.firefox, "edge":     _bc.edge,
            "brave":    _bc.brave,   "opera":    _bc.opera,
            "safari":   _bc.safari,
        }
        loader = loaders.get(browser.lower())
        if loader is None: return None
        jar = loader()
        s = create_retry_session()
        s.cookies.update(jar)
        logger.debug(f"Cookie session: loaded from {browser} ({len(jar)} cookies)")
        return s
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"browser_cookie3 failed ({browser}): {e}")

    # Manual Chrome SQLite fallback (Windows only)
    if browser.lower() == "chrome" and sys.platform == "win32":
        try:
            import sqlite3 as _sq, shutil as _sh, tempfile as _tf
            local = os.environ.get("LOCALAPPDATA", "")
            db_src = pathlib.Path(local) / "Google/Chrome/User Data/Default/Network/Cookies"
            if not db_src.exists():
                db_src = pathlib.Path(local) / "Google/Chrome/User Data/Default/Cookies"
            if db_src.exists():
                tmp = _tf.mktemp(suffix=".db")
                _sh.copy2(db_src, tmp)
                conn = _sq.connect(tmp)
                rows = conn.execute(
                    "SELECT host_key, name, value FROM cookies"
                ).fetchall()
                conn.close()
                os.unlink(tmp)
                s = create_retry_session()
                for host, name, value in rows:
                    s.cookies.set(name, value, domain=host.lstrip("."))
                logger.debug(f"Cookie session: Chrome SQLite ({len(rows)} cookies)")
                return s
        except Exception as e:
            logger.debug(f"Chrome SQLite cookie fallback failed: {e}")
    return None

# ── A: Headless browser fetch ──────────────────────────────────────────────
def _fetch_headless(url: str) -> Optional[bytes]:
    """
    Fetch URL using Playwright (preferred) or Selenium as fallback.
    Used when normal HTTP fetch fails or returns <1KB content for images.
    Returns raw bytes or None.
    """
    # ── Try Playwright first ──────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                ignore_https_errors=True,
            )
            page = ctx.new_page()
            resp = page.goto(url, wait_until="networkidle", timeout=30_000)
            if resp and resp.ok:
                # For images, get raw bytes from response body
                content = resp.body()
                browser.close()
                logger.info(f"  [Headless/Playwright] {url} → {len(content)} bytes")
                return content
            browser.close()
    except ImportError:
        pass  # Playwright not installed
    except Exception as e:
        logger.debug(f"Playwright fetch failed ({url}): {e}")

    # ── Selenium fallback ─────────────────────────────────────────────
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--log-level=3")
        drv = webdriver.Chrome(options=opts)
        drv.get(url)
        # For image URLs, navigate and grab the page source (won't work for binary)
        # Better: use CDP to capture response
        import base64 as _b64
        result = drv.execute_cdp_cmd(
            "Page.captureScreenshot", {}
        )  # fallback if binary nav fails
        drv.quit()
        # Actually navigate + get response via fetch in JS
        drv2 = webdriver.Chrome(options=opts)
        resp_b64 = drv2.execute_script(
            f"""
            return await (async () => {{
              const r = await fetch({url!r});
              const buf = await r.arrayBuffer();
              const bytes = new Uint8Array(buf);
              let binary = '';
              for (let b of bytes) binary += String.fromCharCode(b);
              return btoa(binary);
            }})();
            """
        )
        drv2.quit()
        if resp_b64:
            data = _b64.b64decode(resp_b64)
            logger.info(f"  [Headless/Selenium] {url} → {len(data)} bytes")
            return data
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Selenium fetch failed ({url}): {e}")

    return None


# ── Layer F: gallery-dl fallback ───────────────────────────────────────────
# gallery-dl is useful for post/gallery pages that require a supported extractor
# (Pixiv artwork pages, DeviantArt pages, booru post pages). It is NOT reliable
# as an automatic fallback for raw CDN asset URLs such as i.pximg.net/img-original/*.
# Default mode is off; smart mode only accepts page/post/gallery-style URLs.
_GALLERY_DL_HOSTS: Dict[str, str] = {
    "www.pixiv.net": "pixiv", "pixiv.net": "pixiv",
    "www.deviantart.com": "deviantart", "deviantart.com": "deviantart",
    "danbooru.donmai.us": "danbooru", "donmai.us": "danbooru",
    "e621.net": "e621", "e926.net": "e621",
    "gelbooru.com": "gelbooru", "hypnohub.net": "hypnohub",
    "rule34.xxx": "rule34", "sankaku.app": "sankaku",
    "chan.sankakucomplex.com": "sankaku",
    "x.com": "twitter", "twitter.com": "twitter", "www.twitter.com": "twitter",
}
_GALLERY_DL_CDN_HOSTS: Set[str] = {
    "i.pximg.net", "img-original.pximg.net", "img-zip-ugoira.pximg.net",
    "pbs.twimg.com", "c.deviantart.com", "a.deviantart.net", "wixmp.com",
    "cdn.donmai.us", "static1.e621.net", "static1.e926.net",
    "img3.sankakucomplex.com", "img.sankakucomplex.com",
    "img1.gelbooru.com", "img2.gelbooru.com", "img.hypnohub.net",
    "img.rule34.xxx", "img3.rule34.xxx", "img.rule34.paheal.net",
}
_gdl_available: Optional[bool] = None   # cached
_gallery_dl_mode: str = str(_load_settings().get("gallery_dl_mode", "off") or "off").lower()
_gallery_dl_path: str = "gallery-dl"
_gallery_dl_config: str = ""


def _set_gallery_dl_mode(mode: str = "off", *, path: str = "", config: str = "") -> None:
    """Set gallery-dl integration mode. Modes: off, smart, force."""
    global _gallery_dl_mode, _gallery_dl_path, _gallery_dl_config, _gdl_available
    m = (mode or "off").strip().lower()
    if m not in {"off", "smart", "force"}:
        m = "off"
    old_path, old_config = _gallery_dl_path, _gallery_dl_config
    _gallery_dl_mode = m
    _gallery_dl_path = (path or "gallery-dl").strip()
    _gallery_dl_config = (config or "").strip()
    if old_path != _gallery_dl_path or old_config != _gallery_dl_config:
        _gdl_available = None
    try:
        st = _load_settings(); st["gallery_dl_mode"] = m; _save_settings(st)
    except Exception:
        pass


def _gallery_dl_is_available() -> bool:
    global _gdl_available
    if _gdl_available is not None:
        return _gdl_available
    try:
        import gallery_dl  # noqa
        _gdl_available = True
        return True
    except ImportError:
        pass
    try:
        import subprocess as _sp
        r = _sp.run([_gallery_dl_path or "gallery-dl", "--version"], capture_output=True, timeout=5)
        _gdl_available = (r.returncode == 0)
        return _gdl_available
    except Exception:
        _gdl_available = False
        return False


def _is_probable_raw_cdn_asset(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        ext = os.path.splitext(parsed.path.lower())[1]
        return host in _GALLERY_DL_CDN_HOSTS or ext in IMAGE_EXTENSIONS
    except Exception:
        return True


def _is_gallery_dl_candidate(url: str) -> Optional[str]:
    """Return extractor key only when gallery-dl is appropriate for this URL."""
    if _gallery_dl_mode == "off":
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if _gallery_dl_mode == "smart" and _is_probable_raw_cdn_asset(url):
            return None
        if host in _GALLERY_DL_HOSTS:
            return _GALLERY_DL_HOSTS[host]
        if _gallery_dl_mode == "force":
            # Force is an advanced/manual mode. Still avoid obviously local/data URLs.
            if parsed.scheme in {"http", "https"}:
                return host or "custom"
        # Common page patterns. Keep this conservative.
        if any(token in path for token in ("/artworks/", "/posts/", "/post/", "/view/", "/gallery/", "/status/")):
            return host or "page"
    except Exception:
        pass
    return None

# Backwards-compatible name used by older code paths.
def _is_gallery_dl_site(url: str) -> Optional[str]:
    return _is_gallery_dl_candidate(url)


def _fetch_via_gallery_dl(url: str) -> Optional[bytes]:
    """
    Download a single file through gallery-dl only when explicitly enabled.
    Uses gallery/page URLs best. Raw CDN image URLs are skipped in smart mode.
    """
    import tempfile, subprocess as _sp

    site = _is_gallery_dl_candidate(url)
    if not site:
        return None
    if not _gallery_dl_is_available():
        logger.debug("[gallery-dl] unavailable; install with: pip install gallery-dl")
        return None

    tmpdir = tempfile.mkdtemp(prefix="cyoa_gdl_")
    try:
        cmd = [
            _gallery_dl_path or "gallery-dl",
            "--destination", tmpdir,
            "--no-mtime",
            "--no-download-archive",
            "--range", "1",
            "-q",
        ]
        if _gallery_dl_config and os.path.exists(_gallery_dl_config):
            cmd.extend(["--config", _gallery_dl_config])
        proxy = _get_active_proxy()
        if proxy:
            cmd.extend(["--proxy", proxy])
        cmd.append(url)

        r = _sp.run(cmd, capture_output=True, timeout=90, text=True)
        image_files = _gdl_collect_files(tmpdir)

        if not image_files:
            logger.debug(f"[gallery-dl] No output for {url} | rc={r.returncode} | stderr={(r.stderr or '')[:200]}")
            return None

        best = max(image_files, key=os.path.getsize)
        with open(best, "rb") as f:
            data = f.read()
        if len(data) < 64:
            return None
        logger.info(f"  [gallery-dl ✓] {os.path.basename(best)} ({len(data)//1024}KB)")
        return data

    except _sp.TimeoutExpired:
        logger.warning(f"[gallery-dl] Timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"[gallery-dl] {e}")
        return None
    finally:
        import shutil as _sh
        try:
            _sh.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _gdl_collect_files(directory: str):
    """Collect downloaded media files from gallery-dl output dir."""
    import glob as _glob
    files = _glob.glob(os.path.join(directory, "**", "*"), recursive=True)
    return [
        f for f in files
        if os.path.isfile(f) and not f.lower().endswith((".json", ".log", ".txt", ".part"))
        and os.path.getsize(f) > 64
    ]


_image_hash_map: Dict[str, str] = {}   # sha256 → first local path (dedup)
_hash_lock = _threading.Lock()


def _check_image_dedup(content: bytes, local_path: str) -> Optional[str]:
    """Check if identical content already downloaded this session."""
    if not content:
        return None
    h = _hashlib.sha256(content).hexdigest()
    with _hash_lock:
        if h in _image_hash_map:
            return _image_hash_map[h]
        _image_hash_map[h] = local_path
    return None


def is_cloudflare_challenge(response) -> bool:
    """Detect Cloudflare challenge pages (even on 200 responses)."""
    if response is None:
        return False
    ct = response.headers.get("Content-Type", "")
    if "text/html" not in ct and "application/json" not in ct:
        return False
    server = response.headers.get("Server", "").lower()
    cf_ray = response.headers.get("CF-RAY", "")
    if not cf_ray and "cloudflare" not in server:
        return False
    text_sample = response.text[:2000] if hasattr(response, "text") else ""
    cf_markers = [
        "cf-browser-verification", "challenge-platform",
        "jschl_vc", "jschl-answer", "cf_clearance",
        "Checking your browser", "Enable JavaScript and cookies",
        "cf-turnstile", "DDoS protection",
    ]
    return any(m in text_sample for m in cf_markers)



# ── Cloudflare / FlareSolverr integration ──────────────────────────────────

def _normalize_cloudflare_mode(mode: str) -> str:
    m = (mode or "auto").strip().lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "off": "off", "none": "off", "disabled": "off",
        "auto": "auto",
        "cf-bypass": "cloudscraper", "cloudscraper": "cloudscraper", "cloud-scraper": "cloudscraper",
        "flaresolverr": "flaresolverr", "flare-solverr": "flaresolverr", "flaversolverr": "flaresolverr",
    }
    return aliases.get(m, "auto")


def _display_cloudflare_mode(mode: str) -> str:
    m = _normalize_cloudflare_mode(mode)
    return {"off": "Off", "auto": "Auto", "cloudscraper": "cloudscraper", "flaresolverr": "FlareSolverr"}.get(m, "Auto")


def _normalize_flaresolverr_url(url: str) -> str:
    u = (url or "http://localhost:8191/v1").strip().rstrip("/")
    if not u:
        return "http://localhost:8191/v1"
    # Users often paste http://localhost:8191; the JSON API is /v1.
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        u = "http://" + u
        parsed = urlparse(u)
    if not parsed.path or parsed.path == "/":
        u = u.rstrip("/") + "/v1"
    elif not parsed.path.rstrip("/").endswith("/v1") and not parsed.path.rstrip("/").endswith("v1"):
        u = u.rstrip("/") + "/v1"
    return u


def _load_cloudflare_settings() -> None:
    """Load persisted Cloudflare/FlareSolverr settings into globals."""
    st = _load_settings()
    _set_cloudflare_config(
        mode=st.get("cloudflare_mode", "auto"),
        flaresolverr_url=st.get("flaresolverr_url", "http://localhost:8191/v1"),
        session_policy=st.get("flaresolverr_session_policy", "reuse-domain"),
        timeout=int(st.get("flaresolverr_timeout", 60) or 60),
        wait_after=int(st.get("flaresolverr_wait_after", 3) or 3),
        proxy_mode=st.get("flaresolverr_proxy_mode", "inherit"),
        persist=False,
    )


def _set_cloudflare_config(
    mode: str = "auto",
    *,
    flaresolverr_url: str = "",
    session_policy: str = "",
    timeout: int = 60,
    wait_after: int = 3,
    proxy_mode: str = "inherit",
    persist: bool = True,
) -> None:
    """Set process-local Cloudflare engine configuration."""
    global _CLOUDFLARE_MODE, use_cloudscraper, _FLARESOLVERR_URL
    global _FLARESOLVERR_SESSION_POLICY, _FLARESOLVERR_TIMEOUT, _FLARESOLVERR_WAIT_AFTER, _FLARESOLVERR_PROXY_MODE
    global _shared_session, _shared_session_cf

    old_mode = _CLOUDFLARE_MODE
    _CLOUDFLARE_MODE = _normalize_cloudflare_mode(mode)
    use_cloudscraper = (_CLOUDFLARE_MODE == "cloudscraper")
    if flaresolverr_url:
        _FLARESOLVERR_URL = _normalize_flaresolverr_url(flaresolverr_url)
    _FLARESOLVERR_SESSION_POLICY = (session_policy or _FLARESOLVERR_SESSION_POLICY or "reuse-domain").strip().lower()
    if _FLARESOLVERR_SESSION_POLICY not in {"temporary", "reuse-domain", "manual"}:
        _FLARESOLVERR_SESSION_POLICY = "reuse-domain"
    try:
        _FLARESOLVERR_TIMEOUT = max(5, int(timeout or 60))
    except Exception:
        _FLARESOLVERR_TIMEOUT = 60
    try:
        _FLARESOLVERR_WAIT_AFTER = max(0, int(wait_after or 0))
    except Exception:
        _FLARESOLVERR_WAIT_AFTER = 3
    _FLARESOLVERR_PROXY_MODE = (proxy_mode or "inherit").strip().lower()
    if _FLARESOLVERR_PROXY_MODE not in {"inherit", "none"}:
        _FLARESOLVERR_PROXY_MODE = "inherit"

    if old_mode != _CLOUDFLARE_MODE:
        _shared_session = None
        _shared_session_cf = None
    if persist:
        try:
            st = _load_settings()
            st.update({
                "cloudflare_mode": _CLOUDFLARE_MODE,
                "flaresolverr_url": _FLARESOLVERR_URL,
                "flaresolverr_session_policy": _FLARESOLVERR_SESSION_POLICY,
                "flaresolverr_timeout": _FLARESOLVERR_TIMEOUT,
                "flaresolverr_wait_after": _FLARESOLVERR_WAIT_AFTER,
                "flaresolverr_proxy_mode": _FLARESOLVERR_PROXY_MODE,
            })
            _save_settings(st)
        except Exception as e:
            logger.debug(f"Could not save Cloudflare settings: {e}")


def _flaresolverr_payload_proxy() -> Optional[Dict[str, str]]:
    """Return FlareSolverr proxy object when proxy inheritance is enabled."""
    if _FLARESOLVERR_PROXY_MODE != "inherit":
        return None
    proxy = _get_active_proxy()
    if not proxy:
        return None
    # FlareSolverr expects a proxy object. Minimal URL form works for HTTP/S proxies.
    return {"url": proxy}


def _flaresolverr_post(payload: Dict[str, Any], timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """POST JSON to FlareSolverr /v1. Returns decoded JSON or None."""
    api_url = _normalize_flaresolverr_url(_FLARESOLVERR_URL)
    request_timeout = max((timeout or _FLARESOLVERR_TIMEOUT) + 10, 20)
    try:
        session = requests.Session()
        session.trust_env = (_proxy_mode == "inherit_env")
        proxy = _get_active_proxy()
        if proxy and _FLARESOLVERR_PROXY_MODE == "inherit":
            # This proxy is for reaching FlareSolverr itself. For localhost, this is usually unnecessary;
            # requests will typically bypass localhost via NO_PROXY if configured. Keep it explicit but safe.
            parsed = urlparse(api_url)
            if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
                session.proxies.update({"http": proxy, "https": proxy})
        r = session.post(api_url, json=payload, timeout=request_timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("status") not in {"ok", "success"}:
            logger.warning(f"[FlareSolverr] {data.get('message') or data.get('error') or 'request failed'}")
        return data
    except Exception as e:
        logger.warning(f"[FlareSolverr] API unavailable at {api_url}: {e}")
        return None


def _flaresolverr_session_key(url: str) -> str:
    host = (urlparse(url).hostname or "default").lower()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", host)[:80]
    return f"cyoa_{safe}"


def _flaresolverr_get_session(url: str) -> Optional[str]:
    """Create/reuse a FlareSolverr session according to the configured policy."""
    if _FLARESOLVERR_SESSION_POLICY == "temporary":
        return None
    key = _flaresolverr_session_key(url)
    with _FLARESOLVERR_LOCK:
        existing = _FLARESOLVERR_SESSIONS.get(key)
        if existing:
            return existing
        if _FLARESOLVERR_SESSION_POLICY == "manual":
            logger.debug(f"[FlareSolverr] Manual session policy: no auto-create for {key}")
            return None
        payload: Dict[str, Any] = {"cmd": "sessions.create", "session": key}
        proxy_obj = _flaresolverr_payload_proxy()
        if proxy_obj:
            payload["proxy"] = proxy_obj
        data = _flaresolverr_post(payload, timeout=10)
        if data and data.get("status") == "ok":
            session_name = data.get("session") or key
            _FLARESOLVERR_SESSIONS[key] = session_name
            logger.info(f"[FlareSolverr] Session ready: {session_name}")
            return session_name
    return None


def flaresolverr_destroy_sessions() -> int:
    """Destroy all sessions created by this app instance."""
    destroyed = 0
    with _FLARESOLVERR_LOCK:
        sessions = list(_FLARESOLVERR_SESSIONS.values())
        _FLARESOLVERR_SESSIONS.clear()
    for sess in sessions:
        data = _flaresolverr_post({"cmd": "sessions.destroy", "session": sess}, timeout=10)
        if data and data.get("status") == "ok":
            destroyed += 1
    if destroyed:
        logger.info(f"[FlareSolverr] Destroyed {destroyed} session(s)")
    return destroyed


def flaresolverr_test_connection() -> Tuple[bool, str]:
    """Check whether FlareSolverr API is reachable."""
    data = _flaresolverr_post({"cmd": "sessions.list"}, timeout=10)
    if data and data.get("status") == "ok":
        sessions = data.get("sessions", [])
        return True, f"Connected. Sessions: {len(sessions) if isinstance(sessions, list) else 'unknown'}"
    return False, "Not reachable. Start FlareSolverr and check the URL."


def _apply_flaresolverr_solution_to_sessions(solution: Dict[str, Any], source_url: str) -> Dict[str, str]:
    """Copy cookies/user-agent from FlareSolverr into requests sessions."""
    headers: Dict[str, str] = {}
    ua = solution.get("userAgent") or solution.get("user_agent") or ""
    if ua:
        headers["User-Agent"] = ua
        try:
            _get_shared_session(False).headers.update({"User-Agent": ua})
            _get_shared_session(True).headers.update({"User-Agent": ua})
        except Exception:
            pass

    host = urlparse(source_url).hostname or ""
    cookies = solution.get("cookies") or []
    for cookie in cookies if isinstance(cookies, list) else []:
        try:
            name = cookie.get("name")
            value = cookie.get("value", "")
            domain = cookie.get("domain") or host
            path = cookie.get("path") or "/"
            if name:
                for sess in (_get_shared_session(False), _get_shared_session(True)):
                    sess.cookies.set(name, value, domain=domain, path=path)
        except Exception:
            pass
    return headers


def _response_from_flaresolverr_solution(solution: Dict[str, Any], url: str) -> requests.Response:
    """Build a requests.Response-like object from a FlareSolverr solution."""
    resp = requests.Response()
    status = int(solution.get("status") or 200)
    body = solution.get("response") or ""
    if isinstance(body, str):
        raw = body.encode("utf-8", errors="replace")
    elif isinstance(body, bytes):
        raw = body
    else:
        raw = json.dumps(body).encode("utf-8")
    resp.status_code = status
    resp._content = raw
    resp.url = solution.get("url") or url
    resp.encoding = "utf-8"
    headers = solution.get("headers") or {}
    if isinstance(headers, dict):
        resp.headers.update({str(k): str(v) for k, v in headers.items()})
    if "Content-Type" not in resp.headers:
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


def fetch_via_flaresolverr(url: str, extra_headers: Optional[Dict[str, str]] = None, timeout: Optional[int] = None) -> Optional[requests.Response]:
    """Solve/fetch URL through FlareSolverr and return a Response-like object."""
    session_name = _flaresolverr_get_session(url)
    payload: Dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int((timeout or _FLARESOLVERR_TIMEOUT) * 1000),
    }
    if session_name:
        payload["session"] = session_name
        payload["session_ttl_minutes"] = max(5, int(_FLARESOLVERR_TIMEOUT // 2 or 30))
    if _FLARESOLVERR_WAIT_AFTER:
        payload["waitInSeconds"] = int(_FLARESOLVERR_WAIT_AFTER)
    proxy_obj = _flaresolverr_payload_proxy()
    if proxy_obj:
        payload["proxy"] = proxy_obj
    # Do not send arbitrary request headers here. The documented request.get API
    # focuses on url/session/cookies/proxy/timeout/wait parameters. FlareSolverr
    # returns the browser User-Agent, which is then reused by normal requests.

    logger.info(f"[FlareSolverr] Solving/fetching: {url}")
    data = _flaresolverr_post(payload, timeout=(timeout or _FLARESOLVERR_TIMEOUT))
    if not data or data.get("status") not in {"ok", "success"}:
        return None
    solution = data.get("solution") or {}
    if not isinstance(solution, dict):
        return None
    _apply_flaresolverr_solution_to_sessions(solution, url)
    resp = _response_from_flaresolverr_solution(solution, url)
    if resp.status_code >= 400:
        logger.warning(f"[FlareSolverr] HTTP {resp.status_code}: {url}")
        return None
    logger.info(f"[FlareSolverr ✓] {url}")
    return resp


# ── Resume state ───────────────────────────────────────────────────────────
_RESUME_FILE = "download_state.json"


def load_resume_state(output_dir: str) -> dict:
    import pathlib as _pl
    path = os.path.join(output_dir, _RESUME_FILE)
    if not os.path.exists(path):
        return {"completed": [], "failed": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        completed = data.get("completed", [])
        failed    = data.get("failed", [])
        if not isinstance(completed, list): completed = []
        if not isinstance(failed, list):    failed = []
        completed = [u for u in completed if isinstance(u, str)]
        failed    = [u for u in failed    if isinstance(u, str)]
        return {"completed": completed, "failed": failed}
    except Exception:
        return {"completed": [], "failed": []}


def save_resume_state(output_dir: str, completed: list, failed: list) -> None:
    from datetime import datetime as _dt
    os.makedirs(output_dir, exist_ok=True)
    path     = os.path.join(output_dir, _RESUME_FILE)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"completed": completed, "failed": failed,
                       "updated_at": _dt.now().isoformat()},
                      f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.warning(f"Could not save resume state: {e}")
        try: os.remove(tmp_path)
        except Exception: pass


def clear_resume_state(output_dir: str) -> None:
    path = os.path.join(output_dir, _RESUME_FILE)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def launch_gui() -> None:
    try:
        import customtkinter as ctk
    except ImportError:
        # Fallback to plain tkinter with install hint
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "customtkinter not found",
                "Run:\n  pip install customtkinter\n\nFor a better GUI experience.",
            )
            root.destroy()
        except Exception:
            pass
        print("customtkinter not found. Install: pip install customtkinter")
        sys.exit(1)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    CYOADownloaderGUI(root)
    root.mainloop()


class CYOADownloaderGUI:
    # ── mode definitions ────────────────────────────────────────────
    MODES = [
        # (val,  icon, name,                 desc,                         section)
        ("__sec__",            "", "OUTPUT MODE",        "",                              ""),
        ("auto",               "⚡","Auto (detect)",      "Default: Website Folder",      ""),
        ("embed",              "📄","Embedded JSON",      "Gambar di-base64 dalam JSON",  ""),
        ("zip",                "🗜","ZIP",                "JSON + gambar terpisah",        ""),
        ("both",               "📦","Both",               "Embed + ZIP sekaligus",         ""),
        ("__sec__",            "", "WEBSITE MODE",       "",                              ""),
        ("website_zip",        "🌐","Website ZIP",        "Viewer + all assets",           ""),
        ("website_folder",     "📁","Website Folder",     "Viewer + all assets",           ""),
        ("__sec__",            "", "PURE WEBSITE",       "",                              ""),
        ("pure_website_zip",   "🔒","Pure Website ZIP",   "Viewer only, no project search",""),
        ("pure_website_folder","🔓","Pure Website Folder","Viewer only, no project search",""),
        ("__sec__",            "", "CYOAP_VUE",          "",                              ""),
        ("cyoap_vue_zip",      "⚡","cyoap_vue ZIP",      "Engine khusus cyoap_vue",       ""),
        ("cyoap_vue_folder",   "⚡","cyoap_vue Folder",   "Engine khusus cyoap_vue",       ""),
    ]

    BADGE_COLORS = {
        "auto":                ("#1e3a8a", "#93c5fd"),
        "embed":               ("#1e3a5f", "#60a5fa"),
        "zip":                 ("#1e1e3b", "#a78bfa"),
        "both":                ("#374151", "#d1d5db"),
        "website_zip":         ("#065f46", "#6ee7b7"),
        "website_folder":      ("#065f46", "#6ee7b7"),
        "pure_website_zip":    ("#4c1d95", "#c4b5fd"),
        "pure_website_folder": ("#4c1d95", "#c4b5fd"),
        "cyoap_vue_zip":       ("#78350f", "#fde68a"),
        "cyoap_vue_folder":    ("#78350f", "#fde68a"),
    }

    def __init__(self, root) -> None:
        import customtkinter as ctk
        self.root = root
        self.root.title(f"CYOA Downloader v{_APP_VERSION}")
        self.root.minsize(900, 640)
        self.root.geometry("1100x720")

        self._log_queue: log_queue_module.Queue = log_queue_module.Queue()
        self._queue_data: List[Dict] = []
        self._queue_rows: List = []
        self._is_running  = False
        self._paused      = threading.Event()
        self._paused.set()   # not paused initially (set = running)
        self._speed_samples: List[Tuple[float, int]] = []   # (timestamp, bytes)
        _ai_settings = _load_settings()
        self._ai_provider = _normalize_ai_provider(_ai_settings.get("ai_provider", "anthropic"))
        self._ai_key_storage = _normalize_ai_key_storage(_ai_settings.get("ai_key_storage", "session"))
        self._ai_api_key  = ""  # session-only in-memory key. Secrets are not loaded into GUI by default.
        if self._ai_key_storage == "plain":
            self._ai_api_key = _resolve_ai_api_key(storage="plain", provider=self._ai_provider)
        self._ai_enabled  = _ai_settings.get("ai_enabled", False)
        self._ai_model    = _get_ai_model(self._ai_provider)
        self._ai_mode     = _normalize_ai_mode(_ai_settings.get("ai_mode", "auto_fallback"))
        self._mode_var    = "auto"
        self._mode_btns: Dict = {}
        self._is_dark     = True
        self._language    = _load_settings().get("language", "id") if _load_settings().get("language", "id") in {"id", "en"} else "id"
        self._themed: List = []
        self._last_results: List[Dict] = []
        self._server_thread = None
        self._server_obj    = None
        self._ytdlp_enabled = True  # default on; unchecked if yt-dlp not installed
        _load_cloudflare_settings()

        self._setup_ui()
        self._apply_language()
        self._setup_logging()
        self._poll_log()

    def _p(self) -> Dict[str, str]:
        """Return current palette."""
        if self._is_dark:
            return {
                "bg":        "#0e1117", "panel":    "#0a0d13",
                "surface":   "#141922", "surface2": "#1e2433",
                "fg":        "#e2e8f0", "muted":    "#475569",
                "muted2":    "#334155", "accent":   "#3b82f6",
                "border":    "#1e2433", "sidebar":  "#0a0d13",
                "input_bg":  "#141922", "input_fg": "#e2e8f0",
                "log_bg":    "#0a0d13", "log_fg":   "#475569",
                "sel_row":   "#0f1729", "sel_bar":  "#3b82f6",
                "sel_icon":  "#1e3a5f", "sel_nm":   "#60a5fa",
                "sel_desc":  "#3b82f6",
                # theme-aware semantic colors
                "danger_bg": "#1f0a0a", "danger_fg": "#f87171",
                "danger_hv": "#3b0f0f",
                "accentbg":  "#0c1a2e", "accentbg_hv": "#162b4a",
                "srv_fg":    "#6ee7b7", "srv_hv":   "#065f46",
            }
        return {
            "bg":        "#f1f5f9", "panel":    "#ffffff",
            "surface":   "#e2e8f0", "surface2": "#cbd5e1",
            "fg":        "#0f172a", "muted":    "#64748b",
            "muted2":    "#94a3b8", "accent":   "#3b82f6",
            "border":    "#e2e8f0", "sidebar":  "#f8fafc",
            "input_bg":  "#ffffff", "input_fg": "#0f172a",
            "log_bg":    "#f8fafc", "log_fg":   "#475569",
            "sel_row":   "#dbeafe", "sel_bar":  "#3b82f6",
            "sel_icon":  "#bfdbfe", "sel_nm":   "#1d4ed8",
            "sel_desc":  "#2563eb",
            # theme-aware semantic colors
            "danger_bg": "#fee2e2", "danger_fg": "#dc2626",
            "danger_hv": "#fecaca",
            "accentbg":  "#dbeafe", "accentbg_hv": "#bfdbfe",
            "srv_fg":    "#059669", "srv_hv":   "#d1fae5",
        }

    def _apply_theme(self) -> None:
        """Re-apply palette to all tracked widgets + sidebar + queue rows + log."""
        import customtkinter as ctk
        p = self._p()
        ctk.set_appearance_mode("dark" if self._is_dark else "light")

        for widget, keys in self._themed:
            try:
                widget.configure(**{k: p[v] for k, v in keys.items()})
            except Exception:
                pass

        # Sidebar
        try:
            self._sidebar.configure(fg_color=p["sidebar"],
                                    scrollbar_button_color=p["surface2"],
                                    scrollbar_button_hover_color=p["muted2"])
        except Exception:
            pass

        # Sidebar section labels
        if hasattr(self, "_sec_labels"):
            for lbl in self._sec_labels:
                try: lbl.configure(text_color=p["muted2"])
                except Exception: pass
        if hasattr(self, "_sec_dividers"):
            for div in self._sec_dividers:
                try: div.configure(fg_color=p["border"])
                except Exception: pass

        # Mode buttons
        self._select_mode(self._mode_var)

        # Info box in sidebar
        if hasattr(self, "_info_box"):
            try:
                p2 = self._p()
                self._info_box.configure(fg_color=p2["surface2"])
                self._info_body.configure(text_color=p2["muted"])
            except Exception:
                pass
        self._update_mode_info(self._mode_var if hasattr(self, "_mode_var") else "auto")

        # Queue scrollable frame bg
        if hasattr(self, "_qlist"):
            try:
                self._qlist.configure(
                    fg_color=p["bg"],
                    scrollbar_button_color=p["surface2"],
                    scrollbar_button_hover_color=p["muted2"])
            except Exception:
                pass

        # Row B scrollable frame + scrollbar
        if hasattr(self, "_rowB"):
            try:
                self._rowB.configure(
                    fg_color=p["panel"],
                    scrollbar_button_color=p["surface2"],
                    scrollbar_button_hover_color=p["muted"])
            except Exception:
                pass

        # Queue rows
        for row, dot, url_lbl, badge, rm in self._queue_rows:
            try:
                row.configure(fg_color=p["surface"],  border_color=p["border"])
                dot.configure(bg=p["surface"])
                url_lbl.configure(text_color=p["muted"])
                rm.configure(fg_color="transparent", hover_color=p["surface2"],
                             text_color=p["muted"])
            except Exception:
                pass

        # Log widget
        if hasattr(self, "_log_txt"):
            try:
                self._log_txt.configure(bg=p["log_bg"], fg=p["log_fg"])
            except Exception:
                pass

        # Theme/language pills
        if hasattr(self, "_theme_pill"):
            try:
                self._theme_pill.configure(
                    fg_color=p["surface2"],
                    selected_color=p["accent"],
                    unselected_color=p["surface2"],
                )
            except Exception:
                pass
        if hasattr(self, "_lang_pill"):
            try:
                self._lang_pill.configure(
                    fg_color=p["surface2"],
                    selected_color=p["accent"],
                    unselected_color=p["surface2"],
                )
            except Exception:
                pass

        # Speed graph widgets (tk.Canvas + tk.Label — not CTk)
        if hasattr(self, "_speed_canvas"):
            try:
                self._speed_canvas.configure(bg=p["surface2"])
            except Exception:
                pass
        if hasattr(self, "_speed_label"):
            try:
                self._speed_label.configure(bg=p["panel"], fg=p["muted"])
            except Exception:
                pass


    # ════════════════════════════════════════════════════════════════
    # UI BUILD
    # ════════════════════════════════════════════════════════════════
    def _setup_ui(self) -> None:
        import customtkinter as ctk
        p = self._p()
        self._sec_labels:  List = []
        self._sec_dividers: List = []

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

        def T(widget, **keys):
            """Register widget for theme updates. keys = ctk_prop → palette_key."""
            self._themed.append((widget, keys))
            return widget

        # ── TITLEBAR ────────────────────────────────────────────────
        tb = T(ctk.CTkFrame(self.root, height=46, corner_radius=0,
                            fg_color=p["panel"]),
               fg_color="panel")
        tb.grid(row=0, column=0, columnspan=2, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkLabel(tb, text=" C ", font=ctk.CTkFont("Consolas", 12, "bold"),
                            fg_color="#3b82f6", text_color="#ffffff",
                            corner_radius=6, width=28, height=28)
        logo.grid(row=0, column=0, padx=(14, 8), pady=9)

        T(ctk.CTkLabel(tb, text="CYOA Downloader",
                       font=ctk.CTkFont("Segoe UI", 13, "bold"),
                       text_color=p["fg"]),
          text_color="fg").grid(row=0, column=1, sticky="w")
        T(ctk.CTkLabel(tb, text=f"v{_APP_VERSION}",
                       font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"]),
          text_color="muted").grid(row=0, column=2, padx=(4, 0), sticky="w")

        pill = ctk.CTkSegmentedButton(
            tb, values=["Dark", "Light"],
            command=self._toggle_theme,
            font=ctk.CTkFont("Segoe UI", 11),
            width=120, height=28,
            fg_color=p["surface2"],
            selected_color="#3b82f6",
            selected_hover_color="#2563eb",
            unselected_color=p["surface2"],
            unselected_hover_color=p["surface"],
            text_color="#ffffff",
        )
        pill.set("Dark")
        pill.grid(row=0, column=3, padx=(8, 6), pady=9, sticky="e")
        self._theme_pill = pill

        lang_pill = ctk.CTkSegmentedButton(
            tb, values=["ID", "EN"],
            command=self._toggle_language,
            font=ctk.CTkFont("Segoe UI", 11),
            width=72, height=28,
            fg_color=p["surface2"],
            selected_color="#3b82f6",
            selected_hover_color="#2563eb",
            unselected_color=p["surface2"],
            unselected_hover_color=p["surface"],
            text_color="#ffffff",
        )
        lang_pill.set("EN" if self._language == "en" else "ID")
        lang_pill.grid(row=0, column=4, padx=(0, 14), pady=9, sticky="e")
        self._lang_pill = lang_pill

        # ── SIDEBAR ─────────────────────────────────────────────────
        sb = ctk.CTkScrollableFrame(
            self.root, width=230, corner_radius=0,
            fg_color=p["sidebar"],
            scrollbar_button_color=p["surface2"],
            scrollbar_button_hover_color=p["muted2"],
        )
        sb.grid(row=1, column=0, sticky="nsew")
        sb.grid_columnconfigure(0, weight=1)
        self._sidebar = sb

        for entry in self.MODES:
            val, icon, name, desc, _ = entry
            if val == "__sec__":
                lbl = ctk.CTkLabel(sb, text=name,
                                   font=ctk.CTkFont("Segoe UI", 9, "bold"),
                                   text_color=p["muted2"], anchor="w")
                lbl.pack(fill="x", padx=14, pady=(12, 2))
                self._sec_labels.append(lbl)
                div = ctk.CTkFrame(sb, height=1, fg_color=p["border"], corner_radius=0)
                div.pack(fill="x", padx=10, pady=(0, 2))
                self._sec_dividers.append(div)
                continue

            # Row frame — fixed height, clickable
            row = ctk.CTkFrame(sb, corner_radius=0, fg_color="transparent",
                               cursor="hand2", height=46)
            row.pack(fill="x")
            row.pack_propagate(False)

            # Left accent bar (3px)
            bar = ctk.CTkFrame(row, width=3, corner_radius=0,
                               fg_color="transparent")
            bar.pack(side="left", fill="y")

            # Icon
            icon_box = ctk.CTkLabel(row, text=icon,
                                    width=24, height=24,
                                    font=ctk.CTkFont("Segoe UI Emoji", 13),
                                    fg_color="transparent",
                                    text_color="#64748b",
                                    corner_radius=5)
            icon_box.pack(side="left", padx=(8, 6), pady=7)

            # Name + desc stacked in a sub-frame
            txt = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
            txt.pack(side="left", fill="x", expand=True, pady=5, padx=(0, 6))

            name_lbl = ctk.CTkLabel(txt, text=name, anchor="w",
                                    font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                    text_color="#94a3b8")
            name_lbl.pack(fill="x")

            desc_lbl = ctk.CTkLabel(txt, text=desc, anchor="w",
                                    font=ctk.CTkFont("Segoe UI", 9),
                                    text_color="#475569")
            desc_lbl.pack(fill="x")

            self._mode_btns[val] = (row, bar, icon_box, name_lbl, desc_lbl)

            def _on_click(e=None, v=val): self._select_mode(v)
            def _on_enter(e, v=val, r=row, ib=icon_box, t=txt, nl=name_lbl, dl=desc_lbl):
                if self._mode_var != v:
                    hover = self._p()["surface"]
                    for w in (r, ib, t, nl, dl): w.configure(fg_color=hover)
            def _on_leave(e): self._select_mode(self._mode_var)

            for w in (row, icon_box, txt, name_lbl, desc_lbl):
                w.bind("<Button-1>", _on_click)
                w.bind("<Enter>",    _on_enter)
                w.bind("<Leave>",    _on_leave)

        # ── Mode info box (bottom of sidebar) ───────────────────────
        info_box = ctk.CTkFrame(sb, corner_radius=6,
                                fg_color=p["surface2"], border_width=0)
        info_box.pack(fill="x", padx=10, pady=(12, 8))
        self._info_title = ctk.CTkLabel(
            info_box, text="Auto (detect)",
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color="#60a5fa", anchor="w")
        self._info_title.pack(fill="x", padx=8, pady=(8, 2))
        self._info_body = ctk.CTkLabel(
            info_box, text="",
            font=ctk.CTkFont("Segoe UI", 9),
            text_color=p["muted"], anchor="w",
            wraplength=190, justify="left")
        self._info_body.pack(fill="x", padx=8, pady=(0, 8))
        self._info_output = ctk.CTkLabel(
            info_box, text="",
            font=ctk.CTkFont("Consolas", 9),
            text_color="#3b82f6", anchor="w")
        self._info_output.pack(fill="x", padx=8, pady=(0, 8))
        self._info_box = info_box

        # Select default
        self._select_mode("auto", update_ui=False)
        self._update_mode_info("auto")

        # ── MAIN ────────────────────────────────────────────────────
        main = T(ctk.CTkFrame(self.root, corner_radius=0, fg_color=p["bg"]),
                 fg_color="bg")
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # ─ Input panel ──────────────────────────────────────────────
        inp = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["bg"],
                             border_width=0), fg_color="bg")
        inp.grid(row=0, column=0, sticky="ew")
        inp.grid_columnconfigure(1, weight=1)

        # "Input" header
        T(ctk.CTkLabel(inp, text="Input",
                       font=ctk.CTkFont("Segoe UI", 12, "bold"),
                       text_color=p["accent"], anchor="w"),
          text_color="accent").grid(row=0, column=0, columnspan=5,
                                    sticky="w", padx=14, pady=(10, 2))

        T(ctk.CTkFrame(inp, height=1, fg_color=p["border"], corner_radius=0),
          fg_color="border").grid(row=10, column=0, columnspan=6, sticky="ew")

        T(ctk.CTkLabel(inp, text="URL", font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"], width=50),
          text_color="muted").grid(row=1, column=0, padx=(14, 6), pady=(6, 4), sticky="w")

        self._url_var = ctk.StringVar()
        url_e = T(ctk.CTkEntry(inp, textvariable=self._url_var,
                               placeholder_text="https://author.neocities.org/cyoa/",
                               font=ctk.CTkFont("Segoe UI", 11),
                               fg_color=p["input_bg"], border_color=p["border"],
                               text_color=p["input_fg"], height=34),
                  fg_color="input_bg", border_color="border", text_color="input_fg")
        url_e.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=(6, 4))
        url_e.bind("<Return>", lambda _: self._add_to_queue())

        T(ctk.CTkLabel(inp, text="Filename", font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"]),
          text_color="muted").grid(row=1, column=2, padx=(0, 6), pady=(6, 4), sticky="w")

        self._fn_var = ctk.StringVar()
        T(ctk.CTkEntry(inp, textvariable=self._fn_var,
                       placeholder_text="(opsional)",
                       font=ctk.CTkFont("Segoe UI", 11),
                       fg_color=p["input_bg"], border_color=p["border"],
                       text_color=p["input_fg"], height=34, width=160),
          fg_color="input_bg", border_color="border", text_color="input_fg").grid(
            row=1, column=3, padx=(0, 6), pady=(6, 4))

        ctk.CTkButton(inp, text="Tambah +", height=34,
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      fg_color="#3b82f6", hover_color="#2563eb",
                      command=self._add_to_queue).grid(
            row=1, column=4, padx=(0, 14), pady=(6, 4))

        # Options row
        # ── Options: 2-row compact layout ───────────────────────────
        # Row 1: numeric inputs + Import/Help buttons (right-aligned)
        # Row 2: toggleable checkboxes
        opt_wrap = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        opt_wrap.grid(row=2, column=0, columnspan=5, sticky="ew",
                      padx=14, pady=(0, 6))
        opt_wrap.grid_columnconfigure(0, weight=1)

        # ── Row 1: numerics ──────────────────────────────────────────
        row1 = T(ctk.CTkFrame(opt_wrap, fg_color="transparent"), fg_color="bg")
        row1.grid(row=0, column=0, sticky="ew")

        def _num_lbl(parent, text):
            return T(ctk.CTkLabel(parent, text=text,
                                  font=ctk.CTkFont("Segoe UI", 10),
                                  text_color=p["muted"]),
                     text_color="muted")

        def _num_entry(parent, var, width=46):
            return T(ctk.CTkEntry(parent, textvariable=var,
                                  width=width, height=26,
                                  fg_color=p["input_bg"],
                                  border_color=p["border"],
                                  text_color=p["input_fg"],
                                  font=ctk.CTkFont("Consolas", 10),
                                  justify="center"),
                     fg_color="input_bg", border_color="border",
                     text_color="input_fg")

        _num_lbl(row1, "Threads:").pack(side="left")
        self._threads_var = ctk.StringVar(value=str(DEFAULT_MAX_WORKERS))
        _num_entry(row1, self._threads_var, 42).pack(side="left", padx=(3, 12))

        _num_lbl(row1, "Retry (s):").pack(side="left")
        self._wait_var = ctk.StringVar(value=str(DEFAULT_WAIT_TIME))
        _num_entry(row1, self._wait_var, 46).pack(side="left", padx=(3, 12))

        _num_lbl(row1, "BW (KB/s):").pack(side="left")
        self._bw_var = ctk.StringVar(value="0")
        _num_entry(row1, self._bw_var, 46).pack(side="left", padx=(3, 12))

        # Proxy field — compact, always visible
        _num_lbl(row1, "Proxy:").pack(side="left")
        _proxy_init = _get_active_proxy() or ""
        self._proxy_var = ctk.StringVar(value=_proxy_init)
        _proxy_entry = T(ctk.CTkEntry(
            row1, textvariable=self._proxy_var,
            width=160, height=26, placeholder_text="http://127.0.0.1:7890",
            fg_color=p["input_bg"], border_color=p["border"],
            text_color=p["input_fg"],
            font=ctk.CTkFont("Consolas", 9)),
            fg_color="input_bg", border_color="border", text_color="input_fg")
        _proxy_entry.pack(side="left", padx=(3, 4))

        def _on_proxy_set(*_):
            v = self._proxy_var.get().strip()
            _set_active_proxy(v if v else None)
            s = _load_settings(); s["proxy"] = v; _save_settings(s)
        self._proxy_var.trace_add("write", _on_proxy_set)
        # Load from settings
        _saved_proxy = _load_settings().get("proxy", "")
        if _saved_proxy:
            self._proxy_var.set(_saved_proxy)
            _set_active_proxy(_saved_proxy)

        # DNS — preset dropdown + optional custom entry
        _num_lbl(row1, "DNS:").pack(side="left", padx=(8, 0))

        _saved_dns  = _load_settings().get("dns", "")
        self._dns_var = ctk.StringVar(value=_saved_dns)

        # Find matching preset label (or "Custom…")
        _preset_names = list(DNS_PRESETS.keys())
        _init_label   = next(
            (k for k, v in DNS_PRESETS.items() if v == _saved_dns and v != "__custom__"),
            "Custom…" if _saved_dns else "System (default)"
        )
        self._dns_preset_var = ctk.StringVar(value=_init_label)

        def _on_dns_preset_change(label: str) -> None:
            ip = DNS_PRESETS.get(label, "")
            if ip == "__custom__":
                # Show custom entry. Custom DNS is applied after typing pauses,
                # not on every keystroke.
                _dns_custom_entry.pack(side="left", padx=(2, 0))
            else:
                _dns_custom_entry.pack_forget()
                self._dns_trace_suspended = True
                try:
                    self._dns_var.set(ip)
                finally:
                    self._dns_trace_suspended = False
                _apply_dns(ip)

        def _apply_dns(ip: str) -> None:
            _set_active_dns(ip)
            s = _load_settings(); s["dns"] = ip; _save_settings(s)

        T(ctk.CTkOptionMenu(
            row1, variable=self._dns_preset_var,
            values=_preset_names,
            width=148, height=26,
            font=ctk.CTkFont("Segoe UI", 9),
            fg_color=p["surface2"], button_color=p["surface"],
            button_hover_color=p["surface2"],
            text_color=p["muted"], dropdown_fg_color=p["surface"],
            dropdown_text_color=p["fg"],
            command=_on_dns_preset_change),
          fg_color="surface2", button_color="surface",
          text_color="muted").pack(side="left", padx=(3, 0))

        _dns_custom_entry = T(ctk.CTkEntry(
            row1, textvariable=self._dns_var,
            width=100, height=26, placeholder_text="1.1.1.1",
            fg_color=p["input_bg"], border_color=p["border"],
            text_color=p["input_fg"],
            font=ctk.CTkFont("Consolas", 9)),
            fg_color="input_bg", border_color="border", text_color="input_fg")

        self._dns_trace_suspended = False
        self._dns_after_id = None
        def _on_dns_custom(*_):
            if getattr(self, "_dns_trace_suspended", False):
                return
            try:
                if not _dns_custom_entry.winfo_ismapped():
                    return
            except Exception:
                return
            # Debounce custom DNS typing to avoid applying half-written values
            # and to avoid duplicate DNS log entries.
            try:
                if self._dns_after_id is not None:
                    self.root.after_cancel(self._dns_after_id)
            except Exception:
                pass
            self._dns_after_id = self.root.after(
                750, lambda: _apply_dns(self._dns_var.get().strip())
            )
        self._dns_var.trace_add("write", _on_dns_custom)

        # Only show custom entry if no preset matches
        if _init_label == "Custom…" and _saved_dns:
            _dns_custom_entry.pack(side="left", padx=(2, 0))

        # Apply saved DNS on startup
        if _saved_dns:
            _set_active_dns(_saved_dns)

        # Right side: Import + Help
        self._import_button = T(ctk.CTkButton(row1, text="Import List…", height=26,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._import_list),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2")
        self._import_button.pack(side="right", padx=(4, 0))
        T(ctk.CTkButton(row1, text="?", width=26, height=26,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._show_format_guide),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").pack(side="right", padx=(0, 4))

        # ── Row 2: checkboxes ─────────────────────────────────────────
        row2 = T(ctk.CTkFrame(opt_wrap, fg_color="transparent"), fg_color="bg")
        row2.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        def _chk(parent, text, var, color="#3b82f6", hover="#2563eb", cmd=None, px=10):
            kw = dict(variable=var, font=ctk.CTkFont("Segoe UI", 10),
                      checkbox_width=15, checkbox_height=15,
                      fg_color=color, hover_color=hover,
                      text_color=p["muted"])
            if cmd: kw["command"] = cmd
            return T(ctk.CTkCheckBox(parent, text=text, **kw),
                     text_color="muted")

        self._fonts_var   = ctk.BooleanVar(value=True)
        self._analyse_var = ctk.BooleanVar(value=True)
        self._cf_mode_var = ctk.StringVar(value=_display_cloudflare_mode(_load_settings().get("cloudflare_mode", "auto")))
        self._http2_var = ctk.BooleanVar(value=bool(_load_settings().get("http2_enabled", False)))
        self._ytdlp_var   = ctk.BooleanVar(value=True)

        _chk(row2, "Fonts", self._fonts_var).pack(side="left", padx=(0, px := 12))
        _chk(row2, "Font Analysis", self._analyse_var).pack(side="left", padx=(0, 12))

        # Compact Cloudflare selector. Detailed settings live in the Cloudflare panel.
        self._cf_label = _num_lbl(row2, "Cloudflare:")
        self._cf_label.pack(side="left", padx=(0, 3))
        self._cf_mode_menu = T(ctk.CTkOptionMenu(
            row2, variable=self._cf_mode_var,
            values=["Off", "Auto", "cloudscraper", "FlareSolverr"],
            width=126, height=26,
            font=ctk.CTkFont("Segoe UI", 9),
            fg_color=p["surface2"], button_color=p["surface"],
            button_hover_color=p["surface2"],
            text_color=p["muted"], dropdown_fg_color=p["surface"],
            dropdown_text_color=p["fg"],
            command=self._on_cloudflare_mode_change),
            fg_color="surface2", button_color="surface", text_color="muted")
        self._cf_mode_menu.pack(side="left", padx=(0, 12))
        self._on_cloudflare_mode_change(self._cf_mode_var.get(), validate=False)

        _chk(row2, "HTTP/2", self._http2_var,
             color="#06b6d4", hover="#0891b2",
             cmd=self._on_http2_toggle).pack(side="left", padx=(0, 12))
        _chk(row2, "YT Audio", self._ytdlp_var,
             color="#ef4444", hover="#dc2626",
             cmd=self._on_ytdlp_toggle).pack(side="left", padx=(0, 12))

        # CYOA Manager checkbox
        _cm_settings = _load_settings()
        _cm_auto     = _cm_settings.get("cyoa_mgr_enabled")
        _cm_default  = bool(_find_cyoa_manager_db()) if _cm_auto is None else bool(_cm_auto)
        self._cyoa_mgr_var = ctk.BooleanVar(value=_cm_default)
        def _on_cyoa_mgr_toggle():
            v = self._cyoa_mgr_var.get()
            s = _load_settings(); s["cyoa_mgr_enabled"] = v; _save_settings(s)
            if hasattr(self, '_cm_btn'):
                p2 = self._p()
                self._cm_btn.configure(
                    text="📤 CYOA Mgr  " + ("✓" if v else "✗"),
                    text_color=p2["accent"] if v else p2["muted"])
        _chk(row2, "→ CYOA Mgr", self._cyoa_mgr_var,
             cmd=_on_cyoa_mgr_toggle).pack(side="left", padx=(0, 12))

        # AI Assist toggle
        self._ai_var = ctk.BooleanVar(value=self._ai_enabled)
        def _on_ai_toggle():
            self._ai_enabled = self._ai_var.get()
            s = _load_settings(); s["ai_enabled"] = self._ai_enabled; _save_settings(s)
            if hasattr(self, '_ai_btn'):
                p2 = self._p()
                self._ai_btn.configure(
                    text="🤖 AI  " + ("ON" if self._ai_enabled else "OFF"),
                    text_color=p2["accent"] if self._ai_enabled else p2["muted"])
        _chk(row2, "🤖 AI Assist", self._ai_var,
             color="#8b5cf6", hover="#7c3aed",
             cmd=_on_ai_toggle).pack(side="left", padx=(0, 0))

        # Output folder row
        dirf = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        dirf.grid(row=3, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 12))
        dirf.grid_columnconfigure(1, weight=1)

        self._output_label = T(ctk.CTkLabel(dirf, text="Output folder:", font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"], width=90),
          text_color="muted")
        self._output_label.grid(row=0, column=0, sticky="w")
        self._outdir_var = ctk.StringVar(value=os.getcwd())
        T(ctk.CTkEntry(dirf, textvariable=self._outdir_var,
                       font=ctk.CTkFont("Segoe UI", 11),
                       fg_color=p["input_bg"], border_color=p["border"],
                       text_color=p["muted"], height=30),
          fg_color="input_bg", border_color="border", text_color="muted").grid(
            row=0, column=1, sticky="ew", padx=(6, 6))
        self._browse_button = T(ctk.CTkButton(dirf, text="Browse…", height=30, width=80,
                        font=ctk.CTkFont("Segoe UI", 11),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._browse),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2")
        self._browse_button.grid(row=0, column=2)

        # ─ Queue panel ──────────────────────────────────────────────
        qf = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["bg"],
                            border_width=0), fg_color="bg")
        qf.grid(row=1, column=0, sticky="ew")
        qf.grid_columnconfigure(0, weight=1)
        T(ctk.CTkFrame(qf, height=1, fg_color=p["border"], corner_radius=0),
          fg_color="border").grid(row=0, column=0, columnspan=3, sticky="ew")

        qhdr = T(ctk.CTkFrame(qf, fg_color="transparent"), fg_color="bg")
        qhdr.grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 4))
        qhdr.grid_columnconfigure(0, weight=1)

        self._queue_count_var = ctk.StringVar(value="QUEUE — 0 ITEMS")
        T(ctk.CTkLabel(qhdr, textvariable=self._queue_count_var,
                       font=ctk.CTkFont("Segoe UI", 10, "bold"),
                       text_color=p["muted"]),
          text_color="muted").grid(row=0, column=0, sticky="w")

        T(ctk.CTkButton(qhdr, text="Clear All", height=26, width=72,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._clear_queue),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=2, padx=(4, 0))
        T(ctk.CTkButton(qhdr, text="Remove", height=26, width=66,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._remove),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=1)

        self._qlist = ctk.CTkScrollableFrame(
            qf, height=140, corner_radius=0,
            fg_color=p["bg"],
            scrollbar_button_color=p["surface2"],
            scrollbar_button_hover_color=p["muted2"],
        )
        self._qlist.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 8))
        self._qlist.grid_columnconfigure(0, weight=1)

        T(ctk.CTkFrame(qf, height=1, fg_color=p["border"], corner_radius=0),
          fg_color="border").grid(row=3, column=0, sticky="ew")

        # ─ Action bar ───────────────────────────────────────────────
        # ══ ACTION BAR — 2 static rows, no horizontal scroll ══════════
        ab = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["panel"],
                            border_width=0), fg_color="panel")
        ab.grid(row=2, column=0, sticky="ew")
        ab.grid_columnconfigure(0, weight=1)

        # helper — factory for secondary icon buttons
        def _ab_btn(parent, text, cmd, *, accent=False, danger=False,
                    green=False, width=None):
            kw = dict(
                text=text, height=30,
                font=ctk.CTkFont("Segoe UI", 10, "bold" if accent else "normal"),
                fg_color="#1d4ed8" if accent else p["surface2"],
                hover_color="#1e40af" if accent else
                            "#7f1d1d" if danger else
                            "#065f46" if green else p["surface"],
                text_color="#ffffff" if accent else
                           "#f87171" if danger else
                           "#6ee7b7" if green else p["muted"],
                border_width=0, corner_radius=6,
                command=cmd,
            )
            if width: kw["width"] = width
            return ctk.CTkButton(parent, **kw)

        # ── Row A: primary (Download + status + progress) ─────────────
        rowA = T(ctk.CTkFrame(ab, fg_color=p["panel"], corner_radius=0),
                 fg_color="panel")
        rowA.grid(row=0, column=0, sticky="ew", padx=0)
        rowA.grid_columnconfigure(2, weight=1)   # spacer expands

        self._dl_btn = ctk.CTkButton(
            rowA, text="▶  Download All", height=40,
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            fg_color="#3b82f6", hover_color="#2563eb",
            corner_radius=8, command=self._start)
        self._dl_btn.grid(row=0, column=0, padx=(12, 6), pady=(8, 4))

        T(ctk.CTkButton(rowA, text="🔍 Preview", height=40,
                        font=ctk.CTkFont("Segoe UI", 11),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=0, corner_radius=8,
                        command=self._preview_queue),
          fg_color="surface2", text_color="muted").grid(
            row=0, column=1, padx=(0, 4), pady=(8, 4))

        # Spacer column (col 2 expands)
        ctk.CTkFrame(rowA, fg_color="transparent", height=1).grid(row=0, column=2, sticky="ew")

        # Status label + progress on the right of row A
        self._status_var = ctk.StringVar(value="Idle")
        self._status_lbl = T(ctk.CTkLabel(
            rowA, textvariable=self._status_var,
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=p["muted"], anchor="e"),
            text_color="muted")
        self._status_lbl.grid(row=0, column=3, padx=(0, 10), pady=(8, 4), sticky="e")

        self._pb = T(ctk.CTkProgressBar(
            rowA, width=110, height=5,
            fg_color=p["surface2"], progress_color="#3b82f6",
            mode="indeterminate", indeterminate_speed=1),
            fg_color="surface2")
        self._pb.grid(row=0, column=4, padx=(0, 12), pady=(8, 4))

        # Serve button — right of row A
        self._srv_btn = T(ctk.CTkButton(
            rowA, text="⚡ Serve", height=40,
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color=p["surface2"], hover_color=p["srv_hv"],
            text_color=p["srv_fg"], border_width=0, corner_radius=8,
            command=self._toggle_server),
            fg_color="surface2", hover_color="srv_hv", text_color="srv_fg")
        self._srv_btn.grid(row=0, column=5, padx=(0, 4), pady=(8, 4))
        self._server_running = False

        T(ctk.CTkButton(rowA, text="📁 Open Folder", height=40,
                        font=ctk.CTkFont("Segoe UI", 11),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=0, corner_radius=8,
                        command=self._open_folder),
          fg_color="surface2", text_color="muted").grid(
            row=0, column=6, padx=(0, 12), pady=(8, 4))

        # Thin divider
        ctk.CTkFrame(ab, height=1, fg_color=p["border"], corner_radius=0).grid(
            row=1, column=0, sticky="ew")

        # ── Row B: scrollable tool strip ──────────────────────────────
        # Wrap in a container so the scrollbar sits INSIDE the panel neatly
        rowB_wrap = ctk.CTkFrame(ab, fg_color=p["panel"], corner_radius=0, height=58)
        rowB_wrap.grid(row=2, column=0, sticky="ew")
        rowB_wrap.grid_propagate(False)
        rowB_wrap.grid_columnconfigure(0, weight=1)
        rowB_wrap.grid_rowconfigure(0, weight=1)
        T(rowB_wrap, fg_color="panel")

        rowB = ctk.CTkScrollableFrame(
            rowB_wrap, orientation="horizontal",
            fg_color=p["panel"], height=52,
            scrollbar_button_color=p["surface2"],
            scrollbar_button_hover_color=p["muted"],
        )
        rowB.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        T(rowB, fg_color="panel")
        self._rowB = rowB

        # Pill factory — uses palette KEYS, registers with T() for theme updates
        def _pill(parent, text, cmd, *,
                  bg="surface2", fg="muted", hv="surface", icon=None):
            label = f"{icon}  {text}" if icon else text
            btn = ctk.CTkButton(
                parent, text=label, height=36,
                font=ctk.CTkFont("Segoe UI", 11),
                fg_color=p[bg], hover_color=p[hv],
                text_color=p[fg], corner_radius=18,
                border_width=0, command=cmd,
            )
            T(btn, fg_color=bg, hover_color=hv, text_color=fg)
            return btn

        def _sep():
            f = ctk.CTkFrame(rowB, width=1, height=24, fg_color=p["border"])
            T(f, fg_color="border")
            f.pack(side="left", padx=6, pady=8)

        # Group 1 — Library
        _pill(rowB, "Viewers",      self._manage_offline_viewers, icon="🌐").pack(
            side="left", padx=(10, 2), pady=8)
        _pill(rowB, "Batch Export", self._batch_export_panel,     icon="📦").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Cloudflare", self._cloudflare_panel, icon="☁").pack(
            side="left", padx=(0, 2), pady=8)
        _sep()

        # Group 2 — Info
        _pill(rowB, "Results", self._show_results,      icon="📋").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Panduan", self._show_feature_guide, icon="📖").pack(
            side="left", padx=(0, 2), pady=8)
        _sep()

        # Group 3 — Retry (danger palette keys)
        self._retry_btn = _pill(
            rowB, "Retry Failed", self._retry_failed,
            bg="danger_bg", fg="danger_fg", hv="danger_hv", icon="↺")
        self._retry_btn.pack(side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Retry Images", self._retry_failed_images, icon="🖼").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Retry YT Audio", self._retry_youtube_audio, icon="🎵").pack(
            side="left", padx=(0, 2), pady=8)
        _sep()

        # Group 4 — CYOA Mgr (stateful, accent palette keys)
        _cm_on = self._cyoa_mgr_var.get()
        self._cm_btn = _pill(
            rowB,
            "CYOA Mgr ✓" if _cm_on else "CYOA Mgr ✗",
            self._cyoa_manager_panel,
            bg="accentbg" if _cm_on else "surface2",
            fg="accent"   if _cm_on else "muted",
            hv="accentbg_hv" if _cm_on else "surface",
            icon="📤",
        )
        self._cm_btn.pack(side="left", padx=(0, 2), pady=8)
        _sep()

        # Group 5 — Tools
        self._pause_btn = _pill(rowB, "Pause", self._toggle_pause, icon="⏸")
        self._pause_btn.pack(side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Cache",  self._cache_manager_panel, icon="💾").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Updates", self._check_updates_panel, icon="🔄").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "Batch Check", self._batch_update_panel, icon="📥").pack(
            side="left", padx=(0, 2), pady=8)
        _pill(rowB, "CM Import", self._import_from_cyoa_manager_panel, icon="📚").pack(
            side="left", padx=(0, 2), pady=8)
        _ai_on = self._ai_enabled
        self._ai_btn = _pill(
            rowB, "AI  " + ("ON" if _ai_on else "OFF"),
            self._ai_settings_panel, icon="🤖",
            bg="accentbg" if _ai_on else "surface2",
            fg="accent"   if _ai_on else "muted",
            hv="accentbg_hv" if _ai_on else "surface",
        )
        self._ai_btn.pack(side="left", padx=(0, 10), pady=8)

        # ─ Log ──────────────────────────────────────────────────────
        lf = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["panel"]),
               fg_color="panel")
        lf.grid(row=3, column=0, sticky="nsew")
        lf.grid_rowconfigure(1, weight=1)
        lf.grid_columnconfigure(0, weight=1)

        log_hdr = T(ctk.CTkFrame(lf, fg_color="transparent"), fg_color="panel")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 4))
        log_hdr.grid_columnconfigure(0, weight=1)
        T(ctk.CTkLabel(log_hdr, text="LOG",
                       font=ctk.CTkFont("Segoe UI", 10, "bold"),
                       text_color=p["muted2"]),
          text_color="muted2").grid(row=0, column=0, sticky="w")
        T(ctk.CTkButton(log_hdr, text="Clear", height=24, width=52,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._clear_log),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=1)

        import tkinter as tk
        self._log_txt = tk.Text(
            lf, font=("Consolas", 9), wrap="word",
            bg=p["log_bg"], fg=p["log_fg"],
            relief="flat", bd=0,
            insertbackground=p["fg"],
            selectbackground="#1e3a5f",
            state="disabled",
        )
        self._log_txt.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 4))

        sb2 = tk.Scrollbar(lf, orient="vertical", command=self._log_txt.yview,
                           bg="#0a0d13", troughcolor="#0a0d13",
                           activebackground="#334155", width=10)
        sb2.grid(row=1, column=1, sticky="ns")
        self._log_txt.configure(yscrollcommand=sb2.set)

        ctk.CTkLabel(lf, text="Log written to: cyoa_downloader.log in output folder",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#334155").grid(row=2, column=0, sticky="w",
                                                padx=14, pady=(0, 6))

        # Color tags
        self._log_txt.tag_configure("INFO",    foreground="#475569")
        self._log_txt.tag_configure("WARNING", foreground="#f59e0b")
        self._log_txt.tag_configure("ERROR",   foreground="#ef4444")
        self._log_txt.tag_configure("SUCCESS", foreground="#22c55e")
        self._log_txt.tag_configure("AUTO",    foreground="#a78bfa")

    # ════════════════════════════════════════════════════════════════
    # SIDEBAR MODE SELECTION
    # ════════════════════════════════════════════════════════════════
    def _select_mode(self, val: str, update_ui: bool = True) -> None:
        self._mode_var = val
        if not update_ui or not hasattr(self, "_mode_btns"):
            return
        p = self._p()
        for v, (row, bar, icon_box, name_lbl, desc_lbl) in self._mode_btns.items():
            is_sel = (v == val)
            bg     = p["sel_row"] if is_sel else "transparent"
            try:
                row.configure(fg_color=bg)
                bar.configure(fg_color=p["sel_bar"] if is_sel else "transparent")
                icon_box.configure(
                    fg_color=p["sel_icon"] if is_sel else "transparent",
                    text_color=p["sel_nm"]  if is_sel else p["muted"],
                )
                name_lbl.master.configure(fg_color=bg)
                name_lbl.configure(fg_color=bg, text_color=p["sel_nm"]   if is_sel else p["fg"])
                desc_lbl.configure(fg_color=bg, text_color=p["sel_desc"] if is_sel else p["muted"])
            except Exception:
                pass
        self._update_mode_info(val)

    # ════════════════════════════════════════════════════════════════
    # THEME TOGGLE
    # ════════════════════════════════════════════════════════════════
    def _update_mode_info(self, val: str) -> None:
        """Update sidebar info box to describe current mode and expected output."""
        INFO_EN = {
            "auto": ("Auto (detect)", "Probes the URL and selects the best mode: cyoap_vue → project.json → website fallback.", "Output: depends on probe\n(embed / website_zip / cyoap_vue_zip)"),
            "embed": ("Embedded JSON", "Downloads project.json, encodes images as base64, and saves one standalone JSON file.", "Output: ProjectName.json"),
            "zip": ("ZIP", "Downloads project.json and stores images/audio as separate files inside a ZIP.", "Output: ProjectName.zip"),
            "both": ("Both", "Creates embedded JSON and ZIP in one run.", "Output: ProjectName.json + ProjectName.zip"),
            "website_zip": ("Website ZIP", "Downloads the full viewer with HTML/CSS/JS plus image, audio, and font assets.", "Output: ProjectName_site.zip\n→ index.html, images/, js/, css/"),
            "website_folder": ("Website Folder", "Same as Website ZIP, but keeps the folder uncompressed.", "Output: ProjectName_site/\n→ index.html, images/, js/, css/"),
            "pure_website_zip": ("Pure Website ZIP", "Downloads viewer HTML/CSS/JS without project.json search. Use this for custom CYOA formats.", "Output: ProjectName_site.zip\n(no project search)"),
            "pure_website_folder": ("Pure Website Folder", "Same as Pure Website ZIP, but keeps the folder uncompressed.", "Output: ProjectName_site/\n(no project search)"),
            "cyoap_vue_zip": ("cyoap_vue ZIP", "Dedicated mode for the cyoap_vue engine. Downloads dist/platform.json, dist/nodes, and assets.", "Output: ProjectName_site.zip\n→ dist/platform.json, dist/nodes/"),
            "cyoap_vue_folder": ("cyoap_vue Folder", "Same as cyoap_vue ZIP, but keeps the folder uncompressed.", "Output: ProjectName_site/\n→ dist/platform.json, dist/nodes/"),
        }
        INFO_ID = {
            "auto": ("Auto (deteksi)", "Mendeteksi URL dan memilih mode terbaik: cyoap_vue → project.json → fallback website.", "Output: tergantung hasil deteksi\n(embed / website_zip / cyoap_vue_zip)"),
            "embed": ("JSON Tertanam", "Mengunduh project.json, mengubah gambar menjadi base64, lalu menyimpan satu file JSON.", "Output: NamaProject.json"),
            "zip": ("ZIP", "Mengunduh project.json dan menyimpan gambar/audio sebagai file terpisah di dalam ZIP.", "Output: NamaProject.zip"),
            "both": ("Keduanya", "Membuat JSON tertanam dan ZIP sekaligus.", "Output: NamaProject.json + NamaProject.zip"),
            "website_zip": ("ZIP Website", "Mengunduh viewer lengkap beserta HTML/CSS/JS, gambar, audio, dan font.", "Output: NamaProject_site.zip\n→ index.html, images/, js/, css/"),
            "website_folder": ("Folder Website", "Sama seperti ZIP Website, tetapi folder tidak dikompresi.", "Output: NamaProject_site/\n→ index.html, images/, js/, css/"),
            "pure_website_zip": ("ZIP Pure Website", "Mengunduh HTML/CSS/JS viewer tanpa mencari project.json. Cocok untuk format CYOA custom.", "Output: NamaProject_site.zip\n(tanpa pencarian project)"),
            "pure_website_folder": ("Folder Pure Website", "Sama seperti ZIP Pure Website, tetapi folder tidak dikompresi.", "Output: NamaProject_site/\n(tanpa pencarian project)"),
            "cyoap_vue_zip": ("ZIP cyoap_vue", "Mode khusus engine cyoap_vue. Mengunduh dist/platform.json, dist/nodes, dan asset.", "Output: NamaProject_site.zip\n→ dist/platform.json, dist/nodes/"),
            "cyoap_vue_folder": ("Folder cyoap_vue", "Sama seperti ZIP cyoap_vue, tetapi folder tidak dikompresi.", "Output: NamaProject_site/\n→ dist/platform.json, dist/nodes/"),
        }
        INFO = INFO_EN if getattr(self, "_language", "id") == "en" else INFO_ID
        title, body, output = INFO.get(val, (val, "", ""))
        if hasattr(self, "_info_title"):
            try:
                p = self._p()
                self._info_title.configure(text=title)
                self._info_body.configure(text=body, text_color=p["muted"])
                self._info_output.configure(text=output)
                self._info_box.configure(fg_color=p["surface2"])
            except Exception:
                pass



    def _on_cloudflare_mode_change(self, value: str, validate: bool = True) -> None:
        """Apply Cloudflare mode from the compact selector."""
        mode = _normalize_cloudflare_mode(value)
        st = _load_settings()
        _set_cloudflare_config(
            mode,
            flaresolverr_url=st.get("flaresolverr_url", _FLARESOLVERR_URL),
            session_policy=st.get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY),
            timeout=int(st.get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT) or _FLARESOLVERR_TIMEOUT),
            wait_after=int(st.get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER) or _FLARESOLVERR_WAIT_AFTER),
            proxy_mode=st.get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE),
            persist=True,
        )
        try:
            self._cf_mode_var.set(_display_cloudflare_mode(mode))
        except Exception:
            pass
        if validate and mode == "cloudscraper":
            try:
                import cloudscraper  # noqa
                logger.info("[Cloudflare] cloudscraper available and active")
            except ImportError:
                from tkinter import messagebox
                if getattr(self, "_language", "id") == "en":
                    title = "cloudscraper not installed"
                    body = (
                        "Install it first:\n\n  pip install cloudscraper\n\n"
                        "or choose Auto/FlareSolverr if FlareSolverr is already running."
                    )
                else:
                    title = "cloudscraper belum terpasang"
                    body = (
                        "Instal terlebih dahulu:\n\n  pip install cloudscraper\n\n"
                        "atau pilih Auto/FlareSolverr jika FlareSolverr sudah berjalan."
                    )
                messagebox.showwarning(title, body)
        elif validate and mode == "flaresolverr":
            logger.info(f"[Cloudflare] FlareSolverr mode selected: {_FLARESOLVERR_URL}")

    # Backward-compatible alias for old callbacks.
    def _on_cf_bypass_toggle(self) -> None:
        self._on_cloudflare_mode_change("cloudscraper")

    def _on_http2_toggle(self) -> None:
        enabled = bool(self._http2_var.get())
        _set_http2_enabled(enabled)
        if enabled and not _HTTP2_ENABLED:
            self._http2_var.set(False)
            from tkinter import messagebox
            if getattr(self, "_language", "id") == "en":
                title = "httpx not installed"
                body = "Install it first:\n\n  pip install httpx[h2]\n\nthen restart the program and re-enable HTTP/2."
            else:
                title = "httpx belum terpasang"
                body = "Instal terlebih dahulu:\n\n  pip install httpx[h2]\n\nlalu mulai ulang program dan aktifkan kembali HTTP/2."
            messagebox.showwarning(title, body)
        st = _load_settings(); st["http2_enabled"] = bool(self._http2_var.get()); _save_settings(st)

    def _on_ytdlp_toggle(self) -> None:
        if self._ytdlp_var.get():
            try:
                import yt_dlp
                logger.info("YT Audio: yt-dlp available, YouTube audio will be downloaded automatically")
            except ImportError:
                from tkinter import messagebox
                self._ytdlp_var.set(False)
                if getattr(self, "_language", "id") == "en":
                    title = "yt-dlp not installed"
                    body = (
                        "Install it first:\n\n  pip install yt-dlp\n\n"
                        "ffmpeg is also required for MP3 conversion:\n"
                        "  https://ffmpeg.org/download.html\n\n"
                        "then restart the program and re-enable YT Audio."
                    )
                else:
                    title = "yt-dlp belum terpasang"
                    body = (
                        "Instal terlebih dahulu:\n\n  pip install yt-dlp\n\n"
                        "ffmpeg juga diperlukan untuk konversi MP3:\n"
                        "  https://ffmpeg.org/download.html\n\n"
                        "lalu mulai ulang program dan aktifkan kembali YT Audio."
                    )
                messagebox.showwarning(title, body)

    def _toggle_theme(self, val: str) -> None:
        self._is_dark = (val == "Dark")
        self._apply_theme()

    def _toggle_language(self, val: str) -> None:
        """Switch GUI microcopy between Indonesian and English."""
        self._language = "en" if str(val).upper().startswith("EN") else "id"
        st = _load_settings(); st["language"] = self._language; _save_settings(st)
        self._apply_language()
        logger.info(f"GUI language set: {self._language}")

    def _tr(self, key: str) -> str:
        texts = {
            "download_all": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "browse": {"id": "Browse…", "en": "Browse…"},
            "output_folder": {"id": "Folder output:", "en": "Output folder:"},
            "import_list": {"id": "Import List…", "en": "Import List…"},
            "queue_empty_title": {"id": "Queue kosong", "en": "Queue Empty"},
            "queue_empty_body": {"id": "Tambahkan minimal satu URL.", "en": "Add at least one URL."},
            "downloading": {"id": "Mengunduh…", "en": "Downloading…"},
            "idle": {"id": "Siap", "en": "Idle"},
        }
        lang = getattr(self, "_language", "id")
        return texts.get(key, {}).get(lang, texts.get(key, {}).get("en", key))

    def _translation_pairs(self) -> Dict[str, Dict[str, str]]:
        """Exact GUI text translation map. Keys are the English canonical text."""
        return {
            "Input": {"id": "Input", "en": "Input"},
            "URL": {"id": "URL", "en": "URL"},
            "Filename": {"id": "Nama file", "en": "Filename"},
            "Tambah +": {"id": "Tambah +", "en": "Add +"},
            "Threads:": {"id": "Thread:", "en": "Threads:"},
            "Retry (s):": {"id": "Retry (d):", "en": "Retry (s):"},
            "BW (KB/s):": {"id": "BW (KB/d):", "en": "BW (KB/s):"},
            "Proxy:": {"id": "Proxy:", "en": "Proxy:"},
            "DNS:": {"id": "DNS:", "en": "DNS:"},
            "Import List…": {"id": "Import List…", "en": "Import List…"},
            "Clear All": {"id": "Bersihkan", "en": "Clear All"},
            "Remove": {"id": "Hapus", "en": "Remove"},
            "▶  Download All": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "▶  Download Semua": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "🔍 Preview": {"id": "🔍 Pratinjau", "en": "🔍 Preview"},
            "⚡ Serve": {"id": "⚡ Server", "en": "⚡ Serve"},
            "📁 Open Folder": {"id": "📁 Buka Folder", "en": "📁 Open Folder"},
            "Viewers": {"id": "Viewer", "en": "Viewers"},
            "Batch Export": {"id": "Ekspor Batch", "en": "Batch Export"},
            "Results": {"id": "Hasil", "en": "Results"},
            "Panduan": {"id": "Panduan", "en": "Guide"},
            "Guide": {"id": "Panduan", "en": "Guide"},
            "Retry Failed": {"id": "Ulang Gagal", "en": "Retry Failed"},
            "Retry Images": {"id": "Ulang Gambar", "en": "Retry Images"},
            "Retry YT Audio": {"id": "Ulang Audio YT", "en": "Retry YT Audio"},
            "Pause": {"id": "Jeda", "en": "Pause"},
            "Resume": {"id": "Lanjut", "en": "Resume"},
            "Cache": {"id": "Cache", "en": "Cache"},
            "Updates": {"id": "Update", "en": "Updates"},
            "Batch Check": {"id": "Cek Batch", "en": "Batch Check"},
            "CM Import": {"id": "Import CM", "en": "CM Import"},
            "LOG": {"id": "LOG", "en": "LOG"},
            "Clear": {"id": "Bersihkan", "en": "Clear"},
            "Log written to: cyoa_downloader.log in output folder": {
                "id": "Log ditulis ke: cyoa_downloader.log di folder output",
                "en": "Log written to: cyoa_downloader.log in output folder"
            },
            "OUTPUT MODE": {"id": "MODE OUTPUT", "en": "OUTPUT MODE"},
            "WEBSITE MODE": {"id": "MODE WEBSITE", "en": "WEBSITE MODE"},
            "PURE WEBSITE": {"id": "PURE WEBSITE", "en": "PURE WEBSITE"},
            "Auto (detect)": {"id": "Auto (deteksi)", "en": "Auto (detect)"},
            "Default: Website Folder": {"id": "Default: Folder Website", "en": "Default: Website Folder"},
            "Embedded JSON": {"id": "JSON Tertanam", "en": "Embedded JSON"},
            "Gambar di-base64 dalam JSON": {"id": "Gambar base64 di JSON", "en": "Images base64 in JSON"},
            "ZIP": {"id": "ZIP", "en": "ZIP"},
            "JSON + gambar terpisah": {"id": "JSON + gambar terpisah", "en": "JSON + separate images"},
            "Both": {"id": "Keduanya", "en": "Both"},
            "Embed + ZIP sekaligus": {"id": "Embed + ZIP sekaligus", "en": "Embed + ZIP together"},
            "Website ZIP": {"id": "ZIP Website", "en": "Website ZIP"},
            "Viewer + all assets": {"id": "Viewer + semua asset", "en": "Viewer + all assets"},
            "Website Folder": {"id": "Folder Website", "en": "Website Folder"},
            "Pure Website ZIP": {"id": "ZIP Pure Website", "en": "Pure Website ZIP"},
            "Viewer only, no project search": {"id": "Viewer saja, tanpa pencarian project", "en": "Viewer only, no project search"},
            "Pure Website Folder": {"id": "Folder Pure Website", "en": "Pure Website Folder"},
            "cyoap_vue ZIP": {"id": "ZIP cyoap_vue", "en": "cyoap_vue ZIP"},
            "Engine khusus cyoap_vue": {"id": "Engine khusus cyoap_vue", "en": "cyoap_vue special engine"},
            "cyoap_vue Folder": {"id": "Folder cyoap_vue", "en": "cyoap_vue Folder"},
            "Idle": {"id": "Siap", "en": "Idle"},
            "Output folder:": {"id": "Folder output:", "en": "Output folder:"},
            "📤 CYOA Mgr  ": {"id": "📤 CYOA Mgr  ", "en": "📤 CYOA Mgr  "},
            "🤖 AI  ": {"id": "🤖 AI  ", "en": "🤖 AI  "},
            "Probing URLs sebelum download dimulai…": {"id": "Mengecek URL sebelum download dimulai…", "en": "Probing URLs before download starts…"},
            "▶ Proceed with Download": {"id": "▶ Lanjutkan Download", "en": "▶ Proceed with Download"},
            "⏸ Pause": {"id": "⏸ Jeda", "en": "⏸ Pause"},
            "📦  Batch Export → CYOA Manager": {"id": "📦  Ekspor Batch → CYOA Manager", "en": "📦  Batch Export → CYOA Manager"},
            "📤 Export to CYOA Manager": {"id": "📤 Ekspor ke CYOA Manager", "en": "📤 Export to CYOA Manager"},
            "📤  CYOA Manager Integration": {"id": "📤  Integrasi CYOA Manager", "en": "📤  CYOA Manager Integration"},
            "Click 📂 to browse…": {"id": "Klik 📂 untuk memilih…", "en": "Click 📂 to browse…"},
            "📄 Tambah project.json": {"id": "📄 Tambah project.json", "en": "📄 Add project.json"},
            "📋 Add All (session ini)": {"id": "📋 Tambahkan Semua (sesi ini)", "en": "📋 Add All (this session)"},
            "📁 Batch Export Folder": {"id": "📁 Folder Ekspor Batch", "en": "📁 Batch Export Folder"},
            "Manual — tambahkan project.json:": {"id": "Manual — tambahkan project.json:", "en": "Manual — add project.json:"},
            "📄 Pilih project.json": {"id": "📄 Pilih project.json", "en": "📄 Select project.json"},
            "📋 Add All from Last Session": {"id": "📋 Tambahkan Semua dari Sesi Terakhir", "en": "📋 Add All from Last Session"},
            "▶ Resume": {"id": "▶ Lanjut", "en": "▶ Resume"},
            "Cache speeds up re-downloading the same images.\n": {"id": "Cache mempercepat download ulang gambar yang sama.\n", "en": "Cache speeds up re-downloading the same images.\n"},
            "🗑  Clear Image Cache": {"id": "🗑  Bersihkan Cache Gambar", "en": "🗑  Clear Image Cache"},
            "🔍 Search by name…": {"id": "🔍 Cari berdasarkan nama…", "en": "🔍 Search by name…"},
            "📥 Queue Selected": {"id": "📥 Masukkan yang Dipilih ke Queue", "en": "📥 Queue Selected"},
            "AI Assist — Claude API Integration": {"id": "AI Assist — Integrasi Claude API", "en": "AI Assist — Claude API Integration"},
            "💾 Save": {"id": "💾 Simpan", "en": "💾 Save"},
            "Checking…": {"id": "Mengecek…", "en": "Checking…"},
            "All CYOAs are still up-to-date ✅": {"id": "Semua CYOA masih terbaru ✅", "en": "All CYOAs are still up-to-date ✅"},
            "CYOA Downloader — Panduan Fitur": {"id": "CYOA Downloader — Panduan Fitur", "en": "CYOA Downloader — Feature Guide"},
            "Panduan Fitur — CYOA Downloader v7.3.3": {"id": "Panduan Fitur — CYOA Downloader v7.3.3", "en": "Feature Guide — CYOA Downloader v7.3.3"},
            "Offline Viewers": {"id": "Viewer Offline", "en": "Offline Viewers"},
            "+ Add ZIP": {"id": "+ Tambah ZIP", "en": "+ Add ZIP"},
            "Refresh": {"id": "Muat ulang", "en": "Refresh"},
            "Remove selected": {"id": "Hapus yang dipilih", "en": "Remove selected"},
            "No offline viewers registered.": {"id": "Belum ada viewer offline terdaftar.", "en": "No offline viewers registered."},
            "Cloudflare Bypass": {"id": "Bypass Cloudflare", "en": "Cloudflare Bypass"},
            "Cloudflare:": {"id": "Cloudflare:", "en": "Cloudflare:"},
            "Cloudflare": {"id": "Cloudflare", "en": "Cloudflare"},
            "Cloudflare Access": {"id": "Akses Cloudflare", "en": "Cloudflare Access"},
            "Test Connection": {"id": "Tes Koneksi", "en": "Test Connection"},
            "Clear Sessions": {"id": "Bersihkan Session", "en": "Clear Sessions"},
            "Recommended setup": {"id": "Pengaturan yang disarankan", "en": "Recommended setup"},
            "Network": {"id": "Jaringan", "en": "Network"},
            "Advanced": {"id": "Lanjutan", "en": "Advanced"},
            "Settings": {"id": "Pengaturan", "en": "Settings"},
            "Test": {"id": "Tes", "en": "Test"},
            "Enabled": {"id": "Aktif", "en": "Enabled"},
            "Disabled": {"id": "Nonaktif", "en": "Disabled"},
            "Auto": {"id": "Auto", "en": "Auto"},
            "Save": {"id": "Simpan", "en": "Save"},
            "Cancel": {"id": "Batal", "en": "Cancel"},
            "Close": {"id": "Tutup", "en": "Close"},
            "Open": {"id": "Buka", "en": "Open"},
            "Status": {"id": "Status", "en": "Status"},
            "Folder": {"id": "Folder", "en": "Folder"},
            "Mode": {"id": "Mode", "en": "Mode"},
            "Source URL": {"id": "URL sumber", "en": "Source URL"},
            "Output": {"id": "Output", "en": "Output"},
            "Start": {"id": "Mulai", "en": "Start"},
            "Stop": {"id": "Berhenti", "en": "Stop"},
            "Download": {"id": "Download", "en": "Download"},
            "Done": {"id": "Selesai", "en": "Done"},
            "Failed": {"id": "Gagal", "en": "Failed"},
        }

    def _translate_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        lang = getattr(self, "_language", "id")
        pairs = self._translation_pairs()
        if text in pairs:
            return pairs[text].get(lang, text)
        # Translate known phrases inside longer labels such as window titles.
        for canonical, vals in pairs.items():
            source_vals = set(vals.values()) | {canonical}
            for src in source_vals:
                if src and src in text:
                    return text.replace(src, vals.get(lang, src))
        # Preserve icons/prefixes in tool-strip buttons.
        for canonical, vals in pairs.items():
            for v in vals.values():
                if text.endswith("  " + v):
                    return text[:-(len(v))] + vals.get(lang, v)
        return text

    def _translate_widget_tree(self, widget) -> None:
        try:
            text = widget.cget("text")
            new_text = self._translate_text(text)
            if new_text != text:
                widget.configure(text=new_text)
        except Exception:
            pass
        try:
            placeholder = widget.cget("placeholder_text")
            if placeholder == "(opsional)":
                widget.configure(placeholder_text="(optional)" if self._language == "en" else "(opsional)")
            elif placeholder == "(optional)":
                widget.configure(placeholder_text="(optional)" if self._language == "en" else "(opsional)")
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                self._translate_widget_tree(child)
        except Exception:
            pass

    def _apply_language(self) -> None:
        """Apply Indonesian/English GUI text without rebuilding the UI."""
        try:
            if hasattr(self, "_lang_pill"):
                self._lang_pill.set("EN" if self._language == "en" else "ID")
            self._translate_widget_tree(self.root)
            if hasattr(self, "_dl_btn"):
                self._dl_btn.configure(text=self._tr("download_all"))
            if hasattr(self, "_browse_button"):
                self._browse_button.configure(text=self._tr("browse"))
            if hasattr(self, "_output_label"):
                self._output_label.configure(text=self._tr("output_folder"))
            if hasattr(self, "_import_button"):
                self._import_button.configure(text=self._tr("import_list"))
            # Re-apply sidebar mode names/descriptions from canonical definitions.
            mode_texts = {
                "auto": ("Auto (detect)", "Default: Website Folder"),
                "embed": ("Embedded JSON", "Gambar di-base64 dalam JSON"),
                "zip": ("ZIP", "JSON + gambar terpisah"),
                "both": ("Both", "Embed + ZIP sekaligus"),
                "website_zip": ("Website ZIP", "Viewer + all assets"),
                "website_folder": ("Website Folder", "Viewer + all assets"),
                "pure_website_zip": ("Pure Website ZIP", "Viewer only, no project search"),
                "pure_website_folder": ("Pure Website Folder", "Viewer only, no project search"),
                "cyoap_vue_zip": ("cyoap_vue ZIP", "Engine khusus cyoap_vue"),
                "cyoap_vue_folder": ("cyoap_vue Folder", "Engine khusus cyoap_vue"),
            }
            for val, (_row, _bar, _icon, name_lbl, desc_lbl) in getattr(self, "_mode_btns", {}).items():
                if val in mode_texts:
                    n, d = mode_texts[val]
                    name_lbl.configure(text=self._translate_text(n))
                    desc_lbl.configure(text=self._translate_text(d))
            if hasattr(self, "_sec_labels"):
                for lbl in self._sec_labels:
                    try: lbl.configure(text=self._translate_text(lbl.cget("text")))
                    except Exception: pass
            self._update_mode_info(getattr(self, "_mode_var", "auto"))
        except Exception as e:
            logger.debug(f"Language apply failed: {e}")

    # ════════════════════════════════════════════════════════════════
    # CLOUDFLARE / FLARESOLVERR PANEL
    # ════════════════════════════════════════════════════════════════
    def _cloudflare_panel(self) -> None:
        """Modern Cloudflare settings panel: Off/Auto/cloudscraper/FlareSolverr."""
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        st = _load_settings()

        win = ctk.CTkToplevel(self.root)
        win.title("Cloudflare Access")
        win.geometry("640x560")
        win.minsize(600, 520)
        win.grab_set()

        root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="☁", width=38, height=38,
                     font=ctk.CTkFont("Segoe UI Emoji", 20),
                     fg_color=p["surface2"], text_color=p["accent"],
                     corner_radius=10).grid(row=0, column=0, padx=(16, 10), pady=14)
        ctk.CTkLabel(header, text="Cloudflare Access",
                     font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="w", pady=(12, 0))
        ctk.CTkLabel(header, text="Normal request → cloudscraper → FlareSolverr fallback",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 12))

        body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        root.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        def card(title: str, subtitle: str = ""):
            frame = ctk.CTkFrame(body, fg_color=p["panel"], border_color=p["border"], border_width=1, corner_radius=12)
            frame.pack(fill="x", pady=(0, 12))
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=p["fg"], anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 0))
            if subtitle:
                ctk.CTkLabel(frame, text=subtitle, font=ctk.CTkFont("Segoe UI", 10),
                             text_color=p["muted"], anchor="w", wraplength=560, justify="left").grid(
                    row=1, column=0, columnspan=3, sticky="ew", padx=14, pady=(1, 10))
            return frame

        access = card("Mode", "Auto is recommended. FlareSolverr is used only when the page really shows a Cloudflare challenge.")
        mode_var = ctk.StringVar(value=_display_cloudflare_mode(st.get("cloudflare_mode", _CLOUDFLARE_MODE)))
        ctk.CTkLabel(access, text="Cloudflare mode", text_color=p["muted"], anchor="w").grid(row=2, column=0, padx=14, pady=8, sticky="w")
        mode_menu = ctk.CTkOptionMenu(access, variable=mode_var,
                                      values=["Off", "Auto", "cloudscraper", "FlareSolverr"],
                                      width=180, fg_color=p["surface2"], button_color=p["surface"],
                                      text_color=p["fg"], dropdown_fg_color=p["surface"],
                                      dropdown_text_color=p["fg"])
        mode_menu.grid(row=2, column=1, sticky="w", padx=8, pady=8)

        fs = card("FlareSolverr", "Run flaresolverr.exe or Docker first, then use the API endpoint below. Default: http://localhost:8191/v1")
        url_var = ctk.StringVar(value=st.get("flaresolverr_url", _FLARESOLVERR_URL))
        sess_var = ctk.StringVar(value=st.get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY))
        timeout_var = ctk.StringVar(value=str(st.get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT)))
        wait_var = ctk.StringVar(value=str(st.get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER)))
        proxy_var = ctk.StringVar(value=st.get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE))

        def label(row, text):
            ctk.CTkLabel(fs, text=text, text_color=p["muted"], anchor="w").grid(row=row, column=0, padx=14, pady=6, sticky="w")
        label(2, "API URL")
        ctk.CTkEntry(fs, textvariable=url_var, height=32, fg_color=p["input_bg"], border_color=p["border"],
                     text_color=p["input_fg"], font=ctk.CTkFont("Consolas", 10)).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 14), pady=6)
        label(3, "Session")
        ctk.CTkOptionMenu(fs, variable=sess_var, values=["temporary", "reuse-domain", "manual"],
                          width=150, fg_color=p["surface2"], button_color=p["surface"], text_color=p["fg"]).grid(row=3, column=1, sticky="w", padx=8, pady=6)
        label(4, "Timeout")
        ctk.CTkEntry(fs, textvariable=timeout_var, width=80, height=30, justify="center",
                     fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"]).grid(row=4, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(fs, text="seconds", text_color=p["muted"]).grid(row=4, column=1, sticky="w", padx=(95, 0), pady=6)
        label(5, "Wait after solve")
        ctk.CTkEntry(fs, textvariable=wait_var, width=80, height=30, justify="center",
                     fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"]).grid(row=5, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(fs, text="seconds", text_color=p["muted"]).grid(row=5, column=1, sticky="w", padx=(95, 0), pady=6)
        label(6, "Proxy")
        ctk.CTkOptionMenu(fs, variable=proxy_var, values=["inherit", "none"],
                          width=150, fg_color=p["surface2"], button_color=p["surface"], text_color=p["fg"]).grid(row=6, column=1, sticky="w", padx=8, pady=6)

        status_var = ctk.StringVar(value="Status: not tested")
        status = ctk.CTkLabel(fs, textvariable=status_var, text_color=p["muted"], anchor="w")
        status.grid(row=7, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 2))

        def apply_settings(persist=True):
            try:
                timeout_s = int(timeout_var.get() or 60)
            except Exception:
                timeout_s = 60
            try:
                wait_s = int(wait_var.get() or 3)
            except Exception:
                wait_s = 3
            _set_cloudflare_config(
                mode_var.get(),
                flaresolverr_url=url_var.get(),
                session_policy=sess_var.get(),
                timeout=timeout_s,
                wait_after=wait_s,
                proxy_mode=proxy_var.get(),
                persist=persist,
            )
            try:
                self._cf_mode_var.set(_display_cloudflare_mode(_CLOUDFLARE_MODE))
            except Exception:
                pass

        def do_test():
            apply_settings(True)
            status_var.set("Status: testing FlareSolverr…")
            def worker():
                ok, msg = flaresolverr_test_connection()
                win.after(0, lambda: status_var.set(("Status: ✓ " if ok else "Status: ✗ ") + msg))
            threading.Thread(target=worker, daemon=True).start()

        def do_clear():
            apply_settings(True)
            def worker():
                n = flaresolverr_destroy_sessions()
                win.after(0, lambda: status_var.set(f"Status: cleared {n} session(s)"))
            threading.Thread(target=worker, daemon=True).start()

        actions = ctk.CTkFrame(fs, fg_color="transparent")
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 14))
        ctk.CTkButton(actions, text="Test Connection", fg_color="#3b82f6", hover_color="#2563eb",
                      command=do_test).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Clear Sessions", fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], command=do_clear).pack(side="left")

        info = card("Recommended setup", "Windows: run flaresolverr.exe, keep the terminal open, then test http://localhost:8191/v1 here. Use Auto mode for normal downloads.")
        ctk.CTkLabel(info, text="Recommended defaults: Auto · reuse-domain · timeout 60s · proxy inherit",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=p["accent"], anchor="w").grid(
            row=2, column=0, sticky="ew", padx=14, pady=(0, 14))

        footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        def save_close():
            apply_settings(True)
            messagebox.showinfo("Cloudflare", f"Saved: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
            win.destroy()
        ctk.CTkButton(footer, text="Save", width=90, fg_color="#3b82f6", hover_color="#2563eb",
                      command=save_close).grid(row=0, column=1, padx=(6, 8), pady=12)
        ctk.CTkButton(footer, text="Close", width=90, fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], command=win.destroy).grid(row=0, column=2, padx=(0, 14), pady=12)

    # ════════════════════════════════════════════════════════════════
    # QUEUE
    # ════════════════════════════════════════════════════════════════
    def _badge_colors(self, mode: str) -> tuple:
        return self.BADGE_COLORS.get(mode, ("#1e3a5f", "#60a5fa"))

    def _make_queue_row(self, url: str, mode: str, filename: str) -> None:
        import customtkinter as ctk
        import tkinter as tk
        idx = len(self._queue_rows)
        p   = self._p()

        row = ctk.CTkFrame(self._qlist, corner_radius=6,
                           fg_color=p["surface"],
                           border_width=1, border_color=p["border"])
        row.pack(fill="x", padx=4, pady=3)
        row.grid_columnconfigure(2, weight=1)

        # ── Drag handle ─────────────────────────────────────────────
        drag_lbl = ctk.CTkLabel(row, text="⠿", width=18,
                                font=ctk.CTkFont("Segoe UI", 14),
                                text_color=p["muted2"],
                                cursor="fleur")
        drag_lbl.grid(row=0, column=0, padx=(6, 2), pady=8, rowspan=2)

        # Status dot
        dot = tk.Canvas(row, width=10, height=10,
                        highlightthickness=0, bg=p["surface"])
        dot.create_oval(2, 2, 8, 8, fill=p["muted2"], outline="")
        dot.grid(row=0, column=1, padx=(2, 6), pady=8)

        # URL + editable filename
        url_lbl = ctk.CTkLabel(row, text=url,
                               font=ctk.CTkFont("Consolas", 9),
                               text_color=p["muted"], anchor="w")
        url_lbl.grid(row=0, column=2, sticky="ew", padx=4, pady=(6, 1))

        fn_var = ctk.StringVar(value=filename)
        fn_entry = ctk.CTkEntry(
            row, textvariable=fn_var, height=22,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["input_bg"], text_color=p["input_fg"],
            border_color=p["border"], border_width=1,
            placeholder_text="auto",
        )
        fn_entry.grid(row=1, column=2, sticky="ew", padx=4, pady=(0, 6))

        def _on_fn_change(*_):
            if idx < len(self._queue_data):
                self._queue_data[idx]["filename"] = fn_var.get().strip()
        fn_var.trace_add("write", _on_fn_change)

        # Badge
        bg, fg = self._badge_colors(mode)
        badge = ctk.CTkLabel(row,
                             text=mode.replace("_", " "),
                             font=ctk.CTkFont("Segoe UI", 9, "bold"),
                             fg_color=bg, text_color=fg,
                             corner_radius=10, padx=8, pady=2)
        badge.grid(row=0, column=3, padx=6, rowspan=2)

        # × button
        rm = ctk.CTkButton(row, text="×", width=28, height=28,
                           font=ctk.CTkFont("Segoe UI", 13),
                           fg_color="transparent",
                           hover_color=p["surface2"],
                           text_color=p["muted"],
                           command=lambda i=idx: self._remove_row(i))
        rm.grid(row=0, column=4, padx=(0, 6), rowspan=2)

        self._queue_rows.append((row, dot, url_lbl, badge, rm))

        # ── Drag-to-reorder bindings ─────────────────────────────────
        self._bind_drag(drag_lbl, row)

        self._update_queue_count()

    def _bind_drag(self, handle, row_frame) -> None:
        """Attach drag-reorder behaviour to the ⠿ handle of a queue row."""
        state = {"start_y": 0, "dragging": False, "ghost": None}

        def _row_index(w) -> int:
            """Return current index of row_frame in _queue_rows."""
            for i, (r, *_) in enumerate(self._queue_rows):
                if r is w:
                    return i
            return -1

        def _on_press(e):
            state["start_y"] = e.y_root
            state["dragging"] = False
            row_frame.configure(border_color="#3b82f6")

        def _on_drag(e):
            state["dragging"] = True
            dy = e.y_root - state["start_y"]
            if abs(dy) < 8:
                return
            # Find which row we're hovering over
            my_idx = _row_index(row_frame)
            if my_idx < 0:
                return
            rows = self._queue_rows
            for j, (r, *_) in enumerate(rows):
                ry = r.winfo_rooty()
                rh = r.winfo_height()
                if r is not row_frame and ry <= e.y_root <= ry + rh:
                    # Swap
                    if j != my_idx:
                        self._swap_rows(my_idx, j)
                        state["start_y"] = e.y_root
                    break

        def _on_release(e):
            row_frame.configure(border_color=self._p()["border"])
            state["dragging"] = False

        handle.bind("<ButtonPress-1>", _on_press)
        handle.bind("<B1-Motion>",     _on_drag)
        handle.bind("<ButtonRelease-1>", _on_release)

    def _swap_rows(self, i: int, j: int) -> None:
        """Swap two queue rows (data + visual)."""
        if i < 0 or j < 0 or i >= len(self._queue_rows) or j >= len(self._queue_rows):
            return
        # Swap data
        self._queue_data[i], self._queue_data[j] = \
            self._queue_data[j], self._queue_data[i]
        # Swap visual: re-pack in new order
        row_i, *_ = self._queue_rows[i]
        row_j, *_ = self._queue_rows[j]
        # Forget pack info then re-pack in swapped order
        all_rows = [(r, *rest) for r, *rest in self._queue_rows]
        all_rows[i], all_rows[j] = all_rows[j], all_rows[i]
        self._queue_rows = all_rows
        # Re-pack all rows in correct order
        for r, *_ in self._queue_rows:
            r.pack_forget()
        for r, *_ in self._queue_rows:
            r.pack(fill="x", padx=4, pady=3)
        # Update × button commands
        for k, (*_, rm) in enumerate(self._queue_rows):
            rm.configure(command=lambda k=k: self._remove_row(k))

    def _remove_row(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._queue_rows):
            return
        self._queue_rows[idx][0].destroy()
        self._queue_rows.pop(idx)
        del self._queue_data[idx]
        for i, (_, _, _, _, rm) in enumerate(self._queue_rows):
            rm.configure(command=lambda i=i: self._remove_row(i))
        self._update_queue_count()

    def _update_queue_count(self) -> None:
        n = len(self._queue_rows)
        self._queue_count_var.set(f"QUEUE — {n} ITEM{'S' if n != 1 else ''}")

    def _add_to_queue(self) -> None:
        url = self._url_var.get().strip()
        if not url:
            return
        # Dedup: skip if exact URL already in queue
        if any(it["url"] == url for it in self._queue_data):
            logger.warning(f"URL sudah ada di queue: {url[:60]}")
            self._url_var.set("")
            return
        # History: auto-suffix filename if previously downloaded
        prev = _check_history(url)
        if prev:
            date  = prev.get("last_downloaded", "")[:10]
            fname = prev.get("file_name", "")
            logger.info(
                f"⚠ URL pernah didownload ({date})"
                + (f" → {fname}" if fname else "")
                + " — filename diberi suffix _N"
            )
        fn   = self._fn_var.get().strip()
        # Auto-suffix: if URL was previously downloaded, append _1, _2, ...
        if prev and not fn:
            # Generate suffix-ed filename based on previous filename
            base_fn = prev.get("file_name", "") or ""
            if base_fn:
                # strip existing _N suffix from prev name
                base_fn = re.sub(r'_\d+$', '', base_fn)
                suffix = 1
                fn = f"{base_fn}_{suffix}"
                # increment until unique
                existing = {it.get("filename","") for it in self._queue_data}
                while fn in existing:
                    suffix += 1
                    fn = f"{base_fn}_{suffix}"
        mode = self._mode_var
        self._queue_data.append({"url": url, "filename": fn, "mode": mode})
        self._make_queue_row(url, mode, fn)
        self._url_var.set("")
        self._fn_var.set("")

    def _remove(self) -> None:
        if self._queue_rows:
            self._remove_row(len(self._queue_rows) - 1)

    def _clear_queue(self) -> None:
        for rw in self._queue_rows:
            rw[0].destroy()
        self._queue_rows.clear()
        self._queue_data.clear()
        self._update_queue_count()

    # ════════════════════════════════════════════════════════════════
    # LOG
    # ════════════════════════════════════════════════════════════════
    def _setup_logging(self) -> None:
        # Remove existing GUILogHandler to prevent duplicate entries on re-init
        for h in logger.handlers[:]:
            if isinstance(h, GUILogHandler):
                logger.removeHandler(h)
        h = GUILogHandler(self._log_queue)
        h.setFormatter(_formatter)
        logger.addHandler(h)

    def _poll_log(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._log_txt.configure(state="normal")
                if " - WARNING - " in msg:   tag = "WARNING"
                elif " - ERROR - " in msg:   tag = "ERROR"
                elif any(w in msg for w in ("successful", "complete", "Done")):
                    tag = "SUCCESS"
                elif "[Auto-detect]" in msg:  tag = "AUTO"
                else:                         tag = "INFO"
                self._log_txt.insert("end", msg + "\n", tag)
                self._log_txt.see("end")
                self._log_txt.configure(state="disabled")
        except log_queue_module.Empty:
            pass
        self.root.after(100, self._poll_log)

    def _clear_log(self) -> None:
        self._log_txt.configure(state="normal")
        self._log_txt.delete("1.0", "end")
        self._log_txt.configure(state="disabled")

    # ════════════════════════════════════════════════════════════════
    # ACTIONS
    # ════════════════════════════════════════════════════════════════
    def _browse(self) -> None:
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self._outdir_var.get())
        if d:
            self._outdir_var.set(d)

    def _open_folder(self) -> None:
        import subprocess, platform
        folder = self._outdir_var.get()
        if not os.path.isdir(folder):
            return
        sys_name = platform.system()
        if sys_name == "Windows":   os.startfile(folder)
        elif sys_name == "Darwin":  subprocess.Popen(["open", folder])
        else:                       subprocess.Popen(["xdg-open", folder])

    def _import_list(self) -> None:
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            filetypes=[("Supported", "*.txt *.csv *.xlsx *.xls"),
                       ("All files", "*.*")])
        if not path:
            return
        items = import_queue_items_from_source(path)
        if not items:
            messagebox.showwarning("Import Failed", "No valid URLs found.")
            return
        default_mode = self._mode_var
        for item in items:
            mode = item.get("mode", "").strip() or default_mode
            item["mode"] = mode
            self._queue_data.append({"url": item["url"],
                                     "filename": item.get("filename", ""),
                                     "mode": mode})
            self._make_queue_row(item["url"], mode, item.get("filename", ""))
        logger.info(f"Imported {len(items)} item(s) from {path}")

    def _show_format_guide(self) -> None:
        import customtkinter as ctk
        win = ctk.CTkToplevel(self.root)
        win.title("Batch File Format Guide")
        win.resizable(False, False)
        content = (
            "── Excel / CSV columns ─────────────────────────────────\n\n"
            "  url      (required)   Full URL starting with http(s)://\n"
            "  filename (optional)   Output filename without extension\n"
            "  mode     (optional)   embed | zip | both | website_zip |\n"
            "                        website_folder | pure_website_zip |\n"
            "                        pure_website_folder | cyoap_vue_zip |\n"
            "                        cyoap_vue_folder | auto\n\n"
            "── TXT format (one per line) ───────────────────────────\n\n"
            "  https://example.com/cyoa/\n"
            "  https://example.com/cyoa2/ | MyFilename\n"
            "  https://example.com/cyoa3/ | Name | website_zip\n\n"
            "── Notes ───────────────────────────────────────────────\n\n"
            "  • Column names are case-insensitive.\n"
            "  • Rows without valid URL are skipped silently.\n"
            "  • If mode is empty, GUI mode selection is used.\n"
            "  • 'auto' mode probes URL before downloading.\n"
        )
        import tkinter as tk
        txt = tk.Text(win, width=56, height=22, font=("Consolas", 10),
                      bg="#0a0d13", fg="#94a3b8", relief="flat",
                      padx=12, pady=10, state="normal", wrap="none")
        txt.insert("1.0", content)
        txt.configure(state="disabled")
        txt.pack(padx=10, pady=10)
        ctk.CTkButton(win, text="Tutup", command=win.destroy,
                      width=80).pack(pady=(0, 10))

    def _start(self) -> None:
        from tkinter import messagebox
        if self._is_running:
            return
        if not self._queue_data:
            messagebox.showwarning(self._tr("queue_empty_title"), self._tr("queue_empty_body"))
            return
        self._is_running = True
        self._dl_btn.configure(state="disabled")
        self._pb.start()
        self._start_speed_graph()
        self._status_var.set(self._tr("downloading"))
        threading.Thread(
            target=self._worker,
            args=(list(self._queue_data),
                  self._mode_var,
                  int(self._wait_var.get() or DEFAULT_WAIT_TIME),
                  int(self._threads_var.get() or DEFAULT_MAX_WORKERS),
                  self._outdir_var.get(),
                  self._fonts_var.get(),
                  self._analyse_var.get(),
                  _normalize_cloudflare_mode(self._cf_mode_var.get()),
                  self._http2_var.get(),
                  self._ytdlp_var.get(),
                  float(self._bw_var.get() or 0),
                  self._cyoa_mgr_var.get()),
            daemon=True,
        ).start()

    def _preview_queue(self) -> None:
        """Feature 2: Probe all URLs in queue and show estimated outcomes before download."""
        import customtkinter as ctk
        if self._is_running:
            return
        if not self._queue_data:
            from tkinter import messagebox
            messagebox.showwarning(self._tr("queue_empty_title"), self._tr("queue_empty_body"))
            return

        p    = self._p()
        items = list(self._queue_data)

        win = ctk.CTkToplevel(self.root)
        win.title("Pre-flight Check")
        win.geometry("760x480")
        win.grab_set()

        ctk.CTkLabel(win, text="Pre-flight Check",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(win, text="Probing URLs sebelum download dimulai…",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=p["muted"]).pack(anchor="w", padx=16, pady=(0, 10))

        prog = ctk.CTkProgressBar(win, mode="determinate", height=5)
        prog.pack(fill="x", padx=16, pady=(0, 8))
        prog.set(0)

        status_lbl = ctk.CTkLabel(win, text="",
                                   font=ctk.CTkFont("Segoe UI", 10),
                                   text_color=p["muted"])
        status_lbl.pack(anchor="w", padx=16, pady=(0, 8))

        results_frame = ctk.CTkScrollableFrame(win, fg_color=p["bg"],
                                                scrollbar_button_color=p["surface2"])
        results_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 12))

        proceed_btn = ctk.CTkButton(btn_frame, text="▶ Proceed with Download",
                                     fg_color="#3b82f6", hover_color="#2563eb",
                                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                     command=lambda: (win.destroy(), self._start()),
                                     state="disabled")
        proceed_btn.pack(side="left")
        ctk.CTkButton(btn_frame, text="Tutup", width=80,
                       fg_color=p["surface2"], text_color=p["muted"],
                       command=win.destroy).pack(side="left", padx=8)

        summary_var = ctk.StringVar(value="")
        ctk.CTkLabel(btn_frame, textvariable=summary_var,
                      font=ctk.CTkFont("Segoe UI", 11),
                      text_color=p["muted"]).pack(side="right")

        row_widgets: List = []

        def _add_result_row(idx, url, status_text, status_color, detail=""):
            bg = p["surface"] if idx % 2 == 0 else p["bg"]
            row = ctk.CTkFrame(results_frame, fg_color=bg, corner_radius=4)
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkLabel(row, text=status_text, width=90,
                          font=ctk.CTkFont("Segoe UI", 10, "bold"),
                          text_color=status_color, anchor="w").pack(side="left", padx=(8, 4), pady=6)
            ctk.CTkLabel(row, text=url[:65] + ("…" if len(url) > 65 else ""),
                          font=ctk.CTkFont("Consolas", 9),
                          text_color=p["muted"], anchor="w").pack(side="left", fill="x", expand=True)
            if detail:
                ctk.CTkLabel(row, text=detail,
                              font=ctk.CTkFont("Segoe UI", 9),
                              text_color=p["muted2"], anchor="e").pack(side="right", padx=8)
            row_widgets.append(row)

        def _probe_worker():
            ok = warn = fail = 0
            for i, item in enumerate(items):
                url = item["url"]
                win.after(0, lambda u=url, i=i: status_lbl.configure(
                    text=f"[{i+1}/{len(items)}] Probing: {u[:50]}…"))
                win.after(0, lambda v=(i+1)/len(items): prog.set(v))

                # Quick HEAD check of project candidates
                try:
                    candidates = build_default_project_candidates(url)
                    live = _parallel_head_check(candidates[:12], max_workers=6, timeout=5)
                    if live:
                        detail = f"project.json: {len(live)} candidate"
                        color  = "#22c55e"
                        label  = "✓ FOUND"
                        ok += 1
                    else:
                        # Try page fetch through the shared HTTP pipeline so proxy, DNS,
                        # Cloudflare mode, and FlareSolverr settings are respected.
                        try:
                            rp = fetch_response(url, timeout=6, extra_headers={"User-Agent": "Mozilla/5.0"})
                            if rp is not None and rp.status_code < 400:
                                detail = "Page OK — might need JS scan"
                                color  = "#f59e0b"
                                label  = "⚠ JS/SCAN"
                                warn += 1
                            else:
                                detail = "No reachable page"
                                color  = "#ef4444"
                                label  = "✗ ERROR"
                                fail += 1
                        except Exception as e:
                            detail = str(e)[:40]
                            color  = "#ef4444"
                            label  = "✗ OFFLINE"
                            fail += 1
                except Exception as e:
                    detail = str(e)[:40]
                    color  = "#ef4444"
                    label  = "✗ ERROR"
                    fail += 1

                win.after(0, lambda i=i, u=url, lbl=label, c=color, d=detail:
                          _add_result_row(i, u, lbl, c, d))

            win.after(0, lambda: status_lbl.configure(text="Probe selesai."))
            win.after(0, lambda: prog.set(1.0))
            win.after(0, lambda: proceed_btn.configure(state="normal"))
            win.after(0, lambda: summary_var.set(
                f"✓ {ok}  ⚠ {warn}  ✗ {fail}  dari {len(items)} URL"))

        threading.Thread(target=_probe_worker, daemon=True).start()



    def _set_dot(self, idx: int, state: str) -> None:
        """Update status dot in queue row. state: 'idle'|'running'|'done'|'error'|'skip'"""
        if idx >= len(self._queue_rows):
            return
        _, dot, _, _, _ = self._queue_rows[idx]
        colors = {"idle": self._p()["muted2"], "running": "#3b82f6",
                  "done": "#22c55e", "error": "#ef4444", "skip": "#f59e0b"}
        color = colors.get(state, self._p()["muted2"])

        def _update():
            try:
                # Guard: widget may have been destroyed if user removed the row
                if not dot.winfo_exists():
                    return
                dot.delete("all")
                dot.create_oval(2, 2, 8, 8, fill=color, outline="")
            except Exception:
                pass
        self.root.after(0, _update)

    def _worker(self, items, default_mode, wt, threads, outdir, dl_fonts, show_analysis, cloudflare_mode, http2_enabled, ytdlp_enabled, bw_limit, cyoa_mgr) -> None:
        _self_mod = sys.modules.get(__name__)
        if _self_mod is not None:
            _self_mod._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            _self_mod._gui_speed_cb = self._record_speed_bytes
        global wait_time, use_cloudscraper, _shared_session, _shared_session_cf, _ytdlp_enabled, _bandwidth_limit_kbps
        _ytdlp_enabled        = ytdlp_enabled
        _bandwidth_limit_kbps = bw_limit
        wait_time        = wt
        # Apply Cloudflare engine selection for this worker run.
        _set_cloudflare_config(
            cloudflare_mode,
            flaresolverr_url=_load_settings().get("flaresolverr_url", _FLARESOLVERR_URL),
            session_policy=_load_settings().get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY),
            timeout=int(_load_settings().get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT) or _FLARESOLVERR_TIMEOUT),
            wait_after=int(_load_settings().get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER) or _FLARESOLVERR_WAIT_AFTER),
            proxy_mode=_load_settings().get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE),
            persist=True,
        )
        logger.info(f"[Cloudflare] Mode: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
        _set_http2_enabled(bool(http2_enabled))
        setup_file_logging(outdir)

        # ── Resume state ───────────────────────────────────────────
        state       = load_resume_state(outdir)
        completed   = set(state["completed"])
        prev_failed = set(f["url"] if isinstance(f, dict) else f for f in state["failed"])

        skipped_count = sum(1 for it in items if it["url"] in completed)
        if skipped_count:
            logger.info(f"[Resume] Melanjutkan dari sesi sebelumnya — {skipped_count} URL sudah selesai, di-skip")

        # Mark already-completed items in the queue dots
        for idx, item in enumerate(items):
            if item["url"] in completed:
                self._set_dot(idx, "done")
            elif item["url"] in prev_failed:
                self._set_dot(idx, "error")

        ok = 0
        failed_items: List[Dict[str, str]] = []
        completed_urls: List[str] = list(completed)
        self._last_results: List[Dict] = []   # populated for Results popup

        # ── Auto-detect phase ──────────────────────────────────────
        auto_items = [it for it in items
                      if it.get("mode", default_mode) == "auto"
                      and it["url"] not in completed]
        if auto_items:
            self._set_status(f"Auto-detecting mode for {len(auto_items)} URL(s)…")
            logger.info(f"[Auto-detect] Starting probe for {len(auto_items)} URL(s)…")

            def _progress(done, total):
                self._set_status(f"Auto-detecting… {done}/{total}")

            auto_detect_modes_batch(auto_items, max_workers=min(4, threads),
                                    progress_cb=_progress)
            for i, it in enumerate(items):
                if it.get("auto_detected") and i < len(self._queue_rows):
                    _, _, _, badge_lbl, _ = self._queue_rows[i]
                    bg, fg = self._badge_colors(it["mode"])
                    self.root.after(0, lambda b=badge_lbl, bg=bg, fg=fg,
                                   m=it["mode"]: b.configure(
                                       text=m.replace("_", " ") + " *",
                                       fg_color=bg, text_color=fg))

        # ── Download phase ─────────────────────────────────────────
        pending = [it for it in items if it["url"] not in completed]
        total   = len(items)

        for i, item in enumerate(pending):
            real_idx = items.index(item)   # index in original list for dot update
            url = item["url"]

            # ── Pause gate ──────────────────────────────────────────
            if not self._paused.is_set():
                self._set_status(f"⏸ Paused — {i}/{len(pending)} done")
            self._paused.wait()   # blocks until unpaused

            # Check if download was cancelled while paused
            if not self._is_running:
                break

            # Skip already completed
            if url in completed:
                ok += 1
                self._set_dot(real_idx, "done")
                continue

            mode = item.get("mode", "").strip() or default_mode
            if mode == "auto":
                self._set_status(f"[{i+1}/{len(pending)}] Auto-detecting…")
                mode = auto_detect_mode(url)
                item["mode"] = mode
                logger.info(f"[Auto-detect] {url} → {mode}")

            self._set_status(f"[{i+1}/{len(pending)}] [{mode}] {url[:45]}…")
            self._set_dot(real_idx, "running")

            try:
                is_pure  = mode in {"pure_website_zip", "pure_website_folder"}
                is_cyoap = mode in {"cyoap_vue_zip", "cyoap_vue_folder"}
                run_download(
                    url=url,
                    file_name=item.get("filename", ""),
                    zip_output=(mode == "zip"),
                    both_output=(mode == "both"),
                    website_output=(mode in {"website", "website_zip", "website_folder",
                                             "cyoap_vue_zip", "cyoap_vue_folder"}),
                    website_zip_output=(mode not in {"website_folder", "cyoap_vue_folder",
                                                     "pure_website_folder"}),
                    pure_website=is_pure,
                    download_fonts=dl_fonts,
                    show_font_analysis=show_analysis,
                    output_dir=outdir,
                    max_workers=threads,
                    engine_mode="cyoap_vue" if is_cyoap else "standard",
                    cyoa_mgr_enabled=cyoa_mgr,
                    ai_api_key=_resolve_ai_api_key(session_key=self._ai_api_key, storage=getattr(self, "_ai_key_storage", "session"), provider=getattr(self, "_ai_provider", "anthropic")) if self._ai_enabled and _normalize_ai_mode(getattr(self, "_ai_mode", "auto_fallback")) != "off" else "",
                    ai_provider=getattr(self, "_ai_provider", "anthropic"),
                    ai_mode=getattr(self, "_ai_mode", "auto_fallback"),
                )
                ok += 1
                completed_urls.append(url)
                self._last_results.append({
                    "url": url, "mode": mode, "status": "OK",
                    "filename": item.get("filename", ""), "error": ""
                })
                self._set_dot(real_idx, "done")
                _record_history(url, item.get("filename", ""), mode, success=True)
                # Save state after every success so resume works mid-batch
                save_resume_state(outdir, completed_urls,
                                  [f["url"] for f in failed_items])

            except Exception as e:
                logger.error(f"Failed [{url}]: {e}")
                failed_items.append({"url": url, "error": str(e)})
                self._last_results.append({
                    "url": url, "mode": mode, "status": "FAIL",
                    "filename": item.get("filename", ""), "error": str(e)
                })
                self._set_dot(real_idx, "error")
                _record_history(url, item.get("filename", ""), mode, success=False)
                save_resume_state(outdir, completed_urls,
                                  [f["url"] for f in failed_items])

        write_failed_url_log(failed_items, outdir)
        total_done = ok + skipped_count
        self._set_status(f"Done — {total_done}/{total} succeeded")

        # Clear resume state only on full success
        if len(failed_items) == 0:
            clear_resume_state(outdir)
            logger.info("[Resume] All items succeeded — state cleared")
        else:
            logger.info(f"[Resume] {len(failed_items)} item gagal — state disimpan untuk resume")

        # Show results popup after batch (>1 item)
        if total > 1:
            self.root.after(0, self._show_results)
        self.root.after(0, self._done)


    def _set_status(self, msg: str) -> None:
        self.root.after(0, lambda: self._status_var.set(msg))

    def _retry_youtube_audio(self) -> None:
        """Re-download YouTube audio from skipped_youtube_audio.txt."""
        import glob as _glob
        p = self._p()
        out = self._outdir_var.get()
        skip_files = _glob.glob(os.path.join(out, "**", "skipped_youtube_audio.txt"),
                                recursive=True)
        if not skip_files:
            self._set_status("No skipped_youtube_audio.txt found.")
            return

        urls: List[str] = []
        for f in skip_files:
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line and line.startswith("http") and line not in urls:
                            urls.append(line)
            except Exception:
                pass

        if not urls:
            self._set_status("No YouTube URLs to retry.")
            return

        audio_dir = os.path.join(out, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        self._set_status(f"Retry {len(urls)} YouTube audio…")

        import threading as _thr

        def _do_retry():
            _mod = sys.modules.get(__name__)
            if _mod is not None:
                _mod._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            result = _download_youtube_audio(urls, audio_dir, log_dir=out)
            if _mod is not None:
                _mod._ytdlp_gui_progress_cb = None
            ok = len(result)
            self.root.after(0, lambda: self._set_status(
                f"Retry YT audio selesai: {ok}/{len(urls)} berhasil"))

        _thr.Thread(target=_do_retry, daemon=True).start()

    def _on_ytdlp_progress(self, vid_id: str, idx: int, total: int,
                           pct: str, speed: str) -> None:
        """Called by yt-dlp progress hook → update status label."""
        msg = f"🎵 [{idx}/{total}] {vid_id[:12]}… {pct} @ {speed}"
        self.root.after(0, lambda m=msg: self._set_status(m))

    def _retry_failed_images(self) -> None:
        """
        Read failed_images.txt from output folder, re-download each image,
        and patch the corresponding project JSON(s) in the same folder.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox

        outdir = self._outdir_var.get() or os.getcwd()
        fail_log = os.path.join(outdir, "failed_images.txt")

        if not os.path.exists(fail_log):
            messagebox.showinfo("Retry Failed Images",
                                f"failed_images.txt tidak ditemukan di:\n{outdir}")
            return

        # Parse failed_images.txt — lines without # are: URL\terror
        failed_urls: List[str] = []
        with open(fail_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    url = line.split("\t")[0].strip()
                    if url.startswith("http"):
                        failed_urls.append(url)

        if not failed_urls:
            messagebox.showinfo("Retry Failed Images",
                                "No failed image URLs found in failed_images.txt")
            return

        # Find all JSON files in output folder to patch
        json_files = [
            os.path.join(outdir, f)
            for f in os.listdir(outdir)
            if f.endswith(".json") and not f.endswith("_metadata.json")
               and f != "download_state.json" and f != "download_history.json"
        ]

        if not json_files:
            messagebox.showinfo("Retry Failed Images",
                                f"Tidak ada .json project ditemukan di:\n{outdir}")
            return

        if self._is_running:
            messagebox.showwarning("Retry Failed Images",
                                   "Please wait for the current download to finish.")
            return

        logger.info(f"[Retry Images] {len(failed_urls)} gambar gagal, "
                    f"{len(json_files)} project JSON ditemukan.")

        def _do_retry():
            import base64, mimetypes
            headers = {"User-Agent": "Mozilla/5.0"}
            patched_total = 0

            for json_path in json_files:
                try:
                    project_str = open(json_path, encoding="utf-8",
                                       errors="replace").read()
                except Exception as e:
                    logger.warning(f"  Cannot read {json_path}: {e}")
                    continue

                changed = False
                for url in failed_urls:
                    if url not in project_str:
                        continue
                    # Try to download
                    try:
                        r = fetch_response(url, extra_headers=headers, timeout=30)
                        if r is None:
                            raise RuntimeError("download failed")
                        r.raise_for_status()
                        mime  = r.headers.get("Content-Type", "").split(";")[0].strip() \
                                or "image/webp"
                        b64   = base64.b64encode(r.content).decode()
                        new_  = f"data:{mime};base64,{b64}"
                        project_str = project_str.replace(
                            f'"{url}"', f'"{new_}"'
                        )
                        logger.info(f"  ✓ Re-embedded: {os.path.basename(url)}")
                        changed = True
                        patched_total += 1
                    except Exception as e:
                        logger.warning(f"  ✗ Still failing: {url[:60]} — {e}")

                if changed:
                    try:
                        with open(json_path, "w", encoding="utf-8") as fout:
                            fout.write(project_str)
                        logger.info(f"  Updated: {os.path.basename(json_path)}")
                    except Exception as e:
                        logger.error(f"  Write failed for {json_path}: {e}")

            if patched_total:
                logger.info(f"[Retry Images] Done — {patched_total} gambar berhasil di-embed.")
            else:
                logger.warning(f"[Retry Images] No images were successfully downloaded.")

        import threading
        threading.Thread(target=_do_retry, daemon=True).start()

    def _retry_failed(self) -> None:
        """Re-queue all failed items so they can be re-downloaded."""
        if self._is_running:
            return
        if not self._last_results:
            from tkinter import messagebox
            messagebox.showinfo("Retry Failed", "No results yet. Run a download first.")
            return
        failed_urls = {r["url"] for r in self._last_results if r["status"] != "OK"}
        if not failed_urls:
            from tkinter import messagebox
            messagebox.showinfo("Retry Failed", "No failed items found.")
            return
        # Remove failed URLs from resume state so they get retried
        try:
            outdir = self._outdir_var.get()
            state  = load_resume_state(outdir)
            state["completed"] = [u for u in state["completed"] if u not in failed_urls]
            save_resume_state(outdir, state["completed"], [])
        except Exception:
            pass
        # Reset dot for failed items
        for i, item in enumerate(self._queue_data):
            if item["url"] in failed_urls:
                self._set_dot(i, "idle")
        # Clear failed from results so they show as fresh
        for r in self._last_results:
            if r["url"] in failed_urls:
                r["status"] = "PENDING"
        logger.info(f"[Retry] {len(failed_urls)} item gagal di-reset — memulai ulang download")
        self._start()

    def _done(self) -> None:
        self._is_running = False
        self._paused.set()   # ensure unpaused for next run
        self._pause_btn.configure(text="⏸ Pause")
        self._pb.stop()
        self._stop_speed_graph()
        self._dl_btn.configure(state="normal")
        # Desktop notification
        status = self._status_var.get()
        _send_desktop_notification("CYOA Downloader", status)
        # Only clear queue on full success — keep failed items visible
        try:
            succeeded = int(status.split("—")[1].strip().split("/")[0].strip())
            total     = int(status.split("/")[1].strip().split(" ")[0])
            if succeeded < total:
                logger.info(f"[Queue] {total - succeeded} item gagal — queue tidak di-clear")
                return
        except Exception:
            pass
        self._clear_queue()

    def _show_results(self) -> None:
        """Show popup with per-item download results table."""
        import customtkinter as ctk
        import tkinter as tk

        if not self._last_results:
            from tkinter import messagebox
            messagebox.showinfo("Results", "No results yet. Run a download first.")
            return

        p   = self._p()
        win = ctk.CTkToplevel(self.root)
        win.title("Batch Download Results")
        win.geometry("900x520")
        win.grab_set()

        # Header stats
        total   = len(self._last_results)
        ok_cnt  = sum(1 for r in self._last_results if r["status"] == "OK")
        fail_cnt= total - ok_cnt

        hdr = ctk.CTkFrame(win, corner_radius=0, fg_color=p["panel"])
        hdr.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(hdr, text="Hasil Download Batch",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(side="left", padx=16, pady=12)

        stats_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        stats_frame.pack(side="right", padx=16, pady=8)

        for label, val, color in [
            (f"Total: {total}", "", p["muted"]),
            (f"✓ {ok_cnt} berhasil", "", "#22c55e"),
            (f"✗ {fail_cnt} gagal", "", "#ef4444"),
        ]:
            ctk.CTkLabel(stats_frame, text=label,
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=color).pack(side="left", padx=8)

        # Separator
        ctk.CTkFrame(win, height=1, fg_color=p["border"], corner_radius=0).pack(fill="x")

        # Filter buttons
        filter_frame = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        filter_frame.pack(fill="x", padx=0)
        self._result_filter = ctk.StringVar(value="all")

        def set_filter(v):
            self._result_filter.set(v)
            refresh_table()

        for label, val in [("All", "all"), ("Success ✓", "ok"), ("Failed ✗", "fail")]:
            ctk.CTkButton(filter_frame, text=label, height=28,
                          font=ctk.CTkFont("Segoe UI", 10),
                          fg_color="#3b82f6" if val == "all" else p["surface2"],
                          hover_color="#2563eb",
                          text_color="#fff" if val == "all" else p["muted"],
                          command=lambda v=val: set_filter(v)).pack(
                side="left", padx=(8 if label == "Semua" else 4, 0), pady=6)

        # Export button
        def export_csv():
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                initialfile="download_results.csv")
            if not path:
                return
            import csv as csv_mod
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.DictWriter(f, fieldnames=["status","url","mode","filename","error"])
                w.writeheader()
                w.writerows(self._last_results)
            logger.info(f"Results exported: {path}")

        ctk.CTkButton(filter_frame, text="Export CSV", height=28,
                      font=ctk.CTkFont("Segoe UI", 10),
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], border_width=1,
                      border_color=p["surface2"],
                      command=export_csv).pack(side="right", padx=8, pady=6)

        # Table
        tbl_frame = ctk.CTkScrollableFrame(
            win, corner_radius=0, fg_color=p["bg"],
            scrollbar_button_color=p["surface2"])
        tbl_frame.pack(fill="both", expand=True, padx=0, pady=0)
        tbl_frame.grid_columnconfigure(1, weight=1)
        tbl_frame.grid_columnconfigure(3, weight=1)

        # Column headers
        COL_W = [50, 350, 100, 200, 0]
        for ci, (htext, w) in enumerate(zip(
            ["Status", "URL", "Mode", "Filename", "Error"], COL_W)):
            ctk.CTkLabel(tbl_frame, text=htext,
                         font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=p["muted"], anchor="w",
                         width=w if w else 0).grid(
                row=0, column=ci, sticky="w", padx=(12 if ci == 0 else 4, 4), pady=(8, 4))

        ctk.CTkFrame(tbl_frame, height=1, fg_color=p["border"],
                     corner_radius=0).grid(row=1, column=0, columnspan=5,
                                           sticky="ew", padx=8, pady=0)

        row_widgets_table = []

        def refresh_table():
            for w in row_widgets_table:
                w.destroy()
            row_widgets_table.clear()
            flt = self._result_filter.get()
            rows = [r for r in self._last_results
                    if flt == "all"
                    or (flt == "ok"   and r["status"] == "OK")
                    or (flt == "fail" and r["status"] != "OK")]
            for ri, r in enumerate(rows):
                is_ok  = r["status"] == "OK"
                row_bg = p["bg"] if ri % 2 == 0 else p["surface"]

                def make_lbl(text, col, color=None, mono=False, anchor="w"):
                    lbl = ctk.CTkLabel(tbl_frame, text=text,
                                       font=ctk.CTkFont("Consolas" if mono else "Segoe UI", 9),
                                       text_color=color or p["fg"],
                                       fg_color=row_bg, anchor=anchor)
                    lbl.grid(row=ri+2, column=col, sticky="ew",
                             padx=(12 if col==0 else 4, 4), pady=2)
                    row_widgets_table.append(lbl)
                    return lbl

                make_lbl("✓" if is_ok else "✗", 0,
                         color="#22c55e" if is_ok else "#ef4444")
                make_lbl(r["url"], 1, mono=True)
                make_lbl(r["mode"].replace("_", " "), 2, color=p["muted"])
                make_lbl(r["filename"] or "[auto]", 3, color=p["muted"])
                if not is_ok:
                    make_lbl(r["error"][:80], 4, color="#f87171")
                else:
                    make_lbl("", 4)

        refresh_table()

    # ─ Local server ──────────────────────────────────────────────────────
    def _batch_export_panel(self) -> None:
        """
        Batch export multiple project.json files to CYOA Manager library.
        Supports: scan folder, pick individual files, from download history.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox
        import glob as _glob

        p   = self._p()
        s   = _load_settings()
        win = ctk.CTkToplevel(self.root)
        win.title("Batch Export → CYOA Manager")
        win.geometry("600x520")
        win.grab_set()
        win.resizable(False, False)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📦  Batch Export → CYOA Manager",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=p["fg"]).pack(side="left", padx=16)

        body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # ── Source selection ──────────────────────────────────────────
        src_lbl = ctk.CTkLabel(body, text="Sumber file:",
                               font=ctk.CTkFont("Segoe UI", 10, "bold"),
                               text_color=p["muted"])
        src_lbl.pack(anchor="w", pady=(0, 4))

        files_var  = ctk.Variable(value=[])   # list of paths
        count_var  = ctk.StringVar(value="0 file dipilih")
        status_var = ctk.StringVar()

        # File list display
        list_frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=8)
        list_frame.pack(fill="both", expand=True, pady=(0, 8))

        list_box = ctk.CTkTextbox(
            list_frame, height=220,
            font=ctk.CTkFont("Consolas", 9),
            fg_color=p["surface"], text_color=p["muted"],
            border_width=0,
        )
        list_box.pack(fill="both", expand=True, padx=6, pady=6)
        list_box.configure(state="disabled")

        _file_paths: List[str] = []

        def _refresh_list() -> None:
            list_box.configure(state="normal")
            list_box.delete("1.0", "end")
            for fp in _file_paths:
                list_box.insert("end", f"  {os.path.basename(fp)}\n")
                list_box.insert("end", f"    {fp}\n")
            list_box.configure(state="disabled")
            count_var.set(f"{len(_file_paths)} file dipilih")

        def _scan_folder() -> None:
            folder = filedialog.askdirectory(parent=win, title="Select output folder")
            if not folder:
                return
            found = _glob.glob(os.path.join(folder, "*.json")) + \
                    _glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)
            # Filter: only project-like JSON (not metadata, settings, etc.)
            skip = {"settings.json", "download_history.json",
                    "backup_report.txt", "viewers.json"}
            found = [f for f in found
                     if os.path.basename(f) not in skip
                     and os.path.getsize(f) > 1024]  # >1KB
            for fp in found:
                if fp not in _file_paths:
                    _file_paths.append(fp)
            _refresh_list()

        def _pick_files() -> None:
            paths = filedialog.askopenfilenames(
                parent=win, title="Pilih project.json",
                filetypes=[("JSON", "*.json"), ("All", "*.*")])
            for fp in paths:
                if fp not in _file_paths:
                    _file_paths.append(fp)
            _refresh_list()

        def _from_session() -> None:
            results  = getattr(self, "_last_results", [])
            outdir   = self._outdir_var.get() or os.getcwd()
            for r in results:
                if r.get("status") != "OK":
                    continue
                jp = os.path.join(outdir, r.get("filename", "") + ".json")
                if os.path.exists(jp) and jp not in _file_paths:
                    _file_paths.append(jp)
            _refresh_list()

        def _clear() -> None:
            _file_paths.clear()
            _refresh_list()

        # Source buttons
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))
        for text, cmd in [
            ("📂 Scan Folder",      _scan_folder),
            ("📄 Pick Files",       _pick_files),
            ("📋 From This Session", _from_session),
            ("🗑 Clear All",        _clear),
        ]:
            ctk.CTkButton(btn_row, text=text, height=30,
                          fg_color=p["surface2"], hover_color=p["surface"],
                          text_color=p["muted"],
                          font=ctk.CTkFont("Segoe UI", 10),
                          command=cmd).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(body, textvariable=count_var,
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["fg"]).pack(anchor="w")

        # ── Export button ─────────────────────────────────────────────
        prog_var = ctk.DoubleVar(value=0)
        prog_bar = ctk.CTkProgressBar(body, variable=prog_var, height=8,
                                       fg_color=p["surface2"],
                                       progress_color="#3b82f6")
        prog_bar.pack(fill="x", pady=(8, 4))

        ctk.CTkLabel(body, textvariable=status_var,
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["accent"]).pack(anchor="w")

        def _do_export() -> None:
            if not _file_paths:
                messagebox.showwarning("Empty", "Select files first.", parent=win)
                return
            custom = s.get("cyoa_mgr_db_path", "").strip()
            db = (custom if custom and os.path.exists(custom)
                  else _find_cyoa_manager_db())
            if not db:
                messagebox.showerror(
                    "DB Not Found",
                    "Open 📤 CYOA Mgr to set the library.sqlite3 path.",
                    parent=win)
                return
            added = skipped = failed = 0
            total = len(_file_paths)
            for i, fp in enumerate(_file_paths):
                prog_var.set((i + 1) / total)
                win.update_idletasks()
                name = os.path.splitext(os.path.basename(fp))[0]
                ok = add_to_cyoa_manager(fp, name=name, db_path=db)
                if ok is True:
                    added += 1
                elif ok is None:
                    skipped += 1
                else:
                    failed += 1
            prog_var.set(1.0)
            status_var.set(
                f"✓ {added} ditambahkan  •  "
                f"{skipped} sudah ada  •  "
                f"{failed} gagal"
            )

        ctk.CTkButton(body, text="📤 Export to CYOA Manager", height=38,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=_do_export).pack(fill="x", pady=(8, 0))

    def _cyoa_manager_panel(self) -> None:
        import customtkinter as ctk
        from tkinter import filedialog, messagebox

        p    = self._p()
        s    = _load_settings()
        win  = ctk.CTkToplevel(self.root)
        win.title("CYOA Manager")
        win.geometry("520x400")
        win.grab_set()
        win.resizable(False, False)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📤  CYOA Manager Integration",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=p["fg"]).pack(side="left", padx=16)

        body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        # ── ON/OFF toggle (big, obvious) ───────────────────────────────
        on_var = ctk.BooleanVar(value=self._cyoa_mgr_var.get())

        tog_frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=10)
        tog_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(tog_frame,
                     text="Auto-add to library after each download",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=p["muted"]).pack(side="left", padx=14, pady=12)

        def _apply_toggle(v: bool) -> None:
            on_var.set(v)
            self._cyoa_mgr_var.set(v)
            s["cyoa_mgr_enabled"] = v
            _save_settings(s)
            # Sync action bar button
            if hasattr(self, "_cm_btn") and self._cm_btn:
                p2 = self._p()
                self._cm_btn.configure(
                    text="📤 CYOA Mgr  " + ("✓" if v else "✗"),
                    text_color=p2["accent"] if v else p2["muted"],
                )
            _refresh_tog()

        def _refresh_tog() -> None:
            v = on_var.get()
            on_btn.configure(
                fg_color="#3b82f6" if v else p["surface2"],
                text_color="#ffffff" if v else p["muted"],
            )
            off_btn.configure(
                fg_color="#ef4444" if not v else p["surface2"],
                text_color="#ffffff" if not v else p["muted"],
            )

        btn_row = ctk.CTkFrame(tog_frame, fg_color="transparent")
        btn_row.pack(side="right", padx=10)
        on_btn  = ctk.CTkButton(btn_row, text="ON",  width=52, height=28,
                                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                corner_radius=6,
                                command=lambda: _apply_toggle(True))
        on_btn.pack(side="left", padx=(0, 4))
        off_btn = ctk.CTkButton(btn_row, text="OFF", width=52, height=28,
                                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                corner_radius=6,
                                command=lambda: _apply_toggle(False))
        off_btn.pack(side="left")
        _refresh_tog()

        # ── DB path ───────────────────────────────────────────────────
        db_frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=10)
        db_frame.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(db_frame, text="Library DB  (library.sqlite3)",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["muted"]).pack(anchor="w", padx=14, pady=(10, 4))

        db_var = ctk.StringVar(value=
            s.get("cyoa_mgr_db_path") or
            _find_cyoa_manager_db() or "")
        db_row = ctk.CTkFrame(db_frame, fg_color="transparent")
        db_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkEntry(db_row, textvariable=db_var, height=28,
                     font=ctk.CTkFont("Consolas", 9),
                     fg_color=p["input_bg"], text_color=p["input_fg"],
                     border_color=p["border"],
                     placeholder_text="Click 📂 to browse…",
                     ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        def _browse():
            path = filedialog.askopenfilename(
                parent=win, title="Select library.sqlite3",
                filetypes=[("SQLite DB", "*.sqlite3"), ("All", "*.*")])
            if path:
                db_var.set(path)
                s["cyoa_mgr_db_path"] = path
                _save_settings(s)
        ctk.CTkButton(db_row, text="📂", width=32, height=28,
                      command=_browse).pack(side="left")

        # ── Status ────────────────────────────────────────────────────
        status_var = ctk.StringVar()
        db_ok = bool(db_var.get() and os.path.exists(db_var.get()))
        status_var.set("✓ DB found" if db_ok else "⚠ DB not found")
        ctk.CTkLabel(body, textvariable=status_var,
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["accent"] if db_ok else "#f59e0b",
                     ).pack(anchor="w", pady=(0, 8))

        # ── Manual add ────────────────────────────────────────────────
        def _get_db():
            custom = s.get("cyoa_mgr_db_path", "").strip()
            return (custom if custom and os.path.exists(custom)
                    else _find_cyoa_manager_db())

        def _manual_add():
            json_path = filedialog.askopenfilename(
                parent=win, title="Pilih project.json",
                filetypes=[("JSON", "*.json"), ("All", "*.*")])
            if not json_path: return
            db = _get_db()
            if not db:
                messagebox.showerror("Error", "Library DB not found.", parent=win)
                return
            ok = add_to_cyoa_manager(
                json_path,
                name=os.path.splitext(os.path.basename(json_path))[0],
                db_path=db)
            status_var.set("✓ Ditambahkan" if ok else "✗ Gagal")

        def _add_all():
            results = getattr(self, "_last_results", [])
            db = _get_db()
            if not db:
                messagebox.showerror("Error", "Library DB is invalid.", parent=win)
                return
            added = sum(
                1 for r in results
                if r.get("status") == "OK" and
                add_to_cyoa_manager(
                    os.path.join(self._outdir_var.get() or os.getcwd(),
                                 r.get("filename","") + ".json"),
                    name=r.get("filename",""),
                    source_url=r.get("url",""),
                    db_path=db)
            )
            status_var.set(f"✓ {added} project ditambahkan.")

        def _batch_export():
            """Batch export: browse folder, find all project.json, add to library."""
            from tkinter import filedialog
            db = _get_db()
            if not db:
                messagebox.showerror("Error", "Library DB is invalid.", parent=win)
                return
            folder = filedialog.askdirectory(
                parent=win, title="Pilih folder yang berisi project.json")
            if not folder:
                return
            # Scan recursively for project.json files
            added = 0
            for root_d, dirs, files in os.walk(folder):
                # Skip audio/images/css/js subdirs
                dirs[:] = [d for d in dirs
                           if d.lower() not in {"audio","images","css","js","fonts"}]
                for fname in files:
                    if fname.lower() not in {"project.json", "project.txt"}:
                        continue
                    fp = os.path.join(root_d, fname)
                    name = os.path.basename(root_d)
                    if add_to_cyoa_manager(fp, name=name, db_path=db):
                        added += 1
            status_var.set(f"✓ {added} project.json ditemukan dan ditambahkan.")

        bf = ctk.CTkFrame(body, fg_color="transparent")
        bf.pack(fill="x")
        ctk.CTkButton(bf, text="📄 Tambah project.json", height=32,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=_manual_add).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="📋 Add All (session ini)", height=32,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=_add_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="📁 Batch Export Folder", height=32,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=_batch_export).pack(side="left")

        # ── Master toggle ──────────────────────────────────────────────
        toggle_var = ctk.BooleanVar(value=self._cyoa_mgr_var.get())
        trow = ctk.CTkFrame(win, fg_color=p["surface2"], corner_radius=8)
        trow.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(trow, text="Auto-add to library after download:",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=p["muted"]).pack(side="left", padx=10, pady=10)
        def _on_toggle():
            v = toggle_var.get()
            self._cyoa_mgr_var.set(v)
            s["cyoa_mgr_enabled"] = v
            _save_settings(s)
            # Sync action bar button
            if hasattr(self, '_cm_btn'):
                p2 = self._p()
                self._cm_btn.configure(
                    text="📤 CYOA Mgr  " + ("✓" if v else "✗"),
                    text_color=p2["accent"] if v else p2["muted"],
                )
        ctk.CTkSwitch(trow, variable=toggle_var, onvalue=True, offvalue=False,
                      text="", width=44, command=_on_toggle).pack(side="right", padx=10)

        # ── DB path ───────────────────────────────────────────────────
        db_frame = ctk.CTkFrame(win, fg_color=p["surface2"], corner_radius=8)
        db_frame.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(db_frame, text="Library DB:",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["muted"]).pack(anchor="w", padx=10, pady=(8, 2))

        db_var = ctk.StringVar(value=s.get("cyoa_mgr_db_path") or
                               _find_cyoa_manager_db() or
                               "Not found — click 📂")
        db_entry = ctk.CTkEntry(db_frame, textvariable=db_var, width=440,
                                font=ctk.CTkFont("Consolas", 9),
                                fg_color=p["input_bg"], text_color=p["input_fg"],
                                border_color=p["border"])
        db_entry.pack(side="left", padx=(10, 4), pady=(0, 10))
        def _browse_db():
            path = filedialog.askopenfilename(
                parent=win, title="Select library.sqlite3",
                filetypes=[("SQLite DB", "*.sqlite3"), ("All", "*.*")])
            if path:
                db_var.set(path)
                s["cyoa_mgr_db_path"] = path
                _save_settings(s)
        ctk.CTkButton(db_frame, text="📂", width=32,
                      command=_browse_db).pack(side="left", pady=(0, 10), padx=(0, 10))

        status_var = ctk.StringVar()
        ctk.CTkLabel(win, textvariable=status_var,
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["accent"]).pack(pady=4)

        # ── Manual add ────────────────────────────────────────────────
        ctk.CTkLabel(win, text="Manual — tambahkan project.json:",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["muted"]).pack(anchor="w", padx=16, pady=(4, 2))

        def _get_db():
            custom = s.get("cyoa_mgr_db_path", "").strip()
            return (custom if custom and os.path.exists(custom)
                    else _find_cyoa_manager_db())

        def _manual_add():
            json_path = filedialog.askopenfilename(
                parent=win, title="Pilih project.json",
                filetypes=[("JSON", "*.json"), ("All", "*.*")])
            if not json_path: return
            db = _get_db()
            if not db:
                messagebox.showerror("Error", "Library DB not found.", parent=win)
                return
            name = os.path.splitext(os.path.basename(json_path))[0]
            ok = add_to_cyoa_manager(json_path, name=name, db_path=db)
            status_var.set(f"{'✓ Ditambahkan' if ok else '✗ Gagal'}: {name}")

        def _add_all():
            results = getattr(self, "_last_results", [])
            db = _get_db()
            if not db:
                messagebox.showerror("Error", "Library DB is invalid.", parent=win)
                return
            added = 0
            for r in results:
                if r.get("status") != "OK": continue
                fn  = r.get("filename", "")
                src_url = r.get("url", "")
                outdir = self._outdir_var.get() or os.getcwd()
                jp = os.path.join(outdir, fn + ".json")
                if os.path.exists(jp):
                    if add_to_cyoa_manager(jp, name=fn, source_url=src_url, db_path=db):
                        added += 1
            status_var.set(f"✓ {added} project ditambahkan.")

        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(fill="x", padx=14)
        ctk.CTkButton(bf, text="📄 Pilih project.json", height=34,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=_manual_add).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="📋 Add All from Last Session", height=34,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      font=ctk.CTkFont("Segoe UI", 11),
                      command=_add_all).pack(side="left")

    def _show_cookie_guide(self) -> None:
        """Open Panduan feature guide on the Cookie tab."""
        self._show_feature_guide(initial_tab="cookie")

    # ── Pause / Resume ─────────────────────────────────────────────────
    def _toggle_pause(self) -> None:
        if not self._is_running:
            return
        if self._paused.is_set():
            self._paused.clear()   # pause
            self._pause_btn.configure(text="▶ Resume")
            logger.info("[Pause] Download paused by user")
        else:
            self._paused.set()     # resume
            self._pause_btn.configure(text="⏸ Pause")
            logger.info("[Pause] Download resumed")

    # ── Cache Manager ──────────────────────────────────────────────────
    def _cache_manager_panel(self) -> None:
        import customtkinter as ctk
        p = self._p()
        win = ctk.CTkToplevel(self.root)
        win.title("💾 Cache Manager")
        win.geometry("380x220")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        stats = _cache_stats()
        info_var = ctk.StringVar(
            value=f"Cached images: {stats['entries']}\n"
                  f"Disk usage: ~{stats['size_mb']} MB"
        )
        ctk.CTkLabel(win, textvariable=info_var,
                     font=ctk.CTkFont("Segoe UI", 13),
                     text_color=p["fg"], justify="left").pack(padx=20, pady=(20, 10))

        ctk.CTkLabel(win,
                     text="Cache speeds up re-downloading the same images.\n"
                          "Clear hanya jika perlu menghemat disk space.",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], wraplength=340,
                     justify="left").pack(padx=20, pady=(0, 14))

        def _do_clear():
            n = _clear_image_cache()
            info_var.set(f"Cache cleared — {n} file(s) removed.\n"
                         f"Cached images: 0\nDisk usage: 0 MB")

        ctk.CTkButton(win, text="🗑  Clear Image Cache", height=36,
                      fg_color=p["danger_bg"], hover_color=p["danger_hv"],
                      text_color=p["danger_fg"],
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=_do_clear).pack(padx=20, pady=(0, 10))

        ctk.CTkButton(win, text="Close", height=30,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      command=win.destroy).pack(padx=20, pady=(0, 14))

    # ── CYOA Manager Import (Infaera list download) ────────────────────
    def _import_from_cyoa_manager_panel(self) -> None:
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        projects = _list_cyoa_manager_projects()
        if not projects:
            messagebox.showinfo(
                "CYOA Manager Import",
                "CYOA Manager library not found or empty.\n"
                "Make sure CYOA Manager is installed and has projects in the library."
            )
            return

        win = ctk.CTkToplevel(self.root)
        win.title("📚 Import from CYOA Manager")
        win.geometry("620x480")
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        ctk.CTkLabel(
            win, text=f"CYOA Manager Library — {len(projects)} project(s) with URL",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=p["fg"]
        ).pack(padx=14, pady=(14, 6))

        # Search bar
        search_var = ctk.StringVar()
        ctk.CTkEntry(
            win, textvariable=search_var,
            placeholder_text="🔍 Search by name…",
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32
        ).pack(padx=14, fill="x", pady=(0, 6))

        lf = ctk.CTkScrollableFrame(win, fg_color=p["surface2"], corner_radius=8)
        lf.pack(padx=14, pady=(0, 8), fill="both", expand=True)

        check_vars: List[tuple] = []  # (BooleanVar, project_dict)

        def _rebuild(filter_text=""):
            for w in lf.winfo_children():
                w.destroy()
            check_vars.clear()
            ft = filter_text.lower()
            for proj in projects:
                name = proj.get("name", proj.get("id", ""))
                if ft and ft not in name.lower() and ft not in proj.get("source_url", "").lower():
                    continue
                var = ctk.BooleanVar(value=False)
                row = ctk.CTkFrame(lf, fg_color="transparent", corner_radius=0)
                row.pack(fill="x", padx=2, pady=1)
                ctk.CTkCheckBox(
                    row, text="", variable=var, width=24, height=24,
                    fg_color=p["accentbg"], hover_color=p["accentbg_hv"],
                    border_color=p["border"], checkmark_color=p["accent"],
                ).pack(side="left", padx=(4, 6), pady=2)
                tf = ctk.CTkFrame(row, fg_color="transparent")
                tf.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    tf, text=name[:60], anchor="w",
                    font=ctk.CTkFont("Segoe UI", 11),
                    text_color=p["fg"]
                ).pack(anchor="w")
                ctk.CTkLabel(
                    tf, text=proj.get("source_url", "")[:70], anchor="w",
                    font=ctk.CTkFont("Segoe UI", 9),
                    text_color=p["muted"]
                ).pack(anchor="w")
                check_vars.append((var, proj))

        _rebuild()
        search_var.trace_add("write", lambda *_: _rebuild(search_var.get()))

        # Bottom buttons
        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(padx=14, pady=(0, 14), fill="x")

        def _select_all():
            for v, _ in check_vars:
                v.set(True)

        def _queue_selected():
            queued = 0
            for v, proj in check_vars:
                if v.get():
                    self._add_url_to_queue(proj["source_url"],
                                           filename=proj.get("name", ""))
                    queued += 1
            if queued:
                win.destroy()
                messagebox.showinfo("Queued", f"{queued} CYOA ditambahkan ke queue.")
            else:
                messagebox.showwarning("Import", "Select at least 1 CYOA.")

        ctk.CTkButton(
            bf, text="Select All", height=30, width=90,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["surface2"], hover_color=p["surface"],
            text_color=p["fg"], command=_select_all
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bf, text="📥 Queue Selected", height=30,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=p["accentbg"], hover_color=p["accentbg_hv"],
            text_color=p["accent"], command=_queue_selected
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bf, text="Close", height=30, width=70,
            fg_color=p["surface2"], hover_color=p["surface"],
            text_color=p["muted"], command=win.destroy
        ).pack(side="right")

    # ── Speed Graph (realtime download speed visualization) ────────────
    def _init_speed_graph(self) -> None:
        """Create a small Canvas speed graph below the progress bar."""
        import tkinter as tk
        p = self._p()
        # Canvas: 110px wide (same as progress bar), 32px tall
        self._speed_canvas = tk.Canvas(
            self._pb.master,  # same parent as progress bar (rowA)
            width=110, height=32,
            bg=p["surface2"], highlightthickness=0, bd=0
        )
        self._speed_canvas.grid(row=1, column=4, padx=(0, 12), pady=(0, 4))
        self._speed_label = tk.Label(
            self._pb.master,
            text="0 KB/s", font=("Segoe UI", 8),
            bg=p["panel"], fg=p["muted"],
            anchor="e"
        )
        self._speed_label.grid(row=1, column=3, padx=(0, 4), pady=(0, 4), sticky="e")
        self._speed_history: List[float] = []   # last 60 speed samples (KB/s)
        self._speed_bytes_acc = 0
        self._speed_timer_id = None

    def _record_speed_bytes(self, n_bytes: int) -> None:
        """Called from download threads to record bytes downloaded."""
        self._speed_bytes_acc += n_bytes

    def _speed_graph_tick(self) -> None:
        """Called every 1s via root.after — updates speed history and redraws."""
        if not hasattr(self, "_speed_canvas"):
            return
        import tkinter as tk

        # Compute speed for this 1-second interval
        speed_kbs = self._speed_bytes_acc / 1024.0
        self._speed_bytes_acc = 0
        self._speed_history.append(speed_kbs)
        if len(self._speed_history) > 60:
            self._speed_history = self._speed_history[-60:]

        # Update label
        if speed_kbs >= 1024:
            self._speed_label.configure(text=f"{speed_kbs/1024:.1f} MB/s")
        else:
            self._speed_label.configure(text=f"{speed_kbs:.0f} KB/s")

        # Redraw canvas
        c = self._speed_canvas
        p = self._p()
        c.delete("all")
        c.configure(bg=p["surface2"])
        w, h = 110, 32
        data = self._speed_history
        if not data or max(data) == 0:
            if self._is_running:
                self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)
            return

        peak = max(data)
        n = len(data)
        step = w / max(n - 1, 1)
        points = []
        for i, v in enumerate(data):
            x = i * step
            y = h - (v / peak) * (h - 4) - 2
            points.append((x, y))

        # Fill area — use accent with low opacity simulation
        fill_color = p.get("accentbg", "#1e3a5f")
        fill_pts = [(0, h)] + points + [(w, h)]
        flat = [coord for pt in fill_pts for coord in pt]
        c.create_polygon(flat, fill=fill_color, outline="")

        # Line
        if len(points) >= 2:
            line_flat = [coord for pt in points for coord in pt]
            c.create_line(line_flat, fill=p.get("accent", "#60a5fa"), width=1.5, smooth=True)

        if self._is_running:
            self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)

    def _start_speed_graph(self) -> None:
        self._speed_history = []
        self._speed_bytes_acc = 0
        if not hasattr(self, "_speed_canvas"):
            self._init_speed_graph()
        self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)

    def _stop_speed_graph(self) -> None:
        if hasattr(self, "_speed_timer_id") and self._speed_timer_id:
            self.root.after_cancel(self._speed_timer_id)
            self._speed_timer_id = None
        # Clear global speed callback
        _self_mod = sys.modules.get(__name__)
        if _self_mod is not None:
            _self_mod._gui_speed_cb = None

    # ── AI API Key Settings ────────────────────────────────────────────
    def _ai_settings_panel(self) -> None:
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"
        win = ctk.CTkToplevel(self.root)
        win.title("🤖 AI Assist Settings" if is_en else "🤖 Pengaturan AI Assist")
        win.geometry("560x600")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        title = "AI Assist — Diagnostics & Recovery" if is_en else "AI Assist — Diagnostik & Pemulihan"
        ctk.CTkLabel(win, text=title,
            font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"]
        ).pack(padx=20, pady=(18, 4), anchor="w")

        desc_en = (
            "AI Assist is optional. It helps locate project.json, inspect JS bundles, "
            "and diagnose custom viewers when normal detection fails. API keys are not "
            "stored in settings.json unless you explicitly choose the plain-text option."
        )
        desc_id = (
            "AI Assist bersifat opsional. Fitur ini membantu mencari project.json, "
            "menganalisis bundle JS, dan mendiagnosis viewer custom saat deteksi normal gagal. "
            "API key tidak disimpan di settings.json kecuali Anda memilih opsi plain-text."
        )
        ctk.CTkLabel(win, text=desc_en if is_en else desc_id,
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            wraplength=510, justify="left"
        ).pack(padx=20, pady=(0, 12), anchor="w")

        toggle_var = ctk.BooleanVar(value=self._ai_enabled)
        ctk.CTkSwitch(win,
            text="Enable AI Assist" if is_en else "Aktifkan AI Assist",
            variable=toggle_var, font=ctk.CTkFont("Segoe UI", 11),
            text_color=p["fg"], progress_color="#8b5cf6"
        ).pack(padx=20, pady=(0, 12), anchor="w")

        grid = ctk.CTkFrame(win, fg_color="transparent")
        grid.pack(padx=20, fill="x", pady=(0, 8))
        grid.grid_columnconfigure(1, weight=1)

        def label(row, txt):
            ctk.CTkLabel(grid, text=txt, font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=p["fg"], width=130, anchor="w").grid(row=row, column=0, sticky="w", pady=5)

        st = _load_settings()
        provider_var = ctk.StringVar(value=_normalize_ai_provider(st.get("ai_provider", "anthropic")))
        model_var = ctk.StringVar(value=_get_ai_model(provider_var.get()))
        mode_var = ctk.StringVar(value=_normalize_ai_mode(st.get("ai_mode", "auto_fallback")))
        storage_var = ctk.StringVar(value=_normalize_ai_key_storage(st.get("ai_key_storage", getattr(self, "_ai_key_storage", "session"))))
        session_key_var = ctk.StringVar(value=self._ai_api_key if storage_var.get() in {"session", "plain"} else "")

        label(0, "Provider" if is_en else "Provider")
        provider_menu = ctk.CTkOptionMenu(grid, variable=provider_var, values=["anthropic", "openai", "gemini", "ollama"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32)
        provider_menu.grid(row=0, column=1, sticky="ew", pady=5)

        label(1, "Model" if is_en else "Model")
        # CTkComboBox keeps curated presets but also lets advanced users type a custom model id.
        model_menu = ctk.CTkComboBox(grid, variable=model_var,
            values=_ai_model_options(provider_var.get()),
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            border_color=p["border"], height=32)
        model_menu.grid(row=1, column=1, sticky="ew", pady=5)

        label(2, "AI Mode" if is_en else "Mode AI")
        ctk.CTkOptionMenu(grid, variable=mode_var,
            values=["off", "diagnostics", "auto_fallback", "aggressive_recovery"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32).grid(row=2, column=1, sticky="ew", pady=5)

        label(3, "Key Storage" if is_en else "Penyimpanan Key")
        ctk.CTkOptionMenu(grid, variable=storage_var,
            values=["session", "env", "keyring", "plain"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32).grid(row=3, column=1, sticky="ew", pady=5)

        label(4, "API Key" if is_en else "API Key")
        key_entry = ctk.CTkEntry(grid, textvariable=session_key_var,
            font=ctk.CTkFont("Segoe UI", 11), fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32, show="•")
        key_entry.grid(row=4, column=1, sticky="ew", pady=5)

        label(5, "Ollama URL" if is_en else "URL Ollama")
        ollama_url_var = ctk.StringVar(value=st.get("ollama_url", OLLAMA_DEFAULT_URL))
        ollama_url_entry = ctk.CTkEntry(grid, textvariable=ollama_url_var,
            font=ctk.CTkFont("Segoe UI", 11), fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32)
        ollama_url_entry.grid(row=5, column=1, sticky="ew", pady=5)

        status_var = ctk.StringVar(value=_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get()))
        status_lbl = ctk.CTkLabel(win, textvariable=status_var,
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            wraplength=510, justify="left")
        status_lbl.pack(padx=20, pady=(0, 8), anchor="w")

        warn_var = ctk.StringVar(value="")
        warn_lbl = ctk.CTkLabel(win, textvariable=warn_var,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color="#f59e0b",
            wraplength=510, justify="left")
        warn_lbl.pack(padx=20, pady=(0, 8), anchor="w")

        def _refresh_key_ui(*_):
            mode = _normalize_ai_key_storage(storage_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            if provider == "ollama":
                key_entry.configure(state="disabled", placeholder_text="Ollama uses local API")
                try: ollama_url_entry.configure(state="normal")
                except Exception: pass
                warn_var.set(("Ollama runs locally by default. Set the URL if your Ollama server uses a different host or port." if is_en else
                              "Ollama berjalan lokal secara default. Atur URL jika server Ollama memakai host atau port berbeda."))
            elif mode == "env":
                try: ollama_url_entry.configure(state="disabled")
                except Exception: pass
                key_entry.configure(state="disabled", placeholder_text=_ai_primary_env_var(provider_var.get()) or "No API key needed")
                warn_var.set((("Set " + " or ".join(_ai_env_vars(provider_var.get())) + " in your environment. The app will not store it.") if is_en else
                              ("Atur " + " atau ".join(_ai_env_vars(provider_var.get())) + " di environment. Aplikasi tidak akan menyimpannya.")))
            elif mode == "keyring":
                try: ollama_url_entry.configure(state="disabled")
                except Exception: pass
                key_entry.configure(state="normal", placeholder_text=("Enter key to save to OS Credential Manager" if is_en else "Masukkan key untuk disimpan ke OS Credential Manager"))
                warn_var.set(("Requires optional package: pip install keyring" if not _keyring_module() else
                              ("Key will be stored in the OS credential store." if is_en else "Key akan disimpan di credential store sistem operasi.")))
            elif mode == "plain":
                try: ollama_url_entry.configure(state="disabled")
                except Exception: pass
                key_entry.configure(state="normal", placeholder_text=("not needed" if _normalize_ai_provider(provider_var.get()) == "ollama" else "API key..."))
                warn_var.set(("Warning: this stores the API key as plain text in settings.json." if is_en else
                              "Peringatan: API key akan disimpan sebagai teks biasa di settings.json."))
            else:
                try: ollama_url_entry.configure(state="disabled")
                except Exception: pass
                key_entry.configure(state="normal", placeholder_text=("Session only. Cleared when app exits." if is_en else "Hanya sesi ini. Hilang saat aplikasi ditutup."))
                warn_var.set(("Safest default. The key stays in memory only." if is_en else
                              "Default paling aman. Key hanya tersimpan di memori."))
            status_var.set(_ai_key_status_text(mode, session_key_var.get(), provider_var.get()))

        storage_var.trace_add("write", _refresh_key_ui)

        def _provider_changed(*_):
            prov = _normalize_ai_provider(provider_var.get())
            opts = _ai_model_options(prov)
            try:
                model_menu.configure(values=opts)
            except Exception:
                pass
            if model_var.get() not in opts:
                model_var.set(_default_ai_model(prov))
            mode = _normalize_ai_key_storage(storage_var.get())
            if prov == "ollama":
                session_key_var.set("")
            elif mode == "plain":
                session_key_var.set(_resolve_ai_api_key(storage="plain", provider=prov))
            elif mode in {"env", "keyring"}:
                session_key_var.set("")
            _refresh_key_ui()

        provider_var.trace_add("write", _provider_changed)
        session_key_var.trace_add("write", lambda *_: status_var.set(_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get())))
        _refresh_key_ui()

        def _test_key():
            provider = _normalize_ai_provider(provider_var.get())
            key = _resolve_ai_api_key(session_key=session_key_var.get(), storage=storage_var.get(), provider=provider)
            if provider != "ollama" and not key:
                messagebox.showwarning("AI Assist", "No API key available." if is_en else "API key belum tersedia.")
                return
            res = _ai_call(key, "Reply exactly: OK", max_tokens=16, label="AI key test", model=model_var.get(), provider=provider)
            if res:
                messagebox.showinfo("AI Assist", "API key works." if is_en else "API key berhasil digunakan.")
            else:
                messagebox.showerror("AI Assist", "API key test failed. Check key, model, and network." if is_en else "Tes API key gagal. Cek key, model, dan jaringan.")

        def _clear_key():
            mode = _normalize_ai_key_storage(storage_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            _clear_ai_api_key_storage(mode, provider, clear_all=True)
            session_key_var.set("")
            self._ai_api_key = ""
            status_var.set(_ai_key_status_text(mode, "", provider))

        def _save():
            mode = _normalize_ai_key_storage(storage_var.get())
            api_key = session_key_var.get().strip()
            settings = _load_settings()
            settings["ai_enabled"] = bool(toggle_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            settings["ai_provider"] = provider
            settings["ai_model"] = model_var.get().strip() or _default_ai_model(provider)
            settings["ai_mode"] = _normalize_ai_mode(mode_var.get())
            settings["ai_key_storage"] = mode
            settings["ollama_url"] = (ollama_url_var.get().strip() or OLLAMA_DEFAULT_URL)

            if mode == "plain":
                if api_key:
                    ok = messagebox.askyesno(
                        "Plain text API key" if is_en else "API key teks biasa",
                        "This will store the API key as plain text in settings.json. Continue?" if is_en else
                        "API key akan disimpan sebagai teks biasa di settings.json. Lanjutkan?"
                    )
                    if not ok:
                        return
                _clear_ai_plain_keys(settings, None)
                settings[_plain_ai_key_setting(provider)] = api_key
                self._ai_api_key = api_key
            elif mode == "keyring":
                _clear_ai_plain_keys(settings, None)
                if api_key:
                    if not _write_ai_key_to_keyring(api_key, provider):
                        messagebox.showerror("AI Assist", "Failed to write to OS Credential Manager. Install keyring or choose another storage." if is_en else "Gagal menyimpan ke OS Credential Manager. Install keyring atau pilih storage lain.")
                        return
                self._ai_api_key = ""
            elif mode == "session":
                _clear_ai_plain_keys(settings, None)
                self._ai_api_key = api_key
            else:  # env
                _clear_ai_plain_keys(settings, None)
                self._ai_api_key = ""

            self._ai_enabled = bool(toggle_var.get())
            self._ai_provider = provider
            self._ai_key_storage = mode
            self._ai_model = settings["ai_model"]
            self._ai_mode = settings["ai_mode"]
            if hasattr(self, "_ai_var"):
                self._ai_var.set(self._ai_enabled)
            _save_settings(settings)
            if hasattr(self, '_ai_btn'):
                self._ai_btn.configure(
                    text="🤖 AI  " + ("ON" if self._ai_enabled else "OFF"),
                    text_color=p["accent"] if self._ai_enabled else p["muted"])
            win.destroy()

        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(padx=20, fill="x", pady=(8, 14))
        ctk.CTkButton(bf, text="Test API Key" if is_en else "Tes API Key", height=30,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_test_key
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bf, text="Clear Key" if is_en else "Hapus Key", height=30,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=_clear_key
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bf, text="💾 Save" if is_en else "💾 Simpan", height=30,
            font=ctk.CTkFont("Segoe UI", 11, "bold"), fg_color=p["accentbg"],
            hover_color=p["accentbg_hv"], text_color=p["accent"], command=_save
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bf, text="Cancel" if is_en else "Batal", height=30, width=70,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=win.destroy
        ).pack(side="right")

    # ── Auto-update Checker ────────────────────────────────────────────
    def _check_updates_panel(self) -> None:
        import customtkinter as ctk
        import webbrowser
        p = self._p()
        win = ctk.CTkToplevel(self.root)
        win.title("🔄 Check for Updates")
        win.geometry("400x200")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        status_lbl = ctk.CTkLabel(win, text="Checking…",
                                  font=ctk.CTkFont("Segoe UI", 13),
                                  text_color=p["fg"])
        status_lbl.pack(padx=20, pady=(20, 10))

        notes_lbl = ctk.CTkLabel(win, text="",
                                 font=ctk.CTkFont("Segoe UI", 10),
                                 text_color=p["muted"], wraplength=360,
                                 justify="left")
        notes_lbl.pack(padx=20, pady=(0, 10))

        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(0, 14))

        def _check():
            if not _GITHUB_RELEASE_API:
                status_lbl.configure(
                    text=f"CYOA Downloader v{_APP_VERSION}\n\n"
                         "Auto-update not configured.\n"
                         "Set _GITHUB_RELEASE_API in the script to enable.")

                return
            info = _check_for_app_updates()
            if info:
                status_lbl.configure(
                    text=f"Update tersedia: v{info['version']} "
                         f"(current: v{_APP_VERSION})")
                notes_lbl.configure(text=info.get("notes", "")[:300])
                if info.get("url"):
                    ctk.CTkButton(btn_frame, text="Open Release Page",
                                  height=30,
                                  fg_color=p["accentbg"],
                                  hover_color=p["accentbg_hv"],
                                  text_color=p["accent"],
                                  command=lambda: webbrowser.open(info["url"])
                                  ).pack(side="left", padx=4)
            else:
                status_lbl.configure(
                    text=f"CYOA Downloader v{_APP_VERSION}\n\n"
                         "Already up to date ✅")

        ctk.CTkButton(btn_frame, text="Close", height=30,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      command=win.destroy).pack(side="left", padx=4)

        threading.Thread(target=lambda: self.root.after(0, _check),
                         daemon=True).start()

    # ── Batch Update Checker ───────────────────────────────────────────
    def _batch_update_panel(self) -> None:
        import customtkinter as ctk
        p = self._p()
        history = _load_history()
        if not history:
            from tkinter import messagebox
            messagebox.showinfo("Batch Check", "No download history yet.")
            return

        win = ctk.CTkToplevel(self.root)
        win.title("📥 Batch Update Checker")
        win.geometry("600x400")
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        header = ctk.CTkLabel(
            win, text=f"Checking {len([h for h in history.values() if h.get('success')])} "
                      f"previously downloaded CYOAs…",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=p["fg"])
        header.pack(padx=14, pady=(14, 6))

        pb = ctk.CTkProgressBar(win, height=6)
        pb.pack(padx=14, fill="x")
        pb.set(0)

        result_frame = ctk.CTkScrollableFrame(win, fg_color=p["surface2"],
                                               corner_radius=8)
        result_frame.pack(padx=14, pady=(10, 14), fill="both", expand=True)

        def _run():
            def _prog(done, total):
                if total:
                    self.root.after(0, lambda: pb.set(done / total))

            results = _batch_check_updates(history, progress_cb=_prog)

            def _show():
                pb.set(1.0)
                updated = [r for r in results if r["status"] == "updated"]
                current = [r for r in results if r["status"] == "current"]
                errors  = [r for r in results if r["status"] in ("error", "unreachable")]
                header.configure(
                    text=f"✅ {len(current)} current  |  "
                         f"🔄 {len(updated)} updated  |  "
                         f"❌ {len(errors)} errors")

                for r in updated:
                    f = ctk.CTkFrame(result_frame, fg_color=p["accentbg"],
                                     corner_radius=6)
                    f.pack(fill="x", padx=4, pady=2)
                    ctk.CTkLabel(f, text=f"🔄 {r.get('name') or r['url'][:50]}",
                                 font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                 text_color=p["accent"]).pack(anchor="w", padx=8, pady=(4, 0))
                    ctk.CTkLabel(f, text=r.get("reason", ""),
                                 font=ctk.CTkFont("Segoe UI", 9),
                                 text_color=p["muted"]).pack(anchor="w", padx=8, pady=(0, 4))

                    def _requeue(url=r["url"]):
                        self._add_url_to_queue(url)
                        win.destroy()

                    ctk.CTkButton(f, text="Re-download", height=24, width=100,
                                  font=ctk.CTkFont("Segoe UI", 10),
                                  fg_color=p["surface2"], hover_color=p["surface"],
                                  text_color=p["fg"],
                                  command=_requeue).pack(anchor="e", padx=8, pady=(0, 4))

                for r in errors:
                    f = ctk.CTkFrame(result_frame, fg_color=p["surface"],
                                     corner_radius=6)
                    f.pack(fill="x", padx=4, pady=2)
                    ctk.CTkLabel(f, text=f"❌ {r.get('name') or r['url'][:50]}",
                                 font=ctk.CTkFont("Segoe UI", 10),
                                 text_color=p["muted"]).pack(anchor="w", padx=8, pady=2)

                if not updated and not errors:
                    ctk.CTkLabel(result_frame,
                                 text="All CYOAs are still up-to-date ✅",
                                 font=ctk.CTkFont("Segoe UI", 12),
                                 text_color=p["fg"]).pack(pady=20)

            self.root.after(0, _show)

        threading.Thread(target=_run, daemon=True).start()

    def _add_url_to_queue(self, url: str, filename: str = "") -> None:
        """Programmatically add a URL to the queue (used by batch update / CM import)."""
        try:
            self._url_var.set(url)
            if filename and hasattr(self, "_fn_var"):
                self._fn_var.set(filename)
            self._add_url()
        except Exception:
            pass

    def _show_feature_guide(self, initial_tab: str = "download") -> None:
        """Feature overview + quick-reference panel with full Indonesian/English content."""
        import customtkinter as ctk

        p = self._p()
        lang = getattr(self, "_language", "id")
        is_en = (lang == "en")

        win = ctk.CTkToplevel(self.root)
        win.title("CYOA Downloader — Feature Guide" if is_en else "CYOA Downloader — Panduan Fitur")
        win.geometry("780x660")
        win.grab_set()

        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=58)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        header_text = (
            "📖  Feature Guide — CYOA Downloader v7.3.3"
            if is_en else
            "📖  Panduan Fitur — CYOA Downloader v7.3.3"
        )
        ctk.CTkLabel(
            hdr,
            text=header_text,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=p["fg"],
        ).pack(side="left", padx=16)

        tab_var = ctk.StringVar(value="download")
        tab_frame = ctk.CTkFrame(win, fg_color=p["surface"], corner_radius=0, height=42)
        tab_frame.pack(fill="x")
        tab_frame.pack_propagate(False)

        TABS_EN = [
            ("download", "⬇ Download"),
            ("audio", "🎵 Audio"),
            ("queue", "📋 Queue"),
            ("viewer", "📺 Viewer"),
            ("network", "🌐 Network"),
            ("cyoa_mgr", "📤 Manager"),
            ("cheat", "⚙ Cheat"),
            ("workflow", "⌨ Workflow"),
            ("cookies", "🍪 Cookies"),
        ]
        TABS_ID = [
            ("download", "⬇ Unduh"),
            ("audio", "🎵 Audio"),
            ("queue", "📋 Antrean"),
            ("viewer", "📺 Viewer"),
            ("network", "🌐 Jaringan"),
            ("cyoa_mgr", "📤 Manager"),
            ("cheat", "⚙ Cheat"),
            ("workflow", "⌨ Alur"),
            ("cookies", "🍪 Cookie"),
        ]
        TABS = TABS_EN if is_en else TABS_ID

        content_area = ctk.CTkScrollableFrame(
            win,
            fg_color=p["bg"],
            scrollbar_button_color=p["surface2"],
        )
        content_area.pack(fill="both", expand=True)

        CONTENT_EN = {
            "download": [
                ("⬇  Download Modes", "accent", [
                    ("Auto", "Probes the URL before downloading and selects the best engine. The default output is Website Folder."),
                    ("Embedded JSON", "Stores images as base64 inside one project JSON file. Use this when you want a compact single-file backup."),
                    ("ZIP", "Creates project.json plus separate image and audio folders inside a ZIP archive."),
                    ("Both", "Creates an embedded JSON and a ZIP package in one run."),
                    ("Website Folder", "Downloads viewer HTML, CSS, JavaScript, images, fonts, audio, and project data into a playable local folder."),
                    ("Website ZIP", "Creates the same website package as Website Folder, then compresses it into a ZIP archive."),
                    ("Pure Website", "Downloads the visible site without trying to discover a project JSON. Use it for custom viewer formats."),
                    ("cyoap_vue", "Uses the CYOA-P Vue flow by downloading dist/platform.json and dist/nodes/*.json."),
                ]),
                ("🔧  Options Row", "muted", [
                    ("Threads", "Controls parallel image downloads. Start with 4 to 8 threads for stable hosts."),
                    ("Retry delay", "Wait time after rate-limit responses such as HTTP 429."),
                    ("Bandwidth limit", "Limits download speed in KB/s. Use 0 for unlimited speed."),
                    ("Download Fonts", "Downloads fonts referenced by HTML or CSS files."),
                    ("HTTP/2", "Uses httpx with HTTP/2 for deep-scan fetches when httpx[h2] is installed."),
                    ("YT Audio", "Downloads YouTube audio with yt-dlp and ffmpeg, then patches local audio paths into the project."),
                    ("CYOA Manager", "Adds successful project JSON files to the CYOA Manager library."),
                ]),
                ("📂  Output", "muted", [
                    ("Output folder", "Destination folder for all generated files. The folder is created automatically if it does not exist."),
                    ("Filename", "The filename is generated from the URL by default, but you can edit it before adding the URL to the queue."),
                    ("Reports", "The program writes backup_report.txt, failed_assets.txt, failed_images.txt, and cyoa_downloader.log when relevant."),
                ]),
            ],
            "audio": [
                ("🎵  Offline Audio Flow", "accent", [
                    ("Detection", "Scans project JSON for bgmId, direct audio fields, playlists, and YouTube video IDs."),
                    ("Download", "Uses yt-dlp to download YouTube audio, then ffmpeg converts it to MP3."),
                    ("Patch", "Rewrites bgmId and useAudioURL so the offline viewer can load local audio files."),
                    ("Copy", "Copies the audio folder into the JSON package, ZIP package, or website folder as needed."),
                    ("Skipped items", "Writes skipped_youtube_audio.txt when a YouTube track cannot be downloaded."),
                ]),
                ("⚙  ffmpeg Detection", "yellow", [
                    ("PATH", "Searches for ffmpeg in the active PATH."),
                    ("Windows registry", "Reads user and machine PATH entries from the Windows registry."),
                    ("Package managers", "Checks common winget, Scoop, Chocolatey, and local install locations."),
                    ("Manual fix", "Install ffmpeg and restart the program if audio conversion fails."),
                ]),
                ("🔁  Browser Cookies", "muted", [
                    ("Automatic", "Tries browser cookies from Chrome, Firefox, Edge, Brave, Chromium, and Safari."),
                    ("Locked Chrome", "Copies the cookie database to a temporary location when Chrome keeps it locked."),
                    ("Manual cookies.txt", "Place cookies.txt in the output folder or pass it through the CLI when automatic cookies fail."),
                ]),
                ("🔇  Browser Autoplay", "red", [
                    ("Blocked playback", "Modern browsers can block autoplay. The offline viewer adds an enable-audio banner when needed."),
                    ("User action", "Click the audio banner once to allow playback on the local page."),
                ]),
            ],
            "queue": [
                ("📋  Queue Management", "accent", [
                    ("Add URL", "Paste a URL and press Enter or click Add. Duplicate URLs are skipped."),
                    ("Edit name", "Edit the filename field below each queued URL before downloading."),
                    ("Reorder", "Drag the handle at the left of a row to change download priority."),
                    ("Remove", "Use the row close button, Remove, or Clear All to manage the queue."),
                    ("Batch import", "Imports .txt, .csv, .xlsx, or Google Sheet CSV sources with URL, filename, and mode columns."),
                ]),
                ("🔍  Pre-flight Preview", "muted", [
                    ("Probe", "Checks URLs before starting downloads and shows whether project data is likely available."),
                    ("Results", "FOUND means direct project data was detected. JS/SCAN means discovery may require script scanning. ERROR means the URL failed."),
                    ("Proceed", "Use Proceed with Download to start immediately from the preview results."),
                ]),
                ("💾  Resume and Retry", "muted", [
                    ("Resume", "Successful URLs are written to download_state.json so a repeated batch can skip completed items."),
                    ("Retry Failed", "Adds failed queue items back into the queue."),
                    ("Retry Images", "Uses failed_images.txt to retry image downloads and patch the project JSON."),
                ]),
            ],
            "viewer": [
                ("📺  Offline Viewer", "accent", [
                    ("Register viewer", "Open Viewers and add an offline viewer ZIP such as ICC Plus, ICC Remix, or a compatible custom viewer."),
                    ("Auto-match", "Matches a viewer to the CYOA by reading HTML and script hints."),
                    ("ICC Remix", "Injects project data into the template marker."),
                    ("ICC Plus", "Uses marker-based and balanced-brace injection around the project data placeholder."),
                    ("Custom viewer", "Patches project.json fetch calls when the viewer supports local project data."),
                ]),
                ("⚙  Cheat Overlay", "muted", [
                    ("Gear button", "Adds a floating gear button to offline viewers."),
                    ("Set Points", "Changes point values in Vuex or Pinia stores."),
                    ("Remove Requirements", "Removes required fields from rows and objects."),
                    ("Unlimited Choices", "Sets allowedChoices to unlimited across rows."),
                    ("Select or Deselect", "Selects or deselects all choices in the CYOA."),
                ]),
                ("⚡  Local Server", "green", [
                    ("Start", "Use Serve to select a folder and start a local HTTP server."),
                    ("Browser", "The browser opens to localhost on the selected server port."),
                    ("CORS", "The local server sends permissive CORS headers for local images and audio."),
                    ("Cache", "Serve disables browser cache and opens with a cache-busting URL so old CYOAs are not replayed."),
                    ("Stop", "Use Stop Server to shut down the local server."),
                ]),
            ],
            "network": [
                ("🌐  Network Controls", "accent", [
                    ("Proxy", "Applies HTTP, HTTPS, or SOCKS proxy settings to downloader requests."),
                    ("DNS", "Uses system DNS, preset DNS, custom DNS, or BebasDNS DoH for process-local resolution."),
                    ("BebasDNS", "Uses DNS-over-HTTPS presets without changing Windows, router, browser, or hosts-file settings."),
                    ("HTTP/2", "Uses httpx HTTP/2 for compatible deep-scan requests."),
                    ("Broken assets", "Writes failed asset details into backup_report.txt when available, otherwise into failed_assets.txt."),
                ]),
                ("☁  Cloudflare Access", "yellow", [
                    ("Off", "Never attempts Cloudflare bypass."),
                    ("Auto", "Tries normal request first, then cloudscraper, then FlareSolverr when available."),
                    ("cloudscraper", "Uses cloudscraper for lighter Cloudflare challenge pages."),
                    ("FlareSolverr", "Uses a local FlareSolverr service to solve browser-based challenge pages."),
                    ("Endpoint", "Default API endpoint: http://localhost:8191/v1."),
                    ("Session", "Reuse per domain keeps cookies and user-agent for the same host. Clear sessions when cookies become stale."),
                    ("Proxy mode", "Inherit proxy sends the app proxy to FlareSolverr. None leaves FlareSolverr on its own network path."),
                ]),
                ("🎨  gallery-dl", "muted", [
                    ("Off", "Default. The program never calls gallery-dl."),
                    ("Smart", "Uses gallery-dl only for likely post, artwork, gallery, or status pages, not raw CDN images."),
                    ("Force", "Advanced mode. Passes matching URLs to gallery-dl even when the URL shape is uncertain."),
                    ("Authentication", "Use gallery-dl config for Pixiv OAuth, booru API keys, or account-based extractors."),
                ]),
            ],
            "cyoa_mgr": [
                ("📤  CYOA Manager Integration", "accent", [
                    ("Auto-add", "Enable CYOA Manager in the options row to add successful project JSON files automatically."),
                    ("Status button", "The CYOA Manager button shows whether the integration is enabled."),
                    ("Database path", "Use a custom library.sqlite3 path for portable or non-standard installations."),
                    ("Duplicate check", "The program checks existing file_path entries before inserting a new library record."),
                    ("Viewer preference", "Stores a viewer preference so CYOA Manager can open the project with the correct viewer."),
                ]),
                ("📦  Batch Export", "muted", [
                    ("Scan folder", "Finds project JSON files larger than 1 KB in a selected folder."),
                    ("Pick files", "Allows manual multi-select of project JSON files."),
                    ("Last session", "Exports projects downloaded in the last session."),
                    ("Result", "Shows counts for added, already existing, and failed items."),
                ]),
            ],
            "cheat": [
                ("⚙  Cheat Overlay Detail", "accent", [
                    ("Polling", "Checks every 500 ms until the Vue app and store are available."),
                    ("Vuex", "Targets window.app.__vue__.$store.state.app for older ICC Plus builds."),
                    ("Pinia", "Targets window.__pinia.state.value for newer ICC Plus builds."),
                    ("Injection", "Injects the overlay before the closing body tag in offline viewer folders."),
                ]),
                ("🔧  Available Changes", "muted", [
                    ("Set Points", "Updates starting point values."),
                    ("Remove Requirements", "Deletes requirement arrays from rows and objects."),
                    ("Unlimited Choices", "Sets allowedChoices to unlimited."),
                    ("Select All", "Marks all objects as selected."),
                    ("Deselect All", "Marks all objects as not selected."),
                ]),
            ],
            "workflow": [
                ("⌨  Keyboard and Workflow", "accent", [
                    ("Enter", "Adds the current URL to the queue."),
                    ("Drag handle", "Reorders queue items."),
                    ("Open Folder", "Opens the selected output folder."),
                    ("Preview", "Runs the URL probe without starting a full download."),
                    ("Serve", "Starts a local server for a generated website folder."),
                ]),
                ("🔗  Supported URL Types", "muted", [
                    ("Standard HTTPS", "Supports many Neocities, Netlify, Vercel, GitHub Pages, and self-hosted CYOA pages."),
                    ("cyoa.cafe", "Resolves iframe-based CYOA pages when possible."),
                    ("archive.org", "Can reconstruct original URLs from known CYOA archive patterns."),
                    ("cyoap_vue", "Supports projects with dist/platform.json and dist/nodes/list.json."),
                ]),
                ("📁  Output Files", "muted", [
                    ("project.json", "Project data for embedded or ZIP outputs."),
                    ("audio/", "Downloaded local audio files."),
                    ("backup_report.txt", "Summary of downloaded and failed files."),
                    ("failed_images.txt", "Image URLs available for retry."),
                    ("cyoa_downloader.log", "Full session log written to the output folder."),
                ]),
            ],
            "cookies": [
                ("🎵  yt-dlp Cookies", "green", [
                    ("Automatic", "Log in to YouTube in your browser, then let yt-dlp read browser cookies automatically."),
                    ("Browser order", "Chrome, Firefox, Edge, Brave, Chromium, then Safari."),
                    ("Manual export", "Export cookies.txt with a browser extension when automatic cookie reading fails."),
                    ("Common failures", "Expired cookies, private videos, deleted videos, and region locks can still fail."),
                ]),
                ("🎨  gallery-dl Authentication", "accent", [
                    ("Pixiv OAuth", "Run gallery-dl oauth:pixiv, authorize in the browser, and store the token in gallery-dl config."),
                    ("Danbooru", "Configure username and API key in gallery-dl config."),
                    ("e621", "Configure username and API key in gallery-dl config."),
                    ("Sankaku and similar sites", "Configure username and password only when the extractor requires an account."),
                    ("Config location", "Windows uses AppData Roaming. macOS and Linux usually use ~/.config/gallery-dl/config.json."),
                ]),
            ],
        }

        CONTENT_ID = {
            "download": [
                ("⬇  Mode Unduhan", "accent", [
                    ("Auto", "Memeriksa URL sebelum unduhan dimulai dan memilih mesin yang paling sesuai. Output bawaan adalah Folder Website."),
                    ("JSON Tertanam", "Menyimpan gambar sebagai base64 di satu file JSON project."),
                    ("ZIP", "Membuat project.json bersama folder gambar dan audio terpisah di arsip ZIP."),
                    ("Keduanya", "Membuat JSON tertanam dan paket ZIP dalam satu proses."),
                    ("Folder Website", "Mengunduh HTML, CSS, JavaScript, gambar, font, audio, dan data project ke folder lokal yang dapat dimainkan."),
                    ("ZIP Website", "Membuat paket website seperti Folder Website, lalu mengompresnya menjadi ZIP."),
                    ("Pure Website", "Mengunduh situs yang terlihat tanpa mencari project JSON. Gunakan untuk format viewer khusus."),
                    ("cyoap_vue", "Memakai alur CYOA-P Vue dengan mengunduh dist/platform.json dan dist/nodes/*.json."),
                ]),
                ("🔧  Baris Opsi", "muted", [
                    ("Thread", "Mengatur jumlah unduhan gambar paralel. Awali dengan 4 sampai 8 thread untuk host yang stabil."),
                    ("Jeda retry", "Waktu tunggu setelah respons pembatasan seperti HTTP 429."),
                    ("Batas bandwidth", "Membatasi kecepatan unduh dalam KB/detik. Gunakan 0 untuk tanpa batas."),
                    ("Unduh Font", "Mengunduh font yang dirujuk oleh file HTML atau CSS."),
                    ("HTTP/2", "Memakai httpx dengan HTTP/2 untuk deep scan jika httpx[h2] tersedia."),
                    ("Audio YT", "Mengunduh audio YouTube dengan yt-dlp dan ffmpeg, lalu menambal path audio lokal ke project."),
                    ("CYOA Manager", "Menambahkan file JSON project yang berhasil ke pustaka CYOA Manager."),
                ]),
                ("📂  Output", "muted", [
                    ("Folder output", "Folder tujuan untuk semua file hasil. Folder dibuat otomatis jika belum ada."),
                    ("Nama file", "Nama file dibuat dari URL secara otomatis, tetapi dapat diedit sebelum URL masuk antrean."),
                    ("Laporan", "Program menulis backup_report.txt, failed_assets.txt, failed_images.txt, dan cyoa_downloader.log jika relevan."),
                ]),
            ],
            "audio": [
                ("🎵  Alur Audio Offline", "accent", [
                    ("Deteksi", "Memindai JSON project untuk bgmId, field audio langsung, playlist, dan ID video YouTube."),
                    ("Unduh", "Memakai yt-dlp untuk mengunduh audio YouTube, lalu ffmpeg mengonversinya ke MP3."),
                    ("Patch", "Mengubah bgmId dan useAudioURL agar viewer offline memuat file audio lokal."),
                    ("Salin", "Menyalin folder audio ke paket JSON, paket ZIP, atau folder website sesuai kebutuhan."),
                    ("Item dilewati", "Menulis skipped_youtube_audio.txt jika track YouTube tidak dapat diunduh."),
                ]),
                ("⚙  Deteksi ffmpeg", "yellow", [
                    ("PATH", "Mencari ffmpeg di PATH aktif."),
                    ("Registry Windows", "Membaca PATH user dan mesin dari registry Windows."),
                    ("Package manager", "Memeriksa lokasi umum winget, Scoop, Chocolatey, dan instalasi lokal."),
                    ("Perbaikan manual", "Instal ffmpeg dan mulai ulang program jika konversi audio gagal."),
                ]),
                ("🔁  Cookie Browser", "muted", [
                    ("Otomatis", "Mencoba cookie browser dari Chrome, Firefox, Edge, Brave, Chromium, dan Safari."),
                    ("Chrome terkunci", "Menyalin database cookie ke lokasi sementara saat Chrome menguncinya."),
                    ("cookies.txt manual", "Letakkan cookies.txt di folder output atau kirim lewat CLI saat cookie otomatis gagal."),
                ]),
                ("🔇  Autoplay Browser", "red", [
                    ("Playback diblokir", "Browser modern dapat memblokir autoplay. Viewer offline menambahkan banner aktifkan audio jika diperlukan."),
                    ("Aksi pengguna", "Klik banner audio satu kali agar halaman lokal boleh memutar audio."),
                ]),
            ],
            "queue": [
                ("📋  Manajemen Antrean", "accent", [
                    ("Tambah URL", "Tempel URL dan tekan Enter atau klik Tambah. URL duplikat dilewati."),
                    ("Edit nama", "Edit field nama file di bawah setiap URL antrean sebelum mengunduh."),
                    ("Ubah urutan", "Seret handle di kiri baris untuk mengubah prioritas unduhan."),
                    ("Hapus", "Gunakan tombol tutup baris, Hapus, atau Bersihkan untuk mengatur antrean."),
                    ("Import batch", "Mengimpor sumber .txt, .csv, .xlsx, atau Google Sheet CSV dengan kolom URL, filename, dan mode."),
                ]),
                ("🔍  Pratinjau Awal", "muted", [
                    ("Probe", "Memeriksa URL sebelum unduhan penuh dimulai dan menampilkan kemungkinan ketersediaan data project."),
                    ("Hasil", "FOUND berarti data project langsung terdeteksi. JS/SCAN berarti perlu pemindaian script. ERROR berarti URL gagal."),
                    ("Lanjut", "Gunakan Lanjutkan Download untuk mulai langsung dari hasil pratinjau."),
                ]),
                ("💾  Resume dan Retry", "muted", [
                    ("Resume", "URL yang berhasil ditulis ke download_state.json agar batch ulang dapat melewati item yang selesai."),
                    ("Ulang gagal", "Memasukkan kembali item antrean yang gagal."),
                    ("Ulang gambar", "Memakai failed_images.txt untuk mencoba ulang unduhan gambar dan menambal JSON project."),
                ]),
            ],
            "viewer": [
                ("📺  Viewer Offline", "accent", [
                    ("Daftarkan viewer", "Buka Viewer dan tambahkan ZIP viewer offline seperti ICC Plus, ICC Remix, atau viewer khusus yang kompatibel."),
                    ("Cocok otomatis", "Mencocokkan viewer ke CYOA dengan membaca petunjuk HTML dan script."),
                    ("ICC Remix", "Menyisipkan data project ke marker template."),
                    ("ICC Plus", "Memakai injeksi berbasis marker dan balanced-brace di sekitar placeholder data project."),
                    ("Viewer khusus", "Menambal pemanggilan fetch project.json jika viewer mendukung data project lokal."),
                ]),
                ("⚙  Cheat Overlay", "muted", [
                    ("Tombol gear", "Menambahkan tombol gear mengambang ke viewer offline."),
                    ("Atur poin", "Mengubah nilai poin di store Vuex atau Pinia."),
                    ("Hapus syarat", "Menghapus field requirement dari row dan object."),
                    ("Pilihan tak terbatas", "Mengatur allowedChoices menjadi tak terbatas di semua row."),
                    ("Pilih atau batal", "Memilih atau membatalkan semua pilihan di CYOA."),
                ]),
                ("⚡  Server Lokal", "green", [
                    ("Mulai", "Gunakan Serve untuk memilih folder dan menjalankan server HTTP lokal."),
                    ("Browser", "Browser terbuka ke localhost pada port server yang dipilih."),
                    ("CORS", "Server lokal mengirim header CORS permisif untuk gambar dan audio lokal."),
                    ("Cache", "Serve menonaktifkan cache browser dan membuka URL cache-busting agar CYOA lama tidak terputar ulang."),
                    ("Berhenti", "Gunakan Stop Server untuk mematikan server lokal."),
                ]),
            ],
            "network": [
                ("🌐  Kontrol Jaringan", "accent", [
                    ("Proxy", "Menerapkan proxy HTTP, HTTPS, atau SOCKS untuk request downloader."),
                    ("DNS", "Memakai DNS sistem, preset DNS, DNS khusus, atau BebasDNS DoH untuk resolusi lokal proses."),
                    ("BebasDNS", "Memakai preset DNS-over-HTTPS tanpa mengubah Windows, router, browser, atau hosts file."),
                    ("HTTP/2", "Memakai HTTP/2 dari httpx untuk request deep scan yang kompatibel."),
                    ("Asset rusak", "Menulis detail asset gagal ke backup_report.txt jika tersedia, atau failed_assets.txt jika tidak ada backup report."),
                ]),
                ("☁  Akses Cloudflare", "yellow", [
                    ("Off", "Tidak mencoba bypass Cloudflare."),
                    ("Auto", "Mencoba request normal, lalu cloudscraper, lalu FlareSolverr jika tersedia."),
                    ("cloudscraper", "Memakai cloudscraper untuk halaman challenge Cloudflare ringan."),
                    ("FlareSolverr", "Memakai service FlareSolverr lokal untuk menyelesaikan halaman challenge berbasis browser."),
                    ("Endpoint", "Endpoint API bawaan: http://localhost:8191/v1."),
                    ("Session", "Reuse per domain menyimpan cookie dan user-agent untuk host yang sama. Bersihkan session saat cookie usang."),
                    ("Mode proxy", "Inherit proxy mengirim proxy aplikasi ke FlareSolverr. None membiarkan FlareSolverr memakai jalur jaringan sendiri."),
                ]),
                ("🎨  gallery-dl", "muted", [
                    ("Off", "Bawaan. Program tidak memanggil gallery-dl."),
                    ("Smart", "Memakai gallery-dl hanya untuk URL post, artwork, galeri, atau status yang mungkin cocok, bukan raw CDN image."),
                    ("Force", "Mode lanjut. Mengirim URL yang cocok ke gallery-dl meski bentuk URL belum pasti."),
                    ("Autentikasi", "Gunakan config gallery-dl untuk OAuth Pixiv, API key booru, atau extractor berbasis akun."),
                ]),
            ],
            "cyoa_mgr": [
                ("📤  Integrasi CYOA Manager", "accent", [
                    ("Tambah otomatis", "Aktifkan CYOA Manager di baris opsi untuk menambahkan JSON project yang berhasil secara otomatis."),
                    ("Tombol status", "Tombol CYOA Manager menunjukkan apakah integrasi sedang aktif."),
                    ("Path database", "Gunakan path library.sqlite3 khusus untuk instalasi portable atau tidak standar."),
                    ("Cek duplikat", "Program memeriksa entri file_path yang sudah ada sebelum menulis record pustaka baru."),
                    ("Preferensi viewer", "Menyimpan preferensi viewer agar CYOA Manager membuka project dengan viewer yang tepat."),
                ]),
                ("📦  Ekspor Batch", "muted", [
                    ("Pindai folder", "Mencari file JSON project yang lebih besar dari 1 KB di folder terpilih."),
                    ("Pilih file", "Mengizinkan pemilihan banyak file JSON project secara manual."),
                    ("Sesi terakhir", "Mengekspor project yang diunduh pada sesi terakhir."),
                    ("Hasil", "Menampilkan jumlah item ditambahkan, sudah ada, dan gagal."),
                ]),
            ],
            "cheat": [
                ("⚙  Detail Cheat Overlay", "accent", [
                    ("Polling", "Memeriksa setiap 500 ms sampai aplikasi Vue dan store tersedia."),
                    ("Vuex", "Menargetkan window.app.__vue__.$store.state.app untuk build ICC Plus lama."),
                    ("Pinia", "Menargetkan window.__pinia.state.value untuk build ICC Plus baru."),
                    ("Injeksi", "Menyisipkan overlay sebelum tag penutup body di folder viewer offline."),
                ]),
                ("🔧  Perubahan yang Tersedia", "muted", [
                    ("Atur poin", "Memperbarui nilai poin awal."),
                    ("Hapus syarat", "Menghapus array requirement dari row dan object."),
                    ("Pilihan tak terbatas", "Mengatur allowedChoices menjadi tak terbatas."),
                    ("Pilih semua", "Menandai semua object sebagai dipilih."),
                    ("Batalkan semua", "Menandai semua object sebagai tidak dipilih."),
                ]),
            ],
            "workflow": [
                ("⌨  Keyboard dan Alur Kerja", "accent", [
                    ("Enter", "Menambahkan URL aktif ke antrean."),
                    ("Handle seret", "Mengubah urutan item antrean."),
                    ("Buka Folder", "Membuka folder output yang dipilih."),
                    ("Pratinjau", "Menjalankan pemeriksaan URL tanpa memulai unduhan penuh."),
                    ("Serve", "Menjalankan server lokal untuk folder website yang dihasilkan."),
                ]),
                ("🔗  Jenis URL yang Didukung", "muted", [
                    ("HTTPS standar", "Mendukung banyak halaman CYOA dari Neocities, Netlify, Vercel, GitHub Pages, dan self-hosted."),
                    ("cyoa.cafe", "Menyelesaikan halaman CYOA berbasis iframe jika memungkinkan."),
                    ("archive.org", "Dapat membangun ulang URL asli dari pola arsip CYOA yang dikenal."),
                    ("cyoap_vue", "Mendukung project dengan dist/platform.json dan dist/nodes/list.json."),
                ]),
                ("📁  File Output", "muted", [
                    ("project.json", "Data project untuk output tertanam atau ZIP."),
                    ("audio/", "File audio lokal hasil unduhan."),
                    ("backup_report.txt", "Ringkasan file yang berhasil dan gagal."),
                    ("failed_images.txt", "URL gambar yang tersedia untuk retry."),
                    ("cyoa_downloader.log", "Log sesi lengkap yang ditulis ke folder output."),
                ]),
            ],
            "cookies": [
                ("🎵  Cookie yt-dlp", "green", [
                    ("Otomatis", "Login ke YouTube di browser, lalu biarkan yt-dlp membaca cookie browser secara otomatis."),
                    ("Urutan browser", "Chrome, Firefox, Edge, Brave, Chromium, lalu Safari."),
                    ("Ekspor manual", "Ekspor cookies.txt dengan ekstensi browser saat pembacaan cookie otomatis gagal."),
                    ("Kegagalan umum", "Cookie kedaluwarsa, video privat, video terhapus, dan kunci wilayah tetap dapat gagal."),
                ]),
                ("🎨  Autentikasi gallery-dl", "accent", [
                    ("OAuth Pixiv", "Jalankan gallery-dl oauth:pixiv, beri izin di browser, lalu simpan token di config gallery-dl."),
                    ("Danbooru", "Atur username dan API key di config gallery-dl."),
                    ("e621", "Atur username dan API key di config gallery-dl."),
                    ("Sankaku dan situs sejenis", "Atur username dan password hanya jika extractor membutuhkan akun."),
                    ("Lokasi config", "Windows memakai AppData Roaming. macOS dan Linux biasanya memakai ~/.config/gallery-dl/config.json."),
                ]),
            ],
        }

        CONTENT = CONTENT_EN if is_en else CONTENT_ID

        COLOR_MAP = {
            "accent": "#3b82f6",
            "muted": "#64748b",
            "green": "#34d399",
            "yellow": "#fbbf24",
            "red": "#f87171",
        }

        def _render(tab: str) -> None:
            for w in content_area.winfo_children():
                w.destroy()
            for section_title, color_key, items in CONTENT.get(tab, []):
                color = COLOR_MAP.get(color_key, p["muted"])
                sh = ctk.CTkFrame(content_area, fg_color=p["surface"], corner_radius=8)
                sh.pack(fill="x", padx=12, pady=(10, 2))
                ctk.CTkLabel(
                    sh,
                    text=section_title,
                    font=ctk.CTkFont("Segoe UI", 12, "bold"),
                    text_color=color,
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(10, 4))
                for feat, desc in items:
                    row = ctk.CTkFrame(content_area, fg_color=p["surface2"], corner_radius=6)
                    row.pack(fill="x", padx=12, pady=1)
                    row.grid_columnconfigure(1, weight=1)
                    ctk.CTkLabel(
                        row,
                        text=feat,
                        width=145,
                        anchor="w",
                        font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        text_color=p["fg"],
                    ).grid(row=0, column=0, padx=(12, 6), pady=6, sticky="w")
                    ctk.CTkLabel(
                        row,
                        text=desc,
                        anchor="w",
                        font=ctk.CTkFont("Segoe UI", 10),
                        text_color=p["muted"],
                        wraplength=520,
                        justify="left",
                    ).grid(row=0, column=1, padx=(0, 12), pady=6, sticky="ew")
                ctk.CTkFrame(content_area, height=4, fg_color="transparent").pack()

        tab_btns = {}

        def _select_tab(t: str) -> None:
            tab_var.set(t)
            _render(t)
            for tv, btn in tab_btns.items():
                btn.configure(
                    fg_color="#3b82f6" if tv == t else p["surface"],
                    text_color="#ffffff" if tv == t else p["muted"],
                )

        for val, label in TABS:
            btn = ctk.CTkButton(
                tab_frame,
                text=label,
                height=34,
                width=84,
                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                corner_radius=0,
                fg_color=p["surface"],
                hover_color=p["surface2"],
                text_color=p["muted"],
                command=lambda v=val: _select_tab(v),
            )
            btn.pack(side="left", padx=1)
            tab_btns[val] = btn

        available_tabs = dict(TABS)
        normalized_initial = "cookies" if initial_tab == "cookie" else initial_tab
        _select_tab(normalized_initial if normalized_initial in available_tabs else "download")

    def _manage_offline_viewers(self) -> None:
        """
        GUI popup to manage offline viewer ZIPs.
        Users can: add ZIP, see registered viewers, remove viewers.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox

        p   = self._p()
        win = ctk.CTkToplevel(self.root)
        win.title("Offline Viewers")
        win.geometry("680x460")
        win.grab_set()

        # ── Header ──────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(hdr, text="Offline Viewer Manager",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(side="left")
        ctk.CTkButton(hdr, text="↓ Check Update", width=110, height=30,
                      font=ctk.CTkFont("Segoe UI", 11),
                      fg_color=p["surface2"], hover_color="#065f46",
                      text_color="#6ee7b7",
                      command=lambda: _check_icc_update()).pack(side="right", padx=(0,6))
        ctk.CTkButton(hdr, text="+ Add ZIP", width=90, height=30,
                      font=ctk.CTkFont("Segoe UI", 11),
                      fg_color="#3b82f6", hover_color="#2563eb",
                      command=lambda: _add_viewer()).pack(side="right")

        ctk.CTkLabel(win,
                     text="Upload offline viewer ZIPs (e.g. ICCPlus offline release). "
                          "The script will automatically match viewers with downloaded CYOAs.",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], wraplength=640,
                     justify="left").pack(anchor="w", padx=16, pady=(4, 8))

        # ── Viewer list ──────────────────────────────────────────────────
        frame = ctk.CTkScrollableFrame(win, fg_color=p["bg"],
                                        scrollbar_button_color=p["surface2"])
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        status_var = ctk.StringVar(value="")
        ctk.CTkLabel(win, textvariable=status_var,
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color=p["accent"]).pack(pady=(0, 8))

        def _refresh_list():
            for w in frame.winfo_children():
                w.destroy()
            manifest = _load_viewers_manifest()
            if not manifest:
                ctk.CTkLabel(frame,
                              text="No offline viewers registered yet.\n"
                                   "Click '+ Add ZIP' to add one.",
                              font=ctk.CTkFont("Segoe UI", 11),
                              text_color=p["muted"]).pack(pady=20)
                return
            for i, (vid, meta) in enumerate(manifest.items()):
                bg = p["surface"] if i % 2 == 0 else p["bg"]
                row = ctk.CTkFrame(frame, fg_color=bg, corner_radius=4)
                row.pack(fill="x", padx=4, pady=1)

                # Icon + name + type
                vtype = meta.get("viewer_type", "custom")
                icon  = {"icc_plus":"⚡","icc":"📄","cyoap_vue":"🌿","custom":"📦"}.get(vtype,"📦")
                left  = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", fill="x", expand=True, padx=8, pady=6)
                ctk.CTkLabel(left, text=f"{icon} {meta.get('name', vid)}",
                              font=ctk.CTkFont("Segoe UI", 12, "bold"),
                              text_color=p["fg"], anchor="w").pack(anchor="w")
                ctk.CTkLabel(left,
                              text=f"type: {vtype}  ·  entry: {meta.get('entry_point','index.html')}  "
                                   f"·  {meta.get('zip_filename','')}",
                              font=ctk.CTkFont("Segoe UI", 10),
                              text_color=p["muted"], anchor="w").pack(anchor="w")
                if meta.get("description"):
                    ctk.CTkLabel(left, text=meta["description"],
                                  font=ctk.CTkFont("Segoe UI", 10, "italic"),
                                  text_color=p["muted2"], anchor="w").pack(anchor="w")

                # Remove button
                ctk.CTkButton(row, text="Hapus", width=60, height=26,
                               font=ctk.CTkFont("Segoe UI", 10),
                               fg_color="#7f1d1d", hover_color="#991b1b",
                               text_color="#fca5a5",
                               command=lambda v=vid: _remove(v)).pack(
                    side="right", padx=8, pady=6)

        def _add_viewer():
            """Register a local offline viewer ZIP/RAR from the GUI."""
            zip_path = filedialog.askopenfilename(
                parent=win,
                title="Select offline viewer ZIP",
                filetypes=[("Viewer archives", "*.zip *.rar"), ("ZIP files", "*.zip"), ("RAR files", "*.rar"), ("All files", "*.*")]
            )
            if not zip_path:
                return
            # Ask for name and type
            name_win = ctk.CTkToplevel(win)
            name_win.title("Info Viewer")
            name_win.geometry("400x260")
            name_win.grab_set()

            ctk.CTkLabel(name_win, text="Nama viewer:",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(14,2))
            name_var = ctk.StringVar(value=os.path.splitext(os.path.basename(zip_path))[0])
            ctk.CTkEntry(name_win, textvariable=name_var, width=350).pack(padx=16)

            ctk.CTkLabel(name_win, text="Tipe viewer:",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(10,2))
            type_var = ctk.StringVar(value="icc_plus")
            for t in ["icc_plus", "icc", "cyoap_vue", "custom"]:
                ctk.CTkRadioButton(name_win, text=t, variable=type_var, value=t,
                                    font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=24)

            ctk.CTkLabel(name_win, text="Description (optional):",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(8,2))
            desc_var = ctk.StringVar()
            ctk.CTkEntry(name_win, textvariable=desc_var, width=350).pack(padx=16)

            def _do_register():
                vid = register_offline_viewer(
                    zip_path,
                    name=name_var.get().strip() or os.path.basename(zip_path),
                    viewer_type=type_var.get(),
                    description=desc_var.get().strip(),
                )
                name_win.destroy()
                if vid:
                    status_var.set(f"✓ Viewer '{vid}' berhasil didaftarkan.")
                    _refresh_list()
                else:
                    messagebox.showerror("Error", "Failed to register viewer. Check log for details.",
                                         parent=win)

            ctk.CTkButton(name_win, text="Daftarkan",
                           fg_color="#3b82f6", hover_color="#2563eb",
                           command=_do_register).pack(pady=12)

        def _check_icc_update():
            """Check GitHub for latest ICCPlus release and offer to download."""
            import threading
            status_var.set("Checking GitHub for latest ICCPlus release…")

            def _do_check():
                try:
                    api = "https://api.github.com/repos/wahawa303/ICCPlus/releases/latest"
                    r   = fetch_response(api, timeout=8, extra_headers={"User-Agent": "CYOA-Downloader"})
                    if r is None or r.status_code != 200:
                        win.after(0, lambda: status_var.set(f"GitHub API: {r.status_code}"))
                        return
                    data  = r.json()
                    tag   = data.get("tag_name", "")
                    assets= data.get("assets", [])
                    # Look for local/offline ZIP asset
                    offline_asset = next(
                        (a for a in assets
                         if any(kw in a["name"].lower()
                                for kw in ["local", "offline", "standalone"])),
                        assets[0] if assets else None
                    )
                    if not offline_asset:
                        win.after(0, lambda: status_var.set("No downloadable asset found in latest release."))
                        return

                    asset_name = offline_asset["name"]
                    asset_url  = offline_asset["browser_download_url"]

                    # Check if this version is already registered
                    manifest = _load_viewers_manifest()
                    already  = any(tag in m.get("name","") or tag in vid
                                   for vid, m in manifest.items())

                    def _offer():
                        if already:
                            status_var.set(f"Already have {tag} registered.")
                            return
                        from tkinter import messagebox
                        if messagebox.askyesno(
                            "New ICCPlus Release",
                            f"Latest release: {tag}\nFile: {asset_name}\n\n"
                            f"Download and register? (~beberapa MB)",
                            parent=win
                        ):
                            _do_download(tag, asset_name, asset_url)

                    win.after(0, _offer)

                except Exception as e:
                    win.after(0, lambda: status_var.set(f"Update check failed: {e}"))

            def _do_download(tag, asset_name, asset_url):
                status_var.set(f"Downloading {asset_name}…")

                def _dl():
                    try:
                        os.makedirs(_VIEWERS_DIR, exist_ok=True)
                        dest = os.path.join(_VIEWERS_DIR, asset_name)
                        sess = _get_shared_session(use_cf=False)
                        with sess.get(asset_url, stream=True, timeout=30,
                                      headers={"User-Agent": "CYOA-Downloader"}) as r:
                            r.raise_for_status()
                            total = int(r.headers.get("content-length", 0))
                            done  = 0
                            with open(dest, "wb") as f:
                                for chunk in r.iter_content(65536):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    done += len(chunk)
                                    if total:
                                        pct = done * 100 // total
                                        win.after(0, lambda p=pct: status_var.set(
                                            f"Downloading {asset_name}… {p}%"))

                        vid = register_offline_viewer(
                            dest, name=f"ICCPlus {tag} (auto)", viewer_type="icc_plus"
                        )
                        win.after(0, lambda: (
                            status_var.set(f"✓ {asset_name} registered as '{vid}'."),
                            _refresh_list()
                        ))
                    except Exception as e:
                        win.after(0, lambda: status_var.set(f"Download failed: {e}"))

                threading.Thread(target=_dl, daemon=True).start()

            threading.Thread(target=_do_check, daemon=True).start()

        def _remove(vid: str):
            if messagebox.askyesno("Hapus Viewer",
                                    f"Hapus '{vid}' dari registry?\n(ZIP di-keep, tidak dihapus dari disk)",
                                    parent=win):
                unregister_offline_viewer(vid, delete_zip=False)
                status_var.set(f"Viewer '{vid}' dihapus.")
                _refresh_list()

        _refresh_list()

    def _toggle_server(self) -> None:
        if self._server_running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self) -> None:
        import customtkinter as ctk
        from tkinter import filedialog, messagebox
        import http.server, webbrowser, mimetypes

        # Pick folder to serve
        folder = filedialog.askdirectory(
            title="Select CYOA folder to serve",
            initialdir=self._outdir_var.get())
        if not folder:
            return

        # Do NOT reuse a fixed preview port.
        # Browser localStorage, Cache Storage, service workers, and some SPA
        # viewers are origin-scoped, so reusing localhost:8080 can replay the
        # previous CYOA even when HTTP cache headers are disabled.
        # The actual port is assigned by the OS when ThreadingHTTPServer binds
        # to port 0 below.

        # Register extra MIME types for CYOA assets
        _extra_mimes = {
            ".webp": "image/webp", ".avif": "image/avif",
            ".woff2": "font/woff2", ".woff": "font/woff",
            ".otf": "font/otf", ".ttf": "font/ttf",
            ".mjs": "application/javascript", ".cjs": "application/javascript",
            ".webm": "video/webm", ".mp4": "video/mp4",
            ".ogg": "audio/ogg", ".flac": "audio/flac",
            ".m4a": "audio/mp4", ".aac": "audio/aac",
            ".json": "application/json",
        }
        for ext, mt in _extra_mimes.items():
            mimetypes.add_type(mt, ext)

        # Compressible types for gzip
        _compressible = {
            "text/html", "text/css", "application/javascript",
            "application/json", "image/svg+xml", "text/plain",
        }

        class CYOAHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=folder, **kw)

            def log_message(self, fmt, *args):
                pass  # silence per-request logging, too noisy

            def end_headers(self):
                # CORS for cross-origin viewers
                self.send_header("Access-Control-Allow-Origin", "*")
                # Development/preview server: disable browser cache aggressively.
                # CYOA projects often reuse index.html, project.json, and asset names,
                # so caching can make the browser show a previous CYOA after a new run.
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                try:
                    from urllib.parse import urlparse as _urlparse
                    _p = _urlparse(self.path).path
                    if _p in ("", "/", "/index.html"):
                        self.send_header("Clear-Site-Data", '"cache", "storage"')
                except Exception:
                    pass
                # Keep-alive
                self.send_header("Connection", "keep-alive")
                super().end_headers()

            def do_GET(self):
                """Serve preview files and expose a cache/storage clear route."""
                import gzip as _gz, io as _io
                from urllib.parse import urlparse as _urlparse

                # Explicit browser-side clear route. This clears localStorage,
                # sessionStorage, Cache Storage, and service workers for the
                # current preview origin, then redirects to the CYOA root with a
                # fresh cache-busting URL. This fixes viewers that store the
                # previous project client-side rather than in normal HTTP cache.
                route_path = _urlparse(self.path).path
                if route_path == "/__clear_cache__":
                    stamp = str(int(time.time() * 1000))
                    html_text = f'''<!doctype html><meta charset="utf-8"><title>Clearing preview cache...</title>
<script>
(async function() {{
  try {{ localStorage.clear(); }} catch (e) {{}}
  try {{ sessionStorage.clear(); }} catch (e) {{}}
  try {{
    if ('caches' in window) {{
      const names = await caches.keys();
      await Promise.all(names.map(n => caches.delete(n)));
    }}
  }} catch (e) {{}}
  try {{
    if ('serviceWorker' in navigator) {{
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map(r => r.unregister()));
    }}
  }} catch (e) {{}}
  location.replace('/?cb={stamp}&preview={stamp}');
}})();
</script>
<p>Clearing preview cache...</p>'''
                    html = html_text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Clear-Site-Data", '"cache", "storage"')
                    self.end_headers()
                    self.wfile.write(html)
                    return

                # Check if client accepts gzip
                accept_enc = self.headers.get("Accept-Encoding", "")
                if "gzip" not in accept_enc:
                    return super().do_GET()
                # Only compress known text types
                path_lower = self.path.lower().split("?")[0]
                ct = mimetypes.guess_type(path_lower)[0] or ""
                if ct not in _compressible:
                    return super().do_GET()
                # Translate path
                path = self.translate_path(self.path)
                if not os.path.isfile(path):
                    return super().do_GET()
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                    buf = _io.BytesIO()
                    with _gz.GzipFile(fileobj=buf, mode="wb", compresslevel=4) as gz:
                        gz.write(raw)
                    compressed = buf.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(compressed)))
                    self.end_headers()
                    self.wfile.write(compressed)
                except Exception:
                    return super().do_GET()

            def do_OPTIONS(self):
                """Handle CORS preflight."""
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()

        try:
            # ThreadingHTTPServer handles multiple requests concurrently.
            # Bind to 127.0.0.1:0 so every preview gets a fresh local origin.
            # This avoids stale localStorage/service-worker state from an older
            # localhost:8080 preview.
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CYOAHandler)
            port = int(server.server_address[1])
            server.timeout = 0.5
            self._server_obj = server

            def _run():
                logger.info(f"[Server] Started: http://127.0.0.1:{port}")
                server.serve_forever()

            self._server_thread = threading.Thread(target=_run, daemon=True)
            self._server_thread.start()
            self._server_running = True

            # Update button
            self._srv_btn.configure(
                text=f"■ Stop Server :{port}",
                fg_color="#065f46",
                hover_color="#ef4444",
                text_color="#6ee7b7")

            self._set_status(f"Server: http://127.0.0.1:{port}")
            logger.info(f"[Server] Serving: {folder}")

            # Open the explicit clear route first. It clears browser storage for
            # this preview origin and then redirects to the project root with a
            # unique query string.
            stamp = int(time.time() * 1000)
            webbrowser.open(f"http://127.0.0.1:{port}/__clear_cache__?cb={stamp}")

        except Exception as e:
            messagebox.showerror("Server Error", str(e))

    def _stop_server(self) -> None:
        import customtkinter as ctk
        import threading as _th

        server_to_stop = self._server_obj
        self._server_obj     = None
        self._server_running = False

        # Update button immediately — don't wait for shutdown to complete
        p = self._p()
        self._srv_btn.configure(
            text="⚡ Serve",
            fg_color=p["surface2"],
            hover_color="#065f46",
            text_color="#6ee7b7",
        )
        self._set_status("Idle")

        # Shutdown in background so GUI stays responsive
        def _do_shutdown():
            if server_to_stop:
                try:
                    server_to_stop.shutdown()
                    server_to_stop.server_close()
                except Exception:
                    pass
            logger.info("[Server] Stopped")

        _th.Thread(target=_do_shutdown, daemon=True).start()






# ─────────────────────────────────────────────────────────────────
#  Core orchestration
# ─────────────────────────────────────────────────────────────────

def _finalize_site_folder(site_folder: str, file_name: str, zip_output: bool) -> None:
    """Zip site folder if requested, then delete the folder."""
    if zip_output:
        zip_name = file_name + "_site.zip"
        logger.info(f"Zipping → {zip_name}")
        zip_temp_folder(site_folder, zip_name=zip_name)
        shutil.rmtree(site_folder, ignore_errors=True)
        logger.info(f"Folder {site_folder} deleted after zipping.")
    else:
        logger.info(f"Website folder kept: {site_folder}")


def run_download(
    url: str,
    file_name: str = "",
    zip_output: bool = False,
    both_output: bool = False,
    website_output: bool = False,
    website_zip_output: bool = True,
    pure_website: bool = False,
    download_fonts: bool = False,
    show_font_analysis: bool = True,
    output_dir: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    engine_mode: str = "standard",
    cyoa_mgr_enabled: bool = False,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "auto_fallback",
    analysis_only: bool = False,
) -> None:
    """
    Main download orchestrator.

    pure_website=True: skip project.json search entirely — just download
    the viewer HTML/CSS/JS/assets. Useful for custom-format sites like
    lewd_horizon that don't use a standard ICC project file.
    """
    global wait_time, _LAST_PREVIEW_FOLDER
    _LAST_PREVIEW_FOLDER = None
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode or _load_settings().get("ai_mode", "auto_fallback"))
    ai_budget = AIUsageBudget()
    ai_available = _ai_is_available(ai_api_key, ai_provider) and ai_mode != "off"

    # ── Disk space check (non-critical, just warn) ─────────────────────────
    try:
        target = os.path.abspath(output_dir) if output_dir else os.getcwd()
        if output_dir:
            os.makedirs(target, exist_ok=True)
        if hasattr(os, "statvfs"):
            st = os.statvfs(target)
            free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
            if free_mb < 100:
                logger.warning(
                    f"Disk hampir penuh! Sisa: {free_mb:.0f} MB. "
                    f"Download will continue but may fail midway."
                )
    except Exception:
        pass

    if not file_name:
        file_name = _build_output_name(url)

    # ── archive.org CYOA catalog URL → redirect to original site ───────────
    # CYOA Manager catalog links:
    # https://archive.org/download/CYOAZipArchive/Name.[date].https~~~site.com~path.zip
    _archive_m = _ARCHIVE_ORG_CYOA_RE.search(url)
    if _archive_m:
        zip_filename = _archive_m.group(1)
        original_site = _extract_website_from_archive_zip_name(zip_filename)
        if original_site:
            logger.info(
                f"archive.org catalog URL → using original site: {original_site}"
            )
            url = original_site
            if not file_name or file_name == "downloaded_cyoa":
                file_name = _build_output_name(original_site)
    if not file_name:
        file_name = "downloaded_cyoa"
    file_name = clean_url_path_component(file_name)

    _RUN_DOWNLOAD_LOCK.acquire()
    original_dir = os.getcwd()
    try:
        if output_dir:
            output_dir = os.path.abspath(output_dir)
            os.makedirs(output_dir, exist_ok=True)
            os.chdir(output_dir)
        else:
            output_dir = os.getcwd()

        # ── Pure website mode: skip project search ─────────────────
        if pure_website:
            site_folder = _unique_folder(file_name)
            logger.info(f"Pure website download (no project search) → {site_folder}/")
            prepare_clean_output_folder(site_folder)
            viewer = WebsiteDownloader(url, site_folder, max_workers=max_workers, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
            viewer.download()
            viewer.localize_existing_text_assets()
            # No backup_report since there's no project payload
            if download_fonts:
                # Only scan viewer HTML for fonts (no project.json to scan)
                viewer_html_pu = get_source(url) or ""
                _download_fonts_into_folder("", url, site_folder, html_source=viewer_html_pu)
            _LAST_PREVIEW_FOLDER = os.path.abspath(site_folder) if not website_zip_output else None
            _finalize_site_folder(site_folder, file_name, website_zip_output)
            logger.info("Pure website download complete.")
            return

        if website_output and engine_mode in {"cyoap_vue", "auto"}:
            logger.info("Phase 0/4: probing cyoap_vue dist/ structure…")
            site_folder = _unique_folder(file_name)
            try:
                if try_download_cyoap_vue_site(
                    url,
                    site_folder,
                    website_zip_output=website_zip_output,
                    max_workers=max_workers,
                ):
                    return
                if engine_mode == "cyoap_vue":
                    raise RuntimeError("cyoap_vue mode selected, but dist/platform.json + dist/nodes/list.json were not found.")
                logger.info("cyoap_vue probe: no dist/platform.json + dist/nodes/list.json pair found; falling back.")
            except Exception as e:
                if engine_mode == "cyoap_vue":
                    raise
                logger.warning(f"cyoap_vue auto probe failed, falling back to standard resolver: {e}")

        logger.info("Phase 1/4: resolving project source…")
        project_source, project_url = get_project_source(url, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
        if not project_source:
            # ── AI viewer analysis (diagnostic) ────────────────────────
            ai_hint = ""
            if ai_available and _ai_mode_allows("diagnostics", ai_mode):
                try:
                    _diag_resp = fetch_response(url, timeout=15, extra_headers={"User-Agent": "Mozilla/5.0"})
                    _diag_html = _safe_response_text(_diag_resp) if _diag_resp is not None else ""
                    analysis = _ai_analyze_viewer_logic(
                        _diag_html, {}, url, api_key=ai_api_key, provider=ai_provider,
                        ai_mode=ai_mode, budget_obj=ai_budget)
                    if analysis:
                        viewer_type = analysis.get("viewer_type", "unknown")
                        data_src = analysis.get("data_source", "unknown")
                        suggestions = analysis.get("suggestions", [])
                        logger.info(f"[AI analysis] viewer={viewer_type}, data={data_src}")
                        for s in suggestions:
                            logger.info(f"  → {s}")
                        ai_hint = (
                            f"\n\nAI analysis: viewer_type={viewer_type}, "
                            f"data_source={data_src}\n"
                            + "\n".join(f"  → {s}" for s in suggestions[:5])
                        )
                except Exception:
                    pass

            raise RuntimeError(
                "Could not resolve project data (project.json / project.txt / embedded JS / zip payload).\n"
                "If this site uses a custom viewer without a standard project file,\n"
                "try using mode: Website ZIP/Folder or --website flag to download\n"
                "the viewer HTML/CSS/JS directly without needing a project file."
                + ai_hint
            )

        cleaned = normalize_project_payload_text(project_source) or extract_json_like_block(project_source) or project_source
        logger.info("Phase 2/4: project source resolved.")

        # ── Feature 4: Extract metadata ────────────────────────────────────
        try:
            _meta_obj = json.loads(cleaned) if cleaned.strip().startswith("{") else {}
            _meta_app = _meta_obj.get("app", _meta_obj)
            _rows  = _meta_app.get("rows", [])
            _bp    = _meta_app.get("backpack", [])
            _title = (
                _meta_app.get("title") or
                _meta_app.get("name") or
                _meta_app.get("projectTitle") or ""
            )
            _author = _meta_app.get("author") or _meta_app.get("authorName") or ""
            _meta_img_count = sum(
                1 for r in _rows
                for obj in r.get("objects", [])
                if obj.get("image")
            )
            _metadata = {
                "title":        _title,
                "author":       _author,
                "source_url":   url,
                "project_url":  project_url,
                "rows":         len(_rows),
                "objects_total": sum(len(r.get("objects", [])) for r in _rows),
                "backpack_slots": len(_bp),
                "images_referenced": _meta_img_count,
                "downloaded_at": __import__("datetime").datetime.now().isoformat(),
            }
            logger.info(
                f"Metadata: title={_title!r} rows={len(_rows)} "
                f"objects={_metadata['objects_total']} images={_meta_img_count}"
            )
        except Exception:
            _metadata = {"source_url": url, "project_url": project_url}


        if not file_name:
            file_name = clean_url_path_component(get_first_folder_from_url(project_url))
        if not file_name:
            file_name = clean_url_path_component(get_first_subdomain(project_url))
        if not file_name:
            file_name = "downloaded_cyoa"
        file_name = clean_url_path_component(file_name)

        base_url = strip_document_from_url(project_url)

        # Fetch viewer HTML once — reused for font scanning
        viewer_html: str = get_source(url) or ""

        if show_font_analysis:
            analyse_fonts(cleaned, base_url, html_source=viewer_html)
            if analysis_only:
                logger.info("Analysis-only mode complete; no download output written.")
                return

        # ── Full website mode ───────────────────────────────────────
        if website_output:
            site_folder = _unique_folder(file_name)
            logger.info(f"Downloading full website → {site_folder}/")
            prepare_clean_output_folder(site_folder)

            viewer = WebsiteDownloader(url, site_folder, max_workers=max_workers, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
            viewer.download()

            working = cleaned
            if download_fonts:
                # In website mode, WebsiteDownloader already downloads fonts from CSS/HTML.
                # We only scan project.json here (html_source="" avoids re-downloading
                # viewer HTML fonts that WebsiteDownloader already handled).
                working = _download_fonts_into_folder(
                    working, base_url, site_folder, html_source=""
                )

            tmp = create_random_temp_folder()
            try:
                _, dl_result, _pi_urls = process_images(
                    working, base_url,
                    embed=False, download=True,
                    temp_folder=tmp, wait_time=wait_time, max_workers=max_workers,
                    output_dir=output_dir, source_url=url,
                )
                img_src = os.path.join(tmp, "images")
                img_dst = os.path.join(site_folder, "images")
                if os.path.isdir(img_src):
                    if os.path.isdir(img_dst):
                        shutil.rmtree(img_dst)
                    shutil.copytree(img_src, img_dst)
                # Also move audio folder if present
                audio_src = os.path.join(tmp, "audio")
                if os.path.isdir(audio_src):
                    audio_dst = os.path.join(site_folder, "audio")
                    if os.path.isdir(audio_dst):
                        shutil.rmtree(audio_dst)
                    shutil.copytree(audio_src, audio_dst)

                # Save project_original.json only when URLs differ from raw
                if dl_result != cleaned:
                    save_string_to_file(cleaned, "project_original.json", site_folder)
                    logger.info("Saved: project_original.json (raw URLs before localization)")
                viewer.write_project_payload(project_url, dl_result)

                # Re-scan downloaded viewer files so fonts/images/scripts referenced
                # outside project.json (e.g. in loading.css, app.css) are also localized.
                viewer.localize_existing_text_assets()

                # Deep scan: download any assets referenced in JS/CSS bundles
                # that were not referenced in project.json IMAGE_FIELDS
                _deep_scan_and_download_assets(
                    folder=site_folder,
                    base_url=base_url,
                    output_dir=output_dir,
                    ai_api_key=ai_api_key,
                    ai_provider=ai_provider,
                    ai_mode=ai_mode,
                    ai_budget=ai_budget,
                    skip_urls=_pi_urls,
                )
            finally:
                delete_temp_folder(tmp)

            viewer.write_manifest(project_url=project_url)
            # Feature 6: integrity check
            integrity = viewer.validate_integrity()
            if integrity["missing"]:
                try:
                    report_path = os.path.join(site_folder, "backup_report.txt")
                    with open(report_path, "a", encoding="utf-8") as _rf:
                        _rf.write("\n" + "="*60 + "\n")
                        _rf.write("INTEGRITY CHECK — MISSING LOCAL REFS\n")
                        _rf.write("="*60 + "\n")
                        for miss in integrity["missing"]:
                            _rf.write(f"  {miss}\n")
                except Exception:
                    pass
            _LAST_PREVIEW_FOLDER = os.path.abspath(site_folder) if not website_zip_output else None
            _finalize_site_folder(site_folder, file_name, website_zip_output)
            logger.info("Website download complete.")
            return

        # ── Normal modes ────────────────────────────────────────────
        embed_images = not zip_output or both_output
        need_download = zip_output or both_output
        output_mode_str = (
            "both" if both_output else ("zip" if zip_output else "embed")
        )
        site_folder_local = ""  # only set in website mode above

        # Detect an offline viewer before image processing. Embed-only mode normally
        # does not write image files, but offline viewers need images/audio on disk.
        _viewer_meta_normal = None
        try:
            _viewer_meta_normal = get_viewer_for_site(viewer_html or "", mode=output_mode_str)
            if _viewer_meta_normal and not need_download:
                need_download = True
                logger.info("Offline viewer detected: enabling disk asset download for playable viewer output.")
        except Exception as _vm_e:
            logger.debug(f"Offline viewer pre-check skipped: {_vm_e}")

        tmp = None
        if need_download:
            tmp = create_random_temp_folder()

        working = cleaned
        if download_fonts and need_download and tmp:
            # In zip/embed mode, also scan viewer HTML for fonts
            working = _download_fonts_into_folder(
                working, base_url, tmp, html_source=viewer_html
            )

        embed_result, dl_result, _pi_urls = process_images(
            working, base_url,
            embed=embed_images, download=need_download,
            temp_folder=tmp, wait_time=wait_time, max_workers=max_workers,
            output_dir=output_dir, source_url=url,
            site_folder=site_folder_local,
        )

        if embed_images or both_output:
            has_edits_embed = (embed_result != cleaned)
            # Warn if output file will be very large (base64 inflates ~33%)
            size_mb = len(embed_result.encode("utf-8")) / (1024 * 1024)
            if size_mb > 50:
                logger.warning(
                    f"Output file besar: {size_mb:.0f} MB ({file_name}.json). "
                    f"This file may be slow to open in a browser. "
                    f"Consider ZIP mode for projects with many images."
                )
            if has_edits_embed:
                save_string_to_file(cleaned, file_name + "_original.json")
                logger.info(f"Saved: {file_name}_original.json (raw URLs)")
            save_string_to_file(embed_result, file_name + ".json")
            logger.info(f"Saved: {file_name}.json ({size_mb:.1f} MB, {'localized' if has_edits_embed else 'no URL changes'})")

            # ── Copy audio/ from temp to output_dir ───────────────────────
            # dl_result has "audio/ID.mp3" paths → audio files must be
            # alongside the .json so the viewer can load them.
            if tmp:
                _tmp_audio = os.path.join(tmp, "audio")
                if os.path.isdir(_tmp_audio):
                    _out_audio = os.path.join(output_dir or os.getcwd(), "audio")
                    _n = _copytree_merge_safe(_tmp_audio, _out_audio, label="audio")
                    logger.info(f"Saved/merged: audio/ ({_n} file(s))")

            # ── CYOA Manager integration ───────────────────────────────────
            # Only runs if user has enabled "→ CYOA Mgr" checkbox
            if cyoa_mgr_enabled:
                _cm_json_path = os.path.join(output_dir or os.getcwd(), file_name + ".json")
                _s  = _load_settings()
                _custom_db = _s.get("cyoa_mgr_db_path", "").strip()
                _cm_db = (_custom_db if _custom_db and os.path.exists(_custom_db)
                          else _find_cyoa_manager_db())
                if _cm_db:
                    add_to_cyoa_manager(
                        project_json_path=_cm_json_path,
                        name=file_name,
                        source_url=url,
                        viewer_preference=_cyoa_manager_viewer_pref(output_mode_str),
                        db_path=_cm_db,
                    )
        if both_output or not embed_images:
            has_edits_zip = (dl_result != cleaned)
            if has_edits_zip:
                save_string_to_file(cleaned, "project_original.json", tmp)
            save_string_to_file(dl_result, "project.json", tmp)
            logger.info(f"Saving: {file_name}.zip ({'with project_original.json' if has_edits_zip else 'no URL changes'})")
            zip_temp_folder(tmp, zip_name=file_name + ".zip")
            # Keep tmp until after offline viewer injection; it contains images/audio.

        # ── Feature 4: Save metadata.json ─────────────────────────────────
        try:
            meta_path = file_name + "_metadata.json"
            with open(meta_path, "w", encoding="utf-8") as _mf:
                json.dump(_metadata, _mf, indent=2, ensure_ascii=False)
            logger.info(f"Saved: {meta_path} (metadata)")
        except Exception as _me:
            logger.warning(f"Could not save metadata: {_me}")

        # ── Offline Viewer: apply registered viewer if available ────────────
        try:
            _viewer_meta = _viewer_meta_normal
            if not _viewer_meta:
                _page_html = ""
                try:
                    _rp = fetch_response(url, timeout=8, extra_headers={"User-Agent": "Mozilla/5.0"})
                    if _rp is not None:
                        _page_html = _safe_response_text(_rp)
                except Exception as e:
                    logger.debug(f"Offline viewer page fetch skipped: {e}")
                _viewer_meta = get_viewer_for_site(_page_html, mode=output_mode_str)
            if _viewer_meta:
                # Pass temp image/audio folders directly into the injected viewer.
                # Do not copy them to output_dir roots; that can delete/overwrite folders
                # from other projects.
                _offline_asset_sources: Dict[str, str] = {}
                if tmp and os.path.isdir(tmp):
                    for _asset_dir_name in ("images", "audio"):
                        _src = os.path.join(tmp, _asset_dir_name)
                        if os.path.isdir(_src):
                            _offline_asset_sources[_asset_dir_name] = _src
                # Always use dl_result (URLs kept as-is) for offline viewer injection.
                # embed_result has images as base64 → injecting it into app.js would
                # make the file hundreds of MB. The viewer loads images from the
                # images/ folder; we do NOT need base64 for the offline viewer.
                _viewer_out = _apply_offline_viewer(
                    output_dir=output_dir,
                    project_json_str=dl_result,
                    viewer_meta=_viewer_meta,
                    file_name=file_name,
                    asset_source_dirs=_offline_asset_sources,
                )
                if _viewer_out:
                    logger.info(
                        f"Offline viewer: {_viewer_meta.get('name','')} → "
                        f"{os.path.relpath(_viewer_out, output_dir)}"
                    )
        except Exception as _ov_e:
            logger.debug(f"Offline viewer step skipped: {_ov_e}")

        # ── Feature 5: Post-download validation ────────────────────────────
        try:
            _out_path = file_name + ".json"
            if os.path.exists(_out_path):
                _out_size = os.path.getsize(_out_path)
                if _out_size < 10:
                    logger.error(f"Validation FAIL: {_out_path} — file terlalu kecil ({_out_size} bytes), kemungkinan corrupt")
                else:
                    with open(_out_path, encoding="utf-8", errors="ignore") as _vf:
                        _sample = _vf.read(256)
                    if not (_sample.lstrip().startswith("{") or _sample.lstrip().startswith("[")):
                        logger.error(f"Validation FAIL: {_out_path} — bukan JSON valid (bisa jadi HTML error page)")
                    else:
                        try:
                            with open(_out_path, encoding="utf-8", errors="ignore") as _vf2:
                                _out_text = _vf2.read()
                            _vobj = json.loads(_out_text)
                            # Count referenced images vs actual base64 in file
                            _ref_count  = _out_text.count('"image":"') + _out_text.count('"image": "')
                            _b64_count  = _out_text.count("data:image/")
                            _url_count  = _ref_count - _b64_count
                            logger.info(
                                f"Validation OK: {_out_path} — "
                                f"{_ref_count} image refs, "
                                f"{_b64_count} base64, "
                                f"{_url_count} URL remaining"
                            )
                            if _url_count > 0 and embed_images:
                                logger.warning(
                                    f"Validation WARN: {_url_count} gambar masih berupa URL (bukan base64) — "
                                    f"may have failed to download. Check failed_images.txt"
                                )
                        except json.JSONDecodeError:
                            logger.error(f"Validation FAIL: {_out_path} — JSON parse error")
        except Exception as _ve:
            logger.warning(f"Validation error (non-critical): {_ve}")

        if tmp and os.path.isdir(tmp):
            delete_temp_folder(tmp)

    finally:
        try:
            os.chdir(original_dir)
        finally:
            _RUN_DOWNLOAD_LOCK.release()

    logger.info("Download successful.")


# ─────────────────────────────────────────────────────────────────
#  Image processing  (parallel + all fields)
# ─────────────────────────────────────────────────────────────────

def _make_placeholder_svg(label: str = "") -> bytes:
    """
    Return a minimal SVG placeholder image for use when an image fails to download.
    Shows a grey box with a broken-image icon and the original filename.
    """
    safe_label = (label[:40] + "…") if len(label) > 40 else label
    # Escape XML special chars
    safe_label = safe_label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
        '<rect width="320" height="200" fill="#2a2a2a" rx="6"/>'
        '<line x1="130" y1="70" x2="190" y2="130" stroke="#888" stroke-width="2"/>'
        '<line x1="190" y1="70" x2="130" y2="130" stroke="#888" stroke-width="2"/>'
        '<rect x="120" y="60" width="80" height="80" fill="none" stroke="#666" stroke-width="2" rx="4"/>'
        f'<text x="160" y="165" font-family="monospace" font-size="11" fill="#aaa" '
        f'text-anchor="middle">{safe_label}</text>'
        '</svg>'
    )
    return svg.encode("utf-8")


_PLACEHOLDER_DATA_URI = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(_make_placeholder_svg("[image unavailable]")).decode()
)


def _deep_scan_project_assets(
    project_str: str,
    base_url: str,
) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Parse project.json as JSON and walk the entire object tree to find ALL
    image, audio, and YouTube URLs — including nested structures that the
    simple field-name regex cannot reach.

    Handles the ICC Plus v2.9.1 audio architecture:
      • soundEffects[].audio  → direct audio URL (not a top-level field)
      • bgmId + useAudioURL   → bgmId is a URL only when sibling useAudioURL=true;
                                 otherwise bgmId is a YouTube video ID

    Returns:
        (image_paths, audio_paths, youtube_ids)
        where each is a set of raw strings from the JSON (relative or absolute).
    """
    image_paths:   Set[str] = set()
    audio_paths:   Set[str] = set()
    youtube_ids:   Set[str] = set()

    image_keys = {f.lower() for f in IMAGE_FIELDS}
    audio_keys = {f.lower() for f in AUDIO_FIELDS}

    def _is_data_uri(v: str) -> bool:
        return v.startswith("data:")

    def _looks_like_audio_url(v: str) -> bool:
        """True if value looks like a downloadable audio file URL/path."""
        if _YOUTUBE_URL_RE.search(v):
            return False   # YouTube URL handled separately
        path = urlparse(v).path.lower()
        ext = os.path.splitext(path)[1]
        return ext in AUDIO_EXTENSIONS or v.startswith(("http://", "https://")) and not _YOUTUBE_ID_RE.match(v)

    def _walk(obj, parent_key: str = "", siblings: Optional[Dict] = None) -> None:
        """Recursively walk any JSON value."""
        if obj is None:
            return
        if isinstance(obj, list):
            for item in obj:
                _walk(item)
            return
        if isinstance(obj, dict):
            use_audio_url = bool(obj.get("useAudioURL", False))

            for key, value in obj.items():
                key_lower = key.lower()

                if isinstance(value, str):
                    v = value.strip()
                    if not v or _is_data_uri(v):
                        continue

                    # ── Image fields ─────────────────────────────────────
                    if key_lower in image_keys:
                        image_paths.add(v)

                    # ── bgmId: context-dependent ──────────────────────────
                    elif key_lower == "bgmid":
                        if use_audio_url:
                            # bgmId is a direct audio URL
                            if _YOUTUBE_URL_RE.search(v):
                                youtube_ids.add(v)
                            elif _SOUNDCLOUD_URL_RE.search(v):
                                youtube_ids.add(v)   # yt-dlp handles SoundCloud too
                            else:
                                audio_paths.add(v)
                        else:
                            # bgmId is a YouTube video ID or SoundCloud URL
                            if _YOUTUBE_URL_RE.search(v) or _SOUNDCLOUD_URL_RE.search(v):
                                youtube_ids.add(v)
                            elif _YOUTUBE_ID_RE.match(v) and v:
                                youtube_ids.add(f"https://www.youtube.com/watch?v={v}")
                            # else: unknown format, skip

                    # ── Simple audio fields ───────────────────────────────
                    elif key_lower in audio_keys:
                        if _YOUTUBE_URL_RE.search(v) or _SOUNDCLOUD_URL_RE.search(v):
                            youtube_ids.add(v)
                        else:
                            audio_paths.add(v)

                    # ── Catch any remaining URL-looking values ─────────────
                    # (e.g. custom viewer fields not in our lists)
                    elif v.startswith(("http://", "https://")):
                        path_part = urlparse(v).path.lower()
                        ext = os.path.splitext(path_part)[1]
                        if ext in IMAGE_EXTENSIONS:
                            image_paths.add(v)
                        elif ext in AUDIO_EXTENSIONS:
                            audio_paths.add(v)

                    # ── Relative paths with asset extensions ──────────────
                    # Catch "images/hero.png", "audio/theme.mp3" etc.
                    # that aren't in known field lists but ARE valid paths.
                    elif '/' in v and not v.startswith(('#', 'javascript:')):
                        ext = os.path.splitext(v.split('?')[0])[1].lower()
                        if ext in IMAGE_EXTENSIONS:
                            image_paths.add(v)
                        elif ext in AUDIO_EXTENSIONS:
                            audio_paths.add(v)

                    # ── HTML <img> embedded in text/description fields ────
                    # CYOA creators put HTML in choice text, descriptions,
                    # titles, etc. with inline <img src="..."> tags.
                    _img_refs = re.findall(
                        r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']',
                        v, re.IGNORECASE
                    )
                    for img_url in _img_refs:
                        if img_url and not img_url.startswith('data:'):
                            image_paths.add(img_url)

                    # ── Markdown image syntax ─────────────────────────────
                    # ![alt text](image_url) — used in some custom viewers
                    for md_match in re.finditer(
                        r'!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|webp|gif|svg|avif))\)',
                        v, re.IGNORECASE
                    ):
                        image_paths.add(md_match.group(1))

                    # ── CSS url() in inline style values ──────────────────
                    for css_match in re.finditer(
                        r'url\(["\']?([^"\')\s]+\.(?:png|jpg|jpeg|webp|gif|svg|avif|mp3|ogg|wav|woff2?|ttf))["\']?\)',
                        v, re.IGNORECASE
                    ):
                        css_url = css_match.group(1)
                        if not css_url.startswith('data:'):
                            ext = os.path.splitext(css_url.split('?')[0])[1].lower()
                            if ext in IMAGE_EXTENSIONS:
                                image_paths.add(css_url)
                            elif ext in AUDIO_EXTENSIONS:
                                audio_paths.add(css_url)

                elif isinstance(value, list):
                    # ── bgmList / playlist: list of YouTube IDs or audio URLs ──
                    if key_lower in BGMLIST_FIELDS:
                        for item in value:
                            if isinstance(item, str) and item.strip():
                                v2 = item.strip()
                                if _YOUTUBE_URL_RE.search(v2) or _SOUNDCLOUD_URL_RE.search(v2):
                                    youtube_ids.add(v2)
                                elif _YOUTUBE_ID_RE.match(v2):
                                    youtube_ids.add(f"https://www.youtube.com/watch?v={v2}")
                                elif any(v2.endswith(e) for e in AUDIO_EXTENSIONS):
                                    audio_paths.add(v2)
                            elif isinstance(item, dict):
                                _walk(item, parent_key=key)
                    else:
                        _walk(value, parent_key=key)

                elif isinstance(value, dict):
                    _walk(value, parent_key=key)
            return

        # scalar non-string — nothing to do
        return

    # Try JSON parse first for accuracy
    try:
        obj = json.loads(project_str)
        _walk(obj)
        logger.info(
            f"Deep JSON scan: {len(image_paths)} image(s), "
            f"{len(audio_paths)} direct audio file(s), "
            f"{len(youtube_ids)} YouTube reference(s)."
        )
        return image_paths, audio_paths, youtube_ids
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: already-minified or slightly malformed JSON — use regex scan
    # (the existing process_images regex still handles this path)
    logger.debug("Deep JSON scan: JSON parse failed, falling back to regex scanner.")
    return image_paths, audio_paths, youtube_ids


def _write_failed_images_log(
    failed: List[Dict[str, str]],
    output_dir: str,
    source_url: str = "",
) -> None:
    """
    Append failed image entries to failed_images.txt.
    Uses APPEND mode so batch downloads accumulate instead of overwriting.
    Failed images keep their original URL in the project JSON.
    """
    if not failed:
        return
    target   = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path = os.path.join(target, "failed_images.txt")
    is_new   = not os.path.exists(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# Failed image downloads\n")
            f.write("# Note: Failed images keep their original external URL in the project JSON.\n")
            f.write("#       They will load normally when the original site is online.\n\n")
        f.write(f"# --- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        if source_url:
            f.write(f"# Source CYOA : {source_url}\n")
        f.write(f"# Count       : {len(failed)}\n")
        for item in failed:
            f.write(f"{item['url']}\t{item.get('error', '')}\n")
        f.write("\n")
    logger.warning(f"Failed images log: {log_path}")


def _write_youtube_skip_log(
    items: List[str],
    output_dir: str,
    source_url: str = "",
) -> None:
    """
    Append YouTube URLs (with source CYOA) to skipped_youtube_audio.txt.

    Uses APPEND mode — batch downloads accumulate entries instead of
    overwriting each other. The header/instructions block is written only
    once when the file does not yet exist.
    """
    if not items:
        return
    target   = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path = os.path.join(target, "skipped_youtube_audio.txt")
    is_new   = not os.path.exists(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        # Write explanatory header only on first creation
        if is_new:
            f.write("# Skipped YouTube audio URLs\n")
            f.write("# ============================================================\n")
            f.write("# WHY these cannot be made offline:\n")
            f.write("#   YouTube ToS prohibits downloading streams.\n")
            f.write("#   Streams use signed time-limited URLs (DASH/HLS) — no static file.\n")
            f.write("#   Old ICC viewer (Vue) creates YT.Player via JavaScript — no static\n")
            f.write("#   <iframe> in HTML, so our offline placeholder cannot replace it.\n")
            f.write("#\n")
            f.write("# WORKAROUND (manual, personal archival only):\n")
            f.write("#   1. pip install yt-dlp\n")
            f.write("#   2. yt-dlp -x --audio-format mp3 <youtube_url>\n")
            f.write("#   3. Place .mp3 in: <output_folder>/audio/\n")
            f.write("#   4. Edit project.json: bgmId -> 'audio/filename.mp3', useAudioURL -> true\n")
            f.write("#   Respect copyright — for personal use only.\n")
            f.write("# ============================================================\n\n")

        # Per-CYOA section — appended each time
        f.write(f"# --- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        if source_url:
            f.write(f"# Source CYOA : {source_url}\n")
        f.write(f"# Count       : {len(items)}\n")
        for url in items:
            f.write(url + "\n")
        f.write("\n")

    logger.warning(
        f"{len(items)} YouTube audio URL(s) kept as external links (cannot go offline). "
        f"See: {log_path}"
    )


def _find_ffmpeg() -> Optional[str]:
    """
    Find ffmpeg executable directory.
    Returns directory containing ffmpeg (for yt-dlp ffmpeg_location param),
    or None — in which case yt-dlp will try its own PATH search.
    """
    import shutil as _sh

    # 1. PATH check via shutil.which (works in most cases)
    exe = _sh.which("ffmpeg")
    if exe:
        return str(pathlib.Path(exe).parent)

    # 2. Try running ffmpeg directly — covers cases where PATH in os.environ
    #    is stale (Python launched before winget updated PATH in registry)
    try:
        import subprocess as _sp
        r = _sp.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0:
            # ffmpeg works! find its actual path via 'where ffmpeg' (Windows)
            w = _sp.run(["where", "ffmpeg"], capture_output=True, timeout=5, text=True)
            if w.returncode == 0:
                found_path = w.stdout.strip().splitlines()[0].strip()
                if found_path:
                    return str(pathlib.Path(found_path).parent)
            return ""   # Works but can't determine path — let yt-dlp handle it
    except Exception:
        pass

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        home  = os.environ.get("USERPROFILE", str(pathlib.Path.home()))

        # 3. Read user PATH from Windows registry (updated by winget/installers
        #    even if current process's os.environ is stale)
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment", 0, winreg.KEY_READ
            ) as key:
                reg_path, _ = winreg.QueryValueEx(key, "Path")
            for reg_dir in reg_path.split(";"):
                reg_dir = reg_dir.strip().strip('"')
                if reg_dir and (pathlib.Path(reg_dir) / "ffmpeg.exe").exists():
                    logger.debug(f"ffmpeg found (registry PATH): {reg_dir}")
                    return reg_dir
        except Exception:
            pass

        # Also check SYSTEM PATH from registry
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ
            ) as key:
                sys_path, _ = winreg.QueryValueEx(key, "Path")
            for reg_dir in sys_path.split(";"):
                reg_dir = reg_dir.strip().strip('"').replace("%SystemRoot%",
                    os.environ.get("SystemRoot", r"C:\Windows"))
                if reg_dir and (pathlib.Path(reg_dir) / "ffmpeg.exe").exists():
                    logger.debug(f"ffmpeg found (SYSTEM registry PATH): {reg_dir}")
                    return reg_dir
        except Exception:
            pass

        # 4. winget packages — RECURSIVE scan
        #    Gyan.FFmpeg nests: Packages/Gyan.FFmpeg_.../ffmpeg-8.1-full_build/bin/ffmpeg.exe
        if local:
            winget_base = pathlib.Path(local) / "Microsoft" / "WinGet" / "Packages"
            if winget_base.exists():
                for ffexe in sorted(winget_base.rglob("ffmpeg.exe")):
                    if ffexe.is_file():
                        logger.debug(f"ffmpeg found (winget): {ffexe.parent}")
                        return str(ffexe.parent)

        # 5. Fixed / common locations
        for path in [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\Program Files (x86)\ffmpeg\bin",
            r"C:\tools\ffmpeg\bin",
            r"D:\ffmpeg\bin",
            r"C:\ProgramData\chocolatey\bin",
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, "scoop", "apps", "ffmpeg", "current", "bin"),
            os.path.join(local, "Programs", "yt-dlp"),
            os.path.join(local, "yt-dlp"),
            os.path.join(os.path.dirname(sys.executable), "Scripts"),
            os.path.join(home, "Downloads", "ffmpeg", "bin"),
            os.path.join(home, "Downloads", "ffmpeg-release-essentials", "bin"),
            os.path.join(home, "Downloads", "ffmpeg-master-latest-win64-gpl", "bin"),
            os.path.join(home, "Desktop", "ffmpeg", "bin"),
        ]:
            if (pathlib.Path(path) / "ffmpeg.exe").exists():
                logger.debug(f"ffmpeg found: {path}")
                return path

    else:
        for path in [
            "/usr/local/bin", "/opt/homebrew/bin",
            "/usr/bin", "/usr/local/sbin",
            str(pathlib.Path.home() / ".local" / "bin"),
        ]:
            if (pathlib.Path(path) / "ffmpeg").exists():
                return path

    return None   # yt-dlp will still try its own PATH lookup



# ── yt-dlp GUI progress callback (set by CYOAApp) ─────────────────────────
# Signature: fn(vid_id, current, total, pct_str, speed_str) → None
_ytdlp_gui_progress_cb = None

def _make_ytdlp_hook(vid_id: str, idx: int, total: int):
    """Build a yt-dlp progress_hook that forwards to GUI callback."""
    def _hook(d: dict) -> None:
        if d.get("status") == "downloading":
            pct   = d.get("_percent_str", "?%").strip()
            speed = d.get("_speed_str",   "?B/s").strip()
            eta   = d.get("_eta_str",     "?").strip()
            import logging as _lg
            _lg.getLogger("cyoa_downloader").debug(
                f"  yt-dlp [{idx}/{total}] {vid_id} {pct} @ {speed} ETA {eta}")
            if _ytdlp_gui_progress_cb:
                try: _ytdlp_gui_progress_cb(vid_id, idx, total, pct, speed)
                except Exception: pass
    return _hook


def _download_youtube_audio(
    youtube_urls: List[str],
    output_dir: str,
    source_url: str = "",
    log_dir: str = "",
) -> Dict[str, str]:
    """
    Download YouTube audio as MP3 using yt-dlp.
    output_dir : where to save audio files (may be temp folder)
    log_dir    : where to write skipped_youtube_audio.txt (should be real output dir)
    Returns dict: {youtube_url → local_path_relative_to_output_dir}
    """
    try:
        import yt_dlp  # noqa
        has_ytdlp = True
    except ImportError:
        has_ytdlp = False

    if not has_ytdlp or not _ytdlp_enabled:
        if not has_ytdlp:
            logger.warning(
                f"{len(youtube_urls)} YouTube audio URL(s) — yt-dlp tidak terinstall.\n"
                f"  Install: pip install yt-dlp  (+ ffmpeg for MP3 conversion)"
            )
        else:
            logger.info(f"{len(youtube_urls)} YouTube audio URL(s) dilewati (YT Audio dimatikan).")
        _write_youtube_skip_log(youtube_urls, log_dir or output_dir, source_url=source_url)
        return {}

    audio_dir = os.path.join(output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    result: Dict[str, str] = {}
    failed: List[str]      = []

    logger.info(f"yt-dlp: Downloading {len(youtube_urls)} YouTube audio track(s)…")

    total = len(youtube_urls)
    for idx, yt_url in enumerate(youtube_urls, 1):
        # Sanitise URL
        url_clean = yt_url.strip()
        if not url_clean.startswith("http"):
            url_clean = f"https://www.youtube.com/watch?v={url_clean}"

        # Output template: audio/<video_id>.mp3
        try:
            import re as _re
            vid_m = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url_clean)
            vid_id = vid_m.group(1) if vid_m else "unknown"
        except Exception:
            vid_id = "unknown"

        out_template = os.path.join(audio_dir, f"{vid_id}.%(ext)s")
        expected_mp3 = os.path.join(audio_dir, f"{vid_id}.mp3")
        rel_path     = f"audio/{vid_id}.mp3"

        # Already downloaded in a previous run — skip
        if os.path.exists(expected_mp3):
            logger.info(f"  yt-dlp: already exists — {rel_path}")
            result[yt_url] = rel_path
            continue

        ydl_opts = {
            "format":           "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl":          out_template,
            "postprocessors":   [{
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }],
            "quiet":            True,
            "no_warnings":      True,
            "extract_flat":     False,
            "retries":          3,
            "fragment_retries": 3,
            "sleep_interval":   1,
            "max_sleep_interval": 3,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            # Progress hook — update GUI status bar
            "progress_hooks": [_make_ytdlp_hook(vid_id, idx, total)],
        }

        # Auto-locate ffmpeg — pass to yt-dlp if found; if not, yt-dlp
        # will still search its own PATH. Only warn if conversion later fails.
        ffmpeg_dir = _find_ffmpeg()
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir
            logger.debug(f"  ffmpeg: {ffmpeg_dir}")

        def _any_audio_exists() -> Optional[str]:
            """Return first audio file found for this vid_id, or None."""
            _exts = (".mp3", ".m4a", ".opus", ".webm", ".ogg", ".aac", ".wav")
            found = sorted([
                f for f in os.listdir(audio_dir)
                if f.startswith(vid_id) and f.lower().endswith(_exts)
            ])
            return found[0] if found else None

        def _try_ytdlp(opts: dict) -> Tuple[bool, Optional[str]]:
            """Run yt-dlp. Returns (success, error_str|None). Suppresses stderr output."""
            import yt_dlp, io
            try:
                # Redirect yt-dlp's own stderr to suppress noisy cookie errors
                # while still capturing the message for our own logic
                captured = io.StringIO()
                class _QuietLogger:
                    def debug(self, msg):   pass
                    def info(self, msg):    pass
                    def warning(self, msg): pass
                    def error(self, msg):
                        captured.write(msg + "\n")
                opts2 = {**opts, "logger": _QuietLogger()}
                with yt_dlp.YoutubeDL(opts2) as ydl:
                    ydl.download([url_clean])
                return True, None
            except Exception as e:
                err = str(e) + "\n" + captured.getvalue()
                return False, err

        def _is_bot_error(err: Optional[str]) -> bool:
            if not err: return False
            return any(p.lower() in err.lower() for p in [
                "Sign in to confirm", "bot", "confirm your age",
                "Private video", "Video unavailable", "HTTP Error 403",
                "This video is not available",
            ])

        def _chrome_cookie_path() -> Optional[str]:
            """Return Chrome Cookies DB path, or None if not found."""
            if sys.platform != "win32":
                return None
            local = os.environ.get("LOCALAPPDATA", "")
            for sub in [
                r"Google\Chrome\User Data\Default\Cookies",
                r"Google\Chrome\User Data\Default\Network\Cookies",
                r"BraveSoftware\Brave-Browser\User Data\Default\Cookies",
                r"BraveSoftware\Brave-Browser\User Data\Default\Network\Cookies",
                r"Microsoft\Edge\User Data\Default\Cookies",
                r"Microsoft\Edge\User Data\Default\Network\Cookies",
            ]:
                p = os.path.join(local, sub)
                if os.path.exists(p):
                    return p
            return None

        def _try_with_cookie_file(browser: str, opts: dict) -> Tuple[bool, Optional[str]]:
            """
            Try to use browser cookies. If Chrome is locked (common when browser
            is open), copy the DB to a temp file first so yt-dlp can read it.
            """
            import tempfile, shutil as _sh

            if browser == "chrome" and sys.platform == "win32":
                src_db = _chrome_cookie_path()
                if src_db:
                    try:
                        # Copy to temp — avoids "Could not copy Chrome cookie database" error
                        tmp_db = tempfile.mktemp(suffix=".db")
                        _sh.copy2(src_db, tmp_db)
                        opts_c = {**opts, "cookiesfrombrowser": (browser,)}
                        result = _try_ytdlp(opts_c)
                        try: os.remove(tmp_db)
                        except Exception: pass
                        return result
                    except Exception:
                        pass  # Fall through to standard method

            return _try_ytdlp({**opts, "cookiesfrombrowser": (browser,)})

        try:
            import yt_dlp

            # ── First attempt (no cookies) ─────────────────────────────
            opts = {k: v for k, v in ydl_opts.items() if k != "cookiesfrombrowser"}
            ok1, err1 = _try_ytdlp(opts)

            found_file = _any_audio_exists()

            if not found_file:
                # Nothing downloaded — retry with browser cookies
                # Always retry on failure; prioritise if error looks bot-related
                browsers = ["chrome", "firefox", "edge", "brave", "chromium", "safari"]
                logger.info(
                    f"  yt-dlp: first attempt failed "
                    f"{'(bot-detected)' if _is_bot_error(err1) else '(unknown error)'},"
                    f" retrying with browser cookies…"
                )
                for browser in browsers:
                    opts_c = {**opts, "cookiesfrombrowser": (browser,)}
                    ok_c, err_c = _try_ytdlp(opts_c)
                    found_file = _any_audio_exists()
                    if found_file:
                        logger.info(f"  yt-dlp: cookie source → {browser}")
                        break
                    if ok_c:
                        # yt-dlp said OK but file still missing — next browser
                        continue

            if found_file:
                # ── Convert to MP3 if needed ───────────────────────────
                found_ext = os.path.splitext(found_file)[1].lower()
                if found_ext == ".mp3":
                    rel_path = f"audio/{found_file}"
                    result[yt_url] = rel_path
                    logger.info(f"  yt-dlp OK: {rel_path}")
                else:
                    ffmpeg_dir2 = _find_ffmpeg()
                    if sys.platform == "win32" and ffmpeg_dir2:
                        ffmpeg_exe = os.path.join(ffmpeg_dir2, "ffmpeg.exe")
                    elif ffmpeg_dir2:
                        ffmpeg_exe = os.path.join(ffmpeg_dir2, "ffmpeg")
                    else:
                        ffmpeg_exe = "ffmpeg"
                    src_path = os.path.join(audio_dir, found_file)
                    dst_path = os.path.join(audio_dir, f"{vid_id}.mp3")
                    try:
                        import subprocess as _sp
                        r2 = _sp.run(
                            [ffmpeg_exe, "-i", src_path, "-q:a", "2", "-y", dst_path],
                            capture_output=True, timeout=60,
                        )
                        if os.path.exists(dst_path):
                            os.remove(src_path)
                            rel_path = f"audio/{vid_id}.mp3"
                            result[yt_url] = rel_path
                            logger.info(f"  yt-dlp OK (converted {found_ext}→mp3): {rel_path}")
                        else:
                            rel_path = f"audio/{found_file}"
                            result[yt_url] = rel_path
                            logger.info(f"  yt-dlp OK (kept {found_ext}): {rel_path}")
                    except Exception:
                        rel_path = f"audio/{found_file}"
                        result[yt_url] = rel_path
                        logger.warning(
                            f"  ffmpeg conversion failed ({found_ext}→mp3): {found_file}\n"
                            "  Install ffmpeg: winget install Gyan.FFmpeg  (Windows)\n"
                            "                  brew install ffmpeg           (macOS)\n"
                            "                  sudo apt install ffmpeg       (Linux)"
                        )
            else:
                logger.warning(f"  yt-dlp: file not found after download: {url_clean}")
                failed.append(yt_url)
        except Exception as e:
            logger.error(f"  yt-dlp FAILED: {url_clean} — {e}")
            failed.append(yt_url)

    if failed:
        _write_youtube_skip_log(failed, log_dir or output_dir, source_url=source_url)

    if result:
        logger.info(
            f"yt-dlp: {len(result)}/{len(youtube_urls)} track(s) downloaded. "
            f"Files in: {audio_dir}"
        )

    return result


def _patch_youtube_refs_in_json(
    project_str: str,
    yt_map: Dict[str, str],
) -> str:
    """
    Patch project JSON for offline audio.

    CRITICAL FIX: Eo(e,t,o) in ICC Plus app_B6d7tc9y.js reads e.useAudioURL
    where 'e' is the ROW OBJECT with setBgmIsOn=true, NOT the app root.
    Confirmed from source: `if(e.useAudioURL?...Eo(e,e.bgmId,0)...)`

    So we must add useAudioURL:true to EACH OBJECT that has bgmId, not just root.
    We do BOTH:
      1. Per-object: add useAudioURL:true to each dict that has bgmId (critical)
      2. Root level: add useAudioURL:true at root (belt-and-suspenders)
    """
    if not yt_map:
        return project_str

    import re as _re

    local_paths = set(yt_map.values())  # "audio/ID.mp3"
    local_ytids: Dict[str, str] = {}    # "dQw4w9W" → "audio/dQw4w9W.mp3"
    for yt_url, local_path in yt_map.items():
        vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
        if vm:
            local_ytids[vm.group(1)] = local_path

    try:
        obj = json.loads(project_str)
        patched_count = 0

        def _walk(node) -> None:
            nonlocal patched_count
            if isinstance(node, list):
                for item in node:
                    _walk(item)
            elif isinstance(node, dict):
                bgm = node.get("bgmId", "")
                if bgm:
                    new_path = None
                    if bgm in local_paths:
                        # Already patched path — just ensure useAudioURL is set
                        new_path = bgm
                    elif bgm in local_ytids:
                        # Raw YouTube video ID
                        new_path = local_ytids[bgm]
                    else:
                        # Full YouTube URL
                        vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', bgm)
                        if vm and vm.group(1) in local_ytids:
                            new_path = local_ytids[vm.group(1)]

                    if new_path:
                        node["bgmId"]        = new_path
                        node["useAudioURL"]  = True   # ← ON THE OBJECT (critical!)
                        patched_count += 1

                for v in node.values():
                    if isinstance(v, (dict, list)):
                        _walk(v)

        _walk(obj)

        # Also root-level for extra safety
        if "app" in obj and isinstance(obj["app"], dict):
            obj["app"]["useAudioURL"] = True
        else:
            obj["useAudioURL"] = True

        result = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        logger.info(
            f"YouTube patch: {patched_count} bgmId(s) → local MP3, "
            f"useAudioURL:true set per-object + root"
        )
        return result

    except Exception as _je:
        # JSON parse failed — string regex fallback
        logger.debug(f"Audio patch: JSON parse failed ({_je}), using regex fallback")
        patched = project_str
        any_patched = False
        for yt_url, local_path in yt_map.items():
            vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
            vid_id = vm.group(1) if vm else None
            before = patched
            # Replace bgmId AND inject useAudioURL right after it
            if vid_id:
                patched = _re.sub(
                    rf'"bgmId"\s*:\s*"{_re.escape(vid_id)}"',
                    f'"bgmId":"{local_path}","useAudioURL":true',
                    patched,
                )
            patched = _re.sub(
                rf'"bgmId"\s*:\s*"{_re.escape(yt_url)}"',
                f'"bgmId":"{local_path}","useAudioURL":true',
                patched,
            )
            # Also handle already-patched paths missing useAudioURL
            patched = _re.sub(
                rf'"bgmId"\s*:\s*"{_re.escape(local_path)}"(?!,\s*"useAudioURL")',
                f'"bgmId":"{local_path}","useAudioURL":true',
                patched,
            )
            if patched != before:
                any_patched = True

        # Root-level injection
        if any_patched and '"useAudioURL":true' not in patched[:200]:
            patched = patched.replace('{"version":', '{"useAudioURL":true,"version":', 1)

        return patched


def _safe_response_text(r: "requests.Response") -> str:
    """
    Decode response content with correct encoding.
    Always passes through try_decode_bytes() which tries UTF-8 first.
    Server-declared ISO-8859-1 is ignored (it's the HTTP/1.1 default, not a
    real declaration — most servers omit charset and requests fills in latin-1).
    """
    # Only trust the server's declared encoding if it's explicit and NOT
    # the latin-1 default that requests infers from the HTTP spec.
    _IGNORE = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", ""}
    declared = (r.encoding or "").lower()
    pref = r.encoding if declared not in _IGNORE else ""
    return try_decode_bytes(r.content, preferred_encoding=pref)




def process_images(
    input_str: str,
    base_url: str,
    embed: bool = False,
    download: bool = False,
    temp_folder: Optional[str] = None,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    output_dir: str = "",
    source_url: str = "",
    embed_audio: bool = False,
    site_folder: str = "",   # website mode: check if images already exist here
) -> Tuple[str, str, Set[str]]:
    """
    Download image AND audio assets referenced in a project.json string.

    Image fields (IMAGE_FIELDS): image, backgroundImage, rowBackgroundImage,
        objectBackgroundImage, defaultImage — all downloaded regardless of origin.

    Audio fields (AUDIO_FIELDS): audio, audioSrc, backgroundMusic, etc.
        - Direct mp3/ogg/wav URLs → downloaded.
        - YouTube URLs/IDs        → kept as-is (cannot go offline), logged.

    embed_audio: if True, embed downloaded audio as data:audio/mpeg;base64,...
                 in the embed_str output (works without a server). Ignored if
                 the file exceeds 10 MB (too large to inline safely).

    Failed images → original URL kept in JSON (viewer shows broken image or blank).
    Returns (embed_str, download_str).
    """
    data_uri_re = re.compile(r"^data:(?:image|audio|application)/[a-zA-Z0-9.+-]+;base64,")

    if download and not temp_folder:
        raise ValueError("temp_folder required when download=True")
    if download:
        images_folder = os.path.join(temp_folder, "images")
        audio_folder  = os.path.join(temp_folder, "audio")
        # NOTE: folders are created on-demand when first file is saved,
        # not here — so empty folders are never left behind.

    # ── Phase A: JSON-aware deep scan (handles bgmId+useAudioURL, nested sfx) ──
    deep_images, deep_audio, deep_youtube = _deep_scan_project_assets(input_str, base_url)

    # ── Phase B: Regex scan as fallback/supplement ──────────────────
    # Catches cases where JSON parsing fails (truncated payload, embedded JS, etc.)
    all_fields   = IMAGE_FIELDS + AUDIO_FIELDS
    field_group  = "|".join(re.escape(f) for f in all_fields)
    pattern      = rf'"({field_group})"\s*:\s*"([^"]+)"'
    image_fields_lower = {f.lower() for f in IMAGE_FIELDS}

    image_paths:   Set[str] = set(deep_images)
    audio_paths:   Set[str] = set(deep_audio)
    youtube_paths: Set[str] = set(deep_youtube)

    for m in re.finditer(pattern, input_str, flags=re.IGNORECASE):
        field = m.group(1)
        path  = m.group(2)
        if data_uri_re.match(path):
            continue
        if _YOUTUBE_URL_RE.search(path):
            youtube_paths.add(path)
        elif field.lower() in image_fields_lower:
            image_paths.add(path)
        else:
            audio_paths.add(path)

    # Remove data URIs and blanks that slipped through
    for s in (image_paths, audio_paths, youtube_paths):
        s.discard("")
        to_remove = {p for p in s if data_uri_re.match(p)}
        s -= to_remove

    if not image_paths and not audio_paths and not youtube_paths:
        logger.info("No external images or audio found.")
        return input_str, input_str, set()

    # Log summary
    logger.info(
        f"Assets found: {len(image_paths)} image(s), "
        f"{len(audio_paths)} direct audio file(s), "
        f"{len(youtube_paths)} YouTube reference(s) (kept as-is)."
    )
    _yt_local: Dict[str, str] = {}   # yt-dlp downloaded files: local_rel → local_rel

    if youtube_paths:
        yt_audio_dir = temp_folder if temp_folder else output_dir
        yt_map = _download_youtube_audio(
            sorted(youtube_paths), yt_audio_dir, source_url=source_url,
            log_dir=output_dir,
        )
        if yt_map:
            input_str = _patch_youtube_refs_in_json(input_str, yt_map)
            youtube_paths -= set(yt_map.keys())
            _yt_local = yt_map  # track for pre-population below

    all_downloadable = image_paths | audio_paths
    if all_downloadable:
        ext_count   = sum(1 for p in all_downloadable if p.startswith(("http://", "https://")))
        local_count = len(all_downloadable) - ext_count
        logger.info(
            f"Downloading {len(all_downloadable)} asset(s) "
            f"({local_count} local, {ext_count} external) "
            f"with {max_workers} threads…"
        )

    # ── Fetch all downloadable assets in parallel ──────────────────
    # cache: original_path → (content | None, mime, resolved_url, error_str)
    fetch_cache: Dict[str, Tuple[Optional[bytes], str, str, str]] = {}
    download_map: Dict[str, str] = {}

    # ── Website mode: images already downloaded at original paths ─────
    # WebsiteDownloader preserves directory structure. Skip re-downloading
    # relative paths that already exist on disk (prevents rename collisions).
    if site_folder and os.path.isdir(site_folder):
        for asset_path in list(image_paths):
            if asset_path.startswith(("http://", "https://")):
                continue
            clean = _safe_rel_path(asset_path.lstrip('./').lstrip('/'))
            disk  = _safe_join(site_folder, clean)
            if os.path.exists(disk):
                logger.debug(f"  [site exists, kept] {clean}")
                image_paths.discard(asset_path)
                fetch_cache[asset_path] = (b"__site_existing__", "image", asset_path, "")
                download_map[asset_path] = clean   # keep original relative path

    # Pre-populate with yt-dlp downloads — already on disk, no network fetch needed
    for _yt_rel in _yt_local.values():
        # _yt_rel = "audio/ID.mp3" — marks as successfully "downloaded"
        fetch_cache[_yt_rel] = (b"__yt_local__", "audio/mpeg", _yt_rel, "")
        if download:
            download_map[_yt_rel] = _yt_rel

    def fetch_one(asset_path: str):
        asset_url = (
            asset_path
            if asset_path.startswith(("http://", "https://"))
            else urljoin(base_url.rstrip("/") + "/", asset_path)
        )

        # ── E: Disk cache check ───────────────────────────────────────
        cached = _cache_get(asset_url)
        if cached is not None:
            mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
            logger.info(f"  [CACHE HIT] {asset_url.split('/')[-1]} ({len(cached)//1024}KB)")
            return asset_path, cached, mime, asset_url, ""

        # ── C: CDN-specific headers ───────────────────────────────────
        _domain_throttle(asset_url)  # D: also checks domain backoff
        headers = get_headers_for_url(asset_url) or {}

        last_err = ""
        _cookie_session_tried = False

        for attempt in range(4):  # 4 attempts: 3 normal + 1 cookie
            try:
                # ── B: Cookie session on 3rd attempt for auth-protected content
                if attempt == 2 and not _cookie_session_tried:
                    _cookie_session_tried = True
                    for _browser in ("chrome", "edge", "firefox"):
                        cs = _make_cookie_session(_browser)
                        if cs:
                            try:
                                rc = cs.get(asset_url, headers=headers, timeout=30)
                                if rc.status_code == 200 and len(rc.content) > 64:
                                    mime = rc.headers.get("Content-Type", "").split(";")[0].strip() \
                                           or mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                                    _domain_record_success(asset_url)
                                    _cache_put(asset_url, rc.content)
                                    logger.info(f"  [Cookie/{_browser}] {asset_url.split('/')[-1]}")
                                    return asset_path, rc.content, mime, asset_url, ""
                            except Exception:
                                pass

                r = fetch_response(asset_url, extra_headers=headers, timeout=30, as_bytes=True)
                if r is None:
                    raise requests.RequestException("fetch_response returned None")

                # ── D: Handle 429 with exponential backoff ────────────
                if r.status_code == 429:
                    backoff = _domain_record_failure(asset_url, 429)
                    retry_after = int(r.headers.get("Retry-After", backoff))
                    sleep_s = max(backoff, retry_after, wait_time)
                    logger.warning(f"429 — backoff {sleep_s:.1f}s: {asset_url}")
                    _time.sleep(sleep_s)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    backoff = _domain_record_failure(asset_url, r.status_code)
                    logger.warning(f"{r.status_code} — backoff {backoff:.1f}s: {asset_url}")
                    _time.sleep(backoff)
                    continue

                r.raise_for_status()
                mime = r.headers.get("Content-Type", "").split(";")[0].strip()
                if not mime:
                    mime, _ = mimetypes.guess_type(asset_url)
                    mime = mime or "application/octet-stream"

                _throttle_bandwidth(len(r.content))
                _domain_record_success(asset_url)
                # ── E: Store in disk cache ────────────────────────────
                _cache_put(asset_url, r.content)
                return asset_path, r.content, mime, asset_url, ""

            except requests.exceptions.SSLError:
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = _get_shared_session(use_cf=bool(use_cloudscraper)).get(
                        asset_url, headers=headers, timeout=30, verify=False)
                    r.raise_for_status()
                    mime = r.headers.get("Content-Type", "").split(";")[0].strip() \
                           or "application/octet-stream"
                    logger.warning(f"  SSL fallback (verify=False): {asset_url}")
                    _cache_put(asset_url, r.content)
                    return asset_path, r.content, mime, asset_url, ""
                except Exception as e2:
                    last_err = f"SSL error: {e2}"
            except requests.exceptions.ConnectionError as e:
                err_s = str(e).lower()
                last_err = f"Connection reset (attempt {attempt+1})" \
                    if "connection reset" in err_s or "econnreset" in err_s else str(e)
                logger.warning(f"  {last_err}: {asset_url}")
                _domain_record_failure(asset_url)
                if attempt < 3: _time.sleep(min(10 * (attempt + 1), 30))
            except requests.RequestException as e:
                last_err = str(e)
                logger.warning(f"Attempt {attempt + 1} failed for {asset_url}: {e}")
                _domain_record_failure(asset_url)
                if attempt < 3: _time.sleep(min(10 * (attempt + 1), 30))

        # ── A: Headless fallback (images only) ───────────────────────
        if asset_path in image_paths:
            logger.info(f"  [Headless] Trying browser fetch: {asset_url}")
            headless_data = _fetch_headless(asset_url)
            if headless_data:
                mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                _cache_put(asset_url, headless_data)
                logger.info(f"  [Headless] ✓ {asset_url.split('/')[-1]} ({len(headless_data)//1024}KB)")
                return asset_path, headless_data, mime, asset_url, ""

            # ── F: gallery-dl (Pixiv, booru, DeviantArt auth) ────────
            gdl_site = _is_gallery_dl_site(asset_url)
            if gdl_site:
                logger.info(f"  [gallery-dl] Trying ({gdl_site}): {asset_url}")
                gdl_data = _fetch_via_gallery_dl(asset_url)
                if gdl_data:
                    mime = mimetypes.guess_type(asset_url)[0] or "image/jpeg"
                    _cache_put(asset_url, gdl_data)
                    return asset_path, gdl_data, mime, asset_url, ""

        logger.error(f"All retries failed: {asset_url}")
        return asset_path, None, "", asset_url, last_err

    dedup_count = 0
    if all_downloadable:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_one, p): p for p in all_downloadable}
            done = 0
            for fut in as_completed(futures):
                path, content, mime, resolved, err = fut.result()
                # Dedup is applied at save time, after the final local path is known.
                fetch_cache[path] = (content, mime, resolved, err)
                done += 1
                status = "✓" if content is not None else "✗ FAILED"
                logger.info(f"  [{done}/{len(all_downloadable)}] {status} {resolved.split('/')[-1]}")
    # ── Collect failures ───────────────────────────────────────────
    failed_images: List[Dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in image_paths
    ]
    failed_audio: List[Dict[str, str]] = [
        {"url": resolved, "path": path, "error": err}
        for path, (content, mime, resolved, err) in fetch_cache.items()
        if content is None and path in audio_paths
    ]

    ok_count = sum(1 for _, (c, _, _, _) in fetch_cache.items() if c is not None)
    logger.info(
        f"Download summary: {ok_count} OK, "
        f"{len(failed_images)} image failure(s), "
        f"{len(failed_audio)} audio failure(s), "
        f"{len(youtube_paths)} YouTube (skipped) "
        f"out of {len(all_downloadable) + len(youtube_paths)} total."
    )
    if failed_images:
        logger.warning(
            f"{len(failed_images)} image(s) could not be downloaded — "
            f"original URLs kept in JSON. Check failed_images.txt."
        )
        _write_failed_images_log(failed_images, output_dir, source_url=source_url)
    if failed_audio:
        logger.warning(
            f"{len(failed_audio)} audio file(s) could not be downloaded "
            f"and will remain as external URLs."
        )
    if failed_images or failed_audio:
        try:
            write_asset_failure_summary(
                failed_images + failed_audio,
                output_dir or os.getcwd(),
                source_url=source_url,
                title="Broken Project Asset Report",
            )
        except Exception as e:
            logger.debug(f"Broken asset report could not be written: {e}")

    # ── Build replacement maps ─────────────────────────────────────
    embed_map:  Dict[str, str] = {}
    # download_map already initialized above (with yt_local pre-populated)

    for path, (content, mime, resolved, err) in fetch_cache.items():
        is_image = path in image_paths

        if content is None:
            continue

        # Skip yt-dlp sentinel entries — already in download_map, no re-saving needed
        if content == b"__yt_local__":
            continue

        # Skip site_existing sentinel — file already at original path, path kept
        if content == b"__site_existing__":
            continue

        # ── Successful download ────────────────────────────────────
        if embed:
            if is_image:
                b64 = base64.b64encode(content).decode()
                embed_map[path] = f"data:{mime};base64,{b64}"
            elif embed_audio and not is_image:
                # Embed audio as base64 only if file is small enough to inline
                _audio_size_limit = 10 * 1024 * 1024  # 10 MB
                if len(content) <= _audio_size_limit:
                    b64 = base64.b64encode(content).decode()
                    embed_map[path] = f"data:{mime};base64,{b64}"
                    logger.info(f"  Embedded audio: {path.split('/')[-1]} ({len(content)//1024} KB)")
                else:
                    logger.warning(
                        f"  Audio too large to embed ({len(content)//1024//1024} MB): "
                        f"{path.split('/')[-1]} — keeping as file reference"
                    )

        if download:
            ext = mimetypes.guess_extension(mime) or ".bin"
            if ext in (".jpe", ".jpeg"):
                ext = ".jpg"

            # ── Preserve relative path structure (directory hierarchy) ─────
            # e.g. ./CYOAs/Images/BranchingHeart/0/3.avif
            #   → images/CYOAs/Images/BranchingHeart/0/3.avif
            # This prevents double-downloads: deep scan checks the same
            # directory structure and will find the file already on disk.
            url_path = urlparse(resolved).path.lstrip('/')
            base_url_path = urlparse(base_url).path.rstrip('/')
            if base_url_path and url_path.startswith(base_url_path.lstrip('/')):
                url_path = url_path[len(base_url_path.lstrip('/')):]
            url_path = url_path.lstrip('./ ')

            if '/' in url_path:
                # Multi-segment path: preserve directory structure
                fn = url_path
                # Strip leading directory that duplicates dest_folder name
                # e.g. "images/hero.png" saved under images_folder → "images/images/hero.png" (BAD)
                #   → strip to "hero.png" → "images/hero.png" (GOOD)
                _IMAGE_DIR_PREFIXES = ('images/', 'img/', 'image/', 'pics/', 'pictures/', 'assets/images/', 'assets/img/')
                _AUDIO_DIR_PREFIXES = ('audio/', 'music/', 'sounds/', 'sfx/', 'bgm/', 'assets/audio/', 'assets/music/')
                fn_lower = fn.lower()
                if is_image:
                    for pfx in _IMAGE_DIR_PREFIXES:
                        if fn_lower.startswith(pfx):
                            fn = fn[len(pfx):]
                            break
                else:
                    for pfx in _AUDIO_DIR_PREFIXES:
                        if fn_lower.startswith(pfx):
                            fn = fn[len(pfx):]
                            break
            else:
                fn = os.path.basename(url_path) or ("image" if is_image else "audio")

            if not fn:
                fn = "image" if is_image else "audio"
            if not os.path.splitext(fn)[1]:
                fn += ext

            # Audio goes into audio/ subfolder, images into images/
            if is_image:
                dest_folder = images_folder
                rel_prefix  = "images"
            else:
                dest_folder = audio_folder
                rel_prefix  = "audio"

            # Build full destination path (may include subdirectories), guarded against path traversal
            dest_path = _safe_join(dest_folder, fn, fallback=("image" if is_image else "audio") + ext)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # Collision avoidance: check if file already exists at this path
            base_fn_full, ext_fn = os.path.splitext(dest_path)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = f"{base_fn_full}_{counter}{ext_fn}"
                counter += 1

            duplicate_of = _check_image_dedup(content, dest_path) if is_image else None
            if duplicate_of and os.path.exists(duplicate_of):
                try:
                    rel_saved = os.path.relpath(duplicate_of, os.path.dirname(images_folder)).replace('\\', '/')
                    download_map[path] = rel_saved
                    dedup_count += 1
                    logger.debug(f"  [DEDUP] {path.split('/')[-1]} -> {rel_saved}")
                    continue
                except Exception:
                    pass

            with open(dest_path, "wb") as f:
                f.write(content)

            # Relative path from site root: "images/CYOAs/Images/.../3.avif"
            rel_saved = os.path.relpath(dest_path, os.path.dirname(images_folder)).replace('\\', '/')
            download_map[path] = rel_saved

    if dedup_count:
        logger.info(f"  [DEDUP] {dedup_count} duplicate image(s) reused instead of saved again")

    # ── Single-pass substitution ───────────────────────────────────
    # The regex `pattern` covers IMAGE_FIELDS + AUDIO_FIELDS field names.
    # But bgmId (when useAudioURL=true) is found only by the deep scanner —
    # we add it to the pattern here so it gets rewritten too.
    all_known_paths = set(embed_map) | set(download_map)
    # Build an extended pattern that also matches "bgmId":"<url>" for values
    # that ended up in our maps (i.e. were identified as direct audio URLs).
    bgmid_paths_in_map = {p for p in all_known_paths if p in audio_paths}

    def make_embed(m: re.Match) -> str:
        field, path = m.group(1), m.group(2)
        if data_uri_re.match(path):
            return m.group(0)
        return f'"{field}":"{embed_map.get(path, path)}"'

    def make_download(m: re.Match) -> str:
        field, path = m.group(1), m.group(2)
        if data_uri_re.match(path):
            return m.group(0)
        return f'"{field}":"{download_map.get(path, path)}"'

    # Primary substitution using field-name regex
    embed_str = re.sub(pattern, make_embed,    input_str, flags=re.IGNORECASE) if embed    else input_str
    dl_str    = re.sub(pattern, make_download, input_str, flags=re.IGNORECASE) if download else input_str

    # Secondary substitution: rewrite any URL value found by the deep scanner
    # that was NOT covered by the field-name pattern (e.g. bgmId direct audio,
    # nested soundEffects audio that regex missed).
    # Strategy: simple string replace on the quoted URL value in JSON.
    if embed:
        for orig_path, new_val in embed_map.items():
            json_orig = f'"{orig_path}"'
            json_new  = f'"{new_val}"'
            if json_orig in embed_str:
                embed_str = embed_str.replace(json_orig, json_new)
    if download:
        for orig_path, new_val in download_map.items():
            json_orig = f'"{orig_path}"'
            json_new  = f'"{new_val}"'
            if json_orig in dl_str:
                dl_str = dl_str.replace(json_orig, json_new)

    # Collect successfully downloaded/resolved URLs for skip-list
    _resolved_urls: Set[str] = set()
    for path, (content, mime, resolved, err) in fetch_cache.items():
        if content is not None and resolved:
            _resolved_urls.add(resolved)
            # Also add the original asset path resolved to absolute
            if not path.startswith(("http://", "https://")):
                _resolved_urls.add(urljoin(base_url.rstrip("/") + "/", path))

    return embed_str, dl_str, _resolved_urls


# ─────────────────────────────────────────────────────────────────
#  Font utilities
# ─────────────────────────────────────────────────────────────────

def _find_font_urls(
    project_str: str,
    base_url: str,
    html_source: str = "",
    extra_css_urls: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Return {font_url: description} for all fonts found in:
      1. project.json / project string (direct URLs + CSS url() + Google Fonts refs)
      2. viewer HTML source (Google Fonts <link>, local font <link>)
      3. any extra CSS URLs provided (e.g. from downloaded CSS files)

    Google Fonts CSS is resolved in parallel for speed.
    Duplicate font URLs are deduplicated automatically.
    """
    results: Dict[str, str] = {}
    gf_css_urls: Set[str] = set()   # Google Fonts CSS URLs to resolve
    raw_font_urls: List[Tuple[str, str]] = []  # (url, description)

    # ── 1. Scan project.json ─────────────────────────────────────────
    # Direct font file URLs
    for u in re.findall(
        r'https?://[^\s"\'<>]+\.(?:woff2?|ttf|otf|eot)[^\s"\'<>]*',
        project_str, re.IGNORECASE
    ):
        raw_font_urls.append((u, "project.json direct URL"))

    # Google Fonts CSS links in project.json
    for gf in re.findall(r'https://fonts\.googleapis\.com/css[^\s"\'<>]*', project_str):
        gf_css_urls.add(gf)

    # CSS url() references in project.json
    for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', project_str):
        if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
            url = fu if fu.startswith("http") else urljoin(base_url.rstrip("/") + "/", fu)
            raw_font_urls.append((url, "project.json CSS url()"))

    # ── 2. Scan viewer HTML ──────────────────────────────────────────
    if html_source:
        soup_h = BeautifulSoup(html_source, "html.parser")

        # <link rel="stylesheet" href="https://fonts.googleapis.com/...">
        for tag in soup_h.find_all("link", rel=lambda r: r and "stylesheet" in (r if isinstance(r, str) else " ".join(r)).lower()):
            href = tag.get("href", "")
            if "fonts.googleapis.com" in href:
                gf_css_urls.add(href)
            elif any(href.lower().endswith(ext) for ext in FONT_EXTENSIONS):
                url = href if href.startswith("http") else urljoin(base_url, href)
                raw_font_urls.append((url, "index.html <link>"))

        # Inline <style> blocks
        for style_tag in soup_h.find_all("style"):
            css_text = style_tag.string or ""
            for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text):
                if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
                    url = fu if fu.startswith("http") else urljoin(base_url, fu)
                    raw_font_urls.append((url, "index.html inline <style>"))

    # ── 3. Scan extra CSS files ──────────────────────────────────────
    for css_url in (extra_css_urls or []):
        css_text = get_source(css_url) or ""
        for fu in re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text):
            if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS) and not fu.startswith("data:"):
                resolved = fu if fu.startswith("http") else urljoin(css_url, fu)
                raw_font_urls.append((resolved, f"CSS: {css_url}"))
        for gf in re.findall(r'https://fonts\.googleapis\.com/css[^\s"\'<>]*', css_text):
            gf_css_urls.add(gf)

    # ── 4. Resolve Google Fonts CSS in parallel ──────────────────────
    def _resolve_gf_css(gf_url: str) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        logger.info(f"  Resolving Google Fonts: {gf_url}")
        css = get_source(gf_url, extra_headers={"User-Agent": "Mozilla/5.0"})
        if css:
            for fu in re.findall(r'url\(([^)]+)\)', css):
                fu = fu.strip("\"'")
                if any(fu.lower().endswith(ext) for ext in FONT_EXTENSIONS):
                    found.append((fu, f"Google Fonts ({gf_url})"))
        return found

    if gf_css_urls:
        logger.info(f"Resolving {len(gf_css_urls)} Google Fonts CSS URL(s) in parallel…")
        with ThreadPoolExecutor(max_workers=min(len(gf_css_urls), 8)) as ex:
            for batch in ex.map(_resolve_gf_css, sorted(gf_css_urls)):
                raw_font_urls.extend(batch)

    # ── 5. Deduplicate ───────────────────────────────────────────────
    seen_urls: Set[str] = set()
    for url, desc in raw_font_urls:
        url = url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results[url] = desc

    return results


def analyse_fonts(project_str: str, base_url: str, html_source: str = "") -> None:
    fonts = _find_font_urls(project_str, base_url, html_source=html_source)
    if not fonts:
        logger.info("Font analysis: no external fonts found.")
        return
    logger.info(f"Font analysis: {len(fonts)} font file(s) found:")
    for url, source in fonts.items():
        logger.info(f"  [{source}]  {url}")


def _download_fonts_into_folder(
    project_str: str,
    base_url: str,
    folder: str,
    html_source: str = "",
    skip_if_website_mode: bool = False,
) -> str:
    """
    Download all fonts → <folder>/fonts/, rewrite project_str to use local paths.

    In website mode (skip_if_website_mode=True), WebsiteDownloader already handles
    fonts referenced in CSS/HTML — only fonts found exclusively in project.json
    are downloaded here to avoid double-downloading.
    """
    fonts = _find_font_urls(project_str, base_url, html_source=html_source)
    if not fonts:
        logger.info("No fonts to download.")
        return project_str

    fonts_dir = os.path.join(folder, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    logger.info(f"Downloading {len(fonts)} font(s)…")

    # Track saved filenames to avoid collisions
    saved_names: Dict[str, str] = {}   # basename → full path
    url_to_local: Dict[str, str] = {}

    def _download_one_font(item: Tuple[str, str]) -> Tuple[str, Optional[str]]:
        font_url, source = item
        try:
            r = _get_shared_session(use_cf=bool(use_cloudscraper)).get(
                font_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            raw_fn = os.path.basename(urlparse(font_url).path)
            if not raw_fn:
                raw_fn = hashlib.md5(font_url.encode()).hexdigest()[:8] + ".woff2"
            return font_url, r.content
        except Exception as e:
            logger.error(f"  Font failed: {font_url} — {e}")
            return font_url, None

    # Download in parallel
    with ThreadPoolExecutor(max_workers=min(len(fonts), 6)) as ex:
        for font_url, content in ex.map(_download_one_font, fonts.items()):
            if content is None:
                continue
            raw_fn = os.path.basename(urlparse(font_url).path) or hashlib.md5(font_url.encode()).hexdigest()[:8] + ".woff2"
            # Deduplicate filename
            base_fn, ext_fn = os.path.splitext(raw_fn)
            fn = raw_fn
            counter = 1
            while fn in saved_names and saved_names[fn] != os.path.join(fonts_dir, fn):
                fn = f"{base_fn}_{counter}{ext_fn}"
                counter += 1
            save_path = os.path.join(fonts_dir, fn)
            # Fix: skip if WebsiteDownloader already saved this font
            if os.path.exists(save_path):
                logger.debug(f"  Font already exists (skipping re-download): {fn}")
                saved_names[fn] = save_path
                url_to_local[font_url] = f"fonts/{fn}"
                continue
            with open(save_path, "wb") as f:
                f.write(content)
            saved_names[fn] = save_path
            url_to_local[font_url] = f"fonts/{fn}"
            logger.info(f"  Saved font: {fn}  ({fonts[font_url]})")

    # Rewrite project_str
    for orig, local in url_to_local.items():
        project_str = project_str.replace(orig, local)

    return project_str


# ─────────────────────────────────────────────────────────────────
#  Website downloader
# ─────────────────────────────────────────────────────────────────


def create_retry_session(use_cloudscraper: bool = False) -> requests.Session:
    """Create a requests session with retry strategy and optional proxy."""
    if use_cloudscraper:
        try:
            import cloudscraper as _cs
            session = _cs.create_scraper()
        except ImportError:
            logger.warning("cloudscraper requested but not installed; falling back to normal requests. Install: pip install cloudscraper")
            session = requests.Session()
    else:
        session = requests.Session()

    # requests normally inherits HTTP(S)_PROXY env variables. Honor explicit
    # app-level disabled/manual modes so 'Proxy disabled' really disables env proxy.
    session.trust_env = (globals().get("_proxy_mode", "inherit_env") == "inherit_env")

    retry_strategy = Retry(
        total=3, connect=3, read=3, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"], raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy,
                         pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    # Inject proxy if configured globally
    proxy_url = _get_active_proxy()
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logger.debug(f"Session using proxy: {proxy_url}")

    return session


# ── Global proxy config ────────────────────────────────────────────────
_active_proxy: Optional[str] = None   # e.g. "http://127.0.0.1:7890"
_proxy_mode: str = "inherit_env"  # inherit_env | manual | disabled

def _get_active_proxy() -> Optional[str]:
    """Return currently configured proxy URL, or None. Honors explicit disabled mode."""
    global _active_proxy, _proxy_mode
    if _proxy_mode == "disabled":
        return None
    if _active_proxy:
        return _active_proxy
    if _proxy_mode == "inherit_env":
        # Also check env vars (common for system proxy / Clash / v2ray)
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            val = os.environ.get(key, "")
            if val:
                return val
    return None

def _set_active_proxy(url: Optional[str], *, mode: Optional[str] = None) -> None:
    """Set global proxy. mode=disabled disables env proxy inheritance too."""
    global _active_proxy, _proxy_mode, _shared_session, _shared_session_cf
    if mode is None:
        new_mode = "manual" if (url and str(url).strip()) else "disabled"
    else:
        new_mode = str(mode or "inherit_env").strip().lower().replace("-", "_")
        if new_mode not in {"inherit_env", "manual", "disabled"}:
            new_mode = "inherit_env"
    new_proxy = str(url).strip() if (url and new_mode == "manual") else None
    if new_proxy == _active_proxy and new_mode == _proxy_mode:
        return
    _active_proxy = new_proxy
    _proxy_mode = new_mode
    _shared_session = None
    _shared_session_cf = None
    if _proxy_mode == "manual" and _active_proxy:
        logger.info(f"Proxy set: {_active_proxy}")
    elif _proxy_mode == "inherit_env":
        logger.info("Proxy mode: inherit environment")
    else:
        logger.info("Proxy disabled, including environment proxies")


# ── Global DNS config ──────────────────────────────────────────────────────
import socket as _socket

_active_dns:     Optional[str] = None   # e.g. "1.1.1.1"
_orig_getaddrinfo = _socket.getaddrinfo  # save original

DNS_PRESETS: Dict[str, str] = {
    "System (default)":  "",
    "BebasDNS Default (DoH)": "https://dns.bebasid.com/dns-query",
    "BebasDNS Security (DoH)": "https://security.dns.bebasid.com/dns-query",
    "BebasDNS Unfiltered (DoH)": "https://unfiltered.dns.bebasid.com/dns-query",
    "BebasDNS Family (DoH)": "https://family.dns.bebasid.com/dns-query",
    "Cloudflare 1.1.1.1": "1.1.1.1",
    "Cloudflare 1.0.0.1": "1.0.0.1",
    "Google 8.8.8.8":     "8.8.8.8",
    "Google 8.8.4.4":     "8.8.4.4",
    "Quad9 9.9.9.9":      "9.9.9.9",
    "OpenDNS 208.67.222": "208.67.222.222",
    "AdGuard 94.140.14":  "94.140.14.14",
    "Custom…":            "__custom__",
}

BEBASDNS_DOH_VARIANTS: Dict[str, str] = {
    "default": "https://dns.bebasid.com/dns-query",
    "security": "https://security.dns.bebasid.com/dns-query",
    "unfiltered": "https://unfiltered.dns.bebasid.com/dns-query",
    "family": "https://family.dns.bebasid.com/dns-query",
}


_dns_bypass_local = _threading.local()
_dns_cache: Dict[Tuple[str, str, int], Tuple[float, str]] = {}
_DNS_CACHE_TTL_SECONDS = 300


def _build_dns_query_wire(host: str, qtype: int = 1) -> Tuple[int, bytes]:
    """Build a minimal DNS query packet. qtype=1 means A record."""
    import struct, random as _rnd
    tx_id = _rnd.randint(0, 65535)
    header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
    qname = b"".join(len(p).to_bytes(1, "big") + p.encode("idna") for p in host.rstrip(".").split(".")) + b"\x00"
    return tx_id, header + qname + struct.pack(">HH", qtype, 1)


def _parse_dns_address_response(data: bytes, tx_id: Optional[int] = None, qtype: int = 1) -> Optional[str]:
    """Parse the first A or AAAA answer from a DNS wire response."""
    try:
        import struct
        if len(data) < 12:
            return None
        rid, _flags, qdcount, ancount, _nscount, _arcount = struct.unpack(">HHHHHH", data[:12])
        if tx_id is not None and rid != tx_id:
            return None
        offset = 12

        def _skip_name(buf: bytes, off: int) -> int:
            while off < len(buf):
                length = buf[off]
                if length == 0:
                    return off + 1
                if length & 0xC0 == 0xC0:
                    return off + 2
                off += length + 1
            return off

        for _ in range(qdcount):
            offset = _skip_name(data, offset) + 4
        for _ in range(ancount):
            offset = _skip_name(data, offset)
            if offset + 10 > len(data):
                return None
            rtype, rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset+10])
            offset += 10
            if offset + rdlen > len(data):
                return None
            if qtype == 1 and rtype == 1 and rclass == 1 and rdlen == 4:
                return _store(".".join(str(b) for b in data[offset:offset+4]))
            if qtype == 28 and rtype == 28 and rclass == 1 and rdlen == 16:
                import ipaddress
                return str(ipaddress.IPv6Address(data[offset:offset+16]))
            offset += rdlen
    except Exception as e:
        logger.debug(f"DNS response parse failed: {e}")
    return None


def _doh_resolve_via(host: str, doh_url: str, qtype: int = 1) -> Optional[str]:
    """
    Resolve host through a DNS-over-HTTPS endpoint using RFC 8484 DNS wire format.
    The DoH endpoint itself is resolved with the system resolver to avoid recursion
    through our socket.getaddrinfo patch.
    """
    if not doh_url.lower().startswith("https://"):
        return None
    try:
        tx_id, payload = _build_dns_query_wire(host, qtype=qtype)
        headers = {
            "Accept": "application/dns-message",
            "Content-Type": "application/dns-message",
            "User-Agent": "Mozilla/5.0",
        }
        setattr(_dns_bypass_local, "enabled", True)
        try:
            session = requests.Session()
            session.trust_env = (_proxy_mode == "inherit_env")
            proxy = _get_active_proxy()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
            r = session.post(doh_url, data=payload, headers=headers, timeout=6)
        finally:
            setattr(_dns_bypass_local, "enabled", False)
        if r.status_code != 200:
            logger.debug(f"DoH {doh_url} returned HTTP {r.status_code} for {host}")
            return None
        return _parse_dns_address_response(r.content, tx_id=tx_id, qtype=qtype)
    except Exception as e:
        try:
            setattr(_dns_bypass_local, "enabled", False)
        except Exception:
            pass
        logger.debug(f"DoH resolve failed for {host} via {doh_url}: {e}")
        return None


def _dns_resolve_via(host: str, dns_ip: str, qtype: int = 1) -> Optional[str]:
    """
    Resolve `host` using either plain DNS or DNS-over-HTTPS. qtype=1 A, qtype=28 AAAA.
    Results are cached briefly to avoid repeated DoH/UDP lookups.
    """
    cache_key = (host.lower().rstrip("."), str(dns_ip), int(qtype))
    now = time.time()
    cached = _dns_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]
    def _store(ip: Optional[str]) -> Optional[str]:
        if ip:
            _dns_cache[cache_key] = (time.time() + _DNS_CACHE_TTL_SECONDS, ip)
        return ip
    if (dns_ip or "").lower().startswith("https://"):
        return _store(_doh_resolve_via(host, dns_ip, qtype=qtype))
    # ── dnspython ──────────────────────────────────────────────────────
    try:
        import dns.resolver as _dr
        r = _dr.Resolver(configure=False)
        r.nameservers = [dns_ip]
        r.timeout    = 3
        r.lifetime   = 5
        answers = r.resolve(host, "AAAA" if qtype == 28 else "A")
        return _store(str(answers[0]))
    except ImportError:
        pass
    except Exception:
        pass

    if qtype != 1:
        return None

    # ── Raw UDP DNS query fallback (no extra deps) ─────────────────────
    try:
        import struct, random as _rnd
        tx_id  = _rnd.randint(0, 65535)
        # Minimal DNS query packet for A record
        header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
        qname  = b"".join(len(p).to_bytes(1,"big") + p.encode()
                          for p in host.rstrip(".").split(".")) + b"\x00"
        packet = header + qname + struct.pack(">HH", 1, 1)   # QTYPE=A, QCLASS=IN

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(packet, (dns_ip, 53))
        data, _ = sock.recvfrom(512)
        sock.close()

        # Parse answer count from header
        ancount = struct.unpack(">H", data[6:8])[0]
        if ancount == 0:
            return None

        # Skip header (12) + question section
        offset = 12
        while data[offset] != 0:       # skip QNAME labels
            if data[offset] & 0xC0 == 0xC0:   # pointer
                offset += 2; break
            offset += data[offset] + 1
        else:
            offset += 1
        offset += 4  # skip QTYPE + QCLASS

        # Parse first answer RR
        if data[offset] & 0xC0 == 0xC0:   # pointer compression
            offset += 2
        else:
            while data[offset] != 0: offset += data[offset] + 1
            offset += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[offset:offset+10])
        offset += 10
        if rtype == 1 and rdlen == 4:   # A record
            return _store(".".join(str(b) for b in data[offset:offset+4]))
    except Exception:
        pass

    return None


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """socket.getaddrinfo override — resolve host via custom DNS or DoH."""
    global _active_dns
    if getattr(_dns_bypass_local, "enabled", False):
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    # Don't intercept localhost or bare IPs.
    try:
        _socket.inet_aton(host)
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except OSError:
        pass
    if host in ("localhost", "127.0.0.1", "::1"):
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    if not _active_dns:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    try:
        ip = _dns_resolve_via(host, _active_dns, qtype=1)
        if not ip and family in (0, getattr(_socket, "AF_INET6", -1)):
            ip = _dns_resolve_via(host, _active_dns, qtype=28)
        if ip:
            logger.debug(f"DNS [{_active_dns}] {host} → {ip}")
            return _orig_getaddrinfo(ip, port, family, type, proto, flags)
    except Exception as e:
        logger.debug(f"Custom DNS failed for {host}: {e}")
    # Fallback to system DNS
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def _set_active_dns(server: Optional[str]) -> None:
    """
    Set global DNS server. Patches socket.getaddrinfo globally.
    Pass None or "" to restore system DNS.
    Idempotent: applying the same value twice does not duplicate logs or rebuild sessions.
    """
    global _active_dns, _shared_session, _shared_session_cf, _dns_cache
    server = (server or "").strip()
    new_dns = server or None
    desired_getaddrinfo = _patched_getaddrinfo if new_dns else _orig_getaddrinfo
    if new_dns == _active_dns:
        if _socket.getaddrinfo is not desired_getaddrinfo:
            _socket.getaddrinfo = desired_getaddrinfo
        return

    _active_dns = new_dns
    _dns_cache.clear()
    if _active_dns:
        _socket.getaddrinfo = _patched_getaddrinfo
        if _active_dns.lower().startswith("https://"):
            logger.info(f"DNS-over-HTTPS resolver active → {_active_dns}")
        else:
            logger.info(f"DNS overridden → {_active_dns}")
    else:
        _socket.getaddrinfo = _orig_getaddrinfo
        logger.info("DNS restored to system default")

    # Rebuild sessions so connection pools use new resolution
    _shared_session = None
    _shared_session_cf = None


def _get_active_dns() -> Optional[str]:
    return _active_dns


class WebsiteDownloader:
    """
    Download a viewer into a clean offline package like:
      index.html
      project.json
      css/
      js/
      images/
      fonts/
      assets/

    Differences from v3:
      • output is flattened to viewer-style root layout
      • HTML/CSS/JS are analysed, not just project.json
      • external fonts/scripts/styles from index.html and CSS are localized too
      • JS string URLs are scanned (similar to Extract_Link.py + test.py workflow)
    """

    _quoted_asset_re = re.compile(
        r'(?P<quote>["\'])(?P<url>(?:https?:)?//[^"\']+|(?:\./|\.\./|/)?[^"\']+\.(?:json|txt|zip|js|mjs|css|png|jpe?g|gif|webp|avif|bmp|svg|ico|mp3|ogg|wav|m4a|aac|opus|woff2?|ttf|otf|eot)(?:\?[^"\']*)?)(?P=quote)',
        re.IGNORECASE,
    )
    _css_url_re = re.compile(r'url\(([^)]+)\)', re.IGNORECASE)
    _css_import_re = re.compile(
        r'@import\s+(?:url\()?["\']?([^"\')\s]+)["\']?\)?',
        re.IGNORECASE,
    )

    def __init__(self, start_url: str, output_folder: str, max_workers: int = 4,
                 ai_api_key: str = "", ai_provider: str = "",
                 ai_mode: str = "auto_fallback",
                 ai_budget: Optional[AIUsageBudget] = None) -> None:
        self.start_url     = start_url
        self.output_folder = output_folder
        self.max_workers   = max_workers
        self.ai_api_key    = ai_api_key
        self.ai_provider   = _normalize_ai_provider(ai_provider or _get_ai_provider())
        self.ai_mode       = _normalize_ai_mode(ai_mode or _load_settings().get("ai_mode", "auto_fallback"))
        self.ai_budget     = ai_budget
        # base_url = directory portion of start_url (used for resolving relative paths)
        parsed = urlparse(start_url)
        path   = parsed.path
        if not path.endswith('/'):
            path = path.rsplit('/', 1)[0] + '/'
        self.base_url = urlunparse(parsed._replace(path=path, query='', fragment=''))
        self.max_workers = max_workers
        self.session = create_retry_session()
        self._lock = threading.Lock()
        self._downloaded: Dict[str, str] = {}
        self._source_for_local: Dict[str, str] = {}
        self._used_local_paths: Set[str] = set()
        parsed = urlparse(start_url)
        self.base_origin = f"{parsed.scheme}://{parsed.netloc}"
        self.start_html_local = os.path.join(self.output_folder, "index.html")
        self._success_items: List[Dict[str, str]] = []
        self._failed_items: List[Dict[str, str]] = []
        self._project_aliases: List[str] = []
        self._collision_log: List[Dict[str, str]] = []

    def download(self) -> None:
        os.makedirs(self.output_folder, exist_ok=True)
        logger.info(f"Website download started: {self.start_url}")
        self._download_html(self.start_url, self.start_html_local)
        logger.info(f"Website saved: {self.output_folder}/")
        # Deep scan: find assets referenced in JS/CSS bundles not in HTML
        _deep_scan_and_download_assets(
            folder=self.output_folder,
            base_url=self.base_url,
            output_dir=self.output_folder,
            ai_api_key=self.ai_api_key,
            ai_provider=self.ai_provider,
            ai_mode=self.ai_mode,
            ai_budget=self.ai_budget,
        )

    def validate_integrity(self) -> Dict[str, List[str]]:
        """
        Feature 6: Walk all downloaded HTML/CSS/JS, find every src/href/url() 
        reference that points to a relative local file, verify it exists.
        Returns {"missing": [...], "ok": [...]}
        """
        import re as _re
        missing: List[str] = []
        ok_refs: List[str] = []

        _ref_re = _re.compile(
            r'(?:src|href|url)\s*[=:(]\s*["\']?'
            r'(?!https?://)(?!data:)(?!#)(?!javascript:)'
            r'([^"\')\s>]+)',
            _re.IGNORECASE
        )

        for root, _, files in os.walk(self.output_folder):
            for name in files:
                if os.path.splitext(name)[1].lower() not in {".html", ".css", ".js", ".mjs"}:
                    continue
                local_path = os.path.join(root, name)
                try:
                    text = pathlib.Path(local_path).read_text(encoding="utf-8", errors="ignore")
                    for m in _ref_re.finditer(text):
                        ref = m.group(1).strip().strip("'\"").split("?")[0].split("#")[0]
                        if not ref or ref.startswith(("http", "//", "data:")):
                            continue
                        abs_ref = os.path.normpath(os.path.join(root, ref))
                        if os.path.exists(abs_ref):
                            ok_refs.append(ref)
                        else:
                            missing.append(f"{os.path.relpath(local_path, self.output_folder)} → {ref}")
                except Exception:
                    pass

        if missing:
            logger.warning(
                f"Integrity check: {len(missing)} missing file reference(s), "
                f"{len(ok_refs)} OK"
            )
            for m in missing[:10]:
                logger.warning(f"  MISSING: {m}")
            if len(missing) > 10:
                logger.warning(f"  … and {len(missing)-10} more. See backup_report.txt")
        else:
            logger.info(f"Integrity check: all {len(ok_refs)} file references OK")

        return {"missing": missing, "ok": ok_refs}



    def localize_existing_text_assets(self) -> None:
        """Second-pass scan for index.html/css/js already downloaded."""
        for root, _, files in os.walk(self.output_folder):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in {".html", ".css", ".js", ".mjs"}:
                    continue
                local_path = os.path.join(root, name)
                source_url = self._source_for_local.get(os.path.abspath(local_path), self.start_url)
                try:
                    text = pathlib.Path(local_path).read_text(encoding="utf-8", errors="ignore")
                    if ext == ".css":
                        updated = self._process_css(text, source_url, local_path)
                    elif ext in {".js", ".mjs"}:
                        updated = self._process_js(text, source_url, local_path)
                    else:
                        updated = self._rewrite_direct_urls(text, source_url, local_path)
                    if updated != text:
                        pathlib.Path(local_path).write_text(updated, encoding="utf-8")
                        logger.info(f"  Re-analysed: {os.path.relpath(local_path, self.output_folder)}")
                except Exception as e:
                    logger.warning(f"  Failed to analyse {local_path}: {e}")

    def _headers_for(self, url: str) -> Dict[str, str]:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else self.base_origin + "/"
        headers = dict(self.session.headers)
        headers.update({"Referer": base, "Origin": base.rstrip("/")})
        return headers

    def _fetch(self, url: str) -> Optional[requests.Response]:
        headers = self._headers_for(url)
        try:
            r = fetch_response(url, extra_headers=headers, timeout=20, as_bytes=True)
            if r is None:
                self._failed_items.append({"url": url, "error": "request failed"})
                return None
            _throttle_bandwidth(len(r.content))  # bandwidth limiter
            return r
        except requests.exceptions.SSLError:
            # Retry without SSL verification
            logger.warning(f"  SSL error, retry tanpa verify: {url}")
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = self.session.get(url, timeout=20, allow_redirects=True,
                                     headers=headers, verify=False)
                r.raise_for_status()
                _throttle_bandwidth(len(r.content))
                return r
            except Exception as e2:
                err = str(e2)
                logger.warning(f"  Could not fetch (SSL fallback failed): {url}: {err}")
                self._failed_items.append({"url": url, "error": err})
                return None
        except requests.exceptions.ConnectionError as e:
            err = str(e).lower()
            if "connection reset" in err or "econnreset" in err:
                msg = f"Connection reset: {url}"
            elif "name or service not known" in err or "nodename" in err:
                msg = f"DNS error (domain tidak ditemukan): {url}"
            else:
                msg = f"Could not fetch {url}: {e}"
            logger.warning(f"  {msg}")
            self._failed_items.append({"url": url, "error": msg})
            return None
        except RecursionError:
            # Circular CSS/JS import chain — sentinel in _download_asset prevents
            # true infinite loop, but deep chains may still overflow stack.
            logger.warning(f"  Circular dependency (skipped): {url}")
            return None
        except Exception as e:
            err = str(e)
            logger.warning(f"  Could not fetch {url}: {err}")
            self._failed_items.append({"url": url, "error": err})
            return None

    def _normalize_remote_url(self, url: str, referrer_url: Optional[str] = None) -> Optional[str]:
        if not url:
            return None
        url = url.strip().strip('"\'')
        if url.startswith("data:") or url.startswith("javascript:") or url.startswith("mailto:") or url.startswith("#"):
            return None
        if url.startswith("//"):
            scheme = urlparse(referrer_url or self.start_url).scheme or "https"
            return f"{scheme}:{url}"
        if referrer_url:
            return urljoin(referrer_url, url)
        return url

    def _normalize_cache_key(self, url: str) -> str:
        """
        Normalize URL for _downloaded cache lookup.
        Query strings like ?v=abc123 (cache-busters) are stripped so that
        cyoa-config.js?v=b1234 and cyoa-config.js map to the same cached file,
        preventing duplicate _1 _2 suffixed copies.
        """
        try:
            p = urlparse(url)
            return f"{p.scheme}://{p.netloc}{p.path}"
        except Exception:
            return url

    def _safe_filename(self, url: str, fallback: str = "asset", ext_hint: str = "") -> str:
        parsed = urlparse(url)
        name = os.path.basename(parsed.path) or fallback
        name = clean_url_path_component(name)
        root, ext = os.path.splitext(name)
        if not ext and ext_hint:
            ext = ext_hint
        if not root:
            root = fallback
        if not ext:
            ext = ".bin"
        return f"{root}{ext}"

    def _kind_from(self, url: str, content_type: str = "", preferred_kind: str = "") -> str:
        if preferred_kind:
            return preferred_kind
        lower_ct = (content_type or "").lower()
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1]

        if "text/css" in lower_ct or ext in STYLE_EXTENSIONS:
            return "css"
        if "javascript" in lower_ct or ext in SCRIPT_EXTENSIONS:
            return "js"
        if "font" in lower_ct or ext in FONT_EXTENSIONS:
            return "fonts"
        if lower_ct.startswith("image/") or ext in IMAGE_EXTENSIONS:
            return "images"
        if lower_ct.startswith("audio/") or lower_ct.startswith("video/") or ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            return "media"
        if lower_ct == "application/json" or path.endswith(("project.json", "project.txt", "project.zip")) or ext in {".json", ".txt", ".zip"}:
            return "json"
        if "text/html" in lower_ct or ext in {".html", ".htm"}:
            return "html"
        return "assets"

    def _allocate_local_path(self, url: str, content_type: str = "", preferred_kind: str = "") -> str:
        kind = self._kind_from(url, content_type=content_type, preferred_kind=preferred_kind)
        if kind == "html":
            return self.start_html_local
        if kind == "json" and urlparse(url).path.lower().endswith("project.json"):
            return os.path.join(self.output_folder, "project.json")

        # ── Preserve original relative path from site root ────────────────
        parsed       = urlparse(url)
        start_parsed = urlparse(self.start_url)
        if parsed.netloc == start_parsed.netloc:
            start_dir  = start_parsed.path.rstrip("/") + "/"
            asset_path = unquote(parsed.path)
            if asset_path.startswith(start_dir):
                rel_parts = asset_path[len(start_dir):]   # e.g. "js/shared/components/Foo.js"
            elif asset_path.startswith("/"):
                # Asset is on same domain but above start_dir (e.g. /js/foo.js for start /paradise/)
                # Preserve from root so js/shared/components/ structure is kept
                rel_parts = asset_path.lstrip("/")
            else:
                rel_parts = asset_path

            if rel_parts:
                local_candidate = _safe_join(self.output_folder, rel_parts)
                os.makedirs(os.path.dirname(local_candidate), exist_ok=True)
                local = local_candidate
                root, ext = os.path.splitext(local)
                counter = 1
                while local in self._used_local_paths:
                    local = f"{root}_{counter}{ext}"
                    counter += 1
                if local != local_candidate:
                    logger.warning(
                        f"  Path collision: {os.path.relpath(local_candidate, self.output_folder)} "
                        f"already taken → renamed to {os.path.relpath(local, self.output_folder)}"
                    )
                    self._collision_log.append({
                        "url": url,
                        "original_path": os.path.relpath(local_candidate, self.output_folder).replace("\\", "/"),
                        "saved_as":      os.path.relpath(local, self.output_folder).replace("\\", "/"),
                    })
                self._used_local_paths.add(local)
                return local

        # ── Fallback: cross-domain URL — use type-based flat folder ──────────
        ext_hint = {
            "css": ".css",
            "js": ".js",
            "fonts": ".woff2",
            "images": ".png",
            "json": ".json",
            "media": ".bin",
        }.get(kind, "")
        filename = self._safe_filename(url, fallback=kind[:-1] if kind.endswith("s") else kind, ext_hint=ext_hint)
        folder = os.path.join(self.output_folder, kind if kind not in {"html", "json"} else "assets")
        os.makedirs(folder, exist_ok=True)

        local = os.path.join(folder, filename)
        root, ext = os.path.splitext(local)
        counter = 1
        while local in self._used_local_paths:
            local = f"{root}_{counter}{ext}"
            counter += 1
        if counter > 1:
            original_local = os.path.join(folder, filename)
            logger.warning(
                f"  Path collision (external): {os.path.relpath(original_local, self.output_folder)} "
                f"already taken → renamed to {os.path.relpath(local, self.output_folder)}"
            )
            self._collision_log.append({
                "url": url,
                "original_path": os.path.relpath(original_local, self.output_folder).replace("\\", "/"),
                "saved_as":      os.path.relpath(local, self.output_folder).replace("\\", "/"),
            })
        self._used_local_paths.add(local)
        return local

    def _rel(self, from_file: str, to_file: str) -> str:
        return os.path.relpath(to_file, os.path.dirname(from_file)).replace("\\", "/")

    def _download_asset(self, url: str, preferred_kind: str = "", referrer_url: Optional[str] = None) -> Optional[str]:
        full = self._normalize_remote_url(url, referrer_url)
        if not full:
            return None

        with self._lock:
            # Check both full URL and path-only key (strips ?v=cache_buster)
            cache_key = self._normalize_cache_key(full)
            if full in self._downloaded:
                return self._downloaded[full]
            if cache_key != full and cache_key in self._downloaded:
                cached = self._downloaded[cache_key]
                self._downloaded[full] = cached   # alias
                return cached
            # ── Anti-recursion sentinel ────────────────────────────────────
            self._downloaded[full] = None   # sentinel: in-progress

        r = self._fetch(full)

        # ── JS root-relative fallback ──────────────────────────────
        # Paths in JS/data files like "images/headers/foo.avif" are
        # often intended relative to the page root, NOT the JS file.
        # Example: js/data.js has "images/foo.avif" → wrong resolve is
        #   js/images/foo.avif, correct is images/foo.avif (page root).
        # If the fetch failed AND the raw path is relative AND the
        # referrer was a JS file, retry from page root.
        if r is None and referrer_url and not url.startswith(("http", "//", "data:", "#")):
            ref_path = urlparse(referrer_url).path.lower()
            if ref_path.endswith((".js", ".mjs")):
                alt = self._normalize_remote_url(url, self.start_url)
                if alt and alt != full:
                    # Check cache first — same raw string may appear multiple times in data.js
                    with self._lock:
                        if alt in self._downloaded:
                            return self._downloaded[alt]
                    r_alt = self._fetch(alt)
                    if r_alt:
                        logger.info(f"  JS root-fallback: {url} → {alt}")
                        r    = r_alt
                        full = alt

        if not r:
            return None

        content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
        local = self._allocate_local_path(full, content_type=content_type, preferred_kind=preferred_kind)
        abs_local = os.path.abspath(local)
        os.makedirs(os.path.dirname(local), exist_ok=True)

        if "text/css" in content_type or local.lower().endswith(".css"):
            content = self._process_css(_safe_response_text(r), full, local)
            pathlib.Path(local).write_text(content, encoding="utf-8")
        elif "javascript" in content_type or local.lower().endswith((".js", ".mjs")):
            content = self._process_js(_safe_response_text(r), full, local)
            pathlib.Path(local).write_text(content, encoding="utf-8")
        elif "text/html" in content_type or local.lower().endswith((".html", ".htm")):
            self._download_html(full, local_html=local, html_text=_safe_response_text(r))
        else:
            with open(local, "wb") as f:
                f.write(r.content)

        with self._lock:
            self._downloaded[full] = local
            # Also register path-only key so query-string variants hit cache
            ck = self._normalize_cache_key(full)
            if ck != full:
                self._downloaded[ck] = local
            self._source_for_local[abs_local] = full

        self._success_items.append({
            "url": full,
            "local": os.path.relpath(local, self.output_folder).replace("\\", "/"),
            "kind": self._kind_from(full, content_type=content_type, preferred_kind=preferred_kind),
        })
        logger.info(f"  Asset: {os.path.relpath(local, self.output_folder)}")
        return local

    def _asset_kind_from_path(self, candidate: str) -> str:
        path = urlparse(candidate).path.lower()
        ext = os.path.splitext(path)[1]
        if path.endswith(("project.json", "project.txt", "project.zip")) or ext in {".json", ".txt", ".zip"}:
            return "json"
        if ext in FONT_EXTENSIONS:
            return "fonts"
        if ext in IMAGE_EXTENSIONS:
            return "images"
        if ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS:
            return "media"
        if ext in STYLE_EXTENSIONS:
            return "css"
        if ext in SCRIPT_EXTENSIONS:
            return "js"
        return "assets"

    def _should_download_from_text(self, candidate: str) -> bool:
        c = candidate.strip().strip('"\'')
        if not c or c.startswith(("data:", "javascript:", "mailto:", "#")):
            return False
        if "w3.org/" in c:
            return False
        path = urlparse(c).path.lower()
        if path.endswith(("project.json", "project.txt", "project.zip")):
            return True
        ext = os.path.splitext(path)[1]
        if ext in FONT_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | STYLE_EXTENSIONS | SCRIPT_EXTENSIONS | {".json", ".txt", ".zip"}:
            return True
        return False

    def _rewrite_direct_urls(self, text: str, referrer_url: str, local_text_path: str) -> str:
        def repl(m: re.Match) -> str:
            original = m.group("url")
            if not self._should_download_from_text(original):
                return m.group(0)
            local = self._download_asset(
                original,
                preferred_kind=self._asset_kind_from_path(original),
                referrer_url=referrer_url,
            )
            if not local:
                return m.group(0)
            rel = self._rel(local_text_path, local)
            return f'{m.group("quote")}{rel}{m.group("quote")}'
        return self._quoted_asset_re.sub(repl, text)

    def _process_css(self, css: str, css_url: str, css_local: str) -> str:
        def repl_import(m: re.Match) -> str:
            raw = m.group(1).strip().strip('"\'')
            full = self._normalize_remote_url(raw, css_url)
            if not full:
                return m.group(0)
            local = self._download_asset(full, preferred_kind="css", referrer_url=css_url)
            if not local:
                return m.group(0)
            return f'@import url("{self._rel(css_local, local)}")'

        def repl_url(m: re.Match) -> str:
            raw = m.group(1).strip().strip('"\'')
            full = self._normalize_remote_url(raw, css_url)
            if not full:
                return m.group(0)
            kind = self._asset_kind_from_path(full)
            local = self._download_asset(full, preferred_kind=kind, referrer_url=css_url)
            if not local:
                return m.group(0)
            return f'url("{self._rel(css_local, local)}")'

        css = self._css_import_re.sub(repl_import, css)
        css = self._css_url_re.sub(repl_url, css)
        css = self._rewrite_direct_urls(css, css_url, css_local)
        return css

    # Patterns that identify webpack/Vite application bundles.
    # These files must NOT have their internal paths rewritten —
    # the bundle's own module references (project.json, chunk paths, etc.)
    # are resolved at runtime by webpack, not by URL.
    #
    # webpack hashes: lowercase hex, 8-20 chars  e.g. app.c533aa25.js
    # Vite hashes (dot):        base62, 6-12 chars  e.g. app.B6d7tc9y.js
    # Vite hashes (underscore): Neocities variant   e.g. app_BuGW6RFa.js
    # CYOA Manager:   working.js (ICC Original 1.4MB full bundle)
    _APP_BUNDLE_RE = re.compile(
        r'(?:^|/)(?:app|main|index|chunk-vendors?|vendors?|runtime|polyfills?|core|working)'
        r'(?:[._-][a-zA-Z0-9_-]{4,})?(?:-legacy)?(?:[._-][a-zA-Z0-9_-]{4,})?'
        r'\.m?js$',
        re.IGNORECASE,
    )

    # ── Dynamic loader patterns ────────────────────────────────────────────
    # JS files that compute asset URLs dynamically at runtime.
    # These must NOT be path-rewritten — URLs are computed by the browser,
    # not as literal strings we can safely replace.
    # Each tuple: (detection_pattern, url_extractor_pattern, url_base_func)
    _DYNAMIC_LOADER_PATTERNS = [
        # ICC Plus v2 core.js: basePath = new URL('../', currentScript.src)
        # Extracts: basePath + 'relpath'
        (
            re.compile(r'basePath\s*=\s*new URL\(["\']\.\./', re.IGNORECASE),
            re.compile(r"""basePath\s*\+\s*['"]([^'"]+)['"]"""),
            lambda js_url: __import__('urllib.parse', fromlist=['urljoin']).urljoin(js_url, "../"),
        ),
        # Generic: __webpack_public_path__ / __publicPath__
        (
            re.compile(r'__webpack_public_path__|__publicPath__'),
            re.compile(r"""['"]([^'"]+\.(?:js|css|mjs))['"]"""),
            lambda js_url: __import__('urllib.parse', fromlist=['urljoin']).urljoin(js_url, "./"),
        ),
    ]

    def _detect_dynamic_loader(self, js: str) -> Optional[tuple]:
        """
        Detect if a JS file is a dynamic asset loader (like ICC Plus v2 core.js).
        Returns (extractor_re, base_url_fn) if detected, else None.
        Prevents incorrect URL rewriting for files that compute paths at runtime.
        """
        for detect_re, extract_re, base_fn in self._DYNAMIC_LOADER_PATTERNS:
            if detect_re.search(js):
                return (extract_re, base_fn)
        return None

    def _is_app_bundle(self, js_url: str) -> bool:
        """True for webpack/Vite bundles that must NOT be path-rewritten."""
        path = urlparse(js_url).path
        return bool(self._APP_BUNDLE_RE.search(path))

    def _process_js(self, js: str, js_url: str, js_local: str) -> str:
        """
        Rewrite asset URLs inside a JS file.

        Guard 1 — Dynamic loaders (core.js, webpack bootstrap, etc.):
          Files that compute asset URLs at runtime (basePath, __webpack_public_path__,
          import.meta.url, etc.) are detected via _detect_dynamic_loader().
          We download the assets they reference using the correct server URL,
          then return the file UNCHANGED so the browser computes paths correctly.

        Guard 2 — App bundles (app.*.js, chunk-vendors.*.js, etc.):
          Webpack/Vite bundles are skipped entirely — their internal paths are
          resolved by the module bundler, not as literal filesystem URLs.
        """
        # Guard 1: dynamic loader
        loader = self._detect_dynamic_loader(js)
        if loader:
            extract_re, base_fn = loader
            base_url = base_fn(js_url)
            logger.info(f"  Dynamic loader detected: {js_url.split('/')[-1]} (base: {base_url})")
            for m in extract_re.finditer(js):
                asset_rel = m.group(1)
                asset_url = urljoin(base_url, asset_rel)
                kind      = self._asset_kind_from_path(asset_url)
                self._download_asset(asset_url, preferred_kind=kind, referrer_url=js_url)
            return js  # UNCHANGED — browser resolves paths at runtime

        # Guard 2: app bundle
        if self._is_app_bundle(js_url):
            logger.debug(f"  Skip JS rewrite (app bundle): {js_url.split('/')[-1]}")
            return js

        return self._rewrite_direct_urls(js, js_url, js_local)

    def _rewrite_css_url(self, m: "re.Match", css_url: str, css_local: str) -> str:
        """Rewrite a single CSS url() match to a local path."""
        raw = m.group(1).strip().strip('"\'')
        full = self._normalize_remote_url(raw, css_url)
        if not full:
            return m.group(0)
        kind  = self._asset_kind_from_path(full)
        local = self._download_asset(full, preferred_kind=kind, referrer_url=css_url)
        if not local:
            return m.group(0)
        return f'url("{self._rel(css_local, local)}")'

    def _set_attr_local(self, tag, attr: str, page_url: str, local_html: str, preferred_kind: str = "") -> None:
        value = tag.get(attr)
        if not value:
            return
        if attr == "srcset":
            parts = []
            for chunk in value.split(","):
                bits = chunk.strip().split()
                if not bits:
                    continue
                asset = bits[0]
                suffix = " " + " ".join(bits[1:]) if len(bits) > 1 else ""
                local = self._download_asset(asset, preferred_kind=preferred_kind, referrer_url=page_url)
                if local:
                    parts.append(self._rel(local_html, local) + suffix)
                else:
                    parts.append(chunk.strip())
            tag[attr] = ", ".join(parts)
            return

        local = self._download_asset(value, preferred_kind=preferred_kind, referrer_url=page_url)
        if not local:
            # Fallback: maybe the same file was already downloaded from a different path
            # (e.g. <link rel="preload" href="js/polyfills.js"> downloaded it, but
            #  <script src="polyfills.js"> references it without the js/ prefix).
            # Search _downloaded cache for any URL whose basename matches.
            basename = value.rstrip("/").split("?")[0].split("/")[-1]
            if basename:
                for cached_url, cached_local in self._downloaded.items():
                    if cached_local and cached_url.split("?")[0].split("/")[-1] == basename:
                        local = cached_local
                        logger.debug(f"  Basename fallback: {value!r} → {os.path.relpath(local, self.output_folder)}")
                        break
        if local:
            tag[attr] = self._rel(local_html, local)

    def _download_html(self, url: str, local_html: Optional[str] = None, html_text: Optional[str] = None) -> None:
        local_html = local_html or self.start_html_local
        abs_local = os.path.abspath(local_html)

        if html_text is None:
            r = self._fetch(url)
            if not r:
                return
            html_text = _safe_response_text(r)

        soup = BeautifulSoup(html_text, "html.parser")
        os.makedirs(os.path.dirname(local_html), exist_ok=True)

        for tag in soup.find_all("link"):
            rel_values = {str(v).lower() for v in (tag.get("rel") or [])}
            href = tag.get("href")
            if not href or href.startswith(("data:", "javascript:", "#", "mailto:")):
                continue

            # Resolve absolute-path hrefs (e.g. /favicon.ico) against page origin
            # so they download from correct domain even when we're in a subpath
            if href.startswith("/") and not href.startswith("//"):
                parsed_page = urlparse(url)
                href_resolved = f"{parsed_page.scheme}://{parsed_page.netloc}{href}"
                tag["href"] = href_resolved
                href = href_resolved

            href_lower = href.lower().split("?")[0]  # strip query string for ext check

            if "stylesheet" in rel_values:
                self._set_attr_local(tag, "href", url, local_html, preferred_kind="css")

            elif rel_values & {"icon", "shortcut", "apple-touch-icon",
                               "apple-touch-icon-precomposed", "mask-icon",
                               "image_src"}:
                self._set_attr_local(tag, "href", url, local_html, preferred_kind="images")

            elif "manifest" in rel_values:
                # PWA manifest.json — download as json asset
                self._set_attr_local(tag, "href", url, local_html, preferred_kind="json")

            elif rel_values & {"preload", "prefetch", "modulepreload"}:
                # Preload/prefetch: download based on 'as' attribute or extension
                as_val = (tag.get("as") or "").lower()
                if as_val in ("image", "fetch") or any(
                    href_lower.endswith(ext)
                    for ext in IMAGE_EXTENSIONS | {".ico"}
                ):
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind="images")
                elif as_val == "font" or any(href_lower.endswith(ext) for ext in FONT_EXTENSIONS):
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind="fonts")
                elif as_val in ("script", "worker") or href_lower.endswith((".js", ".mjs")):
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind="js")
                elif as_val == "style" or href_lower.endswith(".css"):
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind="css")

            elif href_lower.endswith("project.json"):
                self._set_attr_local(tag, "href", url, local_html, preferred_kind="json")

            else:
                # Catch-all: any <link href="..."> where href looks like a downloadable asset
                # (regardless of rel value — e.g. rel="license" href="banner.png")
                ext = os.path.splitext(href_lower)[1]
                if ext in IMAGE_EXTENSIONS | FONT_EXTENSIONS | {".ico", ".webmanifest"}:
                    kind = "fonts" if ext in FONT_EXTENSIONS else "images"
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind=kind)
                elif ext in SCRIPT_EXTENSIONS | {".js", ".mjs"}:
                    self._set_attr_local(tag, "href", url, local_html, preferred_kind="js")


        for tag in soup.find_all("script", src=True):
            src_val = tag.get("src", "")
            if "youtube.com/iframe_api" in src_val or "youtube-nocookie.com/iframe_api" in src_val:
                stub_local = self._ensure_youtube_iframe_api_stub()
                tag["src"] = self._rel(local_html, stub_local)
                continue
            self._set_attr_local(tag, "src", url, local_html, preferred_kind="js")

        # Replace YouTube <iframe> embeds with an offline placeholder.
        # Direct YouTube iframes cannot work offline regardless of the JS stub —
        # they require a live connection to youtube.com.
        for tag in soup.find_all("iframe"):
            iframe_src = tag.get("src", "") or tag.get("data-src", "")
            if _YOUTUBE_URL_RE.search(iframe_src):
                video_id = ""
                m = re.search(r'/embed/([A-Za-z0-9_-]+)', iframe_src)
                if m:
                    video_id = m.group(1)
                yt_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else iframe_src
                w = tag.get("width", "560")
                h = tag.get("height", "315")
                placeholder_html = (
                    f'<div style="width:{w}px;height:{h}px;background:#111;color:#aaa;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'flex-direction:column;font-family:sans-serif;border-radius:6px;'
                    f'border:1px solid #333;box-sizing:border-box;">'
                    f'<span style="font-size:32px">▶</span>'
                    f'<span style="margin-top:8px;font-size:12px">YouTube (offline unavailable)</span>'
                    f'<a href="{yt_url}" target="_blank" '
                    f'style="margin-top:6px;font-size:11px;color:#4af">Open on YouTube</a>'
                    f'</div>'
                )
                tag.replace_with(BeautifulSoup(placeholder_html, "html.parser"))
                logger.info(f"  YouTube iframe replaced with offline placeholder: {yt_url}")
                continue

        for tag in soup.find_all(["img", "audio", "video", "source"]):
            if tag.get("src"):
                kind = "images" if tag.name == "img" else "media"
                self._set_attr_local(tag, "src", url, local_html, preferred_kind=kind)
            if tag.get("srcset"):
                self._set_attr_local(tag, "srcset", url, local_html, preferred_kind="images")
            if tag.get("poster"):
                self._set_attr_local(tag, "poster", url, local_html, preferred_kind="images")

        # ── Inline <style> @font-face and url() ─────────────────────────────
        # Fonts declared directly in <style> tags (not linked CSS) are missed
        # unless we process them explicitly here.
        for style_tag in soup.find_all("style"):
            raw_css = style_tag.string or ""
            if not raw_css.strip():
                continue
            # Process as if it were a CSS file at the page URL
            new_css = self._process_css(raw_css, url, local_html)
            if new_css != raw_css:
                style_tag.string = new_css

        # ── Inline style="" attributes (background-image, etc.) ─────────────
        for tag in soup.find_all(True, style=True):
            raw_style = tag.get("style", "")
            if raw_style and "url(" in raw_style:
                new_style = self._css_url_re.sub(
                    lambda m: self._rewrite_css_url(m, url, local_html),
                    raw_style,
                )
                if new_style != raw_style:
                    tag["style"] = new_style

        html_output = str(soup)
        # NOTE: do NOT call _rewrite_direct_urls(html_output) here.
        # All tag attributes have already been rewritten by _set_attr_local above.
        # Calling _rewrite_direct_urls on str(soup) would try to re-download
        # the already-localized relative paths (e.g. "images/favicon.ico"),
        # resolve them against the page URL (wrong!), and corrupt the HTML.
        # The second pass (localize_existing_text_assets) handles any missed URLs.
        pathlib.Path(local_html).write_text(html_output, encoding="utf-8")

        with self._lock:
            self._downloaded[url] = local_html
            self._source_for_local[abs_local] = url

        logger.info(f"  Page: {os.path.relpath(local_html, self.output_folder)}")

    # ── Methods that were previously monkey-patched — now proper class methods ──

    def _ensure_youtube_iframe_api_stub(self) -> str:
        stub_local = os.path.join(self.output_folder, "js", "youtube-iframe-api-stub.js")
        os.makedirs(os.path.dirname(stub_local), exist_ok=True)
        # Always overwrite — ensures new HTML5 audio version replaces old dummy stub
        stub = r"""(function(){
  if (window.YT && window.YT.Player && window.YT.__cyoa_stub__) return;

  function _isLocalAudio(id){
    return typeof id === 'string' && (
      id.indexOf('/') !== -1 ||
      id.indexOf('.mp3') !== -1 || id.indexOf('.ogg') !== -1 ||
      id.indexOf('.wav') !== -1 || id.indexOf('.m4a') !== -1 ||
      id.indexOf('.aac') !== -1 || id.indexOf('.opus') !== -1
    );
  }

  function AudioPlayer(id, options){
    // ICC Plus uses "bgm-player" in newer versions, "bgm" in older ones
    this._el     = typeof id === 'string' ? document.getElementById(id) : id;
    this._opts   = options || {};
    this._state  = -1;
    this._volume = 100;
    this._muted  = false;
    this._audio  = null;
    this._events = this._opts.events || {};
    this._videoData  = {video_id:'', title:''};
    this.playerInfo  = {videoData: this._videoData};
    // Expose on window so ICC Plus can find it by element ID
    if (typeof id === 'string' && id) window['__ytplayer_'+id] = this;
    var self = this;
    setTimeout(function(){
      try { if (typeof self._events.onReady === 'function') self._events.onReady({target:self}); } catch(e){}
    }, 0);
  }

  AudioPlayer.prototype._loadAudio = function(videoId){
    if (!_isLocalAudio(videoId)) return;
    var src = videoId;
    if (src.charAt(0) !== '/' && src.indexOf('://') === -1){
      var base = window.location.href.replace(/\/[^\/]*$/, '/');
      src = base + src;
    }
    if (!this._audio){
      this._audio = new Audio();
      var self = this;
      this._audio.addEventListener('ended', function(){
        self._state = 0;
        try { if (typeof self._events.onStateChange === 'function') self._events.onStateChange({data:0}); } catch(e){}
      });
    }
    this._audio.src = src;
    this._audio.volume = this._volume / 100;
    this._audio.muted  = this._muted;
    this._videoData.video_id = videoId;
    this._videoData.title    = videoId.split('/').pop().replace(/\.[^.]+$/, '');
    this.playerInfo.videoData = this._videoData;
  };
  AudioPlayer.prototype.loadVideoById = function(a){
    var vid = typeof a === 'object' ? (a.videoId||'') : (a||'');
    this._loadAudio(vid);
    if (this._audio && _isLocalAudio(vid)){
      var self = this;
      this._state = 1;
      // ICC Plus retries without CORS if crossOrigin fails (noCors fallback)
      var tryPlay = function(withCors){
        if(withCors) self._audio.crossOrigin = 'anonymous';
        else self._audio.removeAttribute('crossOrigin');
        var p = self._audio.play();
        if(p && typeof p.catch === 'function'){
          p.catch(function(err){
            if(withCors && (String(err).indexOf('CORS') !== -1 || String(err).indexOf('cross') !== -1)){
              // Retry without CORS
              self._audio.src = self._audio.src; // reload
              tryPlay(false);
            } else {
              self._state = -1;
              console.warn('[CYOA stub] Audio play failed:', err);
            }
          });
        }
      };
      tryPlay(true);
      try { if (typeof self._events.onStateChange === 'function') self._events.onStateChange({data:1}); } catch(e){}
    }
  };
  AudioPlayer.prototype.cueVideoById   = function(a){ this._loadAudio(typeof a==='object'?a.videoId||'':a||''); };
  AudioPlayer.prototype.playVideo      = function(){ if(this._audio){this._audio.play().catch(function(){});this._state=1;} };
  AudioPlayer.prototype.pauseVideo     = function(){ if(this._audio){this._audio.pause();this._state=2;} };
  AudioPlayer.prototype.stopVideo      = function(){ if(this._audio){this._audio.pause();this._audio.currentTime=0;this._state=0;} };
  AudioPlayer.prototype.seekTo         = function(s){ if(this._audio)this._audio.currentTime=s; };
  AudioPlayer.prototype.destroy        = function(){ if(this._audio){this._audio.pause();this._audio=null;} };
  AudioPlayer.prototype.getPlayerState = function(){ return this._state; };
  AudioPlayer.prototype.getDuration    = function(){ return this._audio?this._audio.duration||0:0; };
  AudioPlayer.prototype.getCurrentTime = function(){ return this._audio?this._audio.currentTime||0:0; };
  AudioPlayer.prototype.setVolume      = function(v){ this._volume=v; if(this._audio)this._audio.volume=v/100; };
  AudioPlayer.prototype.getVolume      = function(){ return this._volume; };
  AudioPlayer.prototype.mute           = function(){ this._muted=true; if(this._audio)this._audio.muted=true; };
  AudioPlayer.prototype.unMute         = function(){ this._muted=false; if(this._audio)this._audio.muted=false; };
  AudioPlayer.prototype.isMuted        = function(){ return this._muted; };
  AudioPlayer.prototype.setLoop        = function(l){ if(this._audio)this._audio.loop=l; };

  window.YT = window.YT || {};
  window.YT.Player      = AudioPlayer;
  window.YT.__cyoa_stub__ = true;
  window.YT.PlayerState = {UNSTARTED:-1,ENDED:0,PLAYING:1,PAUSED:2,BUFFERING:3,CUED:5};

  // Fire BOTH callback names — viewers vary:
  // Standard:    window.onYouTubeIframeAPIReady()   (documented by Google)
  // New_Viewer:  window.onYouTubeIframeAPI()        (custom callback)
  function _fireCallbacks(){
    var cbs = ['onYouTubeIframeAPIReady', 'onYouTubeIframeAPI'];
    for (var i=0; i<cbs.length; i++){
      try { if (typeof window[cbs[i]] === 'function') window[cbs[i]](); } catch(e){}
    }
  }
  // Fire once immediately (for scripts already parsed)
  setTimeout(_fireCallbacks, 0);
  // Fire again after DOMContentLoaded in case viewer waits for it
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ setTimeout(_fireCallbacks, 50); });
  } else {
    setTimeout(_fireCallbacks, 50);
  }
})();
"""
        pathlib.Path(stub_local).write_text(stub, encoding="utf-8")
        return stub_local

    def write_project_payload(self, project_url: str, project_text: str) -> None:
        root_local = os.path.join(self.output_folder, "project.json")
        pathlib.Path(root_local).write_text(project_text, encoding="utf-8")
        root_abs = os.path.abspath(root_local)
        self._source_for_local[root_abs] = project_url or self.start_url
        if project_url:
            with self._lock:
                self._downloaded[project_url] = root_local

        alias_paths: Set[str] = set()
        if project_url:
            parsed = urlparse(project_url)
            basename = os.path.basename(parsed.path)
            if basename and basename.lower() != "project.json":
                alias_paths.add(os.path.join(self.output_folder, basename))

        for alias in alias_paths:
            if os.path.abspath(alias) == root_abs:
                continue
            os.makedirs(os.path.dirname(alias), exist_ok=True)
            pathlib.Path(alias).write_text(project_text, encoding="utf-8")
            rel_alias = os.path.relpath(alias, self.output_folder).replace("\\", "/")
            self._project_aliases.append(rel_alias)
            self._source_for_local[os.path.abspath(alias)] = project_url or self.start_url
            logger.info(f"  Project alias: {rel_alias}")

    def write_manifest(self, project_url: str = "") -> str:
        def _uniq(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
            seen: Set[tuple] = set()
            out = []
            for item in items:
                key = (item.get("url"), item.get("local"), item.get("kind"), item.get("error"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
            return out

        success = _uniq(self._success_items)
        failed  = _uniq(self._failed_items)

        grouped_success: Dict[str, List[str]] = {}
        for item in success:
            grouped_success.setdefault(item.get("kind", "assets"), []).append(item.get("local", ""))

        grouped_failed: Dict[str, List[str]] = {}
        for item in failed:
            item_url = item.get("url", "")
            ext  = os.path.splitext(urlparse(item_url).path)[1].lower()
            kind = (
                "media"  if ext in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS else
                "images" if ext in IMAGE_EXTENSIONS else
                "fonts"  if ext in FONT_EXTENSIONS else
                "css"    if ext in STYLE_EXTENSIONS else
                "js"     if ext in SCRIPT_EXTENSIONS else
                "assets"
            )
            grouped_failed.setdefault(kind, []).append(item_url)

        report_text = format_backup_report_text(
            start_url=self.start_url,
            project_url=project_url,
            project_root="project.json",
            project_aliases=self._project_aliases,
            downloaded=success,
            failed=failed,
            downloaded_groups=grouped_success,
            failed_groups=grouped_failed,
            notes=["Engine mode: standard website", "Project payload written to project.json root."],
        )

        # Append collision log if any
        if self._collision_log:
            lines = [
                "",
                "=" * 60,
                "PATH COLLISIONS",
                "=" * 60,
                "These files had name conflicts and were renamed.",
                "The JS/CSS referencing them has been updated accordingly.",
                "",
            ]
            for entry in self._collision_log:
                lines.append(f"URL      : {entry['url']}")
                lines.append(f"Wanted   : {entry['original_path']}")
                lines.append(f"Saved as : {entry['saved_as']}")
                lines.append("")
            report_text += "\n".join(lines)

        manifest_path = os.path.join(self.output_folder, "backup_report.txt")
        pathlib.Path(manifest_path).write_text(report_text, encoding="utf-8")
        logger.info(f"  Manifest: {os.path.relpath(manifest_path, self.output_folder)}")
        if failed:
            logger.info(
                "  Website asset failure details are included in backup_report.txt."
            )
        return manifest_path

# ─────────────────────────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    # Auto-register bundled offline viewers (no-op if already registered)
    try:
        _auto_register_bundled_viewers()
    except Exception:
        pass
    global wait_time

    if len(sys.argv) == 1:
        launch_gui()
        return

    _cli_saved_settings = _load_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Download and process a CYOA project. Supports embedded JSON, ZIP, "
            "both formats, or a full offline website. Run without arguments for the GUI."
        )
    )
    parser.add_argument("url", nargs="?", default="", help="URL of the CYOA project.")
    parser.add_argument("filename", nargs="?", default="", help="Optional output filename.")
    parser.add_argument("-u", "--url", dest="url_opt", default="",
                        help="URL of the CYOA project. Overrides positional URL when provided.")
    parser.add_argument("-o", "--output", dest="output_dir", default=os.getcwd(),
                        help="Output directory. Created automatically if missing.")
    parser.add_argument("-L", "--list", dest="list_file", default="",
                        help="Batch input source (.txt/.csv/.xlsx/.xls or remote CSV/Google Sheets URL) with URLs.")
    parser.add_argument("-z", "--zip",     action="store_true", help="Save as ZIP with external images.")
    parser.add_argument("-b", "--both",    action="store_true", help="Save both embedded JSON and ZIP.")
    parser.add_argument("-W", "--website", action="store_true",
                        help="Download the full viewer website + all assets as a ZIP.")
    parser.add_argument("--website-folder", action="store_true",
                        help="Download the full viewer website + all assets as a folder (no ZIP).")
    parser.add_argument("--pure-website", action="store_true",
                        help="Download viewer HTML/CSS/JS only — skip project.json search. "
                             "Useful for custom-format sites (e.g. lewd_horizon). Output: ZIP.")
    parser.add_argument("--pure-website-folder", action="store_true",
                        help="Same as --pure-website but keep as folder instead of ZIP.")
    parser.add_argument("-f", "--fonts",   action="store_true",
                        help="Download & localise fonts (ZIP/website mode).")
    parser.add_argument("--cyoap-vue", action="store_true",
                        help="Auto-probe dedicated cyoap_vue website backup mode before standard ICC/website detection.")
    parser.add_argument("--cyoap-vue-website", action="store_true",
                        help="Use dedicated cyoap_vue website mode and output ZIP.")
    parser.add_argument("--cyoap-vue-folder", action="store_true",
                        help="Use dedicated cyoap_vue website mode and keep folder output.")
    parser.add_argument("-a", "--analyse-fonts", action="store_true",
                        help="Print font analysis report only.")
    parser.add_argument("-t", "--threads", "--workers", dest="threads", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"Parallel download threads (default: {DEFAULT_MAX_WORKERS}).")
    parser.add_argument("-w", "--wait-time", "--wait", dest="wait_time", type=int, default=DEFAULT_WAIT_TIME,
                        help=f"Seconds to wait after 429 (default: {DEFAULT_WAIT_TIME}).")
    parser.add_argument("--proxy", default=None, help="Proxy URL, e.g. http://127.0.0.1:7890. Use --proxy-mode disabled to ignore environment proxies.")
    parser.add_argument("--proxy-mode", choices=["inherit_env", "manual", "disabled"], default=None, help="Proxy mode. Default preserves saved/runtime behavior; disabled ignores HTTP_PROXY/HTTPS_PROXY.")
    parser.add_argument("--dns", default=None, help="Override DNS resolver for this process. Accepts plain DNS IP or DoH URL. Empty string restores system DNS.")
    parser.add_argument("--bebasdns", choices=["default", "security", "unfiltered", "family"], default=None,
                        help="Use BebasDNS DoH resolver variant for this process.")
    parser.add_argument("--cloudflare", choices=["off", "auto", "cloudscraper", "flaresolverr"],
                        default=_load_settings().get("cloudflare_mode", "auto"),
                        help="Cloudflare handling mode. Auto tries normal request, cloudscraper, then FlareSolverr when needed.")
    parser.add_argument("--cf-bypass", "--cloudscraper", dest="cf_bypass", action="store_true",
                        help="Legacy alias: force Cloudflare mode to cloudscraper when installed.")
    parser.add_argument("--flaresolverr-url", default=_load_settings().get("flaresolverr_url", "http://localhost:8191/v1"),
                        help="FlareSolverr API endpoint, e.g. http://localhost:8191/v1.")
    parser.add_argument("--flaresolverr-session", choices=["temporary", "reuse-domain", "manual"],
                        default=_load_settings().get("flaresolverr_session_policy", "reuse-domain"),
                        help="FlareSolverr session policy. reuse-domain keeps cookies per domain.")
    parser.add_argument("--flaresolverr-timeout", type=int, default=int(_load_settings().get("flaresolverr_timeout", 60) or 60),
                        help="FlareSolverr solve timeout in seconds.")
    parser.add_argument("--flaresolverr-wait", type=int, default=int(_load_settings().get("flaresolverr_wait_after", 3) or 3),
                        help="Seconds FlareSolverr waits after page load before returning content.")
    parser.add_argument("--flaresolverr-proxy", choices=["inherit", "none"],
                        default=_load_settings().get("flaresolverr_proxy_mode", "inherit"),
                        help="Whether FlareSolverr should inherit the app proxy for target requests.")
    parser.add_argument("--flaresolverr-test", action="store_true",
                        help="Test the configured FlareSolverr API and exit.")
    parser.add_argument("--http2", action=argparse.BooleanOptionalAction, default=None, help="Use HTTP/2 via httpx for deep-scan asset fetches when available.")
    parser.add_argument("--gallery-dl", choices=["off", "smart", "force"], default=None,
                        help="Optional gallery-dl fallback. Default off; smart only uses page/post/gallery URLs; force is advanced.")
    parser.add_argument("--gallery-dl-path", default="gallery-dl", help="gallery-dl executable path used with --gallery-dl.")
    parser.add_argument("--gallery-dl-config", default="", help="Optional gallery-dl config.json path.")
    parser.add_argument("--bandwidth", type=float, default=0.0, help="Bandwidth limit in KB/s. 0 means unlimited.")
    parser.add_argument("--ai-key", dest="ai_api_key", default="", help="AI API key for this run only. It is not saved to settings.json.")
    parser.add_argument("--ai-provider", choices=["anthropic", "openai", "gemini", "ollama"], default=None,
                        help="AI provider for AI Assist.")
    parser.add_argument("--ai-key-storage", choices=["session", "env", "keyring", "plain"], default=None,
                        help="Where to read/save the AI API key. CLI default follows settings; --ai-key overrides storage for this run.")
    parser.add_argument("--ai-model", default="", help="AI model for the selected provider. If omitted, the provider default is used.")
    parser.add_argument("--ollama-url", default=None,
                        help="Ollama base URL used when --ai-provider ollama. Default: http://localhost:11434")
    parser.add_argument("--ai-mode", choices=["off", "diagnostics", "auto_fallback", "aggressive_recovery"], default=None,
                        help="AI Assist mode. Use off to disable AI even if a key is configured.")
    parser.add_argument("--ai-max-calls", type=int, default=None, help="Maximum AI calls per download. 0 means unlimited.")
    parser.add_argument("--ai-max-html-chars", type=int, default=None, help="Maximum HTML characters sent to AI per call.")
    parser.add_argument("--ai-max-js-chars", type=int, default=None, help="Maximum JS characters sent to AI per call.")
    parser.add_argument("--ai-clear-key", action="store_true", help="Clear the configured AI key from plain settings.json or OS keyring and exit.")
    parser.add_argument("--cyoa-manager", action="store_true", help="Add finished project.json to CYOA Manager when possible.")
    parser.add_argument("--serve", action="store_true", help="Start local HTTP server for the output directory after download.")
    parser.add_argument("--serve-port", type=int, default=0, help="Local server port used with --serve. 0 means auto-pick a fresh port.")
    parser.add_argument("--language", choices=["id", "en"], default=None, help="CLI/UI language preference. Saved only when explicitly provided.")
    parser.add_argument("--no-ytdlp", action="store_true",
                        help="Disable automatic YouTube audio download via yt-dlp.")
    parser.add_argument("--gui", action="store_true", help="Force GUI.")
    args = parser.parse_args()
    args.url = (args.url_opt or args.url or "").strip()
    # Resolve effective AI/network settings without overwriting saved GUI settings unless
    # the user supplied the corresponding CLI flag explicitly.
    ai_provider_eff = _normalize_ai_provider(args.ai_provider or _cli_saved_settings.get("ai_provider", "anthropic"))
    ai_storage_eff = _normalize_ai_key_storage(args.ai_key_storage or _cli_saved_settings.get("ai_key_storage", "session"))
    ai_mode_eff = _normalize_ai_mode(args.ai_mode or _cli_saved_settings.get("ai_mode", "auto_fallback"))
    ai_model_eff = args.ai_model or _get_ai_model(ai_provider_eff)
    ollama_url_eff = args.ollama_url or _cli_saved_settings.get("ollama_url", OLLAMA_DEFAULT_URL)
    if args.ai_clear_key:
        _clear_ai_api_key_storage(ai_storage_eff, ai_provider_eff, clear_all=True)
        logger.info("AI API key storage cleared.")
        return
    _ai_cli_settings = _load_settings()
    changed_settings = False
    if args.ai_provider is not None:
        _ai_cli_settings["ai_provider"] = ai_provider_eff; changed_settings = True
    if args.ai_key_storage is not None:
        _ai_cli_settings["ai_key_storage"] = ai_storage_eff; changed_settings = True
        if ai_storage_eff != "plain":
            _clear_ai_plain_keys(_ai_cli_settings, ai_provider_eff)
    if args.ai_model:
        _ai_cli_settings["ai_model"] = ai_model_eff; changed_settings = True
    if args.ai_mode is not None:
        _ai_cli_settings["ai_mode"] = ai_mode_eff; changed_settings = True
    if args.ai_max_calls is not None:
        _ai_cli_settings["ai_max_calls_per_download"] = max(0, int(args.ai_max_calls)); changed_settings = True
    if args.ai_max_html_chars is not None:
        _ai_cli_settings["ai_max_html_chars"] = max(1000, int(args.ai_max_html_chars)); changed_settings = True
    if args.ai_max_js_chars is not None:
        _ai_cli_settings["ai_max_js_chars"] = max(1000, int(args.ai_max_js_chars)); changed_settings = True
    if args.ollama_url:
        _ai_cli_settings["ollama_url"] = ollama_url_eff; changed_settings = True
    if changed_settings:
        _save_settings(_ai_cli_settings)
    resolved_ai_api_key = "" if ai_mode_eff == "off" else _resolve_ai_api_key(explicit_key=args.ai_api_key, storage=ai_storage_eff, provider=ai_provider_eff)
    os.makedirs(args.output_dir, exist_ok=True)
    if args.bebasdns:
        args.dns = BEBASDNS_DOH_VARIANTS[args.bebasdns]
    if args.proxy_mode == "disabled":
        _set_active_proxy(None, mode="disabled")
    elif args.proxy_mode == "inherit_env":
        _set_active_proxy(None, mode="inherit_env")
    elif args.proxy is not None:
        _set_active_proxy(args.proxy or None, mode="manual" if args.proxy else "disabled")
    if args.dns is not None:
        _set_active_dns(args.dns or None)
    _set_http2_enabled(bool(args.http2) if args.http2 is not None else bool(_cli_saved_settings.get("http2_enabled", False)))
    gallery_dl_eff = args.gallery_dl or _cli_saved_settings.get("gallery_dl_mode", "off")
    _set_gallery_dl_mode(gallery_dl_eff, path=args.gallery_dl_path, config=args.gallery_dl_config)
    global _bandwidth_limit_kbps, use_cloudscraper
    _bandwidth_limit_kbps = max(0.0, float(args.bandwidth or 0.0))
    cf_mode = "cloudscraper" if bool(args.cf_bypass) else args.cloudflare
    _set_cloudflare_config(
        cf_mode,
        flaresolverr_url=args.flaresolverr_url,
        session_policy=args.flaresolverr_session,
        timeout=args.flaresolverr_timeout,
        wait_after=args.flaresolverr_wait,
        proxy_mode=args.flaresolverr_proxy,
        persist=True,
    )
    if args.flaresolverr_test:
        ok, msg = flaresolverr_test_connection()
        print(("OK: " if ok else "ERROR: ") + msg)
        return
    st = _load_settings(); _net_changed = False
    if args.language is not None:
        st["language"] = args.language; _net_changed = True
    if args.http2 is not None:
        st["http2_enabled"] = bool(args.http2); _net_changed = True
    if args.gallery_dl is not None:
        st["gallery_dl_mode"] = gallery_dl_eff; _net_changed = True
    if args.proxy is not None:
        st["proxy"] = args.proxy; _net_changed = True
    if args.dns is not None:
        st["dns"] = args.dns or ""; _net_changed = True
    if args.bebasdns:
        st["bebasdns_variant"] = args.bebasdns; _net_changed = True
    if _net_changed:
        _save_settings(st)

    mode_flags = [
        bool(args.zip), bool(args.both), bool(args.website), bool(args.website_folder),
        bool(args.pure_website), bool(args.pure_website_folder),
        bool(args.cyoap_vue_website), bool(args.cyoap_vue_folder),
    ]
    if sum(mode_flags) > 1:
        parser.error("Choose only one output mode.")

    if args.gui:
        launch_gui()
        return

    wait_time = args.wait_time
    global _ytdlp_enabled
    _ytdlp_enabled = not bool(getattr(args, "no_ytdlp", False))

    # Setup file logging early so CLI output goes to log too
    _outdir_cli = getattr(args, "output_dir", os.getcwd()) if hasattr(args, "output_dir") else os.getcwd()
    setup_file_logging(_outdir_cli)

    pure_website_mode = args.pure_website or args.pure_website_folder
    website_mode = args.website or args.website_folder or args.cyoap_vue_website or args.cyoap_vue_folder
    website_zip_output = not (args.website_folder or args.cyoap_vue_folder or args.pure_website_folder)

    if not args.list_file and not args.url:
        parser.error("Provide a URL or use --list with a batch source.")

    if args.list_file:
        items = import_queue_items_from_source(args.list_file)
        if not items:
            raise RuntimeError("No valid URLs found in batch file.")
        logger.info(f"Batch file    : {args.list_file}")
        logger.info(f"Items         : {len(items)}")
        failed_items: List[Dict[str, str]] = []
        ok = 0
        for idx, item in enumerate(items, 1):
            logger.info(f"Batch {idx}/{len(items)}: {item['url']}")
            try:
                mode_i = (item.get("mode", "") or "").lower().replace("-", "_").replace(" ", "_")
                zip_i = args.zip or mode_i == "zip"
                both_i = args.both or mode_i == "both"
                pure_i = pure_website_mode or mode_i in {"pure_website", "pure_website_zip", "pure_website_folder"}
                website_i = website_mode or mode_i in {"website", "website_zip", "website_folder", "cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder", "pure_website", "pure_website_zip", "pure_website_folder"}
                website_zip_i = website_zip_output
                if mode_i in {"website_folder", "pure_website_folder", "cyoap_vue_folder"}:
                    website_zip_i = False
                if mode_i in {"website", "website_zip", "pure_website", "pure_website_zip", "cyoap_vue", "cyoap_vue_zip"}:
                    website_zip_i = True
                engine_mode = "cyoap_vue" if (args.cyoap_vue_website or args.cyoap_vue_folder or mode_i in {"cyoap_vue", "cyoap_vue_zip", "cyoap_vue_folder"}) else ("auto" if args.cyoap_vue else "standard")
                run_download(
                    url=item["url"],
                    file_name=item.get("filename", ""),
                    zip_output=zip_i,
                    both_output=both_i,
                    website_output=website_i,
                    website_zip_output=website_zip_i,
                    pure_website=pure_i,
                    download_fonts=args.fonts,
                    show_font_analysis=args.analyse_fonts or args.fonts,
                    output_dir=args.output_dir,
                    max_workers=args.threads,
                    engine_mode=engine_mode,
                    cyoa_mgr_enabled=args.cyoa_manager,
                    ai_api_key=resolved_ai_api_key,
                    ai_provider=ai_provider_eff,
                    ai_mode=ai_mode_eff,
                    analysis_only=args.analyse_fonts and not args.fonts,
                )
                ok += 1
            except Exception as e:
                failed_items.append({"url": item["url"], "error": str(e)})
                logger.error(f"Failed: {e}")
        write_failed_url_log(failed_items, args.output_dir)
        logger.info(f"Batch done    : {ok}/{len(items)} succeeded")
        return

    if not args.url:
        raise RuntimeError("URL is required unless --list is used.")

    mode_name = (
        "pure-website-folder" if args.pure_website_folder else
        "pure-website"        if args.pure_website else
        "cyoap-vue-folder"    if args.cyoap_vue_folder else
        "cyoap-vue-website"   if args.cyoap_vue_website else
        "website-folder"      if args.website_folder else
        "website"             if args.website else
        "both"                if args.both else
        "zip"                 if args.zip else "embed"
    )
    logger.info(f"URL          : {args.url}")
    logger.info(f"Filename     : {args.filename or '[auto]'}")
    logger.info(f"Mode         : {mode_name}")
    logger.info(f"Threads      : {args.threads}")
    logger.info(f"Fonts        : {'yes' if args.fonts else 'no'}")
    logger.info(f"Wait on 429  : {args.wait_time}s")
    logger.info(f"Output dir   : {args.output_dir}")
    logger.info(f"HTTP/2       : {'yes' if (_HTTP2_ENABLED) else 'no'}")
    logger.info(f"gallery-dl   : {gallery_dl_eff}")
    logger.info(f"AI Assist    : {ai_mode_eff} | provider={ai_provider_eff} | model={ai_model_eff} | key={'not needed' if ai_provider_eff == 'ollama' else ('yes' if bool(resolved_ai_api_key) else 'no')} | storage={ai_storage_eff}")
    logger.info(f"Cloudflare   : {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
    if _CLOUDFLARE_MODE == "flaresolverr" or _CLOUDFLARE_MODE == "auto":
        logger.info(f"FlareSolverr : {_FLARESOLVERR_URL} | session={_FLARESOLVERR_SESSION_POLICY} | timeout={_FLARESOLVERR_TIMEOUT}s")
    logger.info(f"Proxy        : {args.proxy if args.proxy is not None else '[saved/env/system]'}")
    logger.info(f"DNS          : {args.dns or '[system]'}" + (f" (BebasDNS {args.bebasdns})" if args.bebasdns else ""))

    engine_mode = "cyoap_vue" if (args.cyoap_vue_website or args.cyoap_vue_folder) else ("auto" if args.cyoap_vue else "standard")
    run_download(
        url=args.url,
        file_name=args.filename,
        zip_output=args.zip,
        both_output=args.both,
        website_output=website_mode,
        website_zip_output=website_zip_output,
        pure_website=pure_website_mode,
        download_fonts=args.fonts,
        show_font_analysis=args.analyse_fonts or args.fonts,
        output_dir=args.output_dir,
        max_workers=args.threads,
        engine_mode=engine_mode,
        cyoa_mgr_enabled=args.cyoa_manager,
        ai_api_key=resolved_ai_api_key,
        ai_provider=ai_provider_eff,
        ai_mode=ai_mode_eff,
        analysis_only=args.analyse_fonts and not args.fonts,
    )
    if args.serve:
        import http.server as _http_server
        import webbrowser as _webbrowser

        serve_dir = globals().get("_LAST_PREVIEW_FOLDER") or args.output_dir
        serve_dir = os.path.abspath(serve_dir)
        if not os.path.isdir(serve_dir):
            logger.warning(f"Serve skipped: preview folder not found: {serve_dir}")
            logger.warning("Tip: use --website-folder or --pure-website-folder when you want to preview immediately after download.")
            return

        logger.info(f"Serving {serve_dir} on 127.0.0.1…")

        class _NoCacheCLIHandler(_http_server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=serve_dir, **kw)
            def log_message(self, fmt, *args):
                logger.debug("[serve] " + (fmt % args if args else fmt))
            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                try:
                    from urllib.parse import urlparse as _urlparse
                    if _urlparse(self.path).path in ("", "/", "/index.html"):
                        self.send_header("Clear-Site-Data", '"cache", "storage"')
                except Exception:
                    pass
                super().end_headers()
            def do_GET(self):
                from urllib.parse import urlparse as _urlparse
                if _urlparse(self.path).path == "/__clear_cache__":
                    stamp = str(int(time.time() * 1000))
                    html_text = f'''<!doctype html><meta charset="utf-8"><title>Clearing preview cache...</title><script>
(async()=>{{try{{localStorage.clear();sessionStorage.clear();}}catch(e){{}}try{{if('caches' in window){{const n=await caches.keys();await Promise.all(n.map(x=>caches.delete(x)));}}}}catch(e){{}}try{{if('serviceWorker' in navigator){{const r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(x=>x.unregister()));}}}}catch(e){{}}location.replace('/?cb={stamp}&preview={stamp}');}})();
</script><p>Clearing preview cache...</p>'''
                    html = html_text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Clear-Site-Data", '"cache", "storage"')
                    self.end_headers()
                    self.wfile.write(html)
                    return
                return super().do_GET()

        host = "127.0.0.1"
        port_req = int(args.serve_port or 0)
        with _http_server.ThreadingHTTPServer((host, port_req), _NoCacheCLIHandler) as httpd:
            port = int(httpd.server_address[1])
            clear_url = f"http://{host}:{port}/__clear_cache__?cb={int(time.time()*1000)}"
            logger.info(f"Open {clear_url} to preview with cleared browser storage.")
            try:
                _webbrowser.open(clear_url)
            except Exception:
                pass
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                logger.info("Local server stopped")


# ─────────────────────────────────────────────────────────────────
#  Original utility functions  (unchanged logic)
# ─────────────────────────────────────────────────────────────────


def fetch_response(
    url: str,
    extra_headers: Optional[Dict] = None,
    timeout: int = 20,
    as_bytes: bool = False,
    quiet: bool = False,
) -> Optional[requests.Response]:
    """
    Fetch a URL with automatic fallbacks:
    - Cloudflare mode: off/auto/cloudscraper/flaresolverr
    - Auto mode: normal request → cloudscraper → FlareSolverr when a challenge is detected
    - SSL error fallback: retry with verify=False + warning
    - Domain rate throttle (300ms/domain)
    - Friendly error messages for common connection issues
    """
    _domain_throttle(url)
    headers = get_headers_for_url(url) or {"User-Agent": "Mozilla/5.0"}
    if extra_headers:
        headers.update(extra_headers)

    def _do_request(*, use_cf_session: bool = False, verify_ssl: bool = True):
        try:
            session = _get_shared_session(use_cf=bool(use_cf_session))
            r = session.get(url, headers=headers, timeout=timeout,
                            allow_redirects=True, verify=verify_ssl)
            if is_cloudflare_challenge(r):
                return "CF_CHALLENGE"
            r.raise_for_status()
            if as_bytes:
                _ = r.content
            return r
        except requests.exceptions.SSLError:
            return "SSL_ERROR"
        except requests.exceptions.ConnectionError as e:
            err = str(e).lower()
            if "connection reset" in err or "econnreset" in err:
                logger.warning(f"Connection reset oleh server: {url} — coba lagi nanti")
            elif "name or service not known" in err or "nodename nor servname" in err:
                logger.error(f"Domain tidak ditemukan (DNS): {url}")
            else:
                logger.error(f"Connection error: {url} — {e}")
            return None
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout ({timeout}s): {url}")
            return None
        except requests.RequestException as e:
            # A Cloudflare-protected page often returns 403/503 without a parseable challenge body.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in {403, 429, 503}:
                return "CF_CHALLENGE"
            if quiet:
                logger.debug(f"Probe miss: {url} — {e}")
            else:
                logger.error(f"Error: {url} — {e}")
            return None

    cf_mode = _normalize_cloudflare_mode(_CLOUDFLARE_MODE)
    attempts: List[Tuple[str, bool]] = []
    if cf_mode == "cloudscraper":
        attempts = [("cloudscraper", True), ("normal", False)]
    elif cf_mode == "flaresolverr":
        attempts = [("flaresolverr", False), ("normal", False)]
    else:
        attempts = [("normal", False)]

    result = None
    challenge_seen = False
    ssl_error = False

    for label, use_cf_session in attempts:
        if label == "flaresolverr":
            result = fetch_via_flaresolverr(url, extra_headers=headers, timeout=timeout)
        else:
            result = _do_request(use_cf_session=use_cf_session, verify_ssl=True)
        if result == "CF_CHALLENGE":
            challenge_seen = True
            logger.warning(f"[Cloudflare] Challenge detected: {url}")
            continue
        if result == "SSL_ERROR":
            ssl_error = True
            break
        if result is not None:
            logger.info(f"Downloaded: {url}" + (f" via {label}" if label != "normal" else ""))
            return result

    if ssl_error:
        logger.warning(
            f"SSL certificate error: {url}\n"
            f"  Retry tanpa SSL verification (tidak aman, tapi melanjutkan download)."
        )
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        result = _do_request(use_cf_session=(cf_mode == "cloudscraper"), verify_ssl=False)
        if result and result not in {"SSL_ERROR", "CF_CHALLENGE"}:
            logger.warning(f"  Berhasil tanpa SSL verify: {url}")
            return result

    # Auto fallback chain: only escalate when a Cloudflare challenge is actually detected.
    if cf_mode == "auto" and challenge_seen:
        logger.info("[Cloudflare] Auto mode: trying cloudscraper fallback…")
        result = _do_request(use_cf_session=True, verify_ssl=True)
        if result and result not in {"SSL_ERROR", "CF_CHALLENGE"}:
            logger.info(f"Downloaded: {url} via cloudscraper")
            return result
        logger.info("[Cloudflare] Auto mode: trying FlareSolverr fallback…")
        result = fetch_via_flaresolverr(url, extra_headers=headers, timeout=timeout)
        if result is not None:
            return result

    if challenge_seen:
        logger.warning(
            f"Cloudflare challenge unresolved: {url}\n"
            f"  GUI: set Cloudflare Mode to Auto or FlareSolverr.\n"
            f"  CLI: use --cloudflare auto or --cloudflare flaresolverr."
        )
    return None


def try_decode_bytes(raw: bytes, preferred_encoding: str = "") -> str:
    """
    Decode bytes to str with correct encoding priority.

    UTF-8 is ALWAYS tried first — it's the correct encoding for 95%+ of web
    content. chardet/charset_normalizer are used only when UTF-8 fails.
    latin-1 / ISO-8859-1 / cp1006 variants are treated as last resort ONLY
    because they 'succeed' on any byte sequence (including UTF-8 Korean),
    producing mojibake that then gets double-encoded as %C3%AC%C2%8B... in URLs.
    """
    # ── 1. UTF-8 always first ────────────────────────────────────────────────
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # ── 2. Explicit preferred (only if it's NOT a latin-1 variant) ──────────
    _LATIN_VARIANTS = {"latin-1", "iso-8859-1", "iso8859-1", "windows-1252",
                       "cp1252", "cp1006", "ascii"}
    if preferred_encoding and preferred_encoding.lower() not in _LATIN_VARIANTS:
        try:
            return raw.decode(preferred_encoding)
        except (UnicodeDecodeError, LookupError):
            pass

    # ── 3. chardet / charset_normalizer for genuinely non-UTF-8 content ─────
    if any(b > 0x7f for b in raw[:512]):
        detected = None
        try:
            import chardet
            result = chardet.detect(raw[:4096])
            if result and result.get("confidence", 0) > 0.75:
                enc = result.get("encoding", "")
                if enc and enc.lower() not in _LATIN_VARIANTS:
                    detected = enc
        except ImportError:
            pass
        if not detected:
            try:
                from charset_normalizer import from_bytes
                best = from_bytes(raw[:4096]).best()
                if best and str(best.encoding).lower() not in _LATIN_VARIANTS:
                    detected = best.encoding
            except ImportError:
                pass
        if detected:
            try:
                return raw.decode(detected)
            except (UnicodeDecodeError, LookupError):
                pass

    # ── 4. East-Asian encodings (legacy sites) ───────────────────────────────
    for enc in ["shift-jis", "euc-kr", "gb2312", "big5"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass

    # ── 5. latin-1 as absolute last resort (always succeeds, may be wrong) ───
    return raw.decode("latin-1")


def is_zip_bytes(raw: bytes) -> bool:
    return len(raw) >= 4 and raw[:4] == b"PK\x03\x04"



def looks_like_project_object(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False

    score = 0
    for key in IMAGE_FIELDS:
        if key in obj:
            score += 3
    for key in [
        "rows", "backpack", "cards", "sections", "scenes", "pages", "tabs", "choices",
        "name", "title", "author", "theme", "meta", "character", "points",
        "imageSets", "templates", "objects", "groups", "words", "variables",
        "defaultRowTitle", "defaultChoiceTitle", "pointTypes", "chapters", "version",
    ]:
        if key in obj:
            score += 1

    if isinstance(obj.get("rows"), list):
        score += 3
    if isinstance(obj.get("backpack"), list):
        score += 2
    if isinstance(obj.get("groups"), list):
        score += 1

    return score >= 4


def looks_like_project_payload(text: str) -> bool:
    if not text:
        return False

    parsed = parse_jsonish_text(text)
    if isinstance(parsed, dict):
        return looks_like_project_object(parsed)

    sample = text[:300000]
    lowered = sample.lower()

    score = 0
    for key in IMAGE_FIELDS:
        if re.search(rf'["\\\']?{re.escape(key)}["\\\']?\s*:', sample, flags=re.IGNORECASE):
            score += 3
    for key in [
        "rows", "backpack", "cards", "sections", "scenes", "pages", "tabs", "choices",
        "name", "title", "author", "theme", "meta", "character", "points",
        "imageSets", "templates", "objects", "groups", "words", "variables",
    ]:
        if re.search(rf'["\\\']?{re.escape(key)}["\\\']?\s*:', sample, flags=re.IGNORECASE):
            score += 1

    if score >= 4:
        return True

    if sample.strip().startswith("{") and sample.strip().endswith("}") and ('"image"' in lowered or '"rows"' in lowered or '"backpack"' in lowered):
        return True

    return False


def extract_balanced_brace_block(text: str, start_idx: int) -> str:
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
        return ""
    depth = 0
    in_string = False
    string_char = ""
    escaped = False

    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == string_char:
                in_string = False
            continue

        if ch in {'"', "'"}:
            in_string = True
            string_char = ch
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx:idx + 1]
    return ""



def extract_embedded_project_from_js(js_text: str) -> Optional[str]:
    # ── Fast-path: Vuex },getters split (original downloader technique) ──
    # ICC Plus old viewer embeds project as: Store({state:{app:{...}},getters:...})
    # This is faster than balanced-brace for the exact Vuex pattern.
    for start_marker, end_marker in [
        ("Store({state:{app:", "},getters"),
        ("state:{app:",        "},getters"),
    ]:
        if start_marker in js_text and end_marker in js_text:
            try:
                candidate = js_text.split(start_marker)[-1].split(end_marker)[0]
                # Find the opening brace and try to extract a full object
                brace_idx = candidate.find("{")
                if brace_idx != -1:
                    block = extract_balanced_brace_block(candidate, brace_idx)
                    if block and looks_like_project_payload(block):
                        logger.info(f"Found embedded project payload via Vuex split: {start_marker[:30]}")
                        return block
            except (IndexError, Exception):
                pass

    markers = [
        "Store({state:{app:",
        "state:{app:",
        "__INITIAL_STATE__=",
        "__INITIAL_STATE__ =",
        "window.__INITIAL_STATE__=",
        "window.__INITIAL_STATE__ =",
        "window.__APP__=",
        "window.__APP__ =",
        "window.__NUXT__=",
        "window.__NUXT__ =",
        "app:{",
        '"app":{',
        "project:{",
        '"project":{',
    ]

    for marker in markers:
        start = 0
        while True:
            idx = js_text.find(marker, start)
            if idx == -1:
                break
            brace_idx = js_text.find("{", idx)
            if brace_idx == -1:
                break
            block = extract_balanced_brace_block(js_text, brace_idx)
            if block and looks_like_project_payload(block):
                logger.info(f"Found embedded project payload via marker: {marker[:40]}")
                return block
            start = idx + len(marker)

    fallback_patterns = [
        r'(?:app|project)\s*:\s*\{',
        r'"(?:app|project)"\s*:\s*\{',
        r'(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\{',
        r'return\s*\{',
    ]
    for pattern in fallback_patterns:
        for m in re.finditer(pattern, js_text):
            brace_idx = js_text.find("{", m.start())
            if brace_idx == -1:
                continue
            block = extract_balanced_brace_block(js_text, brace_idx)
            if block and looks_like_project_payload(block):
                logger.info(f"Found embedded project payload via regex: {pattern}")
                return block

    keyword_patterns = [
        r'["\\\']rows["\\\']\s*:',
        r'\brows\s*:',
        r'["\\\']backpack["\\\']\s*:',
        r'\bbackpack\s*:',
        r'["\\\']groups["\\\']\s*:',
        r'["\\\']words["\\\']\s*:',
    ]

    for pattern in keyword_patterns:
        for m in re.finditer(pattern, js_text):
            scan_start = max(0, m.start() - 250000)
            brace_idx = js_text.rfind("{", scan_start, m.start())
            while brace_idx != -1:
                block = extract_balanced_brace_block(js_text, brace_idx)
                if block and len(block) > 100 and looks_like_project_payload(block):
                    logger.info(f"Found embedded project payload near keyword: {pattern}")
                    return block
                brace_idx = js_text.rfind("{", scan_start, brace_idx)

    return None


def extract_project_from_archive_bytes(raw: bytes, source_url: str, depth: int = 0) -> Optional[str]:
    if depth > 2 or not is_zip_bytes(raw):
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return None

            def sort_key(name: str) -> Tuple[int, int, str]:
                lname = name.lower()
                ext = os.path.splitext(lname)[1]
                priority = 99
                if lname.endswith("project.json"):
                    priority = 0
                elif "project" in lname and ext == ".json":
                    priority = 1
                elif ext == ".json":
                    priority = 2
                elif "project" in lname and ext == ".txt":
                    priority = 3
                elif ext == ".txt":
                    priority = 4
                elif ext == ".zip":
                    priority = 5
                return (priority, len(lname), lname)

            for member in sorted(names, key=sort_key):
                try:
                    member_raw = zf.read(member)
                except Exception as e:
                    logger.warning(f"Failed to read archive member {member}: {e}")
                    continue

                logger.info(f"Checking archive member: {member}")

                if is_zip_bytes(member_raw):
                    extracted = extract_project_from_archive_bytes(member_raw, f"{source_url}!/{member}", depth + 1)
                    if extracted:
                        logger.info(f"Found project payload inside nested archive: {member}")
                        return extracted

                text = try_decode_bytes(member_raw)
                project_text = extract_project_text_from_payload(text)
                if project_text:
                    logger.info(f"Found project payload inside archive member: {member}")
                    return project_text
    except zipfile.BadZipFile:
        return None
    except Exception as e:
        logger.warning(f"Failed to inspect archive from {source_url}: {e}")
        return None

    return None



def parse_jsonish_text(text: str) -> Optional[dict]:
    if not text:
        return None

    candidates = [text.strip()]
    trimmed = extract_json_like_block(text)
    if trimmed and trimmed not in candidates:
        candidates.append(trimmed)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
        if json5 is not None:
            try:
                return json5.loads(candidate)
            except Exception:
                pass
    return None


def normalize_project_payload_text(text: str) -> Optional[str]:
    if not text:
        return None

    obj = parse_jsonish_text(text)
    if isinstance(obj, dict) and looks_like_project_object(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    cleaned = extract_json_like_block(text)
    if cleaned:
        obj = parse_jsonish_text(cleaned)
        if isinstance(obj, dict) and looks_like_project_object(obj):
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    stripped = text.strip()
    if looks_like_project_payload(stripped):
        return stripped
    return None


def extract_project_text_from_payload(text: str) -> Optional[str]:
    if not text:
        return None

    normalized = normalize_project_payload_text(text)
    if normalized:
        return normalized

    embedded = extract_embedded_project_from_js(text)
    if embedded:
        normalized = normalize_project_payload_text(embedded)
        return normalized or embedded

    return None


def find_candidate_urls_in_text(text: str, base_url: str) -> List[str]:
    candidates: List[str] = []
    seen: Set[str] = set()

    def add(candidate: str) -> None:
        candidate = candidate.strip().strip('"\'')
        if not candidate or candidate.startswith(("data:", "javascript:", "#")):
            return
        full = candidate
        if not full.startswith(("http://", "https://")):
            full = urljoin(base_url, full)
        if full not in seen:
            seen.add(full)
            candidates.append(full)

    quoted_re = re.compile(
        r'(?P<quote>["\'])(?P<url>(?:https?:)?//[^"\']+|(?:\./|\.\./|/)?[^"\']+)(?P=quote)',
        re.IGNORECASE,
    )
    _PROJECT_FILENAME_RE = re.compile(
        r'(?:^|/)project(?:\.[a-z0-9]+)?$', re.IGNORECASE
    )
    for m in quoted_re.finditer(text):
        candidate = m.group("url")
        path = urlparse(candidate).path.lower()
        ext = os.path.splitext(path)[1]
        # Only accept candidates with a recognised data extension, or paths that
        # literally end with "project.json" / "project.txt" / "project.zip".
        # Avoid false positives like "Load/Save Project" or UI label strings.
        if ext in {".json", ".txt", ".zip"}:
            add(candidate)
        elif _PROJECT_FILENAME_RE.search(path) and "/" in path:
            add(candidate)

    for candidate in extract_placeholder_url(text):
        add(candidate)

    generic_call_re = re.compile(
        r'(?:fetch|axios\.get|axios\(|load|request)\s*\(\s*["\']([^"\']+\.(?:json|txt|zip))["\']',
        re.IGNORECASE,
    )
    # XHR pattern: e.open("GET","project.json",!0) — URL is 2nd arg, not 1st
    # Also catches: XMLHttpRequest.open("GET","...",true)
    xhr_call_re = re.compile(
        r'\.open\s*\(\s*["\']GET["\']\s*,\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    # Vuex-specific: $store.commit("loadApp",...),e.open("GET","...",!0)
    vuex_xhr_re = re.compile(
        r'\$store\.commit\(["\']loadApp["\'].*?\).*?\.open\(["\']GET["\']\s*,\s*["\']([^"\']+)["\']',
        re.DOTALL | re.IGNORECASE,
    )
    for m in generic_call_re.finditer(text):
        add(m.group(1))
    for m in xhr_call_re.finditer(text):
        add(m.group(1))
    for m in vuex_xhr_re.finditer(text):
        add(m.group(1))

    return candidates[:80]



_ARCHIVE_ORG_CYOA_RE = re.compile(
    r'https://archive\.org/download/CYOAZipArchive/([^\s"\'<>]+\.zip)',
    re.IGNORECASE,
)

def _extract_website_from_archive_zip_name(zip_filename: str) -> Optional[str]:
    """
    Convert archive.org CYOA zip filename back to the original website URL.
    Format: Name.[YYYY-MM-DD].https~~~site.com~path~subpath.zip
    → https://site.com/path/subpath
    """
    from urllib.parse import unquote
    fname = unquote(zip_filename)
    m = re.search(r'\.(https~~~[^.]+(?:\.[^.]+)*?)\.zip$', fname, re.IGNORECASE)
    if not m:
        return None
    url_part = m.group(1)
    # https~~~site.com~path → https://site.com/path
    url = url_part.replace("~~~", "://").replace("~", "/")
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/") + "/"




def try_project_candidate(candidate_url: str, label: str = "", quiet: bool = False) -> Tuple[Optional[str], str]:
    if label:
        logger.info(f"Trying {label}: {candidate_url}")
    else:
        logger.info(f"Trying candidate: {candidate_url}")

    response = fetch_response(candidate_url, timeout=25, quiet=quiet)
    if not response:
        return None, ""

    raw = response.content
    archived = extract_project_from_archive_bytes(raw, candidate_url)
    if archived:
        logger.info(f"Resolved project from archive-like payload: {candidate_url}")
        return archived, candidate_url

    text = response.text if hasattr(response, "text") else try_decode_bytes(raw)
    project_text = extract_project_text_from_payload(text)
    if project_text:
        logger.info(f"Resolved project payload from text candidate: {candidate_url}")
        return project_text, candidate_url

    return None, ""



def _script_priority(label: str) -> Tuple[int, str]:
    lower = label.lower()
    if any(part in lower for part in ["app.", "/app.", "app.js", "/js/app", "main.", "/main.", "main.js", "/index.", "runtime."]):
        return (0, lower)
    if any(part in lower for part in ["chunk-vendors", "vendors", "vendor.", "polyfills", "webpack"]):
        return (2, lower)
    if lower.startswith("inline_script_"):
        return (1, lower)
    return (1, lower)


def find_script_sources(html_source: str, base_url: Optional[str] = None) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html_source, "html.parser")
    results: List[Tuple[str, str]] = []

    for index, script in enumerate(soup.find_all("script"), start=1):
        if "document.createElement" in str(script):
            src = extract_app_js_path(str(script))
            if src:
                if base_url and not src.startswith(("http://", "https://")):
                    src = urljoin(base_url, src)
                script_source = get_source(src)
                if script_source:
                    results.append((src, script_source))
        elif script.get("src"):
            src = script["src"]
            if base_url and not src.startswith(("http://", "https://")):
                src = urljoin(base_url, src)
            script_source = get_source(src)
            if script_source:
                results.append((src, script_source))
        else:
            inline = script.string or script.get_text() or ""
            if inline.strip():
                results.append((f"inline_script_{index}", inline))

    results.sort(key=lambda item: _script_priority(item[0]))
    return results


def _scan_html_for_project_hints(html: str, page_url: str, base_url: str) -> List[str]:
    """
    Fast scan of HTML for strong clues about the project file location.
    Returns deduplicated candidate URLs to try *before* brute-forcing.
    Covers: <meta>, data-* attrs, <link rel="preload">, inline window.__ assignments,
    and inline fetch()/axios() calls pointing at .json/.txt/.zip files.
    """
    hints: List[str] = []
    seen: Set[str] = set()

    def add(raw: str) -> None:
        raw = (raw or "").strip().strip("\"'")
        if not raw or raw.startswith(("data:", "javascript:", "#")):
            return
        full = raw if raw.startswith(("http://", "https://")) else urljoin(base_url, raw)
        if full not in seen:
            seen.add(full)
            hints.append(full)

    soup = BeautifulSoup(html, "html.parser")

    # <meta name="project-url" content="...">
    for tag in soup.find_all("meta"):
        if re.search(r"project|cyoa", tag.get("name", ""), re.IGNORECASE):
            add(tag.get("content", ""))

    # data-project / data-src / data-url / data-file pointing at data files
    for tag in soup.find_all(True):
        for attr in ("data-project", "data-src", "data-url", "data-file"):
            val = tag.get(attr, "")
            if val and any(val.lower().endswith(e) for e in (".json", ".txt", ".zip")):
                add(val)

    # <link rel="preload" as="fetch" href="...json">
    for tag in soup.find_all("link"):
        href = tag.get("href", "")
        if href and any(href.lower().endswith(e) for e in (".json", ".txt", ".zip")):
            add(href)

    # inline <script>: window.__X__ = "url" and fetch/axios patterns
    _hint_re = re.compile(
        r'(?:'
        r'window\.__(?:PROJECT|APP|DATA|CYOA|INITIAL_STATE)__\s*=\s*["\']([^"\']+)["\']'
        r'|(?:fetch|axios\.get|axios\(|open)\s*\(\s*["\']([^"\']+\.(?:json|txt|zip))["\']'
        r')',
        re.IGNORECASE,
    )
    for script in soup.find_all("script", src=False):
        for m in _hint_re.finditer(script.string or ""):
            add(m.group(1) or m.group(2))

    return hints


def _parallel_head_check(
    candidates: List[str],
    max_workers: int = 12,
    timeout: int = 5,
) -> List[str]:
    """Check candidate URLs through the unified fetch wrapper.

    This intentionally uses lightweight GET through fetch_response instead of raw
    HEAD so Cloudflare/FlareSolverr, proxy, DNS, and retry policy are consistent.
    """
    results: Dict[str, bool] = {}
    lock = threading.Lock()

    def check(url: str) -> None:
        try:
            r = fetch_response(url, timeout=timeout, extra_headers={"User-Agent": "Mozilla/5.0"}, as_bytes=True, quiet=True)
            ok = bool(r is not None and r.status_code == 200)
        except Exception:
            ok = False
        with lock:
            results[url] = ok

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(check, candidates))

    return [u for u in candidates if results.get(u)]


def get_project_source(url: str, depth: int = 0, ai_api_key: str = "",
                       ai_provider: str = "", ai_mode: str = "auto_fallback",
                       ai_budget: Optional[AIUsageBudget] = None) -> Tuple[Optional[str], str]:
    if depth > 4:
        logger.warning(f"Max recursion depth at {url}")
        return None, ""

    if "cyoa.cafe" in url:
        logger.info("cyoa.cafe detected, resolving real URL…")
        for _resolve_attempt in range(3):
            try:
                resolved = get_iframe_url_from_cyoa_cafe(url)
            except Exception as e:
                logger.error(f"cyoa.cafe resolve error: {e}")
                return None, ""

            if not resolved or resolved == url:
                logger.info("cyoa.cafe: using resolved URL directly")
                break

            logger.info(f"cyoa.cafe resolved → {resolved}")
            url = resolved

            # Stop if URL has left cyoa.cafe entirely
            if "cyoa.cafe" not in url:
                break

            # Stop if URL is now on a SUBDOMAIN of cyoa.cafe (e.g.
            # lordcyoa.cyoa.cafe/isekai-adventures/) — that IS the
            # hosted CYOA, not another redirect layer to follow.
            parsed_resolved = urlparse(url)
            if parsed_resolved.netloc.lower() != "cyoa.cafe":
                logger.info(
                    f"cyoa.cafe: resolved to subdomain host "
                    f"({parsed_resolved.netloc}) — using as final CYOA URL"
                )
                break


    # ── cyoa.cafe React shell detection ──────────────────────────────
    # Some cyoa.cafe subdomains serve a React SPA shell at /slug/ while
    # the actual ICC Plus viewer is at /slug/game/.
    # Detect by: loads "game/assets/*.js" + has <div id="root"> (React).
    try:
        _shell_r = fetch_response(url, extra_headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        _shell_html = _safe_response_text(_shell_r) if _shell_r is not None else ""
        if ('game/assets/' in _shell_html and
                ('<div id="root">' in _shell_html or
                 'placeholder-content' in _shell_html) and
                'id="app"' not in _shell_html and
                'js/app.js' not in _shell_html):
            _game_url = url.rstrip('/') + '/game/'
            logger.info(f"cyoa.cafe React shell detected → redirect to {_game_url}")
            url = _game_url
    except Exception as _se:
        logger.debug(f"cyoa.cafe shell check failed: {_se}")

    logger.info(f"Project search start: {url}")
    base_url = strip_document_from_url(url)

    # ── Phase 0: fetch HTML once (reused by all later phases) ─────────────
    logger.info("Phase 0: fetching page HTML…")
    source = get_source(url)

    # ── Phase 0b: scan HTML for explicit hints — try these first ──────────
    if source:
        html_hints = _scan_html_for_project_hints(source, url, base_url)
        if html_hints:
            logger.info(f"Phase 0b: found {len(html_hints)} HTML hint(s) — trying before brute-force…")
            for hint in html_hints:
                proj, proj_url = try_project_candidate(hint, label="HTML hint")
                if proj:
                    return proj, proj_url

    # ── Phase 0c: try the canonical default locations first ───────────────
    # This makes the control flow match user expectations and avoids waiting
    # for a large brute-force sweep when /project.json exists.
    default_candidates = build_default_project_candidates(url)
    canonical_defaults: List[str] = []
    for _candidate in default_candidates:
        _path = urlparse(_candidate).path.lower().rstrip("/")
        if _path.endswith(("/project.json", "/project.txt", "/project.zip")):
            canonical_defaults.append(_candidate)
        if len(canonical_defaults) >= 3:
            break
    if canonical_defaults:
        logger.info("Phase 0c: trying canonical default project locations first…")
        for candidate in canonical_defaults:
            proj, proj_url = try_project_candidate(candidate, label="default path", quiet=True)
            if proj:
                return proj, proj_url

    # ── Phase 1: parallel-check remaining default candidates ───────────────
    default_candidates = [c for c in default_candidates if c not in set(canonical_defaults)]
    logger.info(
        f"Phase 1: checking {len(default_candidates)} remaining default candidates in parallel…"
    )
    live = _parallel_head_check(default_candidates, max_workers=12)
    logger.info(f"  {len(live)}/{len(default_candidates)} candidate(s) alive — fetching…")
    for candidate in live:
        proj, proj_url = try_project_candidate(candidate, label="default path")
        if proj:
            return proj, proj_url

    if not source:
        logger.warning("Could not download page HTML.")
        return None, ""

    # ── Phase 2: deeper scan of HTML text ────────────────────────────────
    logger.info("Phase 2: scanning HTML text for candidate file references…")
    html_candidates = find_candidate_urls_in_text(source, base_url)
    if html_candidates:
        logger.info(f"  Found {len(html_candidates)} candidate URL(s) in HTML.")
    for idx, candidate in enumerate(html_candidates, start=1):
        logger.info(f"  HTML candidate {idx}/{len(html_candidates)}")
        proj, proj_url = try_project_candidate(candidate, label="HTML-discovered file")
        if proj:
            return proj, proj_url

    # ── Phase 3: scan JS bundles and inline scripts ───────────────────────
    logger.info("Phase 3: scanning script bundles and inline JS…")
    script_sources = find_script_sources(source, base_url)
    logger.info(f"  Found {len(script_sources)} script block(s)/bundle(s) to scan.")

    for idx, (script_label, js_script) in enumerate(script_sources, start=1):
        logger.info(f"  Scanning script {idx}/{len(script_sources)}: {script_label}")

        js_candidates = find_candidate_urls_in_text(js_script, base_url)
        if js_candidates:
            logger.info(f"    {len(js_candidates)} candidate file reference(s) in script.")
        for c_idx, candidate in enumerate(js_candidates, start=1):
            logger.info(f"    JS candidate {c_idx}/{len(js_candidates)}")
            proj, proj_url = try_project_candidate(candidate, label="JS-discovered file")
            if proj:
                return proj, proj_url

        embedded = extract_embedded_project_from_js(js_script)
        if embedded:
            logger.info(f"  Embedded project found in: {script_label}")
            # Return the *page* URL, not the JS file URL, so that relative
            # image paths resolve from the site root (e.g. /images/)
            # rather than the JS directory (e.g. /js/images/).
            return embedded, url

    # ── Phase 4: iframes ─────────────────────────────────────────────────
    logger.info("Phase 4: checking iframes…")
    iframe_urls = extract_iframe_urls(source)
    for idx, iframe_url in enumerate(iframe_urls, start=1):
        iframe_full = urljoin(base_url, iframe_url)
        logger.info(f"  Checking iframe {idx}/{len(iframe_urls)}: {iframe_full}")
        proj, proj_url = get_project_source(iframe_full, depth + 1, ai_api_key=ai_api_key, ai_provider=ai_provider, ai_mode=ai_mode, ai_budget=ai_budget)
        if proj:
            return proj, proj_url

    # ── Phase 5: AI-assisted detection (provider-neutral) ─────────────
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode)
    if depth == 0 and _ai_mode_allows("project_detect", ai_mode) and _ai_is_available(ai_api_key, ai_provider):
        logger.info("Phase 5: AI-assisted project detection…")
        ai_candidate = _ai_detect_project_json(url, source, api_key=ai_api_key, provider=ai_provider, ai_mode=ai_mode, budget=ai_budget)
        if ai_candidate:
            try:
                r = fetch_response(ai_candidate, timeout=15, extra_headers={"User-Agent": "Mozilla/5.0"})
                txt = _safe_response_text(r) if r is not None else ""
                if txt.strip()[:1] in ("{", "["):
                    logger.info(f"  AI candidate confirmed: {ai_candidate}")
                    return txt, ai_candidate
            except Exception as e:
                logger.debug(f"  AI candidate failed: {e}")

    logger.warning("Project search finished without result.")
    return None, ""


def url_file_exists(url: str, timeout: int = 5) -> bool:
    try:
        r = fetch_response(url, timeout=timeout, extra_headers={"User-Agent": "Mozilla/5.0"}, as_bytes=True)
        return bool(r is not None and r.status_code == 200)
    except Exception:
        return False


def auto_detect_mode(url: str, timeout: int = 6) -> str:
    """
    Quick probe to auto-detect the best download mode for a URL.

    Detection order:
      1. cyoap_vue  — dist/platform.json + dist/nodes/list.json both exist
      2. Standard ICC/ICC+ project — any project.json/txt/zip candidate responds 200
      3. Website     — HTML responds 200 but no project file found
      4. Unknown     — fallback to website_folder

    Default for all cases: website_folder
    (downloads the full site + viewer as a local folder, best for offline play)

    Returns one of: 'website_folder', 'cyoap_vue_folder'
    """
    url = url.strip().rstrip("/") + "/"
    base = url

    logger.info(f"[Auto-detect] Probing: {url}")

    # ── 1. cyoap_vue probe ────────────────────────────────────────────────
    cyoap_candidates = [
        urljoin(base, "dist/platform.json"),
        urljoin(base, "dist/nodes/list.json"),
    ]
    cyoap_live = _parallel_head_check(cyoap_candidates, max_workers=4, timeout=timeout)
    if len(cyoap_live) >= 2:
        logger.info(f"[Auto-detect] → cyoap_vue detected")
        return "cyoap_vue_folder"

    # ── 2. Standard project probe ─────────────────────────────────────────
    try:
        candidates = build_default_project_candidates(url)
        live = _parallel_head_check(candidates, max_workers=12, timeout=timeout)
        if live:
            logger.info(
                f"[Auto-detect] → ICC project detected ({len(live)} candidate(s))"
                f" → website_folder"
            )
            return "website_folder"
    except Exception as e:
        logger.warning(f"[Auto-detect] Phase 1 probe failed: {e}")

    # ── 3. Fallback — download as website folder regardless ───────────────
    logger.info(f"[Auto-detect] → defaulting to website_folder")
    return "website_folder"


def auto_detect_modes_batch(
    items: List[Dict],
    max_workers: int = 4,
    progress_cb=None,
) -> List[Dict]:
    """
    Run auto_detect_mode for every item in the batch that has mode == 'auto'.
    Updates item['mode'] in place and calls progress_cb(done, total) if provided.
    Returns the updated list.
    """
    to_probe = [i for i in items if i.get("mode", "embed") == "auto"]
    total = len(to_probe)
    done = {"n": 0}
    lock = threading.Lock()

    def probe_one(item: Dict) -> None:
        try:
            detected = auto_detect_mode(item["url"])
        except Exception as e:
            logger.warning(f"[Auto-detect] Error for {item['url']}: {e}")
            detected = "embed"
        item["mode"] = detected
        item["auto_detected"] = True
        with lock:
            done["n"] += 1
            if progress_cb:
                progress_cb(done["n"], total)

    if to_probe:
        logger.info(f"[Auto-detect] Probing {total} URL(s) in parallel (workers={max_workers})…")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(probe_one, to_probe))
        logger.info("[Auto-detect] Done.")

    return items



def get_iframe_url_from_cyoa_cafe(game_url: str) -> str:
    """
    Resolve a cyoa.cafe URL to its real iframe/project URL.

    Handles two formats:
      OLD: https://cyoa.cafe/game/RECORD_ID
           → /api/collections/games/records/RECORD_ID

      NEW: https://SUBDOMAIN.cyoa.cafe/SLUG/
           → Try API filter by slug, then fall back to fetching
             the page HTML and extracting the iframe src.
    """
    parsed  = urlparse(game_url)
    host    = parsed.netloc.lower()   # e.g. "cyoa.cafe" or "lordcyoa.cyoa.cafe"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # ── OLD format: cyoa.cafe/game/RECORD_ID ──────────────────────────
    if host == "cyoa.cafe":
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "game":
            record_id = parts[1]
            api_url   = f"https://cyoa.cafe/api/collections/games/records/{record_id}"
            try:
                r = fetch_response(api_url, extra_headers=headers, timeout=15)
                if r is None:
                    raise requests.RequestException("empty response")
                data = r.json()
                # PocketBase may use different field names across versions
                for field in ("iframe_url", "iframeUrl", "url", "link", "source", "embed"):
                    iframe_url = data.get(field)
                    if iframe_url and iframe_url.startswith("http"):
                        logger.info(f"cyoa.cafe resolved (record API, field={field!r}): {iframe_url}")
                        return iframe_url
            except Exception as e:
                raise requests.RequestException(f"cyoa.cafe API failed: {e}")
        raise ValueError(f"Invalid cyoa.cafe URL (old format): {game_url}")

    # ── NEW format: SUBDOMAIN.cyoa.cafe/SLUG/ ─────────────────────────
    if host.endswith(".cyoa.cafe"):
        subdomain = host.rsplit(".cyoa.cafe", 1)[0]   # e.g. "lordcyoa"
        slug      = parsed.path.strip("/").split("/")[0]  # e.g. "pony-ranch"

        logger.info(f"cyoa.cafe subdomain format detected: subdomain={subdomain!r} slug={slug!r}")

        # Attempt 1: PocketBase API filter by slug
        if slug:
            for filter_expr in [
                f"(slug='{slug}')",
                f"(slug='{slug}'&&expand=user)",
            ]:
                try:
                    api_url = (
                        f"https://cyoa.cafe/api/collections/games/records"
                        f"?filter={requests.utils.quote(filter_expr)}&perPage=5"
                    )
                    r = fetch_response(api_url, extra_headers=headers, timeout=15)
                    if r is not None:
                        data  = r.json()
                        items = data.get("items", [])
                        if items:
                            for field in ("iframe_url", "iframeUrl", "url", "link", "source"):
                                iframe_url = items[0].get(field)
                                if iframe_url and iframe_url.startswith("http"):
                                    logger.info(f"cyoa.cafe resolved (slug filter, field={field!r}): {iframe_url}")
                                    return iframe_url
                except Exception:
                    pass

        # Attempt 2: Fetch the page HTML and look for iframe src / embedded JSON
        try:
            r = fetch_response(game_url, extra_headers=headers, timeout=15)
            if r is not None:
                html = _safe_response_text(r)
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")

                # 2a. Direct <iframe src="...">
                for iframe in soup.find_all("iframe"):
                    src = iframe.get("src", "")
                    if src and src.startswith("http") and "cyoa.cafe" not in src:
                        logger.info(f"cyoa.cafe resolved (page iframe): {src}")
                        return src

                # 2b. JSON blob in <script> containing iframe_url field
                #     Handles: window.__RECORD__={...}, window.__NUXT__={...}, etc.
                json_patterns = [
                    re.compile(r'"iframe_url"\s*:\s*"(https?://[^"]+)"'),
                    re.compile(r"'iframe_url'\s*:\s*'(https?://[^']+)'"),
                    re.compile(r'iframe_url\s*:\s*["\']?(https?://[^\s"\'<]+)'),
                ]
                for script in soup.find_all("script"):
                    script_text = script.string or ""
                    for pat in json_patterns:
                        m = pat.search(script_text)
                        if m:
                            candidate = m.group(1)
                            if "cyoa.cafe" not in candidate:
                                logger.info(f"cyoa.cafe resolved (script JSON): {candidate}")
                                return candidate

                # 2c. Broad regex over full HTML for any https:// not on cyoa.cafe
                #     that looks like a CYOA host (neocities, github.io, itch.io, etc.)
                known_hosts = ("neocities.org", "github.io", "itch.io", "githubusercontent.com")
                for m in re.finditer(r'https?://([^\s"\'<>]+)', html):
                    candidate = m.group(0).rstrip(".,;)/\"'")
                    if any(h in candidate for h in known_hosts):
                        logger.info(f"cyoa.cafe resolved (HTML host scan): {candidate}")
                        return candidate

                # 2d. og:url or canonical link pointing off-site
                for tag in soup.find_all("meta", property="og:url"):
                    content = tag.get("content", "")
                    if content.startswith("http") and "cyoa.cafe" not in content:
                        logger.info(f"cyoa.cafe resolved (og:url): {content}")
                        return content
                for tag in soup.find_all("link", rel="canonical"):
                    href = tag.get("href", "")
                    if href.startswith("http") and "cyoa.cafe" not in href:
                        logger.info(f"cyoa.cafe resolved (canonical): {href}")
                        return href

        except Exception as e:
            logger.warning(f"cyoa.cafe page fetch failed: {e}")

        # Attempt 3: The subdomain page itself might BE the project page
        # (some cyoa.cafe creators host on a custom subdomain that IS the CYOA)
        logger.warning(
            f"cyoa.cafe: could not resolve iframe for {game_url}\n"
            f"  Trying to download subdomain page directly as a website."
        )
        return game_url   # Fall back: treat the page itself as the target

    raise ValueError(f"Unknown cyoa.cafe URL format: {game_url}")



def extract_json_like_block(text: str) -> str:
    start = text.find("{")
    end   = text.rfind("}") + 1
    return text[start:end] if start != -1 and end > start else ""


def get_source(url: str, extra_headers: Optional[Dict] = None) -> Optional[str]:
    """
    Fetch URL and return text content.
    Uses try_decode_bytes() instead of response.text so Korean/Japanese/Chinese
    filenames in project.json are decoded correctly regardless of server charset header.
    (requests defaults to ISO-8859-1 when server omits charset — breaks Asian text)
    """
    response = fetch_response(url, extra_headers=extra_headers, timeout=20)
    if not response:
        return None
    # response.text uses response.encoding (may be ISO-8859-1 if server omits charset)
    # response.content is the raw bytes — always correct
    return try_decode_bytes(response.content)


def find_scripts(html_source: str, base_url: Optional[str] = None) -> List[str]:
    return [content for _, content in find_script_sources(html_source, base_url)]


def extract_placeholder_url(source: str) -> List[str]:
    p = r'\$store\.commit\("loadApp",.*?\)\}\},e\.open\("GET","(.*?)",!0\)'
    result = re.findall(p, source)
    if result:
        return result
    return re.findall(r'e\.open\(\s*["\']GET["\']\s*,\s*["\']([^"\']+)["\']', source)


def extract_iframe_urls(html_source: str) -> List[str]:
    soup = BeautifulSoup(html_source, "html.parser")
    return [t.get("src") for t in soup.find_all("iframe") if t.get("src")]


def get_first_folder_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def _build_output_name(url: str) -> str:
    """
    Derive a meaningful, unique output name from a URL.

    Priority:
    1. Full path joined with underscores (e.g. landsofmagi_v2)
    2. Subdomain (e.g. coinbt)
    3. Fallback to "downloaded_cyoa"

    Avoids generic names like "assets", "cyoa", "index", "www",
    "v1", "v2" etc. that would cause collisions across different sites.
    """
    parsed   = urlparse(url)
    path     = parsed.path.strip("/")
    parts    = [p for p in path.split("/") if p]

    # Generic path components that shouldn't be used alone
    GENERIC  = {"cyoa","assets","asset","images","files","pages","index",
                "www","web","site","game","viewer","view","public","static",
                "v1","v2","v3","v4","v5","beta","test","demo","page","cyoas"}

    # Build name from all path parts (joined), skipping generic-only results
    if parts:
        # Try full path joined
        full = "_".join(clean_url_path_component(p) for p in parts[:3])
        if full.lower().strip("_") not in GENERIC and len(full) > 1:
            return full
        # Single part but generic → prepend subdomain
        sub = get_first_subdomain(url)
        if sub and sub.lower() not in {"www","neocities"}:
            return f"{clean_url_path_component(sub)}_{full}" if full else clean_url_path_component(sub)

    # No path: use subdomain
    sub = get_first_subdomain(url)
    if sub and sub.lower() not in {"www","neocities"}:
        return clean_url_path_component(sub)

    # Last resort: domain without TLD
    host = parsed.hostname or ""
    domain = host.split(".")[0]
    return clean_url_path_component(domain) if domain else "downloaded_cyoa"

def extract_app_js_path(code: str) -> str:
    m = re.search(r"js/app\.[^'\"]+\.js", code)
    return m.group(0) if m else ""



def get_first_subdomain(url: str) -> str:
    if tldextract is not None:
        try:
            sub = tldextract.extract(url).subdomain
            return sub.split(".")[0] if sub else ""
        except Exception:
            pass

    host = urlparse(url).hostname or ""
    parts = host.split(".")
    if len(parts) >= 3:
        return parts[0]
    return ""


def clean_url_path_component(encoded_str: str) -> str:
    """
    Decode percent-encoded URL component and sanitize for use as a local filename.
    Preserves Unicode (Korean, Japanese, Chinese, etc.) — only strips chars that
    are actually illegal in filenames across Windows/macOS/Linux.
    """
    decoded = unquote(encoded_str)
    # Strip chars illegal in Windows/macOS/Linux filenames
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', decoded)
    # Collapse multiple consecutive underscores
    cleaned = re.sub(r'_+', '_', cleaned)
    # Strip leading/trailing spaces and dots (Windows quirk)
    cleaned = cleaned.strip('. ')
    return cleaned or "asset"


def save_string_to_file(content: str, filename: str, path: str = "") -> None:
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    base, ext = os.path.splitext(filename)
    new = os.path.join(path, filename) if path else filename
    if path:
        os.makedirs(path, exist_ok=True)
    counter = 1
    while os.path.exists(new):
        new = (os.path.join(path, f"{base}_{counter}{ext}") if path else f"{base}_{counter}{ext}")
        counter += 1
    with open(new, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Saved: {new}")


def create_random_temp_folder(prefix: str = "cyoa_") -> str:
    tmp = tempfile.gettempdir()
    while True:
        folder = os.path.join(tmp, prefix + uuid.uuid4().hex[:8])
        if not os.path.exists(folder):
            os.makedirs(folder)
            return folder


def zip_temp_folder(temp_path: str, zip_name: str = "") -> str:
    if not os.path.isdir(temp_path):
        raise ValueError(f"Not a directory: {temp_path}")
    if not zip_name:
        zip_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    zf = zip_name if zip_name.endswith(".zip") else zip_name + ".zip"
    zp = os.path.join(os.getcwd(), zf)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(temp_path):
            for file in files:
                abs_p = os.path.join(root, file)
                z.write(abs_p, arcname=os.path.relpath(abs_p, start=temp_path))
    logger.info(f"ZIP created: {zp}")
    return zp


def delete_temp_folder(temp_path: str) -> None:
    if os.path.isdir(temp_path):
        shutil.rmtree(temp_path)
        logger.info(f"Temp deleted: {temp_path}")
    else:
        logger.warning(f"Temp not found: {temp_path}")


def build_default_project_candidates(url: str) -> List[str]:
    """
    Build a prioritised list of candidate project.json URLs for Phase 1.

    Strategy:
      1. Immediate:  project.json at exactly the page's directory
      2. Ancestors:  walk UP the URL path (site.com/a/b/c/ → /a/b/ → /a/ → /)
      3. Subdirs:    common sub-directory names at each ancestor level
      4. Alt names:  alternative filename patterns
      5. Domain:     host-specific known structures (neocities, github.io, etc.)

    Ordering is intentional — most-likely paths come first so Phase 1's
    parallel HEAD-check can short-circuit early on a hit.
    """
    base_url = strip_document_from_url(url)
    parsed   = urlparse(url)
    host     = (parsed.hostname or "").lower()

    # ── canonical names ordered by frequency in the wild ──────────────
    PRIMARY_NAMES = ["project.json", "project.txt", "project.zip"]
    ALT_NAMES     = [
        "data.json", "app.json", "cyoa.json", "content.json",
        "index.json", "iccplus.json", "data.txt", "main.json",
        "game.json", "story.json", "adventure.json", "choices.json",
        "app.data.json", "project.data.json",
    ]
    ALL_NAMES = PRIMARY_NAMES + ALT_NAMES

    # ── sub-directories to probe at each level ─────────────────────────
    PRIMARY_SUBDIRS = [
        "",            # root of current dir (first!)
        "app/",
        "data/",
        "assets/",
        "project/",
    ]
    EXTRA_SUBDIRS = [
        "public/",
        "dist/",
        "src/",
        "static/",
        "files/",
        "content/",
        "json/",
        "resources/",
        "media/",
        "game/",
        "cyoa/",
        "viewer/",
        "js/data/",
        "js/",
        "scripts/",
        "config/",
    ]
    ALL_SUBDIRS = PRIMARY_SUBDIRS + EXTRA_SUBDIRS

    seen: Set[str] = set()
    result: List[str] = []

    def add(u: str) -> None:
        u = u.split("?")[0].split("#")[0]   # strip query/fragment
        if u not in seen:
            seen.add(u)
            result.append(u)

    # ── 1. Immediate base URL ─────────────────────────────────────────
    for name in PRIMARY_NAMES:
        add(urljoin(base_url, name))

    # ── 2. Walk UP the URL path → try project.json at every ancestor ──
    #   e.g. /games/cyoa/isekai/ → /games/cyoa/ → /games/ → /
    path_parts = [p for p in parsed.path.rstrip("/").split("/") if p]
    # Strip trailing filename (e.g. index.html) — only keep directory components
    if path_parts and "." in path_parts[-1] and not path_parts[-1].startswith("."):
        path_parts = path_parts[:-1]
    ancestor_bases: List[str] = []

    for depth in range(len(path_parts), 0, -1):
        ancestor_path = "/" + "/".join(path_parts[:depth]) + "/"
        ancestor_base = urlunparse(parsed._replace(
            path=ancestor_path, query="", fragment=""))
        ancestor_bases.append(ancestor_base)

    # Add root too
    root_base = urlunparse(parsed._replace(path="/", query="", fragment=""))
    if root_base != base_url:
        ancestor_bases.append(root_base)

    for ancestor in ancestor_bases:
        for name in PRIMARY_NAMES:
            add(urljoin(ancestor, name))

    # ── 3. Primary subdirs at immediate base ──────────────────────────
    for subdir in PRIMARY_SUBDIRS[1:]:   # skip "" already done above
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 4. Primary subdirs at each ancestor ───────────────────────────
    for ancestor in ancestor_bases[:3]:  # only first 3 ancestors (most relevant)
        for subdir in PRIMARY_SUBDIRS[1:]:
            for name in PRIMARY_NAMES:
                add(urljoin(ancestor, subdir + name))

    # ── 5. Alt names at base + common ancestor ────────────────────────
    for name in ALT_NAMES:
        add(urljoin(base_url, name))
    if ancestor_bases:
        for name in ALT_NAMES:
            add(urljoin(ancestor_bases[0], name))

    # ── 6. Extra subdirs at base ──────────────────────────────────────
    for subdir in EXTRA_SUBDIRS:
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 7. Alt names at primary subdirs ──────────────────────────────
    for subdir in PRIMARY_SUBDIRS:
        for name in ALT_NAMES:
            add(urljoin(base_url, subdir + name))

    # ── 8. Domain-specific patterns ──────────────────────────────────
    #   Different hosting platforms have known project.json locations
    if host.endswith(".neocities.org") or host == "neocities.org":
        # Neocities: flat structure common, also /cyoa/ subfolder
        for name in PRIMARY_NAMES:
            add(f"https://{host}/{name}")
            add(f"https://{host}/cyoa/{name}")
            add(f"https://{host}/game/{name}")

    elif host.endswith(".github.io"):
        # GitHub Pages: /docs/, /public/, /<repo-name>/
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, f"docs/{name}"))
            add(urljoin(base_url, f"gh-pages/{name}"))
            add(f"https://{host}/{name}")
        # Try repo subpath: user.github.io/repo/ → try /repo/project.json
        if len(path_parts) >= 1:
            repo = path_parts[0]
            for name in PRIMARY_NAMES:
                add(f"https://{host}/{repo}/{name}")

    elif "itch.io" in host:
        # itch.io: game files in /game/ or /public/
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, f"game/{name}"))
            add(urljoin(base_url, f"public/{name}"))

    elif host.endswith(".cyoa.cafe") or host == "cyoa.cafe":
        # cyoa.cafe subdomains: project.json usually at slug root
        for name in PRIMARY_NAMES:
            add(urljoin(base_url, name))
            if path_parts:
                add(f"https://{host}/{path_parts[0]}/{name}")

    # ── 9. Case variant: Project.json (capital P — some Windows servers) ──
    for base in [base_url] + ancestor_bases[:2]:
        add(urljoin(base, "Project.json"))
        add(urljoin(base, "PROJECT.JSON"))

    return result


def strip_document_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if not path.endswith("/"):
        path = "/".join(path.split("/")[:-1])
        path = (path + "/") if path else "/"
    return urlunparse(parsed._replace(path=path, query=""))


def _scan_file_for_assets(
    text: str,
    file_url: str,
    base_url: str,
    file_ext: str = ".js",
) -> Set[str]:
    """
    Scan a downloaded JS or CSS file for asset URL references and return
    a set of absolute URLs that should be downloaded.

    Handles:
    - String literals: './assets/img.webp', '../images/foo.png'
    - CSS url(): url("image.webp"), url('../fonts/font.woff2')
    - Bare filenames: 'audio.mp3' (no directory component)
    - Absolute URLs: 'https://cdn.example.com/img.avif'
    - Vite/Webpack lazy chunks: import("./chunk-abc123.js")
    - Template literals with plain path segments
    """
    import re as _re

    ASSET_EXTS = (
        r'\.(?:webp|avif|png|jpg|jpeg|gif|svg|ico|bmp|tiff'
        r'|mp3|ogg|wav|m4a|aac|flac'
        r'|mp4|webm|mov'
        r'|woff2|woff|ttf|otf|eot'
        r'|json|css)(?:\?[^\s"\'`<>]*)?'
    )
    # JS chunk pattern — matches Vite/Rollup/Webpack hashed chunk names
    JS_CHUNK = r'\.m?js(?:\?[^\s"\'`<>]*)?'
    # Quote chars — double, single, backtick
    Q = r'''["'`]'''

    found: Set[str] = set()
    file_base = file_url.rsplit('/', 1)[0] + '/'   # directory of this file

    def _resolve(raw: str) -> Optional[str]:
        raw = raw.strip().lstrip()
        if not raw or len(raw) > 400:
            return None
        if raw.startswith(('data:', '#', 'javascript:', 'mailto:',
                            'http://www.w3.org', 'blob:')):
            return None
        if raw.startswith('https://') or raw.startswith('http://'):
            return raw
        if raw.startswith('//'):
            return 'https:' + raw
        if raw.startswith('./') or raw.startswith('../'):
            resolved = urljoin(file_base, raw)
            # ── Fix: Vite encodes asset paths relative to site root, not JS file ──
            # If JS file is inside /assets/ and path starts with ./assets/,
            # urljoin creates /assets/assets/... (double). Correct it.
            double = urlparse(base_url).path.rstrip('/') + urlparse(base_url).path.rstrip('/')
            parsed_r = urlparse(resolved)
            path_r   = parsed_r.path
            # Detect /X/X/ double-prefix pattern (e.g. /assets/assets/)
            base_path = urlparse(base_url).path.rstrip('/')
            if base_path:
                seg = base_path.lstrip('/')
                double_pat = f'/{seg}/{seg}/'
                if double_pat in path_r:
                    path_r  = path_r.replace(double_pat, f'/{seg}/', 1)
                    resolved = urlunparse(parsed_r._replace(path=path_r))
            # Also fix: /assets/assets/ specifically (common Vite pattern)
            if '/assets/assets/' in resolved:
                resolved = resolved.replace('/assets/assets/', '/assets/', 1)
            return resolved
        if raw.startswith('/'):
            parsed_base = urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{raw}"
        return urljoin(file_base, raw)

    # ── CSS url() — scan in ALL file types (CSS, HTML, AND JS) ────────
    # Many JS frameworks embed CSS-in-JS with url() references.
    for m in _re.finditer(
        r'url\(\s*["\']?([^"\')\s]+' + ASSET_EXTS + r'[^"\')\s]*)["\']?\s*\)',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── CSS image-set() — responsive image declarations ───────────────
    for m in _re.finditer(
        r'image-set\([^)]*' + Q + r'([^"\'`]+' + ASSET_EXTS + r')' + Q,
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── String/template literals: "path.ext", 'path.ext', `path.ext` ─
    # CRITICAL: includes single-quotes — many minified JS uses them.
    for m in _re.finditer(
        Q + r'([^"\'`\n\r<>{}()|\\]{1,300}' + ASSET_EXTS + r')' + Q,
        text, _re.IGNORECASE
    ):
        raw = m.group(1)
        if raw.strip() in ('.json', '.mp3', '.webp', '.png', '.js', '.css'):
            continue
        r = _resolve(raw)
        if r: found.add(r)

    # ── Static ES module imports: import{...}from"./foo.js" / import"./foo.js" ──
    # These are NOT dynamic import() calls — they use 'from' or bare import
    for m in _re.finditer(
        r'(?:from|import)\s*["\'](\.[^"\']+' + JS_CHUNK + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── Vite __vite__mapDeps bare filenames ───────────────────────────
    # Vite bundles contain arrays of bare chunk filenames for preloading.
    # These have NO ./ prefix but live in /assets/ (Vite convention).
    # Pattern: __vite__mapDeps([0,1],m=__vite__mapDeps,d=(m.f||(m.f=["foo.js","bar.js"...])))
    _vite_arr = _re.search(
        r'__vite__mapDeps.*?d=\(m\.f\|\|\(m\.f=(\[.*?\])\)\)',
        text, _re.DOTALL
    )
    if _vite_arr:
        bare_chunks = _re.findall(r'["\']([A-Za-z0-9][^"\']+' + JS_CHUNK + r')["\']',
                                  _vite_arr.group(1))
        for bc in bare_chunks:
            if '/' not in bc:   # bare name → Vite puts these in /assets/
                parsed_base = urlparse(base_url)
                r = f"{parsed_base.scheme}://{parsed_base.netloc}/assets/{bc}"
                found.add(r)

    # ── Lazy-loaded JS chunks: import("./chunk-abc.js") ──────────────
    # Dynamic import() for Vite code-split chunks
    for m in _re.finditer(
        r'import\s*\(\s*["\'](\.[^"\']+' + JS_CHUNK + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── import/require with asset extensions ──────────────────────────
    for m in _re.finditer(
        r'(?:import|require)\s*\(\s*["\']([^"\']+' + ASSET_EXTS + r')["\']',
        text, _re.IGNORECASE
    ):
        r = _resolve(m.group(1))
        if r: found.add(r)

    # ── Bare filenames (no path separator) → try root + common subdirs ─
    # Howler.js loads audio relative to HTML root — bare MP3 names are
    # loaded from root or common music directories.
    _bare_re = _re.compile(
        r'["\`]([A-Za-z0-9][^"\`/\n\r<>{}()|\\]{0,200}' + ASSET_EXTS + r')["\`]',
        _re.IGNORECASE
    )
    for m in _bare_re.finditer(text):
        raw = m.group(1)
        if '/' not in raw:
            found.add(urljoin(base_url.rstrip('/') + '/', raw))
            ext_low = raw.rsplit('.', 1)[-1].lower()
            if ext_low in ('mp3', 'ogg', 'wav', 'm4a', 'aac', 'flac'):
                # Audio: try all common music/audio subdirectories
                for sub in ('music/', 'audio/', 'bgm/', 'sfx/', 'sounds/',
                            'assets/music/', 'assets/audio/', 'assets/bgm/',
                            'assets/sfx/', 'assets/sounds/',
                            'static/music/', 'public/music/'):
                    found.add(urljoin(base_url.rstrip('/') + '/', sub + raw))
            else:
                for sub in ('assets/images/', 'images/', 'assets/', 'img/'):
                    found.add(urljoin(base_url.rstrip('/') + '/', sub + raw))

    # ── HTML attributes: srcset, data-src, data-lazy, poster ──────────
    if file_ext in ('.html', '.htm', '.svg'):
        # srcset — "img-2x.webp 2x, img-1x.webp 1x"
        for m in _re.finditer(r'srcset\s*=\s*["\']([^"\']+)["\']', text, _re.IGNORECASE):
            for entry in m.group(1).split(','):
                url_part = entry.strip().split()[0] if entry.strip() else ''
                if url_part:
                    r = _resolve(url_part)
                    if r: found.add(r)
        # data-src, data-lazy, data-background, poster
        for attr in ('data-src', 'data-lazy', 'data-background', 'data-poster',
                     'poster', 'data-original', 'data-bg'):
            for m in _re.finditer(
                attr + r'\s*=\s*["\']([^"\']+' + ASSET_EXTS + r')["\']',
                text, _re.IGNORECASE
            ):
                r = _resolve(m.group(1))
                if r: found.add(r)
        # <source src="..."> elements
        for m in _re.finditer(
            r'<source[^>]+src\s*=\s*["\']([^"\']+)["\']', text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)
        # <link rel="preload" href="...">
        for m in _re.finditer(
            r'<link[^>]+rel\s*=\s*["\']preload["\'][^>]+href\s*=\s*["\']([^"\']+)["\']',
            text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)

    # ── SVG <image href="..."> / <image xlink:href="..."> ─────────────
    if file_ext in ('.svg', '.html', '.htm'):
        for m in _re.finditer(
            r'<image[^>]+(?:href|xlink:href)\s*=\s*["\']([^"\']+)["\']',
            text, _re.IGNORECASE
        ):
            r = _resolve(m.group(1))
            if r: found.add(r)

    # ── Webpack/Vite manifest files ───────────────────────────────────
    # If this file looks like a build manifest, extract all asset paths.
    if file_ext == '.json':
        try:
            manifest = json.loads(text)
            if isinstance(manifest, dict):
                def _walk_manifest(obj):
                    if isinstance(obj, str):
                        if _re.search(ASSET_EXTS, obj, _re.IGNORECASE):
                            r = _resolve(obj)
                            if r: found.add(r)
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            _walk_manifest(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            _walk_manifest(v)
                _walk_manifest(manifest)
        except (json.JSONDecodeError, ValueError):
            pass

    # ── Service Worker precache manifest ──────────────────────────────
    # Pattern: self.__precacheManifest = [{url: "...", revision: "..."}]
    if file_ext in ('.js', '.mjs', '.cjs'):
        for m in _re.finditer(
            r'__precacheManifest\s*=\s*(\[.*?\])',
            text, _re.DOTALL
        ):
            try:
                entries = json.loads(m.group(1))
                for entry in entries:
                    url_val = entry.get('url', '') if isinstance(entry, dict) else ''
                    if url_val:
                        r = _resolve(url_val)
                        if r: found.add(r)
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass

    # ── Deduplicate / validate ────────────────────────────────────────
    cleaned: Set[str] = set()
    for u in found:
        try:
            parsed = urlparse(u)
            if parsed.scheme in ('http', 'https') and parsed.netloc:
                cleaned.add(urlunparse(parsed._replace(fragment='')))
        except Exception:
            pass

    return cleaned



def _deep_scan_and_download_assets(
    folder: str,
    base_url: str,
    output_dir: str,
    wait_time: int = DEFAULT_WAIT_TIME,
    max_workers: int = DEFAULT_MAX_WORKERS,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "aggressive_recovery",
    ai_budget: Optional[AIUsageBudget] = None,
    skip_urls: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """
    Iteratively scan ALL JS, CSS, and HTML files in `folder` for asset
    URL references, download missing ones, then re-scan newly downloaded
    files — repeating until no new assets are found (BFS convergence).

    Handles both project.json-based CYOAs and pure custom React/Vite viewers.
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE

    TEXT_EXTS   = {'.js', '.css', '.html', '.htm', '.mjs', '.cjs', '.json', '.svg'}
    all_downloaded: Dict[str, str] = {}   # url → rel_path
    failed_deep_assets: List[Dict[str, str]] = []
    scanned_files: Set[str]        = set()   # abs file paths already scanned
    known_urls:    Set[str]        = set()   # candidate URLs already seen

    # Pre-populate with URLs already downloaded by process_images
    # (prevents double-downloading the same assets)
    if skip_urls:
        known_urls |= skip_urls
        logger.debug(f"[deep scan] Skipping {len(skip_urls)} URL(s) already downloaded by process_images")

    # ── Pre-build disk file index for O(1) existence checks ───────────
    # Walking the folder once is far cheaper than calling os.path.exists()
    # for every candidate URL individually.
    _disk_files: Set[str] = set()

    def _rebuild_disk_index() -> None:
        _disk_files.clear()
        for _root, _, _fnames in os.walk(folder):
            for _fn in _fnames:
                _rel = os.path.relpath(os.path.join(_root, _fn), folder)
                _disk_files.add(_rel.replace('\\', '/'))

    _rebuild_disk_index()

    def _collect_candidates_from_folder(scan_folder: str) -> Set[str]:
        """Scan all unscanned text files in scan_folder, return new candidate URLs."""
        new_candidates: Set[str] = set()
        for root, _, files in os.walk(scan_folder):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
                fpath = os.path.join(root, fn)
                if fpath in scanned_files:
                    continue
                scanned_files.add(fpath)
                rel    = os.path.relpath(fpath, folder).replace('\\', '/')
                f_url  = urljoin(base_url.rstrip('/') + '/', rel)
                try:
                    with open(fpath, encoding='utf-8', errors='replace') as _fh:
                        text = _fh.read()
                    urls = _scan_file_for_assets(text, f_url, base_url, ext)
                    new_candidates |= (urls - known_urls)
                except Exception as e:
                    logger.debug(f"[deep scan] {fn}: {e}")
        return new_candidates

    def _url_to_local(url: str) -> str:
        """Convert absolute URL to relative path within the folder."""
        parsed   = urlparse(url)
        rel_path = parsed.path.lstrip('/')
        base_path = urlparse(base_url).path.rstrip('/')
        if base_path and rel_path.startswith(base_path.lstrip('/')):
            rel_path = rel_path[len(base_path.lstrip('/')):]
        return rel_path.lstrip('/')

    # ── Reusable session with connection pooling ──────────────────────
    # Keeps TCP connections alive across requests to the same host,
    # avoiding repeated TLS handshakes (saves ~100-200ms per request).
    _session = requests.Session()
    _session.headers.update({"User-Agent": "Mozilla/5.0"})
    _adapter = requests.adapters.HTTPAdapter(
        pool_connections=16, pool_maxsize=32, max_retries=1)
    _session.mount("https://", _adapter)
    _session.mount("http://", _adapter)
    _proxy = _get_active_proxy()
    if _proxy:
        _session.proxies.update({"http": _proxy, "https": _proxy})

    _http2_client = None
    if _HTTP2_ENABLED:
        try:
            import httpx  # type: ignore
            _http2_kwargs = dict(
                http2=True,
                follow_redirects=True,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if _proxy:
                # httpx changed proxy keyword behavior across versions. Try modern
                # proxy= first, then fall back to proxies= for older releases.
                try:
                    _http2_client = httpx.Client(proxy=_proxy, **_http2_kwargs)
                except TypeError:
                    _http2_client = httpx.Client(proxies={"http://": _proxy, "https://": _proxy}, **_http2_kwargs)
                logger.info("[deep scan] HTTP/2 enabled via httpx with proxy")
            else:
                _http2_client = httpx.Client(**_http2_kwargs)
                logger.info("[deep scan] HTTP/2 enabled via httpx")
        except Exception as e:
            logger.warning(f"[deep scan] HTTP/2 unavailable, falling back to requests: {e}")
            _http2_client = None

    def _try_fetch(url: str) -> Tuple[str, Optional[bytes], int]:
        """Single-pass GET with unified fallback support.

        Tries HTTP/2 first when enabled, then falls back to fetch_response so
        Cloudflare/FlareSolverr, proxy, DNS, and retry policy remain consistent.
        """
        try:
            _domain_throttle(url)
            hdrs = get_headers_for_url(url) or {}
            if _http2_client is not None:
                try:
                    r2 = _http2_client.get(url, headers=hdrs)
                    if r2.status_code == 200:
                        content = r2.content
                        _throttle_bandwidth(len(content))
                        return url, content, 200
                    if r2.status_code not in {403, 429, 503}:
                        return url, None, r2.status_code
                except Exception:
                    pass
            r = fetch_response(url, extra_headers=hdrs, timeout=20, as_bytes=True)
            if r is not None and r.status_code == 200:
                content = r.content
                _throttle_bandwidth(len(content))
                return url, content, 200
            return url, None, int(getattr(r, "status_code", 0) or 0)
        except Exception:
            return url, None, 0

    round_n = 0
    max_rounds = 6   # safety cap

    # ── Manifest probing: check for Vite/Webpack/CRA manifest files ────
    _manifest_paths = [
        '.vite/manifest.json',
        'asset-manifest.json',
        'manifest.json',
        'build/asset-manifest.json',
    ]
    parsed_base = urlparse(base_url)
    for mp in _manifest_paths:
        mp_url = f"{parsed_base.scheme}://{parsed_base.netloc}/{mp}"
        mp_rel = mp.replace('/', os.sep)
        if mp.replace('/', '/') in _disk_files:
            continue
        try:
            _, content, status = _try_fetch(mp_url)
            if content and content.strip()[:1] in (b'{', b'['):
                mp_local = _safe_join(folder, mp_rel)
                os.makedirs(os.path.dirname(mp_local), exist_ok=True)
                with open(mp_local, 'wb') as f:
                    f.write(content)
                _disk_files.add(mp)
                logger.info(f"[deep scan] Found manifest: {mp}")
        except Exception:
            pass

    # ── Parallel concurrency: scale with workers, cap at 20 ───────────
    _dl_workers = min(max(max_workers, 8), 20)

    while round_n < max_rounds:
        round_n += 1

        # ── Step 1: collect candidate URLs from all unscanned files ──
        new_candidates = _collect_candidates_from_folder(folder)
        if not new_candidates:
            logger.debug(f"[deep scan] Round {round_n}: no new candidates → done")
            break

        known_urls |= new_candidates
        logger.debug(f"[deep scan] Round {round_n}: {len(new_candidates)} new candidate(s)")

        # ── Step 2: filter out already-on-disk files (O(1) set check) ─
        to_download: List[str] = []
        for url in new_candidates:
            rel = _url_to_local(url)
            if rel and rel in _disk_files:
                continue   # already on disk
            to_download.append(url)

        if not to_download:
            logger.debug(f"[deep scan] Round {round_n}: all already on disk")
            break

        logger.info(f"[deep scan] Round {round_n}: fetching {len(to_download)} asset(s)…")

        # ── Step 3: single-pass parallel GET (replaces HEAD+GET) ──────
        new_this_round = 0
        vite_retry: List[str] = []

        with _TPE(max_workers=_dl_workers) as ex:
            for url, content, status in ex.map(_try_fetch, to_download):
                if content:
                    rel = _url_to_local(url)
                    if not rel:
                        rel = os.path.basename(urlparse(url).path) or 'asset'
                    local = _safe_join(folder, rel)
                    os.makedirs(os.path.dirname(local), exist_ok=True)
                    try:
                        with open(local, 'wb') as f:
                            f.write(content)
                        all_downloaded[url] = rel
                        _disk_files.add(rel)
                        new_this_round += 1
                        logger.info(f"  [deep ✓] {rel}")
                    except Exception as e:
                        failed_deep_assets.append({"url": url, "path": rel, "error": f"save failed: {e}", "kind": "deep-scan"})
                        logger.debug(f"[deep scan] save {rel}: {e}")
                elif status != 200:
                    failed_deep_assets.append({"url": url, "path": _url_to_local(url), "error": f"HTTP {status or 'request failed'}", "kind": "deep-scan"})
                    # Vite correction: if root-level JS 404'd, try /assets/
                    p = urlparse(url).path
                    if p.count('/') == 1 and p.lower().endswith(('.js', '.mjs')):
                        parsed = urlparse(url)
                        alt = urlunparse(parsed._replace(path='/assets' + parsed.path))
                        if alt not in known_urls:
                            vite_retry.append(alt)
                            known_urls.add(alt)

        # ── Vite /assets/ retry for root-level 404s ───────────────────
        if vite_retry:
            with _TPE(max_workers=_dl_workers) as ex:
                for url, content, status in ex.map(_try_fetch, vite_retry):
                    if content:
                        rel = _url_to_local(url)
                        if not rel:
                            rel = os.path.basename(urlparse(url).path) or 'asset'
                        local = _safe_join(folder, rel)
                        os.makedirs(os.path.dirname(local), exist_ok=True)
                        try:
                            with open(local, 'wb') as f:
                                f.write(content)
                            all_downloaded[url] = rel
                            _disk_files.add(rel)
                            new_this_round += 1
                            logger.info(f"  [deep ✓ Vite] {rel}")
                        except Exception as e:
                            failed_deep_assets.append({"url": url, "path": rel, "error": f"save failed: {e}", "kind": "deep-scan"})
                            logger.debug(f"[deep scan] save {rel}: {e}")
            logger.debug(f"[deep scan] Vite /assets/ correction: "
                         f"{len(vite_retry)} tried")

        logger.info(f"[deep scan] Round {round_n}: {new_this_round} saved"
                    + (" — rescanning for new refs…" if new_this_round else ""))

        if new_this_round == 0:
            break   # nothing new downloaded → converged

        # Refresh disk index after downloads
        _rebuild_disk_index()

    if _http2_client is not None:
        try:
            _http2_client.close()
        except Exception:
            pass

    if all_downloaded:
        logger.info(f"[deep scan] Complete: {len(all_downloaded)} total asset(s) downloaded"
                    f" in {round_n} round(s)")

    # ── AI-assisted final round ────────────────────────────────────────
    ai_provider = _normalize_ai_provider(ai_provider or _get_ai_provider())
    ai_mode = _normalize_ai_mode(ai_mode)
    if _ai_mode_allows("asset_scan", ai_mode) and _ai_is_available(ai_api_key, ai_provider):
        try:
            js_files_ai: Dict[str, str] = {}
            for _root, _, _files in os.walk(folder):
                for _fn in _files:
                    if _fn.endswith(('.js', '.mjs', '.cjs')):
                        _fp = os.path.join(_root, _fn)
                        try:
                            _ct = pathlib.Path(_fp).read_text(encoding='utf-8', errors='replace')
                            if len(_ct) > 500:
                                js_files_ai[os.path.relpath(_fp, folder)] = _ct
                        except Exception:
                            pass

            if js_files_ai:
                ai_candidates = _ai_analyze_js_for_assets(js_files_ai, base_url, api_key=ai_api_key,
                    provider=ai_provider, ai_mode=ai_mode, budget_obj=ai_budget)
                ai_new = [u for u in ai_candidates
                          if u not in known_urls and u not in all_downloaded]
                if ai_new:
                    logger.info(f"[AI scan] {len(ai_new)} new candidate(s) from AI analysis")
                    known_urls.update(ai_new)

                    ai_new_count = 0
                    def _try_fetch_ai(url: str) -> Tuple[str, Optional[bytes], int]:
                        return _try_fetch(url)

                    with _TPE(max_workers=_dl_workers) as ex:
                        for url_ai, content_ai, _ in ex.map(_try_fetch_ai, ai_new):
                            if not content_ai:
                                continue
                            rel_ai = _url_to_local(url_ai)
                            if not rel_ai:
                                rel_ai = os.path.basename(urlparse(url_ai).path) or 'asset'
                            local_ai = _safe_join(folder, rel_ai)
                            os.makedirs(os.path.dirname(local_ai), exist_ok=True)
                            try:
                                with open(local_ai, 'wb') as f_ai:
                                    f_ai.write(content_ai)
                                all_downloaded[url_ai] = rel_ai
                                ai_new_count += 1
                                logger.info(f"  [AI ✓] {rel_ai}")
                            except Exception:
                                pass
                    logger.info(f"[AI scan] Done — {ai_new_count} new asset(s)")
        except Exception as e:
            logger.debug(f"[AI scan] error: {e}")

    if failed_deep_assets:
        try:
            report_target = write_asset_failure_summary(
                failed_deep_assets,
                folder,
                source_url=base_url,
                title="Broken Deep-Scan Asset Report",
            )
            logger.warning(f"[deep scan] {len(failed_deep_assets)} asset(s) failed; see {os.path.basename(report_target or 'backup_report.txt')}")
        except Exception as e:
            logger.debug(f"[deep scan] broken report failed: {e}")

    return all_downloaded



def get_headers_for_url(url: str) -> Optional[Dict]:
    """
    Return domain-specific headers to bypass CDN restrictions and hotlink
    protection. Each entry is tuned to what the host actually checks.
    """
    try:
        parsed  = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return {"User-Agent": "Mozilla/5.0"}

    CDN_EXACT: Dict[str, Dict] = {
        "imgur.com":                {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "i.imgur.com":              {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "i.stack.imgur.com":        {"User-Agent": "curl/8.1.1", "Accept": "*/*"},
        "cdn.discordapp.com":       {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                     "Referer": "https://discord.com/",
                                     "Accept": "image/avif,image/webp,*/*"},
        "media.discordapp.net":     {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                     "Referer": "https://discord.com/",
                                     "Accept": "image/avif,image/webp,*/*"},
        "files.catbox.moe":         {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        "litter.catbox.moe":        {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        "res.cloudinary.com":       {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*"},
        "preview.redd.it":          {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)",
                                     "Accept": "image/webp,*/*"},
        "i.redd.it":                {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://www.reddit.com/"},
        "64.media.tumblr.com":      {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://www.tumblr.com/"},
        "i.pximg.net":              {"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://www.pixiv.net/",
                                     "Accept": "image/webp,*/*"},
        "pbs.twimg.com":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://twitter.com/"},
        "drive.google.com":         {"User-Agent": "Mozilla/5.0",
                                     "Accept": "image/webp,*/*,application/octet-stream"},
        "neocities.org":            {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        # ── Booru ─────────────────────────────────────────────────────────
        "img3.rule34.xxx":          {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.xxx/"},
        "img.rule34.xxx":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.xxx/"},
        "img.hypnohub.net":         {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://hypnohub.net/"},
        "img1.gelbooru.com":        {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://gelbooru.com/"},
        "img2.gelbooru.com":        {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://gelbooru.com/"},
        "cdn.donmai.us":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://danbooru.donmai.us/"},
        "static1.e621.net":         {"User-Agent": "Mozilla/5.0 (compatible; e621-dl/1.0)",
                                     "Accept": "image/webp,*/*"},
        "static1.e926.net":         {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*"},
        "img3.sankakucomplex.com":  {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://sankakucomplex.com/"},
        "img.sankakucomplex.com":   {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://sankakucomplex.com/"},
        "img.rule34.paheal.net":    {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://rule34.paheal.net/"},
        "safebooru.org":            {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://safebooru.org/"},
        "derpicdn.net":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://derpibooru.org/"},
        "furbooru.org":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://furbooru.org/"},
        "tbib.org":                 {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://tbib.org/"},
        "xbooru.com":               {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                     "Referer": "https://xbooru.com/"},
    }

    if hostname in CDN_EXACT:
        return CDN_EXACT[hostname]

    CDN_SUFFIX: Dict[str, Dict] = {
        ".patreonusercontent.com": {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.patreon.com/"},
        ".wixmp.com":              {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.deviantart.com/"},
        ".tumblr.com":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://www.tumblr.com/"},
        ".cloudfront.net":         {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".amazonaws.com":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".azureedge.net":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".githubusercontent.com":  {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".neocities.org":          {"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        ".sankakucomplex.com":     {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://sankakucomplex.com/"},
        ".donmai.us":              {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://danbooru.donmai.us/"},
        ".e621.net":               {"User-Agent": "Mozilla/5.0 (compatible; e621-dl/1.0)",
                                    "Accept": "image/webp,*/*"},
        ".rule34.xxx":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://rule34.xxx/"},
        ".gelbooru.com":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://gelbooru.com/"},
        ".hypnohub.net":           {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://hypnohub.net/"},
        ".paheal.net":             {"User-Agent": "Mozilla/5.0", "Accept": "image/webp,*/*",
                                    "Referer": "https://rule34.paheal.net/"},
    }

    for suffix, hdrs in CDN_SUFFIX.items():
        if hostname.endswith(suffix):
            return hdrs

    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


if __name__ == "__main__":
    main()
