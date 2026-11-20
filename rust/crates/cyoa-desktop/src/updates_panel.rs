//! F-052 GUI: app update check and history batch update check (Python
//! `_check_updates_panel` / `_batch_update_panel`). Network work runs on a
//! background thread with its own small Tokio runtime.

use cyoa_engine::updates::{HistoryCheck, UpdateInfo};
use eframe::egui;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
    mpsc::{Receiver, TryRecvError},
};
use tokio_util::sync::CancellationToken;

enum Msg {
    App(Result<Option<UpdateInfo>, String>),
    History(Vec<HistoryCheck>),
}

pub struct UpdatesPanel {
    pub open: bool,
    app_status: String,
    app_info: Option<UpdateInfo>,
    history_status: String,
    results: Vec<HistoryCheck>,
    rx: Option<Receiver<Msg>>,
    done: Arc<AtomicUsize>,
    total: usize,
    cancel: CancellationToken,
}

fn client(net: &crate::net::NetRuntime) -> Result<cyoa_net::client::FetchClient, String> {
    cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        policy: cyoa_net::policy::DestinationPolicy {
            origin: None,
            allow_internal: cyoa_core::allow_internal_hosts(),
        },
        proxy: net.proxy.clone(),
        dns: net.dns.clone(),
        vpn: net.vpn.clone(),
        ..Default::default()
    })
    .map_err(|e| e.to_string())
}

fn run_bg<F, Fut>(ctx: &egui::Context, work: F) -> Option<Receiver<Msg>>
where
    F: FnOnce() -> Fut + Send + 'static,
    Fut: std::future::Future<Output = Msg>,
{
    let (tx, rx) = std::sync::mpsc::channel();
    let ctx = ctx.clone();
    std::thread::Builder::new()
        .name("updates".into())
        .spawn(move || {
            let msg = match tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
            {
                Ok(rt) => rt.block_on(work()),
                Err(e) => Msg::App(Err(e.to_string())),
            };
            let _ = tx.send(msg);
            ctx.request_repaint();
        })
        .ok()
        .map(|_| rx)
}

/// Python result summary line.
pub fn summary(results: &[HistoryCheck]) -> String {
    let count = |f: &dyn Fn(&str) -> bool| results.iter().filter(|r| f(&r.status)).count();
    format!(
        "✅ {} current  |  🔄 {} updated  |  ❌ {} errors",
        count(&|s| s == "current"),
        count(&|s| s == "updated"),
        count(&|s| s == "error" || s == "unreachable")
    )
}

impl UpdatesPanel {
    pub fn new() -> Self {
        Self {
            open: true,
            app_status: format!("CYOA Downloader v{}", cyoa_core::app_version()),
            app_info: None,
            history_status: String::new(),
            results: Vec::new(),
            rx: None,
            done: Arc::new(AtomicUsize::new(0)),
            total: 0,
            cancel: CancellationToken::new(),
        }
    }

    fn busy(&self) -> bool {
        self.rx.is_some()
    }

    fn poll(&mut self) {
        let Some(rx) = &self.rx else { return };
        match rx.try_recv() {
            Ok(Msg::App(r)) => {
                self.rx = None;
                let v = cyoa_core::app_version();
                match r {
                    Ok(Some(info)) => {
                        self.app_status =
                            format!("Update available: v{} (current v{v})", info.version);
                        self.app_info = Some(info);
                    }
                    Ok(None) => {
                        self.app_status = format!("CYOA Downloader v{v}\n\nAlready up to date ✅")
                    }
                    Err(e) => self.app_status = format!("CYOA Downloader v{v}\n\n{e}"),
                }
            }
            Ok(Msg::History(results)) => {
                self.rx = None;
                self.history_status = summary(&results);
                self.results = results;
            }
            Err(TryRecvError::Disconnected) => self.rx = None,
            Err(TryRecvError::Empty) => {}
        }
    }

