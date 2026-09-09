"""Detect and modernize downloaded CYOA sites for direct ``file://`` use.

The normal downloader historically treated every ICC-derived project the same.
That is safe for the original/Plus 1 viewers, whose project lives at a stable
marker in the JavaScript bundle, but not for ICC Plus 2 or ICC Remix.  This
module keeps detection, template selection, runtime replacement, and local
library conversion explicit so custom site files are never discarded.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from filecmp import cmp as files_are_equal
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ...core.atomic_io import atomic_write_text
from ...core.paths import _safe_archive_rel_path
from ...logging_setup import logger
from ...project.parse import extract_balanced_brace_block
from . import registry as viewer_registry
from .registry import _ICC_MARKER_RE


class SiteFamily(str, Enum):
    """Viewer families whose offline data-loading contracts differ."""

    ICC_LEGACY = "icc_legacy"
    ICC_PLUS_2 = "icc_plus_2"
    ICC_REMIX = "icc_remix"
    LT_OUROUMOV = "lt_ouroumov"
    CUSTOM_HTML = "custom_html"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SiteProfile:
    family: SiteFamily
    strategy: str
    site_dir: Path
    index_path: Path | None
    project_path: Path | None
    marker_script: Path | None
    confidence: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ViewerTemplate:
    family: SiteFamily
    archive_path: Path
    inner_archive: str = ""


@dataclass(frozen=True)
class ModernizeResult:
    source: Path
    destination: Path
    family: SiteFamily
    strategy: str
    status: str
    message: str = ""
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectionReport:
    source: Path
    destination: Path
    total_items: int
    modernized: int
    copied: int
    failed: int
    site_results: tuple[ModernizeResult, ...] = field(default_factory=tuple)


_RUNTIME_FILE_NAMES = {"app", "app.js", "polyfills.js"}
_ORIGINAL_SITE_DIR = "__original_site__"
_PLUS2_REQUIRED_HEAD = (
    '<link rel="stylesheet" href="./css/loading.css">',
    '<link id="theme-light" rel="stylesheet" href="./css/smui.css" media="(prefers-color-scheme: light)">',
    '<link id="theme-dark" rel="stylesheet" href="./css/smui-dark.css" media="screen and (prefers-color-scheme: dark)">',
    '<link rel="stylesheet" href="./css/roboto.css">',
    '<link rel="stylesheet" href="./css/bootstrap.min.css">',
    '<link rel="stylesheet" href="./css/material-icons.css">',
    '<script src="./js/polyfills.js"></script>',
    '<link href="./js/app.js" rel="preload" as="script">',
)
_REMIX_REQUIRED_HEAD = (
    '<link rel="stylesheet" href="./css/loading.css">',
    '<link rel="stylesheet" href="./css/smui.css" media="(prefers-color-scheme: light)" id="theme-light">',
    '<link rel="stylesheet" href="./css/smui-dark.css" media="screen and (prefers-color-scheme: dark)" id="theme-dark">',
    '<link rel="stylesheet" href="./css/roboto.css">',
    '<link rel="stylesheet" href="./css/bootstrap.min.css">',
    '<link rel="stylesheet" href="./css/material-icons.css">',
    '<link rel="stylesheet" href="./assets/iccplus_viewer.css">',
    "<script>window.__ICCPLUS_PLAYABLE_SITE__ = true;</script>",
    '<script src="./js/polyfills.js"></script>',
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _project_object(project_path: Path | None) -> dict:
    if project_path is None:
        return {}
    try:
        value = json.loads(_read_text(project_path))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _project_app(value: Mapping) -> Mapping:
    app = value.get("app")
    return app if isinstance(app, Mapping) else value


def _looks_like_icc_project(value: Mapping) -> bool:
    app = _project_app(value)
    return (
        isinstance(app.get("rows"), list)
        and isinstance(app.get("pointTypes"), list)
        and isinstance(app.get("styling"), Mapping)
    )


def _direct_project_path(site_dir: Path) -> Path | None:
    for name in ("project.json", "project_original.json"):
        candidate = site_dir / name
        if candidate.is_file():
            return candidate
    return None


def _candidate_runtime_files(site_dir: Path) -> Iterable[Path]:
    preferred_dirs = (site_dir / "js", site_dir / "assets", site_dir)
    seen: set[Path] = set()
    for directory in preferred_dirs:
        if not directory.is_dir():
            continue
        try:
            entries = (
                directory.rglob("*")
                if directory.name in {"js", "assets"}
                else directory.iterdir()
            )
            for path in entries:
                if not path.is_file() or path in seen:
                    continue
                lower = path.name.lower()
                is_bundle_candidate = (
                    lower == "app"
                    or lower.startswith(("app.", "app_", "app-"))
                    or lower in {"app.js", "index.js", "index.mjs", "polyfills.js"}
                )
                if is_bundle_candidate and (
                    lower == "app" or path.suffix.lower() in {".js", ".mjs"}
                ):
                    seen.add(path)
                    yield path
        except OSError:
            continue


def _find_marker_script(site_dir: Path) -> Path | None:
    checked: set[Path] = set()
    for path in _candidate_runtime_files(site_dir):
        checked.add(path)
        try:
            if _ICC_MARKER_RE.search(_read_text(path)):
                return path
        except OSError:
            continue
    # Some publishers rename the application bundle completely.  Limit the
    # compatibility fallback to the conventional js/ directory so unrelated
    # application trees (for example SvelteKit assets) are not exhaustively
    # scanned.
    js_dir = site_dir / "js"
    if js_dir.is_dir():
        for path in js_dir.rglob("*.js"):
            if path in checked:
                continue
            try:
                if _ICC_MARKER_RE.search(_read_text(path)):
                    return path
            except OSError:
                continue
    return None


def _find_referenced_marker_script(site_dir: Path, html: str) -> Path | None:
    for match in re.finditer(
        r"<script\b[^>]*\bsrc\s*=\s*([\"'])([^\"']+)\1",
        html,
        re.IGNORECASE,
    ):
        reference = match.group(2).strip()
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            continue
        relative = unquote(parsed.path).replace("\\", "/").lstrip("./")
        if not relative:
            continue
        candidate = (site_dir / relative).resolve()
        try:
            candidate.relative_to(site_dir)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            if _ICC_MARKER_RE.search(_read_text(candidate)):
                return candidate
        except OSError:
            continue
    return None


def analyze_site(site_dir: os.PathLike[str] | str) -> SiteProfile:
    """Classify one site root using JSON schema plus runtime evidence."""
    root = Path(site_dir).resolve()
    index_path = root / "index.html"
    if not index_path.is_file():
        return SiteProfile(
            SiteFamily.UNKNOWN,
            "copy_only",
            root,
            None,
            _direct_project_path(root),
            None,
            0,
            ("index.html missing",),
        )

    project_path = _direct_project_path(root)
    project = _project_object(project_path)
    app = _project_app(project)
    is_icc = _looks_like_icc_project(project)
    version = str(project.get("version") or app.get("version") or "").strip()
    html = _read_text(index_path)
    html_lower = html.lower()
    names = {path.name.lower() for path in _candidate_runtime_files(root)}
    reasons: list[str] = []
    has_app_mount = bool(
        re.search(
            r"\bid\s*=\s*([\"'])app\1",
            html,
            flags=re.IGNORECASE,
        )
    )
    has_sveltekit_runtime = (
        "_app/immutable/" in html_lower
        and ("data-sveltekit" in html_lower or "__sveltekit" in html_lower)
    )
    plus2_schema_keys = {
        "variables",
        "rowDesignGroups",
        "objectDesignGroups",
        "globalRequirements",
        "soundEffects",
    }
    is_unversioned_svelte_plus2 = (
        is_icc
        and has_sveltekit_runtime
        and len(plus2_schema_keys.intersection(app.keys())) >= 2
    )

    is_remix = (
        "{{icc_project_data_script}}" in html_lower
        or "__icc_remix__" in html_lower
        or "__iccplus_playable_site__" in html_lower
        or (
            (root / "app").is_file()
            and (root / "assets" / "iccplus_viewer.css").is_file()
        )
    )
    if is_remix:
        reasons.append("ICC Remix template/runtime marker")
        has_local_remix_runtime = (
            "__icc_offline_data__" in html_lower
            or (
                (root / "app").is_file()
                and (root / "assets" / "iccplus_viewer.css").is_file()
            )
        )
        return SiteProfile(
            SiteFamily.ICC_REMIX,
            "patch_existing"
            if "{{icc_project_data_script}}" in html_lower or has_local_remix_runtime
            else "replace_viewer",
            root,
            index_path,
            project_path,
            None,
            95,
            tuple(reasons),
        )

    # A downloaded landing page can sit beside a perfectly valid ICC
    # project.json without being the viewer that consumes it.  Requiring a
    # viewer mount or bundle evidence prevents us from injecting a runtime
    # into publisher-authored menus, splash pages, and other custom HTML.
    if (
        is_icc
        and not is_unversioned_svelte_plus2
        and not has_app_mount
        and "core.js" not in html_lower
        and not names
    ):
        return SiteProfile(
            SiteFamily.CUSTOM_HTML,
            "copy_only",
            root,
            index_path,
            project_path,
            None,
            92,
            ("ICC data beside an HTML page without a viewer mount/runtime",),
        )

    local_plus2_names = {"app.js", "polyfills.js"} <= names
    is_plus2 = is_icc and (
        version.startswith("2.")
        or "core.js" in html_lower
        or "vite_is_modern_browser" in html_lower
        or local_plus2_names
        or is_unversioned_svelte_plus2
    )
    if is_plus2:
        # A marker in the application script loaded directly by index.html is
        # sufficient proof that the viewer is already local. Do not accept an
        # unrelated/obsolete marker elsewhere when index still boots core.js.
        marker_script = _find_referenced_marker_script(root, html)
        if marker_script is None and "core.js" not in html_lower and local_plus2_names:
            marker_script = _find_marker_script(root)
        if version:
            reasons.append(f"ICC project version {version}")
        if "core.js" in html_lower:
            reasons.append("online core.js loader")
        if marker_script is not None:
            reasons.append("local bundle injection marker")
        if is_unversioned_svelte_plus2:
            reasons.append("unversioned ICC Plus 2 SvelteKit runtime/schema")
        return SiteProfile(
            SiteFamily.ICC_PLUS_2,
            "patch_existing" if marker_script is not None else "replace_viewer",
            root,
            index_path,
            project_path,
            marker_script,
            95,
            tuple(reasons),
        )

    marker_script = _find_marker_script(root)
    if marker_script is not None and is_icc:
        family = (
            SiteFamily.LT_OUROUMOV
            if marker_script.name.lower() == "app.d3103a3b.js"
            else SiteFamily.ICC_LEGACY
        )
        reasons.extend(("ICC project schema", "JavaScript injection marker"))
        return SiteProfile(
            family,
            "patch_existing",
            root,
            index_path,
            project_path,
            marker_script,
            98,
            tuple(reasons),
        )

    if not is_icc:
        reason = (
            "non-ICC HTML application"
            if project_path
            else "HTML site without ICC project"
        )
        return SiteProfile(
            SiteFamily.CUSTOM_HTML,
            "copy_only",
            root,
            index_path,
            project_path,
            None,
            90,
            (reason,),
        )

    return SiteProfile(
        SiteFamily.UNKNOWN,
        "copy_only",
        root,
        index_path,
        project_path,
        marker_script,
        45,
        ("ICC-shaped project with an unknown runtime",),
    )


def _archive_inner_viewer(path: Path) -> str:
    if path.suffix.lower() != ".zip":
        return ""
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [
                name
                for name in archive.namelist()
                if name.replace("\\", "/").lower().endswith("viewer-template.zip")
            ]
        return min(matches, key=len) if matches else ""
    except (OSError, zipfile.BadZipFile):
        return ""


def resolve_viewer_templates(
    collection_dir: os.PathLike[str] | str,
) -> dict[SiteFamily, ViewerTemplate]:
    """Resolve the newest supplied local templates without relying on exact names."""
    root = Path(collection_dir).resolve()
    if not root.is_dir():
        return {}
    candidates: dict[SiteFamily, list[ViewerTemplate]] = {}
    for archive_path in root.rglob("*"):
        if not archive_path.is_file() or archive_path.suffix.lower() not in {
            ".zip",
            ".rar",
        }:
            continue
        descriptor = str(archive_path.relative_to(root)).lower().replace("_", " ")
        family: SiteFamily | None = None
        inner_archive = ""
        if "remix" in descriptor:
            family = SiteFamily.ICC_REMIX
            inner_archive = _archive_inner_viewer(archive_path)
        elif (
            "plus 2" in descriptor or re.search(r"\bv2[. _-]", descriptor)
        ) and "local" in descriptor:
            family = SiteFamily.ICC_PLUS_2
        elif "lt. ouroumov" in descriptor or "ltouroumov" in descriptor:
            family = SiteFamily.LT_OUROUMOV
        elif "interactive cyoa creator" in descriptor or "viewer 1.8" in descriptor:
            family = SiteFamily.ICC_LEGACY
        if family is not None:
            candidates.setdefault(family, []).append(
                ViewerTemplate(family, archive_path, inner_archive)
            )

    resolved: dict[SiteFamily, ViewerTemplate] = {}
    for family, matches in candidates.items():
        # Prefer nested Remix viewer templates and then newest mtime/name.
        resolved[family] = max(
            matches,
            key=lambda item: (
                bool(item.inner_archive),
                item.archive_path.stat().st_mtime,
                item.archive_path.name.lower(),
            ),
        )
    return resolved


def resolve_registered_viewer_templates() -> dict[SiteFamily, ViewerTemplate]:
    """Expose registered GUI/CLI viewer archives to in-place website downloads."""
    family_names = {
        "icc_plus2": SiteFamily.ICC_PLUS_2,
        "icc_plus_2": SiteFamily.ICC_PLUS_2,
        "icc_remix": SiteFamily.ICC_REMIX,
        "icc_legacy": SiteFamily.ICC_LEGACY,
        "lt_ouroumov": SiteFamily.LT_OUROUMOV,
        "icc": SiteFamily.ICC_LEGACY,
    }
    candidates: dict[SiteFamily, list[ViewerTemplate]] = {}
    for metadata in viewer_registry._load_viewers_manifest().values():
        viewer_type = str(metadata.get("viewer_type", "") or "")
        runtime_family = str(metadata.get("runtime_family", "") or "")
        family = family_names.get(runtime_family) or family_names.get(viewer_type)
        archive_name = str(metadata.get("zip_filename", "") or "")
        if family is None and viewer_type == "icc_plus":
            descriptor = archive_name.lower()
            family = (
                SiteFamily.ICC_PLUS_2
                if "localviewer" in descriptor or re.search(r"v2[._-]", descriptor)
                else SiteFamily.ICC_LEGACY
            )
        if family is None or not archive_name:
            continue
        if (
            family is SiteFamily.ICC_PLUS_2
            and viewer_registry._archive_viewer_variant(metadata) != "offline"
        ):
            logger.warning(
                "Ignoring non-offline ICC Plus 2 replacement template: %s",
                archive_name,
            )
            continue
        archive_path = Path(viewer_registry._VIEWERS_DIR) / archive_name
        if not archive_path.is_file():
            continue
        candidates.setdefault(family, []).append(
            ViewerTemplate(
                family,
                archive_path,
                str(metadata.get("inner_archive", "") or ""),
            )
        )

    return {
        family: max(
            templates,
            key=lambda item: (
                item.archive_path.suffix.lower() == ".zip",
                item.archive_path.stat().st_mtime,
                item.archive_path.name.lower(),
            ),
        )
        for family, templates in candidates.items()
    }


def _zip_members(archive: zipfile.ZipFile) -> list[str]:
    names = [name for name in archive.namelist() if not name.endswith(("/", "\\"))]
    if len(names) > 10_000:
        raise ValueError("viewer archive contains too many files")
    for name in names:
        _safe_archive_rel_path(name)
    return names


def _extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    names = _zip_members(archive)
    roots = {name.replace("\\", "/").split("/", 1)[0] for name in names}
    strip_root = next(iter(roots)) + "/" if len(roots) == 1 else ""
    total = 0
    for name in names:
        info = archive.getinfo(name)
        if info.file_size > 1024 * 1024 * 1024:
            raise ValueError(f"viewer member too large: {name}")
        total += info.file_size
        if total > 4 * 1024 * 1024 * 1024:
            raise ValueError("viewer archive exceeds 4 GiB extraction budget")
        normalized = name.replace("\\", "/")
        relative = (
            normalized[len(strip_root) :]
            if strip_root and normalized.startswith(strip_root)
            else normalized
        )
        if not relative:
            continue
        safe_relative = _safe_archive_rel_path(relative)
        target = destination.joinpath(*safe_relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(name))


def _extract_template(template: ViewerTemplate, destination: Path) -> None:
    if template.archive_path.suffix.lower() != ".zip":
        raise ValueError(
            f"RAR replacement templates are not supported: {template.archive_path}"
        )
    with zipfile.ZipFile(template.archive_path) as outer:
        if template.inner_archive:
            _zip_members(outer)
            nested = outer.read(template.inner_archive)
            with zipfile.ZipFile(io.BytesIO(nested)) as viewer:
                _extract_zip(viewer, destination)
        else:
            _extract_zip(outer, destination)


def _validate_replacement_template(template_dir: Path, family: SiteFamily) -> None:
    """Validate the runtime contract before any in-place overlay occurs."""
    if family is SiteFamily.ICC_PLUS_2:
        app_path = template_dir / "js" / "app.js"
        if not app_path.is_file() or not _ICC_MARKER_RE.search(_read_text(app_path)):
            raise ValueError(
                "replacement ICC Plus 2 template has no js/app.js injection marker"
            )
    elif family is SiteFamily.ICC_REMIX and not (template_dir / "app").is_file():
        raise ValueError("replacement ICC Remix template has no local app runtime")


def _compact_project(
    project_text: str,
    *,
    export_title: str = "",
    export_favicon: str = "",
) -> str:
    value = json.loads(project_text)
    if not isinstance(value, dict):
        raise TypeError("project data must be a JSON object")
    if export_title:
        value = {**value, "exportSiteTitle": export_title}
    if export_favicon:
        value = {**value, "exportSiteFavicon": export_favicon}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def _inject_marked_script(script_path: Path, project_text: str) -> None:
    script = _read_text(script_path)
    marker = _ICC_MARKER_RE.search(script)
    if marker is None:
        raise ValueError(f"ICC injection marker not found in {script_path}")
    brace_index = script.find("{", marker.end())
    if brace_index < 0:
        raise ValueError(
            f"default project object not found after marker in {script_path}"
        )
    block = extract_balanced_brace_block(script, brace_index)
    if not block:
        raise ValueError(f"default project object is unbalanced in {script_path}")
    replacement = _compact_project(project_text)
    patched = script[:brace_index] + replacement + script[brace_index + len(block) :]
    atomic_write_text(str(script_path), patched)


def _backup_original(site_root: Path, target: Path) -> str | None:
    """Keep the original bytes before an intentional runtime overwrite."""
    if not target.is_file():
        return None
    relative = target.relative_to(site_root)
    if relative.parts and relative.parts[0] == _ORIGINAL_SITE_DIR:
        return None
    backup = site_root / _ORIGINAL_SITE_DIR / relative
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
    return backup.relative_to(site_root).as_posix()


def _inject_into_head(html: str, markup: str) -> str:
    match = re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE)
    if match:
        return html[: match.end()] + "\n" + markup + "\n" + html[match.end() :]
    return markup + "\n" + html


def _inject_before_body_end(html: str, markup: str) -> str:
    match = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    if match:
        return html[: match.start()] + markup + "\n" + html[match.start() :]
    return html + "\n" + markup


def _has_reference(html: str, relative_path: str) -> bool:
    target = relative_path.lower().lstrip("./")
    return target in html.lower().replace("\\", "/")


def _is_hashed_runtime_asset(name: str, extension: str) -> bool:
    """Recognize generated viewer bundles without matching publisher files.

    Names such as ``app.publisher-theme.css`` are common customization files
    and must survive viewer replacement. Generated ICC/Vue bundles use an
    opaque hash segment (at least eight characters and normally containing a
    digit), for example ``app.c533aa25.js`` or ``app.B6d7tc9y.js``.
    """
    return bool(
        re.fullmatch(
            rf"(?:app|chunk-vendors)[._-](?=[A-Za-z0-9_-]{{8,}}\.{extension}$)"
            rf"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\.{extension}",
            name,
            flags=re.IGNORECASE,
        )
    )


def _remove_incompatible_runtime_scripts(html: str, family: SiteFamily) -> str:
    script_tag = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*([\"'])([^\"']+)\1[^>]*>\s*</script\s*>",
        re.IGNORECASE,
    )

    def keep_or_remove(match: re.Match[str]) -> str:
        path = match.group(2).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
        name = path.rsplit("/", 1)[-1].lower()
        parent = (
            path.rstrip("/").rsplit("/", 2)[-2].lower()
            if "/" in path.rstrip("/")
            else ""
        )
        is_viewer_script_path = parent in {"", ".", "js"}
        remove = name == "core.js" and is_viewer_script_path
        if family is SiteFamily.ICC_PLUS_2:
            remove = remove or (
                is_viewer_script_path
                and name != "app.js"
                and (name == "app" or _is_hashed_runtime_asset(name, "js"))
            )
        elif family is SiteFamily.ICC_REMIX:
            is_remix_script_path = parent in {"", ".", "js", "assets"}
            remove = (
                remove
                or is_remix_script_path
                and (
                    name in {"app", "app.js", "app.offline.js"}
                    or _is_hashed_runtime_asset(name, "js")
                )
            )
        return "" if remove else match.group(0)

    html = script_tag.sub(keep_or_remove, html)
    if family is SiteFamily.ICC_PLUS_2:
        inline_script_tag = re.compile(
            r"<script\b(?P<attrs>(?:(?!\bsrc\s*=)[^>])*)>"
            r"(?P<body>.*?)</script\s*>",
            re.IGNORECASE | re.DOTALL,
        )

        def keep_or_remove_inline(match: re.Match[str]) -> str:
            body = match.group("body").lower()
            is_svelte_boot = (
                "_app/immutable/" in body
                and ("__sveltekit" in body or "import(" in body)
            )
            return "" if is_svelte_boot else match.group(0)

        html = inline_script_tag.sub(keep_or_remove_inline, html)
    if family not in {SiteFamily.ICC_PLUS_2, SiteFamily.ICC_REMIX}:
        return html

    link_tag = re.compile(
        r"<link\b[^>]*\bhref\s*=\s*([\"'])([^\"']+)\1[^>]*>", re.IGNORECASE
    )

    def keep_or_remove_link(match: re.Match[str]) -> str:
        path = match.group(2).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
        if (
            family is SiteFamily.ICC_PLUS_2
            and "_app/immutable/" in path.lower()
        ):
            return ""
        name = path.rsplit("/", 1)[-1].lower()
        parent = (
            path.rstrip("/").rsplit("/", 2)[-2].lower()
            if "/" in path.rstrip("/")
            else ""
        )
        is_old_runtime_css = (
            parent in {"", ".", "css"}
            and _is_hashed_runtime_asset(name, "css")
        )
        return "" if is_old_runtime_css else match.group(0)

    return link_tag.sub(keep_or_remove_link, html)


def _ensure_head_markup(html: str, snippets: Iterable[str]) -> str:
    missing: list[str] = []
    for snippet in snippets:
        paths = re.findall(r"(?:href|src)=[\"']([^\"']+)", snippet, flags=re.IGNORECASE)
        if paths and all(_has_reference(html, path) for path in paths):
            continue
        marker = "__ICCPLUS_PLAYABLE_SITE__"
        if not paths and marker in snippet and marker.lower() in html.lower():
            continue
        missing.append(snippet)
    return _inject_into_head(html, "\n".join(missing)) if missing else html


def _html_site_identity(html: str) -> tuple[str, str]:
    title_match = re.search(
        r"<title\b[^>]*>(.*?)</title\s*>", html, flags=re.IGNORECASE | re.DOTALL
    )
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    favicon = ""
    for link_match in re.finditer(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
        tag = link_match.group(0)
        if not re.search(
            r"\brel\s*=\s*([\"'])[^\"']*\bicon\b[^\"']*\1", tag, flags=re.IGNORECASE
        ):
            continue
        href_match = re.search(
            r"\bhref\s*=\s*([\"'])([^\"']+)\1", tag, flags=re.IGNORECASE
        )
        if href_match:
            favicon = href_match.group(2).strip()
            break
    return title, favicon


def _is_runtime_tag(tag: str) -> bool:
    reference = re.search(
        r"\b(?:href|src)\s*=\s*([\"'])([^\"']+)\1", tag, re.IGNORECASE
    )
    if reference is None:
        return False
    path = reference.group(2).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
    name = path.rsplit("/", 1)[-1].lower()
    parent = (
        path.rstrip("/").rsplit("/", 2)[-2].lower()
        if "/" in path.rstrip("/")
        else ""
    )
    is_viewer_asset_path = parent in {"", ".", "js", "css"}
    return (
        is_viewer_asset_path
        and (
            name in {"app", "app.js", "core.js", "polyfills.js"}
            or _is_hashed_runtime_asset(name, "js")
            or name
            in {
                "bootstrap.min.css",
                "loading.css",
                "material-icons.css",
                "roboto.css",
                "smui.css",
                "smui-dark.css",
            }
        )
    )


def _is_hosting_telemetry_script(tag: str) -> bool:
    """Return True for non-functional hosting telemetry/challenge bootstraps."""
    lowered = tag.lower()
    return (
        "data-cf-beacon" in lowered
        or "cloudflareinsights.com" in lowered
        or "/cdn-cgi/challenge-platform/" in lowered
        or "/cdn-cgi/scripts/" in lowered
    )


def _asset_tag_identity(tag: str) -> str:
    """Normalize an HTML asset tag independently of quote/attribute order."""
    kind_match = re.match(r"\s*<\s*(script|link)\b", tag, re.IGNORECASE)
    reference = re.search(
        r"\b(?:href|src)\s*=\s*([\"'])([^\"']+)\1", tag, re.IGNORECASE
    )
    if not kind_match or not reference:
        return ""
    path = reference.group(2).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = re.sub(r"/{2,}", "/", path)
    return f"{kind_match.group(1).lower()}:{path}"


def merge_legacy_index_customizations(template_html: str, source_html: str) -> str:
    """Merge publisher-owned legacy HTML tags without replacing viewer assets."""
    html = template_html
    source_title, _ = _html_site_identity(source_html)
    if source_title:
        if re.search(r"<title\b[^>]*>.*?</title\s*>", html, re.IGNORECASE | re.DOTALL):
            html = re.sub(
                r"<title\b[^>]*>.*?</title\s*>",
                lambda _match: f"<title>{source_title}</title>",
                html,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        else:
            html = _inject_into_head(html, f"<title>{source_title}</title>")

    head_match = re.search(
        r"<head\b[^>]*>(.*?)</head\s*>", source_html, re.IGNORECASE | re.DOTALL
    )
    source_head = head_match.group(1) if head_match else source_html
    head_tags: list[str] = []
    asset_identities = {
        identity
        for identity in (
            _asset_tag_identity(match.group(0))
            for match in re.finditer(
                r"<link\b[^>]*>|<script\b[^>]*>.*?</script\s*>",
                html,
                re.IGNORECASE | re.DOTALL,
            )
        )
        if identity
    }
    for match in re.finditer(
        r"<meta\b[^>]*>|<link\b[^>]*>|<style\b[^>]*>.*?</style\s*>|"
        r"<script\b[^>]*>.*?</script\s*>",
        source_head,
        re.IGNORECASE | re.DOTALL,
    ):
        tag = match.group(0)
        identity = _asset_tag_identity(tag)
        if (
            _is_runtime_tag(tag)
            or _is_hosting_telemetry_script(tag)
            or (identity and identity in asset_identities)
        ):
            continue
        if tag not in html:
            head_tags.append(tag)
            if identity:
                asset_identities.add(identity)
    if head_tags:
        html = _inject_into_head(html, "\n".join(head_tags))

    body_source = source_html[head_match.end() :] if head_match else source_html
    body_scripts = []
    for match in re.finditer(
            r"<script\b[^>]*>.*?</script\s*>",
            body_source,
            re.IGNORECASE | re.DOTALL,
        ):
        tag = match.group(0)
        identity = _asset_tag_identity(tag)
        if (
            _is_runtime_tag(tag)
            or _is_hosting_telemetry_script(tag)
            or (identity and identity in asset_identities)
            or tag in html
        ):
            continue
        body_scripts.append(tag)
        if identity:
            asset_identities.add(identity)
    if body_scripts:
        html = _inject_before_body_end(html, "\n".join(body_scripts))
    return html


def _remix_data_script(
    project_text: str,
    *,
    export_title: str = "",
    export_favicon: str = "",
) -> str:
    data = _compact_project(
        project_text,
        export_title=export_title,
        export_favicon=export_favicon,
    )
    return (
        '<script id="__icc_offline_data__">'
        f"window.__CYOA_PROJECT__={data};"
        f"window.__ICCPLUS_DATA__={data};"
        f"window.__CYOA_DATA__={data};"
        "</script>"
    )


def build_preserved_index(
    html: str,
    family: SiteFamily,
    project_text: str,
) -> str:
    """Adapt source HTML to a local runtime while keeping custom head/body tags."""
    had_sveltekit_runtime = (
        family is SiteFamily.ICC_PLUS_2
        and "_app/immutable/" in html.lower()
        and ("data-sveltekit" in html.lower() or "__sveltekit" in html.lower())
    )
    html = _remove_incompatible_runtime_scripts(html, family)
    if family is SiteFamily.ICC_PLUS_2:
        html = _ensure_head_markup(html, _PLUS2_REQUIRED_HEAD)
        # Current Plus 2 offline bundles embed the project at their injection
        # marker, but still try fetch("project.json") during startup. Browsers
        # reject sibling fetches under file:// and the runtime logs a CORS
        # error before falling back to the embedded state. Satisfy that narrow
        # request from the same project payload so double-click startup is
        # clean and retains the runtime's normal loading path.
        if '__cyoa_offline_patch__' not in html:
            from .iccplus import _build_html_interceptor

            try:
                project = json.loads(project_text)
            except (TypeError, ValueError):
                project = None
            if isinstance(project, dict):
                data_js = json.dumps(
                    project, ensure_ascii=False, separators=(",", ":")
                )
                html = _inject_into_head(
                    html,
                    _build_html_interceptor(
                        data_js, len(project_text.encode("utf-8"))
                    ),
                )
        if not re.search(
            r"\bid\s*=\s*([\"'])app\1", html, flags=re.IGNORECASE
        ):
            html = _inject_before_body_end(html, '<div id="app"></div>')
        runtime_match = re.search(
            r"<script\b[^>]*\bsrc=[\"'][^\"']*js/app\.js[^\"']*[\"'][^>]*>"
            r"\s*</script\s*>",
            html,
            flags=re.IGNORECASE,
        )
        mount_match = re.search(
            r"<(?P<tag>div|main|section)\b"
            r"(?=[^>]*\bid\s*=\s*([\"'])app\2)[^>]*>\s*"
            r"</(?P=tag)\s*>",
            html,
            flags=re.IGNORECASE,
        )
        if (
            runtime_match is not None
            and mount_match is not None
            and runtime_match.start() < mount_match.start()
        ):
            mount_markup = mount_match.group(0)
            html = html[: mount_match.start()] + html[mount_match.end() :]
            runtime_match = re.search(
                r"<script\b[^>]*\bsrc=[\"'][^\"']*js/app\.js[^\"']*[\"']",
                html,
                flags=re.IGNORECASE,
            )
            if runtime_match is not None:
                html = (
                    html[: runtime_match.start()]
                    + mount_markup
                    + html[runtime_match.start() :]
                )
        if not _has_reference(html, "js/app.js") or not re.search(
            r"<script\b[^>]*\bsrc=[\"'][^\"']*js/app\.js", html, flags=re.IGNORECASE
        ):
            html = _inject_before_body_end(html, '<script src="./js/app.js"></script>')
        if (
            had_sveltekit_runtime
            and "loading-overlay" in html.lower()
            and "__cyoa_replacement_loader_bridge__" not in html
        ):
            html = _inject_before_body_end(
                html,
                '<script id="__cyoa_replacement_loader_bridge__">'
                '(function(){var observer;function hide(){var node='
                'document.getElementById("loading-overlay");if(!node)return;'
                'node.style.opacity="0";setTimeout(function(){'
                'node.style.display="none";},500);if(observer)observer.disconnect();}'
                'function ready(){var app=document.getElementById("app");'
                'if(app&&app.childNodes.length){hide();return true;}return false;}'
                'if(!ready()){observer=new MutationObserver(ready);'
                'observer.observe(document.documentElement,{childList:true,subtree:true});}'
                '})();</script>',
            )
    elif family is SiteFamily.ICC_REMIX:
        export_title, export_favicon = _html_site_identity(html)
        html = _ensure_head_markup(html, _REMIX_REQUIRED_HEAD)
        html = re.sub(
            r"<script\b[^>]*id=[\"']__icc_offline_data__[\"'][^>]*>.*?</script\s*>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = _inject_into_head(
            html,
            _remix_data_script(
                project_text,
                export_title=export_title,
                export_favicon=export_favicon,
            ),
        )
        if not re.search(
            r"<script\b[^>]*\bsrc=[\"'][^\"']*(?:^|/)app[\"']",
            html,
            flags=re.IGNORECASE,
        ):
            html = _inject_before_body_end(html, '<script src="./app" defer></script>')
    return html


def _patch_index_for_replacement(
    index_path: Path,
    family: SiteFamily,
    project_text: str,
) -> None:
    html = build_preserved_index(_read_text(index_path), family, project_text)
    atomic_write_text(str(index_path), html)


def _overlay_template(template_dir: Path, destination: Path) -> list[str]:
    changed: list[str] = []
    for source in template_dir.rglob("*"):
        if not source.is_file() or source.name.lower() in {
            "index.html",
            "template-metadata.json",
        }:
            continue
        relative = source.relative_to(template_dir)
        target = destination / relative
        lower_relative = relative.as_posix().lower()
        preserve_existing = (
            lower_relative == "css/loading.css"
            or lower_relative == "favicon.ico"
            or lower_relative.startswith("fonts/")
        )
        if preserve_existing and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and not files_are_equal(source, target, shallow=False):
            backup_path = _backup_original(destination, target)
            if backup_path:
                changed.append(backup_path)
        shutil.copy2(source, target)
        changed.append(relative.as_posix())
    return changed


def _patch_file_protocol_resource_loaders(site_root: Path) -> list[str]:
    """Let simple XHR progress loaders finish when opened through ``file://``.

    Some customized ICC sites use XMLHttpRequest only to count already-linked
    CSS, JavaScript, and project files before hiding a loading overlay. Browsers
    block those local XHR requests even though the same files can be loaded by
    normal ``<link>``/``<script>`` elements. Keep HTTP behavior unchanged and,
    on ``file:``, mark the known resource list complete without issuing XHR.
    """
    changed: list[str] = []
    for script_path in site_root.rglob("*.js"):
        if (
            not script_path.is_file()
            or _ORIGINAL_SITE_DIR in script_path.relative_to(site_root).parts
        ):
            continue
        text = _read_text(script_path)
        if not all(
            marker in text
            for marker in (
                "new XMLHttpRequest",
                "resources.forEach",
                "loadedResources",
            )
        ):
            continue
        if "window.location.protocol !== 'file:'" in text:
            continue

        patched, counter_changes = re.subn(
            r"(\blet\s+loadedResources\s*=\s*)0(\s*;)",
            r"\1window.location.protocol === 'file:' ? resources.length : 0\2",
            text,
            count=1,
        )
        if not counter_changes:
            continue
        patched, loop_changes = re.subn(
            r"(?m)^(?P<indent>\s*)resources\.forEach\s*\(",
            r"\g<indent>if (window.location.protocol !== 'file:') resources.forEach(",
            patched,
            count=1,
        )
        if not loop_changes:
            continue

        backup_path = _backup_original(site_root, script_path)
        if backup_path:
            changed.append(backup_path)
        atomic_write_text(str(script_path), patched)
        changed.append(script_path.relative_to(site_root).as_posix())
    return changed


def _ensure_file_protocol_project_interceptor(
    site_root: Path,
    index_path: Path | None,
    marker_script: Path | None,
    project_text: str,
) -> list[str]:
    """Inject embedded project responses for legacy XHR/fetch loaders.

    Marker injection supplies the Vue/Vuex initial state, but customized legacy
    viewers can still reload ``project.json`` through XHR during ``beforeCreate``.
    Add the narrow project-only interceptor only when that runtime pattern is
    actually present; sites without it remain byte-for-byte unchanged.
    """
    if index_path is None or marker_script is None:
        return []
    script_text = _read_text(marker_script)
    if not (
        "XMLHttpRequest" in script_text
        and re.search(r"(?:project|data)\.json", script_text, re.IGNORECASE)
    ):
        return []
    original_html = _read_text(index_path)
    html = original_html
    has_interceptor = '__cyoa_offline_patch__' in html
    from .iccplus import _build_html_interceptor, _inject_into_head

    compat_nodes: list[tuple[str, str]] = []
    for element_id, tag_name in (("lm", "div"), ("indicator", "span")):
        runtime_lookup = re.search(
            rf"getElementById\(\s*['\"]{re.escape(element_id)}['\"]\s*\)",
            script_text,
        )
        html_element = re.search(
            rf"\bid\s*=\s*['\"]{re.escape(element_id)}['\"]",
            html,
            re.IGNORECASE,
        )
        if runtime_lookup and not html_element:
            compat_nodes.append((element_id, tag_name))
    if compat_nodes:
        node_specs = json.dumps(compat_nodes, separators=(",", ":"))
        # The publisher may replace document.body.innerHTML before loading the
        # viewer bundle (Dragon Echo does this after its AVIF capability test).
        # Keep the compatibility nodes alive from <head> with a tiny observer,
        # rather than placing markup that the replacement immediately deletes.
        compat_script = (
            '<script id="__cyoa_legacy_progress_compat__">'
            '(function(){var specs=' + node_specs + ';'
            'function ensure(){var host=document.body||document.documentElement;'
            'if(!host)return;var wrap=document.querySelector('
            "'[data-cyoa-runtime-compat=\"legacy-progress\"]');"
            'if(!wrap){wrap=document.createElement("div");'
            'wrap.hidden=true;wrap.setAttribute("aria-hidden","true");'
            'wrap.setAttribute("data-cyoa-runtime-compat","legacy-progress");'
            'host.appendChild(wrap);}'
            'specs.forEach(function(spec){if(!document.getElementById(spec[0])){'
            'var node=document.createElement(spec[1]);node.id=spec[0];'
            'wrap.appendChild(node);}});}'
            'if(document.readyState==="loading")'
            'document.addEventListener("DOMContentLoaded",ensure);else ensure();'
            'new MutationObserver(ensure).observe(document.documentElement,'
            '{childList:true,subtree:true});})();</script>'
        )
        html = _inject_into_head(
            html,
            compat_script,
        )

    patched = html
    if not has_interceptor:
        try:
            project = json.loads(project_text)
        except (TypeError, ValueError):
            return []
        data_js = json.dumps(project, ensure_ascii=False, separators=(",", ":"))
        data_js = data_js.replace("</", "<\\/")
        patched = _inject_into_head(
            patched,
            _build_html_interceptor(data_js, len(project_text.encode("utf-8"))),
        )
    if patched == original_html:
        return []
    backup_path = _backup_original(site_root, index_path)
    changed = [backup_path] if backup_path else []
    atomic_write_text(str(index_path), patched)
    changed.append(index_path.relative_to(site_root).as_posix())
    return changed


def modernize_site(
    source_dir: os.PathLike[str] | str,
    destination_dir: os.PathLike[str] | str,
    *,
    viewer_collection: os.PathLike[str] | str | None = None,
    templates: Mapping[SiteFamily, ViewerTemplate] | None = None,
    source_url: str = "",
) -> ModernizeResult:
    """Copy and modernize one site without altering its source directory."""
    source = Path(source_dir).resolve()
    destination = Path(destination_dir).resolve()
    if source != destination:
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copytree(source, destination, copy_function=shutil.copy2)

    profile = analyze_site(destination)
    if profile.strategy == "copy_only":
        return ModernizeResult(
            source,
            destination,
            profile.family,
            profile.strategy,
            "copied",
            profile.reasons[0]
            if profile.reasons
            else "No compatible ICC runtime detected.",
        )
    if profile.project_path is None:
        return ModernizeResult(
            source,
            destination,
            profile.family,
            profile.strategy,
            "failed",
            "project.json is missing",
        )

    project_text = _read_text(profile.project_path)
    changed: list[str] = []
    if profile.strategy == "patch_existing":
        if profile.family is SiteFamily.ICC_REMIX:
            if profile.index_path is None:
                raise ValueError("index.html is missing")
            source_html = _read_text(profile.index_path)
            if "{{ICC_PROJECT_DATA_SCRIPT}}" in source_html:
                export_title, export_favicon = _html_site_identity(source_html)
                data_script = _remix_data_script(
                    project_text,
                    export_title=export_title if "{{" not in export_title else "",
                    export_favicon=export_favicon if "{{" not in export_favicon else "",
                )
                html = source_html.replace("{{ICC_PROJECT_DATA_SCRIPT}}", data_script)
                html = html.replace(
                    "{{ICC_PROJECT_SIZE}}", str(len(project_text.encode("utf-8")))
                )
                html = html.replace("{{ICC_SITE_TITLE}}", "CYOA")
                html = html.replace("{{ICC_FAVICON_TAG}}", "")
            else:
                # A previously modernized Remix viewer already contains its
                # local runtime. Refresh only the embedded project payload;
                # requiring/re-overlaying a template here made a second run
                # fail and could needlessly replace runtime files.
                html = build_preserved_index(
                    source_html, SiteFamily.ICC_REMIX, project_text
                )
            if html != source_html:
                backup_path = _backup_original(destination, profile.index_path)
                if backup_path:
                    changed.append(backup_path)
                atomic_write_text(str(profile.index_path), html)
                changed.append("index.html")
        else:
            if profile.marker_script is None:
                raise ValueError("compatible ICC bundle marker is missing")
            backup_path = _backup_original(destination, profile.marker_script)
            if backup_path:
                changed.append(backup_path)
            _inject_marked_script(profile.marker_script, project_text)
            changed.append(profile.marker_script.relative_to(destination).as_posix())
            changed.extend(
                _ensure_file_protocol_project_interceptor(
                    destination,
                    profile.index_path,
                    profile.marker_script,
                    project_text,
                )
            )
    else:
        available = dict(templates or {})
        if viewer_collection is not None:
            available.update(resolve_viewer_templates(viewer_collection))
        template = available.get(profile.family)
        if template is None:
            raise FileNotFoundError(
                f"no {profile.family.value} offline viewer template was found"
            )
        with tempfile.TemporaryDirectory(prefix="cyoa_viewer_template_") as temporary:
            template_dir = Path(temporary)
            _extract_template(template, template_dir)
            _validate_replacement_template(template_dir, profile.family)
            changed.extend(_overlay_template(template_dir, destination))
        if profile.family is SiteFamily.ICC_PLUS_2:
            expected_local_bundle = destination / "js" / "app.js"
            marker_script = expected_local_bundle
            _inject_marked_script(marker_script, project_text)
            changed.append(marker_script.relative_to(destination).as_posix())
        if profile.index_path is None:
            raise ValueError("index.html is missing")
        backup_path = _backup_original(destination, profile.index_path)
        if backup_path:
            changed.append(backup_path)
        _patch_index_for_replacement(profile.index_path, profile.family, project_text)
        changed.append("index.html")

        # Template overlay can reintroduce remote font URLs (for example the
        # bundled roboto.css) after WebsiteDownloader already localized the
        # original page. Run one final recursive pass on the finished viewer.
        from .injector import (
            _inject_project_font_links,
            _localize_preserved_index_assets,
        )

        final_html = _read_text(profile.index_path)
        try:
            parsed_project = json.loads(project_text)
        except (TypeError, ValueError):
            parsed_project = {}
        final_html = _inject_project_font_links(final_html, parsed_project)
        final_html = _localize_preserved_index_assets(
            final_html,
            source_url,
            str(destination),
        )
        atomic_write_text(str(profile.index_path), final_html)

    # A few legacy/custom viewers use local XHR merely as a loading-progress
    # counter. Patch those narrowly so ordinary browser double-click works;
    # HTTP/server behavior and actual application requests remain untouched.
    changed.extend(_patch_file_protocol_resource_loaders(destination))

    return ModernizeResult(
        source,
        destination,
        profile.family,
        profile.strategy,
        "modernized",
        "; ".join(profile.reasons),
        tuple(dict.fromkeys(changed)),
    )


def _discover_site_roots(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for index_path in root.rglob("index.html"):
        site = index_path.parent
        if site == root or _direct_project_path(site) is not None:
            candidates.append(site)
    return sorted(
        set(candidates), key=lambda path: (len(path.parts), str(path).lower())
    )


def modernize_collection(
    source_dir: os.PathLike[str] | str,
    destination_dir: os.PathLike[str] | str,
    *,
    viewer_collection: os.PathLike[str] | str,
    progress: Callable[[int, int, str], None] | None = None,
) -> CollectionReport:
    """Mirror top-level library entries and modernize every detected site root."""
    source = Path(source_dir).resolve()
    destination = Path(destination_dir).resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)
    if destination == source:
        raise ValueError("collection output must differ from the source")
    destination.mkdir(parents=True, exist_ok=True)
    templates = resolve_viewer_templates(viewer_collection)
    items = [
        path
        for path in source.iterdir()
        if path.is_dir() and path.resolve() != destination
    ]
    items.sort(key=lambda path: path.name.lower())
    results: list[ModernizeResult] = []
    failed_items = 0
    modernized_items = 0

    for position, item in enumerate(items, 1):
        if progress is not None:
            progress(position, len(items), item.name)
        target = destination / item.name
        try:
            if target.exists():
                raise FileExistsError(target)
            shutil.copytree(item, target, copy_function=shutil.copy2)
            site_roots = _discover_site_roots(target)
            if not site_roots:
                results.append(
                    ModernizeResult(
                        item,
                        target,
                        SiteFamily.UNKNOWN,
                        "copy_only",
                        "copied",
                        "No index.html found.",
                    )
                )
            else:
                item_was_modernized = False
                for site_root in site_roots:
                    site_result = modernize_site(
                        site_root,
                        site_root,
                        templates=templates,
                    )
                    original_site_root = item / site_root.relative_to(target)
                    results.append(
                        ModernizeResult(
                            original_site_root,
                            site_result.destination,
                            site_result.family,
                            site_result.strategy,
                            site_result.status,
                            site_result.message,
                            site_result.changed_files,
                        )
                    )
                    item_was_modernized = (
                        item_was_modernized or site_result.status == "modernized"
                    )
                if item_was_modernized:
                    modernized_items += 1
        except (OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            failed_items += 1
            logger.exception("Could not modernize collection item %s", item)
            results.append(
                ModernizeResult(
                    item, target, SiteFamily.UNKNOWN, "error", "failed", str(exc)
                )
            )

    copied_items = len(items) - modernized_items - failed_items
    report = CollectionReport(
        source,
        destination,
        len(items),
        modernized_items,
        max(0, copied_items),
        failed_items,
        tuple(results),
    )
    report_payload = {
        "source": str(report.source),
        "destination": str(report.destination),
        "summary": {
            "total_items": report.total_items,
            "modernized": report.modernized,
            "copied": report.copied,
            "failed": report.failed,
        },
        "sites": [
            {
                "source": str(result.source),
                "destination": str(result.destination),
                "family": result.family.value,
                "strategy": result.strategy,
                "status": result.status,
                "message": result.message,
                "changed_files": list(result.changed_files),
            }
            for result in report.site_results
        ],
    }
    atomic_write_text(
        str(destination / "conversion_report.json"),
        json.dumps(report_payload, ensure_ascii=False, indent=2),
    )
    return report


__all__ = [
    "CollectionReport",
    "ModernizeResult",
    "SiteFamily",
    "SiteProfile",
    "ViewerTemplate",
    "analyze_site",
    "build_preserved_index",
    "merge_legacy_index_customizations",
    "modernize_collection",
    "modernize_site",
    "resolve_registered_viewer_templates",
    "resolve_viewer_templates",
]
