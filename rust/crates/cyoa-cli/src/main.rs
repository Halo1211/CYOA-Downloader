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
    /// Cloudflare handling mode. Auto escalates only after a challenge is detected.
    #[arg(long, value_parser = ["off", "auto", "cloudscraper", "flaresolverr"])]
    cloudflare: Option<String>,
    /// Auto fallback order after a challenge: FlareSolverr first or cloudscraper first.
    #[arg(long, value_parser = ["flaresolverr-first", "cloudscraper-first"])]
    cloudflare_priority: Option<String>,
    /// Legacy alias: force Cloudflare mode to cloudscraper when installed.
    #[arg(long = "cf-bypass", alias = "cloudscraper")]
    cf_bypass: bool,
    /// FlareSolverr API endpoint, e.g. http://localhost:8191/v1.
    #[arg(long)]
    flaresolverr_url: Option<String>,
    /// FlareSolverr session policy. reuse-domain keeps cookies per domain.
    #[arg(long, value_parser = ["temporary", "reuse-domain", "manual"])]
    flaresolverr_session: Option<String>,
    /// FlareSolverr solve timeout in seconds.
    #[arg(long, allow_negative_numbers = true)]
    flaresolverr_timeout: Option<i64>,
    /// Seconds FlareSolverr waits after page load before returning content.
    #[arg(long, allow_negative_numbers = true)]
    flaresolverr_wait: Option<i64>,
    /// Whether FlareSolverr should inherit the app proxy for target requests.
    #[arg(long, value_parser = ["inherit", "none"])]
    flaresolverr_proxy: Option<String>,
    /// Test the configured FlareSolverr API and exit.
    #[arg(long)]
    flaresolverr_test: bool,
    /// Enable the optional itch.io asset downloader for itch.io URLs (default: off).
    #[arg(long)]
    itch: bool,
    /// Download an itch.io URL with itch-dl only; skip CYOA parsing and mirror web assets.
    #[arg(long)]
    itch_only: bool,
    /// Test itch.io backend (itch-dl) + connectivity and exit.
    #[arg(long)]
    itch_test: bool,
    /// Mirror linked web builds; downloaded HTML5 ZIPs are prepared offline automatically.
    #[arg(long)]
    itch_mirror_web: bool,
    /// itch-dl game download workers (default: 1, maximum: 16).
    #[arg(long, default_value_t = 1, value_name = "1-16", value_parser = clap::value_parser!(i32).range(1..=16))]
    itch_parallel: i32,
    /// Print Serve-only userscript integration credit/source notes and exit.
    #[arg(long)]
    userscript_info: bool,
    /// CLI/UI language preference. Saved only when explicitly provided.
    #[arg(long, value_parser = ["id", "en"])]
    language: Option<String>,
    /// Use HTTP/2 via httpx for deep-scan asset fetches when available.
    #[arg(long, overrides_with = "no_http2")]
    http2: bool,
    #[arg(long, hide = true, overrides_with = "http2")]
    no_http2: bool,
    /// Optional gallery-dl fallback. Default off; smart only uses page/post/gallery URLs; force is advanced.
    #[arg(long, value_parser = ["off", "smart", "force"])]
    gallery_dl: Option<String>,
    /// gallery-dl executable path used with --gallery-dl.
    #[arg(long)]
    gallery_dl_path: Option<String>,
    /// Optional gallery-dl config.json path.
    #[arg(long)]
    gallery_dl_config: Option<String>,
    /// Disable automatic YouTube audio download via yt-dlp.
    #[arg(long)]
    no_ytdlp: bool,
    /// Use an exported Netscape cookies.txt for yt-dlp when YouTube requires sign-in.
    #[arg(long, value_name = "FILE", default_value = "")]
    ytdlp_cookies: String,
    /// Disable the headless browser image fallback (default: on).
    #[arg(long)]
    no_selenium: bool,
    /// Session-only Discord bot token for automatic expired attachment recovery.
    #[arg(long, value_name = "TOKEN", default_value = "")]
    discord_token: String,
    /// Disable automatic Discord attachment URL recovery for this run.
    #[arg(long)]
    no_discord_refresh: bool,
    /// Force GUI.
    #[arg(long)]
    gui: bool,
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
    // cli.py persists these first, in this order.
    if let Some(v) = &args.language {
        out.push(("language", s(v)));
    }
    if args.http2 || args.no_http2 {
        out.push(("http2_enabled", Value::Bool(args.http2)));
    }
    if let Some(v) = &args.gallery_dl {
        out.push(("gallery_dl_mode", s(v)));
    }
    if let Some(v) = &args.gallery_dl_path {
        out.push(("gallery_dl_path", s(v)));
    }
    if let Some(v) = &args.gallery_dl_config {
        out.push(("gallery_dl_config", s(v)));
    }
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