    /// Returns URLs the user chose to re-download.
    pub fn ui(
        &mut self,
        ctx: &egui::Context,
        net: impl FnOnce() -> Result<crate::net::NetRuntime, String>,
    ) -> Vec<(String, String)> {
        self.poll();
        let mut requeue = Vec::new();
        let mut open = self.open;
        let mut start_app = false;
        let mut start_history = false;
        egui::Window::new("Updates")
            .id(egui::Id::unique("updates_panel"))
            .open(&mut open)
            .default_size([620.0, 480.0])
            .show(ctx, |ui| {
                ui.heading("App update");
                ui.label(&self.app_status);
                if let Some(info) = &self.app_info {
                    ui.label(
                        egui::RichText::new(info.notes.chars().take(300).collect::<String>())
                            .weak(),
                    );
                    if !info.url.is_empty() && ui.button("Open Release Page").clicked() {
                        ctx.open_url(egui::OpenUrl::new_tab(&info.url));
                    }
                }
                if ui
                    .add_enabled(!self.busy(), egui::Button::new("Check for app update"))
                    .clicked()
                {
                    start_app = true;
                }
                ui.separator();
                ui.heading("Downloaded CYOAs");
                ui.horizontal(|ui| {
                    if ui
                        .add_enabled(!self.busy(), egui::Button::new("Check history for updates"))
                        .clicked()
                    {
                        start_history = true;
                    }
                    if self.busy() && ui.button("Cancel").clicked() {
                        self.cancel.cancel();
                    }
                });
                if self.busy() && self.total > 0 {
                    let d = self.done.load(Ordering::Relaxed);
                    ui.add(
                        egui::ProgressBar::new(d as f32 / self.total as f32)
                            .text(format!("{d}/{}", self.total)),
                    );
                }
                ui.label(&self.history_status);
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        for r in self.results.iter().filter(|r| r.status == "updated") {
                            ui.horizontal(|ui| {
                                ui.vertical(|ui| {
                                    let name = if r.name.is_empty() {
                                        r.url.chars().take(50).collect()
                                    } else {
                                        r.name.clone()
                                    };
                                    ui.strong(format!("🔄 {name}"));
                                    ui.label(egui::RichText::new(&r.reason).weak());
                                });
                                if ui.button("Re-download").clicked() {
                                    requeue.push((r.url.clone(), r.name.clone()));
                                }
                            });
                        }
                    });
            });
        self.open = open;
        if !self.open {
            self.cancel.cancel();
        }
        if start_app || start_history {
            match net() {
                Err(e) => self.app_status = e,
                Ok(net) => {
                    self.cancel = CancellationToken::new();
                    let cancel = self.cancel.clone();
                    if start_app {
                        self.app_status = "Checking…".into();
                        self.rx = run_bg(ctx, move || async move {
                            Msg::App(match client(&net) {
                                Ok(c) => {
                                    cyoa_engine::updates::check_for_app_updates(&c, &cancel).await
                                }
                                Err(e) => Err(format!("Update check failed: {e}")),
                            })
                        });
                    } else {
                        let history = cyoa_storage::history::default_history_path()
                            .map(cyoa_storage::history::load_history)
                            .unwrap_or_default();
                        self.total = cyoa_engine::updates::checkable_entries(&history).len();
                        self.done.store(0, Ordering::Relaxed);
                        self.history_status =
                            format!("Checking {} previously downloaded CYOAs…", self.total);
                        self.results.clear();
                        let done = self.done.clone();
                        let repaint = ctx.clone();
                        self.rx = run_bg(ctx, move || async move {
                            let c = match client(&net) {
                                Ok(c) => c,
                                Err(_) => return Msg::History(Vec::new()),
                            };
                            let progress = move |d: usize, _t: usize| {
                                done.store(d, Ordering::Relaxed);
                                repaint.request_repaint();
                            };
                            Msg::History(
                                cyoa_engine::updates::batch_check_updates(
                                    &c, &history, 4, &cancel, &progress,
                                )
                                .await,
                            )
                        });
                    }
                }
            }
        }
        requeue
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn summary_counts() {
        let r = |s: &str| cyoa_engine::updates::HistoryCheck {
            url: "u".into(),
            name: String::new(),
            status: s.into(),
            reason: String::new(),
            date: String::new(),
        };
        assert_eq!(
            super::summary(&[r("current"), r("updated"), r("error"), r("unreachable")]),
            "✅ 1 current  |  🔄 1 updated  |  ❌ 2 errors"
        );
    }
}
