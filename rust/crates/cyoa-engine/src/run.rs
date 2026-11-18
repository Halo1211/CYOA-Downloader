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

    let temp_guard = RunTempDir::create()?;
    let temp = temp_guard.0.clone();
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
    let processed = pipeline::process_images(
        &source.project_json,
        &source.base_url,
        ctx,
        &process_opts,
        progress,
    )
    .await?;
    if cancel.is_cancelled() {
        return Err(RunError::Cancelled);
    }

    let stem = output::build_output_name(&opts.url);
    let output_dir = opts.output_dir.clone();
    let user_url = opts.url.clone();
    let embed_str = processed.embed_str;
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
            written.push(output::save_embedded_json(
                &embed_str,
                &format!("{stem}.json"),
                &output_dir,
            )?);
            if embed_str != source.project_json {
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
                output::save_embedded_json(&source.project_json, "project_original.json", &temp)
                    .map_err(|e| RunError::OutputStage(format!("original ZIP JSON: {e}")))?;
            }
            output::save_embedded_json(&dl_str, "project.json", &temp)
                .map_err(|e| RunError::OutputStage(format!("localized ZIP JSON: {e}")))?;
            check()?;
            written.push(output::zip_package(
                &temp,
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
