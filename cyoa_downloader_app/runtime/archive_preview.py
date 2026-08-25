"""Helpers for serving framework-based website archives on their original routes."""

from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse


_FLIGHT_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)',
    re.DOTALL,
)


def _normalized_route(value: str) -> str:
    path = unquote(urlparse(str(value or "")).path or "/")
    path = re.sub(r"/+", "/", path)
    return "/" if path == "/" else path.rstrip("/")


def select_archive_root(output_dir: str) -> str:
    """Select the actual viewer folder inside a CLI output directory."""
    root = pathlib.Path(output_dir).resolve()
    if (root / "index.html").is_file() or (root / "archive_manifest.json").is_file():
        return str(root)
    try:
        candidates = []
        for child in root.iterdir():
            try:
                resolved = child.resolve()
                contained = os.path.commonpath([str(root), str(resolved)]) == str(root)
            except (OSError, ValueError):
                contained = False
            if (
                contained
                and not child.is_symlink()
                and resolved.is_dir()
                and (resolved / "index.html").is_file()
                and (resolved / "archive_manifest.json").is_file()
            ):
                candidates.append(resolved)
    except OSError:
        return str(root)
    return str(candidates[0].resolve()) if len(candidates) == 1 else str(root)


def resolve_archived_page(serve_dir: str, request_route: str) -> Optional[str]:
    """Map an original web route to the HTML file recorded in the manifest."""
    root = os.path.abspath(serve_dir)
    root_real = os.path.realpath(root)
    manifest_path = os.path.join(root, "archive_manifest.json")
    try:
        manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None

    wanted = _normalized_route(request_route)
    for page in manifest.get("pages", []):
        if not isinstance(page, dict) or _normalized_route(page.get("url", "")) != wanted:
            continue
        local = str(page.get("local") or "").replace("/", os.sep)
        candidate = os.path.abspath(os.path.join(root, local))
        candidate_real = os.path.realpath(candidate)
        try:
            if (
                os.path.commonpath([root_real, candidate_real]) == root_real
                and os.path.isfile(candidate_real)
            ):
                return candidate_real
        except ValueError:
            return None
    return None


def extract_next_flight_stream(html: str) -> str:
    """Rebuild a Next.js RSC response from the inline Flight queue."""
    parts = []
    for match in _FLIGHT_PUSH_RE.finditer(str(html or "")):
        try:
            value = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)


def resolve_next_optimizer_image(serve_dir: str, request_target: str) -> Optional[str]:
    """Resolve a ``/_next/image`` request to an already-downloaded source image."""
    parsed = urlparse(str(request_target or ""))
    source_values = parse_qs(parsed.query).get("url", [])
    if not source_values:
        return None
    source_path = unquote(urlparse(source_values[0]).path)
    basename = pathlib.PurePosixPath(source_path).name
    stem, suffix = os.path.splitext(basename)
    if not stem:
        return None

    root = pathlib.Path(serve_dir).resolve()
    search_roots = [root / "external" / "images", root / "images", root]
    patterns = [basename]
    if suffix:
        patterns.append(f"{stem}_*{suffix}")
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for pattern in patterns:
            for candidate in search_root.glob(pattern):
                try:
                    resolved = candidate.resolve()
                    if resolved.is_file() and os.path.commonpath([root, resolved]) == str(root):
                        return str(resolved)
                except (OSError, ValueError):
                    continue
    return None


__all__ = [
    "extract_next_flight_stream",
    "resolve_archived_page",
    "resolve_next_optimizer_image",
    "select_archive_root",
]
