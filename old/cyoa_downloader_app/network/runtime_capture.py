"""Playwright response discovery for assets created only at runtime."""

from __future__ import annotations

import asyncio
import os
import re
from collections import deque
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from ..constants.assets import (
    AUDIO_EXTENSIONS,
    FONT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SCRIPT_EXTENSIONS,
    STYLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from .browser import _browser_session_state
from .browser_owner import _external_frozen_chromium
from .captured_responses import CapturedResponseStore
from .proxy import _get_browser_proxy_config
from .runtime_waits import wait_for_runtime
from .vpn import vpn_requirement_satisfied

_RUNTIME_ASSET_EXTENSIONS = (
    IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | FONT_EXTENSIONS |
    SCRIPT_EXTENSIONS | STYLE_EXTENSIONS | {".json", ".hjson", ".wasm", ".webmanifest"}
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


@lru_cache(maxsize=1)
def _playwright_runtime_errors() -> tuple[type[BaseException], ...]:
    """Return the optional browser transport's recoverable exception types."""
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError:
        return (OSError, RuntimeError, TypeError, ValueError)
    return (OSError, PlaywrightError, RuntimeError, TypeError, ValueError)


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
    discovered: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    pages_rendered: int = 0
    scroll_steps: int = 0
    interactions_attempted: int = 0
    interactions_productive: int = 0
    blocked_requests: list[dict[str, str]] = field(default_factory=list)
    stop_reason: str = ""
    captured_responses: int = 0
    captured_bytes: int = 0
    reused_responses: int = 0
    reused_bytes: int = 0


_SAFE_INTERACTION_RE = re.compile(
    r"\b(?:load|show|view|read)\s+more\b|\bnext\b|\bcontinue\b|\bexpand\b|"
    r"\breveal\b|\bopen\s+section\b|\bmuat\s+lagi\b|\btampilkan\b|"
    r"\bselanjutnya\b|\blanjut\b|\b(?:open|expand|show)\s+all\b|"
    r"\b(?:begin|start|enter|play)\s+(?:(?:your|the|this|a|new)\s+)?(?:game|story|adventure|cyoa)\b|"
    r"\bshow\s+content\b|^(?:begin|start|enter|confirm|i understand)[.!\s]*$",
    re.IGNORECASE,
)
_UNSAFE_INTERACTION_RE = re.compile(
    r"\b(?:log\s*in|login|sign\s*in|sign\s*up|register|submit|send|report|"
    r"like|upvote|downvote|pay|buy|purchase|donate|subscribe|checkout|delete|"
    r"logout|share|upload|comment|patreon|boosty|reset|restart|clear|remove)\b|\bstart\s+over\b",
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


def _incremental_scroll(page, settle_time_ms: int, max_steps: int, *, after_poll=None) -> int:
    """Sweep through each viewport so IntersectionObserver lazy loads fire."""
    _raise_if_cancelled()
    steps = 0
    delay = max(80, min(300, int(settle_time_ms) // 6))
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except _playwright_runtime_errors():
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
            wait_for_runtime(page, delay, after_poll=after_poll)
        except DownloadCancelledError:
            raise
        except _playwright_runtime_errors():
            break
    if not steps:
        return 0
    try:
        wait_for_runtime(page, max(250, int(settle_time_ms) // 2), after_poll=after_poll)
        # A return sweep triggers virtualized components that were removed while
        # moving downward, without requiring unsafe clicks.
        page.evaluate("window.scrollTo(0, 0)")
        wait_for_runtime(page, max(120, delay), after_poll=after_poll)
    except DownloadCancelledError:
        raise
    except _playwright_runtime_errors() as exc:
        logger.debug("Runtime scroll reset failed: %s", exc)
    return steps


def _candidate_snapshot(page) -> list[dict[str, object]]:
    try:
        return list(page.evaluate(
            r"""() => { let n=0; return Array.from(document.querySelectorAll(
            'button,[role="button"],summary,input[type="button"]'))
            .filter(el => { const r=el.getBoundingClientRect(); return !el.disabled &&
                r.width>0 && r.height>0 && !el.closest('form') &&
                el.getAttribute('data-cyoa-auto-clicked') !==
                  (el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'')+
                  '|'+el.getAttribute('aria-expanded'); })
            .slice(0,1000).map(el => { const id='c'+(++n); el.setAttribute('data-cyoa-auto-id',id);
                return {id, text:(el.innerText||el.value||el.getAttribute('aria-label')||
                    el.getAttribute('title')||'').replace(/\s+/g,' ').trim().slice(0,160),
                    expanded:el.getAttribute('aria-expanded')==='false',
                    type:(el.getAttribute('type')||'button').toLowerCase(),
                    signature:(el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'')+
                      '|'+el.getAttribute('aria-expanded')}; }); }"""
        ))
    except _playwright_runtime_errors():
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
    except _playwright_runtime_errors():
        return 0


def _dom_progress_signature(page) -> str:
    """Recognize staged gates even when a click has not requested assets yet."""
    try:
        return str(page.evaluate(
            """() => JSON.stringify([location.href, document.body?.innerText.length || 0,
            Array.from(document.querySelectorAll('button,[role="button"],summary')).filter(el => {
            const r=el.getBoundingClientRect(); return r.width>0 && r.height>0;
            }).slice(0,1000).map(el => [el.id,el.innerText,el.getAttribute('aria-expanded')])])"""
        ))
    except _playwright_runtime_errors():
        return ""


def _wait_for_interaction_progress(
    page,
    observed: dict[str, str],
    interaction_state: dict[str, object],
    before: tuple[int, str],
    budget_ms: int,
) -> None:
    """Yield once a changed gate is stable and its requests have finished.

    Unchanged/delayed gates retain the full settling budget. Poll through
    Playwright so response handlers and cancellation keep running meanwhile.
    """
    elapsed = 0
    stable_ms = 0
    previous = before
    budget_ms = max(250, min(1800, int(budget_ms)))
    while elapsed < budget_ms:
        _raise_if_cancelled()
        delay = min(100, budget_ms - elapsed)
        wait_for_runtime(page, delay, after_poll=interaction_state.get("after_poll"))
        elapsed += delay
        current = (len(observed), _dom_progress_signature(page))
        if current != before and current == previous and not interaction_state.get("pending"):
            stable_ms += delay
            if stable_ms >= 300 and any(
                _is_safe_interaction_label(
                    str(item.get("text") or ""),
                    aria_expanded_false=bool(item.get("expanded")),
                    input_type=str(item.get("type") or "button"),
                )
                for item in _candidate_snapshot(page)
            ):
                return
        else:
            stable_ms = 0
        previous = current


def _run_safe_interactions(
    page,
    observed: dict[str, str],
    interaction_state: dict[str, object],
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
        before = (len(observed), _dom_asset_score(page), _dom_progress_signature(page))
        clicked_this_round = 0
        interaction_state["active"] = True
        interaction_state["armed"] = True
        try:
            # Re-snapshot after every activation: a gate may replace all other
            # controls and invalidate the remainder of the previous snapshot.
            for item in candidates[:1]:
                _raise_if_cancelled()
                locator = page.locator(f'[data-cyoa-auto-id="{item["id"]}"]')
                if locator.count() != 1:
                    continue
                try:
                    locator.evaluate("(el, signature) => el.setAttribute('data-cyoa-auto-clicked',signature)",
                                     str(item.get("signature") or ""))
                    locator.click(timeout=2500)
                    attempted += 1
                    clicked_this_round += 1
                    _wait_for_interaction_progress(
                        page, observed, interaction_state, (before[0], before[2]), settle_time_ms,
                    )
                except DownloadCancelledError:
                    raise
                except _playwright_runtime_errors() as exc:
                    logger.debug("Runtime auto-click candidate failed: %s", exc)
                    continue
        finally:
            interaction_state["active"] = False
        if not clicked_this_round:
            stop_reason = "safe_candidates_not_actionable"
            break
        after = (len(observed), _dom_asset_score(page), _dom_progress_signature(page))
        if after[0] > before[0] or after[1] > before[1] or after[2] != before[2]:
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
    options = {"capture_interactions": capture_interactions, "max_scroll_steps": max_scroll_steps,
               "max_interactions": max_interactions, "no_progress_rounds": no_progress_rounds}
    _raise_if_cancelled()
    if not (callable(getattr(downloader, "use_runtime_responses", None)) and
            callable(getattr(downloader, "needs_runtime_response", None))):
        return _capture_runtime_assets_session(downloader, page_urls, settle_time_ms, **options)
    directory = getattr(downloader, "output_folder", None)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with CapturedResponseStore(directory) as store:
        result = _capture_runtime_assets_session(downloader, page_urls, settle_time_ms, response_store=store, **options)
        result.captured_responses, result.captured_bytes = store.captured_count, store.captured_bytes
        result.reused_responses, result.reused_bytes = store.reused_count, store.reused_bytes
        return result


def _capture_runtime_assets_session(
    downloader,
    page_urls: Iterable[str],
    settle_time_ms: int = 1800,
    *,
    capture_interactions: bool = False,
    max_scroll_steps: int = 100,
    max_interactions: int = 20,
    no_progress_rounds: int = 2,
    response_store=None,
) -> RuntimeCaptureResult:
    """Render routes and feed observed asset responses into the normal mirror."""
    _raise_if_cancelled()
    result = RuntimeCaptureResult()
    if not vpn_requirement_satisfied():
        logger.error(
            "VPN guard blocked runtime browser capture; required interface not detected"
        )
        return result
    observed: dict[str, str] = {}
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
            external_browser = _external_frozen_chromium(pw.chromium)
            if external_browser:
                launch_kwargs["executable_path"] = external_browser
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config
            browser = pw.chromium.launch(**launch_kwargs)
            pending = set()
            completed_responses = deque()
            queued_responses = set()
            try:
                session_agent, session_cookies = _browser_session_state(urls)
                context = browser.new_context(
                    user_agent=session_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    ignore_https_errors=False,
                )
                if session_cookies:
                    context.add_cookies(session_cookies)
                page = context.new_page()
                interaction_state: dict[str, object] = {"active": False, "page_url": "", "pending": pending}
                def request_started(request):
                    # The archive already excludes these resources. Their
                    # long polls must not keep a ready story gate waiting.
                    if not _is_archive_noise_url(request.url):
                        pending.add(request)
                page.on("request", request_started)
                page.on("requestfinished", lambda request: pending.discard(request))
                page.on("requestfailed", lambda request: pending.discard(request))

                def route_request(route, request) -> None:
                    try:
                        if not (interaction_state.get("active") or interaction_state.get("armed")):
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
                    except _playwright_runtime_errors() as exc:
                        logger.debug("Runtime request routing failed: %s", exc)
                        try:
                            route.abort()
                        except _playwright_runtime_errors() as abort_exc:
                            logger.debug("Runtime request abort failed: %s", abort_exc)

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
                    except _playwright_runtime_errors() as exc:
                        logger.debug("Runtime response observation failed: %s", exc)

                page.on("response", on_response)
                def retain_finished(request):
                    if response_store is None:
                        return
                    try:
                        response = request.response()
                        if (response is not None and _is_runtime_asset_response(
                                response.url, response.headers.get("content-type", ""), response.status) and
                                downloader.needs_runtime_response(response.url) and response.url not in queued_responses and
                                len(completed_responses) < response_store.max_entries):
                            completed_responses.append(response)
                            queued_responses.add(response.url)
                    except DownloadCancelledError:
                        raise
                    except _playwright_runtime_errors() as exc:
                        logger.debug("Runtime response reuse unavailable: %s", exc)
                def drain_responses():
                    while completed_responses:
                        _raise_if_cancelled()
                        response = completed_responses.popleft()
                        queued_responses.discard(response.url)
                        try:
                            response_store.capture(response)
                        except DownloadCancelledError:
                            raise
                        except _playwright_runtime_errors() as exc:
                            logger.debug("Runtime response body unavailable: %s", exc)
                        response_store.raise_if_failed()
                page.on("requestfinished", retain_finished)
                interaction_state["after_poll"] = drain_responses
                for url in urls:
                    _raise_if_cancelled()
                    drain_responses()
                    # Navigation ends the previous route's settling phase.
                    # Late completion callbacks can still retain its bytes.
                    pending.clear()
                    try:
                        interaction_state["armed"] = False
                        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                        interaction_state["page_url"] = page.url
                        wait_for_runtime(page, settle_time_ms, after_poll=drain_responses)
                        drain_responses()
                        result.pages_rendered += 1
                        result.scroll_steps += _incremental_scroll(
                            page, settle_time_ms, max_scroll_steps, after_poll=drain_responses,
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
                                    page, settle_time_ms, max_scroll_steps, after_poll=drain_responses,
                                )
                        if response_store is not None:
                            drain_responses()
                    except DownloadCancelledError:
                        raise
                    except _playwright_runtime_errors() as exc:
                        logger.warning("Runtime capture page failed (%s): %s", url, exc)
                drain_responses()
            finally:
                try:
                    browser.close()
                except DownloadCancelledError:
                    raise
                except _playwright_runtime_errors() as exc:
                    logger.debug("Runtime browser close failed: %s", exc)
                finally:
                    # Do not retain Request/Page handles during subsequent
                    # native mirroring, even if a page left long polls open.
                    pending.clear()
                    completed_responses.clear()
                    queued_responses.clear()
                    page = context = browser = None
    except DownloadCancelledError:
        raise
    except _playwright_runtime_errors() as exc:
        logger.warning("Runtime browser capture unavailable: %s", exc)

    result.discovered = list(observed)
    if response_store is not None:
        response_store.raise_if_failed()
    with (downloader.use_runtime_responses(response_store) if response_store is not None else nullcontext()):
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
