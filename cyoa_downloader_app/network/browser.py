"""Browser cookie/headless fetch helpers extracted from legacy.py."""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Optional

import requests

from ..logging_setup import logger
from .sessions import create_retry_session

def _make_cookie_session(browser: str = "chrome") -> Optional["requests.Session"]:
    """
    Build a requests.Session with cookies from an installed browser.
    Uses browser-cookie3 if available, falls back to Chrome SQLite directly.
    """
    try:
        import browser_cookie3 as _bc
        loaders = {
            "chrome":   _bc.chrome,  "chromium": _bc.chromium,
            "firefox":  _bc.firefox, "edge":     _bc.edge,
            "brave":    _bc.brave,   "opera":    _bc.opera,
            "safari":   _bc.safari,
        }
        loader = loaders.get(browser.lower())
        if loader is None: return None
        jar = loader()
        s = create_retry_session()
        s.cookies.update(jar)
        logger.debug(f"Cookie session: loaded from {browser} ({len(jar)} cookies)")
        return s
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _make_cookie_session (line 4016): %s", _ignored_exc)
    except Exception as e:
        logger.debug(f"browser_cookie3 failed ({browser}): {e}")

    # Manual Chrome SQLite fallback (Windows only)
    if browser.lower() == "chrome" and sys.platform == "win32":
        try:
            import sqlite3 as _sq, shutil as _sh, tempfile as _tf
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
                s = create_retry_session()
                for host, name, value in rows:
                    s.cookies.set(name, value, domain=host.lstrip("."))
                logger.debug(f"Cookie session: Chrome SQLite ({len(rows)} cookies)")
                return s
        except Exception as e:
            logger.debug(f"Chrome SQLite cookie fallback failed: {e}")
    return None

def _fetch_headless(url: str) -> Optional[bytes]:
    """
    Fetch URL using Playwright (preferred) or Selenium as fallback.
    Used when normal HTTP fetch fails or returns <1KB content for images.
    Returns raw bytes or None.
    """
    # ── Try Playwright first ──────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            # try/finally so the launched Chromium process is
            # always closed. Previously, if page.goto()/resp.body() raised (the
            # common case that triggers this headless fallback in the first
            # place), browser.close() was skipped and the browser process leaked.
            # The `with sync_playwright()` block closes the driver, not the
            # launched browser. The Selenium path below already did this right.
            try:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    ignore_https_errors=True,
                )
                page = ctx.new_page()
                resp = page.goto(url, wait_until="networkidle", timeout=30_000)
                if resp and resp.ok:
                    # For images, get raw bytes from response body
                    content = resp.body()
                    logger.info(f"  [Headless/Playwright] {url} → {len(content)} bytes")
                    return content
            finally:
                try:
                    browser.close()
                except Exception as _ignored_close:
                    logger.debug("Ignored Playwright browser-close exception: %s", _ignored_close)
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _fetch_headless (line 4072): %s", _ignored_exc)
    except Exception as e:
        logger.debug(f"Playwright fetch failed ({url}): {e}")

    # ── Selenium fallback ─────────────────────────────────────────────
    # v7.5.5 fix: previous implementation (a) launched Chrome twice — the first
    # instance only took an unused screenshot, (b) ran `return await ...` inside
    # execute_script, which is a SyntaxError in a non-async wrapper so the
    # fallback ALWAYS threw, and (c) fetched from an about:blank origin, which
    # CORS-blocks most CDNs. Now: single driver, navigate to the URL first
    # (same-origin fetch + correct referer/cookies), then execute_async_script.
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--log-level=3")
        import base64 as _b64
        drv = webdriver.Chrome(options=opts)
        try:
            drv.set_page_load_timeout(30)
            drv.get(url)  # establish origin/cookies; may also solve simple JS checks
            drv.set_script_timeout(30)
            resp_b64 = drv.execute_async_script(
                """
                const cb = arguments[arguments.length - 1];
                (async () => {
                  const r = await fetch(arguments[0], {credentials: 'include'});
                  const buf = await r.arrayBuffer();
                  const bytes = new Uint8Array(buf);
                  let binary = '';
                  for (let b of bytes) binary += String.fromCharCode(b);
                  return btoa(binary);
                })().then(cb).catch(() => cb(null));
                """,
                url,
            )
        finally:
            try:
                drv.quit()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _fetch_headless (line 4116): %s", _ignored_exc)
        if resp_b64:
            data = _b64.b64decode(resp_b64)
            logger.info(f"  [Headless/Selenium] {url} → {len(data)} bytes")
            return data
    except ImportError as _ignored_exc:
        logger.debug("Ignored recoverable exception in _fetch_headless (line 4122): %s", _ignored_exc)
    except Exception as e:
        logger.debug(f"Selenium fetch failed ({url}): {e}")

    return None

__all__ = ["_make_cookie_session", "_fetch_headless"]
