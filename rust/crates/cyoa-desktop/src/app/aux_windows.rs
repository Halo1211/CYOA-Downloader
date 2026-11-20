//! Python secondary windows as their own OS windows: Credits & Sources
//! (`_show_credits_panel`), Help / Guide (`_show_feature_guide`) and the
//! Diagnostics Center (`_v24_diagnostics_panel`). Text comes from the Python
//! sources (`credits.json`, `help_guide.json` exported from the Python GUI).

use super::DownloaderApp;
use crate::settings_dashboard::{muted, rgb};
use crate::theme;
use eframe::egui::{self, Color32, Frame, Margin, RichText, Stroke};
use serde_json::Value;
use std::sync::mpsc::Receiver;
use std::sync::{Arc, LazyLock};

static CREDITS: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(include_str!("../credits.json")).unwrap_or(Value::Null));
static HELP: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../help_guide.json")).unwrap_or(Value::Null)
});

fn bold_text(text: impl Into<String>, size: f32) -> RichText {
    theme::bold(text, size)
}

/// State of the Help tab and the Diagnostics Center run.
pub(crate) struct AuxState {
    pub help_tab: String,
    diag_text: String,
    diag_counts: cyoa_engine::diagnostics::DiagCounts,
    diag_filter: &'static str,
    diag_status: String,
    diag_done: bool,
    diag_job: Option<Receiver<(String, cyoa_engine::diagnostics::DiagCounts)>>,
}

impl Default for AuxState {
    fn default() -> Self {
        Self {
            help_tab: "setup".into(),
            diag_text: String::new(),
            diag_counts: Default::default(),
            diag_filter: "all",
            diag_status: String::new(),
            diag_done: false,
            diag_job: None,
        }
    }
}

fn lang_key() -> &'static str {
    if crate::i18n::is_indonesian() {
        "id"
    } else {
        "en"
    }
}

fn footer_close(ui: &mut egui::Ui, p: &theme::Palette, label: &str, size: egui::Vec2) -> bool {
    ui.add(
        egui::Button::new(RichText::new(label).size(13.0).color(p.fg))
            .fill(p.surface2)
            .corner_radius(6.0)
            .min_size(size),
    )
    .clicked()
}

impl DownloaderApp {
    pub(super) fn aux_windows(&mut self, ctx: &egui::Context) {
        self.credits_window(ctx);
        self.help_window(ctx);
        self.diagnostics_window(ctx);
    }

