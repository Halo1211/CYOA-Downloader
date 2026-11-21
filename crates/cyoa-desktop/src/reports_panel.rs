//! Python Reports Center (`gui/results_panel.py`): CYOA job results and
//! failed assets with badges, filters, cards, Export CSV and Copy Failed.

use crate::theme::{self, bold};
use eframe::egui::{self, Color32, CornerRadius, Frame, Margin, RichText, Stroke, vec2};

/// One report row (Python `FIELDS`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResultRow {
    pub result_type: String,
    pub status: String,
    pub url: String,
    pub mode: String,
    pub filename: String,
    pub error: String,
}

impl ResultRow {
    pub fn failed(&self) -> bool {
        self.status != "OK" && self.status != "SKIP"
    }
    fn is_asset(&self) -> bool {
        self.result_type.eq_ignore_ascii_case("asset")
    }
}

/// Python `_v24_partition_result_rows` groups.
pub fn partition(rows: &[ResultRow]) -> [Vec<ResultRow>; 3] {
    let mut out = [Vec::new(), Vec::new(), Vec::new()];
    for r in rows {
        let i = match (r.is_asset(), r.failed()) {
            (false, false) => 0,
            (false, true) => 1,
            (true, true) => 2,
            (true, false) => continue,
        };
        out[i].push(r.clone());
    }
    out
}

/// Python `write_results_csv` body (CRLF rows, no BOM).
pub fn results_csv(rows: &[ResultRow]) -> Result<Vec<u8>, String> {
    let mut w = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(Vec::new());
    w.write_record(["result_type", "status", "url", "mode", "filename", "error"])
        .map_err(|e| e.to_string())?;
    for r in rows {
        w.write_record([
            &r.result_type,
            &r.status,
            &r.url,
            &r.mode,
            &r.filename,
            &r.error,
        ])
        .map_err(|e| e.to_string())?;
    }
    w.into_inner().map_err(|e| e.to_string())
}

/// Python `failure_text`.
pub fn failure_text(groups: &[Vec<ResultRow>; 3]) -> String {
    let mut sections = Vec::new();
    for (rows, heading) in [
        (&groups[1], "FAILED CYOA"),
        (&groups[2], "FAILED ASSETS / IMAGES"),
    ] {
        if !rows.is_empty() {
            let lines: Vec<String> = rows
                .iter()
                .map(|r| format!("{}\t{}", r.url, r.error))
                .collect();
            sections.push(format!("{heading}\n{}", lines.join("\n")));
        }
    }
    sections.join("\n\n")
}

pub struct ReportsPanel {
    pub open: bool,
    rows: Vec<ResultRow>,
    groups: [Vec<ResultRow>; 3],
    filter: usize,
    page: usize,
    status: String,
}

impl ReportsPanel {
    pub fn new(rows: Vec<ResultRow>) -> Self {
        let groups = partition(&rows);
        Self {
            open: true,
            rows,
            groups,
            filter: 0,
            page: 0,
            status: String::new(),
        }
    }

