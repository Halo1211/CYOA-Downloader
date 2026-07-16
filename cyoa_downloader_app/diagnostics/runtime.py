"""Runtime diagnostic report generation.

Runtime diagnostics stay small and import AI helpers lazily through the
compatibility surface so CLI startup remains light.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

import requests

from ..app_info import _APP_VERSION
from ..config.secrets import _is_secret_setting_key
from ..config.settings import (
    _SETTINGS_DEFAULTS, _SETTINGS_FILE, _detect_ffmpeg_path, _load_settings,
)
from ..storage.cache import _CACHE_DIR, _cache_stats
from ..storage.history import _HISTORY_FILE
from ..network.proxy import _get_active_proxy
from ..network.throttle import http2_runtime_info
from ..integrations.itch import detect_itch_backend


def _legacy():
    import sys as _sys
    return _sys.modules.get("cyoa_downloader_app.runtime.surface") or _sys.modules.get("cyoa_downloader")


def _get_ai_provider():
    l = _legacy()
    return l._get_ai_provider()


def _resolve_ai_api_key(*args, **kwargs):
    l = _legacy()
    return l._resolve_ai_api_key(*args, **kwargs)


def _ai_is_available(*args, **kwargs):
    l = _legacy()
    return l._ai_is_available(*args, **kwargs)


def _ai_call(*args, **kwargs):
    l = _legacy()
    return l._ai_call(*args, **kwargs)


def _ai_provider_label(*args, **kwargs):
    l = _legacy()
    return l._ai_provider_label(*args, **kwargs)


def _first_executable(names: Tuple[str, ...]) -> str:
    """Return the first executable on PATH without raising."""
    import shutil
    for name in names:
        try:
            path = shutil.which(name)
        except Exception:
            path = None
        if path:
            return path
    return ""


def _installed_browser() -> str:
    """Find a browser even when its installer did not add it to PATH."""
    import pathlib

    found = _first_executable((
        "chrome", "google-chrome", "chromium", "chromium-browser", "msedge", "firefox",
    ))
    if found:
        return found
    if sys.platform != "win32":
        return ""
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    candidates = (
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(local, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
    )
    for candidate in candidates:
        if pathlib.Path(candidate).is_file():
            return str(pathlib.Path(candidate))
    return ""


def _playwright_chromium() -> str:
    """Find the separately installed Chromium payload used by Playwright."""
    import glob
    import importlib.util
    import pathlib

    roots = []
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured and configured != "0":
        roots.append(os.path.expanduser(os.path.expandvars(configured)))
    elif configured == "0":
        spec = importlib.util.find_spec("playwright")
        for location in (spec.submodule_search_locations or []) if spec else []:
            roots.append(os.path.join(location, ".local-browsers"))
    if sys.platform == "win32":
        roots.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"))
    elif sys.platform == "darwin":
        roots.append(os.path.expanduser("~/Library/Caches/ms-playwright"))
    else:
        roots.append(os.path.expanduser("~/.cache/ms-playwright"))

    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        patterns = (
            os.path.join(root, "chromium-*", "chrome-win", "chrome.exe"),
            os.path.join(root, "chromium-*", "chrome-linux", "chrome"),
            os.path.join(root, "chromium-*", "chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
        )
        for pattern in patterns:
            for candidate in sorted(glob.glob(pattern)):
                if pathlib.Path(candidate).is_file():
                    return candidate
    return ""


def _rar_backend() -> str:
    """Find an external helper required by rarfile to open RAR archives."""
    return _first_executable(("unrar", "UnRAR", "unar", "7z", "7zz", "bsdtar"))


def build_diagnostic_report(output_dir: str = "", check_network: bool = True,
                            check_ai: bool = False, language: str = "en") -> Tuple[str, Dict[str, int]]:
    """Run runtime diagnostics and return (report_text, counts).

    Each line is formatted "<STATUS>  <name>  — <detail/solution>" where STATUS
    is PASS / WARN / FAIL. Safe to call from a background thread: it performs
    no Tk operations. Network checks are skipped when check_network is False.
    """
    import importlib.util, platform, socket, tempfile
    lines: List[str] = []
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    is_id = str(language or "").lower().startswith("id")
    def _l(en: str, id_text: str) -> str:
        return id_text if is_id else en

    def _add(status: str, name: str, detail: str = "") -> None:
        counts[status] = counts.get(status, 0) + 1
        lines.append(f"{status:4}  {name:28}  {detail}".rstrip())

    # Python version
    pv = sys.version_info
    if pv >= (3, 9):
        _add("PASS", "Python version", f"{platform.python_version()}")
    else:
        _add("FAIL", "Python version", f"{platform.python_version()} (need 3.9+)")

    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", "")
        _add("PASS", "Runtime mode", f"frozen executable: {sys.executable}")
        _add("PASS" if bundle_root else "WARN", "Frozen resource root",
             bundle_root or "_MEIPASS is not exposed")
    else:
        _add("PASS", "Runtime mode", "source Python interpreter")

    # Python dependencies
    deps = [
        ("requests", True), ("urllib3", True), ("bs4", True), ("customtkinter", True),
        ("httpx", False), ("h2", False), ("dns", False),
        ("tldextract", False), ("PIL", False), ("pandas", False),
        ("openpyxl", False), ("xlrd", False),
        ("keyring", False), ("cloudscraper", False),
        ("json5", False), ("yt_dlp", False), ("yt_dlp_ejs", False),
        ("browser_cookie3", False), ("gallery_dl", False),
        ("selenium", False),
        ("playwright", False), ("plyer", False), ("rarfile", False),
    ]
    for mod, required in deps:
        present = importlib.util.find_spec(mod) is not None
        if present:
            _add("PASS", f"dependency: {mod}", "installed")
        elif required:
            _add("FAIL", f"dependency: {mod}", f"required — pip install {mod}")
        else:
            _add("WARN", f"dependency: {mod}", f"optional — pip install {mod} to enable")

    http2 = http2_runtime_info()
    if http2["available"]:
        _add("PASS", "dependency: httpx[http2]", http2["detail"])
    else:
        _add(
            "WARN",
            "dependency: httpx[http2]",
            f"HTTP/2 unavailable in {http2['python']}: "
            f"{http2['detail'] or 'httpx[http2] is incomplete'}; "
            f'install with "{http2["python"]}" -m pip install "httpx[http2]"',
        )

    if importlib.util.find_spec("yt_dlp") is not None:
        try:
            from ..download.audio_download import _yt_dlp_runtime_options
            runtime_options = _yt_dlp_runtime_options()
            runtimes = runtime_options.get("js_runtimes", {})
            if runtimes:
                _add("PASS", "YouTube JavaScript runtime",
                     ", ".join(sorted(runtimes)))
            else:
                _add("WARN", "YouTube JavaScript runtime",
                     "Deno/Node not found; YouTube audio may fail")
            if runtime_options.get("remote_components"):
                _add("WARN", "yt-dlp EJS delivery",
                     "remote EJS fallback enabled; bundle yt_dlp_ejs for offline reliability")
        except Exception as e:
            _add("WARN", "YouTube JavaScript runtime", f"probe error: {e}")
    else:
        _add(
            "WARN",
            "YouTube JavaScript runtime",
            "yt-dlp is not installed; install yt-dlp[default] and Deno to enable YouTube audio",
        )

    # Selenium browser and driver are separate capabilities. Selenium Manager
    # may supply a driver automatically, so a browser is the stronger check.
    if importlib.util.find_spec("selenium") is not None:
        browser = _installed_browser()
        driver = _first_executable(("chromedriver", "msedgedriver", "geckodriver"))
        if browser:
            _add("PASS", "Selenium browser", browser)
        else:
            _add("WARN", "Selenium browser", "Chrome/Edge/Firefox not found")
        _add("PASS" if driver else "WARN", "Selenium driver",
             driver or "not found; Selenium Manager may download one on first use")
    else:
        _add("WARN", "Selenium browser", "selenium package not installed")
        _add("WARN", "Selenium driver", "selenium package not installed")

    if importlib.util.find_spec("playwright") is not None:
        pw_browser = _playwright_chromium()
        _add("PASS" if pw_browser else "WARN", "Playwright Chromium",
             pw_browser or "browser payload not found; run `playwright install chromium`")
    else:
        _add("WARN", "Playwright Chromium",
             "playwright package not installed; install requirements-optional.txt")

    if importlib.util.find_spec("rarfile") is not None:
        rar_helper = _rar_backend()
        _add("PASS" if rar_helper else "WARN", "RAR extraction helper",
             rar_helper or "unrar/7z not found; ZIP still works")
    else:
        _add("WARN", "RAR extraction helper",
             "rarfile package not installed; ZIP remains available")

    # External tools
    import shutil as _sh2
    yt_cli = _sh2.which("yt-dlp")
    _add("PASS" if yt_cli else "WARN", "yt-dlp CLI (optional)",
         yt_cli or "not on PATH; Python yt_dlp API is used by the app")
    gallery_cli = _sh2.which("gallery-dl")
    _add("PASS" if gallery_cli else "WARN", "gallery-dl CLI (optional)",
         gallery_cli or "not on PATH; Python module/config may still work")
    ffmpeg_path = _detect_ffmpeg_path()
    _add("PASS" if ffmpeg_path else "WARN", "FFmpeg",
         ffmpeg_path or "not found; YouTube MP3 conversion is unavailable")

    # itch-dl backend (external CLI; uvx / pipx / itch-dl on PATH)
    try:
        _itch_cmd, _itch_label = detect_itch_backend()
        _add("PASS" if _itch_cmd else "WARN", "itch-dl backend",
             _itch_label if _itch_cmd else "not found (install uv/pipx or `pip install itch-dl`)")
    except Exception as _e:
        _add("WARN", "itch-dl backend", f"probe error: {_e}")

    # Output folder permission
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            probe = os.path.join(output_dir, ".cyoa_diag_probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            _add("PASS", "Output folder writable", output_dir)
        except Exception as e:
            _add("FAIL", "Output folder writable", f"{output_dir}: {e}")
    else:
        _add("WARN", "Output folder", "no output folder selected yet")

    # Cache folder writable
    try:
        cache_dir = _CACHE_DIR if "_CACHE_DIR" in globals() else tempfile.gettempdir()
        os.makedirs(cache_dir, exist_ok=True)
        probe = os.path.join(cache_dir, ".cyoa_diag_probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        _add("PASS", "Cache folder writable", cache_dir)
    except Exception as e:
        _add("WARN", "Cache folder writable", str(e))

    # settings.json validity
    try:
        st = _load_settings()
        _add("PASS", "settings.json valid", f"{len(st)} keys")
    except Exception as e:
        st = dict(_SETTINGS_DEFAULTS) if "_SETTINGS_DEFAULTS" in globals() else {}
        _add("WARN", "settings.json valid", f"could not load: {e}")

    # Runtime paths and safe configuration summary (no secret values).
    try:
        settings_dir = os.path.dirname(_SETTINGS_FILE)
        os.makedirs(settings_dir, exist_ok=True)
        _add("PASS" if os.path.isdir(settings_dir) else "FAIL", "Settings folder", settings_dir)
        _add("PASS" if os.path.exists(_SETTINGS_FILE) else "WARN", "settings.json path",
             _SETTINGS_FILE if os.path.exists(_SETTINGS_FILE) else f"not created yet: {_SETTINGS_FILE}")
        _add("PASS" if os.path.exists(_HISTORY_FILE) else "WARN", "History file",
             _HISTORY_FILE if os.path.exists(_HISTORY_FILE) else "not created yet")
    except Exception as e:
        _add("WARN", "Settings paths", str(e))

    try:
        safe_keys = [
            "enable_deep_scan", "enable_selenium_fallback", "enable_serve_preview",
            "enable_cheat_panel", "enable_itch_downloader", "gallery_dl_mode", "auto_detect_output",
            "gallery_dl_path", "ai_mode", "ai_provider", "cloudflare_mode",
            "use_http2", "download_fonts", "download_youtube_audio",
        ]
        visible = []
        for k in safe_keys:
            if k in st and not _is_secret_setting_key(k):
                v = st.get(k)
                if isinstance(v, str) and len(v) > 80:
                    v = v[:77] + "..."
                visible.append(f"{k}={v}")
        _add("PASS", "Feature settings", "; ".join(visible) if visible else "defaults")
    except Exception as e:
        _add("WARN", "Feature settings", f"summary error: {e}")

    try:
        proxy = _get_active_proxy() if "_get_active_proxy" in globals() else ""
        _add("PASS" if proxy else "WARN", "Proxy setting", "configured" if proxy else "not configured")
    except Exception as e:
        _add("WARN", "Proxy setting", f"probe error: {e}")

    try:
        stats = _cache_stats() if "_cache_stats" in globals() else {"entries": 0, "size_mb": 0}
        _add("PASS", "Image cache", f"{stats.get('entries', 0)} entries, {stats.get('size_mb', 0)} MB at {_CACHE_DIR}")
    except Exception as e:
        _add("WARN", "Image cache", f"probe error: {e}")

    try:
        import platform as _platform
        cfg = str(st.get("gallery_dl_config", "") or "").strip()
        if not cfg:
            if _platform.system() == "Windows":
                base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
                cfg = os.path.join(base, "gallery-dl", "config.json")
            else:
                cfg = os.path.join(os.path.expanduser("~"), ".config", "gallery-dl", "config.json")
        cfg = os.path.abspath(os.path.expanduser(os.path.expandvars(cfg)))
        if os.path.exists(cfg):
            try:
                with open(cfg, encoding="utf-8") as fh:
                    json.load(fh)
                _add("PASS", "gallery-dl config", f"valid JSON: {cfg}")
            except Exception as e:
                _add("WARN", "gallery-dl config", f"exists but invalid JSON: {e}")
        else:
            _add("WARN", "gallery-dl config", f"not found: {cfg}")
    except Exception as e:
        _add("WARN", "gallery-dl config", f"probe error: {e}")

    try:
        if output_dir:
            report_names = ["backup_report.txt", "failed_assets.txt", "failed_images.txt", "skipped_youtube_audio.txt", "cyoa_downloader.log"]
            found_reports = [n for n in report_names if os.path.exists(os.path.join(output_dir, n))]
            _add("PASS" if found_reports else "WARN", "Output reports",
                 ", ".join(found_reports) if found_reports else "no report files found yet")
    except Exception as e:
        _add("WARN", "Output reports", f"probe error: {e}")

    # Network + DNS
    if check_network:
        try:
            socket.getaddrinfo("example.com", 443)
            _add("PASS", "DNS resolution", "example.com resolved")
        except Exception as e:
            _add("FAIL", "DNS resolution", f"failed: {e}")
        try:
            r = requests.get("https://www.google.com/generate_204", timeout=8)
            if r.status_code in (204, 200):
                _add("PASS", "Internet connectivity", f"HTTP {r.status_code}")
            else:
                _add("WARN", "Internet connectivity", f"unexpected HTTP {r.status_code}")
        except Exception as e:
            _add("FAIL", "Internet connectivity", f"no connection: {e}")
    else:
        _add("WARN", "Network checks", "skipped by request")

    # AI provider (only if explicitly requested — never at startup)
    if check_ai:
        try:
            provider = _get_ai_provider()
            key = _resolve_ai_api_key(provider=provider) if "_resolve_ai_api_key" in globals() else ""
            if _ai_is_available(key, provider):
                probe = _ai_call(api_key=key, provider=provider, prompt="Reply with OK.",
                                 max_tokens=5, label="diag")
                if probe:
                    _add("PASS", "AI provider connection", f"{_ai_provider_label(provider)} responded")
                else:
                    _add("FAIL", "AI provider connection", f"{_ai_provider_label(provider)} no response")
            else:
                _add("WARN", "AI provider connection", "not configured (optional)")
        except Exception as e:
            _add("WARN", "AI provider connection", f"check error: {e}")

    header = [
        (f"CYOA Downloader v{_APP_VERSION} diagnostics" if not is_id else f"Diagnostik CYOA Downloader v{_APP_VERSION}"),
        "=" * 52,
    ]
    footer = [
        "-" * 52,
        f"PASS: {counts['PASS']}   WARN: {counts['WARN']}   FAIL: {counts['FAIL']}",
    ]
    return "\n".join(header + lines + footer), counts