    fn credits_window(&mut self, ctx: &egui::Context) {
        if !self.show_credits {
            return;
        }
        let is_en = lang_key() == "en";
        let mut close = false;
        let builder = egui::ViewportBuilder::default()
            .with_title(if is_en {
                "CYOA Downloader — Credits"
            } else {
                "CYOA Downloader — Kredit"
            })
            .with_inner_size([740.0, 580.0])
            .with_min_inner_size([660.0, 500.0]);
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("credits_panel"), builder, |ui, _| {
            if ui.ctx().input(|i| i.viewport().close_requested()) {
                close = true;
            }
            let p = theme::palette(ui);
            egui::Panel::top("credits_header")
                .show_separator_line(false)
                .exact_size(52.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin::symmetric(16, 0)))
                .show(ui, |ui| {
                    ui.horizontal_centered(|ui| {
                        ui.label(bold_text(
                            if is_en { "© Credits & Sources" } else { "© Kredit & Sumber" },
                            14.0,
                        ));
                    });
                });
            egui::Panel::bottom("credits_footer")
                .show_separator_line(false)
                .exact_size(46.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin::symmetric(12, 6)))
                .show(ui, |ui| {
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if footer_close(ui, &p, if is_en { "Close" } else { "Tutup" }, egui::vec2(110.0, 34.0)) {
                            close = true;
                        }
                    });
                });
            egui::CentralPanel::default()
                .frame(Frame::new().fill(p.bg).inner_margin(Margin::symmetric(10, 8)))
                .show(ui, |ui| {
                    let intro = if is_en {
                        "With respect and real gratitude: this app exists because the CYOA community kept sharing tools, ideas, fixes, and patience. These credits are not just a formality; they are a small thank-you to the people and projects that made this work possible."
                    } else {
                        "Dengan hormat dan terima kasih yang tulus: aplikasi ini ada karena komunitas CYOA terus berbagi tool, ide, perbaikan, dan kesabaran. Credit ini bukan sekadar formalitas; ini ucapan terima kasih kecil untuk orang-orang dan proyek yang membuat pekerjaan ini mungkin."
                    };
                    ui.add_space(4.0);
                    ui.horizontal(|ui| {
                        ui.add_space(8.0);
                        ui.add(egui::Label::new(RichText::new(intro).size(11.0).color(p.muted)).wrap());
                    });
                    ui.add_space(6.0);
                    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                        credits_body(ui, &p, is_en);
                    });
                });
        });
        if close {
            self.show_credits = false;
        }
    }

    fn help_window(&mut self, ctx: &egui::Context) {
        if !self.show_help {
            return;
        }
        let lang = lang_key();
        let is_en = lang == "en";
        let data = &HELP[lang];
        let mut close = false;
        let builder = egui::ViewportBuilder::default()
            .with_title(if is_en {
                "CYOA Downloader — Help / Guide"
            } else {
                "CYOA Downloader — Bantuan / Panduan"
            })
            .with_inner_size([1040.0, 760.0])
            .with_min_inner_size([900.0, 640.0]);
        let tab = &mut self.aux.help_tab;
        ctx.show_viewport_immediate(
            egui::ViewportId::from_hash_of("help_guide"),
            builder,
            |ui, _| {
                if ui.ctx().input(|i| i.viewport().close_requested()) {
                    close = true;
                }
                let p = theme::palette(ui);
                egui::Panel::top("help_header")
                    .show_separator_line(false)
                    .exact_size(52.0)
                    .frame(
                        Frame::new()
                            .fill(p.panel)
                            .inner_margin(Margin::symmetric(16, 0)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal_centered(|ui| {
                            ui.label(bold_text(data["header_text"].as_str().unwrap_or(""), 13.0));
                        });
                    });
                egui::Panel::top("help_tabs")
                    .show_separator_line(false)
                    .frame(Frame::new().fill(p.bg).inner_margin(Margin {
                        left: 12,
                        right: 12,
                        top: 6,
                        bottom: 4,
                    }))
                    .show(ui, |ui| {
                        Frame::new()
                            .fill(p.surface)
                            .corner_radius(8.0)
                            .inner_margin(Margin::same(3))
                            .show(ui, |ui| {
                                let tabs = data[if is_en { "TABS_EN" } else { "TABS_ID" }]
                                    .as_array()
                                    .cloned()
                                    .unwrap_or_default();
                                for row in tabs.chunks(7) {
                                    ui.columns(7, |cols| {
                                        for (c, t) in row.iter().enumerate() {
                                            let key = t[0].as_str().unwrap_or("");
                                            let label = t[1].as_str().unwrap_or("");
                                            let on = *tab == key;
                                            let w = cols[c].available_width();
                                            let b =
                                                egui::Button::new(bold_text(label, 8.0).color(
                                                    if on { Color32::WHITE } else { p.muted },
                                                ))
                                                .fill(if on {
                                                    rgb(0x3b, 0x82, 0xf6)
                                                } else {
                                                    p.surface
                                                })
                                                .corner_radius(6.0)
                                                .min_size(egui::vec2(w, 24.0));
                                            if cols[c].add(b).clicked() {
                                                *tab = key.to_owned();
                                            }
                                        }
                                    });
                                }
                            });
                    });
                egui::CentralPanel::default()
                    .frame(
                        Frame::new()
                            .fill(p.bg)
                            .inner_margin(Margin::symmetric(12, 6)),
                    )
                    .show(ui, |ui| {
                        egui::ScrollArea::vertical()
                            .id_salt(("help", tab.clone()))
                            .auto_shrink([false, false])
                            .show(ui, |ui| help_body(ui, &p, data, tab, is_en));
                    });
            },
        );
        if close {
            self.show_help = false;
        }
    }

    fn diagnostics_window(&mut self, ctx: &egui::Context) {
        if !self.show_diagnostics {
            return;
        }
        let is_en = lang_key() == "en";
        let labels = |en: &'static str, id: &'static str| if is_en { en } else { id };
        if self.aux.diag_job.is_none() && !self.aux.diag_done && self.aux.diag_text.is_empty() {
            self.start_diagnostics(ctx);
        }
        if let Some(rx) = &self.aux.diag_job
            && let Ok((text, counts)) = rx.try_recv()
        {
            self.aux.diag_text = text;
            self.aux.diag_counts = counts;
            self.aux.diag_done = true;
            self.aux.diag_job = None;
            self.aux.diag_status = format!(
                "{} — PASS {}, WARN {}, FAIL {}",
                labels("Done", "Selesai"),
                counts.pass,
                counts.warn,
                counts.fail
            );
        }
        let mut close = false;
        let mut run = false;
        let title = labels("Diagnostics Center", "Pusat Diagnostik");
        let builder = egui::ViewportBuilder::default()
            .with_title(title)
            .with_inner_size([1020.0, 680.0])
            .with_min_inner_size([860.0, 560.0]);
        let out_dir = self.settings.output_dir.clone();
        let aux = &mut self.aux;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("diagnostics_center"), builder, |ui, _| {
            if ui.ctx().input(|i| i.viewport().close_requested()) {
                close = true;
            }
            let p = theme::palette(ui);
            egui::Panel::top("diag_header")
                .show_separator_line(false)
                .exact_size(72.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin::symmetric(18, 0)))
                .show(ui, |ui| {
                    ui.horizontal_centered(|ui| {
                        ui.label(bold_text(format!("🩺 {title}"), 16.0));
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            let status = if aux.diag_job.is_some() {
                                labels("Running diagnostics…", "Menjalankan diagnostik…").to_owned()
                            } else {
                                aux.diag_status.clone()
                            };
                            ui.label(RichText::new(status).size(11.0).color(p.muted));
                        });
                    });
                });
            egui::Panel::bottom("diag_buttons")
                .show_separator_line(false)
                .frame(Frame::new().fill(p.bg).inner_margin(Margin { left: 14, right: 14, top: 0, bottom: 12 }))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        let running = aux.diag_job.is_some();
                        let run_btn = egui::Button::new(RichText::new(labels("Run Again", "Jalankan Lagi")).size(13.0).color(Color32::WHITE))
                            .fill(rgb(0x3b, 0x82, 0xf6))
                            .corner_radius(6.0)
                            .min_size(egui::vec2(112.0, 32.0));
                        if ui.add_enabled(!running, run_btn).clicked() {
                            run = true;
                        }
                        let ready = aux.diag_done && !running;
                        let plain = |text: &str, w: f32| {
                            egui::Button::new(RichText::new(text).size(13.0).color(p.fg))
                                .fill(p.surface2)
                                .corner_radius(6.0)
                                .min_size(egui::vec2(w, 32.0))
                        };
                        if ui.add_enabled(ready, plain(labels("Copy", "Salin"), 90.0)).clicked() {
                            ui.ctx().copy_text(aux.diag_text.clone());
                            aux.diag_status = labels("Copied", "Disalin").to_owned();
                        }
                        if ui.add_enabled(ready, plain(labels("Save As…", "Simpan Sebagai…"), 110.0)).clicked()
                            && let Some(path) = rfd::FileDialog::new()
                                .set_file_name("cyoa_diagnostics.txt")
                                .add_filter("Text", &["txt"])
                                .save_file()
                        {
                            aux.diag_status = save_report(&path, &aux.diag_text, is_en);
                        }
                        if ui.add_enabled(ready, plain(labels("Save to Output", "Simpan ke Output"), 132.0)).clicked() {
                            let folder = std::path::PathBuf::from(&out_dir);
                            let _ = std::fs::create_dir_all(&folder);
                            aux.diag_status = save_report(&folder.join("cyoa_diagnostics.txt"), &aux.diag_text, is_en);
                        }
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.add(plain(labels("Close", "Tutup"), 90.0)).clicked() {
                                close = true;
                            }
                        });
                    });
                });
            egui::Panel::left("diag_summary")
                .show_separator_line(false)
                .exact_size(186.0)
                .resizable(false)
                .frame(Frame::new().fill(p.bg).inner_margin(Margin { left: 14, right: 12, top: 12, bottom: 0 }))
                .show(ui, |ui| {
                    Frame::new()
                        .fill(p.surface)
                        .stroke(Stroke::new(1.0, p.border))
                        .corner_radius(14.0)
                        .inner_margin(Margin::same(14))
                        .show(ui, |ui| {
                            ui.set_min_height(ui.available_height());
                            ui.label(bold_text(labels("Summary", "Ringkasan"), 13.0));
                            ui.add_space(6.0);
                            let c = aux.diag_counts;
                            for (name, n, bg, fg) in [
                                ("PASS", c.pass, rgb(0x06, 0x5f, 0x46), rgb(0xd1, 0xfa, 0xe5)),
                                ("WARN", c.warn, rgb(0x92, 0x40, 0x0e), rgb(0xfe, 0xf3, 0xc7)),
                                ("FAIL", c.fail, rgb(0x99, 0x1b, 0x1b), rgb(0xfe, 0xe2, 0xe2)),
                            ] {
                                Frame::new().fill(bg).corner_radius(12.0).show(ui, |ui| {
                                    ui.set_width(ui.available_width());
                                    ui.set_height(58.0);
                                    ui.vertical_centered(|ui| {
                                        ui.add_space(10.0);
                                        ui.label(bold_text(format!("{name}\n{n}"), 13.0).color(fg));
                                    });
                                });
                                ui.add_space(10.0);
                            }
                            ui.add(egui::Label::new(
                                RichText::new(labels(
                                    "Secret-safe: API keys, tokens, cookies, and passwords are not printed.",
                                    "Aman-secret: API key, token, cookie, dan password tidak dicetak.",
                                ))
                                .size(9.0)
                                .color(muted(ui)),
                            ).wrap());
                        });
                });
            egui::CentralPanel::default()
                .frame(Frame::new().fill(p.bg).inner_margin(Margin { left: 0, right: 14, top: 12, bottom: 8 }))
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        for (key, label) in [
                            ("all", labels("All", "Semua")),
                            ("warn", labels("Warnings + Fails", "Peringatan + Gagal")),
                            ("fail", labels("Fails Only", "Gagal Saja")),
                        ] {
                            let on = aux.diag_filter == key;
                            let b = egui::Button::new(RichText::new(label).size(13.0).color(if on { Color32::WHITE } else { p.muted }))
                                .fill(if on { rgb(0x3b, 0x82, 0xf6) } else { p.surface2 })
                                .corner_radius(6.0)
                                .min_size(egui::vec2(130.0, 30.0));
                            if ui.add(b).clicked() {
                                aux.diag_filter = key;
                            }
                        }
                    });
                    ui.add_space(6.0);
                    Frame::new()
                        .fill(p.bg)
                        .stroke(Stroke::new(1.0, p.border))
                        .corner_radius(6.0)
                        .inner_margin(Margin::same(8))
                        .show(ui, |ui| {
                            egui::ScrollArea::both().auto_shrink([false, false]).show(ui, |ui| {
                                let text = if aux.diag_job.is_some() {
                                    labels("Running diagnostics…", "Menjalankan diagnostik…").to_owned()
                                } else {
                                    aux.diag_text.clone()
                                };
                                for line in filtered(&text, aux.diag_filter) {
                                    let color = if line.starts_with("PASS ") {
                                        rgb(0x86, 0xef, 0xac)
                                    } else if line.starts_with("WARN ") {
                                        rgb(0xfc, 0xd3, 0x4d)
                                    } else if line.starts_with("FAIL ") {
                                        rgb(0xfc, 0xa5, 0xa5)
                                    } else if is_head(line) {
                                        rgb(0x93, 0xc5, 0xfd)
                                    } else {
                                        p.muted
                                    };
                                    ui.label(RichText::new(line).monospace().size(11.0).color(color));
                                }
                            });
                        });
                });
        });
        if run {
            self.start_diagnostics(ctx);
        }
        if close {
            self.show_diagnostics = false;
            self.aux.diag_text.clear();
            self.aux.diag_done = false;
        }
    }

    fn start_diagnostics(&mut self, ctx: &egui::Context) {
        let (tx, rx) = std::sync::mpsc::channel();
        let out = std::path::PathBuf::from(&self.settings.output_dir);
        let lang = lang_key();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("diagnostics".into())
            .spawn(move || {
                let _ = tx.send(cyoa_engine::diagnostics::runtime_report(&out, true, lang));
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.aux.diag_job = Some(rx);
            self.aux.diag_done = false;
            self.aux.diag_counts = Default::default();
        }
    }
}

