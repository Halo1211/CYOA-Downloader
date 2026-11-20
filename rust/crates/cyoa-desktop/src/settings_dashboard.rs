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
        self.net_draft = None;
        self.auto_status.clear();
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
                    let color = FEATURE_COLORS[i];
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
                        let value: Value = if key == "gallery_dl_mode" {
                            (if on { "smart" } else { "off" }).into()
                        } else {
                            on.into()
                        };
                        self.status = match update(path, |m| {
                            m.insert(key.into(), value);
                        }) {
                            Ok(()) => format!("{name}: {}", if on { "ON" } else { "OFF" }),
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
            },
        );

        title(
            ui,
            "JavaScript Archive Policy",
            "Auto + Safe is recommended. These are safety caps for dynamic website discovery.",
        );
        let cyan = rgb(0x08, 0x91, 0xb2);
        frame(ui, cyan).show(ui, |ui| {
            ui.set_width(ui.available_width());
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
        let _ = ctx;
        new_pref
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
            "gallery-dl config",
            "Open or create gallery-dl config.json.",
            |ui| {
                if accent_button(ui, "Edit JSON", rgb(0x0f, 0x76, 0x6e)) {
                    self.status = match open_gallery_dl_config(path) {
                        Ok(()) => String::new(),
                        Err(e) => format!("Open gallery-dl config: {e}"),
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

    // ── Maintenance page ───────────────────────────────────────────────

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
                        self.save_cache_limit(path);
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
                            self.clear_cache();
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

const FEATURE_COLORS: &[egui::Color32] = &[
    egui::Color32::from_rgb(0x3b, 0x82, 0xf6),
    egui::Color32::from_rgb(0x8b, 0x5c, 0xf6),
    egui::Color32::from_rgb(0x10, 0xb9, 0x81),
    egui::Color32::from_rgb(0xf5, 0x9e, 0x0b),
    egui::Color32::from_rgb(0x14, 0xb8, 0xa6),
    egui::Color32::from_rgb(0xef, 0x44, 0x44),
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
