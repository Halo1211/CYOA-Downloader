"""CYOA.CAFE metadata resolver.

The deterministic resolver class lives here; compatibility wrappers continue to
delegate through the runtime surface so older import paths keep working.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests

from ..constants.assets import AUDIO_EXTENSIONS, FONT_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..download.asset_scan import _safe_response_text
from ..integrations.ai import _host_resolves_internal
from ..logging_setup import logger
from ..network.fetch import fetch_response
from ..project.parse import extract_project_text_from_payload, looks_like_project_payload
from ..core.url_utils import canonicalize_url, is_probable_url

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    def BeautifulSoup(*_args, **_kwargs):  # type: ignore
        raise RuntimeError(
            "Missing dependency: beautifulsoup4 is required for HTML/ICC parsing. "
            "Install it with: pip install beautifulsoup4"
        )


_CYOA_CAFE_FIELDS: Tuple[str, ...] = (
    "iframe_url", "iframeUrl", "url", "link", "source", "embed",
    "project_url", "game_url",
)
_CYOA_CAFE_CACHE_TTL = 6 * 3600.0
_CYOA_CAFE_CACHE_MAX = 256
_CYOA_CAFE_CACHE: Dict[str, Tuple[float, str]] = {}
_CYOA_CAFE_CACHE_LOCK = threading.RLock()
_CYOA_CAFE_RECORD_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _looks_like_custom_viewer_html(text: str) -> bool:
    """Recognize hand-written CYOA viewers without an ICC project file."""
    lower = str(text or "").lower()
    signatures = (
        ('id="cyoa-container"', "game_data"),
        ("id='cyoa-container'", "game_data"),
        ('id="bg-music"', "point-bar"),
        ("id='bg-music'", "point-bar"),
    )
    # These paired markers are specific enough to avoid treating an arbitrary
    # JavaScript page as a downloadable CYOA viewer.
    return any(all(marker in lower for marker in pair) for pair in signatures)


def _cyoa_cafe_record_id(url: str) -> str:
    """Return a validated PocketBase record id for a /game/<id> URL."""
    try:
        normalized = canonicalize_url(str(url or "").strip())
        parsed = urlparse(normalized)
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != "cyoa.cafe" or len(parts) != 2 or parts[0].lower() != "game":
        return ""
    record_id = parts[1]
    return record_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", record_id) else ""


def fetch_cyoa_cafe_record(url: str, *, timeout: int = 15, fetcher: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Fetch and TTL-cache a public cyoa.cafe game record.

    This is deliberately separate from viewer resolution: records whose
    ``cyoa_pages`` field contains files are valid static CYOAs even though they
    do not expose an iframe/viewer URL.
    """
    record_id = _cyoa_cafe_record_id(url)
    if not record_id:
        return None
    cache_key = f"record:{record_id}"
    now = time.monotonic()
    with _CYOA_CAFE_CACHE_LOCK:
        cached = _CYOA_CAFE_RECORD_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return dict(cached[1])
        if cached:
            _CYOA_CAFE_RECORD_CACHE.pop(cache_key, None)
    api_url = f"https://cyoa.cafe/api/collections/games/records/{quote(record_id, safe='')}"
    fetch = fetcher or CYOACafeResolver._default_fetch
    response = None
    try:
        try:
            response = fetch(api_url, timeout=max(3, int(timeout)))
        except TypeError:
            response = fetch(api_url)
        if response is None or CYOACafeResolver._response_status(response) >= 400:
            return None
        data = CYOACafeResolver._json_from_response(response)
    except Exception as exc:
        logger.debug("cyoa.cafe record fetch failed for %s: %s", record_id, exc)
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    if not isinstance(data, dict) or str(data.get("id") or "") != record_id:
        return None
    with _CYOA_CAFE_CACHE_LOCK:
        _CYOA_CAFE_RECORD_CACHE[cache_key] = (now + _CYOA_CAFE_CACHE_TTL, dict(data))
        if len(_CYOA_CAFE_RECORD_CACHE) > _CYOA_CAFE_CACHE_MAX:
            oldest = min(_CYOA_CAFE_RECORD_CACHE, key=lambda key: _CYOA_CAFE_RECORD_CACHE[key][0])
            _CYOA_CAFE_RECORD_CACHE.pop(oldest, None)
    return dict(data)


