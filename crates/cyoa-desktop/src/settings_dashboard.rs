//! F-049: the categorized Settings dashboard (Python
//! `_settings_dashboard_panel`): left navigation with General, Network,
//! Integrations, Viewers and Maintenance pages plus a keyword search. The
//! cards keep Python's titles, descriptions, limits and status texts.
//!
//! Panels the app already owns (network, CYOA Manager, offline viewers, AI
//! Assist) are rendered by the app inside the matching page; this module
//! renders the remaining cards: YouTube cookies, itch.io key, archive policy,
//! feature switches, gallery-dl config, Discord token, settings file/folder,
//! export/import and the image cache.

use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, TryRecvError};

use eframe::egui;
use serde_json::Value;

/// Python `tab_names` (English) and their search keywords.
pub const PAGES: &[(&str, &str, &str)] = &[
    (
        "Download",
        "↓",
        "general auto output folder zip youtube cookie features deep scan selenium preview cheat gallery itch archive javascript policy pages depth runtime scroll click umum fitur arsip download",
    ),
    (
        "Network",
        "NET",
        "network proxy http https socks vpn dns udp tcp doh dot tls resolver fallback ipv6 jaringan",
    ),
    (
        "Integrations",
        "↔",
        "integrations ai cloudflare flaresolverr discord provider integrasi",
    ),
    (
        "Viewers",
        "VIEW",
        "viewer offline injection inject json icc plus remix legacy ouroumov archive otomatis",
    ),
    (
        "Maintenance",
        "⚙",
        "maintenance settings file folder export import cache tools verify manifest modernize library pemeliharaan",
    ),
];

/// The first page whose name or keywords contain `query` (Python
/// `_search_settings`); None for an empty query or no match.
pub fn search_page(query: &str) -> Option<usize> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return None;
    }
    PAGES.iter().position(|(name, _, kw)| {
        kw.to_lowercase().contains(&q) || name.to_lowercase().contains(&q)
    })
}

/// Archive policy fields: (label, hint, settings key, min, max).
const ARCHIVE_LIMITS: &[(&str, &str, &str, i64, i64)] = &[
    (
        "Max pages (1–5000)",
        "Hard cap for same-origin story routes.",
        "archive_max_pages",
        1,
        5000,
    ),
    (
        "Max depth (0–100)",
        "Route hops from entry; 0 means entry only.",
        "archive_max_depth",
        0,
        100,
    ),
    (
        "Runtime pages (1–100)",
        "Maximum pages rendered by the browser engine.",
        "archive_runtime_max_pages",
        1,
        100,
    ),
    (
        "Settle time ms (250–15000)",
        "Wait after load/action for late assets.",
        "archive_settle_time_ms",
        250,
        15000,
    ),
    (
        "Scroll steps (1–1000)",
        "Maximum incremental lazy-load scrolls.",
        "archive_max_scroll_steps",
        1,
        1000,
    ),
    (
        "Max safe clicks (0–100)",
        "Maximum allowlisted clicks per runtime page.",
        "archive_max_interactions",
        0,
        100,
    ),
    (
        "No-progress rounds (1–10)",
        "Stop after this many rounds find nothing new.",
        "archive_no_progress_rounds",
        1,
        10,
    ),
];
const ARCHIVE_DEFAULTS: &[i64] = &[300, 30, 12, 1800, 100, 20, 2];

/// One-click JavaScript archive presets: (name, help, strategy, limits in
/// `ARCHIVE_LIMITS` order). "Complete offline" trades time for coverage.
const ARCHIVE_PRESETS: &[(&str, &str, &str, [i64; 7])] = &[
    (
        "Fast",
        "Small stories / quick checks: few routes, short waits.",
        "auto",
        [50, 10, 4, 1000, 30, 5, 1],
    ),
    (
        "Balanced",
        "Default caps (recommended for most sites).",
        "auto",
        [300, 30, 12, 1800, 100, 20, 2],
    ),
    (
        "Complete offline",
        "Large or heavily scripted sites: more routes, longer settle and scroll, more safe clicks. Slower.",
        "auto",
        [2000, 60, 40, 3500, 400, 60, 4],
    ),
];

/// Deep-scan presets in `DEEP_SCAN_SETTINGS` order.
const DEEP_PRESETS: &[(&str, [i64; 5])] = &[
    ("Fast", [2, 2_000, 128, 10_000, 5]),
    ("Balanced", [6, 20_000, 512, 100_000, 20]),
    ("Complete offline", [12, 60_000, 2048, 500_000, 80]),
];

/// Feature switches: (badge, title, description, settings key, default).
const FEATURES: &[(&str, &str, &str, &str, bool)] = &[
    (
        "DS",
        "Deep scan",
        "Discover assets from JS/CSS bundles.",
        "deep_scan_enabled",
        true,
    ),
    (
        "SB",
        "Selenium fallback",
        "Browser fallback for difficult pages.",
        "selenium_enabled",
        true,
    ),
    (
        "PV",
        "Serve preview",
        "Local server for downloaded ICC folders.",
        "serve_enabled",
        true,
    ),
    (
        "CP",
        "Cheat panel",
        "⚙ cheat panel in offline viewers and the Serve preview.",
        "cheat_enabled",
        true,
    ),
    (
        "GD",
        "gallery-dl fallback",
        "Smart fallback for gallery URLs.",
        "gallery_dl_mode",
        false,
    ),
    (
        "CV",
        "CYOAP Vue first",
        "Probe dist/ CYOAP Vue sites before the standard download.",
        "cyoap_vue_first",
        false,
    ),
    (
        "IT",
        "itch.io downloader",
        "Optional itch.io backend.",
        "itch_enabled",
        false,
    ),
];

/// Python `f"{x:g}"` (6 significant digits, no trailing zeros).
pub fn py_g(x: f64) -> String {
    if x == 0.0 {
        return "0".into();
    }
    let exp = x.abs().log10().floor() as i32;
    if !(-5..6).contains(&exp) {
        let s = format!("{:.5e}", x);
        let (mant, e) = s.split_once('e').unwrap_or((&s, "0"));
        let mant = mant.trim_end_matches('0').trim_end_matches('.');
        let e: i32 = e.parse().unwrap_or(0);
        return format!("{mant}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    let decimals = (5 - exp).max(0) as usize;
    let s = format!("{x:.decimals$}");
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        s
    }
}

/// Python cache card limit parsing: GB -> MB in 1..=1 TiB.
pub fn cache_limit_mb(gb_text: &str) -> Option<u64> {
    let gb: f64 = gb_text.trim().parse().ok()?;
    if !gb.is_finite() || gb <= 0.0 {
        return None;
    }
    Some(((gb * 1024.0).round() as u64).clamp(1, 1024 * 1024))
}

pub(crate) fn open_with_os(target: &Path) -> std::io::Result<()> {
    #[cfg(windows)]
    let mut cmd = std::process::Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = std::process::Command::new("xdg-open");
    cmd.arg(target).spawn().map(|_| ())
}

/// Python `_open_text_file_for_editing`.
fn edit_text_file(path: &Path) -> std::io::Result<()> {
    #[cfg(windows)]
    {
        std::process::Command::new("notepad.exe")
            .arg(path)
            .spawn()
            .map(|_| ())
    }
    #[cfg(not(windows))]
    {
        open_with_os(path)
    }
}

/// Python `_gallery_dl_default_config_path`.
fn gallery_dl_default_config() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var_os("APPDATA").map_or_else(
            || {
                std::env::var_os("USERPROFILE")
                    .map(PathBuf::from)
                    .unwrap_or_default()
                    .join("AppData")
                    .join("Roaming")
            },
            PathBuf::from,
        );
        base.join("gallery-dl").join("config.json")
    } else {
        std::env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_default()
            .join(".config")
            .join("gallery-dl")
            .join("config.json")
    }
}

type Job = Receiver<String>;
type CacheJob = Receiver<(String, Option<cyoa_storage::cache::ImageCacheStats>)>;

/// Dashboard state kept while the Settings window is open.
pub struct SettingsDashboard {
    pub page: usize,
    pub search: String,
    pub status: String,
    loaded: bool,
    cookie: String,
    itch_key: String,
    /// `itch_mirror_web`, `itch_only`, `itch_parallel`.
    itch_mirror: bool,
    /// gallery-dl executable and config.json paths (`gallery_dl_path`/`_config`).
    gdl_path: String,
    /// Deep-scan limits as typed (`deep_scan_*`, `DEEP_SCAN_SETTINGS` order).
    deep_limits: [String; 5],
    /// `serve_port` (0 = random free port).
    serve_port: String,
    deep_status: String,
    /// Appearance: `theme_mode` (System / Light / Dark).
    theme_mode: String,
    /// Accent hex text field of the theme editor.
    accent_hex: String,
    gdl_config: String,
    itch_only: bool,
    itch_parallel: i32,
    strategy: String,
    interaction: String,
    limits: Vec<String>,
    archive_status: String,
    features: Vec<bool>,
    discord: String,
    discord_status: String,
    /// `discord_refresh_enabled` (CLI `--no-discord-refresh` for one run).
    discord_refresh: bool,
    /// Last non-off `gallery_dl_mode` ("smart" or "force"), restored when the
    /// toggle is switched back on.
    gallery_dl_on_mode: String,
    discord_job: Option<Job>,
    /// Maintenance → Verify output: folder, running job, report text.
    verify_folder: String,
    verify_job: Option<Job>,
    verify_report: String,
    /// Maintenance → Modernize library: library, viewer collection, output.
    modern_paths: [String; 3],
    modern_job: Option<Job>,
    modern_status: String,
    /// Modernize options: target (0 library, 1 single CYOA), viewer source
    /// (0 collection folder, 1 registered offline viewers), analyze only,
    /// allow network for missing viewer assets.
    modern_target: usize,
    modern_source: usize,
    modern_analyze: bool,
    modern_network: bool,
    /// Live "[3/20] name" progress and the cancel flag of the running job.
    modern_progress: std::sync::Arc<std::sync::Mutex<String>>,
    modern_cancel: std::sync::Arc<std::sync::atomic::AtomicBool>,
    modern_last_output: Option<PathBuf>,
    cache_limit: String,
    cache_stats: String,
    cache_status: String,
    cache_job: Option<CacheJob>,
    confirm_clear: bool,
    file_job: Option<Job>,
    cf_mode: String,
    cf_priority: String,
    cf_session: String,
    cf_proxy: String,
    cf_url: String,
    cf_timeout: String,
    cf_wait: String,
    cf_status: String,
    cf_job: Option<Job>,
    /// Saved Cloudflare mode for the main-window selector.
    pub cf_mode_changed: Option<String>,
    /// Open the Updates / Batch Check Center (F-052; Rust addition).
    pub open_updates: bool,
    auto_status: String,
    net_draft: Option<crate::net::NetSettings>,
    net_port: String,
    net_timeout: String,
    net_preset: String,
    net_status: String,
}

impl Default for SettingsDashboard {
    fn default() -> Self {
        Self {
            page: 0,
            search: String::new(),
            status: String::new(),
            loaded: false,
            cookie: String::new(),
            itch_key: String::new(),
            itch_mirror: false,
            gdl_path: String::new(),
            deep_limits: Default::default(),
            serve_port: "0".into(),
            deep_status: String::new(),
            theme_mode: "System".into(),
            accent_hex: String::new(),
            gdl_config: String::new(),
            itch_only: false,
            itch_parallel: 1,
            strategy: "auto".into(),
            interaction: "safe".into(),
            limits: ARCHIVE_DEFAULTS.iter().map(i64::to_string).collect(),
            archive_status: String::new(),
            features: FEATURES.iter().map(|f| f.4).collect(),
            discord: String::new(),
            discord_status: String::new(),
            discord_refresh: true,
            gallery_dl_on_mode: "smart".into(),
            discord_job: None,
            verify_folder: String::new(),
            verify_job: None,
            verify_report: String::new(),
            modern_paths: Default::default(),
            modern_job: None,
            modern_status: String::new(),
            modern_target: 0,
            modern_source: 1,
            modern_analyze: false,
            modern_network: true,
            modern_progress: Default::default(),
            modern_cancel: Default::default(),
            modern_last_output: None,
            cache_limit: "2".into(),
            cache_stats: String::new(),
            cache_status: String::new(),
            cache_job: None,
            confirm_clear: false,
            file_job: None,
            cf_mode: "Auto".into(),
            cf_priority: "FlareSolverr first".into(),
            cf_session: "reuse-domain".into(),
            cf_proxy: "inherit".into(),
            cf_url: "http://localhost:8191/v1".into(),
            cf_timeout: "60".into(),
            cf_wait: "3".into(),
            cf_status: String::new(),
            cf_job: None,
            cf_mode_changed: None,
            open_updates: false,
            auto_status: String::new(),
            net_draft: None,
            net_port: "0".into(),
            net_timeout: "5".into(),
            net_preset: String::new(),
            net_status: "Not validated yet.".into(),
        }
    }
}

