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
    /// Parallel asset workers (default: 4)
    #[arg(short = 't', long = "threads", alias = "workers", default_value_t = 4)]
    workers: usize,
    /// Seconds to wait after a 429 response (default: 60)
    #[arg(short = 'w', long = "wait-time", alias = "wait", default_value_t = 60)]
    wait_time: u64,
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
        "" | "auto" | "embed" | "zip" | "both" => {}
        other => {
            return Err(format!(
                "mode '{other}' is not supported by the Rust port yet"
            ));
        }
    }
    Ok(if args.both || mode == "both" {
        OutputMode::Both
    } else if args.zip || mode == "zip" {
        OutputMode::Zip
    } else {
        OutputMode::Embed
    })
}

enum Outcome {
    Ok,
    Failed(String),
    Cancelled,
}

async fn run_one(options: RunOptions, cancel: &CancellationToken) -> Outcome {
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
            for path in report.written_paths {
                println!("Saved: {}", path.display());
            }
            if !report.failed_assets.is_empty() {
                eprintln!(
                    "{} asset(s) failed; their source URLs were retained.",
                    report.failed_assets.len()
                );
            }
            Outcome::Ok
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
    if args.workers == 0 {
        eprintln!("error: --threads must be at least 1");
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
    let mut failed: Vec<(String, String)> = Vec::new();
    for (idx, item) in items.iter().enumerate() {
        if cancel.is_cancelled() {
            break;
        }
        if batch_mode {
            eprintln!("Batch {}/{total}: {}", idx + 1, item.url);
        }
        let mode = match item_mode(&args, &item.mode) {
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
            ..RunOptions::default()
        };
        match run_one(options, &cancel).await {
            Outcome::Ok => ok += 1,
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
