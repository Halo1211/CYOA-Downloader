"""URL validation, canonical matching, and output-path helpers."""

from __future__ import annotations

import pathlib
import posixpath
import re
from typing import List, Optional, Set, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

from .paths import _safe_join


def is_probable_url(value: str) -> bool:
    return bool(re.match(r"^https?://", str(value).strip(), re.IGNORECASE))


def _cyoap_local_path(output_folder: str, remote_url: str) -> str:
    parsed = urlparse(remote_url)
    remote_path = unquote(parsed.path.lstrip("/"))
    if not remote_path or remote_path.endswith("/"):
        remote_path = remote_path + "index.html" if remote_path else "index.html"
    return _safe_join(output_folder, remote_path, fallback="index.html")


def _same_origin(url_a: str, url_b: str) -> bool:
    # RFC 3986: scheme and host are case-insensitive, and
    # an explicit default port (:80 http / :443 https) is the same origin as
    # no port. The old netloc string compare returned false negatives for
    # "https://Example.com/..." or "https://host:443/...", silently skipping
    # same-site assets in the cyoap_vue downloader.
    def _key(u: str) -> Tuple[str, str, Optional[int]]:
        p = urlparse(u)
        scheme = (p.scheme or "").lower()
        host = (p.hostname or "").lower()
        try:
            port = p.port
        except ValueError:
            port = None
        if port is None:
            port = {"http": 80, "https": 443}.get(scheme)
        return scheme, host, port
    return _key(url_a) == _key(url_b)


def _candidate_urls_for_cyoap_asset(base_url: str, value: str, kind: str) -> List[str]:
    value = (value or "").strip()
    if not value or value.startswith("data:"):
        return []
    if is_probable_url(value):
        return [value]

    norm = value.lstrip("/")
    candidates: List[str] = [
        urljoin(base_url, norm),
        urljoin(base_url, quote(norm, safe="/:_.-")),
    ]

    if not norm.startswith("dist/"):
        if kind == "images":
            candidates.extend([
                urljoin(base_url, "dist/images/" + norm),
                urljoin(base_url, "dist/images/" + quote(norm, safe="/:_.-")),
            ])
        else:
            for folder in ("dist/audio/", "dist/media/", "dist/images/", "audio/", "media/"):
                candidates.extend([
                    urljoin(base_url, folder + norm),
                    urljoin(base_url, folder + quote(norm, safe="/:_.-")),
                ])

    dedup: List[str] = []
    seen: Set[str] = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def _directory_base_url(url: str) -> str:
    """Return an HTTP(S) URL that safely represents a directory base.

    Extensionless final path segments are treated as route/slug directories,
    while obvious document filenames (for example index.html) are stripped.
    This avoids turning /game/<id> into /game/ during CYOAP Vue probing.
    """
    normalized = canonicalize_url(str(url or "").strip())
    parsed = urlparse(normalized)
    path = parsed.path or "/"
    if path.endswith("/"):
        directory = path
    else:
        last = path.rsplit("/", 1)[-1]
        suffix = pathlib.PurePosixPath(last).suffix.lower()
        if suffix in {".html", ".htm", ".php", ".asp", ".aspx", ".jsp"}:
            directory = path.rsplit("/", 1)[0] + "/" if "/" in path else "/"
        else:
            directory = path + "/"
    return urlunparse((parsed.scheme, parsed.netloc, directory, "", "", ""))


def truncate_display_url(url: str, max_length: int = 72) -> str:
    """Return a display-only URL with a middle ellipsis; source data is unchanged."""
    text = str(url or "")
    if max_length < 12 or len(text) <= max_length:
        return text
    parsed = urlparse(text)
    prefix = parsed.netloc + parsed.path if parsed.netloc else text
    suffix = ("?" + parsed.query) if parsed.query else ""
    shown = prefix + suffix
    if len(shown) <= max_length:
        return shown
    keep_left = max(6, int(max_length * 0.62))
    keep_right = max(4, max_length - keep_left - 1)
    return shown[:keep_left] + "…" + shown[-keep_right:]


def canonicalize_url(url: str) -> str:
    """Canonicalize HTTP(S) URLs for deterministic deduplication/cache keys."""
    text = str(url or "").strip()
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {scheme or '?'}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("URL host is missing")
    port = parsed.port
    # urlparse().hostname removes IPv6 brackets; restore them when rebuilding
    # the authority so the canonical URL remains parseable.
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{display_host}:{port}"
    path = parsed.path or "/"
    trailing = path.endswith("/")
    path = posixpath.normpath(path)
    if not path.startswith("/"):
        path = "/" + path
    if trailing and path != "/":
        path += "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


__all__ = [
    "is_probable_url",
    "_cyoap_local_path",
    "_same_origin",
    "_candidate_urls_for_cyoap_asset",
    "_directory_base_url",
    "truncate_display_url",
    "canonicalize_url",
]
