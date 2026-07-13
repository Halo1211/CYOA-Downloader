"""Diagnostic and failure report writers."""

from __future__ import annotations

import os
import pathlib
from datetime import datetime
from typing import Dict, List, Optional

from ..core.paths import _safe_join
from ..logging_setup import logger

_DEPRECATED_BROKEN_ASSET_REPORT = "broken_assets_report.html"

def _remove_deprecated_broken_asset_report(output_dir: str) -> None:
    """Remove the old HTML broken-asset report if it exists.

    v7.3.9 no longer generates broken_assets_report.html. Failed asset
    details are appended to backup_report.txt when available, or written to
    failed_assets.txt for non-ICC outputs. This cleanup only prevents stale
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
    """Write a plain text failed-assets log for non-ICC outputs.

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

__all__ = ['_DEPRECATED_BROKEN_ASSET_REPORT', '_remove_deprecated_broken_asset_report', 'append_asset_failures_to_backup_report', 'write_failed_assets_log', 'write_asset_failure_summary', 'format_backup_report_text']
