//! Pure website mode (`--pure-website` / `--pure-website-folder`):
//! orchestrator.py 374-424. Archives a site without project discovery.
//!
//! Flow: mirror (`download()`; Auto profiles the entry and runs the
//! pre-project deep scan unless project data is present), archive extensions
//! (Classic: Auto manifest only; Smart: route crawl + manifest), captured
//! project recovery (Auto), second-pass text localization, `backup_report.txt`
//! with the pure-website notes, integrity section, folder or `_site.zip`.
//!
//! Not ported (FEATURE_PARITY F-027): CYOA.CAFE static-page records,
//! Perchance generators, `--fonts`, and the Browser strategy's runtime
//! capture. When Auto selects Browser, Rust runs the Smart route crawl
//! instead (logged; the manifest's `policy.strategy` says "smart").

use std::path::{Path, PathBuf};
use std::sync::Arc;

use cyoa_net::client::FetchClient;
use serde_json::{Value, json};
use tokio_util::sync::CancellationToken;

use crate::icc::ArchivePolicy;
use crate::output;
use crate::pipeline::{self, ProcessOptions, Progress};
use crate::run::{RunError, RunReport};
use crate::website::{integrity, mirror::MirrorSink, mirror::RouteCrawl};

pub struct PureRun<'a> {
    pub url: &'a str,
    pub stem: String,
    pub output_dir: PathBuf,
    pub zip: bool,
    pub deep_scan: bool,
    pub workers: usize,
    pub policy: ArchivePolicy,
    pub client: FetchClient,
    /// For the captured-project recovery fetch context.
    pub run_opts: crate::run::RunOptions,
    pub cache: Option<Arc<cyoa_storage::cache::ImageCache>>,
    pub spool_dir: PathBuf,
    pub cancel: CancellationToken,
    pub progress: Option<tokio::sync::mpsc::Sender<Progress>>,
    /// orchestrator.py 484-527 custom-viewer fallback (no project payload in
    /// any mode): no captured-project recovery, no backup_report/integrity.
    pub custom_fallback: bool,
}

struct Mirrored {
    sink: MirrorSink,
    profile: Option<crate::website::profile::ArchiveProfile>,
    effective: String,
    crawl: Option<RouteCrawl>,
}

