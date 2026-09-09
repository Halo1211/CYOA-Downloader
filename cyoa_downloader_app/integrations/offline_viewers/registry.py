"""Offline viewer registry/archive registration helpers.

Phase 31 moves registry and archive import logic out of legacy.py while keeping
the old manifest format and bundled-viewer discovery behavior.
"""

from __future__ import annotations

import json
import os
import re
import threading
import zipfile
from collections.abc import Mapping
from typing import Dict, List, Optional

from ...logging_setup import logger
from ...core.archive import validate_zip_archive
from ...core.atomic_io import atomic_write_bytes, atomic_write_text, interprocess_file_lock
from ...core.paths import _safe_archive_rel_path

def _public_script_dir() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir)
    )

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
_VIEWERS_LOCK = threading.RLock()

# Viewer type tags — used to match a CYOA site to the right viewer
# Detected from: script names, meta tags, HTML patterns in the CYOA site
VIEWER_TYPE_HINTS: Dict[str, List[str]] = {
    "icc_plus2": ["core.js", "vite_is_modern_browser", "js/app.js", "js/polyfills.js"],
    "icc_original": ["Viewer 1.8"],
    "icc_plus_legacy": ["New Viewer 1.18.9", "New.Viewer.1.18.9"],
    "icc_legacy": ["app.c533aa25", "chunk-vendors.59af3576", "Viewer 1.8"],
    "lt_ouroumov": ["app.d3103a3b", "chunk-vendors.ae283b72"],
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

VIEWER_FAMILY_GUIDANCE = (
    {
        "family": "icc_plus2",
        "title_en": "ICC Plus 2 Local Viewer",
        "title_id": "Viewer Lokal ICC Plus 2",
        "use_en": "Required for project.json with version 2.x and modern Plus 2 sites.",
        "use_id": "Wajib untuk project.json version 2.x dan situs Plus 2 modern.",
        "required": True,
    },
    {
        "family": "icc_plus_legacy",
        "title_en": "ICC Plus Legacy Viewer",
        "title_id": "Viewer ICC Plus Legacy",
        "use_en": "Recommended for unversioned ICC Plus projects with advanced design and requirement fields.",
        "use_id": "Disarankan untuk project ICC Plus tanpa version dengan field desain dan requirement lanjutan.",
        "required": True,
    },
    {
        "family": "icc_original",
        "title_en": "ICC Original / New Viewer",
        "title_id": "Viewer ICC Original / New",
        "use_en": "Recommended for classic ICC projects; Viewer 1.8 remains separate from ICC Plus.",
        "use_id": "Disarankan untuk project ICC klasik; Viewer 1.8 dipisahkan dari ICC Plus.",
        "required": True,
    },
    {
        "family": "icc_remix",
        "title_en": "ICC Remix Local Viewer",
        "title_id": "Viewer Lokal ICC Remix",
        "use_en": "Recommended when the source HTML identifies an ICC Remix runtime.",
        "use_id": "Disarankan ketika HTML sumber terdeteksi memakai runtime ICC Remix.",
        "required": False,
    },
    {
        "family": "lt_ouroumov",
        "title_en": "Lt. Ouroumov-compatible Viewer",
        "title_id": "Viewer kompatibel Lt. Ouroumov",
        "use_en": "Optional exact-match viewer; a compatible classic viewer remains the fallback.",
        "use_id": "Viewer exact-match opsional; viewer klasik yang kompatibel tetap menjadi fallback.",
        "required": False,
    },
)

_PLUS_LEGACY_PROJECT_KEYS = frozenset({
    "globalRequirements",
    "isFadingOut",
    "isPointerCursor",
    "mdObjects",
    "objectDesignGroups",
    "objectMap",
    "pointTypeMap",
    "rowDesignGroups",
    "soundEffects",
})


def _classic_project_family(app: Mapping) -> str:
    """Split unversioned classic project data by Plus-only schema evidence."""
    return (
        "icc_plus_legacy"
        if _PLUS_LEGACY_PROJECT_KEYS.intersection(app.keys())
        else "icc_original"
    )


def _classic_viewer_family(meta: Mapping) -> str:
    """Split historical ``icc_legacy`` metadata without rewriting manifests."""
    viewer_type = str(meta.get("viewer_type", "") or "").strip().lower()
    if viewer_type in {"icc_original", "icc_plus_legacy"}:
        return viewer_type
    descriptor = " ".join((
        str(meta.get("name", "") or ""),
        str(meta.get("zip_filename", "") or ""),
        str(meta.get("source_descriptor", "") or ""),
    )).lower().replace("_", " ")
    if "[original]" in descriptor or re.search(r"\bviewer[ .-]*1[ .-]*8\b", descriptor):
        return "icc_original"
    if (
        "new viewer" in descriptor
        or "new.viewer" in descriptor
        or "creator plus" in descriptor
        or viewer_type == "icc_plus"
    ):
        return "icc_plus_legacy"
    return "icc_original"

# ICC Plus marker — exists in ALL ICC Plus versions (v1.x, v2.x).
# The comment block in app.js right before the default state object.
_ICC_MARKER_RE = re.compile(
    r'(/\*!\s*Delete and replace this part[^*]*\*/'
    r'|//\s*Delete and replace this part[^\n]*\n)',
    re.DOTALL | re.IGNORECASE
)


def _safe_viewer_archive_name(value: object) -> str:
    """Return a registry-safe ZIP/RAR basename, or an empty string."""
    text = str(value or "").strip()
    if (
        not text
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or re.match(r"^[A-Za-z]:", text)
        or not text.lower().endswith((".zip", ".rar"))
    ):
        return ""
    return text


def _safe_viewer_relative_path(value: object, *, default: str = "") -> str:
    """Validate a path stored inside a viewer archive manifest."""
    text = str(value or default).strip()
    if not text:
        return ""
    try:
        return _safe_archive_rel_path(text)
    except ValueError:
        return ""


def _load_viewers_manifest() -> Dict[str, Dict]:
    """Load offline viewer registry. Returns {viewer_id: {...metadata}}."""
    try:
        if os.path.exists(_VIEWERS_MANIFEST):
            with open(_VIEWERS_MANIFEST, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cleaned: Dict[str, Dict] = {}
                for viewer_id, meta in data.items():
                    if not isinstance(viewer_id, str) or not isinstance(meta, dict):
                        continue
                    normalized = dict(meta)
                    defaults = {
                        "name": viewer_id,
                        "zip_filename": "",
                        "viewer_type": "custom",
                        "description": "",
                        "entry_point": "index.html",
                        "project_json_path": "",
                        "inner_archive": "",
                        "runtime_family": "",
                        "viewer_variant": "",
                    }
                    for key, fallback in defaults.items():
                        if not isinstance(normalized.get(key), str):
                            normalized[key] = fallback
                    zip_filename = normalized.get("zip_filename", "")
                    if zip_filename and not _safe_viewer_archive_name(zip_filename):
                        logger.warning("Ignoring offline viewer with unsafe archive path: %s", viewer_id)
                        continue
                    entry_point = _safe_viewer_relative_path(
                        normalized.get("entry_point"), default="index.html"
                    )
                    if not entry_point:
                        logger.warning("Ignoring offline viewer with unsafe entry point: %s", viewer_id)
                        continue
                    project_json_path = normalized.get("project_json_path", "")
                    if project_json_path:
                        project_json_path = _safe_viewer_relative_path(project_json_path)
                        if not project_json_path:
                            logger.warning(
                                "Ignoring offline viewer with unsafe project path: %s", viewer_id
                            )
                            continue
                    normalized["entry_point"] = entry_point
                    normalized["project_json_path"] = project_json_path
                    inner_archive = normalized.get("inner_archive", "")
                    if inner_archive:
                        inner_archive = _safe_viewer_relative_path(inner_archive)
                        if not inner_archive or not inner_archive.lower().endswith(".zip"):
                            logger.warning(
                                "Ignoring offline viewer with unsafe nested archive: %s", viewer_id
                            )
                            continue
                    normalized["inner_archive"] = inner_archive
                    cleaned[viewer_id] = normalized
                return cleaned
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _load_viewers_manifest (line 2415): %s", _ignored_exc)
    return {}


def _save_viewers_manifest(manifest: Dict[str, Dict]) -> None:
    """Atomically save offline viewer registry."""
    try:
        os.makedirs(_VIEWERS_DIR, exist_ok=True)
        atomic_write_text(
            _VIEWERS_MANIFEST,
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
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
    Register an offline viewer ZIP, RAR, or unpacked viewer folder.
    Folders are packaged into the private viewer store without changing the
    source directory. Copies the resulting archive to _VIEWERS_DIR and saves
    metadata to the manifest.
    Returns viewer_id or None on failure.
    """
    import shutil, zipfile as _zf

    if not os.path.exists(zip_path):
        logger.error(f"Offline viewer file not found: {zip_path}")
        return None

    source_path = os.path.abspath(zip_path)
    source_name = os.path.basename(os.path.normpath(source_path))
    if os.path.isdir(source_path):
        os.makedirs(_VIEWERS_DIR, exist_ok=True)
        viewer_id = source_name
        packed_path = os.path.join(_VIEWERS_DIR, f"{viewer_id}.zip")
        part_path = packed_path + f".{os.getpid()}.{threading.get_ident()}.part"
        member_count = 0
        total_size = 0
        try:
            with _zf.ZipFile(part_path, "w", compression=_zf.ZIP_DEFLATED) as archive:
                for root, dirs, files in os.walk(source_path):
                    dirs.sort()
                    files.sort()
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(full_path, source_path).replace("\\", "/")
                        _safe_archive_rel_path(rel_path)
                        file_size = os.path.getsize(full_path)
                        member_count += 1
                        total_size += file_size
                        if member_count > 10000:
                            raise ValueError("Viewer folder contains too many files")
                        if file_size > 1024 * 1024 * 1024:
                            raise ValueError(f"Viewer file is too large: {rel_path}")
                        if total_size > 4 * 1024 * 1024 * 1024:
                            raise ValueError("Viewer folder is larger than 4 GiB")
                        archive.write(full_path, rel_path)
            os.replace(part_path, packed_path)
            zip_path = packed_path
            logger.info("Packed unpacked viewer folder: %s", source_path)
        except (OSError, ValueError, _zf.BadZipFile) as exc:
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except OSError:
                pass
            logger.error("Cannot package offline viewer folder %s: %s", source_path, exc)
            return None

    is_rar = zip_path.lower().endswith(".rar")

    entry_point = _safe_viewer_relative_path(entry_point, default="index.html")
    raw_project_json_path = str(project_json_path or "").strip()
    project_json_path = (
        _safe_viewer_relative_path(raw_project_json_path)
        if raw_project_json_path
        else ""
    )
    if not entry_point or (raw_project_json_path and not project_json_path):
        logger.error("Unsafe offline viewer entry/project path")
        return None

    # Validate archive
    try:
        if is_rar:
            import rarfile as _rf
            # Use a context manager so the RAR handle is
            # closed even if namelist() raises. Previously close() ran only on
            # the success path, leaking the handle on any read error (the ZIP
            # branch below already did this correctly).
            with _rf.RarFile(zip_path) as arc:
                names = arc.namelist()
                if len(names) > 10000:
                    raise ValueError("Viewer archive contains too many members")
                for member in names:
                    if not str(member).endswith(("/", "\\")):
                        _safe_archive_rel_path(member)
        else:
            if not _zf.is_zipfile(zip_path):
                logger.error(f"Not a valid ZIP: {zip_path}")
                return None
            validate_zip_archive(
                zip_path,
                max_members=10000,
                max_member_size=1024 * 1024 * 1024,
                max_total_size=4 * 1024 * 1024 * 1024,
                max_ratio=250.0,
            )
            with _zf.ZipFile(zip_path) as arc:
                names = arc.namelist()
    except Exception as e:
        logger.error(f"Cannot open archive {zip_path}: {e}")
        return None

    # Auto-detect entry point
    html_files = [n for n in names if n.endswith(".html") and n.count("/") <= 1]
    if not entry_point and html_files:
        entry_point = os.path.basename(html_files[0])

    # ICC Remix distributes a full editor package whose actual local viewer is
    # nested as viewer-template.zip.  Record that contract so injection never
    # mistakes the editor's own index.html for the playable viewer.
    inner_archive = ""
    if not is_rar:
        nested_viewers = [
            str(member).replace("\\", "/")
            for member in names
            if str(member).replace("\\", "/").lower().endswith("viewer-template.zip")
        ]
        if nested_viewers:
            inner_archive = min(nested_viewers, key=len)

    # Auto-detect viewer type from filenames in archive
    js_files = " ".join(names)
    detected_type = viewer_type
    if inner_archive:
        # A nested viewer-template.zip is an unambiguous ICC Remix package,
        # even when an older GUI submitted its historical `icc_plus` default.
        detected_type = "icc_remix"
    elif detected_type == "custom":
        for vtype, hints in VIEWER_TYPE_HINTS.items():
            if any(h.lower() in js_files.lower() for h in hints):
                detected_type = vtype
                break
    # Split the historical broad icc_plus tag when the archive itself proves
    # which incompatible loading contract it uses.  Keep the broad value for
    # old/custom archives whose contents are inconclusive.
    normalized_names = {
        str(member).replace("\\", "/").lower().lstrip("./") for member in names
    }
    has_plus2_local_bundle = (
        any(name.endswith("js/app.js") for name in normalized_names)
        and any(name.endswith("js/polyfills.js") for name in normalized_names)
    )
    has_plus2_online_loader = any(
        name.endswith("core.js") for name in normalized_names
    )
    runtime_family = ""
    if inner_archive:
        runtime_family = "icc_remix"
    elif has_plus2_local_bundle:
        runtime_family = "icc_plus2"
    elif any("app.d3103a3b" in name for name in normalized_names):
        runtime_family = "lt_ouroumov"
    elif any(
        marker in " ".join(normalized_names)
        for marker in ("app.c533aa25", "chunk-vendors.59af3576")
    ):
        runtime_family = _classic_viewer_family({
            "name": name or source_name,
            "zip_filename": source_name,
            "source_descriptor": source_path,
            "viewer_type": detected_type,
        })
    # New registrations store the precise classic family. Existing manifests
    # using icc_legacy/icc_plus are migrated lazily by _archive_runtime_family.
    if runtime_family in {"icc_original", "icc_plus_legacy"}:
        detected_type = runtime_family
    if detected_type == "icc_plus" and has_plus2_local_bundle:
        detected_type = "icc_plus2"
    viewer_variant = ""
    if runtime_family == "icc_plus2" or detected_type == "icc_plus2":
        if has_plus2_local_bundle:
            viewer_variant = "offline"
        elif has_plus2_online_loader:
            viewer_variant = "online"

    os.makedirs(_VIEWERS_DIR, exist_ok=True)
    viewer_id = os.path.splitext(os.path.basename(zip_path))[0]
    dest      = os.path.join(_VIEWERS_DIR, os.path.basename(zip_path))
    with _VIEWERS_LOCK:
        with interprocess_file_lock(_VIEWERS_MANIFEST):
            if os.path.abspath(dest) != os.path.abspath(zip_path):
                part = dest + f".{os.getpid()}.{threading.get_ident()}.part"
                try:
                    shutil.copy2(zip_path, part)
                    os.replace(part, dest)
                finally:
                    try:
                        if os.path.exists(part):
                            os.remove(part)
                    except OSError as exc:
                        logger.debug(f"Could not remove partial viewer archive {part}: {exc}")

            manifest = _load_viewers_manifest()
            manifest[viewer_id] = {
                "name":              name or viewer_id,
                "zip_filename":      os.path.basename(zip_path),
                "viewer_type":       detected_type,
                "description":       description,
                "entry_point":       entry_point or "index.html",
                "project_json_path": project_json_path,
                "inner_archive":     inner_archive,
                "runtime_family":    runtime_family,
                "viewer_variant":    viewer_variant,
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
    script_dir = _public_script_dir()
    os.makedirs(_VIEWERS_DIR, exist_ok=True)

    # Handle ICCPlus-main.zip — extract LocalViewer and latest Viewer as separate ZIPs
    iccplus_main = os.path.join(script_dir, "ICCPlus-main.zip")
    if os.path.exists(iccplus_main):
        _extract_iccplus_subviewers(iccplus_main)

    # Plain viewer ZIPs/RARs in the script directory
    bundled = [
        ("ICC_Plus_Viewer_v2_9_1_local.zip", "ICC Plus v2.9.1 (Local)",  "icc_plus2"),
        ("ICC_Remix.zip",                     "ICC Remix",                 "icc_remix"),
        ("ICCRemixLocal4.zip",                "ICC Remix Local v4",        "icc_remix"),
        ("Viewer_1_8.rar",                    "ICC Viewer 1.8",            "icc_original"),
        ("New_Viewer_1_18_9.zip",             "New Viewer 1.18.9",         "icc_plus_legacy"),
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
    Extract LocalViewer and latest Viewer from an ICC Plus source ZIP,
    package each as its own ZIP in _VIEWERS_DIR, and register them.
    LocalViewer is preferred for offline use (simpler, single JS file).

    The subviewer folders were previously located by the
    hardcoded prefix ``ICCPlus-main/``. GitHub names the root folder after the
    download ref, so a tagged source ZIP (e.g. ``ICCPlus-2.9.23/``) extracted
    to nothing and failed silently. Matching is now root-agnostic: the single
    top-level root directory is detected, and the subviewer folder is taken as
    its direct child ``<root>/<segment>/``. This deliberately ignores nested
    decoys such as ``<root>/Old/Viewer/`` that would otherwise collide.
    """
    import zipfile as _zf, io

    manifest = _load_viewers_manifest()

    sub_viewers = [
        # (folder_segment, dest_name, display_name, viewer_type, priority)
        ("LocalViewer", "ICCPlus_LocalViewer.zip",
         "ICC Plus LocalViewer (recommended)", "icc_plus", "high"),
        ("Viewer",      "ICCPlus_Viewer_latest.zip",
         "ICC Plus Viewer (latest)",  "icc_plus", "normal"),
    ]

    def _detect_root(names):
        """Return the common single top-level directory (with trailing slash),
        or '' if members live at the archive root / span multiple top dirs."""
        tops = set()
        for n in names:
            head = n.split("/", 1)
            tops.add(head[0] if len(head) > 1 else "")
        non_empty = {t for t in tops if t}
        if len(non_empty) == 1 and "" not in tops:
            return next(iter(non_empty)) + "/"
        return ""

    try:
        validate_zip_archive(
            iccplus_zip_path,
            max_members=10000,
            max_member_size=1024 * 1024 * 1024,
            max_total_size=4 * 1024 * 1024 * 1024,
            max_ratio=250.0,
        )
        with _zf.ZipFile(iccplus_zip_path) as main_zf:
            all_names = [n for n in main_zf.namelist() if not n.endswith('/')]
            root = _detect_root(all_names)

            for segment, dest_fname, display, vtype, priority in sub_viewers:
                vid = os.path.splitext(dest_fname)[0]
                if vid in manifest:
                    continue

                dest_path = os.path.join(_VIEWERS_DIR, dest_fname)

                # Exact direct-child match: <root>/<segment>/...  (root may be '')
                prefix = f"{root}{segment}/"
                matched = []
                for n in all_names:
                    if n.startswith(prefix):
                        rel = n[len(prefix):]
                        if rel:
                            matched.append((n, rel))

                if not matched:
                    continue

                # Re-package as flat ZIP (relative to the matched segment).
                buf = io.BytesIO()
                with _zf.ZipFile(buf, 'w', _zf.ZIP_DEFLATED) as out_zf:
                    for member, rel in matched:
                        data = main_zf.read(member)
                        out_zf.writestr(rel, data)

                atomic_write_bytes(dest_path, buf.getvalue())

                register_offline_viewer(
                    dest_path,
                    name=display,
                    viewer_type=vtype,
                )
                logger.info(f"Extracted ICC Plus subviewer: {dest_fname} ({len(matched)} files)")

    except Exception as e:
        logger.warning(f"Could not process ICC Plus source ZIP: {e}")



def unregister_offline_viewer(viewer_id: str, delete_zip: bool = False) -> bool:
    """Remove a viewer from the registry, optionally delete the ZIP."""
    with _VIEWERS_LOCK:
        with interprocess_file_lock(_VIEWERS_MANIFEST):
            manifest = _load_viewers_manifest()
            if viewer_id not in manifest:
                return False
            entry = manifest.pop(viewer_id)
            if delete_zip:
                zip_filename = _safe_viewer_archive_name(entry.get("zip_filename", ""))
                zip_path = os.path.join(_VIEWERS_DIR, zip_filename) if zip_filename else ""
                try:
                    if zip_path and os.path.exists(zip_path):
                        os.remove(zip_path)
                    elif not zip_filename:
                        logger.warning("Refusing to delete unsafe offline viewer archive path")
                except Exception as e:
                    logger.warning(f"Could not delete viewer ZIP: {e}")
            _save_viewers_manifest(manifest)
    logger.info(f"Offline viewer removed: {viewer_id!r}")
    return True


def detect_project_runtime_family(project_data: object) -> str:
    """Infer the compatible viewer family from project data alone.

    ICC Plus 2 exports an explicit 2.x version. Unversioned ICC Plus projects
    expose fields that Viewer 1.8 did not support; projects without that
    evidence stay on the original viewer. Remix and Lt. Ouroumov require
    HTML/runtime evidence because their JSON is not a unique format.
    """
    try:
        value = json.loads(project_data) if isinstance(project_data, str) else project_data
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(value, Mapping):
        return ""
    app = value.get("app") if isinstance(value.get("app"), Mapping) else value
    if not (
        isinstance(app.get("rows"), list)
        and isinstance(app.get("pointTypes"), list)
        and isinstance(app.get("styling"), Mapping)
    ):
        return ""
    version = str(value.get("version") or app.get("version") or "").strip()
    return "icc_plus2" if version.startswith("2.") else _classic_project_family(app)


def _detect_html_runtime_family(html_text: str) -> tuple[str, str]:
    html_lower = html_text.lower()
    if any(marker in html_lower for marker in (
        "{{icc_project_data_script}}", "__icc_remix__", "app.offline.js", "iccremix"
    )):
        return "icc_remix", "source HTML contains an ICC Remix runtime marker"
    if any(marker in html_lower for marker in (
        "core.js", "vite_is_modern_browser", "app.b6d7tc9y", "app_bugw6rfa"
    )):
        return "icc_plus2", "source HTML contains an ICC Plus 2 runtime marker"
    if "app.d3103a3b" in html_lower:
        return "lt_ouroumov", "source HTML contains the Lt. Ouroumov bundle marker"
    if any(marker in html_lower for marker in (
        "app.c533aa25", "chunk-vendors.59af3576"
    )):
        return "icc_classic", "source HTML contains a classic ICC bundle marker"
    return "", ""


def _archive_runtime_family(meta: Mapping) -> str:
    explicit = str(meta.get("runtime_family", "") or "").strip().lower()
    aliases = {"icc_plus_2": "icc_plus2", "icc": "icc_original"}
    if explicit == "icc_legacy":
        return _classic_viewer_family(meta)
    if explicit:
        return aliases.get(explicit, explicit)
    viewer_type = str(meta.get("viewer_type", "custom") or "custom").strip().lower()
    if viewer_type in {
        "icc_plus2",
        "icc_original",
        "icc_plus_legacy",
        "icc_remix",
        "lt_ouroumov",
    }:
        return viewer_type
    if viewer_type == "icc":
        return "icc_original"

    archive_name = _safe_viewer_archive_name(meta.get("zip_filename", ""))
    archive_path = os.path.join(_VIEWERS_DIR, archive_name) if archive_name else ""
    if archive_path.lower().endswith(".zip") and os.path.isfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names = {name.replace("\\", "/").lower().lstrip("./") for name in archive.namelist()}
            if any(name.endswith("viewer-template.zip") for name in names):
                return "icc_remix"
            if (
                any(name.endswith("js/app.js") for name in names)
                and any(name.endswith("js/polyfills.js") for name in names)
            ):
                return "icc_plus2"
            if any("app.d3103a3b" in name for name in names):
                return "lt_ouroumov"
            if any("app.c533aa25" in name for name in names):
                return _classic_viewer_family(meta)
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
    descriptor = f"{archive_name} {meta.get('name', '')}".lower()
    if "remix" in descriptor:
        return "icc_remix"
    if re.search(r"(?:plus[ ._-]*2|v2[._-])", descriptor):
        return "icc_plus2"
    if any(term in descriptor for term in ("legacy", "viewer 1.8", "new viewer")):
        return _classic_viewer_family(meta)
    return _classic_viewer_family(meta) if viewer_type == "icc_plus" else viewer_type


def _archive_viewer_variant(meta: Mapping) -> str:
    """Return offline/online/unknown for a registered viewer archive."""
    explicit = str(meta.get("viewer_variant", "") or "").strip().lower()
    if explicit in {"offline", "online"}:
        return explicit
    descriptor = f"{meta.get('name', '')} {meta.get('zip_filename', '')}".lower()
    if any(token in descriptor for token in ("local", "offline", "standalone")):
        return "offline"
    if "online" in descriptor:
        return "online"
    archive_name = _safe_viewer_archive_name(meta.get("zip_filename", ""))
    archive_path = os.path.join(_VIEWERS_DIR, archive_name) if archive_name else ""
    if archive_path.lower().endswith(".zip") and os.path.isfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names = {
                    name.replace("\\", "/").lower().lstrip("./")
                    for name in archive.namelist()
                }
            if (
                any(name.endswith("js/app.js") for name in names)
                and any(name.endswith("js/polyfills.js") for name in names)
            ):
                return "offline"
            if any(name.endswith("core.js") for name in names):
                return "online"
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
    return ""


def select_offline_iccplus_asset(assets: object) -> Optional[dict]:
    """Pick only a downloadable ICC Plus offline/local release asset.

    The online bundle is intentionally never used as a fallback because it
    cannot satisfy the downloader's direct-file offline viewer contract.
    """
    if not isinstance(assets, list):
        return None
    candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "") or "")
        url = str(asset.get("browser_download_url", "") or "")
        lower = name.lower()
        if (
            url
            and lower.endswith((".zip", ".rar"))
            and any(token in lower for token in ("local", "offline", "standalone"))
            and "online" not in lower
        ):
            candidates.append(asset)
    if not candidates:
        return None
    return max(candidates, key=lambda item: _viewer_quality({
        "name": item.get("name", ""),
        "zip_filename": item.get("name", ""),
        "registered_at": "",
    }))


def _viewer_quality(meta: Mapping) -> tuple[int, tuple[int, ...], str]:
    descriptor = f"{meta.get('name', '')} {meta.get('zip_filename', '')}".lower()
    versions = tuple(int(part) for part in re.findall(r"\d+", descriptor)[:4])
    return (
        5 if str(meta.get("zip_filename", "")).lower().endswith(".zip") else 0,
        versions,
        str(meta.get("registered_at", "")),
    )


def get_viewer_recommendations() -> list[dict]:
    """Return family coverage for the themed Settings viewer checklist."""
    manifest = _load_viewers_manifest()
    result: list[dict] = []
    for guidance in VIEWER_FAMILY_GUIDANCE:
        family = str(guidance["family"])
        matches = [
            (viewer_id, meta)
            for viewer_id, meta in manifest.items()
            if _archive_runtime_family(meta) == family
            and not (family == "icc_plus2" and _archive_viewer_variant(meta) == "online")
        ]
        selected = max(matches, key=lambda item: _viewer_quality(item[1])) if matches else None
        result.append({
            **guidance,
            "available": selected is not None,
            "viewer_id": selected[0] if selected else "",
            "viewer_name": str(selected[1].get("name", selected[0])) if selected else "",
        })
    return result


def get_viewer_for_site(
    html_text: str,
    mode: str = "auto",
    *,
    project_data: object = None,
    preferred_viewer_id: str = "auto",
) -> Optional[Dict]:
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

    requested_id = str(preferred_viewer_id or "auto").strip()
    if requested_id.casefold() != "auto" and requested_id in manifest:
        requested_meta = manifest[requested_id]
        if (
            _archive_runtime_family(requested_meta) == "icc_plus2"
            and _archive_viewer_variant(requested_meta) == "online"
        ):
            logger.warning(
                "Rejected ICC Plus 2 online viewer override: %s; an offline/local bundle is required",
                requested_id,
            )
            return None
        return {
            "id": requested_id,
            **requested_meta,
            "detected_family": _archive_runtime_family(requested_meta),
            "selection_reason": f"Manual viewer override: {requested_id}",
        }

    detected_site_type, selection_reason = _detect_html_runtime_family(html_text)
    project_family = detect_project_runtime_family(project_data)
    if detected_site_type == "icc_classic":
        detected_site_type = (
            project_family
            if project_family in {"icc_original", "icc_plus_legacy"}
            else "icc_original"
        )
        selection_reason = (
            "classic ICC bundle plus project schema selects " + detected_site_type
        )
    elif not detected_site_type:
        detected_site_type = project_family
        if detected_site_type == "icc_plus2":
            try:
                parsed = json.loads(project_data) if isinstance(project_data, str) else project_data
                app = parsed.get("app") if isinstance(parsed, Mapping) and isinstance(parsed.get("app"), Mapping) else parsed
                version = str(parsed.get("version") or app.get("version") or "").strip()
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                version = "2.x"
            selection_reason = f"project.json version {version} requires ICC Plus 2"
        elif detected_site_type == "icc_plus_legacy":
            selection_reason = "unversioned ICC Plus project.json uses the Plus legacy viewer"
        elif detected_site_type == "icc_original":
            selection_reason = "classic ICC project.json uses the original Viewer 1.8 contract"

    scored: List[tuple] = []
    for vid, meta in manifest.items():
        score = 0
        vtype = meta.get("viewer_type", "custom")
        runtime_family = _archive_runtime_family(meta)
        if runtime_family == "icc_plus2" and _archive_viewer_variant(meta) == "online":
            continue
        hints = VIEWER_TYPE_HINTS.get(vtype, [])
        hint_matches = 0
        for hint in hints:
            if hint.lower() in html_lower:
                hint_matches += 1
        score += hint_matches
        if detected_site_type:
            if runtime_family == detected_site_type:
                score += 100
            elif (
                vtype == "icc_plus"
                and not meta.get("runtime_family")
                and detected_site_type in {
                    "icc_plus2",
                    "icc_plus_legacy",
                    "icc_original",
                }
            ):
                # Backward compatibility for manifests created before the
                # family split. A precise template always outranks this alias.
                score += 25
            elif detected_site_type == "lt_ouroumov" and runtime_family in {
                "icc_original",
                "icc_plus_legacy",
            }:
                # Lt. Ouroumov retains the legacy embedded-project contract,
                # so a legacy viewer is a functional fallback when no exact
                # template has been registered.
                score += 35
        if preferred_type and vtype == preferred_type:
            score += 10
        # LocalViewer is designed for offline — prefer it
        name_lower = meta.get("name", "").lower()
        # A friendly name is not compatibility evidence. Apply this only as a
        # tie-breaker once HTML hints, mode, or Remix detection actually match.
        if score > 0 and ("localviewer" in name_lower or "local" in name_lower):
            score += 5
        scored.append((score, _viewer_quality(meta), vid, meta))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _quality, best_id, best_meta = scored[0]
    if best_score == 0:
        return None
    return {
        "id": best_id,
        **best_meta,
        "detected_family": detected_site_type or _archive_runtime_family(best_meta),
        "selection_reason": selection_reason or "matched registered viewer hints and output mode",
    }


__all__ = [
    "_VIEWERS_DIR", "_VIEWERS_MANIFEST", "VIEWER_TYPE_HINTS", "_ICC_MARKER_RE",
    "_load_viewers_manifest", "_save_viewers_manifest",
    "register_offline_viewer", "_auto_register_bundled_viewers",
    "_extract_iccplus_subviewers", "unregister_offline_viewer",
    "get_viewer_for_site",
    "detect_project_runtime_family", "get_viewer_recommendations",
    "select_offline_iccplus_asset",
]
