"""Offline dependency check report generation."""

from __future__ import annotations

from ..app_info import _APP_VERSION
from ..config.settings import _detect_ffmpeg_path, _ffmpeg_install_guide
from ..integrations.itch import itch_backend_status


def dependency_check_report() -> str:
    """Return an offline dependency report for GUI, network, batch, AI, media, and fallback features."""
    import importlib.util

    # module name, display name, requirement group, purpose, fallback
    checks = [
        ("requests", "requests", "required", "Core HTTP downloader", "No network download."),
        ("urllib3", "urllib3", "required", "Retry/HTTP adapter support used through requests", "No robust retry adapter."),
        ("bs4", "beautifulsoup4", "required", "HTML/ICC parsing via BeautifulSoup", "HTML/project extraction is limited."),
        ("tldextract", "tldextract", "required-for-domain-tools", "Domain/subdomain parsing", "Fallback hostname parsing is used."),
        ("json5", "json5", "optional-parser", "Lenient JSON5 parsing fallback", "Strict JSON parsing still works."),
        ("pandas", "pandas", "optional-batch", "CSV/XLS/XLSX queue import", "TXT batch import still works."),
        ("openpyxl", "openpyxl", "optional-batch", ".xlsx reader backend used by pandas", "CSV/TXT import still works."),
        ("xlrd", "xlrd", "optional-batch", ".xls reader backend used by pandas", "CSV/TXT/XLSX import still works."),
        ("httpx", "httpx[http2]", "optional-network", "HTTP/2 deep-scan fetch", "Falls back to requests/HTTP1."),
        ("yt_dlp", "yt-dlp", "optional-media", "YouTube/SoundCloud and supported media download", "Media URL is skipped or logged as unavailable."),
        ("customtkinter", "customtkinter", "required-for-gui", "Modern GUI widgets", "CLI remains usable; GUI cannot launch."),
        ("PIL", "pillow", "optional-gui-image", "GUI/image preview utilities", "GUI runs with reduced image utilities."),
        ("cloudscraper", "cloudscraper", "optional-network", "Cloudflare fallback", "Normal requests and FlareSolverr path remain."),
        ("plyer", "plyer", "optional-gui", "Desktop notifications", "No desktop notification."),
        ("rarfile", "rarfile", "optional-viewer", ".rar offline viewer import", "ZIP import remains supported."),
        ("gallery_dl", "gallery-dl", "optional-fallback", "Gallery/post downloader fallback", "Core downloader remains active."),
        ("keyring", "keyring", "optional-security", "OS keyring for AI/API keys", "Session/env/plain modes remain available."),
        ("playwright", "playwright", "optional-headless", "Headless browser fallback", "Selenium/requests fallback may still work."),
        ("selenium", "selenium", "optional-headless", "Secondary headless fallback", "Playwright/requests fallback may still work."),
    ]
    lines = [f"CYOA Downloader v{_APP_VERSION} dependency check", "=" * 72]
    ok = 0
    missing_required = 0
    for module, display, group, purpose, fallback in checks:
        found = importlib.util.find_spec(module) is not None
        ok += int(found)
        if (not found) and group == "required":
            missing_required += 1
        status = "OK" if found else "MISSING"
        lines.append(f"{status:7}  {display:18}  {group:22}  {purpose}")
        if not found:
            lines.append(f"         {'':18}  {'fallback':22}  {fallback}")

    ffmpeg_path = _detect_ffmpeg_path()
    if ffmpeg_path:
        lines.append(f"OK       {'ffmpeg':18}  {'optional-media-cli':22}  Available on PATH: {ffmpeg_path}")
    else:
        lines.append(f"MISSING  {'ffmpeg':18}  {'optional-media-cli':22}  Required only for media conversion/merge used by yt-dlp or offline media features")
        lines.append(f"         {'':18}  {'fallback':22}  Normal JSON/image/font downloads continue; media conversion is skipped/limited.")

    lines.append("-" * 72)
    lines.append(f"Installed Python modules: {ok}/{len(checks)} detected")
    if missing_required:
        lines.append(f"Required missing: {missing_required}. Install required modules before full downloader use.")
    else:
        lines.append("Required Python modules: OK")
    lines.append("Optional modules are only needed when the related feature is enabled.")
    lines.append("Recommended install:")
    lines.append("  pip install -r requirements.txt")
    lines.append("  pip install requests urllib3 beautifulsoup4 tldextract json5 pandas openpyxl xlrd yt-dlp customtkinter pillow httpx[http2]")
    lines.append(_ffmpeg_install_guide())
    # itch-dl is an external CLI backend (not a Python import), probed separately.
    try:
        lines.append(itch_backend_status())
    except Exception as _e:
        lines.append(f"itch-dl backend: probe error ({_e})")
    return "\n".join(lines)
