"""Offline viewer registry/archive registration helpers.

Phase 31 moves registry and archive import logic out of legacy.py while keeping
the old manifest format and bundled-viewer discovery behavior.
"""

from __future__ import annotations

import io
import json
import os
import re
from typing import Dict, List, Optional

from ...logging_setup import logger
from ...core.atomic_io import atomic_write_bytes

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
                    }
                    for key, fallback in defaults.items():
                        if not isinstance(normalized.get(key), str):
                            normalized[key] = fallback
                    cleaned[viewer_id] = normalized
                return cleaned
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _load_viewers_manifest (line 2415): %s", _ignored_exc)
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
            # Use a context manager so the RAR handle is
            # closed even if namelist() raises. Previously close() ran only on
            # the success path, leaking the handle on any read error (the ZIP
            # branch below already did this correctly).
            with _rf.RarFile(zip_path) as arc:
                names = arc.namelist()
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
    script_dir = _public_script_dir()
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
        hint_matches = 0
        for hint in hints:
            if hint.lower() in html_lower:
                hint_matches += 1
        score += hint_matches
        # Strong bonus for ICC Remix sites
        if is_remix and vtype == "icc_remix":
            score += 20
        if preferred_type and vtype == preferred_type:
            score += 10
        # LocalViewer is designed for offline — prefer it
        name_lower = meta.get("name", "").lower()
        # A friendly name is not compatibility evidence. Apply this only as a
        # tie-breaker once HTML hints, mode, or Remix detection actually match.
        if score > 0 and ("localviewer" in name_lower or "local" in name_lower):
            score += 5
        scored.append((score, vid, meta))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_id, best_meta = scored[0]
    if best_score == 0:
        return None
    return {"id": best_id, **best_meta}


__all__ = [
    "_VIEWERS_DIR", "_VIEWERS_MANIFEST", "VIEWER_TYPE_HINTS", "_ICC_MARKER_RE",
    "_load_viewers_manifest", "_save_viewers_manifest",
    "register_offline_viewer", "_auto_register_bundled_viewers",
    "_extract_iccplus_subviewers", "unregister_offline_viewer",
    "get_viewer_for_site",
]
