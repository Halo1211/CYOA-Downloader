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

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum OutputMode {
    #[default]
    Embed,
    Zip,
    Both,
}

#[derive(Clone, Debug)]
pub struct RunOptions {
    pub url: String,
    pub output_dir: PathBuf,
    pub mode: OutputMode,
    pub workers: usize,
    pub wait_time: u64,
}
impl Default for RunOptions {
    fn default() -> Self {
        Self {
            url: String::new(),
            output_dir: PathBuf::from("."),
            mode: OutputMode::Embed,
            workers: 4,
            wait_time: 60,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunReport {
    pub written_paths: Vec<PathBuf>,
    pub failed_assets: Vec<FailedAsset>,
}

#[derive(Debug, Error)]
pub enum RunError {
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
        ..ClientConfig::default()
    })
    .map_err(|e| RunError::OutputDirectory(std::io::Error::other(e.to_string())))?;
    let source = discover_net::get_project_source(&client, &opts.url, &cancel).await?;

    let temp = std::env::temp_dir().join(format!(
        "cyoa-run-{}-{}",
        std::process::id(),
        NEXT_TEMP.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&temp)?;
    let mode = opts.mode;
    let embed = matches!(mode, OutputMode::Embed | OutputMode::Both);
    let download = matches!(mode, OutputMode::Zip | OutputMode::Both);
    let source_urls = cyoa_parse::deep_scan::collect_project_assets(&source.project_json);
    let image_paths: HashSet<String> = source_urls.images.into_iter().collect();
    let resolver = Arc::new(|host: &str| {
        (host, 0)
            .to_socket_addrs()
            .map(|values| values.map(|v| v.ip()).collect::<Vec<IpAddr>>())
            .unwrap_or_default()
    });
    let ctx = Arc::new(FetchCtx {
        client: client.clone(),
        cache: None,
        domain: Arc::new(DomainThrottle::new(0.0)),
        base_url: source.base_url.clone(),
        image_paths,
        wait_time: opts.wait_time as f64,
        max_bytes: 512 * 1024 * 1024,
        cancel: cancel.clone(),
        resolver,
    });
    let process_opts = ProcessOptions {
        embed,
        download,
        max_workers: opts.workers.clamp(1, 64),
        download_dir: download.then(|| temp.clone()),
        source_url: opts.url.clone(),
        report_dir: Some(opts.output_dir.clone()),
        ..Default::default()
    };
    let processed = match pipeline::process_images(
        &source.project_json,
        &source.base_url,
        ctx,
        &process_opts,
        progress,
    )
    .await
    {
        Ok(v) => v,
        Err(e) => {
            let _ = std::fs::remove_dir_all(&temp);
            return Err(e.into());
        }
    };
    if cancel.is_cancelled() {
        let _ = std::fs::remove_dir_all(&temp);
        return Err(RunError::Cancelled);
    }

    let stem = output::build_output_name(&opts.url);
    let mut written_paths = Vec::new();
    let output_result = (|| -> Result<(), RunError> {
        if embed {
            written_paths.push(output::save_embedded_json(
                &processed.embed_str,
                &format!("{stem}.json"),
                &opts.output_dir,
            )?);
            if processed.embed_str != source.project_json {
                written_paths.push(output::save_embedded_json(
                    &source.project_json,
                    &format!("{stem}_original.json"),
                    &opts.output_dir,
                )?);
            }
        }
        if download {
            if processed.dl_str != source.project_json {
                output::save_embedded_json(&source.project_json, "project_original.json", &temp)
                    .map_err(|e| RunError::OutputStage(format!("original ZIP JSON: {e}")))?;
            }
            output::save_embedded_json(&processed.dl_str, "project.json", &temp)
                .map_err(|e| RunError::OutputStage(format!("localized ZIP JSON: {e}")))?;
            written_paths.push(output::zip_package(
                &temp,
                &format!("{stem}.zip"),
                &opts.output_dir,
            )?);
        }
        // Python opens `{file_name}_metadata.json` with "w" (overwrites, no suffix).
        let meta_path = opts.output_dir.join(format!("{stem}_metadata.json"));
        let meta =
            crate::reports::project_metadata(&source.project_json, &opts.url, &source.project_url);
        match cyoa_storage::atomic_io::atomic_write_text(&meta_path, &meta) {
            Ok(_) => written_paths.push(meta_path),
            Err(e) => tracing::warn!("Could not save metadata: {e}"),
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&temp);
    output_result?;
    Ok(RunReport {
        written_paths,
        failed_assets: processed.failed,
    })
}
