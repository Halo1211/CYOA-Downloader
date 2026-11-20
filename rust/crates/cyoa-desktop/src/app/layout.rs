//! Main window layout matching the Python GUI (`_setup_ui_base` +
//! `_v466_setup_ui`): titlebar, mode sidebar, Input section with option rows,
//! queue, primary action row, tool pills, progress card and LOG.

use super::{DownloaderApp, Job, Status};
use crate::theme::{self, MODES, Palette, bold};
use eframe::egui::{self, Color32, CornerRadius, Frame, Margin, RichText, Stroke, vec2};

const NOT_PORTED: &str = "Not ported to the Rust version yet";

/// Python `DNS_PRESETS` (runtime/state.py), in order.
pub const DNS_PRESETS: &[(&str, &str)] = &[
    ("System (default)", ""),
    ("Cloudflare 1.1.1.1 (UDP)", "1.1.1.1"),
    ("Cloudflare 1.0.0.1 (UDP)", "1.0.0.1"),
    ("Cloudflare (DoH)", "https://cloudflare-dns.com/dns-query"),
    ("Cloudflare (DoT)", "tls://one.one.one.one"),
    ("Google 8.8.8.8 (UDP)", "8.8.8.8"),
    ("Google 8.8.4.4 (UDP)", "8.8.4.4"),
    ("Google (DoH)", "https://dns.google/dns-query"),
    ("Quad9 9.9.9.9 (UDP)", "9.9.9.9"),
    ("Quad9 (DoH)", "https://dns.quad9.net/dns-query"),
    ("Quad9 (DoT)", "tls://dns.quad9.net"),
    (
        "BebasDNS Default (DoH)",
        "https://dns.bebasid.com/dns-query",
    ),
    (
        "BebasDNS Security (DoH)",
        "https://security.dns.bebasid.com/dns-query",
    ),
    (
        "BebasDNS Unfiltered (DoH)",
        "https://unfiltered.dns.bebasid.com/dns-query",
    ),
    (
        "BebasDNS Family (DoH)",
        "https://family.dns.bebasid.com/dns-query",
    ),
    ("OpenDNS 208.67.222", "208.67.222.222"),
    ("AdGuard 94.140.14", "94.140.14.14"),
    ("Custom…", "__custom__"),
];

/// Python Cloudflare selector labels and saved values.
const CF_MODES: &[(&str, &str)] = &[
    ("Off", "off"),
    ("Auto", "auto"),
    ("cloudscraper", "cloudscraper"),
    ("FlareSolverr", "flaresolverr"),
];

fn status_text(job: &Job) -> String {
    match &job.status {
        Status::Pending => "Pending".to_owned(),
        Status::Queued => "Queued".to_owned(),
        Status::Running { done, total } => format!("Running {done}/{total}"),
        Status::Done { failed_assets: 0 } => "Done".to_owned(),
        Status::Done { failed_assets } => format!("Done ({failed_assets} failed)"),
        Status::Failed(e) => format!("Failed: {e}"),
        Status::Cancelled => "Cancelled".to_owned(),
    }
}

fn open_path(path: &std::path::Path) -> std::io::Result<()> {
    #[cfg(windows)]
    let mut cmd = std::process::Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = std::process::Command::new("xdg-open");
    cmd.arg(path).spawn().map(|_| ())
}

fn label(ui: &mut egui::Ui, p: &Palette, text: &str) {
    ui.label(RichText::new(text).size(10.5).color(p.muted));
}

fn entry(
    ui: &mut egui::Ui,
    text: &mut String,
    hint: &str,
    width: f32,
    height: f32,
    mono: bool,
) -> egui::Response {
    let mut te = egui::TextEdit::singleline(text)
        .hint_text(hint)
        .desired_width(width)
        .min_size(vec2(width, height))
        .margin(Margin::symmetric(8, ((height - 18.0) / 2.0).max(2.0) as i8));
    if mono {
        te = te.font(egui::FontId::monospace(10.0));
    }
    ui.add(te)
}

fn number<T: egui::emath::Numeric>(
    ui: &mut egui::Ui,
    v: &mut T,
    range: std::ops::RangeInclusive<T>,
) {
    ui.add_sized(
        vec2(46.0, 26.0),
        egui::DragValue::new(v)
            .range(range)
            .custom_formatter(|n, _| format!("{n}")),
    );
}

/// Run counters for the progress card (Python `_v46` progress model).
#[derive(Default)]
struct RunStats {
    total: usize,
    completed: usize,
    failed: usize,
    remaining: usize,
    job_index: usize,
    overall: f32,
}

