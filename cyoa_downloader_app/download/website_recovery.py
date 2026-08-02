"""Retry failed website assets and continue bounded route archives."""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .archive_policy import ArchivePolicy
from .archive_runner import resume_existing_archive
from .website import WebsiteDownloader
from ..config.settings import _load_settings
from ..diagnostics.reports import write_failed_assets_log
from ..logging_setup import logger


_SOURCE_RE = re.compile(r"^(?:Source|Start URL)\s*:\s*(https?://\S+)", re.IGNORECASE)
_URL_RE = re.compile(r"^\s*URL\s*:\s*(https?://\S+)", re.IGNORECASE)
_FAILED_LINE_RE = re.compile(r"(https?://\S+?)(?:\s{2,}|\s*\(|$)", re.IGNORECASE)


@dataclass
class WebsiteRecoverySummary:
    discovered_assets: int = 0
    recovered_assets: int = 0
    failed_assets: int = 0
    archives_resumed: int = 0
    new_routes: int = 0


def _archive_policy_from_settings() -> ArchivePolicy:
    settings = _load_settings()
    return ArchivePolicy(
        strategy=str(settings.get("archive_strategy", "auto") or "auto"),
        max_pages=settings.get("archive_max_pages", 300),
        max_depth=settings.get("archive_max_depth", 30),
        interaction_policy=str(settings.get("archive_interaction_policy", "safe") or "safe"),
        runtime_max_pages=settings.get("archive_runtime_max_pages", 12),
        settle_time_ms=settings.get("archive_settle_time_ms", 1800),
        max_scroll_steps=settings.get("archive_max_scroll_steps", 100),
        max_interactions=settings.get("archive_max_interactions", 20),
        no_progress_rounds=settings.get("archive_no_progress_rounds", 2),
    ).normalized()


def _parse_failure_report(path: pathlib.Path) -> Tuple[str, List[str]]:
    source = ""
    urls: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return source, urls
    for line in lines:
        source_match = _SOURCE_RE.match(line.strip())
        if source_match and not source:
            source = source_match.group(1).strip()
        stripped = line.strip()
        failure_row = stripped.startswith(("✗", "×", "âœ—"))
        match = _URL_RE.match(line) or (_FAILED_LINE_RE.search(line) if failure_row else None)
        if match:
            url = match.group(1).rstrip().rstrip(")")
            if url not in urls:
                urls.append(url)
    return source, urls


def has_website_recovery_work(root: str) -> bool:
    base = pathlib.Path(root)
    if not base.is_dir():
        return False
    for report in list(base.rglob("failed_assets.txt")) + list(base.rglob("backup_report.txt")):
        _source, urls = _parse_failure_report(report)
        if urls:
            return True
    for manifest_path in base.rglob("archive_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(manifest, dict) and (
            manifest.get("route_failures") or manifest.get("route_limit_reached")
        ):
            return True
    return False


def retry_website_assets(root: str, policy: ArchivePolicy | None = None) -> WebsiteRecoverySummary:
    """Retry report URLs and continue unresolved route graphs in ``root``."""

    base = pathlib.Path(root).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Output folder not found: {base}")
    policy = (policy or _archive_policy_from_settings()).normalized()
    summary = WebsiteRecoverySummary()

    report_groups: Dict[pathlib.Path, Dict[str, object]] = {}
    report_paths = list(base.rglob("failed_assets.txt")) + list(base.rglob("backup_report.txt"))
    for report in report_paths:
        source, urls = _parse_failure_report(report)
        if not urls:
            continue
        group = report_groups.setdefault(report.parent, {"source": source, "urls": set(), "reports": []})
        if source and not group.get("source"):
            group["source"] = source
        group["urls"].update(urls)
        group["reports"].append(report)

    for folder, group in report_groups.items():
        urls = sorted(group["urls"])
        source = str(group.get("source") or "")
        if not source:
            manifest = folder / "archive_manifest.json"
            try:
                source = str(json.loads(manifest.read_text(encoding="utf-8")).get("start_url") or "")
            except (OSError, ValueError, TypeError):
                source = ""
        if not source:
            logger.warning("Retry Assets skipped %s: source URL is missing", folder)
            summary.failed_assets += len(urls)
            continue
        downloader = WebsiteDownloader(source, str(folder), archive_strategy=policy.strategy)
        remaining: List[Dict[str, str]] = []
        try:
            for url in urls:
                summary.discovered_assets += 1
                try:
                    local = downloader.download_asset(url)
                except Exception as exc:
                    local = None
                    error = str(exc)
                else:
                    error = "retry failed"
                if local:
                    summary.recovered_assets += 1
                else:
                    summary.failed_assets += 1
                    remaining.append({"url": url, "error": error})
            if summary.recovered_assets:
                downloader.localize_existing_text_assets()
        finally:
            downloader.close()
        failed_log = folder / "failed_assets.txt"
        if remaining:
            write_failed_assets_log(remaining, str(folder), source_url=source)
        elif failed_log.exists():
            try:
                failed_log.unlink()
            except OSError:
                pass

    for manifest_path in base.rglob("archive_manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            start_url = str(manifest.get("start_url") or "")
            old_pages = len(manifest.get("pages") or [])
            has_failures = bool(manifest.get("route_failures"))
            limit_reached = bool(manifest.get("route_limit_reached"))
            if not start_url or (not has_failures and not limit_reached):
                continue
            updated = resume_existing_archive(str(manifest_path.parent), start_url, policy)
            new_pages = max(0, len(updated.get("pages") or []) - old_pages)
            summary.archives_resumed += 1
            summary.new_routes += new_pages
        except Exception as exc:
            logger.warning("Retry Assets could not resume %s: %s", manifest_path, exc)

    return summary


__all__ = [
    "WebsiteRecoverySummary", "has_website_recovery_work", "retry_website_assets",
]