/// Python `preview_assets.userscript_integration_report()`.
fn userscript_integration_report() -> String {
    let v = cyoa_core::app_version();
    [
        format!("CYOA Downloader v{v} userscript integration"),
        "=".repeat(58),
        "Name       : IntCyoaEnhancer".into(),
        "Author     : agreg".into(),
        "Version    : 0.7".into(),
        "License    : MIT".into(),
        "Source     : https://greasyfork.org/en/scripts/438947-intcyoaenhancer".into(),
        "Remote JS  : https://update.greasyfork.org/scripts/438947/IntCyoaEnhancer.user.js".into(),
        "Credit     : IntCyoaEnhancer by agreg, MIT License, GreasyFork script 438947".into(),
        String::new(),
        "Policy".into(),
        "------".into(),
        "- Downloaded CYOA output files are never modified by the userscript integration.".into(),
        "- Serve preview exposes a bundled localhost helper at /__userscripts__/intcyoaenhancer.user.js.".into(),
        "- The bundled helper includes a visible Native Cheat Panel; no Tampermonkey menu is required.".into(),
        "- Local .user.js files can still override the bundled helper for advanced testing.".into(),
        "- Local file lookup order: served CYOA folder, userscripts/, serve_userscripts/.".into(),
        "- Native Serve Tools remain available as a fallback through window.$serveTools.".into(),
        "- Use this for localhost/offline debugging, accessibility, and QoL testing only.".into(),
    ]
    .join("\n")
        + "\n"
}

/// Python `print(text)` / `_safe_console_print`: trailing newline, text-mode
/// stdout (CRLF on Windows).
fn console_print(text: &str) {
    use std::io::Write;
    let line = cyoa_engine::reports::os_text(&format!(
        "{text}
"
    ));
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(line.as_bytes());
    let _ = out.flush();
}

/// Python `launch_gui()`: start the desktop app shipped next to this CLI.
fn launch_gui() -> ExitCode {
    let name = if cfg!(windows) {
        "cyoa-desktop.exe"
    } else {
        "cyoa-desktop"
    };
    let exe = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join(name)));
    match exe {
        Some(path) if path.is_file() => match std::process::Command::new(&path).spawn() {
            Ok(_) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("error: could not start the GUI: {e}");
                ExitCode::FAILURE
            }
        },
        _ => {
            eprintln!("error: GUI executable {name} not found next to the CLI");
            ExitCode::FAILURE
        }
    }
}

