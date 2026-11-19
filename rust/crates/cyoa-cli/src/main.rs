use clap::Parser;
use cyoa_engine::{
    batch::{self, BatchItem},
    pipeline::Progress,
    reports,
    run::{self, OutputMode, RunOptions},
};
use std::{path::PathBuf, process::ExitCode};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

#[derive(Debug, Parser)]
#[command(
    name = "cyoa-downloader",
    version,
    about = "Download and process a CYOA project. Supports embedded JSON, ZIP or both formats."
)]
struct Args {
    /// URL of the CYOA project
    url: Option<String>,
    /// Optional output filename
    filename: Option<String>,
    /// URL of the CYOA project (alternative to the positional URL)
    #[arg(short = 'u', long = "url")]
    url_option: Option<String>,
    /// Output directory (default: current directory)
    #[arg(short = 'o', long = "output", default_value_os_t = std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))]
    output: PathBuf,
    /// Batch list file (.txt: `url | filename | mode` per line, or .csv with url/filename/mode columns)
    #[arg(short = 'L', long = "list")]
    list: Option<PathBuf>,
    /// Save as ZIP with external images
    #[arg(short = 'z', long = "zip")]
    zip: bool,
    /// Save both embedded JSON and ZIP
    #[arg(short = 'b', long = "both")]
    both: bool,
    /// ICC Mode (ZIP): download the full ICC viewer + all assets as a ZIP archive.
    #[arg(long = "icc")]
    website: bool,
    /// ICC Mode (Folder): download the full ICC viewer + all assets into a folder.
    #[arg(long = "icc-folder")]
    website_folder: bool,
    /// Pure ICC: download the viewer website without searching for a project file (ZIP).
    #[arg(long)]
    pure_website: bool,
    /// Same as --pure-website but keep as folder instead of ZIP.
    #[arg(long)]
    pure_website_folder: bool,
    /// Download & localise fonts (ZIP/ICC mode).
    #[arg(short = 'f', long)]
    fonts: bool,
    /// Print font analysis report only.
    #[arg(short = 'a', long)]
    analyse_fonts: bool,
    /// Auto-probe dedicated cyoap_vue ICC backup mode before standard ICC detection.
    #[arg(long)]
    cyoap_vue: bool,
    /// Use dedicated cyoap_vue ICC mode and output ZIP.
    #[arg(long)]
    cyoap_vue_website: bool,
    /// Use dedicated cyoap_vue ICC mode and keep folder output.
    #[arg(long)]
    cyoap_vue_folder: bool,
    /// Website archive engine: auto fingerprints the site; classic keeps historical behavior; smart crawls story routes; browser captures runtime assets.
    #[arg(long, value_parser = ["classic", "smart", "browser", "auto"])]
    archive_strategy: Option<String>,
    /// Maximum internal HTML routes for Smart/Browser modes (default: 300).
    #[arg(long, allow_negative_numbers = true)]
    archive_max_pages: Option<i64>,
    /// Maximum internal route-link depth for Smart/Browser modes (default: 30).
    #[arg(long, allow_negative_numbers = true)]
    archive_max_depth: Option<i64>,
    /// Allow fetching assets from cross-origin internal/loopback/RFC1918 hosts. Off by default as an SSRF safeguard; enable only for trusted local setups.
    #[arg(long)]
    allow_internal_hosts: bool,
    /// Bandwidth limit in KB/s. 0 means unlimited.
    #[arg(long, default_value_t = 0.0, allow_negative_numbers = true)]
    bandwidth: f64,
    /// Export current settings (secrets redacted) to FILE and exit.
    #[arg(long, value_name = "FILE")]
    export_settings: Option<PathBuf>,
    /// Merge settings from a prior export FILE (secrets ignored) and exit.
    #[arg(long, value_name = "FILE")]
    import_settings: Option<PathBuf>,
    /// Validate a previously downloaded output FOLDER (read-only integrity check) and exit.
    #[arg(long, value_name = "FOLDER")]
    verify: Option<PathBuf>,
    /// With --verify: write a cyoa_manifest.json checksum sidecar into the folder, then exit.
    #[arg(long)]
    write_manifest: bool,
    /// Start local HTTP server for the output directory after download.
    #[arg(long)]
    serve: bool,
    /// Local server port used with --serve. 0 means auto-pick a fresh port.
    #[arg(long, default_value_t = 0)]
    serve_port: u16,
    /// Disable the JS/CSS deep-scan asset pass (default: on).
    #[arg(long)]
    no_deep_scan: bool,
    /// Parallel asset workers (default: 4)
    #[arg(short = 't', long = "threads", alias = "workers", default_value_t = 4)]
    workers: usize,
    /// Seconds to wait after a 429 response (default: 60)
    #[arg(short = 'w', long = "wait-time", alias = "wait", default_value_t = 60)]
    wait_time: u64,
    /// Add finished project.json to CYOA Manager when possible.
    #[arg(long)]
    cyoa_manager: bool,
    /// Proxy URL, e.g. http://127.0.0.1:7890. Use --proxy-mode disabled to ignore environment proxies.
    #[arg(long)]
    proxy: Option<String>,
    /// Proxy mode. Default preserves saved behavior; disabled ignores HTTP_PROXY/HTTPS_PROXY.
    #[arg(long, value_parser = ["inherit_env", "manual", "disabled"])]
    proxy_mode: Option<String>,
    /// Advanced HTTP proxy URL; overrides --proxy for HTTP requests.
    #[arg(long)]
    proxy_http: Option<String>,
    /// Advanced HTTPS proxy URL; overrides --proxy for HTTPS requests.
    #[arg(long)]
    proxy_https: Option<String>,
    /// Comma-separated hosts that bypass a manual proxy.
    #[arg(long)]
    proxy_bypass: Option<String>,
    /// Custom DNS endpoint, e.g. 1.1.1.1 or an HTTPS resolver URL.
    #[arg(long)]
    dns: Option<String>,
    /// DNS transport: system, udp, tcp, doh, or dot.
    #[arg(long, value_parser = ["system", "udp", "tcp", "doh", "dot"])]
    dns_protocol: Option<String>,
    /// Custom DNS port (0 selects the protocol default).
    #[arg(long)]
    dns_port: Option<u16>,
    /// Custom DNS query timeout in seconds (1-60).
    #[arg(long)]
    dns_timeout: Option<u8>,
    /// Fall back to system DNS when custom resolution fails.
    #[arg(long, conflicts_with = "no_dns_fallback_system")]
    dns_fallback_system: bool,
    /// Disable system DNS fallback.
    #[arg(long)]
    no_dns_fallback_system: bool,
    /// Query AAAA records through the selected resolver.
    #[arg(long, conflicts_with = "no_dns_ipv6")]
    dns_ipv6: bool,
    /// Disable AAAA DNS queries.
    #[arg(long)]
    no_dns_ipv6: bool,
    /// Use a BebasDNS DoH variant.
    #[arg(long, value_parser = ["default", "security", "unfiltered", "family"])]
    bebasdns: Option<String>,
    /// VPN policy. require blocks downloads unless a VPN-like interface is active.
    #[arg(long, value_parser = ["system", "require"])]
    vpn_policy: Option<String>,
    /// Optional interface name/description substring for --vpn-policy require.
    #[arg(long)]
    vpn_interface: Option<String>,
}

