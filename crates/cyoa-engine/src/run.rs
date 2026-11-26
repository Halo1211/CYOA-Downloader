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
    // A cancelled fetch looks like an ordinary page failure inside the static
    // downloader; never zip/report a partial gallery as success.
    if cancel.is_cancelled() {
        return Err(RunError::Cancelled);
    }
    let zip = matches!(opts.mode, OutputMode::IccZip | OutputMode::PureZip);
    let written = {
        let (site_b, stem_b, out_b) = (site.clone(), stem.clone(), opts.output_dir.clone());
        let zip_b = zip;
        let cancel_b = cancel.clone();
        tokio::task::spawn_blocking(move || {
            output::finalize_site_folder_cancellable(&site_b, &stem_b, zip_b, &out_b, &|| {
                cancel_b.is_cancelled()
            })
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("archive worker failed: {e}")))??
    };
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
        bandwidth: (opts.bandwidth_kbps > 0.0)
            .then(|| cyoa_net::throttle::BandwidthThrottle::new(opts.bandwidth_kbps)),
        gallery_dl: {
            let i = &opts.integrations;
            let mut g = crate::gallery_dl::GalleryDl::new(
                &i.gallery_dl_mode,
                &i.gallery_dl_path,
                &i.gallery_dl_config,
            );
            g.proxy = active_proxy(&opts.proxy);
            if opts.proxy.mode == cyoa_net::proxy::ProxyMode::Manual {
                g.no_proxy = opts.proxy.no_proxy.clone();
            }
            g.enabled().then(|| Arc::new(g))
        },
        discord: discord_refresher(opts, client, project_json),
        ai: ai_runtime(opts),
        browser: (opts.integrations.selenium && crate::browser::chromium_executable().is_some())
            .then(|| crate::browser::BrowserTransport::new(client.clone())),
        headless_gate: Arc::default(),
    })
}

/// image_pipeline.py 526-551: recovery is set up when the project has
/// Discord attachment images and refresh is not disabled.
fn discord_refresher(
    opts: &RunOptions,
    client: &FetchClient,
    project_json: &str,
) -> Option<Arc<crate::discord::DiscordRefresher>> {
    let assets = cyoa_parse::deep_scan::collect_project_assets(project_json);
    let count = assets
        .images
        .iter()
        .filter(|p| crate::discord::is_discord_attachment_url(p))
        .collect::<HashSet<_>>()
        .len();
    if count == 0 || !opts.integrations.discord_refresh {
        return None;
    }
    match crate::discord::resolve_bot_token(opts.integrations.discord_token.as_deref()) {
        Some(token) => {
            tracing::info!(
                "Discord recovery is active for {count} attachment(s); direct download is tried first."
            );
            Some(Arc::new(crate::discord::DiscordRefresher::new(
                &token,
                &opts.integrations.discord_api_root,
                client.clone(),
            )))
        }
        None => {
            tracing::info!(
                "Discord attachment URL(s) found. Configure a Bot Token in Settings > Discord Attachments to recover expired URLs automatically."
            );
            None
        }
    }
}

/// `--no-ytdlp` / `--ytdlp-cookies` for the yt-dlp audio step.
pub(crate) fn audio_config(opts: &RunOptions) -> crate::audio::AudioConfig {
    crate::audio::AudioConfig {
        enabled: opts.integrations.ytdlp,
        explicit_cookies: opts.integrations.ytdlp_cookies.clone(),
        browser_cookies: true,
    }
}

/// Python `network.proxy._get_active_proxy()`.
fn active_proxy(cfg: &cyoa_net::proxy::ProxyConfig) -> Option<String> {
    use cyoa_net::proxy::ProxyMode;
    match cfg.mode {
        ProxyMode::Disabled => None,
        ProxyMode::Manual => cfg
            .common
            .clone()
            .or_else(|| cfg.https.clone())
            .or_else(|| cfg.http.clone()),
        ProxyMode::InheritEnv => [
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
        ]
        .iter()
        .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty())),
    }
}

/// orchestrator.py: explicit name, else derived; always cleaned.
fn output_stem(opts: &RunOptions) -> String {
    match opts.file_name.as_deref().map(str::trim) {
        Some(n) if !n.is_empty() => output::clean_url_path_component(n),
        _ => output::build_output_name(&opts.url),
    }
}

