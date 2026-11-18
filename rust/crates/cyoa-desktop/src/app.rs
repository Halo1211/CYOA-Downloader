//! eframe UI. Rendering only touches in-memory state; all network and file work
//! happens in [`crate::worker`].

use crate::worker::{Event, JobId, JobResult, WorkerHandle};
use cyoa_engine::{
    batch::{self, BatchItem},
    run::{OutputMode, RunOptions},
};
use eframe::egui;
use std::{collections::VecDeque, path::PathBuf, time::Duration};

/// Python defaults (`cyoa_downloader_app/app_info.py`).
pub const DEFAULT_MAX_WORKERS: usize = 4;
pub const DEFAULT_WAIT_TIME: u64 = 60;
const MAX_EVENTS_PER_FRAME: usize = 2000;
const LOG_CAPACITY: usize = 5000;
const SETTINGS_KEY: &str = "cyoa-settings";

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Mode {
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
            Mode::IccZip
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
fn archive_policy() -> Result<cyoa_engine::icc::ArchivePolicy, String> {
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    let policy = store
        .load()
        .map(cyoa_engine::icc::ArchivePolicy::from_settings)
        .unwrap_or_default();
    if policy.strategy == "browser" {
        return Err(
            "archive strategy 'browser' is not supported by the Rust port yet (use auto, classic or smart)"
                .into(),
        );
    }
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
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            output_dir: std::env::current_dir()
                .map(|p| p.display().to_string())
                .unwrap_or_default(),
            mode: Mode::Embed,
            threads: DEFAULT_MAX_WORKERS,
            wait_time: DEFAULT_WAIT_TIME,
            net: crate::net::NetSettings::default(),
        }
    }
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
    Queued,
    Running { done: usize, total: usize },
    Done { failed_assets: usize },
    Failed(String),
    Cancelled,
}

