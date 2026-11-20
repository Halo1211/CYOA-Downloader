//! F-037 V5: offline viewer settings panel (Python
//! `_settings_inline_viewers`): automation toggles, preferred viewer,
//! recommended set, registered archives (register / remove).
//!
//! File dialogs and registration (which may pack a large folder) run on
//! background threads; the panel re-reads the small manifest only after a
//! change, never per frame.

use cyoa_engine::viewer_registry::{
    RegisterOptions, register_offline_viewer, unregister_offline_viewer,
};
use cyoa_integrations::offline_viewers::{get_viewer_recommendations, load_manifest, viewers_dir};
use eframe::egui;
use serde_json::{Map, Value};
use std::{
    path::PathBuf,
    sync::mpsc::{Receiver, TryRecvError},
};

pub const VIEWER_TYPES: &[&str] = &[
    "icc_plus2",
    "icc_plus_legacy",
    "icc_original",
    "icc_remix",
    "lt_ouroumov",
    "cyoap_vue",
    "custom",
];
const AUTO_LABEL: &str = "Auto (recommended)";

/// Python label `f"{meta.get('name', viewer_id)} · {viewer_id}"`.
pub fn viewer_label(id: &str, meta: &Value) -> String {
    let name = meta.get("name").and_then(Value::as_str).unwrap_or(id);
    format!("{name} · {id}")
}

/// Python `_save_preferences` selection lookup: label -> id, else "auto".
pub fn selected_id(manifest: &Map<String, Value>, label: &str) -> String {
    manifest
        .iter()
        .find(|(id, meta)| viewer_label(id, meta) == label)
        .map_or_else(|| "auto".to_owned(), |(id, _)| id.clone())
}

pub fn status_message(json: bool, website: bool, label: &str) -> String {
    let state = if json || website { "ON" } else { "OFF" };
    format!("Viewer automation {state}; selection: {label}.")
}

pub struct ViewerPanel {
    settings_path: PathBuf,
    json_enabled: bool,
    website_enabled: bool,
    preferred: String,
    manifest: Map<String, Value>,
    recommendations: Vec<Value>,
    path: String,
    name: String,
    viewer_type: String,
    status: String,
    pick: Option<Receiver<Option<PathBuf>>>,
    registering: Option<Receiver<Option<String>>>,
    confirm_remove: Option<String>,
}