fn load(path: &Path) -> serde_json::Map<String, Value> {
    cyoa_storage::settings::SettingsStore::new(path)
        .load()
        .ok()
        .cloned()
        .unwrap_or_default()
}

fn update(path: &Path, f: impl FnOnce(&mut serde_json::Map<String, Value>)) -> Result<(), String> {
    crate::net::update_shared(path, f)
}

impl SettingsDashboard {
    /// Reload the card values from settings.json (window opened).
    pub fn reload(&mut self, path: &Path, ctx: &egui::Context) {
        let st = load(path);
        let text = |k: &str, d: &str| {
            st.get(k)
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or(d)
                .to_owned()
        };
        self.cookie = text("ytdlp_cookies", "");
        self.strategy = text("archive_strategy", "auto").to_lowercase();
        self.interaction = text("archive_interaction_policy", "safe").to_lowercase();
        // Show what the engine will actually run (legacy capture forces safe).
        if st
            .get("archive_capture_interactions")
            .is_some_and(|v| v.as_bool() == Some(true))
        {
            self.interaction = "safe".into();
        }
        self.limits = ARCHIVE_LIMITS
            .iter()
            .zip(ARCHIVE_DEFAULTS)
            .map(|((_, _, key, _, _), d)| match st.get(*key) {
                Some(Value::Number(n)) => n.to_string(),
                Some(Value::String(s)) => s.clone(),
                _ => d.to_string(),
            })
            .collect();
        let gdl = text("gallery_dl_mode", "off").to_lowercase();
        if gdl == "force" || gdl == "smart" {
            self.gallery_dl_on_mode = gdl;
        }
        self.features = FEATURES
            .iter()
            .map(|(_, _, _, key, default)| {
                if *key == "gallery_dl_mode" {
                    text(key, "off").to_lowercase() != "off"
                } else {
                    st.get(*key).and_then(Value::as_bool).unwrap_or(*default)
                }
            })
            .collect();
        // Secrets are loaded into the hidden entries like Python.
        self.gdl_path = text("gallery_dl_path", "");
        self.serve_port = st
            .get("serve_port")
            .and_then(Value::as_u64)
            .filter(|p| *p <= 65535)
            .unwrap_or(0)
            .to_string();
        for (i, (key, _, _, lo, hi, default)) in
            cyoa_engine::website::deep_driver::DEEP_SCAN_SETTINGS
                .iter()
                .enumerate()
        {
            let v = st
                .get(*key)
                .and_then(Value::as_i64)
                .unwrap_or(*default)
                .clamp(*lo, *hi);
            self.deep_limits[i] = v.to_string();
        }
        self.theme_mode = match text("theme_mode", "System").as_str() {
            m @ ("Light" | "Dark") => m.to_owned(),
            _ => "System".to_owned(),
        };
        self.gdl_config = text("gallery_dl_config", "");
        self.itch_mirror = st
            .get("itch_mirror_web")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        self.itch_only = st
            .get("itch_only")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        self.itch_parallel = st
            .get("itch_parallel")
            .and_then(Value::as_i64)
            .map_or(1, |n| n.clamp(1, 16) as i32);
        self.itch_key =
            cyoa_engine::itch::resolve_itch_api_key(None, Some(&cyoa_engine::itch::OsItchKeyring))
                .key
                .unwrap_or_default();
        self.discord = cyoa_engine::discord::resolve_bot_token(None).unwrap_or_default();
        self.discord_refresh = st
            .get("discord_refresh_enabled")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        if !self.discord.is_empty() {
            self.discord_status = "Token loaded and hidden.".into();
        }
        let cf = cyoa_net::cloudflare::CloudflareConfig::from_settings(&st);
        self.cf_mode = cyoa_net::cloudflare::display_cloudflare_mode(&cf.mode);
        self.cf_priority = cyoa_net::cloudflare::display_cloudflare_priority(&cf.priority);
        self.cf_session = cf.session_policy.clone();
        self.cf_proxy = cf.proxy_mode.clone();
        self.cf_url = cf.flaresolverr_url.clone();
        self.cf_timeout = cf.timeout_s.to_string();
        self.cf_wait = cf.wait_after_s.to_string();
        self.refresh_cache(ctx);
        self.loaded = true;
    }

    pub fn ensure_loaded(&mut self, path: &Path, ctx: &egui::Context) {
        if !self.loaded {
            self.reload(path, ctx);
        }
    }

    pub fn closed(&mut self) {
        self.loaded = false;
        self.net_draft = None;
        self.auto_status.clear();
    }

    fn poll(&mut self) {
        for (job, slot) in [
            (&mut self.discord_job, &mut self.discord_status),
            (&mut self.file_job, &mut self.status),
            (&mut self.cf_job, &mut self.cf_status),
            (&mut self.verify_job, &mut self.verify_report),
            (&mut self.modern_job, &mut self.modern_status),
        ] {
            if let Some(rx) = job {
                match rx.try_recv() {
                    Ok(msg) => {
                        *slot = msg;
                        *job = None;
                    }
                    Err(TryRecvError::Disconnected) => *job = None,
                    Err(TryRecvError::Empty) => {}
                }
            }
        }
        if let Some(rx) = &self.cache_job {
            match rx.try_recv() {
                Ok((status, stats)) => {
                    if !status.is_empty() {
                        self.cache_status = status;
                    }
                    if let Some(stats) = stats {
                        self.cache_stats = format!(
                            "{} files · {} MB used",
                            stats.entries,
                            stats.size_bytes / (1024 * 1024)
                        );
                        self.cache_limit = py_g(stats.limit_mb as f64 / 1024.0);
                    }
                    self.cache_job = None;
                }
                Err(TryRecvError::Disconnected) => {
                    self.cache_status = "Cache operation stopped unexpectedly.".into();
                    self.cache_job = None;
                }
                Err(TryRecvError::Empty) => {}
            }
        }
    }