/// Optional integrations configured per run (Python process globals).
#[derive(Clone, Debug)]
pub struct Integrations {
    /// `_ytdlp_enabled` (`--no-ytdlp` turns it off).
    pub ytdlp: bool,
    /// `CYOA_YTDLP_COOKIES`: validated Netscape cookies.txt path.
    pub ytdlp_cookies: Option<PathBuf>,
    /// `CYOA_DISCORD_BOT_TOKEN` (session only, never persisted or logged).
    pub discord_token: Option<String>,
    /// `CYOA_DISABLE_DISCORD_REFRESH` unset.
    pub discord_refresh: bool,
    /// `_set_selenium_enabled` (headless browser image fallback).
    pub selenium: bool,
    /// `_set_gallery_dl_mode`: off / smart / force.
    pub gallery_dl_mode: String,
    pub gallery_dl_path: String,
    pub gallery_dl_config: String,
    /// `_set_itch_enabled`.
    pub itch: bool,
    /// `--itch-mirror-web` / settings `itch_mirror_web`.
    pub itch_mirror_web: bool,
    /// `--itch-parallel` / settings `itch_parallel` (1..=16).
    pub itch_parallel: i32,
    /// `--itch-only` / settings `itch_only`: run only itch-dl for itch.io URLs.
    pub itch_only: bool,
    /// Discord API root (tests point it at a local mock).
    pub discord_api_root: String,
}

impl Default for Integrations {
    fn default() -> Self {
        Self {
            ytdlp: true,
            ytdlp_cookies: None,
            discord_token: None,
            discord_refresh: true,
            selenium: true,
            gallery_dl_mode: "off".into(),
            gallery_dl_path: "gallery-dl".into(),
            gallery_dl_config: String::new(),
            itch: false,
            itch_mirror_web: false,
            itch_parallel: 1,
            itch_only: false,
            discord_api_root: crate::discord::DEFAULT_API_ROOT.into(),
        }
    }
}

/// Python `engine_mode` (`--cyoap-vue-website/--cyoap-vue-folder` ->
/// CyoapVue, `--cyoap-vue` -> Auto probe, otherwise Standard).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Engine {
    #[default]
    Standard,
    CyoapVue,
    Auto,
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
    /// Python `engine_mode` for website modes: standard, cyoap_vue, auto.
    pub engine: Engine,
    /// `--fonts`: download fonts into the output and rewrite project URLs.
    pub fonts: bool,
    /// `--bandwidth`: aggregate download limit in KB/s (0 = unlimited).
    pub bandwidth_kbps: f64,
    /// `--http2` / settings `http2_enabled` (default off: HTTP/1.1).
    pub http2: bool,
    /// Cloudflare handling (`--cloudflare*`, `--flaresolverr-*`, settings).
    pub cloudflare: Option<Arc<cyoa_net::cloudflare::CloudflareConfig>>,
    /// Session toggles of cli.py (`--no-ytdlp`, `--ytdlp-cookies`,
    /// `--discord-token`, `--no-discord-refresh`, `--no-selenium`,
    /// `--gallery-dl*`); consumed by the media/fallback integrations.
    pub integrations: Integrations,
    /// Python `show_font_analysis`: log the font report.
    pub analyse_fonts: bool,
    /// Python `analysis_only` (CLI `--analyse-fonts` without `--fonts`):
    /// stop after the font report.
    pub analysis_only: bool,
    /// F-037: settings `offline_viewer_json_enabled` (Some = enabled) and
    /// `offline_viewer_preferred_id`; normal modes only, like orchestrator.py.
    pub offline_viewer: Option<OfflineViewerOptions>,
    /// F-043: AI Assist (None = off).
    pub ai: Option<crate::ai_run::AiRunConfig>,
}

/// The download's AI runtime, if AI Assist is configured and has a key.
pub(crate) fn ai_runtime(opts: &RunOptions) -> Option<Arc<crate::ai_run::AiRuntime>> {
    opts.ai
        .as_ref()?
        .runtime(&opts.proxy, opts.dns.clone(), opts.vpn.clone())
}

