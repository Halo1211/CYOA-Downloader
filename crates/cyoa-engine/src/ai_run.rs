//! F-043 A3 wiring: the per-download AI runtime used by project discovery
//! (Phase 5), the no-project viewer diagnostics and the deep-scan AI round.

use std::path::Path;
use std::sync::Arc;

use cyoa_integrations::ai::{self, UsageBudget};
use cyoa_integrations::ai_analyze::{self, AiContext};
use cyoa_net::client::{ClientConfig, FetchClient};
use cyoa_net::policy::DestinationPolicy;
use cyoa_storage::settings::Settings;
use serde_json::Value;
use tokio_util::sync::CancellationToken;

/// AI settings for one run (CLI `--ai-*` / GUI / settings.json).
#[derive(Clone, Default)]
pub struct AiRunConfig {
    /// Resolved key (never logged).
    pub api_key: String,
    pub provider: String,
    pub mode: String,
    /// settings.json snapshot (model, limits, endpoints).
    pub settings: Settings,
    /// One runtime (client + call budget) per download, shared by clones.
    runtime: Arc<std::sync::OnceLock<Option<Arc<AiRuntime>>>>,
}

impl AiRunConfig {
    pub fn new(api_key: String, provider: String, mode: String, settings: Settings) -> Self {
        Self {
            api_key,
            provider,
            mode,
            settings,
            runtime: Arc::default(),
        }
    }

    /// The same settings with a fresh runtime (and call budget): one per
    /// download, so a batch row never inherits an earlier row's spent budget.
    pub fn for_new_download(&self) -> Self {
        Self {
            runtime: Arc::default(),
            ..self.clone()
        }
    }

    /// The download's AI runtime (created on first use).
    pub fn runtime(
        &self,
        proxy: &cyoa_net::proxy::ProxyConfig,
        dns: Option<Arc<cyoa_net::dns_resolve::CustomResolver>>,
        vpn: Option<Arc<cyoa_net::vpn::VpnGuard>>,
    ) -> Option<Arc<AiRuntime>> {
        self.runtime
            .get_or_init(|| AiRuntime::new(self, proxy, dns, vpn))
            .clone()
    }
}

impl std::fmt::Debug for AiRunConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AiRunConfig")
            .field("api_key", &ai::mask_secret(&self.api_key))
            .field("provider", &self.provider)
            .field("mode", &self.mode)
            .finish_non_exhaustive()
    }
}

/// [`AiRuntime::collect_js`] retention limits.
pub const JS_COLLECT_MAX_FILES: usize = 2000;
/// [`AiRuntime::collect_js`] total byte budget (64 MiB).
pub const JS_COLLECT_MAX_BYTES: u64 = 64 * 1024 * 1024;

/// One download's AI state: a client for the configured endpoint and the
/// shared call budget (`AIUsageBudget`).
pub struct AiRuntime {
    client: FetchClient,
    cfg: AiRunConfig,
    budget: tokio::sync::Mutex<UsageBudget>,
}