fn run_stats(jobs: &[Job]) -> RunStats {
    let mut s = RunStats::default();
    let mut progress = 0.0f32;
    for (i, j) in jobs.iter().enumerate() {
        match &j.status {
            Status::Pending => continue,
            Status::Done { .. } => {
                s.completed += 1;
                progress += 1.0;
            }
            Status::Failed(_) | Status::Cancelled => {
                s.failed += 1;
                progress += 1.0;
            }
            Status::Running { done, total } => {
                s.remaining += 1;
                s.job_index = i + 1;
                if *total > 0 {
                    progress += *done as f32 / *total as f32;
                }
            }
            Status::Queued => s.remaining += 1,
        }
        s.total += 1;
    }
    if s.job_index == 0 {
        s.job_index = s.completed + s.failed;
    }
    s.overall = if s.total == 0 {
        0.0
    } else {
        progress / s.total as f32
    };
    s
}

fn current_is_custom(dns: &str) -> bool {
    !dns.is_empty() && !DNS_PRESETS.iter().any(|(_, v)| *v == dns)
}

impl DownloaderApp {
    /// Python TITLEBAR: logo, title stack, version, theme and language pills.
    pub(super) fn titlebar(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        let dark = ui.visuals().dark_mode;
        let logo = self.logo_texture(ui.ctx(), dark);
        ui.horizontal_centered(|ui| {
            ui.add_space(24.0);
            ui.add(egui::Image::new((logo.id(), vec2(42.0, 42.0))));
            ui.add_space(26.0);
            ui.vertical(|ui| {
                ui.add_space(20.0);
                ui.label(bold("CYOA Downloader", 16.0).color(p.fg));
                ui.add_space(6.0);
                ui.label(
                    RichText::new("ICC backup · serve preview · diagnostics")
                        .size(8.5)
                        .color(p.muted),
                );
            });
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.add_space(14.0);
                Frame::new()
                    .fill(p.surface)
                    .corner_radius(CornerRadius::same(6))
                    .inner_margin(Margin::same(2))
                    .show(ui, |ui| {
                        ui.spacing_mut().item_spacing.x = 2.0;
                        ui.horizontal(|ui| {
                            theme::flat(
                                ui,
                                RichText::new("EN").size(10.0).color(Color32::WHITE),
                                p.accent,
                                vec2(28.0, 26.0),
                                true,
                            );
                            ui.add_enabled(
                                false,
                                egui::Button::new(RichText::new("ID").size(10.0))
                                    .fill(Color32::TRANSPARENT)
                                    .min_size(vec2(28.0, 26.0)),
                            )
                            .on_disabled_hover_text("Indonesian UI text is not ported yet");
                        });
                    });
                ui.add_space(8.0);
                let mut changed = false;
                Frame::new()
                    .fill(p.surface)
                    .corner_radius(CornerRadius::same(6))
                    .inner_margin(Margin::same(2))
                    .show(ui, |ui| {
                        ui.spacing_mut().item_spacing.x = 2.0;
                        ui.horizontal(|ui| {
                            // right-to-left parent: iterate reversed so it reads Dark · Light · System.
                            for mode in ["System", "Light", "Dark"] {
                                let on = self.theme_mode == mode;
                                let (fill, color) = if on {
                                    (p.accent, Color32::WHITE)
                                } else {
                                    (Color32::TRANSPARENT, p.fg)
                                };
                                if theme::flat(
                                    ui,
                                    RichText::new(mode).size(10.0).color(color),
                                    fill,
                                    vec2(52.0, 26.0),
                                    true,
                                )
                                .clicked()
                                    && !on
                                {
                                    self.theme_mode = mode.to_owned();
                                    changed = true;
                                }
                            }
                        });
                    });
                ui.add_space(8.0);
                ui.label(
                    RichText::new(format!("v{}", cyoa_core::app_version()))
                        .size(10.0)
                        .color(p.muted),
                );
                if changed {
                    theme::apply_theme_mode(ui.ctx(), &self.theme_mode);
                    let m = self.theme_mode.clone();
                    self.save_shared_key("theme_mode", m.into());
                }
            });
        });
    }

    /// Python SIDEBAR: section headers, mode cards with selection bar, icon box.
    pub(super) fn sidebar(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        let dark = ui.visuals().dark_mode;
        let auto_pref = self.auto_pref.clone();
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.spacing_mut().item_spacing.y = 0.0;
                ui.add_space(8.0);
                for entry in MODES {
                    if entry.key == "__sec__" {
                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            ui.add_space(12.0);
                            ui.label(bold(entry.name, 8.0).color(p.muted2));
                        });
                        ui.add_space(0.0);
                        ui.horizontal(|ui| {
                            ui.add_space(10.0);
                            let w = ui.available_width() - 10.0;
                            let (r, _) = ui.allocate_exact_size(vec2(w, 1.0), egui::Sense::hover());
                            ui.painter().rect_filled(r, 0.0, p.separator);
                        });
                        ui.add_space(2.0);
                        continue;
                    }
                    let Some(mode) = entry.mode else { continue };
                    let selected = mode == self.settings.mode;
                    let width = ui.available_width() - 18.0;
                    let (rect, resp) =
                        ui.allocate_exact_size(vec2(width + 18.0, 48.0), egui::Sense::click());
                    let card =
                        egui::Rect::from_min_size(rect.min + vec2(8.0, 2.0), vec2(width, 46.0));
                    let painter = ui.painter();
                    if selected {
                        painter.rect_filled(card, CornerRadius::same(9), p.sel_row);
                        painter.rect_filled(
                            egui::Rect::from_min_size(card.min + vec2(0.0, 5.0), vec2(4.0, 36.0)),
                            CornerRadius::same(2),
                            p.sel_bar,
                        );
                    } else if resp.hovered() {
                        painter.rect_filled(card, CornerRadius::same(9), p.surface);
                    }
                    let icon_box =
                        egui::Rect::from_center_size(card.min + vec2(28.0, 23.0), vec2(25.0, 25.0));
                    if selected {
                        let sel_icon = if dark {
                            Color32::from_rgb(0x1e, 0x3a, 0x5f)
                        } else {
                            Color32::from_rgb(0xbf, 0xdb, 0xfe)
                        };
                        painter.rect_filled(icon_box, CornerRadius::same(7), sel_icon);
                    }
                    painter.text(
                        icon_box.center(),
                        egui::Align2::CENTER_CENTER,
                        entry.icon,
                        egui::FontId::proportional(14.0),
                        if selected {
                            p.sel_nm
                        } else {
                            Color32::from_rgb(0x94, 0xa3, 0xb8)
                        },
                    );
                    let name_color = if selected {
                        p.sel_nm
                    } else if dark {
                        Color32::from_rgb(0xe2, 0xe8, 0xf0)
                    } else {
                        p.fg
                    };
                    painter.text(
                        card.min + vec2(52.0, 15.0),
                        egui::Align2::LEFT_CENTER,
                        entry.name,
                        egui::FontId::new(10.5, egui::FontFamily::Name("bold".into())),
                        name_color,
                    );
                    painter.text(
                        card.min + vec2(52.0, 33.0),
                        egui::Align2::LEFT_CENTER,
                        theme::sidebar_desc(entry, &auto_pref),
                        egui::FontId::proportional(8.0),
                        if selected {
                            p.sel_desc
                        } else {
                            Color32::from_rgb(0x94, 0xa3, 0xb8)
                        },
                    );
                    if resp.clicked() && !selected {
                        self.settings.mode = mode;
                        self.log.push(format!("Mode: {}", entry.name));
                    }
                }
                ui.add_space(8.0);
            });
    }

    /// Python MAIN input card: header, URL row, option rows, output folder.
    pub(super) fn input_section(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.spacing_mut().item_spacing = vec2(8.0, 8.0);
        ui.horizontal(|ui| {
            ui.add_space(4.0);
            ui.label(bold("Input", 12.0).color(p.accent));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let hdr = |ui: &mut egui::Ui, t: &str, w: f32| {
                    theme::flat(
                        ui,
                        bold(t, 10.0).color(p.muted),
                        p.surface2,
                        vec2(w, 26.0),
                        true,
                    )
                };
                if hdr(ui, "❔ Help / Guide", 116.0).clicked() {
                    self.show_help = true;
                }
                if hdr(ui, "🩺 Diagnostics", 112.0).clicked() {
                    self.show_diagnostics = true;
                    if self.diag_report.is_none() {
                        self.diag_report =
                            Some(cyoa_engine::diagnostics::dependency_check_report());
                    }
                }
                if hdr(ui, "© Credits", 96.0).clicked() {
                    self.show_credits = true;
                }
            });
        });
        ui.add_space(2.0);
        ui.horizontal(|ui| {
            ui.add_space(14.0);
            ui.add_sized(
                vec2(50.0, 34.0),
                egui::Label::new(bold("URL", 10.5).color(p.muted)),
            );
            let rest = ui.available_width();
            let fn_w = (rest * 0.27).clamp(160.0, 300.0);
            let url_w = (rest - fn_w - 150.0 - 100.0).max(160.0);
            let r = entry(ui, &mut self.url_input, "", url_w, 34.0, false);
            ui.add_sized(
                vec2(72.0, 34.0),
                egui::Label::new(bold("Filename", 10.5).color(p.muted)),
            );
            let r2 = entry(ui, &mut self.fn_input, "", fn_w, 34.0, false);
            let enter = (r.lost_focus() || r2.lost_focus())
                && ui.input(|i| i.key_pressed(egui::Key::Enter));
            let add = theme::flat(
                ui,
                bold("➕  Add URL", 10.5).color(Color32::WHITE),
                p.accent,
                vec2(ui.available_width(), 34.0),
                true,
            );
            if add.clicked() || enter {
                self.enqueue();
            }
        });
        if let Some(e) = self.url_error {
            ui.horizontal(|ui| {
                ui.add_space(72.0);
                ui.colored_label(p.danger_fg, e);
            });
        }
        // Row 1: numerics, proxy, DNS.
        ui.horizontal(|ui| {
            ui.add_space(14.0);
            label(ui, &p, "Threads:");
            number(ui, &mut self.settings.threads, 1..=64);
            label(ui, &p, "Retry (s):");
            number(ui, &mut self.settings.wait_time, 0..=3600);
            label(ui, &p, "BW (KB/s):");
            number(ui, &mut self.settings.bandwidth_kbps, 0.0..=1_000_000.0);
            label(ui, &p, "Proxy:");
            let before = self.settings.net.proxy.clone();
            entry(
                ui,
                &mut self.settings.net.proxy,
                "http://127.0.0.1:7890",
                160.0,
                26.0,
                true,
            );
            if self.settings.net.proxy != before {
                // Python `_apply_compact_proxy`.
                let n = &mut self.settings.net;
                let any = [&n.proxy, &n.proxy_http, &n.proxy_https]
                    .iter()
                    .any(|v| !v.trim().is_empty());
                n.proxy_mode = if any { "manual" } else { "disabled" }.into();
            }
            label(ui, &p, "DNS:");
            let current = DNS_PRESETS
                .iter()
                .find(|(_, v)| *v == self.settings.net.dns && *v != "__custom__")
                .map(|(k, _)| *k)
                .unwrap_or(if self.settings.net.dns.is_empty() {
                    "System (default)"
                } else {
                    "Custom…"
                });
            let mut picked = None;
            egui::ComboBox::from_id_salt("dns_preset")
                .selected_text(RichText::new(current).size(9.5).color(p.muted))
                .width(200.0)
                .show_ui(ui, |ui| {
                    for (name, value) in DNS_PRESETS {
                        if ui.selectable_label(*name == current, *name).clicked() {
                            picked = Some(*value);
                        }
                    }
                });
            match picked {
                Some("__custom__") => self.dns_custom = true,
                Some(v) => {
                    self.dns_custom = false;
                    self.settings.net.dns = v.to_owned();
                    self.settings.net.dns_protocol = if v.is_empty() {
                        "system".into()
                    } else {
                        cyoa_net::dns::infer_dns_protocol(v, None)
                    };
                }
                None => {}
            }
        });
        if self.dns_custom || current_is_custom(&self.settings.net.dns) {
            ui.horizontal(|ui| {
                ui.add_space(14.0);
                label(ui, &p, "Custom DNS:");
                let before = self.settings.net.dns.clone();
                entry(
                    ui,
                    &mut self.settings.net.dns,
                    "1.1.1.1 or https://dns.example/dns-query",
                    260.0,
                    26.0,
                    true,
                );
                if self.settings.net.dns != before {
                    let v = self.settings.net.dns.trim().to_owned();
                    self.settings.net.dns_protocol = if v.is_empty() {
                        "system".into()
                    } else {
                        cyoa_net::dns::infer_dns_protocol(&v, None)
                    };
                }
            });
        }
        // Row 2: checkboxes and Cloudflare.
        ui.horizontal(|ui| {
            ui.add_space(14.0);
            if theme::checkbox(ui, &p, &mut self.settings.fonts, "Fonts", p.accent, true).changed()
            {
                let state = if self.settings.fonts {
                    "enabled"
                } else {
                    "disabled"
                };
                self.log.push(format!("[Feature] Font download: {state}"));
            }
            ui.add_space(48.0);
            if theme::checkbox(
                ui,
                &p,
                &mut self.settings.analyse_fonts,
                "Font Analysis",
                p.accent,
                true,
            )
            .changed()
            {
                let state = if self.settings.analyse_fonts {
                    "enabled"
                } else {
                    "disabled"
                };
                self.log.push(format!("[Feature] Font analysis: {state}"));
            }
            ui.add_space(20.0);
            label(ui, &p, "Cloudflare:");
            let current = CF_MODES
                .iter()
                .find(|(_, v)| *v == self.cf_mode)
                .map_or("Auto", |(l, _)| *l);
            let mut picked = None;
            egui::ComboBox::from_id_salt("cf_mode")
                .selected_text(RichText::new(current).size(9.5).color(p.muted))
                .width(125.0)
                .show_ui(ui, |ui| {
                    for (l, v) in CF_MODES {
                        if ui.selectable_label(*l == current, *l).clicked() {
                            picked = Some(*v);
                        }
                    }
                });
            if let Some(v) = picked {
                self.cf_mode = v.to_owned();
                self.save_shared_key("cloudflare_mode", v.into());
            }
            ui.add_space(14.0);
            if theme::checkbox(ui, &p, &mut self.settings.http2, "HTTP/2", p.accent, true).changed()
            {
                let v = self.settings.http2;
                self.save_shared_key("http2_enabled", v.into());
            }
            ui.add_space(40.0);
            theme::checkbox(
                ui,
                &p,
                &mut self.settings.ytdlp,
                "YT Audio",
                Color32::from_rgb(0xef, 0x44, 0x44),
                true,
            );
            ui.add_space(40.0);
            let mut off = false;
            theme::checkbox(ui, &p, &mut off, "🤖 AI Assist", p.accent, false)
                .on_hover_text(NOT_PORTED);
        });
        // Row 3: list actions (right aligned).
        ui.horizontal(|ui| {
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                theme::flat(ui, RichText::new("Export List…").size(10.5).color(p.muted), p.surface2, vec2(208.0, 26.0), false)
                    .on_disabled_hover_text(NOT_PORTED);
                if theme::flat(ui, RichText::new("Import List…").size(10.5).color(p.muted), p.surface2, vec2(208.0, 26.0), self.list_pick.is_none())
                    .on_hover_text(".txt (url | filename | mode), or .csv/.xlsx/.xls with url/filename/mode columns")
                    .clicked()
                {
                    self.start_list_import(ui.ctx());
                }
            });
        });
        // Output folder.
        ui.horizontal(|ui| {
            ui.add_space(24.0);
            ui.add_sized(
                vec2(100.0, 30.0),
                egui::Label::new(RichText::new("Output folder:").size(10.5).color(p.muted)),
            );
            let w = ui.available_width() - 120.0 - 8.0;
            entry(ui, &mut self.settings.output_dir, "", w, 30.0, false);
            let picking = self.folder_pick.is_some();
            if theme::flat(
                ui,
                RichText::new("Browse…").size(10.5).color(p.muted),
                p.surface2,
                vec2(ui.available_width(), 30.0),
                !picking,
            )
            .clicked()
            {
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
        ui.add_space(4.0);
    }

    /// Queue header and rows (fills the space between input and actions).
    pub(super) fn queue_section(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.add_space(10.0);
        ui.horizontal(|ui| {
            ui.add_space(20.0);
            ui.label(bold(format!("QUEUE — {} ITEMS", self.jobs.len()), 10.5).color(p.muted));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.add_space(12.0);
                if theme::flat(
                    ui,
                    RichText::new("Clear All").size(10.5).color(p.muted),
                    p.surface2,
                    vec2(72.0, 26.0),
                    true,
                )
                .clicked()
                {
                    self.jobs
                        .retain(|j| matches!(j.status, Status::Running { .. } | Status::Queued));
                    self.selected = None;
                }
                let sel = self
                    .selected
                    .and_then(|i| self.jobs.get(i).map(|j| (i, j.id, j.status.clone())));
                if theme::flat(
                    ui,
                    RichText::new("Remove").size(10.5).color(p.muted),
                    p.surface2,
                    vec2(66.0, 26.0),
                    sel.is_some(),
                )
                .clicked()
                    && let Some((i, id, status)) = sel
                {
                    if matches!(status, Status::Running { .. } | Status::Queued) {
                        if let Some(w) = &self.worker {
                            w.cancel(id);
                        }
                    } else {
                        self.jobs.remove(i);
                        self.selected = None;
                    }
                }
            });
        });
        let height = (ui.available_height() - 6.0).max(40.0);
        self.queue(ui, height);
    }

    /// Queue rows with Python mode badges (virtualized).
    pub(super) fn queue(&mut self, ui: &mut egui::Ui, height: f32) {
        let p = theme::palette(ui);
        let row_h = 30.0;
        let total = self.jobs.len();
        egui::ScrollArea::vertical()
            .id_salt("queue")
            .auto_shrink([false, false])
            .max_height(height)
            .show_rows(ui, row_h, total, |ui, range| {
                for i in range {
                    let Some(job) = self.jobs.get(i) else {
                        continue;
                    };
                    let selected = self.selected == Some(i);
                    let (rect, resp) = ui.allocate_exact_size(
                        vec2(ui.available_width(), row_h - 2.0),
                        egui::Sense::click(),
                    );
                    let rect = rect.shrink2(vec2(12.0, 0.0));
                    let painter = ui.painter();
                    if selected {
                        painter.rect_filled(rect, CornerRadius::same(6), p.sel_row);
                    } else if resp.hovered() {
                        painter.rect_filled(rect, CornerRadius::same(6), p.surface);
                    }
                    let mut x = rect.min.x + 8.0;
                    if let Ok(mode) = &job.mode {
                        let (bg, fg) = theme::badge(*mode);
                        let label = theme::mode_entry(*mode).name;
                        let galley = painter.layout_no_wrap(
                            label.to_owned(),
                            egui::FontId::proportional(8.5),
                            fg,
                        );
                        let badge = egui::Rect::from_min_size(
                            egui::pos2(x, rect.center().y - 9.0),
                            vec2(galley.size().x + 14.0, 18.0),
                        );
                        painter.rect_filled(badge, CornerRadius::same(6), bg);
                        painter.galley(
                            badge.min + vec2(7.0, 9.0 - galley.size().y / 2.0),
                            galley,
                            fg,
                        );
                        x = badge.max.x + 8.0;
                    }
                    let name = if job.filename.is_empty() {
                        job.url.clone()
                    } else {
                        format!("{} — {}", job.filename, job.url)
                    };
                    painter.text(
                        egui::pos2(x, rect.center().y),
                        egui::Align2::LEFT_CENTER,
                        name,
                        egui::FontId::proportional(10.0),
                        p.fg,
                    );
                    let color = match job.status {
                        Status::Done { failed_assets: 0 } => p.srv_fg,
                        Status::Failed(_) => p.danger_fg,
                        Status::Running { .. } | Status::Queued => p.accent,
                        _ => p.muted,
                    };
                    painter.text(
                        egui::pos2(rect.max.x - 8.0, rect.center().y),
                        egui::Align2::RIGHT_CENTER,
                        status_text(job),
                        egui::FontId::proportional(9.0),
                        color,
                    );
                    if resp.clicked() {
                        self.selected = Some(i);
                    }
                }
            });
    }

    /// Python Row A: Download All, Preview, Pause, Start Serve, Folder.
    pub(super) fn action_row(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.add_space(10.0);
        ui.horizontal(|ui| {
            ui.add_space(18.0);
            ui.spacing_mut().item_spacing.x = 8.0;
            let h = 32.0;
            if theme::flat(
                ui,
                bold("▶  Download All", 11.0).color(Color32::WHITE),
                p.accent,
                vec2(126.0, h),
                true,
            )
            .clicked()
            {
                self.download_all();
            }
            theme::flat(
                ui,
                RichText::new("🔍 Preview").size(10.5).color(p.muted),
                p.surface2,
                vec2(90.0, h),
                false,
            )
            .on_disabled_hover_text(NOT_PORTED);
            theme::flat(
                ui,
                RichText::new("⏸ Pause").size(10.5).color(p.fg),
                p.surface2,
                vec2(86.0, h),
                false,
            )
            .on_disabled_hover_text(NOT_PORTED);
            if self.preview.is_some() {
                if theme::flat(
                    ui,
                    RichText::new("■ Stop Serve").size(10.5).color(p.srv_fg),
                    p.surface2,
                    vec2(112.0, h),
                    true,
                )
                .clicked()
                {
                    self.preview = None;
                    self.log.push("[Server] Preview stopped.".to_owned());
                }
            } else if theme::flat(
                ui,
                RichText::new("⚡ Start Serve").size(10.5).color(p.srv_fg),
                p.surface2,
                vec2(112.0, h),
                self.last_preview.is_some(),
            )
            .on_hover_text("Serve the last website folder on 127.0.0.1 and open it")
            .clicked()
            {
                self.start_preview();
            }
            if theme::flat(
                ui,
                RichText::new("📁 Folder").size(10.5).color(p.muted),
                p.surface2,
                vec2(84.0, h),
                true,
            )
            .clicked()
                && let Err(e) = open_path(std::path::Path::new(&self.settings.output_dir))
            {
                self.log.push(format!("Could not open folder: {e}"));
            }
        });
        ui.add_space(10.0);
    }

    /// Python Row B tool strip.
    pub(super) fn tool_strip(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal(|ui| {
            ui.add_space(12.0);
            ui.spacing_mut().item_spacing.x = 2.0;
            theme::pill(ui, "↺", "Retry Assets", p.retry_asset, false)
                .on_disabled_hover_text(NOT_PORTED);
            theme::pill(ui, "🖼", "Retry Images", p.retry_image, false)
                .on_disabled_hover_text(NOT_PORTED);
            theme::pill(ui, "🎵", "Retry Audio", p.retry_audio, false)
                .on_disabled_hover_text(NOT_PORTED);
            if theme::pill(ui, "🛠", "Settings", p.settings, true).clicked() {
                self.show_settings = true;
            }
            theme::pill(ui, "📋", "Reports", (p.surface2, p.muted), false)
                .on_disabled_hover_text(NOT_PORTED);
            ui.add_space(12.0);
            let label = if self.manager.enabled {
                "CYOA Mgr ✓"
            } else {
                "CYOA Mgr ✗"
            };
            if theme::pill(ui, "📤", label, p.manager, self.library.is_none()).clicked() {
                self.open_library(ui.ctx());
            }
            if theme::pill(
                ui,
                "🔄",
                "Updates",
                (p.accentbg, p.accent),
                self.updates.is_none(),
            )
            .clicked()
            {
                self.updates = Some(crate::updates_panel::UpdatesPanel::new());
            }
        });
    }

    /// Python progress card (left) and LOG (right).
    pub(super) fn bottom_area(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        let card_w = 400.0f32.min(ui.available_width() * 0.45);
        let h = ui.available_height();
        ui.horizontal_top(|ui| {
            ui.add_space(8.0);
            Frame::new()
                .fill(p.surface)
                .stroke(Stroke::new(1.0, p.border))
                .corner_radius(CornerRadius::same(9))
                .inner_margin(Margin::symmetric(12, 10))
                .show(ui, |ui| {
                    ui.vertical(|ui| {
                        ui.set_width(card_w - 24.0);
                        ui.set_min_height((h - 30.0).max(0.0));
                        self.progress_card(ui, &p);
                    });
                });
            ui.add_space(14.0);
            ui.vertical(|ui| {
                ui.set_height((h - 8.0).max(0.0));
                self.log_panel(ui);
            });
        });
    }

    fn progress_card(&mut self, ui: &mut egui::Ui, p: &Palette) {
        let s = run_stats(&self.jobs);
        let running = self.running_job().is_some();
        let state = if running { "RUNNING" } else { "IDLE" };
        ui.horizontal(|ui| {
            ui.label(
                bold(
                    format!(
                        "Progress: {state} | Job {} of {} | Overall {:.1}%",
                        s.job_index,
                        s.total,
                        s.overall * 100.0
                    ),
                    11.0,
                )
                .color(p.fg),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let t = if self.show_details {
                    "Hide Details"
                } else {
                    "Show Details"
                };
                if theme::flat(
                    ui,
                    RichText::new(t).size(10.5).color(p.muted),
                    p.surface2,
                    vec2(0.0, 26.0),
                    true,
                )
                .clicked()
                {
                    self.show_details = !self.show_details;
                }
            });
        });
        ui.add_space(4.0);
        ui.label(
            RichText::new(format!(
                "Job {} of {} — Overall: {:.1}%",
                s.job_index,
                s.total,
                s.overall * 100.0
            ))
            .size(9.0)
            .color(p.muted),
        );
        ui.label(
            RichText::new(format!(
                "Completed: {} | Failed: {} | Remaining: {}",
                s.completed, s.failed, s.remaining
            ))
            .size(9.0)
            .color(p.muted),
        );
        if self.show_details
            && let Some(j) = self.running_job()
        {
            ui.label(
                RichText::new(j.current.chars().take(60).collect::<String>())
                    .monospace()
                    .size(8.5)
                    .color(p.muted),
            );
        }
        ui.add_space(4.0);
        let (r, _) = ui.allocate_exact_size(vec2(ui.available_width(), 8.0), egui::Sense::hover());
        ui.painter()
            .rect_filled(r, CornerRadius::same(4), p.surface2);
        let w = (r.width() * s.overall).max(8.0);
        ui.painter().rect_filled(
            egui::Rect::from_min_size(r.min, vec2(w, r.height())),
            CornerRadius::same(4),
            Color32::from_rgb(0x22, 0xc5, 0x5e),
        );
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            if theme::flat(
                ui,
                RichText::new("Cancel").size(11.5).color(Color32::WHITE),
                Color32::from_rgb(0xb9, 0x1c, 0x1c),
                vec2(80.0, 30.0),
                true,
            )
            .clicked()
                && let Some(w) = &self.worker
            {
                w.cancel_all();
            }
            let last_error = self.jobs.iter().rev().find_map(|j| match &j.status {
                Status::Failed(e) => Some(format!("{}: {e}", j.url)),
                _ => None,
            });
            if theme::flat(
                ui,
                RichText::new("Copy Error").size(11.5).color(p.fg),
                p.surface2,
                vec2(92.0, 30.0),
                last_error.is_some(),
            )
            .clicked()
                && let Some(e) = last_error
            {
                ui.ctx().copy_text(e);
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.label(bold(state, 9.0).color(p.muted));
            });
        });
    }

    /// Python LOG panel.
    pub(super) fn log_panel(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal(|ui| {
            ui.label(bold("LOG", 10.0).color(p.muted));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.add_space(14.0);
                if theme::flat(
                    ui,
                    RichText::new("Clear").size(10.5).color(p.muted),
                    p.surface2,
                    vec2(52.0, 24.0),
                    true,
                )
                .clicked()
                {
                    self.log = super::RingLog::new(super::LOG_CAPACITY);
                }
            });
        });
        let row_h = ui.text_style_height(&egui::TextStyle::Monospace);
        let footer = 20.0;
        egui::ScrollArea::vertical()
            .id_salt("log")
            .auto_shrink([false, false])
            .max_height((ui.available_height() - footer).max(40.0))
            .stick_to_bottom(true)
            .show_rows(ui, row_h, self.log.len(), |ui, range| {
                for i in range {
                    if let Some(line) = self.log.get(i) {
                        ui.label(RichText::new(line).monospace().size(8.5).color(p.log_fg));
                    }
                }
            });
        ui.label(
            RichText::new("Log written to: cyoa_downloader.log in output folder")
                .size(8.5)
                .color(p.muted),
        );
    }

    /// Credits, Help, Diagnostics and Settings windows.
    pub(super) fn dialogs(&mut self, ctx: &egui::Context) {
        let mut open = self.show_credits;
        egui::Window::new("Credits").open(&mut open).resizable(false).show(ctx, |ui| {
            ui.label(bold(format!("CYOA Downloader v{}", cyoa_core::app_version()), 11.5));
            ui.label("Native Rust port of the Python CYOA Downloader.");
            ui.label("Bundled third-party licenses: see the LICENSES folder next to the executable.");
            ui.label("Serve userscript integration: IntCyoaEnhancer (Greasy Fork #438947) — see --userscript-info.");
        });
        self.show_credits = open;

        let mut open = self.show_help;
        let auto_pref = self.auto_pref.clone();
        egui::Window::new("Help / Guide").open(&mut open).default_width(520.0).show(ctx, |ui| {
            ui.label(bold("Quick start", 10.5));
            for line in [
                "1. Pick an output mode in the left sidebar (Auto detects the engine).",
                "2. Paste the CYOA URL (and an optional filename), then press ➕ Add URL.",
                "3. Repeat or use Import List… for .txt/.csv/.xlsx lists.",
                "4. Press ▶ Download All. Progress appears in the queue, progress card and LOG.",
                "5. Website folder modes can be opened with ⚡ Start Serve.",
            ] {
                ui.label(line);
            }
            ui.add_space(6.0);
            ui.label(bold("Modes", 10.5));
            for e in MODES.iter().filter(|e| e.key != "__sec__") {
                ui.label(format!("{} {} — {}", e.icon, e.name, theme::sidebar_desc(e, &auto_pref)));
            }
        });
        self.show_help = open;

        let mut open = self.show_diagnostics;
        egui::Window::new("Diagnostics")
            .open(&mut open)
            .default_size([720.0, 520.0])
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    if ui.button("Refresh dependency check").clicked() {
                        self.diag_report =
                            Some(cyoa_engine::diagnostics::dependency_check_report());
                    }
                    if ui.button("Run self-test").clicked() {
                        self.diag_report =
                            Some(cyoa_engine::diagnostics::run_internal_self_test().1);
                    }
                    if ui.button("Copy").clicked()
                        && let Some(r) = &self.diag_report
                    {
                        ui.ctx().copy_text(r.clone());
                    }
                });
                egui::ScrollArea::both()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        ui.label(
                            RichText::new(self.diag_report.as_deref().unwrap_or(""))
                                .monospace()
                                .size(8.5),
                        );
                    });
            });
        self.show_diagnostics = open;

        let mut open = self.show_settings;
        let mut new_pref = None;
        egui::Window::new("Settings")
            .open(&mut open)
            .default_width(680.0)
            .show(ctx, |ui| {
                ui.label(bold("Default Auto Output", 10.0));
                ui.horizontal(|ui| {
                    for (v, l) in [("folder", "Folder"), ("zip", "ZIP")] {
                        if ui.radio(self.auto_pref == v, l).clicked() && self.auto_pref != v {
                            new_pref = Some(v);
                        }
                    }
                });
                ui.separator();
                ui.label(bold("Network", 10.0));
                self.settings.net.ui(ui);
                ui.separator();
                ui.label(bold("CYOA Manager", 10.0));
                self.manager.ui(ui);
                ui.separator();
                ui.label(bold("Offline viewers", 10.0));
                if ui
                    .add_enabled(self.viewers.is_none(), egui::Button::new("Viewers…"))
                    .clicked()
                    && let Some((path, _)) = &self.shared
                {
                    self.viewers = Some(crate::viewers::ViewerPanel::open(path.clone()));
                }
            });
        if let Some(v) = new_pref {
            self.auto_pref = v.to_owned();
            self.save_shared_key("auto_detect_output", v.into());
        }
        self.show_settings = open;
    }

    pub(super) fn open_library(&mut self, ctx: &egui::Context) {
        let serve_enabled = self
            .shared
            .as_ref()
            .and_then(|(p, _)| {
                let mut store = cyoa_storage::settings::SettingsStore::new(p);
                store.load().ok()?.get("serve_enabled")?.as_bool()
            })
            .unwrap_or(true);
        self.library = Some(crate::library::LibraryPanel::open(
            self.manager.db_path.clone(),
            serve_enabled,
            ctx,
        ));
    }

    /// Persist one Python settings.json key.
    fn save_shared_key(&mut self, key: &'static str, value: serde_json::Value) {
        if let Some((path, _)) = &self.shared
            && let Err(e) = crate::net::update_shared(path, |map| {
                map.insert(key.into(), value);
            })
        {
            self.log.push(format!("Could not save {key}: {e}"));
        }
    }

    /// Python `_load_logo_images` (dark/light emblem), uploaded once.
    fn logo_texture(&mut self, ctx: &egui::Context, dark: bool) -> egui::TextureHandle {
        let slot = if dark {
            &mut self.logo_dark
        } else {
            &mut self.logo_light
        };
        slot.get_or_insert_with(|| {
            let bytes: &[u8] = if dark {
                include_bytes!("../../assets/logo-dark-128.rgba")
            } else {
                include_bytes!("../../assets/logo-light-128.rgba")
            };
            let img = egui::ColorImage::from_rgba_unmultiplied([128, 128], bytes);
            ctx.load_texture(
                if dark { "logo-dark" } else { "logo-light" },
                img,
                egui::TextureOptions::LINEAR,
            )
        })
        .clone()
    }
}
