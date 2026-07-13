"""Bounded same-origin route crawler for SPA/SSR story websites."""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .archive_policy import ArchivePolicy
from .asset_scan import _safe_response_text
from .package import clean_url_path_component
from ..core.atomic_io import atomic_write_text
from ..logging_setup import logger
from ..project.parse import try_decode_bytes

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


_SKIP_PATH_RE = re.compile(
    r"/(?:api|auth|login|logout|sign-in|signin|signup|register|account|admin|checkout|payment)(?:/|$)",
    re.IGNORECASE,
)
_TRACKING_QUERY_KEYS = {"from", "ref", "source", "fbclid", "gclid"}


@dataclass
class RouteCrawlResult:
    pages: Dict[str, str] = field(default_factory=dict)
    failed: List[Dict[str, str]] = field(default_factory=list)
    discovered: List[str] = field(default_factory=list)
    limit_reached: bool = False
    remaining_queued: int = 0


class RouteCrawler:
    """Crawl navigable document routes while staying inside the starting story."""

    def __init__(self, downloader, policy: ArchivePolicy) -> None:
        self.downloader = downloader
        self.policy = policy.normalized()
        self.start_url = self._canonicalize(downloader.start_url)
        parsed = urlparse(self.start_url)
        self.origin = (parsed.scheme.lower(), parsed.netloc.lower())
        self.scope_path = self._scope_for(parsed.path)
        self._local_route_owners: Dict[str, str] = {}

    @staticmethod
    def _scope_for(path: str) -> str:
        path = re.sub(r"/+", "/", path or "/")
        if path.endswith("/"):
            return path
        leaf = path.rsplit("/", 1)[-1]
        if "." in leaf:
            return path.rsplit("/", 1)[0] + "/"
        return path.rstrip("/") + "/"

    @staticmethod
    def _canonicalize(url: str) -> str:
        try:
            p = urlparse(url)
            path = re.sub(r"/+", "/", p.path or "/")
            pairs = []
            for key, value in parse_qsl(p.query, keep_blank_values=True):
                lower = key.lower()
                if lower in _TRACKING_QUERY_KEYS or lower.startswith("utm_"):
                    continue
                pairs.append((key, value))
            return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(pairs), ""))
        except (TypeError, ValueError):
            return ""

    def _is_allowed(self, url: str) -> bool:
        p = urlparse(url)
        if (p.scheme.lower(), p.netloc.lower()) != self.origin:
            return False
        if _SKIP_PATH_RE.search(p.path or "/"):
            return False
        start_path = urlparse(self.start_url).path.rstrip("/")
        path = (p.path or "/").rstrip("/")
        if path == start_path:
            return True
        return (p.path or "/").startswith(self.scope_path)

    def _route_local_path(self, url: str) -> str:
        if self._canonicalize(url) == self.start_url:
            return self.downloader.start_html_local
        p = urlparse(url)
        start_path = urlparse(self.start_url).path.rstrip("/")
        suffix = p.path[len(start_path):].strip("/") if p.path.startswith(start_path) else p.path.strip("/")
        if not suffix:
            suffix = "root"
        parts = [part for part in suffix.split("/") if part and part not in {".", ".."}]
        safe_parts = [clean_url_path_component(part) or "route" for part in parts]
        if p.query:
            safe_parts[-1] += "_" + hashlib.sha1(p.query.encode("utf-8")).hexdigest()[:8]
        local = os.path.join(self.downloader.output_folder, "routes", *safe_parts, "index.html")
        canonical = self._canonicalize(url)
        path_key = os.path.normcase(os.path.abspath(local))
        owner = self._local_route_owners.get(path_key)
        if owner and owner != canonical:
            safe_parts[-1] += "_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
            local = os.path.join(self.downloader.output_folder, "routes", *safe_parts, "index.html")
            path_key = os.path.normcase(os.path.abspath(local))
        self._local_route_owners[path_key] = canonical
        return local

    def _fetch_html(self, url: str) -> Optional[str]:
        response = self.downloader._fetch(url)
        if response:
            try:
                return _safe_response_text(response)
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        if self.policy.strategy in {"smart", "browser"}:
            try:
                from ..network.browser import _fetch_headless
                raw = _fetch_headless(url)
                if raw:
                    return try_decode_bytes(raw)
            except Exception as exc:
                logger.debug("Headless route fetch failed for %s: %s", url, exc)
        return None

    def _links_from(self, html: str, page_url: str) -> List[str]:
        if BeautifulSoup is None:
            return []
        soup = BeautifulSoup(html, "html.parser")
        links: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = str(tag.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
                continue
            try:
                candidate = self._canonicalize(urljoin(page_url, href))
            except (TypeError, ValueError):
                continue
            if self._is_allowed(candidate):
                links.append(candidate)
        return links

    def crawl(
        self,
        seed_urls: Optional[List[str]] = None,
        existing_pages: Optional[Dict[str, str]] = None,
    ) -> RouteCrawlResult:
        result = RouteCrawlResult(pages=dict(existing_pages or {}))
        for existing_url, existing_local in result.pages.items():
            self._local_route_owners[os.path.normcase(os.path.abspath(existing_local))] = self._canonicalize(existing_url)
        seeds = [self._canonicalize(url) for url in (seed_urls or [self.start_url])]
        queue: deque[Tuple[str, int]] = deque(
            (url, 0) for url in seeds if url not in result.pages and self._is_allowed(url)
        )
        queued: Set[str] = set(result.pages) | set(seeds)

        while queue and len(result.pages) < self.policy.max_pages:
            url, depth = queue.popleft()
            html = self._fetch_html(url)
            if not html:
                result.failed.append({"url": url, "error": "could not fetch route HTML"})
                continue
            local = self._route_local_path(url)
            try:
                self.downloader.download_html_page(url, local, html)
            except Exception as exc:
                result.failed.append({"url": url, "error": str(exc)})
                continue
            result.pages[url] = local
            if depth >= self.policy.max_depth:
                continue
            for link in self._links_from(html, url):
                if link not in queued:
                    queued.add(link)
                    result.discovered.append(link)
                    queue.append((link, depth + 1))

        if queue and len(result.pages) >= self.policy.max_pages:
            result.limit_reached = True
            result.remaining_queued = len(queue)
            logger.warning(
                "Archive route limit reached at %d page(s); %d discovered route(s) remain queued. "
                "Increase archive_max_pages to continue.",
                self.policy.max_pages, result.remaining_queued,
            )
        self._rewrite_route_links(result.pages)
        logger.info("Archive route crawl: %d page(s), %d failed", len(result.pages), len(result.failed))
        return result

    def _rewrite_route_links(self, pages: Dict[str, str]) -> None:
        if BeautifulSoup is None:
            return
        route_map = {self._canonicalize(url): local for url, local in pages.items()}
        guard = """<script data-cyoa-offline-route-guard>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[data-cyoa-local-route]');if(a){e.preventDefault();e.stopImmediatePropagation();location.href=a.href;}},true);</script>"""
        for page_url, local in pages.items():
            try:
                text = pathlib.Path(local).read_text(encoding="utf-8", errors="ignore")
                soup = BeautifulSoup(text, "html.parser")
                changed = False
                for tag in soup.find_all("a", href=True):
                    try:
                        target = self._canonicalize(urljoin(page_url, str(tag.get("href") or "")))
                    except (TypeError, ValueError):
                        continue
                    target_local = route_map.get(target)
                    if not target_local:
                        continue
                    tag["href"] = os.path.relpath(target_local, os.path.dirname(local)).replace("\\", "/")
                    tag["data-cyoa-local-route"] = "1"
                    changed = True
                if changed and not soup.find(attrs={"data-cyoa-offline-route-guard": True}):
                    target = soup.body or soup
                    target.append(BeautifulSoup(guard, "html.parser"))
                if changed:
                    atomic_write_text(local, str(soup))
            except Exception as exc:
                logger.warning("Could not rewrite route links in %s: %s", local, exc)
