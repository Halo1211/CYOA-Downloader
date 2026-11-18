use crate::{
    discover_net::{self, DiscoverError},
    fetch::FetchCtx,
    output::{self, OutputError},
    pipeline::{self, FailedAsset, PipelineError, ProcessOptions, Progress},
};
use cyoa_net::{
    admission::RequestBudget,
    client::{ClientConfig, FetchClient},
    policy::DestinationPolicy,
    proxy::ProxyConfig,
    throttle::DomainThrottle,
};
use std::{
    collections::HashSet,
    net::{IpAddr, ToSocketAddrs},
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};
use thiserror::Error;
use tokio::sync::mpsc::Sender;
use tokio_util::sync::CancellationToken;
use url::Url;

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

/// Python `runtime/state.py::_domain_min_interval`: minimum spacing between
/// requests to one host (politeness; also what bounds Python's throughput).
pub const DOMAIN_MIN_INTERVAL: f64 = 0.3;

/// Exclusively created private work directory, removed on drop (also when the
/// run future is aborted).
struct RunTempDir(PathBuf);

impl RunTempDir {
    fn create() -> std::io::Result<Self> {
        let base = std::env::temp_dir();
        loop {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.subsec_nanos());
            let path = base.join(format!(
                "cyoa-run-{}-{}-{nanos:08x}",
                std::process::id(),
                NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
            ));
            match std::fs::create_dir(&path) {
                Ok(()) => return Ok(Self(path)),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
    }
}

impl Drop for RunTempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// CYOA.CAFE static-page export (`download_cyoa_cafe_static_record`).
async fn static_cafe_record(
    opts: &RunOptions,
    client: &FetchClient,
    cancel: &CancellationToken,
) -> Result<Option<RunReport>, RunError> {
    let (c, h, k, url) = (
        client.clone(),
        tokio::runtime::Handle::current(),
        cancel.clone(),
        opts.url.clone(),
    );
    let stem = output_stem(opts);
    let site = output::unique_folder(&opts.output_dir.join(&stem));
    let site_c = site.clone();
    let workers = opts.workers;
    let outcome = tokio::task::spawn_blocking(move || -> Result<bool, RunError> {
        let mut fetch = crate::discover_net::ClientCafeFetch {
            client: c,
            handle: h,
            cancel: k,
        };
        let record = crate::cafe::fetch_cyoa_cafe_record(&mut fetch, &url, false);
        if crate::cafe::classify_cyoa_cafe_record(record.as_ref()) != "static_pages" {
            return Ok(false);
        }
        let Some(record) = record else {
            return Ok(false);
        };
        tracing::info!(
            "CYOA.CAFE static-page record detected → {}/",
            site_c.display()
        );
        output::prepare_clean_output_folder(&site_c)?;
        crate::cafe_static::download_static_record(&record, &site_c, &url, workers, &fetch)
            .map_err(RunError::OutputStage)?;
        Ok(true)
    })
    .await
    .map_err(|e| RunError::OutputStage(format!("static gallery worker failed: {e}")))??;
    if !outcome {
        return Ok(None);
    }
    let zip = matches!(opts.mode, OutputMode::IccZip | OutputMode::PureZip);
    let written = output::finalize_site_folder(&site, &stem, zip, &opts.output_dir)?;
    tracing::info!("CYOA.CAFE static archive complete.");
    Ok(Some(RunReport {
        stem,
        written_paths: vec![written.unwrap_or(site)],
        failed_assets: Vec::new(),
    }))
}

/// A cache that cannot be opened only disables caching (Python logs and continues).
fn open_cache(opts: &RunOptions) -> Option<Arc<cyoa_storage::cache::ImageCache>> {
    opts.cache_dir.as_ref().and_then(|dir| {
        match cyoa_storage::cache::ImageCache::open(dir, opts.cache_limit_mb) {
            Ok(c) => Some(Arc::new(c)),
            Err(e) => {
                tracing::warn!("image cache disabled: {e}");
                None
            }
        }
    })
}

/// Fetch context for `process_images` over `project_json` (image paths are
/// the project's image references, like Python `image_paths`).
pub(crate) fn build_ctx(
    opts: &RunOptions,
    client: &FetchClient,
    cancel: &CancellationToken,
    base_url: String,
    project_json: &str,
    cache: Option<Arc<cyoa_storage::cache::ImageCache>>,
    spool_dir: PathBuf,
) -> Arc<FetchCtx> {
    let source_urls = cyoa_parse::deep_scan::collect_project_assets(project_json);
    let image_paths: HashSet<String> = source_urls.images.into_iter().collect();
    let resolver = Arc::new(|host: &str| {
        (host, 0)
            .to_socket_addrs()
            .map(|values| values.map(|v| v.ip()).collect::<Vec<IpAddr>>())
            .unwrap_or_default()
    });
    Arc::new(FetchCtx {
        client: client.clone(),
        cache,
        domain: Arc::new(DomainThrottle::new(DOMAIN_MIN_INTERVAL)),
        base_url,
        image_paths,
        wait_time: opts.wait_time as f64,
        max_bytes: 512 * 1024 * 1024,
        cancel: cancel.clone(),
        resolver,
        spool_dir: Some(spool_dir),
    })
}

/// orchestrator.py: explicit name, else derived; always cleaned.
fn output_stem(opts: &RunOptions) -> String {
    match opts.file_name.as_deref().map(str::trim) {
        Some(n) if !n.is_empty() => output::clean_url_path_component(n),
        _ => output::build_output_name(&opts.url),
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum OutputMode {
    #[default]
    Embed,
    Zip,
    Both,
    /// `--icc`: full viewer website, zipped to `<name>_site.zip`.
    IccZip,
    /// `--icc-folder`: full viewer website kept as a folder.
    IccFolder,
    /// `--pure-website`: viewer archive without project discovery, zipped.
    PureZip,
    /// `--pure-website-folder`: the same, kept as a folder.
    PureFolder,
}

#[derive(Clone)]
pub struct RunOptions {
    pub url: String,
    pub output_dir: PathBuf,
    pub mode: OutputMode,
    pub workers: usize,
    pub wait_time: u64,
    /// Persistent image cache shared with Python (`~/.cyoa_downloader/image_cache`).
    /// `None` disables caching.
    pub cache_dir: Option<PathBuf>,
    /// Python setting `image_cache_max_mb` (default 2048).
    pub cache_limit_mb: u64,
    /// Optional output name (Python `file_name`; cleaned like Python).
    pub file_name: Option<String>,
    /// F-016 proxy profile for this run.
    pub proxy: ProxyConfig,
    pub dns: Option<Arc<cyoa_net::dns_resolve::CustomResolver>>,
    /// F-018 VPN guard (`require` policy only).
    pub vpn: Option<Arc<cyoa_net::vpn::VpnGuard>>,
    /// ICC deep scan (`--no-deep-scan` turns it off; Python default on).
    pub deep_scan: bool,
    /// Python `ArchivePolicy` from settings (`archive_*`); auto by default.
    pub archive: crate::icc::ArchivePolicy,
}

/// Python default for `image_cache_max_mb`.
pub const DEFAULT_CACHE_LIMIT_MB: u64 = 2048;

impl Default for RunOptions {
    fn default() -> Self {
        Self {
            url: String::new(),
            output_dir: PathBuf::from("."),
            mode: OutputMode::Embed,
            workers: 4,
            wait_time: 60,
            cache_dir: cyoa_storage::cache::ImageCache::default_root(),
            cache_limit_mb: DEFAULT_CACHE_LIMIT_MB,
            file_name: None,
            proxy: ProxyConfig::disabled(),
            dns: None,
            vpn: None,
            deep_scan: true,
            archive: crate::icc::ArchivePolicy::default(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunReport {
    /// Cleaned output name (`file_name` in orchestrator.py).
    pub stem: String,
    pub written_paths: Vec<PathBuf>,
    pub failed_assets: Vec<FailedAsset>,
}

#[derive(Debug, Error)]
pub enum RunError {
    /// orchestrator.py's message when no project source is found (AI disabled).
    #[error(
        "Could not resolve project data (project.json / project.txt / embedded JS / zip payload).
If this site uses a custom viewer without a standard project file,
try using mode: ICC ZIP/Folder or --icc to download
the viewer HTML/CSS/JS directly without needing a project file."
    )]
    ProjectUnresolved,
    #[error("invalid URL: expected an absolute http:// or https:// URL")]
    InvalidUrl,
    #[error("could not prepare output directory: {0}")]
    OutputDirectory(#[from] std::io::Error),
    #[error(transparent)]
    Discover(#[from] DiscoverError),
    #[error(transparent)]
    Pipeline(#[from] PipelineError),
    #[error(transparent)]
    Output(#[from] OutputError),
    #[error("output step failed: {0}")]
    OutputStage(String),
    #[error("download cancelled")]
    Cancelled,
}

pub async fn run_download(
    opts: &RunOptions,
    cancel: CancellationToken,
    progress: Option<Sender<Progress>>,
) -> Result<RunReport, RunError> {
    let url = Url::parse(&opts.url).map_err(|_| RunError::InvalidUrl)?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err(RunError::InvalidUrl);
    }
    std::fs::create_dir_all(&opts.output_dir)?;
    if !opts.output_dir.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "output path is not a directory",
        )
        .into());
    }
    if cancel.is_cancelled() {
        return Err(RunError::Cancelled);
    }

    let client = FetchClient::new(ClientConfig {
        policy: DestinationPolicy {
            origin: Some(url.clone()),
            allow_internal: false,
        },
        budget: RequestBudget::new(16, 8),
        proxy: opts.proxy.clone(),
        dns: opts.dns.clone(),
        vpn: opts.vpn.clone(),
        ..ClientConfig::default()
    })
    .map_err(|e| RunError::OutputDirectory(std::io::Error::other(e.to_string())))?;
    // CYOA.CAFE static-page records (orchestrator.py 354-372): ICC and Pure
    // Website export the PocketBase page files directly as an offline gallery.
    if matches!(
        opts.mode,
        OutputMode::IccZip | OutputMode::IccFolder | OutputMode::PureZip | OutputMode::PureFolder
    ) && let Some(report) = static_cafe_record(opts, &client, &cancel).await?
    {
        return Ok(report);
    }
    // Pure website: no project discovery at all (orchestrator.py 374-424).
    if matches!(opts.mode, OutputMode::PureZip | OutputMode::PureFolder) {
        let temp_guard = RunTempDir::create()?;
        let spool_dir = temp_guard.0.join("spool");
        std::fs::create_dir(&spool_dir)?;
        let result = crate::pure::PureRun {
            url: &opts.url,
            stem: output_stem(opts),
            output_dir: opts.output_dir.clone(),
            zip: opts.mode == OutputMode::PureZip,
            deep_scan: opts.deep_scan,
            workers: opts.workers,
            policy: opts.archive.clone(),
            client: client.clone(),
            run_opts: opts.clone(),
            cache: open_cache(opts),
            spool_dir,
            cancel: cancel.clone(),
            progress,
        }
        .run()
        .await;
        drop(temp_guard);
        return result;
    }
    let source = match discover_net::get_project_source(&client, &opts.url, &cancel).await {
        Ok(s) => s,
        Err(DiscoverError::Fetch(cyoa_net::client::FetchError::Cancelled)) => {
            return Err(RunError::Cancelled);
        }
        Err(e) => {
            tracing::warn!("project discovery failed: {e}");
            return Err(RunError::ProjectUnresolved);
        }
    };

    let temp_guard = RunTempDir::create()?;
    let temp = temp_guard.0.clone();
    // pkg/ becomes the ZIP; spool/ holds fetched bodies; embedded.json is the
    // streamed embed output. Keeping them apart keeps spool files out of the ZIP.
    let pkg = temp.join("pkg");
    let spool_dir = temp.join("spool");
    let embed_out = temp.join("embedded.json");
    std::fs::create_dir(&pkg)?;
    std::fs::create_dir(&spool_dir)?;
    let mode = opts.mode;
    let embed = matches!(mode, OutputMode::Embed | OutputMode::Both);
    let download = matches!(mode, OutputMode::Zip | OutputMode::Both);
    let cache = open_cache(opts);
    let ctx = build_ctx(
        opts,
        &client,
        &cancel,
        source.base_url.clone(),
        &source.project_json,
        cache.clone(),
        spool_dir,
    );
    if matches!(mode, OutputMode::IccZip | OutputMode::IccFolder) {
        // temp_guard stays alive until the ICC run returns: the fetch spool
        // directory lives inside it.
        return crate::icc::IccRun {
            entry_url: &opts.url,
            source: &source,
            stem: output_stem(opts),
            output_dir: opts.output_dir.clone(),
            zip: mode == OutputMode::IccZip,
            deep_scan: opts.deep_scan,
            policy: opts.archive.clone(),
            workers: opts.workers,
            client: client.clone(),
            ctx,
            cancel: cancel.clone(),
            progress,
        }
        .run()
        .await;
    }
    let process_opts = ProcessOptions {
        embed,
        download,
        max_workers: opts.workers.clamp(1, 64),
        download_dir: download.then(|| pkg.clone()),
        embed_out: embed.then(|| embed_out.clone()),
        source_url: opts.url.clone(),
        report_dir: Some(opts.output_dir.clone()),
        ..Default::default()
    };
    let processed = pipeline::process_images(
        &source.project_json,
        &source.base_url,
        ctx,
        &process_opts,
        progress,
    )
    .await?;
    if let Some(c) = &cache
        && let Err(e) = c.flush()
    {
        tracing::warn!("image cache index flush failed: {e}");
    }
    if cancel.is_cancelled() {
        return Err(RunError::Cancelled);
    }

    let stem = output_stem(opts);
    let report_stem = stem.clone();
    let output_dir = opts.output_dir.clone();
    let user_url = opts.url.clone();
    let embed_str = processed.embed_str;
    let embed_file = processed.embed_file;
    let embed_changed = processed.embed_changed;
    let dl_str = processed.dl_str;
    let stage_cancel = cancel.clone();
    // Blocking file I/O and compression run off the async runtime. Cancellation is
    // checked before every publication; `temp_guard` cleans the work dir on drop.
    let written_paths = tokio::task::spawn_blocking(move || -> Result<Vec<PathBuf>, RunError> {
        let check = || {
            if stage_cancel.is_cancelled() {
                Err(RunError::Cancelled)
            } else {
                Ok(())
            }
        };
        let mut written = Vec::new();
        if embed {
            check()?;
            let name = format!("{stem}.json");
            written.push(match &embed_file {
                Some(file) => output::save_file_no_clobber(file, &name, &output_dir)?,
                None => output::save_embedded_json(&embed_str, &name, &output_dir)?,
            });
            if embed_changed {
                check()?;
                written.push(output::save_embedded_json(
                    &source.project_json,
                    &format!("{stem}_original.json"),
                    &output_dir,
                )?);
            }
        }
        if download {
            if dl_str != source.project_json {
                output::save_embedded_json(&source.project_json, "project_original.json", &pkg)
                    .map_err(|e| RunError::OutputStage(format!("original ZIP JSON: {e}")))?;
            }
            output::save_embedded_json(&dl_str, "project.json", &pkg)
                .map_err(|e| RunError::OutputStage(format!("localized ZIP JSON: {e}")))?;
            check()?;
            written.push(output::zip_package(
                &pkg,
                &format!("{stem}.zip"),
                &output_dir,
            )?);
        }
        // Python opens `{file_name}_metadata.json` with "w" (overwrites, no suffix).
        check()?;
        let meta_path = output_dir.join(format!("{stem}_metadata.json"));
        let meta =
            crate::reports::project_metadata(&source.project_json, &user_url, &source.project_url);
        match cyoa_storage::atomic_io::atomic_write_text(&meta_path, &meta) {
            Ok(_) => written.push(meta_path),
            Err(e) => tracing::warn!("Could not save metadata: {e}"),
        }
        Ok(written)
    })
    .await
    .map_err(|e| RunError::OutputStage(format!("output worker failed: {e}")))??;
    drop(temp_guard);
    Ok(RunReport {
        stem: report_stem,
        written_paths,
        failed_assets: processed.failed,
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod temp_dir_tests {
    use super::RunTempDir;

    #[test]
    fn run_temp_dir_is_exclusive_and_removed_on_drop() {
        let a = RunTempDir::create().unwrap();
        let b = RunTempDir::create().unwrap();
        assert_ne!(a.0, b.0);
        std::fs::write(a.0.join("x"), "1").unwrap();
        let path = a.0.clone();
        drop(a);
        assert!(!path.exists());
    }
}