    fn refresh_cache(&mut self, ctx: &egui::Context) {
        self.start_cache_job(ctx, "", move || {
            let root = cyoa_storage::cache::ImageCache::default_root()
                .ok_or_else(|| "Image cache unavailable".to_owned())?;
            let limit =
                cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path())
                    .load()
                    .ok()
                    .and_then(|s| s.get("image_cache_max_mb").and_then(Value::as_u64))
                    .unwrap_or(2048);
            let cache = cyoa_storage::cache::ImageCache::open(&root, limit)
                .map_err(|error| error.to_string())?;
            Ok((String::new(), cache.stats()))
        });
    }

    fn start_cache_job(
        &mut self,
        ctx: &egui::Context,
        started: &str,
        work: impl FnOnce() -> Result<(String, cyoa_storage::cache::ImageCacheStats), String>
        + Send
        + 'static,
    ) {
        if self.cache_job.is_some() {
            return;
        }
        let (tx, rx) = std::sync::mpsc::channel();
        let repaint = ctx.clone();
        self.cache_status = started.to_owned();
        self.cache_job = Some(rx);
        let spawned = std::thread::Builder::new()
            .name("image-cache-maintenance".into())
            .spawn(move || {
                let result = work();
                let message = match result {
                    Ok((status, stats)) => (status, Some(stats)),
                    Err(error) => (error, None),
                };
                let _ = tx.send(message);
                repaint.request_repaint();
            });
        if spawned.is_err() {
            self.cache_job = None;
            self.cache_status = "Could not start cache operation.".into();
        }
    }

    // ── General page ───────────────────────────────────────────────────

    /// Python Download page (auto output, features, credentials, archive).
    /// Returns a new `auto_detect_output` value when the segment changed.
    pub fn general_cards(
        &mut self,
        ui: &mut egui::Ui,
        path: &Path,
        ctx: &egui::Context,
        auto_pref: &str,
    ) -> Option<&'static str> {
        self.poll();
        self.appearance_card(ui, path, ctx);
        let p = crate::theme::palette(ui);
        let mut new_pref = None;
        // Default Auto Output (blue-bordered card with a segmented control).
        let blue = rgb(0x3b, 0x82, 0xf6);
        egui::Frame::new()
            .fill(p.surface)
            .stroke(egui::Stroke::new(1.0, blue))
            .corner_radius(12.0)
            .inner_margin(egui::Margin::same(12))
            .outer_margin(egui::Margin::symmetric(0, 5))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    badge_label(ui, "AUTO", rgb(0x25, 0x63, 0xeb));
                    ui.add_space(4.0);
                    ui.vertical(|ui| {
                        ui.label(
                            egui::RichText::new("Default Auto Output")
                                .family(egui::FontFamily::Name("bold".into()))
                                .size(12.0),
                        );
                        ui.label(
                            egui::RichText::new(
                                "Chooses Folder or ZIP whenever output mode is Auto.",
                            )
                            .size(9.0)
                            .color(muted(ui)),
                        );
                        let status = if self.auto_status.is_empty() {
                            if auto_pref == "zip" {
                                "Currently: ZIP output"
                            } else {
                                "Currently: folder output"
                            }
                            .to_owned()
                        } else {
                            self.auto_status.clone()
                        };
                        ui.label(egui::RichText::new(status).size(9.0).color(muted2(ui)));
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if let Some(v) =
                            segmented(ui, &[("ZIP", "zip"), ("Folder", "folder")], auto_pref)
                        {
                            self.auto_status = if v == "zip" {
                                "Saved: ZIP output".into()
                            } else {
                                "Saved: folder output".into()
                            };
                            new_pref = Some(v);
                        }
                    });
                });
            });

        title(
            ui,
            "Download Features",
            "Optional helpers are applied immediately to the current session.",
        );
        for pair in (0..FEATURES.len()).collect::<Vec<_>>().chunks(2) {
            ui.columns(2, |cols| {
                for (c, &i) in pair.iter().enumerate() {
                    let ui = &mut cols[c];
                    let (badge, name, desc, key, _) = FEATURES[i];
                    let color = FEATURE_COLORS[i % FEATURE_COLORS.len()];
                    let mut on = self.features[i];
                    let mut changed = false;
                    frame(ui, crate::theme::palette(ui).border).show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.set_min_height(46.0);
                        ui.horizontal(|ui| {
                            badge_label(ui, badge, color);
                            ui.vertical(|ui| {
                                ui.label(
                                    egui::RichText::new(name)
                                        .family(egui::FontFamily::Name("bold".into()))
                                        .size(11.0),
                                );
                                ui.label(egui::RichText::new(desc).size(9.0).color(muted(ui)));
                            });
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    changed = toggle(ui, &mut on, color);
                                },
                            );
                        });
                    });
                    if changed {
                        self.features[i] = on;
                        let gdl = key == "gallery_dl_mode";
                        let value: Value = if gdl {
                            // Keep "force" instead of silently downgrading it.
                            (if on {
                                self.gallery_dl_on_mode.as_str()
                            } else {
                                "off"
                            })
                            .into()
                        } else {
                            on.into()
                        };
                        let shown = match (on, gdl) {
                            (true, true) => format!("ON ({})", self.gallery_dl_on_mode),
                            (true, false) => "ON".to_owned(),
                            (false, _) => "OFF".to_owned(),
                        };
                        self.status = match update(path, |m| {
                            m.insert(key.into(), value);
                        }) {
                            Ok(()) => format!("{name}: {shown}"),
                            Err(e) => format!("{name}: {e}"),
                        };
                    }
                }
            });
        }

        title(
            ui,
            "Access & Credentials",
            "Optional authentication used by download services.",
        );
        boxed(
            ui,
            "YT",
            rgb(0xb9, 0x1c, 0x1c),
            "YouTube cookies",
            "Used by yt-dlp for login or age-restricted media; the file remains local.",
            |ui| {
                ui.horizontal(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.cookie)
                            .hint_text("Netscape cookies.txt (optional)")
                            .desired_width(ui.available_width() - 160.0),
                    );
                    if ui.button("Browse").clicked()
                        && let Some(p) = rfd::FileDialog::new()
                            .add_filter("Cookies", &["txt"])
                            .pick_file()
                    {
                        self.cookie = p.display().to_string();
                    }
                    if accent_button(ui, "Save", rgb(0xb9, 0x1c, 0x1c)) {
                        let value = self.cookie.trim().to_owned();
                        self.status = if !value.is_empty() && !Path::new(&value).is_file() {
                            format!("yt-dlp cookie file not found: {value}")
                        } else {
                            match update(path, |m| {
                                m.insert("ytdlp_cookies".into(), value.into());
                            }) {
                                Ok(()) => "YouTube cookie setting saved.".into(),
                                Err(e) => e,
                            }
                        };
                    }
                });
            },
        );
        boxed(
            ui,
            "KEY",
            rgb(0x25, 0x63, 0xeb),
            "itch.io API key",
            "Required by itch-dl; keyring storage is preferred.",
            |ui| {
                ui.horizontal(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.itch_key)
                            .password(true)
                            .hint_text("itch.io API key")
                            .desired_width(220.0),
                    );
                    if accent_button(ui, "Save", rgb(0x25, 0x63, 0xeb)) {
                        self.save_itch_key(path);
                    }
                });
                // itch-dl run options (used when the itch.io downloader is on).
                let mut changed = false;
                ui.horizontal(|ui| {
                    changed |= ui
                        .checkbox(&mut self.itch_mirror, "Mirror linked web builds")
                        .changed();
                    changed |= ui
                        .checkbox(&mut self.itch_only, "itch.io only (skip the CYOA download)")
                        .on_hover_text("For itch.io URLs, run only itch-dl, like --itch-only.")
                        .changed();
                });
                ui.horizontal(|ui| {
                    ui.label("Parallel downloads");
                    changed |= ui
                        .add(egui::DragValue::new(&mut self.itch_parallel).range(1..=16))
                        .changed();
                });
                if changed {
                    let (mirror, only, parallel) =
                        (self.itch_mirror, self.itch_only, self.itch_parallel);
                    self.status = match update(path, |m| {
                        m.insert("itch_mirror_web".into(), mirror.into());
                        m.insert("itch_only".into(), only.into());
                        m.insert("itch_parallel".into(), parallel.into());
                    }) {
                        Ok(()) => "itch.io options saved.".into(),
                        Err(e) => e,
                    };
                }
            },
        );

        title(
            ui,
            "JavaScript Archive Policy",
            "Auto + Safe is recommended. These are safety caps for dynamic website discovery.",
        );
        let cyan = rgb(0x08, 0x91, 0xb2);
        let mut archive_preset: Option<(&str, [i64; 7])> = None;
        frame(ui, cyan).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("Preset").size(10.0));
                for (name, help, strategy, values) in ARCHIVE_PRESETS {
                    if ui.button(*name).on_hover_text(*help).clicked() {
                        archive_preset = Some((strategy, *values));
                    }
                }
            });
            group_label(ui, "Archive behavior");
            ui.columns(2, |cols| {
                field_label(&mut cols[0], "Strategy", "Auto selects the lightest complete pipeline.");
                combo(
                    &mut cols[0],
                    "arch-strategy",
                    &mut self.strategy,
                    &["auto", "classic", "smart", "browser"],
                );
                field_label(
                    &mut cols[1],
                    "Safe interaction",
                    "Safe permits guarded scroll/click; Off never clicks.",
                );
                combo(&mut cols[1], "arch-interaction", &mut self.interaction, &["safe", "off"]);
            });
            for (group, idx) in [
                ("Discovery limits", vec![0usize, 1]),
                ("Browser runtime limits", vec![2, 3, 4, 5, 6]),
            ] {
                group_label(ui, group);
                for pair in idx.chunks(2) {
                    ui.columns(2, |cols| {
                        for (c, &i) in pair.iter().enumerate() {
                            let (label, hint, _, _, _) = ARCHIVE_LIMITS[i];
                            field_label(&mut cols[c], label, hint);
                            let w = cols[c].available_width();
                            cols[c].add(
                                egui::TextEdit::singleline(&mut self.limits[i]).desired_width(w),
                            );
                        }
                        if pair.len() == 1 {
                            let p = crate::theme::palette(&cols[1]);
                            egui::Frame::new()
                                .fill(p.panel)
                                .corner_radius(8.0)
                                .inner_margin(egui::Margin::same(10))
                                .show(&mut cols[1], |ui| {
                                    ui.label(
                                        egui::RichText::new(
                                            "Every number is a safety cap, not a download target. Keep the defaults unless a site is incomplete.",
                                        )
                                        .size(9.0)
                                        .color(muted(ui)),
                                    );
                                });
                        }
                    });
                }
            }
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new(&self.archive_status)
                        .size(9.0)
                        .color(rgb(0x22, 0xd3, 0xee)),
                );
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if accent_button(ui, "Save archive policy", cyan) {
                        self.save_archive(path);
                    }
                });
            });
        });
        if let Some((strategy, values)) = archive_preset {
            self.strategy = strategy.to_owned();
            self.interaction = "safe".into();
            for (slot, v) in self.limits.iter_mut().zip(values) {
                *slot = v.to_string();
            }
            self.save_archive(path);
        }
        self.deep_scan_card(ui, path);
        self.serve_port_row(ui, path);
        let _ = ctx;
        new_pref
    }

    /// Serve preview options (`serve_*`): port, browser, tools, storage,
    /// auto-serve after a website download.
    fn serve_port_row(&mut self, ui: &mut egui::Ui, path: &Path) {
        let opts = cyoa_engine::serve_tools::ServeOptions::from_settings(&load(path));
        let amber = rgb(0xd9, 0x77, 0x06);
        let mut flags = [
            (
                "serve_open_browser",
                opts.open_browser,
                "Open the browser when Serve starts",
                "Off: copy the URL from the main window instead.",
            ),
            (
                "serve_tools_enabled",
                opts.tools,
                "Inject Serve Tools (⚡ bar + ⚙ cheat panel)",
                "Off: pages are served exactly as downloaded.",
            ),
            (
                "serve_clear_on_start",
                opts.clear_on_start,
                "Start with cleared browser storage",
                "Opens /__clear_cache__ first so old saves or cached builds don't interfere.",
            ),
            (
                "serve_auto_after_download",
                opts.auto_after_download,
                "Serve automatically after a website download",
                "Starts the preview as soon as a website folder finishes.",
            ),
        ];
        let mut changed: Option<(&'static str, bool)> = None;
        boxed(
            ui,
            "SRV",
            amber,
            "Serve preview",
            "Local preview server on 127.0.0.1 (main window → Start Serve).",
            |ui| {
                ui.horizontal(|ui| {
                    ui.label("Port");
                    let resp = ui
                        .add(
                            egui::TextEdit::singleline(&mut self.serve_port)
                                .hint_text("0")
                                .desired_width(60.0),
                        )
                        .on_hover_text("0 picks a random free port each time; set a number to keep the same URL.");
                    if resp.lost_focus() {
                        match self.serve_port.trim().parse::<u16>() {
                            Ok(port) => {
                                self.status = match update(path, |m| {
                                    m.insert("serve_port".into(), port.into());
                                }) {
                                    Ok(()) => format!("Serve port saved: {port}"),
                                    Err(e) => e,
                                };
                            }
                            Err(_) => {
                                self.status = "Serve port must be 0–65535.".into();
                                self.serve_port = "0".into();
                            }
                        }
                    }
                    ui.label(
                        egui::RichText::new("0 = random")
                            .size(9.0)
                            .color(muted(ui)),
                    );
                });
                for (key, on, label, help) in &mut flags {
                    if ui.checkbox(on, *label).on_hover_text(*help).changed() {
                        changed = Some((key, *on));
                    }
                }
            },
        );
        if let Some((key, on)) = changed {
            self.status = match update(path, |m| {
                m.insert(key.into(), on.into());
            }) {
                Ok(()) => format!("Saved: {key} = {on}"),
                Err(e) => e,
            };
        }
    }

    /// Deep-scan caps (`deep_scan_*`): rounds, candidates per round, file
    /// size, requests and total bytes, with one-click presets.
    fn deep_scan_card(&mut self, ui: &mut egui::Ui, path: &Path) {
        use cyoa_engine::website::deep_driver::DEEP_SCAN_SETTINGS;
        let teal = rgb(0x0d, 0x94, 0x88);
        let mut save = false;
        title(
            ui,
            "Deep Scan",
            "Follows JS/CSS/JSON references to fetch assets the page loads later. Raise the limits for big sites.",
        );
        frame(ui, teal).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("Preset").size(10.0));
                for (name, values) in DEEP_PRESETS {
                    if ui.button(*name).clicked() {
                        for (slot, v) in self.deep_limits.iter_mut().zip(values) {
                            *slot = v.to_string();
                        }
                        save = true;
                    }
                }
            });
            for chunk in (0..DEEP_SCAN_SETTINGS.len()).collect::<Vec<_>>().chunks(2) {
                ui.columns(2, |cols| {
                    for (c, &i) in chunk.iter().enumerate() {
                        let (_, label, help, ..) = DEEP_SCAN_SETTINGS[i];
                        field_label(&mut cols[c], label, help);
                        let w = cols[c].available_width();
                        cols[c].add(
                            egui::TextEdit::singleline(&mut self.deep_limits[i]).desired_width(w),
                        );
                    }
                });
            }
            ui.horizontal(|ui| {
                if accent_button(ui, "Save deep scan limits", teal) {
                    save = true;
                }
                ui.label(
                    egui::RichText::new(&self.deep_status)
                        .size(9.0)
                        .color(muted(ui)),
                );
            });
        });
        if !save {
            return;
        }
        let mut values = Vec::new();
        for (i, (key, _, _, lo, hi, _)) in DEEP_SCAN_SETTINGS.iter().enumerate() {
            let Ok(v) = self.deep_limits[i].trim().parse::<i64>() else {
                self.deep_status = "Use whole numbers for deep scan limits.".into();
                return;
            };
            let v = v.clamp(*lo, *hi);
            self.deep_limits[i] = v.to_string();
            values.push((*key, v));
        }
        self.deep_status = match update(path, |m| {
            for (k, v) in values {
                m.insert(k.into(), v.into());
            }
        }) {
            Ok(()) => "Saved.".into(),
            Err(e) => e,
        };
    }

    fn save_itch_key(&mut self, path: &Path) {
        let key = self.itch_key.trim().to_owned();
        let api = cyoa_storage::secrets::native_keyring();
        let stored = if key.is_empty() {
            Ok(())
        } else {
            api.set_password("cyoa_downloader_itch", "itch_api_key", &key)
        };
        self.status = match stored {
            Ok(()) => match update(path, |m| {
                m.insert("itch_key_storage".into(), "keyring".into());
                m.insert("itch_api_key".into(), "".into());
            }) {
                Ok(()) => "Saved to OS keyring.".into(),
                Err(e) => e,
            },
            // DL-5: never fall back to a plaintext secret silently.
            Err(e) => format!("Could not save to the OS keyring: {e}"),
        };
    }

    /// Python `_save_archive`: clamp whole numbers, save all keys at once.
    fn save_archive(&mut self, path: &Path) {
        let mut values = Vec::new();
        for (i, (_, _, key, min, max)) in ARCHIVE_LIMITS.iter().enumerate() {
            let Ok(v) = self.limits[i].trim().parse::<i64>() else {
                self.archive_status = "Use whole numbers for archive limits.".into();
                return;
            };
            let v = v.clamp(*min, *max);
            self.limits[i] = v.to_string();
            values.push((*key, v));
        }
        let (strategy, interaction) = (
            self.strategy.to_lowercase(),
            self.interaction.to_lowercase(),
        );
        match update(path, |m| {
            m.insert("archive_strategy".into(), strategy.into());
            // The legacy `archive_capture_interactions` flag forces "safe" in the
            // engine; keep it in sync so a displayed "off" really runs off.
            m.insert(
                "archive_capture_interactions".into(),
                (interaction == "safe").into(),
            );
            m.insert("archive_interaction_policy".into(), interaction.into());
            for (k, v) in values {
                m.insert(k.into(), v.into());
            }
        }) {
            Ok(()) => {
                self.archive_status = "Saved.".into();
                self.status = "Archive settings saved.".into();
            }
            Err(e) => self.archive_status = e,
        }
    }

    /// Python Network page card (proxy, DNS, VPN guard; Save & apply,
    /// Validate offline). Edits a draft; "Save & apply" writes it to `net`.
    pub fn network_card(&mut self, ui: &mut egui::Ui, net: &mut crate::net::NetSettings) {
        let draft = self.net_draft.get_or_insert_with(|| {
            self.net_port = net.dns_port.to_string();
            self.net_timeout = net.dns_timeout.to_string();
            self.net_preset = crate::app::dns_preset_label(&net.dns);
            net.clone()
        });
        let p = crate::theme::palette(ui);
        let mut apply = false;
        let mut validate = false;
        // CLI `--allow-internal-hosts`: process-wide and deliberately not saved.
        let mut allow_internal = cyoa_core::allow_internal_hosts();
        if ui
            .checkbox(
                &mut allow_internal,
                "Allow internal network hosts (this session only)",
            )
            .on_hover_text(
                "Lets downloads reach localhost/LAN addresses (e.g. a self-hosted CYOA). \
                 Leave off for public sites: it disables SSRF protection until the app restarts.",
            )
            .changed()
        {
            cyoa_core::set_allow_internal_hosts(allow_internal);
        }
        if allow_internal {
            ui.label(
                egui::RichText::new("⚠ Internal hosts allowed for this session.")
                    .size(9.0)
                    .color(rgb(0xf5, 0x9e, 0x0b)),
            );
        }
        frame(ui, p.border).show(ui, |ui| {
            ui.set_width(ui.available_width());
            let section = |ui: &mut egui::Ui, name: &str, desc: &str| {
                ui.add_space(6.0);
                ui.label(egui::RichText::new(name).family(egui::FontFamily::Name("bold".into())).size(11.0));
                ui.label(egui::RichText::new(desc).size(9.0).color(muted(ui)));
                ui.add_space(4.0);
            };
            let label = |ui: &mut egui::Ui, t: &str| {
                ui.label(egui::RichText::new(t).size(9.0).color(muted(ui)));
            };
            section(
                ui,
                "Proxy",
                "HTTP, HTTPS, SOCKS4/5, and SOCKS5h are accepted. SOCKS5h resolves names through the proxy.",
            );
            let quarter = (ui.available_width() - 30.0) / 4.0;
            ui.horizontal_top(|ui| {
                ui.vertical(|ui| {
                    label(ui, "Mode");
                    combo_w(ui, "net-mode", &mut draft.proxy_mode, &["inherit_env", "manual", "disabled"], quarter);
                });
                ui.vertical(|ui| {
                    label(ui, "Common proxy");
                    ui.add(
                        egui::TextEdit::singleline(&mut draft.proxy)
                            .hint_text("socks5h://127.0.0.1:1080")
                            .desired_width(ui.available_width()),
                    );
                });
            });
            ui.columns(2, |cols| {
                label(&mut cols[0], "HTTP override");
                let w = cols[0].available_width();
                cols[0].add(
                    egui::TextEdit::singleline(&mut draft.proxy_http)
                        .hint_text("http://127.0.0.1:8080")
                        .desired_width(w),
                );
                label(&mut cols[1], "HTTPS override");
                let w = cols[1].available_width();
                cols[1].add(
                    egui::TextEdit::singleline(&mut draft.proxy_https)
                        .hint_text("http://127.0.0.1:8080")
                        .desired_width(w),
                );
            });
            label(ui, "Bypass hosts / NO_PROXY");
            ui.add(
                egui::TextEdit::singleline(&mut draft.proxy_no_proxy)
                    .hint_text("localhost,127.0.0.1,::1,.local")
                    .desired_width(ui.available_width()),
            );
            section(
                ui,
                "DNS",
                "Choose system DNS, plain UDP/TCP, DNS-over-HTTPS (DoH), or DNS-over-TLS (DoT).",
            );
            ui.horizontal_top(|ui| {
                ui.vertical(|ui| {
                    label(ui, "Preset");
                    let before = self.net_preset.clone();
                    let labels: Vec<&str> =
                        crate::app::DNS_PRESET_LIST.iter().map(|(l, _)| *l).collect();
                    combo_w(ui, "net-preset", &mut self.net_preset, &labels, quarter);
                    if self.net_preset != before
                        && let Some((_, endpoint)) = crate::app::DNS_PRESET_LIST
                            .iter()
                            .find(|(l, _)| *l == self.net_preset)
                        && *endpoint != "__custom__"
                    {
                        draft.dns = (*endpoint).to_owned();
                        draft.dns_protocol =
                            cyoa_net::dns::infer_dns_protocol(endpoint, None);
                        self.net_port = "0".into();
                    }
                });
                ui.vertical(|ui| {
                    label(ui, "Protocol");
                    combo_w(ui, "net-proto", &mut draft.dns_protocol, &["system", "udp", "tcp", "doh", "dot"], quarter);
                });
                ui.vertical(|ui| {
                    label(ui, "Resolver endpoint");
                    ui.add(
                        egui::TextEdit::singleline(&mut draft.dns)
                            .hint_text("1.1.1.1 or https://cloudflare-dns.com/dns-query")
                            .desired_width(ui.available_width()),
                    );
                });
            });
            ui.horizontal_top(|ui| {
                ui.vertical(|ui| {
                    label(ui, "Port (0 = default)");
                    ui.add(egui::TextEdit::singleline(&mut self.net_port).desired_width(quarter));
                });
                ui.vertical(|ui| {
                    label(ui, "Timeout (seconds)");
                    ui.add(egui::TextEdit::singleline(&mut self.net_timeout).desired_width(quarter));
                });
                ui.vertical(|ui| {
                    ui.add_space(16.0);
                    ui.horizontal(|ui| {
                        crate::theme::checkbox(ui, &p, &mut draft.dns_fallback_system, "Fall back to system DNS", p.accent, true);
                        ui.add_space(20.0);
                        crate::theme::checkbox(ui, &p, &mut draft.dns_ipv6, "Resolve IPv6 / AAAA", p.accent, true);
                    });
                });
            });
            section(
                ui,
                "VPN guard",
                "System follows the OS route table. Require blocks downloader requests unless a VPN-like interface is active; it never starts or changes a VPN.",
            );
            ui.horizontal_top(|ui| {
                ui.vertical(|ui| {
                    label(ui, "Policy");
                    combo_w(ui, "net-vpn", &mut draft.vpn_policy, &["system", "require"], quarter);
                });
                ui.vertical(|ui| {
                    label(ui, "Interface name contains (optional)");
                    ui.add(
                        egui::TextEdit::singleline(&mut draft.vpn_interface)
                            .hint_text("WireGuard, Wintun, NordLynx, Tailscale…")
                            .desired_width(ui.available_width()),
                    );
                });
            });
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new(&self.net_status).size(9.0).color(muted(ui)));
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    validate = ui.button("Validate offline").clicked();
                    apply = accent_button(ui, "Save & apply", rgb(0x25, 0x63, 0xeb));
                });
            });
        });
        if apply || validate {
            self.apply_network(net, validate);
        }
    }

    /// Python `_apply_network_settings` / `_test_network_settings`.
    fn apply_network(&mut self, net: &mut crate::net::NetSettings, validate: bool) {
        let Some(draft) = self.net_draft.clone() else {
            return;
        };
        let port = match self.net_port.trim() {
            "" => Ok(0),
            t => t.parse::<i64>().map(|v| v.clamp(0, 65535) as u16),
        };
        let timeout = match self.net_timeout.trim() {
            "" => Ok(5),
            t => t.parse::<i64>().map(|v| v.clamp(1, 60) as u32),
        };
        let (Ok(port), Ok(timeout)) = (port, timeout) else {
            self.net_status = "Error: invalid literal for int() with base 10".into();
            return;
        };
        let mut next = draft;
        next.dns_port = port;
        next.dns_timeout = timeout;
        for v in [
            &mut next.proxy,
            &mut next.proxy_http,
            &mut next.proxy_https,
            &mut next.proxy_no_proxy,
            &mut next.dns,
            &mut next.vpn_interface,
        ] {
            *v = v.trim().to_owned();
        }
        if let Err(e) = next.build_check() {
            self.net_status = format!("Error: {e}");
            return;
        }
        *net = next.clone();
        self.net_draft = Some(next.clone());
        self.net_preset = crate::app::dns_preset_label(&next.dns);
        let saved = "Saved and applied to new requests.".to_owned();
        self.net_status = if validate {
            let guard = cyoa_net::vpn::VpnConfig::new(&next.vpn_policy, &next.vpn_interface)
                .map(cyoa_net::vpn::VpnGuard::new);
            match guard {
                Ok(g) if next.vpn_policy == "require" && !g.status().available => {
                    "Valid config, but required VPN interface is not active. No external request sent.".into()
                }
                Ok(_) => {
                    let proxy = match next.proxy_mode.as_str() {
                        "manual" => {
                            let mut keys = Vec::new();
                            if !next.proxy_http.is_empty() || !next.proxy.is_empty() {
                                keys.push("HTTP");
                            }
                            if !next.proxy_https.is_empty() || !next.proxy.is_empty() {
                                keys.push("HTTPS");
                            }
                            keys.join("/")
                        }
                        "inherit_env" => {
                            let mut keys: Vec<String> = ["all", "http", "https"]
                                .iter()
                                .filter(|k| {
                                    std::env::var(format!("{k}_proxy")).is_ok()
                                        || std::env::var(format!("{}_PROXY", k.to_uppercase())).is_ok()
                                })
                                .map(|k| k.to_uppercase())
                                .collect();
                            keys.sort();
                            keys.join("/")
                        }
                        _ => String::new(),
                    };
                    let proxy = if proxy.is_empty() { "direct/system".to_owned() } else { proxy };
                    format!(
                        "Valid: DNS {}; proxy {proxy}. No external request sent.",
                        next.dns_protocol.to_uppercase()
                    )
                }
                Err(e) => format!("Error: {e}"),
            }
        } else {
            saved
        };
        self.status = self.net_status.clone();
    }

    // ── Integrations page ──────────────────────────────────────────────

    pub fn integration_cards(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        self.poll();
        self.cloudflare_card(ui, path, ctx);
        boxed(
            ui,
            "GD",
            rgb(0x0f, 0x76, 0x6e),
            "gallery-dl",
            "Mode, executable and config used for gallery URLs.",
            |ui| {
                let mut save = false;
                for (label, value, hint, file) in [
                    (
                        "Executable",
                        &mut self.gdl_path,
                        "gallery-dl (on PATH)",
                        true,
                    ),
                    ("Config", &mut self.gdl_config, "Default config.json", true),
                ] {
                    ui.horizontal(|ui| {
                        ui.add_sized([70.0, 18.0], egui::Label::new(label));
                        save |= ui
                            .add(
                                egui::TextEdit::singleline(value)
                                    .hint_text(hint)
                                    .desired_width(240.0),
                            )
                            .lost_focus();
                        if file
                            && ui.button("…").clicked()
                            && let Some(f) = rfd::FileDialog::new().pick_file()
                        {
                            *value = f.display().to_string();
                            save = true;
                        }
                    });
                }
                ui.horizontal(|ui| {
                    if accent_button(ui, "Edit JSON", rgb(0x0f, 0x76, 0x6e)) {
                        self.status = match open_gallery_dl_config(path) {
                            Ok(()) => String::new(),
                            Err(e) => format!("Open gallery-dl config: {e}"),
                        };
                    }
                    ui.label("Mode");
                    let mut mode = if self.features_gdl_on() {
                        self.gallery_dl_on_mode.clone()
                    } else {
                        "off".to_owned()
                    };
                    let before = mode.clone();
                    egui::ComboBox::from_id_salt("gdl-mode")
                        .selected_text(&mode)
                        .show_ui(ui, |ui| {
                            for m in ["off", "smart", "force"] {
                                ui.selectable_value(&mut mode, m.to_owned(), m);
                            }
                        });
                    if mode != before {
                        if mode != "off" {
                            self.gallery_dl_on_mode = mode.clone();
                        }
                        self.set_gdl_feature(mode != "off");
                        self.status = match update(path, |m| {
                            m.insert("gallery_dl_mode".into(), mode.clone().into());
                        }) {
                            Ok(()) => format!("gallery-dl: {mode}"),
                            Err(e) => e,
                        };
                    }
                });
                if save {
                    let (exe, cfg) = (
                        self.gdl_path.trim().to_owned(),
                        self.gdl_config.trim().to_owned(),
                    );
                    self.status = match update(path, |m| {
                        m.insert("gallery_dl_path".into(), exe.into());
                        m.insert("gallery_dl_config".into(), cfg.into());
                    }) {
                        Ok(()) => "gallery-dl paths saved.".into(),
                        Err(e) => e,
                    };
                }
            },
        );
        let blurple = rgb(0x58, 0x65, 0xf2);
        boxed(
            ui,
            "DC",
            blurple,
            "Discord Bot Token",
            "Used only to refresh expired Discord attachment URLs.",
            |ui| {
                ui.horizontal(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.discord)
                            .password(true)
                            .hint_text("Discord Bot Token")
                            .desired_width(260.0),
                    );
                    if accent_button(ui, "Save", blurple) {
                        let token = self.discord.trim().to_owned();
                        self.discord_status = if token.is_empty() {
                            "Enter a token first.".into()
                        } else {
                            match update(path, |m| {
                                m.insert("discord_bot_token".into(), token.into());
                            }) {
                                Ok(()) => "Saved.".into(),
                                Err(e) => e,
                            }
                        };
                    }
                    if ui
                        .add_enabled(self.discord_job.is_none(), egui::Button::new("Test"))
                        .clicked()
                    {
                        self.test_discord(ctx);
                    }
                });
                if ui
                    .checkbox(
                        &mut self.discord_refresh,
                        "Refresh expired Discord attachment URLs",
                    )
                    .changed()
                {
                    let on = self.discord_refresh;
                    if let Err(e) = update(path, |m| {
                        m.insert("discord_refresh_enabled".into(), on.into());
                    }) {
                        self.discord_status = e;
                    }
                }
                ui.label(
                    egui::RichText::new(&self.discord_status)
                        .size(9.0)
                        .color(muted(ui)),
                );
            },
        );
    }

    /// Python `_settings_inline_cloudflare`.
    fn cloudflare_card(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        let orange = rgb(0xf3, 0x80, 0x20);
        boxed(
            ui,
            "CF",
            orange,
            "Cloudflare / FlareSolverr",
            "Challenge-solving behavior for protected websites.",
            |ui| {
                for (l, h, r, rh) in [
                    (
                        "Mode",
                        "Auto selects a solver only when challenged.",
                        "Auto priority",
                        "Solver attempted first in Auto mode.",
                    ),
                    (
                        "Session",
                        "Controls challenge-cookie reuse.",
                        "Proxy",
                        "Inherit uses the proxy from the main form.",
                    ),
                ] {
                    ui.columns(2, |cols| {
                        field_label(&mut cols[0], l, h);
                        field_label(&mut cols[1], r, rh);
                        let (w0, w1) = (cols[0].available_width(), cols[1].available_width());
                        if l == "Mode" {
                            combo_w(
                                &mut cols[0],
                                "cf-mode",
                                &mut self.cf_mode,
                                &["Off", "Auto", "cloudscraper", "FlareSolverr"],
                                w0,
                            );
                            combo_w(
                                &mut cols[1],
                                "cf-prio",
                                &mut self.cf_priority,
                                &["FlareSolverr first", "cloudscraper first"],
                                w1,
                            );
                        } else {
                            combo_w(
                                &mut cols[0],
                                "cf-session",
                                &mut self.cf_session,
                                &["temporary", "reuse-domain", "manual"],
                                w0,
                            );
                            combo_w(
                                &mut cols[1],
                                "cf-proxy",
                                &mut self.cf_proxy,
                                &["inherit", "none"],
                                w1,
                            );
                        }
                    });
                }
                field_label(
                    ui,
                    "FlareSolverr API URL",
                    "Address of the running FlareSolverr service.",
                );
                ui.add(
                    egui::TextEdit::singleline(&mut self.cf_url)
                        .desired_width(ui.available_width()),
                );
                ui.columns(2, |cols| {
                    field_label(
                        &mut cols[0],
                        "Timeout (s)",
                        "Maximum time allowed to solve a challenge.",
                    );
                    field_label(
                        &mut cols[1],
                        "Wait after solve (s)",
                        "Extra delay for the solved page to stabilize.",
                    );
                    let (w0, w1) = (cols[0].available_width(), cols[1].available_width());
                    cols[0].add(egui::TextEdit::singleline(&mut self.cf_timeout).desired_width(w0));
                    cols[1].add(egui::TextEdit::singleline(&mut self.cf_wait).desired_width(w1));
                });
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new(&self.cf_status)
                            .size(9.0)
                            .color(muted(ui)),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if accent_button(ui, "Save", orange) && self.apply_cf(path).is_some() {
                            self.cf_status = "Saved.".into();
                            self.status = "Cloudflare settings saved.".into();
                        }
                        if ui
                            .add_enabled(self.cf_job.is_none(), egui::Button::new("Test"))
                            .clicked()
                            && let Some(cfg) = self.apply_cf(path)
                        {
                            self.cf_status = "Testing FlareSolverr…".into();
                            self.spawn_cf(ctx, cfg, true);
                        }
                        if ui.button("Clear sessions").clicked() {
                            self.cf_status = "Clearing sessions…".into();
                            if let Some(cfg) = self.apply_cf(path) {
                                self.spawn_cf(ctx, cfg, false);
                            }
                        }
                    });
                });
            },
        );
    }

    /// Python `_apply_cf`: parse, normalize and persist.
    fn apply_cf(&mut self, path: &Path) -> Option<cyoa_net::cloudflare::CloudflareConfig> {
        let int = |t: &str, d: i64| -> Result<i64, String> {
            let t = t.trim();
            if t.is_empty() {
                return Ok(d);
            }
            t.parse::<i64>()
                .map_err(|_| format!("invalid literal for int() with base 10: '{t}'"))
        };
        let (timeout, wait) = match (int(&self.cf_timeout, 60), int(&self.cf_wait, 3)) {
            (Ok(t), Ok(w)) => (t.max(1), w.max(0)),
            (Err(e), _) | (_, Err(e)) => {
                self.cf_status = e;
                return None;
            }
        };
        let cfg = cyoa_net::cloudflare::CloudflareConfig::new(
            &self.cf_mode,
            &self.cf_priority,
            self.cf_url.trim(),
            &self.cf_session,
            timeout,
            wait,
            &self.cf_proxy,
        );
        let c = cfg.clone();
        if let Err(e) = update(path, |m| {
            m.insert("cloudflare_mode".into(), c.mode.clone().into());
            m.insert("cloudflare_priority".into(), c.priority.clone().into());
            m.insert("flaresolverr_url".into(), c.flaresolverr_url.clone().into());
            m.insert(
                "flaresolverr_session_policy".into(),
                c.session_policy.clone().into(),
            );
            m.insert("flaresolverr_timeout".into(), c.timeout_s.into());
            m.insert("flaresolverr_wait_after".into(), c.wait_after_s.into());
            m.insert(
                "flaresolverr_proxy_mode".into(),
                c.proxy_mode.clone().into(),
            );
        }) {
            self.cf_status = e;
            return None;
        }
        self.cf_mode_changed = Some(cfg.mode.clone());
        Some(cfg)
    }

    fn spawn_cf(
        &mut self,
        ctx: &egui::Context,
        cfg: cyoa_net::cloudflare::CloudflareConfig,
        test: bool,
    ) {
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("flaresolverr".into())
            .spawn(move || {
                let msg = (|| -> Result<String, String> {
                    let rt = tokio::runtime::Builder::new_current_thread()
                        .enable_all()
                        .build()
                        .map_err(|e| e.to_string())?;
                    let fs = cyoa_net::cloudflare::FlareSolverr::new(
                        std::sync::Arc::new(cfg),
                        None,
                        cyoa_net::proxy::ProxyMode::InheritEnv,
                        "",
                    )
                    .map_err(|e| e.to_string())?;
                    Ok(if test {
                        let (ok, message) = rt.block_on(fs.test_connection());
                        format!("{}{message}", if ok { "OK: " } else { "Error: " })
                    } else {
                        format!("Cleared {} session(s).", rt.block_on(fs.destroy_sessions()))
                    })
                })()
                .unwrap_or_else(|e| format!("Error: {e}"));
                let _ = tx.send(msg);
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.cf_job = Some(rx);
        }
    }

    fn test_discord(&mut self, ctx: &egui::Context) {
        let token = match self.discord.trim() {
            "" => cyoa_engine::discord::resolve_bot_token(None).unwrap_or_default(),
            t => t.to_owned(),
        };
        if token.is_empty() {
            self.discord_status = "Enter or save a token first.".into();
            return;
        }
        self.discord_status = "Testing…".into();
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("discord-test".into())
            .spawn(move || {
                let _ = tx.send(validate_discord_token(&token));
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.discord_job = Some(rx);
        }
    }

    fn gdl_index() -> Option<usize> {
        FEATURES.iter().position(|f| f.3 == "gallery_dl_mode")
    }

    fn features_gdl_on(&self) -> bool {
        Self::gdl_index()
            .and_then(|i| self.features.get(i).copied())
            .unwrap_or(false)
    }

    fn set_gdl_feature(&mut self, on: bool) {
        if let Some(slot) = Self::gdl_index().and_then(|i| self.features.get_mut(i)) {
            *slot = on;
        }
    }

    /// Theme editor: mode, accent (presets + picker + hex), per-palette color
    /// overrides for dark and light, UI scale, and reset. Changes apply live
    /// and are saved to settings.json (`theme_mode`, `theme_accent_color`,
    /// `theme_custom`, `ui_scale`).
    fn appearance_card(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        let accent_now = crate::theme::palette(ui).accent;
        let mut mode_pick = None;
        let mut accent_pick: Option<String> = None;
        let mut overrides_changed = false;
        let mut scale_changed = false;
        let mut reset = false;
        if self.accent_hex.is_empty() {
            self.accent_hex = crate::theme::accent_hex();
        }
        boxed(
            ui,
            "UI",
            accent_now,
            "Appearance",
            "Theme mode, accent color, custom palette and UI scale.",
            |ui| {
                ui.horizontal(|ui| {
                    ui.add_sized([60.0, 18.0], egui::Label::new("Theme"));
                    for mode in ["System", "Light", "Dark"] {
                        if ui.selectable_label(self.theme_mode == mode, mode).clicked()
                            && self.theme_mode != mode
                        {
                            mode_pick = Some(mode);
                        }
                    }
                });
                ui.horizontal(|ui| {
                    ui.add_sized([60.0, 18.0], egui::Label::new("Accent"));
                    for preset in crate::theme::ACCENT_PRESETS {
                        let Some(rgb) = crate::theme::parse_accent(preset) else {
                            continue;
                        };
                        let color =
                            egui::Color32::from_rgb((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8);
                        let selected = accent_now == color;
                        let (rect, resp) =
                            ui.allocate_exact_size(egui::vec2(18.0, 18.0), egui::Sense::click());
                        ui.painter()
                            .rect_filled(rect, egui::CornerRadius::same(9), color);
                        if selected {
                            ui.painter().rect_stroke(
                                rect.expand(2.0),
                                egui::CornerRadius::same(11),
                                egui::Stroke::new(1.5, ui.visuals().text_color()),
                                egui::StrokeKind::Outside,
                            );
                        }
                        if resp.on_hover_text(preset).clicked() && !selected {
                            accent_pick = Some(preset.to_owned());
                        }
                    }
                    ui.add_space(6.0);
                    let mut custom = accent_now;
                    if egui::color_picker::color_edit_button_srgba(
                        ui,
                        &mut custom,
                        egui::color_picker::Alpha::Opaque,
                    )
                    .on_hover_text("Custom accent color")
                    .changed()
                    {
                        accent_pick = Some(format!(
                            "#{:02x}{:02x}{:02x}",
                            custom.r(),
                            custom.g(),
                            custom.b()
                        ));
                    }
                    let resp = ui.add(
                        egui::TextEdit::singleline(&mut self.accent_hex)
                            .hint_text("#3b82f6")
                            .desired_width(70.0),
                    );
                    if resp.lost_focus() {
                        if crate::theme::parse_accent(&self.accent_hex).is_some() {
                            accent_pick = Some(self.accent_hex.trim().to_lowercase());
                        } else {
                            self.accent_hex = crate::theme::accent_hex();
                        }
                    }
                });
                ui.horizontal(|ui| {
                    ui.add_sized([60.0, 18.0], egui::Label::new("UI scale"));
                    let mut scale = crate::theme::ui_scale();
                    let resp = ui.add(
                        egui::Slider::new(&mut scale, crate::theme::SCALE_RANGE)
                            .step_by(0.05)
                            .custom_formatter(|v, _| format!("{:.0}%", v * 100.0))
                            .custom_parser(|text| {
                                let v: f64 =
                                    text.trim().trim_end_matches('%').trim().parse().ok()?;
                                // The field shows percent; accept a factor too.
                                Some(if v > 3.0 { v / 100.0 } else { v })
                            }),
                    );
                    // Apply when the drag ends so the window doesn't resize under
                    // the pointer.
                    if resp.drag_stopped() || (resp.changed() && !resp.dragged()) {
                        crate::theme::set_ui_scale(scale);
                        scale_changed = true;
                    }
                });
                egui::CollapsingHeader::new("Advanced colors")
                    .id_salt("theme-advanced")
                    .show(ui, |ui| {
                        ui.label(
                            egui::RichText::new(
                                "Override any palette color for dark and light mode. ↺ resets one color.",
                            )
                            .size(9.0)
                            .color(muted(ui)),
                        );
                        egui::Grid::new("theme-grid")
                            .num_columns(3)
                            .spacing([14.0, 6.0])
                            .show(ui, |ui| {
                                ui.label("");
                                ui.label(egui::RichText::new("Dark").strong());
                                ui.label(egui::RichText::new("Light").strong());
                                ui.end_row();
                                for (key, label) in crate::theme::EDITABLE {
                                    ui.label(label);
                                    for dark in [true, false] {
                                        ui.horizontal(|ui| {
                                            let (mut color, overridden) =
                                                crate::theme::editable_color(dark, key);
                                            if egui::color_picker::color_edit_button_srgba(
                                                ui,
                                                &mut color,
                                                egui::color_picker::Alpha::Opaque,
                                            )
                                            .changed()
                                            {
                                                crate::theme::set_override(dark, key, Some(color));
                                                overrides_changed = true;
                                            }
                                            if overridden
                                                && ui
                                                    .small_button("↺")
                                                    .on_hover_text("Reset to default")
                                                    .clicked()
                                            {
                                                crate::theme::set_override(dark, key, None);
                                                overrides_changed = true;
                                            }
                                        });
                                    }
                                    ui.end_row();
                                }
                            });
                        ui.add_space(4.0);
                        if ui
                            .button("Reset theme to defaults")
                            .on_hover_text("Default accent, colors and 100% scale")
                            .clicked()
                        {
                            reset = true;
                        }
                    });
            },
        );
        let mut save: Vec<(&'static str, Value)> = Vec::new();
        if let Some(mode) = mode_pick {
            self.theme_mode = mode.to_owned();
            crate::theme::apply_theme_mode(ctx, mode);
            save.push(("theme_mode", mode.into()));
        }
        if let Some(hex) = accent_pick
            && crate::theme::apply_accent(ctx, &hex)
        {
            self.accent_hex = hex.clone();
            save.push(("theme_accent_color", hex.into()));
        }
        if reset {
            crate::theme::reset_overrides();
            self.accent_hex = crate::theme::accent_hex();
            save.push(("theme_accent_color", self.accent_hex.clone().into()));
            overrides_changed = true;
            scale_changed = true;
        }
        if overrides_changed || scale_changed {
            crate::theme::reapply(ctx);
            save.push(("theme_custom", crate::theme::overrides_json()));
            save.push(("ui_scale", f64::from(crate::theme::ui_scale()).into()));
        }
        if !save.is_empty()
            && let Err(e) = update(path, |m| {
                for (k, v) in save {
                    m.insert(k.into(), v);
                }
            })
        {
            self.status = e;
        }
    }

    // ── Maintenance page ───────────────────────────────────────────────

    /// CLI `--verify` / `--write-manifest` for an output folder.
    fn verify_card(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        let teal = rgb(0x0f, 0x76, 0x6e);
        let busy = self.verify_job.is_some();
        let mut run: Option<bool> = None;
        boxed(
            ui,
            "CHK",
            teal,
            "Verify output",
            "Check a downloaded folder against its package manifest, or write a new manifest.",
            |ui| {
                ui.horizontal(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.verify_folder)
                            .hint_text("Output folder")
                            .desired_width(260.0),
                    );
                    if ui
                        .add_enabled(!busy, egui::Button::new("Browse…"))
                        .clicked()
                        && let Some(dir) = rfd::FileDialog::new().pick_folder()
                    {
                        self.verify_folder = dir.display().to_string();
                    }
                    if accent_button(ui, "Verify", teal) && !busy {
                        run = Some(false);
                    }
                    if ui
                        .add_enabled(!busy, egui::Button::new("Write manifest"))
                        .clicked()
                    {
                        run = Some(true);
                    }
                });
                if !self.verify_report.is_empty() {
                    egui::ScrollArea::vertical()
                        .id_salt("verify-report")
                        .max_height(140.0)
                        .show(ui, |ui| {
                            ui.label(
                                egui::RichText::new(&self.verify_report)
                                    .monospace()
                                    .size(9.5),
                            );
                        });
                }
            },
        );
        if let Some(write) = run {
            let folder = PathBuf::from(self.verify_folder.trim());
            if !folder.is_dir() {
                self.verify_report = "Choose an existing output folder first.".into();
                return;
            }
            self.verify_report = if write {
                "Writing manifest…"
            } else {
                "Verifying…"
            }
            .into();
            let (tx, rx) = std::sync::mpsc::channel();
            let ctx = ctx.clone();
            let spawned = std::thread::Builder::new()
                .name("verify-output".into())
                .spawn(move || {
                    let (ok, text) = if write {
                        cyoa_engine::verify::write_package_manifest(&folder)
                    } else {
                        cyoa_engine::verify::verify_output_package(&folder)
                    };
                    let head = if ok { "OK" } else { "PROBLEMS FOUND" };
                    let _ = tx.send(format!("{head}\n{text}"));
                    ctx.request_repaint();
                });
            if spawned.is_ok() {
                self.verify_job = Some(rx);
            }
        }
    }

    /// Modernize CYOAs with current offline viewers: a whole library or one
    /// CYOA folder, templates from a viewer collection or the registered
    /// offline viewers, optional analyze-only report, live progress, cancel.
    fn modernize_card(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        let violet = rgb(0x6d, 0x28, 0xd9);
        let busy = self.modern_job.is_some();
        let mut start = false;
        boxed(
            ui,
            "MOD",
            violet,
            "Modernize CYOAs",
            "Copy CYOAs into a new folder with modern offline viewers. Originals are never changed.",
            |ui| {
                ui.add_enabled_ui(!busy, |ui| {
                    ui.horizontal(|ui| {
                        ui.add_sized([110.0, 18.0], egui::Label::new("Target"));
                        ui.selectable_value(
                            &mut self.modern_target,
                            0,
                            "Library (folder of CYOAs)",
                        );
                        ui.selectable_value(&mut self.modern_target, 1, "Single CYOA folder");
                    });
                    ui.horizontal(|ui| {
                        ui.add_sized([110.0, 18.0], egui::Label::new("Viewers from"));
                        ui.selectable_value(
                            &mut self.modern_source,
                            1,
                            "Registered offline viewers",
                        );
                        ui.selectable_value(&mut self.modern_source, 0, "Viewer collection folder");
                    });
                    let source_label = if self.modern_target == 0 {
                        "Library folder"
                    } else {
                        "CYOA folder"
                    };
                    let mut rows: Vec<(usize, &str, &str)> =
                        vec![(0, source_label, "Folder to modernize")];
                    if self.modern_source == 0 {
                        rows.push((
                            1,
                            "Viewer collection",
                            "Folder with viewer template ZIP/RAR files",
                        ));
                    }
                    if !self.modern_analyze {
                        rows.push((
                            2,
                            "Output folder",
                            "Empty or new folder (default: <source>_edited)",
                        ));
                    }
                    for (i, label, hint) in rows {
                        ui.horizontal(|ui| {
                            ui.add_sized([110.0, 18.0], egui::Label::new(label));
                            ui.add(
                                egui::TextEdit::singleline(&mut self.modern_paths[i])
                                    .hint_text(hint)
                                    .desired_width(260.0),
                            );
                            if ui.button("…").clicked()
                                && let Some(dir) = rfd::FileDialog::new().pick_folder()
                            {
                                self.modern_paths[i] = dir.display().to_string();
                            }
                        });
                    }
                    ui.checkbox(
                        &mut self.modern_analyze,
                        "Analyze only (report viewer types, change nothing)",
                    );
                    ui.checkbox(
                        &mut self.modern_network,
                        "Fetch missing viewer assets from the internet",
                    )
                    .on_hover_text("Off: only files already present are used (fully offline run).");
                });
                ui.horizontal(|ui| {
                    if busy {
                        if ui.button("Cancel").clicked() {
                            self.modern_cancel
                                .store(true, std::sync::atomic::Ordering::Relaxed);
                        }
                        ui.spinner();
                        let progress = self
                            .modern_progress
                            .lock()
                            .map(|p| p.clone())
                            .unwrap_or_default();
                        ui.label(egui::RichText::new(progress).size(9.5));
                    } else {
                        let label = if self.modern_analyze {
                            "Analyze"
                        } else {
                            "Modernize"
                        };
                        if accent_button(ui, label, violet) {
                            start = true;
                        }
                        if let Some(out) = self.modern_last_output.clone()
                            && out.is_dir()
                            && ui.button("Open output").clicked()
                        {
                            let _ = open_with_os(&out);
                        }
                    }
                });
                if !self.modern_status.is_empty() {
                    egui::ScrollArea::vertical()
                        .id_salt("modernize-report")
                        .max_height(160.0)
                        .show(ui, |ui| {
                            ui.label(
                                egui::RichText::new(&self.modern_status)
                                    .monospace()
                                    .size(9.5),
                            );
                        });
                }
            },
        );
        if busy {
            ctx.request_repaint_after(std::time::Duration::from_millis(250));
        }
        if !start {
            return;
        }
        let source = PathBuf::from(self.modern_paths[0].trim());
        if !source.is_dir() {
            self.modern_status = "Choose an existing folder to modernize.".into();
            return;
        }
        let collection = PathBuf::from(self.modern_paths[1].trim());
        if self.modern_source == 0 && !self.modern_analyze && !collection.is_dir() {
            self.modern_status = "Viewer collection folder not found.".into();
            return;
        }
        let output = match self.modern_paths[2].trim() {
            "" => {
                let name = source
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| "cyoa".into());
                source.with_file_name(format!("{name}_edited"))
            }
            o => PathBuf::from(o),
        };
        if !self.modern_analyze
            && output.exists()
            && std::fs::read_dir(&output).map_or(true, |mut d| d.next().is_some())
        {
            self.modern_status = "Output folder must be empty or absent.".into();
            return;
        }
        let (target, from_registered, analyze, network) = (
            self.modern_target,
            self.modern_source == 1,
            self.modern_analyze,
            self.modern_network,
        );
        self.modern_cancel
            .store(false, std::sync::atomic::Ordering::Relaxed);
        if let Ok(mut p) = self.modern_progress.lock() {
            p.clear();
        }
        self.modern_last_output = (!analyze).then(|| output.clone());
        self.modern_status = if analyze {
            "Analyzing…"
        } else {
            "Modernizing…"
        }
        .into();
        let progress = std::sync::Arc::clone(&self.modern_progress);
        let cancel = std::sync::Arc::clone(&self.modern_cancel);
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("modernize".into())
            .spawn(move || {
                let msg = run_modernize(
                    &source,
                    &collection,
                    &output,
                    target,
                    from_registered,
                    analyze,
                    network,
                    &progress,
                    &cancel,
                );
                let _ = tx.send(msg);
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.modern_job = Some(rx);
        }
    }

    pub fn maintenance_cards(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        self.poll();
        let blue = rgb(0x25, 0x63, 0xeb);
        let green = rgb(0x06, 0x5f, 0x46);
        let deep = rgb(0x1d, 0x4e, 0xd8);
        let mut clicks = [false; 4];
        ui.columns(2, |cols| {
            clicks[0] = tool(
                &mut cols[0],
                "CFG",
                blue,
                "Settings file",
                "Edit the active settings.json.",
                "Edit JSON",
            );
            clicks[1] = tool(
                &mut cols[1],
                "DIR",
                blue,
                "Settings folder",
                "Show the local settings/history folder.",
                "Show",
            );
        });
        ui.columns(2, |cols| {
            clicks[2] = tool(
                &mut cols[0],
                "OUT",
                green,
                "Export settings",
                "Create a portable secret-safe export.",
                "Export…",
            );
            clicks[3] = tool(
                &mut cols[1],
                "IN",
                deep,
                "Import settings",
                "Merge a previous portable export.",
                "Import…",
            );
        });
        self.verify_card(ui, ctx);
        self.modernize_card(ui, ctx);
        ui.columns(2, |cols| {
            if tool(
                &mut cols[0],
                "UPD",
                rgb(0x0e, 0x74, 0x90),
                "Updates",
                "Check the app and downloaded projects for updates.",
                "Open",
            ) {
                self.open_updates = true;
            }
        });
        if clicks[0] {
            self.status = open_settings_file(path)
                .err()
                .map_or_else(String::new, |e| format!("Open settings.json: {e}"));
        }
        if clicks[1] {
            let dir = path.parent().map(Path::to_path_buf).unwrap_or_default();
            let _ = std::fs::create_dir_all(&dir);
            self.status = open_with_os(&dir)
                .err()
                .map_or_else(String::new, |e| format!("Open settings folder: {e}"));
        }
        if clicks[2] {
            self.start_file_job(path, true, ctx);
        }
        if clicks[3] {
            self.start_file_job(path, false, ctx);
        }
        boxed(
            ui,
            "IMG",
            rgb(0x7c, 0x3a, 0xed),
            "Image cache",
            &self.cache_stats.clone(),
            |ui| {
                let p = crate::theme::palette(ui);
                ui.label(
                    egui::RichText::new(
                        "Speeds up repeated downloads; clearing it does not remove completed output.",
                    )
                    .size(9.0)
                    .color(p.muted),
                );
                ui.horizontal(|ui| {
                    ui.label("Maximum size (GB)");
                    ui.add(egui::TextEdit::singleline(&mut self.cache_limit).desired_width(80.0));
                    if accent_button(ui, "Save limit", blue) {
                        self.save_cache_limit(path, ctx);
                    }
                    if ui.button("Show folder").clicked()
                        && let Some(root) = cyoa_storage::cache::ImageCache::default_root()
                    {
                        let _ = std::fs::create_dir_all(&root);
                        let _ = open_with_os(&root);
                    }
                    if ui
                        .add(egui::Button::new(
                            egui::RichText::new("Clear cache").color(p.danger_fg),
                        ))
                        .clicked()
                    {
                        self.confirm_clear = true;
                    }
                });
                if self.confirm_clear {
                    ui.horizontal(|ui| {
                        ui.colored_label(
                            rgb(0xf5, 0x9e, 0x0b),
                            "Remove cached images? Completed downloads are not touched.",
                        );
                        if ui.button("Yes").clicked() {
                            self.confirm_clear = false;
                            self.clear_cache(ctx);
                        }
                        if ui.button("No").clicked() {
                            self.confirm_clear = false;
                        }
                    });
                }
                ui.label(
                    egui::RichText::new(&self.cache_status)
                        .size(9.0)
                        .color(p.muted),
                );
            },
        );
    }

    fn save_cache_limit(&mut self, path: &Path, ctx: &egui::Context) {
        let Some(mb) = cache_limit_mb(&self.cache_limit) else {
            self.cache_status = "Enter a positive GB value.".into();
            return;
        };
        match update(path, |m| {
            m.insert("image_cache_max_mb".into(), mb.into());
        }) {
            Ok(()) => {
                self.start_cache_job(ctx, "Saving limit and trimming cache…", move || {
                    let root = cyoa_storage::cache::ImageCache::default_root()
                        .ok_or_else(|| "Image cache unavailable".to_owned())?;
                    let cache = cyoa_storage::cache::ImageCache::open(&root, mb)
                        .map_err(|error| error.to_string())?;
                    cache.enforce_limit().map_err(|error| error.to_string())?;
                    Ok((
                        format!("Limit saved: {} GB", py_g(mb as f64 / 1024.0)),
                        cache.stats(),
                    ))
                });
            }
            Err(e) => self.cache_status = e,
        }
    }

    fn clear_cache(&mut self, ctx: &egui::Context) {
        self.start_cache_job(ctx, "Clearing image cache…", move || {
            let root = cyoa_storage::cache::ImageCache::default_root()
                .ok_or_else(|| "Image cache unavailable".to_owned())?;
            let limit =
                cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path())
                    .load()
                    .ok()
                    .and_then(|settings| settings.get("image_cache_max_mb").and_then(Value::as_u64))
                    .unwrap_or(2048);
            let cache = cyoa_storage::cache::ImageCache::open(&root, limit)
                .map_err(|error| error.to_string())?;
            let removed = cache.clear().map_err(|error| error.to_string())?;
            Ok((format!("Cleared {removed} file(s)."), cache.stats()))
        });
    }

    /// Export/Import dialogs on a helper thread (Python messageboxes become
    /// the status line).
    fn start_file_job(&mut self, path: &Path, export: bool, ctx: &egui::Context) {
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let settings_path = path.to_path_buf();
        let spawned = std::thread::Builder::new()
            .name("settings-file".into())
            .spawn(move || {
                let mut store = cyoa_storage::settings::SettingsStore::new(&settings_path);
                let msg = if export {
                    rfd::FileDialog::new()
                        .set_title("Export Settings")
                        .set_file_name("cyoa_settings_export.json")
                        .add_filter("JSON", &["json"])
                        .save_file()
                        .map(|p| cyoa_storage::settings::export_settings_file(&mut store, &p).1)
                } else {
                    rfd::FileDialog::new()
                        .set_title("Import Settings")
                        .add_filter("JSON", &["json"])
                        .add_filter("All files", &["*"])
                        .pick_file()
                        .map(|p| {
                            let (ok, msg) =
                                cyoa_storage::settings::import_settings_file(&mut store, &p);
                            if ok {
                                format!(
                                    "{msg} Some changes may require reopening panels or restarting the app to take full effect."
                                )
                            } else {
                                msg
                            }
                        })
                };
                if let Some(msg) = msg {
                    let _ = tx.send(msg);
                }
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.file_job = Some(rx);
        }
    }
}

