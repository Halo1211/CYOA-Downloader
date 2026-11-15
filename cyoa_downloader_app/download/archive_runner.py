"""Orchestrates optional Smart/Browser archive stages."""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import asdict, replace
from urllib.parse import urljoin

from ..core.atomic_io import atomic_write_text
from ..core.paths import _safe_archive_join
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from .archive_policy import ArchivePolicy
from .archive_profiler import profile_archive_target
from .route_crawler import RouteCrawler


def run_archive_extensions(downloader, policy: ArchivePolicy):
    requested_policy = policy.normalized()
    auto_profile = None
    policy = requested_policy
    if requested_policy.strategy == "auto":
        auto_profile = profile_archive_target(downloader)
        policy = replace(requested_policy, strategy=auto_profile.effective_strategy).normalized()
    if not policy.crawl_routes and requested_policy.strategy != "auto":
        return None

    if not policy.crawl_routes:
        pages = []
        if os.path.isfile(downloader.start_html_local):
            pages.append({"url": downloader.start_url, "local": "index.html"})
        manifest = {
            "format": 1,
            "requested_policy": asdict(requested_policy),
            "policy": asdict(policy),
            "auto_profile": auto_profile.to_dict() if auto_profile else None,
            "start_url": downloader.start_url,
            "pages": pages,
            "route_failures": [],
            "route_limit_reached": False,
            "remaining_queued_routes": 0,
            "runtime": None,
        }
        path = os.path.join(downloader.output_folder, "archive_manifest.json")
        atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False))
        logger.info(
            "Auto archive selected Classic; browser/route stages skipped: %s",
            auto_profile.reason if auto_profile else "no extra stages needed",
        )
        return manifest

    crawl = RouteCrawler(downloader, policy).crawl()
    runtime = None
    if policy.capture_runtime:
        # Route crawling may have enabled WebsiteDownloader's reusable
        # Playwright transport after an HTTP 429. Close it before starting the
        # dedicated runtime-capture Playwright lifecycle; nested Sync API
        # instances otherwise fail and skip late assets.
        close_transport = getattr(downloader, "release_browser_transport", None) or getattr(downloader, "close", None)
        if callable(close_transport):
            close_transport()
        from ..network.runtime_capture import capture_runtime_assets
        runtime_urls = list(crawl.pages.keys())[:policy.runtime_max_pages]
        runtime = capture_runtime_assets(
            downloader,
            runtime_urls or [downloader.start_url],
            settle_time_ms=policy.settle_time_ms,
            capture_interactions=policy.safe_interactions,
            max_scroll_steps=policy.max_scroll_steps,
            max_interactions=policy.max_interactions,
            no_progress_rounds=policy.no_progress_rounds,
        )

    manifest = {
        "format": 1,
        "requested_policy": asdict(requested_policy),
        "policy": asdict(policy),
        "auto_profile": auto_profile.to_dict() if auto_profile else None,
        "start_url": downloader.start_url,
        "pages": [
            {"url": url, "local": os.path.relpath(local, downloader.output_folder).replace("\\", "/")}
            for url, local in crawl.pages.items()
        ],
        "route_failures": crawl.failed,
        "route_link_bases": crawl.link_bases,
        "pending_routes": crawl.pending,
        "route_limit_reached": crawl.limit_reached,
        "remaining_queued_routes": crawl.remaining_queued,
        "runtime": asdict(runtime) if runtime is not None else None,
    }
    path = os.path.join(downloader.output_folder, "archive_manifest.json")
    atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("Archive manifest saved: %s", path)
    return manifest


def finalize_existing_archive(folder: str, start_url: str, policy: ArchivePolicy):
    """Write a recovery manifest for a crawl that completed before finalization."""
    folder = os.path.abspath(folder)
    pages = []
    root = os.path.join(folder, "index.html")
    if os.path.isfile(root):
        pages.append({"url": start_url, "local": "index.html"})
    routes_root = pathlib.Path(folder) / "routes"
    if routes_root.is_dir():
        for route_file in sorted(routes_root.rglob("index.html")):
            relative_parent = route_file.parent.relative_to(routes_root).as_posix().strip("/")
            pages.append({
                "url": urljoin(start_url.rstrip("/") + "/", relative_parent),
                "local": route_file.relative_to(pathlib.Path(folder)).as_posix(),
            })
    manifest = {
        "format": 1,
        "policy": asdict(policy.normalized()),
        "start_url": start_url,
        "pages": pages,
        "route_failures": [],
        "route_limit_reached": False,
        "remaining_queued_routes": 0,
        "runtime": {"status": "recovered_after_interrupted_runtime_capture"},
    }
    path = os.path.join(folder, "archive_manifest.json")
    atomic_write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("Recovered archive manifest saved: %s (%d pages)", path, len(pages))
    return manifest


