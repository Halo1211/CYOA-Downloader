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

/// `PagedCards.PAGE_SIZE`.
const PAGE_SIZE: usize = 12;

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
    filter: &'static str,
    page: usize,
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

/// Python Batch Check Center filter (`error` also shows `unreachable`).
pub fn filter_match(filter: &str, status: &str) -> bool {
    filter == "all" || filter == status || (filter == "error" && status == "unreachable")
}

/// Python Batch Check Center summary line.
pub fn summary(results: &[HistoryCheck]) -> String {
    let count = |f: &dyn Fn(&str) -> bool| results.iter().filter(|r| f(&r.status)).count();
    format!(
        "TOTAL {} · CURRENT {} · UPDATED {} · ERROR {}",
        results.len(),
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
            filter: "all",
            page: 0,
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
                self.history_status = if results.is_empty() && self.total == 0 {
                    "No download history yet.".into()
                } else {
                    summary(&results)
                };
                self.results = results;
            }
            Err(TryRecvError::Disconnected) => self.rx = None,
            Err(TryRecvError::Empty) => {}
        }
    }

    /// Python `open_history_panel` ("Batch Check Center", 900x620): header
    /// with status, summary, progress, filters, Run Check / Queue Updated,
    /// paged cards (12 per page). The app-update check (Python
    /// `_check_updates_panel`) sits in the header as a compact Rust addition.
    pub fn ui(
        &mut self,
        ctx: &egui::Context,
        net: impl FnOnce() -> Result<crate::net::NetRuntime, String>,
    ) -> Vec<(String, String)> {
        use crate::theme;
        use egui::{Color32, CornerRadius, Frame, Margin, RichText, Stroke, vec2};
        self.poll();
        let mut requeue = Vec::new();
        let mut close = false;
        let mut start_app = false;
        let mut start_history = false;
        let builder = egui::ViewportBuilder::default()
            .with_title("Batch Check Center")
            .with_inner_size([900.0, 620.0])
            .with_min_inner_size([760.0, 500.0]);
        let busy = self.busy();
        ctx.show_viewport_immediate(
            egui::ViewportId::from_hash_of("batch_check_center"),
            builder,
            |ui, _| {
                if ui.ctx().input(|i| i.viewport().close_requested()) {
                    close = true;
                }
                let p = theme::palette(ui);
                let ctk_blue = Color32::from_rgb(0x1f, 0x6a, 0xa5);
                egui::Panel::top("bcc_header")
                    .show_separator_line(false)
                    .exact_size(60.0)
                    .frame(
                        Frame::new()
                            .fill(p.panel)
                            .inner_margin(Margin::symmetric(18, 0)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal_centered(|ui| {
                            ui.label(theme::bold("Batch Check Center", 16.0).color(p.fg));
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if theme::flat(
                                        ui,
                                        RichText::new("App update").size(12.0).color(p.fg),
                                        p.surface2,
                                        vec2(96.0, 26.0),
                                        !busy,
                                    )
                                    .clicked()
                                    {
                                        start_app = true;
                                    }
                                    if let Some(info) = &self.app_info
                                        && !info.url.is_empty()
                                        && theme::flat(
                                            ui,
                                            RichText::new("Release page")
                                                .size(12.0)
                                                .color(Color32::WHITE),
                                            ctk_blue,
                                            vec2(96.0, 26.0),
                                            true,
                                        )
                                        .clicked()
                                    {
                                        ui.ctx().open_url(egui::OpenUrl::new_tab(&info.url));
                                    }
                                    let status = if busy {
                                        "Checking…".to_owned()
                                    } else {
                                        self.app_status.replace("\n\n", " · ")
                                    };
                                    ui.label(RichText::new(status).size(12.0).color(p.muted));
                                },
                            );
                        });
                    });
                egui::CentralPanel::default()
                    .frame(
                        Frame::new()
                            .fill(p.bg)
                            .inner_margin(Margin::symmetric(14, 12)),
                    )
                    .show(ui, |ui| {
                        let line = if self.history_status.is_empty() {
                            summary(&self.results)
                        } else {
                            self.history_status.clone()
                        };
                        ui.label(RichText::new(line).size(13.0).color(p.fg));
                        ui.add_space(8.0);
                        let frac = if self.total > 0 {
                            self.done.load(Ordering::Relaxed) as f32 / self.total as f32
                        } else if self.results.is_empty() {
                            0.04
                        } else {
                            1.0
                        };
                        ui.add(
                            egui::ProgressBar::new(frac)
                                .desired_height(8.0)
                                .fill(ctk_blue)
                                .corner_radius(4.0),
                        );
                        ui.add_space(6.0);
                        let updated: Vec<(String, String)> = self
                            .results
                            .iter()
                            .filter(|r| r.status == "updated" && !r.url.is_empty())
                            .map(|r| (r.url.clone(), r.name.clone()))
                            .collect();
                        ui.horizontal(|ui| {
                            for (key, label) in [
                                ("all", "All"),
                                ("updated", "Updated"),
                                ("error", "Errors"),
                                ("current", "Current"),
                            ] {
                                let on = self.filter == key;
                                let fill = if on {
                                    Color32::from_rgb(0x3b, 0x82, 0xf6)
                                } else {
                                    p.surface2
                                };
                                let color = if on { Color32::WHITE } else { p.fg };
                                if theme::flat(
                                    ui,
                                    RichText::new(label).size(13.0).color(color),
                                    fill,
                                    vec2(85.0, 28.0),
                                    true,
                                )
                                .clicked()
                                {
                                    self.filter = key;
                                    self.page = 0;
                                }
                            }
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if theme::flat(
                                        ui,
                                        RichText::new("Queue Updated")
                                            .size(13.0)
                                            .color(Color32::WHITE),
                                        ctk_blue,
                                        vec2(125.0, 28.0),
                                        !updated.is_empty(),
                                    )
                                    .clicked()
                                    {
                                        self.history_status =
                                            format!("Queued {} updated URLs", updated.len());
                                        requeue.extend(updated.clone());
                                    }
                                    if busy {
                                        if theme::flat(
                                            ui,
                                            RichText::new("Cancel")
                                                .size(13.0)
                                                .color(Color32::WHITE),
                                            Color32::from_rgb(0xb9, 0x1c, 0x1c),
                                            vec2(112.0, 28.0),
                                            true,
                                        )
                                        .clicked()
                                        {
                                            self.cancel.cancel();
                                        }
                                    } else if theme::flat(
                                        ui,
                                        RichText::new("Run Check").size(13.0).color(Color32::WHITE),
                                        ctk_blue,
                                        vec2(112.0, 28.0),
                                        true,
                                    )
                                    .clicked()
                                    {
                                        start_history = true;
                                    }
                                },
                            );
                        });
                        ui.add_space(6.0);
                        let shown: Vec<&HistoryCheck> = self
                            .results
                            .iter()
                            .filter(|r| filter_match(self.filter, &r.status))
                            .collect();
                        let pages = shown.len().div_ceil(PAGE_SIZE).max(1);
                        self.page = self.page.min(pages - 1);
                        let start = self.page * PAGE_SIZE;
                        let end = (start + PAGE_SIZE).min(shown.len());
                        let list_h = ui.available_height() - 44.0;
                        Frame::new()
                            .fill(p.surface)
                            .corner_radius(CornerRadius::same(8))
                            .inner_margin(Margin::same(12))
                            .show(ui, |ui| {
                                ui.set_min_height(list_h);
                                egui::ScrollArea::vertical()
                                    .id_salt(("bcc", self.filter, self.page))
                                    .auto_shrink([false, false])
                                    .max_height(list_h)
                                    .show(ui, |ui| {
                                        if shown.is_empty() {
                                            ui.vertical_centered(|ui| {
                                                ui.add_space(30.0);
                                                ui.label(
                                                    RichText::new("Run the check to see results.")
                                                        .size(13.0)
                                                        .color(p.fg),
                                                );
                                            });
                                        }
                                        for r in &shown[start..end] {
                                            let color = match r.status.as_str() {
                                                "current" => Color32::from_rgb(0x04, 0x78, 0x57),
                                                "updated" => Color32::from_rgb(0x25, 0x63, 0xeb),
                                                _ => Color32::from_rgb(0xdc, 0x26, 0x26),
                                            };
                                            Frame::new()
                                                .fill(p.surface)
                                                .stroke(Stroke::new(1.0, p.border))
                                                .corner_radius(CornerRadius::same(10))
                                                .inner_margin(Margin::symmetric(12, 10))
                                                .show(ui, |ui| {
                                                    ui.set_width(ui.available_width());
                                                    ui.horizontal(|ui| {
                                                        let w = ui.available_width() - 80.0;
                                                        ui.vertical(|ui| {
                                                            ui.set_width(w);
                                                            let name = if r.name.is_empty() {
                                                                &r.url
                                                            } else {
                                                                &r.name
                                                            };
                                                            ui.label(
                                                                theme::bold(
                                                                    name.chars()
                                                                        .take(110)
                                                                        .collect::<String>(),
                                                                    12.0,
                                                                )
                                                                .color(color),
                                                            );
                                                            let why = if r.reason.is_empty() {
                                                                &r.status
                                                            } else {
                                                                &r.reason
                                                            };
                                                            ui.label(
                                                                RichText::new(format!(
                                                                    "{}\n{}",
                                                                    r.url
                                                                        .chars()
                                                                        .take(120)
                                                                        .collect::<String>(),
                                                                    why.chars()
                                                                        .take(120)
                                                                        .collect::<String>()
                                                                ))
                                                                .monospace()
                                                                .size(9.0)
                                                                .color(p.muted),
                                                            );
                                                        });
                                                        if ["updated", "error", "unreachable"]
                                                            .contains(&r.status.as_str())
                                                            && theme::flat(
                                                                ui,
                                                                RichText::new("Queue")
                                                                    .size(12.0)
                                                                    .color(Color32::WHITE),
                                                                ctk_blue,
                                                                vec2(68.0, 28.0),
                                                                true,
                                                            )
                                                            .clicked()
                                                        {
                                                            requeue.push((
                                                                r.url.clone(),
                                                                String::new(),
                                                            ));
                                                            self.history_status =
                                                                "Queued for download".into();
                                                        }
                                                    });
                                                });
                                            ui.add_space(8.0);
                                        }
                                    });
                            });
                        ui.add_space(6.0);
                        ui.horizontal(|ui| {
                            if theme::flat(
                                ui,
                                RichText::new("←").size(14.0).color(Color32::WHITE),
                                ctk_blue,
                                vec2(50.0, 28.0),
                                self.page > 0,
                            )
                            .clicked()
                            {
                                self.page -= 1;
                            }
                            let label = if shown.is_empty() {
                                "0–0 / 0".to_owned()
                            } else {
                                format!("{}–{} / {}", start + 1, end, shown.len())
                            };
                            let w = ui.available_width() - 60.0;
                            ui.allocate_ui_with_layout(
                                vec2(w, 28.0),
                                egui::Layout::centered_and_justified(egui::Direction::LeftToRight),
                                |ui| {
                                    ui.label(RichText::new(label).size(13.0).color(p.muted));
                                },
                            );
                            if theme::flat(
                                ui,
                                RichText::new("→").size(14.0).color(Color32::WHITE),
                                ctk_blue,
                                vec2(50.0, 28.0),
                                self.page + 1 < pages,
                            )
                            .clicked()
                            {
                                self.page += 1;
                            }
                        });
                    });
            },
        );
        if close {
            self.open = false;
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
        assert!(
            super::filter_match("error", "unreachable")
                && !super::filter_match("current", "updated")
        );
        assert_eq!(
            super::summary(&[r("current"), r("updated"), r("error"), r("unreachable")]),
            "TOTAL 4 · CURRENT 1 · UPDATED 1 · ERROR 2"
        );
    }
}