fn is_head(line: &str) -> bool {
    line.starts_with("CYOA Downloader")
        || line.starts_with("Diagnostik CYOA")
        || (!line.is_empty() && (line.chars().all(|c| c == '=') || line.chars().all(|c| c == '-')))
}

/// Python `_filtered_lines`.
fn filtered<'a>(text: &'a str, filter: &str) -> Vec<&'a str> {
    text.lines()
        .filter(|l| match filter {
            "fail" => l.starts_with("FAIL ") || is_head(l),
            "warn" => l.starts_with("WARN ") || l.starts_with("FAIL ") || is_head(l),
            _ => true,
        })
        .collect()
}

fn save_report(path: &std::path::Path, text: &str, is_en: bool) -> String {
    match std::fs::write(path, text) {
        Ok(()) => format!(
            "{}{}",
            if is_en { "Saved: " } else { "Tersimpan: " },
            path.display()
        ),
        Err(e) => e.to_string(),
    }
}

fn license_color(lic: &str, p: &theme::Palette) -> Color32 {
    let low = lic.to_lowercase();
    if low.contains("gpl") {
        rgb(0xf5, 0x9e, 0x0b)
    } else if low.contains("mit") || low.contains("apache") || low.contains("bsd") {
        rgb(0x22, 0xc5, 0x5e)
    } else {
        p.muted
    }
}