def classify_cyoa_cafe_record(record: Optional[Dict[str, Any]]) -> str:
    """Classify a catalogue record as static pages, linked viewer, or unknown."""
    if not isinstance(record, dict):
        return "unknown"
    pages = record.get("cyoa_pages")
    if isinstance(pages, list) and any(isinstance(item, str) and item.strip() for item in pages):
        return "static_pages"
    for key in _CYOA_CAFE_FIELDS:
        value = record.get(key)
        if isinstance(value, str) and is_probable_url(value):
            return "linked_viewer"
    return "unknown"


def build_cyoa_cafe_file_url(record: Dict[str, Any], filename: str) -> str:
    """Build a same-origin PocketBase file URL from validated record metadata."""
    collection = str(record.get("collectionId") or "").strip()
    record_id = str(record.get("id") or "").strip()
    name = str(filename or "").strip()
    valid = re.compile(r"[A-Za-z0-9_-]{1,80}")
    if not valid.fullmatch(collection) or not valid.fullmatch(record_id) or not name:
        raise ValueError("invalid cyoa.cafe file metadata")
    # PocketBase filenames are one path segment. Quoting slash/backslash also
    # prevents a malicious metadata record from escaping the record directory.
    return (
        "https://cyoa.cafe/api/files/"
        f"{quote(collection, safe='')}/{quote(record_id, safe='')}/{quote(name, safe='')}"
    )


class CYOACafeResolutionError(RuntimeError):
    """Raised when a cyoa.cafe metadata page has no validated viewer target."""


