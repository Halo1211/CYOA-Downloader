use std::process::ExitCode;

fn main() -> ExitCode {
    if std::env::args().nth(1).as_deref() == Some("--version") {
        println!("CYOA Downloader {}", cyoa_core::app_version());
        ExitCode::SUCCESS
    } else {
        eprintln!("CYOA Downloader: not yet implemented");
        ExitCode::from(2)
    }
}