/// Python cli.py (after network validation): the network flags given on the
/// command line are persisted to settings.json. Returns the keys to update.
fn network_setting_updates(
    args: &Args,
    proxy_mode_eff: &str,
    dns_cfg: &cyoa_net::dns::DnsConfig,
) -> Vec<(&'static str, serde_json::Value)> {
    use serde_json::Value;
    let s = |v: &str| Value::String(v.to_owned());
    let mut out = Vec::new();
    if let Some(v) = &args.proxy {
        out.push(("proxy", s(v)));
    }
    if let Some(v) = &args.proxy_mode {
        out.push(("proxy_mode", s(v)));
    }
    if let Some(v) = &args.proxy_http {
        out.push(("proxy_http", s(v)));
    }
    if let Some(v) = &args.proxy_https {
        out.push(("proxy_https", s(v)));
    }
    if let Some(v) = &args.proxy_bypass {
        out.push(("proxy_no_proxy", s(v)));
    }
    if args.proxy_mode.is_none()
        && (args.proxy.is_some() || args.proxy_http.is_some() || args.proxy_https.is_some())
    {
        out.push(("proxy_mode", s(proxy_mode_eff)));
    }
    // --bebasdns rewrites --dns/--dns-protocol before this point in Python.
    let dns_given = args.dns.is_some() || args.bebasdns.is_some();
    if dns_given {
        out.push(("dns", s(&dns_cfg.server)));
    }
    if let (Some(v), None) = (&args.dns_protocol, &args.bebasdns) {
        out.push(("dns_protocol", s(v)));
    } else if dns_given {
        out.push(("dns_protocol", s(&dns_cfg.protocol)));
    }
    if let Some(v) = args.dns_port {
        out.push(("dns_port", Value::from(v)));
    }
    if let Some(v) = args.dns_timeout {
        out.push(("dns_timeout", Value::from(v)));
    }
    if args.dns_fallback_system || args.no_dns_fallback_system {
        out.push(("dns_fallback_system", Value::Bool(args.dns_fallback_system)));
    }
    if args.dns_ipv6 || args.no_dns_ipv6 {
        out.push(("dns_ipv6", Value::Bool(args.dns_ipv6)));
    }
    if let Some(v) = &args.bebasdns {
        out.push(("bebasdns_variant", s(v)));
    }
    if let Some(v) = &args.vpn_policy {
        out.push(("vpn_policy", s(v)));
    }
    if let Some(v) = &args.vpn_interface {
        out.push(("vpn_interface", s(v)));
    }
    out
}

