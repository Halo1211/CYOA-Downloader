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
        "maintenance settings file folder export import cache tools pemeliharaan",
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
        "ICE helper for localhost preview.",
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

fn open_with_os(target: &Path) -> std::io::Result<()> {
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

/// Dashboard state kept while the Settings window is open.
pub struct SettingsDashboard {
    pub page: usize,
    pub search: String,
    pub status: String,
    loaded: bool,
    cookie: String,
    itch_key: String,
    strategy: String,
    interaction: String,
    limits: Vec<String>,
    archive_status: String,
    features: Vec<bool>,
    discord: String,
    discord_status: String,
    discord_job: Option<Job>,
    cache_limit: String,
    cache_stats: String,
    cache_status: String,
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
            strategy: "auto".into(),
            interaction: "safe".into(),
            limits: ARCHIVE_DEFAULTS.iter().map(i64::to_string).collect(),
            archive_status: String::new(),
            features: FEATURES.iter().map(|f| f.4).collect(),
            discord: String::new(),
            discord_status: String::new(),
            discord_job: None,
            cache_limit: "2".into(),
            cache_stats: String::new(),
            cache_status: String::new(),
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
    pub fn reload(&mut self, path: &Path) {
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
        self.limits = ARCHIVE_LIMITS
            .iter()
            .zip(ARCHIVE_DEFAULTS)
            .map(|((_, _, key, _, _), d)| match st.get(*key) {
                Some(Value::Number(n)) => n.to_string(),
                Some(Value::String(s)) => s.clone(),
                _ => d.to_string(),
            })
            .collect();
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
        self.itch_key =
            cyoa_engine::itch::resolve_itch_api_key(None, Some(&cyoa_engine::itch::OsItchKeyring))
                .key
                .unwrap_or_default();
        self.discord = cyoa_engine::discord::resolve_bot_token(None).unwrap_or_default();
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
        self.refresh_cache();
        self.loaded = true;
    }

    pub fn ensure_loaded(&mut self, path: &Path) {
        if !self.loaded {
            self.reload(path);
        }
    }

    pub fn closed(&mut self) {
        self.loaded = false;
    }

    fn poll(&mut self) {
        for (job, slot) in [
            (&mut self.discord_job, &mut self.discord_status),
            (&mut self.file_job, &mut self.status),
            (&mut self.cf_job, &mut self.cf_status),
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
    }

    fn refresh_cache(&mut self) {
        let Some(root) = cyoa_storage::cache::ImageCache::default_root() else {
            self.cache_stats = "Image cache unavailable".into();
            return;
        };
        let limit =
            cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path())
                .load()
                .ok()
                .and_then(|s| s.get("image_cache_max_mb").and_then(Value::as_u64))
                .unwrap_or(2048);
        match cyoa_storage::cache::ImageCache::open(&root, limit) {
            Ok(cache) => {
                let s = cache.stats();
                self.cache_limit = py_g(s.limit_mb as f64 / 1024.0);
                self.cache_stats = format!(
                    "{} files · {} MB used",
                    s.entries,
                    s.size_bytes / (1024 * 1024)
                );
            }
            Err(e) => self.cache_stats = e.to_string(),
        }
    }

    // ── General page ───────────────────────────────────────────────────

    pub fn general_cards(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        self.poll();
        card(
            ui,
            "Access & Credentials",
            "Optional authentication used by download services.",
        );
        ui.label(egui::RichText::new("YouTube cookies").strong());
        ui.label(
            egui::RichText::new(
                "Used by yt-dlp for login or age-restricted media; the file remains local.",
            )
            .weak(),
        );
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut self.cookie)
                    .hint_text("Netscape cookies.txt (optional)")
                    .desired_width(320.0),
            );
            if ui.button("Browse").clicked()
                && let Some(p) = rfd::FileDialog::new()
                    .add_filter("Cookies", &["txt"])
                    .pick_file()
            {
                self.cookie = p.display().to_string();
            }
            if ui.button("Save").clicked() {
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
        ui.add_space(4.0);
        ui.label(egui::RichText::new("itch.io API key").strong());
        ui.label(egui::RichText::new("Required by itch-dl; keyring storage is preferred.").weak());
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut self.itch_key)
                    .password(true)
                    .hint_text("itch.io API key")
                    .desired_width(220.0),
            );
            if ui.button("Save").clicked() {
                self.save_itch_key(path);
            }
        });

        card(
            ui,
            "JavaScript Archive Policy",
            "Auto + Safe is recommended. These are safety caps for dynamic website discovery.",
        );
        egui::Grid::new("archive-policy")
            .num_columns(2)
            .spacing([16.0, 4.0])
            .show(ui, |ui| {
                ui.label("Strategy")
                    .on_hover_text("Auto selects the lightest complete pipeline.");
                combo(
                    ui,
                    "arch-strategy",
                    &mut self.strategy,
                    &["auto", "classic", "smart", "browser"],
                );
                ui.end_row();
                ui.label("Safe interaction")
                    .on_hover_text("Safe permits guarded scroll/click; Off never clicks.");
                combo(
                    ui,
                    "arch-interaction",
                    &mut self.interaction,
                    &["safe", "off"],
                );
                ui.end_row();
                for (i, (label, hint, _, _, _)) in ARCHIVE_LIMITS.iter().enumerate() {
                    ui.label(*label).on_hover_text(*hint);
                    ui.add(egui::TextEdit::singleline(&mut self.limits[i]).desired_width(90.0));
                    ui.end_row();
                }
            });
        ui.label(
            egui::RichText::new(
                "Every number is a safety cap, not a download target. Keep the defaults unless a site is incomplete.",
            )
            .weak(),
        );
        ui.horizontal(|ui| {
            if ui.button("Save archive policy").clicked() {
                self.save_archive(path);
            }
            ui.label(&self.archive_status);
        });

        card(
            ui,
            "Download Features",
            "Optional helpers are applied immediately to the current session.",
        );
        egui::Grid::new("features").num_columns(2).show(ui, |ui| {
            for (i, (badge, title, desc, key, _)) in FEATURES.iter().enumerate() {
                let mut on = self.features[i];
                let resp = ui
                    .checkbox(&mut on, format!("[{badge}] {title}"))
                    .on_hover_text(*desc);
                if resp.changed() {
                    self.features[i] = on;
                    let value: Value = if *key == "gallery_dl_mode" {
                        (if on { "smart" } else { "off" }).into()
                    } else {
                        on.into()
                    };
                    self.status = match update(path, |m| {
                        m.insert((*key).into(), value);
                    }) {
                        Ok(()) => format!("{title}: {}", if on { "ON" } else { "OFF" }),
                        Err(e) => format!("{title}: {e}"),
                    };
                }
                if i % 2 == 1 {
                    ui.end_row();
                }
            }
        });
        let _ = ctx;
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

    // ── Integrations page ──────────────────────────────────────────────

    pub fn integration_cards(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        self.poll();
        self.cloudflare_card(ui, path, ctx);
        card(
            ui,
            "gallery-dl config",
            "Open or create gallery-dl config.json.",
        );
        if ui.button("Edit JSON").clicked() {
            self.status = match open_gallery_dl_config(path) {
                Ok(()) => String::new(),
                Err(e) => format!("Open gallery-dl config: {e}"),
            };
        }
        card(
            ui,
            "Discord Bot Token",
            "Used only to refresh expired Discord attachment URLs.",
        );
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut self.discord)
                    .password(true)
                    .hint_text("Discord Bot Token")
                    .desired_width(260.0),
            );
            if ui.button("Save").clicked() {
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
        ui.label(egui::RichText::new(&self.discord_status).weak());
    }

    /// Python `_settings_inline_cloudflare`.
    fn cloudflare_card(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        card(
            ui,
            "Cloudflare / FlareSolverr",
            "Challenge-solving behavior for protected websites.",
        );
        egui::Grid::new("cf-grid")
            .num_columns(4)
            .spacing([12.0, 4.0])
            .show(ui, |ui| {
                ui.label("Mode")
                    .on_hover_text("Auto selects a solver only when challenged.");
                combo(
                    ui,
                    "cf-mode",
                    &mut self.cf_mode,
                    &["Off", "Auto", "cloudscraper", "FlareSolverr"],
                );
                ui.label("Auto priority")
                    .on_hover_text("Solver attempted first in Auto mode.");
                combo(
                    ui,
                    "cf-prio",
                    &mut self.cf_priority,
                    &["FlareSolverr first", "cloudscraper first"],
                );
                ui.end_row();
                ui.label("Session")
                    .on_hover_text("Controls challenge-cookie reuse.");
                combo(
                    ui,
                    "cf-session",
                    &mut self.cf_session,
                    &["temporary", "reuse-domain", "manual"],
                );
                ui.label("Proxy")
                    .on_hover_text("Inherit uses the proxy from the main form.");
                combo(ui, "cf-proxy", &mut self.cf_proxy, &["inherit", "none"]);
                ui.end_row();
                ui.label("FlareSolverr API URL")
                    .on_hover_text("Address of the running FlareSolverr service.");
                ui.add(egui::TextEdit::singleline(&mut self.cf_url).desired_width(240.0));
                ui.end_row();
                ui.label("Timeout (s)")
                    .on_hover_text("Maximum time allowed to solve a challenge.");
                ui.add(egui::TextEdit::singleline(&mut self.cf_timeout).desired_width(60.0));
                ui.label("Wait after solve (s)")
                    .on_hover_text("Extra delay for the solved page to stabilize.");
                ui.add(egui::TextEdit::singleline(&mut self.cf_wait).desired_width(60.0));
                ui.end_row();
            });
        ui.horizontal(|ui| {
            if ui.button("Clear sessions").clicked() {
                self.cf_status = "Clearing sessions…".into();
                if let Some(cfg) = self.apply_cf(path) {
                    self.spawn_cf(ctx, cfg, false);
                }
            }
            if ui
                .add_enabled(self.cf_job.is_none(), egui::Button::new("Test"))
                .clicked()
                && let Some(cfg) = self.apply_cf(path)
            {
                self.cf_status = "Testing FlareSolverr…".into();
                self.spawn_cf(ctx, cfg, true);
            }
            if ui.button("Save").clicked() && self.apply_cf(path).is_some() {
                self.cf_status = "Saved.".into();
                self.status = "Cloudflare settings saved.".into();
            }
            ui.label(egui::RichText::new(&self.cf_status).weak());
        });
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

    // ── Maintenance page ───────────────────────────────────────────────

    pub fn maintenance_cards(&mut self, ui: &mut egui::Ui, path: &Path, ctx: &egui::Context) {
        self.poll();
        card(
            ui,
            "Tools & Maintenance",
            "Manage storage and portable settings without another app window.",
        );
        egui::Grid::new("tools")
            .num_columns(2)
            .spacing([16.0, 6.0])
            .show(ui, |ui| {
                if tool(
                    ui,
                    "Settings file",
                    "Edit the active settings.json.",
                    "Edit JSON",
                ) {
                    self.status = open_settings_file(path)
                        .err()
                        .map_or_else(String::new, |e| format!("Open settings.json: {e}"));
                }
                if tool(
                    ui,
                    "Settings folder",
                    "Show the local settings/history folder.",
                    "Show",
                ) {
                    let dir = path.parent().map(Path::to_path_buf).unwrap_or_default();
                    let _ = std::fs::create_dir_all(&dir);
                    self.status = open_with_os(&dir)
                        .err()
                        .map_or_else(String::new, |e| format!("Open settings folder: {e}"));
                }
                ui.end_row();
                if tool(
                    ui,
                    "Export settings",
                    "Create a portable secret-safe export.",
                    "Export…",
                ) {
                    self.start_file_job(path, true, ctx);
                }
                if tool(
                    ui,
                    "Import settings",
                    "Merge a previous portable export.",
                    "Import…",
                ) {
                    self.start_file_job(path, false, ctx);
                }
                ui.end_row();
            });

        card(
            ui,
            "Image cache",
            "Speeds up repeated downloads; clearing it does not remove completed output.",
        );
        ui.label(&self.cache_stats);
        ui.horizontal(|ui| {
            ui.label("Maximum size (GB)");
            ui.add(egui::TextEdit::singleline(&mut self.cache_limit).desired_width(70.0));
            if ui.button("Save limit").clicked() {
                self.save_cache_limit(path);
            }
            if ui.button("Show folder").clicked()
                && let Some(root) = cyoa_storage::cache::ImageCache::default_root()
            {
                let _ = std::fs::create_dir_all(&root);
                let _ = open_with_os(&root);
            }
            if ui.button("Clear cache").clicked() {
                self.confirm_clear = true;
            }
        });
        if self.confirm_clear {
            ui.horizontal(|ui| {
                ui.colored_label(
                    egui::Color32::from_rgb(0xf5, 0x9e, 0x0b),
                    "Remove cached images? Completed downloads are not touched.",
                );
                if ui.button("Yes").clicked() {
                    self.confirm_clear = false;
                    self.clear_cache();
                }
                if ui.button("No").clicked() {
                    self.confirm_clear = false;
                }
            });
        }
        ui.label(egui::RichText::new(&self.cache_status).weak());
    }

    fn save_cache_limit(&mut self, path: &Path) {
        let Some(mb) = cache_limit_mb(&self.cache_limit) else {
            self.cache_status = "Enter a positive GB value.".into();
            return;
        };
        match update(path, |m| {
            m.insert("image_cache_max_mb".into(), mb.into());
        }) {
            Ok(()) => {
                if let Some(root) = cyoa_storage::cache::ImageCache::default_root()
                    && let Ok(cache) = cyoa_storage::cache::ImageCache::open(&root, mb)
                {
                    let _ = cache.flush();
                }
                self.refresh_cache();
                self.cache_status = format!("Limit saved: {} GB", py_g(mb as f64 / 1024.0));
            }
            Err(e) => self.cache_status = e,
        }
    }

    fn clear_cache(&mut self) {
        let Some(root) = cyoa_storage::cache::ImageCache::default_root() else {
            return;
        };
        let result = cyoa_storage::cache::ImageCache::open(&root, 2048).and_then(|c| c.clear());
        self.refresh_cache();
        self.cache_status = match result {
            Ok(n) => format!("Cleared {n} file(s)."),
            Err(e) => e.to_string(),
        };
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

fn card(ui: &mut egui::Ui, title: &str, desc: &str) {
    ui.add_space(8.0);
    ui.label(
        egui::RichText::new(title.to_uppercase())
            .strong()
            .size(10.0),
    );
    ui.label(egui::RichText::new(desc).weak().size(9.5));
    ui.separator();
}

fn tool(ui: &mut egui::Ui, title: &str, desc: &str, button: &str) -> bool {
    let mut clicked = false;
    ui.vertical(|ui| {
        ui.label(egui::RichText::new(title).strong());
        ui.label(egui::RichText::new(desc).weak().size(9.5));
        clicked = ui.button(button).clicked();
    });
    clicked
}

fn combo(ui: &mut egui::Ui, id: &str, value: &mut String, options: &[&str]) {
    egui::ComboBox::from_id_salt(id)
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