/// Offline viewer step of the normal modes (orchestrator.py 797-997).
#[derive(Clone, Debug)]
pub struct OfflineViewerOptions {
    /// `~/.cyoa_downloader/offline_viewers` (shared `viewers.json`).
    pub viewers_dir: PathBuf,
    /// `auto` or a registered viewer id.
    pub preferred_id: String,
    /// `cheat_enabled` (default on): inject the cheat panel overlay.
    pub cheat_panel: bool,
}

impl OfflineViewerOptions {
    /// Settings `offline_viewer_json_enabled` / `offline_viewer_preferred_id`
    /// with Python defaults (off, `auto`); `None` when disabled.
    pub fn from_settings(settings: &serde_json::Map<String, serde_json::Value>) -> Option<Self> {
        let enabled = settings
            .get("offline_viewer_json_enabled")
            .is_some_and(|v| match v {
                serde_json::Value::Bool(b) => *b,
                serde_json::Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
                serde_json::Value::String(s) => !s.is_empty(),
                serde_json::Value::Array(a) => !a.is_empty(),
                serde_json::Value::Object(o) => !o.is_empty(),
                serde_json::Value::Null => false,
            });
        if !enabled {
            return None;
        }
        let preferred = match settings.get("offline_viewer_preferred_id") {
            Some(serde_json::Value::String(s)) if !s.is_empty() => s.clone(),
            _ => "auto".to_owned(),
        };
        Some(Self {
            viewers_dir: cyoa_integrations::offline_viewers::viewers_dir(),
            preferred_id: preferred,
            cheat_panel: settings
                .get("cheat_enabled")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(true),
        })
    }
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
            engine: Engine::Standard,
            fonts: false,
            bandwidth_kbps: 0.0,
            http2: false,
            cloudflare: None,
            integrations: Integrations::default(),
            analyse_fonts: false,
            analysis_only: false,
            offline_viewer: None,
            ai: None,
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
    /// orchestrator.py RuntimeError text (engine cyoap_vue, no dist pair).
    #[error(
        "cyoap_vue mode selected, but dist/platform.json + dist/nodes/list.json were not found."
    )]
    CyoapNotFound,
    /// A website-mode error whose message must stay exactly Python's.
    #[error("{0}")]
    Website(String),
}

/// Python `_v466_is_cafe_metadata_game_url`: `https://cyoa.cafe/game/<key>`.
fn is_cafe_metadata_game_url(value: &str) -> bool {
    let Ok(canonical) = cyoa_core::url::canonicalize_url(value) else {
        return false;
    };
    let Ok(parsed) = Url::parse(&canonical) else {
        return false;
    };
    let mut path = String::new();
    for ch in parsed.path().chars() {
        if !(ch == '/' && path.ends_with('/')) {
            path.push(ch);
        }
    }
    let rest = path.trim_end_matches('/');
    parsed
        .host_str()
        .is_some_and(|h| h.eq_ignore_ascii_case("cyoa.cafe"))
        && rest.len() > 6
        && rest.is_char_boundary(6)
        && rest[..6].eq_ignore_ascii_case("/game/")
        && !rest[6..].contains('/')
}

