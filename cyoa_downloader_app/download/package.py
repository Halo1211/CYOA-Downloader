"""Package/finalization/ZIP helpers.

Phase 13 physically moves small package/output helpers out of legacy.py while
keeping their public signatures and file formats unchanged.
"""

from __future__ import annotations

import hashlib as _hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time as _time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

try:
    import tldextract  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tldextract = None

from ..app_info import _APP_VERSION
from ..logging_setup import logger
from ..core.archive import validate_zip_archive
from ..core.atomic_io import atomic_write_text, validate_response_content_length
from ..core.cancellation import _emit_progress_event, _raise_if_cancelled
from ..core.output import prepare_clean_output_folder, _cleanup_recent_part_files
from ..core.url_utils import canonicalize_url
from ..network.throttle import _throttle_bandwidth



def looks_like_project_object(obj: dict) -> bool:
    """Compatibility import for project-shape detection."""
    from ..project.parse import looks_like_project_object as _looks_like_project_object
    return _looks_like_project_object(obj)

def _finalize_site_folder(site_folder: str, file_name: str, zip_output: bool) -> None:
    """Zip site folder if requested, then delete the folder."""
    if zip_output:
        zip_name = file_name + "_site.zip"
        logger.info(f"Zipping → {zip_name}")
        zip_temp_folder(site_folder, zip_name=zip_name)
        shutil.rmtree(site_folder, ignore_errors=True)
        logger.info(f"Folder {site_folder} deleted after zipping.")
    else:
        logger.info(f"ICC folder kept: {site_folder}")


_VERIFY_LOCAL_REF_RE = re.compile(
    r"""(?:src|href|url)\s*[=:(]\s*["']?"""      # attribute/css lead-in
    r"""(?!https?:|data:|//|javascript:|mailto:|#)"""  # skip remote/data/anchors
    r"""([^"')\s>]+\.(?:png|jpe?g|gif|webp|svg|bmp|ico|"""
    r"""mp3|ogg|wav|m4a|aac|flac|mp4|webm|"""
    r"""woff2?|ttf|otf|eot|css|js|json))""",
    re.IGNORECASE,
)


_VERIFY_JSON_PATH_RE = re.compile(
    r"""["'](?!https?:|data:|//)"""
    r"""((?:[\w.\-]+/)*[\w.\-]+\.(?:png|jpe?g|gif|webp|svg|bmp|"""
    r"""mp3|ogg|wav|m4a|aac|flac|mp4|webm|woff2?|ttf|otf))["']""",
    re.IGNORECASE,
)


_MANIFEST_NAME = "cyoa_manifest.json"


_MANIFEST_HASH_CHUNK = 1 << 20


def _hash_file_sha256(path: str) -> Optional[str]:
    """Return the sha256 hex digest of a file, streaming to bound memory."""
    try:
        h = _hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_MANIFEST_HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _walk_package_files(root: str) -> List[str]:
    """Return absolute paths of every file under root (sorted, deterministic)."""
    out: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    out.sort()
    return out


def write_package_manifest(folder: str) -> Tuple[bool, str]:
    """Write a checksum manifest for an existing output folder.

    Records sha256 + size for every file under ``folder`` (excluding the
    manifest itself) into ``cyoa_manifest.json`` at the folder root. Returns
    (ok, message). Purely additive and opt-in: never invoked by the download
    pipeline, only by ``--verify FOLDER --write-manifest``.
    """
    if not folder or not os.path.isdir(folder):
        return False, f"FAIL  folder does not exist: {folder}"
    root = os.path.abspath(folder)
    manifest_path = os.path.join(root, _MANIFEST_NAME)
    entries: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for p in _walk_package_files(root):
        if os.path.basename(p) == _MANIFEST_NAME:
            continue
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        digest = _hash_file_sha256(p)
        if digest is None:
            skipped += 1
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        entries[rel] = {"sha256": digest, "size": size}
    payload = {
        "manifest_version": 1,
        "app_version": _APP_VERSION,
        "created_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "file_count": len(entries),
        "files": entries,
    }
    try:
        # Reuse the project's atomic writer when available; fall back to direct.
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        try:
            atomic_write_text(manifest_path, text)
        except Exception:
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write(text)
    except Exception as e:
        return False, f"FAIL  could not write manifest: {e}"
    msg = f"OK  wrote {_MANIFEST_NAME} with {len(entries)} file checksum(s)"
    if skipped:
        msg += f" ({skipped} unreadable file(s) skipped)"
    return True, msg


