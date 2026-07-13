"""Orchestrates optional Smart/Browser archive stages."""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import asdict, replace
from urllib.parse import urljoin

from .archive_policy import ArchivePolicy
from .archive_profiler import profile_archive_target
from .route_crawler import RouteCrawler
from ..core.atomic_io import atomic_write_text
from ..core.paths import _safe_join
from ..logging_setup import logger


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
    if not os.path.isfile(manifest_path):
        return finalize_existing_archive(folder, start_url, policy)
    try:
        old_manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
        if not isinstance(old_manifest, dict):
            raise ValueError("manifest root must be an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Archive manifest is unreadable; rebuilding recovery manifest: %s", exc)
        old_manifest = finalize_existing_archive(folder, start_url, policy)
    existing = {}
    for item in old_manifest.get("pages", []):
        if not isinstance(item, dict) or not item.get("url") or not item.get("local"):
            continue
        try:
            local = _safe_join(folder, str(item["local"]), fallback="index.html")
        except ValueError:
            continue
        if os.path.isfile(local):
            existing[str(item["url"])] = local
    normalized_policy = policy.normalized()
    downloader = WebsiteDownloader(
        start_url, folder, archive_strategy=normalized_policy.strategy,
    )
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
    canonical_existing = {crawler._canonicalize(url): local for url, local in existing.items()}
    seeds = set()
    for page_url, local in canonical_existing.items():
        try:
            soup = BeautifulSoup(pathlib.Path(local).read_text(encoding="utf-8", errors="ignore"), "html.parser")
            for tag in soup.find_all("a", href=True):
                href = str(tag.get("href") or "").strip()
                if (not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "//"))
                        or tag.has_attr("data-cyoa-local-route")):
                    continue
                try:
                    candidate = crawler._canonicalize(urljoin(page_url, href))
                except (TypeError, ValueError):
                    continue
                if crawler._is_allowed(candidate) and candidate not in canonical_existing:
                    seeds.add(candidate)
        except Exception as exc:
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
