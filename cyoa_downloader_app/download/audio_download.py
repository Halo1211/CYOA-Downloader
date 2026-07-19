"""yt-dlp audio download helpers moved out of legacy.py.

The GUI/CLI still set transitional globals on ``legacy.py``. This module reads
those values lazily so the public behavior and callback wiring remain unchanged.
"""

from __future__ import annotations

import os
import glob
import shutil
import sys
from typing import Dict, List, Optional, Tuple

from ..logging_setup import logger
from .audio_reports import _find_ffmpeg, _write_youtube_skip_log


_COOKIE_DATABASE_LOCK_MARKERS = (
    "could not copy chrome cookie database",
    "could not copy chromium cookie database",
    "permission denied",
    "access is denied",
)


def _is_cookie_database_lock_error(error: Optional[str]) -> bool:
    """Return whether yt-dlp failed before downloading because a cookie DB is locked."""
    text = str(error or "").lower()
    return "cookie database" in text and any(marker in text for marker in _COOKIE_DATABASE_LOCK_MARKERS)


def _summarize_ytdlp_error(error: Optional[str]) -> str:
    """Turn noisy yt-dlp/browser-cookie errors into an actionable report reason."""
    if _is_cookie_database_lock_error(error):
        return (
            "browser cookie database is locked; close Chrome/Edge/Brave completely "
            "and retry, or select an exported Netscape cookies.txt in YouTube cookies"
        )
    text = " ".join((str(error or "no output file")).split())
    lowered = text.lower()
    if "no supported javascript runtime" in lowered:
        return (
            "YouTube now requires a JavaScript runtime for audio extraction; "
            "install/update yt-dlp[default], install Deno, and restart CYOA Downloader"
        )
    if "signature solving failed" in lowered or "challenge solving failed" in lowered:
        return (
            "YouTube format signatures could not be solved; install/update Deno "
            "and yt-dlp, then retry"
        )
    if "provided youtube account cookies are no longer valid" in lowered:
        return (
            "YouTube rejected the exported cookies; log in again and export a "
            "fresh Netscape cookies.txt"
        )
    return text[:500]


def _legacy_module():
    import sys as _sys
    return _sys.modules.get("cyoa_downloader_app.runtime.surface")


def _yt_dlp_enabled() -> bool:
    mod = _legacy_module()
    return bool(getattr(mod, "_ytdlp_enabled", True))


def _yt_dlp_progress_cb():
    # Runtime state is the mutable source of truth after the refactor; keep
    # the surface fallback for compatibility with older callers/tests.
    import sys as _sys
    state = _sys.modules.get("cyoa_downloader_app.runtime.state")
    callback = getattr(state, "_ytdlp_gui_progress_cb", None)
    if callback:
        return callback
    mod = _legacy_module()
    return getattr(mod, "_ytdlp_gui_progress_cb", None)


def _make_ytdlp_hook(vid_id: str, idx: int, total: int):
    """Build a yt-dlp progress_hook that forwards to GUI callback."""
    def _hook(d: dict) -> None:
        if d.get("status") == "downloading":
            pct   = d.get("_percent_str", "?%").strip()
            speed = d.get("_speed_str",   "?B/s").strip()
            eta   = d.get("_eta_str",     "?").strip()
            import logging as _lg
            _lg.getLogger("cyoa_downloader").debug(
                f"  yt-dlp [{idx}/{total}] {vid_id} {pct} @ {speed} ETA {eta}")
            cb = _yt_dlp_progress_cb()
            if cb:
                try:
                    cb(vid_id, idx, total, pct, speed)
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _hook: %s", _ignored_exc)
    return _hook