def _load_package_manifest(root: str) -> Optional[Dict[str, Any]]:
    """Load and validate a manifest sidecar. Returns the dict or None."""
    mp = os.path.join(root, _MANIFEST_NAME)
    if not os.path.isfile(mp):
        return None
    try:
        with open(mp, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except Exception:
        return None
    return None


def verify_output_package(folder: str) -> Tuple[bool, str]:
    """Validate a downloaded CYOA output folder.

    Checks performed (all read-only):
      * folder exists and is non-empty
      * project.json (if present) parses as JSON and looks like a project
      * no zero-byte asset files (a frequent silent-failure signature)
      * local asset references in project.json/HTML/CSS/JS resolve to a file
        on disk (missing-asset detection)
      * surfaces counts for failed_assets/failed_images logs if present

    Returns (ok, human_readable_report). ok is False when any blocking issue is
    found (missing folder, broken project.json, missing referenced assets, or
    zero-byte files); informational notes alone keep ok True.
    """
    issues: List[str] = []      # blocking
    notes: List[str] = []       # informational
    lines: List[str] = [f"CYOA Downloader v{_APP_VERSION} package verification",
                        "=" * 56,
                        f"Folder: {folder}"]

    if not folder or not os.path.isdir(folder):
        return False, "\n".join(lines + ["", "FAIL  folder does not exist or is not a directory"])

    root = os.path.abspath(folder)
    all_files: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            all_files.append(os.path.join(dirpath, fn))
    if not all_files:
        return False, "\n".join(lines + ["", "FAIL  folder is empty"])

    rel = lambda p: os.path.relpath(p, root)

    # ── 1. zero-byte files ───────────────────────────────────────
    report_names = {"backup_report.txt", "failed_assets.txt", "failed_images.txt",
                    "failed_urls.txt", "skipped_youtube_audio.txt"}
    zero_byte = []
    for p in all_files:
        base = os.path.basename(p)
        if base in report_names:
            continue
        try:
            if os.path.getsize(p) == 0:
                zero_byte.append(rel(p))
        except OSError:
            issues.append(f"unreadable file: {rel(p)}")
    if zero_byte:
        for z in zero_byte[:25]:
            issues.append(f"zero-byte asset: {z}")
        if len(zero_byte) > 25:
            issues.append(f"... and {len(zero_byte) - 25} more zero-byte files")

    # ── 2. project.json sanity ───────────────────────────────────
    project_text = ""
    pj_path = os.path.join(root, "project.json")
    if os.path.isfile(pj_path):
        try:
            with open(pj_path, encoding="utf-8") as f:
                project_text = f.read()
            obj = json.loads(project_text)
            if isinstance(obj, dict) and looks_like_project_object(obj):
                lines.append("OK    project.json parses + looks like a project")
            elif isinstance(obj, dict):
                notes.append("project.json parses but lacks typical project keys")
            else:
                issues.append("project.json is valid JSON but not an object")
        except Exception as e:
            issues.append(f"project.json failed to parse: {e}")
    else:
        notes.append("no project.json at root (expected for pure-website modes)")

    # ── 2b. manifest checksum verification (if a sidecar exists) ──
    manifest_path = os.path.join(root, _MANIFEST_NAME)
    manifest = _load_package_manifest(root)
    if manifest is not None:
        recorded = manifest.get("files", {})
        present_rel = {rel(p).replace(os.sep, "/") for p in all_files
                       if os.path.basename(p) != _MANIFEST_NAME}
        recorded_rel = set(recorded.keys())
        corrupted = []
        missing_from_disk = sorted(recorded_rel - present_rel)
        extra_on_disk = sorted(present_rel - recorded_rel)
        for relpath in sorted(recorded_rel & present_rel):
            entry = recorded[relpath]
            if not isinstance(entry, dict):
                issues.append(f"invalid manifest entry (expected object): {relpath}")
                continue
            want = entry.get("sha256")
            got = _hash_file_sha256(os.path.join(root, relpath))
            if want and got and want != got:
                corrupted.append(relpath)
        lines.append(f"OK    manifest found ({manifest.get('file_count', len(recorded))} "
                     f"recorded checksum(s), created {manifest.get('created_utc', '?')})")
        if corrupted:
            for c in corrupted[:25]:
                issues.append(f"checksum mismatch (corrupt/modified): {c}")
            if len(corrupted) > 25:
                issues.append(f"... and {len(corrupted) - 25} more checksum mismatch(es)")
        if missing_from_disk:
            for mfd in missing_from_disk[:25]:
                issues.append(f"file in manifest but missing on disk: {mfd}")
            if len(missing_from_disk) > 25:
                issues.append(f"... and {len(missing_from_disk) - 25} more missing")
        if extra_on_disk:
            notes.append(f"{len(extra_on_disk)} file(s) on disk not in manifest "
                         f"(added after manifest was written)")
        if not corrupted and not missing_from_disk:
            lines.append(f"OK    all {len(recorded_rel & present_rel)} checksummed file(s) intact")
    elif os.path.isfile(manifest_path):
        issues.append(f"invalid or unreadable {_MANIFEST_NAME} sidecar")
    else:
        notes.append(f"no {_MANIFEST_NAME} sidecar — run with --write-manifest to enable "
                     "checksum verification")

    # ── 3. local asset reference resolution ──────────────────────
    # Build a set of present files (relative, forward-slash, lowercased) for
    # tolerant matching against references.
    present = set()
    for p in all_files:
        r = rel(p).replace(os.sep, "/")
        present.add(r.lower())
        present.add(os.path.basename(r).lower())   # basename fallback

    missing_refs = {}   # ref -> source file
    checked_refs = 0

    # Project JSON is structured data, so quoted asset paths are meaningful.
    # Keep this check separate from executable JavaScript where arbitrary
    # strings such as download filenames and ``style.cssText`` are not file
    # dependencies.
    if project_text:
        for match in _VERIFY_JSON_PATH_RE.finditer(project_text):
            ref = match.group(1)
            checked_refs += 1
            norm = ref.split("?")[0].split("#")[0].lstrip("./").replace("\\", "/").lower()
            if norm and norm not in present and os.path.basename(norm) not in present:
                missing_refs.setdefault(ref, "project.json")

    # Reuse the website downloader's context-aware dependency validator. It
    # parses HTML attributes and reachable CSS, and only accepts executable URL
    # contexts in JS. This avoids false positives from sourceMappingURL comments,
    # canvas download filenames, and minified property names.
    try:
        from .website import WebsiteDownloader

        checker = WebsiteDownloader.__new__(WebsiteDownloader)
        checker.output_folder = root
        integrity = checker.validate_integrity()
        checked_refs += len(integrity.get("ok", [])) + len(integrity.get("missing", []))
        for label in integrity.get("missing", []):
            if " â†’ " in label:
                src_name, ref = label.split(" â†’ ", 1)
            elif " → " in label:
                src_name, ref = label.split(" → ", 1)
            else:
                src_name, ref = "website files", label
            missing_refs.setdefault(ref, src_name)
    except Exception as exc:
        notes.append(f"context-aware website dependency scan unavailable: {exc}")

    if missing_refs:
        for ref, src in list(missing_refs.items())[:25]:
            issues.append(f"missing asset: {ref}  (referenced by {src})")
        if len(missing_refs) > 25:
            issues.append(f"... and {len(missing_refs) - 25} more missing asset(s)")
    else:
        lines.append(f"OK    all {checked_refs} local asset reference(s) resolve")

    # ── 4. surface existing failure logs ─────────────────────────
    for logname in ("failed_assets.txt", "failed_images.txt", "failed_urls.txt"):
        lp = os.path.join(root, logname)
        if os.path.isfile(lp):
            try:
                with open(lp, encoding="utf-8", errors="ignore") as f:
                    n = sum(1 for ln in f if ln.strip())
                if n:
                    notes.append(f"{logname} lists {n} prior failure(s) from the original download")
            except OSError:
                pass

    # ── summary ──────────────────────────────────────────────────
    lines.append(f"Files scanned: {len(all_files)} | asset references checked: {checked_refs}")
    lines.append("-" * 56)
    if issues:
        lines.append(f"ISSUES ({len(issues)}):")
        lines.extend(f"  ✗ {i}" for i in issues)
    if notes:
        lines.append(f"NOTES ({len(notes)}):")
        lines.extend(f"  • {n}" for n in notes)
    ok = not issues
    lines.append("-" * 56)
    lines.append("Result: PASS — package looks intact" if ok
                 else f"Result: FAIL — {len(issues)} blocking issue(s)")
    return ok, "\n".join(lines)


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


def get_first_subdomain(url: str) -> str:
    if tldextract is not None:
        try:
            sub = tldextract.extract(url).subdomain
            return sub.split(".")[0] if sub else ""
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in get_first_subdomain (line 18544): %s", _ignored_exc)

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
    if not cleaned:
        return "asset"
    # Guard against Windows reserved device names. A file
    # named CON, PRN, AUX, NUL, COM1-9 or LPT1-9 (with or without an extension,
    # case-insensitive) cannot be created on Windows. A CYOA whose output name
    # resolves to one of these would silently fail to save. Prefix with "_" so
    # the on-disk name is legal everywhere while staying recognizable. The base
    # name (portion before the first dot) is what Windows checks.
    _base = cleaned.split(".", 1)[0]
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", _base):
        cleaned = "_" + cleaned
    # Filesystem name limits (Linux NAME_MAX 255 bytes,
    # Windows ~260-char paths). Over-long names caused OSError 36 at save time
    # so the asset silently failed. Truncate the stem, keep the extension, and
    # append a short hash of the ORIGINAL name so distinct long names cannot
    # collide after truncation. Names within the limit are returned unchanged.
    if len(cleaned.encode("utf-8", "replace")) > 140:
        import hashlib as _hl
        _root, _ext = os.path.splitext(cleaned)
        if len(_ext) > 16:  # absurd "extension" — treat whole thing as stem
            _root, _ext = cleaned, ""
        _digest = _hl.sha1(cleaned.encode("utf-8", "replace")).hexdigest()[:10]
        _rb = _root.encode("utf-8", "replace")[: max(1, 140 - len(_ext) - 11)]
        _root = _rb.decode("utf-8", "ignore")
        cleaned = f"{_root}_{_digest}{_ext}"
    return cleaned


def create_random_temp_folder(prefix: str = "cyoa_") -> str:
    tmp = tempfile.gettempdir()
    # Avoid a check-then-create TOCTOU race. The old code did
    # `if not os.path.exists(folder): os.makedirs(folder)` — between the check and
    # the makedirs, another thread/process could create the same path, raising an
    # unhandled FileExistsError. Create directly and only retry (new uuid) if the
    # name actually collides, so creation is atomic.
    for _ in range(1000):
        folder = os.path.join(tmp, prefix + uuid.uuid4().hex[:8])
        try:
            os.makedirs(folder)
            return folder
        except FileExistsError:
            continue
    # Astronomically unlikely after 1000 uuid attempts; fall back to mkdtemp.
    return tempfile.mkdtemp(prefix=prefix)


def delete_temp_folder(temp_path: str) -> None:
    if os.path.isdir(temp_path):
        shutil.rmtree(temp_path)
        logger.info(f"Temp deleted: {temp_path}")
    else:
        logger.warning(f"Temp not found: {temp_path}")


def atomic_stream_response_to_file(
    response: Any,
    path: str,
    *,
    chunk_size: int = 128 * 1024,
) -> int:
    """Stream an HTTP response to a sibling .part file and atomically replace.

    The function is cancellation-aware, validates an identity Content-Length,
    updates bandwidth/speed telemetry per chunk, and removes partial output on
    every failure path.
    """
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or os.getcwd(), exist_ok=True)
    part = target + f".{os.getpid()}.{threading.get_ident()}.part"
    downloaded = 0
    headers = getattr(response, "headers", {}) or {}
    raw_total = headers.get("Content-Length") or headers.get("content-length")
    try:
        total = int(raw_total) if raw_total not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    _emit_progress_event(
        "file_started",
        name=os.path.relpath(target, os.path.dirname(target)),
        url=str(getattr(response, "url", "") or ""),
        total_bytes=total,
    )
    try:
        with open(part, "wb") as fh:
            for chunk in response.iter_content(chunk_size=max(4096, int(chunk_size))):
                _raise_if_cancelled()
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                _throttle_bandwidth(len(chunk))
                _emit_progress_event("file_progress", downloaded=downloaded, total=total)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError as exc:
                logger.debug(f"fsync unavailable for {part}: {exc}")
        validate_response_content_length(response, downloaded)
        if downloaded <= 0:
            raise IOError("Downloaded response body is empty")
        os.replace(part, target)
        _emit_progress_event("file_completed", name=os.path.basename(target), url=str(getattr(response, "url", "") or ""))
        return downloaded
    except Exception:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError as cleanup_exc:
            logger.debug(f"Could not remove partial stream file {part}: {cleanup_exc}")
        raise


