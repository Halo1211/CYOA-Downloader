"""CLI entry point implementation.

Phase 51 moves the large historical ``main()`` body out of ``legacy.py`` while
keeping the public ``cyoa_downloader.py`` script and compatibility facade stable.
The implementation intentionally keeps the original global-name lookups; legacy
sync supplies those names during the transition.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


_CONSOLE_ASCII_REPLACEMENTS = str.maketrans({
    "→": "->",
    "←": "<-",
    "—": "-",
    "–": "-",
    "•": "*",
    "…": "...",
    "✓": "OK",
    "✗": "X",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    " ": " ",
})


def _safe_console_print(value="", *, file=None) -> None:
    """Print safely on legacy Windows console encodings such as CP1252."""
    stream = file or sys.stdout
    text = str(value)
    encoding = getattr(stream, "encoding", None) or ""
    normalized_encoding = encoding.lower().replace("_", "-")
    if normalized_encoding and normalized_encoding not in {"utf-8", "utf8", "utf-8-sig"}:
        text = text.translate(_CONSOLE_ASCII_REPLACEMENTS)
        text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    try:
        print(text, file=stream)
        return
    except UnicodeEncodeError:
        # Keep diagnostic output readable on legacy Windows code pages. The
        # old implementation replaced unsupported arrows/checkmarks with '?',
        # which then appeared as U+FFFD in some GUI/terminal captures.
        encoding = encoding or "ascii"
        fallback = text.translate(_CONSOLE_ASCII_REPLACEMENTS)
        fallback = fallback.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(fallback, file=stream)


def _legacy() -> ModuleType:
    mod = sys.modules.get("cyoa_downloader_app.runtime.surface")
    if mod is not None:
        return mod
    return importlib.import_module("cyoa_downloader_app.runtime.surface")


def _sync_legacy_globals(namespace: dict | None = None) -> None:
    """Expose legacy/global compatibility names to the moved CLI body."""
    if namespace is None:
        namespace = vars(_legacy())
    globals().update({
        key: value
        for key, value in namespace.items()
        if not (key.startswith("__") and key.endswith("__"))
    })


def _sync_runtime_globals_to_legacy() -> None:
    """Write CLI-mutated runtime globals back to runtime.state and legacy."""
    l = _legacy()
    try:
        from .runtime import state as _runtime_state
    except Exception:
        _runtime_state = None
    for name in ("wait_time", "_bandwidth_limit_kbps", "use_cloudscraper", "_ytdlp_enabled"):
        if name in globals():
            value = globals()[name]
            if _runtime_state is not None and hasattr(_runtime_state, name):
                try:
                    setattr(_runtime_state, name, value)
                except Exception:
                    pass
            try:
                setattr(l, name, value)
            except Exception:
                pass
    try:
        # The download orchestrator keeps a copied wait_time global for the
        # moved base implementation; refresh it if the module is loaded.
        orch = sys.modules.get("cyoa_downloader_app.download.orchestrator")
        if orch is not None and "wait_time" in globals():
            setattr(orch, "wait_time", globals()["wait_time"])
    except Exception:
        pass


_sync_legacy_globals()

def main() -> None:
    # Auto-register bundled offline viewers (no-op if already registered)
    try:
        _auto_register_bundled_viewers()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in main (line 16552): %s", _ignored_exc)
    global wait_time

    if len(sys.argv) == 1:
        launch_gui()
        return

    _cli_saved_settings = _load_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Download and process a CYOA project. Supports embedded JSON, ZIP, "
            "both formats, or a full offline ICC viewer package. Run without arguments for the GUI."
        )
    )
    parser.add_argument("url", nargs="?", default="", help="URL of the CYOA project.")
    parser.add_argument("filename", nargs="?", default="", help="Optional output filename.")
    parser.add_argument("-u", "--url", dest="url_opt", default="",
                        help="URL of the CYOA project. Overrides positional URL when provided.")
    parser.add_argument("-o", "--output", dest="output_dir", default=os.getcwd(),
                        help="Output directory. Created automatically if missing.")
    parser.add_argument("-L", "--list", dest="list_file", default="",
                        help="Batch input source (.txt/.csv/.xlsx/.xls or remote CSV/Google Sheets URL) with URLs.")
    parser.add_argument("-z", "--zip",     action="store_true", help="Save as ZIP with external images.")
    parser.add_argument("-b", "--both",    action="store_true", help="Save both embedded JSON and ZIP.")
    parser.add_argument("--icc", dest="website", action="store_true",
                        help="ICC Mode (ZIP): download the full ICC viewer + all assets as a ZIP archive.")
    parser.add_argument("--icc-folder", dest="website_folder", action="store_true",
                        help="ICC Mode (Folder): download the full ICC viewer + all assets as a local folder without ZIP compression.")
    parser.add_argument("--pure-website", action="store_true",
                        help="Download viewer HTML/CSS/JS only — skip project.json search. "
                             "Useful for custom-format sites (e.g. lewd_horizon). Output: ZIP.")
    parser.add_argument("--pure-website-folder", action="store_true",
                        help="Same as --pure-website but keep as folder instead of ZIP.")
    parser.add_argument("--archive-strategy", choices=["classic", "smart", "browser", "auto"],
                        default=str(_cli_saved_settings.get("archive_strategy", "classic")),
                        help="Website archive engine: auto fingerprints the site; classic keeps historical behavior; smart crawls story routes; browser captures runtime assets.")
    parser.add_argument("--archive-max-pages", type=int,
                        default=int(_cli_saved_settings.get("archive_max_pages", 300) or 300),
                        help="Maximum internal HTML routes for Smart/Browser modes (default: 300).")
    parser.add_argument("--archive-max-depth", type=int,
                        default=int(_cli_saved_settings.get("archive_max_depth", 30)),
                        help="Maximum internal route-link depth for Smart/Browser modes (default: 30).")
    parser.add_argument("-f", "--fonts",   action="store_true",
                        help="Download & localise fonts (ZIP/ICC mode).")
    parser.add_argument("--cyoap-vue", action="store_true",
                        help="Auto-probe dedicated cyoap_vue ICC backup mode before standard ICC detection.")
    parser.add_argument("--cyoap-vue-website", action="store_true",
                        help="Use dedicated cyoap_vue ICC mode and output ZIP.")
    parser.add_argument("--cyoap-vue-folder", action="store_true",
                        help="Use dedicated cyoap_vue ICC mode and keep folder output.")
    parser.add_argument("-a", "--analyse-fonts", action="store_true",
                        help="Print font analysis report only.")
    parser.add_argument("-t", "--threads", "--workers", dest="threads", type=int, default=DEFAULT_MAX_WORKERS,
                        help=f"Parallel download threads (default: {DEFAULT_MAX_WORKERS}).")
    parser.add_argument("-w", "--wait-time", "--wait", dest="wait_time", type=int, default=DEFAULT_WAIT_TIME,
                        help=f"Seconds to wait after 429 (default: {DEFAULT_WAIT_TIME}).")
    parser.add_argument("--proxy", default=None, help="Proxy URL, e.g. http://127.0.0.1:7890. Use --proxy-mode disabled to ignore environment proxies.")
    parser.add_argument("--proxy-mode", choices=["inherit_env", "manual", "disabled"], default=None, help="Proxy mode. Default preserves saved/runtime behavior; disabled ignores HTTP_PROXY/HTTPS_PROXY.")
    parser.add_argument("--dns", default=None, help="Override DNS resolver for this process. Accepts plain DNS IP or DoH URL. Empty string restores system DNS.")
    parser.add_argument("--bebasdns", choices=["default", "security", "unfiltered", "family"], default=None,
                        help="Use BebasDNS DoH resolver variant for this process.")
    parser.add_argument("--cloudflare", choices=["off", "auto", "cloudscraper", "flaresolverr"],
                        default=_load_settings().get("cloudflare_mode", "auto"),
                        help="Cloudflare handling mode. Auto escalates only after a challenge is detected.")
    parser.add_argument("--cloudflare-priority", choices=["flaresolverr-first", "cloudscraper-first"],
                        default=str(_load_settings().get("cloudflare_priority", "flaresolverr_first")).replace("_", "-"),
                        help="Auto fallback order after a challenge: FlareSolverr first or cloudscraper first.")
    parser.add_argument("--cf-bypass", "--cloudscraper", dest="cf_bypass", action="store_true",
                        help="Legacy alias: force Cloudflare mode to cloudscraper when installed.")
    parser.add_argument("--flaresolverr-url", default=_load_settings().get("flaresolverr_url", "http://localhost:8191/v1"),
                        help="FlareSolverr API endpoint, e.g. http://localhost:8191/v1.")
    parser.add_argument("--flaresolverr-session", choices=["temporary", "reuse-domain", "manual"],
                        default=_load_settings().get("flaresolverr_session_policy", "reuse-domain"),
                        help="FlareSolverr session policy. reuse-domain keeps cookies per domain.")
    parser.add_argument("--flaresolverr-timeout", type=int, default=int(_load_settings().get("flaresolverr_timeout", 60) or 60),
                        help="FlareSolverr solve timeout in seconds.")
    parser.add_argument("--flaresolverr-wait", type=int, default=int(_load_settings().get("flaresolverr_wait_after", 3) or 3),
                        help="Seconds FlareSolverr waits after page load before returning content.")
    parser.add_argument("--flaresolverr-proxy", choices=["inherit", "none"],
                        default=_load_settings().get("flaresolverr_proxy_mode", "inherit"),
                        help="Whether FlareSolverr should inherit the app proxy for target requests.")
    parser.add_argument("--flaresolverr-test", action="store_true",
                        help="Test the configured FlareSolverr API and exit.")
    parser.add_argument("--http2", action=argparse.BooleanOptionalAction, default=None, help="Use HTTP/2 via httpx for deep-scan asset fetches when available.")
    parser.add_argument("--gallery-dl", choices=["off", "smart", "force"], default=None,
                        help="Optional gallery-dl fallback. Default off; smart only uses page/post/gallery URLs; force is advanced.")
    parser.add_argument("--gallery-dl-path", default="gallery-dl", help="gallery-dl executable path used with --gallery-dl.")
    parser.add_argument("--gallery-dl-config", default="", help="Optional gallery-dl config.json path.")
    parser.add_argument("--bandwidth", type=float, default=0.0, help="Bandwidth limit in KB/s. 0 means unlimited.")
    parser.add_argument("--allow-internal-hosts", action="store_true",
                        help="Allow fetching assets from cross-origin internal/loopback/RFC1918 hosts. "
                             "Off by default as an SSRF safeguard; enable only for trusted local setups.")
    parser.add_argument("--ai-key", dest="ai_api_key", default="", help="AI API key for this run only. It is not saved to settings.json.")
    parser.add_argument("--ai-provider",
                        choices=["anthropic", "openai", "gemini", "ollama", "deepseek", "qwen", "groq", "openrouter", "custom"],
                        default=None,
                        help="AI provider for AI Assist.")
    parser.add_argument("--ai-key-storage", choices=["session", "env", "keyring", "plain"], default=None,
                        help="Where to read/save the AI API key. CLI default follows settings; --ai-key overrides storage for this run.")
    parser.add_argument("--ai-model", default="", help="AI model for the selected provider. If omitted, the provider default is used.")
    parser.add_argument("--ollama-url", default=None,
                        help="Ollama base URL used when --ai-provider ollama. Default: http://localhost:11434")
    parser.add_argument("--ai-mode", choices=["off", "diagnostics", "auto_fallback", "aggressive_recovery"], default=None,
                        help="AI Assist mode. Use off to disable AI even if a key is configured.")
    parser.add_argument("--ai-max-calls", type=int, default=None, help="Maximum AI calls per download. 0 means unlimited.")
    parser.add_argument("--ai-max-html-chars", type=int, default=None, help="Maximum HTML characters sent to AI per call.")
    parser.add_argument("--ai-max-js-chars", type=int, default=None, help="Maximum JS characters sent to AI per call.")
    parser.add_argument("--ai-clear-key", action="store_true", help="Clear the configured AI key from plain settings.json or OS keyring and exit.")
    parser.add_argument("--cyoa-manager", action="store_true", help="Add finished project.json to CYOA Manager when possible.")
    parser.add_argument("--serve", action="store_true", help="Start local HTTP server for the output directory after download.")
    parser.add_argument("--serve-port", type=int, default=0, help="Local server port used with --serve. 0 means auto-pick a fresh port.")
    parser.add_argument("--language", choices=["id", "en"], default=None, help="CLI/UI language preference. Saved only when explicitly provided.")
    parser.add_argument("--no-ytdlp", action="store_true",
                        help="Disable automatic YouTube audio download via yt-dlp.")
    parser.add_argument("--ytdlp-cookies", metavar="FILE", default="",
                        help="Use an exported Netscape cookies.txt for yt-dlp when YouTube requires sign-in.")
    # ── v7.5.8 feature toggles (additive; defaults preserve old behavior) ──
    parser.add_argument("--no-deep-scan", action="store_true",
                        help="Disable the JS/CSS deep-scan asset pass (default: on).")
    parser.add_argument("--no-selenium", action="store_true",
                        help="Disable the headless browser image fallback (default: on).")
    parser.add_argument("--itch", action="store_true",
                        help="Enable the optional itch.io asset downloader for itch.io URLs (default: off).")
    parser.add_argument("--itch-test", action="store_true",
                        help="Test itch.io backend (itch-dl) + connectivity and exit.")
    parser.add_argument("--itch-mirror-web", action="store_true",
                        help="Pass --mirror-web to itch-dl (mirror linked web builds) when supported.")
    parser.add_argument("--dependency-check", action="store_true", help="Print optional/required dependency status and exit.")
    parser.add_argument("--userscript-info", action="store_true", help="Print Serve-only userscript integration credit/source notes and exit.")
    parser.add_argument("--self-test", action="store_true", help="Run offline internal smoke tests and exit.")
    parser.add_argument("--verify", metavar="FOLDER", default=None,
                        help="Validate a previously downloaded output FOLDER (read-only integrity check) and exit.")
    parser.add_argument("--write-manifest", action="store_true",
                        help="With --verify: write a cyoa_manifest.json checksum sidecar into the folder, then exit. Enables later checksum verification.")
    parser.add_argument("--export-settings", metavar="FILE", default=None,
                        help="Export current settings (secrets redacted) to FILE and exit.")
    parser.add_argument("--import-settings", metavar="FILE", default=None,
                        help="Merge settings from a prior export FILE (secrets ignored) and exit.")
    parser.add_argument("--gui", action="store_true", help="Force GUI.")
    args = parser.parse_args()
    args.url = (args.url_opt or args.url or "").strip()

    if args.ytdlp_cookies:
        cookie_path = os.path.abspath(os.path.expanduser(args.ytdlp_cookies))
        if not os.path.isfile(cookie_path):
            parser.error(f"yt-dlp cookie file not found: {cookie_path}")
        # Pass only the path through the process environment; the cookie
        # contents must never enter settings, logs, or command output.
        os.environ["CYOA_YTDLP_COOKIES"] = cookie_path

    if args.dependency_check:
        _safe_console_print(dependency_check_report())
        return

    if args.userscript_info:
        _safe_console_print(userscript_integration_report())
        return

    if args.self_test:
        ok, report = run_internal_self_test()
        _safe_console_print(report)
        if not ok:
            raise SystemExit(1)
        return

    if args.verify:
        if args.write_manifest:
            ok, msg = write_package_manifest(args.verify)
            _safe_console_print(msg)
            if not ok:
                raise SystemExit(1)
            return
        ok, report = verify_output_package(args.verify)
        _safe_console_print(report)
        if not ok:
            raise SystemExit(1)
        return

    if args.export_settings:
        ok, msg = export_settings(args.export_settings)
        _safe_console_print(msg)
        if not ok:
            raise SystemExit(1)
        return

    if args.import_settings:
        ok, msg = import_settings(args.import_settings)
        _safe_console_print(msg)
        if not ok:
            raise SystemExit(1)
        return
    # Resolve effective AI/network settings without overwriting saved GUI settings unless
    # the user supplied the corresponding CLI flag explicitly.
    ai_provider_eff = _normalize_ai_provider(args.ai_provider or _cli_saved_settings.get("ai_provider", "anthropic"))
    ai_storage_eff = _normalize_ai_key_storage(args.ai_key_storage or _cli_saved_settings.get("ai_key_storage", "session"))
    ai_mode_eff = _normalize_ai_mode(args.ai_mode or _cli_saved_settings.get("ai_mode", "auto_fallback"))
    ai_model_eff = args.ai_model or _get_ai_model(ai_provider_eff)
    ollama_url_eff = args.ollama_url or _cli_saved_settings.get("ollama_url", OLLAMA_DEFAULT_URL)
    if args.ai_clear_key:
        _clear_ai_api_key_storage(ai_storage_eff, ai_provider_eff, clear_all=True)
        logger.info("AI API key storage cleared.")
        return
    _ai_cli_settings = _load_settings()
    changed_settings = False
    if args.ai_provider is not None:
        _ai_cli_settings["ai_provider"] = ai_provider_eff; changed_settings = True
    if args.ai_key_storage is not None:
        _ai_cli_settings["ai_key_storage"] = ai_storage_eff; changed_settings = True
        if ai_storage_eff != "plain":
            _clear_ai_plain_keys(_ai_cli_settings, ai_provider_eff)
    if args.ai_model:
        _ai_cli_settings["ai_model"] = ai_model_eff; changed_settings = True
    if args.ai_mode is not None:
        _ai_cli_settings["ai_mode"] = ai_mode_eff; changed_settings = True
    if args.ai_max_calls is not None:
        _ai_cli_settings["ai_max_calls_per_download"] = max(0, int(args.ai_max_calls)); changed_settings = True
    if args.ai_max_html_chars is not None:
        _ai_cli_settings["ai_max_html_chars"] = max(1000, int(args.ai_max_html_chars)); changed_settings = True
    if args.ai_max_js_chars is not None:
        _ai_cli_settings["ai_max_js_chars"] = max(1000, int(args.ai_max_js_chars)); changed_settings = True
    if args.ollama_url:
        _ai_cli_settings["ollama_url"] = ollama_url_eff; changed_settings = True
    if changed_settings:
        _save_settings(_ai_cli_settings)
    resolved_ai_api_key = "" if ai_mode_eff == "off" else _resolve_ai_api_key(explicit_key=args.ai_api_key, storage=ai_storage_eff, provider=ai_provider_eff)
    # v7.5.6 fix: fail fast with a clear message when the output folder
    # cannot be created or written, instead of a raw traceback (CLI) or a
    # mid-download failure.
    try:
        os.makedirs(args.output_dir, exist_ok=True)
        _probe = os.path.join(args.output_dir, ".cyoa_write_test")
        with open(_probe, "w") as _pf:
            _pf.write("ok")
        os.remove(_probe)
    except Exception as _e:
        _safe_console_print(
            f"ERROR: output folder tidak bisa ditulis: {args.output_dir}\n       {_e}",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.bebasdns:
        args.dns = BEBASDNS_DOH_VARIANTS[args.bebasdns]
    if args.proxy_mode == "disabled":
        _set_active_proxy(None, mode="disabled")
    elif args.proxy_mode == "inherit_env":
        _set_active_proxy(None, mode="inherit_env")
    elif args.proxy is not None:
        _set_active_proxy(args.proxy or None, mode="manual" if args.proxy else "disabled")
    if args.dns is not None:
        _set_active_dns(args.dns or None)
    _set_http2_enabled(bool(args.http2) if args.http2 is not None else bool(_cli_saved_settings.get("http2_enabled", False)))
    # ── v7.5.8: feature toggles (CLI flags override saved settings) ──
    _set_deep_scan_enabled(
        (not args.no_deep_scan) if getattr(args, "no_deep_scan", False)
        else bool(_cli_saved_settings.get("deep_scan_enabled", True)))
    _set_selenium_enabled(
        (not args.no_selenium) if getattr(args, "no_selenium", False)
        else bool(_cli_saved_settings.get("selenium_enabled", True)))
    _set_serve_enabled(bool(_cli_saved_settings.get("serve_enabled", True)))
    _set_cheat_enabled(bool(_cli_saved_settings.get("cheat_enabled", True)))
    _set_itch_enabled(
        bool(args.itch) if getattr(args, "itch", False)
        else bool(_cli_saved_settings.get("itch_enabled", False)))
    gallery_dl_eff = args.gallery_dl or _cli_saved_settings.get("gallery_dl_mode", "off")
    # v7.5.6 cleanup: a `_gallery_cli_explicit` flag was computed here but
    # never used. gallery-dl CLI selection is intentionally session-only
    # (persist=False) — unlike --cloudflare, it does not save to settings.
    # Removed the dead computation; behavior unchanged.
    _set_gallery_dl_mode(gallery_dl_eff, path=args.gallery_dl_path, config=args.gallery_dl_config, persist=False)
    global _bandwidth_limit_kbps, use_cloudscraper
    _bandwidth_limit_kbps = max(0.0, float(args.bandwidth or 0.0))
    _sync_runtime_globals_to_legacy()
    _set_allow_internal_hosts(bool(getattr(args, "allow_internal_hosts", False)))
    cf_mode = "cloudscraper" if bool(args.cf_bypass) else args.cloudflare
    _cloudflare_cli_explicit = any(
        a == "--cloudflare" or a.startswith("--cloudflare=") or
        a in {"--cf-bypass", "--cloudscraper"} or
        a == "--flaresolverr-url" or a.startswith("--flaresolverr-url=") or
        a == "--flaresolverr-session" or a.startswith("--flaresolverr-session=") or
        a == "--flaresolverr-timeout" or a.startswith("--flaresolverr-timeout=") or
        a == "--flaresolverr-wait" or a.startswith("--flaresolverr-wait=") or
        a == "--flaresolverr-proxy" or a.startswith("--flaresolverr-proxy=") or
        a == "--cloudflare-priority" or a.startswith("--cloudflare-priority=")
        for a in sys.argv[1:]
    )
    _set_cloudflare_config(
        cf_mode,
        priority=args.cloudflare_priority,
        flaresolverr_url=args.flaresolverr_url,
        session_policy=args.flaresolverr_session,
        timeout=args.flaresolverr_timeout,
        wait_after=args.flaresolverr_wait,
        proxy_mode=args.flaresolverr_proxy,
        persist=_cloudflare_cli_explicit,
    )
    if args.flaresolverr_test:
        ok, msg = flaresolverr_test_connection()
        _safe_console_print(("OK: " if ok else "ERROR: ") + msg)
        return
    if getattr(args, "itch_test", False):
        ok, msg = itch_test_connection(explicit_key=os.environ.get("ITCH_API_KEY", ""))
        _safe_console_print(("OK: " if ok else "ERROR: ") + msg)
        return
    st = _load_settings(); _net_changed = False
    if args.language is not None:
        st["language"] = args.language; _net_changed = True
    if args.http2 is not None:
        st["http2_enabled"] = bool(args.http2); _net_changed = True
    if args.gallery_dl is not None:
        st["gallery_dl_mode"] = gallery_dl_eff; _net_changed = True
    if any(a == "--gallery-dl-path" or a.startswith("--gallery-dl-path=") for a in sys.argv[1:]):
        st["gallery_dl_path"] = args.gallery_dl_path; _net_changed = True
    if any(a == "--gallery-dl-config" or a.startswith("--gallery-dl-config=") for a in sys.argv[1:]):
        st["gallery_dl_config"] = args.gallery_dl_config; _net_changed = True
    if args.proxy is not None:
        st["proxy"] = args.proxy; _net_changed = True
    if args.dns is not None:
        st["dns"] = args.dns or ""; _net_changed = True
    if args.bebasdns:
        st["bebasdns_variant"] = args.bebasdns; _net_changed = True
    if _net_changed:
        _save_settings(st)

    mode_flags = [
        bool(args.zip), bool(args.both), bool(args.website), bool(args.website_folder),
        bool(args.pure_website), bool(args.pure_website_folder),
        bool(args.cyoap_vue_website), bool(args.cyoap_vue_folder),
    ]
    if sum(mode_flags) > 1:
        parser.error("Choose only one output mode.")

    if args.gui:
        launch_gui()
        return

    wait_time = args.wait_time
    _sync_runtime_globals_to_legacy()
    global _ytdlp_enabled
    _ytdlp_enabled = not bool(getattr(args, "no_ytdlp", False))
    _sync_runtime_globals_to_legacy()

    # Setup file logging early so CLI output goes to log too
    _outdir_cli = getattr(args, "output_dir", os.getcwd()) if hasattr(args, "output_dir") else os.getcwd()
    setup_file_logging(_outdir_cli)

    pure_website_mode = args.pure_website or args.pure_website_folder
    website_mode = args.website or args.website_folder or args.cyoap_vue_website or args.cyoap_vue_folder
    website_zip_output = not (args.website_folder or args.cyoap_vue_folder or args.pure_website_folder)

    if not args.list_file and not args.url:
        parser.error("Provide a URL or use --list with a batch source.")

    if args.list_file:
        items = import_queue_items_from_source(args.list_file)
        if not items:
            raise RuntimeError("No valid URLs found in batch file.")
        logger.info(f"Batch file    : {args.list_file}")
        logger.info(f"Items         : {len(items)}")
        failed_items: List[Dict[str, str]] = []
        ok = 0
        for idx, item in enumerate(items, 1):
            logger.info(f"Batch {idx}/{len(items)}: {item['url']}")
            try:
                mode_i = (item.get("mode", "") or "").lower().replace("-", "_").replace(" ", "_")
                # [STAB-rev18] Per-row flags come from the shared derivation
                # helper (parity with the GUI loop); global CLI flags are then
                # OR-ed in, exactly as before. mode_i is already canonical
                # (normalized at import), so legacy icc* aliases never appear,
                # but the helper still maps them defensively.
                _mf = _derive_mode_flags(mode_i)
                zip_i = args.zip or _mf["zip"]
                both_i = args.both or _mf["both"]
                pure_i = pure_website_mode or _mf["pure"]
                website_i = website_mode or _mf["website"]
                # website_zip default follows global flags unless this row names
                # an explicit folder/zip mode (helper decides per-row).
                if mode_i:
                    website_zip_i = _mf["website_zip"]
                else:
                    website_zip_i = website_zip_output
                # Preserve the global --cyoap-vue "auto" probe branch.
                if _mf["engine"] == "cyoap_vue" or args.cyoap_vue_website or args.cyoap_vue_folder:
                    engine_mode = "cyoap_vue"
                elif args.cyoap_vue:
                    engine_mode = "auto"
                else:
                    engine_mode = "standard"
                run_download(
                    url=item["url"],
                    file_name=item.get("filename", ""),
                    zip_output=zip_i,
                    both_output=both_i,
                    website_output=website_i,
                    website_zip_output=website_zip_i,
                    pure_website=pure_i,
                    download_fonts=args.fonts,
                    show_font_analysis=args.analyse_fonts or args.fonts,
                    output_dir=args.output_dir,
                    max_workers=args.threads,
                    engine_mode=engine_mode,
                    cyoa_mgr_enabled=args.cyoa_manager,
                    ai_api_key=resolved_ai_api_key,
                    ai_provider=ai_provider_eff,
                    ai_mode=ai_mode_eff,
                    analysis_only=args.analyse_fonts and not args.fonts,
                    archive_strategy=args.archive_strategy,
                    archive_max_pages=args.archive_max_pages,
                    archive_max_depth=args.archive_max_depth,
                )
                ok += 1
            except Exception as e:
                failed_items.append({"url": item["url"], "error": str(e)})
                logger.error(f"Failed: {e}")
        write_failed_url_log(failed_items, args.output_dir)
        logger.info(f"Batch done    : {ok}/{len(items)} succeeded")
        return

    if not args.url:
        raise RuntimeError("URL is required unless --list is used.")

    mode_name = (
        "pure-website-folder" if args.pure_website_folder else
        "pure-website"        if args.pure_website else
        "cyoap-vue-folder"    if args.cyoap_vue_folder else
        "cyoap-vue-website"   if args.cyoap_vue_website else
        "icc-folder"          if args.website_folder else
        "icc"                 if args.website else
        "both"                if args.both else
        "zip"                 if args.zip else "embed"
    )
    logger.info(f"URL          : {args.url}")
    logger.info(f"Filename     : {args.filename or '[auto]'}")
    logger.info(f"Mode         : {mode_name}")
    logger.info(f"Archive      : {args.archive_strategy} | max-pages={args.archive_max_pages} | max-depth={args.archive_max_depth}")
    logger.info(f"Threads      : {args.threads}")
    logger.info(f"Fonts        : {'yes' if args.fonts else 'no'}")
    logger.info(f"Wait on 429  : {args.wait_time}s")
    logger.info(f"Output dir   : {args.output_dir}")
    logger.info(f"HTTP/2       : {'yes' if (_HTTP2_ENABLED) else 'no'}")
    logger.info(f"gallery-dl   : {gallery_dl_eff}")
    logger.info(f"AI Assist    : {ai_mode_eff} | provider={ai_provider_eff} | model={ai_model_eff} | key={'not needed' if ai_provider_eff == 'ollama' else ('yes' if bool(resolved_ai_api_key) else 'no')} | storage={ai_storage_eff}")
    logger.info(f"Cloudflare   : {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
    if _CLOUDFLARE_MODE == "flaresolverr" or _CLOUDFLARE_MODE == "auto":
        logger.info(f"FlareSolverr : {_FLARESOLVERR_URL} | session={_FLARESOLVERR_SESSION_POLICY} | timeout={_FLARESOLVERR_TIMEOUT}s")
    logger.info(f"Proxy        : {args.proxy if args.proxy is not None else '[saved/env/system]'}")
    logger.info(f"DNS          : {args.dns or '[system]'}" + (f" (BebasDNS {args.bebasdns})" if args.bebasdns else ""))

    engine_mode = "cyoap_vue" if (args.cyoap_vue_website or args.cyoap_vue_folder) else ("auto" if args.cyoap_vue else "standard")
    run_download(
        url=args.url,
        file_name=args.filename,
        zip_output=args.zip,
        both_output=args.both,
        website_output=website_mode,
        website_zip_output=website_zip_output,
        pure_website=pure_website_mode,
        download_fonts=args.fonts,
        show_font_analysis=args.analyse_fonts or args.fonts,
        output_dir=args.output_dir,
        max_workers=args.threads,
        engine_mode=engine_mode,
        cyoa_mgr_enabled=args.cyoa_manager,
        ai_api_key=resolved_ai_api_key,
        ai_provider=ai_provider_eff,
        ai_mode=ai_mode_eff,
        analysis_only=args.analyse_fonts and not args.fonts,
        archive_strategy=args.archive_strategy,
        archive_max_pages=args.archive_max_pages,
        archive_max_depth=args.archive_max_depth,
    )
    # ── v7.5.8 Item 8 (rewritten v7.6): optional itch.io pass via itch-dl ──
    if _ITCH_ENABLED and _is_itch_url(args.url):
        logger.info("itch.io downloader enabled — invoking itch-dl backend.")
        try:
            res = download_itch_assets(
                args.url, args.output_dir, explicit_key="",
                mirror_web=bool(getattr(args, "itch_mirror_web", False)))
            logger.info(f"[itch] {res.get('message','')}")
        except Exception as e:
            logger.warning(f"[itch] downloader error (CYOA result unaffected): {e}")
    if args.serve:
        # Respect the serve toggle in the CLI path too (Item 6 parity with GUI).
        if not _SERVE_ENABLED:
            logger.info("Serve preview disabled by toggle — not starting CLI server.")
            return
        import http.server as _http_server
        import webbrowser as _webbrowser

        serve_dir = globals().get("_LAST_PREVIEW_FOLDER") or args.output_dir
        serve_dir = os.path.abspath(serve_dir)
        if not os.path.isdir(serve_dir):
            logger.warning(f"Serve skipped: preview folder not found: {serve_dir}")
            logger.warning("Tip: use --icc-folder or --pure-website-folder when you want to preview immediately after download.")
            return

        # Mint a fresh preview session token for this CLI serve.
        _cli_preview_token = _new_preview_token()
        logger.info(f"Serving {serve_dir} on 127.0.0.1…")

        class _NoCacheCLIHandler(_http_server.SimpleHTTPRequestHandler):
            '''CLI/GUI-independent local preview server with visible Serve Tools.

            v7.4.9 makes the tools visible in the browser by default and uses visible button controls. Earlier CLI
            serve mode only opened a plain static server, so IntCyoaEnhancer/Serve
            Tools did not appear unless the GUI server path was used.
            '''
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=serve_dir, **kw)

            def log_message(self, fmt, *args):
                logger.debug("[serve] " + (fmt % args if args else fmt))

            def end_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                # Never clear preview storage on ordinary HTML requests. ICC Plus
                # stores builds in IndexedDB/localStorage, and data loss here is worse
                # than stale cache. Clearing is explicit through /__clear_cache__.
                super().end_headers()

            def _send_bytes(self, data: bytes, ctype: str = "text/html; charset=utf-8", status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("X-CYOA-Serve-Tools", "route-or-injected")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def _send_text(self, text_value: str, ctype: str = "text/html; charset=utf-8", status: int = 200) -> None:
                self._send_bytes(text_value.encode("utf-8"), ctype=ctype, status=status)

            def _overlay_script(self) -> str:
                info = _INT_CYOA_ENHANCER_INFO
                remote = info.get("raw_url", "https://update.greasyfork.org/scripts/438947/IntCyoaEnhancer.user.js")
                source = info.get("source_url", "https://greasyfork.org/en/scripts/438947-intcyoaenhancer")
                credit = info.get("credit", "IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947")
                return f'''<script id="cyoa-serve-tools-auto">
(function(){{
  if (window.__CYOA_SERVE_TOOLS_AUTO__) return;
  window.__CYOA_SERVE_TOOLS_AUTO__ = true;
  const ICE_REMOTE_URL = {json.dumps(remote)};
  const ICE_SOURCE_URL = {json.dumps(source)};
  const ICE_CREDIT = {json.dumps(credit)};
  const CHEAT_ENABLED = {json.dumps(bool(_CHEAT_ENABLED))};
  const qs = new URLSearchParams(location.search);
  if (qs.get('no_tools') === '1' || qs.get('tools') === '0' || qs.get('serve_tools') === '0') return;
  function css() {{
    if (document.getElementById('cyoa-serve-tools-style')) return;
    const s = document.createElement('style');
    s.id = 'cyoa-serve-tools-style';
    s.textContent = `
#cyoaServeTools{{position:fixed!important;top:12px!important;right:12px!important;bottom:auto!important;z-index:2147483647!important;width:310px;background:#111827;color:#f9fafb;border:1px solid #374151;border-radius:14px;box-shadow:0 18px 48px rgba(0,0,0,.42);font:13px/1.45 system-ui,-apple-system,Segoe UI,Arial,sans-serif;overflow:hidden}}
#cyoaServeTools *{{box-sizing:border-box}}
#cyoaServeTools .h{{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:#1f2937;font-weight:700}}
#cyoaServeTools .b{{padding:11px 12px;display:grid;gap:8px}}
#cyoaServeTools button,#cyoaServeTools a{{border:0;border-radius:9px;padding:8px 10px;background:#2563eb;color:white;text-decoration:none;cursor:pointer;text-align:center;font:inherit}}
#cyoaServeTools .row{{display:grid;grid-template-columns:1fr 1fr;gap:7px}}
#cyoaServeTools .muted{{color:#9ca3af;font-size:11px}}
#cyoaServeTools .credit{{border-top:1px solid #374151;padding-top:8px;color:#d1d5db;font-size:11px}}
#cyoaServeTools.min .b{{display:none}}
#cyoaServeTools .secondary{{background:#374151}}
#cyoaServeTools .warn{{background:#b45309}}
`;
    document.head.appendChild(s);
  }}
  function loadScript(src, id) {{
    return new Promise((resolve, reject) => {{
      if (id && document.getElementById(id)) return resolve();
      const el = document.createElement('script');
      if (id) el.id = id;
      el.src = src;
      el.onload = resolve;
      el.onerror = reject;
      document.documentElement.appendChild(el);
    }});
  }}
  async function clearStorage() {{
    try {{ localStorage.clear(); sessionStorage.clear(); }} catch(e) {{}}
    try {{ if ('caches' in window) {{ const keys = await caches.keys(); await Promise.all(keys.map(k => caches.delete(k))); }} }} catch(e) {{}}
    try {{ if ('serviceWorker' in navigator) {{ const regs = await navigator.serviceWorker.getRegistrations(); await Promise.all(regs.map(r => r.unregister())); }} }} catch(e) {{}}
    alert('Preview storage cleared. Reloading...'); location.href='/?cb='+Date.now();
  }}
  async function loadLocalICE() {{
    try {{ await loadScript('/__userscripts__/intcyoaenhancer.user.js?cb='+Date.now(), 'cyoa-intcyoaenhancer-local'); alert('Bundled IntCyoaEnhancer Cheat helper requested. Credit: '+ICE_CREDIT); }}
    catch(e) {{ alert('Bundled IntCyoaEnhancer Cheat helper.user.js not found. Put it in the served folder, userscripts/, or serve_userscripts/.\\n\\nSource: '+ICE_SOURCE_URL); }}
  }}
  async function loadRemoteICE() {{
    if (!confirm('Load IntCyoaEnhancer from GreasyFork for this localhost preview only?\\n\\nCredit: '+ICE_CREDIT+'\\n\\nThis does not modify downloaded files.')) return;
    try {{ await loadScript(ICE_REMOTE_URL, 'cyoa-intcyoaenhancer-remote'); }}
    catch(e) {{ alert('Remote load failed. Download the .user.js manually and place it in userscripts/.'); }}
  }}
  function revealDisabled() {{
    document.querySelectorAll('[disabled],.disabled,.is-disabled,.locked,.unavailable').forEach(el => {{ try {{ el.disabled=false; el.classList.remove('disabled','is-disabled','locked','unavailable'); el.style.pointerEvents='auto'; el.style.opacity='1'; }} catch(e) {{}} }});
  }}
  window.$serveTools = {{
    credit: ICE_CREDIT,
    source: ICE_SOURCE_URL,
    loadLocalIntCyoaEnhancer: loadLocalICE,
    loadRemoteIntCyoaEnhancer: loadRemoteICE,
    clearStorage,
    revealDisabled
  }};
  function ui() {{
    if (document.getElementById('cyoaServeTools')) return;
    css();
    const d = document.createElement('div'); d.id = 'cyoaServeTools';
    d.innerHTML = `<div class="h"><span>⚡ Serve Tools</span><button class="secondary" data-a="min">—</button></div>
<div class="b">
  <div class="row">${{CHEAT_ENABLED ? '<button data-a="local">Bundled ICE</button>' : '<button class="secondary" disabled>ICE disabled</button>'}}<button data-a="remote">GreasyFork ICE</button></div>
  <div class="row"><button class="secondary" data-a="clear">Clear State</button><button class="secondary" data-a="reveal">Reveal UI</button></div>
  <a href="/__serve_tools__" target="_blank">Open full tools page</a>
  <a class="secondary" href="?no_tools=1">Clean preview</a>
  <div class="credit"><strong>Credit:</strong> IntCyoaEnhancer by agreg · MIT License · GreasyFork script 438947<br><span class="muted">Serve-only optional loader. Downloaded files are not modified.</span></div>
</div>`;
    d.addEventListener('click', ev => {{ const a = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-a'); if(!a) return; if(a==='min') d.classList.toggle('min'); if(a==='local') loadLocalICE(); if(a==='remote') loadRemoteICE(); if(a==='clear') clearStorage(); if(a==='reveal') revealDisabled(); }});
    window.__cyoaToggleServeTools=function(){{ d.classList.toggle('min'); d.style.setProperty('display','block','important'); d.style.setProperty('visibility','visible','important'); return !d.classList.contains('min'); }};
    window.__cyoaOpenServeTools=function(){{ d.classList.remove('min'); d.style.setProperty('display','block','important'); d.style.setProperty('visibility','visible','important'); return true; }};
    (document.body || document.documentElement).appendChild(d);
    const ice = qs.get('load_ice');
    if (ice === 'local') setTimeout(loadLocalICE, 600);
    if (ice === 'web' || ice === 'remote' || ice === 'greasyfork') setTimeout(loadRemoteICE, 600);
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ui); else ui();
}})();
</script>'''

            def _inject_tools(self, html_text: str) -> str:
                script = self._overlay_script()
                if "cyoa-serve-tools-auto" in html_text:
                    return html_text
                lower = html_text.lower()
                idx = lower.rfind("</body>")
                if idx >= 0:
                    return html_text[:idx] + script + html_text[idx:]
                return html_text + script

            def _serve_tools_page(self) -> None:
                info = _INT_CYOA_ENHANCER_INFO
                page = f'''<!doctype html><meta charset="utf-8"><title>CYOA Serve Tools</title>
<style>body{{font:15px/1.55 system-ui,Segoe UI,Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:0}}main{{max-width:920px;margin:auto;padding:28px}}.card{{background:#111827;border:1px solid #374151;border-radius:14px;padding:18px;margin:14px 0}}a,button{{display:inline-block;margin:5px 6px 5px 0;padding:9px 12px;border-radius:9px;background:#2563eb;color:white;text-decoration:none;border:0;cursor:pointer}}.muted{{color:#9ca3af}}code{{background:#1f2937;padding:2px 5px;border-radius:5px}}</style>
<main><h1>⚡ CYOA Serve Tools</h1><p class="muted">Visible local preview helpers for downloaded/offline CYOAs. The overlay is now injected automatically into HTML previews. Use <code>?no_tools=1</code> for a clean preview.</p>
<div class="card"><h2>Open Preview</h2><a href="/?cb=tools">Open preview with auto tools</a><a href="/?no_tools=1">Open clean preview</a><a href="/__clear_cache__">Clear state and reopen</a></div>
<div class="card"><h2>Userscript Lab</h2><a href="/?load_ice=local&cb=local">Open + bundled ICE Cheat Panel</a><a href="/?load_ice=web&cb=web">Open + GreasyFork IntCyoaEnhancer</a><a href="/__userscripts__/intcyoaenhancer.meta.json" target="_blank">Metadata</a><p><strong>Credit:</strong> {info.get('credit')}</p><p class="muted">Source: <a href="{info.get('source_url')}" target="_blank">{info.get('source_url')}</a>. Optional Serve-only loader; downloaded files are not modified.</p></div>
<div class="card"><h2>Console helpers</h2><p>In preview pages, use <code>window.$serveTools</code> for native helpers. If IntCyoaEnhancer loads successfully, it may also expose its own debug helpers.</p></div>
</main>'''
                self._send_text(page)

            def _serve_userscript_meta(self) -> None:
                payload = dict(_INT_CYOA_ENHANCER_INFO)
                payload.update({
                    "bundled": True,
                    "bundled_available": True,
                    "bundled_size_bytes": len(_BUNDLED_INTCYOAENHANCER_USERSCRIPT.encode('utf-8')),
                    "route": "/__userscripts__/intcyoaenhancer.user.js",
                    "cheat_enabled": bool(_CHEAT_ENABLED),
                    "integration_policy": "Serve-only bundled helper. No network download required.",
                })
                self._send_text(json.dumps(payload, indent=2), ctype="application/json; charset=utf-8")

            def _serve_local_intcyoaenhancer(self) -> None:
                candidates = [
                    os.path.join(serve_dir, "IntCyoaEnhancer.user.js"),
                    os.path.join(serve_dir, "userscripts", "IntCyoaEnhancer.user.js"),
                    os.path.join(serve_dir, "serve_userscripts", "IntCyoaEnhancer.user.js"),
                ]
                prefix = f"/* Served by CYOA Downloader v{_APP_VERSION}. {_INT_CYOA_ENHANCER_INFO.get('credit')} | Source: {_INT_CYOA_ENHANCER_INFO.get('source_url')} | Bundled localhost helper; no network download required. */\n"
                for c in candidates:
                    if os.path.isfile(c):
                        try:
                            body = pathlib.Path(c).read_text(encoding="utf-8", errors="ignore")
                            return self._send_text(prefix + body, ctype="application/javascript; charset=utf-8")
                        except Exception as e:
                            logger.warning(f"Local IntCyoaEnhancer override failed, serving bundled helper instead: {e}")
                return self._send_text(prefix + _BUNDLED_INTCYOAENHANCER_USERSCRIPT, ctype="application/javascript; charset=utf-8", status=200)

            def do_GET(self):
                from urllib.parse import urlparse as _urlparse
                parsed = _urlparse(self.path)
                route = parsed.path or "/"
                query = parsed.query or ""

                if route == "/__serve_tools__":
                    return self._serve_tools_page()
                if route == "/__serve_tools__/status.json":
                    return self._send_text(json.dumps({
                        'ok': True,
                        'version': _APP_VERSION,
                        'server': 'cli-serve',
                        'injection': 'html-intercept',
                        'credit': _INT_CYOA_ENHANCER_INFO.get('credit',''),
                        'source': _INT_CYOA_ENHANCER_INFO.get('source_url','')
                    }, indent=2), ctype="application/json; charset=utf-8")
                if route == "/__userscripts__/intcyoaenhancer.meta.json":
                    return self._serve_userscript_meta()
                if route == "/__userscripts__/intcyoaenhancer.user.js":
                    if not _CHEAT_ENABLED:
                        return self._send_text(
                            "// Cheat panel disabled by toggle in CYOA Downloader settings.\n",
                            ctype="application/javascript; charset=utf-8")
                    from urllib.parse import parse_qs as _parse_qs
                    _rt = (_parse_qs(query or "").get("ptok", [""]) or [""])[0]
                    if _rt and not _preview_token_valid(_rt):
                        logger.info("[cli-serve] Cheat helper rejected: stale preview session token.")
                        return self._send_text(
                            "// Stale preview session — this tab belongs to a closed preview.\n",
                            ctype="application/javascript; charset=utf-8")
                    return self._serve_local_intcyoaenhancer()
                if route == "/__clear_cache__":
                    stamp = str(int(time.time() * 1000))
                    _ptok = _current_preview_token()
                    html_text = f'''<!doctype html><meta charset="utf-8"><title>Clearing preview cache...</title><script>
(async()=>{{try{{localStorage.clear();sessionStorage.clear();}}catch(e){{}}try{{if('caches' in window){{const n=await caches.keys();await Promise.all(n.map(k=>caches.delete(k)));}}}}catch(e){{}}try{{if('serviceWorker' in navigator){{const r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(x=>x.unregister()));}}}}catch(e){{}}location.replace('/?cb={stamp}&ptok={_ptok}');}})();
</script><p>Clearing preview cache...</p>'''
                    data = html_text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Clear-Site-Data", '"cache", "storage"')
                    self.end_headers()
                    self.wfile.write(data)
                    return

                disabled = any(flag in query for flag in ("no_tools=1", "serve_tools=0", "cyoa_tools=0", "tools=0"))
                if not disabled:
                    try:
                        fs_path = self.translate_path(self.path)
                        if os.path.isdir(fs_path):
                            fs_path = os.path.join(fs_path, "index.html")
                        ext = os.path.splitext(fs_path)[1].lower()
                        if os.path.isfile(fs_path) and ext in {".html", ".htm", ""}:
                            raw = pathlib.Path(fs_path).read_text(encoding="utf-8", errors="ignore")
                            return self._send_text(self._inject_tools(raw), ctype="text/html; charset=utf-8")
                    except Exception as e:
                        logger.debug(f"CLI Serve Tools injection skipped: {e}")
                return super().do_GET()


        host = "127.0.0.1"
        port_req = int(args.serve_port or 0)
        with _http_server.ThreadingHTTPServer((host, port_req), _NoCacheCLIHandler) as httpd:
            port = int(httpd.server_address[1])
            clear_url = f"http://{host}:{port}/__clear_cache__?cb={int(time.time()*1000)}&ptok={_cli_preview_token}"
            logger.info(f"Open {clear_url} to preview with cleared browser storage.")
            try:
                _webbrowser.open(clear_url)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in main (line 17233): %s", _ignored_exc)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                logger.info("Local server stopped")
            finally:
                _clear_preview_token()


__all__ = ["main", "_sync_legacy_globals"]
