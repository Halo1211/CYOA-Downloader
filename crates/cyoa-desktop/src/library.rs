//! CYOA Manager library panel (Python `gui/library_panel.py`).
//!
//! Loading the SQLite library and preparing a Serve folder both run on
//! background threads; the panel only filters and renders in-memory rows.

use cyoa_integrations::cyoa_manager::{self, ManagerProject};
use eframe::egui;
use std::{
    collections::BTreeSet,
    path::PathBuf,
    sync::mpsc::{Receiver, TryRecvError},
};
use tokio_util::sync::CancellationToken;

/// What the panel asks the app to do after a frame.
#[derive(Debug, PartialEq, Eq)]
pub enum LibraryAction {
    /// `(url, filename)` records for the queue, plus the local-only count.
    Queue(Vec<(String, String)>, usize),
    /// Serve this prepared folder (stop any running server first).
    Serve(PathBuf),
}

/// `PagedCards.PAGE_SIZE`.
const LIB_PAGE: usize = 12;

pub struct LibraryPanel {
    pub open: bool,
    projects: Vec<ManagerProject>,
    search: String,
    matching: Vec<usize>,
    selected: BTreeSet<usize>,
    status: String,
    loading: Option<Receiver<Vec<ManagerProject>>>,
    preparing: Option<Receiver<Result<PathBuf, String>>>,
    cancel: CancellationToken,
    serve_enabled: bool,
    page: usize,
}

/// Python `rebuild`: case-insensitive substring over name, id, URL and file.
pub fn filter(projects: &[ManagerProject], search: &str) -> Vec<usize> {
    let ft = search.trim().to_lowercase();
    projects
        .iter()
        .enumerate()
        .filter(|(_, p)| {
            ft.is_empty()
                || [&p.name, &p.id, &p.source_url, &p.file_path]
                    .iter()
                    .any(|v| v.to_lowercase().contains(&ft))
        })
        .map(|(i, _)| i)
        .collect()
}

/// Python `queue`: selected projects with a URL, in index order.
pub fn queue_records(
    projects: &[ManagerProject],
    selected: &BTreeSet<usize>,
) -> Vec<(String, String)> {
    selected
        .iter()
        .filter_map(|&i| projects.get(i))
        .filter(|p| !p.source_url.is_empty())
        .map(|p| (p.source_url.clone(), p.name.clone()))
        .collect()
}