pub struct Job {
    pub id: JobId,
    pub url: String,
    pub status: Status,
    pub current: String,
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
                }
                if let Some(j) = self.job_mut(job) {
                    j.status = Status::Running { done, total };
                    j.current = name;
                }
            }
            Event::Finished { job, result } => {
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
                self.enqueue_one(url, String::new(), Ok(self.settings.mode));
                self.url_input.clear();
            }
        }
    }

    fn enqueue_one(&mut self, url: String, filename: String, mode: Result<Mode, String>) {
        let id = self.next_id;
        self.next_id += 1;
        let net = self.net_runtime();
        // ICC jobs follow the shared `archive_strategy` setting.
        let archive = match &mode {
            Ok(m) if m.is_icc() => archive_policy().map(Some),
            _ => Ok(None),
        };
        let status = match (mode, net, archive) {
            (Err(e), _, _) | (_, Err(e), _) | (_, _, Err(e)) => Status::Failed(e),
            (Ok(mode), Ok(net), Ok(archive_auto)) => {
                let opts = RunOptions {
                    url: url.clone(),
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
                let spec = crate::worker::JobSpec { opts, manager_db };
                if self.worker.as_ref().is_some_and(|w| w.start(id, spec)) {
                    Status::Queued
                } else {
                    Status::Failed("download worker unavailable or queue full".into())
                }
            }
        };
        self.jobs.push(Job {
            id,
            url,
            status,
            current: String::new(),
        });
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
                    .add_filter("Batch list", &["txt", "csv"])
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

    /// Drop finished jobs from the visible queue (bounded history on demand).
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

    fn clear_finished(&mut self) {
        self.jobs
            .retain(|j| matches!(j.status, Status::Queued | Status::Running { .. }));
        self.selected = None;
    }

    fn controls(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label("Project URL:");
            let r = ui.add(
                egui::TextEdit::singleline(&mut self.url_input)
                    .hint_text("https://example.com/cyoa/")
                    .desired_width(ui.available_width() - 120.0),
            );
            let enter = r.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
            if ui.button("Add to queue").clicked() || enter {
                self.enqueue();
            }
            if ui
                .add_enabled(self.list_pick.is_none(), egui::Button::new("Import list…"))
                .on_hover_text(
                    ".txt (url | filename | mode) or .csv with url/filename/mode columns",
                )
                .clicked()
            {
                self.start_list_import(ui.ctx());
            }
        });
        if let Some(e) = self.url_error {
            ui.colored_label(ui.visuals().error_fg_color, e);
        }
        ui.horizontal(|ui| {
            ui.label("Output folder:");
            ui.add(
                egui::TextEdit::singleline(&mut self.settings.output_dir)
                    .desired_width(ui.available_width() - 120.0),
            );
            let picking = self.folder_pick.is_some();
            if ui
                .add_enabled(!picking, egui::Button::new("Browse…"))
                .clicked()
            {
                // The native dialog runs on its own thread so the UI keeps
                // repainting and draining worker events while it is open.
                let (tx, rx) = std::sync::mpsc::channel();
                let start = self.settings.output_dir.clone();
                let ctx = ui.ctx().clone();
                let spawned = std::thread::Builder::new()
                    .name("folder-dialog".into())
                    .spawn(move || {
                        let picked = rfd::FileDialog::new().set_directory(start).pick_folder();
                        let _ = tx.send(picked);
                        ctx.request_repaint();
                    });
                if spawned.is_ok() {
                    self.folder_pick = Some(rx);
                }
            }
            if let Some(rx) = &self.folder_pick {
                match rx.try_recv() {
                    Ok(picked) => {
                        if let Some(dir) = picked {
                            self.settings.output_dir = dir.display().to_string();
                        }
                        self.folder_pick = None;
                    }
                    Err(std::sync::mpsc::TryRecvError::Disconnected) => self.folder_pick = None,
                    Err(std::sync::mpsc::TryRecvError::Empty) => {}
                }
            }
        });
        ui.horizontal(|ui| {
            ui.label("Format:");
            ui.radio_value(&mut self.settings.mode, Mode::Embed, "Embedded JSON");
            ui.radio_value(&mut self.settings.mode, Mode::Zip, "ZIP");
            ui.radio_value(&mut self.settings.mode, Mode::Both, "Both");
            ui.radio_value(&mut self.settings.mode, Mode::IccZip, "ICC website (ZIP)");
            ui.radio_value(
                &mut self.settings.mode,
                Mode::IccFolder,
                "ICC website (folder)",
            );
            ui.radio_value(&mut self.settings.mode, Mode::PureZip, "Pure website (ZIP)");
            ui.radio_value(
                &mut self.settings.mode,
                Mode::PureFolder,
                "Pure website (folder)",
            );
            ui.radio_value(&mut self.settings.mode, Mode::CyoapZip, "CYOAP Vue (ZIP)");
            ui.radio_value(
                &mut self.settings.mode,
                Mode::CyoapFolder,
                "CYOAP Vue (folder)",
            );
            ui.separator();
            ui.label("Threads:");
            ui.add(egui::DragValue::new(&mut self.settings.threads).range(1..=64));
            ui.label("Wait (s):");
            ui.add(egui::DragValue::new(&mut self.settings.wait_time).range(0..=3600));
        });
    }

    fn running_job(&self) -> Option<&Job> {
        self.jobs
            .iter()
            .rev()
            .find(|j| matches!(j.status, Status::Running { .. }))
    }

    fn status_bar(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            if let Some(j) = self.running_job() {
                if let Status::Running { done, total } = j.status {
                    let frac = if total == 0 {
                        0.0
                    } else {
                        done as f32 / total as f32
                    };
                    ui.add(
                        egui::ProgressBar::new(frac)
                            .desired_width(240.0)
                            .text(format!("{done}/{total}")),
                    );
                    ui.label(egui::RichText::new(&j.current).monospace());
                }
            } else {
                ui.label("Idle");
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("Clear finished").clicked() {
                    self.clear_finished();
                }
                if ui.button("Cancel all").clicked()
                    && let Some(w) = &self.worker
                {
                    w.cancel_all();
                }
                let sel = self.selected.and_then(|i| self.jobs.get(i)).map(|j| j.id);
                if ui
                    .add_enabled(sel.is_some(), egui::Button::new("Cancel selected"))
                    .clicked()
                    && let (Some(id), Some(w)) = (sel, &self.worker)
                {
                    w.cancel(id);
                }
            });
        });
    }

    fn queue(&mut self, ui: &mut egui::Ui) {
        let row_h = ui.text_style_height(&egui::TextStyle::Body) + 4.0;
        let total = self.jobs.len();
        egui::ScrollArea::vertical()
            .id_salt("queue")
            .auto_shrink([false, false])
            .max_height(ui.available_height() * 0.55)
            .show_rows(ui, row_h, total, |ui, range| {
                for i in range {
                    let Some(job) = self.jobs.get(i) else {
                        continue;
                    };
                    let status = match &job.status {
                        Status::Queued => "Queued".to_owned(),
                        Status::Running { done, total } => format!("Running {done}/{total}"),
                        Status::Done { failed_assets: 0 } => "Done".to_owned(),
                        Status::Done { failed_assets } => {
                            format!("Done ({failed_assets} failed)")
                        }
                        Status::Failed(e) => format!("Failed: {e}"),
                        Status::Cancelled => "Cancelled".to_owned(),
                    };
                    let selected = self.selected == Some(i);
                    if ui
                        .selectable_label(
                            selected,
                            format!("#{}  {}  —  {status}", job.id, job.url),
                        )
                        .clicked()
                    {
                        self.selected = Some(i);
                    }
                }
            });
    }

    fn log_view(&self, ui: &mut egui::Ui) {
        let row_h = ui.text_style_height(&egui::TextStyle::Monospace);
        egui::ScrollArea::vertical()
            .id_salt("log")
            .auto_shrink([false, false])
            .stick_to_bottom(true)
            .show_rows(ui, row_h, self.log.len(), |ui, range| {
                for i in range {
                    if let Some(line) = self.log.get(i) {
                        ui.label(egui::RichText::new(line).monospace());
                    }
                }
            });
    }
}

impl eframe::App for DownloaderApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        self.drain_events();
        self.poll_list_import();
        egui::Frame::central_panel(ui.style()).show(ui, |ui| {
            self.controls(ui);
            self.settings.net.ui(ui);
            self.manager.ui(ui);
            ui.separator();
            self.status_bar(ui);
            ui.separator();
            ui.heading("Queue");
            self.queue(ui);
            ui.separator();
            ui.heading("Log");
            self.log_view(ui);
        });
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
        }
    }

    /// Frame cost with a 100k-row queue and a full log (virtualized rows).
    /// Prints the measured time; run with `--release --nocapture` for numbers.
    #[test]
    fn hundred_thousand_row_queue_frame_time() {
        let mut app = headless_app(100_000, LOG_CAPACITY);
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
                app.controls(ui);
                app.status_bar(ui);
                app.queue(ui);
                app.log_view(ui);
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