const FEATURE_COLORS: &[egui::Color32] = &[
    egui::Color32::from_rgb(0x3b, 0x82, 0xf6),
    egui::Color32::from_rgb(0x8b, 0x5c, 0xf6),
    egui::Color32::from_rgb(0x10, 0xb9, 0x81),
    egui::Color32::from_rgb(0xf5, 0x9e, 0x0b),
    egui::Color32::from_rgb(0x14, 0xb8, 0xa6),
    egui::Color32::from_rgb(0xef, 0x44, 0x44),
    egui::Color32::from_rgb(0xec, 0x48, 0x99),
];

/// Settings-local secondary text (Python overrides `muted` in Settings).
pub(crate) fn muted(ui: &egui::Ui) -> egui::Color32 {
    if ui.visuals().dark_mode {
        rgb(0x94, 0xa3, 0xb8)
    } else {
        rgb(0x64, 0x74, 0x8b)
    }
}

pub(crate) fn muted2(ui: &egui::Ui) -> egui::Color32 {
    if ui.visuals().dark_mode {
        rgb(0x64, 0x74, 0x8b)
    } else {
        rgb(0x94, 0xa3, 0xb8)
    }
}

pub(crate) fn rgb(r: u8, g: u8, b: u8) -> egui::Color32 {
    egui::Color32::from_rgb(r, g, b)
}

