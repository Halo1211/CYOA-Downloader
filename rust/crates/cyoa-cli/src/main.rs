use clap::Parser;
use cyoa_engine::{
    pipeline::Progress,
    run::{self, OutputMode, RunOptions},
};
use std::{path::PathBuf, process::ExitCode};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Parser)]
#[command(
    name = "cyoa-downloader",
    version,
    about = "Download and package a CYOA project"
)]
struct Args {
    /// Project or viewer URL
    url: Option<String>,
    /// Project or viewer URL (alternative to the positional URL)
    #[arg(short = 'u', long = "url")]
    url_option: Option<String>,
    /// Output directory (default: current directory)
    #[arg(short = 'o', long = "output", default_value_os_t = std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))]
    output: PathBuf,
    /// Write a ZIP package
    #[arg(short = 'z', long = "zip")]
    zip: bool,
    /// Write both embedded JSON and ZIP package
    #[arg(short = 'b', long = "both")]
    both: bool,
    /// Parallel asset workers (default: 4)
    #[arg(short = 't', long = "threads", alias = "workers", default_value_t = 4)]
    workers: usize,
    /// Seconds to wait after a 429 response (default: 60)
    #[arg(short = 'w', long = "wait-time", alias = "wait", default_value_t = 60)]
    wait_time: u64,
}

#[tokio::main]
async fn main() -> ExitCode {
    if std::env::args_os().len() == 1 {
        let _ = Args::command().print_help();
        println!();
        return ExitCode::SUCCESS;
    }
    let args = Args::parse();
    let Some(url) = args.url_option.or(args.url) else {
        eprintln!("error: a project URL is required (use positional URL or --url)");
        return ExitCode::from(2);
    };
    if args.workers == 0 {
        eprintln!("error: --threads must be at least 1");
        return ExitCode::from(2);
    }
    if !(url.starts_with("http://") || url.starts_with("https://")) || url.contains(' ') {
        eprintln!("error: invalid URL '{url}': expected an absolute http:// or https:// URL");
        return ExitCode::from(2);
    }
    if let Err(error) = std::fs::create_dir_all(&args.output) {
        eprintln!(
            "error: cannot create output directory '{}': {error}",
            args.output.display()
        );
        return ExitCode::from(2);
    }
    if !args.output.is_dir() {
        eprintln!(
            "error: output path '{}' is not a directory",
            args.output.display()
        );
        return ExitCode::from(2);
    }

    let options = RunOptions {
        url,
        output_dir: args.output,
        mode: if args.both {
            OutputMode::Both
        } else if args.zip {
            OutputMode::Zip
        } else {
            OutputMode::Embed
        },
        workers: args.workers,
        wait_time: args.wait_time,
    };
    let cancel = CancellationToken::new();
    let (tx, mut rx) = mpsc::channel::<Progress>(32);
    let printer = tokio::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                Progress::Started { total } => eprintln!("Processing {total} asset(s)..."),
                Progress::AssetDone {
                    done,
                    total,
                    ok,
                    name,
                } => eprintln!(
                    "[{done}/{total}] {} {name}",
                    if ok { "OK" } else { "FAILED" }
                ),
                Progress::Finished { failed } => {
                    eprintln!("Finished with {failed} failed asset(s).")
                }
            }
        }
    });
    let download = run::run_download(&options, cancel.clone(), Some(tx));
    tokio::pin!(download);
    let result = tokio::select! {
        result = &mut download => Some(result),
        signal = tokio::signal::ctrl_c() => match signal {
            Ok(()) => {
                cancel.cancel();
                let _ = (&mut download).await;
                None
            }
            Err(e) => {
                // No interrupt happened; keep downloading and report the real result.
                eprintln!("warning: Ctrl-C handler unavailable: {e}");
                Some((&mut download).await)
            }
        }
    };
    let _ = printer.await;
    match result {
        None => ExitCode::from(130),
        Some(Ok(report)) => {
            for path in report.written_paths {
                println!("Saved: {}", path.display());
            }
            if !report.failed_assets.is_empty() {
                eprintln!(
                    "{} asset(s) failed; their source URLs were retained.",
                    report.failed_assets.len()
                );
            }
            ExitCode::SUCCESS
        }
        Some(Err(error)) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

use clap::CommandFactory;