impl ViewerPanel {
    pub fn open(settings_path: PathBuf) -> Self {
        let saved = cyoa_storage::settings::SettingsStore::new(&settings_path)
            .load()
            .ok()
            .cloned()
            .unwrap_or_default();
        let flag = |k: &str| saved.get(k).and_then(Value::as_bool).unwrap_or(false);
        let mut panel = Self {
            json_enabled: flag("offline_viewer_json_enabled"),
            website_enabled: flag("offline_viewer_website_enabled"),
            preferred: AUTO_LABEL.into(),
            manifest: Map::new(),
            recommendations: Vec::new(),
            path: String::new(),
            name: String::new(),
            viewer_type: "icc_plus2".into(),
            status: String::new(),
            pick: None,
            registering: None,
            confirm_remove: None,
            settings_path,
        };
        let saved_id = saved
            .get("offline_viewer_preferred_id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or("auto")
            .to_owned();
        panel.refresh(&saved_id);
        panel
    }

    /// Python `_refresh_viewers`.
    fn refresh(&mut self, saved_id: &str) {
        let dir = viewers_dir();
        self.manifest = load_manifest(&dir);
        self.recommendations = get_viewer_recommendations(&dir);
        if saved_id != "auto"
            && let Some(meta) = self.manifest.get(saved_id)
        {
            self.preferred = viewer_label(saved_id, meta);
            return;
        }
        if !self.labels().contains(&self.preferred) {
            self.preferred = AUTO_LABEL.into();
        }
    }

    fn labels(&self) -> Vec<String> {
        std::iter::once(AUTO_LABEL.to_owned())
            .chain(self.manifest.iter().map(|(id, m)| viewer_label(id, m)))
            .collect()
    }

    fn save_preferences(&mut self) {
        let id = selected_id(&self.manifest, &self.preferred);
        let (json, web) = (self.json_enabled, self.website_enabled);
        let res = crate::net::update_shared(&self.settings_path, |map| {
            map.insert("offline_viewer_json_enabled".into(), json.into());
            map.insert("offline_viewer_website_enabled".into(), web.into());
            map.insert("offline_viewer_preferred_id".into(), id.into());
        });
        self.status = match res {
            Ok(()) => status_message(json, web, &self.preferred),
            Err(e) => format!("Could not save settings: {e}"),
        };
    }

    fn start_pick(&mut self, folder: bool, ctx: &egui::Context) {
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("viewer-pick".into())
            .spawn(move || {
                let dialog = rfd::FileDialog::new();
                let picked = if folder {
                    dialog.pick_folder()
                } else {
                    dialog
                        .add_filter("Viewer archive", &["zip", "rar"])
                        .pick_file()
                };
                let _ = tx.send(picked);
                ctx.request_repaint();
            });
        if spawned.is_ok() {
            self.pick = Some(rx);
        }
    }

    fn start_register(&mut self, ctx: &egui::Context) {
        let path = self.path.trim().to_owned();
        if path.is_empty() {
            self.status = "Choose an archive or folder first.".into();
            return;
        }
        let name = match self.name.trim() {
            "" => std::path::Path::new(&path)
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
            n => n.to_owned(),
        };
        let opts = RegisterOptions {
            name,
            viewer_type: self.viewer_type.clone(),
            ..RegisterOptions::default()
        };
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("viewer-register".into())
            .spawn(move || {
                let _ = tx.send(register_offline_viewer(
                    &viewers_dir(),
                    std::path::Path::new(&path),
                    &opts,
                ));
                ctx.request_repaint();
            });
        match spawned {
            Ok(_) => {
                self.status = "Registering…".into();
                self.registering = Some(rx);
            }
            Err(e) => self.status = e.to_string(),
        }
    }

    fn poll(&mut self) {
        if let Some(rx) = &self.pick {
            match rx.try_recv() {
                Ok(p) => {
                    self.pick = None;
                    if let Some(p) = p {
                        self.path = p.display().to_string();
                    }
                }
                Err(TryRecvError::Disconnected) => self.pick = None,
                Err(TryRecvError::Empty) => {}
            }
        }
        if let Some(rx) = &self.registering {
            match rx.try_recv() {
                Ok(Some(id)) => {
                    self.registering = None;
                    self.path.clear();
                    self.name.clear();
                    self.status = format!("Registered: {id}");
                    let saved = selected_id(&self.manifest, &self.preferred);
                    self.refresh(&saved);
                }
                Ok(None) | Err(TryRecvError::Disconnected) => {
                    self.registering = None;
                    self.status = "Registration failed".into();
                }
                Err(TryRecvError::Empty) => {}
            }
        }
    }

    /// Python `_settings_inline_viewers`: the Viewers page cards.
    pub fn inline(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        use crate::settings_dashboard::{
            accent_button, badge_label, frame, muted, muted2, rgb, toggle,
        };
        self.poll();
        let p = crate::theme::palette(ui);
        let blue = rgb(0x3b, 0x82, 0xf6);
        let mut changed = false;
        frame(ui, p.border).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                badge_label(ui, "AUTO", blue);
                ui.vertical(|ui| {
                    ui.label(egui::RichText::new("Automatic viewer handling").family(egui::FontFamily::Name("bold".into())).size(13.0));
                    ui.label(
                        egui::RichText::new(
                            "Both workflows are opt-in and remain OFF by default. Auto is safest.",
                        )
                        .size(9.0)
                        .color(muted(ui)),
                    );
                });
            });
            egui::Frame::new()
                .fill(p.bg)
                .corner_radius(10.0)
                .inner_margin(egui::Margin::symmetric(18, 10))
                .show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    for (on, label, hint) in [
                        (
                            &mut self.json_enabled,
                            "JSON-only downloads → create a playable viewer folder",
                            "Uses project schema/version plus HTML evidence to choose a compatible runtime.",
                        ),
                        (
                            &mut self.website_enabled,
                            "Full ICC website downloads → modernize viewer for file://",
                            "Legacy is patched in place; Plus 2/Remix use matched local templates and preserve custom HTML.",
                        ),
                    ] {
                        ui.horizontal(|ui| {
                            changed |= toggle(ui, on, blue);
                            ui.label(egui::RichText::new(label).size(12.0));
                        });
                        ui.label(egui::RichText::new(hint).size(9.0).color(muted(ui)));
                        ui.add_space(6.0);
                    }
                });
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("Viewer selection").family(egui::FontFamily::Name("bold".into())).size(10.0));
                egui::ComboBox::from_id_salt("preferred_viewer_inline")
                    .selected_text(self.preferred.clone())
                    .width(ui.available_width() - 8.0)
                    .show_ui(ui, |ui| {
                        for label in self.labels() {
                            changed |= ui
                                .selectable_value(&mut self.preferred, label.clone(), label)
                                .changed();
                        }
                    });
            });
            ui.label(
                egui::RichText::new(
                    "Manual choice overrides compatibility detection. Use only for testing.",
                )
                .size(9.0)
                .color(muted(ui)),
            );
        });
        if changed {
            self.save_preferences();
        }

        frame(ui, p.border).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.label(egui::RichText::new("Recommended viewer set").family(egui::FontFamily::Name("bold".into())).size(12.0));
            ui.label(
                egui::RichText::new(
                    "Install both Required families before enabling Auto; Plus 2 must be the offline/local build.",
                )
                .size(9.0)
                .color(muted(ui)),
            );
            for rec in &self.recommendations {
                let ready = rec.get("available").and_then(Value::as_bool).unwrap_or(false);
                let required = rec.get("required").and_then(Value::as_bool).unwrap_or(false);
                let (badge, bg, fg) = if ready {
                    ("READY", rgb(0x14, 0x53, 0x2d), rgb(0xbb, 0xf7, 0xd0))
                } else if required {
                    ("MISSING · REQUIRED", rgb(0x7f, 0x1d, 0x1d), rgb(0xfe, 0xca, 0xca))
                } else {
                    ("MISSING · OPTIONAL", p.surface2, muted(ui))
                };
                egui::Frame::new()
                    .fill(p.bg)
                    .corner_radius(8.0)
                    .inner_margin(egui::Margin::symmetric(14, 10))
                    .outer_margin(egui::Margin::symmetric(0, 3))
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.horizontal(|ui| {
                            ui.vertical(|ui| {
                                ui.label(
                                    egui::RichText::new(
                                        rec.get("title_en").and_then(Value::as_str).unwrap_or(""),
                                    )
                                    .family(egui::FontFamily::Name("bold".into()))
                                    .size(10.0),
                                );
                                ui.label(
                                    egui::RichText::new(
                                        rec.get("use_en").and_then(Value::as_str).unwrap_or(""),
                                    )
                                    .size(9.0)
                                    .color(muted(ui)),
                                );
                            });
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    egui::Frame::new()
                                        .fill(bg)
                                        .corner_radius(7.0)
                                        .inner_margin(egui::Margin::symmetric(14, 5))
                                        .show(ui, |ui| {
                                            ui.set_width(100.0);
                                            ui.vertical_centered(|ui| {
                                                ui.label(
                                                    egui::RichText::new(badge)
                                                        .family(egui::FontFamily::Name("bold".into()))
                                                        .size(8.0)
                                                        .color(fg),
                                                );
                                            });
                                        });
                                },
                            );
                        });
                    });
            }
        });

        frame(ui, p.border).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new("Registered archives")
                        .family(egui::FontFamily::Name("bold".into()))
                        .size(12.0),
                );
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("Open folder").clicked() {
                        let dir = viewers_dir();
                        let _ = std::fs::create_dir_all(&dir);
                        if let Err(e) = open_folder(&dir) {
                            self.status = format!("Could not open folder: {e}");
                        }
                    }
                });
            });
            let busy = self.pick.is_some() || self.registering.is_some();
            ui.horizontal(|ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.path)
                        .hint_text("Viewer ZIP/RAR or unpacked folder")
                        .desired_width(ui.available_width() - 180.0),
                );
                if ui
                    .add_enabled(!busy, egui::Button::new("Archive…"))
                    .clicked()
                {
                    self.start_pick(false, ctx);
                }
                if ui
                    .add_enabled(!busy, egui::Button::new("Folder…"))
                    .clicked()
                {
                    self.start_pick(true, ctx);
                }
            });
            ui.horizontal(|ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.name)
                        .hint_text("Display name")
                        .desired_width(ui.available_width() - 330.0),
                );
                egui::ComboBox::from_id_salt("viewer_type_inline")
                    .selected_text(self.viewer_type.clone())
                    .width(200.0)
                    .show_ui(ui, |ui| {
                        for t in VIEWER_TYPES {
                            ui.selectable_value(&mut self.viewer_type, (*t).to_owned(), *t);
                        }
                    });
                if !busy && accent_button(ui, "Register", blue) {
                    self.start_register(ctx);
                }
            });
            if self.manifest.is_empty() {
                ui.label(
                    egui::RichText::new("No offline viewers registered.")
                        .size(9.0)
                        .color(muted(ui)),
                );
            }
            let mut remove = None;
            for (id, meta) in &self.manifest {
                let s =
                    |k: &str, d: &str| meta.get(k).and_then(Value::as_str).unwrap_or(d).to_owned();
                egui::Frame::new()
                    .fill(p.bg)
                    .corner_radius(8.0)
                    .inner_margin(egui::Margin::symmetric(14, 8))
                    .outer_margin(egui::Margin::symmetric(0, 3))
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.horizontal(|ui| {
                            ui.vertical(|ui| {
                                ui.label(
                                    egui::RichText::new(s("name", id))
                                        .family(egui::FontFamily::Name("bold".into()))
                                        .size(10.0),
                                );
                                ui.label(
                                    egui::RichText::new(format!(
                                        "{} · {}",
                                        s("viewer_type", "custom"),
                                        s("zip_filename", "")
                                    ))
                                    .size(8.0)
                                    .color(muted2(ui)),
                                );
                            });
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.add_enabled(!busy, egui::Button::new("Remove")).clicked()
                                    {
                                        remove = Some(id.clone());
                                    }
                                },
                            );
                        });
                    });
            }
            if remove.is_some() {
                self.confirm_remove = remove;
            }
            if let Some(id) = self.confirm_remove.clone() {
                ui.horizontal(|ui| {
                    ui.label(format!(
                        "Remove '{id}' from the registry? The archive remains on disk."
                    ));
                    if ui.button("Yes").clicked() {
                        unregister_offline_viewer(&viewers_dir(), &id, false);
                        self.status = format!("Removed: {id}");
                        self.confirm_remove = None;
                        let saved = selected_id(&self.manifest, &self.preferred);
                        self.refresh(&saved);
                    }
                    if ui.button("No").clicked() {
                        self.confirm_remove = None;
                    }
                });
            }
            ui.label(egui::RichText::new(&self.status).size(9.0).color(muted(ui)));
        });
    }
}

fn open_folder(dir: &std::path::Path) -> std::io::Result<()> {
    #[cfg(windows)]
    let mut cmd = std::process::Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = std::process::Command::new("xdg-open");
    cmd.arg(dir).spawn().map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_and_selection_follow_python() {
        let m: Map<String, Value> =
            serde_json::from_str(r#"{"a":{"name":"Viewer A"},"b":{}}"#).unwrap_or_default();
        assert_eq!(viewer_label("a", &m["a"]), "Viewer A · a");
        assert_eq!(viewer_label("b", &m["b"]), "b · b");
        assert_eq!(selected_id(&m, "b · b"), "b");
        assert_eq!(selected_id(&m, AUTO_LABEL), "auto");
        assert_eq!(selected_id(&m, "gone · x"), "auto");
        assert_eq!(
            status_message(false, true, "b · b"),
            "Viewer automation ON; selection: b · b."
        );
        assert_eq!(
            status_message(false, false, AUTO_LABEL),
            "Viewer automation OFF; selection: Auto (recommended)."
        );
    }
}