/// Python card frame: surface fill, 1 px border, 10 px corners.
pub(crate) fn frame(ui: &egui::Ui, border: egui::Color32) -> egui::Frame {
    let p = crate::theme::palette(ui);
    egui::Frame::new()
        .fill(p.surface)
        .stroke(egui::Stroke::new(1.0, border))
        .corner_radius(10.0)
        .inner_margin(egui::Margin::same(10))
        .outer_margin(egui::Margin::symmetric(0, 4))
}

/// Python page `_title(page, row, title, desc)`.
pub(crate) fn title(ui: &mut egui::Ui, text: &str, desc: &str) {
    let p = crate::theme::palette(ui);
    ui.add_space(10.0);
    ui.label(
        egui::RichText::new(text)
            .family(egui::FontFamily::Name("bold".into()))
            .size(14.0)
            .color(p.fg),
    );
    ui.label(egui::RichText::new(desc).size(9.0).color(muted(ui)));
    ui.add_space(2.0);
}

/// Python coloured badge (`text="YT"`, white bold 9 pt on the accent).
pub(crate) fn badge_label(ui: &mut egui::Ui, text: &str, color: egui::Color32) {
    egui::Frame::new()
        .fill(color)
        .corner_radius(8.0)
        .inner_margin(egui::Margin::symmetric(6, 6))
        .show(ui, |ui| {
            ui.set_min_width(22.0);
            ui.label(
                egui::RichText::new(text)
                    .family(egui::FontFamily::Name("bold".into()))
                    .size(9.0)
                    .color(egui::Color32::WHITE),
            );
        });
}

