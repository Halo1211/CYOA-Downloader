"""Browser cookie/headless fetch helpers extracted from legacy.py."""

from __future__ import annotations

import os
import pathlib
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from ..core.cancellation import _raise_if_cancelled
from ..core.media_io import FilePayload
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from .browser_owner import BrowserFetchSession
from .proxy import _get_browser_proxy_config
from .sessions import _get_shared_session, create_retry_session
from .vpn import vpn_requirement_satisfied


@dataclass(frozen=True)
class BrowserFetchResult:
    content: bytes | FilePayload
    headers: dict[str, str]
    status: int
    url: str


def _browser_session_state(urls, session=None) -> tuple[str, list[dict]]:
    """Share only matching HTTP-session cookies with a runtime browser.

    FlareSolverr solves against a particular user agent. Starting a fresh
    browser without its matching cookies/agent otherwise triggers the same
    challenge again, even after the ordinary downloader has solved it.
    """
    session = session if session is not None else _get_shared_session(False)
    hosts = {(urlparse(url).hostname or '').lower() for url in urls}
    cookies = []
    for cookie in session.cookies:
        domain = str(cookie.domain or '').lower().lstrip('.')
        if not domain or not any(host == domain or host.endswith('.'+domain) for host in hosts):
            continue
        if cookie.expires is not None and cookie.expires <= time.time():
            continue
        cookies.append({'name': cookie.name,'value': cookie.value,'domain': cookie.domain,
                            'path': cookie.path or '/','secure': bool(cookie.secure)})
    agent = str(session.headers.get('User-Agent') or '')
    return (agent if agent != 'Mozilla/5.0' else ''), cookies



def _make_cookie_session(browser: str = "chrome") -> requests.Session | None:
    """
    Build a requests.Session with cookies from an installed browser.
    Uses browser-cookie3 if available, falls back to Chrome SQLite directly.
    """
    cookie_errors = (OSError, RuntimeError, TypeError, ValueError)
    try:
        import browser_cookie3 as _bc
        cookie_errors += (getattr(_bc, "BrowserCookieError", RuntimeError),)
        loaders = {
            "chrome":   _bc.chrome,  "chromium": _bc.chromium,
            "firefox":  _bc.firefox, "edge":     _bc.edge,
            "brave":    _bc.brave,   "opera":    _bc.opera,
            "safari":   _bc.safari,
        }
        loader = loaders.get(browser.lower())
        if loader is None: return None
        jar = loader()
        # Chromium's newer App-Bound Encryption can make browser_cookie3
        # return a jar whose entries have empty values after a decryption
        # failure.  Treat that as a failed extraction; sending empty cookies
        # makes callers believe authentication succeeded and is never useful.
        valid = [cookie for cookie in jar if str(cookie.value or "")]
        if not valid:
            logger.debug("Cookie session: %s yielded no decryptable cookie values", browser)
            return None
        s = create_retry_session()
        for cookie in valid:
            s.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain,
                path=cookie.path or "/",
            )
        logger.debug(f"Cookie session: loaded from {browser} ({len(valid)} cookies)")
        return s
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _make_cookie_session (line 4016): %s", _ignored_exc)
    except cookie_errors as e:
        logger.debug(f"browser_cookie3 failed ({browser}): {e}")

    # Manual Chrome SQLite fallback (Windows only)
    if browser.lower() == "chrome" and sys.platform == "win32":
        try:
            import shutil as _sh
            import sqlite3 as _sq
            import tempfile as _tf
            local = os.environ.get("LOCALAPPDATA", "")
            db_src = pathlib.Path(local) / "Google/Chrome/User Data/Default/Network/Cookies"
            if not db_src.exists():
                db_src = pathlib.Path(local) / "Google/Chrome/User Data/Default/Cookies"
            if db_src.exists():
                # NamedTemporaryFile avoids the race-prone/deprecated mktemp().
                # Close the handle before copy/connect so this also works on Windows.
                with _tf.NamedTemporaryFile(suffix=".db", delete=False) as tmp_handle:
                    tmp = tmp_handle.name
                try:
                    _sh.copy2(db_src, tmp)
                    with _sq.connect(tmp) as conn:
                        rows = conn.execute(
                            "SELECT host_key, name, value FROM cookies"
                        ).fetchall()
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError as cleanup_exc:
                        logger.debug(f"Chrome cookie temp cleanup failed: {cleanup_exc}")
                # The legacy plaintext `value` column is empty for modern
                # Chromium profiles.  Do not return a misleading session;
                # encrypted_value must be decrypted by the browser/yt-dlp.
                valid_rows = [row for row in rows if str(row[2] or "")]
                if valid_rows:
                    s = create_retry_session()
                    for host, name, value in valid_rows:
                        s.cookies.set(name, value, domain=host.lstrip("."))
                    logger.debug(f"Cookie session: Chrome SQLite ({len(valid_rows)} cookies)")
                    return s
                logger.debug("Cookie session: Chrome SQLite contains no plaintext cookie values")
        except (OSError, _sq.DatabaseError, TypeError, ValueError) as e:
            logger.debug(f"Chrome SQLite cookie fallback failed: {e}")
    return None

