//! Python "Pre-flight Check" window (`_preview_queue`): probes every queued
//! URL on a background thread and offers "▶ Proceed with Download".

use crate::theme::{self, bold};
use cyoa_engine::preflight::Preflight;
use eframe::egui::{self, Color32, RichText, vec2};
use std::sync::mpsc::{Receiver, TryRecvError};
use tokio_util::sync::CancellationToken;

enum Msg {
    Probing(usize, String),
    Row(String, Preflight),
    Done,
}

pub struct PreflightPanel {
    pub open: bool,
    total: usize,
    status: String,
    progress: f32,
    rows: Vec<(String, Preflight)>,
    done: bool,
    rx: Option<Receiver<Msg>>,
    cancel: CancellationToken,
}

/// Python summary `✓ {ok}  ⚠ {warn}  ✗ {fail}  dari {n} URL`.
pub fn summary(rows: &[(String, Preflight)], total: usize) -> String {
    let count = |k: usize| rows.iter().filter(|(_, r)| r.kind() == k).count();
    format!(
        "✓ {}  ⚠ {}  ✗ {}  dari {total} URL",
        count(0),
        count(1),
        count(2)
    )
}

impl PreflightPanel {
    pub fn start(urls: Vec<String>, net: crate::net::NetRuntime, ctx: &egui::Context) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        let cancel = CancellationToken::new();
        let (c, repaint, total) = (cancel.clone(), ctx.clone(), urls.len());
        let _ = std::thread::Builder::new()
            .name("preflight".into())
            .spawn(move || {
                let Ok(rt) = tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(2)
                    .enable_all()
                    .build()
                else {
                    let _ = tx.send(Msg::Done);
                    return;
                };
                let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
                    policy: cyoa_net::policy::DestinationPolicy {
                        origin: None,
                        allow_internal: cyoa_core::allow_internal_hosts(),
                    },
                    proxy: net.proxy,
                    dns: net.dns,
                    vpn: net.vpn,
                    ..Default::default()
                });
                for (i, url) in urls.into_iter().enumerate() {
                    if c.is_cancelled() {
                        return;
                    }
                    let _ = tx.send(Msg::Probing(i, url.clone()));
                    repaint.request_repaint();
                    let r = match &client {
                        Ok(cl) => {
                            rt.block_on(cyoa_engine::preflight::preflight_probe(cl, &url, &c))
                        }
                        Err(e) => Preflight {
                            label: "✗ ERROR",
                            color: 0xef4444,
                            detail: e.to_string().chars().take(40).collect(),
                        },
                    };
                    let _ = tx.send(Msg::Row(url, r));
                    repaint.request_repaint();
                }
                let _ = tx.send(Msg::Done);
                repaint.request_repaint();
            });
        Self {
            open: true,
            total,
            status: String::new(),
            progress: 0.0,
            rows: Vec::new(),
            done: false,
            rx: Some(rx),
            cancel,
        }
    }

    fn poll(&mut self) {
        let Some(rx) = &self.rx else { return };
        loop {
            match rx.try_recv() {
                Ok(Msg::Probing(i, url)) => {
                    let short: String = url.chars().take(50).collect();
                    self.status = format!("[{}/{}] Probing: {short}…", i + 1, self.total);
                    self.progress = (i + 1) as f32 / self.total.max(1) as f32;
                }
                Ok(Msg::Row(url, r)) => self.rows.push((url, r)),
                Ok(Msg::Done) => {
                    self.status = "Probe selesai.".into();
                    self.progress = 1.0;
                    self.done = true;
                    self.rx = None;
                    return;
                }
                Err(TryRecvError::Empty) => return,
                Err(TryRecvError::Disconnected) => {
                    self.rx = None;
                    return;
                }
            }
        }
    }

    /// Returns true when "▶ Proceed with Download" was pressed.
    pub fn ui(&mut self, ctx: &egui::Context) -> bool {
        self.poll();
        let mut proceed = false;
        let mut open = true;
        let builder = egui::ViewportBuilder::default()
            .with_title("Pre-flight Check")
            .with_inner_size([760.0, 480.0]);
        ctx.show_viewport_immediate(
            egui::ViewportId::from_hash_of("queue_preview"),
            builder,
            |ui, _| {
                if ui.ctx().input(|i| i.viewport().close_requested()) {
                    open = false;
                }
                let p = theme::palette(ui);
                // Python sets no fg_color here: the CTk default window grey.
                let win_bg = if ui.visuals().dark_mode {
                    Color32::from_rgb(0x24, 0x24, 0x24)
                } else {
                    Color32::from_rgb(0xeb, 0xeb, 0xeb)
                };
                egui::CentralPanel::default()
                    .frame(
                        egui::Frame::new()
                            .fill(win_bg)
                            .inner_margin(egui::Margin::same(16)),
                    )
                    .show(ui, |ui| {
                        ui.label(bold("Pre-flight Check", 14.0).color(p.fg));
                        ui.label(
                            RichText::new("Probing URLs sebelum download dimulai…")
                                .size(11.0)
                                .color(p.muted),
                        );
                        ui.add(egui::ProgressBar::new(self.progress).desired_height(5.0));
                        ui.label(RichText::new(&self.status).size(10.0).color(p.muted));
                        let footer = 40.0;
                        ui.add_space(8.0);
                        let list_h = ui.available_height() - footer;
                        egui::Frame::new()
                            .fill(p.bg)
                            .corner_radius(egui::CornerRadius::same(8))
                            .inner_margin(egui::Margin::same(8))
                            .show(ui, |ui| {
                                egui::ScrollArea::vertical()
                                    .auto_shrink([false, false])
                                    .max_height(list_h - 16.0)
                                    .show(ui, |ui| {
                                        for (i, (url, r)) in self.rows.iter().enumerate() {
                                            let bg = if i % 2 == 0 { p.surface } else { p.bg };
                                            egui::Frame::new()
                                                .fill(bg)
                                                .corner_radius(egui::CornerRadius::same(4))
                                                .inner_margin(egui::Margin::symmetric(8, 5))
                                                .show(ui, |ui| {
                                                    ui.set_width(ui.available_width());
                                                    ui.horizontal(|ui| {
                                                        let c = Color32::from_rgb(
                                                            (r.color >> 16) as u8,
                                                            (r.color >> 8) as u8,
                                                            r.color as u8,
                                                        );
                                                        ui.add_sized(
                                                            vec2(90.0, 16.0),
                                                            egui::Label::new(
                                                                bold(r.label, 10.0).color(c),
                                                            ),
                                                        );
                                                        let short: String =
                                                            url.chars().take(65).collect();
                                                        let ell = if url.chars().count() > 65 {
                                                            "…"
                                                        } else {
                                                            ""
                                                        };
                                                        ui.label(
                                                            RichText::new(format!("{short}{ell}"))
                                                                .monospace()
                                                                .size(9.0)
                                                                .color(p.muted),
                                                        );
                                                        ui.with_layout(
                                                            egui::Layout::right_to_left(
                                                                egui::Align::Center,
                                                            ),
                                                            |ui| {
                                                                ui.label(
                                                                    RichText::new(&r.detail)
                                                                        .size(9.0)
                                                                        .color(p.muted2),
                                                                );
                                                            },
                                                        );
                                                    });
                                                });
                                        }
                                    });
                            });
                        ui.add_space(6.0);
                        ui.horizontal(|ui| {
                            if theme::flat(
                                ui,
                                bold("▶ Proceed with Download", 11.0).color(Color32::WHITE),
                                p.accent,
                                vec2(0.0, 28.0),
                                self.done,
                            )
                            .clicked()
                            {
                                proceed = true;
                            }
                            if theme::flat(
                                ui,
                                RichText::new("Tutup").color(p.muted),
                                p.surface2,
                                vec2(80.0, 28.0),
                                true,
                            )
                            .clicked()
                            {
                                self.open = false;
                            }
                            if self.done {
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        ui.label(
                                            RichText::new(summary(&self.rows, self.total))
                                                .size(11.0)
                                                .color(p.muted),
                                        );
                                    },
                                );
                            }
                        });
                    });
            },
        );
        self.open = self.open && open && !proceed;
        if !self.open {
            self.cancel.cancel();
        }
        proceed
    }
}