/// A Python card: badge, bold title, muted description, then contents.
pub(crate) fn boxed(
    ui: &mut egui::Ui,
    badge: &str,
    color: egui::Color32,
    name: &str,
    desc: &str,
    add: impl FnOnce(&mut egui::Ui),
) {
    let p = crate::theme::palette(ui);
    frame(ui, p.border).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.horizontal_top(|ui| {
            badge_label(ui, badge, color);
            ui.vertical(|ui| {
                ui.label(
                    egui::RichText::new(name)
                        .family(egui::FontFamily::Name("bold".into()))
                        .size(11.0)
                        .color(p.fg),
                );
                if !desc.is_empty() {
                    ui.label(egui::RichText::new(desc).size(9.0).color(muted(ui)));
                }
                ui.add_space(3.0);
                add(ui);
            });
        });
    });
}

pub(crate) fn group_label(ui: &mut egui::Ui, text: &str) {
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new(text.to_uppercase())
            .family(egui::FontFamily::Name("bold".into()))
            .size(9.0)
            .color(rgb(0x22, 0xd3, 0xee)),
    );
}

pub(crate) fn field_label(ui: &mut egui::Ui, label: &str, hint: &str) {
    let p = crate::theme::palette(ui);
    ui.vertical(|ui| {
        ui.label(egui::RichText::new(label).size(9.0).color(p.muted));
        ui.label(egui::RichText::new(hint).size(8.0).color(p.muted2));
    });
}