def resume_existing_archive(folder: str, start_url: str, policy: ArchivePolicy):
    """Continue only the unresolved same-story links in an existing manifest."""
    from bs4 import BeautifulSoup  # type: ignore

    from .website import WebsiteDownloader

    folder = os.path.abspath(folder)
    manifest_path = os.path.join(folder, "archive_manifest.json")
    from ..storage.archive_checkpoint import ArchiveCheckpoint

    has_checkpoints = ArchiveCheckpoint.exists(folder)
    if not os.path.isfile(manifest_path) and not has_checkpoints:
        return finalize_existing_archive(folder, start_url, policy)
    try:
        old_manifest = (json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
                        if os.path.isfile(manifest_path) else {})
        if not isinstance(old_manifest, dict):
            raise TypeError("manifest root must be an object")
    except (OSError, RecursionError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Archive manifest is unreadable; rebuilding recovery manifest: %s", exc)
        old_manifest = finalize_existing_archive(folder, start_url, policy)
    existing = {}
    missing_pages = []
    old_pages = old_manifest.get("pages", [])
    if not isinstance(old_pages, list):
        old_pages = []
    for item in old_pages:
        if not isinstance(item, dict) or not item.get("url") or not item.get("local"):
            continue
        try:
            local = _safe_archive_join(folder, str(item["local"]), decode_url=False)
        except ValueError:
            continue
        if os.path.isfile(local):
            existing[str(item["url"])] = local
        else:
            missing_pages.append(str(item["url"]))
    normalized_policy = policy.normalized()
    downloader = WebsiteDownloader(
        start_url, folder, archive_strategy=normalized_policy.strategy,
    )
    try:
        enable_resume = getattr(downloader, "enable_checkpoint_resume", None)
        checkpoint = enable_resume() if callable(enable_resume) else None
        # New checkpoints are authoritative: a file's presence alone is not
        # evidence of a committed route. Legacy manifests retain their policy.
        if has_checkpoints and (checkpoint is None or checkpoint.urls("route") or checkpoint.urls("asset") or not checkpoint.compatible):
            existing = {}
            missing_pages.append(start_url)
        auto_profile = None
        if normalized_policy.strategy == "auto":
            auto_profile = profile_archive_target(downloader)
            normalized_policy = replace(
                normalized_policy, strategy=auto_profile.effective_strategy,
            ).normalized()
            if normalized_policy.strategy == "classic":
                old_manifest.update({
                    "requested_policy": asdict(policy.normalized()),
                    "policy": asdict(normalized_policy),
                    "auto_profile": auto_profile.to_dict(),
                })
                atomic_write_text(manifest_path, json.dumps(old_manifest, indent=2, ensure_ascii=False))
                logger.info("Archive resume skipped: Auto profile remains Classic/project-first.")
                return old_manifest
        crawler = RouteCrawler(downloader, normalized_policy)
        canonical_existing = {}
        for url, local in existing.items():
            canonical = crawler._canonicalize(url)
            if canonical and crawler._is_allowed(canonical):
                canonical_existing[canonical] = local
        seeds = set()
        saved_bases = old_manifest.get("route_link_bases", {})
        if isinstance(saved_bases, dict):
            for page_url, base in saved_bases.items():
                canonical = crawler._canonicalize(page_url)
                if canonical in canonical_existing and isinstance(base, str):
                    normalized_base = crawler._canonicalize(base)
                    if normalized_base:
                        crawler._page_link_bases[canonical] = normalized_base
        # Failed routes may no longer have an authored link in a repaired page.
        # Manifest paths are literal filesystem names; URLs remain remote keys.
        failed_pages = old_manifest.get("route_failures", [])
        if not isinstance(failed_pages, list):
            failed_pages = []
        pending_urls = missing_pages + [
            item["url"] for item in failed_pages
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        saved_pending = old_manifest.get("pending_routes", [])
        if isinstance(saved_pending, list):
            pending_urls.extend(url for url in saved_pending if isinstance(url, str))
        for pending_url in pending_urls:
            candidate = crawler._canonicalize(pending_url)
            if candidate and crawler._is_allowed(candidate) and candidate not in canonical_existing:
                seeds.add(candidate)
        for page_url, local in canonical_existing.items():
            try:
                soup = BeautifulSoup(pathlib.Path(local).read_text(encoding="utf-8", errors="ignore"), "html.parser")
                for tag in soup.find_all("a", href=True):
                    href = str(tag.get("href") or "").strip()
                    if (not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "//"))
                            or tag.has_attr("data-cyoa-local-route")):
                        continue
                    try:
                        candidate = crawler._canonicalize(urljoin(crawler._page_link_bases.get(page_url, page_url), href))
                    except (TypeError, ValueError):
                        continue
                    if crawler._is_allowed(candidate) and candidate not in canonical_existing:
                        seeds.add(candidate)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Could not inspect existing route %s: %s", local, exc)

        result = crawler.crawl(seed_urls=sorted(seeds), existing_pages=canonical_existing)
        manifest = {
            "format": 1,
            "requested_policy": asdict(policy.normalized()),
            "policy": asdict(normalized_policy),
            "auto_profile": auto_profile.to_dict() if auto_profile else old_manifest.get("auto_profile"),
            "start_url": start_url,
            "pages": [
                {"url": url, "local": os.path.relpath(local, folder).replace("\\", "/")}
                for url, local in result.pages.items()
            ],
            "route_failures": result.failed,
            "route_link_bases": result.link_bases,
            "pending_routes": result.pending,
            "route_limit_reached": result.limit_reached,
            "remaining_queued_routes": result.remaining_queued,
            "runtime": old_manifest.get("runtime"),
        }
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))
        logger.info(
            "Archive resume: %d seed(s), %d total page(s), %d failed",
            len(seeds), len(result.pages), len(result.failed),
        )
        return manifest
    finally:
        try:
            downloader.close()
        except DownloadCancelledError:
            raise
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Archive resume downloader close failed: %s", exc)
