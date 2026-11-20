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
            status: String::new(),
        }
    }

    pub fn ui(&mut self, ctx: &egui::Context) {
        let mut open = self.open;
        egui::Window::new("Reports Center")
            .id(egui::Id::unique("reports_center"))
            .open(&mut open)
            .default_size([900.0, 560.0])
            .show(ctx, |ui| {
                let p = theme::palette(ui);
                ui.horizontal(|ui| {
                    ui.label(bold("📋 Reports Center", 16.0).color(p.fg));
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let [ok, fail, asset] =
                            [&self.groups[0], &self.groups[1], &self.groups[2]].map(Vec::len);
                        for (text, color) in [
                            (
                                format!("ASSET FAIL {asset}"),
                                Color32::from_rgb(0xb4, 0x53, 0x09),
                            ),
                            (
                                format!("CYOA FAIL {fail}"),
                                Color32::from_rgb(0xb9, 0x1c, 0x1c),
                            ),
                            (format!("OK {ok}"), Color32::from_rgb(0x04, 0x78, 0x57)),
                            (
                                format!("CYOA {}", ok + fail),
                                Color32::from_rgb(0x33, 0x41, 0x55),
                            ),
                        ] {
                            Frame::new()
                                .fill(color)
                                .corner_radius(CornerRadius::same(7))
                                .inner_margin(Margin::symmetric(10, 4))
                                .show(ui, |ui| {
                                    ui.label(bold(text, 10.0).color(Color32::WHITE));
                                });
                        }
                    });
                });
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    for (i, label) in ["All", "CYOA Success", "CYOA Failed", "Asset Failed"]
                        .iter()
                        .enumerate()
                    {
                        let on = self.filter == i;
                        let (fill, color) = if on {
                            (p.accent, Color32::WHITE)
                        } else {
                            (p.surface2, p.muted)
                        };
                        if theme::flat(
                            ui,
                            RichText::new(*label).color(color),
                            fill,
                            vec2(90.0, 30.0),
                            true,
                        )
                        .clicked()
                        {
                            self.filter = i;
                        }
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if theme::flat(
                            ui,
                            RichText::new("Export CSV").color(Color32::WHITE),
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
                            RichText::new("Copy Failed").color(p.fg),
                            p.surface2,
                            vec2(108.0, 30.0),
                            true,
                        )
                        .clicked()
                        {
                            ui.ctx().copy_text(failure_text(&self.groups));
                        }
                    });
                });
                if !self.status.is_empty() {
                    ui.label(RichText::new(&self.status).color(p.muted));
                }
                let shown: &[ResultRow] = match self.filter {
                    0 => &self.rows,
                    i => &self.groups[i - 1],
                };
                if shown.is_empty() {
                    ui.label(RichText::new("No rows in this filter.").color(p.muted));
                    return;
                }
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show_rows(ui, 64.0, shown.len(), |ui, range| {
                        for r in &shown[range] {
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
                                .inner_margin(Margin::same(8))
                                .show(ui, |ui| {
                                    ui.set_width(ui.available_width());
                                    ui.horizontal(|ui| {
                                        let (rect, _) = ui.allocate_exact_size(
                                            vec2(34.0, 34.0),
                                            egui::Sense::hover(),
                                        );
                                        ui.painter().circle_filled(rect.center(), 17.0, accent);
                                        ui.painter().text(
                                            rect.center(),
                                            egui::Align2::CENTER_CENTER,
                                            if ok { "✓" } else { "!" },
                                            egui::FontId::proportional(14.0),
                                            Color32::WHITE,
                                        );
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
                                            let detail = if ok || r.error.is_empty() {
                                                r.url.clone()
                                            } else {
                                                format!(
                                                    "{}\n{}",
                                                    r.url,
                                                    r.error.chars().take(180).collect::<String>()
                                                )
                                            };
                                            ui.label(
                                                RichText::new(detail)
                                                    .monospace()
                                                    .size(9.0)
                                                    .color(if ok { p.muted } else { p.fg }),
                                            );
                                        });
                                    });
                                });
                        }
                    });
            });
        self.open = open;
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