def save_string_to_file(content: str, filename: str, path: str = "") -> None:
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    base, ext = os.path.splitext(filename)
    new = os.path.join(path, filename) if path else filename
    if path:
        os.makedirs(path, exist_ok=True)
    counter = 1
    while os.path.exists(new):
        new = os.path.join(path, f"{base}_{counter}{ext}") if path else f"{base}_{counter}{ext}"
        counter += 1
    atomic_write_text(new, content)
    logger.info(f"Saved: {new}")


def zip_temp_folder(temp_path: str, zip_name: str = "") -> str:
    if not os.path.isdir(temp_path):
        raise ValueError(f"Not a directory: {temp_path}")
    if not zip_name:
        zip_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    zf_name = zip_name if zip_name.endswith(".zip") else zip_name + ".zip"
    target = os.path.abspath(os.path.join(os.getcwd(), zf_name))
    part = target + f".{os.getpid()}.{threading.get_ident()}.part"
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for root, _, files in os.walk(temp_path):
                _raise_if_cancelled()
                for file in files:
                    _raise_if_cancelled()
                    abs_path = os.path.join(root, file)
                    # ZIP spec requires '/' separators. On
                    # Windows os.path.relpath returns backslashes, so a member
                    # like "images\\a.png" would extract as one literal filename
                    # on macOS/Linux instead of an images/ subfolder. Normalize.
                    arc = os.path.relpath(abs_path, start=temp_path).replace("\\", "/")
                    archive.write(abs_path, arcname=arc)
        validate_zip_archive(part)
        os.replace(part, target)
    except Exception:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError as cleanup_exc:
            logger.debug(f"Could not remove partial ZIP {part}: {cleanup_exc}")
        raise
    logger.info(f"ZIP created: {target}")
    return target

__all__ = [
    "_finalize_site_folder", "_hash_file_sha256", "_walk_package_files",
    "write_package_manifest", "_load_package_manifest", "verify_output_package",
    "validate_zip_archive", "atomic_stream_response_to_file",
    "validate_response_content_length", "save_string_to_file", "zip_temp_folder",
    "prepare_clean_output_folder", "_cleanup_recent_part_files",
    "_build_output_name", "clean_url_path_component", "get_first_subdomain",
    "create_random_temp_folder", "delete_temp_folder", "canonicalize_url",
    "_VERIFY_LOCAL_REF_RE", "_VERIFY_JSON_PATH_RE", "_MANIFEST_NAME", "_MANIFEST_HASH_CHUNK",
]
