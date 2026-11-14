"""One thread owns each job's reusable browser and serialized body transfer."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from urllib.parse import urlparse

from ..core.cancellation import _raise_if_cancelled
from ..core.media_io import MEDIA_CHUNK_BYTES, spool_chunks
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger


def _external_frozen_chromium(chromium):
    """Use the exact installed revision when a frozen bundle omits browsers."""
    if not getattr(sys, "frozen", False) or "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        return None
    expected = Path(chromium.executable_path)
    if expected.is_file() or ".local-browsers" not in expected.parts:
        return None
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        cache = Path(local) / "ms-playwright"
    elif sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
    candidate = cache.joinpath(*expected.parts[expected.parts.index(".local-browsers") + 1:])
    return str(candidate) if candidate.is_file() else None


class BrowserFetchSession:
    supports_file_payloads = True

    def __init__(self, *, page_request_limit=8, page_byte_limit=32 * 1024 * 1024):
        self._admission = threading.Lock()
        self._state_lock = threading.Lock()
        self._executor = None
        self._closed = False
        self._owner_thread = None
        self._playwright = self._browser = self._context = self._page = None
        self._proxy_config = self._context_key = self._spool = None
        self._page_requests = self._page_bytes = 0
        self._page_request_limit = max(1, int(page_request_limit))
        self._page_byte_limit = max(1, int(page_byte_limit))
        self.stats = {"launches": 0, "contexts": 0, "pages": 0, "requests": 0, "spooled_bytes": 0,
                      "peak_page_body_bytes": 0}

    def _call(self, function, *args):
        while not self._admission.acquire(timeout=.05):
            _raise_if_cancelled()
            if self._closed:
                return None
        try:
            _raise_if_cancelled()
            with self._state_lock:
                if self._closed:
                    return None
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cyoa-browser")
                future = self._executor.submit(self._invoke, function, args)
            while True:
                try:
                    _raise_if_cancelled()
                    result = future.result(timeout=.05)
                    return None if self._closed else result
                except TimeoutError:
                    if future.done():
                        result = future.result()
                        return None if self._closed else result
                except DownloadCancelledError:
                    future.cancel()
                    raise
        finally:
            self._admission.release()

    def _invoke(self, function, args):
        self._owner_thread = threading.get_ident()
        _raise_if_cancelled()
        return function(*args)

    def _ensure(self, url):
        from .browser import _browser_session_state, _get_browser_proxy_config, _get_shared_session

        proxy = _get_browser_proxy_config(url)
        if proxy is None:
            raise RuntimeError("manual proxy transport is unsupported by browser fallback")
        session = _get_shared_session(False)
        agent, cookies = _browser_session_state((url,), session)
        agent = agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
        parsed = urlparse(url)
        origin = (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or (443 if parsed.scheme == "https" else 80))
        cookie_key = hashlib.sha256(repr(sorted(tuple(sorted(c.items())) for c in cookies)).encode()).hexdigest()
        # Retain the source object as well as its id: a reset session must not
        # inherit browser state if Python later reuses the old object's id.
        context_key = (origin, agent, (id(session), session), cookie_key)
        if self._browser is not None and proxy != self._proxy_config:
            self._close_runtime()
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()
                options = {"headless": True, "args": ["--no-sandbox"]}
                external_browser = _external_frozen_chromium(self._playwright.chromium)
                if external_browser:
                    options["executable_path"] = external_browser
                if proxy:
                    options["proxy"] = proxy
                self._browser = self._playwright.chromium.launch(**options)
                self.stats["launches"] += 1
                self._proxy_config = dict(proxy)
            if self._context is None or context_key != self._context_key:
                if self._context is not None:
                    self._context.close()
                self._page = None
                self._page_requests = self._page_bytes = 0
                self._context = self._browser.new_context(user_agent=agent, ignore_https_errors=False)
                if cookies:
                    self._context.add_cookies(cookies)
                self._context_key = context_key
                self.stats["contexts"] += 1
        except BaseException:
            self._close_runtime()
            raise

    def _fetch(self, url, timeout_ms, spool_dir, file_backed, reject_error_documents):
        from .browser import BrowserFetchResult, _looks_like_error_document, vpn_requirement_satisfied

        if not vpn_requirement_satisfied():
            return None
        try:
            from playwright.sync_api import Error as PlaywrightError
        except ImportError as exc:
            logger.debug("Reusable browser unavailable: %s", exc)
            return None
        completed = False
        try:
            self._ensure(url)
            _raise_if_cancelled()
            if not vpn_requirement_satisfied():
                return None
            if self._page is None:
                self._page = self._context.new_page()
                self.stats["pages"] += 1
            self.stats["requests"] += 1
            deadline = time.monotonic() + timeout_ms / 1000 if timeout_ms else float("inf")
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            _raise_if_cancelled()
            if response is None or not response.ok:
                return None
            request = response.request
            # Response.body has no timeout argument. Wait for the navigation's
            # requestfinished metadata first, pumping events on its owner.
            while request.timing.get("responseEnd", -1) < 0:
                _raise_if_cancelled()
                if request.failure:
                    raise RuntimeError(f"Browser response failed: {request.failure}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Browser response body exceeded navigation deadline")
                self._page.wait_for_timeout(min(50, remaining * 1000))
            body = response.body()
            _raise_if_cancelled()
            headers = {str(k): str(v) for k, v in response.headers.items()}
            if reject_error_documents and _looks_like_error_document(body, headers.get("content-type", "")):
                return None
            status, effective_url = int(response.status), str(response.url)
            self._page_requests += 1
            self._page_bytes += len(body)
            self.stats["peak_page_body_bytes"] = max(self.stats["peak_page_body_bytes"], self._page_bytes)
            completed = True
        except DownloadCancelledError:
            raise
        except (OSError, PlaywrightError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Reusable browser fetch failed (%s): %s", url, exc)
            return None
        finally:
            if self._page is not None and (not completed or
                    self._page_requests >= self._page_request_limit or self._page_bytes >= self._page_byte_limit):
                try:
                    self._page.close()
                except (OSError, PlaywrightError, RuntimeError) as exc:
                    logger.debug("Could not close browser response page: %s", exc)
                self._page = None
                self._page_requests = self._page_bytes = 0
        # Disk failures are job failures, not a reason to launch another backend.
        if file_backed and spool_dir is None:
            if self._spool is None:
                self._spool = tempfile.TemporaryDirectory(prefix="cyoa-browser-media-")
            spool_dir = self._spool.name
        content = self._retain(body, spool_dir)
        return BrowserFetchResult(content, headers, status, effective_url)

    def _retain(self, body, spool_dir):
        if spool_dir is None:
            return body
        payload = spool_chunks((body[i:i + MEDIA_CHUNK_BYTES] for i in range(0, len(body), MEDIA_CHUNK_BYTES)), spool_dir)
        self.stats["spooled_bytes"] += payload.size
        return payload

    def fetch(self, url, *, timeout_ms=45_000, file_backed=False):
        from .browser import vpn_requirement_satisfied

        if not vpn_requirement_satisfied():
            logger.error("VPN guard blocked reusable browser fetch: %s", url)
            return None
        return self._call(self._fetch, url, timeout_ms, None, file_backed, False)

    def _headless(self, url, reject_error_documents, spool_dir):
        from .browser import _fetch_selenium, _get_browser_proxy_config, vpn_requirement_satisfied

        result = self._fetch(url, 30_000, spool_dir, False, reject_error_documents)
        if result is not None:
            return result.content
        # Do not leave Chromium idle while launching the compatibility backend.
        self._close_runtime()
        if not vpn_requirement_satisfied():
            return None
        proxy = _get_browser_proxy_config(url)
        if proxy is None:
            return None
        body = _fetch_selenium(url, proxy, reject_error_documents)
        return self._retain(body, spool_dir) if body else None

    def fetch_headless(self, url, *, reject_error_documents=False, spool_dir=None):
        from .browser import _get_browser_proxy_config, vpn_requirement_satisfied

        if not vpn_requirement_satisfied() or _get_browser_proxy_config(url) is None:
            return None
        return self._call(self._headless, url, reject_error_documents, spool_dir)

    def _close_runtime(self):
        try:
            from playwright.sync_api import Error as PlaywrightError
        except ImportError:
            PlaywrightError = RuntimeError
        for obj in (self._page, self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except (OSError, PlaywrightError, RuntimeError) as exc:
                    logger.debug("Could not close browser object: %s", exc)
        self._page = self._context = self._browser = None
        self._page_requests = self._page_bytes = 0
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except (OSError, PlaywrightError, RuntimeError) as exc:
            logger.debug("Could not stop browser runtime: %s", exc)
        self._playwright = self._proxy_config = self._context_key = None

    def _finish(self):
        self._close_runtime()
        if self._spool is not None:
            self._spool.cleanup()
            self._spool = None

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        if executor is not None:
            try:
                executor.submit(self._finish).result()
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
                self._executor = None
