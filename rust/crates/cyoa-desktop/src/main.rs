#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod worker;

fn main() -> eframe::Result {
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_title("CYOA Downloader")
            .with_inner_size([1000.0, 700.0])
            .with_min_inner_size([900.0, 600.0]),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native(
        "CYOA Downloader",
        options,
        Box::new(|cc| Ok(Box::new(app::DownloaderApp::new(cc)))),
    )
}