impl PureRun<'_> {
    pub async fn run(self) -> Result<RunReport, RunError> {
        let site = output::unique_folder(&self.output_dir.join(&self.stem));
        if self.custom_fallback {
            tracing::warn!(
                "No standard project payload; automatically falling back to custom viewer archive → {}/",
                site.display()
            );
        } else {
            tracing::info!(
                "Pure website download (no project search) → {}/",
                site.display()
            );
        }
        output::prepare_clean_output_folder(&site)?;

        let auto = self.policy.strategy == "auto";
        let (url, site_c, client, cancel) = (
            self.url.to_owned(),
            site.clone(),
            self.client.clone(),
            self.cancel.clone(),
        );
        let (deep, workers, policy) = (self.deep_scan, self.workers, self.policy.clone());
        let handle = tokio::runtime::Handle::current();
        let ai_rt = crate::run::ai_runtime(&self.run_opts);
        let mirrored = tokio::task::spawn_blocking(move || -> Result<Mirrored, String> {
            let base = crate::website::js::directory_base_url(&url);
            let mut sink = MirrorSink::new(site_c, &url, &base, client, handle, cancel, 512 << 20);
            sink.workers = workers;
            sink.archive_strategy = policy.strategy.clone();
            sink.ai = ai_rt;
            let (profile, effective) = if auto {
                let p = sink.download_site_auto(deep)?;
                let eff = p.effective_strategy.clone();
                (Some(p), eff)
            } else {
                sink.download_site(deep)?;
                (None, policy.strategy.clone())
            };
            let crawl = sink.run_archive_stages(&effective, &policy);
            Ok(Mirrored {
                sink,
                profile,
                effective,
                crawl,
            })
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))?
        .map_err(RunError::OutputStage)?;
        if self.cancel.is_cancelled() {
            return Err(RunError::Cancelled);
        }
        self.write_archive_manifest(&site, &mirrored)?;
        let Mirrored { mut sink, .. } = mirrored;

        // Auto: localize assets of a project.json the archive happened to save.
        if auto && !self.custom_fallback {
            self.recover_captured_project(&site).await?;
        }

        let manifest_site = site.clone();
        let fallback = self.custom_fallback;
        let check = tokio::task::spawn_blocking(move || {
            sink.localize_existing_text_assets();
            sink.close_browser();
            if fallback {
                return Ok(PathBuf::new());
            }
            let manifest = sink.write_manifest("");
            let result = integrity::validate_integrity(&manifest_site);
            if let Err(e) = integrity::append_to_backup_report(&manifest_site, &result) {
                tracing::debug!("Could not append Pure Website integrity report: {e}");
            }
            manifest
        })
        .await
        .map_err(|e| RunError::OutputStage(format!("website worker failed: {e}")))?;
        check.map_err(RunError::OutputStage)?;
        if self.run_opts.fonts {
            // orchestrator.py 417-420 / 518-521: viewer HTML fonts only.
            let html = crate::discover_net::fetch_page_text(&self.client, self.url, &self.cancel)
                .await
                .unwrap_or_default();
            crate::fonts::download_fonts_with_client(
                &self.client,
                &self.cancel,
                "",
                self.url,
                &site,
                &html,
            )
            .await
            .map_err(RunError::OutputStage)?;
        }
        let zip = {
            let (site_b, stem_b, out_b) =
                (site.clone(), self.stem.clone(), self.output_dir.clone());
            let zip_b = self.zip;
            tokio::task::spawn_blocking(move || {
                output::finalize_site_folder(&site_b, &stem_b, zip_b, &out_b)
            })
            .await
            .map_err(|e| RunError::OutputStage(format!("archive worker failed: {e}")))??
        };
        if self.custom_fallback {
            tracing::info!("Custom viewer archive fallback complete.");
        } else {
            tracing::info!("Pure website download complete.");
        }
        Ok(RunReport {
            stem: self.stem,
            written_paths: vec![zip.unwrap_or(site)],
            failed_assets: Vec::new(),
        })
    }

    /// `run_archive_extensions` for Pure Website.
    fn write_archive_manifest(&self, site: &Path, m: &Mirrored) -> Result<(), RunError> {
        let auto = self.policy.strategy == "auto";
        if !auto && m.crawl.is_none() {
            return Ok(()); // explicit Classic: no extension stages, no manifest
        }
        let requested = self.policy.to_json(&self.policy.strategy);
        let effective = self.policy.to_json(&m.effective);
        let profile = m.profile.as_ref().map_or(
            Value::Null,
            crate::website::profile::ArchiveProfile::to_json,
        );
        let mut manifest = serde_json::Map::new();
        manifest.insert("format".into(), json!(1));
        manifest.insert("requested_policy".into(), requested);
        manifest.insert("policy".into(), effective);
        manifest.insert("auto_profile".into(), profile);
        manifest.insert("start_url".into(), json!(self.url));
        match &m.crawl {
            None => {
                let pages = if site.join("index.html").is_file() {
                    json!([{"url": self.url, "local": "index.html"}])
                } else {
                    json!([])
                };
                manifest.insert("pages".into(), pages);
                manifest.insert("route_failures".into(), json!([]));
                manifest.insert("route_limit_reached".into(), json!(false));
                manifest.insert("remaining_queued_routes".into(), json!(0));
            }
            Some(crawl) => {
                let pages: Vec<Value> = crawl
                    .pages
                    .iter()
                    .map(|(u, l)| json!({"url": u, "local": l}))
                    .collect();
                let failures: Vec<Value> = crawl
                    .failed
                    .iter()
                    .map(|(u, e)| json!({"url": u, "error": e}))
                    .collect();
                let mut bases = serde_json::Map::new();
                for (k, v) in &crawl.link_bases {
                    bases.insert(k.clone(), v.clone().into());
                }
                manifest.insert("pages".into(), json!(pages));
                manifest.insert("route_failures".into(), json!(failures));
                manifest.insert("route_link_bases".into(), Value::Object(bases));
                manifest.insert("pending_routes".into(), json!(crawl.pending));
                manifest.insert("route_limit_reached".into(), json!(crawl.limit_reached));
                manifest.insert(
                    "remaining_queued_routes".into(),
                    json!(crawl.remaining_queued),
                );
            }
        }
        let runtime = m.crawl.as_ref().and_then(|c| c.runtime.clone());
        manifest.insert("runtime".into(), runtime.unwrap_or(Value::Null));
        let text = serde_json::to_string_pretty(&Value::Object(manifest))
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        let path = crate::website::mirror::contained_path(site, "archive_manifest.json")
            .ok_or_else(|| RunError::OutputStage("unsafe manifest path".into()))?;
        cyoa_storage::atomic_io::atomic_write_text(&path, &text)
            .map_err(|e| RunError::OutputStage(e.to_string()))?;
        if m.crawl.is_none() {
            tracing::info!(
                "Auto archive selected Classic; browser/route stages skipped: {}",
                m.profile
                    .as_ref()
                    .map_or("no extra stages needed", |p| p.reason.as_str())
            );
        } else {
            tracing::info!("Archive manifest saved: {}", path.display());
        }
        Ok(())
    }

    /// `_recover_captured_project_assets`.
    async fn recover_captured_project(&self, site: &Path) -> Result<(), RunError> {
        let project_path = site.join("project.json");
        if !project_path.is_file() {
            return Ok(());
        }
        let Ok(original) = std::fs::read_to_string(&project_path) else {
            tracing::warn!("Captured project.json could not be read");
            return Ok(());
        };
        let Some(normalized) = cyoa_parse::project::normalize_project_payload_text(&original)
        else {
            tracing::debug!("Captured project.json is not a recognized project payload");
            return Ok(());
        };
        tracing::info!(
            "Pure Website Auto captured project.json; recovering all referenced project assets."
        );
        let tmp = std::env::temp_dir().join(format!(
            "cyoa_pure_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&tmp)?;
        let result = async {
            let opts = ProcessOptions {
                embed: false,
                download: true,
                max_workers: self.workers.clamp(1, 64),
                download_dir: Some(tmp.clone()),
                report_dir: Some(site.to_path_buf()),
                site_folder: Some(site.to_path_buf()),
                source_url: self.url.to_owned(),
                ..Default::default()
            };
            let base = cyoa_parse::discover::strip_document_from_url(self.url);
            let ctx = crate::run::build_ctx(
                &self.run_opts,
                &self.client,
                &self.cancel,
                base.clone(),
                &normalized,
                self.cache.clone(),
                self.spool_dir.clone(),
            );
            let processed =
                pipeline::process_images(&normalized, &base, ctx, &opts, self.progress.clone())
                    .await?;
            for name in ["images", "audio"] {
                let src = tmp.join(name);
                if src.is_dir() {
                    let label = format!("captured project {name}");
                    let report =
                        cyoa_core::paths::copytree_merge_safe(&src, &site.join(name), &label);
                    tracing::info!(
                        "Recovered {} captured-project file(s) in {name}/",
                        report.copied
                    );
                }
            }
            if processed.dl_str != normalized {
                let original_path = site.join("project_original.json");
                if !original_path.exists() {
                    cyoa_storage::atomic_io::atomic_write_text(&original_path, &original)
                        .map_err(|e| RunError::OutputStage(e.to_string()))?;
                }
                cyoa_storage::atomic_io::atomic_write_text(&project_path, &processed.dl_str)
                    .map_err(|e| RunError::OutputStage(e.to_string()))?;
            }
            Ok::<(), RunError>(())
        }
        .await;
        let _ = std::fs::remove_dir_all(&tmp);
        result
    }
}