/// A filled accent button like the Python CTkButton colours.
pub(crate) fn accent_button(ui: &mut egui::Ui, text: &str, color: egui::Color32) -> bool {
    ui.add(
        egui::Button::new(
            egui::RichText::new(text)
                .size(13.0)
                .color(egui::Color32::WHITE),
        )
        .fill(color)
        .corner_radius(6.0)
        .min_size(egui::vec2(82.0, 30.0)),
    )
    .clicked()
}

/// A CTkSwitch-like toggle; returns true when flipped.
pub(crate) fn toggle(ui: &mut egui::Ui, on: &mut bool, color: egui::Color32) -> bool {
    let size = egui::vec2(40.0, 20.0);
    let (rect, resp) = ui.allocate_exact_size(size, egui::Sense::click());
    if resp.clicked() {
        *on = !*on;
    }
    let t = ui.ctx().animate_bool(resp.id, *on);
    let p = crate::theme::palette(ui);
    let bg = if *on { color } else { p.surface2 };
    let painter = ui.painter();
    painter.rect_filled(rect, 10.0, bg);
    let x = egui::lerp((rect.left() + 10.0)..=(rect.right() - 10.0), t);
    painter.circle_filled(egui::pos2(x, rect.center().y), 7.0, rgb(0xe5, 0xe7, 0xeb));
    resp.clicked()
}

