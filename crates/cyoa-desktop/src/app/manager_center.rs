//! Python `_cyoa_manager_panel` (CYOA Manager Center): auto-add toggle,
//! library.sqlite3 path, and library actions (browse, export, add
//! project.json, add current session, scan folder) in one window.

use super::{DownloaderApp, Status};
use crate::settings_dashboard::{muted, rgb};
use crate::theme;
use cyoa_integrations::cyoa_manager::{
    AddOptions, AddOutcome, add_archive_to_cyoa_manager, add_to_cyoa_manager, resolve_db,
};
use eframe::egui::{self, Color32, Frame, Margin, RichText, Stroke};
use std::path::{Path, PathBuf};

/// (icon, title, description, (button fill, text), action key).
type ActionCard = (
    &'static str,
    &'static str,
    &'static str,
    (Color32, Color32),
    &'static str,
);

#[derive(Default)]
pub(crate) struct ManagerCenter {
    pub open: bool,
    db_text: String,
    status: String,
    status_ok: Option<bool>,
    loaded: bool,
}

fn add_json(path: &Path, name: &str, url: &str, db: &Path) -> AddOutcome {
    add_to_cyoa_manager(
        path,
        &AddOptions {
            name,
            source_url: url,
            description: "",
            tags: &[],
            viewer_preference: "",
        },
        db,
    )
}