def _looks_like_error_document(content: bytes, content_type: str = "") -> bool:
    """Identify HTML/JSON error pages returned by an asset URL."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in {"text/html", "application/xhtml+xml", "application/json"}:
        return True
    prefix = bytes(content or b"")[:512].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"{\"error"))


def _fetch_headless(url: str, reject_error_documents: bool = False, *,
                    browser_session=None, spool_dir=None) -> bytes | FilePayload | None:
    """
    Fetch URL using Playwright (preferred) or Selenium as fallback.
    Used when normal HTTP fetch fails or returns <1KB content for images.
    Returns raw bytes or None.
    """
    if browser_session is not None:
        return browser_session.fetch_headless(url, reject_error_documents=reject_error_documents,
                                              spool_dir=spool_dir)
    _raise_if_cancelled()
    if not vpn_requirement_satisfied():
        logger.error("VPN guard blocked headless browser fetch: %s", url)
        return None
    proxy_config = _get_browser_proxy_config(url)
    if proxy_config is None:
        logger.error("Manual proxy transport is unsupported by browser fallback: %s", url)
        return None
    # ── Try Playwright first ──────────────────────────────────────────
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            launch_kwargs = {"headless": True, "args": ["--no-sandbox"]}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            browser = pw.chromium.launch(**launch_kwargs)
            # try/finally so the launched Chromium process is
            # always closed. Previously, if page.goto()/resp.body() raised (the
            # common case that triggers this headless fallback in the first
            # place), browser.close() was skipped and the browser process leaked.
            # The `with sync_playwright()` block closes the driver, not the
            # launched browser. The Selenium path below already did this right.
            try:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    ignore_https_errors=False,
                )
                page = ctx.new_page()
                resp = page.goto(url, wait_until="networkidle", timeout=30_000)
                _raise_if_cancelled()
                if resp and resp.ok:
                    # For images, get raw bytes from response body
                    content = resp.body()
                    _raise_if_cancelled()
                    if reject_error_documents and _looks_like_error_document(
                        content, resp.headers.get("content-type", "")
                    ):
                        logger.warning(f"  [Headless/Playwright] rejected error document: {url}")
                        return None
                    logger.info(f"  [Headless/Playwright] {url} → {len(content)} bytes")
                    return content
            finally:
                try:
                    browser.close()
                except (OSError, PlaywrightError, RuntimeError) as _ignored_close:
                    logger.debug("Ignored Playwright browser-close exception: %s", _ignored_close)
    except DownloadCancelledError:
        raise
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _fetch_headless (line 4072): %s", _ignored_exc)
    except (OSError, PlaywrightError, RuntimeError, TypeError, ValueError) as e:
        logger.debug(f"Playwright fetch failed ({url}): {e}")

    return _fetch_selenium(url, proxy_config, reject_error_documents)


def _fetch_selenium(url, proxy_config, reject_error_documents):
    """Compatibility backend; the caller owns serialization and file spooling."""
    _raise_if_cancelled()
    try:
        from selenium import webdriver
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--log-level=3")
        if proxy_config:
            opts.add_argument(f"--proxy-server={proxy_config['server']}")
        import base64 as _b64
        drv = webdriver.Chrome(options=opts)
        try:
            drv.set_page_load_timeout(30)
            drv.get(url)  # establish origin/cookies; may also solve simple JS checks
            _raise_if_cancelled()
            drv.set_script_timeout(30)
            resp_info = drv.execute_async_script(
                """
                const cb = arguments[arguments.length - 1];
                (async () => {
                  const r = await fetch(arguments[0], {credentials: 'include'});
                  const buf = await r.arrayBuffer();
                  const bytes = new Uint8Array(buf);
                  let binary = '';
                  for (let b of bytes) binary += String.fromCharCode(b);
                  return {ok: r.ok, status: r.status,
                          contentType: r.headers.get('content-type') || '',
                          data: btoa(binary)};
                })().then(cb).catch(() => cb(null));
                """,
                url,
            )
        finally:
            try:
                drv.quit()
            except (OSError, RuntimeError, WebDriverException, Urllib3HTTPError) as _ignored_exc:
                logger.debug("Ignored recoverable exception in _fetch_headless (line 4116): %s", _ignored_exc)
        _raise_if_cancelled()
        if resp_info and resp_info.get("ok") and resp_info.get("data"):
            data = _b64.b64decode(resp_info["data"])
            if reject_error_documents and _looks_like_error_document(
                data, resp_info.get("contentType", "")
            ):
                logger.warning(
                    f"  [Headless/Selenium] rejected HTTP {resp_info.get('status') or '?'} "
                    f"error document: {url}"
                )
                return None
            logger.info(f"  [Headless/Selenium] {url} → {len(data)} bytes")
            return data
    except DownloadCancelledError:
        raise
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _fetch_headless (line 4122): %s", _ignored_exc)
    except (OSError, RuntimeError, TypeError, ValueError, WebDriverException, Urllib3HTTPError) as e:
        logger.debug(f"Selenium fetch failed ({url}): {e}")

    return None

__all__ = [
    "BrowserFetchResult",
    "BrowserFetchSession",
    "_fetch_headless",
    "_make_cookie_session",
]
