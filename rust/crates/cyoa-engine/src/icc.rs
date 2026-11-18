//! ICC website mode (`--icc` / `--icc-folder`): orchestrator.py 636-785.
//!
//! Classic archive strategy: mirror the viewer (`MirrorSink`), localize the
//! project images into the site folder, write `project.json` (+ original),
//! second-pass text localization, deep scan, `backup_report.txt` manifest and
//! integrity section, then keep the folder or zip it to `<name>_site.zip`.
//!
//! Not ported (FEATURE_PARITY F-026): Smart/Browser/Auto archive strategies
//! and route crawl, `--fonts` project font pass, opt-in offline viewer
//! modernization (F-037), the CYOA.CAFE authoritative-viewer entry rewrite.

use std::collections::{BTreeSet, HashMap};
use std::path::PathBuf;
use std::sync::Arc;

use cyoa_net::client::FetchClient;
use tokio_util::sync::CancellationToken;

use crate::discover_net::ProjectSource;
use crate::fetch::FetchCtx;
use crate::output;
use crate::pipeline::{self, ProcessOptions, Progress};
use crate::run::{RunError, RunReport};
use crate::website::{deep_driver::DeepScan, integrity, mirror::MirrorSink};

/// Inputs for one ICC run (built by `run::run_download`).
pub struct IccRun<'a> {
    pub entry_url: &'a str,
    pub source: &'a ProjectSource,
    pub stem: String,
    pub output_dir: PathBuf,
    pub zip: bool,
    pub deep_scan: bool,
    /// Python default `archive_strategy = "auto"`: with project data the
    /// profile is `project_json` -> Classic, the manifest is written and the
    /// recovery pass and deep scan are skipped. `false` = explicit "classic".
    pub auto_strategy: bool,
    pub workers: usize,
    pub client: FetchClient,
    pub ctx: Arc<FetchCtx>,
    pub cancel: CancellationToken,
    pub progress: Option<tokio::sync::mpsc::Sender<Progress>>,
}

impl IccRun<'_> {
    pub async fn run(self) -> Result<RunReport, RunError> {
        let site = output::unique_folder(&self.output_dir.join(&self.stem));
        tracing::info!("Downloading full website → {}/", site.display());
        output::prepare_clean_output_folder(&site)?;

        // 1. Mirror the viewer (blocking client calls run off the async workers).
        let (entry, base) = (self.entry_url.to_owned(), self.source.base_url.clone());
        let (site_c, client, cancel) = (site.clone(), self.client.clone(), self.cancel.clone());
        let handle = tokio::runtime::Handle::current();
        let pre_deep_scan = !self.auto_strategy && self.deep_scan;
        let workers = self.workers;
        let mut sink = tokio::task::spawn_blocking(move || {
            let mut sink =
                MirrorSink::new(site_c, &entry, &base, client, handle, cancel, 512 << 20);
            sink.workers = workers;
            let result = sink.download_site(pre_deep_scan);
            (sink, result)
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))
        .and_then(|(sink, r)| r.map(|()| sink).map_err(RunError::OutputStage))?;
        if self.cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }
        if self.auto_strategy {
            self.write_archive_manifest(&site)?;
        }

        // 2. Project images into a temp tree, reusing files the mirror saved.
        let tmp = tempfile_dir()?;
        let opts = ProcessOptions {
            embed: false,
            download: true,
            max_workers: self.workers.clamp(1, 64),
            download_dir: Some(tmp.path.clone()),
            report_dir: Some(site.clone()),
            site_folder: Some(site.clone()),
            source_url: self.entry_url.to_owned(),
            ..Default::default()
        };
        let processed = pipeline::process_images(
            &self.source.project_json,
            &self.source.base_url,
            self.ctx.clone(),
            &opts,
            self.progress.clone(),
        )
        .await?;
        for (name, label) in [("images", "website images"), ("audio", "website audio")] {
            let src = tmp.path.join(name);
            if src.is_dir() {
                let report = cyoa_core::paths::copytree_merge_safe(&src, &site.join(name), label);
                tracing::info!("Saved/merged: {name}/ ({} file(s))", report.copied);
            }
        }
        if processed.dl_str != self.source.project_json {
            output::save_embedded_json(&self.source.project_json, "project_original.json", &site)?;
            tracing::info!("Saved: project_original.json (raw URLs before localization)");
        }

        // 3. project.json + second pass (blocking file/network work).
        let (project_url, dl) = (self.source.project_url.clone(), processed.dl_str.clone());
        let auto = self.auto_strategy;
        let sink = tokio::task::spawn_blocking(move || {
            let r = sink.write_project_payload(&project_url, &dl);
            if r.is_ok() {
                if auto {
                    tracing::info!(
                        "[Auto] Website text assets were already localized; skipping duplicate recovery pass."
                    );
                } else {
                    sink.localize_existing_text_assets();
                }
            }
            r.map(|()| sink)
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))?
        .map_err(RunError::OutputStage)?;

        // 4. Deep scan (Python default on; skip_urls = process_images URLs).
        if self.auto_strategy {
            tracing::info!(
                "[Auto] Project assets were localized by process_images; skipping redundant post-project JS/CSS deep scan."
            );
        } else if self.deep_scan {
            // File walking/parsing is blocking work: run it on a blocking
            // thread that drives the async fetches via the runtime handle.
            let skip: BTreeSet<String> = processed.resolved_urls.iter().cloned().collect();
            let (site_c, base, client, cancel, workers) = (
                site.clone(),
                self.source.base_url.clone(),
                self.client.clone(),
                self.cancel.clone(),
                self.workers,
            );
            let handle = tokio::runtime::Handle::current();
            tokio::task::spawn_blocking(move || {
                let mut sources = HashMap::new();
                handle.block_on(
                    DeepScan {
                        folder: &site_c,
                        base_url: &base,
                        client: &client,
                        cancel: &cancel,
                        max_workers: workers,
                        skip_urls: &skip,
                        exclude_relative_paths: &["project.json"],
                        source_urls: &mut sources,
                    }
                    .run(),
                )
            })
            .await
            .map_err(|e| RunError::OutputStage(format!("deep scan worker failed: {e}")))?;
        } else {
            tracing::info!("Deep scan disabled by toggle — skipping JS/CSS asset pass.");
        }
        tracing::info!("Offline website modernization is disabled in Settings.");
        if self.cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }

        // 5. Manifest, integrity, finalize.
        let project_url = self.source.project_url.clone();
        let site_c = site.clone();
        let check = tokio::task::spawn_blocking(move || {
            let manifest = sink.write_manifest(&project_url);
            let result = integrity::validate_integrity(&site_c);
            if let Err(e) = integrity::append_to_backup_report(&site_c, &result) {
                tracing::debug!("integrity report append failed: {e}");
            }
            manifest
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))?;
        check.map_err(RunError::OutputStage)?;
        let zip = output::finalize_site_folder(&site, &self.stem, self.zip, &self.output_dir)?;
        tracing::info!("ICC download complete.");
        Ok(RunReport {
            stem: self.stem,
            written_paths: vec![zip.unwrap_or(site)],
            failed_assets: processed.failed,
        })
    }
}