def _yt_dlp_runtime_options() -> Dict[str, object]:
    """Return safe yt-dlp options for YouTube's current JS challenge flow.

    Recent yt-dlp releases need an external JavaScript runtime for full
    YouTube support.  Do not guess a runtime path: ``shutil.which`` keeps the
    setting portable and lets yt-dlp report a useful diagnostic when none is
    installed.  The official EJS scripts are allowed to be fetched only when
    the companion package is not installed.
    """
    def _runtime_path(name: str) -> Optional[str]:
        # GUI-launched processes do not always inherit the PATH that an
        # interactive PowerShell has.  Prefer an explicit override, then PATH,
        # then the per-user install locations used by the Windows installers.
        env_name = f"CYOA_YTDLP_{name.upper()}"
        candidates = [os.environ.get(env_name, ""), shutil.which(name)]
        if sys.platform == "win32":
            local = os.environ.get("LOCALAPPDATA", "")
            user = os.environ.get("USERPROFILE", os.path.expanduser("~"))
            if name == "deno":
                candidates.extend([
                    os.path.join(local, "Programs", "deno", "deno.exe"),
                    os.path.join(user, ".deno", "bin", "deno.exe"),
                ])
                candidates.extend(sorted(glob.glob(os.path.join(
                    local, "Microsoft", "WinGet", "Packages", "DenoLand.Deno_*",
                    "deno.exe"
                ))))
            elif name == "bun":
                candidates.append(os.path.join(user, ".bun", "bin", "bun.exe"))
            elif name == "node":
                candidates.append(os.path.join(local, "Programs", "nodejs", "node.exe"))

        path = next((os.path.abspath(os.path.expanduser(candidate))
                     for candidate in candidates
                     if candidate and os.path.isfile(os.path.expanduser(candidate))), None)
        if not path:
            return None
        if name == "node":
            # yt-dlp 2026+ requires Node 22 or newer; Node 20/21 must not be
            # advertised as available because yt-dlp will reject it.
            try:
                import subprocess
                output = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.STDOUT,
                    text=True, timeout=3,
                ).strip().lstrip("v")
                major = int(output.split(".", 1)[0])
                if major < 22:
                    return None
            except Exception:
                return None
        return path

    runtimes: Dict[str, Dict[str, str]] = {}
    for name in ("deno", "bun", "node", "qjs"):
        path = _runtime_path(name)
        if path:
            runtimes[name] = {"path": path}

    options: Dict[str, object] = {}
    if runtimes:
        options["js_runtimes"] = runtimes
        logger.debug(
            "yt-dlp: JavaScript runtime(s) enabled: %s",
            ", ".join(sorted(runtimes)),
        )
        try:
            import importlib.util
            has_ejs = importlib.util.find_spec("yt_dlp_ejs") is not None
        except Exception:
            has_ejs = False
        if not has_ejs:
            # This is the upstream-supported fallback for a plain PyPI
            # yt-dlp install.  It is not a CAPTCHA/anti-bot bypass.
            options["remote_components"] = {"ejs:github"}
    else:
        logger.warning(
            "yt-dlp: no JavaScript runtime found; install Deno and restart "
            "the downloader (yt-dlp[default] is recommended)"
        )
    return options


def _ytdlp_cookie_files(output_dir: str, log_dir: str) -> List[str]:
    """Find explicitly supplied Netscape cookie files without exposing them."""
    candidates = [
        os.environ.get("CYOA_YTDLP_COOKIES", ""),
        os.environ.get("YTDLP_COOKIES", ""),
        os.path.join(output_dir, "cookies.txt") if output_dir else "",
        os.path.join(log_dir, "cookies.txt") if log_dir else "",
    ]
    found: List[str] = []
    for candidate in candidates:
        path = os.path.abspath(os.path.expanduser(candidate)) if candidate else ""
        if path and os.path.isfile(path) and path not in found:
            found.append(path)
    return found


def _ytdlp_browser_profiles(browser: str) -> List[Optional[str]]:
    """Return installed Chromium/Firefox profiles worth trying.

    Native yt-dlp cookie loading supports a profile argument.  The old code
    only tried the default profile, which silently failed when YouTube was
    logged in under ``Profile 1`` or another profile.
    """
    if sys.platform != "win32":
        return [None]
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    roots = {
        "chrome": os.path.join(local, "Google", "Chrome", "User Data"),
        "edge": os.path.join(local, "Microsoft", "Edge", "User Data"),
        "brave": os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
        "chromium": os.path.join(local, "Chromium", "User Data"),
        "firefox": os.path.join(appdata, "Mozilla", "Firefox", "Profiles"),
    }
    root = roots.get(browser.lower(), "")
    if not root or not os.path.isdir(root):
        return []
    try:
        names = sorted(
            entry.name for entry in os.scandir(root)
            if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile "))
        )
    except OSError:
        names = []
    if browser.lower() == "firefox":
        return [entry for entry in names] or [None]
    return [None] + [entry for entry in names if entry != "Default"]