impl LibraryPanel {
    /// Open the panel and start reading the library (`db_hint` = saved path).
    pub fn open(db_hint: String, serve_enabled: bool, ctx: &egui::Context) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let spawned = std::thread::Builder::new()
            .name("library-load".into())
            .spawn(move || {
                let db = cyoa_manager::resolve_db(&db_hint);
                let projects = match db {
                    Some(db) => cyoa_manager::list_projects(Some(&db)),
                    None => Vec::new(),
                };
                let _ = tx.send(projects);
                ctx.request_repaint();
            });
        Self {
            open: true,
            projects: Vec::new(),
            search: String::new(),
            matching: Vec::new(),
            selected: BTreeSet::new(),
            status: if spawned.is_ok() {
                "Loading…".into()
            } else {
                "Could not start the library reader.".into()
            },
            loading: spawned.ok().map(|_| rx),
            preparing: None,
            cancel: CancellationToken::new(),
            serve_enabled,
            page: 0,
        }
    }

    fn busy(&self) -> bool {
        self.loading.is_some() || self.preparing.is_some()
    }

    fn selection_status(&mut self) {
        self.status = format!(
            "Selected {}; matching {}",
            self.selected.len(),
            self.matching.len()
        );
    }

    fn rebuild(&mut self) {
        self.matching = filter(&self.projects, &self.search);
        self.selection_status();
    }

    fn poll(&mut self) -> Option<LibraryAction> {
        if let Some(rx) = &self.loading {
            match rx.try_recv() {
                Ok(projects) => {
                    self.loading = None;
                    self.projects = projects;
                    self.rebuild();
                }
                Err(TryRecvError::Disconnected) => {
                    self.loading = None;
                    self.status = "Library could not be read.".into();
                }
                Err(TryRecvError::Empty) => {}
            }
        }
        if let Some(rx) = &self.preparing {
            match rx.try_recv() {
                Ok(Ok(folder)) => {
                    self.preparing = None;
                    self.open = false;
                    return Some(LibraryAction::Serve(folder));
                }
                Ok(Err(e)) => {
                    self.preparing = None;
                    self.status = e;
                }
                Err(TryRecvError::Disconnected) => {
                    self.preparing = None;
                    self.status = "Preparing the local viewer failed.".into();
                }
                Err(TryRecvError::Empty) => {}
            }
        }
        None
    }

    fn start_serve(&mut self, net: crate::net::NetRuntime, ctx: &egui::Context) {
        if self.selected.len() != 1 {
            self.status = "Select exactly one project for Serve.".into();
            return;
        }
        let Some(project) = self.selected.first().and_then(|&i| self.projects.get(i)) else {
            return;
        };
        if project.file_path.is_empty() {
            self.status = "This project has no local file.".into();
            return;
        }
        let path = PathBuf::from(&project.file_path);
        let (tx, rx) = std::sync::mpsc::channel();
        let ctx = ctx.clone();
        let cancel = self.cancel.clone();
        let spawned = std::thread::Builder::new()
            .name("library-serve".into())
            .spawn(move || {
                let _ = tx.send(prepare(&path, net, cancel));
                ctx.request_repaint();
            });
        match spawned {
            Ok(_) => {
                self.status = "Preparing local viewer…".into();
                self.preparing = Some(rx);
            }
            Err(e) => self.status = format!("Could not start Serve preparation: {e}"),
        }
    }

    /// Draw the window. `net` is only called when Serve is pressed.
    pub fn ui(
        &mut self,
        ctx: &egui::Context,
        net: impl FnOnce() -> Result<crate::net::NetRuntime, String>,
    ) -> Option<LibraryAction> {
        let mut action = self.poll();
        let mut open = self.open;
        let mut serve_clicked = false;
        let mut close = false;
        let title = format!("CYOA Manager Library — {}", self.projects.len());
        let builder = egui::ViewportBuilder::default()
            .with_title("CYOA Manager Library")
            .with_inner_size([760.0, 580.0])
            .with_min_inner_size([640.0, 460.0]);
        ctx.show_viewport_immediate(
            egui::ViewportId::from_hash_of("cyoa_manager_library"),
            builder,
            |ui, _| {
                use crate::theme;
                use egui::{Color32, CornerRadius, Frame, Margin, RichText, vec2};
                if ui.ctx().input(|i| i.viewport().close_requested()) {
                    close = true;
                }
                let p = theme::palette(ui);
                let ctk_blue = Color32::from_rgb(0x1f, 0x6a, 0xa5);
                egui::CentralPanel::default()
                    .frame(Frame::new().fill(p.bg).inner_margin(Margin::same(16)))
                    .show(ui, |ui| {
                        ui.label(theme::bold(&title, 14.0).color(p.fg));
                        ui.add_space(6.0);
                        ui.label(RichText::new(&self.status).size(13.0).color(p.muted));
                        ui.add_space(6.0);
                        let r = ui.add(
                            egui::TextEdit::singleline(&mut self.search)
                                .hint_text("Search name, URL or file…")
                                .min_size(vec2(0.0, 26.0))
                                .desired_width(f32::INFINITY),
                        );
                        if r.changed() {
                            self.rebuild();
                            self.page = 0;
                        }
                        ui.add_space(8.0);
                        let enabled = !self.busy();
                        let pages = self.matching.len().div_ceil(LIB_PAGE).max(1);
                        self.page = self.page.min(pages - 1);
                        let start = self.page * LIB_PAGE;
                        let end = (start + LIB_PAGE).min(self.matching.len());
                        let list_h = ui.available_height() - 84.0;
                        Frame::new()
                            .fill(p.surface)
                            .corner_radius(CornerRadius::same(8))
                            .inner_margin(Margin::same(10))
                            .show(ui, |ui| {
                                ui.set_min_height(list_h);
                                egui::ScrollArea::vertical()
                                    .id_salt(("library_rows", self.page))
                                    .auto_shrink([false, false])
                                    .max_height(list_h)
                                    .show(ui, |ui| {
                                        if self.loading.is_none() && self.matching.is_empty() {
                                            ui.vertical_centered(|ui| {
                                                ui.add_space(30.0);
                                                ui.label(
                                                    RichText::new("Library not found or empty.")
                                                        .size(13.0)
                                                        .color(p.fg),
                                                );
                                            });
                                            return;
                                        }
                                        let mut changed = false;
                                        for &i in &self.matching[start..end] {
                                            let Some(pr) = self.projects.get(i) else {
                                                continue;
                                            };
                                            let mut on = self.selected.contains(&i);
                                            Frame::new()
                                                .fill(p.bg)
                                                .corner_radius(CornerRadius::same(8))
                                                .inner_margin(Margin::symmetric(8, 6))
                                                .show(ui, |ui| {
                                                    ui.set_width(ui.available_width());
                                                    ui.horizontal(|ui| {
                                                        if theme::checkbox(
                                                            ui, &p, &mut on, "", ctk_blue, true,
                                                        )
                                                        .changed()
                                                        {
                                                            changed = true;
                                                        }
                                                        ui.vertical(|ui| {
                                                            let name = if !pr.name.is_empty() {
                                                                &pr.name
                                                            } else if !pr.id.is_empty() {
                                                                &pr.id
                                                            } else {
                                                                "-"
                                                            };
                                                            let url = if !pr.source_url.is_empty() {
                                                                &pr.source_url
                                                            } else if !pr.file_path.is_empty() {
                                                                &pr.file_path
                                                            } else {
                                                                "No URL or local file"
                                                            };
                                                            ui.label(
                                                                theme::bold(
                                                                    truncate(name, 80),
                                                                    11.0,
                                                                )
                                                                .color(p.fg),
                                                            );
                                                            ui.label(
                                                                RichText::new(truncate(url, 110))
                                                                    .monospace()
                                                                    .size(9.0)
                                                                    .color(p.muted),
                                                            );
                                                        });
                                                    });
                                                });
                                            ui.add_space(4.0);
                                            if on {
                                                self.selected.insert(i);
                                            } else {
                                                self.selected.remove(&i);
                                            }
                                        }
                                        if changed {
                                            self.selection_status();
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
                            let label = if self.matching.is_empty() {
                                "0–0 / 0".to_owned()
                            } else {
                                format!("{}–{} / {}", start + 1, end, self.matching.len())
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
                        ui.add_space(6.0);
                        ui.horizontal(|ui| {
                            let btn = |ui: &mut egui::Ui, t: &str, w: f32, en: bool| {
                                theme::flat(
                                    ui,
                                    RichText::new(t).size(13.0).color(Color32::WHITE),
                                    ctk_blue,
                                    vec2(w, 28.0),
                                    en,
                                )
                                .clicked()
                            };
                            if btn(ui, "Select All Matches", 124.0, enabled) {
                                self.selected.extend(self.matching.iter().copied());
                                self.selection_status();
                            }
                            if btn(ui, "Clear", 114.0, enabled) {
                                self.selected.clear();
                                self.selection_status();
                            }
                            if btn(ui, "Queue URLs", 114.0, enabled) {
                                let records = queue_records(&self.projects, &self.selected);
                                if records.is_empty() {
                                    self.status = "Select projects with URLs.".into();
                                } else {
                                    let skipped = self.selected.len() - records.len();
                                    action = Some(LibraryAction::Queue(records, skipped));
                                    close = true;
                                }
                            }
                            if btn(ui, "Serve Local", 114.0, enabled) {
                                serve_clicked = true;
                            }
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if btn(ui, "Close", 64.0, true) {
                                        close = true;
                                    }
                                },
                            );
                        });
                    });
            },
        );
        if serve_clicked {
            if !self.serve_enabled {
                self.status = "Enable Serve in settings first.".into();
            } else {
                match net() {
                    Ok(net) => self.start_serve(net, ctx),
                    Err(e) => self.status = e,
                }
            }
        }
        if close {
            open = false;
        }
        self.open = open;
        if !self.open {
            self.cancel.cancel();
        }
        action
    }
}

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `prepare_cyoa_manager_serve_folder` with the network fetcher for
/// preserved remote assets. Runs on its own small Tokio runtime.
fn prepare(
    path: &std::path::Path,
    net: crate::net::NetRuntime,
    cancel: CancellationToken,
) -> Result<PathBuf, String> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(1)
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    let _guard = runtime.enter();
    let fetcher = cyoa_engine::offline_viewer::NetworkAssetFetcher::new(
        "", net.proxy, net.dns, net.vpn, false, cancel,
    );
    let no_network = cyoa_engine::offline_viewer::NoNetwork;
    let fetcher: &dyn cyoa_engine::offline_viewer::AssetFetcher = match &fetcher {
        Some(f) => f,
        None => &no_network,
    };
    let result = cyoa_engine::manager_serve::prepare_cyoa_manager_serve_folder(
        path,
        &cyoa_integrations::offline_viewers::viewers_dir(),
        &std::env::temp_dir(),
        fetcher,
    );
    drop(_guard);
    runtime.shutdown_timeout(std::time::Duration::from_secs(1));
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proj(name: &str, url: &str, file: &str) -> ManagerProject {
        ManagerProject {
            id: format!("id-{name}"),
            name: name.into(),
            source_url: url.into(),
            file_path: file.into(),
            ..ManagerProject::default()
        }
    }

    #[test]
    fn filter_matches_python_keys_case_insensitively() {
        let p = vec![
            proj("Alpha", "https://A.example/x", ""),
            proj("beta", "", r"C:\Lib\Beta\project.json"),
            proj("Gamma", "", ""),
        ];
        assert_eq!(filter(&p, ""), vec![0, 1, 2]);
        assert_eq!(filter(&p, "  ALPHA "), vec![0]);
        assert_eq!(filter(&p, "a.example"), vec![0]);
        assert_eq!(filter(&p, "lib\\beta"), vec![1]);
        assert_eq!(filter(&p, "id-gam"), vec![2]);
        assert!(filter(&p, "zzz").is_empty());
    }

    #[test]
    fn queue_skips_local_only_projects() {
        let p = vec![
            proj("a", "https://a.example/", ""),
            proj("b", "", "x/project.json"),
            proj("c", "https://c.example/", ""),
        ];
        let sel: BTreeSet<usize> = [2, 0, 1].into_iter().collect();
        assert_eq!(
            queue_records(&p, &sel),
            vec![
                ("https://a.example/".to_owned(), "a".to_owned()),
                ("https://c.example/".to_owned(), "c".to_owned()),
            ]
        );
    }
}