impl IccRun<'_> {
    /// `archive_runner.run_archive_extensions` for Auto -> Classic (no route
    /// crawl): `archive_manifest.json` exactly as Python writes it.
    fn write_archive_manifest(&self, site: &std::path::Path) -> Result<(), RunError> {
        let policy = |strategy: &str| {
            serde_json::json!({
                "strategy": strategy,
                "max_pages": 300,
                "max_depth": 30,
                "capture_interactions": false,
                "interaction_policy": "safe",
                "settle_time_ms": 1800,
                "runtime_max_pages": 12,
                "max_scroll_steps": 100,
                "max_interactions": 20,
                "no_progress_rounds": 2
            })
        };
        let pages: Vec<serde_json::Value> = if site.join("index.html").is_file() {
            vec![serde_json::json!({"url": self.entry_url, "local": "index.html"})]
        } else {
            Vec::new()
        };
        let manifest = serde_json::json!({
            "format": 1,
            "requested_policy": policy("auto"),
            "policy": policy("classic"),
            "auto_profile": {
                "detected_engine": "project_json",
                "effective_strategy": "classic",
                "reason": "structured project data already enumerates the authoritative assets",
                "signals": {
                    "source_url": self.entry_url,
                    "project_url": self.source.project_url,
                    "browser_needed": false
                }
            },
            "start_url": self.entry_url,
            "pages": pages,
            "route_failures": [],
            "route_limit_reached": false,
            "remaining_queued_routes": 0,
            "runtime": null
        });
        let text = serde_json::to_string_pretty(&manifest)
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        let path = crate::website::mirror::contained_path(site, "archive_manifest.json")
            .ok_or_else(|| RunError::OutputStage("unsafe manifest path".into()))?;
        cyoa_storage::atomic_io::atomic_write_text(&path, &text)
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        tracing::info!(
            "Auto archive selected Classic; browser/route stages skipped: structured project data already enumerates the authoritative assets"
        );
        Ok(())
    }
}

/// Self-deleting temp directory (Python `create_random_temp_folder` +
/// `delete_temp_folder` in `finally`).
struct TempDir {
    path: PathBuf,
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

fn tempfile_dir() -> std::io::Result<TempDir> {
    let path = std::env::temp_dir().join(format!(
        "cyoa_icc_{}_{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    std::fs::create_dir_all(&path)?;
    Ok(TempDir { path })
}
