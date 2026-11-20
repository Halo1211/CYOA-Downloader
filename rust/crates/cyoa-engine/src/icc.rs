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
    /// Python `ArchivePolicy` (settings `archive_*`, already normalized).
    /// auto (default): project data -> Classic, manifest written, recovery
    /// pass and post-project deep scan skipped; smart: route crawl.
    pub policy: ArchivePolicy,
    pub workers: usize,
    pub client: FetchClient,
    pub ctx: Arc<FetchCtx>,
    pub cancel: CancellationToken,
    pub progress: Option<tokio::sync::mpsc::Sender<Progress>>,
    /// `--fonts`: project.json fonts into `<site>/fonts/` (viewer fonts are
    /// handled by the mirror), rewritten project fed to image processing.
    pub fonts: bool,
    /// yt-dlp audio settings (`--no-ytdlp`, `--ytdlp-cookies`).
    pub audio: crate::audio::AudioConfig,
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
        let auto = self.policy.strategy == "auto";
        let pre_deep_scan = !auto && self.deep_scan;
        let policy = self.policy.clone();
        let workers = self.workers;
        let sink = tokio::task::spawn_blocking(move || {
            let mut sink =
                MirrorSink::new(site_c, &entry, &base, client, handle, cancel, 512 << 20);
            sink.workers = workers;
            sink.archive_strategy = policy.strategy.clone();
            let result = sink.download_site(pre_deep_scan).map(|()| {
                // run_archive_extensions: Smart crawls same-story routes,
                // Browser also captures runtime assets.
                sink.run_archive_stages(&policy.strategy, &policy)
            });
            (sink, result)
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))?;
        let (mut sink, crawl) = (sink.0, sink.1.map_err(RunError::OutputStage)?);
        if self.cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }
        if auto {
            self.write_archive_manifest(&site, None)?;
        } else if let Some(crawl) = &crawl {
            self.write_archive_manifest(&site, Some(crawl))?;
        }

        let working = if self.fonts {
            crate::fonts::download_fonts_with_client(
                &self.client,
                &self.cancel,
                &self.source.project_json,
                &self.source.base_url,
                &site,
                "",
            )
            .await
            .map_err(RunError::OutputStage)?
        } else {
            self.source.project_json.clone()
        };

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
            youtube: Some(self.audio.clone()),
            ..Default::default()
        };
        let processed = pipeline::process_images(
            &working,
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
        let mut sink = tokio::task::spawn_blocking(move || {
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
        if auto {
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
        // orchestrator.py 742-765: opt-in full-site modernization (F-037 V4).
        let website_enabled = {
            let mut store =
                cyoa_storage::settings::SettingsStore::new(cyoa_storage::settings::default_path());
            store
                .load()
                .ok()
                .and_then(|m| m.get("offline_viewer_website_enabled").cloned())
        }
        .is_some_and(|v| v.as_bool().unwrap_or(false));
        if website_enabled {
            let fetcher =
                crate::offline_viewer::NetworkAssetFetcher(crate::discover_net::ClientCafeFetch {
                    client: self.client.clone(),
                    handle: tokio::runtime::Handle::current(),
                    cancel: self.cancel.clone(),
                });
            let (site_c, base) = (site.clone(), self.source.base_url.clone());
            let outcome = tokio::task::spawn_blocking(move || {
                let templates = crate::modernizer::resolve_registered_viewer_templates(
                    &cyoa_integrations::offline_viewers::viewers_dir(),
                );
                crate::modernizer::modernize_site(
                    &site_c, &site_c, None, &templates, &base, &fetcher,
                )
            })
            .await
            .map_err(|e| e.to_string())
            .and_then(|r| r);
            match outcome {
                Ok(m) => tracing::info!(
                    "Offline website modernization: family={} strategy={} status={}",
                    m.family.value(),
                    m.strategy,
                    m.status
                ),
                Err(e) => tracing::warn!("Offline website modernization skipped: {e}"),
            }
        } else {
            tracing::info!("Offline website modernization is disabled in Settings.");
        }
        if self.cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }

        // 5. Manifest, integrity, finalize.
        let project_url = self.source.project_url.clone();
        let site_c = site.clone();
        let check = tokio::task::spawn_blocking(move || {
            sink.close_browser();
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
    fn write_archive_manifest(
        &self,
        site: &std::path::Path,
        crawl: Option<&crate::website::mirror::RouteCrawl>,
    ) -> Result<(), RunError> {
        if let Some(crawl) = crawl {
            return self.write_smart_manifest(site, crawl);
        }
        let policy = |strategy: &str| self.policy.to_json(strategy);
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

impl IccRun<'_> {
    /// `run_archive_extensions` for Smart: manifest with the crawl results.
    fn write_smart_manifest(
        &self,
        site: &std::path::Path,
        crawl: &crate::website::mirror::RouteCrawl,
    ) -> Result<(), RunError> {
        let policy = self.policy.to_json(&self.policy.strategy);
        let mut bases = serde_json::Map::new();
        for (k, v) in &crawl.link_bases {
            bases.insert(k.clone(), v.clone().into());
        }
        let pages: Vec<serde_json::Value> = crawl
            .pages
            .iter()
            .map(|(u, l)| serde_json::json!({"url": u, "local": l}))
            .collect();
        let failures: Vec<serde_json::Value> = crawl
            .failed
            .iter()
            .map(|(u, e)| serde_json::json!({"url": u, "error": e}))
            .collect();
        let manifest = serde_json::json!({
            "format": 1,
            "requested_policy": policy,
            "policy": policy,
            "auto_profile": null,
            "start_url": self.entry_url,
            "pages": pages,
            "route_failures": failures,
            "route_link_bases": bases,
            "pending_routes": crawl.pending,
            "route_limit_reached": crawl.limit_reached,
            "remaining_queued_routes": crawl.remaining_queued,
            "runtime": crawl.runtime.clone().unwrap_or(serde_json::Value::Null)
        });
        let text = serde_json::to_string_pretty(&manifest)
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        let path = crate::website::mirror::contained_path(site, "archive_manifest.json")
            .ok_or_else(|| RunError::OutputStage("unsafe manifest path".into()))?;
        cyoa_storage::atomic_io::atomic_write_text(&path, &text)
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        tracing::info!("Archive manifest saved: {}", path.display());
        Ok(())
    }
}

/// Python `ArchivePolicy.normalized()` (download/archive_policy.py).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArchivePolicy {
    pub strategy: String,
    pub max_pages: i64,
    pub max_depth: i64,
    pub capture_interactions: bool,
    pub interaction_policy: String,
    pub settle_time_ms: i64,
    pub runtime_max_pages: i64,
    pub max_scroll_steps: i64,
    pub max_interactions: i64,
    pub no_progress_rounds: i64,
}

impl Default for ArchivePolicy {
    fn default() -> Self {
        Self::from_settings(&serde_json::Map::new())
    }
}

/// Python `int(value)` for a settings value (None on TypeError/ValueError).
fn py_int(v: Option<&serde_json::Value>) -> Option<i64> {
    match v? {
        serde_json::Value::Bool(b) => Some(i64::from(*b)),
        serde_json::Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        serde_json::Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python truthiness of a JSON value.
fn py_truthy(v: &serde_json::Value) -> bool {
    match v {
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        serde_json::Value::String(x) => !x.is_empty(),
        serde_json::Value::Null => false,
        serde_json::Value::Array(a) => !a.is_empty(),
        serde_json::Value::Object(o) => !o.is_empty(),
    }
}

impl ArchivePolicy {
    /// Orchestrator construction from settings, then `normalized()`.
    pub fn from_settings(s: &serde_json::Map<String, serde_json::Value>) -> Self {
        let text = |k: &str, d: &str| {
            s.get(k)
                .and_then(|v| v.as_str())
                .map(str::to_owned)
                .unwrap_or_else(|| d.to_owned())
        };
        let bounded =
            |k: &str, d: i64, lo: i64, hi: i64| py_int(s.get(k)).unwrap_or(d).clamp(lo, hi);
        let mut strategy = text("archive_strategy", "auto").trim().to_lowercase();
        if !["classic", "smart", "browser", "auto"].contains(&strategy.as_str()) {
            strategy = "classic".into();
        }
        let capture = s.get("archive_capture_interactions").is_some_and(py_truthy);
        let mut interaction = text("archive_interaction_policy", "safe")
            .trim()
            .to_lowercase();
        if !["off", "safe"].contains(&interaction.as_str()) || capture {
            interaction = "safe".into();
        }
        Self {
            strategy,
            max_pages: bounded("archive_max_pages", 300, 1, 5000),
            max_depth: bounded("archive_max_depth", 30, 0, 100),
            capture_interactions: capture,
            interaction_policy: interaction,
            settle_time_ms: bounded("archive_settle_time_ms", 1800, 250, 15000),
            runtime_max_pages: bounded("archive_runtime_max_pages", 12, 1, 100),
            max_scroll_steps: bounded("archive_max_scroll_steps", 100, 1, 1000),
            max_interactions: bounded("archive_max_interactions", 20, 0, 100),
            no_progress_rounds: bounded("archive_no_progress_rounds", 2, 1, 10),
        }
    }

    /// `asdict(policy)` with the given strategy, in dataclass field order.
    pub fn to_json(&self, strategy: &str) -> serde_json::Value {
        serde_json::json!({
            "strategy": strategy,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "capture_interactions": self.capture_interactions,
            "interaction_policy": self.interaction_policy,
            "settle_time_ms": self.settle_time_ms,
            "runtime_max_pages": self.runtime_max_pages,
            "max_scroll_steps": self.max_scroll_steps,
            "max_interactions": self.max_interactions,
            "no_progress_rounds": self.no_progress_rounds
        })
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