fn credits_body(ui: &mut egui::Ui, p: &theme::Palette, is_en: bool) {
    let cats: [(&str, &str, &str); 7] = [
        ("base", "Base & Origins", "Dasar & Asal-usul"),
        (
            "inspiration",
            "Inspiration & References",
            "Inspirasi & Referensi",
        ),
        ("ecosystem", "Viewer Ecosystems", "Ekosistem Viewer"),
        ("library", "Libraries & Backends", "Library & Backend"),
        ("tooling", "Development Tooling", "Perkakas Pengembangan"),
        ("community", "Community & People", "Komunitas & Orang"),
        ("gratitude", "Gratitude", "Ucapan Syukur"),
    ];
    let items = CREDITS.as_array().cloned().unwrap_or_default();
    for (cat, en, id) in cats {
        let group: Vec<&Value> = items.iter().filter(|i| i["category"] == cat).collect();
        if group.is_empty() {
            continue;
        }
        ui.add_space(10.0);
        ui.horizontal(|ui| {
            ui.add_space(8.0);
            ui.label(bold_text((if is_en { en } else { id }).to_uppercase(), 10.0).color(p.accent));
        });
        for item in group {
            let s = |k: &str| item[k].as_str().unwrap_or("").to_owned();
            Frame::new()
                .fill(p.surface2)
                .stroke(Stroke::new(1.0, p.border))
                .corner_radius(10.0)
                .inner_margin(Margin::same(10))
                .outer_margin(Margin::symmetric(4, 4))
                .show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.horizontal_top(|ui| {
                        let icon = if s("url").is_empty() { "✦" } else { "🔗" };
                        ui.label(bold_text(icon, 16.0).color(p.accent));
                        ui.add_space(4.0);
                        let url = s("url");
                        let text_w = ui.available_width() - if url.is_empty() { 0.0 } else { 90.0 };
                        ui.vertical(|ui| {
                            ui.set_width(text_w);
                            ui.label(bold_text(s("title"), 12.0).color(p.fg));
                            let role = s(if is_en { "role_en" } else { "role_id" });
                            if !role.is_empty() {
                                ui.label(bold_text(role, 10.0).color(p.fg));
                            }
                            let lic = s("license");
                            if !lic.is_empty() {
                                ui.label(
                                    bold_text(
                                        format!(
                                            "{}{lic}",
                                            if is_en { "License: " } else { "Lisensi: " }
                                        ),
                                        9.0,
                                    )
                                    .color(license_color(&lic, p)),
                                );
                            }
                            ui.add(
                                egui::Label::new(
                                    RichText::new(s(if is_en { "desc_en" } else { "desc_id" }))
                                        .size(10.0)
                                        .color(p.muted),
                                )
                                .wrap(),
                            );
                            if !url.is_empty() {
                                ui.label(RichText::new(&url).monospace().size(9.0).color(p.accent));
                            }
                        });
                        if !url.is_empty() {
                            ui.allocate_ui_with_layout(
                                egui::vec2(80.0, 60.0),
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    let b = egui::Button::new(
                                        RichText::new(if is_en { "Open" } else { "Buka" })
                                            .size(13.0)
                                            .color(Color32::WHITE),
                                    )
                                    .fill(p.accent)
                                    .corner_radius(6.0)
                                    .min_size(egui::vec2(72.0, 28.0));
                                    if ui.add(b).clicked() {
                                        ui.ctx().open_url(egui::OpenUrl::new_tab(url.clone()));
                                    }
                                },
                            );
                        }
                    });
                });
        }
    }
}

