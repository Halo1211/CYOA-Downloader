#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod library;
mod net;
mod preflight_panel;
mod reports_panel;
mod theme;
mod updates_panel;
mod viewers;
mod worker;

fn main() -> eframe::Result {
    // File log only; the GUI shows its own log view.
    cyoa_engine::logfile::init(false);
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_title(format!("CYOA Downloader v{}", cyoa_core::app_version()))
            .with_inner_size([1110.0, 724.0])
            .with_min_inner_size([960.0, 640.0])
            .with_icon(eframe::egui::IconData {
                rgba: include_bytes!("../assets/icon-64.rgba").to_vec(),
                width: 64,
                height: 64,
            }),
        renderer: eframe::Renderer::Glow,
        // Python opens at its responsive default size every time.
        persist_window: false,
        ..Default::default()
    };
    eframe::run_native(
        "CYOA Downloader",
        options,
        Box::new(|cc| Ok(Box::new(app::DownloaderApp::new(cc)))),
    )
}
