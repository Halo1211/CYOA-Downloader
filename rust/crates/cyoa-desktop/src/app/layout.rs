//! Main window layout modelled on the Python GUI (`_setup_ui_base`):
//! titlebar, mode sidebar, Input section, queue, action rows and LOG panel.

use super::{DownloaderApp, Job, Status};
use crate::theme::{self, MODES, Palette, bold};
use eframe::egui::{self, Color32, CornerRadius, Frame, Margin, RichText, Stroke};

const NOT_PORTED: &str = "Not ported to the Rust version yet";

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

fn section_label(ui: &mut egui::Ui, p: &Palette, text: &str) {
    ui.label(bold(text, 10.0).color(p.muted2));
}

impl DownloaderApp {
    /// Python TITLEBAR: logo, title stack, version, theme pill.
    pub(super) fn titlebar(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal_centered(|ui| {
            ui.add_space(16.0);
            let (rect, _) = ui.allocate_exact_size(egui::vec2(42.0, 42.0), egui::Sense::hover());
            ui.painter()
                .rect_filled(rect, CornerRadius::same(10), p.accentbg);
            ui.painter().text(
                rect.center(),
                egui::Align2::CENTER_CENTER,
                "C↯",
                egui::FontId::new(16.0, egui::FontFamily::Monospace),
                p.accent,
            );
            ui.add_space(12.0);
            ui.vertical(|ui| {
                ui.add_space(18.0);
                ui.label(bold("CYOA Downloader", 18.0).color(p.fg));
                ui.label(
                    RichText::new("ICC backup · serve preview · diagnostics")
                        .size(11.0)
                        .color(p.muted),
                );
            });
            ui.add_space(8.0);
            ui.label(
                RichText::new(format!("v{}", cyoa_core::app_version()))
                    .size(11.0)
                    .color(p.muted),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.add_space(14.0);
                let mut changed = false;
                for mode in ["Dark", "Light", "System"] {
                    let on = self.theme_mode == mode;
                    let text = RichText::new(mode).size(12.0).color(if on {
                        Color32::WHITE
                    } else {
                        p.muted
                    });
                    let fill = if on { p.accent } else { p.surface };
                    if ui
                        .add(
                            egui::Button::new(text)
                                .fill(fill)
                                .corner_radius(CornerRadius::same(7)),
                        )
                        .clicked()
                        && !on
                    {
                        self.theme_mode = mode.to_owned();
                        changed = true;
                    }
                }
                if changed {
                    theme::apply_theme_mode(ui.ctx(), &self.theme_mode);
                    if let Some((path, _)) = &self.shared {
                        let m = self.theme_mode.clone();
                        if let Err(e) = crate::net::update_shared(path, |map| {
                            map.insert("theme_mode".into(), m.into());
                        }) {
                            self.log.push(format!("Could not save theme: {e}"));
                        }
                    }
                }
            });
        });
    }

    /// Python SIDEBAR: section headers, mode cards with selection bar, info box.
    pub(super) fn sidebar(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.add_space(6.0);
                for entry in MODES {
                    if entry.key == "__sec__" {
                        ui.add_space(6.0);
                        ui.horizontal(|ui| {
                            ui.add_space(12.0);
                            section_label(ui, &p, entry.name);
                        });
                        let (r, _) = ui.allocate_exact_size(
                            egui::vec2(ui.available_width(), 1.0),
                            egui::Sense::hover(),
                        );
                        ui.painter().rect_filled(
                            r.shrink2(egui::vec2(10.0, 0.0)),
                            0.0,
                            p.separator,
                        );
                        ui.add_space(2.0);
                        continue;
                    }
                    let selected = entry.mode == Some(self.settings.mode);
                    let enabled = entry.mode.is_some();
                    let width = ui.available_width() - 18.0;
                    let (rect, resp) = ui
                        .allocate_exact_size(egui::vec2(width + 18.0, 48.0), egui::Sense::click());
                    let card = egui::Rect::from_min_size(
                        rect.min + egui::vec2(8.0, 2.0),
                        egui::vec2(width, 44.0),
                    );
                    let hovered = resp.hovered() && enabled;
                    let painter = ui.painter();
                    if selected {
                        painter.rect_filled(card, CornerRadius::same(9), p.sel_row);
                        painter.rect_filled(
                            egui::Rect::from_min_size(
                                card.min + egui::vec2(0.0, 5.0),
                                egui::vec2(4.0, 34.0),
                            ),
                            CornerRadius::same(2),
                            p.sel_bar,
                        );
                    } else if hovered {
                        painter.rect_filled(card, CornerRadius::same(9), p.surface);
                    }
                    let icon_color = if selected {
                        p.sel_nm
                    } else {
                        Color32::from_rgb(0x64, 0x74, 0x8b)
                    };
                    painter.text(
                        card.min + egui::vec2(20.0, 22.0),
                        egui::Align2::CENTER_CENTER,
                        entry.icon,
                        egui::FontId::proportional(15.0),
                        icon_color,
                    );
                    let name_color = if !enabled {
                        p.muted2
                    } else if selected {
                        p.sel_nm
                    } else {
                        Color32::from_rgb(0x94, 0xa3, 0xb8)
                    };
                    painter.text(
                        card.min + egui::vec2(38.0, 13.0),
                        egui::Align2::LEFT_CENTER,
                        entry.name,
                        egui::FontId::new(13.0, egui::FontFamily::Name("bold".into())),
                        name_color,
                    );
                    painter.text(
                        card.min + egui::vec2(38.0, 31.0),
                        egui::Align2::LEFT_CENTER,
                        entry.desc,
                        egui::FontId::proportional(11.0),
                        if selected {
                            p.sel_desc
                        } else {
                            Color32::from_rgb(0x64, 0x74, 0x8b)
                        },
                    );
                    let resp = if enabled {
                        resp
                    } else {
                        resp.on_hover_text(NOT_PORTED)
                    };
                    if resp.clicked()
                        && let Some(mode) = entry.mode
                        && mode != self.settings.mode
                    {
                        self.settings.mode = mode;
                        self.log.push(format!("Mode: {}", entry.name));
                    }
                }
                ui.add_space(8.0);
                let e = theme::mode_entry(self.settings.mode);
                Frame::new()
                    .fill(p.surface2)
                    .corner_radius(CornerRadius::same(7))
                    .inner_margin(Margin::same(10))
                    .outer_margin(Margin::symmetric(10, 4))
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.label(bold(format!("{} {}", e.icon, e.name), 12.0).color(p.fg));
                        ui.label(RichText::new(e.desc).size(11.0).color(p.muted));
                    });
            });
    }

    /// Python MAIN: Input header, URL row, option rows, output folder, queue,
    /// primary action row and tool strip.
    pub(super) fn main_area(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal(|ui| {
            ui.label(bold("Input", 15.0).color(p.accent));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if theme::outline(ui, &p, "❔ Help / Guide").clicked() {
                    self.show_help = true;
                }
                if theme::outline(ui, &p, "🩺 Diagnostics").clicked() {
                    self.show_diagnostics = true;
                    if self.diag_report.is_none() {
                        self.diag_report =
                            Some(cyoa_engine::diagnostics::dependency_check_report());
                    }
                }
                if theme::outline(ui, &p, "© Credits").clicked() {
                    self.show_credits = true;
                }
            });
        });
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            ui.label(bold("URL", 13.0).color(p.muted));
            let fn_width = 260.0;
            let url_w = (ui.available_width() - fn_width - 80.0 - 170.0).max(200.0);
            let r = ui.add(
                egui::TextEdit::singleline(&mut self.url_input)
                    .hint_text("https://author.neocities.org/cyoa/")
                    .desired_width(url_w)
                    .min_size(egui::vec2(0.0, 30.0)),
            );
            ui.label(bold("Filename", 13.0).color(p.muted));
            let r2 = ui.add(
                egui::TextEdit::singleline(&mut self.fn_input)
                    .hint_text("(optional)")
                    .desired_width(fn_width)
                    .min_size(egui::vec2(0.0, 30.0)),
            );
            let enter = (r.lost_focus() || r2.lost_focus())
                && ui.input(|i| i.key_pressed(egui::Key::Enter));
            if theme::primary(ui, "➕  Add URL", p.accent, true).clicked() || enter {
                self.enqueue();
            }
        });
        if let Some(e) = self.url_error {
            ui.colored_label(p.danger_fg, e);
        }
        ui.add_space(4.0);
        // Row 1: numerics + list import.
        ui.horizontal(|ui| {
            ui.label(RichText::new("Threads").color(p.muted));
            ui.add(egui::DragValue::new(&mut self.settings.threads).range(1..=64));
            ui.label(RichText::new("Wait (s)").color(p.muted));
            ui.add(egui::DragValue::new(&mut self.settings.wait_time).range(0..=3600));
            ui.label(RichText::new("Limit KB/s").color(p.muted))
                .on_hover_text("Bandwidth limit for downloads; 0 = unlimited");
            ui.add(egui::DragValue::new(&mut self.settings.bandwidth_kbps).range(0.0..=1_000_000.0).speed(10.0));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.add_enabled(false, egui::Button::new("Export List…")).on_disabled_hover_text(NOT_PORTED);
                if ui
                    .add_enabled(self.list_pick.is_none(), egui::Button::new("Import List…"))
                    .on_hover_text(".txt (url | filename | mode), or .csv/.xlsx/.xls with url/filename/mode columns")
                    .clicked()
                {
                    self.start_list_import(ui.ctx());
                }
            });
        });
        // Network (proxy / DNS / VPN) row as in the Python option rows.
        self.settings.net.ui(ui);
        // Row 2: checkboxes.
        ui.horizontal(|ui| {
            if ui.checkbox(&mut self.settings.fonts, "Fonts").changed() {
                let state = if self.settings.fonts {
                    "enabled"
                } else {
                    "disabled"
                };
                self.log.push(format!("[Feature] Font download: {state}"));
            }
            if ui
                .checkbox(&mut self.settings.analyse_fonts, "Font analysis")
                .changed()
            {
                let state = if self.settings.analyse_fonts {
                    "enabled"
                } else {
                    "disabled"
                };
                self.log.push(format!("[Feature] Font analysis: {state}"));
            }
            ui.separator();
            self.manager.ui(ui);
        });
        // Output folder.
        ui.horizontal(|ui| {
            ui.label(RichText::new("Output folder:").color(p.muted));
            ui.add(
                egui::TextEdit::singleline(&mut self.settings.output_dir)
                    .desired_width(ui.available_width() - 90.0),
            );
            let picking = self.folder_pick.is_some();
            if ui
                .add_enabled(!picking, egui::Button::new("Browse…"))
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
        let (r, _) =
            ui.allocate_exact_size(egui::vec2(ui.available_width(), 1.0), egui::Sense::hover());
        ui.painter().rect_filled(r, 0.0, p.separator);
        ui.add_space(4.0);
        // Queue header.
        ui.horizontal(|ui| {
            ui.label(bold(format!("QUEUE  {}", self.jobs.len()), 12.0).color(p.muted));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let sel = self
                    .selected
                    .and_then(|i| self.jobs.get(i).map(|j| (i, j.id, j.status.clone())));
                if ui
                    .add_enabled(sel.is_some(), egui::Button::new("Remove"))
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
                if ui.button("Clear All").clicked() {
                    self.jobs
                        .retain(|j| matches!(j.status, Status::Running { .. } | Status::Queued));
                    self.selected = None;
                }
            });
        });
        let queue_h = (ui.available_height() - 100.0).max(80.0);
        self.queue(ui, queue_h);
        ui.add_space(6.0);
        self.action_rows(ui);
    }

    /// Queue rows with Python mode badges (virtualized).
    pub(super) fn queue(&mut self, ui: &mut egui::Ui, height: f32) {
        let p = theme::palette(ui);
        let row_h = 30.0;
        let total = self.jobs.len();
        Frame::new()
            .fill(p.panel)
            .corner_radius(CornerRadius::same(9))
            .stroke(Stroke::new(1.0, p.border))
            .inner_margin(Margin::same(6))
            .show(ui, |ui| {
                if total == 0 {
                    ui.set_min_height(height.min(80.0));
                    ui.centered_and_justified(|ui| {
                        ui.label(RichText::new("Queue is empty — add a URL above.").color(p.muted));
                    });
                    return;
                }
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
                                egui::vec2(ui.available_width(), row_h - 2.0),
                                egui::Sense::click(),
                            );
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
                                    egui::FontId::proportional(11.0),
                                    fg,
                                );
                                let badge = egui::Rect::from_min_size(
                                    egui::pos2(x, rect.center().y - 9.0),
                                    egui::vec2(galley.size().x + 14.0, 18.0),
                                );
                                painter.rect_filled(badge, CornerRadius::same(6), bg);
                                painter.galley(
                                    badge.min + egui::vec2(7.0, 9.0 - galley.size().y / 2.0),
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
                                egui::FontId::proportional(13.0),
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
                                egui::FontId::proportional(12.0),
                                color,
                            );
                            if resp.clicked() {
                                self.selected = Some(i);
                            }
                        }
                    });
            });
    }

    /// Python Row A (primary controls + progress) and Row B (tool pills).
    fn action_rows(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal(|ui| {
            if theme::primary(
                ui,
                "▶ Download All",
                Color32::from_rgb(0x16, 0xa3, 0x4a),
                true,
            )
            .clicked()
            {
                self.download_all();
            }
            ui.add_enabled(
                false,
                egui::Button::new("🔍 Preview").min_size(egui::vec2(0.0, 32.0)),
            )
            .on_disabled_hover_text(NOT_PORTED);
            ui.add_enabled(
                false,
                egui::Button::new("⏸ Pause").min_size(egui::vec2(0.0, 32.0)),
            )
            .on_disabled_hover_text(NOT_PORTED);
            if ui
                .add(egui::Button::new("⏹ Cancel").min_size(egui::vec2(0.0, 32.0)))
                .clicked()
                && let Some(w) = &self.worker
            {
                w.cancel_all();
            }
            if self.preview.is_some() {
                if ui
                    .add(
                        egui::Button::new(RichText::new("■ Stop Serve").color(p.srv_fg))
                            .min_size(egui::vec2(0.0, 32.0)),
                    )
                    .clicked()
                {
                    self.preview = None;
                    self.log.push("[Server] Preview stopped.".to_owned());
                }
            } else if ui
                .add_enabled(
                    self.last_preview.is_some(),
                    egui::Button::new(RichText::new("⚡ Start Serve").color(p.srv_fg))
                        .min_size(egui::vec2(0.0, 32.0)),
                )
                .on_hover_text("Serve the last website folder on 127.0.0.1 and open it")
                .clicked()
            {
                self.start_preview();
            }
            if ui
                .add(egui::Button::new("📁 Folder").min_size(egui::vec2(0.0, 32.0)))
                .clicked()
                && let Err(e) = open_path(std::path::Path::new(&self.settings.output_dir))
            {
                self.log.push(format!("Could not open folder: {e}"));
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if let Some(j) = self.running_job() {
                    if let Status::Running { done, total } = j.status {
                        let frac = if total == 0 {
                            0.0
                        } else {
                            done as f32 / total as f32
                        };
                        ui.add(
                            egui::ProgressBar::new(frac)
                                .desired_width(220.0)
                                .text(format!("{done}/{total}")),
                        );
                        ui.label(
                            RichText::new(j.current.chars().take(40).collect::<String>())
                                .monospace()
                                .size(11.0)
                                .color(p.muted),
                        );
                    }
                } else {
                    ui.label(RichText::new("Idle").color(p.muted));
                }
            });
        });
        ui.add_space(4.0);
        Frame::new()
            .fill(p.panel)
            .corner_radius(CornerRadius::same(7))
            .inner_margin(Margin::symmetric(6, 3))
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    theme::pill(ui, "↺", "Retry Assets", p.retry_asset, false)
                        .on_disabled_hover_text(NOT_PORTED);
                    theme::pill(ui, "🖼", "Retry Images", p.retry_image, false)
                        .on_disabled_hover_text(NOT_PORTED);
                    theme::pill(ui, "🎵", "Retry Audio", p.retry_audio, false)
                        .on_disabled_hover_text(NOT_PORTED);
                    ui.separator();
                    if theme::pill(ui, "🛠", "Settings", p.settings, true).clicked() {
                        self.show_settings = true;
                    }
                    theme::pill(ui, "📋", "Reports", (p.surface2, p.fg), false)
                        .on_disabled_hover_text(NOT_PORTED);
                    ui.separator();
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
            });
    }

    /// Python LOG panel.
    pub(super) fn log_panel(&mut self, ui: &mut egui::Ui) {
        let p = theme::palette(ui);
        ui.horizontal(|ui| {
            ui.label(bold("LOG", 11.0).color(p.muted));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.small_button("Clear").clicked() {
                    self.log = super::RingLog::new(super::LOG_CAPACITY);
                }
            });
        });
        let row_h = ui.text_style_height(&egui::TextStyle::Monospace);
        let footer = 18.0;
        egui::ScrollArea::vertical()
            .id_salt("log")
            .auto_shrink([false, false])
            .max_height((ui.available_height() - footer).max(40.0))
            .stick_to_bottom(true)
            .show_rows(ui, row_h, self.log.len(), |ui, range| {
                for i in range {
                    if let Some(line) = self.log.get(i) {
                        ui.label(RichText::new(line).monospace().color(p.log_fg));
                    }
                }
            });
        ui.label(
            RichText::new("Log written to: cyoa_downloader.log in output folder")
                .size(10.0)
                .color(p.muted2),
        );
    }

    /// Credits, Help, Diagnostics and Settings windows.
    pub(super) fn dialogs(&mut self, ctx: &egui::Context) {
        let mut open = self.show_credits;
        egui::Window::new("Credits").open(&mut open).resizable(false).show(ctx, |ui| {
            ui.label(bold(format!("CYOA Downloader v{}", cyoa_core::app_version()), 15.0));
            ui.label("Native Rust port of the Python CYOA Downloader.");
            ui.label("Bundled third-party licenses: see the LICENSES folder next to the executable.");
            ui.label("Serve userscript integration: IntCyoaEnhancer (Greasy Fork #438947) — credited in --userscript-info.");
        });
        self.show_credits = open;

        let mut open = self.show_help;
        egui::Window::new("Help / Guide")
            .open(&mut open)
            .default_width(520.0)
            .show(ctx, |ui| {
                ui.label(bold("Quick start", 14.0));
                for line in [
                    "1. Pick an output mode in the left sidebar.",
                    "2. Paste the CYOA URL (and an optional filename), then press ➕ Add URL.",
                    "3. Repeat or use Import List… for .txt/.csv/.xlsx lists.",
                    "4. Press ▶ Download All. Progress and messages appear in the queue and LOG.",
                    "5. Website folder modes can be opened with ⚡ Start Serve.",
                ] {
                    ui.label(line);
                }
                ui.add_space(6.0);
                ui.label(bold("Modes", 14.0));
                for e in MODES.iter().filter(|e| e.key != "__sec__") {
                    ui.label(format!("{} {} — {}", e.icon, e.name, e.desc));
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
                                .size(11.0),
                        );
                    });
            });
        self.show_diagnostics = open;

        let mut open = self.show_settings;
        egui::Window::new("Settings")
            .open(&mut open)
            .default_width(640.0)
            .show(ctx, |ui| {
                ui.label(
                    RichText::new(
                        "Network and CYOA Manager options are in the main Input section.",
                    )
                    .weak(),
                );
                ui.separator();
                ui.label(bold("Offline viewers", 13.0));
                if ui
                    .add_enabled(self.viewers.is_none(), egui::Button::new("Viewers…"))
                    .clicked()
                    && let Some((path, _)) = &self.shared
                {
                    self.viewers = Some(crate::viewers::ViewerPanel::open(path.clone()));
                }
                ui.separator();
                ui.label(bold("Diagnostics", 13.0));
                if ui.button("🩺 Open Diagnostics").clicked() {
                    self.show_diagnostics = true;
                    if self.diag_report.is_none() {
                        self.diag_report =
                            Some(cyoa_engine::diagnostics::dependency_check_report());
                    }
                }
            });
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
}