fn valid_url(url: &str) -> bool {
    (url.starts_with("http://") || url.starts_with("https://")) && !url.contains(' ')
}

/// Batch row mode OR-ed with the global flags, like Python's CLI loop.
/// Website/ICC/pure/cyoap-vue modes are not ported yet: refuse instead of
/// silently producing a different output.
fn item_mode(args: &Args, mode: &str) -> Result<OutputMode, String> {
    match mode {
        ""
        | "auto"
        | "embed"
        | "zip"
        | "both"
        | "website"
        | "website_zip"
        | "website_folder"
        | "pure_website"
        | "pure_website_zip"
        | "pure_website_folder"
        | "cyoap_vue"
        | "cyoap_vue_zip"
        | "cyoap_vue_folder" => {}
        other => {
            return Err(format!(
                "mode '{other}' is not supported by the Rust port yet"
            ));
        }
    }
    Ok(
        if args.pure_website || matches!(mode, "pure_website" | "pure_website_zip") {
            OutputMode::PureZip
        } else if args.pure_website_folder || mode == "pure_website_folder" {
            OutputMode::PureFolder
        } else if args.cyoap_vue_website || matches!(mode, "cyoap_vue" | "cyoap_vue_zip") {
            OutputMode::IccZip
        } else if args.cyoap_vue_folder || mode == "cyoap_vue_folder" {
            OutputMode::IccFolder
        } else if args.website || matches!(mode, "website" | "website_zip") {
            OutputMode::IccZip
        } else if args.website_folder || mode == "website_folder" {
            OutputMode::IccFolder
        } else if args.both || mode == "both" {
            OutputMode::Both
        } else if args.zip || mode == "zip" {
            OutputMode::Zip
        } else {
            OutputMode::Embed
        },
    )
}

/// Python `--serve`: serve the last website folder (or the output folder's
/// archive root) on 127.0.0.1 until the process is interrupted.
fn serve_after_download(
    saved: &serde_json::Map<String, serde_json::Value>,
    last_preview: Option<&std::path::Path>,
    output: &std::path::Path,
    port: u16,
) {
    if !saved
        .get("serve_enabled")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(true)
    {
        tracing::info!("Serve preview disabled by toggle — not starting CLI server.");
        return;
    }
    let dir = cyoa_integrations::preview::select_archive_root(last_preview.unwrap_or(output));
    if !dir.is_dir() {
        tracing::warn!("Serve skipped: preview folder not found: {}", dir.display());
        tracing::warn!(
            "Tip: use --icc-folder or --pure-website-folder when you want to preview immediately after download."
        );
        return;
    }
    let server = match cyoa_integrations::preview::PreviewServer::start_on(&dir, port) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: could not start the local server: {e}");
            return;
        }
    };
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or_default();
    let clear_url = format!("{}__clear_cache__?cb={stamp}", server.url());
    tracing::info!("Open {clear_url} to preview with cleared browser storage.");
    let shown = dir.display().to_string();
    let shown = shown.strip_prefix(r"\\?\").unwrap_or(&shown);
    println!("Serving {shown} at {}", server.url());
    open_browser(&clear_url);
    // Python `serve_forever()`: run until Ctrl+C ends the process.
    loop {
        std::thread::park();
    }
}

fn open_browser(url: &str) {
    #[cfg(windows)]
    let r = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url])
        .spawn();
    #[cfg(target_os = "macos")]
    let r = std::process::Command::new("open").arg(url).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let r = std::process::Command::new("xdg-open").arg(url).spawn();
    if let Err(e) = r {
        tracing::debug!("Could not open the browser: {e}");
    }
}

