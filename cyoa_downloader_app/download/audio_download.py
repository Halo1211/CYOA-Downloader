"""yt-dlp audio download helpers moved out of legacy.py.

The GUI/CLI still set transitional globals on ``legacy.py``. This module reads
those values lazily so the public behavior and callback wiring remain unchanged.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

from ..logging_setup import logger
from .audio_reports import _find_ffmpeg, _write_youtube_skip_log


def _legacy_module():
    import sys as _sys
    return _sys.modules.get("cyoa_downloader_app.runtime.surface")


def _yt_dlp_enabled() -> bool:
    mod = _legacy_module()
    return bool(getattr(mod, "_ytdlp_enabled", True))


def _yt_dlp_progress_cb():
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
            "retries":          3,
            "fragment_retries": 3,
            "sleep_interval":   1,
            "max_sleep_interval": 3,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
            # Progress hook — update GUI status bar
            "progress_hooks": [_make_ytdlp_hook(vid_id, idx, total)],
        }

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
                    def info(self, msg):    pass
                    def warning(self, msg): pass
                    def error(self, msg):
                        captured.write(msg + "\n")
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

        def _chrome_cookie_path() -> Optional[str]:
            """Return Chrome Cookies DB path, or None if not found."""
            if sys.platform != "win32":
                return None
            local = os.environ.get("LOCALAPPDATA", "")
            for sub in [
                r"Google\Chrome\User Data\Default\Cookies",
                r"Google\Chrome\User Data\Default\Network\Cookies",
                r"BraveSoftware\Brave-Browser\User Data\Default\Cookies",
                r"BraveSoftware\Brave-Browser\User Data\Default\Network\Cookies",
                r"Microsoft\Edge\User Data\Default\Cookies",
                r"Microsoft\Edge\User Data\Default\Network\Cookies",
            ]:
                p = os.path.join(local, sub)
                if os.path.exists(p):
                    return p
            return None

        def _try_with_cookie_file(browser: str, opts: dict) -> Tuple[bool, Optional[str]]:
            """
            Try to use browser cookies. If Chrome is locked (common when browser
            is open), copy the DB to a temp file first so yt-dlp can read it.
            """
            if browser == "chrome" and sys.platform == "win32":
                # yt-dlp performs its own safe browser-cookie database copy.
                # The previous code created a temporary SQLite copy but never
                # passed it to yt-dlp, so it added a security warning and no
                # functional benefit. Keep the same public behavior without
                # the unused/race-prone temporary file.
                src_db = _chrome_cookie_path()
                if src_db:
                    logger.debug(f"yt-dlp browser cookie source detected: {src_db}")

            return _try_ytdlp({**opts, "cookiesfrombrowser": (browser,)})

        try:
            import yt_dlp

            # ── First attempt (no cookies) ─────────────────────────────
            opts = {k: v for k, v in ydl_opts.items() if k != "cookiesfrombrowser"}
            ok1, err1 = _try_ytdlp(opts)

            found_file = _any_audio_exists()

            if not found_file:
                # Nothing downloaded — retry with browser cookies
                # Always retry on failure; prioritise if error looks bot-related
                browsers = ["chrome", "firefox", "edge", "brave", "chromium", "safari"]
                logger.info(
                    f"  yt-dlp: first attempt failed "
                    f"{'(bot-detected)' if _is_bot_error(err1) else '(unknown error)'},"
                    f" retrying with browser cookies…"
                )
                for browser in browsers:
                    opts_c = {**opts, "cookiesfrombrowser": (browser,)}
                    ok_c, err_c = _try_ytdlp(opts_c)
                    found_file = _any_audio_exists()
                    if found_file:
                        logger.info(f"  yt-dlp: cookie source → {browser}")
                        break
                    if ok_c:
                        # yt-dlp said OK but file still missing — next browser
                        continue

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
                logger.warning(f"  yt-dlp: file not found after download: {url_clean}")
                failed.append(yt_url)
        except Exception as e:
            logger.error(f"  yt-dlp FAILED: {url_clean} — {e}")
            failed.append(yt_url)

    if failed:
        _write_youtube_skip_log(failed, log_dir or output_dir, source_url=source_url)

    if result:
        logger.info(
            f"yt-dlp: {len(result)}/{len(youtube_urls)} track(s) downloaded. "
            f"Files in: {audio_dir}"
        )

    return result


__all__ = ["_make_ytdlp_hook", "_download_youtube_audio"]