def _download_youtube_audio(
    youtube_urls: List[str],
    output_dir: str,
    source_url: str = "",
    log_dir: str = "",
) -> Dict[str, str]:
    """
    Download YouTube audio as MP3 using yt-dlp.
    output_dir : where to save audio files (may be temp folder)
    log_dir    : where to write skipped_youtube_audio.txt (should be real output dir)
    Returns dict: {youtube_url → local_path_relative_to_output_dir}
    """
    try:
        import yt_dlp  # noqa
        has_ytdlp = True
    except ImportError:
        has_ytdlp = False

    if not has_ytdlp or not _yt_dlp_enabled():
        if not has_ytdlp:
            logger.warning(
                f"{len(youtube_urls)} YouTube audio URL(s) — yt-dlp tidak terinstall.\n"
                f"  Install: pip install yt-dlp  (+ ffmpeg for MP3 conversion)"
            )
        else:
            logger.info(f"{len(youtube_urls)} YouTube audio URL(s) dilewati (YT Audio dimatikan).")
        _write_youtube_skip_log(youtube_urls, log_dir or output_dir, source_url=source_url)
        return {}

    audio_dir = os.path.join(output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    result: Dict[str, str] = {}
    failed: List[str]      = []
    failure_reasons: Dict[str, str] = {}

    logger.info(f"yt-dlp: Downloading {len(youtube_urls)} YouTube audio track(s)…")

    total = len(youtube_urls)
    for idx, yt_url in enumerate(youtube_urls, 1):
        # Sanitise URL
        url_clean = yt_url.strip()
        if not url_clean.startswith("http"):
            url_clean = f"https://www.youtube.com/watch?v={url_clean}"

        # Output template: audio/<video_id>.mp3
        try:
            import re as _re
            vid_m = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url_clean)
            vid_id = vid_m.group(1) if vid_m else "unknown"
        except Exception:
            vid_id = "unknown"

        if vid_id == "unknown":
            # yt-dlp also accepts SoundCloud and other media URLs.  A shared
            # ``unknown.mp3`` name caused different retry items to overwrite
            # one another and made project references ambiguous. Keep the
            # readable URL slug, plus a short URL hash for uniqueness.
            try:
                import hashlib as _hashlib
                from urllib.parse import urlparse as _urlparse
                _slug = _urlparse(url_clean).path.rstrip("/").rsplit("/", 1)[-1]
                _slug = _re.sub(r"[^A-Za-z0-9._-]+", "-", _slug).strip(".-_")[:64]
                _digest = _hashlib.sha1(url_clean.encode("utf-8")).hexdigest()[:8]
                vid_id = f"{_slug or 'audio'}-{_digest}"
            except Exception:
                vid_id = "audio"

        out_template = os.path.join(audio_dir, f"{vid_id}.%(ext)s")
        expected_mp3 = os.path.join(audio_dir, f"{vid_id}.mp3")
        rel_path     = f"audio/{vid_id}.mp3"

        # Already downloaded in a previous run — skip
        if os.path.exists(expected_mp3):
            logger.info(f"  yt-dlp: already exists — {rel_path}")
            result[yt_url] = rel_path
            continue

        ydl_opts = {
            "format":           "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl":          out_template,
            "postprocessors":   [{
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }],
            "quiet":            True,
            "no_warnings":      True,
            "extract_flat":     False,
            "retries":          5,
            "fragment_retries": 5,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "socket_timeout":   30,
            "continuedl":       True,
            "sleep_interval":   1,
            "max_sleep_interval": 3,
            # Avoid a stale hard-coded browser identity. yt-dlp supplies a
            # current standard header and browser-cookie attempts add only
            # the authenticated Cookie header.
            "http_headers": {},
            # Progress hook — update GUI status bar
            "progress_hooks": [_make_ytdlp_hook(vid_id, idx, total)],
        }

        ydl_opts.update(_yt_dlp_runtime_options())

        # Auto-locate ffmpeg — pass to yt-dlp if found; if not, yt-dlp
        # will still search its own PATH. Only warn if conversion later fails.
        ffmpeg_dir = _find_ffmpeg()
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir
            logger.debug(f"  ffmpeg: {ffmpeg_dir}")

        def _any_audio_exists() -> Optional[str]:
            """Return first audio file found for this vid_id, or None."""
            _exts = (".mp3", ".m4a", ".opus", ".webm", ".ogg", ".aac", ".wav")
            found = sorted([
                f for f in os.listdir(audio_dir)
                if f.startswith(vid_id) and f.lower().endswith(_exts)
            ])
            return found[0] if found else None

        def _try_ytdlp(opts: dict) -> Tuple[bool, Optional[str]]:
            """Run yt-dlp. Returns (success, error_str|None). Suppresses stderr output."""
            import yt_dlp, io
            try:
                # Redirect yt-dlp's own stderr to suppress noisy cookie errors
                # while still capturing the message for our own logic
                captured = io.StringIO()
                class _QuietLogger:
                    def debug(self, msg):   pass
                    def info(self, msg):     pass
                    def warning(self, msg): captured.write("WARNING: " + str(msg) + "\n")
                    def error(self, msg):
                        captured.write("ERROR: " + str(msg) + "\n")
                opts2 = {**opts, "logger": _QuietLogger()}
                with yt_dlp.YoutubeDL(opts2) as ydl:
                    ydl.download([url_clean])
                return True, None
            except Exception as e:
                err = str(e) + "\n" + captured.getvalue()
                return False, err

        def _is_bot_error(err: Optional[str]) -> bool:
            if not err: return False
            return any(p.lower() in err.lower() for p in [
                "Sign in to confirm", "bot", "confirm your age",
                "Private video", "Video unavailable", "HTTP Error 403",
                "This video is not available",
            ])

        def _try_with_cookie_file(browser: str, profile: Optional[str], opts: dict) -> Tuple[bool, Optional[str]]:
            """
            Try the browser's authenticated cookie store, not its HTTP asset
            cache. yt-dlp's native reader is first because it understands
            Chromium App-Bound Encryption. On Windows, an open Chromium
            browser can still deny the database copy; browser_cookie3 is the
            compatibility fallback for older Chromium profiles and Firefox.
            """
            # Native yt-dlp extraction is the reliable path for modern Chrome,
            # Edge, Brave, Chromium, and Firefox profiles. It also handles a
            # browser profile without making this downloader handle encrypted
            # Chromium SQLite values itself.
            cookie_source = (browser,) if profile is None else (browser, profile)
            native_ok, native_err = _try_ytdlp(
                {**opts, "cookiesfrombrowser": cookie_source}
            )
            if _any_audio_exists():
                return native_ok, native_err

            # browser_cookie3 can still decrypt older profiles and some
            # Firefox installations when the native extractor cannot. Only
            # pass non-empty cookies through the request header; an empty
            # browser jar is not an authenticated fallback.
            try:
                from ..network.browser import _make_cookie_session
                session = _make_cookie_session(browser)
                headers = dict(opts.get("http_headers") or {})
                if session is not None:
                    pairs = [
                        f"{cookie.name}={cookie.value}"
                        for cookie in session.cookies
                        if cookie.name and cookie.value
                    ]
                    if pairs:
                        headers["Cookie"] = "; ".join(pairs)
                        return _try_ytdlp({**opts, "http_headers": headers})
            except Exception as exc:
                logger.debug("Browser cookie session unavailable for %s: %s", browser, exc)
            return native_ok, native_err

        try:
            import yt_dlp

            # ── First attempt (no cookies) ─────────────────────────────
            opts = {k: v for k, v in ydl_opts.items() if k != "cookiesfrombrowser"}
            ok1, err1 = _try_ytdlp(opts)

            found_file = _any_audio_exists()
            last_error = err1

            manual_cookie_files = _ytdlp_cookie_files(output_dir, log_dir)
            if not found_file:
                # An explicitly exported cookie file is the least ambiguous
                # authenticated retry and also works when Chromium's profile
                # is locked by App-Bound Encryption.
                for cookie_file in manual_cookie_files:
                    logger.info("  yt-dlp: trying supplied browser cookie file")
                    ok_c, err_c = _try_ytdlp({**opts, "cookiefile": cookie_file})
                    last_error = err_c or last_error
                    found_file = _any_audio_exists()
                    if found_file:
                        logger.info("  yt-dlp: cookie file authentication succeeded")
                        break

            if not found_file and not manual_cookie_files:
                # Nothing downloaded — retry with installed browser cookies.
                # Do not probe browsers that have no local profile; that used
                # to create a long list of misleading cookie errors.
                browsers = ["chrome", "edge", "brave", "chromium", "firefox"]
                logger.info(
                    f"  yt-dlp: first attempt failed "
                    f"{'(bot-detected)' if _is_bot_error(err1) else '(unknown error)'},"
                    f" retrying with browser cookies…"
                )
                for browser in browsers:
                    profiles = _ytdlp_browser_profiles(browser)
                    for profile in profiles:
                        ok_c, err_c = _try_with_cookie_file(browser, profile, opts)
                        last_error = err_c or last_error
                        found_file = _any_audio_exists()
                        if found_file:
                            suffix = f" ({profile})" if profile else ""
                            logger.info(f"  yt-dlp: cookie source → {browser}{suffix}")
                            break
                        if _is_cookie_database_lock_error(err_c):
                            # Every Chromium profile uses the same browser-level
                            # locking behavior on Windows. Trying Profile 1,
                            # Profile 2, etc. only repeats the same noisy failure;
                            # continue with another browser or the manual file.
                            logger.debug(
                                "  yt-dlp: skipping remaining %s profiles because its cookie database is locked",
                                browser,
                            )
                            break
                        if ok_c:
                            # yt-dlp said OK but file still missing — next profile
                            continue
                    if found_file:
                        break
            elif not found_file and manual_cookie_files:
                # An explicitly selected cookie file is authoritative. If it
                # fails, do not fall back to Chrome and report a second,
                # unrelated cookie-database error. This also prevents a stale
                # automatic browser session from overriding the user's choice.
                logger.info(
                    "  yt-dlp: supplied cookies.txt did not produce a file; "
                    "automatic browser-cookie probing skipped"
                )

            if found_file:
                # ── Convert to MP3 if needed ───────────────────────────
                found_ext = os.path.splitext(found_file)[1].lower()
                if found_ext == ".mp3":
                    rel_path = f"audio/{found_file}"
                    result[yt_url] = rel_path
                    logger.info(f"  yt-dlp OK: {rel_path}")
                else:
                    ffmpeg_dir2 = _find_ffmpeg()
                    if sys.platform == "win32" and ffmpeg_dir2:
                        ffmpeg_exe = os.path.join(ffmpeg_dir2, "ffmpeg.exe")
                    elif ffmpeg_dir2:
                        ffmpeg_exe = os.path.join(ffmpeg_dir2, "ffmpeg")
                    else:
                        ffmpeg_exe = "ffmpeg"
                    src_path = os.path.join(audio_dir, found_file)
                    dst_path = os.path.join(audio_dir, f"{vid_id}.mp3")
                    try:
                        import subprocess as _sp
                        r2 = _sp.run(
                            [ffmpeg_exe, "-i", src_path, "-q:a", "2", "-y", dst_path],
                            capture_output=True, timeout=60,
                        )
                        if os.path.exists(dst_path):
                            os.remove(src_path)
                            rel_path = f"audio/{vid_id}.mp3"
                            result[yt_url] = rel_path
                            logger.info(f"  yt-dlp OK (converted {found_ext}→mp3): {rel_path}")
                        else:
                            rel_path = f"audio/{found_file}"
                            result[yt_url] = rel_path
                            logger.info(f"  yt-dlp OK (kept {found_ext}): {rel_path}")
                    except Exception:
                        rel_path = f"audio/{found_file}"
                        result[yt_url] = rel_path
                        logger.warning(
                            f"  ffmpeg conversion failed ({found_ext}→mp3): {found_file}\n"
                            "  Install ffmpeg: winget install Gyan.FFmpeg  (Windows)\n"
                            "                  brew install ffmpeg           (macOS)\n"
                            "                  sudo apt install ffmpeg       (Linux)"
                        )
            else:
                reason = _summarize_ytdlp_error(last_error)
                logger.warning(f"  yt-dlp: file not found after download: {url_clean} — {reason}")
                failed.append(yt_url)
                failure_reasons[yt_url] = reason
        except Exception as e:
            logger.error(f"  yt-dlp FAILED: {url_clean} — {e}")
            failed.append(yt_url)
            failure_reasons[yt_url] = str(e)[:500]

    if failed:
        _write_youtube_skip_log(
            failed, log_dir or output_dir, source_url=source_url,
            reasons=failure_reasons,
        )

    if result:
        logger.info(
            f"yt-dlp: {len(result)}/{len(youtube_urls)} track(s) downloaded. "
            f"Files in: {audio_dir}"
        )

    return result


__all__ = ["_make_ytdlp_hook", "_download_youtube_audio"]