fn tool(
    ui: &mut egui::Ui,
    badge: &str,
    color: egui::Color32,
    name: &str,
    desc: &str,
    button: &str,
) -> bool {
    let mut clicked = false;
    let p = crate::theme::palette(ui);
    frame(ui, p.border).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.horizontal(|ui| {
            badge_label(ui, badge, color);
            ui.vertical(|ui| {
                ui.label(
                    egui::RichText::new(name)
                        .family(egui::FontFamily::Name("bold".into()))
                        .size(11.0)
                        .color(p.fg),
                );
                ui.label(egui::RichText::new(desc).size(9.0).color(muted(ui)));
            });
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                clicked = accent_button(ui, button, color);
            });
        });
    });
    clicked
}

pub(crate) fn combo(ui: &mut egui::Ui, id: &str, value: &mut String, options: &[&str]) {
    egui::ComboBox::from_id_salt(id)
        .icon(crate::theme::combo_icon)
        .selected_text(value.as_str())
        .show_ui(ui, |ui| {
            for o in options {
                ui.selectable_value(value, (*o).to_owned(), *o);
            }
        });
}

/// Python `_open_settings_json`: create defaults if missing, then edit.
fn open_settings_file(path: &Path) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if !path.is_file() {
        let mut store = cyoa_storage::settings::SettingsStore::new(path);
        let _ = store.load();
        store
            .save()
            .map_err(|e| std::io::Error::other(e.to_string()))?;
    }
    edit_text_file(path)
}

/// Python `_open_gallery_dl_config`.
fn open_gallery_dl_config(path: &Path) -> std::io::Result<()> {
    let st = load(path);
    let mut cfg = st
        .get("gallery_dl_config")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_owned();
    if cfg.is_empty() {
        cfg = gallery_dl_default_config().display().to_string();
        let value = cfg.clone();
        update(path, |m| {
            m.insert("gallery_dl_config".into(), value.into());
        })
        .map_err(std::io::Error::other)?;
    }
    let cfg = std::path::absolute(PathBuf::from(&cfg))?;
    if let Some(dir) = cfg.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if !cfg.exists() {
        std::fs::write(&cfg, "{\n  \"extractor\": {},\n  \"downloader\": {}\n}\n")?;
    }
    edit_text_file(&cfg)
}

/// Python `DiscordAttachmentClient(token).validate_token()` (`/users/@me`).
fn validate_discord_token(token: &str) -> String {
    let run = || -> Result<String, String> {
        let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig::default())
            .map_err(|e| e.to_string())?;
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| e.to_string())?;
        let url = url::Url::parse(&format!(
            "{}/v10/users/@me",
            cyoa_engine::discord::DEFAULT_API_ROOT
        ))
        .map_err(|e| e.to_string())?;
        let mut headers = cyoa_net::client::header::HeaderMap::new();
        let mut auth = cyoa_net::client::header::HeaderValue::from_str(
            &cyoa_engine::discord::auth_header(token),
        )
        .map_err(|e| e.to_string())?;
        auth.set_sensitive(true);
        headers.insert(cyoa_net::client::header::AUTHORIZATION, auth);
        let opts = cyoa_net::client::FetchOptions {
            headers,
            return_error_response: true,
            ..Default::default()
        };
        let cancel = tokio_util::sync::CancellationToken::new();
        rt.block_on(async {
            let resp = client
                .get(&url, &opts, &cancel)
                .await
                .map_err(|e| e.to_string())?;
            let status = resp.status.as_u16();
            let text = cyoa_net::client::FetchClient::text(resp, 1 << 20)
                .await
                .map_err(|e| e.to_string())?;
            if status != 200 {
                return Err(format!("HTTP {status}"));
            }
            let v: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            let name = v
                .get("username")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .or_else(|| v.get("global_name").and_then(Value::as_str))
                .unwrap_or("bot")
                .to_owned();
            Ok(name)
        })
    };
    match run() {
        Ok(name) => format!("OK: {name}"),
        Err(e) => format!("Error: {e}"),
    }
}

/// A CTkOptionMenu-like combo of a fixed width.
pub(crate) fn combo_w(
    ui: &mut egui::Ui,
    id: &str,
    value: &mut String,
    options: &[&str],
    width: f32,
) {
    egui::ComboBox::from_id_salt(id)
        .icon(crate::theme::combo_icon)
        .selected_text(value.as_str())
        .width(width)
        .show_ui(ui, |ui| {
            for o in options {
                ui.selectable_value(value, (*o).to_owned(), *o);
            }
        });
}

/// A CTkSegmentedButton (options are laid out right to left); returns the
/// newly selected value.
pub(crate) fn segmented(
    ui: &mut egui::Ui,
    options: &[(&str, &'static str)],
    current: &str,
) -> Option<&'static str> {
    let p = crate::theme::palette(ui);
    let mut picked = None;
    egui::Frame::new()
        .fill(p.surface2)
        .corner_radius(8.0)
        .inner_margin(egui::Margin::same(3))
        .show(ui, |ui| {
            ui.spacing_mut().item_spacing.x = 2.0;
            for (label, value) in options {
                let on = current == *value;
                let button = egui::Button::new(
                    egui::RichText::new(*label)
                        .size(12.0)
                        .color(egui::Color32::WHITE),
                )
                .fill(if on {
                    rgb(0x3b, 0x82, 0xf6)
                } else {
                    p.surface2
                })
                .corner_radius(6.0)
                .min_size(egui::vec2(78.0, 28.0));
                if ui.add(button).clicked() && !on {
                    picked = Some(*value);
                }
            }
        });
    picked
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_g_formatting() {
        assert_eq!(py_g(2.0), "2");
        assert_eq!(py_g(1.5), "1.5");
        assert_eq!(py_g(100.0 / 1024.0), "0.0976562");
        assert_eq!(py_g(1024.0), "1024");
        assert_eq!(py_g(1.0 / 1024.0), "0.000976562");
    }

    #[test]
    fn cache_limit_parsing_matches_python() {
        assert_eq!(cache_limit_mb("2"), Some(2048));
        assert_eq!(cache_limit_mb(" 0.0001 "), Some(1));
        assert_eq!(cache_limit_mb("5000"), Some(1024 * 1024));
        assert_eq!(cache_limit_mb("0"), None);
        assert_eq!(cache_limit_mb("abc"), None);
        assert_eq!(cache_limit_mb("inf"), None);
    }

    #[test]
    fn search_follows_python_keywords() {
        assert_eq!(search_page("proxy"), Some(1));
        assert_eq!(search_page("DISCORD"), Some(2));
        assert_eq!(search_page("cache"), Some(4));
        assert_eq!(search_page("viewers"), Some(3));
        assert_eq!(search_page("deep scan"), Some(0));
        assert_eq!(search_page(""), None);
        assert_eq!(search_page("zzz"), None);
    }
}

#[cfg(test)]
mod feature_color_tests {
    use super::*;

    #[test]
    fn every_feature_switch_has_its_own_color() {
        assert!(FEATURE_COLORS.len() >= FEATURES.len());
    }
}

/// Background body of the Modernize card; returns the report text.
#[allow(clippy::too_many_arguments)]
fn run_modernize(
    source: &Path,
    collection: &Path,
    output: &Path,
    target: usize,
    from_registered: bool,
    analyze: bool,
    network: bool,
    progress: &std::sync::Mutex<String>,
    cancel: &std::sync::atomic::AtomicBool,
) -> String {
    use cyoa_engine::modernizer as m;
    let set_progress = |text: String| {
        if let Ok(mut p) = progress.lock() {
            *p = text;
        }
    };
    // Sites to look at: the folder itself (single) or its subfolders (library).
    let items: Vec<PathBuf> = if target == 1 {
        vec![source.to_path_buf()]
    } else {
        let mut v: Vec<PathBuf> = std::fs::read_dir(source)
            .map(|d| {
                d.flatten()
                    .map(|e| e.path())
                    .filter(|p| p.is_dir())
                    .collect()
            })
            .unwrap_or_default();
        v.sort();
        v
    };
    if analyze {
        let mut lines = vec![format!("{} item(s) analyzed:", items.len())];
        for (i, item) in items.iter().enumerate() {
            if cancel.load(std::sync::atomic::Ordering::Relaxed) {
                lines.push("Cancelled.".into());
                break;
            }
            let name = item
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            set_progress(format!("[{}/{}] {name}", i + 1, items.len()));
            let p = m::analyze_site(item);
            lines.push(format!(
                "{name}: {} · {} · {}% confident",
                p.family.value(),
                p.strategy,
                p.confidence
            ));
        }
        return lines.join("\n");
    }
    let templates = if from_registered {
        m::resolve_registered_viewer_templates(&cyoa_integrations::offline_viewers::viewers_dir())
    } else {
        m::resolve_viewer_templates(collection)
    };
    if templates.is_empty() {
        return if from_registered {
            "No registered offline viewers can be used as templates. Add viewers under Offline viewers, or pick a viewer collection folder.".into()
        } else {
            "No viewer templates found in the collection folder.".into()
        };
    }
    // The network fetcher needs a Tokio runtime on this thread.
    let runtime = network
        .then(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .ok()
        })
        .flatten();
    let _enter = runtime.as_ref().map(tokio::runtime::Runtime::enter);
    let net = runtime.as_ref().and_then(|_| {
        cyoa_engine::offline_viewer::NetworkAssetFetcher::new(
            "",
            cyoa_net::proxy::ProxyConfig::default(),
            None,
            None,
            false,
            tokio_util::sync::CancellationToken::new(),
        )
    });
    let no_net = cyoa_engine::offline_viewer::NoNetwork;
    let fetcher: &dyn cyoa_engine::offline_viewer::AssetFetcher = match &net {
        Some(f) => f,
        None => &no_net,
    };
    if target == 1 {
        set_progress(format!("[1/1] {}", source.display()));
        return match m::modernize_site(source, output, None, &templates, "", fetcher) {
            Ok(r) => format!(
                "{}: {} ({}) — {} file(s) changed. {}\nOutput: {}",
                r.status,
                r.family.value(),
                r.strategy,
                r.changed_files.len(),
                r.message,
                r.destination.display()
            ),
            Err(e) => format!("Could not modernize: {e}"),
        };
    }
    let mut on_progress = |pos: usize, total: usize, name: &str| {
        set_progress(format!("[{pos}/{total}] {name}"));
    };
    match m::modernize_collection_with_templates(
        source,
        output,
        &templates,
        &mut on_progress,
        fetcher,
        &|| cancel.load(std::sync::atomic::Ordering::Relaxed),
    ) {
        Ok(r) => {
            let mut lines = vec![format!(
                "Done: {} modernized, {} copied unchanged, {} failed.{}",
                r.modernized,
                r.copied,
                r.failed,
                if cancel.load(std::sync::atomic::Ordering::Relaxed) {
                    " (cancelled)"
                } else {
                    ""
                }
            )];
            for site in r.site_results.iter().filter(|s| s.status == "failed") {
                lines.push(format!("✗ {}: {}", site.source.display(), site.message));
            }
            lines.push(format!(
                "Report: {}",
                output.join("conversion_report.json").display()
            ));
            lines.join("\n")
        }
        Err(e) => format!("Could not modernize library: {e}"),
    }
}