/// ICC / pure website runs (no CYOA Manager registration, like Python).
fn is_site_mode(mode: OutputMode) -> bool {
    matches!(
        mode,
        OutputMode::IccZip | OutputMode::IccFolder | OutputMode::PureZip | OutputMode::PureFolder
    )
}

enum Outcome {
    /// Success; carries the website folder output, if any (Python
    /// `_LAST_PREVIEW_FOLDER`).
    Ok(Option<PathBuf>),
    Failed(String),
    Cancelled,
}

/// orchestrator.py: embedded JSON (embed/both) or the ZIP (zip) is
/// registered in the CYOA Manager library.
async fn register_with_manager(
    db: PathBuf,
    mode: OutputMode,
    url: String,
    report: &run::RunReport,
) {
    let embedded = !matches!(mode, OutputMode::Zip);
    let written = report.written_paths.clone();
    let name = report.stem.clone();
    let _ = tokio::task::spawn_blocking(move || {
        cyoa_integrations::cyoa_manager::register_output(&db, embedded, &written, &name, &url)
    })
    .await;
}

async fn run_one(
    options: RunOptions,
    cancel: &CancellationToken,
    manager_db: Option<&PathBuf>,
) -> Outcome {
    let (mode, url) = (options.mode, options.url.clone());
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
    let result = run::run_download(&options, cancel.clone(), Some(tx)).await;
    let _ = printer.await;
    match result {
        Ok(report) => {
            // Python's ICC branch returns before CYOA Manager registration.
            if let Some(db) = manager_db.filter(|_| !is_site_mode(mode)) {
                register_with_manager(db.clone(), mode, url, &report).await;
            }
            let preview = report.written_paths.iter().find(|p| p.is_dir()).cloned();
            for path in report.written_paths {
                println!("Saved: {}", path.display());
            }
            if !report.failed_assets.is_empty() {
                eprintln!(
                    "{} asset(s) failed; their source URLs were retained.",
                    report.failed_assets.len()
                );
            }
            Outcome::Ok(preview)
        }
        Err(_) if cancel.is_cancelled() => Outcome::Cancelled,
        Err(error) => Outcome::Failed(error.to_string()),
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    if std::env::args_os().len() == 1 {
        use clap::CommandFactory;
        let _ = Args::command().print_help();
        println!();
        return ExitCode::SUCCESS;
    }
    let mut args = Args::parse();
    // cli.py 477-489: --verify [--write-manifest] runs before anything else.
    if let Some(folder) = &args.verify {
        let (ok, text) = if args.write_manifest {
            cyoa_engine::verify::write_package_manifest(folder)
        } else {
            cyoa_engine::verify::verify_output_package(folder)
        };
        println!("{text}");
        return if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        };
    }
    // cli.py 491-503: settings export/import run before any download.
    if args.export_settings.is_some() || args.import_settings.is_some() {
        let mut store =
            cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
        let (ok, msg) = if let Some(path) = &args.export_settings {
            cyoa_storage::settings::export_settings_file(&mut store, path)
        } else if let Some(path) = &args.import_settings {
            cyoa_storage::settings::import_settings_file(&mut store, path)
        } else {
            (false, String::new())
        };
        println!("{msg}");
        return if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        };
    }
    // cli.py: _set_allow_internal_hosts(bool(args.allow_internal_hosts)).
    cyoa_core::set_allow_internal_hosts(args.allow_internal_hosts);
    if args.workers == 0 {
        eprintln!("error: --threads must be at least 1");
        return ExitCode::from(2);
    }
    // cli.py: parser.error("Choose only one output mode.")
    if [
        args.zip,
        args.both,
        args.website,
        args.website_folder,
        args.pure_website,
        args.pure_website_folder,
        args.cyoap_vue_website,
        args.cyoap_vue_folder,
    ]
    .iter()
    .filter(|f| **f)
    .count()
        > 1
    {
        eprintln!("error: Choose only one output mode.");
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
    // Python cli.py: console log handler + setup_file_logging(output_dir).
    cyoa_engine::logfile::init(true);
    cyoa_engine::logfile::setup_file_logging(&args.output);

    // Saved settings provide defaults; network flags given here are persisted
    // back after validation (Python cli.py).
    let mut store =
        cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
    let saved = store
        .load()
        .cloned()
        .unwrap_or_else(|_| cyoa_storage::settings::defaults());
    let lookup = |key: &str| saved.get(key).and_then(|v| v.as_str()).map(str::to_string);
    let proxy_cfg = match cyoa_net::proxy::resolve_cli(
        &cyoa_net::proxy::ProxyArgs {
            mode: args.proxy_mode.as_deref(),
            proxy: args.proxy.as_deref(),
            http: args.proxy_http.as_deref(),
            https: args.proxy_https.as_deref(),
            bypass: args.proxy_bypass.as_deref(),
        },
        &lookup,
    ) {
        Ok(cfg) => cfg,
        Err(error) => {
            eprintln!("error: {error}");
            return ExitCode::from(2);
        }
    };
    let saved_bool = |key: &str| saved.get(key).and_then(|v| v.as_bool());
    let saved_int = |key: &str| saved.get(key).and_then(|v| v.as_i64());
    let dns_cfg = match cyoa_net::dns::resolve_cli(
        &cyoa_net::dns::DnsArgs {
            server: args.dns.as_deref(),
            protocol: args.dns_protocol.as_deref(),
            port: args.dns_port,
            timeout: args.dns_timeout,
            fallback_system: if args.no_dns_fallback_system {
                Some(false)
            } else if args.dns_fallback_system {
                Some(true)
            } else {
                None
            },
            ipv6: if args.no_dns_ipv6 {
                Some(false)
            } else if args.dns_ipv6 {
                Some(true)
            } else {
                None
            },
            bebasdns: args.bebasdns.as_deref(),
        },
        &lookup,
        &saved_bool,
        &saved_int,
    ) {
        Ok(cfg) => cfg,
        Err(error) => {
            eprintln!("error: Invalid network configuration: {error}");
            return ExitCode::from(2);
        }
    };
    let net_updates = network_setting_updates(&args, proxy_cfg.mode.as_str(), &dns_cfg);
    let dns = (dns_cfg.protocol != "system" && !dns_cfg.server.is_empty()).then(|| {
        std::sync::Arc::new(cyoa_net::dns_resolve::CustomResolver::new(
            dns_cfg,
            Some(proxy_cfg.clone()),
        ))
    });

    let vpn_cfg = match cyoa_net::vpn::VpnConfig::new(
        &args
            .vpn_policy
            .clone()
            .or_else(|| lookup("vpn_policy"))
            .unwrap_or_else(|| "system".to_owned()),
        &args
            .vpn_interface
            .clone()
            .or_else(|| lookup("vpn_interface"))
            .unwrap_or_default(),
    ) {
        Ok(cfg) => cfg,
        Err(error) => {
            eprintln!("error: Invalid network configuration: {error}");
            return ExitCode::from(2);
        }
    };
    let vpn = (vpn_cfg.policy == cyoa_net::vpn::VpnPolicy::Require)
        .then(|| std::sync::Arc::new(cyoa_net::vpn::VpnGuard::new(vpn_cfg)));
    if !net_updates.is_empty() {
        let values = store.values_mut();
        for (key, value) in net_updates {
            values.insert(key.to_owned(), value);
        }
        if let Err(error) = store.save() {
            tracing::warn!("Could not save network settings: {error}");
        }
    }

    // orchestrator.py: saved `cyoa_mgr_db_path` if it exists, else auto-detect;
    // silently skipped when no library is found.
    let manager_db = args.cyoa_manager.then(|| {
        cyoa_integrations::cyoa_manager::resolve_db(&lookup("cyoa_mgr_db_path").unwrap_or_default())
    });
    let manager_db = manager_db.flatten();
    // Python orchestrator: ArchivePolicy from settings (`archive_*`).
    // cli.py: --archive-* default to the saved settings; given values win
    // for this run only (not persisted) and get the same bounds.
    let archive = {
        let mut effective = saved.clone();
        if let Some(s) = &args.archive_strategy {
            effective.insert("archive_strategy".into(), s.clone().into());
        }
        if let Some(n) = args.archive_max_pages {
            effective.insert("archive_max_pages".into(), n.into());
        }
        if let Some(n) = args.archive_max_depth {
            effective.insert("archive_max_depth".into(), n.into());
        }
        cyoa_engine::icc::ArchivePolicy::from_settings(&effective)
    };
    let batch_mode = args.list.is_some();
    let items: Vec<BatchItem> = if let Some(list) = args.list.take() {
        match batch::import_batch_file(&list) {
            Ok(items) if !items.is_empty() => {
                eprintln!("Batch file    : {}", list.display());
                eprintln!("Items         : {}", items.len());
                items
            }
            Ok(_) => {
                eprintln!("error: No valid URLs found in batch file.");
                return ExitCode::from(2);
            }
            Err(e) => {
                eprintln!("error: {e}");
                return ExitCode::from(2);
            }
        }
    } else {
        let Some(url) = args.url_option.clone().or(args.url.clone()) else {
            eprintln!("error: URL is required unless --list is used.");
            return ExitCode::from(2);
        };
        if !valid_url(&url) {
            eprintln!("error: invalid URL '{url}': expected an absolute http:// or https:// URL");
            return ExitCode::from(2);
        }
        vec![BatchItem {
            url,
            filename: args.filename.clone().unwrap_or_default(),
            mode: String::new(),
        }]
    };

    let cancel = CancellationToken::new();
    let signal_cancel = cancel.clone();
    tokio::spawn(async move {
        match tokio::signal::ctrl_c().await {
            Ok(()) => signal_cancel.cancel(),
            // No interrupt happened; keep downloading and report real results.
            Err(e) => eprintln!("warning: Ctrl-C handler unavailable: {e}"),
        }
    });

    let total = items.len();
    let mut ok = 0usize;
    let mut last_preview: Option<PathBuf> = None;
    let mut failed: Vec<(String, String)> = Vec::new();
    for (idx, item) in items.iter().enumerate() {
        if cancel.is_cancelled() {
            break;
        }
        if batch_mode {
            eprintln!("Batch {}/{total}: {}", idx + 1, item.url);
        }
        let mode = match item_mode(&args, &item.mode).and_then(|m| {
            if matches!(
                m,
                OutputMode::IccZip
                    | OutputMode::IccFolder
                    | OutputMode::PureZip
                    | OutputMode::PureFolder
            ) && archive.strategy == "browser"
            {
                Err("archive strategy 'browser' is not supported by the Rust port yet (use auto, classic or smart)".to_owned())
            } else {
                Ok(m)
            }
        }) {
            Ok(m) => m,
            Err(e) => {
                eprintln!("Failed: {e}");
                failed.push((item.url.clone(), e));
                continue;
            }
        };
        let options = RunOptions {
            url: item.url.clone(),
            output_dir: args.output.clone(),
            mode,
            workers: args.workers,
            wait_time: args.wait_time,
            file_name: (!item.filename.is_empty()).then(|| item.filename.clone()),
            proxy: proxy_cfg.clone(),
            dns: dns.clone(),
            vpn: vpn.clone(),
            deep_scan: !args.no_deep_scan,
            fonts: args.fonts,
            // cli.py: max(0.0, float(args.bandwidth or 0.0)); never persisted.
            bandwidth_kbps: if args.bandwidth.is_finite() {
                args.bandwidth.max(0.0)
            } else {
                0.0
            },
            // cli.py: show_font_analysis = -a or -f; analysis_only = -a without -f.
            analyse_fonts: args.analyse_fonts || args.fonts,
            analysis_only: args.analyse_fonts && !args.fonts,
            // cli.py: engine per row (cyoap_vue* rows) or from the flags.
            engine: if item.mode.starts_with("cyoap_vue")
                || args.cyoap_vue_website
                || args.cyoap_vue_folder
            {
                cyoa_engine::run::Engine::CyoapVue
            } else if args.cyoap_vue {
                cyoa_engine::run::Engine::Auto
            } else {
                cyoa_engine::run::Engine::Standard
            },
            archive: archive.clone(),
            ..RunOptions::default()
        };
        match run_one(options, &cancel, manager_db.as_ref()).await {
            Outcome::Ok(preview) => {
                ok += 1;
                last_preview = preview;
            }
            Outcome::Cancelled => break,
            Outcome::Failed(e) => {
                eprintln!("error: {e}");
                failed.push((item.url.clone(), e));
            }
        }
    }

    if cancel.is_cancelled() {
        return ExitCode::from(130);
    }
    // cli.py 929-1270: `--serve` after a single download.
    if args.serve && !batch_mode && failed.is_empty() {
        serve_after_download(
            &saved,
            last_preview.as_deref(),
            &args.output,
            args.serve_port,
        );
    }
    if batch_mode {
        if let Err(e) = reports::write_failed_url_log(&failed, &args.output) {
            eprintln!("warning: could not write failed_urls.txt: {e}");
        }
        eprintln!("Batch done    : {ok}/{total} succeeded");
    }
    if failed.is_empty() {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
