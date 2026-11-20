//! eframe UI. Rendering only touches in-memory state; all network and file work
//! happens in [`crate::worker`].

use crate::worker::{Event, JobId, JobResult, WorkerHandle};
use cyoa_engine::{
    batch::{self, BatchItem},
    run::{OutputMode, RunOptions},
};
use eframe::egui;
use std::{collections::VecDeque, path::PathBuf, time::Duration};

mod layout;

/// Python defaults (`cyoa_downloader_app/app_info.py`).
pub const DEFAULT_MAX_WORKERS: usize = 4;
pub const DEFAULT_WAIT_TIME: u64 = 60;
const MAX_EVENTS_PER_FRAME: usize = 2000;
const LOG_CAPACITY: usize = 5000;
const SETTINGS_KEY: &str = "cyoa-settings";

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Mode {
    /// Python "Auto (detect)": resolved per job (cyoap_vue or ICC website).
    Auto,
    Embed,
    Zip,
    Both,
    /// ICC website as `<name>_site.zip` (Python "ICC ZIP").
    IccZip,
    /// ICC website kept as a folder (Python "ICC Folder").
    IccFolder,
    /// Pure website (no project search) as `<name>_site.zip`.
    PureZip,
    /// Pure website kept as a folder.
    PureFolder,
    /// Dedicated cyoap_vue ICC mode, ZIP (Python "CYOAP Vue ZIP").
    CyoapZip,
    /// Dedicated cyoap_vue ICC mode, folder.
    CyoapFolder,
}

impl Mode {
    pub fn is_icc(self) -> bool {
        matches!(
            self,
            Mode::Auto
                | Mode::IccZip
                | Mode::IccFolder
                | Mode::PureZip
                | Mode::PureFolder
                | Mode::CyoapZip
                | Mode::CyoapFolder
        )
    }

    /// Python `engine_mode` for this GUI mode.
    pub fn engine(self) -> cyoa_engine::run::Engine {
        match self {
            Mode::CyoapZip | Mode::CyoapFolder => cyoa_engine::run::Engine::CyoapVue,
            _ => cyoa_engine::run::Engine::Standard,
        }
    }
}

impl From<Mode> for OutputMode {
    fn from(m: Mode) -> Self {
        match m {
            // Placeholder; the worker replaces it with the detected mode.
            Mode::Auto => OutputMode::IccFolder,
            Mode::Embed => OutputMode::Embed,
            Mode::Zip => OutputMode::Zip,
            Mode::Both => OutputMode::Both,
            Mode::IccZip => OutputMode::IccZip,
            Mode::IccFolder => OutputMode::IccFolder,
            Mode::PureZip => OutputMode::PureZip,
            Mode::PureFolder => OutputMode::PureFolder,
            Mode::CyoapZip => OutputMode::IccZip,
            Mode::CyoapFolder => OutputMode::IccFolder,
        }
    }
}

/// Python `ArchivePolicy` from the shared settings.json (`archive_*`);
/// the browser strategy (CDP) is not ported and is refused.
/// Saved Cloudflare/FlareSolverr settings (Python GUI `_load_cloudflare_settings`).
fn cloudflare_config() -> std::sync::Arc<cyoa_net::cloudflare::CloudflareConfig> {
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    std::sync::Arc::new(
        store
            .load()
            .map(cyoa_net::cloudflare::CloudflareConfig::from_settings)
            .unwrap_or_default(),
    )
}

/// Saved offline viewer settings (shared settings.json, Python keys).
fn offline_viewer_config() -> Option<cyoa_engine::run::OfflineViewerOptions> {
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    store
        .load()
        .ok()
        .and_then(cyoa_engine::run::OfflineViewerOptions::from_settings)
}