class CYOACafeResolver:
    """Single deterministic resolver pipeline for old and subdomain cyoa.cafe URLs."""

    def __init__(
        self,
        fetcher: Optional[Any] = None,
        validator: Optional[Any] = None,
        *,
        timeout: int = 15,
        max_hops: int = 6,
        max_depth: int = 4,
    ) -> None:
        self.fetcher = fetcher or self._default_fetch
        self._uses_default_fetcher = fetcher is None
        self.validator = validator
        self.timeout = max(3, int(timeout))
        self.max_hops = max(1, int(max_hops))
        self.max_depth = max(1, int(max_depth))
        self.visited: Set[str] = set()
        self.rejections: List[Tuple[str, str]] = []
        # Per-resolution response cache prevents duplicate GETs when a direct
        # creator URL is first validated and then parsed for iframe/JSON fallback.
        self._responses: Dict[str, Any] = {}

    @staticmethod
    def _default_fetch(url: str, timeout: int = 15) -> Optional[requests.Response]:
        return fetch_response(
            url,
            extra_headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json,*/*"},
            timeout=timeout,
            return_error_response=True,
        )

    @staticmethod
    def normalize_input(url: str) -> str:
        normalized = canonicalize_url(url)
        parsed = urlparse(normalized)
        # Metadata routes ignore query/fragment for deterministic cache lookup.
        if parsed.netloc.lower() == "cyoa.cafe" and parsed.path.startswith("/game/"):
            normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
        return normalized

    @staticmethod
    def _cache_get(key: str) -> Optional[str]:
        now = time.monotonic()
        with _CYOA_CAFE_CACHE_LOCK:
            item = _CYOA_CAFE_CACHE.get(key)
            if not item:
                return None
            expires, value = item
            if expires <= now:
                _CYOA_CAFE_CACHE.pop(key, None)
                return None
            return value

    @staticmethod
    def _cache_put(key: str, value: str) -> None:
        with _CYOA_CAFE_CACHE_LOCK:
            if len(_CYOA_CAFE_CACHE) >= _CYOA_CAFE_CACHE_MAX:
                oldest = min(_CYOA_CAFE_CACHE, key=lambda k: _CYOA_CAFE_CACHE[k][0])
                _CYOA_CAFE_CACHE.pop(oldest, None)
            _CYOA_CAFE_CACHE[key] = (time.monotonic() + _CYOA_CAFE_CACHE_TTL, value)

    @staticmethod
    def invalidate(url: str) -> None:
        try:
            key = CYOACafeResolver.normalize_input(url)
        except Exception:
            key = str(url or "")
        with _CYOA_CAFE_CACHE_LOCK:
            _CYOA_CAFE_CACHE.pop(key, None)
            record_id = _cyoa_cafe_record_id(url)
            if record_id:
                _CYOA_CAFE_RECORD_CACHE.pop(f"record:{record_id}", None)
            stale = [k for k, (_exp, v) in _CYOA_CAFE_CACHE.items() if v == key or v == url]
            for k in stale:
                _CYOA_CAFE_CACHE.pop(k, None)

    @staticmethod
    def _response_status(resp: Any) -> int:
        try:
            return int(getattr(resp, "status_code", 0) or 0)
        except Exception:
            return 0

    @staticmethod
    def _response_text(resp: Any) -> str:
        if resp is None:
            return ""
        try:
            return _safe_response_text(resp)
        except Exception:
            try:
                return str(resp.text or "")
            except Exception:
                return ""

    @staticmethod
    def _json_from_response(resp: Any) -> Any:
        try:
            return resp.json()
        except Exception:
            text = CYOACafeResolver._response_text(resp)
            return json.loads(text)

    def _fetch(self, url: str) -> Optional[Any]:
        if url in self._responses:
            return self._responses[url]
        if len(self.visited) >= self.max_hops * 8:
            return None
        try:
            response = self.fetcher(url, timeout=self.timeout)
        except TypeError:
            response = self.fetcher(url)
        except Exception as exc:
            logger.debug(f"cyoa.cafe fetch failed: {url}: {exc}")
            return None
        if response is not None:
            self._responses[url] = response
        return response

    def _authoritative_metadata_target(self, normalized: str) -> str:
        """Return the viewer named by the current catalogue record, if any.

        A resolver cache is useful for repeated downloads, but a catalogue
        route is an alias whose target can change.  Resolve the record id again
        before trusting a cached target so a previous game's viewer can never
        bleed into the current ``/game/<id>`` request.
        """
        parsed = urlparse(normalized)
        path = re.sub(r"/+", "/", parsed.path or "/")
        if parsed.netloc.lower() != "cyoa.cafe" or not re.fullmatch(
            r"/game/[^/]+/?", path, flags=re.IGNORECASE
        ):
            return ""
        try:
            record = fetch_cyoa_cafe_record(
                normalized,
                timeout=self.timeout,
                fetcher=self.fetcher,
            )
        except Exception as exc:
            logger.debug("Authoritative CYOA.CAFE record check skipped: %s", exc)
            return ""
        if not isinstance(record, dict):
            return ""
        for field in _CYOA_CAFE_FIELDS:
            value = record.get(field)
            if not isinstance(value, str) or not is_probable_url(value):
                continue
            try:
                candidate = canonicalize_url(value)
            except Exception:
                continue
            allowed, _reason = self._candidate_allowed(candidate)
            if allowed:
                return candidate
        return ""

    def _reject(self, url: str, reason: str) -> None:
        self.rejections.append((url, reason))
        logger.debug(f"cyoa.cafe candidate rejected: {url} — {reason}")

    def _candidate_allowed(self, url: str) -> Tuple[bool, str]:
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "unparseable URL"
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return False, "not an HTTP(S) URL"
        lower = url.lower()
        path = parsed.path.lower()
        if os.path.splitext(path)[1] in (IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | FONT_EXTENSIONS):
            return False, "direct media/font asset"
        blocked_hosts = (
            "github.com", "discord.com", "discord.gg", "twitter.com", "x.com",
            "facebook.com", "instagram.com", "reddit.com", "google-analytics.com",
            "googletagmanager.com",
        )
        host = (parsed.hostname or "").lower()
        if _host_resolves_internal(host):
            return False, "internal/loopback/private address (SSRF guard)"
        if any(host == item or host.endswith("." + item) for item in blocked_hosts):
            return False, "repository/social/analytics host"
        if any(token in lower for token in ("/profile/", "/docs/", "/documentation/", "utm_")):
            return False, "profile/documentation/tracking URL"
        return True, ""

    def _extract_json_candidates(self, value: Any, depth: int = 0) -> List[str]:
        if depth > self.max_depth:
            return []
        out: List[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _CYOA_CAFE_FIELDS and isinstance(item, str) and is_probable_url(item):
                    out.append(item)
                elif isinstance(item, (dict, list)):
                    out.extend(self._extract_json_candidates(item, depth + 1))
        elif isinstance(value, list):
            for item in value[:500]:
                out.extend(self._extract_json_candidates(item, depth + 1))
        return out

    def _api_candidates(self, normalized: str) -> List[Tuple[str, str]]:
        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        parts = [p for p in parsed.path.split("/") if p]
        api_urls: List[str] = []
        if host == "cyoa.cafe" and len(parts) >= 2 and parts[0] == "game":
            if self._uses_default_fetcher:
                record = fetch_cyoa_cafe_record(normalized, timeout=self.timeout)
                if record is not None:
                    return [
                        (candidate, "PocketBase API")
                        for candidate in self._extract_json_candidates(record)
                    ]
            record_id = parts[1]
            api_urls.append(f"https://cyoa.cafe/api/collections/games/records/{quote(record_id, safe='')}")
        elif host.endswith(".cyoa.cafe") and host != "cyoa.cafe":
            slug = parts[0] if parts else ""
            if slug:
                filters = [f"slug='{slug}'", f"iframe_url~'{slug}'"]
                for expression in filters:
                    api_urls.append(
                        "https://cyoa.cafe/api/collections/games/records"
                        f"?filter={quote(expression, safe='')}&perPage=10"
                    )
        out: List[Tuple[str, str]] = []
        for api_url in api_urls:
            resp = self._fetch(api_url)
            if resp is None:
                logger.debug(f"cyoa.cafe API unavailable, continuing fallback: {api_url}")
                continue
            status = self._response_status(resp)
            if status and status >= 400:
                logger.debug(f"cyoa.cafe API HTTP {status}, continuing fallback: {api_url}")
                continue
            try:
                data = self._json_from_response(resp)
            except Exception as exc:
                logger.debug(f"cyoa.cafe API JSON invalid: {api_url}: {exc}")
                continue
            for candidate in self._extract_json_candidates(data):
                out.append((candidate, "PocketBase API"))
        return out

    def _html_candidates(self, normalized: str) -> List[Tuple[str, str]]:
        resp = self._fetch(normalized)
        if resp is None:
            return []
        if self._response_status(resp) == 404:
            return []
        html = self._response_text(resp)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str]] = []
        for iframe in soup.find_all("iframe"):
            src = str(iframe.get("src") or "").strip()
            if src:
                out.append((urljoin(normalized, src), "HTML iframe"))
        for script in soup.find_all("script"):
            script_text = script.string or script.get_text("", strip=False) or ""
            script_type = str(script.get("type") or "").lower()
            if script_type in {"application/json", "application/ld+json"} or script.get("id") in {"__NEXT_DATA__", "__NUXT__"}:
                try:
                    data = json.loads(script_text)
                    out.extend((u, "embedded JSON") for u in self._extract_json_candidates(data))
                except Exception as exc:
                    logger.debug(f"cyoa.cafe embedded JSON parse skipped: {exc}")
            for field in _CYOA_CAFE_FIELDS:
                pattern = re.compile(
                    rf'["\']{re.escape(field)}["\']\s*:\s*["\'](https?://[^"\']+)["\']',
                    re.IGNORECASE,
                )
                out.extend((m.group(1).replace("\\/", "/"), f"script field {field}") for m in pattern.finditer(script_text))
        # Canonical/og values are only candidates; validation is still mandatory.
        for tag in soup.find_all("meta", attrs={"property": "og:url"}):
            value = str(tag.get("content") or "").strip()
            if value:
                out.append((urljoin(normalized, value), "og:url"))
        for tag in soup.find_all("link", rel=lambda v: v and "canonical" in v):
            value = str(tag.get("href") or "").strip()
            if value:
                out.append((urljoin(normalized, value), "canonical"))
        return out

    def _probe_project_endpoint(self, url: str) -> bool:
        resp = self._fetch(url)
        if resp is None or self._response_status(resp) >= 400:
            return False
        text = self._response_text(resp).strip()
        path = urlparse(url).path.lower()
        if path.endswith("dist/nodes/list.json"):
            try:
                return isinstance(json.loads(text), list)
            except Exception:
                return False
        if path.endswith("dist/platform.json"):
            try:
                return isinstance(json.loads(text), dict)
            except Exception:
                return False
        if path.endswith(("project.json", "project.txt")):
            return bool(extract_project_text_from_payload(text) or looks_like_project_payload(text))
        return False

    def validate_candidate(self, candidate: str) -> bool:
        if self.validator is not None:
            return bool(self.validator(candidate))
        allowed, reason = self._candidate_allowed(candidate)
        if not allowed:
            self._reject(candidate, reason)
            return False
        try:
            canonical = canonicalize_url(candidate)
        except Exception as exc:
            self._reject(candidate, str(exc))
            return False
        if canonical in self.visited:
            self._reject(canonical, "already visited")
            return False
        self.visited.add(canonical)
        resp = self._fetch(canonical)
        if resp is None:
            self._reject(canonical, "request failed")
            return False
        status = self._response_status(resp)
        if status == 404:
            self.invalidate(canonical)
            self._reject(canonical, "HTTP 404")
            return False
        if status and status >= 400:
            self._reject(canonical, f"HTTP {status}")
            return False
        text = self._response_text(resp)
        ctype = str(getattr(resp, "headers", {}).get("Content-Type", "")).lower()
        path = urlparse(canonical).path.lower()
        if "json" in ctype or path.endswith((".json", ".txt")):
            if path.endswith("dist/nodes/list.json"):
                try:
                    return isinstance(json.loads(text), list)
                except Exception:
                    return False
            if path.endswith("dist/platform.json"):
                try:
                    return isinstance(json.loads(text), dict)
                except Exception:
                    return False
            if extract_project_text_from_payload(text) or looks_like_project_payload(text):
                return True
        lower = text.lower()
        viewer_markers = (
            'id="app"', "id='app'", "interactive cyoa creator", "interactive cyoa",
            "project.json", "project.txt", "dist/platform.json", "dist/nodes/list.json",
            "window.app", "cyoa-viewer", "loading omnitrix",
        )
        if any(marker in lower for marker in viewer_markers) or _looks_like_custom_viewer_html(text):
            # Exclude the metadata/catalog shell unless a viewer marker is strong.
            if urlparse(canonical).netloc.lower() == "cyoa.cafe" and "/game/" in path:
                if not any(marker in lower for marker in ("iframe", "project.json", "interactive cyoa creator")) \
                    and not _looks_like_custom_viewer_html(text):
                    self._reject(canonical, "metadata page, not viewer")
                    return False
            return True

        # The CYOA.CAFE core can also publish a self-contained, CSS/HTML-only
        # CYOA.  It has no app root, JavaScript bundle, or project.json; its
        # catalogue record is the authoritative link.  Recognize that generic
        # shape instead of requiring a viewer-specific template signature.
        # Requiring the CYOA title plus the input/label choice structure keeps
        # ordinary HTML pages out of this fallback.
        if (
            ("text/html" in ctype or lower.startswith(("<!doctype html", "<html")))
            and "<title>[cyoa]" in lower
            and all(marker in lower for marker in ("<main", "<input", "<label"))
        ):
            return True

        base = canonical if canonical.endswith("/") else canonical + "/"
        endpoints = (
            urljoin(base, "project.json"),
            urljoin(base, "project.txt"),
            urljoin(base, "dist/platform.json"),
            urljoin(base, "dist/nodes/list.json"),
        )
        valid_platform = False
        valid_nodes = False
        for endpoint in endpoints:
            ok = self._probe_project_endpoint(endpoint)
            if endpoint.endswith("dist/platform.json"):
                valid_platform = ok
            elif endpoint.endswith("dist/nodes/list.json"):
                valid_nodes = ok
            elif ok:
                return True
        if valid_platform and valid_nodes:
            return True
        self._reject(canonical, "no valid project/viewer signature")
        return False

    def _close_responses(self) -> None:
        """Close response objects retained for one resolution only."""
        responses = list(self._responses.values())
        self._responses.clear()
        for response in responses:
            try:
                response.close()
            except Exception:
                pass

    def resolve(self, url: str) -> str:
        """Resolve a CYOA.CAFE URL and release all probe responses."""
        try:
            return self._resolve(url)
        finally:
            self._close_responses()

    def _resolve(self, url: str) -> str:
        normalized = self.normalize_input(url)
        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        if host != "cyoa.cafe" and not host.endswith(".cyoa.cafe"):
            return normalized

        authoritative_target = self._authoritative_metadata_target(normalized)
        if authoritative_target:
            cached_target = self._cache_get(normalized)
            if cached_target:
                try:
                    cached_key = canonicalize_url(cached_target)
                except Exception:
                    cached_key = cached_target
                if cached_key != authoritative_target:
                    logger.info(
                        "Discarding stale CYOA.CAFE resolver cache: "
                        f"{cached_target} (record says {authoritative_target})"
                    )
                    self.invalidate(normalized)

        cached = self._cache_get(normalized)
        if cached:
            if self.validate_candidate(cached):
                logger.info(f"cyoa.cafe resolved from TTL cache: {cached}")
                return cached
            self.invalidate(normalized)
        candidates: List[Tuple[str, str]] = []
        # Creator subdomain URLs are normally the real viewer. Validate and
        # return before querying the central catalog API; the API is only a
        # fallback when the direct viewer signature is absent.
        if host.endswith(".cyoa.cafe") and host != "cyoa.cafe":
            if self.validate_candidate(normalized):
                self._cache_put(normalized, normalized)
                logger.info(f"cyoa.cafe resolved via direct creator URL: {normalized}")
                return normalized
        candidates.extend(self._api_candidates(normalized))
        candidates.extend(self._html_candidates(normalized))
        # Common subdomain route. Add after metadata/API candidates and validate.
        if host.endswith(".cyoa.cafe") and host != "cyoa.cafe":
            base = normalized.rstrip("/") + "/"
            if not urlparse(base).path.rstrip("/").endswith("/game"):
                candidates.append((urljoin(base, "game/"), "common /game/ route"))
        seen: Set[str] = set()
        for raw, method in candidates[: self.max_hops * 8]:
            try:
                candidate = canonicalize_url(urljoin(normalized, raw))
            except Exception as exc:
                self._reject(str(raw), f"normalization failed: {exc}")
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate == normalized and method not in {"direct creator URL"}:
                continue
            if self.validate_candidate(candidate):
                self._cache_put(normalized, candidate)
                logger.info(f"cyoa.cafe resolved via {method}: {candidate}")
                return candidate
        detail = "; ".join(f"{u} ({why})" for u, why in self.rejections[-5:])
        raise CYOACafeResolutionError(
            f"No validated CYOA viewer target found for {normalized}."
            + (f" Rejected candidates: {detail}" if detail else " API/HTML candidates were unavailable.")
        )


def get_iframe_url_from_cyoa_cafe(game_url: str) -> str:
    """Compatibility wrapper around the single deterministic v46 resolver."""
    return CYOACafeResolver().resolve(game_url)


def _legacy():
    from ._bridge import legacy
    return legacy()


def _v462_default_cafe_fetch(*args, **kwargs):
    return _legacy()._v462_default_cafe_fetch(*args, **kwargs)


def _v462_invalidate_cafe_cache(*args, **kwargs):
    return _legacy()._v462_invalidate_cafe_cache(*args, **kwargs)


def _v462_validate_pure_website_candidate(*args, **kwargs):
    return _legacy()._v462_validate_pure_website_candidate(*args, **kwargs)


def _v462_resolve_cafe(*args, **kwargs):
    return _legacy()._v462_resolve_cafe(*args, **kwargs)


def _v462_auto_detect_output_variant(*args, **kwargs):
    return _legacy()._v462_auto_detect_output_variant(*args, **kwargs)


def _v462_auto_detect_mode(*args, **kwargs):
    return _legacy()._v462_auto_detect_mode(*args, **kwargs)


def _v462_is_cafe_url(*args, **kwargs):
    return _legacy()._v462_is_cafe_url(*args, **kwargs)


def _v466_is_cafe_metadata_game_url(*args, **kwargs):
    return _legacy()._v466_is_cafe_metadata_game_url(*args, **kwargs)


__all__ = [
    "CYOACafeResolutionError", "CYOACafeResolver", "get_iframe_url_from_cyoa_cafe",
    "fetch_cyoa_cafe_record", "classify_cyoa_cafe_record", "_looks_like_custom_viewer_html",
    "build_cyoa_cafe_file_url",
    "_CYOA_CAFE_FIELDS", "_CYOA_CAFE_CACHE_TTL", "_CYOA_CAFE_CACHE_MAX",
    "_CYOA_CAFE_CACHE", "_CYOA_CAFE_CACHE_LOCK", "_CYOA_CAFE_RECORD_CACHE",
    "_v462_default_cafe_fetch", "_v462_invalidate_cafe_cache",
    "_v462_validate_pure_website_candidate", "_v462_resolve_cafe",
    "_v462_auto_detect_output_variant", "_v462_auto_detect_mode",
    "_v462_is_cafe_url", "_v466_is_cafe_metadata_game_url",
]
