"""Playwright response discovery for assets created only at runtime."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
import re
from typing import Dict, Iterable, List
from urllib.parse import urlparse

from ..constants.assets import (
    AUDIO_EXTENSIONS, FONT_EXTENSIONS, IMAGE_EXTENSIONS, SCRIPT_EXTENSIONS,
    STYLE_EXTENSIONS, VIDEO_EXTENSIONS,
)
from ..logging_setup import logger
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from .proxy import _get_browser_proxy_config
from .vpn import vpn_requirement_satisfied


_RUNTIME_ASSET_EXTENSIONS = (
    IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | FONT_EXTENSIONS |
    SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | {".json", ".wasm", ".webmanifest"}
)
_RUNTIME_CONTENT_TYPES = {
    "text/css", "application/javascript", "text/javascript", "application/json",
    "application/wasm", "application/font-woff", "application/font-woff2",
}

_ARCHIVE_NOISE_HOSTS = {
    "www.googletagmanager.com", "googletagmanager.com",
    "www.google-analytics.com", "google-analytics.com",
    "stats.g.doubleclick.net", "cct.google", "vercel.live",
    "www.clarity.ms", "clarity.ms", "browser.sentry-cdn.com",
    "static.cloudflareinsights.com",
}


def _is_archive_noise_url(url: str) -> bool:
    """Return whether a runtime URL is telemetry/challenge infrastructure.

    These resources are injected by hosting/analytics providers, not required
    by the archived story. Keeping Cloudflare challenge scripts in particular
    creates hidden iframes and POST errors when the mirror runs on localhost.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
    except (TypeError, ValueError):
        return False
    return (
        host in _ARCHIVE_NOISE_HOSTS
        or host.endswith((".sentry.io", ".posthog.com"))
        or path.startswith("/cdn-cgi/challenge-platform/")
    )


def _is_runtime_asset_response(url: str, content_type: str, status: int = 200) -> bool:
    try:
        normalized_status = int(status)
    except (TypeError, ValueError, OverflowError):
        normalized_status = 0
    if normalized_status < 200 or normalized_status >= 400:
        return False
    if _is_archive_noise_url(url):
        return False
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized.startswith(("image/", "audio/", "video/", "font/")):
        return True
    if normalized in _RUNTIME_CONTENT_TYPES:
        return True
    try:
        return os.path.splitext(urlparse(url).path.lower())[1] in _RUNTIME_ASSET_EXTENSIONS
    except (TypeError, ValueError):
        return False


@dataclass
class RuntimeCaptureResult:
    discovered: List[str] = field(default_factory=list)
    downloaded: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    pages_rendered: int = 0
    scroll_steps: int = 0
    interactions_attempted: int = 0
    interactions_productive: int = 0
    blocked_requests: List[Dict[str, str]] = field(default_factory=list)
    stop_reason: str = ""


_SAFE_INTERACTION_RE = re.compile(
    r"\b(?:load|show|view|read)\s+more\b|\bnext\b|\bcontinue\b|\bexpand\b|"
    r"\breveal\b|\bopen\s+section\b|\bmuat\s+lagi\b|\btampilkan\b|"
    r"\bselanjutnya\b|\blanjut\b",
    re.IGNORECASE,
)
_UNSAFE_INTERACTION_RE = re.compile(
    r"\b(?:log\s*in|login|sign\s*in|sign\s*up|register|submit|send|report|"
    r"like|upvote|downvote|pay|buy|purchase|donate|subscribe|checkout|delete|"
    r"logout|share|upload|comment|patreon|boosty)\b",
    re.IGNORECASE,
)