fn archive_policy() -> Result<cyoa_engine::icc::ArchivePolicy, String> {
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    let policy = store
        .load()
        .map(cyoa_engine::icc::ArchivePolicy::from_settings)
        .unwrap_or_default();
    Ok(policy)
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Settings {
    pub output_dir: String,
    pub mode: Mode,
    pub threads: usize,
    pub wait_time: u64,
    /// Proxy/DNS/VPN (absent in older saved state: defaults).
    #[serde(default)]
    pub net: crate::net::NetSettings,
    /// Python GUI "Fonts" checkbox (default on).
    #[serde(default = "default_true")]
    pub fonts: bool,
    /// Python GUI "Font analysis" checkbox.
    #[serde(default)]
    pub analyse_fonts: bool,
    /// Bandwidth limit in KB/s (0 = unlimited), Python GUI `bw`.
    #[serde(default)]
    pub bandwidth_kbps: f64,
    /// Python "HTTP/2" checkbox (`http2_enabled`).
    #[serde(default)]
    pub http2: bool,
    /// Python "YT Audio" checkbox (yt-dlp audio recovery, default on).
    #[serde(default = "default_true")]
    pub ytdlp: bool,
}

fn default_true() -> bool {
    true
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            output_dir: std::env::current_dir()
                .map(|p| p.display().to_string())
                .unwrap_or_default(),
            mode: Mode::Auto,
            threads: DEFAULT_MAX_WORKERS,
            wait_time: DEFAULT_WAIT_TIME,
            net: crate::net::NetSettings::default(),
            fonts: true,
            analyse_fonts: false,
            bandwidth_kbps: 0.0,
            http2: false,
            ytdlp: true,
        }
    }
}