/// Python `_v466_run_download` pre-resolution for website modes.
async fn resolve_cafe_website_target(
    url: &str,
    client: &FetchClient,
    cancel: &CancellationToken,
) -> String {
    if !is_cafe_metadata_game_url(url) {
        return url.to_owned();
    }
    let (c, h, k, u) = (
        client.clone(),
        tokio::runtime::Handle::current(),
        cancel.clone(),
        url.to_owned(),
    );
    let resolved = tokio::task::spawn_blocking(move || {
        let mut fetch = crate::discover_net::ClientCafeFetch {
            client: c,
            handle: h,
            cancel: k,
        };
        let record = crate::cafe::fetch_cyoa_cafe_record(&mut fetch, &u, false);
        if crate::cafe::classify_cyoa_cafe_record(record.as_ref()) == "static_pages" {
            return None;
        }
        crate::cafe::CafeResolver::new(&mut fetch).resolve(&u).ok()
    })
    .await
    .ok()
    .flatten();
    let canon = |v: &str| cyoa_core::url::canonicalize_url(v).unwrap_or_else(|_| v.to_owned());
    match resolved {
        Some(r) if canon(&r) != canon(url) => {
            tracing::info!("CYOA.CAFE website target resolved before download: {url} → {r}");
            r
        }
        _ => url.to_owned(),
    }
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
            // `--allow-internal-hosts` also lifts the connection-level screen.
            allow_internal: cyoa_core::allow_internal_hosts(),
        },
        budget: RequestBudget::new(16, 8),
        proxy: opts.proxy.clone(),
        dns: opts.dns.clone(),
        vpn: opts.vpn.clone(),
        http2: opts.http2,
        cloudflare: opts.cloudflare.clone(),
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
    // final_behaviors.py `_v466_run_download`: ICC/Pure Website mirror the
    // resolved viewer, not the CYOA.CAFE catalogue page (the output name
    // stays derived from the original URL).
    let website_url = if matches!(
        opts.mode,
        OutputMode::IccZip | OutputMode::IccFolder | OutputMode::PureZip | OutputMode::PureFolder
    ) {
        resolve_cafe_website_target(&opts.url, &client, &cancel).await
    } else {
        opts.url.clone()
    };
    // Pure website: no project discovery at all (orchestrator.py 374-424).
    if matches!(opts.mode, OutputMode::PureZip | OutputMode::PureFolder) {
        let temp_guard = RunTempDir::create()?;
        let spool_dir = temp_guard.0.join("spool");
        std::fs::create_dir(&spool_dir)?;
        let result = crate::pure::PureRun {
            url: &website_url,
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
            custom_fallback: false,
        }
        .run()
        .await;
        drop(temp_guard);
        return result;
    }
    // CYOAP Vue (orchestrator.py 426-446): website modes with engine
    // cyoap_vue/auto try the dist/platform.json + dist/nodes/list.json flow.
    if matches!(opts.mode, OutputMode::IccZip | OutputMode::IccFolder)
        && matches!(opts.engine, Engine::CyoapVue | Engine::Auto)
    {
        tracing::info!("Phase 0/4: probing cyoap_vue dist/ structure…");
        let stem = output_stem(opts);
        let site = opts.output_dir.join(&stem);
        let zip = opts.mode == OutputMode::IccZip;
        match crate::cyoap::try_download_cyoap_vue_site(
            &client,
            // The resolved CYOA.CAFE viewer, not the catalogue page.
            &website_url,
            &site,
            zip,
            opts.workers,
            &cancel,
        )
        .await
        {
            Ok(Some(written)) => {
                return Ok(RunReport {
                    stem,
                    written_paths: vec![written],
                    failed_assets: Vec::new(),
                });
            }
            Ok(None) if opts.engine == Engine::CyoapVue => return Err(RunError::CyoapNotFound),
            Ok(None) => tracing::info!(
                "cyoap_vue probe: no dist/platform.json + dist/nodes/list.json pair found; falling back."
            ),
            Err(e) if e == crate::cyoap::CYOAP_CANCELLED => return Err(RunError::Cancelled),
            Err(e) if opts.engine == Engine::CyoapVue => return Err(RunError::Website(e)),
            Err(e) => tracing::warn!(
                "cyoap_vue auto probe failed, falling back to standard resolver: {e}"
            ),
        }
    }
    let discovered = match discover_net::get_project_source(&client, &opts.url, &cancel).await {
        Err(DiscoverError::Fetch(cyoa_net::client::FetchError::Cancelled)) => {
            return Err(RunError::Cancelled);
        }
        Err(e) => {
            // discover.py Phase 5: AI-assisted project detection.
            let ai = ai_runtime(opts);
            let mut found = None;
            if let Some(ai) = &ai {
                let page = discover_net::fetch_page_text(&client, &opts.url, &cancel)
                    .await
                    .unwrap_or_default();
                found = ai.detect_project(&client, &opts.url, &page, &cancel).await;
            }
            match found {
                Some(s) => Ok(s),
                None => Err((e, ai)),
            }
        }
        Ok(s) => Ok(s),
    };
    let source = match discovered {
        Ok(s) => s,
        Err((e, ai)) => {
            tracing::warn!("project discovery failed: {e}");
            // orchestrator.py 484-527: an HTML page without a project payload
            // is archived as a custom viewer website (any output mode).
            let page = discover_net::fetch_page_text(&client, &opts.url, &cancel)
                .await
                .unwrap_or_default();
            if let Some(ai) = &ai {
                ai.viewer_diagnostics(&page, &opts.url, &cancel).await;
            }
            if !page.to_lowercase().contains("<html") {
                return Err(RunError::ProjectUnresolved);
            }
            let temp_guard = RunTempDir::create()?;
            let spool_dir = temp_guard.0.join("spool");
            std::fs::create_dir(&spool_dir)?;
            let zip = !matches!(opts.mode, OutputMode::IccFolder | OutputMode::PureFolder);
            let result = crate::pure::PureRun {
                url: &opts.url,
                stem: output_stem(opts),
                output_dir: opts.output_dir.clone(),
                zip,
                deep_scan: opts.deep_scan,
                workers: opts.workers,
                policy: opts.archive.clone(),
                client: client.clone(),
                run_opts: opts.clone(),
                cache: open_cache(opts),
                spool_dir,
                cancel: cancel.clone(),
                progress,
                custom_fallback: true,
            }
            .run()
            .await;
            drop(temp_guard);
            return result;
        }
    };

    // orchestrator.py 628-635: font analysis on the project + viewer HTML.
    // Python always fetches it once; Rust only when a consumer needs it.
    let viewer_html = if opts.fonts
        || opts.analyse_fonts
        || opts.analysis_only
        || opts.offline_viewer.is_some()
    {
        discover_net::fetch_page_text(&client, &opts.url, &cancel)
            .await
            .unwrap_or_default()
    } else {
        String::new()
    };
    if opts.analyse_fonts || opts.analysis_only {
        crate::fonts::analyse_fonts_with_client(
            &client,
            &cancel,
            &source.project_json,
            &source.base_url,
            &viewer_html,
        )
        .await;
        if opts.analysis_only {
            tracing::info!("Analysis-only mode complete; no download output written.");
            return Ok(RunReport {
                stem: output_stem(opts),
                written_paths: Vec::new(),
                failed_assets: Vec::new(),
            });
        }
    }

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
            entry_url: &website_url,
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
            fonts: opts.fonts,
            audio: audio_config(opts),
        }
        .run()
        .await;
    }
    // orchestrator.py 797-827: pick an offline viewer before image processing;
    // a selected viewer needs images/audio on disk even in embed-only mode.
    let viewer_mode = match mode {
        OutputMode::Both => "both",
        OutputMode::Zip => "zip",
        _ => "embed",
    };
    let mut viewer_meta = None;
    if let Some(v) = &opts.offline_viewer {
        viewer_meta = cyoa_integrations::offline_viewers::get_viewer_for_site(
            &v.viewers_dir,
            &viewer_html,
            viewer_mode,
            &serde_json::Value::String(source.project_json.clone()),
            &v.preferred_id,
        );
        if let Some(m) = &viewer_meta {
            log_viewer_selected("Offline viewer selected", m);
            if !download {
                tracing::info!(
                    "Offline viewer detected: enabling disk asset download for playable viewer output."
                );
            }
        }
    }
    let download_assets = download || viewer_meta.is_some();
    // orchestrator.py 835-839: fonts go into the work folder whenever assets
    // are downloaded, and the rewritten project feeds image processing.
    let working = if opts.fonts && download_assets {
        crate::fonts::download_fonts_with_client(
            &client,
            &cancel,
            &source.project_json,
            &source.base_url,
            &pkg,
            &viewer_html,
        )
        .await
        .map_err(RunError::OutputStage)?
    } else {
        source.project_json.clone()
    };
    let process_opts = ProcessOptions {
        embed,
        download: download_assets,
        max_workers: opts.workers.clamp(1, 64),
        download_dir: download_assets.then(|| pkg.clone()),
        embed_out: embed.then(|| embed_out.clone()),
        source_url: opts.url.clone(),
        report_dir: Some(opts.output_dir.clone()),
        youtube: Some(audio_config(opts)),
        ..Default::default()
    };
    let processed =
        pipeline::process_images(&working, &source.base_url, ctx, &process_opts, progress).await?;
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
    // Python compares the embedded output with the original `cleaned`.
    let embed_changed = processed.embed_changed || working != source.project_json;
    let dl_str = processed.dl_str;
    let stage_cancel = cancel.clone();
    let viewer_stage = (
        pkg.clone(),
        dl_str.clone(),
        stem.clone(),
        output_dir.clone(),
    );
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
            // orchestrator.py 885-893: audio beside the JSON for the viewer.
            let tmp_audio = pkg.join("audio");
            if tmp_audio.is_dir() {
                check()?;
                let report = cyoa_core::paths::copytree_merge_safe(
                    &tmp_audio,
                    &output_dir.join("audio"),
                    "audio",
                );
                tracing::info!("Saved/merged: audio/ ({} file(s))", report.copied);
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
            written.push(output::zip_package_cancellable(
                &pkg,
                &format!("{stem}.zip"),
                &output_dir,
                &|| stage_cancel.is_cancelled(),
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
    let mut written_paths = written_paths;
    if let Some(v) = opts.offline_viewer.clone() {
        if cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }
        let fetch = discover_net::ClientCafeFetch {
            client: client.clone(),
            handle: tokio::runtime::Handle::current(),
            cancel: cancel.clone(),
        };
        let url = opts.url.clone();
        let (pkg, dl_str, stem, output_dir) = viewer_stage;
        let out = tokio::task::spawn_blocking(move || {
            offline_viewer_stage(
                &v,
                &fetch,
                viewer_meta,
                &viewer_html,
                viewer_mode,
                &url,
                &pkg,
                &dl_str,
                &stem,
                &output_dir,
            )
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("offline viewer worker failed: {e}")))?;
        written_paths.extend(out);
    }
    drop(temp_guard);
    Ok(RunReport {
        stem: report_stem,
        written_paths,
        failed_assets: processed.failed,
    })
}

fn log_viewer_selected(prefix: &str, m: &serde_json::Map<String, serde_json::Value>) {
    let s = |k: &str| m.get(k).and_then(serde_json::Value::as_str).unwrap_or("");
    let name = if s("name").is_empty() {
        s("id")
    } else {
        s("name")
    };
    let family = if s("detected_family").is_empty() {
        "unknown"
    } else {
        s("detected_family")
    };
    let reason = if s("selection_reason").is_empty() {
        "automatic match"
    } else {
        s("selection_reason")
    };
    tracing::info!("{prefix}: {name} (family={family}; reason={reason})");
}

/// orchestrator.py 945-997 (runs on a blocking thread).
#[allow(clippy::too_many_arguments)]
fn offline_viewer_stage(
    v: &OfflineViewerOptions,
    fetch: &discover_net::ClientCafeFetch,
    preselected: Option<serde_json::Map<String, serde_json::Value>>,
    viewer_html: &str,
    viewer_mode: &str,
    url: &str,
    pkg: &std::path::Path,
    dl_str: &str,
    stem: &str,
    output_dir: &std::path::Path,
) -> Option<PathBuf> {
    let mut page_html = String::new();
    let meta = match preselected {
        Some(m) => m,
        None => {
            page_html = fetch
                .handle
                .block_on(discover_net::fetch_page_text(
                    &fetch.client,
                    url,
                    &fetch.cancel,
                ))
                .unwrap_or_default();
            let m = cyoa_integrations::offline_viewers::get_viewer_for_site(
                &v.viewers_dir,
                &page_html,
                viewer_mode,
                &serde_json::Value::String(dl_str.to_owned()),
                &v.preferred_id,
            )?;
            log_viewer_selected("Offline viewer selected after page inspection", &m);
            m
        }
    };
    let sources: Vec<(String, PathBuf)> = ["images", "audio"]
        .iter()
        .map(|n| ((*n).to_owned(), pkg.join(n)))
        .filter(|(_, p)| p.is_dir())
        .collect();
    let source_html = if viewer_html.is_empty() {
        page_html.as_str()
    } else {
        viewer_html
    };
    let out = crate::offline_viewer::apply_offline_viewer(crate::offline_viewer::ApplyOptions {
        viewers_dir: &v.viewers_dir,
        output_dir,
        project_json_str: dl_str,
        viewer_meta: &meta,
        file_name: stem,
        asset_source_dirs: Some(&sources),
        source_html,
        source_url: url,
        preserved_asset_cache_dir: None,
        fetcher: fetch,
        allow_internal: false,
        cheat_panel: v.cheat_panel,
    })?;
    let name = meta
        .get("name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let rel = out
        .strip_prefix(output_dir)
        .unwrap_or(&out)
        .display()
        .to_string();
    tracing::info!("Offline viewer: {name} → {rel}");
    Some(out)
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