impl AiRuntime {
    /// None when AI is off or no key is available (no request is ever sent).
    pub fn new(
        cfg: &AiRunConfig,
        proxy: &cyoa_net::proxy::ProxyConfig,
        dns: Option<Arc<cyoa_net::dns_resolve::CustomResolver>>,
        vpn: Option<Arc<cyoa_net::vpn::VpnGuard>>,
    ) -> Option<Arc<Self>> {
        let provider = ai::normalize_provider(&cfg.provider);
        if ai::normalize_mode(&cfg.mode) == "off" || !ai::is_available(&cfg.api_key, provider) {
            return None;
        }
        // The endpoint is user-configured (Ollama/custom are usually local),
        // never taken from downloaded content: no internal-host screen.
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: None,
                allow_internal: true,
            },
            proxy: proxy.clone(),
            dns,
            vpn,
            // A cold local model can take far longer than 20 s to send its
            // first byte; each call keeps its own provider deadline (<= 120 s).
            read_timeout: std::time::Duration::from_secs(180),
            ..ClientConfig::default()
        })
        .ok()?;
        Some(Arc::new(Self {
            client,
            budget: tokio::sync::Mutex::new(UsageBudget::from_settings(&cfg.settings)),
            cfg: AiRunConfig {
                provider: provider.to_owned(),
                runtime: Arc::default(),
                ..cfg.clone()
            },
        }))
    }

    pub fn mode(&self) -> &str {
        &self.cfg.mode
    }

    async fn with_ctx<T>(
        &self,
        cancel: &CancellationToken,
        f: impl AsyncFnOnce(&mut AiContext<'_>) -> T,
    ) -> T {
        let mut budget = self.budget.lock().await;
        let mut ctx = AiContext {
            client: &self.client,
            settings: &self.cfg.settings,
            api_key: &self.cfg.api_key,
            provider: &self.cfg.provider,
            mode: &self.cfg.mode,
            budget: &mut budget,
            cancel,
        };
        f(&mut ctx).await
    }

    /// Discover Phase 5: an AI-suggested project URL whose body is JSON.
    pub async fn detect_project(
        &self,
        client: &FetchClient,
        url: &str,
        html: &str,
        cancel: &CancellationToken,
    ) -> Option<crate::discover_net::ProjectSource> {
        if !ai::mode_allows("project_detect", &self.cfg.mode) {
            return None;
        }
        tracing::info!("Phase 5: AI-assisted project detection…");
        let candidate = self
            .with_ctx(cancel, async |ctx| {
                ai_analyze::detect_project_json(ctx, url, html).await
            })
            .await?;
        let text = crate::discover_net::fetch_page_text(client, &candidate, cancel).await?;
        // An error object (`{"error": ...}`) is JSON too: require a project
        // shape so the website fallback still runs.
        let looks_ok = serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}'))
            .ok()
            .is_some_and(|v| v.is_object() && cyoa_parse::project::looks_like_project_object(&v));
        if !looks_ok {
            tracing::info!("  AI candidate is not a project payload: {candidate}");
            return None;
        }
        tracing::info!("  AI candidate confirmed: {candidate}");
        Some(crate::discover_net::ProjectSource {
            project_json: text,
            base_url: cyoa_parse::discover::strip_document_from_url(&candidate),
            project_url: candidate,
        })
    }

    /// Orchestrator diagnostics when no project payload was found (logs only).
    pub async fn viewer_diagnostics(&self, html: &str, url: &str, cancel: &CancellationToken) {
        let analysis = self
            .with_ctx(cancel, async |ctx| {
                ai_analyze::analyze_viewer_logic(ctx, html, &[], url).await
            })
            .await;
        if analysis.is_empty() {
            return;
        }
        let text = |k: &str| match analysis.get(k) {
            Some(Value::String(s)) => s.clone(),
            Some(v) => v.to_string(),
            None => "unknown".into(),
        };
        tracing::info!(
            "[AI analysis] viewer={}, data={}",
            text("viewer_type"),
            text("data_source")
        );
        if let Some(Value::Array(items)) = analysis.get("suggestions") {
            for s in items {
                let s = s.as_str().map_or_else(|| s.to_string(), str::to_owned);
                tracing::info!("  → {s}");
            }
        }
    }

    /// Deep-scan AI round input: `.js/.mjs/.cjs` files over 500 characters
    /// below `folder` (relative names; sorted, Python walks in OS order).
    ///
    /// Security bound (deviation from Python, which reads everything): at
    /// most [`JS_COLLECT_MAX_FILES`] files and [`JS_COLLECT_MAX_BYTES`] in
    /// total are retained, so a hostile site cannot exhaust memory here. The
    /// prompt only samples file heads, so ordinary sites are unaffected.
    pub fn collect_js(folder: &Path) -> Vec<(String, String)> {
        let mut out = Vec::new();
        let mut total: u64 = 0;
        let mut stack = vec![folder.to_path_buf()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                let Ok(kind) = entry.file_type() else {
                    continue;
                };
                if kind.is_dir() {
                    stack.push(path);
                    continue;
                }
                let name = entry.file_name().to_string_lossy().into_owned();
                if !(name.ends_with(".js") || name.ends_with(".mjs") || name.ends_with(".cjs")) {
                    continue;
                }
                if out.len() >= JS_COLLECT_MAX_FILES {
                    break;
                }
                // Regular files only (DirEntry::file_type does not follow
                // symlinks); reads are capped to the remaining budget.
                if !kind.is_file() {
                    continue;
                }
                let remaining = JS_COLLECT_MAX_BYTES.saturating_sub(total);
                let Ok(file) = std::fs::File::open(&path) else {
                    continue;
                };
                let mut bytes = Vec::new();
                if std::io::Read::read_to_end(
                    &mut std::io::Read::take(file, remaining + 1),
                    &mut bytes,
                )
                .is_err()
                {
                    continue;
                }
                if bytes.len() as u64 > remaining {
                    continue;
                }
                total = total.saturating_add(bytes.len() as u64);
                // Python `Path.read_text()`: universal newlines.
                let text = String::from_utf8_lossy(&bytes)
                    .replace("\r\n", "\n")
                    .replace('\r', "\n");
                if text.chars().count() > 500
                    && let Ok(rel) = path.strip_prefix(folder)
                {
                    out.push((rel.to_string_lossy().into_owned(), text));
                }
            }
        }
        out.sort();
        out
    }

    /// Aggressive Recovery: AI-suggested asset URLs for the JS files.
    pub async fn js_asset_candidates(
        &self,
        js_files: &[(String, String)],
        base_url: &str,
        cancel: &CancellationToken,
    ) -> Vec<String> {
        self.with_ctx(cancel, async |ctx| {
            ai_analyze::analyze_js_for_assets(ctx, js_files, base_url).await
        })
        .await
    }
}