/// Python `_configured_ytdlp_cookie_path`: explicit path must exist; a
/// missing saved path only warns.
fn configured_ytdlp_cookie_path(explicit: &str, saved: &str) -> Result<Option<PathBuf>, String> {
    let raw = if explicit.trim().is_empty() {
        saved
    } else {
        explicit
    }
    .trim();
    if raw.is_empty() {
        return Ok(None);
    }
    let expanded = match raw.strip_prefix('~') {
        Some(rest) => dirs_home().map(|h| h.join(rest.trim_start_matches(['/', '\\']))),
        None => None,
    }
    .unwrap_or_else(|| PathBuf::from(raw));
    let path = std::path::absolute(&expanded).unwrap_or(expanded);
    if path.is_file() {
        return Ok(Some(path));
    }
    if !explicit.trim().is_empty() {
        return Err(path.display().to_string());
    }
    tracing::warn!("Saved yt-dlp cookie file was not found; continuing without it");
    Ok(None)
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
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
    if args.userscript_info {
        console_print(&userscript_integration_report());
        return ExitCode::SUCCESS;
    }
    // cli.py 477-489: --verify [--write-manifest] runs before anything else.
    if let Some(folder) = &args.verify {
        let (ok, text) = if args.write_manifest {
            cyoa_engine::verify::write_package_manifest(folder)
        } else {
            cyoa_engine::verify::verify_output_package(folder)
        };
        console_print(&text);
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
        console_print(&msg);
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
    let saved_bool = |key: &str, default: bool| {
        saved
            .get(key)
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(default)
    };
    // cli.py 510-520: validated yt-dlp cookie path (explicit must exist).
    let ytdlp_cookies = match configured_ytdlp_cookie_path(
        &args.ytdlp_cookies,
        &lookup("ytdlp_cookies").unwrap_or_default(),
    ) {
        Ok(p) => p,
        Err(path) => {
            eprintln!("error: yt-dlp cookie file not found: {path}");
            return ExitCode::from(2);
        }
    };
    // cli.py: effective session toggles (flags override saved settings).
    let integrations = cyoa_engine::run::Integrations {
        ytdlp: !args.no_ytdlp,
        ytdlp_cookies,
        discord_token: Some(args.discord_token.trim().to_owned()).filter(|t| !t.is_empty()),
        discord_refresh: !args.no_discord_refresh,
        selenium: if args.no_selenium {
            false
        } else {
            saved_bool("selenium_enabled", true)
        },
        gallery_dl_mode: args
            .gallery_dl
            .clone()
            .or_else(|| lookup("gallery_dl_mode"))
            .unwrap_or_else(|| "off".into()),
        gallery_dl_path: args
            .gallery_dl_path
            .clone()
            .unwrap_or_else(|| "gallery-dl".into()),
        gallery_dl_config: args.gallery_dl_config.clone().unwrap_or_default(),
        // cli.py: _set_itch_enabled(args.itch or saved itch_enabled).
        itch: args.itch || saved_bool("itch_enabled", false),
        ..cyoa_engine::run::Integrations::default()
    };
    // cli.py 645-680: Cloudflare config; argparse defaults come from the
    // saved settings; any explicit flag persists the whole normalized set.
    let cloudflare = {
        let text = |k: &str, d: &str| lookup(k).unwrap_or_else(|| d.to_owned());
        let int = |k: &str, d: i64| {
            saved
                .get(k)
                .and_then(|v| {
                    v.as_i64()
                        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
                })
                .filter(|v| *v != 0)
                .unwrap_or(d)
        };
        let mode = if args.cf_bypass {
            "cloudscraper".to_owned()
        } else {
            args.cloudflare
                .clone()
                .unwrap_or_else(|| text("cloudflare_mode", "auto"))
        };
        let priority = args
            .cloudflare_priority
            .clone()
            .unwrap_or_else(|| text("cloudflare_priority", "flaresolverr_first").replace('_', "-"));
        let cfg = cyoa_net::cloudflare::CloudflareConfig::new(
            &mode,
            &priority,
            &args
                .flaresolverr_url
                .clone()
                .unwrap_or_else(|| text("flaresolverr_url", "http://localhost:8191/v1")),
            &args
                .flaresolverr_session
                .clone()
                .unwrap_or_else(|| text("flaresolverr_session_policy", "reuse-domain")),
            args.flaresolverr_timeout
                .unwrap_or_else(|| int("flaresolverr_timeout", 60)),
            args.flaresolverr_wait
                .unwrap_or_else(|| int("flaresolverr_wait_after", 3)),
            &args
                .flaresolverr_proxy
                .clone()
                .unwrap_or_else(|| text("flaresolverr_proxy_mode", "inherit")),
        );
        let explicit = args.cf_bypass
            || args.cloudflare.is_some()
            || args.cloudflare_priority.is_some()
            || args.flaresolverr_url.is_some()
            || args.flaresolverr_session.is_some()
            || args.flaresolverr_timeout.is_some()
            || args.flaresolverr_wait.is_some()
            || args.flaresolverr_proxy.is_some();
        if explicit {
            let values = store.values_mut();
            for (k, v) in [
                ("cloudflare_mode", serde_json::Value::from(cfg.mode.clone())),
                ("cloudflare_priority", cfg.priority.clone().into()),
                ("flaresolverr_url", cfg.flaresolverr_url.clone().into()),
                (
                    "flaresolverr_session_policy",
                    cfg.session_policy.clone().into(),
                ),
                ("flaresolverr_timeout", cfg.timeout_s.into()),
                ("flaresolverr_wait_after", cfg.wait_after_s.into()),
                ("flaresolverr_proxy_mode", cfg.proxy_mode.clone().into()),
            ] {
                values.insert(k.to_owned(), v);
            }
            if let Err(e) = store.save() {
                tracing::debug!("Could not save Cloudflare settings: {e}");
            }
        }
        std::sync::Arc::new(cfg)
    };
    let http2 = if args.http2 || args.no_http2 {
        args.http2
    } else {
        saved_bool("http2_enabled", false)
    };
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
    // cli.py 674-679: --flaresolverr-test.
    if args.flaresolverr_test {
        let client = match cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
            proxy: proxy_cfg.clone(),
            cloudflare: Some(cloudflare.clone()),
            ..Default::default()
        }) {
            Ok(c) => c,
            Err(e) => {
                console_print(&format!("ERROR: {e}"));
                return ExitCode::FAILURE;
            }
        };
        let (ok, msg) = client.flaresolverr_test().await;
        console_print(&format!("{}{msg}", if ok { "OK: " } else { "ERROR: " }));
        return if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        };
    }
    // cli.py 680-685: --itch-test.
    if args.itch_test {
        let client = match cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
            proxy: proxy_cfg.clone(),
            dns: dns.clone(),
            vpn: vpn.clone(),
            ..Default::default()
        }) {
            Ok(c) => c,
            Err(e) => {
                console_print(&format!("ERROR: {e}"));
                return ExitCode::FAILURE;
            }
        };
        let env_key = std::env::var("ITCH_API_KEY").unwrap_or_default();
        let (ok, msg) = cyoa_engine::itch::itch_test_connection(
            Some(env_key.as_str()).filter(|k| !k.is_empty()),
            &client,
            &cyoa_engine::itch::SystemItchBackend,
        )
        .await;
        console_print(&format!("{}{msg}", if ok { "OK: " } else { "ERROR: " }));
        return if ok {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        };
    }
    // cli.py 741-743: --gui launches the desktop app after settings handling.
    if args.gui {
        return launch_gui();
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
    // cli.py 762-771: --itch-only runs itch-dl alone (mirror web) and exits.
    if args.itch_only {
        let url = args
            .url_option
            .clone()
            .or_else(|| args.url.clone())
            .unwrap_or_default();
        if batch_mode || !cyoa_engine::itch::is_itch_url(url.trim()) {
            eprintln!("error: --itch-only requires one http(s) itch.io URL and no --list.");
            return ExitCode::from(2);
        }
        let cancel_c = CancellationToken::new();
        let on_ctrl_c = cancel_c.clone();
        tokio::spawn(async move {
            if tokio::signal::ctrl_c().await.is_ok() {
                on_ctrl_c.cancel();
            }
        });
        let (out, parallel) = (args.output.clone(), args.itch_parallel);
        let result = tokio::task::spawn_blocking(move || {
            cyoa_engine::itch::download_itch_assets(
                url.trim(),
                &out,
                None,
                true,
                parallel,
                &cancel_c,
                &cyoa_engine::itch::SystemItchBackend,
            )
        })
        .await;
        return match result {
            Ok(Ok(r)) => {
                tracing::info!("[itch] {}", r.message);
                if r.ok {
                    ExitCode::SUCCESS
                } else {
                    ExitCode::FAILURE
                }
            }
            Ok(Err(e)) => {
                eprintln!("error: {e}");
                ExitCode::FAILURE
            }
            Err(e) => {
                eprintln!("error: {e}");
                ExitCode::FAILURE
            }
        };
    }
    let mut itch_seen: std::collections::HashSet<String> = std::collections::HashSet::new();
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
        // cli.py 783-797 / 896-905: the optional itch-dl backend runs first
        // for itch.io URLs (once per URL); it never affects the CYOA result.
        if integrations.itch
            && cyoa_engine::itch::is_itch_url(&item.url)
            && itch_seen.insert(item.url.clone())
        {
            if !batch_mode {
                tracing::info!("itch.io downloader enabled — invoking itch-dl backend.");
            }
            let (url, out, mirror, parallel, cancel_c) = (
                item.url.clone(),
                args.output.clone(),
                args.itch_mirror_web,
                args.itch_parallel,
                cancel.clone(),
            );
            match tokio::task::spawn_blocking(move || {
                cyoa_engine::itch::download_itch_assets(
                    &url,
                    &out,
                    None,
                    mirror,
                    parallel,
                    &cancel_c,
                    &cyoa_engine::itch::SystemItchBackend,
                )
            })
            .await
            {
                Ok(Ok(r)) => tracing::info!("[itch] {}", r.message),
                Ok(Err(e)) if batch_mode => {
                    tracing::warn!("[itch] Optional backend failed: {e}");
                }
                Ok(Err(e)) => {
                    tracing::warn!("[itch] downloader error (CYOA result unaffected): {e}");
                }
                Err(e) => tracing::warn!("[itch] Optional backend failed: {e}"),
            }
            if cancel.is_cancelled() {
                break;
            }
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
            http2,
            cloudflare: Some(cloudflare.clone()),
            integrations: integrations.clone(),
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
            // Settings `offline_viewer_json_enabled` / `offline_viewer_preferred_id`.
            offline_viewer: cyoa_engine::run::OfflineViewerOptions::from_settings(&saved),
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