fn help_body(ui: &mut egui::Ui, p: &theme::Palette, data: &Value, tab: &str, is_en: bool) {
    let content = &data[if is_en { "CONTENT_EN" } else { "CONTENT_ID" }][tab];
    let colors = &data["COLOR_MAP"];
    let color_of = |key: &str| {
        colors[key]
            .as_str()
            .and_then(|h| Color32::from_hex(h).ok())
            .unwrap_or(p.muted)
    };
    for section in content.as_array().cloned().unwrap_or_default() {
        let title = section[0].as_str().unwrap_or("");
        let color = color_of(section[1].as_str().unwrap_or("muted"));
        ui.add_space(10.0);
        Frame::new()
            .fill(p.surface)
            .corner_radius(8.0)
            .inner_margin(Margin {
                left: 14,
                right: 14,
                top: 10,
                bottom: 4,
            })
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.label(bold_text(title, 12.0).color(color));
            });
        if title.contains("Excel / CSV Template") || title.contains("Template Excel / CSV") {
            spreadsheet(ui, p, data);
            continue;
        }
        for row in section[2].as_array().cloned().unwrap_or_default() {
            help_row(
                ui,
                p,
                row[0].as_str().unwrap_or(""),
                row[1].as_str().unwrap_or(""),
                false,
            );
        }
    }
}