impl DownloaderApp {
    pub(super) fn manager_center(&mut self, ctx: &egui::Context) {
        if !self.mgr_center.open {
            return;
        }
        if !self.mgr_center.loaded {
            self.mgr_center.loaded = true;
            self.mgr_center.db_text = if self.manager.db_path.trim().is_empty() {
                cyoa_integrations::cyoa_manager::find_db()
                    .map(|p| p.display().to_string())
                    .unwrap_or_default()
            } else {
                self.manager.db_path.clone()
            };
            let ok = Path::new(self.mgr_center.db_text.trim()).exists()
                && !self.mgr_center.db_text.trim().is_empty();
            self.mgr_center.status = if ok {
                "DB found.".into()
            } else {
                "DB not found. Select library.sqlite3 or install/open CYOA Manager once.".into()
            };
            self.mgr_center.status_ok = Some(ok);
        }
        let mut close = false;
        let mut browse_library = false;
        let builder = egui::ViewportBuilder::default()
            .with_title("CYOA Manager")
            .with_inner_size([740.0, 600.0])
            .with_min_inner_size([680.0, 540.0]);
        let enabled = self.manager.enabled;
        let mut toggle: Option<bool> = None;
        let mut action: Option<&'static str> = None;
        let mc = &mut self.mgr_center;
        ctx.show_viewport_immediate(egui::ViewportId::from_hash_of("cyoa_manager"), builder, |ui, _| {
            if ui.ctx().input(|i| i.viewport().close_requested()) {
                close = true;
            }
            let p = theme::palette(ui);
            egui::Panel::top("mgr_header")
                .show_separator_line(false)
                .exact_size(58.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin { left: 18, right: 18, top: 10, bottom: 8 }))
                .show(ui, |ui| {
                    ui.label(theme::bold("📤  CYOA Manager Center", 15.0).color(p.fg));
                    ui.label(
                        RichText::new("Import, export, auto-add, library path, and manual project registration.")
                            .size(10.0)
                            .color(p.muted),
                    );
                });
            egui::Panel::bottom("mgr_footer")
                .show_separator_line(false)
                .exact_size(42.0)
                .frame(Frame::new().fill(p.panel).inner_margin(Margin::symmetric(14, 6)))
                .show(ui, |ui| {
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let b = egui::Button::new(RichText::new("Close").size(13.0).color(p.fg))
                            .fill(p.surface2)
                            .corner_radius(6.0)
                            .min_size(egui::vec2(90.0, 28.0));
                        if ui.add(b).clicked() {
                            close = true;
                        }
                    });
                });
            egui::Panel::bottom("mgr_status")
                .show_separator_line(false)
                .frame(Frame::new().fill(p.bg).inner_margin(Margin { left: 16, right: 16, top: 2, bottom: 6 }))
                .show(ui, |ui| {
                    let color = match mc.status_ok {
                        Some(true) => p.accent,
                        Some(false) => rgb(0xf5, 0x9e, 0x0b),
                        None => p.muted,
                    };
                    ui.label(RichText::new(&mc.status).size(10.0).color(color));
                });
            egui::CentralPanel::default()
                .frame(Frame::new().fill(p.bg).inner_margin(Margin::same(12)))
                .show(ui, |ui| {
                    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                        ui.set_width(ui.available_width() - 12.0);
                        let card = |ui: &mut egui::Ui, add: &mut dyn FnMut(&mut egui::Ui)| {
                            Frame::new()
                                .fill(p.surface)
                                .stroke(Stroke::new(1.0, p.border))
                                .corner_radius(12.0)
                                .inner_margin(Margin::same(14))
                                .outer_margin(Margin::symmetric(6, 5))
                                .show(ui, |ui| {
                                    ui.set_width(ui.available_width());
                                    add(ui);
                                });
                        };
                        card(ui, &mut |ui| {
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("📚").size(18.0).color(p.manager.1));
                                ui.vertical(|ui| {
                                    ui.label(theme::bold("Auto-add successful downloads", 13.0).color(p.fg));
                                    ui.label(
                                        RichText::new("When enabled, successful project.json outputs are registered into CYOA Manager.")
                                            .size(10.0)
                                            .color(p.muted),
                                    );
                                });
                                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                    for (label, on_val, color) in [("OFF", false, rgb(0xdc, 0x26, 0x26)), ("ON", true, rgb(0x16, 0xa3, 0x4a))] {
                                        let on = enabled == on_val;
                                        let b = egui::Button::new(theme::bold(label, 10.0).color(if on { Color32::WHITE } else { p.muted }))
                                            .fill(if on { color } else { p.surface2 })
                                            .corner_radius(7.0)
                                            .min_size(egui::vec2(54.0, 30.0));
                                        if ui.add(b).clicked() {
                                            toggle = Some(on_val);
                                        }
                                    }
                                });
                            });
                        });
                        card(ui, &mut |ui| {
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("🗄").size(18.0).color(p.accent));
                                ui.vertical(|ui| {
                                    ui.horizontal(|ui| {
                                        ui.label(theme::bold("library.sqlite3", 12.0).color(p.fg));
                                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                            let b = egui::Button::new(RichText::new("Browse").size(13.0).color(p.fg))
                                                .fill(p.surface2)
                                                .corner_radius(6.0)
                                                .min_size(egui::vec2(76.0, 30.0));
                                            if ui.add(b).clicked() {
                                                action = Some("browse_db");
                                            }
                                        });
                                    });
                                    ui.horizontal(|ui| {
                                        ui.add(
                                            egui::TextEdit::singleline(&mut mc.db_text)
                                                .font(egui::TextStyle::Monospace)
                                                .hint_text("Select or auto-detect library.sqlite3")
                                                .desired_width(ui.available_width() - 90.0),
                                        );
                                        let b = egui::Button::new(RichText::new("Save").size(13.0).color(Color32::WHITE))
                                            .fill(rgb(0x25, 0x63, 0xeb))
                                            .corner_radius(6.0)
                                            .min_size(egui::vec2(76.0, 30.0));
                                        if ui.add(b).clicked() {
                                            action = Some("save_db");
                                        }
                                    });
                                });
                            });
                        });
                        ui.add_space(8.0);
                        ui.horizontal(|ui| {
                            ui.add_space(6.0);
                            ui.label(theme::bold("LIBRARY ACTIONS", 10.0).color(p.accent));
                        });
                        let actions: [ActionCard; 5] = [
                            ("📚", "Browse CYOA Manager", "Queue URLs or Serve local projects from the Manager library.", p.manager, "browse"),
                            ("📦", "Export to CYOA Manager", "Export finished downloads or selected project.json files to the library.", (rgb(0x6d, 0x28, 0xd9), rgb(0xed, 0xe9, 0xfe)), "export"),
                            ("📄", "Add project.json", "Register one local project.json/project.txt manually.", (rgb(0x25, 0x63, 0xeb), Color32::WHITE), "manual"),
                            ("📋", "Add current session", "Register successful downloads from the latest run.", (p.surface2, p.fg), "session"),
                            ("📁", "Scan folder for project.json", "Recursively find project.json/project.txt and register them.", (p.surface2, p.fg), "scan"),
                        ];
                        for pair in actions.chunks(2) {
                            ui.columns(2, |cols| {
                                for (c, (icon, title, desc, (bg, fg), key)) in pair.iter().enumerate() {
                                    Frame::new()
                                        .fill(p.surface)
                                        .stroke(Stroke::new(1.0, p.border))
                                        .corner_radius(10.0)
                                        .inner_margin(Margin::same(12))
                                        .outer_margin(Margin::symmetric(6, 5))
                                        .show(&mut cols[c], |ui| {
                                            ui.set_width(ui.available_width());
                                            ui.horizontal_top(|ui| {
                                                ui.label(RichText::new(*icon).size(16.0).color(*fg));
                                                let w = ui.available_width() - 84.0;
                                                ui.vertical(|ui| {
                                                    ui.set_width(w);
                                                    ui.label(theme::bold(*title, 12.0).color(p.fg));
                                                    ui.add(egui::Label::new(RichText::new(*desc).size(10.0).color(p.muted)).wrap());
                                                });
                                                ui.allocate_ui_with_layout(egui::vec2(76.0, 40.0), egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                                    let b = egui::Button::new(theme::bold("Open", 10.0).color(*fg))
                                                        .fill(*bg)
                                                        .corner_radius(6.0)
                                                        .min_size(egui::vec2(68.0, 28.0));
                                                    if ui.add(b).clicked() {
                                                        action = Some(key);
                                                    }
                                                });
                                            });
                                        });
                                }
                            });
                        }
                    });
                });
        });
        if let Some(v) = toggle {
            self.manager.enabled = v;
            self.mgr_center.status = if v {
                "Auto-add enabled."
            } else {
                "Auto-add disabled."
            }
            .into();
            self.mgr_center.status_ok = Some(true);
        }
        if let Some(a) = action {
            self.manager_action(a, &mut browse_library);
        }
        if browse_library {
            self.open_library(ctx);
        }
        if close {
            self.mgr_center.open = false;
            self.mgr_center.loaded = false;
        }
    }

    fn manager_db(&self) -> Option<PathBuf> {
        resolve_db(self.mgr_center.db_text.trim())
    }

    fn manager_action(&mut self, action: &str, browse_library: &mut bool) {
        let mc_status = |s: &mut ManagerCenter, text: String, ok: Option<bool>| {
            s.status = text;
            s.status_ok = ok;
        };
        match action {
            "browse_db" => {
                if let Some(p) = rfd::FileDialog::new()
                    .set_title("Select library.sqlite3")
                    .add_filter("SQLite DB", &["sqlite3"])
                    .add_filter("All", &["*"])
                    .pick_file()
                {
                    self.mgr_center.db_text = p.display().to_string();
                    self.manager_action("save_db", browse_library);
                }
            }
            "save_db" => {
                let val = self.mgr_center.db_text.trim().to_owned();
                if !val.is_empty() {
                    self.manager.db_path = val.clone();
                }
                let ok = !val.is_empty() && Path::new(&val).exists();
                mc_status(
                    &mut self.mgr_center,
                    if ok {
                        "DB path saved.".into()
                    } else {
                        "DB path saved, but file was not found.".into()
                    },
                    Some(ok),
                );
            }
            "browse" => *browse_library = true,
            "manual" => {
                let Some(path) = rfd::FileDialog::new()
                    .set_title("Select project JSON or ZIP")
                    .add_filter("CYOA project", &["json", "zip"])
                    .add_filter("All", &["*"])
                    .pick_file()
                else {
                    return;
                };
                let Some(db) = self.manager_db() else {
                    mc_status(
                        &mut self.mgr_center,
                        "Library DB not found.".into(),
                        Some(false),
                    );
                    return;
                };
                let name = path
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let is_zip = path
                    .extension()
                    .is_some_and(|e| e.eq_ignore_ascii_case("zip"));
                let out = if is_zip {
                    add_archive_to_cyoa_manager(&path, &name, "", &db)
                } else {
                    add_json(&path, &name, "", &db)
                };
                let ok = matches!(out, AddOutcome::Added);
                mc_status(
                    &mut self.mgr_center,
                    if ok {
                        "Project added.".into()
                    } else {
                        "Add failed.".into()
                    },
                    Some(ok),
                );
            }
            "session" => {
                let Some(db) = self.manager_db() else {
                    mc_status(
                        &mut self.mgr_center,
                        "Library DB is invalid.".into(),
                        Some(false),
                    );
                    return;
                };
                let out_dir = PathBuf::from(&self.settings.output_dir);
                let mut added = 0;
                let mut failed = 0;
                for j in self
                    .jobs
                    .iter()
                    .filter(|j| matches!(j.status, Status::Done { .. }))
                {
                    // The file this run wrote, not whatever `<name>.json`
                    // already existed (the engine suffixes a taken name).
                    let lower = |p: &PathBuf| p.to_string_lossy().to_lowercase();
                    let written = j
                        .written
                        .iter()
                        .find(|p| {
                            let l = lower(p);
                            l.ends_with(".json")
                                && !l.ends_with("_metadata.json")
                                && !l.ends_with("_original.json")
                                && p.is_file()
                        })
                        .or_else(|| j.written.iter().find(|p| lower(p).ends_with(".zip")))
                        .cloned();
                    let fp =
                        written.unwrap_or_else(|| out_dir.join(format!("{}.json", j.filename)));
                    let ok = if fp.is_file() && lower(&fp).ends_with(".json") {
                        add_json(&fp, &j.filename, &j.url, &db)
                    } else {
                        add_archive_to_cyoa_manager(
                            &fp.with_extension("zip"),
                            &j.filename,
                            &j.url,
                            &db,
                        )
                    };
                    match ok {
                        AddOutcome::Added => added += 1,
                        AddOutcome::Failed => failed += 1,
                        _ => {}
                    }
                }
                mc_status(
                    &mut self.mgr_center,
                    if failed > 0 {
                        format!("{added} session project(s) added, {failed} failed.")
                    } else {
                        format!("{added} session project(s) added.")
                    },
                    Some(true),
                );
            }
            "scan" => {
                let Some(db) = self.manager_db() else {
                    mc_status(
                        &mut self.mgr_center,
                        "Library DB is invalid.".into(),
                        Some(false),
                    );
                    return;
                };
                let Some(folder) = rfd::FileDialog::new()
                    .set_title("Select folder containing project.json")
                    .pick_folder()
                else {
                    return;
                };
                let mut added = 0;
                let mut stack = vec![folder];
                while let Some(dir) = stack.pop() {
                    let Ok(entries) = std::fs::read_dir(&dir) else {
                        continue;
                    };
                    for e in entries.flatten() {
                        let path = e.path();
                        let name = e.file_name().to_string_lossy().to_lowercase();
                        // Never follow links/junctions out of the chosen folder.
                        if cyoa_core::paths::is_link_or_junction(&path) {
                            continue;
                        }
                        if path.is_dir() {
                            if !["audio", "images", "css", "js", "fonts"].contains(&name.as_str()) {
                                stack.push(path);
                            }
                        } else if (name == "project.json" || name == "project.txt")
                            && matches!(
                                add_json(
                                    &path,
                                    &dir.file_name()
                                        .map(|s| s.to_string_lossy().into_owned())
                                        .unwrap_or_default(),
                                    "",
                                    &db
                                ),
                                AddOutcome::Added
                            )
                        {
                            added += 1;
                        }
                    }
                }
                mc_status(
                    &mut self.mgr_center,
                    format!("{added} project file(s) found and added."),
                    Some(true),
                );
            }
            "export" => {
                let Some(db) = self.manager_db() else {
                    mc_status(
                        &mut self.mgr_center,
                        "Library DB is invalid.".into(),
                        Some(false),
                    );
                    return;
                };
                let Some(files) = rfd::FileDialog::new()
                    .set_title("Select project.json/project.txt files")
                    .add_filter("Project files", &["json", "txt"])
                    .add_filter("All files", &["*"])
                    .pick_files()
                else {
                    return;
                };
                let (mut added, mut skipped, mut failed) = (0, 0, 0);
                for fp in files {
                    let name = fp
                        .file_stem()
                        .map(|s| s.to_string_lossy().into_owned())
                        .unwrap_or_default();
                    match add_json(&fp, &name, "", &db) {
                        AddOutcome::Added => added += 1,
                        AddOutcome::AlreadyPresent => skipped += 1,
                        AddOutcome::Failed => failed += 1,
                    }
                }
                mc_status(
                    &mut self.mgr_center,
                    format!("Export done: {added} added, {skipped} existing, {failed} failed."),
                    Some(failed == 0),
                );
            }
            _ => {}
        }
        let _ = muted;
    }
}