    /// Python `open_results_panel`: its own window (980x640, min 820x520),
    /// header with count badges, filters, paged cards (12 per page).
    pub fn ui(&mut self, ctx: &egui::Context) {
        let mut close = false;
        let builder = egui::ViewportBuilder::default()
            .with_title("Reports Center")
            .with_inner_size([980.0, 640.0])
            .with_min_inner_size([820.0, 520.0]);
        ctx.show_viewport_immediate(
            egui::ViewportId::from_hash_of("reports_center"),
            builder,
            |ui, _| {
                if ui.ctx().input(|i| i.viewport().close_requested()) {
                    close = true;
                }
                let p = theme::palette(ui);
                let ctk_blue = Color32::from_rgb(0x1f, 0x6a, 0xa5);
                egui::Panel::top("reports_header")
                    .show_separator_line(false)
                    .exact_size(68.0)
                    .frame(
                        Frame::new()
                            .fill(p.panel)
                            .inner_margin(Margin::symmetric(18, 0)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal_centered(|ui| {
                            ui.label(bold("📋 Reports Center", 16.0).color(p.fg));
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    let [ok, fail, asset] =
                                        [&self.groups[0], &self.groups[1], &self.groups[2]]
                                            .map(Vec::len);
                                    for (text, color, w) in [
                                        (
                                            format!("ASSET FAIL {asset}"),
                                            Color32::from_rgb(0xb4, 0x53, 0x09),
                                            120.0,
                                        ),
                                        (
                                            format!("CYOA FAIL {fail}"),
                                            Color32::from_rgb(0xb9, 0x1c, 0x1c),
                                            116.0,
                                        ),
                                        (
                                            format!("OK {ok}"),
                                            Color32::from_rgb(0x04, 0x78, 0x57),
                                            82.0,
                                        ),
                                        (
                                            format!("CYOA {}", ok + fail),
                                            Color32::from_rgb(0x33, 0x41, 0x55),
                                            96.0,
                                        ),
                                    ] {
                                        let b = egui::Button::new(
                                            bold(text, 10.0).color(Color32::WHITE),
                                        )
                                        .fill(color)
                                        .corner_radius(CornerRadius::same(8))
                                        .min_size(vec2(w, 32.0));
                                        ui.add(b);
                                    }
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
                        ui.horizontal(|ui| {
                            for (i, label) in ["All", "CYOA Success", "CYOA Failed", "Asset Failed"]
                                .iter()
                                .enumerate()
                            {
                                let on = self.filter == i;
                                let (fill, color) = if on {
                                    (Color32::from_rgb(0x3b, 0x82, 0xf6), Color32::WHITE)
                                } else {
                                    (p.surface2, p.muted)
                                };
                                if theme::flat(
                                    ui,
                                    RichText::new(*label).size(13.0).color(color),
                                    fill,
                                    vec2(90.0, 30.0),
                                    true,
                                )
                                .clicked()
                                {
                                    self.filter = i;
                                    self.page = 0;
                                }
                            }
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if theme::flat(
                                        ui,
                                        RichText::new("Export CSV")
                                            .size(13.0)
                                            .color(Color32::WHITE),
                                        Color32::from_rgb(0x0f, 0x76, 0x6e),
                                        vec2(104.0, 30.0),
                                        true,
                                    )
                                    .clicked()
                                    {
                                        self.export();
                                    }
                                    if theme::flat(
                                        ui,
                                        RichText::new("Copy Failed")
                                            .size(13.0)
                                            .color(Color32::WHITE),
                                        ctk_blue,
                                        vec2(108.0, 30.0),
                                        true,
                                    )
                                    .clicked()
                                    {
                                        ui.ctx().copy_text(failure_text(&self.groups));
                                    }
                                },
                            );
                        });
                        ui.add_space(8.0);
                        ui.label(RichText::new(&self.status).color(p.muted));
                        let shown: Vec<ResultRow> = match self.filter {
                            0 => self.rows.clone(),
                            i => self.groups[i - 1].clone(),
                        };
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
                                    .id_salt(("reports", self.filter, self.page))
                                    .auto_shrink([false, false])
                                    .max_height(list_h)
                                    .show(ui, |ui| {
                                        if shown.is_empty() {
                                            ui.label(
                                                RichText::new("No rows in this filter.")
                                                    .color(p.muted),
                                            );
                                        }
                                        for r in &shown[start..end] {
                                            result_card(ui, &p, r);
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
                                "0 / 0".to_owned()
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
        }
    }

    fn export(&mut self) {
        let rows = self.rows.clone();
        let Some(path) = rfd::FileDialog::new()
            .set_file_name("download_results.csv")
            .add_filter("CSV", &["csv"])
            .save_file()
        else {
            return;
        };
        self.status = match results_csv(&rows).and_then(|b| {
            cyoa_storage::atomic_io::atomic_write_bytes(&path, &b)
                .map(|_| ())
                .map_err(|e| e.to_string())
        }) {
            Ok(()) => {
                tracing::info!("Results exported: {}", path.display());
                "Exported".into()
            }
            Err(e) => {
                rfd::MessageDialog::new()
                    .set_level(rfd::MessageLevel::Error)
                    .set_title("Reports")
                    .set_description(&e)
                    .show();
                "Export failed".into()
            }
        };
    }
}

/// `PagedCards.PAGE_SIZE`.
const PAGE_SIZE: usize = 12;

/// Python results card: status circle, title, URL (and error).
fn result_card(ui: &mut egui::Ui, p: &theme::Palette, r: &ResultRow) {
    let ok = !r.failed();
    let asset = r.is_asset();
    let accent = if ok {
        Color32::from_rgb(0x22, 0xc5, 0x5e)
    } else if asset {
        Color32::from_rgb(0xf5, 0x9e, 0x0b)
    } else {
        Color32::from_rgb(0xef, 0x44, 0x44)
    };
    Frame::new()
        .fill(p.surface)
        .stroke(Stroke::new(1.0, p.border))
        .corner_radius(CornerRadius::same(10))
        .inner_margin(Margin {
            left: 12,
            right: 12,
            top: 10,
            bottom: 10,
        })
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                let (rect, _) = ui.allocate_exact_size(vec2(50.0, 34.0), egui::Sense::hover());
                ui.painter()
                    .rect_filled(rect, CornerRadius::same(17), accent);
                ui.painter().text(
                    rect.center(),
                    egui::Align2::CENTER_CENTER,
                    if ok { "✓" } else { "!" },
                    egui::FontId::new(14.0, egui::FontFamily::Name("bold".into())),
                    Color32::WHITE,
                );
                ui.add_space(8.0);
                ui.vertical(|ui| {
                    let name = if r.filename.is_empty() {
                        "[auto]"
                    } else {
                        &r.filename
                    };
                    let title = format!(
                        "{}  ·  {name}  ·  {}",
                        if asset { "ASSET" } else { "CYOA" },
                        r.mode.replace('_', " ")
                    );
                    ui.label(bold(title, 12.0).color(p.fg));
                    ui.add_space(6.0);
                    let detail = if ok || r.error.is_empty() {
                        r.url.clone()
                    } else {
                        format!(
                            "{}
{}",
                            r.url,
                            r.error.chars().take(180).collect::<String>()
                        )
                    };
                    ui.label(RichText::new(detail).monospace().size(9.0).color(if ok {
                        p.muted
                    } else {
                        p.fg
                    }));
                });
            });
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(t: &str, s: &str, url: &str, err: &str) -> ResultRow {
        ResultRow {
            result_type: t.into(),
            status: s.into(),
            url: url.into(),
            mode: "website_zip".into(),
            filename: String::new(),
            error: err.into(),
        }
    }

    #[test]
    fn csv_and_failure_text_follow_python() {
        let rows = vec![
            row("job", "OK", "https://a.example/", ""),
            row("job", "FAIL", "https://b.example/", "HTTP 404, gone"),
            row("asset", "FAIL", "https://a.example/x.png", "timeout"),
        ];
        let g = partition(&rows);
        assert_eq!([g[0].len(), g[1].len(), g[2].len()], [1, 1, 1]);
        assert_eq!(
            String::from_utf8(results_csv(&rows).unwrap_or_default()).unwrap_or_default(),
            "result_type,status,url,mode,filename,error\r\njob,OK,https://a.example/,website_zip,,\r\njob,FAIL,https://b.example/,website_zip,,\"HTTP 404, gone\"\r\nasset,FAIL,https://a.example/x.png,website_zip,,timeout\r\n"
        );
        assert_eq!(
            failure_text(&g),
            "FAILED CYOA\nhttps://b.example/\tHTTP 404, gone\n\nFAILED ASSETS / IMAGES\nhttps://a.example/x.png\ttimeout"
        );
    }
}
