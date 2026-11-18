//! eframe UI. Rendering only touches in-memory state; all network and file work
//! happens in [`crate::worker`].

use crate::worker::{Event, JobId, JobResult, WorkerHandle};
use cyoa_engine::run::{OutputMode, RunOptions};
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
}

impl From<Mode> for OutputMode {
    fn from(m: Mode) -> Self {
        match m {
            Mode::Embed => OutputMode::Embed,
            Mode::Zip => OutputMode::Zip,
            Mode::Both => OutputMode::Both,
        }
    }
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Settings {
    pub output_dir: String,
    pub mode: Mode,
    pub threads: usize,
    pub wait_time: u64,
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
}

impl DownloaderApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let settings = cc
            .storage
            .and_then(|s| eframe::get_value(s, SETTINGS_KEY))
            .unwrap_or_default();
        let ctx = cc.egui_ctx.clone();
        let mut log = RingLog::new(LOG_CAPACITY);
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
                let id = self.next_id;
                self.next_id += 1;
                let opts = RunOptions {
                    url: url.clone(),
                    output_dir: PathBuf::from(&self.settings.output_dir),
                    mode: self.settings.mode.into(),
                    workers: self.settings.threads,
                    wait_time: self.settings.wait_time,
                };
                let sent = self.worker.as_ref().is_some_and(|w| w.start(id, opts));
                self.jobs.push(Job {
                    id,
                    url,
                    status: if sent {
                        Status::Queued
                    } else {
                        Status::Failed("download worker unavailable".into())
                    },
                    current: String::new(),
                });
                self.url_input.clear();
            }
        }
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
        egui::Frame::central_panel(ui.style()).show(ui, |ui| {
            self.controls(ui);
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
