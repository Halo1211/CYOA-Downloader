
"""Tkinter GUI application shell and base class.

Phase 48 moved the large ``CYOADownloaderGUI`` class body out of the original
single-file script while keeping behavior frozen. The class methods were copied
mechanically, and compatibility globals are synchronized immediately before the
final GUI patch stack is composed.
"""

from __future__ import annotations

import uuid
from collections import Counter


def _responsive_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Return safe initial/minimum sizes for the available laptop display."""
    sw = max(1, int(screen_width or 1))
    sh = max(1, int(screen_height or 1))
    available_width = max(320, sw - 32)
    available_height = max(320, sh - 88)
    safe_width = max(320, min(1100, available_width))
    safe_height = max(320, min(720, available_height))
    min_width = max(320, min(900, available_width))
    min_height = max(320, min(640, available_height))
    return safe_width, safe_height, min_width, min_height


def _sync_legacy_globals(namespace: dict) -> type:
    """Expose legacy module globals to mechanically moved GUI methods.

    The moved methods intentionally keep their original global-name lookups.
    During the transition, ``runtime.surface`` calls this once after
    CLI/download helpers are defined and before the GUI patch stack is composed.
    """
    globals().update({
        key: value
        for key, value in namespace.items()
        if not (key.startswith("__") and key.endswith("__"))
    })
    return CYOADownloaderGUI


def launch_gui() -> None:
    try:
        import customtkinter as ctk
    except ImportError:
        # Fallback to plain tkinter with install hint
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "customtkinter not found",
                "Run:\n  pip install customtkinter\n\nFor a better GUI experience.",
            )
            root.destroy()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in launch_gui (line 4691): %s", _ignored_exc)
        print("customtkinter not found. Install: pip install customtkinter")
        sys.exit(1)

    _theme_boot = _load_settings()
    ctk.set_appearance_mode(_normalize_theme_mode(_theme_boot.get("theme_mode", "System")).lower())
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    CYOADownloaderGUI(root)
    root.mainloop()

class CYOADownloaderGUI:
    # ── mode definitions ────────────────────────────────────────────
    MODES = [
        # (val,  icon, name,                 desc,                         section)
        ("__sec__",            "", "OUTPUT MODE",        "",                              ""),
        ("auto",               "⚡","Auto (detect)",      "Default: Settings",            ""),
        ("embed",              "📄","Embedded JSON",      "Images embedded in JSON",  ""),
        ("zip",                "🗜","ZIP",                "JSON + separate assets",        ""),
        ("both",               "📦","Both",               "Embed + ZIP together",         ""),
        ("__sec__",            "", "ICC MODE",           "",                              ""),
        ("website_zip",        "🌐","ICC ZIP",            "Offline viewer files",           ""),
        ("website_folder",     "📁","ICC Folder",         "Offline viewer files",           ""),
        ("__sec__",            "", "PURE WEBSITE",       "",                              ""),
        ("pure_website_zip",   "🔒","Pure Website ZIP",   "Viewer only, no project scan",""),
        ("pure_website_folder","🔓","Pure Website Folder","Viewer only, no project scan",""),
        ("__sec__",            "", "CYOAP_VUE",          "",                              ""),
        ("cyoap_vue_zip",      "⚡","cyoap_vue ZIP",      "cyoap_vue engine backup",       ""),
        ("cyoap_vue_folder",   "⚡","cyoap_vue Folder",   "cyoap_vue engine backup",       ""),
    ]

    # These are the modes exposed by the main GUI.  Queue rows use the same
    # canonical values as the batch importer/dispatcher.
    QUEUE_MODE_OPTIONS = (
        "auto", "embed", "zip", "both", "website_zip", "website_folder",
        "pure_website_zip", "pure_website_folder",
        "cyoap_vue_zip", "cyoap_vue_folder",
    )

    BADGE_COLORS = {
        "auto":                ("#1e3a8a", "#93c5fd"),
        "embed":               ("#1e3a5f", "#60a5fa"),
        "zip":                 ("#1e1e3b", "#a78bfa"),
        "both":                ("#374151", "#d1d5db"),
        "website_zip":         ("#065f46", "#6ee7b7"),
        "website_folder":      ("#065f46", "#6ee7b7"),
        "pure_website_zip":    ("#4c1d95", "#c4b5fd"),
        "pure_website_folder": ("#4c1d95", "#c4b5fd"),
        "cyoap_vue_zip":       ("#78350f", "#fde68a"),
        "cyoap_vue_folder":    ("#78350f", "#fde68a"),
    }

    def _init_base(self, root) -> None:
        import customtkinter as ctk
        self.root = root
        self.root.title(f"CYOA Downloader v{_APP_VERSION}")
        self._window_icon = _load_window_icon_photo(self.root)
        if self._window_icon is not None:
            try:
                self.root.iconphoto(True, self._window_icon)
                # Apply it explicitly to the root window too; this avoids
                # Windows retaining the launcher/process icon in the title bar.
                self.root.wm_iconphoto(False, self._window_icon)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception while setting GUI icon: %s", _ignored_exc)
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1366, 768
        initial_w, initial_h, min_w, min_h = _responsive_window_geometry(sw, sh)
        self.root.minsize(min_w, min_h)
        self.root.geometry(f"{initial_w}x{initial_h}")

        # Windows can restore the launcher icon when the Tk window is first
        # realized. Re-apply the canonical emblem after the window is visible.
        def _reapply_window_icon() -> None:
            try:
                if self._window_icon is not None and self.root.winfo_exists():
                    self.root.iconphoto(False, self._window_icon)
                    self.root.wm_iconphoto(False, self._window_icon)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception while refreshing GUI icon: %s", _ignored_exc)

        try:
            self.root.after_idle(_reapply_window_icon)
            self.root.after(250, _reapply_window_icon)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception while scheduling GUI icon refresh: %s", _ignored_exc)

        self._log_queue: log_queue_module.Queue = log_queue_module.Queue(maxsize=5000)
        self._queue_data: List[Dict] = []
        self._queue_rows: List = []
        self._is_running  = False
        self._paused      = threading.Event()
        self._paused.set()   # not paused initially (set = running)
        self._speed_samples: List[Tuple[float, int]] = []   # (timestamp, bytes)
        _ai_settings = _load_settings()
        self._ai_provider = _normalize_ai_provider(_ai_settings.get("ai_provider", "anthropic"))
        self._ai_key_storage = _normalize_ai_key_storage(_ai_settings.get("ai_key_storage", "session"))
        self._ai_api_key  = ""  # session-only in-memory key. Secrets are not loaded into GUI by default.
        if self._ai_key_storage == "plain":
            self._ai_api_key = _resolve_ai_api_key(storage="plain", provider=self._ai_provider)
        self._ai_enabled  = _ai_settings.get("ai_enabled", False)
        self._ai_model    = _get_ai_model(self._ai_provider)
        self._ai_mode     = _normalize_ai_mode(_ai_settings.get("ai_mode", "auto_fallback"))
        self._mode_var    = "auto"
        self._mode_btns: Dict = {}
        _theme_settings = _load_settings()
        self._theme_mode = _normalize_theme_mode(_theme_settings.get("theme_mode", "System"))
        self._theme_accent = _normalize_accent_color(_theme_settings.get("theme_accent_color", "#3b82f6"))
        self._is_dark     = _resolve_theme_is_dark(self._theme_mode)
        self._language    = _theme_settings.get("language", "en") if _theme_settings.get("language", "en") in {"id", "en"} else "en"
        self._themed: List = []
        self._last_results: List[Dict] = []
        # URLs captured at the moment a download starts. If new queue items are
        # added while a worker is running, _done() must not clear those newer
        # rows after the current run completes.
        self._active_run_urls: Set[str] = set()
        self._server_thread = None
        self._server_obj    = None
        self._server_folder = None
        # Track one-window panels so repeated button clicks focus the existing
        # panel instead of creating duplicate dialogs.
        self._singleton_windows: Dict[str, Any] = {}
        self._ytdlp_enabled = True  # default on; unchecked if yt-dlp not installed
        _load_cloudflare_settings()

        # ── v7.5.8: apply persisted feature toggles into execution gates ──
        _s = _load_settings()
        _set_deep_scan_enabled(_s.get("deep_scan_enabled", True))
        _set_selenium_enabled(_s.get("selenium_enabled", True))
        _set_serve_enabled(_s.get("serve_enabled", True))
        _set_cheat_enabled(_s.get("cheat_enabled", True))
        _set_itch_enabled(_s.get("itch_enabled", False))

        # Keep only the selected cookie-file path in the GUI/settings layer.
        # The Netscape cookie contents are never loaded into settings or logs.
        _cookie_setting = str(_s.get("ytdlp_cookies", "") or "").strip()
        if not _cookie_setting:
            _cookie_setting = os.environ.get("CYOA_YTDLP_COOKIES", "").strip()
        self._ytdlp_cookies_var = ctk.StringVar(
            value=os.path.abspath(os.path.expanduser(_cookie_setting)) if _cookie_setting else "")

        self._setup_ui()
        self._apply_language()
        self._setup_logging()
        self._poll_log()

    def __init__(self, root) -> None:
        """Compose the final v46 GUI initialization from the app class."""
        final_init = globals().get("_v46_gui_init")
        if final_init is None:
            return self._init_base(root)
        return final_init(self, root)

    def _p(self) -> Dict[str, str]:
        """Return current palette."""
        accent = _normalize_accent_color(getattr(self, "_theme_accent", "#3b82f6"))
        if self._is_dark:
            return {
                "bg":        "#0e1117", "panel":    "#0a0d13",
                "surface":   "#141922", "surface2": "#1e2433",
                "fg":        "#e2e8f0", "muted":    "#475569",
                "muted2":    "#334155", "accent":   accent,
                "border":    "#1e2433", "separator": "#202a3a", "toolbar_separator": "#33465f", "toolbar_separator_shadow": "#162033", "sidebar":  "#0a0d13",
                "input_bg":  "#141922", "input_fg": "#e2e8f0",
                "log_bg":    "#0a0d13", "log_fg":   "#475569",
                "sel_row":   "#0f1729", "sel_bar":  "#3b82f6",
                "sel_icon":  "#1e3a5f", "sel_nm":   "#60a5fa",
                "sel_desc":  "#3b82f6",
                # theme-aware semantic colors
                "danger_bg": "#1f0a0a", "danger_fg": "#f87171",
                "danger_hv": "#3b0f0f",
                "accentbg":  "#0c1a2e", "accentbg_hv": "#162b4a",
                "srv_fg":    "#6ee7b7", "srv_hv":   "#065f46",
                "retry_asset_bg": "#3b2506", "retry_asset_fg": "#fbbf24",
                "retry_asset_hv": "#5a3708",
                "retry_image_bg": "#2e1065", "retry_image_fg": "#c4b5fd",
                "retry_image_hv": "#4c1d95",
                "retry_audio_bg": "#083344", "retry_audio_fg": "#67e8f9",
                "retry_audio_hv": "#155e75",
                "settings_bg": "#052e16", "settings_fg": "#86efac",
                "settings_hv": "#064e3b",
                "manager_bg": "#2e1065", "manager_fg": "#d8b4fe",
                "manager_hv": "#4c1d95",
            }
        return {
            "bg":        "#f1f5f9", "panel":    "#ffffff",
            "surface":   "#e2e8f0", "surface2": "#cbd5e1",
            "fg":        "#0f172a", "muted":    "#64748b",
            "muted2":    "#94a3b8", "accent":   accent,
            "border":    "#e2e8f0", "separator": "#d7dee8", "toolbar_separator": "#cbd5e1", "toolbar_separator_shadow": "#e2e8f0", "sidebar":  "#f8fafc",
            "input_bg":  "#ffffff", "input_fg": "#0f172a",
            "log_bg":    "#f8fafc", "log_fg":   "#475569",
            "sel_row":   "#dbeafe", "sel_bar":  "#3b82f6",
            "sel_icon":  "#bfdbfe", "sel_nm":   "#1d4ed8",
            "sel_desc":  "#2563eb",
            # theme-aware semantic colors
            "danger_bg": "#fee2e2", "danger_fg": "#dc2626",
            "danger_hv": "#fecaca",
            "accentbg":  "#dbeafe", "accentbg_hv": "#bfdbfe",
            "srv_fg":    "#059669", "srv_hv":   "#d1fae5",
            "retry_asset_bg": "#fef3c7", "retry_asset_fg": "#b45309",
            "retry_asset_hv": "#fde68a",
            "retry_image_bg": "#ede9fe", "retry_image_fg": "#6d28d9",
            "retry_image_hv": "#ddd6fe",
            "retry_audio_bg": "#cffafe", "retry_audio_fg": "#0e7490",
            "retry_audio_hv": "#a5f3fc",
            "settings_bg": "#dcfce7", "settings_fg": "#166534",
            "settings_hv": "#bbf7d0",
            "manager_bg": "#f3e8ff", "manager_fg": "#7e22ce",
            "manager_hv": "#e9d5ff",
        }

    def _apply_theme_base(self) -> None:
        """Re-apply palette to all tracked widgets + sidebar + queue rows + log."""
        import customtkinter as ctk
        self._theme_mode = _normalize_theme_mode(getattr(self, "_theme_mode", "System"))
        self._is_dark = _resolve_theme_is_dark(self._theme_mode)
        p = self._p()
        ctk.set_appearance_mode(self._theme_mode.lower())

        for widget, keys in self._themed:
            try:
                widget.configure(**{k: p[v] for k, v in keys.items()})
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4858): %s", _ignored_exc)

        # Sidebar
        try:
            self._sidebar.configure(fg_color=p["sidebar"],
                                    scrollbar_button_color=p["surface2"],
                                    scrollbar_button_hover_color=p["muted2"])
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _apply_theme (line 4866): %s", _ignored_exc)

        # Sidebar section labels
        if hasattr(self, "_sec_labels"):
            for lbl in self._sec_labels:
                try: lbl.configure(text_color=p["muted2"])
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _apply_theme (line 4873): %s", _ignored_exc)
        if hasattr(self, "_sec_dividers"):
            for div in self._sec_dividers:
                try: div.configure(fg_color=p.get("separator", p["border"]))
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _apply_theme (line 4877): %s", _ignored_exc)

        # Mode buttons
        self._select_mode(self._mode_var)

        # Info box in sidebar
        if hasattr(self, "_info_box"):
            try:
                p2 = self._p()
                self._info_box.configure(fg_color=p2["surface2"])
                self._info_body.configure(text_color=p2["muted"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4888): %s", _ignored_exc)
        self._update_mode_info(self._mode_var if hasattr(self, "_mode_var") else "auto")

        # Queue scrollable frame bg
        if hasattr(self, "_qlist"):
            try:
                self._qlist.configure(
                    fg_color=p["bg"],
                    scrollbar_button_color=p["surface2"],
                    scrollbar_button_hover_color=p["muted2"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4899): %s", _ignored_exc)

        # Row B scrollable frame + scrollbar
        if hasattr(self, "_rowB"):
            try:
                self._rowB.configure(
                    fg_color=p["panel"],
                    scrollbar_button_color=p["surface2"],
                    scrollbar_button_hover_color=p["muted"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4909): %s", _ignored_exc)

        # Queue rows
        for row, dot, url_lbl, badge, rm in self._queue_rows:
            try:
                row.configure(fg_color=p["surface"],  border_color=p["border"])
                dot.configure(bg=p["surface"])
                url_lbl.configure(text_color=p["muted"])
                rm.configure(fg_color="transparent", hover_color=p["surface2"],
                             text_color=p["muted"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4920): %s", _ignored_exc)

        # Log widget
        if hasattr(self, "_log_txt"):
            try:
                self._log_txt.configure(bg=p["log_bg"], fg=p["log_fg"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4927): %s", _ignored_exc)

        # Theme/language pills
        if hasattr(self, "_theme_pill"):
            try:
                self._theme_pill.configure(
                    fg_color=p["surface2"],
                    selected_color=p["accent"],
                    unselected_color=p["surface2"],
                )
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4938): %s", _ignored_exc)
        if hasattr(self, "_lang_pill"):
            try:
                self._lang_pill.configure(
                    fg_color=p["surface2"],
                    selected_color=p["accent"],
                    unselected_color=p["surface2"],
                )
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4947): %s", _ignored_exc)

        # Speed graph widgets (tk.Canvas + tk.Label — not CTk)
        if hasattr(self, "_speed_canvas"):
            try:
                self._speed_canvas.configure(bg=p["surface2"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4954): %s", _ignored_exc)
        if hasattr(self, "_speed_label"):
            try:
                self._speed_label.configure(bg=p["panel"], fg=p["muted"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _apply_theme (line 4959): %s", _ignored_exc)


    # ════════════════════════════════════════════════════════════════
    def _apply_theme(self) -> None:
        """Apply the final v46.5+ GUI theme behavior directly on the class."""
        self._apply_theme_base()
        _v465_configure_log_tags(self)
        # The progress/telemetry card is built once from the active palette.
        # Rebuild only when idle so live download widgets are not destroyed.
        if not getattr(self, "_is_running", False):
            try:
                _v463_rebuild_progress_workspace(self)
            except Exception as exc:
                logger.debug(f"Progress workspace re-theme skipped: {exc}")

    # SINGLETON WINDOW GUARDS
    # ════════════════════════════════════════════════════════════════
    def _focus_singleton_window(self, key: str):
        """Focus an already-open utility panel if it still exists."""
        wins = getattr(self, "_singleton_windows", None)
        if not isinstance(wins, dict):
            self._singleton_windows = {}
            return None
        win = wins.get(key)
        if win is None:
            return None
        try:
            if win.winfo_exists():
                try: win.deiconify()
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _focus_singleton_window (line 4978): %s", _ignored_exc)
                try: win.lift()
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _focus_singleton_window (line 4980): %s", _ignored_exc)
                try: win.focus_force()
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _focus_singleton_window (line 4982): %s", _ignored_exc)
                return win
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _focus_singleton_window (line 4984): %s", _ignored_exc)
        try:
            wins.pop(key, None)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _focus_singleton_window (line 4988): %s", _ignored_exc)
        return None

    def _make_singleton_window(self, key: str):
        """Create a CTkToplevel once; repeated calls only focus the old one.

        This is intentionally small and reversible: it changes only the GUI
        window lifecycle, not the handlers, downloader, settings schema, or
        output contract.
        """
        import customtkinter as ctk
        existing = self._focus_singleton_window(key)
        if existing is not None:
            return None
        if not isinstance(getattr(self, "_singleton_windows", None), dict):
            self._singleton_windows = {}
        win = ctk.CTkToplevel(self.root)
        self._apply_window_icon_to(win)
        self._singleton_windows[key] = win

        def _cleanup(event=None, _key=key, _win=win):
            try:
                if event is not None and getattr(event, "widget", None) is not _win:
                    return
                if getattr(self, "_singleton_windows", {}).get(_key) is _win:
                    self._singleton_windows.pop(_key, None)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _cleanup (line 5014): %s", _ignored_exc)

        try:
            win.bind("<Destroy>", _cleanup, add="+")
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _make_singleton_window (line 5019): %s", _ignored_exc)

        # A modal Toplevel keeps a Tk grab. If Windows minimizes that window,
        # the hidden grab can make the main window appear impossible to
        # restore. Release it while the utility window is unmapped and restore
        # it only after the utility window comes back.
        grab_state = {"released": False}

        def _release_grab_on_unmap(event=None, _win=win):
            try:
                if event is not None and getattr(event, "widget", None) is not _win:
                    return
                current = str(_win.grab_current() or "")
                own_path = str(_win)
                if current == own_path or current.startswith(own_path + "."):
                    _win.grab_release()
                    grab_state["released"] = True
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception releasing utility-window grab: %s", _ignored_exc)

        def _restore_grab_on_map(event=None, _win=win):
            try:
                if event is not None and getattr(event, "widget", None) is not _win:
                    return
                if grab_state["released"] and _win.state() == "normal":
                    _win.grab_set()
                    grab_state["released"] = False
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception restoring utility-window grab: %s", _ignored_exc)

        try:
            win.bind("<Unmap>", _release_grab_on_unmap, add="+")
            win.bind("<Map>", _restore_grab_on_map, add="+")
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception binding utility-window map handlers: %s", _ignored_exc)
        return win

    def _apply_window_icon_to(self, window) -> None:
        """Use the same white emblem for the root and every child window."""
        icon = getattr(self, "_window_icon", None)
        if icon is None or window is None:
            return

        def _apply() -> None:
            try:
                if window.winfo_exists():
                    window.iconphoto(False, icon)
                    window.wm_iconphoto(False, icon)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception while setting child GUI icon: %s", _ignored_exc)

        _apply()
        try:
            window.after_idle(_apply)
            window.after(250, _apply)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception while scheduling child GUI icon: %s", _ignored_exc)

    # ════════════════════════════════════════════════════════════════
    # UI BUILD
    # ════════════════════════════════════════════════════════════════
    def _open_group_menu(self, title, icon, items):
        """Open a small grouped action menu (v7.6 UI simplification).

        `items` is a list of (label, callback, description) tuples. This keeps
        every existing feature reachable while collapsing the long single-row
        pill strip into a few clearly-labeled functional groups. No feature is
        removed; each button simply calls the original handler.
        """
        import customtkinter as ctk
        p = self._p()
        win = self._make_singleton_window("group_menu")
        if win is None:
            return
        win.title(title)
        win.geometry("360x460")
        try:
            win.grab_set()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _open_group_menu (line 5043): %s", _ignored_exc)
        ctk.CTkLabel(win, text=f"{icon}  {title}",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(anchor="w", padx=16, pady=(14, 8))
        body = ctk.CTkScrollableFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        for entry in items:
            label, cb = entry[0], entry[1]
            desc = entry[2] if len(entry) > 2 else ""
            if cb is None:
                # Section labels keep grouped menus readable without creating
                # extra buttons or hiding any existing handler. This is a pure
                # UI affordance; entries with real callbacks behave exactly as
                # before.
                section = str(label).replace("__section__:", "").strip()
                if section:
                    ctk.CTkLabel(body, text=section.upper(),
                                 font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                 text_color=p["muted"], anchor="w").pack(
                                     fill="x", padx=4, pady=(10, 0))
                continue

            def _run(_cb=cb):
                try:
                    win.destroy()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _run (line 5069): %s", _ignored_exc)
                _cb()
            ctk.CTkButton(body, text=label, height=34, anchor="w",
                          font=ctk.CTkFont("Segoe UI", 12),
                          fg_color=p["surface2"], hover_color=p["surface"],
                          text_color=p["fg"], corner_radius=8,
                          command=_run).pack(fill="x", pady=(6, 0))
            if desc:
                ctk.CTkLabel(body, text=desc,
                             font=ctk.CTkFont("Segoe UI", 10),
                             text_color=p["muted"], anchor="w",
                             justify="left", wraplength=312).pack(
                                 fill="x", padx=4, pady=(1, 2))

    def _settings_maintenance_panel(self) -> None:
        """Compact Settings / Maintenance center.

        This replaces the long generic action list for settings with a more
        discoverable panel. The default Auto output switch is intentionally
        shown first because it affects Auto mode, imported batch rows with
        mode=auto, and CLI batch rows with mode=auto.
        """
        import customtkinter as ctk
        from tkinter import messagebox

        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"
        win = self._make_singleton_window("settings_maintenance")
        if win is None:
            return
        win.title("Settings / Maintenance" if is_en else "Pengaturan / Pemeliharaan")
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = min(820, max(720, sw - 260)), min(620, max(540, sh - 220))
            x, y = max(24, (sw - w) // 2), max(24, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("760x580")
        win.minsize(680, 520)
        try:
            win.grab_set()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _settings_maintenance_panel (line 5111): %s", _ignored_exc)

        root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=62)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr,
            text=("🛠  Settings / Maintenance" if is_en else "🛠  Pengaturan / Pemeliharaan"),
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=p["fg"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        ctk.CTkLabel(
            hdr,
            text=(
                "Edit settings, runtime helpers, cache, viewers, and maintenance tools."
                if is_en else
                "Kelola settings, helper runtime, cache, viewer, dan alat pemeliharaan."
            ),
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 8))

        body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        def _section(row: int, text: str) -> int:
            ctk.CTkLabel(
                body, text=text.upper(),
                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                text_color=p["accent"], anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 4))
            return row + 1

        def _action(row: int, col: int, label: str, desc: str, icon: str, cmd, *,
                    color: str = "surface2", hover: str = "surface", fg: str = "fg") -> None:
            card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=10,
                                border_width=1, border_color=p["border"])
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=5)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, width=30, font=ctk.CTkFont("Segoe UI Emoji", 16),
                         text_color=p.get(fg, fg)).grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=10, sticky="n")
            ctk.CTkLabel(card, text=label, anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=p["fg"]).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(10, 1))
            ctk.CTkLabel(card, text=desc, anchor="w", justify="left", wraplength=300,
                         font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"]).grid(
                             row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))
            btn = ctk.CTkButton(card, text=("Open" if is_en else "Buka"), width=70, height=26,
                                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                fg_color=p[color] if color in p else color,
                                hover_color=p[hover] if hover in p else hover,
                                text_color=p[fg] if fg in p else fg,
                                command=lambda: cmd())
            btn.grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=12)

        # Quick default Auto output switch at the top of the Settings window.
        auto_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12,
                                 border_width=1, border_color="#3b82f6")
        auto_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 10))
        auto_card.grid_columnconfigure(1, weight=1)
        st = _load_settings()
        current = _normalize_auto_detect_output(st.get("auto_detect_output", "folder"))
        auto_var = ctk.StringVar(value=("ZIP" if current == "zip" else "Folder"))
        ctk.CTkLabel(auto_card, text="⚡", width=34, font=ctk.CTkFont("Segoe UI Emoji", 20),
                     text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(14, 4), pady=12)
        ctk.CTkLabel(
            auto_card,
            text=("Default Auto Output" if is_en else "Default Output Auto"),
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=p["fg"], anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(12, 2))
        auto_status = ctk.StringVar(value=(
            "Auto currently resolves to folder outputs." if current == "folder" and is_en else
            "Auto currently resolves to ZIP outputs." if current == "zip" and is_en else
            "Auto saat ini menghasilkan output folder." if current == "folder" else
            "Auto saat ini menghasilkan output ZIP."
        ))
        ctk.CTkLabel(
            auto_card, textvariable=auto_status, font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"], anchor="w", justify="left", wraplength=430,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 12))

        def _save_auto(choice: str) -> None:
            pref = "zip" if str(choice).strip().lower() == "zip" else "folder"
            _update_setting("auto_detect_output", pref)
            try:
                self._update_mode_info(getattr(self, "_mode_var", "auto"))
                self._apply_language()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _save_auto (line 5209): %s", _ignored_exc)
            auto_status.set((
                "Saved. Auto now uses ZIP for ICC/cyoap_vue results." if pref == "zip" and is_en else
                "Saved. Auto now uses Folder for ICC/cyoap_vue results." if is_en else
                "Tersimpan. Auto sekarang memakai ZIP untuk hasil ICC/cyoap_vue." if pref == "zip" else
                "Tersimpan. Auto sekarang memakai Folder untuk hasil ICC/cyoap_vue."
            ))

        ctk.CTkSegmentedButton(
            auto_card, values=["Folder", "ZIP"], variable=auto_var, command=_save_auto,
            width=170, height=32, fg_color=p["surface2"], selected_color="#3b82f6",
            selected_hover_color="#2563eb", unselected_color=p["surface2"],
            unselected_hover_color=p["surface"], text_color="#ffffff",
        ).grid(row=0, column=2, rowspan=2, padx=(8, 14), pady=14, sticky="e")

        # JavaScript archive policy belongs in Settings: it controls persistent
        # download behavior, not a temporary feature toggle. Keep all Auto,
        # Smart, Browser, runtime, and safe-interaction limits in one place.
        archive_card = ctk.CTkFrame(
            body, fg_color=p["surface"], corner_radius=12,
            border_width=1, border_color="#0891b2",
        )
        archive_card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 10))
        archive_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            archive_card, text="🌐", width=34,
            font=ctk.CTkFont("Segoe UI Emoji", 19), text_color="#22d3ee",
        ).grid(row=0, column=0, rowspan=2, padx=(14, 4), pady=(12, 4), sticky="n")
        ctk.CTkLabel(
            archive_card,
            text=("JavaScript Archive Policy" if is_en else "Kebijakan Arsip JavaScript"),
            font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(12, 1))
        ctk.CTkLabel(
            archive_card,
            text=(
                "Auto is recommended. It selects the lightest complete adapter; explicit modes remain available for debugging."
                if is_en else
                "Auto direkomendasikan. Auto memilih adapter lengkap yang paling ringan; mode eksplisit tetap tersedia untuk debugging."
            ),
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            anchor="w", justify="left", wraplength=650,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))

        archive_form = ctk.CTkFrame(archive_card, fg_color="transparent")
        archive_form.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 4))
        for archive_col in range(3):
            archive_form.grid_columnconfigure(archive_col, weight=1, uniform="archive_setting")

        archive_values = {
            "strategy": ctk.StringVar(value=str(st.get("archive_strategy", "classic") or "classic").lower()),
            "interaction": ctk.StringVar(value=str(st.get("archive_interaction_policy", "safe") or "safe").lower()),
            "pages": ctk.StringVar(value=str(st.get("archive_max_pages", 300))),
            "depth": ctk.StringVar(value=str(st.get("archive_max_depth", 30))),
            "runtime_pages": ctk.StringVar(value=str(st.get("archive_runtime_max_pages", 12))),
            "settle": ctk.StringVar(value=str(st.get("archive_settle_time_ms", 1800))),
            "scroll": ctk.StringVar(value=str(st.get("archive_max_scroll_steps", 100))),
            "clicks": ctk.StringVar(value=str(st.get("archive_max_interactions", 20))),
            "stale": ctk.StringVar(value=str(st.get("archive_no_progress_rounds", 2))),
        }

        def _archive_field(row: int, col: int, label: str, hint: str, variable, *, values=None) -> None:
            field = ctk.CTkFrame(archive_form, fg_color="transparent")
            field.grid(row=row, column=col, sticky="ew", padx=5, pady=4)
            field.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                field, text=label, font=ctk.CTkFont("Segoe UI", 9),
                text_color=p["muted"], anchor="w",
            ).grid(row=0, column=0, sticky="ew", pady=(0, 2))
            ctk.CTkLabel(
                field, text=hint, font=ctk.CTkFont("Segoe UI", 8),
                text_color=p["muted2"], anchor="w", justify="left", wraplength=300,
            ).grid(row=1, column=0, sticky="ew", pady=(0, 4))
            if values:
                widget = ctk.CTkOptionMenu(
                    field, values=values, variable=variable, height=30,
                    fg_color="#0891b2", button_color="#0e7490",
                    button_hover_color="#155e75",
                )
            else:
                widget = ctk.CTkEntry(
                    field, textvariable=variable, height=30,
                    fg_color=p["input_bg"], text_color=p["input_fg"],
                    border_color=p["border"],
                )
            widget.grid(row=2, column=0, sticky="ew")

        _archive_field(0, 0, "Strategy" if is_en else "Strategi",
                       "Auto chooses a pipeline; explicit modes force one." if is_en else "Auto memilih pipeline; mode eksplisit memaksa satu metode.", archive_values["strategy"],
                       values=["auto", "classic", "smart", "browser"])
        _archive_field(0, 1, "Safe interaction" if is_en else "Interaksi aman",
                       "Safe allows guarded scroll/click; Off never clicks." if is_en else "Safe mengizinkan scroll/klik terjaga; Off tidak pernah klik.", archive_values["interaction"],
                       values=["safe", "off"])
        _archive_field(0, 2, "Max pages (1–5000)" if is_en else "Maks. halaman (1–5000)",
                       "Hard cap for same-origin story routes." if is_en else "Batas keras rute cerita dalam origin yang sama.", archive_values["pages"])
        _archive_field(1, 0, "Max depth (0–100)" if is_en else "Maks. kedalaman (0–100)",
                       "Route hops from entry; 0 means entry only." if is_en else "Jumlah lompatan rute; 0 berarti halaman awal saja.", archive_values["depth"])
        _archive_field(1, 1, "Runtime pages (1–100)" if is_en else "Halaman runtime (1–100)",
                       "Maximum pages rendered by the browser engine." if is_en else "Maksimum halaman yang dirender mesin browser.", archive_values["runtime_pages"])
        _archive_field(1, 2, "Settle time ms (250–15000)" if is_en else "Waktu tunggu ms (250–15000)",
                       "Wait after load/action for late assets." if is_en else "Waktu tunggu setelah load/aksi untuk aset terlambat.", archive_values["settle"])
        _archive_field(2, 0, "Scroll steps (1–1000)" if is_en else "Langkah scroll (1–1000)",
                       "Maximum incremental lazy-load scrolls." if is_en else "Maksimum scroll bertahap untuk lazy-load.", archive_values["scroll"])
        _archive_field(2, 1, "Max safe clicks (0–100)" if is_en else "Maks. klik aman (0–100)",
                       "Maximum allowlisted clicks per runtime page." if is_en else "Maksimum klik allowlist per halaman runtime.", archive_values["clicks"])
        _archive_field(2, 2, "No-progress rounds (1–10)" if is_en else "Putaran tanpa progres (1–10)",
                       "Stop after this many rounds find nothing new." if is_en else "Berhenti setelah sejumlah putaran tanpa temuan baru.", archive_values["stale"])

        archive_status = ctk.StringVar(value="")

        def _save_archive_settings() -> None:
            ranges = {
                "pages": (1, 5000, "archive_max_pages"),
                "depth": (0, 100, "archive_max_depth"),
                "runtime_pages": (1, 100, "archive_runtime_max_pages"),
                "settle": (250, 15000, "archive_settle_time_ms"),
                "scroll": (1, 1000, "archive_max_scroll_steps"),
                "clicks": (0, 100, "archive_max_interactions"),
                "stale": (1, 10, "archive_no_progress_rounds"),
            }
            updates = {
                "archive_strategy": archive_values["strategy"].get().strip().lower(),
                "archive_interaction_policy": archive_values["interaction"].get().strip().lower(),
            }
            try:
                for name, (minimum, maximum, key) in ranges.items():
                    parsed = int(archive_values[name].get().strip())
                    parsed = max(minimum, min(maximum, parsed))
                    archive_values[name].set(str(parsed))
                    updates[key] = parsed
            except (TypeError, ValueError):
                archive_status.set(
                    "Use whole numbers for every limit." if is_en else
                    "Gunakan angka bulat untuk semua batas."
                )
                return
            _update_settings(updates)
            logger.info(
                "[Settings] JavaScript archive policy saved: strategy=%s, interaction=%s",
                updates["archive_strategy"], updates["archive_interaction_policy"],
            )
            archive_status.set(
                "Saved. New downloads will use this policy." if is_en else
                "Tersimpan. Download berikutnya akan memakai kebijakan ini."
            )

        archive_footer = ctk.CTkFrame(archive_card, fg_color="transparent")
        archive_footer.grid(row=3, column=0, columnspan=2, sticky="ew", padx=18, pady=(3, 12))
        archive_footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            archive_footer,
            text=(
                "Recommended: Auto + Safe. Every number is a safety cap, not a download target. Full option details and presets are in Help / Guide."
                if is_en else
                "Rekomendasi: Auto + Safe. Semua angka adalah batas pengaman, bukan target download. Detail opsi dan preset lengkap ada di Bantuan / Panduan."
            ),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
            anchor="w", justify="left", wraplength=650,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 3))
        ctk.CTkLabel(
            archive_footer, textvariable=archive_status,
            font=ctk.CTkFont("Segoe UI", 9), text_color="#22d3ee", anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            archive_footer, text=("Open Guide" if is_en else "Buka Panduan"),
            command=lambda: self._show_feature_guide("settings"), width=104, height=30,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(4, 6))
        ctk.CTkButton(
            archive_footer, text=("Save archive policy" if is_en else "Simpan kebijakan arsip"),
            command=_save_archive_settings, width=150, height=30,
            fg_color="#0891b2", hover_color="#0e7490",
        ).grid(row=0, column=2, rowspan=2, sticky="e")

        # YouTube authentication belongs in persistent Settings rather than
        # the main download form. Only the selected file path is retained;
        # cookie contents remain in the user's local Netscape file.
        cookie_card = ctk.CTkFrame(
            body, fg_color=p["surface"], corner_radius=12,
            border_width=1, border_color=p["border"],
        )
        cookie_card.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 10))
        cookie_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            cookie_card, text="🍪", width=34,
            font=ctk.CTkFont("Segoe UI Emoji", 19), text_color="#f87171",
        ).grid(row=0, column=0, rowspan=3, padx=(14, 4), pady=(12, 8), sticky="n")
        ctk.CTkLabel(
            cookie_card,
            text=("YouTube cookies" if is_en else "Cookie YouTube"),
            font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w",
        ).grid(row=0, column=1, columnspan=4, sticky="ew", padx=(0, 10), pady=(12, 1))
        ctk.CTkLabel(
            cookie_card,
            text=(
                "Select an exported Netscape cookies.txt. When selected, yt-dlp uses this file only."
                if is_en else
                "Pilih cookies.txt format Netscape. Jika dipilih, yt-dlp hanya memakai file ini."
            ),
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            anchor="w", justify="left", wraplength=650,
        ).grid(row=1, column=1, columnspan=4, sticky="ew", padx=(0, 14), pady=(0, 7))
        cookie_card.grid_columnconfigure(1, weight=1)
        cookie_entry = ctk.CTkEntry(
            cookie_card, textvariable=self._ytdlp_cookies_var, height=30,
            placeholder_text=("Netscape cookies.txt (optional)" if is_en else "Netscape cookies.txt (opsional)"),
            fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
        )
        cookie_entry.grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=(0, 12))
        cookie_status = ctk.StringVar(value=(
            "Saved cookies.txt will be used for YouTube audio."
            if is_en and self._ytdlp_cookies_var.get().strip() else
            "File cookies.txt tersimpan akan dipakai untuk audio YouTube."
            if self._ytdlp_cookies_var.get().strip() else
            "Automatic browser cookies are used when this is empty."
            if is_en else
            "Cookie browser otomatis dipakai jika kolom ini kosong."
        ))
        ctk.CTkLabel(
            cookie_card, textvariable=cookie_status,
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"], anchor="w",
        ).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(0, 12))

        def _browse_cookie_setting() -> None:
            self._browse_ytdlp_cookies()
            cookie_status.set(
                "Path selected; click Save." if is_en else
                "Path dipilih; klik Simpan."
            )

        def _save_cookie_setting() -> None:
            if self._save_ytdlp_cookie_setting(show_error=True):
                cookie_status.set(
                    "Saved. yt-dlp will use this file for future downloads." if is_en else
                    "Tersimpan. yt-dlp akan memakai file ini untuk download berikutnya."
                )

        ctk.CTkButton(
            cookie_card, text=("Browse…" if is_en else "Browse…"), width=82, height=30,
            command=_browse_cookie_setting, fg_color=p["surface2"],
            hover_color=p["surface"], text_color=p["fg"],
        ).grid(row=2, column=2, padx=(0, 6), pady=(0, 12))
        ctk.CTkButton(
            cookie_card, text=("Save" if is_en else "Simpan"), width=82, height=30,
            command=_save_cookie_setting, fg_color="#b91c1c", hover_color="#991b1b",
            text_color="#ffffff",
        ).grid(row=2, column=3, padx=(0, 6), pady=(0, 12))
        ctk.CTkButton(
            cookie_card, text=("Clear" if is_en else "Bersihkan"), width=82, height=30,
            command=lambda: (self._clear_ytdlp_cookies(), cookie_status.set(
                "Cleared; automatic browser cookies will be used." if is_en else
                "Dibersihkan; cookie browser otomatis akan dipakai."
            )), fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
        ).grid(row=2, column=4, pady=(0, 12))

        r = 3
        r = _section(r, "Settings files" if is_en else "File settings")
        _action(r, 0, "Open settings.json" if is_en else "Buka settings.json",
                "Open the active JSON in an editable editor." if is_en else "Buka JSON aktif di editor yang bisa disimpan.",
                "📝", self._open_settings_json)
        _action(r, 1, "Open settings folder" if is_en else "Buka folder settings",
                "Open ~/.cyoa_downloader settings/history folder." if is_en else "Buka folder ~/.cyoa_downloader untuk settings/history.",
                "📁", self._open_settings_folder)
        r += 1
        _action(r, 0, "Export Settings" if is_en else "Ekspor Settings",
                "Export portable settings; secrets are excluded." if is_en else "Ekspor settings portabel; secret tidak ikut.",
                "📤", self._export_settings_dialog, color="#065f46", hover="#047857", fg="#d1fae5")
        _action(r, 1, "Import Settings" if is_en else "Impor Settings",
                "Merge prior export; secret values are ignored." if is_en else "Merge export lama; nilai secret diabaikan.",
                "📂", self._import_settings_dialog, color="#1d4ed8", hover="#2563eb", fg="#dbeafe")
        r += 1

        r = _section(r, "Runtime configuration" if is_en else "Konfigurasi runtime")
        _action(r, 0, "AI Assist Settings" if is_en else "Pengaturan AI Assist",
                "Provider/model/API-key handling for optional recovery." if is_en else "Provider/model/API-key untuk recovery opsional.",
                "🤖", self._ai_settings_panel, color="settings_bg", hover="settings_hv", fg="settings_fg")
        _action(r, 1, "Open gallery-dl config" if is_en else "Buka config gallery-dl",
                "Create config.json if missing, then open it for editing." if is_en else "Buat config.json jika belum ada, lalu buka untuk diedit.",
                "🎨", self._open_gallery_dl_config, color="#0f766e", hover="#0d9488", fg="#ccfbf1")
        r += 1
        _action(r, 0, "Cloudflare / FlareSolverr",
                "Challenge handling, proxy, DNS, and HTTP/2 options." if is_en else "Pengaturan challenge, proxy, DNS, dan HTTP/2.",
                "☁", self._cloudflare_panel)
        _action(r, 1, "Offline Viewers" if is_en else "Viewer Offline",
                "Register/manage offline viewer ZIP packages." if is_en else "Daftarkan/kelola paket ZIP viewer offline.",
                "🌐", self._manage_offline_viewers)
        r += 1

        # Batch Check and Image Cache remain available as internal compatibility
        # methods, but are intentionally not exposed in the normal GUI.

        footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=48)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text=(
            "Tip: Auto mode uses the Default Auto Output switch above; explicit modes are unchanged."
            if is_en else
            "Tip: Mode Auto mengikuti switch Default Output Auto di atas; mode eksplisit tidak berubah."
        ), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=12)
        ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=90,
                      fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
                      command=win.destroy).grid(row=0, column=1, padx=14, pady=10)


    def _setup_ui_base(self) -> None:
        import customtkinter as ctk
        p = self._p()
        self._sec_labels:  List = []
        self._sec_dividers: List = []

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

        def T(widget, **keys):
            """Register widget for theme updates. keys = ctk_prop → palette_key."""
            self._themed.append((widget, keys))
            return widget

        # ── TITLEBAR ────────────────────────────────────────────────
        tb = T(ctk.CTkFrame(self.root, height=84, corner_radius=0,
                            fg_color=p["panel"]),
               fg_color="panel")
        tb.grid(row=0, column=0, columnspan=2, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_rowconfigure(0, minsize=84)
        tb.grid_columnconfigure(1, weight=1)

        # Borderless logo: drop the surface fill + 1px
        # border so the temple emblem stands on its own against the toolbar,
        # per request. The 56x56 square footprint and centred placement from
        # rev12 are kept so spacing/alignment stay consistent — only the badge
        # chrome (fill + border) is removed. Emblem artwork unchanged.
        logo = T(ctk.CTkFrame(tb, width=56, height=56, corner_radius=0,
                             fg_color="transparent", border_width=0),
                 fg_color="transparent")
        logo.grid(row=0, column=0, padx=(16, 12), pady=(14, 14), sticky="w")
        logo.grid_propagate(False)
        self._logo_image = None
        try:
            light_logo, dark_logo = _load_logo_images()
            if light_logo is not None and dark_logo is not None:
                self._logo_image = ctk.CTkImage(
                    light_image=light_logo, dark_image=dark_logo, size=(42, 42)
                )
                ctk.CTkLabel(logo, text="", image=self._logo_image, fg_color="transparent").place(
                    relx=0.5, rely=0.5, anchor="center"
                )
            else:
                raise RuntimeError("logo unavailable")
        except Exception:
            ctk.CTkLabel(logo, text="C↯", font=ctk.CTkFont("Consolas", 16, "bold"),
                         text_color=p["fg"], fg_color="transparent").place(relx=0.5, rely=0.5, anchor="center")

        # Align the title block to the logo's vertical centre. Previously the
        # title used pady=(18,16) while the logo used (14,14), so the text sat
        # slightly high relative to the emblem. sticky="ns" + symmetric pady
        # puts both on the same optical centre line.
        title_stack = ctk.CTkFrame(tb, fg_color="transparent")
        title_stack.grid(row=0, column=1, sticky="nsw", pady=(14, 14))
        title_stack.grid_rowconfigure(0, weight=1)
        T(ctk.CTkLabel(title_stack, text="CYOA Downloader",
                       font=ctk.CTkFont("Segoe UI", 16, "bold"),
                       text_color=p["fg"], anchor="w"),
          text_color="fg").pack(anchor="w", pady=(0, 1))
        T(ctk.CTkLabel(title_stack, text="ICC backup · serve preview · diagnostics",
                       font=ctk.CTkFont("Segoe UI", 9),
                       text_color=p["muted"], anchor="w"),
          text_color="muted").pack(anchor="w", pady=(0, 0))
        T(ctk.CTkLabel(tb, text=f"v{_APP_VERSION}",
                       font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"]),
          text_color="muted").grid(row=0, column=2, padx=(8, 0), sticky="w")

        pill = ctk.CTkSegmentedButton(
            tb, values=["Dark", "Light", "System"],
            command=self._toggle_theme,
            font=ctk.CTkFont("Segoe UI", 11),
            width=168, height=28,
            fg_color=p["surface2"],
            selected_color=p["accent"],
            selected_hover_color="#2563eb",
            unselected_color=p["surface2"],
            unselected_hover_color=p["surface"],
            text_color="#ffffff",
        )
        pill.set(getattr(self, "_theme_mode", "System"))
        pill.grid(row=0, column=3, padx=(8, 6), pady=(28, 28), sticky="e")
        self._theme_pill = pill

        lang_pill = ctk.CTkSegmentedButton(
            tb, values=["ID", "EN"],
            command=self._toggle_language,
            font=ctk.CTkFont("Segoe UI", 11),
            width=72, height=28,
            fg_color=p["surface2"],
            selected_color=p["accent"],
            selected_hover_color="#2563eb",
            unselected_color=p["surface2"],
            unselected_hover_color=p["surface"],
            text_color="#ffffff",
        )
        lang_pill.set("EN" if self._language == "en" else "ID")
        lang_pill.grid(row=0, column=4, padx=(0, 14), pady=(28, 28), sticky="e")
        self._lang_pill = lang_pill

        # ── SIDEBAR ─────────────────────────────────────────────────
        sb = ctk.CTkScrollableFrame(
            self.root, width=248, corner_radius=0,
            fg_color=p["sidebar"],
            scrollbar_button_color=p["surface2"],
            scrollbar_button_hover_color=p["muted2"],
        )
        sb.grid(row=1, column=0, sticky="nsew")
        sb.grid_columnconfigure(0, weight=1)
        self._sidebar = sb

        for entry in self.MODES:
            val, icon, name, desc, _ = entry
            if val == "__sec__":
                # Compact section header. Keep grouping, but avoid the tall
                # separator stack that made the sidebar feel cut and sparse.
                lbl = ctk.CTkLabel(sb, text=name,
                                   font=ctk.CTkFont("Segoe UI", 8, "bold"),
                                   text_color=p["muted2"], anchor="w")
                lbl.pack(fill="x", padx=12, pady=(6, 1))
                self._sec_labels.append(lbl)
                div = ctk.CTkFrame(sb, height=1, fg_color=p.get("separator", p["border"]), corner_radius=0)
                div.pack(fill="x", padx=10, pady=(0, 2))
                self._sec_dividers.append(div)
                continue

            # Balanced compact selector: v31 was too tight and clipped text;
            # this keeps the slim list style but restores enough breathing room
            # for the mode name + one-line description.
            row = ctk.CTkFrame(sb, corner_radius=9, fg_color="transparent",
                               border_width=0, cursor="hand2", height=46)
            row.pack(fill="x", padx=(8, 10), pady=2)
            row.pack_propagate(False)

            bar = ctk.CTkFrame(row, width=4, corner_radius=2,
                               fg_color="transparent")
            bar.pack(side="left", fill="y", pady=5)

            icon_box = ctk.CTkLabel(row, text=icon,
                                    width=25, height=25,
                                    font=ctk.CTkFont("Segoe UI Emoji", 13),
                                    fg_color="transparent",
                                    text_color="#64748b",
                                    corner_radius=7)
            icon_box.pack(side="left", padx=(8, 7), pady=6)

            txt = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
            txt.pack(side="left", fill="both", expand=True, pady=5, padx=(0, 6))

            name_lbl = ctk.CTkLabel(txt, text=name, anchor="w",
                                    font=ctk.CTkFont("Segoe UI", 10, "bold"),
                                    text_color="#94a3b8")
            name_lbl.pack(fill="x", pady=(0, 0))

            desc_lbl = ctk.CTkLabel(txt, text=desc, anchor="w",
                                    font=ctk.CTkFont("Segoe UI", 8),
                                    text_color="#64748b",
                                    wraplength=165)
            desc_lbl.pack(fill="x", pady=(0, 0))

            self._mode_btns[val] = (row, bar, icon_box, name_lbl, desc_lbl)

            def _on_click(e=None, v=val): self._select_mode(v, log_change=True)
            def _on_enter(e, v=val, r=row, ib=icon_box, t=txt, nl=name_lbl, dl=desc_lbl):
                if self._mode_var != v:
                    hover = self._p()["surface"]
                    for w in (r, ib, t, nl, dl): w.configure(fg_color=hover)
            def _on_leave(e): self._select_mode(self._mode_var)

            for w in (row, icon_box, txt, name_lbl, desc_lbl):
                w.bind("<Button-1>", _on_click)
                w.bind("<Enter>",    _on_enter)
                w.bind("<Leave>",    _on_leave)

        # ── Mode info box (bottom of sidebar) ───────────────────────
        info_box = ctk.CTkFrame(sb, corner_radius=7,
                                fg_color=p["surface2"], border_width=0)
        info_box.pack(fill="x", padx=8, pady=(5, 5))
        self._info_title = ctk.CTkLabel(
            info_box, text="Auto (detect)",
            font=ctk.CTkFont("Segoe UI", 9, "bold"),
            text_color="#60a5fa", anchor="w")
        self._info_title.pack(fill="x", padx=8, pady=(7, 1))
        self._info_body = ctk.CTkLabel(
            info_box, text="",
            font=ctk.CTkFont("Segoe UI", 8),
            text_color=p["muted"], anchor="w",
            wraplength=208, justify="left")
        self._info_body.pack(fill="x", padx=8, pady=(0, 8))
        self._info_output = ctk.CTkLabel(
            info_box, text="",
            font=ctk.CTkFont("Consolas", 9),
            text_color="#3b82f6", anchor="w")
        self._info_output.pack(fill="x", padx=8, pady=(0, 8))
        self._info_box = info_box

        # Select default
        self._select_mode("auto", update_ui=False)
        self._update_mode_info("auto")

        # ── MAIN ────────────────────────────────────────────────────
        main = T(ctk.CTkFrame(self.root, corner_radius=0, fg_color=p["bg"]),
                 fg_color="bg")
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # ─ Input panel ──────────────────────────────────────────────
        inp = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["bg"],
                             border_width=0), fg_color="bg")
        inp.grid(row=0, column=0, sticky="ew")
        inp.grid_columnconfigure(1, weight=10)  # URL gets the largest share
        inp.grid_columnconfigure(3, weight=6)   # filename is still roomy, but secondary
        inp.grid_columnconfigure(4, weight=0)   # Add button stays compact

        # "Input" header. Keep Help in the header so it is always visible
        # and does not get pushed under the horizontal options row on smaller
        # windows.
        T(ctk.CTkLabel(inp, text="Input",
                       font=ctk.CTkFont("Segoe UI", 12, "bold"),
                       text_color=p["accent"], anchor="w"),
          text_color="accent").grid(row=0, column=0, columnspan=4,
                                    sticky="w", padx=14, pady=(10, 2))
        header_actions = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        header_actions.grid(row=0, column=4, columnspan=2, sticky="e",
                            padx=(0, 14), pady=(8, 2))
        T(ctk.CTkButton(header_actions, text="© Credits", width=96, height=26,
                        font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._show_credits_panel),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").pack(side="left", padx=(0, 6))
        T(ctk.CTkButton(header_actions, text="🩺 Diagnostics", width=112, height=26,
                        font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._diagnostics_panel),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").pack(side="left", padx=(0, 6))
        T(ctk.CTkButton(header_actions, text="❔ Help / Guide", width=116, height=26,
                        font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=lambda: self._show_feature_guide("setup")),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").pack(side="left")

        T(ctk.CTkFrame(inp, height=1, fg_color=p.get("separator", p["border"]), corner_radius=0),
          fg_color="separator").grid(row=10, column=0, columnspan=6, sticky="ew")

        # URL/Filename row is isolated from the dense options grid below.
        # This keeps the URL field long, the filename field medium, and the
        # Add button compact—matching the intended workflow: paste link first.
        input_row = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        input_row.grid(row=1, column=0, columnspan=5, sticky="ew", padx=14, pady=(6, 4))
        input_row.grid_columnconfigure(0, weight=0, minsize=54)
        input_row.grid_columnconfigure(1, weight=9, minsize=520)
        input_row.grid_columnconfigure(2, weight=0, minsize=78)
        input_row.grid_columnconfigure(3, weight=4, minsize=260)
        input_row.grid_columnconfigure(4, weight=0, minsize=160)

        self._url_label = T(ctk.CTkLabel(input_row, text="URL", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                       text_color=p["muted"], width=50),
          text_color="muted")
        self._url_label.grid(row=0, column=0, padx=(0, 6), sticky="w")

        self._url_var = ctk.StringVar()
        url_e = T(ctk.CTkEntry(input_row, textvariable=self._url_var,
                               placeholder_text="https://author.neocities.org/cyoa/",
                               font=ctk.CTkFont("Segoe UI", 11),
                               fg_color=p["input_bg"], border_color=p["border"],
                               text_color=p["input_fg"], height=34, width=560),
                  fg_color="input_bg", border_color="border", text_color="input_fg")
        url_e.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self._url_entry = url_e
        url_e.bind("<Return>", lambda _: self._add_to_queue())

        self._fn_label = T(ctk.CTkLabel(input_row, text="Filename", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                       text_color=p["muted"]),
          text_color="muted")
        self._fn_label.grid(row=0, column=2, padx=(0, 6), sticky="w")

        self._fn_var = ctk.StringVar()
        self._fn_entry = T(ctk.CTkEntry(input_row, textvariable=self._fn_var,
                       placeholder_text="(opsional)",
                       font=ctk.CTkFont("Segoe UI", 11),
                       fg_color=p["input_bg"], border_color=p["border"],
                       text_color=p["input_fg"], height=34, width=300),
          fg_color="input_bg", border_color="border", text_color="input_fg")
        self._fn_entry.grid(row=0, column=3, sticky="ew", padx=(0, 10))

        self._add_btn = T(ctk.CTkButton(input_row, text=self._tr("add_url"), height=34, width=150,
                      font=ctk.CTkFont("Segoe UI", 11, "bold"),
                      fg_color="#3b82f6", hover_color="#2563eb", text_color="#ffffff",
                      corner_radius=9,
                      command=self._add_to_queue),
          fg_color="#3b82f6", hover_color="#2563eb", text_color="#ffffff")
        self._add_btn.grid(row=0, column=4, padx=(0, 0), sticky="ew")
        self._input_row = input_row
        self._input_header_actions = header_actions

        # Options row
        # ── Options: 2-row compact layout ───────────────────────────
        # Row 1: numeric inputs + Import/Help buttons (right-aligned)
        # Row 2: toggleable checkboxes
        opt_wrap = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        opt_wrap.grid(row=2, column=0, columnspan=5, sticky="ew",
                      padx=14, pady=(0, 6))
        opt_wrap.grid_columnconfigure(0, weight=1)

        # ── Row 1: numerics ──────────────────────────────────────────
        row1 = T(ctk.CTkFrame(opt_wrap, fg_color="transparent"), fg_color="bg")
        row1.grid(row=0, column=0, sticky="ew")

        def _num_lbl(parent, text):
            return T(ctk.CTkLabel(parent, text=text,
                                  font=ctk.CTkFont("Segoe UI", 10),
                                  text_color=p["muted"]),
                     text_color="muted")

        def _num_entry(parent, var, width=46):
            return T(ctk.CTkEntry(parent, textvariable=var,
                                  width=width, height=26,
                                  fg_color=p["input_bg"],
                                  border_color=p["border"],
                                  text_color=p["input_fg"],
                                  font=ctk.CTkFont("Consolas", 10),
                                  justify="center"),
                     fg_color="input_bg", border_color="border",
                     text_color="input_fg")

        _num_lbl(row1, "Threads:").pack(side="left")
        self._threads_var = ctk.StringVar(value=str(DEFAULT_MAX_WORKERS))
        _num_entry(row1, self._threads_var, 42).pack(side="left", padx=(3, 12))

        _num_lbl(row1, "Retry (s):").pack(side="left")
        self._wait_var = ctk.StringVar(value=str(DEFAULT_WAIT_TIME))
        _num_entry(row1, self._wait_var, 46).pack(side="left", padx=(3, 12))

        _num_lbl(row1, "BW (KB/s):").pack(side="left")
        self._bw_var = ctk.StringVar(value="0")
        _num_entry(row1, self._bw_var, 46).pack(side="left", padx=(3, 12))

        # Proxy field — compact, always visible
        _num_lbl(row1, "Proxy:").pack(side="left")
        _proxy_init = _get_active_proxy() or ""
        self._proxy_var = ctk.StringVar(value=_proxy_init)
        _proxy_entry = T(ctk.CTkEntry(
            row1, textvariable=self._proxy_var,
            width=160, height=26, placeholder_text="http://127.0.0.1:7890",
            fg_color=p["input_bg"], border_color=p["border"],
            text_color=p["input_fg"],
            font=ctk.CTkFont("Consolas", 9)),
            fg_color="input_bg", border_color="border", text_color="input_fg")
        _proxy_entry.pack(side="left", padx=(3, 4))

        def _on_proxy_set(*_):
            v = self._proxy_var.get().strip()
            _set_active_proxy(v if v else None)
            _update_setting("proxy", v)
        self._proxy_var.trace_add("write", _on_proxy_set)
        # Load from settings
        _saved_proxy = _load_settings().get("proxy", "")
        if _saved_proxy:
            self._proxy_var.set(_saved_proxy)
            _set_active_proxy(_saved_proxy)

        # DNS — preset dropdown + optional custom entry
        _num_lbl(row1, "DNS:").pack(side="left", padx=(8, 0))

        _saved_dns  = _load_settings().get("dns", "")
        self._dns_var = ctk.StringVar(value=_saved_dns)

        # Find matching preset label (or "Custom…")
        _preset_names = list(DNS_PRESETS.keys())
        _init_label   = next(
            (k for k, v in DNS_PRESETS.items() if v == _saved_dns and v != "__custom__"),
            "Custom…" if _saved_dns else "System (default)"
        )
        self._dns_preset_var = ctk.StringVar(value=_init_label)

        def _on_dns_preset_change(label: str) -> None:
            ip = DNS_PRESETS.get(label, "")
            if ip == "__custom__":
                # Show the custom DNS field on its own row so it remains visible
                # in normal-width windows. v24 fixes the old cramped row where
                # the entry was clipped behind Import/List controls.
                try:
                    _dns_custom_row.grid()
                    _dns_custom_entry.focus_set()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _on_dns_preset_change (line 5648): %s", _ignored_exc)
            else:
                try:
                    _dns_custom_row.grid_remove()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _on_dns_preset_change (line 5653): %s", _ignored_exc)
                self._dns_trace_suspended = True
                try:
                    self._dns_var.set(ip)
                finally:
                    self._dns_trace_suspended = False
                _apply_dns(ip)

        def _apply_dns(ip: str) -> None:
            _set_active_dns(ip)
            _update_setting("dns", ip)

        T(ctk.CTkOptionMenu(
            row1, variable=self._dns_preset_var,
            values=_preset_names,
            width=148, height=26,
            font=ctk.CTkFont("Segoe UI", 9),
            fg_color=p["surface2"], button_color=p["surface"],
            button_hover_color=p["surface2"],
            text_color=p["muted"], dropdown_fg_color=p["surface"],
            dropdown_text_color=p["fg"],
            command=_on_dns_preset_change),
          fg_color="surface2", button_color="surface",
          text_color="muted").pack(side="left", padx=(3, 0))

        _dns_custom_row = T(ctk.CTkFrame(opt_wrap, fg_color="transparent"), fg_color="bg")
        _dns_custom_row.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        _dns_custom_row.grid_remove()
        _num_lbl(_dns_custom_row, "Custom DNS:").pack(side="left", padx=(0, 6))
        _dns_custom_entry = T(ctk.CTkEntry(
            _dns_custom_row, textvariable=self._dns_var,
            width=260, height=26, placeholder_text="1.1.1.1 or https://dns.example/dns-query",
            fg_color=p["input_bg"], border_color=p["border"],
            text_color=p["input_fg"],
            font=ctk.CTkFont("Consolas", 9)),
            fg_color="input_bg", border_color="border", text_color="input_fg")
        _dns_custom_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        _num_lbl(_dns_custom_row, "Applied after typing stops.").pack(side="left", padx=(0, 0))

        self._dns_trace_suspended = False
        self._dns_after_id = None
        def _on_dns_custom(*_):
            if getattr(self, "_dns_trace_suspended", False):
                return
            try:
                if not _dns_custom_entry.winfo_ismapped():
                    return
            except Exception:
                return
            # Debounce custom DNS typing to avoid applying half-written values
            # and to avoid duplicate DNS log entries.
            try:
                if self._dns_after_id is not None:
                    self.root.after_cancel(self._dns_after_id)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _on_dns_custom (line 5708): %s", _ignored_exc)
            self._dns_after_id = self.root.after(
                750, lambda: _apply_dns(self._dns_var.get().strip())
            )
        self._dns_var.trace_add("write", _on_dns_custom)

        # Only show custom entry if no preset matches
        if _init_label == "Custom…" and _saved_dns:
            try:
                _dns_custom_row.grid()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _setup_ui (line 5719): %s", _ignored_exc)

        # Apply saved DNS on startup
        if _saved_dns:
            _set_active_dns(_saved_dns)

        # Right side: Import/export. Help lives in the Input header so it stays
        # visible even when the options row is crowded.
        self._export_button = T(ctk.CTkButton(row1, text="Export List…", height=26,
                         font=ctk.CTkFont("Segoe UI", 10),
                         fg_color=p["surface2"], hover_color=p["surface"],
                         text_color=p["muted"], border_width=1,
                         border_color=p["surface2"],
                         command=self._export_list),
           fg_color="surface2", hover_color="surface", text_color="muted",
           border_color="surface2")
        self._export_button.pack(side="right", padx=(4, 0))
        self._import_button = T(ctk.CTkButton(row1, text="Import List…", height=26,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._import_list),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2")
        self._import_button.pack(side="right", padx=(4, 0))

        # ── Row 2: checkboxes ─────────────────────────────────────────
        row2 = T(ctk.CTkFrame(opt_wrap, fg_color="transparent"), fg_color="bg")
        row2.grid(row=2, column=0, sticky="ew", pady=(3, 0))

        def _chk(parent, text, var, color="#3b82f6", hover="#2563eb", cmd=None, px=10):
            kw = dict(variable=var, font=ctk.CTkFont("Segoe UI", 10),
                      checkbox_width=15, checkbox_height=15,
                      fg_color=color, hover_color=hover,
                      text_color=p["muted"])
            if cmd: kw["command"] = cmd
            return T(ctk.CTkCheckBox(parent, text=text, **kw),
                     text_color="muted")

        self._fonts_var   = ctk.BooleanVar(value=True)
        self._analyse_var = ctk.BooleanVar(value=True)
        self._cf_mode_var = ctk.StringVar(value=_display_cloudflare_mode(_load_settings().get("cloudflare_mode", "auto")))
        _http2_saved = bool(_load_settings().get("http2_enabled", False))
        if _http2_saved:
            try:
                from ..network.throttle import http2_runtime_info
                _http2_saved = bool(http2_runtime_info()["available"])
            except Exception as _http2_probe_exc:
                _http2_saved = False
                logger.debug("HTTP/2 startup capability probe failed: %s", _http2_probe_exc)
        self._http2_var = ctk.BooleanVar(value=_http2_saved)
        self._ytdlp_var   = ctk.BooleanVar(value=True)

        _chk(row2, "Fonts", self._fonts_var, cmd=self._on_fonts_toggle).pack(side="left", padx=(0, px := 12))
        _chk(row2, "Font Analysis", self._analyse_var, cmd=self._on_font_analysis_toggle).pack(side="left", padx=(0, 12))

        # Compact Cloudflare selector. Detailed settings live in the Cloudflare panel.
        self._cf_label = _num_lbl(row2, "Cloudflare:")
        self._cf_label.pack(side="left", padx=(0, 3))
        self._cf_mode_menu = T(ctk.CTkOptionMenu(
            row2, variable=self._cf_mode_var,
            values=["Off", "Auto", "cloudscraper", "FlareSolverr"],
            width=126, height=26,
            font=ctk.CTkFont("Segoe UI", 9),
            fg_color=p["surface2"], button_color=p["surface"],
            button_hover_color=p["surface2"],
            text_color=p["muted"], dropdown_fg_color=p["surface"],
            dropdown_text_color=p["fg"],
            command=self._on_cloudflare_mode_change),
            fg_color="surface2", button_color="surface", text_color="muted")
        self._cf_mode_menu.pack(side="left", padx=(0, 12))
        self._on_cloudflare_mode_change(self._cf_mode_var.get(), validate=False)

        _chk(row2, "HTTP/2", self._http2_var,
             color="#06b6d4", hover="#0891b2",
             cmd=self._on_http2_toggle).pack(side="left", padx=(0, 12))
        _chk(row2, "YT Audio", self._ytdlp_var,
             color="#ef4444", hover="#dc2626",
             cmd=self._on_ytdlp_toggle).pack(side="left", padx=(0, 12))

        # CYOA Manager checkbox
        _cm_settings = _load_settings()
        _cm_auto     = _cm_settings.get("cyoa_mgr_enabled")
        _cm_default  = bool(_find_cyoa_manager_db()) if _cm_auto is None else bool(_cm_auto)
        self._cyoa_mgr_var = ctk.BooleanVar(value=_cm_default)
        def _on_cyoa_mgr_toggle():
            v = self._cyoa_mgr_var.get()
            _update_setting("cyoa_mgr_enabled", v)
            logger.info(f"[Feature] CYOA Manager integration: {'enabled' if v else 'disabled'}")
            if hasattr(self, '_cm_btn'):
                p2 = self._p()
                self._cm_btn.configure(
                    text="📤  CYOA Mgr " + ("✓" if v else "✗"),
                    fg_color=p2["manager_bg"],
                    hover_color=p2["manager_hv"],
                    text_color=p2["manager_fg"])
        # CYOA Manager is now exposed through one dedicated toolbar button.
        # Keep _cyoa_mgr_var and _on_cyoa_mgr_toggle for existing state sync,
        # but do not render a duplicate inline checkbox here.

        # AI Assist toggle
        self._ai_var = ctk.BooleanVar(value=self._ai_enabled)
        def _on_ai_toggle():
            self._ai_enabled = self._ai_var.get()
            _update_setting("ai_enabled", self._ai_enabled)
            logger.info(f"[Feature] AI Assist: {'enabled' if self._ai_enabled else 'disabled'}")
            if hasattr(self, '_ai_btn'):
                p2 = self._p()
                self._ai_btn.configure(
                    text="🤖 AI  " + ("ON" if self._ai_enabled else "OFF"),
                    text_color=p2["accent"] if self._ai_enabled else p2["muted"])
        _chk(row2, "🤖 AI Assist", self._ai_var,
             color="#8b5cf6", hover="#7c3aed",
             cmd=_on_ai_toggle).pack(side="left", padx=(0, 0))

        # Output folder row
        dirf = T(ctk.CTkFrame(inp, fg_color="transparent"), fg_color="bg")
        dirf.grid(row=3, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 12))
        dirf.grid_columnconfigure(1, weight=1)

        self._output_label = T(ctk.CTkLabel(dirf, text="Output folder:", font=ctk.CTkFont("Segoe UI", 11),
                       text_color=p["muted"], width=90),
          text_color="muted")
        self._output_label.grid(row=0, column=0, sticky="w")
        self._outdir_var = ctk.StringVar(value=os.getcwd())
        T(ctk.CTkEntry(dirf, textvariable=self._outdir_var,
                       font=ctk.CTkFont("Segoe UI", 11),
                       fg_color=p["input_bg"], border_color=p["border"],
                       text_color=p["muted"], height=30),
          fg_color="input_bg", border_color="border", text_color="muted").grid(
            row=0, column=1, sticky="ew", padx=(6, 6))
        self._browse_button = T(ctk.CTkButton(dirf, text="Browse…", height=30, width=80,
                        font=ctk.CTkFont("Segoe UI", 11),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._browse),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2")
        self._browse_button.grid(row=0, column=2)

        # ─ Queue panel ──────────────────────────────────────────────
        qf = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["bg"],
                            border_width=0), fg_color="bg")
        qf.grid(row=1, column=0, sticky="ew")
        qf.grid_columnconfigure(0, weight=1)
        T(ctk.CTkFrame(qf, height=1, fg_color=p.get("separator", p["border"]), corner_radius=0),
          fg_color="separator").grid(row=0, column=0, columnspan=3, sticky="ew")

        qhdr = T(ctk.CTkFrame(qf, fg_color="transparent"), fg_color="bg")
        qhdr.grid(row=1, column=0, sticky="ew", padx=14, pady=(8, 4))
        qhdr.grid_columnconfigure(0, weight=1)

        self._queue_count_var = ctk.StringVar(value="QUEUE — 0 ITEMS")
        T(ctk.CTkLabel(qhdr, textvariable=self._queue_count_var,
                       font=ctk.CTkFont("Segoe UI", 10, "bold"),
                       text_color=p["muted"]),
          text_color="muted").grid(row=0, column=0, sticky="w")

        T(ctk.CTkButton(qhdr, text="Clear All", height=26, width=72,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._clear_queue),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=2, padx=(4, 0))
        T(ctk.CTkButton(qhdr, text="Remove", height=26, width=66,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._remove),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=1)

        self._qlist = ctk.CTkScrollableFrame(
            qf, height=140, corner_radius=0,
            fg_color=p["bg"],
            scrollbar_button_color=p["surface2"],
            scrollbar_button_hover_color=p["muted2"],
        )
        self._qlist.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 8))
        self._qlist.grid_columnconfigure(0, weight=1)

        T(ctk.CTkFrame(qf, height=1, fg_color=p.get("separator", p["border"]), corner_radius=0),
          fg_color="separator").grid(row=3, column=0, sticky="ew")

        # ─ Action bar ───────────────────────────────────────────────
        # ══ ACTION BAR — 2 static rows, no horizontal scroll ══════════
        ab = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["panel"],
                            border_width=0), fg_color="panel")
        ab.grid(row=2, column=0, sticky="ew")
        ab.grid_columnconfigure(0, weight=1)
        # Keep the action/tool divider visible even after CTk geometry recalculation.
        ab.grid_rowconfigure(1, minsize=3)

        # helper — factory for secondary icon buttons
        def _ab_btn(parent, text, cmd, *, accent=False, danger=False,
                    green=False, width=None):
            kw = dict(
                text=text, height=30,
                font=ctk.CTkFont("Segoe UI", 10, "bold" if accent else "normal"),
                fg_color="#1d4ed8" if accent else p["surface2"],
                hover_color="#1e40af" if accent else
                            "#7f1d1d" if danger else
                            "#065f46" if green else p["surface"],
                text_color="#ffffff" if accent else
                           "#f87171" if danger else
                           "#6ee7b7" if green else p["muted"],
                border_width=0, corner_radius=6,
                command=cmd,
            )
            if width: kw["width"] = width
            return ctk.CTkButton(parent, **kw)

        # ── Row A: primary live controls + pinned status/progress ─────────
        # Keep the primary controls and status on one compact row. Buttons are
        # fixed-width; the status/progress group absorbs remaining width so
        # retry text cannot push Serve/Open Folder out of view.
        rowA = T(ctk.CTkFrame(ab, fg_color=p["panel"], corner_radius=0),
                 fg_color="panel")
        rowA.grid(row=0, column=0, sticky="ew", padx=0)
        rowA.grid_columnconfigure(5, weight=1)

        self._dl_btn = ctk.CTkButton(
            rowA, text="▶ Download All", height=32, width=126,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color="#3b82f6", hover_color="#2563eb",
            corner_radius=8, command=self._start)
        self._dl_btn.grid(row=0, column=0, padx=(12, 4), pady=(6, 6), sticky="w")

        T(ctk.CTkButton(rowA, text="🔍 Preview", height=32, width=90,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=0, corner_radius=8,
                        command=self._preview_queue),
          fg_color="surface2", text_color="muted").grid(
            row=0, column=1, padx=(0, 4), pady=(6, 6), sticky="w")

        self._pause_btn = T(ctk.CTkButton(
            rowA, text="⏸ Pause", height=32, width=86,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["surface2"], hover_color=p["surface"],
            text_color=p["muted"], border_width=0, corner_radius=8,
            state="disabled", command=self._toggle_pause),
            fg_color="surface2", hover_color="surface", text_color="muted")
        self._pause_btn.grid(row=0, column=2, padx=(0, 4), pady=(6, 6), sticky="w")

        self._srv_btn = T(ctk.CTkButton(
            rowA, text="⚡ Start Serve", height=32, width=112,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["surface2"], hover_color=p["srv_hv"],
            text_color=p["srv_fg"], border_width=0, corner_radius=8,
            command=self._toggle_server),
            fg_color="surface2", hover_color="srv_hv", text_color="srv_fg")
        self._srv_btn.grid(row=0, column=3, padx=(0, 4), pady=(6, 6), sticky="w")
        self._server_running = False

        T(ctk.CTkButton(rowA, text="📁 Folder", height=32, width=84,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=0, corner_radius=8,
                        command=self._open_folder),
          fg_color="surface2", text_color="muted").grid(
            row=0, column=4, padx=(0, 8), pady=(6, 6), sticky="w")

        self._status_var = ctk.StringVar(value="Idle")
        status_line = T(ctk.CTkFrame(rowA, fg_color="transparent"), fg_color="panel")
        status_line.grid(row=0, column=5, sticky="ew", padx=(6, 12), pady=(6, 6))
        status_line.grid_columnconfigure(1, weight=1)

        self._status_lbl = T(ctk.CTkLabel(
            status_line, textvariable=self._status_var,
            width=92,
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"], anchor="w"),
            text_color="muted")
        self._status_lbl.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self._pb = T(ctk.CTkProgressBar(
            status_line, width=110, height=5,
            fg_color=p["surface2"], progress_color="#3b82f6",
            mode="indeterminate", indeterminate_speed=1),
            fg_color="surface2")
        self._pb.grid(row=0, column=1, sticky="ew", padx=(0, 0))

        # Theme-aware divider between the primary action row and the tool strip.
        # Rendered as a tiny stacked strip instead of a single 1 px frame: on
        # some Windows/CustomTkinter scaling combinations a one-pixel dark line
        # gets swallowed by the surrounding panel. This keeps the line visible
        # without returning to the old harsh white separator.
        self._toolbar_divider_wrap = T(
            ctk.CTkFrame(ab, height=5, fg_color=p["panel"], corner_radius=0),
            fg_color="panel"
        )
        self._toolbar_divider_wrap.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 0))
        self._toolbar_divider_wrap.grid_propagate(False)
        self._toolbar_divider_wrap.grid_columnconfigure(0, weight=1)

        self._toolbar_divider_shadow = T(
            ctk.CTkFrame(
                self._toolbar_divider_wrap, height=1,
                fg_color=p.get("toolbar_separator_shadow", p.get("separator", p["border"])),
                corner_radius=0,
            ),
            fg_color="toolbar_separator_shadow"
        )
        self._toolbar_divider_shadow.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 0))

        self._toolbar_divider = T(
            ctk.CTkFrame(
                self._toolbar_divider_wrap, height=2,
                fg_color=p.get("toolbar_separator", p.get("separator", p["border"])),
                corner_radius=0,
            ),
            fg_color="toolbar_separator"
        )
        self._toolbar_divider.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 0))

        # ── Row B: compact single-line tool strip ─────────────────────
        # Keep common tools visible without burning vertical space. Buttons are
        # short and fixed-width; the log area receives the freed height.
        rowB_wrap = ctk.CTkFrame(ab, fg_color=p["panel"], corner_radius=0, height=38)
        rowB_wrap.grid(row=2, column=0, sticky="ew")
        rowB_wrap.grid_propagate(False)
        rowB_wrap.grid_columnconfigure(0, weight=1)
        T(rowB_wrap, fg_color="panel")
        self._rowB = rowB_wrap

        rowB1 = T(ctk.CTkFrame(rowB_wrap, fg_color="transparent"), fg_color="panel")
        rowB1.grid(row=0, column=0, sticky="w", padx=12, pady=(3, 3))
        self._rowB_wrap = rowB_wrap
        self._rowB1 = rowB1

        def _pill(parent, text, cmd, *,
                  bg="surface2", fg="muted", hv="surface", icon=None, width=86):
            label = f"{icon} {text}" if icon else text
            btn = ctk.CTkButton(
                parent, text=label, height=26, width=width,
                font=ctk.CTkFont("Segoe UI", 9),
                fg_color=p[bg], hover_color=p[hv],
                text_color=p[fg], corner_radius=11,
                border_width=0, command=cmd,
            )
            T(btn, fg_color=bg, hover_color=hv, text_color=fg)
            return btn

        def _sep(parent):
            f = ctk.CTkFrame(parent, width=1, height=20, fg_color=p["border"])
            T(f, fg_color="border")
            f.pack(side="left", padx=3, pady=3)

        _pill(rowB1, "Features", self._toggles_panel, icon="🎛",
              bg="accentbg", fg="accent", hv="accentbg_hv", width=88).pack(
            side="left", padx=(0, 1), pady=3)
        self._retry_btn = _pill(
            rowB1, "Retry Assets", self._retry_failed, icon="↺", width=92,
            bg="retry_asset_bg", fg="retry_asset_fg", hv="retry_asset_hv")
        self._retry_btn.pack(side="left", padx=(0, 1), pady=3)
        _pill(
            rowB1, "Retry Images", self._retry_failed_images, icon="🖼", width=96,
            bg="retry_image_bg", fg="retry_image_fg", hv="retry_image_hv").pack(
            side="left", padx=(0, 1), pady=3)
        _pill(
            rowB1, "Retry Audio", self._retry_youtube_audio, icon="🎵", width=90,
            bg="retry_audio_bg", fg="retry_audio_fg", hv="retry_audio_hv").pack(
            side="left", padx=(0, 1), pady=3)
        _sep(rowB1)

        _pill(rowB1, "Settings", self._settings_maintenance_panel, icon="🛠", width=96,
              bg="settings_bg", fg="settings_fg", hv="settings_hv").pack(
            side="left", padx=(0, 2), pady=3)

        _pill(rowB1, "Reports", self._show_results, icon="📋", width=88).pack(
            side="left", padx=(0, 1), pady=3)
        _sep(rowB1)

        _cm_on = self._cyoa_mgr_var.get()
        self._cm_btn = _pill(
            rowB1,
            "CYOA Mgr ✓" if _cm_on else "CYOA Mgr ✗",
            self._cyoa_manager_panel,
            bg="manager_bg",
            fg="manager_fg",
            hv="manager_hv",
            icon="📤", width=96,
        )
        self._cm_btn.pack(side="left", padx=(0, 1), pady=3)

        # AI Assist toggle remains in Feature Toggles/inline checkbox; do not
        # render a duplicate ON/OFF toolbar pill.

        # ─ Log ──────────────────────────────────────────────────────
        lf = T(ctk.CTkFrame(main, corner_radius=0, fg_color=p["panel"]),
               fg_color="panel")
        lf.grid(row=3, column=0, sticky="nsew")
        lf.grid_rowconfigure(1, weight=1)
        lf.grid_columnconfigure(0, weight=1)

        log_hdr = T(ctk.CTkFrame(lf, fg_color="transparent"), fg_color="panel")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(6, 3))
        log_hdr.grid_columnconfigure(0, weight=1)
        T(ctk.CTkLabel(log_hdr, text="LOG",
                       font=ctk.CTkFont("Segoe UI", 10, "bold"),
                       text_color=p["muted2"]),
          text_color="muted2").grid(row=0, column=0, sticky="w")
        T(ctk.CTkButton(log_hdr, text="Clear", height=24, width=52,
                        font=ctk.CTkFont("Segoe UI", 10),
                        fg_color=p["surface2"], hover_color=p["surface"],
                        text_color=p["muted"], border_width=1,
                        border_color=p["surface2"],
                        command=self._clear_log),
          fg_color="surface2", hover_color="surface", text_color="muted",
          border_color="surface2").grid(row=0, column=1)

        import tkinter as tk
        self._log_txt = tk.Text(
            lf, font=("Consolas", 10), wrap="word",
            bg=p["log_bg"], fg=p["log_fg"],
            relief="flat", bd=0,
            insertbackground=p["fg"],
            selectbackground="#1e3a5f",
            state="disabled",
        )
        self._log_txt.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 4))

        sb2 = tk.Scrollbar(lf, orient="vertical", command=self._log_txt.yview,
                           bg="#0a0d13", troughcolor="#0a0d13",
                           activebackground="#334155", width=10)
        sb2.grid(row=1, column=1, sticky="ns")
        self._log_txt.configure(yscrollcommand=sb2.set)

        ctk.CTkLabel(lf, text="Log written to: cyoa_downloader.log in output folder",
                     font=ctk.CTkFont("Segoe UI", 9),
                     text_color="#334155").grid(row=2, column=0, sticky="w",
                                                padx=14, pady=(0, 6))

        # Color tags
        self._log_txt.tag_configure("INFO",    foreground="#475569")
        self._log_txt.tag_configure("WARNING", foreground="#f59e0b")
        self._log_txt.tag_configure("ERROR",   foreground="#ef4444")
        self._log_txt.tag_configure("SUCCESS", foreground="#22c55e")
        self._log_txt.tag_configure("AUTO",    foreground="#a78bfa")

    # ════════════════════════════════════════════════════════════════
    def _setup_ui(self) -> None:
        """Compose the final v46.6 GUI setup chain from the app class."""
        final_setup = globals().get("_v466_setup_ui")
        if final_setup is None:
            return self._setup_ui_base()
        return final_setup(self)

    def _dispatch_gui_patch(self, patch_name: str, *args, fallback=None, **kwargs):
        patch_func = globals().get(patch_name)
        if patch_func is None:
            if fallback is not None:
                return fallback(*args, **kwargs)
            raise RuntimeError(f"GUI patch body is not available: {patch_name}")
        return patch_func(self, *args, **kwargs)

    def _v46_enqueue_progress(self, event: Dict[str, Any]) -> None:
        return self._dispatch_gui_patch("_v46_enqueue_progress", event)

    def _v46_set_event_sink(self) -> None:
        return self._dispatch_gui_patch("_v46_set_event_sink")

    def _v46_apply_progress_visibility(self, expanded: Optional[bool] = None) -> None:
        return self._dispatch_gui_patch("_v463_apply_progress_visibility", expanded)

    def _v46_toggle_progress_panel(self) -> None:
        return self._dispatch_gui_patch("_v46_toggle_progress_panel")

    def _v46_install_url_menu(self, label: Any, getter: Any, kind: str) -> None:
        return self._dispatch_gui_patch("_v46_install_url_menu", label, getter, kind)

    def _v462_refresh_responsive_layout(self) -> None:
        return self._dispatch_gui_patch("_v462_refresh_responsive_layout")

    def _v463_arrange_progress_and_log(self) -> None:
        return self._dispatch_gui_patch("_v463_arrange_progress_and_log")

    def _v463_rebuild_progress_workspace(self) -> None:
        return self._dispatch_gui_patch("_v463_rebuild_progress_workspace")

    def _v46_cancel(self) -> None:
        return self._dispatch_gui_patch("_v46_cancel")

    def _v46_on_close(self) -> None:
        return self._dispatch_gui_patch("_v46_on_close")

    def _v46_finish_close(self) -> None:
        return self._dispatch_gui_patch("_v46_finish_close")

    def _v46_copy_error(self) -> None:
        return self._dispatch_gui_patch("_v46_copy_error")

    def _v46_poll_progress(self) -> None:
        return self._dispatch_gui_patch("_v46_poll_progress")

    def _v46_render_progress(self, state: Dict[str, Any]) -> None:
        return self._dispatch_gui_patch("_v46_render_progress", state)

    def _v46_draw_speed_graph(self) -> None:
        return self._dispatch_gui_patch("_v46_draw_speed_graph")

    # SIDEBAR MODE SELECTION
    # ════════════════════════════════════════════════════════════════
    def _select_mode(self, val: str, update_ui: bool = True, log_change: bool = False) -> None:
        old_val = getattr(self, "_mode_var", None)
        self._mode_var = val
        if log_change and old_val != val:
            try:
                label = next((m[2] for m in self.MODES if m and m[0] == val), val)
            except Exception:
                label = val
            logger.info(f"[Mode] Output mode changed: {old_val or '-'} → {val} ({label})")
        if not update_ui or not hasattr(self, "_mode_btns"):
            return
        p = self._p()
        for v, (row, bar, icon_box, name_lbl, desc_lbl) in self._mode_btns.items():
            is_sel = (v == val)
            bg     = p["sel_row"] if is_sel else "transparent"
            try:
                row.configure(fg_color=bg)
                bar.configure(fg_color=p["sel_bar"] if is_sel else "transparent")
                icon_box.configure(
                    fg_color=p["sel_icon"] if is_sel else "transparent",
                    text_color=p["sel_nm"]  if is_sel else p["muted"],
                )
                name_lbl.master.configure(fg_color=bg)
                name_lbl.configure(fg_color=bg, text_color=p["sel_nm"]   if is_sel else p["fg"])
                desc_lbl.configure(fg_color=bg, text_color=p["sel_desc"] if is_sel else p["muted"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _select_mode (line 6146): %s", _ignored_exc)
        self._update_mode_info(val)

    # ════════════════════════════════════════════════════════════════
    # THEME TOGGLE
    # ════════════════════════════════════════════════════════════════
    def _update_mode_info(self, val: str) -> None:
        """Update sidebar info box to describe current mode and expected output."""
        auto_pref = _normalize_auto_detect_output(_load_settings().get("auto_detect_output", "folder"))
        if auto_pref == "zip":
            auto_pair_en = "website_zip / cyoap_vue_zip"
            auto_pair_id = "website_zip / cyoap_vue_zip"
            auto_default_en = "Auto default: ZIP"
            auto_default_id = "Default Auto: ZIP"
        else:
            auto_pair_en = "website_folder / cyoap_vue_folder"
            auto_pair_id = "website_folder / cyoap_vue_folder"
            auto_default_en = "Auto default: Folder"
            auto_default_id = "Default Auto: Folder"
        INFO_EN = {
            "auto": (
                "Auto (detect)",
                f"{auto_default_en}. Detects cyoap_vue first, then project.json, then falls back to ICC mode.",
                f"Output: embed or {auto_pair_en}\nSetting: Settings → Default Auto Output"
            ),
            "embed": (
                "Embedded JSON",
                "Creates one portable JSON with image data embedded as base64. Best for classic project.json backups.",
                "Output: ProjectName.json\nNo website viewer folder"
            ),
            "zip": (
                "ZIP",
                "Keeps project.json plus images/audio/fonts as separate files inside one archive.",
                "Output: ProjectName.zip\nGood for smaller JSON files"
            ),
            "both": (
                "Both",
                "Runs Embedded JSON and ZIP in the same job without changing naming rules.",
                "Output: ProjectName.json + ProjectName.zip"
            ),
            "website_zip": (
                "ICC ZIP",
                "Downloads a full offline ICC viewer: HTML, CSS, JS, images, audio, fonts, and reports.",
                "Output: ProjectName_site.zip\nOpen index.html after extraction"
            ),
            "website_folder": (
                "ICC Folder",
                "Same ICC viewer capture as ICC ZIP, but leaves files as a normal folder.",
                "Output: ProjectName_site/\nBest for preview and serve"
            ),
            "pure_website_zip": (
                "Pure Website ZIP",
                "Captures the visible website only and skips project.json discovery. Use for custom/non-standard viewers.",
                "Output: ProjectName_site.zip\nNo project.json search"
            ),
            "pure_website_folder": (
                "Pure Website Folder",
                "Pure Website capture kept as a folder. Useful for debugging assets before archiving.",
                "Output: ProjectName_site/\nNo project.json search"
            ),
            "cyoap_vue_zip": (
                "cyoap_vue ZIP",
                "Dedicated cyoap_vue engine backup including dist/platform.json, dist/nodes, and viewer assets.",
                "Output: ProjectName_site.zip\ncyoap_vue-specific structure"
            ),
            "cyoap_vue_folder": (
                "cyoap_vue Folder",
                "Same cyoap_vue backup as ZIP mode, but kept as a folder for serve/preview.",
                "Output: ProjectName_site/\ncyoap_vue-specific structure"
            ),
        }
        INFO_ID = {
            "auto": (
                "Auto (deteksi)",
                f"{auto_default_id}. Deteksi cyoap_vue dulu, lalu project.json, lalu fallback ke mode ICC.",
                f"Output: embed atau {auto_pair_id}\nAtur di: Settings → Default Output Auto"
            ),
            "embed": (
                "JSON Tertanam",
                "Membuat satu JSON portabel dengan data gambar sebagai base64. Cocok untuk backup project.json klasik.",
                "Output: NamaProject.json\nTanpa folder ICC viewer"
            ),
            "zip": (
                "ZIP",
                "Menyimpan project.json dan gambar/audio/font sebagai file terpisah di dalam satu arsip.",
                "Output: NamaProject.zip\nJSON lebih kecil"
            ),
            "both": (
                "Keduanya",
                "Menjalankan JSON tertanam dan ZIP dalam satu job tanpa mengubah aturan nama output.",
                "Output: NamaProject.json + NamaProject.zip"
            ),
            "website_zip": (
                "ZIP ICC",
                "Mengunduh viewer offline lengkap: HTML, CSS, JS, gambar, audio, font, dan laporan.",
                "Output: NamaProject_site.zip\nBuka index.html setelah ekstrak"
            ),
            "website_folder": (
                "Folder ICC",
                "Capture viewer ICC seperti ICC ZIP, tetapi disimpan sebagai folder biasa.",
                "Output: NamaProject_site/\nPaling enak untuk preview dan serve"
            ),
            "pure_website_zip": (
                "ZIP Pure Website",
                "Mengambil website yang terlihat tanpa mencari project.json. Cocok untuk viewer custom/non-standar.",
                "Output: NamaProject_site.zip\nTanpa pencarian project.json"
            ),
            "pure_website_folder": (
                "Folder Pure Website",
                "Capture Pure Website disimpan sebagai folder. Berguna untuk debug aset sebelum diarsipkan.",
                "Output: NamaProject_site/\nTanpa pencarian project.json"
            ),
            "cyoap_vue_zip": (
                "ZIP cyoap_vue",
                "Backup khusus engine cyoap_vue, termasuk dist/platform.json, dist/nodes, dan aset viewer.",
                "Output: NamaProject_site.zip\nStruktur khusus cyoap_vue"
            ),
            "cyoap_vue_folder": (
                "Folder cyoap_vue",
                "Backup cyoap_vue seperti mode ZIP, tetapi disimpan sebagai folder untuk serve/preview.",
                "Output: NamaProject_site/\nStruktur khusus cyoap_vue"
            ),
        }
        INFO = INFO_EN if getattr(self, "_language", "id") == "en" else INFO_ID
        title, body, output = INFO.get(val, (val, "", ""))
        if hasattr(self, "_info_title"):
            try:
                p = self._p()
                self._info_title.configure(text=title)
                self._info_body.configure(text=body, text_color=p["muted"])
                self._info_output.configure(text=output)
                self._info_box.configure(fg_color=p["surface2"])
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _update_mode_info (line 6279): %s", _ignored_exc)



    def _on_cloudflare_mode_change(self, value: str, validate: bool = True) -> None:
        """Apply Cloudflare mode from the compact selector."""
        mode = _normalize_cloudflare_mode(value)
        st = _load_settings()
        _set_cloudflare_config(
            mode,
            flaresolverr_url=st.get("flaresolverr_url", _FLARESOLVERR_URL),
            session_policy=st.get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY),
            timeout=_coerce_int(st.get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT), _FLARESOLVERR_TIMEOUT),
            wait_after=_coerce_int(st.get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER), _FLARESOLVERR_WAIT_AFTER),
            proxy_mode=st.get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE),
            persist=True,
        )
        try:
            self._cf_mode_var.set(_display_cloudflare_mode(mode))
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _on_cloudflare_mode_change (line 6299): %s", _ignored_exc)
        if validate:
            logger.info(f"[Feature] Cloudflare mode set: {_display_cloudflare_mode(mode)}")
        if validate and mode == "cloudscraper":
            try:
                import cloudscraper  # noqa
                logger.info("[Cloudflare] cloudscraper available and active")
            except ImportError:
                from tkinter import messagebox
                if getattr(self, "_language", "id") == "en":
                    title = "cloudscraper not installed"
                    body = (
                        "Install it first:\n\n  pip install cloudscraper\n\n"
                        "or choose Auto/FlareSolverr if FlareSolverr is already running."
                    )
                else:
                    title = "cloudscraper belum terpasang"
                    body = (
                        "Instal terlebih dahulu:\n\n  pip install cloudscraper\n\n"
                        "atau pilih Auto/FlareSolverr jika FlareSolverr sudah berjalan."
                    )
                messagebox.showwarning(title, body)
        elif validate and mode == "flaresolverr":
            logger.info(f"[Cloudflare] FlareSolverr mode selected: {_FLARESOLVERR_URL}")

    # Backward-compatible alias for old callbacks.
    def _on_cf_bypass_toggle(self) -> None:
        self._on_cloudflare_mode_change("cloudscraper")

    def _on_http2_toggle(self) -> None:
        enabled = bool(self._http2_var.get())
        # _HTTP2_ENABLED is copied into this module during compatibility
        # bootstrap, so reading it here can return the old value even though
        # throttle.py successfully enabled HTTP/2 in runtime.state.
        final_enabled = bool(_set_http2_enabled(enabled))
        if enabled and not final_enabled:
            self._http2_var.set(False)
            from tkinter import messagebox
            import sys as _sys
            from ..network.throttle import http2_runtime_info
            _http2_info = http2_runtime_info()
            _install_cmd = f'"{_sys.executable}" -m pip install "httpx[http2]"'
            _reason = _http2_info.get("detail") or "httpx[http2] belum lengkap"
            if getattr(self, "_language", "id") == "en":
                title = "HTTP/2 dependency unavailable"
                body = (
                    f"The active Python cannot use HTTP/2:\n\n{_reason}\n\n"
                    f"Install into this exact Python:\n  {_install_cmd}\n\n"
                    "Then restart the program and re-enable HTTP/2."
                )
            else:
                title = "Dependensi HTTP/2 belum tersedia"
                body = (
                    f"Python yang sedang menjalankan program belum bisa memakai HTTP/2:\n\n{_reason}\n\n"
                    f"Instal ke Python yang sama:\n  {_install_cmd}\n\n"
                    "Lalu mulai ulang program dan aktifkan kembali HTTP/2."
                )
            messagebox.showwarning(title, body)
        final_enabled = bool(self._http2_var.get()) and final_enabled
        _update_setting("http2_enabled", final_enabled)
        logger.info(f"[Feature] HTTP/2: {'enabled' if final_enabled else 'disabled'}")

    def _on_ytdlp_toggle(self) -> None:
        if self._ytdlp_var.get():
            try:
                import yt_dlp
                logger.info("YT Audio: yt-dlp available, YouTube audio will be downloaded automatically")
            except ImportError:
                from tkinter import messagebox
                self._ytdlp_var.set(False)
                if getattr(self, "_language", "id") == "en":
                    title = "yt-dlp not installed"
                    body = (
                        "Install it first:\n\n  pip install yt-dlp\n\n"
                        "ffmpeg is also required for MP3 conversion:\n"
                        "  https://ffmpeg.org/download.html\n\n"
                        "then restart the program and re-enable YT Audio."
                    )
                else:
                    title = "yt-dlp belum terpasang"
                    body = (
                        "Instal terlebih dahulu:\n\n  pip install yt-dlp\n\n"
                        "ffmpeg juga diperlukan untuk konversi MP3:\n"
                        "  https://ffmpeg.org/download.html\n\n"
                        "lalu mulai ulang program dan aktifkan kembali YT Audio."
                    )
                messagebox.showwarning(title, body)
        logger.info(f"[Feature] YouTube audio: {'enabled' if bool(self._ytdlp_var.get()) else 'disabled'}")

    def _on_fonts_toggle(self) -> None:
        enabled = bool(getattr(self, "_fonts_var", None).get()) if hasattr(self, "_fonts_var") else False
        logger.info(f"[Feature] Font download: {'enabled' if enabled else 'disabled'}")

    def _on_font_analysis_toggle(self) -> None:
        enabled = bool(getattr(self, "_analyse_var", None).get()) if hasattr(self, "_analyse_var") else False
        logger.info(f"[Feature] Font analysis: {'enabled' if enabled else 'disabled'}")

    def _toggle_theme(self, val: str) -> None:
        self._theme_mode = _normalize_theme_mode(val)
        self._is_dark = _resolve_theme_is_dark(self._theme_mode)
        _update_setting("theme_mode", self._theme_mode)
        self._apply_theme()
        logger.info(f"GUI theme set: {self._theme_mode}")

    def _toggle_language(self, val: str) -> None:
        """Switch GUI microcopy between Indonesian and English."""
        self._language = "en" if str(val).upper().startswith("EN") else "id"
        _update_setting("language", self._language)
        self._apply_language()
        # v46.9: the progress card's static labels (Cancel / Copy Error /
        # Show Details) are set at build time, so re-localize them on a live
        # language switch. Dynamic telemetry text already re-localizes via the
        # 125 ms poll. Rebuild only when idle to avoid destroying live widgets.
        if not getattr(self, "_is_running", False):
            try:
                _v463_rebuild_progress_workspace(self)
            except Exception as exc:
                logger.debug(f"Progress re-localize skipped: {exc}")
        logger.info(f"GUI language set: {self._language}")

    def _tr(self, key: str) -> str:
        texts = {
            "download_all": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "browse": {"id": "Browse…", "en": "Browse…"},
            "output_folder": {"id": "Folder output:", "en": "Output folder:"},
            "ytdlp_cookies": {"id": "Cookie YouTube:", "en": "YouTube cookies:"},
            "clear": {"id": "Bersihkan", "en": "Clear"},
            "import_list": {"id": "Import List…", "en": "Import List…"},
            "export_list": {"id": "Ekspor List…", "en": "Export List…"},
            "queue_empty_title": {"id": "Queue kosong", "en": "Queue Empty"},
            "queue_empty_body": {"id": "Tambahkan minimal satu URL.", "en": "Add at least one URL."},
            "downloading": {"id": "Mengunduh…", "en": "Downloading…"},
            "idle": {"id": "Siap", "en": "Idle"},
            "add_url": {"id": "➕  Tambah URL", "en": "➕  Add URL"},
        }
        lang = getattr(self, "_language", "id")
        return texts.get(key, {}).get(lang, texts.get(key, {}).get("en", key))

    def _translation_pairs(self) -> Dict[str, Dict[str, str]]:
        """Exact GUI text translation map. Keys are the English canonical text."""
        return {
            "Input": {"id": "Input", "en": "Input"},
            "URL": {"id": "URL", "en": "URL"},
            "Filename": {"id": "Nama file", "en": "Filename"},
            "Tambah +": {"id": "Tambah +", "en": "Add +"},
            "➕  Add URL": {"id": "➕  Tambah URL", "en": "➕  Add URL"},
            "➕  Tambah URL": {"id": "➕  Tambah URL", "en": "➕  Add URL"},
            "+  Tambah URL": {"id": "➕  Tambah URL", "en": "➕  Add URL"},
            "Tambah URL": {"id": "Tambah URL", "en": "Add URL"},
            "Threads:": {"id": "Thread:", "en": "Threads:"},
            "Retry (s):": {"id": "Retry (dtk):", "en": "Retry (s):"},
            "BW (KB/s):": {"id": "BW (KB/dtk):", "en": "BW (KB/s):"},
            "Proxy:": {"id": "Proxy:", "en": "Proxy:"},
            "DNS:": {"id": "DNS:", "en": "DNS:"},
            "Import List…": {"id": "Import List…", "en": "Import List…"},
            "Export List…": {"id": "Ekspor List…", "en": "Export List…"},
            "Clear All": {"id": "Bersihkan", "en": "Clear All"},
            "Remove": {"id": "Hapus", "en": "Remove"},
            "▶  Download All": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "▶  Download Semua": {"id": "▶  Download Semua", "en": "▶  Download All"},
            "🔍 Preview": {"id": "🔍 Pratinjau", "en": "🔍 Preview"},
            "⚡ Serve": {"id": "⚡ Server", "en": "⚡ Serve"},
            "📁 Open Folder": {"id": "📁 Buka Folder", "en": "📁 Open Folder"},
            "Viewers": {"id": "Viewer", "en": "Viewers"},
            "Batch Export": {"id": "Ekspor Batch", "en": "Batch Export"},
            "Results": {"id": "Hasil", "en": "Results"},
            "Panduan": {"id": "Panduan", "en": "Guide"},
            "Guide": {"id": "Panduan", "en": "Guide"},
            "Retry Failed": {"id": "Ulang Gagal", "en": "Retry Failed"},
            "Retry Images": {"id": "Ulang Gambar", "en": "Retry Images"},
            "Retry YT Audio": {"id": "Ulang Audio YT", "en": "Retry YT Audio"},
            "Pause": {"id": "Jeda", "en": "Pause"},
            "Continue": {"id": "Lanjutkan", "en": "Continue"},
            "Resume": {"id": "Lanjut", "en": "Resume"},
            "Cache": {"id": "Cache", "en": "Cache"},
            "Updates": {"id": "Update", "en": "Updates"},
            "Batch Check": {"id": "Cek Batch", "en": "Batch Check"},
            "CM Import": {"id": "Import CM", "en": "CM Import"},
            "LOG": {"id": "LOG", "en": "LOG"},
            "Clear": {"id": "Bersihkan", "en": "Clear"},
            "Log written to: cyoa_downloader.log in output folder": {
                "id": "Log ditulis ke: cyoa_downloader.log di folder output",
                "en": "Log written to: cyoa_downloader.log in output folder"
            },
            "OUTPUT MODE": {"id": "MODE OUTPUT", "en": "OUTPUT MODE"},
            "ICC MODE": {"id": "MODE ICC", "en": "ICC MODE"},
            "PURE WEBSITE": {"id": "PURE WEBSITE", "en": "PURE WEBSITE"},
            "Auto (detect)": {"id": "Auto (deteksi)", "en": "Auto (detect)"},
            "Default: Settings": {"id": "Default: Pengaturan", "en": "Default: Settings"},
            "Default: ICC Folder": {"id": "Default: Folder ICC", "en": "Default: ICC Folder"},
            "Default: Folder output": {"id": "Default: Output folder", "en": "Default: Folder output"},
            "Embedded JSON": {"id": "JSON Tertanam", "en": "Embedded JSON"},
            "ZIP": {"id": "ZIP", "en": "ZIP"},
            "Both": {"id": "Keduanya", "en": "Both"},
            "ICC ZIP": {"id": "ZIP ICC", "en": "ICC ZIP"},
            "ICC Folder": {"id": "Folder ICC", "en": "ICC Folder"},
            "Pure Website ZIP": {"id": "ZIP Pure Website", "en": "Pure Website ZIP"},
            "Pure Website Folder": {"id": "Folder Pure Website", "en": "Pure Website Folder"},
            "cyoap_vue ZIP": {"id": "ZIP cyoap_vue", "en": "cyoap_vue ZIP"},
            "cyoap_vue Folder": {"id": "Folder cyoap_vue", "en": "cyoap_vue Folder"},
            "Images embedded in JSON": {"id": "Gambar tertanam di JSON", "en": "Images embedded in JSON"},
            "JSON + separate assets": {"id": "JSON + aset terpisah", "en": "JSON + separate assets"},
            "Embed + ZIP together": {"id": "Embed + ZIP bersamaan", "en": "Embed + ZIP together"},
            "Offline viewer files": {"id": "File viewer offline", "en": "Offline viewer files"},
            "Offline viewer archive": {"id": "Arsip viewer offline", "en": "Offline viewer archive"},
            "Offline viewer folder": {"id": "Folder viewer offline", "en": "Offline viewer folder"},
            "Viewer only, no project scan": {"id": "Viewer saja, tanpa scan project", "en": "Viewer only, no project scan"},
            "Viewer folder, no project scan": {"id": "Folder viewer, tanpa scan project", "en": "Viewer folder, no project scan"},
            "cyoap_vue engine backup": {"id": "Backup engine cyoap_vue", "en": "cyoap_vue engine backup"},
            "cyoap_vue engine archive": {"id": "Arsip engine cyoap_vue", "en": "cyoap_vue engine archive"},
            "cyoap_vue engine folder": {"id": "Folder engine cyoap_vue", "en": "cyoap_vue engine folder"},
            "ICC backup · serve preview · diagnostics": {"id": "backup offline · pratinjau server · diagnostik", "en": "ICC backup · serve preview · diagnostics"},
            "Idle": {"id": "Siap", "en": "Idle"},
            "Output folder:": {"id": "Folder output:", "en": "Output folder:"},
            "📤 CYOA Manager ": {"id": "📤 CYOA Manager ", "en": "📤 CYOA Manager "},
            "🤖 AI  ": {"id": "🤖 AI  ", "en": "🤖 AI  "},
            "Probing URLs sebelum download dimulai…": {"id": "Mengecek URL sebelum download dimulai…", "en": "Probing URLs before download starts…"},
            "▶ Proceed with Download": {"id": "▶ Lanjutkan Download", "en": "▶ Proceed with Download"},
            "⏸ Pause": {"id": "⏸ Jeda", "en": "⏸ Pause"},
            "⏸ Pause Download": {"id": "⏸ Jeda Download", "en": "⏸ Pause Download"},
            "▶ Continue Download": {"id": "▶ Lanjutkan Download", "en": "▶ Continue Download"},
            "📦  Batch Export → CYOA Manager": {"id": "📦  Ekspor Batch → CYOA Manager", "en": "📦  Batch Export → CYOA Manager"},
            "📤 Export to CYOA Manager": {"id": "📤 Ekspor ke CYOA Manager", "en": "📤 Export to CYOA Manager"},
            "📤  CYOA Manager Integration": {"id": "📤  Integrasi CYOA Manager", "en": "📤  CYOA Manager Integration"},
            "Click 📂 to browse…": {"id": "Klik 📂 untuk memilih…", "en": "Click 📂 to browse…"},
            "📄 Tambah project.json": {"id": "📄 Tambah project.json", "en": "📄 Add project.json"},
            "📋 Add All (session ini)": {"id": "📋 Tambahkan Semua (sesi ini)", "en": "📋 Add All (this session)"},
            "📁 Batch Export Folder": {"id": "📁 Folder Ekspor Batch", "en": "📁 Batch Export Folder"},
            "Manual — tambahkan project.json:": {"id": "Manual — tambahkan project.json:", "en": "Manual — add project.json:"},
            "📄 Pilih project.json": {"id": "📄 Pilih project.json", "en": "📄 Select project.json"},
            "📋 Add All from Last Session": {"id": "📋 Tambahkan Semua dari Sesi Terakhir", "en": "📋 Add All from Last Session"},
            "▶ Resume": {"id": "▶ Lanjut", "en": "▶ Resume"},
            "Cache speeds up re-downloading the same images.\n": {"id": "Cache mempercepat download ulang gambar yang sama.\n", "en": "Cache speeds up re-downloading the same images.\n"},
            "🗑  Clear Image Cache": {"id": "🗑  Bersihkan Cache Gambar", "en": "🗑  Clear Image Cache"},
            "🔍 Search by name…": {"id": "🔍 Cari berdasarkan nama…", "en": "🔍 Search by name…"},
            "📥 Queue Selected": {"id": "📥 Masukkan yang Dipilih ke Queue", "en": "📥 Queue Selected"},
            "AI Assist — Claude API Integration": {"id": "AI Assist — Integrasi Claude API", "en": "AI Assist — Claude API Integration"},
            "💾 Save": {"id": "💾 Simpan", "en": "💾 Save"},
            "Checking…": {"id": "Mengecek…", "en": "Checking…"},
            "All CYOAs are still up-to-date ✅": {"id": "Semua CYOA masih terbaru ✅", "en": "All CYOAs are still up-to-date ✅"},
            "CYOA Downloader — Panduan Fitur": {"id": "CYOA Downloader — Panduan Fitur", "en": "CYOA Downloader — Feature Guide"},
            f"Panduan Fitur — CYOA Downloader v{_APP_VERSION}": {"id": f"Panduan Fitur — CYOA Downloader v{_APP_VERSION}", "en": f"Feature Guide — CYOA Downloader v{_APP_VERSION}"},
            "Offline Viewers": {"id": "Viewer Offline", "en": "Offline Viewers"},
            "+ Add ZIP": {"id": "+ Tambah ZIP", "en": "+ Add ZIP"},
            "Refresh": {"id": "Muat ulang", "en": "Refresh"},
            "Remove selected": {"id": "Hapus yang dipilih", "en": "Remove selected"},
            "No offline viewers registered.": {"id": "Belum ada viewer offline terdaftar.", "en": "No offline viewers registered."},
            "Cloudflare Bypass": {"id": "Bypass Cloudflare", "en": "Cloudflare Bypass"},
            "Cloudflare:": {"id": "Cloudflare:", "en": "Cloudflare:"},
            "Cloudflare": {"id": "Cloudflare", "en": "Cloudflare"},
            "Cloudflare Access": {"id": "Akses Cloudflare", "en": "Cloudflare Access"},
            "Test Connection": {"id": "Tes Koneksi", "en": "Test Connection"},
            "Clear Sessions": {"id": "Bersihkan Sesi", "en": "Clear Sessions"},
            "Recommended setup": {"id": "Pengaturan yang disarankan", "en": "Recommended setup"},
            "Network": {"id": "Jaringan", "en": "Network"},
            "Advanced": {"id": "Lanjutan", "en": "Advanced"},
            "Settings": {"id": "Pengaturan", "en": "Settings"},
            "Batch / Queue": {"id": "Batch / Antrean", "en": "Batch / Queue"},
            "Preview Tools": {"id": "Alat Pratinjau", "en": "Preview Tools"},
            "Settings / Maintenance": {"id": "Pengaturan / Pemeliharaan", "en": "Settings / Maintenance"},
            "Logs / Diagnostics": {"id": "Log / Diagnostik", "en": "Logs / Diagnostics"},
            "Advanced Tools": {"id": "Alat Lanjutan", "en": "Advanced Tools"},
            "itch.io Controls": {"id": "Kontrol itch.io", "en": "itch.io Controls"},
            "AI Assist Settings": {"id": "Pengaturan AI Assist", "en": "AI Assist Settings"},
            "Auto Detect Output": {"id": "Output Auto Detect", "en": "Auto Detect Output"},
            "Default Auto Output": {"id": "Default Output Auto", "en": "Default Auto Output"},
            "⚡  Default Auto Output": {"id": "⚡  Default Output Auto", "en": "⚡  Default Auto Output"},
            "⚡  Auto Detect Output": {"id": "⚡  Output Auto Detect", "en": "⚡  Auto Detect Output"},
            "Choose whether Auto mode outputs ICC/cyoap_vue as folder or ZIP.": {"id": "Pilih apakah mode Auto menghasilkan ICC/cyoap_vue sebagai folder atau ZIP.", "en": "Choose whether Auto mode outputs ICC/cyoap_vue as folder or ZIP."},
            "Test": {"id": "Tes", "en": "Test"},
            "Enabled": {"id": "Aktif", "en": "Enabled"},
            "Disabled": {"id": "Nonaktif", "en": "Disabled"},
            "Auto": {"id": "Auto", "en": "Auto"},
            "Save": {"id": "Simpan", "en": "Save"},
            "Cancel": {"id": "Batal", "en": "Cancel"},
            "Close": {"id": "Tutup", "en": "Close"},
            "Open": {"id": "Buka", "en": "Open"},
            "Status": {"id": "Status", "en": "Status"},
            "Folder": {"id": "Folder", "en": "Folder"},
            "Mode": {"id": "Mode", "en": "Mode"},
            "Source URL": {"id": "URL sumber", "en": "Source URL"},
            "Output": {"id": "Output", "en": "Output"},
            "Start": {"id": "Mulai", "en": "Start"},
            "Stop": {"id": "Berhenti", "en": "Stop"},
            "Download": {"id": "Unduh", "en": "Download"},
            "Features": {"id": "Fitur", "en": "Features"},
            "Feature": {"id": "Fitur", "en": "Feature"},
            "Done": {"id": "Selesai", "en": "Done"},
            "Failed": {"id": "Gagal", "en": "Failed"},
            "Dependency Check": {"id": "Cek Dependensi", "en": "Dependency Check"},
            "Self Test": {"id": "Tes Mandiri", "en": "Self Test"},
            "Guidelines": {"id": "Pedoman", "en": "Guidelines"},
            "Bug Fixes": {"id": "Perbaikan Bug", "en": "Bug Fixes"},
            "Feature Tests": {"id": "Tes Fitur", "en": "Feature Tests"},
            "Serve Tools": {"id": "Alat Serve", "en": "Serve Tools"},
            "Serve Developer Tools": {"id": "Serve Developer Tools", "en": "Serve Developer Tools"},
            "Local override helpers": {"id": "Helper override lokal", "en": "Local override helpers"},
            "ICC Plus compatibility": {"id": "Kompatibilitas ICC Plus", "en": "ICC Plus compatibility"},
            "IndexedDB build save tools": {"id": "Alat save build IndexedDB", "en": "IndexedDB build save tools"},
            "Apply ICC Plus viewerConfig": {"id": "Terapkan viewerConfig ICC Plus", "en": "Apply ICC Plus viewerConfig"},
            "Safe preview state policy": {"id": "Kebijakan state preview aman", "en": "Safe preview state policy"},
            "Svelte-aware Developer Tools": {"id": "Developer Tools sadar Svelte", "en": "Svelte-aware Developer Tools"},
            "Local cheat helpers": {"id": "Helper cheat lokal", "en": "Local cheat helpers"},
            "Clear preview storage": {"id": "Bersihkan storage preview", "en": "Clear preview storage"},
            "Export localStorage": {"id": "Ekspor localStorage", "en": "Export localStorage"},
            "Import localStorage": {"id": "Impor localStorage", "en": "Import localStorage"},
            "Reveal disabled UI": {"id": "Tampilkan UI disabled", "en": "Reveal disabled UI"},
            "Open with tools": {"id": "Buka dengan tools", "en": "Open with tools"},
            "Comprehensive Check": {"id": "Cek Komprehensif", "en": "Comprehensive Check"},
            "Download failed": {"id": "Download gagal", "en": "Download failed"},
            "Download complete": {"id": "Download selesai", "en": "Download complete"},
            "Open report": {"id": "Buka laporan", "en": "Open report"},
            "Missing dependency": {"id": "Dependensi belum tersedia", "en": "Missing dependency"},
            "Optional dependency": {"id": "Dependensi opsional", "en": "Optional dependency"},
            "Userscript Lab": {"id": "Lab Userscript", "en": "Userscript Lab"},
            "Load bundled IntCyoaEnhancer helper": {"id": "Muat IntCyoaEnhancer lokal", "en": "Load bundled IntCyoaEnhancer helper"},
            "Load from GreasyFork": {"id": "Muat dari GreasyFork", "en": "Load from GreasyFork"},
            "Credit / Source": {"id": "Credit / Sumber", "en": "Credit / Source"},
            "Native Bridge": {"id": "Bridge Native", "en": "Native Bridge"},
            "Serve-only userscript injector": {"id": "Injector userscript khusus Serve", "en": "Serve-only userscript injector"},
            "External userscript credit": {"id": "Credit userscript eksternal", "en": "External userscript credit"},
            "Load ICE Local": {"id": "Muat ICE Lokal", "en": "Load ICE Local"},
            "Load ICE Web": {"id": "Muat ICE Web", "en": "Load ICE Web"},
            "🩺 Diagnostics": {"id": "🩺 Diagnostik", "en": "🩺 Diagnostics"},
            "© Credits": {"id": "© Kredit", "en": "© Credits"},
            "❔ Help / Guide": {"id": "❔ Bantuan / Panduan", "en": "❔ Help / Guide"},
            "Retry Assets": {"id": "Ulang Aset", "en": "Retry Assets"},
            "Retry Audio": {"id": "Ulang Audio", "en": "Retry Audio"},
            "Reports": {"id": "Laporan", "en": "Reports"},
            "❔ Bantuan / Guide": {"id": "❔ Bantuan / Panduan", "en": "❔ Help / Guide"},
            "Bantuan / Guide": {"id": "Bantuan / Panduan", "en": "Help / Guide"},
            "Bantuan / Panduan": {"id": "Bantuan / Panduan", "en": "Help / Guide"},
            "Pengaturan / Maintenance": {"id": "Pengaturan / Pemeliharaan", "en": "Settings / Maintenance"},
            "CYOA Mgr ✓": {"id": "CYOA Mgr ✓", "en": "CYOA Mgr ✓"},
            "CYOA Mgr ✗": {"id": "CYOA Mgr ✗", "en": "CYOA Mgr ✗"},
            "Start Serve": {"id": "Mulai Server", "en": "Start Serve"},
            "Stop Serve": {"id": "Hentikan Server", "en": "Stop Serve"},
            "Feature Toggles": {"id": "Toggle Fitur", "en": "Feature Toggles"},
            "Enable deep scan (JS/CSS asset discovery)": {"id": "Aktifkan deep scan (penemuan aset JS/CSS)", "en": "Enable deep scan (JS/CSS asset discovery)"},
            "Enable Selenium / headless image fallback": {"id": "Aktifkan fallback gambar Selenium/headless", "en": "Enable Selenium / headless image fallback"},
            "Enable serve preview (local HTTP server)": {"id": "Aktifkan pratinjau Serve (server HTTP lokal)", "en": "Enable serve preview (local HTTP server)"},
            "Enable cheat panel (bundled ICE helper)": {"id": "Aktifkan panel cheat (helper ICE bawaan)", "en": "Enable cheat panel (bundled ICE helper)"},
            "Enable gallery-dl fallback (smart mode)": {"id": "Aktifkan fallback gallery-dl (mode smart)", "en": "Enable gallery-dl fallback (smart mode)"},
            "Changes take effect immediately and are saved.": {"id": "Perubahan langsung berlaku dan disimpan.", "en": "Changes take effect immediately and are saved."},
            "Open settings.json": {"id": "Buka settings.json", "en": "Open settings.json"},
            "Open settings folder": {"id": "Buka folder settings", "en": "Open settings folder"},
            "Open gallery-dl config": {"id": "Buka config gallery-dl", "en": "Open gallery-dl config"},
            "Export Settings": {"id": "Ekspor Pengaturan", "en": "Export Settings"},
            "Import Settings": {"id": "Impor Pengaturan", "en": "Import Settings"},
            "Image Cache": {"id": "Cache Gambar", "en": "Image Cache"},
            "Settings files": {"id": "File pengaturan", "en": "Settings files"},
            "Runtime configuration": {"id": "Konfigurasi runtime", "en": "Runtime configuration"},
            "Maintenance": {"id": "Pemeliharaan", "en": "Maintenance"},
            "Results / Reports": {"id": "Hasil / Laporan", "en": "Results / Reports"},
            "Credit": {"id": "Credit", "en": "Credit"},
        }

    def _translate_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        lang = getattr(self, "_language", "id")
        pairs = self._translation_pairs()
        if text in pairs:
            return pairs[text].get(lang, text)
        # Translate known phrases inside longer labels such as window titles.
        # The old code did a plain `src in text` substring
        # match, so a short alphabetic source translated MID-WORD: e.g. the
        # "Guide"→"Panduan" pair turned an unmapped label "Guidelines" into the
        # garbled "Panduanlines", and "Update"/"Feature"/"Viewer" corrupted any
        # longer word that merely contained them. GUI labels never need a
        # mid-word replacement, so require a non-alphanumeric boundary on both
        # edges when the source itself is alphanumeric-edged (short whole
        # words). Sources whose edges are already non-alphanumeric — emoji/
        # punctuation-prefixed buttons, "…", "—", ":" — keep the exact previous
        # `in`/replace behavior, so window-title and icon-button translation is
        # byte-identical to before.
        for canonical, vals in pairs.items():
            source_vals = set(vals.values()) | {canonical}
            for src in source_vals:
                if not src:
                    continue
                if src[0].isalnum() and src[-1].isalnum():
                    _pat = r"(?<![0-9A-Za-z])" + re.escape(src) + r"(?![0-9A-Za-z])"
                    if re.search(_pat, text):
                        _repl = vals.get(lang, src)
                        return re.sub(_pat, lambda _m, _r=_repl: _r, text)
                elif src in text:
                    return text.replace(src, vals.get(lang, src))
        # Preserve icons/prefixes in tool-strip buttons.
        for canonical, vals in pairs.items():
            for v in vals.values():
                if text.endswith("  " + v):
                    return text[:-(len(v))] + vals.get(lang, v)
        return text

    def _translate_widget_tree(self, widget) -> None:
        try:
            text = widget.cget("text")
            new_text = self._translate_text(text)
            if new_text != text:
                widget.configure(text=new_text)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _translate_widget_tree (line 6657): %s", _ignored_exc)
        try:
            placeholder = widget.cget("placeholder_text")
            if placeholder == "(opsional)":
                widget.configure(placeholder_text="(optional)" if self._language == "en" else "(opsional)")
            elif placeholder == "(optional)":
                widget.configure(placeholder_text="(optional)" if self._language == "en" else "(opsional)")
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _translate_widget_tree (line 6665): %s", _ignored_exc)
        try:
            for child in widget.winfo_children():
                self._translate_widget_tree(child)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _translate_widget_tree (line 6670): %s", _ignored_exc)

    def _auto_sidebar_default_desc(self) -> str:
        pref = _normalize_auto_detect_output(_load_settings().get("auto_detect_output", "folder"))
        if getattr(self, "_language", "id") == "en":
            return "Default: ZIP output" if pref == "zip" else "Default: Folder output"
        return "Default: output ZIP" if pref == "zip" else "Default: output Folder"

    def _apply_language(self) -> None:
        """Apply Indonesian/English GUI text without rebuilding the UI."""
        try:
            if hasattr(self, "_lang_pill"):
                self._lang_pill.set("EN" if self._language == "en" else "ID")
            self._translate_widget_tree(self.root)
            if hasattr(self, "_dl_btn"):
                self._dl_btn.configure(text=self._tr("download_all"))
            if hasattr(self, "_browse_button"):
                self._browse_button.configure(text=self._tr("browse"))
            if hasattr(self, "_output_label"):
                self._output_label.configure(text=self._tr("output_folder"))
            if hasattr(self, "_import_button"):
                self._import_button.configure(text=self._tr("import_list"))
            if hasattr(self, "_export_button"):
                self._export_button.configure(text=self._tr("export_list"))
            if hasattr(self, "_add_btn"):
                self._add_btn.configure(text=self._tr("add_url"))
            # Re-apply sidebar mode names/descriptions from canonical definitions.
            mode_texts = {
                "auto": ("Auto (detect)", self._auto_sidebar_default_desc()),
                "embed": ("Embedded JSON", "Images embedded in JSON"),
                "zip": ("ZIP", "JSON + separate assets"),
                "both": ("Both", "Embed + ZIP together"),
                "website_zip": ("ICC ZIP", "Offline viewer archive"),
                "website_folder": ("ICC Folder", "Offline viewer folder"),
                "pure_website_zip": ("Pure Website ZIP", "Viewer only, no project scan"),
                "pure_website_folder": ("Pure Website Folder", "Viewer folder, no project scan"),
                "cyoap_vue_zip": ("cyoap_vue ZIP", "cyoap_vue engine archive"),
                "cyoap_vue_folder": ("cyoap_vue Folder", "cyoap_vue engine folder"),
            }
            for val, (_row, _bar, _icon, name_lbl, desc_lbl) in getattr(self, "_mode_btns", {}).items():
                if val in mode_texts:
                    n, d = mode_texts[val]
                    name_lbl.configure(text=self._translate_text(n))
                    desc_lbl.configure(text=self._translate_text(d))
            if hasattr(self, "_sec_labels"):
                for lbl in self._sec_labels:
                    try: lbl.configure(text=self._translate_text(lbl.cget("text")))
                    except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _apply_language (line 6716): %s", _ignored_exc)
            self._update_mode_info(getattr(self, "_mode_var", "auto"))
        except Exception as e:
            logger.debug(f"Language apply failed: {e}")

    # ════════════════════════════════════════════════════════════════
    # CLOUDFLARE / FLARESOLVERR PANEL
    # ════════════════════════════════════════════════════════════════
    def _cloudflare_panel(self) -> None:
        """Modern Cloudflare settings panel: Off/Auto/cloudscraper/FlareSolverr."""
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        st = _load_settings()

        win = self._make_singleton_window("cloudflare_panel_legacy")
        if win is None:
            return
        win.title("Cloudflare Access")
        win.geometry("640x560")
        win.minsize(600, 520)
        win.grab_set()

        root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="☁", width=38, height=38,
                     font=ctk.CTkFont("Segoe UI Emoji", 20),
                     fg_color=p["surface2"], text_color=p["accent"],
                     corner_radius=10).grid(row=0, column=0, padx=(16, 10), pady=14)
        ctk.CTkLabel(header, text="Cloudflare Access",
                     font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="w", pady=(12, 0))
        ctk.CTkLabel(header, text="Normal request → cloudscraper → FlareSolverr fallback",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 12))

        body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        root.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        def card(title: str, subtitle: str = ""):
            frame = ctk.CTkFrame(body, fg_color=p["panel"], border_color=p["border"], border_width=1, corner_radius=12)
            frame.pack(fill="x", pady=(0, 12))
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=p["fg"], anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 0))
            if subtitle:
                ctk.CTkLabel(frame, text=subtitle, font=ctk.CTkFont("Segoe UI", 10),
                             text_color=p["muted"], anchor="w", wraplength=560, justify="left").grid(
                    row=1, column=0, columnspan=3, sticky="ew", padx=14, pady=(1, 10))
            return frame

        access = card("Mode", "Auto is recommended. FlareSolverr is used only when the page really shows a Cloudflare challenge.")
        mode_var = ctk.StringVar(value=_display_cloudflare_mode(st.get("cloudflare_mode", _CLOUDFLARE_MODE)))
        ctk.CTkLabel(access, text="Cloudflare mode", text_color=p["muted"], anchor="w").grid(row=2, column=0, padx=14, pady=8, sticky="w")
        mode_menu = ctk.CTkOptionMenu(access, variable=mode_var,
                                      values=["Off", "Auto", "cloudscraper", "FlareSolverr"],
                                      width=180, fg_color=p["surface2"], button_color=p["surface"],
                                      text_color=p["fg"], dropdown_fg_color=p["surface"],
                                      dropdown_text_color=p["fg"])
        mode_menu.grid(row=2, column=1, sticky="w", padx=8, pady=8)

        fs = card("FlareSolverr", "Run flaresolverr.exe or Docker first, then use the API endpoint below. Default: http://localhost:8191/v1")
        url_var = ctk.StringVar(value=st.get("flaresolverr_url", _FLARESOLVERR_URL))
        sess_var = ctk.StringVar(value=st.get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY))
        timeout_var = ctk.StringVar(value=str(st.get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT)))
        wait_var = ctk.StringVar(value=str(st.get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER)))
        proxy_var = ctk.StringVar(value=st.get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE))

        def label(row, text):
            ctk.CTkLabel(fs, text=text, text_color=p["muted"], anchor="w").grid(row=row, column=0, padx=14, pady=6, sticky="w")
        label(2, "API URL")
        ctk.CTkEntry(fs, textvariable=url_var, height=32, fg_color=p["input_bg"], border_color=p["border"],
                     text_color=p["input_fg"], font=ctk.CTkFont("Consolas", 10)).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 14), pady=6)
        label(3, "Session")
        ctk.CTkOptionMenu(fs, variable=sess_var, values=["temporary", "reuse-domain", "manual"],
                          width=150, fg_color=p["surface2"], button_color=p["surface"], text_color=p["fg"]).grid(row=3, column=1, sticky="w", padx=8, pady=6)
        label(4, "Timeout")
        ctk.CTkEntry(fs, textvariable=timeout_var, width=80, height=30, justify="center",
                     fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"]).grid(row=4, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(fs, text="seconds", text_color=p["muted"]).grid(row=4, column=1, sticky="w", padx=(95, 0), pady=6)
        label(5, "Wait after solve")
        ctk.CTkEntry(fs, textvariable=wait_var, width=80, height=30, justify="center",
                     fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"]).grid(row=5, column=1, sticky="w", padx=8, pady=6)
        ctk.CTkLabel(fs, text="seconds", text_color=p["muted"]).grid(row=5, column=1, sticky="w", padx=(95, 0), pady=6)
        label(6, "Proxy")
        ctk.CTkOptionMenu(fs, variable=proxy_var, values=["inherit", "none"],
                          width=150, fg_color=p["surface2"], button_color=p["surface"], text_color=p["fg"]).grid(row=6, column=1, sticky="w", padx=8, pady=6)

        status_var = ctk.StringVar(value="Status: not tested")
        status = ctk.CTkLabel(fs, textvariable=status_var, text_color=p["muted"], anchor="w")
        status.grid(row=7, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 2))

        def apply_settings(persist=True):
            try:
                timeout_s = int(timeout_var.get() or 60)
            except Exception:
                timeout_s = 60
            try:
                wait_s = int(wait_var.get() or 3)
            except Exception:
                wait_s = 3
            _set_cloudflare_config(
                mode_var.get(),
                flaresolverr_url=url_var.get(),
                session_policy=sess_var.get(),
                timeout=timeout_s,
                wait_after=wait_s,
                proxy_mode=proxy_var.get(),
                persist=persist,
            )
            try:
                self._cf_mode_var.set(_display_cloudflare_mode(_CLOUDFLARE_MODE))
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in apply_settings (line 6835): %s", _ignored_exc)

        def do_test():
            apply_settings(True)
            status_var.set("Status: testing FlareSolverr…")
            def worker():
                ok, msg = flaresolverr_test_connection()
                win.after(0, lambda: status_var.set(("Status: ✓ " if ok else "Status: ✗ ") + msg))
            threading.Thread(target=worker, daemon=True).start()

        def do_clear():
            apply_settings(True)
            def worker():
                n = flaresolverr_destroy_sessions()
                win.after(0, lambda: status_var.set(f"Status: cleared {n} session(s)"))
            threading.Thread(target=worker, daemon=True).start()

        actions = ctk.CTkFrame(fs, fg_color="transparent")
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 14))
        ctk.CTkButton(actions, text="Test Connection", fg_color="#3b82f6", hover_color="#2563eb",
                      command=do_test).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Clear Sessions", fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], command=do_clear).pack(side="left")

        info = card("Recommended setup", "Windows: run flaresolverr.exe, keep the terminal open, then test http://localhost:8191/v1 here. Use Auto mode for normal downloads.")
        ctk.CTkLabel(info, text="Recommended defaults: Auto · reuse-domain · timeout 60s · proxy inherit",
                     font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=p["accent"], anchor="w").grid(
            row=2, column=0, sticky="ew", padx=14, pady=(0, 14))

        footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        def save_close():
            apply_settings(True)
            messagebox.showinfo("Cloudflare", f"Saved: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
            win.destroy()
        ctk.CTkButton(footer, text="Save", width=90, fg_color="#3b82f6", hover_color="#2563eb",
                      command=save_close).grid(row=0, column=1, padx=(6, 8), pady=12)
        ctk.CTkButton(footer, text="Close", width=90, fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], command=win.destroy).grid(row=0, column=2, padx=(0, 14), pady=12)

    # ════════════════════════════════════════════════════════════════
    # QUEUE
    # ════════════════════════════════════════════════════════════════
    def _badge_colors(self, mode: str) -> tuple:
        return self.BADGE_COLORS.get(mode, ("#1e3a5f", "#60a5fa"))

    def _set_queue_item_mode(self, item_ref: dict, mode: str, badge) -> None:
        """Change one queued row's mode without rebuilding or removing the row."""
        if item_ref is None or not any(candidate is item_ref for candidate in self._queue_data):
            return
        mode = (mode or "auto").strip().lower().replace("-", "_").replace(" ", "_")
        if mode not in self.QUEUE_MODE_OPTIONS:
            return
        item_ref["mode"] = mode
        # A manual choice should no longer look like a result from auto-detect.
        item_ref.pop("auto_detected", None)
        item_ref.pop("auto_detected_mode", None)
        try:
            bg, fg = self._badge_colors(mode)
            badge.configure(text=mode.replace("_", " "), fg_color=bg, text_color=fg)
        except Exception as exc:
            logger.debug("Queue mode badge update skipped: %s", exc)

    def _show_queue_mode_menu(self, item_ref: dict, badge) -> None:
        """Show the canonical mode choices for a queue row's mode badge."""
        import tkinter as tk

        if item_ref is None or not any(candidate is item_ref for candidate in self._queue_data):
            return
        menu = tk.Menu(self.root, tearoff=False)
        for mode in self.QUEUE_MODE_OPTIONS:
            menu.add_command(
                label=mode.replace("_", " "),
                command=lambda selected=mode: self._set_queue_item_mode(
                    item_ref, selected, badge))
        try:
            menu.tk_popup(
                badge.winfo_rootx(),
                badge.winfo_rooty() + badge.winfo_height())
        finally:
            menu.grab_release()

    def _make_queue_row(self, url: str, mode: str, filename: str) -> None:
        import customtkinter as ctk
        import tkinter as tk
        idx = len(self._queue_rows)
        # Keep the editor bound to its item, not to its original list index.
        # Removing/reordering another row changes list indices and used to make
        # a filename edit land on a different CYOA during a batch run.
        item_ref = self._queue_data[idx] if idx < len(self._queue_data) else None
        p   = self._p()

        row = ctk.CTkFrame(self._qlist, corner_radius=6,
                           fg_color=p["surface"],
                           border_width=1, border_color=p["border"])
        row.pack(fill="x", padx=4, pady=3)
        row.grid_columnconfigure(2, weight=1)

        # ── Drag handle ─────────────────────────────────────────────
        drag_lbl = ctk.CTkLabel(row, text="⠿", width=18,
                                font=ctk.CTkFont("Segoe UI", 14),
                                text_color=p["muted2"],
                                cursor="fleur")
        drag_lbl.grid(row=0, column=0, padx=(6, 2), pady=8, rowspan=2)

        # Status dot
        dot = tk.Canvas(row, width=10, height=10,
                        highlightthickness=0, bg=p["surface"])
        dot.create_oval(2, 2, 8, 8, fill=p["muted2"], outline="")
        dot.grid(row=0, column=1, padx=(2, 6), pady=8)

        # URL + editable filename
        url_lbl = ctk.CTkLabel(row, text=url,
                               font=ctk.CTkFont("Consolas", 9),
                               text_color=p["muted"], anchor="w")
        url_lbl.grid(row=0, column=2, sticky="ew", padx=4, pady=(6, 1))

        fn_var = ctk.StringVar(value=filename)
        fn_entry = ctk.CTkEntry(
            row, textvariable=fn_var, height=22,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["input_bg"], text_color=p["input_fg"],
            border_color=p["border"], border_width=1,
            placeholder_text="auto",
        )
        fn_entry.grid(row=1, column=2, sticky="ew", padx=4, pady=(0, 6))

        def _on_fn_change(*_):
            if item_ref is not None and any(candidate is item_ref for candidate in self._queue_data):
                item_ref["filename"] = fn_var.get().strip()
        fn_var.trace_add("write", _on_fn_change)

        # Mode badge.  It stays compact like a label, but clicking it opens a
        # menu so the URL can switch mode in place without remove/re-add.
        bg, fg = self._badge_colors(mode)
        badge = ctk.CTkButton(row,
                              text=mode.replace("_", " "),
                              font=ctk.CTkFont("Segoe UI", 9, "bold"),
                              fg_color=bg, hover_color=bg,
                              text_color=fg, corner_radius=10,
                              width=112, height=24,
                              command=lambda ref=item_ref: self._show_queue_mode_menu(
                                  ref, badge))
        badge.grid(row=0, column=3, padx=6, rowspan=2)

        # × button
        rm = ctk.CTkButton(row, text="×", width=28, height=28,
                           font=ctk.CTkFont("Segoe UI", 13),
                           fg_color="transparent",
                           hover_color=p["surface2"],
                           text_color=p["muted"],
                           command=lambda i=idx: self._remove_row(i))
        rm.grid(row=0, column=4, padx=(0, 6), rowspan=2)

        self._queue_rows.append((row, dot, url_lbl, badge, rm))

        # ── Drag-to-reorder bindings ─────────────────────────────────
        self._bind_drag(drag_lbl, row)

        self._update_queue_count()

    def _bind_drag(self, handle, row_frame) -> None:
        """Attach drag-reorder behaviour to the ⠿ handle of a queue row."""
        state = {"start_y": 0, "dragging": False, "ghost": None}

        def _row_index(w) -> int:
            """Return current index of row_frame in _queue_rows."""
            for i, (r, *_) in enumerate(self._queue_rows):
                if r is w:
                    return i
            return -1

        def _on_press(e):
            state["start_y"] = e.y_root
            state["dragging"] = False
            row_frame.configure(border_color="#3b82f6")

        def _on_drag(e):
            state["dragging"] = True
            dy = e.y_root - state["start_y"]
            if abs(dy) < 8:
                return
            # Find which row we're hovering over
            my_idx = _row_index(row_frame)
            if my_idx < 0:
                return
            rows = self._queue_rows
            for j, (r, *_) in enumerate(rows):
                ry = r.winfo_rooty()
                rh = r.winfo_height()
                if r is not row_frame and ry <= e.y_root <= ry + rh:
                    # Swap
                    if j != my_idx:
                        self._swap_rows(my_idx, j)
                        state["start_y"] = e.y_root
                    break

        def _on_release(e):
            row_frame.configure(border_color=self._p()["border"])
            state["dragging"] = False

        handle.bind("<ButtonPress-1>", _on_press)
        handle.bind("<B1-Motion>",     _on_drag)
        handle.bind("<ButtonRelease-1>", _on_release)

    def _swap_rows(self, i: int, j: int) -> None:
        """Swap two queue rows (data + visual)."""
        if i < 0 or j < 0 or i >= len(self._queue_rows) or j >= len(self._queue_rows):
            return
        # Swap data
        self._queue_data[i], self._queue_data[j] = \
            self._queue_data[j], self._queue_data[i]
        # Swap visual: re-pack in new order
        row_i, *_ = self._queue_rows[i]
        row_j, *_ = self._queue_rows[j]
        # Forget pack info then re-pack in swapped order
        all_rows = [(r, *rest) for r, *rest in self._queue_rows]
        all_rows[i], all_rows[j] = all_rows[j], all_rows[i]
        self._queue_rows = all_rows
        # Re-pack all rows in correct order
        for r, *_ in self._queue_rows:
            r.pack_forget()
        for r, *_ in self._queue_rows:
            r.pack(fill="x", padx=4, pady=3)
        # Update × button commands
        for k, (*_, rm) in enumerate(self._queue_rows):
            rm.configure(command=lambda k=k: self._remove_row(k))

    def _remove_row(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._queue_rows):
            return
        self._queue_rows[idx][0].destroy()
        self._queue_rows.pop(idx)
        del self._queue_data[idx]
        for i, (_, _, _, _, rm) in enumerate(self._queue_rows):
            rm.configure(command=lambda i=i: self._remove_row(i))
        self._update_queue_count()

    def _update_queue_count(self) -> None:
        n = len(self._queue_rows)
        self._queue_count_var.set(f"QUEUE — {n} ITEM{'S' if n != 1 else ''}")

    def _add_to_queue(self) -> None:
        url = self._url_var.get().strip()
        if not url:
            return
        # Duplicate URLs are valid separate jobs.  The worker uses each row's
        # _queue_id for resume/removal bookkeeping, so users can intentionally
        # download the same CYOA twice with different output identities.
        # History: auto-suffix filename if previously downloaded
        prev = _check_history(url)
        if prev:
            date  = prev.get("last_downloaded", "")[:10]
            fname = prev.get("file_name", "")
            logger.info(
                f"⚠ URL pernah didownload ({date})"
                + (f" → {fname}" if fname else "")
                + " — filename diberi suffix _N"
            )
        fn   = self._fn_var.get().strip()
        # Auto-suffix: if URL was previously downloaded, append _1, _2, ...
        if prev and not fn:
            # Generate suffix-ed filename based on previous filename
            base_fn = prev.get("file_name", "") or ""
            if base_fn:
                # strip existing _N suffix from prev name
                base_fn = re.sub(r'_\d+$', '', base_fn)
                suffix = 1
                fn = f"{base_fn}_{suffix}"
                # increment until unique
                existing = {it.get("filename","") for it in self._queue_data}
                while fn in existing:
                    suffix += 1
                    fn = f"{base_fn}_{suffix}"
        mode = self._mode_var
        self._queue_data.append({
            "url": url,
            "filename": fn,
            "mode": mode,
            "_queue_id": uuid.uuid4().hex,
        })
        self._make_queue_row(url, mode, fn)
        self._url_var.set("")
        self._fn_var.set("")

    def _remove(self) -> None:
        if self._queue_rows:
            self._remove_row(len(self._queue_rows) - 1)

    def _clear_queue(self) -> None:
        for rw in self._queue_rows:
            try:
                if rw and rw[0].winfo_exists():
                    rw[0].destroy()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _clear_queue (line 7084): %s", _ignored_exc)
        self._queue_rows.clear()
        self._queue_data.clear()
        self._update_queue_count()

    # ════════════════════════════════════════════════════════════════
    # LOG
    # ════════════════════════════════════════════════════════════════
    def _setup_logging(self) -> None:
        # Remove existing GUILogHandler to prevent duplicate entries on re-init
        for h in logger.handlers[:]:
            if isinstance(h, GUILogHandler):
                logger.removeHandler(h)
        h = GUILogHandler(self._log_queue)
        h.setFormatter(_formatter)
        logger.addHandler(h)

    def _poll_log(self) -> None:
        # v7.5.6 perf fix: drain the queue in one batch per tick instead of
        # configure/insert/see per line. Heavy downloads can emit thousands
        # of lines; per-line widget churn froze the GUI for seconds. Also
        # caps the batch (keeps the UI responsive under log floods) and trims
        # the widget so multi-hour batch sessions don't grow memory unbounded.
        try:
            if not self.root.winfo_exists() or not self._log_txt.winfo_exists():
                return
        except Exception:
            return
        batch = []
        try:
            for _ in range(400):                      # max lines per tick
                batch.append(self._log_queue.get_nowait())
        except log_queue_module.Empty as _ignored_exc:
            _ = _ignored_exc  # expected non-blocking queue control flow
        if batch:
            self._log_txt.configure(state="normal")
            for msg in batch:
                if " - WARNING - " in msg:   tag = "WARNING"
                elif " - ERROR - " in msg:   tag = "ERROR"
                elif any(w in msg for w in ("successful", "complete", "Done")):
                    tag = "SUCCESS"
                elif "[Auto-detect]" in msg:  tag = "AUTO"
                else:                         tag = "INFO"
                self._log_txt.insert("end", msg + "\n", tag)
            # Trim to the last ~4000 lines
            try:
                line_count = int(self._log_txt.index("end-1c").split(".")[0])
                if line_count > 4500:
                    self._log_txt.delete("1.0", f"{line_count - 4000}.0")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _poll_log (line 7134): %s", _ignored_exc)
            self._log_txt.see("end")
            self._log_txt.configure(state="disabled")
        try:
            if self.root.winfo_exists():
                self.root.after(100, self._poll_log)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _poll_log (line 7141): %s", _ignored_exc)

    def _clear_log(self) -> None:
        self._log_txt.configure(state="normal")
        self._log_txt.delete("1.0", "end")
        self._log_txt.configure(state="disabled")

    # ════════════════════════════════════════════════════════════════
    # ACTIONS
    # ════════════════════════════════════════════════════════════════
    def _browse(self) -> None:
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self._outdir_var.get())
        if d:
            self._outdir_var.set(d)

    def _browse_ytdlp_cookies(self) -> None:
        """Choose an exported Netscape cookies.txt for YouTube audio."""
        from tkinter import filedialog
        current = self._ytdlp_cookies_var.get().strip()
        initialdir = os.path.dirname(current) if current else os.path.expanduser("~/Downloads")
        if not os.path.isdir(initialdir):
            initialdir = os.getcwd()
        path = filedialog.askopenfilename(
            title=("Select YouTube cookies.txt" if self._language == "en" else "Pilih cookies.txt YouTube"),
            initialdir=initialdir,
            filetypes=[
                ("Netscape cookies", "*.txt"),
                ("Cookie files", "*.txt;*.cookies"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._ytdlp_cookies_var.set(os.path.abspath(path))

    def _save_ytdlp_cookie_setting(self, show_error: bool = True) -> bool:
        """Validate the selected file and persist only its path."""
        from tkinter import messagebox
        raw = self._ytdlp_cookies_var.get().strip()
        if not raw:
            _update_setting("ytdlp_cookies", "")
            os.environ.pop("CYOA_YTDLP_COOKIES", None)
            return True
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isfile(path):
            if show_error:
                messagebox.showerror(
                    "YouTube cookies",
                    (f"Cookie file tidak ditemukan:\n{path}\n\nPilih file Netscape cookies.txt yang valid."
                     if self._language != "en" else
                     f"Cookie file was not found:\n{path}\n\nChoose a valid Netscape cookies.txt file."),
                )
            return False
        self._ytdlp_cookies_var.set(path)
        _update_setting("ytdlp_cookies", path)
        os.environ["CYOA_YTDLP_COOKIES"] = path
        logger.info("yt-dlp: GUI cookie path saved")
        return True

    def _clear_ytdlp_cookies(self) -> None:
        """Return YouTube audio authentication to automatic browser cookies."""
        self._ytdlp_cookies_var.set("")
        _update_setting("ytdlp_cookies", "")
        os.environ.pop("CYOA_YTDLP_COOKIES", None)

    def _prepare_ytdlp_cookies(self) -> bool:
        """Validate and activate the GUI-selected cookie path for this run."""
        return self._save_ytdlp_cookie_setting(show_error=True)

    def _open_path_in_os(self, path: str) -> None:
        """Open a file/folder with the platform default handler."""
        import subprocess, platform
        from tkinter import messagebox
        if not path or not os.path.exists(path):
            messagebox.showwarning("Open Path", f"Path not found:\n{path}")
            return
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys_name == "Darwin":
                subprocess.Popen(["open", path], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", path], close_fds=True)
        except Exception as e:
            messagebox.showerror("Open Path", str(e))

    def _open_text_file_for_editing(self, path: str) -> None:
        """Open a text file in an editor so the user can edit and save it.

        On Windows, relying on os.startfile() for .json can raise WinError 1155
        when no default app is associated. Notepad is available on normal
        Windows installs, so settings.json opens directly as an editable file.
        """
        import platform, shlex, subprocess
        from tkinter import messagebox
        if not path or not os.path.exists(path):
            messagebox.showwarning("Open settings.json", f"File not found:\n{path}")
            return
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                subprocess.Popen(["notepad.exe", path], close_fds=True)
                return
            if sys_name == "Darwin":
                subprocess.Popen(["open", "-e", path], close_fds=True)  # TextEdit, editable
                return

            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
            if editor:
                subprocess.Popen(shlex.split(editor) + [path], close_fds=True)
                return

            for cmd in ("xdg-open", "sensible-editor", "gedit", "kate", "xed", "mousepad"):
                exe = shutil.which(cmd)
                if exe:
                    subprocess.Popen([exe, path], close_fds=True)
                    return

            messagebox.showinfo(
                "Open settings.json",
                "settings.json sudah dibuat, tetapi editor tidak ditemukan.\n"
                "Buka file ini dengan text editor apa pun, lalu Save setelah edit:\n\n"
                f"{path}")
        except Exception as e:
            messagebox.showerror("Open settings.json", str(e))

    def _open_settings_json(self) -> None:
        """Open the active settings.json in an editable text editor."""
        from tkinter import messagebox
        try:
            os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
            if not os.path.exists(_SETTINGS_FILE):
                # Create a minimal default file instead of failing silently.
                # This does not export/redact; it simply materializes the normal
                # app settings location so the user can inspect/edit it.
                _save_settings(dict(_SETTINGS_DEFAULTS))
            self._open_text_file_for_editing(_SETTINGS_FILE)
        except Exception as e:
            messagebox.showerror("Open settings.json", str(e))

    def _open_settings_folder(self) -> None:
        """Open the folder that stores settings.json and download_history.json."""
        from tkinter import messagebox
        try:
            folder = os.path.dirname(_SETTINGS_FILE)
            os.makedirs(folder, exist_ok=True)
            self._open_path_in_os(folder)
        except Exception as e:
            messagebox.showerror("Open settings folder", str(e))

    def _gallery_dl_default_config_path(self) -> str:
        """Return gallery-dl's conventional user config path for this OS."""
        import platform
        if platform.system() == "Windows":
            base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
            return os.path.join(base, "gallery-dl", "config.json")
        return os.path.join(os.path.expanduser("~"), ".config", "gallery-dl", "config.json")

    def _open_gallery_dl_config(self) -> None:
        """Create/open gallery-dl config.json in an editable text editor."""
        from tkinter import messagebox
        try:
            s = _load_settings()
            cfg = str(s.get("gallery_dl_config", "") or "").strip()
            if not cfg:
                cfg = self._gallery_dl_default_config_path()
                _update_setting("gallery_dl_config", cfg)
            cfg = os.path.abspath(os.path.expanduser(os.path.expandvars(cfg)))
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            if not os.path.exists(cfg):
                pathlib.Path(cfg).write_text(
                    '{\n  "extractor": {},\n  "downloader": {}\n}\n',
                    encoding="utf-8",
                )
            _set_gallery_dl_mode(
                str(s.get("gallery_dl_mode", "off") or "off"),
                path=str(s.get("gallery_dl_path", "gallery-dl") or "gallery-dl"),
                config=cfg,
                persist=True,
            )
            self._open_text_file_for_editing(cfg)
        except Exception as e:
            messagebox.showerror("Open gallery-dl config", str(e))

    def _open_folder(self) -> None:
        folder = self._outdir_var.get()
        if not os.path.isdir(folder):
            return
        self._open_path_in_os(folder)

    def _import_list(self) -> None:
        from tkinter import filedialog, messagebox
        path = filedialog.askopenfilename(
            filetypes=[("Supported", "*.txt *.csv *.xlsx *.xls"),
                       ("All files", "*.*")])
        if not path:
            return
        items = import_queue_items_from_source(path)
        if not items:
            messagebox.showwarning("Import Failed", "No valid URLs found.")
            return
        default_mode = self._mode_var
        for item in items:
            mode = item.get("mode", "").strip() or default_mode
            item["mode"] = mode
            self._queue_data.append({"url": item["url"],
                                     "filename": item.get("filename", ""),
                                     "mode": mode,
                                     "_queue_id": uuid.uuid4().hex})
            self._make_queue_row(item["url"], mode, item.get("filename", ""))
        logger.info(f"Imported {len(items)} item(s) from {path}")

    def _export_list(self) -> None:
        """Export the current queue, including each row's filename and mode."""
        from tkinter import filedialog, messagebox
        from ..importers.batch import export_queue_items_to_file

        if not self._queue_data:
            messagebox.showwarning(
                "Export List",
                "Queue is empty. Add at least one URL before exporting.",
            )
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="cyoa_queue.csv",
            filetypes=[
                ("CSV list", "*.csv"),
                ("Text list", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            count = export_queue_items_to_file(self._queue_data, path)
        except Exception as exc:
            logger.exception("Queue export failed")
            messagebox.showerror("Export Failed", str(exc))
            return
        logger.info("Exported %s queue item(s) to %s", count, path)
        messagebox.showinfo("Export Complete", f"Exported {count} item(s).")

    def _show_format_guide(self) -> None:
        """Show the small '?' help window beside Import List.

        v7.4.3 expands this from a batch-format-only note into a bilingual
        quick help panel covering workflow, import format, diagnostics,
        current bugfix behavior, operational guidelines, and troubleshooting.
        """
        # v1.0 Release stabilization patch v11: the old small Help popup and
        # the separate Feature Guide are intentionally unified. Keep this
        # method as a backward-compatible entry point for any older handler.
        return self._show_feature_guide("setup")

        import customtkinter as ctk
        import tkinter as tk

        lang = getattr(self, "_language", "id")
        is_en = (lang == "en")
        title = "Help, Setup & Import Guide" if is_en else "Bantuan, Setup & Panduan Import"

        win = self._make_singleton_window("format_guide")
        if win is None:
            return
        win.title(title)
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(980, max(860, sw - 120))
            h = min(760, max(620, sh - 120))
            x = max(20, (sw - w) // 2)
            y = max(20, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("940x720")
        win.minsize(820, 600)
        win.resizable(True, True)
        try:
            win.transient(self.root)
            win.grab_set()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _show_format_guide (line 7338): %s", _ignored_exc)

        p = self._p()
        outer = ctk.CTkFrame(win, fg_color=p["bg"])
        outer.pack(fill="both", expand=True)

        header = ctk.CTkFrame(outer, fg_color=p["surface"], corner_radius=12)
        header.pack(fill="x", padx=14, pady=(14, 8))
        ctk.CTkLabel(
            header,
            text=(f"❔  {title}"),
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
            text_color=p["fg"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            header,
            text=(
                f"CYOA Downloader v{_APP_VERSION} — feature guide, batch format, diagnostics, and safe-use notes"
                if is_en else
                f"CYOA Downloader v{_APP_VERSION} — panduan fitur, format batch, diagnostik, dan catatan penggunaan aman"
            ),
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=p["muted"],
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=14, pady=(0, 12))

        if is_en:
            content = f"""
CYOA DOWNLOADER v{_APP_VERSION} — HELP / SETUP / IMPORT
============================================================

0) Current toolbar map
----------------------
• Top row: Download All, Preview, Pause/Continue, Start/Stop Serve, Folder. Status/progress stays on its own line so controls never slide away.
• Download: feature toggles, including deep scan, Selenium fallback, serve preview, cheat panel, itch.io, and gallery-dl fallback.
• Retry Assets / Retry Images / Retry Audio: direct recovery buttons that do not open extra menus.
• Batch Check: runs update checks without opening an extra menu.
• Settings: settings.json, settings folder, export/import settings, AI Assist, gallery-dl config, Cloudflare/FlareSolverr, Offline Viewers, cache, updates.
• Logs / Diagnostics: results, diagnostics, and the feature guide.
• CYOA Manager: import, export, and integration settings in one place.

1) Recommended workflow
-----------------------
• Paste one or more CYOA URLs into the queue.
• Choose an output mode. For most users, ICC Folder is the safest default.
• Use Preview before large batches to check whether project data is detectable.
• Use Serve after download to open ICC-folder output through localhost.
• Check backup_report.txt, failed_assets.txt, failed_images.txt, and cyoa_downloader.log when something is missing.

2) Batch import format
----------------------
Excel / CSV column names are case-insensitive:

  url       required   Full URL starting with http:// or https://
  filename  optional   Output filename without extension
  mode      optional   embed | zip | both | website_zip | website_folder |
                       pure_website_zip | pure_website_folder |
                       cyoap_vue_zip | cyoap_vue_folder | auto

TXT format:

  https://example.com/cyoa/
  https://example.com/cyoa2/ | MyFilename
  https://example.com/cyoa3/ | Name | website_zip

Rows without a valid URL are skipped. If mode is empty, the current GUI mode is used.

3) Output mode guide
--------------------
• ICC Folder: downloads viewer files and assets into a browser-openable folder.
• ICC ZIP: same as ICC Folder, then compresses and removes the folder.
• Embedded JSON: stores images as base64 inside a single JSON file; can become very large.
• ZIP: stores project.json plus local images/audio as separate files.
• Both: creates Embedded JSON and ZIP together.
• Pure Website: downloads the site without trying to resolve project.json first.
• cyoap_vue: dedicated flow for dist/platform.json + dist/nodes/list.json projects.

4) Diagnostics and safety tools
-------------------------------
• CLI dependency check:  python cyoa_downloader.py --dependency-check
• CLI self-test:          python cyoa_downloader.py --self-test
• Verify a finished backup (read-only):  python cyoa_downloader.py --verify "OUTPUT_FOLDER"
• Optional checksum baseline (run once):  python cyoa_downloader.py --verify "OUTPUT_FOLDER" --write-manifest
• Failed assets: appended to backup_report.txt when available; otherwise failed_assets.txt.
• 429 rate-limit: handled as rate-limit/backoff, not automatically as Cloudflare.
• TLS certificates are always verified; invalid certificates fail safely instead of using an insecure fallback.

5) Serve Developer Tools / local override helpers
------------------------------------
• Serve now opens the preview with a small local Tools overlay.
• Tools are local-only: clear storage/cache, export/import localStorage and IndexedDB, hard reload, open reports, inspect Svelte/debugApp, and temporarily reveal/enable disabled UI controls for offline testing.
• The helpers are intended for debugging downloaded CYOAs, not for attacking live websites.
• Serve Tools now appears automatically in served HTML preview, pinned to the top-right with a forced z-index and visible button controls. Use ?no_tools=1 for a clean preview.
• Manual routes: /__serve_tools__ opens the tools page; /__clear_cache__ clears browser preview state.
• Userscript Lab can optionally load IntCyoaEnhancer from a local .user.js file or from GreasyFork for localhost preview only. Use ?load_ice=local or ?load_ice=web to auto-load it in the preview page.
• Credits are shown in the GUI: source inspirations, viewer/helper projects, optional backends, community acknowledgements, and AI assistance are listed in the Credits panel.
• Native Bridge exposes $serveTools in the browser console for local inspection when the external userscript is unavailable.

6) ICC Plus compatibility notes
--------------------------------
• ICC Plus/Svelte projects can use viewerConfig, googleFonts/customFonts/customCSS, loadingBgImage, favicon, border images, backpack images, and design-group images.
• Serve Developer Tools can export/import IndexedDB because ICC Plus build saves are commonly stored in cyoaPlusDB/buildStore.
• Normal Serve preview no longer clears storage automatically; use /__clear_cache__ only when you intentionally want a clean preview.

7) Operational guidelines
--------------------------------
• Avoid destructive folder operations. Merge assets instead of deleting existing images/audio folders.
• Use unified fetch_response() for network requests so proxy, Cloudflare, SSL, DNS, and logs stay consistent.
• CLI should not persist GUI/network settings unless the matching CLI flag was explicitly supplied.
• Keep documentation, handoff, GUI help, and translation strings synchronized in every release.
• Treat real-site tests separately from offline smoke tests; document what was and was not live-tested.

8) Troubleshooting
------------------
• Missing images: open failed_images.txt and use Retry Images.
• Broken ICC folder: use Serve instead of opening index.html directly.
• Cloudflare page: set Cloudflare Mode to Auto or FlareSolverr, then retry.
• YouTube/audio failure: install yt-dlp, then open Settings / Maintenance → YouTube cookies, choose a Netscape cookies.txt, click Save, and use Retry YT Audio.
• XLS/XLSX import failure: install pandas, xlrd, and openpyxl.
• Slow/broken network: reduce threads, increase retry seconds, or set proxy/DNS.
""".strip()
            close_text = "Close"
            copy_text = "Copy help text"
            copied_text = "Copied"
        else:
            content = f"""
CYOA DOWNLOADER v{_APP_VERSION} — BANTUAN / SETUP / IMPORT
============================================================

0) Peta toolbar terbaru
-----------------------
• Row atas: Download All, Preview, Pause/Continue, Start/Stop Serve, Folder. Status/progress berada di baris sendiri supaya tombol tidak bergeser/hilang.
• Download: feature toggles, termasuk deep scan, fallback Selenium, serve preview, cheat panel, itch.io, dan gallery-dl fallback.
• Retry Assets / Retry Images / Retry Audio: tombol recovery langsung tanpa membuka menu tambahan.
• Batch Check: menjalankan update check tanpa membuka menu tambahan.
• Settings: settings.json, folder settings, export/import settings, AI Assist, config gallery-dl, Cloudflare/FlareSolverr, Offline Viewers, cache, updates.
• Logs / Diagnostics: results, diagnostics, dan feature guide.
• CYOA Manager: import, export, dan integration settings dalam satu tempat.

1) Alur kerja yang disarankan
-----------------------------
• Masukkan satu atau beberapa URL CYOA ke queue.
• Pilih mode output. Untuk mayoritas pengguna, ICC Folder adalah pilihan paling aman.
• Gunakan Preview sebelum batch besar untuk mengecek apakah data project kemungkinan terdeteksi.
• Gunakan Serve setelah download agar output ICC-folder dibuka melalui localhost.
• Jika ada aset hilang, cek backup_report.txt, failed_assets.txt, failed_images.txt, dan cyoa_downloader.log.

2) Format import batch
----------------------
Nama kolom Excel / CSV tidak sensitif huruf besar-kecil:

  url       wajib      URL penuh yang diawali http:// atau https://
  filename  opsional   Nama file output tanpa ekstensi
  mode      opsional   embed | zip | both | website_zip | website_folder |
                       pure_website_zip | pure_website_folder |
                       cyoap_vue_zip | cyoap_vue_folder | auto

Format TXT:

  https://example.com/cyoa/
  https://example.com/cyoa2/ | NamaFile
  https://example.com/cyoa3/ | Nama | website_zip

Baris tanpa URL valid akan dilewati. Jika mode kosong, program memakai mode yang sedang dipilih di GUI.

3) Panduan mode output
----------------------
• ICC Folder: mengunduh viewer dan aset menjadi folder yang bisa dibuka di browser.
• ICC ZIP: sama seperti ICC Folder, lalu dikompresi dan foldernya dihapus.
• Embedded JSON: gambar dimasukkan sebagai base64 dalam satu JSON; ukuran file bisa sangat besar.
• ZIP: menyimpan project.json bersama gambar/audio lokal sebagai file terpisah.
• Both: membuat Embedded JSON dan ZIP sekaligus.
• Pure Website: mengunduh situs tanpa mencari project.json terlebih dahulu.
• cyoap_vue: flow khusus untuk project dist/platform.json + dist/nodes/list.json.

4) Diagnostik dan alat keamanan
-------------------------------
• Cek dependency CLI:  python cyoa_downloader.py --dependency-check
• Self-test CLI:       python cyoa_downloader.py --self-test
• Verifikasi backup yang sudah selesai (read-only):  python cyoa_downloader.py --verify "FOLDER_OUTPUT"
• Baseline checksum opsional (jalankan sekali):  python cyoa_downloader.py --verify "FOLDER_OUTPUT" --write-manifest
• Aset gagal: ditambahkan ke backup_report.txt jika ada; jika tidak, ditulis ke failed_assets.txt.
• 429 rate-limit: diperlakukan sebagai rate-limit/backoff, bukan otomatis sebagai Cloudflare.
• Sertifikat TLS selalu diverifikasi; sertifikat invalid gagal secara aman tanpa fallback tidak aman.

5) Serve Developer Tools / helper override lokal
----------------------------------
• Serve sekarang membuka preview dengan overlay Tools kecil.
• Tools bersifat lokal: clear storage/cache, export/import localStorage dan IndexedDB, hard reload, buka report, inspeksi Svelte/debugApp, dan sementara menampilkan/mengaktifkan kontrol UI yang tersembunyi/disabled untuk testing offline.
• Helper ini ditujukan untuk debugging CYOA hasil download, bukan untuk menyerang website live.
• Serve Tools sekarang muncul otomatis di preview HTML lokal, dipaksa berada di kanan atas dengan z-index maksimum dan kontrol tombol yang terlihat. Gunakan ?no_tools=1 untuk preview bersih.
• Route manual: /__serve_tools__ membuka halaman tools; /__clear_cache__ membersihkan state preview browser.
• Userscript Lab dapat memuat IntCyoaEnhancer dari file .user.js lokal atau dari GreasyFork khusus untuk preview localhost. Gunakan ?load_ice=local atau ?load_ice=web untuk auto-load di halaman preview.
• Kredit ditampilkan di GUI: inspirasi sumber, proyek viewer/helper, backend opsional, komunitas, dan bantuan AI dicantumkan di panel Credits.
• Native Bridge menyediakan $serveTools di browser console untuk inspeksi lokal saat userscript eksternal tidak tersedia.

6) Catatan kompatibilitas ICC Plus
----------------------------------
• Project ICC Plus/Svelte dapat memakai viewerConfig, googleFonts/customFonts/customCSS, loadingBgImage, favicon, gambar border, gambar backpack, dan gambar design-group.
• Serve Developer Tools dapat export/import IndexedDB karena save build ICC Plus umumnya disimpan di cyoaPlusDB/buildStore.
• Preview Serve normal tidak lagi menghapus storage secara otomatis; gunakan /__clear_cache__ hanya jika Anda memang ingin preview bersih.

7) Guideline operasional
------------------------
• Hindari operasi folder destruktif. Gabungkan aset, jangan hapus folder images/audio yang sudah ada.
• Gunakan fetch_response() untuk request jaringan agar proxy, Cloudflare, SSL, DNS, dan log tetap konsisten.
• CLI tidak boleh menyimpan setting GUI/network kecuali flag terkait memang diberikan secara eksplisit.
• Dokumentasi, handoff, bantuan GUI, dan string translasi harus sinkron pada setiap rilis.
• Pisahkan test situs nyata dari smoke test offline; tulis jelas bagian yang sudah dan belum live-tested.

8) Troubleshooting
------------------
• Gambar hilang: buka failed_images.txt lalu gunakan Retry Images.
• Folder website bermasalah: buka melalui Serve, jangan langsung double-click index.html.
• Halaman Cloudflare: pilih Cloudflare Mode Auto atau FlareSolverr, lalu ulangi.
• YouTube/audio gagal: instal yt-dlp, lalu buka Settings / Maintenance → Cookie YouTube, pilih cookies.txt format Netscape, klik Simpan, lalu gunakan Ulang Audio YT.
• Import XLS/XLSX gagal: instal pandas, xlrd, dan openpyxl.
• Jaringan lambat/sering gagal: kurangi thread, naikkan retry seconds, atau atur proxy/DNS.
""".strip()
            close_text = "Tutup"
            copy_text = "Salin teks panduan"
            copied_text = "Tersalin"

        body_frame = ctk.CTkFrame(outer, fg_color=p["surface"], corner_radius=12)
        body_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        text_wrap = ctk.CTkFrame(body_frame, fg_color="transparent")
        text_wrap.pack(fill="both", expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(text_wrap)
        scrollbar.pack(side="right", fill="y")
        txt = tk.Text(
            text_wrap,
            font=("Consolas", 10),
            bg="#0a0d13",
            fg="#cbd5e1",
            insertbackground="#cbd5e1",
            selectbackground="#334155",
            relief="flat",
            padx=14,
            pady=12,
            wrap="word",
            yscrollcommand=scrollbar.set,
        )
        txt.insert("1.0", content)
        txt.configure(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=txt.yview)

        btns = ctk.CTkFrame(outer, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 14))

        def _copy_help() -> None:
            try:
                win.clipboard_clear()
                win.clipboard_append(content)
                copy_btn.configure(text=copied_text)
                _v25_safe_after_widget(win, copy_btn,
                                       lambda: copy_btn.configure(text=copy_text), delay=1200)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _copy_help (line 7588): %s", _ignored_exc)

        copy_btn = ctk.CTkButton(btns, text=copy_text, width=150, command=_copy_help)
        copy_btn.pack(side="left")
        ctk.CTkButton(btns, text=close_text, width=90, command=win.destroy).pack(side="right")

    def _start_base(self) -> None:
        from tkinter import messagebox
        if self._is_running:
            return
        if not self._queue_data:
            messagebox.showwarning(self._tr("queue_empty_title"), self._tr("queue_empty_body"))
            return
        # ── v7.5.6 fix: parse numeric fields and validate the output folder
        # BEFORE flipping _is_running / disabling the button / starting the
        # progress bar. Previously a non-numeric Wait/Threads/Bandwidth value
        # raised ValueError mid-setup and left the GUI permanently locked
        # (button disabled, spinner running, _is_running stuck True).
        def _safe_int(raw, default):
            try:
                return int(str(raw).strip() or default)
            except (ValueError, TypeError):
                return int(default)

        def _safe_float(raw, default):
            try:
                return float(str(raw).strip() or default)
            except (ValueError, TypeError):
                return float(default)

        wt      = _safe_int(self._wait_var.get(), DEFAULT_WAIT_TIME)
        threads = max(1, _safe_int(self._threads_var.get(), DEFAULT_MAX_WORKERS))
        bw      = max(0.0, _safe_float(self._bw_var.get(), 0))

        outdir = self._outdir_var.get()
        if outdir:
            try:
                os.makedirs(outdir, exist_ok=True)
                probe = os.path.join(outdir, ".cyoa_write_test")
                with open(probe, "w") as _pf:
                    _pf.write("ok")
                os.remove(probe)
            except Exception as e:
                messagebox.showerror(
                    "Output folder",
                    f"Folder output tidak bisa ditulis:\n{outdir}\n\n{e}\n\n"
                    "Pilih folder lain lalu coba lagi.")
                return

        if not self._prepare_ytdlp_cookies():
            return

        # Snapshot the queue for this run only. The GUI intentionally still
        # allows users to add more URLs while a run is active; those new rows
        # must remain queued after the current snapshot finishes.
        run_items = list(self._queue_data)
        self._active_run_queue_ids = {
            str(it.get("_queue_id") or "")
            for it in run_items
            if it.get("_queue_id")
        }
        # Keep the URL set for older fallback code; active v46 completion uses
        # the row identities above so same-URL rows cannot remove each other.
        self._active_run_urls = {str(it.get("url", "")) for it in run_items if it.get("url")}

        self._is_running = True
        self._dl_btn.configure(state="disabled")
        self._pause_btn.configure(state="normal", text="⏸ Pause")
        self._pb.start()
        self._start_speed_graph()
        self._status_var.set(self._tr("downloading"))
        threading.Thread(
            target=self._worker,
            args=(run_items,
                  self._mode_var,
                  wt,
                  threads,
                  outdir,
                  self._fonts_var.get(),
                  self._analyse_var.get(),
                  _normalize_cloudflare_mode(self._cf_mode_var.get()),
                  self._http2_var.get(),
                  self._ytdlp_var.get(),
                  bw,
                  self._cyoa_mgr_var.get()),
            daemon=True,
        ).start()

    def _start(self) -> None:
        return self._dispatch_gui_patch("_v46_start", fallback=self._start_base)

    def _preview_queue(self) -> None:
        """Feature 2: Probe all URLs in queue and show estimated outcomes before download."""
        import customtkinter as ctk
        if self._is_running:
            return
        if not self._queue_data:
            from tkinter import messagebox
            messagebox.showwarning(self._tr("queue_empty_title"), self._tr("queue_empty_body"))
            return

        p    = self._p()
        items = list(self._queue_data)

        win = self._make_singleton_window("queue_preview")
        if win is None:
            return
        win.title("Pre-flight Check")
        win.geometry("760x480")
        win.grab_set()

        ctk.CTkLabel(win, text="Pre-flight Check",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(win, text="Probing URLs sebelum download dimulai…",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=p["muted"]).pack(anchor="w", padx=16, pady=(0, 10))

        prog = ctk.CTkProgressBar(win, mode="determinate", height=5)
        prog.pack(fill="x", padx=16, pady=(0, 8))
        prog.set(0)

        status_lbl = ctk.CTkLabel(win, text="",
                                   font=ctk.CTkFont("Segoe UI", 10),
                                   text_color=p["muted"])
        status_lbl.pack(anchor="w", padx=16, pady=(0, 8))

        results_frame = ctk.CTkScrollableFrame(win, fg_color=p["bg"],
                                                scrollbar_button_color=p["surface2"])
        results_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 12))

        proceed_btn = ctk.CTkButton(btn_frame, text="▶ Proceed with Download",
                                     fg_color="#3b82f6", hover_color="#2563eb",
                                     font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                     command=lambda: (win.destroy(), self._start()),
                                     state="disabled")
        proceed_btn.pack(side="left")
        ctk.CTkButton(btn_frame, text="Tutup", width=80,
                       fg_color=p["surface2"], text_color=p["muted"],
                       command=win.destroy).pack(side="left", padx=8)

        summary_var = ctk.StringVar(value="")
        ctk.CTkLabel(btn_frame, textvariable=summary_var,
                      font=ctk.CTkFont("Segoe UI", 11),
                      text_color=p["muted"]).pack(side="right")

        row_widgets: List = []

        def _add_result_row(idx, url, status_text, status_color, detail=""):
            bg = p["surface"] if idx % 2 == 0 else p["bg"]
            row = ctk.CTkFrame(results_frame, fg_color=bg, corner_radius=4)
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkLabel(row, text=status_text, width=90,
                          font=ctk.CTkFont("Segoe UI", 10, "bold"),
                          text_color=status_color, anchor="w").pack(side="left", padx=(8, 4), pady=6)
            ctk.CTkLabel(row, text=url[:65] + ("…" if len(url) > 65 else ""),
                          font=ctk.CTkFont("Consolas", 9),
                          text_color=p["muted"], anchor="w").pack(side="left", fill="x", expand=True)
            if detail:
                ctk.CTkLabel(row, text=detail,
                              font=ctk.CTkFont("Segoe UI", 9),
                              text_color=p["muted2"], anchor="e").pack(side="right", padx=8)
            row_widgets.append(row)

        def _probe_worker():
            # Route every cross-thread Tk update through
            # _v25_safe_after so closing the probe window mid-run can no longer
            # raise TclError ("invalid command name") from a daemon thread that
            # still holds references to destroyed widgets. This matches the
            # guarded pattern already used by the viewer/update panels; behavior
            # while the window is open is unchanged.
            ok = warn = fail = 0
            for i, item in enumerate(items):
                url = item["url"]
                _v25_safe_after(win, lambda u=url, i=i: status_lbl.configure(
                    text=f"[{i+1}/{len(items)}] Probing: {u[:50]}…"))
                _v25_safe_after(win, lambda v=(i+1)/len(items): prog.set(v))

                # Quick HEAD check of project candidates
                try:
                    candidates = build_default_project_candidates(url)
                    live = _parallel_head_check(candidates[:12], max_workers=6, timeout=5)
                    if live:
                        detail = f"project.json: {len(live)} candidate"
                        color  = "#22c55e"
                        label  = "✓ FOUND"
                        ok += 1
                    else:
                        # Try page fetch through the shared HTTP pipeline so proxy, DNS,
                        # Cloudflare mode, and FlareSolverr settings are respected.
                        try:
                            rp = None
                            try:
                                rp = fetch_response(url, timeout=6, extra_headers={"User-Agent": "Mozilla/5.0"})
                            finally:
                                if rp is not None:
                                    try:
                                        rp.close()
                                    except Exception:
                                        pass
                            if rp is not None and rp.status_code < 400:
                                detail = "Page OK — might need JS scan"
                                color  = "#f59e0b"
                                label  = "⚠ JS/SCAN"
                                warn += 1
                            else:
                                detail = "No reachable page"
                                color  = "#ef4444"
                                label  = "✗ ERROR"
                                fail += 1
                        except Exception as e:
                            detail = str(e)[:40]
                            color  = "#ef4444"
                            label  = "✗ OFFLINE"
                            fail += 1
                except Exception as e:
                    detail = str(e)[:40]
                    color  = "#ef4444"
                    label  = "✗ ERROR"
                    fail += 1

                _v25_safe_after(win, lambda i=i, u=url, lbl=label, c=color, d=detail:
                          _add_result_row(i, u, lbl, c, d))

            _v25_safe_after(win, lambda: status_lbl.configure(text="Probe selesai."))
            _v25_safe_after(win, lambda: prog.set(1.0))
            _v25_safe_after(win, lambda: proceed_btn.configure(state="normal"))
            _v25_safe_after(win, lambda: summary_var.set(
                f"✓ {ok}  ⚠ {warn}  ✗ {fail}  dari {len(items)} URL"))

        threading.Thread(target=_probe_worker, daemon=True).start()



    def _set_dot(self, idx: int, state: str) -> None:
        """Update status dot in queue row. state: 'idle'|'running'|'done'|'error'|'skip'"""
        if idx >= len(self._queue_rows):
            return
        _, dot, _, _, _ = self._queue_rows[idx]
        colors = {"idle": self._p()["muted2"], "running": "#3b82f6",
                  "done": "#22c55e", "error": "#ef4444", "skip": "#f59e0b"}
        color = colors.get(state, self._p()["muted2"])

        def _update():
            try:
                # Guard: widget may have been destroyed if user removed the row
                if not dot.winfo_exists():
                    return
                dot.delete("all")
                dot.create_oval(2, 2, 8, 8, fill=color, outline="")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _update (line 7815): %s", _ignored_exc)
        self.root.after(0, _update)

    def _worker_base(self, items, default_mode, wt, threads, outdir, dl_fonts, show_analysis, cloudflare_mode, http2_enabled, ytdlp_enabled, bw_limit, cyoa_mgr) -> None:
        _self_mod = sys.modules.get(__name__)
        if _self_mod is not None:
            _self_mod._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            _self_mod._gui_speed_cb = self._record_speed_bytes
        try:
            from cyoa_downloader_app.runtime import state as _runtime_state
            from cyoa_downloader_app.runtime.compat import mirror_to_legacy
            _runtime_state._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            _runtime_state._gui_speed_cb = self._record_speed_bytes
            _runtime_state._ytdlp_enabled = ytdlp_enabled
            _runtime_state._bandwidth_limit_kbps = bw_limit
            _runtime_state.wait_time = wt
            mirror_to_legacy("_ytdlp_gui_progress_cb", _runtime_state._ytdlp_gui_progress_cb)
            mirror_to_legacy("_gui_speed_cb", _runtime_state._gui_speed_cb)
            mirror_to_legacy("_ytdlp_enabled", _runtime_state._ytdlp_enabled)
            mirror_to_legacy("_bandwidth_limit_kbps", _runtime_state._bandwidth_limit_kbps)
            mirror_to_legacy("wait_time", _runtime_state.wait_time)
        except Exception as _state_sync_exc:
            logger.debug("Ignored runtime-state sync exception in GUI worker: %s", _state_sync_exc)
        global wait_time, use_cloudscraper, _shared_session, _shared_session_cf, _ytdlp_enabled, _bandwidth_limit_kbps
        _ytdlp_enabled        = ytdlp_enabled
        _bandwidth_limit_kbps = bw_limit
        wait_time        = wt
        # Apply Cloudflare engine selection for this worker run.
        _set_cloudflare_config(
            cloudflare_mode,
            flaresolverr_url=_load_settings().get("flaresolverr_url", _FLARESOLVERR_URL),
            session_policy=_load_settings().get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY),
            timeout=int(_load_settings().get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT) or _FLARESOLVERR_TIMEOUT),
            wait_after=int(_load_settings().get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER) or _FLARESOLVERR_WAIT_AFTER),
            proxy_mode=_load_settings().get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE),
            persist=True,
        )
        logger.info(f"[Cloudflare] Mode: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}")
        _set_http2_enabled(bool(http2_enabled))
        setup_file_logging(outdir)

        # ── Resume state ───────────────────────────────────────────
        state       = load_resume_state(outdir)
        completed   = set(state["completed"])
        prev_failed = set(f["url"] if isinstance(f, dict) else f for f in state["failed"])
        url_counts = Counter(str(item.get("url") or "") for item in items if item.get("url"))
        duplicate_urls = {url for url, count in url_counts.items() if count > 1}

        skipped_count = sum(
            1 for it in items
            if it["url"] in completed and it["url"] not in duplicate_urls
        )
        if skipped_count:
            logger.info(f"[Resume] Melanjutkan dari sesi sebelumnya — {skipped_count} URL sudah selesai, di-skip")

        # Mark already-completed items in the queue dots
        for idx, item in enumerate(items):
            if item["url"] in completed and item["url"] not in duplicate_urls:
                self._set_dot(idx, "done")
            elif item["url"] in prev_failed:
                self._set_dot(idx, "error")

        ok = 0
        failed_items: List[Dict[str, str]] = []
        completed_urls: List[str] = list(completed)
        self._active_run_success_ids = set()
        self._last_results: List[Dict] = []   # populated for Results popup

        # ── Auto-detect phase ──────────────────────────────────────
        auto_items = [it for it in items
                      if it.get("mode", default_mode) == "auto"
                      and (it["url"] not in completed or it["url"] in duplicate_urls)]
        if auto_items:
            self._set_status(f"Auto-detecting mode for {len(auto_items)} URL(s)…")
            logger.info(f"[Auto-detect] Starting probe for {len(auto_items)} URL(s)…")

            def _progress(done, total):
                self._set_status(f"Auto-detecting… {done}/{total}")

            auto_detect_modes_batch(auto_items, max_workers=min(4, threads),
                                    progress_cb=_progress)
            for i, it in enumerate(items):
                if not it.get("auto_detected"):
                    continue
                # ``items`` can contain manual rows before an Auto row.  Find
                # the widget by queue-item identity instead of assuming that
                # the item's position equals the auto-item position.
                row_idx = next(
                    (queue_idx for queue_idx, queued in enumerate(self._queue_data)
                     if queued is it or (
                         it.get("_queue_id") and
                         queued.get("_queue_id") == it.get("_queue_id")
                     )),
                    i,
                )
                if row_idx < len(self._queue_rows):
                    _, _, _, badge_lbl, _ = self._queue_rows[row_idx]
                    bg, fg = self._badge_colors(it["mode"])
                    _v25_safe_after_widget(
                        self.root, badge_lbl,
                        lambda b=badge_lbl, bg=bg, fg=fg, m=it["mode"]: b.configure(
                            text=m.replace("_", " ") + " *", fg_color=bg, text_color=fg))

        # ── Download phase ─────────────────────────────────────────
            pending = [it for it in items
                       if it["url"] not in completed or it["url"] in duplicate_urls]
        total   = len(items)

        for i, item in enumerate(pending):
            real_idx = items.index(item)   # index in original list for dot update
            url = item["url"]

            # ── Pause gate ──────────────────────────────────────────
            if not self._paused.is_set():
                self._set_status(f"⏸ Paused — {i}/{len(pending)} done")
            self._paused.wait()   # blocks until unpaused

            # Check if download was cancelled while paused
            if not self._is_running:
                break

            # Skip already completed
            if url in completed and url not in duplicate_urls:
                ok += 1
                if item.get("_queue_id"):
                    self._active_run_success_ids.add(str(item["_queue_id"]))
                self._set_dot(real_idx, "done")
                continue

            mode = item.get("mode", "").strip() or default_mode
            if mode == "auto":
                self._set_status(f"[{i+1}/{len(pending)}] Auto-detecting…")
                mode = auto_detect_mode(url)
                item["mode"] = mode
                logger.info(f"[Auto-detect] {url} → {mode}")

            self._set_status(f"[{i+1}/{len(pending)}] [{mode}] {url[:45]}…")
            self._set_dot(real_idx, "running")

            try:
                _mf = _derive_mode_flags(mode)
                run_download(
                    url=url,
                    file_name=item.get("filename", ""),
                    zip_output=_mf["zip"],
                    both_output=_mf["both"],
                    website_output=_mf["website"],
                    website_zip_output=_mf["website_zip"],
                    pure_website=_mf["pure"],
                    download_fonts=dl_fonts,
                    show_font_analysis=show_analysis,
                    output_dir=outdir,
                    max_workers=threads,
                    engine_mode=_mf["engine"],
                    cyoa_mgr_enabled=cyoa_mgr,
                    ai_api_key=_resolve_ai_api_key(session_key=self._ai_api_key, storage=getattr(self, "_ai_key_storage", "session"), provider=getattr(self, "_ai_provider", "anthropic")) if self._ai_enabled and _normalize_ai_mode(getattr(self, "_ai_mode", "auto_fallback")) != "off" else "",
                    ai_provider=getattr(self, "_ai_provider", "anthropic"),
                    ai_mode=getattr(self, "_ai_mode", "auto_fallback"),
                )
                ok += 1
                completed_urls.append(url)
                if item.get("_queue_id"):
                    self._active_run_success_ids.add(str(item["_queue_id"]))
                self._last_results.append({
                    "url": url, "mode": mode, "status": "OK",
                    "filename": item.get("filename", ""), "error": ""
                })
                self._set_dot(real_idx, "done")
                _record_history(url, item.get("filename", ""), mode, success=True)
                # Save state after every success so resume works mid-batch
                save_resume_state(outdir, completed_urls,
                                  [f["url"] for f in failed_items])

            except Exception as e:
                logger.error(f"Failed [{url}]: {e}")
                failed_items.append({"url": url, "error": str(e)})
                self._last_results.append({
                    "url": url, "mode": mode, "status": "FAIL",
                    "filename": item.get("filename", ""), "error": str(e)
                })
                self._set_dot(real_idx, "error")
                _record_history(url, item.get("filename", ""), mode, success=False)
                save_resume_state(outdir, completed_urls,
                                  [f["url"] for f in failed_items])

        write_failed_url_log(failed_items, outdir)
        total_done = ok + skipped_count
        self._set_status(f"Done — {total_done}/{total} succeeded")

        # Clear resume state only on full success
        if len(failed_items) == 0:
            clear_resume_state(outdir)
            logger.info("[Resume] All items succeeded — state cleared")
        else:
            logger.info(f"[Resume] {len(failed_items)} item gagal — state disimpan untuk resume")

        # Show results popup after batch (>1 item)
        if total > 1:
            self.root.after(0, self._show_results)
        self.root.after(0, self._done)


    def _worker(self, items, default_mode, wt, threads, outdir, dl_fonts, show_analysis, cloudflare_mode, http2_enabled, ytdlp_enabled, bw_limit, cyoa_mgr) -> None:
        return self._dispatch_gui_patch(
            "_v46_worker",
            items, default_mode, wt, threads, outdir, dl_fonts, show_analysis,
            cloudflare_mode, http2_enabled, ytdlp_enabled, bw_limit, cyoa_mgr,
            fallback=self._worker_base,
        )

    def _auto_detect_output_panel(self) -> None:
        """Choose the concrete output variant used by Auto mode.

        This only affects Auto mode. Explicit modes such as website_folder,
        website_zip, cyoap_vue_folder, and cyoap_vue_zip keep their old behavior.
        """
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"
        st = _load_settings()
        current = _normalize_auto_detect_output(st.get("auto_detect_output", "folder"))

        win = self._make_singleton_window("auto_detect_output")
        if win is None:
            return
        win.title("Auto Detect Output" if is_en else "Output Auto Detect")
        win.geometry("520x360")
        win.grab_set()

        ctk.CTkLabel(
            win,
            text=("⚡  Auto Detect Output" if is_en else "⚡  Output Auto Detect"),
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=p["fg"],
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            win,
            text=(
                "Controls the default output used by Auto mode after detection. Explicit modes are unchanged."
                if is_en else
                "Mengatur default output yang dipakai mode Auto setelah deteksi. Mode eksplisit tidak berubah."
            ),
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"],
            wraplength=470,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        body = ctk.CTkFrame(win, fg_color=p["surface"], corner_radius=12)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        var = ctk.StringVar(value=("ZIP" if current == "zip" else "Folder"))
        ctk.CTkLabel(
            body,
            text=("Default Auto output" if is_en else "Default output Auto"),
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=p["fg"],
        ).pack(anchor="w", padx=14, pady=(14, 6))
        ctk.CTkSegmentedButton(
            body,
            values=["Folder", "ZIP"],
            variable=var,
            height=34,
        ).pack(anchor="w", padx=14, pady=(0, 12))

        mapping = (
            "Folder → website_folder / cyoap_vue_folder\n"
            "ZIP    → website_zip / cyoap_vue_zip\n\n"
            "This setting affects Auto mode in GUI queue, imported rows with mode=auto, and CLI batch rows with mode=auto."
            if is_en else
            "Folder → website_folder / cyoap_vue_folder\n"
            "ZIP    → website_zip / cyoap_vue_zip\n\n"
            "Pengaturan ini berlaku untuk mode Auto di antrean GUI, baris import dengan mode=auto, dan batch CLI dengan mode=auto."
        )
        ctk.CTkLabel(
            body,
            text=mapping,
            font=ctk.CTkFont("Consolas", 10),
            text_color=p["muted"],
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        def _save():
            pref = "zip" if var.get().strip().lower() == "zip" else "folder"
            _update_setting("auto_detect_output", pref)
            try:
                self._update_mode_info(getattr(self, "_mode_var", "auto"))
                self._apply_language()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _save (line 8061): %s", _ignored_exc)
            messagebox.showinfo(
                "Saved" if is_en else "Tersimpan",
                (f"Auto mode now outputs: {pref.upper()}" if is_en else f"Mode Auto sekarang menghasilkan: {pref.upper()}"),
            )
            win.destroy()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 14))
        ctk.CTkButton(btns, text=("Save" if is_en else "Simpan"), command=_save, width=110).pack(side="right")
        ctk.CTkButton(btns, text=("Cancel" if is_en else "Batal"), command=win.destroy, width=100,
                      fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"]).pack(side="right", padx=(0, 8))

    def _toggles_panel(self) -> None:
        """Modern feature toggles center.

        The toggles still write the same settings keys and call the same runtime
        setters. This is a UI-only consolidation pass: no download behavior,
        output format, or CLI contract is changed.
        """
        import customtkinter as ctk
        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"

        win = self._make_singleton_window("feature_toggles")
        if win is None:
            return
        win.title("Feature Toggles" if is_en else "Toggle Fitur")
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = min(820, max(720, sw - 420)), min(680, max(600, sh - 240))
            win.geometry(f"{w}x{h}+{max(24,(sw-w)//2)}+{max(24,(sh-h)//2)}")
        except Exception:
            win.geometry("780x640")
        win.minsize(700, 560)
        win.configure(fg_color=p["bg"])
        try:
            win.grab_set()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _toggles_panel (line 8100): %s", _ignored_exc)

        root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=70)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr,
            text="🎛  Feature Toggles" if is_en else "🎛  Toggle Fitur",
            font=ctk.CTkFont("Segoe UI", 16, "bold"),
            text_color=p["fg"], anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        ctk.CTkLabel(
            hdr,
            text=(
                "Enable/disable optional helpers. Changes are saved immediately."
                if is_en else
                "Aktif/nonaktifkan helper opsional. Perubahan langsung tersimpan."
            ),
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"], anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(2, 8))

        s = _load_settings()
        body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        status_var = ctk.StringVar(value="")
        def _set_status(msg: str, ok: bool = True) -> None:
            status_var.set(msg)
            try:
                status_lbl.configure(text_color=p["accent"] if ok else "#f59e0b")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _set_status (line 8140): %s", _ignored_exc)

        def _section(row: int, text: str) -> int:
            ctk.CTkLabel(
                body, text=text.upper(), font=ctk.CTkFont("Segoe UI", 10, "bold"),
                text_color=p["accent"], anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 4))
            return row + 1

        def _switch_card(row: int, col: int, icon: str, title: str, desc: str,
                         key: str, default: bool, setter, accent: str) -> ctk.BooleanVar:
            var = ctk.BooleanVar(value=bool(s.get(key, default)))
            card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12,
                                border_width=1, border_color=p["border"])
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, width=30, font=ctk.CTkFont("Segoe UI Emoji", 18),
                         text_color=accent).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=12, sticky="n")
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"),
                         text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 1))
            ctk.CTkLabel(card, text=desc, font=ctk.CTkFont("Segoe UI", 10),
                         text_color=p["muted"], anchor="w", justify="left", wraplength=285).grid(row=1, column=1, sticky="ew", pady=(0, 12))
            def _on() -> None:
                val = bool(var.get())
                _update_setting(key, val)
                setter(val)
                logger.info(f"[Feature] {key}: {'enabled' if val else 'disabled'}")
                _set_status((f"{title}: {'ON' if val else 'OFF'}" if is_en else f"{title}: {'AKTIF' if val else 'NONAKTIF'}"), True)
            sw = ctk.CTkSwitch(card, text="", variable=var, command=_on,
                               progress_color=accent, button_color="#e5e7eb",
                               button_hover_color="#ffffff", width=46)
            sw.grid(row=0, column=2, rowspan=2, padx=(8, 12), pady=12)
            return var

        r = 0
        r = _section(r, "Core workflow" if is_en else "Alur utama")
        _switch_card(r, 0, "🔎", "Deep scan" if is_en else "Deep scan",
                     "Discover assets referenced by JS/CSS bundles." if is_en else "Mencari aset dari bundle JS/CSS.",
                     "deep_scan_enabled", True, _set_deep_scan_enabled, "#3b82f6")
        _switch_card(r, 1, "🖼", "Selenium fallback" if is_en else "Fallback Selenium",
                     "Headless browser fallback for difficult image detection." if is_en else "Fallback headless browser untuk deteksi gambar sulit.",
                     "selenium_enabled", True, _set_selenium_enabled, "#8b5cf6")
        r += 1
        _switch_card(r, 0, "⚡", "Serve preview" if is_en else "Preview server",
                     "Local HTTP server for downloaded ICC folders." if is_en else "Server HTTP lokal untuk folder ICC hasil download.",
                     "serve_enabled", True, _set_serve_enabled, "#10b981")
        _switch_card(r, 1, "🧩", "Cheat panel" if is_en else "Panel cheat",
                     "Bundled ICE helper for localhost preview only." if is_en else "Helper ICE bawaan khusus preview localhost.",
                     "cheat_enabled", True, _set_cheat_enabled, "#f59e0b")
        r += 1

        archive_card = ctk.CTkFrame(
            body, fg_color=p["surface"], corner_radius=12,
            border_width=1, border_color=p["border"],
        )
        archive_card.grid(row=r, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        archive_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            archive_card, text="🌐", width=30,
            font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#06b6d4",
        ).grid(row=0, column=0, rowspan=4, padx=(12, 6), pady=12, sticky="n")
        ctk.CTkLabel(
            archive_card,
            text=("JavaScript website archive" if is_en else "Arsip website JavaScript"),
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w",
        ).grid(row=0, column=1, sticky="ew", pady=(12, 1))
        ctk.CTkLabel(
            archive_card,
            text=(
                "Auto fingerprints the site; Classic preserves the old flow; Smart/Browser add bounded discovery."
                if is_en else
                "Auto mengenali tipe situs; Classic mempertahankan alur lama; Smart/Browser menambah discovery terbatas."
            ),
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            anchor="w", justify="left", wraplength=470,
        ).grid(row=1, column=1, sticky="ew", pady=(0, 12))
        archive_var = ctk.StringVar(value=str(s.get("archive_strategy", "classic") or "classic").lower())

        def _set_archive_strategy(value: str) -> None:
            strategy = str(value or "classic").lower()
            _update_setting("archive_strategy", strategy)
            logger.info(f"[Feature] archive_strategy: {strategy}")
            _set_status(
                (f"Website archive: {strategy.upper()}" if is_en else f"Arsip website: {strategy.upper()}"),
                True,
            )

        ctk.CTkOptionMenu(
            archive_card, values=["classic", "smart", "browser", "auto"],
            variable=archive_var, command=_set_archive_strategy,
            width=120, fg_color="#0891b2", button_color="#0e7490",
            button_hover_color="#155e75",
        ).grid(row=0, column=2, rowspan=2, padx=(10, 12), pady=12)

        # Smart/Browser limits are editable here so large route trees do not
        # require a manual settings.json change (Isekai Quest exceeds 300).
        archive_limits = ctk.CTkFrame(archive_card, fg_color="transparent")
        archive_limits.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 12), padx=(0, 12))
        archive_pages_var = ctk.StringVar(value=str(s.get("archive_max_pages", 300) or 300))
        archive_depth_var = ctk.StringVar(value=str(s.get("archive_max_depth", 30)))
        archive_interaction_var = ctk.StringVar(
            value=str(s.get("archive_interaction_policy", "safe") or "safe").lower()
        )
        archive_scroll_var = ctk.StringVar(value=str(s.get("archive_max_scroll_steps", 100) or 100))
        archive_click_var = ctk.StringVar(value=str(s.get("archive_max_interactions", 20)))

        ctk.CTkLabel(
            archive_limits, text=("Max pages" if is_en else "Maks. halaman"),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
        ).pack(side="left", padx=(0, 4))
        archive_pages_entry = ctk.CTkEntry(
            archive_limits, textvariable=archive_pages_var, width=72, height=28,
            fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
        )
        archive_pages_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            archive_limits, text=("Max depth" if is_en else "Maks. kedalaman"),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
        ).pack(side="left", padx=(0, 4))
        archive_depth_entry = ctk.CTkEntry(
            archive_limits, textvariable=archive_depth_var, width=62, height=28,
            fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
        )
        archive_depth_entry.pack(side="left", padx=(0, 10))

        archive_runtime = ctk.CTkFrame(archive_card, fg_color="transparent")
        archive_runtime.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 12), padx=(0, 12))
        ctk.CTkLabel(
            archive_runtime, text=("Interaction" if is_en else "Interaksi"),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
        ).pack(side="left", padx=(0, 4))
        ctk.CTkOptionMenu(
            archive_runtime, values=["off", "safe"], variable=archive_interaction_var,
            width=82, height=28, fg_color="#0891b2", button_color="#0e7490",
            button_hover_color="#155e75",
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            archive_runtime, text=("Scroll steps" if is_en else "Langkah scroll"),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
        ).pack(side="left", padx=(0, 4))
        archive_scroll_entry = ctk.CTkEntry(
            archive_runtime, textvariable=archive_scroll_var, width=62, height=28,
            fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
        )
        archive_scroll_entry.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            archive_runtime, text=("Max clicks" if is_en else "Maks. klik"),
            font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"],
        ).pack(side="left", padx=(0, 4))
        archive_click_entry = ctk.CTkEntry(
            archive_runtime, textvariable=archive_click_var, width=56, height=28,
            fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
        )
        archive_click_entry.pack(side="left")

        def _save_archive_limits(_event=None) -> None:
            try:
                max_pages = max(1, min(5000, int(archive_pages_var.get().strip())))
                max_depth = max(0, min(100, int(archive_depth_var.get().strip())))
                max_scroll = max(1, min(1000, int(archive_scroll_var.get().strip())))
                max_clicks = max(0, min(100, int(archive_click_var.get().strip())))
            except (TypeError, ValueError):
                _set_status(
                    "Archive limits must be whole numbers." if is_en else
                    "Batas arsip harus berupa angka bulat.",
                    False,
                )
                return
            archive_pages_var.set(str(max_pages))
            archive_depth_var.set(str(max_depth))
            archive_scroll_var.set(str(max_scroll))
            archive_click_var.set(str(max_clicks))
            _update_settings({
                "archive_max_pages": max_pages,
                "archive_max_depth": max_depth,
                "archive_interaction_policy": archive_interaction_var.get().strip().lower(),
                "archive_max_scroll_steps": max_scroll,
                "archive_max_interactions": max_clicks,
            })
            logger.info(
                f"[Feature] archive limits: max_pages={max_pages}, max_depth={max_depth}, "
                f"max_scroll={max_scroll}, max_interactions={max_clicks}"
            )
            _set_status(
                (f"Archive limits saved: {max_pages} pages, depth {max_depth}" if is_en else
                 f"Batas arsip tersimpan: {max_pages} halaman, kedalaman {max_depth}"),
                True,
            )

        ctk.CTkButton(
            archive_limits, text=("Save" if is_en else "Simpan"),
            command=_save_archive_limits, width=72, height=28,
            fg_color="#0891b2", hover_color="#0e7490",
        ).pack(side="left")
        archive_pages_entry.bind("<Return>", _save_archive_limits)
        archive_depth_entry.bind("<Return>", _save_archive_limits)
        archive_scroll_entry.bind("<Return>", _save_archive_limits)
        archive_click_entry.bind("<Return>", _save_archive_limits)
        # Persistent archive policy has moved to Settings / Maintenance. Keep
        # this legacy construction temporarily for compatibility with older
        # patch layers, but do not expose duplicate controls in Features.
        archive_card.grid_remove()
        r += 1

        r = _section(r + 1, "Optional backends" if is_en else "Backend opsional")
        # gallery-dl maps OFF <-> SMART so legacy CLI/output behavior remains unchanged.
        gallery_var = ctk.BooleanVar(value=str(s.get("gallery_dl_mode", "off") or "off").lower() != "off")
        def _set_gallery_from_toggle(_val: bool) -> None:
            mode = "smart" if bool(gallery_var.get()) else "off"
            _set_gallery_dl_mode(
                mode,
                path=str(s.get("gallery_dl_path", "gallery-dl") or "gallery-dl"),
                config=str(s.get("gallery_dl_config", "") or ""),
                persist=True,
            )
            logger.info(f"[Feature] gallery_dl_mode: {mode}")
            _set_status((f"gallery-dl fallback: {mode}" if is_en else f"Fallback gallery-dl: {mode}"), True)
        card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12, border_width=1, border_color=p["border"])
        card.grid(row=r, column=0, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="🖼", width=30, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#14b8a6").grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=12, sticky="n")
        ctk.CTkLabel(card, text="gallery-dl fallback", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 1))
        ctk.CTkLabel(card, text=("Smart fallback for gallery/post URLs." if is_en else "Fallback smart untuk URL galeri/post."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", wraplength=285).grid(row=1, column=1, sticky="ew", pady=(0, 12))
        ctk.CTkSwitch(card, text="", variable=gallery_var, command=lambda: _set_gallery_from_toggle(gallery_var.get()), progress_color="#14b8a6", width=46).grid(row=0, column=2, rowspan=2, padx=(8, 12), pady=12)

        itch_var = _switch_card(r, 1, "🎮", "itch.io downloader" if is_en else "Downloader itch.io",
                                "Optional backend; public mode works without an API key." if is_en else "Backend opsional; mode publik tetap bisa tanpa API key.",
                                "itch_enabled", False, _set_itch_enabled, "#ef4444")
        r += 1

        r = _section(r + 1, "itch.io API key" if is_en else "API key itch.io")
        key_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12, border_width=1, border_color=p["border"])
        key_card.grid(row=r, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        key_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(key_card, text="🔐", width=30, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#60a5fa").grid(row=0, column=0, rowspan=3, padx=(12, 8), pady=14, sticky="n")
        ctk.CTkLabel(key_card, text=("Optional API key" if is_en else "API key opsional"), font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 2))
        ctk.CTkLabel(key_card, text=("Leave blank for public assets. Secrets are never printed in diagnostics/logs." if is_en else "Biarkan kosong untuk aset publik. Secret tidak dicetak di diagnostik/log."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 4))
        key_entry = ctk.CTkEntry(key_card, show="•", placeholder_text=("leave blank for public assets" if is_en else "kosongkan untuk aset publik"), height=32, fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"])
        key_entry.grid(row=2, column=1, sticky="ew", pady=(0, 12))
        try:
            existing_key, _src = _resolve_itch_api_key("")
            if existing_key:
                key_entry.insert(0, existing_key)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _toggles_panel (line 8231): %s", _ignored_exc)
        btns = ctk.CTkFrame(key_card, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=3, padx=(12, 14), pady=14, sticky="e")
        def _save_key() -> None:
            k = key_entry.get().strip()
            kr = _keyring_module()
            if kr is not None:
                try:
                    if k:
                        kr.set_password(_ITCH_KEYRING_SERVICE, _ITCH_KEYRING_USER, k)
                    _update_settings({"itch_key_storage": "keyring", "itch_api_key": ""})
                    _set_status("Key saved to OS keyring." if is_en else "Key tersimpan ke OS keyring.", True)
                    return
                except Exception as e:
                    logger.debug(f"itch keyring write failed: {e}")
            _update_settings({"itch_key_storage": "plain", "itch_api_key": k})
            _set_status("keyring unavailable — key saved in plaintext settings.json." if is_en else "keyring tidak tersedia — key tersimpan plaintext di settings.json.", False)
        def _test_key() -> None:
            _set_status("Testing itch.io…" if is_en else "Menguji itch.io…", True)
            import threading as _th
            def _work():
                ok, msg = itch_test_connection(explicit_key=key_entry.get().strip())
                self.root.after(0, lambda: _set_status(("✓ " if ok else "✗ ") + msg, bool(ok)))
            _th.Thread(target=_work, daemon=True).start()
        ctk.CTkButton(btns, text=("Save key" if is_en else "Simpan key"), width=118, height=30, command=_save_key, fg_color="#2563eb", hover_color="#1d4ed8", text_color="#ffffff").pack(pady=(0, 7))
        ctk.CTkButton(btns, text=("Test connection" if is_en else "Tes koneksi"), width=118, height=30, command=_test_key, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"]).pack()

        status_lbl = ctk.CTkLabel(root, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w")
        status_lbl.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 6))
        footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=42)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=90, height=28, command=win.destroy, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"]).grid(row=0, column=1, padx=14, pady=7)

    def _diagnostics_panel(self) -> None:
        """Show a richer diagnostics center.

        The checks run on a background thread and every Tk update is marshalled
        through self.root.after, so the panel remains responsive and Tk-safe.
        No API key, cookie, password, or token value is printed in the report.
        """
        import customtkinter as ctk
        from tkinter import filedialog
        p = self._p()
        is_en = (getattr(self, "_language", "id") == "en")
        lbl = {
            "title": "Diagnostics Center" if is_en else "Pusat Diagnostik",
            "header": "🩺 Diagnostics Center" if is_en else "🩺 Pusat Diagnostik",
            "running": "Running checks…" if is_en else "Menjalankan pengecekan…",
            "hint": (
                "Checks dependencies, paths, settings, output folder, cache, optional backends, and network. Secrets are redacted by design."
                if is_en else
                "Mengecek dependency, path, settings, folder output, cache, backend opsional, dan jaringan. Secret selalu disamarkan."
            ),
            "copied": "Report copied" if is_en else "Laporan disalin",
            "copy_failed": "Copy failed" if is_en else "Gagal menyalin",
            "save_title": "Save Diagnostic Report" if is_en else "Simpan Laporan Diagnostik",
            "saved": "Saved" if is_en else "Tersimpan",
            "save_failed": "Save failed" if is_en else "Gagal menyimpan",
            "done": "Done" if is_en else "Selesai",
            "run": "Run Again" if is_en else "Jalankan Lagi",
            "copy": "Copy" if is_en else "Salin",
            "save_as": "Save As…" if is_en else "Simpan Sebagai…",
            "save_output": "Save to Output" if is_en else "Simpan ke Output",
            "settings_folder": "Settings Folder" if is_en else "Folder Settings",
            "close": "Close" if is_en else "Tutup",
        }

        win = self._make_singleton_window("diagnostics_legacy")
        if win is None:
            return
        win.title(lbl["title"])
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(980, max(860, sw - 180))
            h = min(700, max(600, sh - 170))
            x = max(20, (sw - w) // 2)
            y = max(20, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("900x640")
        win.minsize(820, 560)
        win.grab_set()

        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=64)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text=lbl["header"],
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=p["fg"],
        ).pack(side="left", padx=16)
        status_lbl = ctk.CTkLabel(
            hdr, text=lbl["running"],
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=p["muted"],
        )
        status_lbl.pack(side="right", padx=16)

        hint = ctk.CTkLabel(
            win,
            text=lbl["hint"],
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"],
            anchor="w",
        )
        hint.pack(fill="x", padx=14, pady=(8, 6))

        summary_row = ctk.CTkFrame(win, fg_color="transparent")
        summary_row.pack(fill="x", padx=14, pady=(0, 8))
        pass_lbl = ctk.CTkLabel(summary_row, text="PASS 0", width=92,
                                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                fg_color="#064e3b", text_color="#bbf7d0", corner_radius=8)
        warn_lbl = ctk.CTkLabel(summary_row, text="WARN 0", width=92,
                                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                fg_color="#713f12", text_color="#fde68a", corner_radius=8)
        fail_lbl = ctk.CTkLabel(summary_row, text="FAIL 0", width=92,
                                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                fg_color="#7f1d1d", text_color="#fecaca", corner_radius=8)
        for badge_lbl in (pass_lbl, warn_lbl, fail_lbl):
            badge_lbl.pack(side="left", padx=(0, 8))

        box = ctk.CTkTextbox(win, font=ctk.CTkFont("Consolas", 11),
                             fg_color=p["bg"], text_color=p["fg"], wrap="none")
        box.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        try:
            box._textbox.configure(insertbackground=p["fg"])
            box._textbox.tag_config("diag_pass", foreground="#86efac")
            box._textbox.tag_config("diag_warn", foreground="#fcd34d")
            box._textbox.tag_config("diag_fail", foreground="#fca5a5")
            box._textbox.tag_config("diag_head", foreground="#93c5fd")
            box._textbox.tag_config("diag_muted", foreground=p["muted"])
            box._textbox.tag_config("diag_plain", foreground=p["fg"])
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _diagnostics_panel (line 8365): %s", _ignored_exc)
        box.insert("1.0", ("Running diagnostics…\n" if is_en else "Menjalankan diagnostik…\n"))
        box.configure(state="disabled")

        report_holder = {"text": "", "counts": {"PASS": 0, "WARN": 0, "FAIL": 0}}

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        def _set_buttons(state: str) -> None:
            for b in (copy_btn, save_btn, save_out_btn):
                b.configure(state=state)

        def _copy() -> None:
            try:
                win.clipboard_clear()
                win.clipboard_append(report_holder["text"])
                status_lbl.configure(text=lbl["copied"])
            except Exception as e:
                status_lbl.configure(text=f"{lbl['copy_failed']}: {e}")

        def _save_as() -> None:
            try:
                path = filedialog.asksaveasfilename(
                    title=lbl["save_title"],
                    defaultextension=".txt",
                    initialfile="cyoa_diagnostics.txt",
                    filetypes=[("Text", "*.txt"), ("All files", "*.*")])
                if path:
                    pathlib.Path(path).write_text(report_holder["text"], encoding="utf-8")
                    status_lbl.configure(text=f"{lbl['saved']}: {path}")
            except Exception as e:
                status_lbl.configure(text=f"{lbl['save_failed']}: {e}")

        def _save_to_output() -> None:
            try:
                folder = self._outdir_var.get() or os.path.dirname(_SETTINGS_FILE)
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, "cyoa_diagnostics.txt")
                pathlib.Path(path).write_text(report_holder["text"], encoding="utf-8")
                status_lbl.configure(text=f"{lbl['saved']}: {path}")
            except Exception as e:
                status_lbl.configure(text=f"{lbl['save_failed']}: {e}")

        def _render(text: str, counts: Dict[str, int]) -> None:
            report_holder["text"] = text
            report_holder["counts"] = dict(counts or {})
            box.configure(state="normal")
            box.delete("1.0", "end")

            def _insert_line(line: str) -> None:
                stripped = line.rstrip("\n")
                if not stripped:
                    box.insert("end", "\n")
                    return
                if re.match(r"^PASS:\s*\d+.*WARN:\s*\d+.*FAIL:\s*\d+", stripped):
                    m = re.search(r"PASS:\s*(\d+)\s+WARN:\s*(\d+)\s+FAIL:\s*(\d+)", stripped)
                    if m:
                        box.insert("end", "PASS: ", "diag_pass")
                        box.insert("end", m.group(1), "diag_pass")
                        box.insert("end", "    ")
                        box.insert("end", "WARN: ", "diag_warn")
                        box.insert("end", m.group(2), "diag_warn")
                        box.insert("end", "    ")
                        box.insert("end", "FAIL: ", "diag_fail")
                        box.insert("end", m.group(3), "diag_fail")
                        box.insert("end", "\n")
                        return
                if stripped.startswith("PASS "):
                    tag = "diag_pass"
                elif stripped.startswith("WARN "):
                    tag = "diag_warn"
                elif stripped.startswith("FAIL "):
                    tag = "diag_fail"
                elif stripped.startswith("CYOA Downloader ") or set(stripped) in ({"="}, {"-"}):
                    tag = "diag_head"
                else:
                    tag = "diag_plain"
                box.insert("end", stripped + "\n", tag)

            for line in (text or "").splitlines():
                _insert_line(line)
            box.configure(state="disabled")
            pcount = int((counts or {}).get("PASS", 0))
            wcount = int((counts or {}).get("WARN", 0))
            fcount = int((counts or {}).get("FAIL", 0))
            pass_lbl.configure(text=f"PASS {pcount}")
            warn_lbl.configure(text=f"WARN {wcount}")
            fail_lbl.configure(text=f"FAIL {fcount}")
            status_lbl.configure(text=f"{lbl['done']} — PASS {pcount}, WARN {wcount}, FAIL {fcount}")
            _set_buttons("normal")
            run_btn.configure(state="normal")

        def _run() -> None:
            run_btn.configure(state="disabled")
            _set_buttons("disabled")
            status_lbl.configure(text=lbl["running"])
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", ("Running diagnostics…\n" if is_en else "Menjalankan diagnostik…\n"))
            box.configure(state="disabled")

            def _worker() -> None:
                try:
                    text, counts = build_diagnostic_report(
                        output_dir=self._outdir_var.get() or "",
                        check_network=True,
                        check_ai=bool(getattr(self, "_ai_enabled", False)),
                        language=getattr(self, "_language", "id"),
                    )
                except Exception as e:
                    text, counts = f"Diagnostics error: {e}", {"PASS": 0, "WARN": 0, "FAIL": 1}
                self.root.after(0, lambda: _render(text, counts))

            threading.Thread(target=_worker, daemon=True).start()

        run_btn = ctk.CTkButton(btn_row, text=lbl["run"], command=_run,
                                fg_color=p["surface2"], text_color=p["fg"], width=92)
        run_btn.pack(side="left", padx=(0, 8))
        copy_btn = ctk.CTkButton(btn_row, text=lbl["copy"], command=_copy, state="disabled",
                                 fg_color=p["surface2"], text_color=p["fg"], width=80)
        copy_btn.pack(side="left", padx=(0, 8))
        save_btn = ctk.CTkButton(btn_row, text=lbl["save_as"], command=_save_as, state="disabled",
                                 fg_color=p["surface2"], text_color=p["fg"], width=92)
        save_btn.pack(side="left", padx=(0, 8))
        save_out_btn = ctk.CTkButton(btn_row, text=lbl["save_output"], command=_save_to_output, state="disabled",
                                     fg_color=p["surface2"], text_color=p["fg"], width=118)
        save_out_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text=lbl["settings_folder"], command=self._open_settings_folder,
                      fg_color=p["surface2"], text_color=p["fg"], width=120).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text=lbl["close"], command=win.destroy,
                      fg_color=p["surface2"], text_color=p["fg"], width=82).pack(side="right")

        _run()

    def _set_status(self, msg: str) -> None:
        self.root.after(0, lambda: self._status_var.set(msg))

    def _retry_youtube_audio(self) -> None:
        """Re-download YouTube audio from skipped_youtube_audio.txt."""
        import glob as _glob
        if not self._prepare_ytdlp_cookies():
            return
        out = os.path.abspath(self._outdir_var.get() or os.getcwd())
        skip_files = _glob.glob(os.path.join(out, "**", "skipped_youtube_audio.txt"),
                                recursive=True)
        if not skip_files:
            self._set_status("No skipped_youtube_audio.txt found.")
            return

        entries: List[Tuple[str, str]] = []
        for f in skip_files:
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line and line.startswith("http"):
                            item = (line, os.path.dirname(os.path.abspath(f)))
                            if item not in entries:
                                entries.append(item)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _retry_youtube_audio (line 8523): %s", _ignored_exc)

        if not entries:
            self._set_status("No YouTube URLs to retry.")
            return

        urls = sorted({url for url, _folder in entries})
        self._set_status(f"Retry {len(urls)} YouTube audio…")

        import threading as _thr

        def _do_retry():
            _mod = sys.modules.get(__name__)
            if _mod is not None:
                _mod._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            _state = sys.modules.get("cyoa_downloader_app.runtime.state")
            if _state is not None:
                _state._ytdlp_gui_progress_cb = self._on_ytdlp_progress
            ok = 0
            try:
                # Use each project's folder as the download root. The old code
                # passed ``out/audio`` here, which created ``audio/audio`` and
                # never changed the project JSON after a successful retry.
                json_files: List[str] = []
                for root, _dirs, files in os.walk(out):
                    for name in files:
                        if not name.endswith(".json") or name.endswith("_metadata.json"):
                            continue
                        if name in {"download_state.json", "download_history.json"}:
                            continue
                        json_files.append(os.path.join(root, name))

                candidates: Dict[str, str] = {}
                for json_path in json_files:
                    try:
                        with open(json_path, encoding="utf-8", errors="replace") as fh:
                            project_text = fh.read()
                    except OSError as exc:
                        logger.warning("Cannot read %s during audio retry: %s", json_path, exc)
                        continue
                    candidates[json_path] = project_text

                groups: Dict[str, Dict[str, Any]] = {}
                import re as _re

                def _reference_keys(url: str) -> set[str]:
                    keys = {url}
                    match = _re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
                    if match:
                        keys.add(match.group(1))
                    return keys

                for url, report_folder in entries:
                    keys = _reference_keys(url)
                    matches = [
                        (path, text) for path, text in candidates.items()
                        if any(key in text for key in keys)
                    ]
                    # Prefer the live payload, but allow the raw backup to
                    # identify the project on a second retry after the live
                    # project has already been localized.
                    matches.sort(key=lambda item: (
                        0 if os.path.basename(item[0]).lower() == "project.json" else (
                            2 if item[0].lower().endswith("_original.json") else 1
                        ),
                        item[0].lower(),
                    ))
                    if matches:
                        json_path, project_text = matches[0]
                        if json_path.lower().endswith("_original.json"):
                            # project_original.json is a read-only source
                            # reference; patch its sibling project.json.
                            target = json_path[:-len("_original.json")] + ".json"
                            if target in candidates:
                                json_path, project_text = target, candidates[target]
                        folder = os.path.dirname(json_path)
                        group = groups.setdefault(folder, {"urls": set(), "projects": {}})
                        group["urls"].add(url)
                        group["projects"][json_path] = project_text
                    else:
                        # Deterministic fallback: keep audio beside the report,
                        # never in the global output/audio directory by accident.
                        group = groups.setdefault(report_folder, {"urls": set(), "projects": {}})
                        group["urls"].add(url)

                if not groups:
                    groups[out] = {"urls": set(urls), "projects": {}}

                downloaded_total = 0
                patched_total = 0
                for folder, group in groups.items():
                    result = _download_youtube_audio(
                        sorted(group["urls"]), folder, log_dir=folder,
                    )
                    downloaded_total += len(result)
                    for json_path, project_text in group["projects"].items():
                        patched = _patch_youtube_refs_in_json(project_text, result)
                        if patched == project_text:
                            continue
                        try:
                            with open(json_path, "w", encoding="utf-8") as fh:
                                fh.write(patched)
                            patched_total += 1
                            logger.info("Updated audio paths: %s", json_path)
                        except OSError as exc:
                            logger.error("Audio project update failed for %s: %s", json_path, exc)
                logger.info(
                    "[Retry Audio] %s track(s) downloaded, %s project file(s) patched.",
                    downloaded_total, patched_total,
                )
                ok = downloaded_total
            except Exception as exc:
                logger.exception("[Retry Audio] failed: %s", exc)
            finally:
                if _mod is not None:
                    _mod._ytdlp_gui_progress_cb = None
                if _state is not None:
                    _state._ytdlp_gui_progress_cb = None
            self.root.after(0, lambda: self._set_status(
                f"Retry YT audio selesai: {ok}/{len(urls)} berhasil"))

        _thr.Thread(target=_do_retry, daemon=True).start()

    def _on_ytdlp_progress_base(self, vid_id: str, idx: int, total: int,
                                pct: str, speed: str) -> None:
        """Called by yt-dlp progress hook → update status label."""
        msg = f"🎵 [{idx}/{total}] {vid_id[:12]}… {pct} @ {speed}"
        self.root.after(0, lambda m=msg: self._set_status(m))

    def _on_ytdlp_progress(self, vid_id: str, idx: int, total: int,
                           pct: str, speed: str) -> None:
        return self._dispatch_gui_patch(
            "_v46_on_ytdlp_progress",
            vid_id, idx, total, pct, speed,
            fallback=self._on_ytdlp_progress_base,
        )

    def _retry_failed_images(self) -> None:
        """
        Read failed_images.txt from output folder, re-download each image,
        and patch the corresponding project JSON(s) in the same folder.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox
        import glob

        outdir = os.path.abspath(self._outdir_var.get() or os.getcwd())
        fail_logs = glob.glob(os.path.join(outdir, "**", "failed_images.txt"), recursive=True)

        if not fail_logs:
            messagebox.showinfo("Retry Failed Images",
                                f"failed_images.txt tidak ditemukan di:\n{outdir}")
            return

        # Parse every per-project failed_images.txt. Batch/folder downloads
        # write one report inside each CYOA directory, not only at outdir.
        failed_urls: List[str] = []
        for fail_log in fail_logs:
            try:
                with open(fail_log, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            url = line.split("\t")[0].strip()
                            if url.startswith("http") and url not in failed_urls:
                                failed_urls.append(url)
            except OSError as exc:
                logger.warning("Cannot read %s during image retry: %s", fail_log, exc)

        if not failed_urls:
            messagebox.showinfo("Retry Failed Images",
                                "No failed image URLs found in failed_images.txt")
            return

        # Find project JSON files recursively. ICC-folder downloads keep their
        # project.json below a per-CYOA directory, so checking only outdir
        # silently made Retry Images a no-op for those downloads.
        json_files: List[str] = []
        for root, _dirs, files in os.walk(outdir):
            for name in files:
                if not name.endswith(".json") or name.endswith("_metadata.json"):
                    continue
                if name in {"download_state.json", "download_history.json"}:
                    continue
                json_files.append(os.path.join(root, name))

        if not json_files:
            messagebox.showinfo("Retry Failed Images",
                                f"Tidak ada .json project ditemukan di:\n{outdir}")
            return

        if self._is_running:
            messagebox.showwarning("Retry Failed Images",
                                   "Please wait for the current download to finish.")
            return

        logger.info(f"[Retry Images] {len(failed_urls)} gambar gagal, "
                    f"{len(json_files)} project JSON ditemukan.")

        def _do_retry():
            import base64, mimetypes
            headers = {"User-Agent": "Mozilla/5.0"}
            patched_total = 0
            embedded_by_url: Dict[str, str] = {}

            for json_path in json_files:
                try:
                    # Use a context manager so the file handle is
                    # released deterministically even on non-CPython runtimes.
                    with open(json_path, encoding="utf-8",
                              errors="replace") as _jf:
                        project_str = _jf.read()
                except Exception as e:
                    logger.warning(f"  Cannot read {json_path}: {e}")
                    continue

                changed = False
                for url in failed_urls:
                    if url not in project_str:
                        continue
                    cached_data_uri = embedded_by_url.get(url)
                    if cached_data_uri:
                        project_str = project_str.replace(url, cached_data_uri)
                        project_str = project_str.replace(url.replace("/", "\\/"), cached_data_uri)
                        changed = True
                        patched_total += 1
                        logger.info("  Reused retry image: %s", os.path.basename(url))
                        continue
                    # Try to download
                    r = None
                    try:
                        r = fetch_response(url, extra_headers=headers, timeout=30)
                        if r is None:
                            raise RuntimeError("download failed")
                        r.raise_for_status()
                        mime  = r.headers.get("Content-Type", "").split(";")[0].strip()
                        if not mime:
                            mime = mimetypes.guess_type(url)[0] or "image/webp"
                        if not mime.startswith("image/"):
                            raise RuntimeError(f"response is not an image ({mime})")
                        content = r.content
                        b64   = base64.b64encode(content).decode()
                        new_  = f"data:{mime};base64,{b64}"
                        embedded_by_url[url] = new_
                        # Handle both normal JSON URLs and JSON-escaped
                        # slashes emitted by some minified viewers.
                        project_str = project_str.replace(url, new_)
                        project_str = project_str.replace(url.replace("/", "\\/"), new_)
                        logger.info(f"  ✓ Re-embedded: {os.path.basename(url)}")
                        changed = True
                        patched_total += 1
                    except Exception as e:
                        logger.warning(f"  ✗ Still failing: {url[:60]} — {e}")

                    finally:
                        if r is not None:
                            try:
                                r.close()
                            except Exception:
                                pass

                if changed:
                    try:
                        with open(json_path, "w", encoding="utf-8") as fout:
                            fout.write(project_str)
                        logger.info(f"  Updated: {os.path.basename(json_path)}")
                    except Exception as e:
                        logger.error(f"  Write failed for {json_path}: {e}")

            if patched_total:
                logger.info(f"[Retry Images] Done — {patched_total} gambar berhasil di-embed.")
            else:
                logger.warning(f"[Retry Images] No images were successfully downloaded.")

        import threading
        threading.Thread(target=_do_retry, daemon=True).start()

    def _retry_failed(self) -> None:
        """Re-queue all failed items so they can be re-downloaded."""
        if self._is_running:
            return
        if not self._last_results:
            from tkinter import messagebox
            messagebox.showinfo("Retry Failed", "No results yet. Run a download first.")
            return
        failed_urls = {r["url"] for r in self._last_results if r["status"] != "OK"}
        if not failed_urls:
            from tkinter import messagebox
            messagebox.showinfo("Retry Failed", "No failed items found.")
            return
        # Remove failed URLs from resume state so they get retried
        try:
            outdir = self._outdir_var.get()
            state  = load_resume_state(outdir)
            state["completed"] = [u for u in state["completed"] if u not in failed_urls]
            save_resume_state(outdir, state["completed"], [])
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _retry_failed (line 8678): %s", _ignored_exc)
        # Reset dot for failed items
        for i, item in enumerate(self._queue_data):
            if item["url"] in failed_urls:
                self._set_dot(i, "idle")
        # Clear failed from results so they show as fresh
        for r in self._last_results:
            if r["url"] in failed_urls:
                r["status"] = "PENDING"
        logger.info(f"[Retry] {len(failed_urls)} item gagal di-reset — memulai ulang download")
        self._start()

    def _remove_urls_from_queue(self, urls: Set[str]) -> int:
        """Remove only matching queue rows, preserving newer user-added rows."""
        if not urls:
            return 0
        removed = 0
        for idx in range(len(self._queue_data) - 1, -1, -1):
            try:
                if str(self._queue_data[idx].get("url", "")) in urls:
                    self._remove_row(idx)
                    removed += 1
            except Exception as e:
                logger.debug(f"[Queue] Could not remove completed row {idx}: {e}")
        return removed

    def _remove_queue_ids_from_queue(self, queue_ids: Set[str]) -> int:
        """Remove only the exact queue rows from a completed run snapshot."""
        if not queue_ids:
            return 0
        removed = 0
        for idx in range(len(self._queue_data) - 1, -1, -1):
            try:
                if str(self._queue_data[idx].get("_queue_id", "")) in queue_ids:
                    self._remove_row(idx)
                    removed += 1
            except Exception as e:
                logger.debug(f"[Queue] Could not remove queue row {idx}: {e}")
        return removed

    def _done_base(self) -> None:
        self._is_running = False
        self._paused.set()   # ensure unpaused for next run
        self._pause_btn.configure(text="⏸ Pause", state="disabled")
        self._pb.stop()
        self._stop_speed_graph()
        self._dl_btn.configure(state="normal")
        # Desktop notification
        status = self._status_var.get()
        _send_desktop_notification("CYOA Downloader", status)
        # Only remove rows that belonged to the completed run snapshot.
        # If the user added new URLs while the worker was running, those rows
        # were not part of this run and must stay queued for the next run.
        try:
            succeeded = int(status.split("—")[1].strip().split("/")[0].strip())
            total     = int(status.split("/")[1].strip().split(" ")[0])
            if succeeded < total:
                logger.info(f"[Queue] {total - succeeded} item gagal — queue tidak di-clear")
                self._remove_queue_ids_from_queue(
                    set(getattr(self, "_active_run_success_ids", set()))
                )
                self._active_run_urls = set()
                self._active_run_queue_ids = set()
                self._active_run_success_ids = set()
                return
        except Exception as _ignored_exc:
            # Conservative on unparseable status. Previously
            # a status string that didn't match the "… — N/M …" shape (localized
            # text, error status, format drift) let the IndexError/ValueError be
            # swallowed and execution FELL THROUGH to the row-removal/clear path
            # below — silently defeating the failure-preservation safeguard a few
            # lines up (a failed run could have its queue cleared). When we can't
            # confirm every item succeeded, we must NOT clear: preserve the queue
            # and return, matching the partial-failure branch above.
            logger.debug("Ignored recoverable exception in _done (line 8725): %s", _ignored_exc)
            self._active_run_urls = set()
            self._active_run_queue_ids = set()
            self._active_run_success_ids = set()
            return

        active_ids = set(getattr(self, "_active_run_success_ids", set()))
        if active_ids:
            removed = self._remove_queue_ids_from_queue(active_ids)
            if self._queue_data:
                logger.info(
                    f"[Queue] Run selesai — {removed} item selesai dihapus; "
                    f"{len(self._queue_data)} item baru tetap di queue")
            else:
                logger.info(f"[Queue] Run selesai — {removed} item selesai dihapus")
        else:
            self._clear_queue()
        self._active_run_urls = set()
        self._active_run_queue_ids = set()
        self._active_run_success_ids = set()

    def _done(self) -> None:
        return self._dispatch_gui_patch("_v46_done", fallback=self._done_base)

    def _show_results(self) -> None:
        """Show popup with per-item download results table."""
        import customtkinter as ctk
        import tkinter as tk

        if not self._last_results:
            from tkinter import messagebox
            messagebox.showinfo("Results", "No results yet. Run a download first.")
            return

        p   = self._p()
        win = self._make_singleton_window("reports_legacy")
        if win is None:
            return
        win.title("Batch Download Results")
        win.geometry("900x520")
        win.grab_set()

        # Header stats
        total   = len(self._last_results)
        ok_cnt  = sum(1 for r in self._last_results if r["status"] == "OK")
        fail_cnt= total - ok_cnt

        hdr = ctk.CTkFrame(win, corner_radius=0, fg_color=p["panel"])
        hdr.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(hdr, text="Hasil Download Batch",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(side="left", padx=16, pady=12)

        stats_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        stats_frame.pack(side="right", padx=16, pady=8)

        for label, val, color in [
            (f"Total: {total}", "", p["muted"]),
            (f"✓ {ok_cnt} berhasil", "", "#22c55e"),
            (f"✗ {fail_cnt} gagal", "", "#ef4444"),
        ]:
            ctk.CTkLabel(stats_frame, text=label,
                         font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=color).pack(side="left", padx=8)

        # Separator
        ctk.CTkFrame(win, height=1, fg_color=p["border"], corner_radius=0).pack(fill="x")

        # Filter buttons
        filter_frame = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        filter_frame.pack(fill="x", padx=0)
        self._result_filter = ctk.StringVar(value="all")

        def set_filter(v):
            self._result_filter.set(v)
            refresh_table()

        for label, val in [("All", "all"), ("Success ✓", "ok"), ("Failed ✗", "fail")]:
            ctk.CTkButton(filter_frame, text=label, height=28,
                          font=ctk.CTkFont("Segoe UI", 10),
                          fg_color="#3b82f6" if val == "all" else p["surface2"],
                          hover_color="#2563eb",
                          text_color="#fff" if val == "all" else p["muted"],
                          command=lambda v=val: set_filter(v)).pack(
                side="left", padx=(8 if label == "Semua" else 4, 0), pady=6)

        # Export button
        def export_csv():
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
                initialfile="download_results.csv")
            if not path:
                return
            import csv as csv_mod
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv_mod.DictWriter(f, fieldnames=["status","url","mode","filename","error"])
                w.writeheader()
                w.writerows(self._last_results)
            logger.info(f"Results exported: {path}")

        ctk.CTkButton(filter_frame, text="Export CSV", height=28,
                      font=ctk.CTkFont("Segoe UI", 10),
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"], border_width=1,
                      border_color=p["surface2"],
                      command=export_csv).pack(side="right", padx=8, pady=6)

        # Table
        tbl_frame = ctk.CTkScrollableFrame(
            win, corner_radius=0, fg_color=p["bg"],
            scrollbar_button_color=p["surface2"])
        tbl_frame.pack(fill="both", expand=True, padx=0, pady=0)
        tbl_frame.grid_columnconfigure(1, weight=1)
        tbl_frame.grid_columnconfigure(3, weight=1)

        # Column headers
        COL_W = [50, 350, 100, 200, 0]
        for ci, (htext, w) in enumerate(zip(
            ["Status", "URL", "Mode", "Filename", "Error"], COL_W)):
            ctk.CTkLabel(tbl_frame, text=htext,
                         font=ctk.CTkFont("Segoe UI", 9, "bold"),
                         text_color=p["muted"], anchor="w",
                         width=w if w else 0).grid(
                row=0, column=ci, sticky="w", padx=(12 if ci == 0 else 4, 4), pady=(8, 4))

        ctk.CTkFrame(tbl_frame, height=1, fg_color=p["border"],
                     corner_radius=0).grid(row=1, column=0, columnspan=5,
                                           sticky="ew", padx=8, pady=0)

        row_widgets_table = []

        def refresh_table():
            for w in row_widgets_table:
                w.destroy()
            row_widgets_table.clear()
            flt = self._result_filter.get()
            rows = [r for r in self._last_results
                    if flt == "all"
                    or (flt == "ok"   and r["status"] == "OK")
                    or (flt == "fail" and r["status"] != "OK")]
            for ri, r in enumerate(rows):
                is_ok  = r["status"] == "OK"
                row_bg = p["bg"] if ri % 2 == 0 else p["surface"]

                def make_lbl(text, col, color=None, mono=False, anchor="w"):
                    lbl = ctk.CTkLabel(tbl_frame, text=text,
                                       font=ctk.CTkFont("Consolas" if mono else "Segoe UI", 9),
                                       text_color=color or p["fg"],
                                       fg_color=row_bg, anchor=anchor)
                    lbl.grid(row=ri+2, column=col, sticky="ew",
                             padx=(12 if col==0 else 4, 4), pady=2)
                    row_widgets_table.append(lbl)
                    return lbl

                make_lbl("✓" if is_ok else "✗", 0,
                         color="#22c55e" if is_ok else "#ef4444")
                make_lbl(r["url"], 1, mono=True)
                make_lbl(r["mode"].replace("_", " "), 2, color=p["muted"])
                make_lbl(r["filename"] or "[auto]", 3, color=p["muted"])
                if not is_ok:
                    make_lbl(r["error"][:80], 4, color="#f87171")
                else:
                    make_lbl("", 4)

        refresh_table()

    # ─ Local server ──────────────────────────────────────────────────────
    def _batch_export_panel(self) -> None:
        """
        Batch export multiple project.json files to CYOA Manager library.
        Supports: scan folder, pick individual files, from download history.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox
        import glob as _glob

        p   = self._p()
        s   = _load_settings()
        win = self._make_singleton_window("batch_export")
        if win is None:
            return
        win.title("Batch Export → CYOA Manager")
        win.geometry("600x520")
        win.grab_set()
        win.resizable(False, False)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=52)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📦  Batch Export → CYOA Manager",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=p["fg"]).pack(side="left", padx=16)

        body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # ── Source selection ──────────────────────────────────────────
        src_lbl = ctk.CTkLabel(body, text="Sumber file:",
                               font=ctk.CTkFont("Segoe UI", 10, "bold"),
                               text_color=p["muted"])
        src_lbl.pack(anchor="w", pady=(0, 4))

        count_var  = ctk.StringVar(value="0 file dipilih")
        status_var = ctk.StringVar()

        # File list display
        list_frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=8)
        list_frame.pack(fill="both", expand=True, pady=(0, 8))

        list_box = ctk.CTkTextbox(
            list_frame, height=220,
            font=ctk.CTkFont("Consolas", 9),
            fg_color=p["surface"], text_color=p["muted"],
            border_width=0,
        )
        list_box.pack(fill="both", expand=True, padx=6, pady=6)
        list_box.configure(state="disabled")

        _file_paths: List[str] = []

        def _refresh_list() -> None:
            list_box.configure(state="normal")
            list_box.delete("1.0", "end")
            for fp in _file_paths:
                list_box.insert("end", f"  {os.path.basename(fp)}\n")
                list_box.insert("end", f"    {fp}\n")
            list_box.configure(state="disabled")
            count_var.set(f"{len(_file_paths)} file dipilih")

        def _scan_folder() -> None:
            folder = filedialog.askdirectory(parent=win, title="Select output folder")
            if not folder:
                return
            found = _glob.glob(os.path.join(folder, "*.json")) + \
                    _glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)
            # Filter: only project-like JSON (not metadata, settings, etc.)
            skip = {"settings.json", "download_history.json",
                    "backup_report.txt", "viewers.json"}
            found = [f for f in found
                     if os.path.basename(f) not in skip
                     and os.path.getsize(f) > 1024]  # >1KB
            for fp in found:
                if fp not in _file_paths:
                    _file_paths.append(fp)
            _refresh_list()

        def _pick_files() -> None:
            paths = filedialog.askopenfilenames(
                parent=win, title="Pilih project.json",
                filetypes=[("JSON", "*.json"), ("All", "*.*")])
            for fp in paths:
                if fp not in _file_paths:
                    _file_paths.append(fp)
            _refresh_list()

        def _from_session() -> None:
            results  = getattr(self, "_last_results", [])
            outdir   = self._outdir_var.get() or os.getcwd()
            for r in results:
                if r.get("status") != "OK":
                    continue
                jp = os.path.join(outdir, r.get("filename", "") + ".json")
                if os.path.exists(jp) and jp not in _file_paths:
                    _file_paths.append(jp)
            _refresh_list()

        def _clear() -> None:
            _file_paths.clear()
            _refresh_list()

        # Source buttons
        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))
        for text, cmd in [
            ("📂 Scan Folder",      _scan_folder),
            ("📄 Pick Files",       _pick_files),
            ("📋 From This Session", _from_session),
            ("🗑 Clear All",        _clear),
        ]:
            ctk.CTkButton(btn_row, text=text, height=30,
                          fg_color=p["surface2"], hover_color=p["surface"],
                          text_color=p["muted"],
                          font=ctk.CTkFont("Segoe UI", 10),
                          command=cmd).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(body, textvariable=count_var,
                     font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["fg"]).pack(anchor="w")

        # ── Export button ─────────────────────────────────────────────
        prog_var = ctk.DoubleVar(value=0)
        prog_bar = ctk.CTkProgressBar(body, variable=prog_var, height=8,
                                       fg_color=p["surface2"],
                                       progress_color="#3b82f6")
        prog_bar.pack(fill="x", pady=(8, 4))

        ctk.CTkLabel(body, textvariable=status_var,
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["accent"]).pack(anchor="w")

        def _do_export() -> None:
            if not _file_paths:
                messagebox.showwarning("Empty", "Select files first.", parent=win)
                return
            custom = s.get("cyoa_mgr_db_path", "").strip()
            db = (custom if custom and os.path.exists(custom)
                  else _find_cyoa_manager_db())
            if not db:
                messagebox.showerror(
                    "DB Not Found",
                    "Open 📤 CYOA Manager to set the library.sqlite3 path.",
                    parent=win)
                return
            added = skipped = failed = 0
            total = len(_file_paths)
            for i, fp in enumerate(_file_paths):
                prog_var.set((i + 1) / total)
                win.update_idletasks()
                name = os.path.splitext(os.path.basename(fp))[0]
                ok = add_to_cyoa_manager(fp, name=name, db_path=db)
                if ok is True:
                    added += 1
                elif ok is None:
                    skipped += 1
                else:
                    failed += 1
            prog_var.set(1.0)
            status_var.set(
                f"✓ {added} ditambahkan  •  "
                f"{skipped} sudah ada  •  "
                f"{failed} gagal"
            )

        ctk.CTkButton(body, text="📤 Export to CYOA Manager", height=38,
                      fg_color="#3b82f6", hover_color="#2563eb",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=_do_export).pack(fill="x", pady=(8, 0))

    def _cyoa_manager_panel(self) -> None:
        """Consolidated CYOA Manager center.

        Import, export, auto-add, DB path, and manual add are intentionally
        kept in one window so the toolbar does not expose three overlapping
        CYOA Manager actions. The panel stays open after actions so users can
        confirm status or run the next operation without reopening it.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox

        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"
        s = _load_settings()
        win = self._make_singleton_window("cyoa_manager")
        if win is None:
            return
        win.title("CYOA Manager")
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = min(780, max(700, sw - 360)), min(640, max(560, sh - 220))
            x, y = max(24, (sw - w) // 2), max(24, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("740x600")
        win.minsize(680, 540)
        try:
            win.grab_set()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _cyoa_manager_panel (line 9087): %s", _ignored_exc)

        root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=58)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr,
            text=("📤  CYOA Manager Center" if is_en else "📤  Pusat CYOA Manager"),
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=p["fg"], anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(10, 0))
        ctk.CTkLabel(
            hdr,
            text=(
                "Import, export, auto-add, library path, and manual project registration."
                if is_en else
                "Import, ekspor, tambah otomatis, path library, dan registrasi project manual."
            ),
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=p["muted"], anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 8))

        body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        status_var = ctk.StringVar(value="")
        on_var = ctk.BooleanVar(value=bool(self._cyoa_mgr_var.get()))
        db_var = ctk.StringVar(value=(s.get("cyoa_mgr_db_path") or _find_cyoa_manager_db() or ""))

        def _set_status(text: str, ok: Optional[bool] = None) -> None:
            status_var.set(text)
            try:
                status_label.configure(text_color=(p["accent"] if ok is True else "#f59e0b" if ok is False else p["muted"]))
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _set_status (line 9129): %s", _ignored_exc)

        def _sync_toolbar_button() -> None:
            if hasattr(self, "_cm_btn") and self._cm_btn:
                p2 = self._p()
                enabled = bool(self._cyoa_mgr_var.get())
                self._cm_btn.configure(
                    text="📤  CYOA Mgr " + ("✓" if enabled else "✗"),
                    fg_color=p2["manager_bg"],
                    hover_color=p2["manager_hv"],
                    text_color=p2["manager_fg"],
                )

        def _get_db() -> Optional[str]:
            custom = (db_var.get() or s.get("cyoa_mgr_db_path", "") or "").strip()
            return custom if custom and os.path.exists(custom) else _find_cyoa_manager_db()

        def _apply_toggle(v: bool) -> None:
            on_var.set(bool(v))
            self._cyoa_mgr_var.set(bool(v))
            s["cyoa_mgr_enabled"] = bool(v)
            _update_setting("cyoa_mgr_enabled", bool(v))
            _sync_toolbar_button()
            _refresh_toggle_buttons()
            _set_status(("Auto-add enabled." if v else "Auto-add disabled.") if is_en else ("Tambah otomatis aktif." if v else "Tambah otomatis nonaktif."), True)

        def _refresh_toggle_buttons() -> None:
            v = bool(on_var.get())
            on_btn.configure(fg_color="#16a34a" if v else p["surface2"], text_color="#ffffff" if v else p["muted"])
            off_btn.configure(fg_color="#dc2626" if not v else p["surface2"], text_color="#ffffff" if not v else p["muted"])

        # Auto-add card
        auto = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12, border_width=1, border_color=p["border"])
        auto.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 8))
        auto.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(auto, text="📚", font=ctk.CTkFont("Segoe UI Emoji", 18), text_color=p["manager_fg"]).grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=12)
        ctk.CTkLabel(auto, text=("Auto-add successful downloads" if is_en else "Tambah otomatis hasil download"),
                     font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 1))
        ctk.CTkLabel(auto, text=("When enabled, successful project.json outputs are registered into CYOA Manager." if is_en else "Jika aktif, output project.json yang berhasil akan didaftarkan ke CYOA Manager."),
                     font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", wraplength=460).grid(row=1, column=1, sticky="ew", pady=(0, 12))
        trow = ctk.CTkFrame(auto, fg_color="transparent")
        trow.grid(row=0, column=2, rowspan=2, sticky="e", padx=14, pady=12)
        on_btn = ctk.CTkButton(trow, text="ON", width=54, height=30, corner_radius=7, font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda: _apply_toggle(True))
        off_btn = ctk.CTkButton(trow, text="OFF", width=54, height=30, corner_radius=7, font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda: _apply_toggle(False))
        on_btn.pack(side="left", padx=(0, 5)); off_btn.pack(side="left")

        # DB path card
        db = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=12, border_width=1, border_color=p["border"])
        db.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        db.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(db, text="🗄", font=ctk.CTkFont("Segoe UI Emoji", 18), text_color=p["accent"]).grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=12)
        ctk.CTkLabel(db, text="library.sqlite3", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 2))
        db_entry = ctk.CTkEntry(db, textvariable=db_var, height=30, font=ctk.CTkFont("Consolas", 9),
                                fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"],
                                placeholder_text="Select or auto-detect library.sqlite3")
        db_entry.grid(row=1, column=1, sticky="ew", pady=(0, 12))

        def _save_db_path() -> None:
            val = (db_var.get() or "").strip()
            if val:
                s["cyoa_mgr_db_path"] = val
                _update_setting("cyoa_mgr_db_path", val)
            db_ok = bool(val and os.path.exists(val))
            _set_status(("DB path saved." if db_ok else "DB path saved, but file was not found.") if is_en else ("Path DB tersimpan." if db_ok else "Path DB tersimpan, tetapi file tidak ditemukan."), db_ok)

        def _browse_db() -> None:
            path = filedialog.askopenfilename(parent=win, title="Select library.sqlite3", filetypes=[("SQLite DB", "*.sqlite3"), ("All", "*.*")])
            if path:
                db_var.set(path)
                _save_db_path()

        ctk.CTkButton(db, text=("Browse" if is_en else "Pilih"), width=76, height=30, command=_browse_db,
                      fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"]).grid(row=0, column=2, padx=(10, 12), pady=(12, 2), sticky="e")
        ctk.CTkButton(db, text=("Save" if is_en else "Simpan"), width=76, height=30, command=_save_db_path,
                      fg_color="#2563eb", hover_color="#1d4ed8", text_color="#ffffff").grid(row=1, column=2, padx=(10, 12), pady=(0, 12), sticky="e")

        def _section(row: int, label: str) -> int:
            ctk.CTkLabel(body, text=label.upper(), font=ctk.CTkFont("Segoe UI", 10, "bold"),
                         text_color=p["accent"], anchor="w").grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=(12, 4))
            return row + 1

        def _action(row: int, col: int, icon: str, title: str, desc: str, cmd, *, color: str = "surface2", hover: str = "surface", fg: str = "fg") -> None:
            card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=10, border_width=1, border_color=p["border"])
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=5)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, width=28, font=ctk.CTkFont("Segoe UI Emoji", 16), text_color=p[fg] if fg in p else fg).grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=10, sticky="n")
            ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"]).grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(10, 1))
            ctk.CTkLabel(card, text=desc, anchor="w", justify="left", wraplength=280, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"]).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))
            ctk.CTkButton(card, text=("Open" if is_en else "Buka"), width=68, height=28,
                          fg_color=p[color] if color in p else color, hover_color=p[hover] if hover in p else hover,
                          text_color=p[fg] if fg in p else fg, font=ctk.CTkFont("Segoe UI", 10, "bold"),
                          command=cmd).grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=12)

        def _manual_add() -> None:
            json_path = filedialog.askopenfilename(parent=win, title=("Select project.json" if is_en else "Pilih project.json"), filetypes=[("JSON", "*.json"), ("All", "*.*")])
            if not json_path:
                return
            db_path = _get_db()
            if not db_path:
                messagebox.showerror("CYOA Manager", "Library DB not found." if is_en else "Library DB tidak ditemukan.", parent=win)
                return
            ok = add_to_cyoa_manager(json_path, name=os.path.splitext(os.path.basename(json_path))[0], db_path=db_path)
            _set_status(("Project added." if ok else "Add failed.") if is_en else ("Project ditambahkan." if ok else "Tambah project gagal."), ok)

        def _add_session() -> None:
            results = getattr(self, "_last_results", []) or []
            db_path = _get_db()
            if not db_path:
                messagebox.showerror("CYOA Manager", "Library DB is invalid." if is_en else "Library DB tidak valid.", parent=win)
                return
            added = 0
            for r in results:
                if r.get("status") != "OK":
                    continue
                fp = os.path.join(self._outdir_var.get() or os.getcwd(), r.get("filename", "") + ".json")
                if add_to_cyoa_manager(fp, name=r.get("filename", ""), source_url=r.get("url", ""), db_path=db_path):
                    added += 1
            _set_status((f"{added} session project(s) added." if is_en else f"{added} project sesi ditambahkan."), True)

        def _batch_export_folder() -> None:
            db_path = _get_db()
            if not db_path:
                messagebox.showerror("CYOA Manager", "Library DB is invalid." if is_en else "Library DB tidak valid.", parent=win)
                return
            folder = filedialog.askdirectory(parent=win, title=("Select folder containing project.json" if is_en else "Pilih folder berisi project.json"))
            if not folder:
                return
            added = 0
            for root_d, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d.lower() not in {"audio", "images", "css", "js", "fonts"}]
                for fname in files:
                    if fname.lower() not in {"project.json", "project.txt"}:
                        continue
                    fp = os.path.join(root_d, fname)
                    if add_to_cyoa_manager(fp, name=os.path.basename(root_d), db_path=db_path):
                        added += 1
            _set_status((f"{added} project file(s) found and added." if is_en else f"{added} file project ditemukan dan ditambahkan."), True)

        def _open_import_panel() -> None:
            """Open a child selector without closing/hiding the Manager Center."""
            projects = _list_cyoa_manager_projects(_get_db() or "")
            if not projects:
                messagebox.showinfo(
                    "CYOA Manager",
                    "CYOA Manager library not found or empty." if is_en else "Library CYOA Manager tidak ditemukan atau kosong.",
                    parent=win,
                )
                _set_status("No importable projects found." if is_en else "Tidak ada project yang bisa diimpor.", False)
                return
            child = ctk.CTkToplevel(win)
            self._apply_window_icon_to(child)
            child.title("Import from CYOA Manager" if is_en else "Impor dari CYOA Manager")
            child.geometry("720x520")
            child.configure(fg_color=p["bg"])
            child.transient(win)
            try:
                child.grab_set()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _open_import_panel (line 9286): %s", _ignored_exc)
            def _close_child() -> None:
                try:
                    child.grab_release()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _close_child (line 9291): %s", _ignored_exc)
                child.destroy()
                try:
                    win.lift(); win.focus_force()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _close_child (line 9296): %s", _ignored_exc)
            child.protocol("WM_DELETE_WINDOW", _close_child)
            ctk.CTkLabel(child, text=(f"CYOA Manager Library — {len(projects)} project(s)" if is_en else f"Library CYOA Manager — {len(projects)} project"), font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"]).pack(anchor="w", padx=16, pady=(14, 4))
            ctk.CTkLabel(child, text=("Select projects to add to the current queue. The Manager Center stays open." if is_en else "Pilih project untuk ditambahkan ke antrean. Pusat Manager tetap terbuka."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"]).pack(anchor="w", padx=16, pady=(0, 8))
            search_var = ctk.StringVar()
            ctk.CTkEntry(child, textvariable=search_var, placeholder_text=("🔍 Search name or URL…" if is_en else "🔍 Cari nama atau URL…"), height=32, fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"]).pack(fill="x", padx=16, pady=(0, 8))
            lf = ctk.CTkScrollableFrame(child, fg_color=p["surface"], corner_radius=10, border_width=1, border_color=p["border"])
            lf.pack(fill="both", expand=True, padx=16, pady=(0, 10))
            check_vars = []
            def _rebuild(*_):
                for wdg in lf.winfo_children():
                    wdg.destroy()
                check_vars.clear()
                ft = (search_var.get() or "").lower().strip()
                for proj in projects:
                    name = proj.get("name") or proj.get("id") or "-"
                    url = proj.get("source_url", "")
                    if ft and ft not in name.lower() and ft not in url.lower():
                        continue
                    var = ctk.BooleanVar(value=False)
                    row = ctk.CTkFrame(lf, fg_color=p["surface2"], corner_radius=8)
                    row.pack(fill="x", padx=5, pady=4)
                    ctk.CTkCheckBox(row, text="", variable=var, width=22, fg_color=p["manager_bg"], hover_color=p["manager_hv"], border_color=p["border"]).pack(side="left", padx=(8, 6), pady=8)
                    meta = ctk.CTkFrame(row, fg_color="transparent")
                    meta.pack(side="left", fill="x", expand=True, pady=6)
                    ctk.CTkLabel(meta, text=name[:80], anchor="w", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=p["fg"]).pack(anchor="w")
                    ctk.CTkLabel(meta, text=url[:110], anchor="w", font=ctk.CTkFont("Consolas", 9), text_color=p["muted"]).pack(anchor="w")
                    check_vars.append((var, proj))
            _rebuild()
            search_var.trace_add("write", _rebuild)
            bf = ctk.CTkFrame(child, fg_color=p["panel"], height=44, corner_radius=0)
            bf.pack(fill="x", side="bottom")
            def _select_all() -> None:
                for v, _p in check_vars:
                    v.set(True)
            def _queue_selected() -> None:
                queued = 0
                for v, proj in check_vars:
                    if v.get():
                        self._add_url_to_queue(proj.get("source_url", ""), filename=proj.get("name", ""))
                        queued += 1
                if queued:
                    _set_status((f"{queued} project(s) queued from CYOA Manager." if is_en else f"{queued} project ditambahkan ke antrean dari CYOA Manager."), True)
                    _close_child()
                else:
                    _set_status("Select at least one project first." if is_en else "Pilih minimal satu project dulu.", False)
            ctk.CTkButton(bf, text=("Select All" if is_en else "Pilih Semua"), width=100, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_select_all).pack(side="left", padx=(16, 6), pady=7)
            ctk.CTkButton(bf, text=("Queue Selected" if is_en else "Masukkan Antrean"), width=140, height=30, fg_color=p["manager_bg"], hover_color=p["manager_hv"], text_color=p["manager_fg"], command=_queue_selected).pack(side="left", padx=(0, 6), pady=7)
            ctk.CTkButton(bf, text=("Close" if is_en else "Tutup"), width=86, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_close_child).pack(side="right", padx=16, pady=7)

        def _open_batch_export_panel() -> None:
            """Export selected project files directly from the Manager Center."""
            db_path = _get_db()
            if not db_path:
                messagebox.showerror("CYOA Manager", "Library DB is invalid." if is_en else "Library DB tidak valid.", parent=win)
                return
            files = filedialog.askopenfilenames(
                parent=win,
                title=("Select project.json/project.txt files" if is_en else "Pilih file project.json/project.txt"),
                filetypes=[("Project files", "project.json project.txt *.json *.txt"), ("All files", "*.*")],
            )
            if not files:
                return
            added = skipped = failed = 0
            for fp in files:
                name = os.path.splitext(os.path.basename(fp))[0]
                ok = add_to_cyoa_manager(fp, name=name, db_path=db_path)
                if ok is True:
                    added += 1
                elif ok is None:
                    skipped += 1
                else:
                    failed += 1
            _set_status((f"Export done: {added} added, {skipped} existing, {failed} failed." if is_en else f"Ekspor selesai: {added} ditambah, {skipped} sudah ada, {failed} gagal."), failed == 0)

        r = 2
        r = _section(r, "Library actions" if is_en else "Aksi library")
        _action(r, 0, "📚", "Import from CYOA Manager" if is_en else "Impor dari CYOA Manager",
                "Pull projects from an existing CYOA Manager library into the queue." if is_en else "Ambil project dari library CYOA Manager ke antrean.",
                _open_import_panel, color="manager_bg", hover="manager_hv", fg="manager_fg")
        _action(r, 1, "📦", "Export to CYOA Manager" if is_en else "Ekspor ke CYOA Manager",
                "Export finished downloads or selected project.json files to the library." if is_en else "Ekspor hasil download atau project.json terpilih ke library.",
                _open_batch_export_panel, color="#6d28d9", hover="#7c3aed", fg="#ede9fe")
        r += 1
        _action(r, 0, "📄", "Add project.json" if is_en else "Tambah project.json",
                "Register one local project.json/project.txt manually." if is_en else "Daftarkan satu project.json/project.txt lokal secara manual.",
                _manual_add, color="#2563eb", hover="#1d4ed8", fg="#ffffff")
        _action(r, 1, "📋", "Add current session" if is_en else "Tambah sesi aktif",
                "Register successful downloads from the latest run." if is_en else "Daftarkan download berhasil dari run terakhir.",
                _add_session)
        r += 1
        _action(r, 0, "📁", "Scan folder for project.json" if is_en else "Scan folder project.json",
                "Recursively find project.json/project.txt and register them." if is_en else "Cari project.json/project.txt secara rekursif lalu daftarkan.",
                _batch_export_folder)

        status_label = ctk.CTkLabel(root, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 10),
                                    text_color=p["muted"], anchor="w")
        status_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        footer = ctk.CTkFrame(root, fg_color=p["panel"], height=42, corner_radius=0)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=90,
                      fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
                      command=win.destroy).grid(row=0, column=1, padx=14, pady=8)

        db_ok = bool(db_var.get() and os.path.exists(db_var.get()))
        _refresh_toggle_buttons()
        _set_status(("DB found." if db_ok else "DB not found. Select library.sqlite3 or install/open CYOA Manager once.") if is_en else ("DB ditemukan." if db_ok else "DB tidak ditemukan. Pilih library.sqlite3 atau buka CYOA Manager sekali."), db_ok)

    def _show_cookie_guide(self) -> None:
        """Open Panduan feature guide on the Cookie tab."""
        self._show_feature_guide(initial_tab="cookie")

    # ── Pause / Continue ────────────────────────────────────────────────
    def _toggle_pause(self) -> None:
        if not self._is_running:
            return
        if self._paused.is_set():
            self._paused.clear()   # pause
            self._pause_btn.configure(text="▶ Continue")
            logger.info("[Pause] Download paused by user")
        else:
            self._paused.set()     # continue
            self._pause_btn.configure(text="⏸ Pause")
            logger.info("[Pause] Download continued")

    # ── Cache Manager ──────────────────────────────────────────────────
    def _export_settings_dialog(self) -> None:
        """GUI: export redacted settings to a chosen file (v1.0 Release Feature #1)."""
        from tkinter import filedialog, messagebox
        try:
            path = filedialog.asksaveasfilename(
                title="Export Settings",
                defaultextension=".json",
                initialfile="cyoa_settings_export.json",
                filetypes=[("JSON", "*.json")])
            if not path:
                return
            ok, msg = export_settings(path)
            logger.info(msg)
            (messagebox.showinfo if ok else messagebox.showerror)("Export Settings", msg)
        except Exception as e:
            logger.warning(f"Export settings dialog failed: {e}")
            try:
                messagebox.showerror("Export Settings", f"Failed: {e}")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _export_settings_dialog (line 9442): %s", _ignored_exc)

    def _import_settings_dialog(self) -> None:
        """GUI: merge settings from a prior export (secrets ignored)."""
        from tkinter import filedialog, messagebox
        try:
            path = filedialog.askopenfilename(
                title="Import Settings",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")])
            if not path:
                return
            ok, msg = import_settings(path)
            logger.info(msg)
            if ok:
                messagebox.showinfo(
                    "Import Settings",
                    msg + "\n\nSome changes may require reopening panels or "
                          "restarting the app to take full effect.")
            else:
                messagebox.showerror("Import Settings", msg)
        except Exception as e:
            logger.warning(f"Import settings dialog failed: {e}")
            try:
                messagebox.showerror("Import Settings", f"Failed: {e}")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _import_settings_dialog (line 9467): %s", _ignored_exc)

    def _cache_manager_panel(self) -> None:
        import customtkinter as ctk
        p = self._p()
        win = self._make_singleton_window("cache_manager")
        if win is None:
            return
        win.title("💾 Cache Manager")
        win.geometry("380x220")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        stats = _cache_stats()
        info_var = ctk.StringVar(
            value=f"Cached images: {stats['entries']}\n"
                  f"Disk usage: ~{stats['size_mb']} MB"
        )
        ctk.CTkLabel(win, textvariable=info_var,
                     font=ctk.CTkFont("Segoe UI", 13),
                     text_color=p["fg"], justify="left").pack(padx=20, pady=(20, 10))

        ctk.CTkLabel(win,
                     text="Cache speeds up re-downloading the same images.\n"
                          "Clear hanya jika perlu menghemat disk space.",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], wraplength=340,
                     justify="left").pack(padx=20, pady=(0, 14))

        def _do_clear():
            n = _clear_image_cache()
            info_var.set(f"Cache cleared — {n} file(s) removed.\n"
                         f"Cached images: 0\nDisk usage: 0 MB")

        ctk.CTkButton(win, text="🗑  Clear Image Cache", height=36,
                      fg_color=p["danger_bg"], hover_color=p["danger_hv"],
                      text_color=p["danger_fg"],
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=_do_clear).pack(padx=20, pady=(0, 10))

        ctk.CTkButton(win, text="Close", height=30,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      command=win.destroy).pack(padx=20, pady=(0, 14))

    # ── CYOA Manager Import (Infaera list download) ────────────────────
    def _import_from_cyoa_manager_panel(self) -> None:
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        projects = _list_cyoa_manager_projects()
        if not projects:
            messagebox.showinfo(
                "CYOA Manager Import",
                "CYOA Manager library not found or empty.\n"
                "Make sure CYOA Manager is installed and has projects in the library."
            )
            return

        win = self._make_singleton_window("cyoa_manager_import_legacy")
        if win is None:
            return
        win.title("📚 Import from CYOA Manager")
        win.geometry("620x480")
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        ctk.CTkLabel(
            win, text=f"CYOA Manager Library — {len(projects)} project(s) with URL",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=p["fg"]
        ).pack(padx=14, pady=(14, 6))

        # Search bar
        search_var = ctk.StringVar()
        ctk.CTkEntry(
            win, textvariable=search_var,
            placeholder_text="🔍 Search by name…",
            font=ctk.CTkFont("Segoe UI", 11),
            fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32
        ).pack(padx=14, fill="x", pady=(0, 6))

        lf = ctk.CTkScrollableFrame(win, fg_color=p["surface2"], corner_radius=8)
        lf.pack(padx=14, pady=(0, 8), fill="both", expand=True)

        check_vars: List[tuple] = []  # (BooleanVar, project_dict)

        def _rebuild(filter_text=""):
            for w in lf.winfo_children():
                w.destroy()
            check_vars.clear()
            ft = filter_text.lower()
            for proj in projects:
                name = proj.get("name", proj.get("id", ""))
                if ft and ft not in name.lower() and ft not in proj.get("source_url", "").lower():
                    continue
                var = ctk.BooleanVar(value=False)
                row = ctk.CTkFrame(lf, fg_color="transparent", corner_radius=0)
                row.pack(fill="x", padx=2, pady=1)
                ctk.CTkCheckBox(
                    row, text="", variable=var, width=24, height=24,
                    fg_color=p["accentbg"], hover_color=p["accentbg_hv"],
                    border_color=p["border"], checkmark_color=p["accent"],
                ).pack(side="left", padx=(4, 6), pady=2)
                tf = ctk.CTkFrame(row, fg_color="transparent")
                tf.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(
                    tf, text=name[:60], anchor="w",
                    font=ctk.CTkFont("Segoe UI", 11),
                    text_color=p["fg"]
                ).pack(anchor="w")
                ctk.CTkLabel(
                    tf, text=proj.get("source_url", "")[:70], anchor="w",
                    font=ctk.CTkFont("Segoe UI", 9),
                    text_color=p["muted"]
                ).pack(anchor="w")
                check_vars.append((var, proj))

        _rebuild()
        search_var.trace_add("write", lambda *_: _rebuild(search_var.get()))

        # Bottom buttons
        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(padx=14, pady=(0, 14), fill="x")

        def _select_all():
            for v, _ in check_vars:
                v.set(True)

        def _queue_selected():
            queued = 0
            for v, proj in check_vars:
                if v.get():
                    self._add_url_to_queue(proj["source_url"],
                                           filename=proj.get("name", ""))
                    queued += 1
            if queued:
                win.destroy()
                messagebox.showinfo("Queued", f"{queued} CYOA ditambahkan ke queue.")
            else:
                messagebox.showwarning("Import", "Select at least 1 CYOA.")

        ctk.CTkButton(
            bf, text="Select All", height=30, width=90,
            font=ctk.CTkFont("Segoe UI", 10),
            fg_color=p["surface2"], hover_color=p["surface"],
            text_color=p["fg"], command=_select_all
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bf, text="📥 Queue Selected", height=30,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            fg_color=p["accentbg"], hover_color=p["accentbg_hv"],
            text_color=p["accent"], command=_queue_selected
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bf, text="Close", height=30, width=70,
            fg_color=p["surface2"], hover_color=p["surface"],
            text_color=p["muted"], command=win.destroy
        ).pack(side="right")

    # ── Speed Graph (realtime download speed visualization) ────────────
    def _init_speed_graph(self) -> None:
        """Create a small Canvas speed graph below the progress bar."""
        import tkinter as tk
        p = self._p()
        # Canvas: 110px wide (same as progress bar), 32px tall
        self._speed_canvas = tk.Canvas(
            self._pb.master,  # same parent as progress bar (rowA)
            width=110, height=32,
            bg=p["surface2"], highlightthickness=0, bd=0
        )
        self._speed_canvas.grid(row=1, column=4, padx=(0, 12), pady=(0, 4))
        self._speed_label = tk.Label(
            self._pb.master,
            text="0 KB/s", font=("Segoe UI", 8),
            bg=p["panel"], fg=p["muted"],
            anchor="e"
        )
        self._speed_label.grid(row=1, column=3, padx=(0, 4), pady=(0, 4), sticky="e")
        self._speed_history: List[float] = []   # last 60 speed samples (KB/s)
        self._speed_bytes_acc = 0
        self._speed_timer_id = None

    def _record_speed_bytes_base(self, n_bytes: int) -> None:
        """Called from download threads to record bytes downloaded."""
        self._speed_bytes_acc += n_bytes

    def _record_speed_bytes(self, n_bytes: int) -> None:
        return self._dispatch_gui_patch(
            "_v46_record_speed_bytes",
            n_bytes,
            fallback=self._record_speed_bytes_base,
        )

    def _speed_graph_tick(self) -> None:
        """Called every 1s via root.after — updates speed history and redraws."""
        if not hasattr(self, "_speed_canvas"):
            return
        import tkinter as tk

        # Compute speed for this 1-second interval
        speed_kbs = self._speed_bytes_acc / 1024.0
        self._speed_bytes_acc = 0
        self._speed_history.append(speed_kbs)
        if len(self._speed_history) > 60:
            self._speed_history = self._speed_history[-60:]

        # Update label
        if speed_kbs >= 1024:
            self._speed_label.configure(text=f"{speed_kbs/1024:.1f} MB/s")
        else:
            self._speed_label.configure(text=f"{speed_kbs:.0f} KB/s")

        # Redraw canvas
        c = self._speed_canvas
        p = self._p()
        c.delete("all")
        c.configure(bg=p["surface2"])
        w, h = 110, 32
        data = self._speed_history
        if not data or max(data) == 0:
            if self._is_running:
                self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)
            return

        peak = max(data)
        n = len(data)
        step = w / max(n - 1, 1)
        points = []
        for i, v in enumerate(data):
            x = i * step
            y = h - (v / peak) * (h - 4) - 2
            points.append((x, y))

        # Fill area — use accent with low opacity simulation
        fill_color = p.get("accentbg", "#1e3a5f")
        fill_pts = [(0, h)] + points + [(w, h)]
        flat = [coord for pt in fill_pts for coord in pt]
        c.create_polygon(flat, fill=fill_color, outline="")

        # Line
        if len(points) >= 2:
            line_flat = [coord for pt in points for coord in pt]
            c.create_line(line_flat, fill=p.get("accent", "#60a5fa"), width=1.5, smooth=True)

        if self._is_running:
            self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)

    def _start_speed_graph_base(self) -> None:
        self._speed_history = []
        self._speed_bytes_acc = 0
        if not hasattr(self, "_speed_canvas"):
            self._init_speed_graph()
        self._speed_timer_id = self.root.after(1000, self._speed_graph_tick)

    def _start_speed_graph(self) -> None:
        return self._dispatch_gui_patch(
            "_v46_start_speed_graph",
            fallback=self._start_speed_graph_base,
        )

    def _stop_speed_graph_base(self) -> None:
        if hasattr(self, "_speed_timer_id") and self._speed_timer_id:
            self.root.after_cancel(self._speed_timer_id)
            self._speed_timer_id = None
        # Clear global speed callback
        _self_mod = sys.modules.get(__name__)
        if _self_mod is not None:
            _self_mod._gui_speed_cb = None

    def _stop_speed_graph(self) -> None:
        return self._dispatch_gui_patch(
            "_v46_stop_speed_graph",
            fallback=self._stop_speed_graph_base,
        )

    # ── AI API Key Settings ────────────────────────────────────────────
    def _ai_settings_panel(self) -> None:
        import customtkinter as ctk
        from tkinter import messagebox
        p = self._p()
        is_en = getattr(self, "_language", "id") == "en"
        win = self._make_singleton_window("ai_settings_legacy")
        if win is None:
            return
        win.title("🤖 AI Assist Settings" if is_en else "🤖 Pengaturan AI Assist")
        win.geometry("560x600")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        title = "AI Assist — Diagnostics & Recovery" if is_en else "AI Assist — Diagnostik & Pemulihan"
        ctk.CTkLabel(win, text=title,
            font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"]
        ).pack(padx=20, pady=(18, 4), anchor="w")

        desc_en = (
            "AI Assist is optional. It helps locate project.json, inspect JS bundles, "
            "and diagnose custom viewers when normal detection fails. API keys are not "
            "stored in settings.json unless you explicitly choose the plain-text option."
        )
        desc_id = (
            "AI Assist bersifat opsional. Fitur ini membantu mencari project.json, "
            "menganalisis bundle JS, dan mendiagnosis viewer custom saat deteksi normal gagal. "
            "API key tidak disimpan di settings.json kecuali Anda memilih opsi plain-text."
        )
        ctk.CTkLabel(win, text=desc_en if is_en else desc_id,
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            wraplength=510, justify="left"
        ).pack(padx=20, pady=(0, 12), anchor="w")

        toggle_var = ctk.BooleanVar(value=self._ai_enabled)
        ctk.CTkSwitch(win,
            text="Enable AI Assist" if is_en else "Aktifkan AI Assist",
            variable=toggle_var, font=ctk.CTkFont("Segoe UI", 11),
            text_color=p["fg"], progress_color="#8b5cf6"
        ).pack(padx=20, pady=(0, 12), anchor="w")

        grid = ctk.CTkFrame(win, fg_color="transparent")
        grid.pack(padx=20, fill="x", pady=(0, 8))
        grid.grid_columnconfigure(1, weight=1)

        def label(row, txt):
            ctk.CTkLabel(grid, text=txt, font=ctk.CTkFont("Segoe UI", 11, "bold"),
                         text_color=p["fg"], width=130, anchor="w").grid(row=row, column=0, sticky="w", pady=5)

        st = _load_settings()
        provider_var = ctk.StringVar(value=_normalize_ai_provider(st.get("ai_provider", "anthropic")))
        model_var = ctk.StringVar(value=_get_ai_model(provider_var.get()))
        mode_var = ctk.StringVar(value=_normalize_ai_mode(st.get("ai_mode", "auto_fallback")))
        storage_var = ctk.StringVar(value=_normalize_ai_key_storage(st.get("ai_key_storage", getattr(self, "_ai_key_storage", "session"))))
        session_key_var = ctk.StringVar(value=self._ai_api_key if storage_var.get() in {"session", "plain"} else "")

        label(0, "Provider" if is_en else "Provider")
        provider_menu = ctk.CTkOptionMenu(grid, variable=provider_var, values=["anthropic", "openai", "gemini", "ollama"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32)
        provider_menu.grid(row=0, column=1, sticky="ew", pady=5)

        label(1, "Model" if is_en else "Model")
        # CTkComboBox keeps curated presets but also lets advanced users type a custom model id.
        model_menu = ctk.CTkComboBox(grid, variable=model_var,
            values=_ai_model_options(provider_var.get()),
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            border_color=p["border"], height=32)
        model_menu.grid(row=1, column=1, sticky="ew", pady=5)

        label(2, "AI Mode" if is_en else "Mode AI")
        ctk.CTkOptionMenu(grid, variable=mode_var,
            values=["off", "diagnostics", "auto_fallback", "aggressive_recovery"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32).grid(row=2, column=1, sticky="ew", pady=5)

        label(3, "Key Storage" if is_en else "Penyimpanan Key")
        ctk.CTkOptionMenu(grid, variable=storage_var,
            values=["session", "env", "keyring", "plain"],
            fg_color=p["surface2"], button_color=p["surface2"], button_hover_color=p["surface"],
            text_color=p["fg"], dropdown_fg_color=p["surface2"], dropdown_text_color=p["fg"],
            height=32).grid(row=3, column=1, sticky="ew", pady=5)

        label(4, "API Key" if is_en else "API Key")
        key_entry = ctk.CTkEntry(grid, textvariable=session_key_var,
            font=ctk.CTkFont("Segoe UI", 11), fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32, show="•")
        key_entry.grid(row=4, column=1, sticky="ew", pady=5)

        label(5, "Ollama URL" if is_en else "URL Ollama")
        ollama_url_var = ctk.StringVar(value=st.get("ollama_url", OLLAMA_DEFAULT_URL))
        ollama_url_entry = ctk.CTkEntry(grid, textvariable=ollama_url_var,
            font=ctk.CTkFont("Segoe UI", 11), fg_color=p["surface2"], text_color=p["fg"],
            border_color=p["border"], height=32)
        ollama_url_entry.grid(row=5, column=1, sticky="ew", pady=5)

        status_var = ctk.StringVar(value=_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get()))
        status_lbl = ctk.CTkLabel(win, textvariable=status_var,
            font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"],
            wraplength=510, justify="left")
        status_lbl.pack(padx=20, pady=(0, 8), anchor="w")

        warn_var = ctk.StringVar(value="")
        warn_lbl = ctk.CTkLabel(win, textvariable=warn_var,
            font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color="#f59e0b",
            wraplength=510, justify="left")
        warn_lbl.pack(padx=20, pady=(0, 8), anchor="w")

        def _refresh_key_ui(*_):
            mode = _normalize_ai_key_storage(storage_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            if provider == "ollama":
                key_entry.configure(state="disabled", placeholder_text="Ollama uses local API")
                try: ollama_url_entry.configure(state="normal")
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 9848): %s", _ignored_exc)
                warn_var.set(("Ollama runs locally by default. Set the URL if your Ollama server uses a different host or port." if is_en else
                              "Ollama berjalan lokal secara default. Atur URL jika server Ollama memakai host atau port berbeda."))
            elif mode == "env":
                try: ollama_url_entry.configure(state="disabled")
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 9853): %s", _ignored_exc)
                key_entry.configure(state="disabled", placeholder_text=_ai_primary_env_var(provider_var.get()) or "No API key needed")
                warn_var.set((("Set " + " or ".join(_ai_env_vars(provider_var.get())) + " in your environment. The app will not store it.") if is_en else
                              ("Atur " + " atau ".join(_ai_env_vars(provider_var.get())) + " di environment. Aplikasi tidak akan menyimpannya.")))
            elif mode == "keyring":
                try: ollama_url_entry.configure(state="disabled")
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 9859): %s", _ignored_exc)
                key_entry.configure(state="normal", placeholder_text=("Enter key to save to OS Credential Manager" if is_en else "Masukkan key untuk disimpan ke OS Credential Manager"))
                warn_var.set(("Requires optional package: pip install keyring" if not _keyring_module() else
                              ("Key will be stored in the OS credential store." if is_en else "Key akan disimpan di credential store sistem operasi.")))
            elif mode == "plain":
                try: ollama_url_entry.configure(state="disabled")
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 9865): %s", _ignored_exc)
                key_entry.configure(state="normal", placeholder_text=("not needed" if _normalize_ai_provider(provider_var.get()) == "ollama" else "API key..."))
                warn_var.set(("Warning: this stores the API key as plain text in settings.json." if is_en else
                              "Peringatan: API key akan disimpan sebagai teks biasa di settings.json."))
            else:
                try: ollama_url_entry.configure(state="disabled")
                except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 9871): %s", _ignored_exc)
                key_entry.configure(state="normal", placeholder_text=("Session only. Cleared when app exits." if is_en else "Hanya sesi ini. Hilang saat aplikasi ditutup."))
                warn_var.set(("Safest default. The key stays in memory only." if is_en else
                              "Default paling aman. Key hanya tersimpan di memori."))
            status_var.set(_ai_key_status_text(mode, session_key_var.get(), provider_var.get()))

        storage_var.trace_add("write", _refresh_key_ui)

        def _provider_changed(*_):
            prov = _normalize_ai_provider(provider_var.get())
            opts = _ai_model_options(prov)
            try:
                model_menu.configure(values=opts)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _provider_changed (line 9884): %s", _ignored_exc)
            if model_var.get() not in opts:
                model_var.set(_default_ai_model(prov))
            mode = _normalize_ai_key_storage(storage_var.get())
            if prov == "ollama":
                session_key_var.set("")
            elif mode == "plain":
                session_key_var.set(_resolve_ai_api_key(storage="plain", provider=prov))
            elif mode in {"env", "keyring"}:
                session_key_var.set("")
            _refresh_key_ui()

        provider_var.trace_add("write", _provider_changed)
        session_key_var.trace_add("write", lambda *_: status_var.set(_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get())))
        _refresh_key_ui()

        def _test_key():
            provider = _normalize_ai_provider(provider_var.get())
            key = _resolve_ai_api_key(session_key=session_key_var.get(), storage=storage_var.get(), provider=provider)
            if provider != "ollama" and not key:
                messagebox.showwarning("AI Assist", "No API key available." if is_en else "API key belum tersedia.")
                return
            res = _ai_call(key, "Reply exactly: OK", max_tokens=16, label="AI key test", model=model_var.get(), provider=provider)
            if res:
                messagebox.showinfo("AI Assist", "API key works." if is_en else "API key berhasil digunakan.")
            else:
                messagebox.showerror("AI Assist", "API key test failed. Check key, model, and network." if is_en else "Tes API key gagal. Cek key, model, dan jaringan.")

        def _clear_key():
            mode = _normalize_ai_key_storage(storage_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            _clear_ai_api_key_storage(mode, provider, clear_all=True)
            session_key_var.set("")
            self._ai_api_key = ""
            status_var.set(_ai_key_status_text(mode, "", provider))

        def _save():
            mode = _normalize_ai_key_storage(storage_var.get())
            api_key = session_key_var.get().strip()
            settings = _load_settings()
            settings["ai_enabled"] = bool(toggle_var.get())
            provider = _normalize_ai_provider(provider_var.get())
            settings["ai_provider"] = provider
            settings["ai_model"] = model_var.get().strip() or _default_ai_model(provider)
            settings["ai_mode"] = _normalize_ai_mode(mode_var.get())
            settings["ai_key_storage"] = mode
            settings["ollama_url"] = (ollama_url_var.get().strip() or OLLAMA_DEFAULT_URL)

            if mode == "plain":
                if api_key:
                    ok = messagebox.askyesno(
                        "Plain text API key" if is_en else "API key teks biasa",
                        "This will store the API key as plain text in settings.json. Continue?" if is_en else
                        "API key akan disimpan sebagai teks biasa di settings.json. Lanjutkan?"
                    )
                    if not ok:
                        return
                _clear_ai_plain_keys(settings, None)
                settings[_plain_ai_key_setting(provider)] = api_key
                self._ai_api_key = api_key
            elif mode == "keyring":
                _clear_ai_plain_keys(settings, None)
                if api_key:
                    if not _write_ai_key_to_keyring(api_key, provider):
                        messagebox.showerror("AI Assist", "Failed to write to OS Credential Manager. Install keyring or choose another storage." if is_en else "Gagal menyimpan ke OS Credential Manager. Install keyring atau pilih storage lain.")
                        return
                self._ai_api_key = ""
            elif mode == "session":
                _clear_ai_plain_keys(settings, None)
                self._ai_api_key = api_key
            else:  # env
                _clear_ai_plain_keys(settings, None)
                self._ai_api_key = ""

            self._ai_enabled = bool(toggle_var.get())
            self._ai_provider = provider
            self._ai_key_storage = mode
            self._ai_model = settings["ai_model"]
            self._ai_mode = settings["ai_mode"]
            if hasattr(self, "_ai_var"):
                self._ai_var.set(self._ai_enabled)
            # Lock-safe: merge this full AI-config snapshot under the settings
            # lock so a concurrent single-key toggle elsewhere isn't dropped.
            _update_settings(dict(settings))
            if hasattr(self, '_ai_btn'):
                self._ai_btn.configure(
                    text="🤖 AI  " + ("ON" if self._ai_enabled else "OFF"),
                    text_color=p["accent"] if self._ai_enabled else p["muted"])
            win.destroy()

        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(padx=20, fill="x", pady=(8, 14))
        ctk.CTkButton(bf, text="Test API Key" if is_en else "Tes API Key", height=30,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_test_key
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bf, text="Clear Key" if is_en else "Hapus Key", height=30,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=_clear_key
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bf, text="💾 Save" if is_en else "💾 Simpan", height=30,
            font=ctk.CTkFont("Segoe UI", 11, "bold"), fg_color=p["accentbg"],
            hover_color=p["accentbg_hv"], text_color=p["accent"], command=_save
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bf, text="Cancel" if is_en else "Batal", height=30, width=70,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=win.destroy
        ).pack(side="right")

    # ── Auto-update Checker ────────────────────────────────────────────
    def _check_updates_panel(self) -> None:
        import customtkinter as ctk
        import webbrowser
        p = self._p()
        win = self._make_singleton_window("check_updates")
        if win is None:
            return
        win.title("🔄 Check for Updates")
        win.geometry("400x200")
        win.resizable(False, False)
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        status_lbl = ctk.CTkLabel(win, text="Checking…",
                                  font=ctk.CTkFont("Segoe UI", 13),
                                  text_color=p["fg"])
        status_lbl.pack(padx=20, pady=(20, 10))

        notes_lbl = ctk.CTkLabel(win, text="",
                                 font=ctk.CTkFont("Segoe UI", 10),
                                 text_color=p["muted"], wraplength=360,
                                 justify="left")
        notes_lbl.pack(padx=20, pady=(0, 10))

        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(0, 14))

        def _apply_check_result(info=None, error: str = ""):
            if error:
                status_lbl.configure(
                    text=f"CYOA Downloader v{_APP_VERSION}\n\n"
                         f"Update check failed: {error}")
                return
            if info == "__not_configured__":
                status_lbl.configure(
                    text=f"CYOA Downloader v{_APP_VERSION}\n\n"
                         "Auto-update not configured.\n"
                         "Set _GITHUB_RELEASE_API in the script to enable.")
                return
            if info:
                status_lbl.configure(
                    text=f"Update tersedia: v{info['version']} "
                         f"(current: v{_APP_VERSION})")
                notes_lbl.configure(text=info.get("notes", "")[:300])
                if info.get("url"):
                    ctk.CTkButton(btn_frame, text="Open Release Page",
                                  height=30,
                                  fg_color=p["accentbg"],
                                  hover_color=p["accentbg_hv"],
                                  text_color=p["accent"],
                                  command=lambda: webbrowser.open(info["url"])
                                  ).pack(side="left", padx=4)
            else:
                status_lbl.configure(
                    text=f"CYOA Downloader v{_APP_VERSION}\n\n"
                         "Already up to date ✅")

        def _check_worker():
            try:
                if not _GITHUB_RELEASE_API:
                    self.root.after(0, lambda: _apply_check_result("__not_configured__"))
                    return
                info = _check_for_app_updates()
                self.root.after(0, lambda i=info: _apply_check_result(i))
            except Exception as e:
                self.root.after(0, lambda err=str(e): _apply_check_result(None, err))

        ctk.CTkButton(btn_frame, text="Close", height=30,
                      fg_color=p["surface2"], hover_color=p["surface"],
                      text_color=p["muted"],
                      command=win.destroy).pack(side="left", padx=4)

        threading.Thread(target=_check_worker, daemon=True).start()

    # ── Batch Update Checker ───────────────────────────────────────────
    def _batch_update_panel(self) -> None:
        import customtkinter as ctk
        p = self._p()
        history = _load_history()
        if not history:
            from tkinter import messagebox
            messagebox.showinfo("Batch Check", "No download history yet.")
            return

        win = self._make_singleton_window("batch_check_legacy")
        if win is None:
            return
        win.title("📥 Batch Update Checker")
        win.geometry("600x400")
        win.configure(fg_color=p["bg"])
        win.transient(self.root)
        win.grab_set()

        header = ctk.CTkLabel(
            win, text=f"Checking {len([h for h in history.values() if h.get('success')])} "
                      f"previously downloaded CYOAs…",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=p["fg"])
        header.pack(padx=14, pady=(14, 6))

        pb = ctk.CTkProgressBar(win, height=6)
        pb.pack(padx=14, fill="x")
        pb.set(0)

        result_frame = ctk.CTkScrollableFrame(win, fg_color=p["surface2"],
                                               corner_radius=8)
        result_frame.pack(padx=14, pady=(10, 14), fill="both", expand=True)

        def _run():
            def _prog(done, total):
                if total:
                    _v25_safe_after_widget(self.root, pb,
                                           lambda: pb.set(done / total))

            results = _batch_check_updates(history, progress_cb=_prog)

            def _show():
                pb.set(1.0)
                updated = [r for r in results if r["status"] == "updated"]
                current = [r for r in results if r["status"] == "current"]
                errors  = [r for r in results if r["status"] in ("error", "unreachable")]
                header.configure(
                    text=f"✅ {len(current)} current  |  "
                         f"🔄 {len(updated)} updated  |  "
                         f"❌ {len(errors)} errors")

                for r in updated:
                    f = ctk.CTkFrame(result_frame, fg_color=p["accentbg"],
                                     corner_radius=6)
                    f.pack(fill="x", padx=4, pady=2)
                    ctk.CTkLabel(f, text=f"🔄 {r.get('name') or r['url'][:50]}",
                                 font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                 text_color=p["accent"]).pack(anchor="w", padx=8, pady=(4, 0))
                    ctk.CTkLabel(f, text=r.get("reason", ""),
                                 font=ctk.CTkFont("Segoe UI", 9),
                                 text_color=p["muted"]).pack(anchor="w", padx=8, pady=(0, 4))

                    def _requeue(url=r["url"]):
                        self._add_url_to_queue(url)
                        win.destroy()

                    ctk.CTkButton(f, text="Re-download", height=24, width=100,
                                  font=ctk.CTkFont("Segoe UI", 10),
                                  fg_color=p["surface2"], hover_color=p["surface"],
                                  text_color=p["fg"],
                                  command=_requeue).pack(anchor="e", padx=8, pady=(0, 4))

                for r in errors:
                    f = ctk.CTkFrame(result_frame, fg_color=p["surface"],
                                     corner_radius=6)
                    f.pack(fill="x", padx=4, pady=2)
                    ctk.CTkLabel(f, text=f"❌ {r.get('name') or r['url'][:50]}",
                                 font=ctk.CTkFont("Segoe UI", 10),
                                 text_color=p["muted"]).pack(anchor="w", padx=8, pady=2)

                if not updated and not errors:
                    ctk.CTkLabel(result_frame,
                                 text="All CYOAs are still up-to-date ✅",
                                 font=ctk.CTkFont("Segoe UI", 12),
                                 text_color=p["fg"]).pack(pady=20)

            self.root.after(0, _show)

        threading.Thread(target=_run, daemon=True).start()

    def _add_url_to_queue(self, url: str, filename: str = "") -> None:
        """Programmatically add a URL to the queue (used by batch update / CM import)."""
        try:
            self._url_var.set(url)
            if filename and hasattr(self, "_fn_var"):
                self._fn_var.set(filename)
            # v7.6 bugfix: was calling self._add_url() which does not exist
            # (AttributeError was silently swallowed, so the URL was never
            # added). The real handler is _add_to_queue(), which reads _url_var.
            self._add_to_queue()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _add_url_to_queue (line 10167): %s", _ignored_exc)

    def _show_credits_panel(self) -> None:
        """Show a compact credits/sources panel."""
        import customtkinter as ctk
        import webbrowser

        p = self._p()
        panel_card = p.get("panel2") or p.get("surface2") or p.get("panel") or p.get("bg", "#111827")
        border_col = p.get("border") or p.get("surface2") or "#334155"
        accent_col = p.get("accent") or "#3b82f6"
        accent2_col = p.get("accent2") or "#2563eb"
        lang = getattr(self, "_language", "id")
        is_en = (lang == "en")

        win = self._make_singleton_window("credits_panel")
        if win is None:
            return
        win.title("CYOA Downloader — Credits" if is_en else "CYOA Downloader — Kredit")
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(780, max(680, sw - 240))
            h = min(640, max(520, sh - 220))
            x = max(20, (sw - w) // 2)
            y = max(20, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("740x580")
        win.minsize(660, 500)
        try:
            win.transient(self.root)
            win.lift()
            win.focus_force()
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _show_credits_panel (line 10201): %s", _ignored_exc)

        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text=("© Credits & Sources" if is_en else "© Kredit & Sumber"),
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            text_color=p["fg"],
        ).pack(side="left", padx=16)

        intro = (
            "With respect and real gratitude: this app exists because the CYOA community kept sharing tools, ideas, fixes, and patience. These credits are not just a formality; they are a small thank-you to the people and projects that made this work possible."
            if is_en else
            "Dengan hormat dan terima kasih yang tulus: aplikasi ini ada karena komunitas CYOA terus berbagi tool, ide, perbaikan, dan kesabaran. Credit ini bukan sekadar formalitas; ini ucapan terima kasih kecil untuk orang dan proyek yang membuat pekerjaan ini mungkin."
        )
        ctk.CTkLabel(
            win,
            text=intro,
            justify="left",
            wraplength=720,
            text_color=p["muted"],
            font=ctk.CTkFont("Segoe UI", 11),
        ).pack(fill="x", padx=18, pady=(10, 6), anchor="w")

        body = ctk.CTkScrollableFrame(win, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        sources = [
            {
                "title": "Yet Another Interactive CYOA Downloader",
                "desc_en": "Respectfully credited as a base script and one of the earliest practical inspirations for this downloader. A lot of the spirit of preserving interactive CYOAs starts here.",
                "desc_id": "Dengan hormat dicantumkan sebagai script dasar dan salah satu inspirasi praktis paling awal untuk downloader ini. Banyak semangat menjaga CYOA interaktif tetap bisa disimpan berawal dari sini.",
                "url": "https://forum.cyoa.cafe/d/14-yet-another-interactive-cyoa-downloader",
                "category": "base",
                "role_en": "Base script & earliest inspiration",
                "role_id": "Script dasar & inspirasi paling awal",
                "license": "See source",
            },
            {
                "title": "CYOA Launcher",
                "desc_en": "Thank you for the launcher/workflow inspiration and for showing another thoughtful way to organize local CYOA access.",
                "desc_id": "Terima kasih atas inspirasi launcher/alur kerja dan contoh cara lain yang rapi untuk mengatur akses CYOA lokal.",
                "url": "https://github.com/DragonsWho/CYOA_Launcher",
                "category": "inspiration",
                "role_en": "Launcher / workflow inspiration",
                "role_id": "Inspirasi launcher / alur kerja",
                "license": "See repo",
            },
            {
                "title": "IntCyoaEnhancer",
                "desc_en": "Credited for the cheat-helper reference and ideas used only for local preview/debug workflows. The original author and source remain acknowledged with respect.",
                "desc_id": "Dicantumkan untuk referensi dan ide cheat-helper yang hanya dipakai pada alur preview/debug lokal. Pembuat asli dan sumbernya tetap dihormati.",
                "url": "https://greasyfork.org/en/scripts/438947-intcyoaenhancer",
                "category": "inspiration",
                "role_en": "Cheat-helper reference (local preview/debug)",
                "role_id": "Referensi cheat-helper (preview/debug lokal)",
                "license": "MIT",
            },
            {
                "title": "ICCPlus",
                "desc_en": "Thank you to ICCPlus and its maintainers for the viewer ecosystem that many modern interactive CYOAs rely on.",
                "desc_id": "Terima kasih kepada ICCPlus dan para pengembangnya atas ekosistem viewer yang menjadi dasar banyak CYOA interaktif modern.",
                "url": "https://github.com/wahaha303/ICCPlus",
                "category": "ecosystem",
                "role_en": "Viewer ecosystem for modern CYOAs",
                "role_id": "Ekosistem viewer untuk CYOA modern",
                "license": "See repo",
            },
            {
                "title": "CYOA Manager",
                "desc_en": "Thank you to the CYOA Manager project for the library-management ideas and the workflow inspiration for organizing local CYOA collections more carefully.",
                "desc_id": "Terima kasih kepada proyek CYOA Manager atas ide pengelolaan library dan inspirasi alur kerja untuk mengatur koleksi CYOA lokal dengan lebih rapi.",
                "url": "https://github.com/alexncode/CYOA-Manager/",
                "category": "ecosystem",
                "role_en": "Library-management workflow inspiration",
                "role_id": "Inspirasi alur kelola library",
                "license": "See repo",
            },
            {
                "title": "itch-dl",
                "desc_en": "Respectfully credited for the itch.io download backend inspiration and tooling ecosystem that helps preserve downloadable game/project files.",
                "desc_id": "Dengan hormat dicantumkan untuk inspirasi backend unduhan itch.io dan ekosistem tooling yang membantu menyimpan file game/proyek yang dapat diunduh.",
                "url": "https://github.com/DragoonAethis/itch-dl",
                "category": "library",
                "role_en": "itch.io download backend inspiration",
                "role_id": "Inspirasi backend unduhan itch.io",
                "license": "MIT",
            },
            {
                "title": "gallery-dl",
                "desc_en": "Thank you to gallery-dl and its maintainers for the mature downloader ecosystem that inspired and supports optional gallery/post fallback workflows.",
                "desc_id": "Terima kasih kepada gallery-dl dan para pengembangnya atas ekosistem downloader matang yang menginspirasi dan mendukung alur fallback opsional untuk galeri/post.",
                "url": "https://github.com/mikf/gallery-dl",
                "category": "library",
                "role_en": "Optional gallery/post fallback downloader",
                "role_id": "Downloader fallback galeri/post opsional",
                "license": "GPL-2.0-only",
            },
            {
                "title": "Selenium",
                "desc_en": "Thank you to the Selenium project for the browser automation foundation used as one of the optional headless fallback paths.",
                "desc_id": "Terima kasih kepada proyek Selenium atas fondasi otomasi browser yang digunakan sebagai salah satu jalur fallback headless opsional.",
                "url": "https://www.selenium.dev/",
                "category": "library",
                "role_en": "Optional headless browser automation",
                "role_id": "Otomasi browser headless opsional",
                "license": "Apache-2.0",
            },
            {
                "title": "Playwright",
                "desc_en": "Thank you for the modern, reliable headless-browser engine used as the preferred JavaScript-rendering fallback when plain HTTP fetches are not enough.",
                "desc_id": "Terima kasih atas engine headless-browser modern dan andal yang dipakai sebagai fallback render JavaScript utama saat fetch HTTP biasa tidak cukup.",
                "url": "https://playwright.dev/",
                "category": "library",
                "role_en": "Preferred headless JS-rendering fallback",
                "role_id": "Fallback render JS headless utama",
                "license": "Apache-2.0",
            },
            {
                "title": "requests + urllib3",
                "desc_en": "The dependable HTTP foundation behind nearly every network request this tool makes — retries, sessions, streaming, and proxy handling all build on it.",
                "desc_id": "Fondasi HTTP yang andal di balik hampir semua permintaan jaringan tool ini — retry, session, streaming, dan penanganan proxy semuanya dibangun di atasnya.",
                "url": "https://requests.readthedocs.io/",
                "category": "library",
                "role_en": "Core HTTP client (downloader backbone)",
                "role_id": "Klien HTTP inti (tulang punggung downloader)",
                "license": "Apache-2.0",
            },
            {
                "title": "BeautifulSoup4",
                "desc_en": "Thank you for the forgiving HTML parser that makes resolving viewers, scripts, and project hints from messy real-world pages possible.",
                "desc_id": "Terima kasih atas parser HTML yang toleran sehingga viewer, script, dan petunjuk project bisa di-resolve dari halaman dunia nyata yang berantakan.",
                "url": "https://www.crummy.com/software/BeautifulSoup/",
                "category": "library",
                "role_en": "HTML parsing & viewer/script detection",
                "role_id": "Parsing HTML & deteksi viewer/script",
                "license": "MIT",
            },
            {
                "title": "Pillow",
                "desc_en": "Thank you to the Pillow maintainers for the image-handling toolkit used for placeholders, validation, and asset processing.",
                "desc_id": "Terima kasih kepada para pengembang Pillow atas toolkit penanganan gambar yang dipakai untuk placeholder, validasi, dan pemrosesan aset.",
                "url": "https://python-pillow.org/",
                "category": "library",
                "role_en": "Image handling & placeholders",
                "role_id": "Penanganan gambar & placeholder",
                "license": "MIT-CMU",
            },
            {
                "title": "CustomTkinter",
                "desc_en": "Thank you for the modern themed widget toolkit that gives this app its desktop GUI without leaving the Python/Tk ecosystem.",
                "desc_id": "Terima kasih atas toolkit widget bertema modern yang memberi aplikasi ini GUI desktop tanpa keluar dari ekosistem Python/Tk.",
                "url": "https://github.com/TomSchimansky/CustomTkinter",
                "category": "library",
                "role_en": "Desktop GUI framework",
                "role_id": "Framework GUI desktop",
                "license": "MIT",
            },
            {
                "title": "yt-dlp + FFmpeg",
                "desc_en": "Thank you to yt-dlp and FFmpeg for the media download/transcode pipeline that lets background audio be preserved for offline playback.",
                "desc_id": "Terima kasih kepada yt-dlp dan FFmpeg atas pipeline unduh/transcode media yang memungkinkan audio latar disimpan untuk pemutaran offline.",
                "url": "https://github.com/yt-dlp/yt-dlp",
                "category": "library",
                "role_en": "Optional audio download & conversion",
                "role_id": "Unduh & konversi audio opsional",
                "license": "Unlicense / LGPL",
            },
            {
                "title": "httpx + h2 · cloudscraper · tldextract · pandas · json5 · keyring · rarfile · plyer",
                "desc_en": "A quiet thank-you to the many focused libraries that power individual features: HTTP/2 deep scan, Cloudflare handling, domain parsing, batch import, lenient JSON, OS keyring storage, RAR viewers, and desktop notifications.",
                "desc_id": "Terima kasih diam-diam untuk banyak library terfokus yang menggerakkan fitur masing-masing: deep scan HTTP/2, penanganan Cloudflare, parsing domain, import batch, JSON longgar, penyimpanan keyring OS, viewer RAR, dan notifikasi desktop.",
                "url": None,
                "category": "library",
                "role_en": "Per-feature optional libraries",
                "role_id": "Library opsional per-fitur",
                "license": "Mixed (BSD/MIT/Apache)",
            },
            {
                "title": "FlareSolverr",
                "desc_en": "Thank you for the external challenge-solving proxy that helps reach viewers behind tougher Cloudflare protections for personal preservation.",
                "desc_id": "Terima kasih atas proxy penyelesai tantangan eksternal yang membantu menjangkau viewer di balik proteksi Cloudflare yang lebih ketat untuk preservasi pribadi.",
                "url": "https://github.com/FlareSolverr/FlareSolverr",
                "category": "library",
                "role_en": "Optional Cloudflare challenge solver",
                "role_id": "Penyelesai tantangan Cloudflare opsional",
                "license": "MIT",
            },
            {
                "title": "CYOA community",
                "desc_en": "Thank you to the wider CYOA community: creators, archivists, tool makers, testers, bug reporters, and users who keep these works alive.",
                "desc_id": "Terima kasih kepada komunitas CYOA yang lebih luas: kreator, pengarsip, pembuat tool, tester, pelapor bug, dan pengguna yang ikut menjaga karya-karya ini tetap hidup.",
                "url": None,
                "category": "community",
                "role_en": "Creators, archivists, testers, users",
                "role_id": "Kreator, pengarsip, tester, pengguna",
                "license": None,
            },
            {
                "title": "Everyone who helped along the way",
                "desc_en": "Some people helped through small comments, screenshots, testing, ideas, or quiet encouragement. Not everyone can be named one by one, but the help is remembered and appreciated.",
                "desc_id": "Ada yang membantu lewat komentar kecil, screenshot, pengujian, ide, atau dukungan diam-diam. Tidak semua bisa disebut satu per satu, tetapi bantuan itu tetap diingat dan dihargai.",
                "url": None,
                "category": "community",
                "role_en": "Comments, screenshots, testing, ideas",
                "role_id": "Komentar, screenshot, pengujian, ide",
                "license": None,
            },
            {
                "title": "Claude + ChatGPT",
                "desc_en": "Thank you to Claude and ChatGPT as AI assistants used during review, refactoring, documentation, and stability work.",
                "desc_id": "Terima kasih kepada Claude dan ChatGPT sebagai asisten AI yang digunakan dalam review, refactoring, dokumentasi, dan stabilisasi.",
                "url": None,
                "category": "tooling",
                "role_en": "AI assistants for review & docs",
                "role_id": "Asisten AI untuk review & dokumentasi",
                "license": None,
            },
            {
                "title": "With humble gratitude to the Triune God",
                "desc_en": "Above all, this work gives thanks to God the Father, to our Lord Jesus Christ, and to the Holy Spirit—for grace, wisdom, patience, and the strength to keep building something useful with care.",
                "desc_id": "Yang terutama, syukur kepada Tuhan, kepada Yesus Kristus, dan kepada Roh Kudus atas kasih karunia, kesabaran, kekuatan, dan kesempatan untuk terus membangun sesuatu yang berguna dengan sepenuh hati.",
                "url": None,
                "category": "gratitude",
                "role_en": "Above all, thanks be to God",
                "role_id": "Yang terutama, syukur kepada Tuhan",
                "license": None,
            },
        ]

        def open_url(url: str):
            try:
                webbrowser.open(url)
                # Some platforms move the app behind the browser after open().
                # Bring this credits panel back to the foreground without making it permanently topmost.
                def _refocus():
                    try:
                        win.lift()
                        win.focus_force()
                        win.attributes("-topmost", True)
                        win.after(220, lambda: win.attributes("-topmost", False))
                    except Exception as _ignored_exc:
                        logger.debug("Ignored recoverable exception in _refocus (line 10317): %s", _ignored_exc)
                try:
                    win.after(250, _refocus)
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in open_url (line 10321): %s", _ignored_exc)
            except Exception as e:
                self._safe_message("Open URL", str(e))

        # v46.11: group credits by category and show role + license metadata.
        category_titles = {
            "base":        ("Base & Origins",          "Dasar & Asal-usul"),
            "inspiration": ("Inspiration & References", "Inspirasi & Referensi"),
            "ecosystem":   ("Viewer Ecosystems",       "Ekosistem Viewer"),
            "library":     ("Libraries & Backends",     "Library & Backend"),
            "tooling":     ("Development Tooling",       "Perkakas Pengembangan"),
            "community":   ("Community & People",        "Komunitas & Orang"),
            "gratitude":   ("Gratitude",                 "Ucapan Syukur"),
        }
        category_order = ["base", "inspiration", "ecosystem", "library", "tooling", "community", "gratitude"]

        def license_color(lic: Optional[str]) -> str:
            if not lic:
                return p["muted"]
            low = lic.lower()
            if "gpl" in low:
                return "#f59e0b"   # copyleft — amber
            if "mit" in low or "apache" in low or "bsd" in low:
                return "#22c55e"   # permissive — green
            return p["muted"]      # unknown / see repo

        grouped: Dict[str, list] = {}
        for item in sources:
            grouped.setdefault(item.get("category", "community"), []).append(item)

        for cat in category_order:
            items = grouped.get(cat)
            if not items:
                continue
            cat_en, cat_id = category_titles.get(cat, (cat.title(), cat.title()))
            ctk.CTkLabel(
                body,
                text=(cat_en if is_en else cat_id).upper(),
                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                text_color=accent_col, anchor="w",
            ).pack(fill="x", padx=8, pady=(12, 2), anchor="w")

            for item in items:
                card = ctk.CTkFrame(
                    body, fg_color=panel_card, corner_radius=10,
                    border_width=1, border_color=border_col
                )
                card.pack(fill="x", padx=4, pady=4)
                card.grid_columnconfigure(1, weight=1)

                icon = "🔗" if item.get("url") else "✦"
                ctk.CTkLabel(
                    card, text=icon, width=28,
                    font=ctk.CTkFont("Segoe UI", 16, "bold"),
                    text_color=accent_col,
                ).grid(row=0, column=0, rowspan=4, padx=(10, 8), pady=8, sticky="n")

                ctk.CTkLabel(
                    card, text=item["title"],
                    font=ctk.CTkFont("Segoe UI", 12, "bold"),
                    text_color=p["fg"], anchor="w",
                ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 1))

                # Role + license metadata line
                role_txt = item.get("role_en" if is_en else "role_id", "")
                lic = item.get("license")
                meta_bits = []
                if role_txt:
                    meta_bits.append(role_txt)
                meta_line = "  ·  ".join(meta_bits)
                if meta_line:
                    ctk.CTkLabel(
                        card, text=meta_line, justify="left", wraplength=560,
                        text_color=p["fg"], font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        anchor="w",
                    ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 1))
                if lic:
                    ctk.CTkLabel(
                        card,
                        text=("License: " if is_en else "Lisensi: ") + lic,
                        text_color=license_color(lic),
                        font=ctk.CTkFont("Segoe UI", 9, "bold"), anchor="w",
                    ).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 1))

                ctk.CTkLabel(
                    card,
                    text=item["desc_en"] if is_en else item["desc_id"],
                    justify="left", wraplength=590, text_color=p["muted"],
                    font=ctk.CTkFont("Segoe UI", 10), anchor="w",
                ).grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))

                if item.get("url"):
                    ctk.CTkLabel(
                        card, text=item["url"], justify="left", wraplength=590,
                        text_color=p["accent"], font=ctk.CTkFont("Consolas", 9), anchor="w",
                    ).grid(row=4, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
                    ctk.CTkButton(
                        card, text=("Open" if is_en else "Buka"), width=72, height=28,
                        fg_color=accent_col, hover_color=accent2_col, text_color="#ffffff",
                        command=lambda u=item["url"]: open_url(u)
                    ).grid(row=0, column=2, rowspan=4, padx=(4, 10), pady=8, sticky="e")

        foot = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=46)
        foot.pack(fill="x", side="bottom")
        ctk.CTkButton(
            foot, text=("Close" if is_en else "Tutup"), width=110, height=34,
            fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
            command=win.destroy
        ).pack(side="right", padx=12, pady=7)


    def _show_feature_guide(self, initial_tab: str = "download") -> None:
        """Feature overview + quick-reference panel with full Indonesian/English content."""
        import customtkinter as ctk

        p = self._p()
        lang = getattr(self, "_language", "id")
        is_en = (lang == "en")

        win = self._make_singleton_window("help_guide")
        if win is None:
            return
        win.title("CYOA Downloader — Help / Guide" if is_en else "CYOA Downloader — Bantuan / Panduan")
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(1040, max(900, sw - 140))
            h = min(760, max(660, sh - 140))
            x = max(20, (sw - w) // 2)
            y = max(20, (sh - h) // 2)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            win.geometry("980x720")
        win.minsize(900, 640)
        win.grab_set()

        hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        header_text = (
            f"❔  Help / Guide — CYOA Downloader v{_APP_VERSION}  ·  {_STABILIZATION_PATCH_ID}"
            if is_en else
            f"❔  Bantuan / Panduan — CYOA Downloader v{_APP_VERSION}  ·  {_STABILIZATION_PATCH_ID}"
        )
        ctk.CTkLabel(
            hdr,
            text=header_text,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=p["fg"],
        ).pack(side="left", padx=16)

        tab_var = ctk.StringVar(value=initial_tab or "setup")
        # Full-width two-row tab grid. This restores all guide sections in the
        # visible header while keeping the buttons compact and predictable.
        tab_frame = ctk.CTkFrame(win, fg_color=p["surface"], corner_radius=8, height=62)
        tab_frame.pack(fill="x", padx=12, pady=(6, 4))
        tab_frame.grid_propagate(False)
        for _col in range(7):
            tab_frame.grid_columnconfigure(_col, weight=1, uniform="guide_tabs")

        TABS_EN = [
            ("setup", "❔ Start"),
            ("import", "📥 Batch"),
            ("cli", "⌨ CLI"),
            ("files", "📁 File"),
            ("settings", "⚙ Settings"),
            ("download", "⬇ DL"),
            ("audio", "🎵 Audio"),
            ("queue", "📋 Queue"),
            ("viewer", "📺 Viewer"),
            ("network", "🌐 Net"),
            ("cyoa_mgr", "📤 Manager"),
            ("cheat", "⚙ Cheat"),
            ("workflow", "⌨ Flow"),
            ("cookies", "🍪 Cookies"),
        ]
        TABS_ID = [
            ("setup", "❔ Awal"),
            ("import", "📥 Batch"),
            ("cli", "⌨ CLI"),
            ("files", "📁 File"),
            ("settings", "⚙ Atur"),
            ("download", "⬇ Unduh"),
            ("audio", "🎵 Audio"),
            ("queue", "📋 Antre"),
            ("viewer", "📺 Lihat"),
            ("network", "🌐 Net"),
            ("cyoa_mgr", "📤 Manajer"),
            ("cheat", "⚙ Cheat"),
            ("workflow", "⌨ Alur"),
            ("cookies", "🍪 Cookie"),
        ]
        TABS = TABS_EN if is_en else TABS_ID
        content_area = ctk.CTkScrollableFrame(
            win,
            fg_color=p["bg"],
            scrollbar_button_color=p["surface2"],
        )
        content_area.pack(fill="both", expand=True)

        CONTENT_EN = {
            "download": [
                ("⬇  Download Modes", "accent", [
                    ("Auto", "Probes the URL before downloading and selects the best engine. Default Auto Output can be Folder or ZIP from Settings."),
                    ("Embedded JSON", "Stores images as base64 inside one project JSON file. Use this when you want a compact single-file backup."),
                    ("ZIP", "Creates project.json plus separate image and audio folders inside a ZIP archive."),
                    ("Both", "Creates an embedded JSON and a ZIP package in one run."),
                    ("ICC Folder", "Downloads viewer HTML, CSS, JavaScript, images, fonts, audio, and project data into a playable local folder."),
                    ("ICC ZIP", "Creates the same website package as ICC Folder, then compresses it into a ZIP archive."),
                    ("Pure Website", "Downloads the visible site without trying to discover a project JSON. Use it for custom viewer formats."),
                    ("cyoap_vue", "Uses the CYOA-P Vue flow by downloading dist/platform.json and dist/nodes/*.json."),
                ]),
                ("🔧  Options Row", "muted", [
                    ("Threads", "Controls parallel image downloads. Start with 4 to 8 threads for stable hosts."),
                    ("Retry delay", "Wait time after rate-limit responses such as HTTP 429."),
                    ("Bandwidth limit", "Limits download speed in KB/s. Use 0 for unlimited speed."),
                    ("Download Fonts", "Downloads fonts referenced by HTML or CSS files."),
                    ("HTTP/2", "Uses httpx with HTTP/2 for deep-scan fetches when httpx[http2] is installed."),
                    ("YT Audio", "Downloads YouTube audio with yt-dlp and ffmpeg, then patches local audio paths into the project."),
                    ("AI Assist", "Optional recovery/diagnostic helper. Configure provider and keys from Settings / Maintenance."),
                    ("Gallery-dl", "Optional fallback for supported gallery/post pages. Open its config from Settings / Maintenance."),
                ]),
                ("📂  Output", "muted", [
                    ("Output folder", "Destination folder for all generated files. The folder is created automatically if it does not exist."),
                    ("Filename", "The filename is generated from the URL by default, but you can edit it before adding the URL to the queue."),
                    ("Reports", "The program writes backup_report.txt, failed_assets.txt, failed_images.txt, and cyoa_downloader.log when relevant."),
                ]),
                ("🧭  Toolbar Layout", "muted", [
                    ("Top row", "Download All, Preview, Pause/Continue, Start/Stop Serve, Open Folder, and status/progress are pinned into one compact row."),
                    ("Settings", "AI Assist, gallery-dl config, Cloudflare/FlareSolverr, Offline Viewers, and settings import/export live here."),
                    ("Recovery", "Retry Assets, Retry Images, and Retry Audio are direct compact toolbar buttons."),
                ]),
            ],
            "audio": [
                ("🎵  Offline Audio Flow", "accent", [
                    ("Detection", "Scans project JSON for bgmId, direct audio fields, playlists, and YouTube video IDs."),
                    ("Download", "Uses yt-dlp to download YouTube audio, then ffmpeg converts it to MP3."),
                    ("Patch", "Rewrites bgmId and useAudioURL so the offline viewer can load local audio files."),
                    ("Copy", "Copies the audio folder into the JSON package, ZIP package, or ICC folder as needed."),
                    ("Skipped items", "Writes skipped_youtube_audio.txt when a YouTube track cannot be downloaded."),
                ]),
                ("⚙  ffmpeg Detection", "yellow", [
                    ("PATH", "Searches for ffmpeg in the active PATH."),
                    ("Windows registry", "Reads user and machine PATH entries from the Windows registry."),
                    ("Package managers", "Checks common winget, Scoop, Chocolatey, and local install locations."),
                    ("Manual fix", "Install ffmpeg and restart the program if audio conversion fails."),
                ]),
                ("🔁  Browser Cookies", "muted", [
                    ("Automatic", "Tries browser cookies from Chrome, Firefox, Edge, Brave, Chromium, and Safari."),
                    ("Locked Chrome", "Windows may lock the Chromium cookie database while the browser is open; close Chrome/Edge/Brave completely before retrying, or use an exported cookies.txt."),
                    ("Manual cookies.txt", "Export a Netscape cookies.txt with a browser extension, then open Settings / Maintenance → YouTube cookies, choose it, and click Save."),
                ]),
                ("🔇  Browser Autoplay", "red", [
                    ("Blocked playback", "Modern browsers can block autoplay. The offline viewer adds an enable-audio banner when needed."),
                    ("User action", "Click the audio banner once to allow playback on the local page."),
                ]),
            ],
            "queue": [
                ("📋  Queue Management", "accent", [
                    ("Add URL", "Paste a URL and press Enter or click Add. Duplicate URLs are kept as separate jobs."),
                    ("Edit name", "Edit the filename field below each queued URL before downloading."),
                    ("Change mode", "Click the mode badge (for example, Auto) on a row and choose another mode. The URL stays in the queue."),
                    ("Reorder", "Drag the handle at the left of a row to change download priority."),
                    ("Remove", "Use the row close button, Remove, or Clear All to manage the queue."),
                    ("Batch import", "Imports .txt, .csv, .xlsx, or Google Sheet CSV sources with URL, filename, and mode columns."),
                    ("Export list", "Click Export List to save the current URL, filename, and mode as CSV or TXT for backup or reuse."),
                ]),
                ("🔍  Pre-flight Preview", "muted", [
                    ("Probe", "Checks URLs before starting downloads and shows whether project data is likely available."),
                    ("Results", "FOUND means direct project data was detected. JS/SCAN means discovery may require script scanning. ERROR means the URL failed."),
                    ("Proceed", "Use Proceed with Download to start immediately from the preview results."),
                ]),
                ("💾  Resume and Retry", "muted", [
                    ("Resume", "Successful URLs are written to download_state.json so a repeated batch can skip completed items."),
                    ("Retry Failed", "Adds failed queue items back into the queue."),
                    ("Retry Images", "Uses failed_images.txt to retry image downloads and patch the project JSON."),
                ]),
            ],
            "viewer": [
                ("📺  Offline Viewer", "accent", [
                    ("Register viewer", "Open Viewers and add an offline viewer ZIP such as ICC Plus, ICC Remix, or a compatible custom viewer."),
                    ("Auto-match", "Matches a viewer to the CYOA by reading HTML and script hints."),
                    ("Inject manually", "Each viewer card has an Inject button. Use it to build a playable offline viewer from a project source you already have, even when auto-match did not run. Source can be a file (project.json, app.js, or a zip/rar), a download folder (auto-scans for project.json or app*.js plus images/ and audio/), or a URL (uses the full resolver, including embedded-JS extraction and optional AI). Output is a self-contained <name>_offline folder."),
                    ("ICC Remix", "Injects project data into the template marker."),
                    ("ICC Plus", "Uses marker-based and balanced-brace injection around the project data placeholder."),
                    ("Custom viewer", "Patches project.json fetch calls when the viewer supports local project data."),
                ]),
                ("⚙  Cheat Overlay", "muted", [
                    ("Gear button", "Adds a floating gear button to offline viewers."),
                    ("Set Points", "Changes point values in Vuex or Pinia stores."),
                    ("Remove Requirements", "Removes required fields from rows and objects."),
                    ("Unlimited Choices", "Sets allowedChoices to unlimited across rows."),
                    ("Select or Deselect", "Selects or deselects all choices in the CYOA."),
                ]),
                ("⚡  Local Server", "green", [
                    ("Start", "Use Serve to select a folder and start a local HTTP server."),
                    ("Browser", "The browser opens to localhost on the selected server port."),
                    ("CORS", "The local server sends permissive CORS headers for local images and audio."),
                    ("Cache", "Serve disables browser cache and opens with a cache-busting URL so old CYOAs are not replayed."),
                    ("Stop", "Use Stop Server to shut down the local server."),
                ]),
            ],
            "network": [
                ("🌐  Network Controls", "accent", [
                    ("Proxy", "Applies HTTP, HTTPS, or SOCKS proxy settings to downloader requests."),
                    ("DNS", "Uses system DNS, preset DNS, custom DNS, or BebasDNS DoH for process-local resolution. Custom DNS now opens on its own visible row, so the field is not hidden on normal window widths."),
                    ("BebasDNS", "Uses DNS-over-HTTPS presets without changing Windows, router, browser, or hosts-file settings."),
                    ("HTTP/2", "Uses httpx HTTP/2 for compatible deep-scan requests."),
                    ("Broken assets", "Writes failed asset details into backup_report.txt when available, otherwise into failed_assets.txt."),
                ]),
                ("☁  Cloudflare Access", "yellow", [
                    ("Off", "Never attempts Cloudflare bypass."),
                    ("Auto", "Tries normal request first, then cloudscraper, then FlareSolverr when available."),
                    ("cloudscraper", "Uses cloudscraper for lighter Cloudflare challenge pages."),
                    ("FlareSolverr", "Uses a local FlareSolverr service to solve browser-based challenge pages."),
                    ("Endpoint", "Default API endpoint: http://localhost:8191/v1."),
                    ("Session", "Reuse per domain keeps cookies and user-agent for the same host. Clear sessions when cookies become stale."),
                    ("Proxy mode", "Inherit proxy sends the app proxy to FlareSolverr. None leaves FlareSolverr on its own network path."),
                ]),
                ("🎨  gallery-dl", "muted", [
                    ("Off", "Default. The program never calls gallery-dl."),
                    ("Smart", "Uses gallery-dl only for likely post, artwork, gallery, or status pages, not raw CDN images."),
                    ("Force", "Advanced mode. Passes matching URLs to gallery-dl even when the URL shape is uncertain."),
                    ("Authentication", "Use gallery-dl config for Pixiv OAuth, booru API keys, or account-based extractors."),
                ]),
            ],
            "cyoa_mgr": [
                ("📤  CYOA Manager Integration", "accent", [
                    ("Auto-add", "Enable CYOA Manager in the options row to add successful project JSON files automatically."),
                    ("One panel", "The CYOA Manager button opens one center containing import, export, auto-add, DB path, manual add, and folder scan."),
                    ("Status button", "The CYOA Manager button still shows whether auto-add is enabled."),
                    ("Database path", "Use a custom library.sqlite3 path for portable or non-standard installations."),
                    ("Duplicate check", "The program checks existing file_path entries before inserting a new library record."),
                    ("Viewer preference", "Stores a viewer preference so CYOA Manager can open the project with the correct viewer."),
                ]),
                ("📦  Batch Export", "muted", [
                    ("Scan folder", "Finds project JSON files larger than 1 KB in a selected folder."),
                    ("Pick files", "Allows manual multi-select of project JSON files."),
                    ("Last session", "Exports projects downloaded in the last session."),
                    ("Result", "Shows counts for added, already existing, and failed items."),
                ]),
            ],
            "cheat": [
                ("⚙  Cheat Overlay Detail", "accent", [
                    ("Polling", "Checks every 500 ms until the Vue app and store are available."),
                    ("Vuex", "Targets window.app.__vue__.$store.state.app for older ICC Plus builds."),
                    ("Pinia", "Targets window.__pinia.state.value for newer ICC Plus builds."),
                    ("Injection", "Injects the overlay before the closing body tag in offline viewer folders."),
                ]),
                ("🔧  Available Changes", "muted", [
                    ("Set Points", "Updates starting point values."),
                    ("Remove Requirements", "Deletes requirement arrays from rows and objects."),
                    ("Unlimited Choices", "Sets allowedChoices to unlimited."),
                    ("Select All", "Marks all objects as selected."),
                    ("Deselect All", "Marks all objects as not selected."),
                ]),
            ],
            "workflow": [
                ("⌨  Keyboard and Workflow", "accent", [
                    ("Enter", "Adds the current URL to the queue."),
                    ("Drag handle", "Reorders queue items."),
                    ("Open Folder", "Opens the selected output folder."),
                    ("Preview", "Runs the URL probe without starting a full download."),
                    ("Serve", "Starts a local server for a generated ICC folder."),
                ]),
                ("🔗  Supported URL Types", "muted", [
                    ("Standard HTTPS", "Supports many Neocities, Netlify, Vercel, GitHub Pages, and self-hosted CYOA pages."),
                    ("cyoa.cafe", "Resolves iframe-based CYOA pages when possible."),
                    ("archive.org", "Can reconstruct original URLs from known CYOA archive patterns."),
                    ("cyoap_vue", "Supports projects with dist/platform.json and dist/nodes/list.json."),
                ]),
                ("📁  Output Files", "muted", [
                    ("project.json", "Project data for embedded or ZIP outputs."),
                    ("audio/", "Downloaded local audio files."),
                    ("backup_report.txt", "Summary of downloaded and failed files."),
                    ("failed_images.txt", "Image URLs available for retry."),
                    ("cyoa_downloader.log", "Full session log written to the output folder."),
                ]),
            ],
            "cookies": [
                ("🎵  yt-dlp Cookies", "green", [
                    ("Automatic", "Log in to YouTube in your browser, then let yt-dlp read browser cookies automatically."),
                    ("Browser order", "Chrome, Firefox, Edge, Brave, Chromium, then Safari."),
                    ("Manual export", "Export Netscape cookies.txt with a browser extension, select it in Settings / Maintenance → YouTube cookies, then click Save."),
                    ("Common failures", "Expired cookies, private videos, deleted videos, and region locks can still fail."),
                ]),
                ("🎨  gallery-dl Authentication", "accent", [
                    ("Pixiv OAuth", "Run gallery-dl oauth:pixiv, authorize in the browser, and store the token in gallery-dl config."),
                    ("Danbooru", "Configure username and API key in gallery-dl config."),
                    ("e621", "Configure username and API key in gallery-dl config."),
                    ("Sankaku and similar sites", "Configure username and password only when the extractor requires an account."),
                    ("Config location", "Windows uses AppData Roaming. macOS and Linux usually use ~/.config/gallery-dl/config.json."),
                ]),
            ],
        }

        CONTENT_ID = {
            "download": [
                ("⬇  Mode Unduhan", "accent", [
                    ("Auto", "Memeriksa URL sebelum unduhan dimulai dan memilih mesin yang paling sesuai. Output bawaan adalah Folder ICC."),
                    ("JSON Tertanam", "Menyimpan gambar sebagai base64 di satu file JSON project."),
                    ("ZIP", "Membuat project.json bersama folder gambar dan audio terpisah di arsip ZIP."),
                    ("Keduanya", "Membuat JSON tertanam dan paket ZIP dalam satu proses."),
                    ("Folder ICC", "Mengunduh HTML, CSS, JavaScript, gambar, font, audio, dan data project ke folder lokal yang dapat dimainkan."),
                    ("ZIP ICC", "Membuat paket website seperti Folder ICC, lalu mengompresnya menjadi ZIP."),
                    ("Pure Website", "Mengunduh situs yang terlihat tanpa mencari project JSON. Gunakan untuk format viewer khusus."),
                    ("cyoap_vue", "Memakai alur CYOA-P Vue dengan mengunduh dist/platform.json dan dist/nodes/*.json."),
                ]),
                ("🔧  Baris Opsi", "muted", [
                    ("Thread", "Mengatur jumlah unduhan gambar paralel. Awali dengan 4 sampai 8 thread untuk host yang stabil."),
                    ("Jeda retry", "Waktu tunggu setelah respons pembatasan seperti HTTP 429."),
                    ("Batas bandwidth", "Membatasi kecepatan unduh dalam KB/detik. Gunakan 0 untuk tanpa batas."),
                    ("Unduh Font", "Mengunduh font yang dirujuk oleh file HTML atau CSS."),
                    ("HTTP/2", "Memakai httpx dengan HTTP/2 untuk deep scan jika httpx[http2] tersedia."),
                    ("Audio YT", "Mengunduh audio YouTube dengan yt-dlp dan ffmpeg, lalu menambal path audio lokal ke project."),
                    ("CYOA Manager", "Menambahkan file JSON project yang berhasil ke pustaka CYOA Manager."),
                ]),
                ("📂  Output", "muted", [
                    ("Folder output", "Folder tujuan untuk semua file hasil. Folder dibuat otomatis jika belum ada."),
                    ("Nama file", "Nama file dibuat dari URL secara otomatis, tetapi dapat diedit sebelum URL masuk antrean."),
                    ("Laporan", "Program menulis backup_report.txt, failed_assets.txt, failed_images.txt, dan cyoa_downloader.log jika relevan."),
                ]),
            ],
            "audio": [
                ("🎵  Alur Audio Offline", "accent", [
                    ("Deteksi", "Memindai JSON project untuk bgmId, field audio langsung, playlist, dan ID video YouTube."),
                    ("Unduh", "Memakai yt-dlp untuk mengunduh audio YouTube, lalu ffmpeg mengonversinya ke MP3."),
                    ("Patch", "Mengubah bgmId dan useAudioURL agar viewer offline memuat file audio lokal."),
                    ("Salin", "Menyalin folder audio ke paket JSON, paket ZIP, atau folder ICC sesuai kebutuhan."),
                    ("Item dilewati", "Menulis skipped_youtube_audio.txt jika track YouTube tidak dapat diunduh."),
                ]),
                ("⚙  Deteksi ffmpeg", "yellow", [
                    ("PATH", "Mencari ffmpeg di PATH aktif."),
                    ("Registry Windows", "Membaca PATH user dan mesin dari registry Windows."),
                    ("Package manager", "Memeriksa lokasi umum winget, Scoop, Chocolatey, dan instalasi lokal."),
                    ("Perbaikan manual", "Instal ffmpeg dan mulai ulang program jika konversi audio gagal."),
                ]),
                ("🔁  Cookie Browser", "muted", [
                    ("Otomatis", "Mencoba cookie browser dari Chrome, Firefox, Edge, Brave, Chromium, dan Safari."),
                    ("Chrome terkunci", "Windows dapat mengunci database cookie Chromium saat browser terbuka; tutup Chrome/Edge/Brave sepenuhnya sebelum mencoba lagi, atau gunakan cookies.txt hasil ekspor."),
                    ("cookies.txt manual", "Ekspor cookies.txt format Netscape dengan ekstensi browser, lalu buka Settings / Maintenance → Cookie YouTube, pilih file, dan klik Simpan."),
                ]),
                ("🔇  Autoplay Browser", "red", [
                    ("Pemutaran diblokir", "Browser modern dapat memblokir autoplay. Viewer offline menambahkan banner aktifkan audio jika diperlukan."),
                    ("Aksi pengguna", "Klik banner audio satu kali agar halaman lokal boleh memutar audio."),
                ]),
            ],
            "queue": [
                ("📋  Manajemen Antrean", "accent", [
                    ("Tambah URL", "Tempel URL dan tekan Enter atau klik Tambah. URL duplikat tetap menjadi job terpisah."),
                    ("Edit nama", "Edit field nama file di bawah setiap URL antrean sebelum mengunduh."),
                    ("Ubah mode", "Klik badge mode, misalnya Auto, pada baris lalu pilih mode lain. URL tetap berada di antrean."),
                    ("Ubah urutan", "Seret handle di kiri baris untuk mengubah prioritas unduhan."),
                    ("Hapus", "Gunakan tombol tutup baris, Hapus, atau Bersihkan untuk mengatur antrean."),
                    ("Impor batch", "Mengimpor sumber .txt, .csv, .xlsx, atau Google Sheet CSV dengan kolom URL, filename, dan mode."),
                    ("Ekspor list", "Klik Ekspor List untuk menyimpan URL, nama file, dan mode saat ini sebagai CSV atau TXT."),
                ]),
                ("🔍  Pratinjau Awal", "muted", [
                    ("Probe", "Memeriksa URL sebelum unduhan penuh dimulai dan menampilkan kemungkinan ketersediaan data project."),
                    ("Hasil", "FOUND berarti data project langsung terdeteksi. JS/SCAN berarti perlu pemindaian script. ERROR berarti URL gagal."),
                    ("Lanjut", "Gunakan Lanjutkan Download untuk mulai langsung dari hasil pratinjau."),
                ]),
                ("💾  Lanjutkan dan Retry", "muted", [
                    ("Resume", "URL yang berhasil ditulis ke download_state.json agar batch ulang dapat melewati item yang selesai."),
                    ("Ulang gagal", "Memasukkan kembali item antrean yang gagal."),
                    ("Ulang gambar", "Memakai failed_images.txt untuk mencoba ulang unduhan gambar dan menambal JSON project."),
                ]),
            ],
            "viewer": [
                ("📺  Viewer Offline", "accent", [
                    ("Daftarkan viewer", "Buka Viewer dan tambahkan ZIP viewer offline seperti ICC Plus, ICC Remix, atau viewer khusus yang kompatibel."),
                    ("Cocok otomatis", "Mencocokkan viewer ke CYOA dengan membaca petunjuk HTML dan script."),
                    ("Inject manual", "Tiap kartu viewer punya tombol Inject. Pakai untuk membuat viewer offline yang playable dari sumber project yang sudah Anda punya, bahkan saat cocok-otomatis tidak berjalan. Sumber bisa berupa file (project.json, app.js, atau zip/rar), folder hasil download (auto-scan project.json atau app*.js plus folder images/ dan audio/), atau URL (memakai resolver penuh, termasuk ekstraksi dari JS tertanam dan AI opsional). Hasilnya folder <nama>_offline yang mandiri."),
                    ("ICC Remix", "Menyisipkan data project ke marker template."),
                    ("ICC Plus", "Memakai injeksi berbasis marker dan balanced-brace di sekitar placeholder data project."),
                    ("Viewer khusus", "Menambal pemanggilan fetch project.json jika viewer mendukung data project lokal."),
                ]),
                ("⚙  Cheat Overlay", "muted", [
                    ("Tombol gear", "Menambahkan tombol gear mengambang ke viewer offline."),
                    ("Atur poin", "Mengubah nilai poin di store Vuex atau Pinia."),
                    ("Hapus syarat", "Menghapus field requirement dari row dan object."),
                    ("Pilihan tak terbatas", "Mengatur allowedChoices menjadi tak terbatas di semua row."),
                    ("Pilih atau batal", "Memilih atau membatalkan semua pilihan di CYOA."),
                ]),
                ("⚡  Server Lokal", "green", [
                    ("Mulai", "Gunakan Serve untuk memilih folder dan menjalankan server HTTP lokal."),
                    ("Browser", "Browser terbuka ke localhost pada port server yang dipilih."),
                    ("CORS", "Server lokal mengirim header CORS permisif untuk gambar dan audio lokal."),
                    ("Cache", "Serve menonaktifkan cache browser dan membuka URL cache-busting agar CYOA lama tidak terputar ulang."),
                    ("Berhenti", "Gunakan Stop Server untuk mematikan server lokal."),
                ]),
            ],
            "network": [
                ("🌐  Kontrol Jaringan", "accent", [
                    ("Proxy", "Menerapkan proxy HTTP, HTTPS, atau SOCKS untuk request downloader."),
                    ("DNS", "Memakai DNS sistem, preset DNS, DNS khusus, atau BebasDNS DoH untuk resolusi lokal proses. DNS khusus sekarang tampil di baris sendiri agar kolomnya tidak tersembunyi pada lebar window normal."),
                    ("BebasDNS", "Memakai preset DNS-over-HTTPS tanpa mengubah Windows, router, browser, atau hosts file."),
                    ("HTTP/2", "Memakai HTTP/2 dari httpx untuk request deep scan yang kompatibel."),
                    ("Asset rusak", "Menulis detail asset gagal ke backup_report.txt jika tersedia, atau failed_assets.txt jika tidak ada backup report."),
                ]),
                ("☁  Akses Cloudflare", "yellow", [
                    ("Off", "Tidak mencoba bypass Cloudflare."),
                    ("Auto", "Mencoba request normal, lalu cloudscraper, lalu FlareSolverr jika tersedia."),
                    ("cloudscraper", "Memakai cloudscraper untuk halaman challenge Cloudflare ringan."),
                    ("FlareSolverr", "Memakai service FlareSolverr lokal untuk menyelesaikan halaman challenge berbasis browser."),
                    ("Endpoint", "Endpoint API bawaan: http://localhost:8191/v1."),
                    ("Session", "Reuse per domain menyimpan cookie dan user-agent untuk host yang sama. Bersihkan session saat cookie usang."),
                    ("Mode proxy", "Inherit proxy mengirim proxy aplikasi ke FlareSolverr. None membiarkan FlareSolverr memakai jalur jaringan sendiri."),
                ]),
                ("🎨  gallery-dl", "muted", [
                    ("Off", "Bawaan. Program tidak memanggil gallery-dl."),
                    ("Smart", "Memakai gallery-dl hanya untuk URL post, artwork, galeri, atau status yang mungkin cocok, bukan raw CDN image."),
                    ("Force", "Mode lanjut. Mengirim URL yang cocok ke gallery-dl meski bentuk URL belum pasti."),
                    ("Autentikasi", "Gunakan config gallery-dl untuk OAuth Pixiv, API key booru, atau extractor berbasis akun."),
                ]),
            ],
            "cyoa_mgr": [
                ("📤  Integrasi CYOA Manager", "accent", [
                    ("Tambah otomatis", "Aktifkan CYOA Manager di baris opsi untuk menambahkan JSON project yang berhasil secara otomatis."),
                    ("Satu panel", "Tombol CYOA Manager membuka satu pusat berisi impor, ekspor, tambah otomatis, path DB, tambah manual, dan scan folder."),
                    ("Tombol status", "Tombol CYOA Manager tetap menunjukkan apakah tambah otomatis aktif."),
                    ("Path database", "Gunakan path library.sqlite3 khusus untuk instalasi portable atau tidak standar."),
                    ("Cek duplikat", "Program memeriksa entri file_path yang sudah ada sebelum menulis record pustaka baru."),
                    ("Preferensi viewer", "Menyimpan preferensi viewer agar CYOA Manager membuka project dengan viewer yang tepat."),
                ]),
                ("📦  Ekspor Batch", "muted", [
                    ("Pindai folder", "Mencari file JSON project yang lebih besar dari 1 KB di folder terpilih."),
                    ("Pilih file", "Mengizinkan pemilihan banyak file JSON project secara manual."),
                    ("Sesi terakhir", "Mengekspor project yang diunduh pada sesi terakhir."),
                    ("Hasil", "Menampilkan jumlah item ditambahkan, sudah ada, dan gagal."),
                ]),
            ],
            "cheat": [
                ("⚙  Detail Cheat Overlay", "accent", [
                    ("Polling", "Memeriksa setiap 500 ms sampai aplikasi Vue dan store tersedia."),
                    ("Vuex", "Menargetkan window.app.__vue__.$store.state.app untuk build ICC Plus lama."),
                    ("Pinia", "Menargetkan window.__pinia.state.value untuk build ICC Plus baru."),
                    ("Injeksi", "Menyisipkan overlay sebelum tag penutup body di folder viewer offline."),
                ]),
                ("🔧  Perubahan yang Tersedia", "muted", [
                    ("Atur poin", "Memperbarui nilai poin awal."),
                    ("Hapus syarat", "Menghapus array requirement dari row dan object."),
                    ("Pilihan tak terbatas", "Mengatur allowedChoices menjadi tak terbatas."),
                    ("Pilih semua", "Menandai semua object sebagai dipilih."),
                    ("Batalkan semua", "Menandai semua object sebagai tidak dipilih."),
                ]),
            ],
            "workflow": [
                ("⌨  Keyboard dan Alur Kerja", "accent", [
                    ("Enter", "Menambahkan URL aktif ke antrean."),
                    ("Handle seret", "Mengubah urutan item antrean."),
                    ("Buka Folder", "Membuka folder output yang dipilih."),
                    ("Pratinjau", "Menjalankan pemeriksaan URL tanpa memulai unduhan penuh."),
                    ("Serve", "Menjalankan server lokal untuk folder ICC yang dihasilkan."),
                ]),
                ("🔗  Jenis URL yang Didukung", "muted", [
                    ("HTTPS standar", "Mendukung banyak halaman CYOA dari Neocities, Netlify, Vercel, GitHub Pages, dan self-hosted."),
                    ("cyoa.cafe", "Menyelesaikan halaman CYOA berbasis iframe jika memungkinkan."),
                    ("archive.org", "Dapat membangun ulang URL asli dari pola arsip CYOA yang dikenal."),
                    ("cyoap_vue", "Mendukung project dengan dist/platform.json dan dist/nodes/list.json."),
                ]),
                ("📁  File Output", "muted", [
                    ("project.json", "Data project untuk output tertanam atau ZIP."),
                    ("audio/", "File audio lokal hasil unduhan."),
                    ("backup_report.txt", "Ringkasan file yang berhasil dan gagal."),
                    ("failed_images.txt", "URL gambar yang tersedia untuk retry."),
                    ("cyoa_downloader.log", "Log sesi lengkap yang ditulis ke folder output."),
                ]),
            ],
            "cookies": [
                ("🎵  Cookie yt-dlp", "green", [
                    ("Otomatis", "Login ke YouTube di browser, lalu biarkan yt-dlp membaca cookie browser secara otomatis."),
                    ("Urutan browser", "Chrome, Firefox, Edge, Brave, Chromium, lalu Safari."),
                    ("Ekspor manual", "Ekspor cookies.txt format Netscape dengan ekstensi browser, pilih di Settings / Maintenance → Cookie YouTube, lalu klik Simpan."),
                    ("Kegagalan umum", "Cookie kedaluwarsa, video privat, video terhapus, dan kunci wilayah tetap dapat gagal."),
                ]),
                ("🎨  Autentikasi gallery-dl", "accent", [
                    ("OAuth Pixiv", "Jalankan gallery-dl oauth:pixiv, beri izin di browser, lalu simpan token di config gallery-dl."),
                    ("Danbooru", "Atur username dan API key di config gallery-dl."),
                    ("e621", "Atur username dan API key di config gallery-dl."),
                    ("Sankaku dan situs sejenis", "Atur username dan password hanya jika extractor membutuhkan akun."),
                    ("Lokasi config", "Windows memakai AppData Roaming. macOS dan Linux biasanya memakai ~/.config/gallery-dl/config.json."),
                ]),
            ],
        }

        # Unified Help/Guideline content. This replaces the old split between
        # the small "? Help" popup and the separate Guide item.
        CONTENT_EN["setup"] = [
            (f"❔  Quick Start — ID {_STABILIZATION_PATCH_ID}", "accent", [
                ("1. Add URLs", "Paste a URL, optional filename, then press Add or Enter. Queue items added while a download is running stay queued for the next run."),
                ("2. Choose mode", "Use Auto or ICC Folder for the safest normal workflow. Auto can output folders or ZIPs from Settings → Auto Detect Output."),
                ("3. Download", "Download All processes a snapshot of the current queue. New links added during the run are not deleted when the active run finishes."),
                ("4. Preview/Serve", "Preview checks detectability; Serve starts/stops localhost preview for downloaded ICC folders."),
            ]),
            ("🧭  Current Toolbar", "muted", [
                ("Top bar", "Download All, Preview, Pause/Continue, Start/Stop Serve, Folder, and pinned status/progress."),
                ("Header tools", "Diagnostics sits next to Help / Guide so checks are one click away."),
                ("Recovery bar", "Download toggles, Retry Assets, Retry Images, Retry Audio, Settings, Reports Center, and CYOA Manager."),
                ("Settings", "Default Auto Output stays at the top. Settings stays open when you open/edit files, config, cache, viewers, or update tools."),
                ("Reports", "Opens the modern Reports Center for last-run results, filterable success/failure cards, CSV export, and failed-URL copy. Diagnostics remains one click away next to Help / Guide."),
            ]),
            ("🛡  Stability Rules", "green", [
                ("Compatibility", "Do not change output names, folder layout, embedded JSON, ZIP/report format, or existing CLI flags."),
                ("Secrets", "API keys, tokens, cookies, passwords, and bearer credentials must never be printed to logs, reports, settings export, or console."),
                ("Queue safety", "Download All uses a snapshot; new links added mid-run stay queued and are not removed when the active run finishes."),
                ("UI safety", "Keep primary controls visible without horizontal scrolling; long status text must never push Start/Stop Serve or Folder out of view."),
                ("Manual checks", "GUI rendering, Serve overlay drag/close/reopen, live browser preview, and real downloads still need manual testing on Windows."),
            ]),
        ]
        CONTENT_EN["import"] = [
            ("📥  Batch Import Format", "accent", [
                ("CSV/XLSX columns", "url is required. filename/name/output/title is optional. mode/output_mode/type is optional."),
                ("Recommended columns", "Use: url | filename | mode | notes. Only url is required; notes is ignored by the importer and is safe for your own comments."),
                ("TXT format", "One URL per line, or: URL | Filename | mode."),
                ("Mode values", "embed, zip, both, website_zip, website_folder, pure_website_zip, pure_website_folder, cyoap_vue_zip, cyoap_vue_folder, auto."),
                ("Auto default output", "If a row mode is auto, the detected ICC/cyoap_vue result follows Settings → Auto Detect Output: Folder or ZIP."),
                ("Invalid rows", "Rows without a valid http:// or https:// URL are skipped safely."),
            ]),
            ("🧾  Excel / CSV Template Example", "green", [
                ("Excel row 1", "A1=url | B1=filename | C1=mode | D1=notes"),
                ("Excel row 2", "A2=https://author.neocities.org/cyoa/ | B2=Example_One | C2=website_folder | D2=normal offline ICC folder"),
                ("Excel row 3", "A3=https://example.com/story/ | B3=Example_Zip | C3=website_zip | D3=compress ICC output"),
                ("Excel row 4", "A4=https://example.com/project/ | B4=Example_JSON | C4=embed | D4=single embedded JSON backup"),
                ("CSV line", "url,filename,mode,notes"),
                ("CSV sample", "https://author.neocities.org/cyoa/,Example_One,website_folder,normal offline ICC folder"),
                ("Google Sheets", "Use the same columns, then import the sheet URL; the app converts Google Sheets links to CSV export when possible."),
                ("Notes column", "notes is only for your own description; the importer ignores it safely."),
            ]),
            ("✅  Import Safety Rules", "yellow", [
                ("Blank mode", "If mode is empty, the current GUI/CLI default mode is used."),
                ("Unknown mode", "Unknown mode values are logged as warnings and safely fall back to the default."),
                ("Notes column", "notes is intentionally ignored by the importer, so it is safe for personal comments."),
                ("No formulas needed", "The file only needs plain cell values; formulas, macros, and styling are not required."),
            ]),
            ("🔧  Verification", "muted", [
                ("Compile", "python -m py_compile cyoa_downloader.py"),
                ("Self-test", "python cyoa_downloader.py --self-test"),
                ("Dependencies", "python cyoa_downloader.py --dependency-check"),
                ("Help", "python cyoa_downloader.py --help"),
            ]),
            ("🧯  Troubleshooting", "yellow", [
                ("Missing images", "Open failed_images.txt, then use Retry Images."),
                ("Broken ICC folder", "Use Serve instead of opening index.html directly."),
                ("Cloudflare", "Configure Cloudflare/FlareSolverr from Settings, then retry."),
                ("YouTube audio", "Install yt-dlp and ffmpeg; use Retry Audio after checking cookies."),
            ]),
        ]
        CONTENT_EN["setup"].append(
            ("JavaScript Website Archive", "accent", [
                ("Where", "Open Settings, then use the JavaScript Archive Policy card."),
                ("Classic", "Keeps the historical single-page flow and remains the default."),
                ("Smart", "Adds bounded same-story route crawling for choices that navigate to other pages."),
                ("Browser", "Adds runtime asset observation for lazy loading, SPA/Next.js, and JavaScript-built galleries."),
                ("Auto", "Profiles project data, CYOA.CAFE records, routes, and runtime signals, then chooses the lightest complete pipeline."),
                ("Safe interaction", "Allowlisted non-form controls only; mutation requests and navigation are blocked."),
                ("Limits", "Max pages accepts 1..5000; max depth accepts 0..100. Large stories can start at 800 pages / depth 30."),
                ("Output mode", "Use Pure Website Folder for custom sites that do not expose a standard project.json."),
                ("Preview", "Use Serve for local HTTP preview; file:// can break modules, fetch, and browser security rules."),
            ])
        )
        CONTENT_ID["setup"] = [
            (f"❔  Mulai Cepat — ID {_STABILIZATION_PATCH_ID}", "accent", [
                ("1. Tambah URL", "Tempel URL, isi filename bila perlu, lalu tekan Tambah atau Enter. Link baru yang ditambahkan saat download berjalan tetap tersimpan untuk run berikutnya."),
                ("2. Pilih mode", "Gunakan Auto atau ICC Folder untuk alur normal paling aman. Output Auto bisa folder atau ZIP lewat Pengaturan → Output Auto Detect."),
                ("3. Download", "Download All memproses snapshot queue saat tombol ditekan. Link baru tidak ikut hilang saat run aktif selesai."),
                ("4. Preview/Serve", "Preview mengecek deteksi awal; Serve menjalankan atau menghentikan localhost preview untuk ICC folder."),
            ]),
            ("🧭  Toolbar Saat Ini", "muted", [
                ("Bar atas", "Download Semua, Pratinjau, Jeda/Lanjutkan, Mulai/Hentikan Server, Folder, serta status/progress yang dipin."),
                ("Tool header", "Diagnostik berada di sebelah Bantuan/Panduan agar pengecekan bisa dibuka satu klik."),
                ("Bar recovery", "Toggle Download, Ulang Aset, Ulang Gambar, Ulang Audio, Cek Batch, Pengaturan, Laporan, dan CYOA Manager."),
                ("Pengaturan", "settings.json, folder settings, ekspor/impor, Output Auto Detect, AI Assist, config gallery-dl, Cloudflare/FlareSolverr, Viewer Offline, cache, dan update."),
                ("Laporan", "Membuka hasil run/report terakhir. Area teks di bawah toolbar adalah viewer log sebenarnya."),
            ]),
            ("🛡  Aturan Stabilitas", "green", [
                ("Kompatibilitas", "Jangan ubah nama output, struktur folder, embedded JSON, format ZIP/report, atau flag CLI lama."),
                ("Secret", "API key, token, cookie, password, dan bearer credential tidak boleh muncul di log, report, export settings, atau console."),
                ("Keamanan queue", "Download All memakai snapshot; link baru yang ditambahkan di tengah run tetap antre dan tidak dihapus saat run aktif selesai."),
                ("Keamanan UI", "Kontrol utama harus terlihat tanpa scroll horizontal; teks status panjang tidak boleh mendorong tombol Mulai/Hentikan Server atau Folder."),
                ("Uji manual", "Render GUI, drag/close/reopen Serve overlay, browser preview live, dan download real tetap perlu diuji manual di Windows."),
            ]),
        ]
        CONTENT_ID["import"] = [
            ("📥  Format Batch Import", "accent", [
                ("Kolom CSV/XLSX", "url wajib. filename/name/output/title opsional. mode/output_mode/type opsional."),
                ("Kolom rekomendasi", "Gunakan: url | filename | mode | notes. Hanya url yang wajib; notes diabaikan importer dan aman untuk catatan sendiri."),
                ("Format TXT", "Satu URL per baris, atau: URL | Filename | mode."),
                ("Nilai mode", "embed, zip, both, website_zip, website_folder, pure_website_zip, pure_website_folder, cyoap_vue_zip, cyoap_vue_folder, auto."),
                ("Default output Auto", "Jika mode baris adalah auto, hasil deteksi ICC/cyoap_vue mengikuti Pengaturan → Output Auto Detect: Folder atau ZIP."),
                ("Baris invalid", "Baris tanpa URL http:// atau https:// dilewati dengan aman."),
            ]),
            ("🧾  Contoh Template Excel / CSV", "green", [
                ("Excel baris 1", "A1=url | B1=filename | C1=mode | D1=notes"),
                ("Excel baris 2", "A2=https://author.neocities.org/cyoa/ | B2=Contoh_Satu | C2=website_folder | D2=folder ICC offline normal"),
                ("Excel baris 3", "A3=https://example.com/story/ | B3=Contoh_Zip | C3=website_zip | D3=output website dikompres"),
                ("Excel baris 4", "A4=https://example.com/project/ | B4=Contoh_JSON | C4=embed | D4=backup JSON embedded satu file"),
                ("Baris CSV", "url,filename,mode,notes"),
                ("Contoh CSV", "https://author.neocities.org/cyoa/,Contoh_Satu,website_folder,folder ICC offline normal"),
                ("Google Sheets", "Gunakan kolom yang sama, lalu import URL sheet; app mengubah link Google Sheets ke CSV export jika memungkinkan."),
                ("Kolom notes", "notes hanya untuk catatan pribadi; importer mengabaikannya dengan aman."),
            ]),
            ("✅  Aturan Aman Import", "yellow", [
                ("Mode kosong", "Jika mode dikosongkan, program memakai mode default dari GUI/CLI saat ini."),
                ("Mode tidak dikenal", "Nilai mode yang tidak dikenal hanya dicatat sebagai peringatan dan jatuh balik ke default."),
                ("Kolom notes", "notes sengaja diabaikan importer, jadi aman untuk komentar pribadi."),
                ("Tidak perlu formula", "File cukup berisi nilai sel biasa; formula, macro, dan styling tidak dibutuhkan."),
            ]),
            ("🔧  Verifikasi", "muted", [
                ("Compile", "python -m py_compile cyoa_downloader.py"),
                ("Self-test", "python cyoa_downloader.py --self-test"),
                ("Dependency", "python cyoa_downloader.py --dependency-check"),
                ("Help", "python cyoa_downloader.py --help"),
            ]),
            ("🧯  Troubleshooting", "yellow", [
                ("Gambar hilang", "Buka failed_images.txt, lalu pakai Retry Images."),
                ("Folder ICC rusak", "Gunakan Serve, jangan buka index.html langsung."),
                ("Cloudflare", "Atur Cloudflare/FlareSolverr dari Settings, lalu retry."),
                ("YouTube audio", "Install yt-dlp dan ffmpeg; gunakan Retry Audio setelah cek cookie."),
            ]),
        ]
        CONTENT_ID["setup"].append(
            ("Arsip Website JavaScript", "accent", [
                ("Lokasi", "Buka Settings, lalu gunakan kartu Kebijakan Arsip JavaScript."),
                ("Classic", "Mempertahankan alur satu halaman lama dan tetap menjadi default."),
                ("Smart", "Menambahkan crawl rute cerita yang dibatasi untuk pilihan yang berpindah halaman."),
                ("Browser", "Menambahkan observasi aset runtime untuk lazy loading, SPA/Next.js, dan galeri buatan JavaScript."),
                ("Auto", "Mengenali project data, record CYOA.CAFE, route, dan signal runtime lalu memilih pipeline paling ringan yang lengkap."),
                ("Interaksi aman", "Hanya kontrol non-form dalam allowlist; request mutasi dan navigasi diblokir."),
                ("Batas", "Maks. halaman menerima 1..5000; maks. kedalaman 0..100. Cerita besar dapat dimulai dari 800 halaman / depth 30."),
                ("Mode output", "Gunakan Pure Website Folder untuk situs custom tanpa project.json standar."),
                ("Pratinjau", "Gunakan Serve untuk HTTP lokal; file:// dapat merusak module, fetch, dan aturan keamanan browser."),
            ])
        )


        CONTENT_EN["cli"] = [
            ("⌨  CLI Quick Reference", "accent", [
                ("GUI", "python cyoa_downloader.py --gui, or run without arguments to open the GUI."),
                ("Normal download", "python cyoa_downloader.py <URL> -o <output_folder>"),
                ("ICC Folder", "python cyoa_downloader.py <URL> --icc-folder -o <output_folder>"),
                ("ICC ZIP", "python cyoa_downloader.py <URL> --icc -o <output_folder>"),
                ("Embedded JSON", "python cyoa_downloader.py <URL> --embed -o <output_folder>"),
                ("ZIP / Both", "Use --zip for project.json + assets ZIP, or --both to generate embedded JSON and ZIP together."),
                ("Serve", "Add --serve after a ICC-folder download to start local preview."),
                ("JavaScript archive", "Use --pure-website-folder --archive-strategy auto; use browser explicitly only when debugging runtime capture."),
            ]),
            ("🧪  Verification Commands", "green", [
                ("Compile", "python -m py_compile cyoa_downloader.py"),
                ("Self-test", "python cyoa_downloader.py --self-test"),
                ("Dependencies", "python cyoa_downloader.py --dependency-check"),
                ("CLI help", "python cyoa_downloader.py --help"),
                ("Userscript info", "python cyoa_downloader.py --userscript-info"),
            ]),
            ("🔐  Secret-safe settings", "yellow", [
                ("Export", "python cyoa_downloader.py --export-settings settings_export.json"),
                ("Import", "python cyoa_downloader.py --import-settings settings_export.json"),
                ("AI key cleanup", "python cyoa_downloader.py --ai-clear-key"),
                ("Policy", "API keys, tokens, cookies, passwords, and bearer credentials are not written to exports/reports/logs."),
            ]),
            ("🌐  Network / fallback flags", "muted", [
                ("Cloudflare", "Use --cf-bypass, --cf-mode, --flaresolverr-url, and --flaresolverr-test for Cloudflare-protected pages."),
                ("Audio", "Use --no-ytdlp to disable yt-dlp audio recovery when needed."),
                ("Scanners", "Use --no-deep-scan or --no-selenium only for troubleshooting; they reduce recovery coverage."),
                ("itch.io", "Use --itch, --itch-test, and --itch-mirror-web for itch.io-specific downloads."),
            ]),
        ]
        CONTENT_EN["files"] = [
            ("📁  Output contract", "accent", [
                ("Do not rename contract files", "project.json, backup_report.txt, failed_assets.txt, failed_images.txt, skipped_youtube_audio.txt, and cyoa_downloader.log are part of the stable workflow."),
                ("ICC Folder", "Contains local HTML/CSS/JS/assets and patched project data for localhost preview."),
                ("ZIP", "Contains project.json and asset folders using the existing output naming rules."),
                ("Embedded JSON", "Keeps image payloads inside JSON while preserving the old embedded structure."),
            ]),
            ("🧾  Reports", "muted", [
                ("backup_report.txt", "Primary download manifest and failure summary."),
                ("failed_assets.txt / failed_images.txt", "Recovery source for Retry Assets and Retry Images."),
                ("skipped_youtube_audio.txt", "Recovery source for Retry Audio."),
                ("cyoa_diagnostics.txt", "Optional Diagnostics export saved manually or to the output folder."),
            ]),
            ("🗂  User data locations", "green", [
                ("settings.json", "~/.cyoa_downloader/settings.json"),
                ("download_history.json", "~/.cyoa_downloader/download_history.json"),
                ("gallery-dl config", "%APPDATA%\\gallery-dl\\config.json on Windows, ~/.config/gallery-dl/config.json on macOS/Linux."),
                ("Image cache", "Stored under the app cache folder and can be cleared from Settings."),
            ]),
        ]
        CONTENT_EN["settings"] = [
            ("⚙  Settings / Maintenance", "accent", [
                ("Open settings.json", "Opens the active settings file in an editable text editor."),
                ("Open settings folder", "Opens the folder that stores settings.json and download_history.json."),
                ("Export/Import Settings", "Creates or merges a secret-safe settings envelope."),
                ("Default Auto Output", "Shown first in Settings. Choose Folder or ZIP; mode=auto rows and GUI Auto follow this switch."),
                ("JavaScript Archive Policy", "Configure Auto/Classic/Smart/Browser, safe interaction, runtime, scroll, click, and route limits here."),
                ("AI Assist Settings", "Configures provider/model/API-key handling for optional AI recovery features."),
                ("Open gallery-dl config", "Creates the gallery-dl config if missing, then opens it for editing."),
                ("Cloudflare / FlareSolverr", "Configures Cloudflare fallback behavior and tests FlareSolverr connectivity."),
                ("Offline Viewers", "Registers viewer ZIPs for local viewer workflows."),
            ]),
            ("🌐  JavaScript Archive Policy options", "accent", [
                ("Strategy", "Auto profiles the site and chooses the lightest complete pipeline. Classic downloads the entry page and static assets. Smart also crawls bounded same-origin story routes. Browser additionally renders JavaScript and observes runtime/lazy-loaded assets."),
                ("Safe interaction", "Safe performs incremental scroll and clicks only allowlisted, non-form controls while blocking mutation requests and unsafe navigation. Off renders and scrolls but never clicks controls."),
                ("Max pages", "Hard ceiling for same-origin routes saved by the archive. It is a safety limit, not a required target."),
                ("Max depth", "Maximum route hops from the entry page. Zero archives only the entry route; depth 30 is a practical starting point for large branching stories."),
                ("Runtime pages", "Maximum number of routes opened in the browser engine. Keep this much lower than Max pages because browser rendering is the expensive stage."),
                ("Settle time", "Milliseconds to wait after page load, scroll, or safe click so delayed network requests and animations can expose assets."),
                ("Scroll steps", "Maximum incremental scroll operations used to trigger lazy loading. The run stops early when no new content appears."),
                ("Max safe clicks", "Maximum allowlisted clicks per runtime page. Login, submit, payment, upload, delete, vote, and similar controls remain blocked."),
                ("No-progress rounds", "Stops runtime exploration after this many consecutive rounds discover no new routes, assets, or useful responses."),
                ("Recommended start", "Use Auto + Safe. Small gallery: 50 pages / depth 10 / runtime 6. Medium story: 300 / 30 / 12. Large story: 800 / 30 / 20, then raise limits only when the manifest reaches a cap."),
            ]),
            ("🩺  Diagnostics", "green", [
                ("What it checks", "Python modules, external binaries, settings, output folder, cache, gallery-dl config, proxy, reports, DNS, and internet connectivity."),
                ("Colors", "PASS is green, WARN is yellow, FAIL is red, and section headers are blue."),
                ("Export", "Use Copy, Save As, or Save to Output when sharing a diagnostic result."),
            ]),
            ("🧠  How the program works", "muted", [
                ("1. Detect", "The URL is probed to identify project JSON, website assets, CYOA-P Vue data, or fallback-only pages."),
                ("2. Scan", "HTML/CSS/JS/project JSON are scanned for images, fonts, audio, scripts, and nested asset references."),
                ("3. Download", "Assets are downloaded with retry/caching while preserving existing naming and folder rules."),
                ("4. Patch", "Local references are rewritten only where needed so offline preview can load assets."),
                ("5. Report", "The manifest, failure logs, and diagnostics help recover missing assets without changing old outputs."),
            ]),
        ]
        CONTENT_ID["cli"] = [
            ("⌨  Ringkasan CLI", "accent", [
                ("GUI", "python cyoa_downloader.py --gui, atau jalankan tanpa argumen untuk membuka GUI."),
                ("Download normal", "python cyoa_downloader.py <URL> -o <folder_output>"),
                ("Folder ICC", "python cyoa_downloader.py <URL> --icc-folder -o <folder_output>"),
                ("ZIP ICC", "python cyoa_downloader.py <URL> --icc -o <folder_output>"),
                ("Embedded JSON", "python cyoa_downloader.py <URL> --embed -o <folder_output>"),
                ("ZIP / Both", "Gunakan --zip untuk project.json + aset dalam ZIP, atau --both untuk embedded JSON dan ZIP sekaligus."),
                ("Serve", "Tambahkan --serve setelah download ICC-folder untuk menjalankan pratinjau lokal."),
                ("Arsip JavaScript", "Gunakan --pure-website-folder --archive-strategy browser --archive-max-pages 800 --archive-max-depth 30 untuk cerita dinamis besar."),
            ]),
            ("🧪  Command Verifikasi", "green", [
                ("Compile", "python -m py_compile cyoa_downloader.py"),
                ("Self-test", "python cyoa_downloader.py --self-test"),
                ("Dependency", "python cyoa_downloader.py --dependency-check"),
                ("Help CLI", "python cyoa_downloader.py --help"),
                ("Info userscript", "python cyoa_downloader.py --userscript-info"),
            ]),
            ("🔐  Settings aman-secret", "yellow", [
                ("Ekspor", "python cyoa_downloader.py --export-settings settings_export.json"),
                ("Impor", "python cyoa_downloader.py --import-settings settings_export.json"),
                ("Bersihkan key AI", "python cyoa_downloader.py --ai-clear-key"),
                ("Kebijakan", "API key, token, cookie, password, dan bearer credential tidak ditulis ke export/report/log."),
            ]),
            ("🌐  Flag jaringan / fallback", "muted", [
                ("Cloudflare", "Pakai --cf-bypass, --cf-mode, --flaresolverr-url, dan --flaresolverr-test untuk halaman Cloudflare."),
                ("Audio", "Pakai --no-ytdlp kalau ingin mematikan recovery audio yt-dlp."),
                ("Scanner", "Pakai --no-deep-scan atau --no-selenium hanya untuk troubleshooting karena coverage recovery berkurang."),
                ("itch.io", "Pakai --itch, --itch-test, dan --itch-mirror-web untuk download khusus itch.io."),
            ]),
        ]
        CONTENT_ID["files"] = [
            ("📁  Kontrak output", "accent", [
                ("Jangan ubah nama file kontrak", "project.json, backup_report.txt, failed_assets.txt, failed_images.txt, skipped_youtube_audio.txt, dan cyoa_downloader.log adalah bagian workflow stabil."),
                ("Folder ICC", "Berisi HTML/CSS/JS/aset lokal dan data project yang sudah dipatch untuk pratinjau localhost."),
                ("ZIP", "Berisi project.json dan folder aset dengan aturan nama output lama."),
                ("Embedded JSON", "Menyimpan payload gambar di dalam JSON sambil menjaga struktur embedded lama."),
            ]),
            ("🧾  Laporan", "muted", [
                ("backup_report.txt", "Manifest utama download dan ringkasan kegagalan."),
                ("failed_assets.txt / failed_images.txt", "Sumber recovery untuk Retry Assets dan Retry Images."),
                ("skipped_youtube_audio.txt", "Sumber recovery untuk Retry Audio."),
                ("cyoa_diagnostics.txt", "Export Diagnostics opsional yang disimpan manual atau ke folder output."),
            ]),
            ("🗂  Lokasi data user", "green", [
                ("settings.json", "~/.cyoa_downloader/settings.json"),
                ("download_history.json", "~/.cyoa_downloader/download_history.json"),
                ("config gallery-dl", "%APPDATA%\\gallery-dl\\config.json di Windows, ~/.config/gallery-dl/config.json di macOS/Linux."),
                ("Cache gambar", "Disimpan di folder cache aplikasi dan bisa dibersihkan dari Settings."),
            ]),
        ]
        CONTENT_ID["settings"] = [
            ("⚙  Pengaturan / Maintenance", "accent", [
                ("Buka settings.json", "Membuka file settings aktif di text editor yang bisa diedit dan disave."),
                ("Buka folder settings", "Membuka folder yang menyimpan settings.json dan download_history.json."),
                ("Ekspor/Impor Settings", "Membuat atau merge envelope settings yang aman dari secret."),
                ("Default Output Auto", "Ditampilkan paling atas di Settings. Pilih Folder atau ZIP; baris mode=auto dan GUI Auto mengikuti switch ini."),
                ("Kebijakan Arsip JavaScript", "Atur Auto/Classic/Smart/Browser, interaksi aman, runtime, scroll, klik, serta batas route di sini."),
                ("Pengaturan AI Assist", "Mengatur provider/model/penyimpanan API key untuk fitur recovery AI opsional."),
                ("Buka config gallery-dl", "Membuat config gallery-dl jika belum ada, lalu membukanya untuk diedit."),
                ("Cloudflare / FlareSolverr", "Mengatur perilaku fallback Cloudflare dan mengetes koneksi FlareSolverr."),
                ("Viewer Offline", "Mendaftarkan ZIP viewer untuk workflow viewer lokal."),
            ]),
            ("🌐  Opsi Kebijakan Arsip JavaScript", "accent", [
                ("Strategi", "Auto memprofilkan situs lalu memilih pipeline lengkap yang paling ringan. Classic mengunduh halaman awal dan aset statis. Smart juga mengikuti rute cerita same-origin secara terbatas. Browser menambahkan render JavaScript dan observasi aset runtime/lazy-load."),
                ("Interaksi aman", "Safe melakukan scroll bertahap dan hanya mengeklik kontrol non-form dalam allowlist sambil memblokir request mutasi dan navigasi berbahaya. Off tetap merender dan scroll tetapi tidak pernah mengeklik kontrol."),
                ("Maks. halaman", "Batas keras jumlah rute same-origin yang disimpan. Angka ini adalah pagar keselamatan, bukan target yang harus dihabiskan."),
                ("Maks. kedalaman", "Maksimum lompatan rute dari halaman awal. Nol hanya mengarsipkan rute awal; depth 30 adalah titik awal praktis untuk cerita bercabang besar."),
                ("Halaman runtime", "Maksimum rute yang dibuka mesin browser. Jaga jauh lebih kecil dari Maks. halaman karena render browser merupakan tahap mahal."),
                ("Waktu tunggu", "Milidetik menunggu setelah load, scroll, atau klik aman agar request terlambat dan animasi sempat menampilkan aset."),
                ("Langkah scroll", "Maksimum scroll bertahap untuk memicu lazy loading. Proses berhenti lebih awal jika tidak ada konten baru."),
                ("Maks. klik aman", "Maksimum klik allowlist per halaman runtime. Login, submit, pembayaran, upload, hapus, vote, dan kontrol serupa tetap diblokir."),
                ("Putaran tanpa progres", "Menghentikan eksplorasi runtime setelah sejumlah putaran berturut-turut tidak menemukan rute, aset, atau respons berguna baru."),
                ("Rekomendasi awal", "Gunakan Auto + Safe. Galeri kecil: 50 halaman / depth 10 / runtime 6. Cerita sedang: 300 / 30 / 12. Cerita besar: 800 / 30 / 20; naikkan hanya jika manifest benar-benar menyentuh batas."),
            ]),
            ("🩺  Diagnostik", "green", [
                ("Yang dicek", "Modul Python, binary eksternal, settings, folder output, cache, config gallery-dl, proxy, report, DNS, dan internet."),
                ("Warna", "PASS hijau, WARN kuning, FAIL merah, dan judul section biru."),
                ("Export", "Gunakan Salin, Simpan Sebagai, atau Simpan ke Output saat ingin membagikan hasil diagnosis."),
            ]),
            ("🧠  Cara kerja program", "muted", [
                ("1. Deteksi", "URL diprobe untuk menemukan project JSON, aset website, data CYOA-P Vue, atau halaman fallback-only."),
                ("2. Scan", "HTML/CSS/JS/project JSON discan untuk gambar, font, audio, script, dan referensi aset bersarang."),
                ("3. Download", "Aset diunduh dengan retry/cache sambil menjaga aturan nama dan folder lama."),
                ("4. Patch", "Referensi lokal ditulis ulang hanya jika diperlukan agar pratinjau offline bisa memuat aset."),
                ("5. Report", "Manifest, log gagal, dan diagnostics membantu recovery aset tanpa mengubah output lama."),
            ]),
        ]

        CONTENT = CONTENT_EN if is_en else CONTENT_ID

        COLOR_MAP = {
            "accent": "#3b82f6",
            "muted": "#64748b",
            "green": "#34d399",
            "yellow": "#fbbf24",
            "red": "#f87171",
        }

        def _render_spreadsheet_example(parent) -> None:
            """Render a small Excel-like table so batch import is easier to copy."""
            headers = ["url", "filename", "mode", "notes"]
            rows = (
                ["https://author.neocities.org/cyoa/", "Example_One" if is_en else "Contoh_Satu", "website_folder", "normal offline ICC folder" if is_en else "folder ICC offline normal"],
                ["https://example.com/story/", "Example_Zip" if is_en else "Contoh_Zip", "website_zip", "compressed ICC output" if is_en else "output website dikompres"],
                ["https://example.com/project/", "Example_JSON" if is_en else "Contoh_JSON", "embed", "single embedded JSON backup" if is_en else "backup JSON embedded satu file"],
                ["https://example.com/cyoa-vue/", "Example_CYOAP_Vue" if is_en else "Contoh_CYOAP_Vue", "cyoap_vue_folder", "CYOA-P Vue folder output" if is_en else "output folder CYOA-P Vue"],
                ["https://example.com/auto/", "Example_Auto" if is_en else "Contoh_Auto", "auto", "follows Settings → Default Auto Output" if is_en else "mengikuti Settings → Default Output Auto"],
            )
            sheet = ctk.CTkFrame(parent, fg_color=p["bg"], corner_radius=8)
            sheet.pack(fill="x", padx=12, pady=(2, 8))
            widths = [34, 330, 180, 160, 360]

            def _cell(r, c, text, *, bg, fg, bold=False, anchor="w", width=120, wrap=180):
                cell = ctk.CTkFrame(sheet, fg_color=bg, corner_radius=0)
                cell.grid(row=r, column=c, sticky="nsew", padx=(0, 1), pady=(0, 1))
                label = ctk.CTkLabel(
                    cell, text=text, anchor=anchor, justify="left", width=width,
                    font=ctk.CTkFont("Consolas" if c > 0 else "Segoe UI", 9, "bold" if bold else "normal"),
                    text_color=fg, wraplength=wrap,
                )
                label.pack(fill="both", expand=True, padx=6, pady=5)
                return cell

            for c, wdt in enumerate(widths):
                sheet.grid_columnconfigure(c, weight=(1 if c in (1, 4) else 0), minsize=wdt)

            _cell(0, 0, "", bg="#e5e7eb", fg="#111827", bold=True, anchor="center", width=widths[0], wrap=widths[0])
            for c, letter in enumerate(["A", "B", "C", "D"], start=1):
                _cell(0, c, letter, bg="#e5e7eb", fg="#111827", bold=True, anchor="center", width=widths[c], wrap=widths[c]-12)
            _cell(1, 0, "1", bg="#f3f4f6", fg="#374151", anchor="center", width=widths[0], wrap=widths[0])
            for c, text in enumerate(headers, start=1):
                _cell(1, c, text, bg="#2563eb", fg="#ffffff", bold=True, anchor="center", width=widths[c], wrap=widths[c]-12)
            for r_i, data in enumerate(rows, start=2):
                row_bg = "#dbeafe" if r_i % 2 == 0 else "#f8fafc"
                _cell(r_i, 0, str(r_i), bg="#f3f4f6", fg="#374151", anchor="center", width=widths[0], wrap=widths[0])
                for c, text in enumerate(data, start=1):
                    _cell(r_i, c, text, bg=row_bg, fg="#0f172a", width=widths[c], wrap=widths[c]-12)

            csv_title = "CSV equivalent" if is_en else "Padanan CSV"
            csv_text = (
                "url,filename,mode,notes\n"
                "https://author.neocities.org/cyoa/,Example_One,website_folder,normal offline ICC folder\n"
                "https://example.com/story/,Example_Zip,website_zip,compressed ICC output\n"
                "https://example.com/auto/,Example_Auto,auto,follows Default Auto Output"
                if is_en else
                "url,filename,mode,notes\n"
                "https://author.neocities.org/cyoa/,Contoh_Satu,website_folder,folder ICC offline normal\n"
                "https://example.com/story/,Contoh_Zip,website_zip,output website dikompres\n"
                "https://example.com/auto/,Contoh_Auto,auto,mengikuti Default Output Auto"
            )
            note = ctk.CTkFrame(parent, fg_color=p["surface2"], corner_radius=6)
            note.pack(fill="x", padx=12, pady=(0, 2))
            ctk.CTkLabel(note, text=csv_title, width=145, anchor="w",
                         font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=p["fg"]).grid(row=0, column=0, padx=(12, 6), pady=6, sticky="nw")
            ctk.CTkLabel(note, text=csv_text, anchor="w", justify="left",
                         font=ctk.CTkFont("Consolas", 9), text_color=p["muted"], wraplength=760).grid(row=0, column=1, padx=(0, 12), pady=6, sticky="ew")
            note.grid_columnconfigure(1, weight=1)

            legend_items = (
                [("url", "Required URL, must start with http:// or https://."),
                 ("filename", "Optional output name."),
                 ("mode", "Optional: embed, zip, both, website_folder, website_zip, cyoap_vue_folder, cyoap_vue_zip, auto."),
                 ("notes", "Personal notes only; importer ignores this column safely."),
                 ("auto", "Uses Settings → Default Auto Output: Folder or ZIP.")]
                if is_en else
                [("url", "URL wajib, harus diawali http:// atau https://."),
                 ("filename", "Nama output opsional."),
                 ("mode", "Opsional: embed, zip, both, website_folder, website_zip, cyoap_vue_folder, cyoap_vue_zip, auto."),
                 ("notes", "Catatan pribadi saja; importer mengabaikan kolom ini dengan aman."),
                 ("auto", "Mengikuti Settings → Default Output Auto: Folder atau ZIP.")]
            )
            for feat, desc in legend_items:
                row = ctk.CTkFrame(parent, fg_color=p["surface2"], corner_radius=6)
                row.pack(fill="x", padx=12, pady=1)
                row.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(row, text=feat, width=145, anchor="w",
                             font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=p["fg"]).grid(row=0, column=0, padx=(12, 6), pady=5, sticky="w")
                ctk.CTkLabel(row, text=desc, anchor="w", font=ctk.CTkFont("Segoe UI", 10),
                             text_color=p["muted"], wraplength=760, justify="left").grid(row=0, column=1, padx=(0, 12), pady=5, sticky="ew")

        def _render(tab: str) -> None:
            for w in content_area.winfo_children():
                w.destroy()
            for section_title, color_key, items in CONTENT.get(tab, []):
                color = COLOR_MAP.get(color_key, p["muted"])
                sh = ctk.CTkFrame(content_area, fg_color=p["surface"], corner_radius=8)
                sh.pack(fill="x", padx=12, pady=(10, 2))
                ctk.CTkLabel(
                    sh,
                    text=section_title,
                    font=ctk.CTkFont("Segoe UI", 12, "bold"),
                    text_color=color,
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(10, 4))
                if "Excel / CSV Template" in section_title or "Template Excel / CSV" in section_title:
                    _render_spreadsheet_example(content_area)
                    ctk.CTkFrame(content_area, height=4, fg_color="transparent").pack()
                    continue
                for feat, desc in items:
                    row = ctk.CTkFrame(content_area, fg_color=p["surface2"], corner_radius=6)
                    row.pack(fill="x", padx=12, pady=1)
                    row.grid_columnconfigure(1, weight=1)
                    ctk.CTkLabel(
                        row,
                        text=feat,
                        width=145,
                        anchor="w",
                        font=ctk.CTkFont("Segoe UI", 10, "bold"),
                        text_color=p["fg"],
                    ).grid(row=0, column=0, padx=(12, 6), pady=6, sticky="w")
                    ctk.CTkLabel(
                        row,
                        text=desc,
                        anchor="w",
                        font=ctk.CTkFont("Segoe UI", 10),
                        text_color=p["muted"],
                        wraplength=660,
                        justify="left",
                    ).grid(row=0, column=1, padx=(0, 12), pady=6, sticky="ew")
                ctk.CTkFrame(content_area, height=4, fg_color="transparent").pack()

        tab_btns = {}

        def _select_tab(t: str) -> None:
            tab_var.set(t)
            _render(t)
            for tv, btn in tab_btns.items():
                btn.configure(
                    fg_color="#3b82f6" if tv == t else p["surface"],
                    text_color="#ffffff" if tv == t else p["muted"],
                )

        for idx, (val, label) in enumerate(TABS):
            btn = ctk.CTkButton(
                tab_frame,
                text=label,
                height=24,
                font=ctk.CTkFont("Segoe UI", 8, "bold"),
                corner_radius=6,
                fg_color=p["surface"],
                hover_color=p["surface2"],
                text_color=p["muted"],
                command=lambda v=val: _select_tab(v),
            )
            btn.grid(row=(0 if idx < 7 else 1), column=(idx % 7), padx=3, pady=3, sticky="ew")
            tab_btns[val] = btn

        available_tabs = dict(TABS)
        normalized_initial = "cookies" if initial_tab == "cookie" else initial_tab
        _select_tab(normalized_initial if normalized_initial in available_tabs else "download")

    def _manage_offline_viewers(self) -> None:
        """
        GUI popup to manage offline viewer ZIPs.
        Users can: add ZIP, see registered viewers, remove viewers.
        """
        import customtkinter as ctk
        from tkinter import filedialog, messagebox

        p   = self._p()
        win = self._make_singleton_window("offline_viewers_legacy")
        if win is None:
            return
        win.title("Offline Viewers")
        win.geometry("680x460")
        win.grab_set()

        # ── Header ──────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(hdr, text="Offline Viewer Manager",
                     font=ctk.CTkFont("Segoe UI", 14, "bold"),
                     text_color=p["fg"]).pack(side="left")
        ctk.CTkButton(hdr, text="+ Add ZIP", width=90, height=30,
                      font=ctk.CTkFont("Segoe UI", 11),
                      fg_color="#3b82f6", hover_color="#2563eb",
                      command=lambda: _add_viewer()).pack(side="right")

        ctk.CTkLabel(win,
                     text="Upload offline viewer ZIPs (e.g. ICCPlus offline release). "
                          "The script will automatically match viewers with downloaded CYOAs.",
                     font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], wraplength=640,
                     justify="left").pack(anchor="w", padx=16, pady=(4, 8))

        # ── Viewer list ──────────────────────────────────────────────────
        frame = ctk.CTkScrollableFrame(win, fg_color=p["bg"],
                                        scrollbar_button_color=p["surface2"])
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        status_var = ctk.StringVar(value="")
        ctk.CTkLabel(win, textvariable=status_var,
                      font=ctk.CTkFont("Segoe UI", 10),
                      text_color=p["accent"]).pack(pady=(0, 8))

        def _refresh_list():
            for w in frame.winfo_children():
                w.destroy()
            manifest = _load_viewers_manifest()
            if not manifest:
                ctk.CTkLabel(frame,
                              text="No offline viewers registered yet.\n"
                                   "Click '+ Add ZIP' to add one.",
                              font=ctk.CTkFont("Segoe UI", 11),
                              text_color=p["muted"]).pack(pady=20)
                return
            for i, (vid, meta) in enumerate(manifest.items()):
                bg = p["surface"] if i % 2 == 0 else p["bg"]
                row = ctk.CTkFrame(frame, fg_color=bg, corner_radius=4)
                row.pack(fill="x", padx=4, pady=1)

                # Icon + name + type
                vtype = meta.get("viewer_type", "custom")
                icon  = {"icc_plus":"⚡","icc":"📄","cyoap_vue":"🌿","custom":"📦"}.get(vtype,"📦")
                left  = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", fill="x", expand=True, padx=8, pady=6)
                ctk.CTkLabel(left, text=f"{icon} {meta.get('name', vid)}",
                              font=ctk.CTkFont("Segoe UI", 12, "bold"),
                              text_color=p["fg"], anchor="w").pack(anchor="w")
                ctk.CTkLabel(left,
                              text=f"type: {vtype}  ·  entry: {meta.get('entry_point','index.html')}  "
                                   f"·  {meta.get('zip_filename','')}",
                              font=ctk.CTkFont("Segoe UI", 10),
                              text_color=p["muted"], anchor="w").pack(anchor="w")
                if meta.get("description"):
                    ctk.CTkLabel(left, text=meta["description"],
                                  font=ctk.CTkFont("Segoe UI", 10, "italic"),
                                  text_color=p["muted2"], anchor="w").pack(anchor="w")

                # Remove button
                ctk.CTkButton(row, text="Hapus", width=60, height=26,
                               font=ctk.CTkFont("Segoe UI", 10),
                               fg_color="#7f1d1d", hover_color="#991b1b",
                               text_color="#fca5a5",
                               command=lambda v=vid: _remove(v)).pack(
                    side="right", padx=8, pady=6)

        def _add_viewer():
            """Register a local offline viewer ZIP/RAR from the GUI."""
            zip_path = filedialog.askopenfilename(
                parent=win,
                title="Select offline viewer ZIP",
                filetypes=[("Viewer archives", "*.zip *.rar"), ("ZIP files", "*.zip"), ("RAR files", "*.rar"), ("All files", "*.*")]
            )
            if not zip_path:
                return
            # Ask for name and type
            name_win = ctk.CTkToplevel(win)
            self._apply_window_icon_to(name_win)
            name_win.title("Info Viewer")
            name_win.geometry("400x260")
            name_win.grab_set()

            ctk.CTkLabel(name_win, text="Nama viewer:",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(14,2))
            name_var = ctk.StringVar(value=os.path.splitext(os.path.basename(zip_path))[0])
            ctk.CTkEntry(name_win, textvariable=name_var, width=350).pack(padx=16)

            ctk.CTkLabel(name_win, text="Tipe viewer:",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(10,2))
            type_var = ctk.StringVar(value="icc_plus")
            for t in ["icc_plus", "icc", "cyoap_vue", "custom"]:
                ctk.CTkRadioButton(name_win, text=t, variable=type_var, value=t,
                                    font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=24)

            ctk.CTkLabel(name_win, text="Description (optional):",
                          font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=16, pady=(8,2))
            desc_var = ctk.StringVar()
            ctk.CTkEntry(name_win, textvariable=desc_var, width=350).pack(padx=16)

            def _do_register():
                vid = register_offline_viewer(
                    zip_path,
                    name=name_var.get().strip() or os.path.basename(zip_path),
                    viewer_type=type_var.get(),
                    description=desc_var.get().strip(),
                )
                name_win.destroy()
                if vid:
                    status_var.set(f"✓ Viewer '{vid}' berhasil didaftarkan.")
                    _refresh_list()
                else:
                    messagebox.showerror("Error", "Failed to register viewer. Check log for details.",
                                         parent=win)

            ctk.CTkButton(name_win, text="Daftarkan",
                           fg_color="#3b82f6", hover_color="#2563eb",
                           command=_do_register).pack(pady=12)

        def _check_icc_update():
            """Check GitHub for latest ICCPlus release and offer to download."""
            import threading
            status_var.set("Checking GitHub for latest ICCPlus release…")

            def _do_check():
                r = None
                try:
                    api = "https://api.github.com/repos/wahawa303/ICCPlus/releases/latest"
                    r   = fetch_response(api, timeout=8, extra_headers={"User-Agent": "CYOA-Downloader"})
                    if r is None or r.status_code != 200:
                        # v7.5.6 fix: capture the status text now — if r is None,
                        # the deferred lambda used to raise AttributeError on
                        # `r.status_code` inside the Tk callback.
                        code = getattr(r, "status_code", None)
                        msg = f"GitHub API: {code}" if code is not None else "GitHub API: no response"
                        win.after(0, lambda m=msg: status_var.set(m))
                        return
                    data  = r.json()
                    tag   = data.get("tag_name", "")
                    assets= data.get("assets", [])
                    # Look for local/offline ZIP asset
                    offline_asset = next(
                        (a for a in assets
                         if any(kw in a["name"].lower()
                                for kw in ["local", "offline", "standalone"])),
                        assets[0] if assets else None
                    )
                    if not offline_asset:
                        win.after(0, lambda: status_var.set("No downloadable asset found in latest release."))
                        return

                    asset_name = offline_asset["name"]
                    asset_url  = offline_asset["browser_download_url"]

                    # Check if this version is already registered
                    manifest = _load_viewers_manifest()
                    already  = any(tag in m.get("name","") or tag in vid
                                   for vid, m in manifest.items())

                    def _offer():
                        if already:
                            status_var.set(f"Already have {tag} registered.")
                            return
                        from tkinter import messagebox
                        if messagebox.askyesno(
                            "New ICCPlus Release",
                            f"Latest release: {tag}\nFile: {asset_name}\n\n"
                            f"Download and register? (~beberapa MB)",
                            parent=win
                        ):
                            _do_download(tag, asset_name, asset_url)

                    win.after(0, _offer)

                except Exception as e:
                    # v7.5.5 fix: capture message now — `e` is deleted when the
                    # except block exits, so the deferred lambda raised NameError.
                    win.after(0, lambda msg=str(e): status_var.set(f"Update check failed: {msg}"))
                finally:
                    if r is not None:
                        try:
                            r.close()
                        except Exception:
                            pass

            def _do_download(tag, asset_name, asset_url):
                status_var.set(f"Downloading {asset_name}…")

                def _dl():
                    try:
                        os.makedirs(_VIEWERS_DIR, exist_ok=True)
                        dest = os.path.join(_VIEWERS_DIR, asset_name)
                        sess = _get_shared_session(use_cf=False)
                        with sess.get(asset_url, stream=True, timeout=30,
                                      headers={"User-Agent": "CYOA-Downloader"}) as r:
                            r.raise_for_status()
                            total = int(r.headers.get("content-length", 0))
                            done  = 0
                            with open(dest, "wb") as f:
                                for chunk in r.iter_content(65536):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    done += len(chunk)
                                    if total:
                                        pct = done * 100 // total
                                        win.after(0, lambda p=pct: status_var.set(
                                            f"Downloading {asset_name}… {p}%"))

                        vid = register_offline_viewer(
                            dest, name=f"ICCPlus {tag} (auto)", viewer_type="icc_plus"
                        )
                        _v25_safe_after_widget(win, win, lambda: (
                            status_var.set(f"✓ {asset_name} registered as '{vid}'."),
                            _refresh_list()
                        ))
                    except Exception as e:
                        # v7.5.5 fix: same late-binding NameError as update check.
                        win.after(0, lambda msg=str(e): status_var.set(f"Download failed: {msg}"))

                threading.Thread(target=_dl, daemon=True).start()

            threading.Thread(target=_do_check, daemon=True).start()

        def _remove(vid: str):
            if messagebox.askyesno("Hapus Viewer",
                                    f"Hapus '{vid}' dari registry?\n(ZIP di-keep, tidak dihapus dari disk)",
                                    parent=win):
                unregister_offline_viewer(vid, delete_zip=False)
                status_var.set(f"Viewer '{vid}' dihapus.")
                _refresh_list()

        _refresh_list()

    def _toggle_server(self) -> None:
        if self._server_running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self, folder: "Optional[str]" = None) -> None:
        import customtkinter as ctk
        from tkinter import filedialog, messagebox
        import http.server, webbrowser, mimetypes

        # ── Lifecycle guard ───────────────────────────────────────────────
        # Item 6: respect the serve toggle. When off, never auto-start a server.
        if not _SERVE_ENABLED:
            logger.info("Serve preview disabled by toggle — not starting server.")
            try:
                from tkinter import messagebox as _mb
                _mb.showinfo("Serve disabled",
                             "Serve preview is turned off in settings.\n"
                             "Enable it in the toggles to use local preview.")
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _start_server (line 11545): %s", _ignored_exc)
            return
        # Prevent a second server from being launched on top of a running one.
        # Without this, a stray double-invoke would orphan the previous
        # serve_forever() thread and open a new browser tab — the "serve
        # reopens a CYOA that was already closed" symptom.
        if self._server_running:
            return
        # If a previous server object lingers (shutdown still settling), close
        # it before binding a new socket so we never leak a listening thread.
        _stale = self._server_obj
        if _stale is not None:
            self._server_obj = None
            try:
                _stale.shutdown()
                _stale.server_close()
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _start_server (line 11562): %s", _ignored_exc)

        # Pick folder to serve (skip dialog when a folder is supplied, e.g. restart)
        if not folder:
            folder = filedialog.askdirectory(
                title="Select CYOA folder to serve",
                initialdir=self._outdir_var.get())
        if not folder:
            return

        # Mint a fresh preview session token for THIS serve. Injected pages and
        # the cheat route carry it; a previously-open browser tab from an older
        # serve holds a stale token and will disable its own tools instead of
        # driving this (or a closed) CYOA. This is the core stale-preview guard.
        preview_token = _new_preview_token()
        logger.info("[Server] New preview session token minted.")

        # Do NOT reuse a fixed preview port.
        # Browser localStorage, Cache Storage, service workers, and some SPA
        # viewers are origin-scoped, so reusing localhost:8080 can replay the
        # previous CYOA even when HTTP cache headers are disabled.
        # The actual port is assigned by the OS when ThreadingHTTPServer binds
        # to port 0 below.

        # Register extra MIME types for CYOA assets
        _extra_mimes = {
            ".webp": "image/webp", ".avif": "image/avif",
            ".woff2": "font/woff2", ".woff": "font/woff",
            ".otf": "font/otf", ".ttf": "font/ttf",
            ".mjs": "application/javascript", ".cjs": "application/javascript",
            ".webm": "video/webm", ".mp4": "video/mp4",
            ".ogg": "audio/ogg", ".flac": "audio/flac",
            ".m4a": "audio/mp4", ".aac": "audio/aac",
            ".json": "application/json",
        }
        for ext, mt in _extra_mimes.items():
            mimetypes.add_type(mt, ext)

        # Compressible types for gzip
        _compressible = {
            "text/html", "text/css", "application/javascript",
            "application/json", "image/svg+xml", "text/plain",
        }

        def _serve_tools_overlay_script() -> str:
            """Return the local-only Serve Tools + Cheat overlay injected into preview HTML.

            v7.6 rewrite goals (all addressed below):
              • No heavy keepalive loop. The old build force-repositioned the panel
                every 1.5s, which caused lag, made the panel non-draggable, and felt
                like the tools "kept reopening". Replaced by a single debounced
                MutationObserver that only re-attaches if the node is actually gone.
              • Draggable + collapsible + real close (full teardown).
              • Basic / Advanced modes so the default UI is small and clear.
              • Status indicator: detected / not detected / locked / unlocked.
              • Cheat detection runs only on open / manual refresh — never in a loop.
              • Error boundary so a cheat failure can never break the CYOA viewer.
              • Carries the preview session token; a stale tab (old token) disables
                its own tools instead of acting on a closed preview.
            """
            token = _current_preview_token()
            script = """
<script id="cyoa-serve-tools-overlay">
(function () {
  'use strict';
  if (window.__cyoaServeToolsLoaded) return;
  window.__cyoaServeToolsLoaded = true;

  var TOKEN = '__PREVIEW_TOKEN__';
  var STYLE_ID = 'cyoa-serve-tools-style';
  var TOOLS_ID = 'cyoa-serve-tools-panel';
  var REVEAL_STYLE_ID = 'cyoa-serve-tools-reveal-style';
  var ICE_REMOTE_URL = '__INTCYOA_REMOTE_URL__';
  var ICE_SOURCE_URL = 'https://greasyfork.org/en/scripts/438947-intcyoaenhancer';
  var ICE_CREDIT = '__INTCYOA_CREDIT__';
  var CHEAT_ENABLED = __CHEAT_ENABLED__;

  // ── Stale-session guard ────────────────────────────────────────────────
  // If this page's token does not match the active preview server token, the
  // preview was closed/replaced. Do nothing rather than drive a dead CYOA.
  function tokenLooksStale() {
    try {
      var qs = new URLSearchParams(location.search || '');
      var pageTok = qs.get('ptok') || '';
      // Only enforce when both sides have a token; older links stay tolerant.
      if (TOKEN && pageTok && pageTok !== TOKEN) return true;
    } catch (e) {}
    return false;
  }

  function safe(fn) { return function () { try { return fn.apply(this, arguments); } catch (e) { try { console.warn('[Serve Tools]', e); } catch (_) {} } }; }

  function toast(msg) {
    try { console.info('[Serve Tools]', msg); } catch (_) {}
    var old = document.getElementById('cyoa-serve-tools-toast');
    if (old) old.remove();
    var el = document.createElement('div');
    el.id = 'cyoa-serve-tools-toast';
    el.textContent = msg;
    (document.body || document.documentElement).appendChild(el);
    setTimeout(function () { try { el.remove(); } catch (_) {} }, 2400);
  }

  function disableCheatArtifacts() {
    var ids = ['cyoa-bundled-ice-cheat-panel', 'cyoa-bundled-ice-panel', 'cyoa-bundled-ice-style', 'cyoa-intcyoaenhancer-local'];
    ids.forEach(function (id) { try { var el = document.getElementById(id); if (el) el.remove(); } catch (_) {} });
    try { window.IntCyoaEnhancerCheat = undefined; } catch (_) {}
    try { window.IntCyoaEnhancerBundled = undefined; } catch (_) {}
    try { if (window.$serveTools) { delete window.$serveTools.openCheat; delete window.$serveTools.openPanel; delete window.$serveTools.showPanel; } } catch (_) {}
  }

  function isCheatAllowedNow(done) {
    if (!CHEAT_ENABLED) { done(false); return; }
    try {
      fetch('/__serve_tools__/status.json?cb=' + Date.now(), { cache: 'no-store' })
        .then(function (r) { return r && r.ok ? r.json() : null; })
        .then(function (j) { done(!(j && j.cheat_enabled === false)); })
        .catch(function () { done(CHEAT_ENABLED); });
    } catch (_) { done(CHEAT_ENABLED); }
  }

  function blockCheatUi() {
    CHEAT_ENABLED = false;
    disableCheatArtifacts();
    setStatus('warn', 'Cheat panel disabled in CYOA Downloader settings');
    toast('Cheat tools are disabled in settings');
  }

  function runCheatAction(fn) {
    isCheatAllowedNow(function (allowed) {
      if (!allowed) { blockCheatUi(); return; }
      try { fn(); } catch (e) { try { console.warn('[Serve Tools]', e); } catch (_) {} }
    });
  }

  function addStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
'#' + TOOLS_ID + '{position:fixed;top:14px;right:14px;z-index:2147483647;font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#e5e7eb}',
'#' + TOOLS_ID + ' *{box-sizing:border-box}',
'#' + TOOLS_ID + ' .card{width:264px;max-width:calc(100vw - 24px);background:rgba(17,24,39,.97);border:1px solid rgba(148,163,184,.4);border-radius:13px;box-shadow:0 16px 44px rgba(0,0,0,.45);overflow:hidden;backdrop-filter:blur(7px)}',
'#' + TOOLS_ID + ' .head{display:flex;align-items:center;gap:8px;padding:9px 11px;background:rgba(31,41,55,.96);font-weight:700;font-size:13px;cursor:move;user-select:none}',
'#' + TOOLS_ID + ' .head .ttl{flex:1}',
'#' + TOOLS_ID + ' .dot{width:9px;height:9px;border-radius:50%;background:#6b7280;flex:0 0 auto}',
'#' + TOOLS_ID + ' .dot.ok{background:#22c55e}#' + TOOLS_ID + ' .dot.warn{background:#f59e0b}#' + TOOLS_ID + ' .dot.bad{background:#ef4444}',
'#' + TOOLS_ID + ' .hx{border:0;background:transparent;color:#cbd5e1;font-size:15px;cursor:pointer;padding:0 4px;line-height:1}',
'#' + TOOLS_ID + ' .hx:hover{color:#fff}',
'#' + TOOLS_ID + ' .body{padding:9px;display:flex;flex-direction:column;gap:6px}',
'#' + TOOLS_ID + ' .status{font-size:11px;color:#cbd5e1;padding:2px 2px 4px}',
'#' + TOOLS_ID + ' .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}',
'#' + TOOLS_ID + ' button.b{border:0;border-radius:8px;padding:8px;font-size:12px;font-weight:600;cursor:pointer;background:#374151;color:#f9fafb}',
'#' + TOOLS_ID + ' button.b:hover{background:#4b5563}',
'#' + TOOLS_ID + ' .b.danger{background:#7f1d1d}#' + TOOLS_ID + ' .b.danger:hover{background:#991b1b}',
'#' + TOOLS_ID + ' .b.primary{background:#065f46}#' + TOOLS_ID + ' .b.primary:hover{background:#047857}',
'#' + TOOLS_ID + ' .b.accent{background:#1d4ed8}#' + TOOLS_ID + ' .b.accent:hover{background:#2563eb}',
'#' + TOOLS_ID + ' .wide{grid-column:1/-1}',
'#' + TOOLS_ID + ' .adv{display:none}#' + TOOLS_ID + '.show-adv .adv{display:grid}',
'#' + TOOLS_ID + ' .note{font-size:10px;color:#9ca3af;line-height:1.35;padding-top:2px}',
'#' + TOOLS_ID + ' .pill{display:none;border-radius:999px;background:#111827;border:1px solid rgba(148,163,184,.5);padding:8px 12px;color:#fff;cursor:pointer;font-weight:700;font-size:12px;box-shadow:0 8px 24px rgba(0,0,0,.4)}',
'#' + TOOLS_ID + '.min .card{display:none}#' + TOOLS_ID + '.min .pill{display:block}',
'#cyoa-serve-tools-toast{position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#111827;color:#fff;border:1px solid #475569;border-radius:9px;padding:8px 11px;font:12px system-ui;box-shadow:0 8px 26px rgba(0,0,0,.45);max-width:320px}'
    ].join('\\n');
    (document.head || document.documentElement).appendChild(style);
  }

  // ── CYOA app detection (cheat). Runs ONLY on demand, never in a loop. ────
  function getApp() {
    try {
      if (window.debugApp) {
        if (window.debugApp.app && Array.isArray(window.debugApp.app.rows)) return window.debugApp.app;
        if (window.debugApp.state && window.debugApp.state.app && Array.isArray(window.debugApp.state.app.rows)) return window.debugApp.state.app;
        if (Array.isArray(window.debugApp.rows)) return window.debugApp;
      }
      var s = window.app && window.app.__vue__ && window.app.__vue__.$store && window.app.__vue__.$store.state;
      if (s && s.app && Array.isArray(s.app.rows)) return s.app;
      if (window.__pinia) {
        var stores = Object.values(window.__pinia.state.value || {});
        for (var i = 0; i < stores.length; i++) if (stores[i] && Array.isArray(stores[i].rows)) return stores[i];
      }
    } catch (e) {}
    return null;
  }

  function allObjects(app) {
    var out = [];
    ((app && app.rows) || []).forEach(function (r) {
      ((r && r.objects) || []).forEach(function (o) { if (o) out.push(o); });
    });
    return out;
  }

  var state = { app: null, locked: false };

  function setStatus(dot, text) {
    var d = document.querySelector('#' + TOOLS_ID + ' .dot');
    var t = document.querySelector('#' + TOOLS_ID + ' .status');
    if (d) d.className = 'dot' + (dot ? ' ' + dot : '');
    if (t) t.textContent = text;
  }

  function refreshDetect() {
    state.app = getApp();
    if (!state.app) { setStatus('warn', 'CYOA not detected (open the viewer, then Refresh)'); return; }
    var objs = allObjects(state.app);
    var locked = objs.filter(function (o) { return o && (o.isSelectableMultiple === false ? false : (o.requireds && o.requireds.length)); }).length;
    setStatus('ok', 'Detected: ' + objs.length + ' options' + (locked ? ', some have requirements' : ''));
  }

  function unlockAll() {
    var app = state.app || getApp(); state.app = app;
    if (!app) { setStatus('warn', 'CYOA not detected'); return; }
    var n = 0;
    allObjects(app).forEach(function (o) {
      try {
        if (o.requireds) { o.requireds = []; }
        if (o.isNotSelectable) { o.isNotSelectable = false; }
        n++;
      } catch (e) {}
    });
    state.locked = false;
    setStatus('ok', 'Unlocked requirements on ' + n + ' options');
    toast('Unlocked ' + n + ' options');
  }

  function adjustPoints(delta) {
    var app = state.app || getApp(); state.app = app;
    if (!app || !Array.isArray(app.pointTypes)) { setStatus('warn', 'No point types found'); return; }
    app.pointTypes.forEach(function (pt) { try { pt.startingSum = (Number(pt.startingSum) || 0) + delta; } catch (e) {} });
    setStatus('ok', 'Adjusted starting points by ' + (delta > 0 ? '+' : '') + delta);
    toast('Points ' + (delta > 0 ? '+' : '') + delta);
  }

  // ── Storage helpers (preview-origin only) ──────────────────────────────
  function clearPreviewStorage(reload) {
    try { localStorage.clear(); } catch (_) {}
    try { sessionStorage.clear(); } catch (_) {}
    Promise.resolve()
      .then(function () { return ('caches' in window) ? caches.keys().then(function (n) { return Promise.all(n.map(function (x) { return caches.delete(x); })); }) : null; })
      .then(function () { return ('serviceWorker' in navigator) ? navigator.serviceWorker.getRegistrations().then(function (r) { return Promise.all(r.map(function (x) { return x.unregister(); })); }) : null; })
      .then(function () { toast('Preview storage cleared'); if (reload) location.href = '/?cb=' + Date.now() + (TOKEN ? '&ptok=' + TOKEN : ''); })
      .catch(function () {});
  }
  function exportLS() {
    var data = {};
    try { for (var i = 0; i < localStorage.length; i++) { var k = localStorage.key(i); data[k] = localStorage.getItem(k); } } catch (e) { toast('Export failed'); return; }
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'cyoa-localStorage-' + Date.now() + '.json'; a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  function importLS() {
    var raw = prompt('Paste localStorage JSON object:'); if (!raw) return;
    try { var obj = JSON.parse(raw); Object.keys(obj).forEach(function (k) { localStorage.setItem(k, String(obj[k])); }); toast('localStorage imported'); }
    catch (e) { alert('Invalid JSON: ' + e.message); }
  }
  function toggleReveal() {
    var existing = document.getElementById(REVEAL_STYLE_ID);
    if (existing) { existing.remove(); toast('Reveal disabled'); return; }
    var style = document.createElement('style'); style.id = REVEAL_STYLE_ID;
    style.textContent = '[hidden]{display:block!important}.hidden,.is-hidden{visibility:visible!important;opacity:1!important}button:disabled,input:disabled,select:disabled{pointer-events:auto!important;opacity:1!important}';
    document.head.appendChild(style);
    try { document.querySelectorAll('button:disabled,input:disabled,select:disabled').forEach(function (el) { el.disabled = false; }); } catch (_) {}
    toast('Reveal helper active');
  }

  // ── Userscript compat + ICE loader (bundled, token-aware) ───────────────
  function installUserscriptCompat() {
    try {
      if (!window.unsafeWindow) window.unsafeWindow = window;
      if (!window.GM_addStyle) window.GM_addStyle = function (css) { var s = document.createElement('style'); s.textContent = String(css || ''); document.head.appendChild(s); return s; };
      if (!window.GM_getValue) window.GM_getValue = function (k, d) { try { var r = localStorage.getItem('__GM__' + k); return r == null ? d : JSON.parse(r); } catch (_) { return d; } };
      if (!window.GM_setValue) window.GM_setValue = function (k, v) { try { localStorage.setItem('__GM__' + k, JSON.stringify(v)); } catch (_) {} };
    } catch (_) {}
  }
  function loadScript(src, id, label) {
    installUserscriptCompat();
    return new Promise(function (resolve, reject) {
      if (id && document.getElementById(id)) { toast(label + ' already loaded'); resolve(true); return; }
      var s = document.createElement('script');
      if (id) s.id = id;
      s.src = src; s.async = false;
      s.onload = function () { toast(label + ' loaded'); resolve(true); };
      s.onerror = function () { toast(label + ' failed to load'); reject(new Error('load failed')); };
      document.documentElement.appendChild(s);
    });
  }
  function loadLocalICE() {
    // Gate is checked at click-time, not only page-load time. This prevents an
    // already-open preview tab from reopening a previously-loaded cheat panel
    // after the GUI toggle has been switched off.
    isCheatAllowedNow(function (allowed) {
      if (!allowed) {
        CHEAT_ENABLED = false;
        disableCheatArtifacts();
        setStatus('warn', 'Cheat panel disabled in CYOA Downloader settings');
        toast('Cheat panel is disabled in settings');
        return;
      }
      // One unified entry: load the bundled cheat engine (once), then open the
      // Cheat Panel directly. No separate "ICE helper" panel — that intermediary
      // duplicated these same actions and caused the double-panel confusion.
      try {
        if (window.IntCyoaEnhancerCheat && window.IntCyoaEnhancerCheat.openCheat) {
          window.IntCyoaEnhancerCheat.openCheat();
          setTimeout(refreshDetect, 300);
          return;
        }
      } catch (_) {}
      var url = '/__userscripts__/intcyoaenhancer.user.js?cb=' + Date.now() + (TOKEN ? '&ptok=' + TOKEN : '');
      loadScript(url, 'cyoa-intcyoaenhancer-local', 'Bundled IntCyoaEnhancer').then(function () {
        setTimeout(function () {
          try { if (window.IntCyoaEnhancerCheat && window.IntCyoaEnhancerCheat.openCheat) window.IntCyoaEnhancerCheat.openCheat(); } catch (_) {}
          refreshDetect();
        }, 600);
      }).catch(function () {
        alert('Bundled cheat not available (cheat may be disabled in settings).');
      });
    });
  }
  function showCredit() {
    alert('IntCyoaEnhancer by agreg — MIT — GreasyFork 438947\\n' + ICE_SOURCE_URL + '\\n\\nServe-only local helper. Downloaded files are not modified.');
  }
  function openPath(p) { window.open(p, '_blank', 'noopener,noreferrer'); }

  // ── Drag (pointer events, no per-frame layout thrash) ──────────────────
  function makeDraggable(panel, handle) {
    var sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    handle.addEventListener('pointerdown', function (e) {
      if (e.target.closest('.hx')) return;
      dragging = true;
      var r = panel.getBoundingClientRect();
      ox = r.left; oy = r.top; sx = e.clientX; sy = e.clientY;
      panel.style.right = 'auto'; panel.style.left = ox + 'px'; panel.style.top = oy + 'px';
      try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    });
    handle.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var nx = Math.max(0, Math.min(window.innerWidth - 60, ox + (e.clientX - sx)));
      var ny = Math.max(0, Math.min(window.innerHeight - 30, oy + (e.clientY - sy)));
      panel.style.left = nx + 'px'; panel.style.top = ny + 'px';
    });
    handle.addEventListener('pointerup', function () { dragging = false; });
    handle.addEventListener('pointercancel', function () { dragging = false; });
  }

  var observer = null;
  function teardown() {
    try { if (observer) observer.disconnect(); } catch (_) {}
    observer = null;
    var p = document.getElementById(TOOLS_ID);
    if (p) { try { p.remove(); } catch (_) {} }
    window.__cyoaServeToolsLoaded = false;
    toast('Serve Tools closed');
  }

  function buildPanel() {
    addStyle();
    var existingPanel = document.getElementById(TOOLS_ID);
    if (existingPanel) return existingPanel;
    var panel = document.createElement('div');
    panel.id = TOOLS_ID;
    var cheatGridHtml = CHEAT_ENABLED
      ? '<button class="b accent wide" type="button" data-act="ice">🧩 Load Advanced Cheat</button>' +
        '<button class="b primary" type="button" data-act="refresh">Refresh</button>' +
        '<button class="b" type="button" data-act="unlock">Unlock all</button>' +
        '<button class="b" type="button" data-act="pp">+100 pts</button>' +
        '<button class="b" type="button" data-act="pm">-100 pts</button>'
      : '<button class="b wide" type="button" data-act="ice" disabled title="Disabled in CYOA Downloader settings">🧩 Cheat disabled</button>' +
        '<button class="b primary wide" type="button" data-act="refresh">Refresh</button>';
    panel.innerHTML =
      '<button class="pill" type="button" data-act="restore">⚡ Tools</button>' +
      '<div class="card">' +
        '<div class="head"><span class="dot"></span><span class="ttl">⚡ Serve Tools</span>' +
          '<button class="hx" type="button" data-act="adv" title="Advanced">⚙</button>' +
          '<button class="hx" type="button" data-act="min" title="Minimize">—</button>' +
          '<button class="hx" type="button" data-act="close" title="Close">✕</button></div>' +
        '<div class="body">' +
          '<div class="status">Idle — click Refresh to detect the CYOA</div>' +
          '<div class="grid">' + cheatGridHtml + '</div>' +
          '<div class="grid adv">' +
            '<button class="b danger" type="button" data-act="clear">Clear state</button>' +
            '<button class="b primary" type="button" data-act="reload">Hard reload</button>' +
            '<button class="b" type="button" data-act="els">Export LS</button>' +
            '<button class="b" type="button" data-act="ils">Import LS</button>' +
            '<button class="b" type="button" data-act="reveal">Reveal UI</button>' +
            '<button class="b" type="button" data-act="credit">Credit</button>' +
            '<button class="b" type="button" data-act="project">project.json</button>' +
            '<button class="b" type="button" data-act="report">Report</button>' +
          '</div>' +
          '<div class="note">Cheat runs locally on the preview only; downloaded files are untouched. Credit: IntCyoaEnhancer by agreg (MIT, GreasyFork 438947).</div>' +
        '</div>' +
      '</div>';

    var acts = {
      restore: function () { panel.classList.remove('min'); },
      min: function () { panel.classList.add('min'); },
      close: teardown,
      adv: function () { panel.classList.toggle('show-adv'); },
      ice: loadLocalICE,
      refresh: refreshDetect,
      unlock: function () { runCheatAction(unlockAll); },
      pp: function () { runCheatAction(function () { adjustPoints(100); }); },
      pm: function () { runCheatAction(function () { adjustPoints(-100); }); },
      clear: function () { clearPreviewStorage(false); },
      reload: function () { clearPreviewStorage(true); },
      els: exportLS,
      ils: importLS,
      reveal: toggleReveal,
      credit: showCredit,
      project: function () { openPath('/project.json'); },
      report: function () { openPath('/backup_report.txt'); }
    };
    panel.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-act]'); if (!btn) return;
      var fn = acts[btn.getAttribute('data-act')];
      if (fn) safe(fn)();
    });

    (document.body || document.documentElement).appendChild(panel);
    makeDraggable(panel, panel.querySelector('.head'));

    // v1.0 Release: single Serve interface. The static green fallback launcher is
    // only a hard fallback for when this overlay.js cannot load. Now that the
    // full draggable panel is attached, remove the redundant fallback so the
    // user sees ONE Serve Tools panel instead of two stacked ones.
    try {
      var fb = document.getElementById('cyoa-serve-tools-fallback');
      if (fb) fb.parentNode.removeChild(fb);
    } catch (_) {}

    // Lightweight, debounced re-attach: ONLY if the node is actually removed
    // by the viewer. No periodic repositioning, no layout thrash.
    // Disconnect any previous observer first; repeated viewer-side removals
    // otherwise leave old observers alive and can rebuild the panel multiple times.
    try { if (observer) observer.disconnect(); } catch (_) {}
    observer = null;
    try {
      var pending = false;
      observer = new MutationObserver(function () {
        if (pending) return;
        if (document.getElementById(TOOLS_ID)) return;
        pending = true;
        var schedule = window.requestIdleCallback || function (f) { return setTimeout(f, 200); };
        schedule(function () { pending = false; if (!document.getElementById(TOOLS_ID)) buildPanel(); });
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    } catch (_) {}

    // Console controls (kept for compatibility with existing docs/buttons).
    window.__cyoaOpenServeTools = function () { var p = document.getElementById(TOOLS_ID) || (buildPanel(), document.getElementById(TOOLS_ID)); if (p) p.classList.remove('min'); return true; };
    window.__cyoaToggleServeTools = function () { var p = document.getElementById(TOOLS_ID) || (buildPanel(), document.getElementById(TOOLS_ID)); if (p) { p.classList.toggle('min'); return !p.classList.contains('min'); } return false; };
    window.__cyoaCloseServeTools = teardown;
    return panel;
  }

  function boot() {
    if (tokenLooksStale()) { try { console.info('[Serve Tools] stale preview token — tools disabled for this closed session.'); } catch (_) {} return; }
    if (!CHEAT_ENABLED) disableCheatArtifacts();
    buildPanel();
    try {
      var qs = new URLSearchParams(location.search || '');
      var ice = (qs.get('load_ice') || '').toLowerCase();
      if (ice === 'local') setTimeout(loadLocalICE, 700);
    } catch (_) {}
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
</script>
"""
            return (script
                    .replace("__PREVIEW_TOKEN__", token)
                    .replace("__CHEAT_ENABLED__", "true" if _CHEAT_ENABLED else "false")
                    .replace("__INTCYOA_REMOTE_URL__", _INT_CYOA_ENHANCER_INFO["raw_url"])
                    .replace("https://greasyfork.org/en/scripts/438947-intcyoaenhancer", _INT_CYOA_ENHANCER_INFO["source_url"])
                    .replace("__INTCYOA_CREDIT__", _INT_CYOA_ENHANCER_INFO["credit"]))

        def _serve_tools_overlay_js() -> str:
            'Return Serve Tools overlay as plain JavaScript for /__serve_tools__/overlay.js.'
            script = _serve_tools_overlay_script().strip()
            script = re.sub(r'^\s*<script[^>]*>', '', script, flags=re.IGNORECASE)
            script = re.sub(r'</script>\s*$', '', script, flags=re.IGNORECASE)
            return script

        def _strip_local_preview_csp(html_text: str) -> str:
            'Remove CSP meta tags from the served copy only so localhost tools can run.'
            try:
                html_text = re.sub(
                    r'<meta[^>]+http-equiv=["\']Content-Security-Policy["\'][^>]*>',
                    '', html_text, flags=re.IGNORECASE,
                )
                html_text = re.sub(
                    r'<meta[^>]+content=["\'][^"\']*script-src[^"\']*["\'][^>]*http-equiv=["\']Content-Security-Policy["\'][^>]*>',
                    '', html_text, flags=re.IGNORECASE,
                )
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in _strip_local_preview_csp (line 12059): %s", _ignored_exc)
            return html_text

        def _serve_tools_fallback_launcher() -> str:
            'Visible hard fallback launcher injected into preview HTML.'
            credit = _INT_CYOA_ENHANCER_INFO["credit"]
            source = _INT_CYOA_ENHANCER_INFO["source_url"]
            launcher = '''
<!-- CYOA_SERVE_TOOLS_INJECTED_v748 -->
<div id="cyoa-serve-tools-fallback" style="position:fixed!important;top:8px!important;right:8px!important;bottom:auto!important;left:auto!important;z-index:2147483647!important;background:#111827!important;color:#f9fafb!important;border:2px solid #22c55e!important;border-radius:12px!important;padding:10px!important;box-shadow:0 16px 50px rgba(0,0,0,.55)!important;font:12px/1.35 system-ui,-apple-system,Segoe UI,sans-serif!important;display:block!important;visibility:visible!important;opacity:1!important;pointer-events:auto!important;min-width:210px!important;max-width:280px!important;isolation:isolate!important;">
  <div style="font-weight:800!important;font-size:13px!important;margin-bottom:6px!important;color:#bbf7d0!important;">⚡ Serve Tools</div>
  <div style="display:flex!important;gap:6px!important;flex-wrap:wrap!important;margin-bottom:6px!important;">
    <button type="button" onclick="window.open('/__serve_tools__','_blank')" style="all:unset!important;display:inline-block!important;padding:5px 8px!important;border-radius:8px!important;background:#065f46!important;color:white!important;cursor:pointer!important;font-weight:700!important;">Open</button>
    <button type="button" onclick="try{localStorage.clear();sessionStorage.clear();location.reload();}catch(e){alert(e)}" style="all:unset!important;display:inline-block!important;padding:5px 8px!important;border-radius:8px!important;background:#7f1d1d!important;color:white!important;cursor:pointer!important;font-weight:700!important;">Clear</button>
    __ICE_BUTTON__
  </div>
  <div style="font-size:10px!important;color:#cbd5e1!important;">Credit: __ICE_CREDIT__<br><a href="__ICE_SOURCE__" target="_blank" rel="noopener" style="color:#67e8f9!important;">GreasyFork source</a> · visible ⚡ Tools button · console: window.__cyoaToggleServeTools()</div>
</div>
<script src="/__serve_tools__/overlay.js?cb=748" defer></script>
<script>
(function(){
  function keepFallbackVisible(){
    var fb=document.getElementById('cyoa-serve-tools-fallback');
    if(!fb)return;
    fb.style.setProperty('display','block','important');
    fb.style.setProperty('visibility','visible','important');
    fb.style.setProperty('opacity','1','important');
    fb.style.setProperty('z-index','2147483647','important');
    fb.style.setProperty('top','8px','important');
    fb.style.setProperty('right','8px','important');
  }
  window.__cyoaToggleServeTools=function(){
    var main=document.getElementById('cyoa-serve-tools-panel');
    if(main && main.classList){ main.classList.toggle('min'); main.style.setProperty('display','block','important'); main.style.setProperty('visibility','visible','important'); return !main.classList.contains('min'); }
    var fb=document.getElementById('cyoa-serve-tools-fallback');
    if(fb){ var hidden=fb.getAttribute('data-hidden')==='1'; fb.setAttribute('data-hidden', hidden?'0':'1'); fb.style.setProperty('display', hidden?'block':'none','important'); return hidden; }
    window.open('/__serve_tools__','_blank'); return false;
  };
  window.__cyoaOpenServeTools=function(){
    var main=document.getElementById('cyoa-serve-tools-panel');
    if(main && main.classList){ main.classList.remove('min'); main.style.setProperty('display','block','important'); main.style.setProperty('visibility','visible','important'); return true; }
    var fb=document.getElementById('cyoa-serve-tools-fallback');
    if(fb){ fb.setAttribute('data-hidden','0'); fb.style.setProperty('display','block','important'); keepFallbackVisible(); return true; }
    window.open('/__serve_tools__','_blank'); return false;
  };
  keepFallbackVisible();
  // v7.6: no repeating reposition loop. The draggable overlay panel
  // (/__serve_tools__/overlay.js) owns visibility now; a 1.2s interval here
  // fought dragging and added load. One initial call is enough.
})();
</script>
'''
            ice_button = (
                '<button type="button" onclick="try{var s=document.createElement(\'script\');s.src=\'/__userscripts__/intcyoaenhancer.user.js?cb=\'+Date.now();document.documentElement.appendChild(s);}catch(e){alert(e)}" style="all:unset!important;display:inline-block!important;padding:5px 8px!important;border-radius:8px!important;background:#1d4ed8!important;color:white!important;cursor:pointer!important;font-weight:700!important;">Bundled ICE</button>'
                if _CHEAT_ENABLED
                else '<span style="display:inline-block!important;padding:5px 8px!important;border-radius:8px!important;background:#374151!important;color:#94a3b8!important;font-weight:700!important;">ICE disabled</span>'
            )
            return (launcher.replace('__ICE_CREDIT__', credit)
                            .replace('__ICE_SOURCE__', source)
                            .replace('__ICE_BUTTON__', ice_button))

        def _inject_serve_tools(html_text: str) -> str:
            if 'CYOA_SERVE_TOOLS_INJECTED_v748' in html_text or 'id="cyoa-serve-tools-fallback"' in html_text:
                return html_text
            html_text = _strip_local_preview_csp(html_text)
            launcher = _serve_tools_fallback_launcher()
            lower = html_text.lower()
            m = re.search(r'<body\b[^>]*>', html_text, flags=re.IGNORECASE)
            if m:
                return html_text[:m.end()] + launcher + html_text[m.end():]
            idx = lower.rfind('</html>')
            if idx >= 0:
                return html_text[:idx] + launcher + html_text[idx:]
            return html_text + launcher

        def _serve_html_bytes(handler, html_text: str, status: int = 200) -> None:
            data = html_text.encode('utf-8', errors='replace')
            handler.send_response(status)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('X-CYOA-Serve-Tools', 'injected-or-route')
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            handler.send_header('Pragma', 'no-cache')
            handler.send_header('Expires', '0')
            handler.end_headers()
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _serve_js_bytes(handler, js_text: str, status: int = 200) -> None:
            data = js_text.encode('utf-8', errors='replace')
            handler.send_response(status)
            handler.send_header('Content-Type', 'application/javascript; charset=utf-8')
            handler.send_header('X-CYOA-Serve-Tools', 'route')
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            handler.send_header('Pragma', 'no-cache')
            handler.send_header('Expires', '0')
            handler.end_headers()
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _serve_json_bytes(handler, json_text: str, status: int = 200) -> None:
            data = json_text.encode('utf-8', errors='replace')
            handler.send_response(status)
            handler.send_header('Content-Type', 'application/json; charset=utf-8')
            handler.send_header('X-CYOA-Serve-Tools', 'route')
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            handler.send_header('Pragma', 'no-cache')
            handler.send_header('Expires', '0')
            handler.end_headers()
            try:
                handler.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _intcyoaenhancer_local_candidates() -> List[str]:
            script_dir = os.path.dirname(os.path.abspath(_CYOA_LEGACY_PUBLIC_FILE))
            names = (
                'IntCyoaEnhancer.user.js',
                'intcyoaenhancer.user.js',
                '438947-intcyoaenhancer.user.js',
            )
            roots = (
                folder,
                os.path.join(folder, 'userscripts'),
                os.path.join(folder, 'serve_userscripts'),
                script_dir,
                os.path.join(script_dir, 'userscripts'),
                os.path.join(script_dir, 'serve_userscripts'),
            )
            out: List[str] = []
            for root in roots:
                for name in names:
                    out.append(os.path.join(root, name))
            return out

        def _find_local_intcyoaenhancer_script() -> Optional[str]:
            for candidate in _intcyoaenhancer_local_candidates():
                try:
                    if os.path.isfile(candidate):
                        return candidate
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _find_local_intcyoaenhancer_script (line 12206): %s", _ignored_exc)
            return None

        def _serve_intcyoaenhancer_metadata(handler) -> None:
            local_path = _find_local_intcyoaenhancer_script()
            payload = dict(_INT_CYOA_ENHANCER_INFO)
            payload.update({
                'bundled': True,
                'bundled_available': True,
                'bundled_size_bytes': len(_BUNDLED_INTCYOAENHANCER_USERSCRIPT.encode('utf-8')),
                'local_override_available': bool(local_path),
                'local_override_path': local_path or '',
                'local_candidates': _intcyoaenhancer_local_candidates(),
                'route': '/__userscripts__/intcyoaenhancer.user.js',
                'cheat_enabled': bool(_CHEAT_ENABLED),
                'integration_policy': 'Serve-only bundled helper. No network download required. Downloaded CYOA files are not modified.',
            })
            _serve_json_bytes(handler, json.dumps(payload, indent=2, ensure_ascii=False))

        def _serve_local_intcyoaenhancer(handler) -> None:
            local_path = _find_local_intcyoaenhancer_script()
            credit = (
                f"/* Served by CYOA Downloader v{_APP_VERSION}. {_INT_CYOA_ENHANCER_INFO['credit']} | "
                f"Source: {_INT_CYOA_ENHANCER_INFO['source_url']} | Bundled localhost helper; no network download required. */\n"
            )
            if local_path:
                try:
                    local_text = pathlib.Path(local_path).read_text(encoding='utf-8', errors='replace')
                    _serve_js_bytes(handler, credit + local_text, status=200)
                    return
                except Exception as e:
                    logger.warning(f"Bundled IntCyoaEnhancer Cheat helper override failed, serving bundled helper instead: {e}")
            _serve_js_bytes(handler, credit + _BUNDLED_INTCYOAENHANCER_USERSCRIPT, status=200)

        def _serve_tools_page(handler) -> None:
            page = """<!doctype html><meta charset="utf-8"><title>CYOA Serve Tools</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f172a;color:#e5e7eb;margin:0;padding:28px;line-height:1.55}main{max-width:820px;margin:auto}a,button{font:inherit}button,a.btn{display:inline-block;margin:6px 6px 6px 0;padding:10px 13px;border-radius:10px;border:1px solid #475569;background:#1f2937;color:#f9fafb;text-decoration:none;cursor:pointer}.primary{background:#065f46}.danger{background:#7f1d1d}.card{background:#111827;border:1px solid #334155;border-radius:16px;padding:18px;margin:16px 0}.muted{color:#9ca3af}</style>
<main><h1>⚡ CYOA Serve Tools</h1><p class="muted">Local-only preview helpers for downloaded/offline CYOAs. These tools affect the current localhost preview origin and do not modify downloaded files.</p>
<div class="card"><h2>Open Preview</h2><a class="btn primary" href="/?serve_tools=1&cb=manual">Open CYOA with tools overlay</a><a class="btn" href="/?no_tools=1">Open CYOA without tools</a></div>
<div class="card"><h2>Storage Tools</h2><button class="danger" onclick="clearAll(false)">Clear storage/cache</button><button class="primary" onclick="clearAll(true)">Clear + reopen with tools</button><button onclick="exportLS()">Export localStorage</button><button onclick="importLS()">Import localStorage</button><button onclick="exportIDB()">Export IndexedDB</button><button onclick="importIDB()">Import IndexedDB</button><button onclick="clearIDB()">Clear IndexedDB</button><p class="muted">ICC Plus Svelte stores build saves in IndexedDB, commonly cyoaPlusDB/buildStore.</p></div>
<div class="card"><h2>Userscript Lab</h2><a class="btn primary" href="/?serve_tools=1&load_ice=local&cb=local">Open preview + bundled ICE Cheat Panel</a><a class="btn" href="/?serve_tools=1&load_ice=web&cb=web">Open preview + GreasyFork IntCyoaEnhancer</a><button onclick="showCredit()">Credit / Source</button><a class="btn" href="/__userscripts__/intcyoaenhancer.meta.json" target="_blank">Metadata</a><p class="muted">Optional Serve-only loader. The userscript must run inside the CYOA preview page, so these buttons open the preview with the loader enabled. The helper is bundled inside the program and served locally from <code>/__userscripts__/intcyoaenhancer.user.js</code>, so no separate download is required. Local files can still override it for advanced testing. Source credit: IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947.</p></div>
<div class="card"><h2>Reports</h2><a class="btn" href="/project.json" target="_blank">project.json</a><a class="btn" href="/backup_report.txt" target="_blank">backup_report.txt</a><a class="btn" href="/failed_assets.txt" target="_blank">failed_assets.txt</a><a class="btn" href="/cyoa_downloader.log" target="_blank">cyoa_downloader.log</a></div>
<script>
async function clearAll(reopen){try{localStorage.clear();sessionStorage.clear()}catch(e){}try{if('caches'in window){const n=await caches.keys();await Promise.all(n.map(x=>caches.delete(x)))}}catch(e){}try{if('serviceWorker'in navigator){const r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(x=>x.unregister()))}}catch(e){}alert('Preview storage cleared');if(reopen)location.href='/?serve_tools=1&cb='+Date.now()}
function addScript(src,id,label){return new Promise((ok,bad)=>{if(document.getElementById(id)){alert(label+' already loaded');ok();return}const s=document.createElement('script');s.id=id;s.src=src;s.async=false;s.onload=()=>{alert(label+' loaded') ;ok()};s.onerror=()=>{alert(label+' failed to load');bad()};document.documentElement.appendChild(s)})}
function loadLocalICE(){addScript('/__userscripts__/intcyoaenhancer.user.js?cb='+Date.now(),'cyoa-intcyoaenhancer-local','Bundled IntCyoaEnhancer Cheat helper').catch(()=>{})}
function loadRemoteICE(){if(confirm('Load IntCyoaEnhancer from GreasyFork for this localhost preview only?'))addScript('https://update.greasyfork.org/scripts/438947/IntCyoaEnhancer.user.js','cyoa-intcyoaenhancer-remote','GreasyFork IntCyoaEnhancer').catch(()=>{})}
function showCredit(){alert('IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947\nSource: https://greasyfork.org/en/scripts/438947-intcyoaenhancer\n\nIntegration is Serve-only and does not modify downloaded files.')}
function exportLS(){const d={};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);d[k]=localStorage.getItem(k)}const b=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='cyoa-localStorage-'+Date.now()+'.json';a.click()}
function importLS(){const raw=prompt('Paste localStorage JSON object:');if(!raw)return;const obj=JSON.parse(raw);Object.keys(obj).forEach(k=>localStorage.setItem(k,String(obj[k])));alert('Imported')}
async function idbNames(){if(indexedDB.databases){try{return(await indexedDB.databases()).map(d=>d.name).filter(Boolean)}catch(e){}}return ['cyoaPlusDB','cyoaDB','CYOAPlusDB','buildStore']}
function idbOpen(n){return new Promise((res,rej)=>{const r=indexedDB.open(n);r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error||new Error('open failed'))})}
async function exportIDB(){const p={exportedAt:new Date().toISOString(),databases:{}};for(const n of await idbNames()){let db;try{db=await idbOpen(n)}catch(e){continue}p.databases[n]={stores:{}};for(const sn of Array.from(db.objectStoreNames)){await new Promise(ok=>{try{const tx=db.transaction(sn,'readonly'),rq=tx.objectStore(sn).getAll();rq.onsuccess=()=>{p.databases[n].stores[sn]=rq.result||[];ok()};rq.onerror=()=>ok()}catch(e){ok()}})}db.close()}const b=new Blob([JSON.stringify(p,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='cyoa-indexeddb-'+Date.now()+'.json';a.click()}
async function clearIDB(){if(!confirm('Clear IndexedDB for this preview origin?'))return;for(const n of await idbNames()){await new Promise(ok=>{try{const r=indexedDB.deleteDatabase(n);r.onsuccess=r.onerror=r.onblocked=()=>ok()}catch(e){ok()}})}alert('IndexedDB clear requested')}
async function importIDB(){const raw=prompt('Paste IndexedDB export JSON:');if(!raw)return;const p=JSON.parse(raw);for(const n of Object.keys(p.databases||{})){const db=await idbOpen(n);for(const sn of Object.keys((p.databases[n]||{}).stores||{})){if(!Array.from(db.objectStoreNames).includes(sn))continue;await new Promise(ok=>{try{const tx=db.transaction(sn,'readwrite'),st=tx.objectStore(sn);((p.databases[n].stores[sn])||[]).forEach(x=>{try{st.put(x)}catch(e){}});tx.oncomplete=tx.onerror=()=>ok()}catch(e){ok()}})}db.close()}alert('IndexedDB import finished')}
</script></main>"""
            if not _CHEAT_ENABLED:
                page = page.replace(
                    '<a class="btn primary" href="/?serve_tools=1&load_ice=local&cb=local">Open preview + bundled ICE Cheat Panel</a>',
                    '<button class="btn" disabled title="Disabled in CYOA Downloader settings">Bundled ICE disabled in settings</button>'
                )
                page = page.replace(
                    "function loadLocalICE(){addScript('/__userscripts__/intcyoaenhancer.user.js?cb='+Date.now(),'cyoa-intcyoaenhancer-local','Bundled IntCyoaEnhancer Cheat helper').catch(()=>{})}",
                    "function loadLocalICE(){alert('Cheat panel is disabled in CYOA Downloader settings.')}"
                )
            _serve_html_bytes(handler, page)

        class CYOAHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=folder, **kw)

            def log_message(self, fmt, *args):
                pass  # silence per-request logging, too noisy

            def end_headers(self):
                # CORS for cross-origin viewers
                self.send_header("Access-Control-Allow-Origin", "*")
                # Development/preview server: disable browser cache aggressively.
                # CYOA projects often reuse index.html, project.json, and asset names,
                # so caching can make the browser show a previous CYOA after a new run.
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                # v7.4.3: do not clear preview storage on normal / or /index.html.
                # ICC Plus stores build saves in IndexedDB; state clearing is explicit
                # through /__clear_cache__ or Serve Developer Tools only.
                # Close each response cleanly. This avoids noisy Windows keep-alive
                # resets when Chromium cancels large image/script transfers.
                self.send_header("Connection", "close")
                super().end_headers()

            def copyfile(self, source, outputfile):
                try:
                    return super().copyfile(source, outputfile)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def do_GET(self):
                """Serve preview files and expose a cache/storage clear route."""
                import gzip as _gz, io as _io
                from urllib.parse import urlparse as _urlparse

                # Explicit browser-side clear route. This clears localStorage,
                # sessionStorage, Cache Storage, and service workers for the
                # current preview origin, then redirects to the CYOA root with a
                # fresh cache-busting URL. This fixes viewers that store the
                # previous project client-side rather than in normal HTTP cache.
                parsed_request = _urlparse(self.path)
                route_path = parsed_request.path

                if route_path == "/__serve_tools__":
                    _serve_tools_page(self)
                    return
                if route_path == "/__serve_tools__/overlay.js":
                    _serve_js_bytes(self, _serve_tools_overlay_js(), status=200)
                    return
                if route_path == "/__serve_tools__/probe":
                    _serve_html_bytes(self, _inject_serve_tools("<!doctype html><html><body><h1>Serve Tools injection probe</h1><p>If the green launcher appears at top-right, injection works.</p></body></html>"), status=200)
                    return
                if route_path == "/__serve_tools__/status.json":
                    _serve_json_bytes(self, json.dumps({
                        'ok': True,
                        'version': _APP_VERSION,
                        'injection': 'hard-html-intercept',
                        'visible_launcher': 'draggable overlay; fallback removed after overlay mount',
                        'cheat_enabled': bool(_CHEAT_ENABLED),
                        'clean_preview_flags': ['?no_tools=1','?serve_tools=0','?tools=0'],
                        'credit': _INT_CYOA_ENHANCER_INFO.get('credit',''),
                        'source': _INT_CYOA_ENHANCER_INFO.get('source_url',''),
                    }, indent=2))
                    return
                if route_path == "/__userscripts__/intcyoaenhancer.meta.json":
                    _serve_intcyoaenhancer_metadata(self)
                    return
                if route_path == "/__userscripts__/intcyoaenhancer.user.js":
                    if not _CHEAT_ENABLED:
                        _serve_js_bytes(
                            self,
                            "// Cheat panel disabled by toggle in CYOA Downloader settings.\n"
                            "console.info('[CYOA] Cheat helper is disabled in settings.');\n",
                            status=200)
                        return
                    # Reject requests from a stale/closed preview tab. A missing
                    # token stays tolerant (older bookmarks/manual hits), but a
                    # token that does not match the active session is refused.
                    from urllib.parse import parse_qs as _parse_qs
                    _req_tok = (_parse_qs(parsed_request.query or "").get("ptok", [""]) or [""])[0]
                    if _req_tok and not _preview_token_valid(_req_tok):
                        logger.info("[Server] Cheat helper request rejected: stale preview session token.")
                        _serve_js_bytes(
                            self,
                            "// Stale preview session — this tab belongs to a closed preview.\n"
                            "console.info('[CYOA] Cheat helper not served: stale preview session.');\n",
                            status=200)
                        return
                    _serve_local_intcyoaenhancer(self)
                    return

                # v7.4.9: Inject Serve Tools into served HTML by default and harden visible button controls.
                # Previous builds injected only when ?serve_tools=1 was present,
                # which made users think the IntCyoaEnhancer/Serve Tools feature
                # was missing when they opened the normal preview URL.
                # Use ?no_tools=1, ?serve_tools=0, or ?tools=0 for a clean preview.
                _query = parsed_request.query or ""
                _tools_disabled = any(flag in _query for flag in ("no_tools=1", "serve_tools=0", "cyoa_tools=0", "tools=0"))
                if not _tools_disabled:
                    try:
                        html_path = self.translate_path(route_path)
                        if os.path.isdir(html_path):
                            index_html = os.path.join(html_path, "index.html")
                            index_htm = os.path.join(html_path, "index.htm")
                            html_path = index_html if os.path.isfile(index_html) else index_htm
                        if os.path.isfile(html_path) and html_path.lower().endswith((".html", ".htm")):
                            raw_html = pathlib.Path(html_path).read_text(encoding="utf-8", errors="replace")
                            _serve_html_bytes(self, _inject_serve_tools(raw_html))
                            logger.info(f"[Server] Serve Tools injected into HTML: {os.path.relpath(html_path, folder)}")
                            return
                    except Exception as _tools_e:
                        logger.debug(f"Serve Tools auto-injection skipped: {_tools_e}")

                if route_path == "/__clear_cache__":
                    stamp = str(int(time.time() * 1000))
                    ptok = _current_preview_token()
                    html_text = f'''<!doctype html><meta charset="utf-8"><title>Clearing preview cache...</title>
<script>
(async function() {{
  try {{ localStorage.clear(); }} catch (e) {{}}
  try {{ sessionStorage.clear(); }} catch (e) {{}}
  try {{
    if ('caches' in window) {{
      const names = await caches.keys();
      await Promise.all(names.map(n => caches.delete(n)));
    }}
  }} catch (e) {{}}
  try {{
    if ('serviceWorker' in navigator) {{
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map(r => r.unregister()));
    }}
  }} catch (e) {{}}
  location.replace('/?cb={stamp}&preview={stamp}&serve_tools=1&ptok={ptok}');
}})();
</script>
<p>Clearing preview cache...</p>'''
                    html = html_text.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                    self.send_header("Clear-Site-Data", '"cache", "storage"')
                    self.end_headers()
                    self.wfile.write(html)
                    return

                # Check if client accepts gzip
                accept_enc = self.headers.get("Accept-Encoding", "")
                if "gzip" not in accept_enc:
                    return super().do_GET()
                # Only compress known text types
                path_lower = self.path.lower().split("?")[0]
                ct = mimetypes.guess_type(path_lower)[0] or ""
                if ct not in _compressible:
                    return super().do_GET()
                # Translate path
                path = self.translate_path(self.path)
                if not os.path.isfile(path):
                    return super().do_GET()
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                    buf = _io.BytesIO()
                    with _gz.GzipFile(fileobj=buf, mode="wb", compresslevel=4) as gz:
                        gz.write(raw)
                    compressed = buf.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(compressed)))
                    self.end_headers()
                    self.wfile.write(compressed)
                except Exception:
                    return super().do_GET()

            def do_OPTIONS(self):
                """Handle CORS preflight."""
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()

        try:
            # ThreadingHTTPServer handles multiple requests concurrently.
            # Bind to 127.0.0.1:0 so every preview gets a fresh local origin.
            # This avoids stale localStorage/service-worker state from an older
            # localhost:8080 preview.
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CYOAHandler)
            port = int(server.server_address[1])
            server.timeout = 0.5
            self._server_obj = server

            def _run():
                logger.info(f"[Server] Started: http://127.0.0.1:{port}")
                server.serve_forever()

            self._server_thread = threading.Thread(target=_run, daemon=True)
            self._server_thread.start()
            self._server_running = True
            self._server_folder = folder

            # Update button
            self._srv_btn.configure(
                text=f"■ Stop :{port}",
                width=118,
                fg_color="#065f46",
                hover_color="#ef4444",
                text_color="#ffffff")

            # Keep the top status compact so it never pushes the Stop button
            # off-screen in narrower windows. Full diagnostic details remain in
            # the log and the served status endpoint.
            self._set_status(f"Serving :{port}")
            logger.info(f"[Server] Serving: {folder}")
            logger.info(f"[Server] URL: http://127.0.0.1:{port} | tools: /__serve_tools__/probe | clean: ?no_tools=1")

            # Open the explicit clear route first. It clears browser storage for
            # this preview origin and then redirects to the project root with a
            # unique query string.
            stamp = int(time.time() * 1000)
            webbrowser.open(f"http://127.0.0.1:{port}/__clear_cache__?cb={stamp}&serve_tools=1&ptok={preview_token}")

        except Exception as e:
            messagebox.showerror("Server Error", str(e))

    def _stop_server(self) -> None:
        import customtkinter as ctk
        import threading as _th

        server_to_stop = self._server_obj
        self._server_obj     = None
        self._server_running = False
        _last_folder = self._server_folder
        self._server_folder = None

        # Invalidate the preview session: any tab still open from this serve now
        # holds a stale token, so its cheat route and overlay deactivate. This is
        # what prevents a closed preview from being driven after Stop.
        _clear_preview_token()
        logger.info("[Server] Preview session invalidated on stop.")
        # Update button immediately — don't wait for shutdown to complete
        p = self._p()
        self._srv_btn.configure(
            text="⚡ Start Serve",
            width=118,
            fg_color=p["surface2"],
            hover_color="#065f46",
            text_color="#6ee7b7",
        )
        self._set_status("Idle")

        # Shutdown in background so GUI stays responsive
        def _do_shutdown():
            if server_to_stop:
                try:
                    server_to_stop.shutdown()
                    server_to_stop.server_close()
                except Exception as _ignored_exc:
                    logger.debug("Ignored recoverable exception in _do_shutdown (line 12535): %s", _ignored_exc)
            logger.info("[Server] Stopped")

        _th.Thread(target=_do_shutdown, daemon=True).start()

    def _restart_server(self) -> None:
        """Stop the current preview server (if any) and re-serve the same folder.

        Reuses the last-served folder so the user is not re-prompted. A fresh
        OS-assigned port is bound, giving the preview a clean origin.
        """
        folder = self._server_folder
        if self._server_running:
            self._stop_server()
        # Allow the background shutdown to release the socket before rebinding.
        def _resume():
            self._start_server(folder=folder)
        try:
            self.root.after(600, _resume)
        except Exception:
            # No Tk loop available (shouldn't happen from GUI) — best effort.
            self._start_server(folder=folder)

    def _show_results(self) -> None:
        return self._dispatch_gui_patch("_v24_show_results")

    def _batch_update_panel(self) -> None:
        return self._dispatch_gui_patch("_v24_batch_update_panel")

    def _diagnostics_panel(self) -> None:
        return self._dispatch_gui_patch("_v24_diagnostics_panel")

    def _add_url_to_queue(self, url: str, filename: str = "") -> None:
        return self._dispatch_gui_patch("_v24_add_url_to_queue", url, filename)

    def _cloudflare_panel(self) -> None:
        return self._dispatch_gui_patch("_v25_cloudflare_panel")

    def _manage_offline_viewers(self) -> None:
        return self._dispatch_gui_patch("_v25_manage_offline_viewers")

    def _cache_manager_panel(self) -> None:
        return self._dispatch_gui_patch("_v27_cache_manager_panel")

    def _check_updates_panel(self) -> None:
        return self._dispatch_gui_patch("_v27_check_updates_panel")

    def _ai_settings_panel(self) -> None:
        return self._dispatch_gui_patch("_v27_ai_settings_panel")

def _gui_exists(widget: Any) -> bool:
    try:
        return bool(widget and widget.winfo_exists())
    except Exception:
        return False

__all__ = ["CYOADownloaderGUI", "launch_gui", "_gui_exists", "_sync_legacy_globals"]
