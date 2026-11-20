//! Developer aid for GUI parity work: with `CYOA_DESKTOP_SHOTS=<dir>` the app
//! walks through its windows (main, every Settings page, Help, Credits,
//! Diagnostics), saves a PNG of each, then closes. Without the variable this
//! module does nothing.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, channel};

use eframe::egui;

/// The views the script captures, in order.
pub const STEPS: &[&str] = &[
    "main",
    "settings_0",
    "settings_1",
    "settings_2",
    "settings_3",
    "settings_4",
    "help",
    "credits",
    "diagnostics",
    "reports",
    "manager",
];

pub struct ShotScript {
    dir: PathBuf,
    step: usize,
    frames: u32,
    pending: Option<Receiver<Arc<egui::ColorImage>>>,
}

impl ShotScript {
    pub fn from_env() -> Option<Self> {
        let dir = PathBuf::from(std::env::var_os("CYOA_DESKTOP_SHOTS")?);
        std::fs::create_dir_all(&dir).ok()?;
        Some(Self {
            dir,
            step: 0,
            frames: 0,
            pending: None,
        })
    }

    /// The step to display now, or None when finished.
    pub fn current(&self) -> Option<&'static str> {
        STEPS.get(self.step).copied()
    }

    /// Drive the script: wait for the view to settle, capture, save, advance.
    pub fn tick(&mut self, ctx: &egui::Context, target: egui::ViewportId) {
        let _ = target;
        let target = egui::ViewportId::ROOT;
        ctx.request_repaint();
        if self.step == 0 && self.frames == 0 {
            // Child viewports are rendered inside the root window while
            // capturing so a single screenshot shows them.
            ctx.set_embed_viewports(true);
        }
        if self.current().is_some_and(|s| s != "main") && self.frames == 1 {
            ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(egui::vec2(1110.0, 800.0)));
        }
        if let Some(rx) = &self.pending {
            if let Ok(image) = rx.try_recv() {
                if let Some(name) = self.current() {
                    let path = self.dir.join(format!("rs_{name}.png"));
                    if let Err(e) = save_png(&path, &image) {
                        eprintln!("screenshot {name}: {e}");
                    }
                }
                self.pending = None;
                self.step += 1;
                self.frames = 0;
                if self.current().is_none() {
                    ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                }
            }
            return;
        }
        self.frames += 1;
        if self.frames > 40 && self.current().is_some() {
            let (tx, rx) = channel();
            let waker = ctx.clone();
            ctx.send_viewport_cmd_to(
                target,
                egui::ViewportCommand::Screenshot(egui::ScreenshotCallback::new(move |image| {
                    let _ = tx.send(image);
                    waker.request_repaint();
                })),
            );
            self.pending = Some(rx);
        }
    }
}

fn save_png(path: &std::path::Path, image: &egui::ColorImage) -> Result<(), String> {
    let file = std::fs::File::create(path).map_err(|e| e.to_string())?;
    let mut enc = png::Encoder::new(
        std::io::BufWriter::new(file),
        image.size[0] as u32,
        image.size[1] as u32,
    );
    enc.set_color(png::ColorType::Rgba);
    enc.set_depth(png::BitDepth::Eight);
    let mut writer = enc.write_header().map_err(|e| e.to_string())?;
    let bytes: Vec<u8> = image.pixels.iter().flat_map(|c| c.to_array()).collect();
    writer.write_image_data(&bytes).map_err(|e| e.to_string())
}

/// The Python screenshot sample (2 OK, 1 FAIL).
pub fn sample_rows() -> Vec<crate::reports_panel::ResultRow> {
    let row = |url: &str, mode: &str, status: &str, name: &str, error: &str| {
        crate::reports_panel::ResultRow {
            result_type: "job".into(),
            status: status.into(),
            url: url.into(),
            mode: mode.into(),
            filename: name.into(),
            error: error.into(),
        }
    };
    vec![
        row(
            "https://example.com/cyoa-one/",
            "website_folder",
            "OK",
            "Example_One",
            "",
        ),
        row(
            "https://example.com/cyoa-two/",
            "zip",
            "OK",
            "Example_Two",
            "",
        ),
        row(
            "https://example.com/broken/",
            "auto",
            "FAIL",
            "Broken",
            "Could not resolve project data",
        ),
    ]
}
