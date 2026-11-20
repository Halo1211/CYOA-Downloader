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
    pub open: bool,
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
            open: true,
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

    pub fn ui(&mut self, ctx: &egui::Context) {
        self.poll();
        let mut open = self.open;
        egui::Window::new("Offline viewers")
            .id(egui::Id::unique("offline_viewer_panel"))
            .open(&mut open)
            .default_size([720.0, 560.0])
            .show(ctx, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| self.body(ui, ctx));
            });
        self.open = open;
    }

    fn body(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        ui.heading("Automatic viewer handling");
        ui.label(
            egui::RichText::new(
                "Both workflows are opt-in and remain OFF by default. Auto is safest.",
            )
            .weak(),
        );
        let mut changed = ui
            .checkbox(
                &mut self.json_enabled,
                "JSON-only downloads → create a playable viewer folder",
            )
            .on_hover_text(
                "Uses project schema/version plus HTML evidence to choose a compatible runtime.",
            )
            .changed();
        changed |= ui
            .checkbox(&mut self.website_enabled, "Full ICC website downloads → modernize viewer for file://")
            .on_hover_text("Legacy is patched in place; Plus 2/Remix use matched local templates and preserve custom HTML.")
            .changed();
        ui.horizontal(|ui| {
            ui.label("Viewer selection:");
            egui::ComboBox::from_id_salt("preferred_viewer")
                .selected_text(self.preferred.clone())
                .width(320.0)
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
            .weak(),
        );
        if changed {
            self.save_preferences();
        }
        ui.separator();
        ui.strong("Recommended viewer set");
        ui.label(
            egui::RichText::new(
                "Install both Required families before enabling Auto; Plus 2 must be the offline/local build.",
            )
            .weak(),
        );
        for rec in &self.recommendations {
            let ready = rec
                .get("available")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let required = rec
                .get("required")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let (badge, color) = if ready {
                ("READY", egui::Color32::from_rgb(0x22, 0xc5, 0x5e))
            } else if required {
                (
                    "MISSING · REQUIRED",
                    egui::Color32::from_rgb(0xef, 0x44, 0x44),
                )
            } else {
                ("MISSING · OPTIONAL", ui.visuals().weak_text_color())
            };
            ui.horizontal(|ui| {
                ui.colored_label(color, egui::RichText::new(badge).strong());
                ui.vertical(|ui| {
                    ui.strong(rec.get("title_en").and_then(Value::as_str).unwrap_or(""));
                    ui.label(
                        egui::RichText::new(
                            rec.get("use_en").and_then(Value::as_str).unwrap_or(""),
                        )
                        .weak(),
                    );
                });
            });
        }
        ui.separator();
        ui.horizontal(|ui| {
            ui.strong("Registered archives");
            if ui.button("Open folder").clicked() {
                let dir = viewers_dir();
                let _ = std::fs::create_dir_all(&dir);
                if let Err(e) = open_folder(&dir) {
                    self.status = format!("Could not open folder: {e}");
                }
            }
        });
        let busy = self.pick.is_some() || self.registering.is_some();
        ui.horizontal(|ui| {
            ui.add(
                egui::TextEdit::singleline(&mut self.path)
                    .hint_text("Viewer ZIP/RAR or unpacked folder")
                    .desired_width(360.0),
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
                    .desired_width(220.0),
            );
            egui::ComboBox::from_id_salt("viewer_type")
                .selected_text(self.viewer_type.clone())
                .show_ui(ui, |ui| {
                    for t in VIEWER_TYPES {
                        ui.selectable_value(&mut self.viewer_type, (*t).to_owned(), *t);
                    }
                });
            if ui
                .add_enabled(!busy, egui::Button::new("Register"))
                .clicked()
            {
                self.start_register(ctx);
            }
        });
        if self.manifest.is_empty() {
            ui.label(egui::RichText::new("No offline viewers registered.").weak());
        }
        let mut remove = None;
        for (id, meta) in &self.manifest {
            let s = |k: &str, d: &str| meta.get(k).and_then(Value::as_str).unwrap_or(d).to_owned();
            ui.group(|ui| {
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.strong(s("name", id));
                        ui.label(
                            egui::RichText::new(format!(
                                "{} · {}",
                                s("viewer_type", "custom"),
                                s("zip_filename", "")
                            ))
                            .small()
                            .weak(),
                        );
                    });
                    if ui.add_enabled(!busy, egui::Button::new("Remove")).clicked() {
                        remove = Some(id.clone());
                    }
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
        ui.label(egui::RichText::new(&self.status).weak());
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
