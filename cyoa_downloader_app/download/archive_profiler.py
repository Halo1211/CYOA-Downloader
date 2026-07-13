"""Deterministic site fingerprinting for the optional Auto archive strategy."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import pathlib
import re
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

from ..logging_setup import logger
from ..project.cyoa_cafe import classify_cyoa_cafe_record, fetch_cyoa_cafe_record
from ..project.parse import looks_like_project_payload

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


@dataclass(frozen=True)
class ArchiveProfile:
    detected_engine: str
    effective_strategy: str
    reason: str
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def project_archive_profile(url: str, project_url: str = "") -> ArchiveProfile:
    return ArchiveProfile(
        detected_engine="project_json",
        effective_strategy="classic",
        reason="structured project data already enumerates the authoritative assets",
        signals={"source_url": url, "project_url": project_url, "browser_needed": False},
    )


def _hint_profile(value: Any) -> ArchiveProfile | None:
    if isinstance(value, ArchiveProfile):
        return value
    if isinstance(value, dict):
        try:
            return ArchiveProfile(
                detected_engine=str(value["detected_engine"]),
                effective_strategy=str(value["effective_strategy"]),
                reason=str(value.get("reason") or "precomputed archive profile"),
                signals=dict(value.get("signals") or {}),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _has_local_project(downloader) -> bool:
    candidates = [
        pathlib.Path(downloader.output_folder) / "project.json",
        pathlib.Path(downloader.output_folder) / "project_original.json",
    ]
    downloaded = getattr(downloader, "_downloaded", {}) or {}
    candidates.extend(
        pathlib.Path(path)
        for url, path in downloaded.items()
        if str(url).split("?", 1)[0].lower().endswith(("project.json", "project.txt")) and path
    )
    for path in candidates:
        try:
            if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if looks_like_project_payload(text):
                return True
        except (OSError, ValueError):
            continue
    return False


def _bounded_bundle_signals(folder: str) -> Dict[str, int]:
    signals = {"fetch": 0, "intersection_observer": 0, "dynamic_import": 0, "new_image": 0}
    scanned = 0
    try:
        paths = pathlib.Path(folder).rglob("*.js")
        for path in paths:
            if scanned >= 3 * 1024 * 1024:
                break
            try:
                remaining = 3 * 1024 * 1024 - scanned
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read(min(1024 * 1024, remaining))
                scanned += len(text)
            except OSError:
                continue
            signals["fetch"] += len(re.findall(r"\bfetch\s*\(", text))
            signals["intersection_observer"] += text.count("IntersectionObserver")
            signals["dynamic_import"] += len(re.findall(r"\bimport\s*\(", text))
            signals["new_image"] += len(re.findall(r"\bnew\s+Image\s*\(", text))
    except OSError:
        pass
    signals["scanned_chars"] = scanned
    return signals


def profile_archive_target(downloader) -> ArchiveProfile:
    """Classify a downloaded entry without executing untrusted interactions."""
    hinted = _hint_profile(getattr(downloader, "archive_auto_profile", None))
    if hinted is not None:
        return hinted
    start_url = str(downloader.start_url)
    record = fetch_cyoa_cafe_record(start_url)
    record_kind = classify_cyoa_cafe_record(record)
    if record_kind == "static_pages":
        return ArchiveProfile(
            detected_engine="cyoa_cafe_static",
            effective_strategy="classic",
            reason="cyoa_pages files are available directly from structured metadata",
            signals={"record_id": record.get("id") if record else "", "browser_needed": False},
        )
    if _has_local_project(downloader):
        return project_archive_profile(start_url)

    html = ""
    try:
        html = pathlib.Path(downloader.start_html_local).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        pass
    script_count = len(re.findall(r"<script\b[^>]*\bsrc\s*=", html, re.IGNORECASE))
    module_count = len(re.findall(r"<script\b[^>]*\btype\s*=\s*[\"']module[\"']", html, re.IGNORECASE))
    lazy_count = len(re.findall(r"\b(?:data-src|data-lazy-src|loading\s*=\s*[\"']lazy)", html, re.IGNORECASE))
    framework_root = bool(re.search(r"\bid\s*=\s*[\"'](?:app|root|__next)[\"']", html, re.IGNORECASE))
    next_assets = html.lower().count("/_next/")
    canvas_count = len(re.findall(r"<canvas\b", html, re.IGNORECASE))
    route_count = 0
    if BeautifulSoup is not None and html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            start = urlparse(start_url)
            scope = start.path.rstrip("/") + "/"
            seen = set()
            for tag in soup.find_all("a", href=True):
                href = str(tag.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                    continue
                try:
                    candidate = urlparse(urljoin(start_url, href))
                except ValueError:
                    continue
                if candidate.netloc.lower() != start.netloc.lower():
                    continue
                if candidate.path.rstrip("/") == start.path.rstrip("/") or candidate.path.startswith(scope):
                    seen.add((candidate.path, candidate.query))
            route_count = len(seen)
        except Exception:
            route_count = 0
    bundles = _bounded_bundle_signals(downloader.output_folder)
    runtime_score = sum((
        2 if next_assets else 0,
        1 if framework_root and script_count else 0,
        1 if module_count else 0,
        1 if canvas_count else 0,
        1 if bundles["fetch"] else 0,
        1 if bundles["intersection_observer"] else 0,
        1 if bundles["new_image"] else 0,
    ))
    signals: Dict[str, Any] = {
        "script_count": script_count,
        "module_script_count": module_count,
        "lazy_attribute_count": lazy_count,
        "same_story_route_count": route_count,
        "framework_root": framework_root,
        "next_asset_markers": next_assets,
        "canvas_count": canvas_count,
        "bundle_signals": bundles,
        "runtime_score": runtime_score,
    }
    if route_count and runtime_score >= 2:
        profile = ArchiveProfile(
            "javascript_routes", "browser",
            "same-story routes and runtime framework signals both require capture", signals,
        )
    elif route_count:
        profile = ArchiveProfile(
            "multi_route_html", "smart", "same-story document routes were discovered", signals,
        )
    elif runtime_score >= 2:
        profile = ArchiveProfile(
            "javascript_runtime", "browser", "runtime-only framework signals exceed the safe threshold", signals,
        )
    else:
        profile = ArchiveProfile(
            "static_or_scannable", "classic",
            "static scanners can enumerate the entry assets without browser interaction", signals,
        )
    logger.info(
        "Auto archive profile: engine=%s strategy=%s (%s)",
        profile.detected_engine, profile.effective_strategy, profile.reason,
    )
    return profile


__all__ = ["ArchiveProfile", "profile_archive_target", "project_archive_profile"]