def _is_safe_interaction_label(
    text: str,
    *,
    aria_expanded_false: bool = False,
    in_form: bool = False,
    input_type: str = "button",
) -> bool:
    """Conservative allowlist used by Browser/Auto safe interaction mode."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if in_form or str(input_type or "").lower() in {"submit", "reset", "image"}:
        return False
    if _UNSAFE_INTERACTION_RE.search(value):
        return False
    return bool(aria_expanded_false or _SAFE_INTERACTION_RE.search(value))


def _incremental_scroll(page, settle_time_ms: int, max_steps: int) -> int:
    """Sweep through each viewport so IntersectionObserver lazy loads fire."""
    _raise_if_cancelled()
    steps = 0
    delay = max(80, min(300, int(settle_time_ms) // 6))
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        return 0
    while steps < max(1, int(max_steps)):
        _raise_if_cancelled()
        try:
            state = page.evaluate(
                """() => { const s=document.scrollingElement||document.documentElement; return {
                top:s.scrollTop, height:s.scrollHeight, viewport:window.innerHeight||s.clientHeight}; }"""
            )
            top = float(state.get("top") or 0)
            height = float(state.get("height") or 0)
            viewport = max(1.0, float(state.get("viewport") or 1))
            if top + viewport >= height - 2:
                break
            page.evaluate("step => window.scrollBy(0, step)", max(240, int(viewport * 0.8)))
            steps += 1
            page.wait_for_timeout(delay)
        except DownloadCancelledError:
            raise
        except Exception:
            break
    try:
        page.wait_for_timeout(max(250, int(settle_time_ms) // 2))
        # A return sweep triggers virtualized components that were removed while
        # moving downward, without requiring unsafe clicks.
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(max(120, delay))
    except Exception:
        pass
    return steps


def _candidate_snapshot(page) -> List[Dict[str, object]]:
    try:
        return list(page.evaluate(
            r"""() => { let n=0; return Array.from(document.querySelectorAll(
            'button,[role="button"],summary,input[type="button"]'))
            .filter(el => { const r=el.getBoundingClientRect(); return !el.disabled &&
                r.width>0 && r.height>0 && !el.closest('form') &&
                !el.hasAttribute('data-cyoa-auto-clicked'); })
            .slice(0,100).map(el => { const id='c'+(++n); el.setAttribute('data-cyoa-auto-id',id);
                return {id, text:(el.innerText||el.value||el.getAttribute('aria-label')||
                    el.getAttribute('title')||'').replace(/\s+/g,' ').trim().slice(0,160),
                    expanded:el.getAttribute('aria-expanded')==='false',
                    type:(el.getAttribute('type')||'button').toLowerCase()}; }); }"""
        ))
    except Exception:
        return []


def _dom_asset_score(page) -> int:
    try:
        return int(page.evaluate(
            """() => { const values=new Set(); document.querySelectorAll(
            'img,source,video,audio,[data-src],[data-lazy-src]').forEach(el => {
                ['src','srcset','data-src','data-lazy-src','poster'].forEach(k => {
                    const v=el.getAttribute&&el.getAttribute(k); if(v) values.add(v); }); });
                return values.size; }"""
        ))
    except Exception:
        return 0


def _run_safe_interactions(
    page,
    observed: Dict[str, str],
    interaction_state: Dict[str, object],
    *,
    settle_time_ms: int,
    max_interactions: int,
    no_progress_rounds: int,
) -> tuple[int, int, str]:
    attempted = 0
    productive = 0
    stale_rounds = 0
    stop_reason = "no_safe_candidates"
    while attempted < max_interactions and stale_rounds < no_progress_rounds:
        _raise_if_cancelled()
        candidates = [
            item for item in _candidate_snapshot(page)
            if _is_safe_interaction_label(
                str(item.get("text") or ""),
                aria_expanded_false=bool(item.get("expanded")),
                input_type=str(item.get("type") or "button"),
            )
        ]
        if not candidates:
            break
        before = (len(observed), _dom_asset_score(page))
        clicked_this_round = 0
        interaction_state["active"] = True
        try:
            for item in candidates[: min(5, max_interactions - attempted)]:
                _raise_if_cancelled()
                locator = page.locator(f'[data-cyoa-auto-id="{item["id"]}"]')
                if locator.count() != 1:
                    continue
                try:
                    locator.evaluate("el => el.setAttribute('data-cyoa-auto-clicked','1')")
                    locator.click(timeout=2500)
                    attempted += 1
                    clicked_this_round += 1
                    page.wait_for_timeout(max(120, min(500, settle_time_ms // 4)))
                except DownloadCancelledError:
                    raise
                except Exception:
                    continue
        finally:
            interaction_state["active"] = False
        if not clicked_this_round:
            stop_reason = "safe_candidates_not_actionable"
            break
        after = (len(observed), _dom_asset_score(page))
        if after[0] > before[0] or after[1] > before[1]:
            productive += clicked_this_round
            stale_rounds = 0
            stop_reason = "interaction_limit" if attempted >= max_interactions else "no_more_safe_candidates"
        else:
            stale_rounds += 1
            stop_reason = "no_progress"
    return attempted, productive, stop_reason


def capture_runtime_assets(
    downloader,
    page_urls: Iterable[str],
    settle_time_ms: int = 1800,
    *,
    capture_interactions: bool = False,
    max_scroll_steps: int = 100,
    max_interactions: int = 20,
    no_progress_rounds: int = 2,
) -> RuntimeCaptureResult:
    """Render runtime assets, safely bridging callers that run on asyncio.

    Playwright's synchronous API refuses to start on a thread whose asyncio
    loop is already running. The downloader API is intentionally synchronous,
    so in that situation run the complete Playwright lifecycle and subsequent
    asset localization in one dedicated worker thread. No Playwright object is
    passed between threads.
    """
    urls = tuple(page_urls)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _capture_runtime_assets_sync(
            downloader,
            urls,
            settle_time_ms,
            capture_interactions=capture_interactions,
            max_scroll_steps=max_scroll_steps,
            max_interactions=max_interactions,
            no_progress_rounds=no_progress_rounds,
        )

    logger.debug(
        "Running synchronous Playwright capture on a worker thread because "
        "the caller has an active asyncio loop."
    )
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cyoa-runtime-browser") as pool:
        return pool.submit(
            _capture_runtime_assets_sync,
            downloader,
            urls,
            settle_time_ms,
            capture_interactions=capture_interactions,
            max_scroll_steps=max_scroll_steps,
            max_interactions=max_interactions,
            no_progress_rounds=no_progress_rounds,
        ).result()


def _capture_runtime_assets_sync(
    downloader,
    page_urls: Iterable[str],
    settle_time_ms: int = 1800,
    *,
    capture_interactions: bool = False,
    max_scroll_steps: int = 100,
    max_interactions: int = 20,
    no_progress_rounds: int = 2,
) -> RuntimeCaptureResult:
    """Render routes and feed observed asset responses into the normal mirror."""
    _raise_if_cancelled()
    result = RuntimeCaptureResult()
    if not vpn_requirement_satisfied():
        logger.error(
            "VPN guard blocked runtime browser capture; required interface not detected"
        )
        return result
    observed: Dict[str, str] = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Browser archive mode needs Playwright; continuing with Smart route crawl only.")
        return result

    urls = list(dict.fromkeys(page_urls))
    proxy_config = _get_browser_proxy_config(urls[0] if urls else "")
    if proxy_config is None:
        logger.error(
            "Manual proxy transport is unsupported by runtime browser capture"
        )
        return result
    try:
        with sync_playwright() as pw:
            launch_kwargs = {"headless": True, "args": ["--no-sandbox"]}
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    ignore_https_errors=True,
                )
                page = context.new_page()
                interaction_state: Dict[str, object] = {"active": False, "page_url": ""}

                def route_request(route, request) -> None:
                    try:
                        if not interaction_state.get("active"):
                            route.continue_()
                            return
                        method = str(request.method or "GET").upper()
                        blocked_reason = ""
                        if method not in {"GET", "HEAD"}:
                            blocked_reason = "state-changing method"
                        else:
                            is_navigation = request.is_navigation_request()
                            current = str(interaction_state.get("page_url") or "")
                            if is_navigation and current:
                                requested = urlparse(request.url)
                                active = urlparse(current)
                                if (requested.scheme, requested.netloc, requested.path, requested.query) != (
                                    active.scheme, active.netloc, active.path, active.query,
                                ):
                                    blocked_reason = "navigation during safe interaction"
                        if blocked_reason:
                            result.blocked_requests.append({
                                "method": method, "url": request.url, "reason": blocked_reason,
                            })
                            route.abort()
                        else:
                            route.continue_()
                    except Exception:
                        try:
                            route.abort()
                        except Exception:
                            pass

                context.route("**/*", route_request)
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.on("popup", lambda popup: popup.close())

                def on_response(response) -> None:
                    try:
                        content_type = (response.headers.get("content-type", "").split(";", 1)[0].lower())
                        if _is_runtime_asset_response(
                            response.url, content_type, response.status,
                        ):
                            observed.setdefault(response.url, content_type)
                    except Exception:
                        pass

                page.on("response", on_response)
                for url in urls:
                    _raise_if_cancelled()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                        interaction_state["page_url"] = page.url
                        page.wait_for_timeout(settle_time_ms)
                        result.pages_rendered += 1
                        result.scroll_steps += _incremental_scroll(
                            page, settle_time_ms, max_scroll_steps,
                        )
                        if capture_interactions and max_interactions > 0:
                            attempted, productive, reason = _run_safe_interactions(
                                page, observed, interaction_state,
                                settle_time_ms=settle_time_ms,
                                max_interactions=max_interactions,
                                no_progress_rounds=max(1, no_progress_rounds),
                            )
                            result.interactions_attempted += attempted
                            result.interactions_productive += productive
                            result.stop_reason = reason
                            if productive:
                                result.scroll_steps += _incremental_scroll(
                                    page, settle_time_ms, max_scroll_steps,
                                )
                    except DownloadCancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("Runtime capture page failed (%s): %s", url, exc)
            finally:
                browser.close()
    except DownloadCancelledError:
        raise
    except Exception as exc:
        logger.warning("Runtime browser capture unavailable: %s", exc)
        return result

    result.discovered = list(observed)
    for url, content_type in observed.items():
        _raise_if_cancelled()
        kind = downloader._kind_from(url, content_type=content_type)
        local = downloader.download_asset(url, preferred_kind=kind)
        if local:
            result.downloaded.append(url)
        else:
            result.failed.append(url)
    logger.info(
        "Runtime capture: %d observed, %d downloaded, %d failed, %d scroll step(s), "
        "%d/%d productive interaction(s)",
        len(result.discovered), len(result.downloaded), len(result.failed), result.scroll_steps,
        result.interactions_productive, result.interactions_attempted,
    )
    return result