fn help_row(ui: &mut egui::Ui, p: &theme::Palette, feat: &str, desc: &str, mono: bool) {
    ui.add_space(1.0);
    Frame::new()
        .fill(p.surface2)
        .corner_radius(6.0)
        .inner_margin(Margin {
            left: 12,
            right: 12,
            top: 6,
            bottom: 6,
        })
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal_top(|ui| {
                ui.allocate_ui_with_layout(
                    egui::vec2(145.0, 18.0),
                    egui::Layout::left_to_right(egui::Align::Min),
                    |ui| {
                        ui.set_width(145.0);
                        ui.label(bold_text(feat, 10.0).color(p.fg));
                    },
                );
                let text = RichText::new(desc)
                    .size(if mono { 9.0 } else { 10.0 })
                    .color(p.muted);
                let text = if mono { text.monospace() } else { text };
                ui.add(egui::Label::new(text).wrap());
            });
        });
}

/// Python `_render_spreadsheet_example`.
fn spreadsheet(ui: &mut egui::Ui, p: &theme::Palette, data: &Value) {
    let headers: Vec<String> = data["headers"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    let rows = data["rows"].as_array().cloned().unwrap_or_default();
    let cell = |ui: &mut egui::Ui, text: &str, bg: Color32, fg: Color32, bold: bool, w: f32| {
        Frame::new()
            .fill(bg)
            .inner_margin(Margin::symmetric(6, 5))
            .show(ui, |ui| {
                ui.set_width(w);
                let t = if bold {
                    bold_text(text, 9.0)
                } else {
                    RichText::new(text).size(9.0).monospace()
                };
                ui.add(egui::Label::new(t.color(fg)).wrap());
            });
    };
    Frame::new()
        .fill(p.bg)
        .corner_radius(8.0)
        .inner_margin(Margin::symmetric(12, 4))
        .show(ui, |ui| {
            let total = ui.available_width() - 34.0 - 30.0;
            let widths = [34.0, total * 0.33, total * 0.18, total * 0.16, total * 0.33];
            ui.spacing_mut().item_spacing = egui::vec2(1.0, 1.0);
            let grey = rgb(0xe5, 0xe7, 0xeb);
            let dark = rgb(0x11, 0x18, 0x27);
            ui.horizontal(|ui| {
                cell(ui, "", grey, dark, true, widths[0]);
                for (i, l) in ["A", "B", "C", "D"].iter().enumerate() {
                    cell(ui, l, grey, dark, true, widths[i + 1]);
                }
            });
            ui.horizontal(|ui| {
                cell(
                    ui,
                    "1",
                    rgb(0xf3, 0xf4, 0xf6),
                    rgb(0x37, 0x41, 0x51),
                    false,
                    widths[0],
                );
                for (i, h) in headers.iter().enumerate() {
                    cell(
                        ui,
                        h,
                        rgb(0x25, 0x63, 0xeb),
                        Color32::WHITE,
                        true,
                        widths[i + 1],
                    );
                }
            });
            for (r, row) in rows.iter().enumerate() {
                let r_i = r + 2;
                let bg = if r_i % 2 == 0 {
                    rgb(0xdb, 0xea, 0xfe)
                } else {
                    rgb(0xf8, 0xfa, 0xfc)
                };
                ui.horizontal(|ui| {
                    cell(
                        ui,
                        &r_i.to_string(),
                        rgb(0xf3, 0xf4, 0xf6),
                        rgb(0x37, 0x41, 0x51),
                        false,
                        widths[0],
                    );
                    for (i, v) in row
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                        .iter()
                        .enumerate()
                    {
                        cell(
                            ui,
                            v.as_str().unwrap_or(""),
                            bg,
                            rgb(0x0f, 0x17, 0x2a),
                            false,
                            widths[(i + 1).min(4)],
                        );
                    }
                });
            }
        });
    help_row(
        ui,
        p,
        data["csv_title"].as_str().unwrap_or(""),
        data["csv_text"].as_str().unwrap_or(""),
        true,
    );
    for item in data["legend_items"].as_array().cloned().unwrap_or_default() {
        help_row(
            ui,
            p,
            item[0].as_str().unwrap_or(""),
            item[1].as_str().unwrap_or(""),
            false,
        );
    }
    let _ = Arc::new(());
}