/// Open a local preview URL (always `http://127.0.0.1:<port>/`) in the
/// default browser.
fn open_in_browser(url: &str) -> std::io::Result<()> {
    if !url.starts_with("http://127.0.0.1:") {
        return Err(std::io::Error::other("refusing non-local preview URL"));
    }
    #[cfg(windows)]
    let mut cmd = {
        let mut c = std::process::Command::new("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        c
    };
    #[cfg(target_os = "macos")]
    let mut cmd = {
        let mut c = std::process::Command::new("open");
        c.arg(url);
        c
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = {
        let mut c = std::process::Command::new("xdg-open");
        c.arg(url);
        c
    };
    cmd.spawn().map(|_| ())
}

/// Same rule as the CLI/backend: absolute http(s) URL with a host.
pub fn validate_url(input: &str) -> Result<String, &'static str> {
    let t = input.trim();
    if t.is_empty() {
        return Err("Enter a project URL.");
    }
    match url::Url::parse(t) {
        Ok(u) if matches!(u.scheme(), "http" | "https") && u.host_str().is_some() => {
            Ok(t.to_owned())
        }
        _ => Err("URL must start with http:// or https://"),
    }
}

/// Bounded log: oldest lines are dropped beyond `cap`.
pub struct RingLog {
    lines: VecDeque<String>,
    cap: usize,
}

impl RingLog {
    pub fn new(cap: usize) -> Self {
        Self {
            lines: VecDeque::with_capacity(cap.min(1024)),
            cap,
        }
    }
    pub fn push(&mut self, line: String) {
        if self.lines.len() == self.cap {
            self.lines.pop_front();
        }
        self.lines.push_back(line);
    }
    pub fn len(&self) -> usize {
        self.lines.len()
    }
    pub fn get(&self, i: usize) -> Option<&str> {
        self.lines.get(i).map(String::as_str)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Status {
    /// Added to the queue; starts on "Download All" (Python queue rows).
    Pending,
    Queued,
    Running {
        done: usize,
        total: usize,
    },
    Done {
        failed_assets: usize,
    },
    Failed(String),
    Cancelled,
}

pub struct Job {
    pub id: JobId,
    pub url: String,
    pub status: Status,
    pub current: String,
    /// Output filename (empty: derived) and resolved mode for a pending row.
    pub filename: String,
    pub mode: Result<Mode, String>,
}

pub struct DownloaderApp {
    settings: Settings,
    url_input: String,
    url_error: Option<&'static str>,
    jobs: Vec<Job>,
    selected: Option<usize>,
    next_id: JobId,
    log: RingLog,
    worker: Option<WorkerHandle>,
    folder_pick: Option<std::sync::mpsc::Receiver<Option<PathBuf>>>,
    list_pick: Option<ListPick>,
    /// Built network objects, reused while the settings stay unchanged.
    net_cache: Option<(crate::net::NetSettings, crate::net::NetRuntime)>,
    /// Shared Python-format `settings.json` and the network values last
    /// synced with it (None: no shared file, e.g. headless tests).
    shared: Option<(PathBuf, crate::net::NetSettings)>,
    /// CYOA Manager toggle, persisted only in settings.json like Python.
    manager: crate::net::ManagerPrefs,
    manager_synced: crate::net::ManagerPrefs,
    /// Last website folder output (Python `_set_last_preview_folder`).
    last_preview: Option<PathBuf>,
    /// Running local preview server (Serve).
    preview: Option<cyoa_integrations::preview::PreviewServer>,
    /// CYOA Manager library panel (Python `open_library_panel`).
    library: Option<crate::library::LibraryPanel>,
    /// Offline viewer settings panel (F-037 V5).
    viewers: Option<crate::viewers::ViewerPanel>,
    /// F-043 AI Assist panel and the session AI state (key never persisted).
    ai_panel: Option<crate::ai_panel::AiPanel>,
    ai_session: crate::ai_panel::AiSession,
    /// App/history update checks (F-052).
    updates: Option<crate::updates_panel::UpdatesPanel>,
    /// Python "Filename" entry next to the URL.
    fn_input: String,
    /// Python `theme_mode` (System / Light / Dark), saved to settings.json.
    theme_mode: String,
    show_settings: bool,
    show_credits: bool,
    show_help: bool,
    show_diagnostics: bool,
    diag_report: Option<String>,
    /// Python `auto_detect_output` ("folder" / "zip").
    auto_pref: String,
    /// Python `cloudflare_mode` selector value.
    cf_mode: String,
    /// DNS "Custom…" row visible.
    dns_custom: bool,
    /// Progress card "Show Details".
    show_details: bool,
    logo_dark: Option<egui::TextureHandle>,
    logo_light: Option<egui::TextureHandle>,
    /// Python `_paused` (queue paused between jobs).
    paused: bool,
    /// Background Retry Assets result.
    retry_rx: Option<std::sync::mpsc::Receiver<String>>,
    /// Failed assets reported by finished/running jobs (Reports Center).
    asset_failures: Vec<crate::reports_panel::ResultRow>,
    reports: Option<crate::reports_panel::ReportsPanel>,
    preflight: Option<crate::preflight_panel::PreflightPanel>,
}

type ListPick = std::sync::mpsc::Receiver<Option<Result<Vec<BatchItem>, String>>>;

/// Batch row mode: empty/"auto" uses the GUI default; unknown modes are
/// refused instead of silently downgraded.
pub fn batch_item_mode(default: Mode, mode: &str) -> Result<Mode, String> {
    match mode {
        "" | "auto" => Ok(default),
        "embed" => Ok(Mode::Embed),
        "zip" => Ok(Mode::Zip),
        "both" => Ok(Mode::Both),
        "website" | "website_zip" => Ok(Mode::IccZip),
        "pure_website" | "pure_website_zip" => Ok(Mode::PureZip),
        "pure_website_folder" => Ok(Mode::PureFolder),
        "website_folder" => Ok(Mode::IccFolder),
        "cyoap_vue" | "cyoap_vue_zip" => Ok(Mode::CyoapZip),
        "cyoap_vue_folder" => Ok(Mode::CyoapFolder),
        other => Err(format!(
            "mode '{other}' is not supported by the Rust port yet"
        )),
    }
}

/// Saved Python `language` (default "en").
fn saved_str_lang(path: &std::path::Path) -> String {
    cyoa_storage::settings::SettingsStore::new(path)
        .load()
        .ok()
        .and_then(|m| {
            m.get("language")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "en".into())
}

impl DownloaderApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mut settings: Settings = cc
            .storage
            .and_then(|s| eframe::get_value(s, SETTINGS_KEY))
            .unwrap_or_default();
        let ctx = cc.egui_ctx.clone();
        let mut log = RingLog::new(LOG_CAPACITY);
        // settings.json (shared with the Python app and the CLI) wins over the
        // eframe state file for network keys.
        let shared_path = cyoa_storage::settings::default_path();
        if let Err(e) = settings.net.load_shared(&shared_path) {
            log.push(format!("Could not read {}: {e}", shared_path.display()));
        }
        let manager = cyoa_storage::settings::SettingsStore::new(&shared_path)
            .load()
            .map(crate::net::ManagerPrefs::from_python)
            .unwrap_or_default();
        let theme_mode = cyoa_storage::settings::SettingsStore::new(&shared_path)
            .load()
            .ok()
            .and_then(|m| {
                m.get("theme_mode")
                    .and_then(|v| v.as_str())
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| "System".into());
        crate::theme::install(&cc.egui_ctx, &theme_mode);
        crate::i18n::set_language(&saved_str_lang(&shared_path));
        let saved_str = |k: &str, d: &str| {
            cyoa_storage::settings::SettingsStore::new(&shared_path)
                .load()
                .ok()
                .and_then(|m| m.get(k).and_then(|v| v.as_str()).map(str::to_owned))
                .unwrap_or_else(|| d.to_owned())
        };
        let auto_pref =
            cyoa_engine::auto_detect::normalize_pref(&saved_str("auto_detect_output", "folder"))
                .to_owned();
        let cf_mode =
            cyoa_net::cloudflare::normalize_cloudflare_mode(&saved_str("cloudflare_mode", "auto"));
        let ai_enabled = cyoa_storage::settings::SettingsStore::new(&shared_path)
            .load()
            .ok()
            .and_then(|s| s.get("ai_enabled").and_then(serde_json::Value::as_bool))
            .unwrap_or(false);
        let shared = Some((shared_path, settings.net.clone()));
        let worker = match WorkerHandle::spawn(Box::new(move || ctx.request_repaint())) {
            Ok(w) => Some(w),
            Err(e) => {
                log.push(format!("Could not start the download worker: {e}"));
                None
            }
        };
        Self {
            settings,
            url_input: String::new(),
            url_error: None,
            jobs: Vec::new(),
            selected: None,
            next_id: 1,
            log,
            worker,
            folder_pick: None,
            list_pick: None,
            net_cache: None,
            shared,
            manager_synced: manager.clone(),
            last_preview: None,
            preview: None,
            library: None,
            viewers: None,
            ai_panel: None,
            ai_session: crate::ai_panel::AiSession {
                enabled: ai_enabled,
                key: String::new(),
            },
            updates: None,
            fn_input: String::new(),
            theme_mode,
            show_settings: false,
            show_credits: false,
            show_help: false,
            show_diagnostics: false,
            diag_report: None,
            auto_pref,
            cf_mode,
            dns_custom: false,
            show_details: false,
            logo_dark: None,
            logo_light: None,
            paused: false,
            retry_rx: None,
            asset_failures: Vec::new(),
            reports: None,
            preflight: None,
            manager,
        }
    }

    fn job_mut(&mut self, id: JobId) -> Option<&mut Job> {
        // Ids are monotonic, so the queue is sorted by id.
        let i = self.jobs.binary_search_by_key(&id, |j| j.id).ok()?;
        self.jobs.get_mut(i)
    }

    fn drain_events(&mut self) {
        let Some(worker) = &self.worker else { return };
        let mut events = Vec::new();
        while events.len() < MAX_EVENTS_PER_FRAME {
            match worker.try_recv() {
                Some(e) => events.push(e),
                None => break,
            }
        }
        for ev in events {
            self.apply(ev);
        }
    }

    fn apply(&mut self, ev: Event) {
        match ev {
            Event::Started { job } => {
                if let Some(j) = self.job_mut(job) {
                    j.status = Status::Running { done: 0, total: 0 };
                    let msg = format!("Started: {}", j.url);
                    self.log.push(msg);
                }
            }
            Event::Progress {
                job,
                done,
                total,
                name,
                ok,
            } => {
                if !ok {
                    self.log.push(format!("FAILED asset: {name}"));
                    if self.asset_failures.len() < 100_000 {
                        let mode = self
                            .job_mut(job)
                            .and_then(|j| j.mode.as_ref().ok().copied())
                            .map(|m| crate::theme::mode_entry(m).key.to_owned())
                            .unwrap_or_default();
                        self.asset_failures.push(crate::reports_panel::ResultRow {
                            result_type: "asset".into(),
                            status: "FAIL".into(),
                            url: name.clone(),
                            mode,
                            filename: String::new(),
                            error: "download failed".into(),
                        });
                    }
                }
                if let Some(j) = self.job_mut(job) {
                    j.status = Status::Running { done, total };
                    j.current = name;
                }
            }
            Event::Finished { job, result } => {
                // Folder website outputs become the Serve target.
                if let JobResult::Done(s) = &result
                    && let Some(dir) = s.written.iter().map(PathBuf::from).find(|p| p.is_dir())
                {
                    self.last_preview = Some(dir);
                }
                let line;
                if let Some(j) = self.job_mut(job) {
                    j.current.clear();
                    match result {
                        JobResult::Done(s) => {
                            j.status = Status::Done {
                                failed_assets: s.failed_assets,
                            };
                            line = format!(
                                "Done: {} ({} file(s), {} failed asset(s))",
                                j.url,
                                s.written.len(),
                                s.failed_assets
                            );
                        }
                        JobResult::Failed(e) => {
                            line = format!("Failed: {}: {e}", j.url);
                            j.status = Status::Failed(e);
                        }
                        JobResult::Cancelled => {
                            j.status = Status::Cancelled;
                            line = format!("Cancelled: {}", j.url);
                        }
                    }
                    self.log.push(line);
                }
            }
        }
    }

    fn enqueue(&mut self) {
        match validate_url(&self.url_input) {
            Err(e) => self.url_error = Some(e),
            Ok(url) => {
                self.url_error = None;
                let filename = self.fn_input.trim().to_owned();
                self.enqueue_one(url, filename, Ok(self.settings.mode));
                self.url_input.clear();
                self.fn_input.clear();
            }
        }
    }

    /// Python "Add URL" / queue import: the row waits for "Download All".
    fn enqueue_one(&mut self, url: String, filename: String, mode: Result<Mode, String>) {
        let id = self.next_id;
        self.next_id += 1;
        let status = match &mode {
            Err(e) => Status::Failed(e.clone()),
            Ok(_) => Status::Pending,
        };
        self.jobs.push(Job {
            id,
            url,
            status,
            current: String::new(),
            filename,
            mode,
        });
    }

    /// Python "▶ Download All": hand every pending row to the worker.
    fn download_all(&mut self) {
        let pending: Vec<usize> = (0..self.jobs.len())
            .filter(|&i| self.jobs[i].status == Status::Pending)
            .collect();
        if pending.is_empty() {
            self.log
                .push("Queue is empty — add a URL first.".to_owned());
            return;
        }
        for i in pending {
            let (id, url, filename, mode) = {
                let j = &self.jobs[i];
                (j.id, j.url.clone(), j.filename.clone(), j.mode.clone())
            };
            let status = self.start_job(id, url, filename, mode);
            self.jobs[i].status = status;
        }
    }

    fn start_job(
        &mut self,
        id: JobId,
        url: String,
        filename: String,
        mode: Result<Mode, String>,
    ) -> Status {
        let net = self.net_runtime();
        // ICC jobs follow the shared `archive_strategy` setting.
        let archive = match &mode {
            Ok(m) if m.is_icc() => archive_policy().map(Some),
            _ => Ok(None),
        };
        match (mode, net, archive) {
            (Err(e), _, _) | (_, Err(e), _) | (_, _, Err(e)) => Status::Failed(e),
            (Ok(mode), Ok(net), Ok(archive_auto)) => {
                let opts = RunOptions {
                    url,
                    output_dir: PathBuf::from(&self.settings.output_dir),
                    mode: mode.into(),
                    workers: self.settings.threads,
                    wait_time: self.settings.wait_time,
                    file_name: (!filename.is_empty()).then_some(filename),
                    proxy: net.proxy,
                    dns: net.dns,
                    vpn: net.vpn,
                    archive: archive_auto.unwrap_or_default(),
                    engine: mode.engine(),
                    fonts: self.settings.fonts,
                    bandwidth_kbps: self.settings.bandwidth_kbps,
                    cloudflare: Some(cloudflare_config()),
                    analyse_fonts: self.settings.analyse_fonts,
                    offline_viewer: offline_viewer_config(),
                    ai: crate::ai_panel::run_config(
                        self.shared.as_ref().map(|(p, _)| p.as_path()),
                        &self.ai_session,
                    ),
                    http2: self.settings.http2,
                    integrations: cyoa_engine::run::Integrations {
                        ytdlp: self.settings.ytdlp,
                        ..RunOptions::default().integrations
                    },
                    ..RunOptions::default()
                };
                // Python's ICC branch returns before CYOA Manager registration.
                let manager_db = if self.manager.enabled && !mode.is_icc() {
                    let db = cyoa_integrations::cyoa_manager::resolve_db(&self.manager.db_path);
                    if db.is_none() {
                        self.log.push(
                            "CYOA Manager: library.sqlite3 not found; registration skipped."
                                .to_owned(),
                        );
                    }
                    db
                } else {
                    None
                };
                let auto = (mode == Mode::Auto).then(cyoa_engine::auto_detect::saved_pref);
                let spec = crate::worker::JobSpec {
                    opts,
                    manager_db,
                    auto,
                };
                if self.worker.as_ref().is_some_and(|w| w.start(id, spec)) {
                    Status::Queued
                } else {
                    Status::Failed("download worker unavailable or queue full".into())
                }
            }
        }
    }

    fn poll_list_import(&mut self) {
        let Some(rx) = &self.list_pick else { return };
        match rx.try_recv() {
            Ok(Some(Ok(items))) => {
                self.list_pick = None;
                let n = items.len();
                for item in items {
                    let mode = batch_item_mode(self.settings.mode, &item.mode);
                    self.enqueue_one(item.url, item.filename, mode);
                }
                self.log.push(format!("Imported {n} item(s) from list."));
            }
            Ok(Some(Err(e))) => {
                self.list_pick = None;
                self.log.push(format!("List import failed: {e}"));
            }
            Ok(None) | Err(std::sync::mpsc::TryRecvError::Disconnected) => self.list_pick = None,
            Err(std::sync::mpsc::TryRecvError::Empty) => {}
        }
    }

    fn start_list_import(&mut self, ctx: &egui::Context) {
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("list-import".into())
            .spawn(move || {
                // Dialog and file parsing both stay off the UI thread.
                let picked = rfd::FileDialog::new()
                    .add_filter("Batch list", &["txt", "csv", "xlsx", "xls"])
                    .pick_file();
                let result = picked.map(|path| match batch::import_batch_file(&path) {
                    Ok(items) if items.is_empty() => {
                        Err("No valid URLs found in batch file.".to_owned())
                    }
                    Ok(items) => Ok(items),
                    Err(e) => Err(e.to_string()),
                });
                let _ = tx.send(result);
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.list_pick = Some(rx);
        }
    }

    /// Validated network objects; rebuilt only when the settings changed so
    /// jobs share one DNS cache / VPN status cache.
    fn net_runtime(&mut self) -> Result<crate::net::NetRuntime, String> {
        if let Some((s, rt)) = &self.net_cache
            && *s == self.settings.net
        {
            return Ok(rt.clone());
        }
        let rt = self.settings.net.build()?;
        self.net_cache = Some((self.settings.net.clone(), rt.clone()));
        Ok(rt)
    }

    fn start_preview(&mut self) {
        let Some(folder) = self.last_preview.clone() else {
            return;
        };
        match cyoa_integrations::preview::PreviewServer::start(&folder) {
            Ok(server) => {
                let url = server.url();
                self.log
                    .push(format!("[Server] Serving {} at {url}", folder.display()));
                if let Err(e) = open_in_browser(&url) {
                    self.log
                        .push(format!("[Server] Could not open browser: {e}"));
                }
                self.preview = Some(server);
            }
            Err(e) => self
                .log
                .push(format!("[Server] Could not start preview: {e}")),
        }
    }

    fn library_ui(&mut self, ctx: &egui::Context) {
        let Some(mut panel) = self.library.take() else {
            return;
        };
        let action = panel.ui(ctx, || self.net_runtime());
        match action {
            Some(crate::library::LibraryAction::Queue(records, skipped)) => {
                let n = records.len();
                for (url, name) in records {
                    self.enqueue_one(url, name, Ok(self.settings.mode));
                }
                self.log
                    .push(format!("{n} URL queued; {skipped} local-only skipped."));
            }
            Some(crate::library::LibraryAction::Serve(folder)) => {
                if self.preview.take().is_some() {
                    self.log.push("[Server] Preview stopped.".to_owned());
                }
                self.last_preview = Some(folder);
                self.start_preview();
            }
            None => {}
        }
        if panel.open {
            self.library = Some(panel);
        }
    }

    fn running_job(&self) -> Option<&Job> {
        self.jobs
            .iter()
            .rev()
            .find(|j| matches!(j.status, Status::Running { .. }))
    }
}

impl eframe::App for DownloaderApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        self.drain_events();
        self.poll_list_import();
        self.poll_retry();
        // Python: `_paused.set()` when the run ends.
        if self.paused
            && !self
                .jobs
                .iter()
                .any(|j| matches!(j.status, Status::Running { .. } | Status::Queued))
        {
            self.paused = false;
            if let Some(w) = &self.worker {
                w.set_paused(false);
            }
        }
        let p = crate::theme::palette(ui);
        egui::Panel::top("titlebar")
            .exact_size(90.0)
            .frame(egui::Frame::new().fill(p.panel))
            .show(ui, |ui| self.titlebar(ui));
        egui::Panel::left("sidebar")
            .exact_size(256.0)
            .resizable(false)
            .frame(egui::Frame::new().fill(p.sidebar))
            .show(ui, |ui| self.sidebar(ui));
        // Pinned bottom: tool pills, then progress card + LOG (Python rowB + workspace).
        egui::Panel::bottom("workspace")
            .exact_size(214.0)
            .frame(egui::Frame::new().fill(p.panel).inner_margin(egui::Margin {
                left: 0,
                right: 8,
                top: 6,
                bottom: 8,
            }))
            .show(ui, |ui| {
                self.tool_strip(ui);
                ui.add_space(8.0);
                self.bottom_area(ui);
            });
        egui::Panel::bottom("action_row")
            .exact_size(54.0)
            .frame(egui::Frame::new().fill(p.bg))
            .show(ui, |ui| self.action_row(ui));
        egui::Panel::top("input")
            .frame(egui::Frame::new().fill(p.bg).inner_margin(egui::Margin {
                left: 10,
                right: 14,
                top: 14,
                bottom: 8,
            }))
            .show(ui, |ui| self.input_section(ui));
        egui::CentralPanel::default()
            .frame(egui::Frame::new().fill(p.bg))
            .show(ui, |ui| self.queue_section(ui));
        self.dialogs(ui.ctx());
        self.library_ui(ui.ctx());
        if let Some(mut panel) = self.updates.take() {
            let requeue = panel.ui(ui.ctx(), || self.net_runtime());
            for (url, name) in requeue {
                self.enqueue_one(url, name, Ok(self.settings.mode));
            }
            if panel.open {
                self.updates = Some(panel);
            }
        }
        if let Some(mut panel) = self.preflight.take() {
            if panel.ui(ui.ctx()) {
                self.download_all();
            }
            if panel.open {
                self.preflight = Some(panel);
            }
        }
        if let Some(panel) = &mut self.reports {
            panel.ui(ui.ctx());
            if !panel.open {
                self.reports = None;
            }
        }
        if let Some(panel) = &mut self.viewers {
            panel.ui(ui.ctx());
            if !panel.open {
                self.viewers = None;
            }
        }
        if let Some(panel) = &mut self.ai_panel {
            panel.ui(ui.ctx());
            if let Some(session) = panel.updated.take() {
                self.ai_session = session;
            }
            if !panel.open {
                self.ai_panel = None;
            }
        }
        // While a job runs, the worker's waker drives repaints; no idle polling.
        if self.worker.is_some() && self.running_job().is_some() {
            ui.ctx().request_repaint_after(Duration::from_millis(250));
        }
    }

    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        eframe::set_value(storage, SETTINGS_KEY, &self.settings);
        if let Some((path, _)) = &self.shared
            && self.manager != self.manager_synced
        {
            let prefs = self.manager.clone();
            match crate::net::update_shared(path, |map| prefs.write_python(map)) {
                Ok(()) => self.manager_synced = prefs,
                Err(e) => self
                    .log
                    .push(format!("Could not save {}: {e}", path.display())),
            }
        }
        if let Some((path, synced)) = &mut self.shared
            && *synced != self.settings.net
        {
            match self.settings.net.save_shared(path) {
                Ok(()) => *synced = self.settings.net.clone(),
                Err(e) => self
                    .log
                    .push(format!("Could not save {}: {e}", path.display())),
            }
        }
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        if let Some(mut w) = self.worker.take() {
            // Cancellation is signalled instantly; wait only briefly so closing
            // the window never feels stuck. The engine cleans temp dirs on drop.
            w.shutdown(Duration::from_millis(500));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_validation() {
        assert!(validate_url("https://a.example/cyoa/").is_ok());
        assert!(validate_url("  http://a.example  ").is_ok());
        assert!(validate_url("").is_err());
        assert!(validate_url("ftp://a.example/").is_err());
        assert!(validate_url("example.com").is_err());
        assert!(validate_url("http://").is_err());
    }

    fn headless_app(jobs: usize, log_lines: usize) -> DownloaderApp {
        let mut log = RingLog::new(LOG_CAPACITY);
        for i in 0..log_lines {
            log.push(format!("log line {i}"));
        }
        DownloaderApp {
            settings: Settings::default(),
            url_input: String::new(),
            url_error: None,
            jobs: (0..jobs as JobId)
                .map(|id| Job {
                    id,
                    url: format!("https://example.com/cyoa/{id}/"),
                    status: if id % 3 == 0 {
                        Status::Running { done: 5, total: 10 }
                    } else {
                        Status::Queued
                    },
                    current: String::new(),
                    filename: String::new(),
                    mode: Ok(Mode::Embed),
                })
                .collect(),
            selected: None,
            next_id: jobs as JobId,
            log,
            worker: None,
            folder_pick: None,
            list_pick: None,
            net_cache: None,
            shared: None,
            manager: crate::net::ManagerPrefs::default(),
            manager_synced: crate::net::ManagerPrefs::default(),
            last_preview: None,
            preview: None,
            library: None,
            viewers: None,
            ai_panel: None,
            ai_session: crate::ai_panel::AiSession::default(),
            updates: None,
            fn_input: String::new(),
            theme_mode: "System".into(),
            show_settings: false,
            show_credits: false,
            show_help: false,
            show_diagnostics: false,
            diag_report: None,
            auto_pref: "folder".into(),
            cf_mode: "auto".into(),
            dns_custom: false,
            show_details: false,
            logo_dark: None,
            logo_light: None,
            paused: false,
            retry_rx: None,
            asset_failures: Vec::new(),
            reports: None,
            preflight: None,
        }
    }

    /// Frame cost with a 100k-row queue and a full log (virtualized rows).
    /// Prints the measured time; run with `--release --nocapture` for numbers.
    #[test]
    fn hundred_thousand_row_queue_frame_time() {
        let mut app = headless_app(100_000, LOG_CAPACITY);
        app.show_help = true;
        app.show_credits = true;
        app.show_settings = true;
        let ctx = egui::Context::default();
        let input = || egui::RawInput {
            screen_rect: Some(egui::Rect::from_min_size(
                egui::Pos2::ZERO,
                egui::vec2(1000.0, 700.0),
            )),
            ..Default::default()
        };
        let frame = |app: &mut DownloaderApp| {
            let mut out = ctx.run_ui(input(), |ui| {
                app.titlebar(ui);
                app.sidebar(ui);
                app.input_section(ui);
                app.queue_section(ui);
                app.action_row(ui);
                app.tool_strip(ui);
                app.bottom_area(ui);
                app.dialogs(ui.ctx());
            });
            // No GPU here: tessellate (part of real frame cost) and discard textures.
            let _ = ctx.tessellate(std::mem::take(&mut out.shapes), out.pixels_per_point);
            out.textures_delta.clear();
        };
        for _ in 0..3 {
            frame(&mut app); // warm-up (layout caches, fonts)
        }
        let n = 30;
        let start = std::time::Instant::now();
        for _ in 0..n {
            frame(&mut app);
        }
        let per_frame = start.elapsed() / n;
        println!("100k-row queue + 5000-line log: {per_frame:?} per frame");
        assert!(
            per_frame < std::time::Duration::from_millis(250),
            "{per_frame:?}"
        );
    }

    #[test]
    fn batch_modes_follow_default_or_are_refused() {
        assert_eq!(batch_item_mode(Mode::Zip, ""), Ok(Mode::Zip));
        assert_eq!(batch_item_mode(Mode::Zip, "auto"), Ok(Mode::Zip));
        assert_eq!(batch_item_mode(Mode::Zip, "both"), Ok(Mode::Both));
        assert_eq!(
            batch_item_mode(Mode::Embed, "website_folder"),
            Ok(Mode::IccFolder)
        );
        assert_eq!(
            batch_item_mode(Mode::Embed, "website_zip"),
            Ok(Mode::IccZip)
        );
        assert_eq!(
            batch_item_mode(Mode::Embed, "pure_website_folder"),
            Ok(Mode::PureFolder)
        );
        assert_eq!(
            batch_item_mode(Mode::Embed, "cyoap_vue_folder"),
            Ok(Mode::CyoapFolder)
        );
        assert!(batch_item_mode(Mode::Embed, "bogus").is_err());
    }

    #[test]
    fn ring_log_is_bounded() {
        let mut log = RingLog::new(3);
        for i in 0..10 {
            log.push(i.to_string());
        }
        assert_eq!(log.len(), 3);
        assert_eq!(log.get(0), Some("7"));
        assert_eq!(log.get(2), Some("9"));
    }
}
