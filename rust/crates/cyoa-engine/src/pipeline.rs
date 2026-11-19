//! Bounded Rust orchestration port of Python `_process_images`.
use crate::{
    embed::{self, Replacement},
    fetch::{FetchCtx, FetchOutcome, fetch_one},
    localize::asset_rel_path,
};
use cyoa_parse::{
    assets::{AUDIO_FIELDS, IMAGE_FIELDS},
    deep_scan::collect_project_assets,
};
use sha1::Digest;
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};
use thiserror::Error;
use tokio::sync::{Mutex, mpsc::Sender};

const SITE_EXISTING: &[u8] = b"__site_existing__";

#[derive(Clone, Debug)]
pub struct ProcessOptions {
    pub embed: bool,
    pub download: bool,
    pub embed_audio: bool,
    pub max_workers: usize,
    /// Root for `images/` and `audio/` outputs.
    pub download_dir: Option<PathBuf>,
    pub report_dir: Option<PathBuf>,
    /// Existing website tree whose authored local assets can be reused.
    pub site_folder: Option<PathBuf>,
    pub source_url: String,
    /// Write the embedded JSON straight to this file instead of `embed_str`,
    /// so base64 media never has to fit in memory (Python `embedded_file`).
    pub embed_out: Option<PathBuf>,
}

impl Default for ProcessOptions {
    fn default() -> Self {
        Self {
            embed: false,
            download: false,
            embed_audio: false,
            max_workers: 8,
            download_dir: None,
            report_dir: None,
            site_folder: None,
            source_url: String::new(),
            embed_out: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Progress {
    Started {
        total: usize,
    },
    AssetDone {
        done: usize,
        total: usize,
        ok: bool,
        name: String,
    },
    Finished {
        failed: usize,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FailedAsset {
    pub url: String,
    pub path: String,
    pub error: String,
    pub kind: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProcessResult {
    /// Embedded JSON when `embed_out` is unset (empty otherwise).
    pub embed_str: String,
    /// The file holding the embedded JSON when `embed_out` is set.
    pub embed_file: Option<PathBuf>,
    /// Embedded output differs from the input (Python `has_edits_embed`).
    pub embed_changed: bool,
    pub dl_str: String,
    pub resolved_urls: BTreeSet<String>,
    pub failed: Vec<FailedAsset>,
}

#[derive(Debug, Error)]
pub enum PipelineError {
    #[error("processing cancelled")]
    Cancelled,
    #[error("download=true requires download_dir")]
    DownloadDirRequired,
    #[error(transparent)]
    Embed(#[from] embed::EmbedError),
    #[error("asset I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("atomic write error: {0}")]
    Atomic(#[from] cyoa_storage::atomic_io::AtomicWriteError),
    #[error("asset worker task failed: {0}")]
    Worker(#[from] tokio::task::JoinError),
}

#[derive(Clone)]
struct Asset {
    path: String,
    image: bool,
}

fn fetch_identity(path: &str, base: &str) -> String {
    let resolved = crate::fetch_plan::resolve_asset_url(path, base);
    let Ok(mut url) = url::Url::parse(&resolved) else {
        return resolved.split('#').next().unwrap_or(&resolved).to_owned();
    };
    url.set_fragment(None);
    let bust = [
        "v",
        "ver",
        "version",
        "cb",
        "cache",
        "cachebust",
        "cache_bust",
        "cachebuster",
        "t",
        "ts",
        "timestamp",
        "_",
        "dpl",
    ];
    let pairs: Vec<(String, String)> = url
        .query_pairs()
        .filter(|(k, _)| !bust.iter().any(|b| k.eq_ignore_ascii_case(b)))
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    url.set_query(None);
    if !pairs.is_empty() {
        url.query_pairs_mut().extend_pairs(pairs);
    }
    url.to_string()
}

fn encode_base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let a = chunk[0];
        let b = chunk.get(1).copied().unwrap_or(0);
        let c = chunk.get(2).copied().unwrap_or(0);
        out.push(char::from(TABLE[usize::from(a >> 2)]));
        out.push(char::from(TABLE[usize::from(((a & 3) << 4) | (b >> 4))]));
        out.push(if chunk.len() > 1 {
            char::from(TABLE[usize::from(((b & 15) << 2) | (c >> 6))])
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            char::from(TABLE[usize::from(c & 63)])
        } else {
            '='
        });
    }
    out
}
fn send_progress(tx: &Option<Sender<Progress>>, event: Progress) {
    if let Some(tx) = tx {
        let _ = tx.try_send(event);
    }
}

fn image_fields() -> Vec<String> {
    IMAGE_FIELDS.iter().map(|s| (*s).to_owned()).collect()
}
fn field_group() -> String {
    IMAGE_FIELDS
        .iter()
        .chain(AUDIO_FIELDS)
        .map(|f| regex::escape(f))
        .collect::<Vec<_>>()
        .join("|")
}

fn local_timestamp() -> String {
    crate::reports::local_timestamp()
}

fn write_failed_log(failed: &[FailedAsset], dir: &Path, source: &str) -> Result<(), PipelineError> {
    if failed.is_empty() {
        return Ok(());
    }
    fs::create_dir_all(dir)?;
    let path = dir.join("failed_images.txt");
    // Existing bytes are kept as-is; only the appended section gets Python's
    // text-mode newline translation (CRLF on Windows).
    let existing = fs::read_to_string(&path).unwrap_or_default();
    let mut text = String::new();
    if !path.exists() {
        text.push_str("# Failed image downloads\n# Note: Failed images keep their original external URL in the project JSON.\n#       They will load normally when the original site is online.\n\n");
    }
    text.push_str(&format!("# --- {} ---\n", local_timestamp()));
    if !source.is_empty() {
        text.push_str(&format!("# Source CYOA : {source}\n"));
    }
    text.push_str(&format!("# Count       : {}\n", failed.len()));
    for item in failed {
        text.push_str(&format!("{}\t{}\n", item.url, item.error));
    }
    text.push('\n');
    let text = existing + &crate::reports::os_text(&text);
    cyoa_storage::atomic_io::atomic_write_text(path, &text)?;
    Ok(())
}

fn site_path(site: &Path, asset: &str, base: &str) -> Option<(PathBuf, String)> {
    let resolved = crate::fetch_plan::resolve_asset_url(asset, base);
    let url = crate::localize::py_urlparse(&resolved);
    let base_url = crate::localize::py_urlparse(base);
    let rel = if !url.netloc.is_empty()
        && url.scheme == base_url.scheme
        && url.netloc.eq_ignore_ascii_case(base_url.netloc)
    {
        let bp = base_url.path.trim_end_matches('/');
        if bp.is_empty() {
            url.path.trim_start_matches('/')
        } else if !url.path.starts_with(&format!("{bp}/")) {
            return None;
        } else {
            url.path[bp.len()..].trim_start_matches('/')
        }
    } else if asset.starts_with("http://") || asset.starts_with("https://") {
        return None;
    } else {
        asset.trim_start_matches(['.', '/'])
    };
    let safe = cyoa_core::paths::safe_rel_path(rel, "");
    if safe.is_empty() {
        return None;
    }
    Some((site.join(&safe), safe))
}

fn has_reparse_or_symlink(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        false
    }
}

fn safe_target(root: &Path, candidate: &Path) -> bool {
    let Ok(root_abs) = root.canonicalize() else {
        return false;
    };
    let Ok(relative) = candidate.strip_prefix(root) else {
        return false;
    };
    let mut cursor = root_abs;
    let components: Vec<_> = relative.components().collect();
    if components
        .iter()
        .any(|part| !matches!(part, std::path::Component::Normal(_)))
    {
        return false;
    }
    for component in components.iter().take(components.len().saturating_sub(1)) {
        let std::path::Component::Normal(name) = component else {
            return false;
        };
        cursor.push(name);
        if let Ok(metadata) = fs::symlink_metadata(&cursor)
            && has_reparse_or_symlink(&metadata)
        {
            return false;
        }
    }
    true
}

fn create_safe_parent(root: &Path, relative_parent: &Path) -> std::io::Result<bool> {
    let root_abs = root.canonicalize()?;
    let mut cursor = root_abs.clone();
    for component in relative_parent.components() {
        let std::path::Component::Normal(name) = component else {
            return Ok(false);
        };
        cursor.push(name);
        match fs::symlink_metadata(&cursor) {
            Ok(metadata) if has_reparse_or_symlink(&metadata) || !metadata.is_dir() => {
                return Ok(false);
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                match fs::create_dir(&cursor) {
                    Ok(()) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                    Err(error) => return Err(error),
                }
                let metadata = fs::symlink_metadata(&cursor)?;
                if has_reparse_or_symlink(&metadata) || !metadata.is_dir() {
                    return Ok(false);
                }
            }
            Err(error) => return Err(error),
        }
        if !cursor.starts_with(&root_abs) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Streamed comparison of a file with in-memory bytes.
fn file_equals(path: &Path, expected: &[u8]) -> std::io::Result<bool> {
    use std::io::Read;
    if fs::metadata(path)?.len() != expected.len() as u64 {
        return Ok(false);
    }
    let mut file = fs::File::open(path)?;
    let mut buf = vec![0u8; 1 << 16];
    let mut offset = 0;
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            return Ok(offset == expected.len());
        }
        if expected.get(offset..offset + n) != Some(&buf[..n]) {
            return Ok(false);
        }
        offset += n;
    }
}

/// `io::Write` adapter that feeds a digest (streamed hashing of spooled files).
struct HashWriter<D>(D);

impl<D: sha1::Digest> std::io::Write for HashWriter<D> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.update(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn write_no_clobber(
    root: &Path,
    rel: &str,
    payload: &crate::fetch::Payload,
    cancel: &tokio_util::sync::CancellationToken,
) -> std::io::Result<Option<String>> {
    fs::create_dir_all(root)?;
    let rel = cyoa_core::paths::safe_rel_path(rel, "");
    if rel.is_empty() {
        return Ok(None);
    }
    let (folder, name) = rel.split_once('/').map_or_else(
        || ("images".to_owned(), rel.clone()),
        |(f, n)| (f.to_owned(), n.to_owned()),
    );
    let ext = Path::new(&name)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{e}"))
        .unwrap_or_default();
    let stem = name.strip_suffix(&ext).unwrap_or(&name).to_owned();
    for index in 0..100_000usize {
        let candidate = if index == 0 {
            rel.clone()
        } else {
            format!("{folder}/{stem}_{index}{ext}")
        };
        let target = root.join(&candidate);
        let parent = target.parent().unwrap_or(root);
        let rel_parent = Path::new(&candidate).parent().unwrap_or(Path::new(""));
        if !create_safe_parent(root, rel_parent)? || !safe_target(root, &target) {
            return Ok(None);
        }
        let temp = parent.join(format!(".cyoa-{}-{index}.tmp", std::process::id()));
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        payload.copy_to(&mut file)?;
        file.sync_all()?;
        drop(file);
        if cancel.is_cancelled() {
            let _ = fs::remove_file(&temp);
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "cancelled",
            ));
        }
        match fs::hard_link(&temp, &target) {
            Ok(()) => {
                fs::remove_file(temp)?;
                return Ok(Some(candidate));
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                let _ = fs::remove_file(temp);
            }
            Err(error) => {
                let _ = fs::remove_file(temp);
                return Err(error);
            }
        }
    }
    Err(std::io::Error::other(
        "asset collision suffix limit exceeded",
    ))
}

/// Fetch, embed, and/or localize the downloadable assets referenced by project JSON.
pub async fn process_images(
    input: &str,
    base_url: &str,
    ctx: Arc<FetchCtx>,
    opts: &ProcessOptions,
    progress: Option<Sender<Progress>>,
) -> Result<ProcessResult, PipelineError> {
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }
    if opts.download && opts.download_dir.is_none() {
        return Err(PipelineError::DownloadDirRequired);
    }
    let assets = collect_project_assets(input);
    let mut all: Vec<Asset> = assets
        .images
        .iter()
        .map(|p| Asset {
            path: p.clone(),
            image: true,
        })
        .chain(assets.audio.iter().map(|p| Asset {
            path: p.clone(),
            image: false,
        }))
        .collect();
    all.sort_by(|a, b| a.path.cmp(&b.path));
    let total = all.len() + assets.youtube.len();
    send_progress(&progress, Progress::Started { total });
    if total == 0 {
        return Ok(ProcessResult {
            embed_file: None,
            embed_changed: false,
            embed_str: input.to_owned(),

            dl_str: input.to_owned(),
            resolved_urls: BTreeSet::new(),
            failed: Vec::new(),
        });
    }

    let mut completed: HashMap<String, FetchOutcome> = HashMap::new();
    let mut skipped: BTreeMap<String, String> = BTreeMap::new();
    let mut groups: BTreeMap<String, Vec<Asset>> = BTreeMap::new();
    for asset in all.iter().cloned() {
        if ctx.cancel.is_cancelled() {
            return Err(PipelineError::Cancelled);
        }
        if let Some(site) = &opts.site_folder
            && let Some((disk, rel)) = site_path(site, &asset.path, base_url)
            && disk.exists()
        {
            skipped.insert(asset.path.clone(), rel);
            continue;
        }
        groups
            .entry(fetch_identity(&asset.path, base_url))
            .or_default()
            .push(asset);
    }
    // YouTube, SoundCloud and bare YouTube IDs are retained unchanged; invoking
    // yt-dlp is intentionally deferred for this native pipeline.
    let jobs = Arc::new(Mutex::new(VecDeque::from_iter(groups)));
    let worker_count = opts.max_workers.max(1).min(jobs.lock().await.len().max(1));
    let (result_tx, mut result_rx) =
        tokio::sync::mpsc::channel::<(String, Vec<Asset>, FetchOutcome)>(worker_count.max(1));
    let mut workers = tokio::task::JoinSet::new();
    for _ in 0..worker_count {
        let queue = jobs.clone();
        let worker_ctx = ctx.clone();
        let tx = result_tx.clone();
        workers.spawn(async move {
            loop {
                let job = queue.lock().await.pop_front();
                let Some((identity, members)) = job else {
                    break;
                };
                let path = members
                    .first()
                    .map_or_else(String::new, |asset| asset.path.clone());
                let outcome = fetch_one(&worker_ctx, &path).await;
                if tx.send((identity, members, outcome)).await.is_err() {
                    break;
                }
            }
        });
    }
    drop(result_tx);
    let mut done = 0usize;
    while let Some((_identity, members, outcome)) = result_rx.recv().await {
        if ctx.cancel.is_cancelled() {
            workers.abort_all();
            return Err(PipelineError::Cancelled);
        }
        let is_ok = outcome.data.is_some() && outcome.error.is_empty();
        for member in members {
            done += 1;
            if let Some(tx) = &progress {
                let _ = tx
                    .send(Progress::AssetDone {
                        done,
                        total,
                        ok: is_ok,
                        name: member.path.clone(),
                    })
                    .await;
                tokio::task::yield_now().await;
            }
            completed.insert(member.path, outcome.clone());
        }
    }
    while let Some(joined) = workers.join_next().await {
        joined?;
    }
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }

    for (path, rel) in &skipped {
        done += 1;
        send_progress(
            &progress,
            Progress::AssetDone {
                done,
                total,
                ok: true,
                name: path.clone(),
            },
        );
        let resolved = crate::fetch_plan::resolve_asset_url(path, base_url);
        completed.insert(
            path.clone(),
            FetchOutcome {
                asset_path: path.clone(),
                data: Some(crate::fetch::Payload::Mem(
                    tokio_util::bytes::Bytes::from_static(SITE_EXISTING),
                )),
                mime: if assets.images.contains(path) {
                    "image"
                } else {
                    "audio/mpeg"
                }
                .to_owned(),
                asset_url: resolved,
                error: String::new(),
            },
        );
        // Keep the exact authored relative path.
        let _ = rel;
    }

    let mut failed = Vec::new();
    for asset in &all {
        if ctx.cancel.is_cancelled() {
            return Err(PipelineError::Cancelled);
        }
        let Some(out) = completed.get(&asset.path) else {
            continue;
        };
        if out.data.is_none() {
            failed.push(FailedAsset {
                url: out.asset_url.clone(),
                path: asset.path.clone(),
                error: out.error.clone(),
                kind: if asset.image { "image" } else { "audio" }.to_owned(),
            });
        }
    }
    let mut embeds: Vec<(String, Replacement)> = Vec::new();
    let mut downloads: Vec<(String, Replacement)> = Vec::new();
    let mut resolved_urls = BTreeSet::new();
    let mut saved_by_digest: HashMap<String, String> = HashMap::new();
    for asset in &all {
        if ctx.cancel.is_cancelled() {
            return Err(PipelineError::Cancelled);
        }
        let Some(out) = completed.get(&asset.path) else {
            continue;
        };
        let Some(data) = &out.data else { continue };
        if !out.asset_url.is_empty() {
            resolved_urls.insert(out.asset_url.clone());
            if !asset.path.starts_with("http://") && !asset.path.starts_with("https://") {
                resolved_urls.insert(crate::fetch_plan::resolve_asset_url(&asset.path, base_url));
            }
        }
        if data.is_mem(SITE_EXISTING) {
            if let Some(rel) = skipped.get(&asset.path) {
                downloads.push((asset.path.clone(), Replacement::Text(rel.clone())));
            }
            continue;
        }
        if opts.embed && (asset.image || (opts.embed_audio && data.len() <= 10 * 1024 * 1024)) {
            // Spooled payloads stream into the output (Python `EmbeddedMedia`).
            let replacement = match data {
                crate::fetch::Payload::Mem(bytes) => {
                    Replacement::Text(format!("data:{};base64,{}", out.mime, encode_base64(bytes)))
                }
                crate::fetch::Payload::File { path, .. } => {
                    Replacement::Media(embed::EmbeddedMedia {
                        path: path.clone(),
                        mime: out.mime.clone(),
                    })
                }
            };
            embeds.push((asset.path.clone(), replacement));
        }
        if opts.download {
            let root = opts
                .download_dir
                .as_ref()
                .ok_or(PipelineError::DownloadDirRequired)?;
            let rel = asset_rel_path(
                &out.asset_url,
                base_url,
                &out.mime,
                data.prefix(),
                asset.image,
            );
            let hash_src = data.clone();
            let digest = tokio::task::spawn_blocking(move || -> std::io::Result<String> {
                let mut hasher = HashWriter(sha1::Sha1::new());
                hash_src.copy_to(&mut hasher)?;
                Ok(hasher
                    .0
                    .finalize()
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>())
            })
            .await??;
            if opts.site_folder.is_none()
                && asset.image
                && let Some(existing) = saved_by_digest.get(&digest)
            {
                downloads.push((asset.path.clone(), Replacement::Text(existing.clone())));
                continue;
            }
            if ctx.cancel.is_cancelled() {
                return Err(PipelineError::Cancelled);
            }
            let root = root.clone();
            let rel_for_write = rel.clone();
            let data = data.clone();
            let cancel = ctx.cancel.clone();
            let written = tokio::task::spawn_blocking(move || {
                write_no_clobber(&root, &rel_for_write, &data, &cancel)
            })
            .await?;
            if ctx.cancel.is_cancelled() {
                return Err(PipelineError::Cancelled);
            }
            let Some(candidate) = written? else {
                failed.push(FailedAsset {
                    url: out.asset_url.clone(),
                    path: asset.path.clone(),
                    error: "unsafe download path: symlink or path escapes download directory"
                        .to_owned(),
                    kind: if asset.image { "image" } else { "audio" }.to_owned(),
                });
                continue;
            };
            if opts.site_folder.is_none() && asset.image {
                saved_by_digest.insert(digest, candidate.clone());
            }
            downloads.push((asset.path.clone(), Replacement::Text(candidate)));
        }
    }
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }
    if let Some(dir) = &opts.report_dir {
        let image_fails: Vec<_> = failed
            .iter()
            .filter(|f| f.kind == "image")
            .cloned()
            .collect();
        let dir = dir.clone();
        let source = opts.source_url.clone();
        let all_fails = failed.clone();
        tokio::task::spawn_blocking(move || -> Result<(), PipelineError> {
            write_failed_log(&image_fails, &dir, &source)?;
            let items: Vec<_> = all_fails
                .iter()
                .map(|f| crate::reports::FailedItem {
                    url: &f.url,
                    path: &f.path,
                    error: &f.error,
                    // Python process_images failure dicts carry no "kind".
                    kind: "",
                })
                .collect();
            crate::reports::write_asset_failure_summary(
                &items,
                &dir,
                &source,
                "Broken Project Asset Report",
            )?;
            Ok(())
        })
        .await??;
    }

    // Map alias references to the same completed replacement.
    for (alias, target) in &assets.image_field_aliases {
        if let Some((_, value)) = embeds.iter().find(|(key, _)| key == target).cloned() {
            embeds.push((alias.clone(), value));
        }
        if let Some((_, value)) = downloads.iter().find(|(key, _)| key == target).cloned() {
            downloads.push((alias.clone(), value));
        }
    }
    let group = field_group();
    let image_names = image_fields();
    let aliases = assets.image_field_aliases;
    let mut embed_buf = Vec::new();
    let mut embed_file = None;
    let mut embed_changed = false;
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }
    if opts.embed {
        if let Some(path) = opts.embed_out.clone() {
            // Blocking: streams spooled media through base64 into the file.
            let (input_s, group_s, names_s, aliases_s) = (
                input.to_owned(),
                group.clone(),
                image_names.clone(),
                aliases.clone(),
            );
            let embeds_s = std::mem::take(&mut embeds);
            embed_changed = tokio::task::spawn_blocking(move || -> Result<bool, PipelineError> {
                let file = fs::File::create(&path)?;
                let mut w = std::io::BufWriter::with_capacity(1 << 20, file);
                embed::project_chunks(&input_s, &embeds_s, &group_s, &names_s, &aliases_s, &mut w)?;
                let file = w.into_inner().map_err(|e| e.into_error())?;
                file.sync_all()?;
                drop(file);
                Ok(!file_equals(&path, input_s.as_bytes())?)
            })
            .await??;
            embed_file = opts.embed_out.clone();
        } else {
            embed::project_chunks(
                input,
                &embeds,
                &group,
                &image_names,
                &aliases,
                &mut embed_buf,
            )?;
            embed_changed = embed_buf != input.as_bytes();
        }
    }
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }
    let mut dl_buf = Vec::new();
    if opts.download {
        embed::project_chunks(
            input,
            &downloads,
            &group,
            &image_names,
            &aliases,
            &mut dl_buf,
        )?;
    }
    if ctx.cancel.is_cancelled() {
        return Err(PipelineError::Cancelled);
    }
    send_progress(
        &progress,
        Progress::Finished {
            failed: failed.len(),
        },
    );
    Ok(ProcessResult {
        embed_file,
        embed_changed,
        embed_str: if opts.embed_out.is_some() {
            String::new()
        } else if opts.embed {
            String::from_utf8_lossy(&embed_buf).into_owned()
        } else {
            input.to_owned()
        },
        dl_str: if opts.download {
            String::from_utf8_lossy(&dl_buf).into_owned()
        } else {
            input.to_owned()
        },
        resolved_urls,
        failed,
    })
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;
    use crate::fetch::FetchCtx;
    use cyoa_net::{
        client::{ClientConfig, FetchClient},
        policy::DestinationPolicy,
        throttle::DomainThrottle,
    };
    use std::{
        net::{IpAddr, Ipv4Addr},
        sync::atomic::{AtomicUsize, Ordering},
        time::Duration,
    };
    use tokio_util::sync::CancellationToken;
    use url::Url;
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    fn context(server: &MockServer, cancel: CancellationToken) -> Arc<FetchCtx> {
        let origin = Url::parse(&server.uri()).unwrap();
        Arc::new(FetchCtx {
            client: FetchClient::new(ClientConfig {
                policy: DestinationPolicy {
                    origin: Some(origin),
                    allow_internal: false,
                },
                ..Default::default()
            })
            .unwrap(),
            cache: None,
            domain: Arc::new(DomainThrottle::new(0.0)),
            base_url: server.uri(),
            image_paths: Default::default(),
            wait_time: 0.0,
            max_bytes: 2 * 1024 * 1024,
            cancel,
            resolver: Arc::new(|_| vec![IpAddr::V4(Ipv4Addr::LOCALHOST)]),
            spool_dir: None,
            bandwidth: None,
        })
    }
    fn opts() -> ProcessOptions {
        ProcessOptions {
            embed: true,
            max_workers: 3,
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn duplicate_identity_and_failure_preserve_original_reference() {
        let server = MockServer::start().await;
        Mock::given(path("/ok.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"\x89PNG\r\n\x1a\ncontent".to_vec()),
            )
            .mount(&server)
            .await;
        Mock::given(path("/bad.png"))
            .respond_with(ResponseTemplate::new(404))
            .mount(&server)
            .await;
        let input = format!(
            r#"{{"image":"{}/ok.png?cb=1","backgroundImage":"{}/ok.png?cb=2","thumbnail":"{}/bad.png"}}"#,
            server.uri(),
            server.uri(),
            server.uri()
        );
        let report = tempfile::tempdir().unwrap();
        let (tx, mut rx) = tokio::sync::mpsc::channel(16);
        let mut options = opts();
        options.report_dir = Some(report.path().to_path_buf());
        options.source_url = "https://source.test/project".to_owned();
        let result = process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            Some(tx),
        )
        .await
        .unwrap();
        assert_eq!(result.failed.len(), 1);
        assert!(result.embed_str.contains("data:image/png;base64,"));
        assert!(
            result
                .embed_str
                .contains(&format!("{}/bad.png", server.uri()))
        );
        assert_eq!(server.received_requests().await.unwrap().len(), 2);
        let log = fs::read_to_string(report.path().join("failed_images.txt")).unwrap();
        assert!(log.contains("# Failed image downloads"));
        assert!(log.contains("# Source CYOA : https://source.test/project"));
        assert!(log.contains("# Count       : 1"));
        assert!(log.contains("HTTP 404: asset not found"));
        assert!(matches!(
            rx.recv().await,
            Some(Progress::Started { total: 3 })
        ));
        assert!(matches!(rx.recv().await, Some(Progress::AssetDone { .. })));
    }

    #[tokio::test]
    async fn downloads_named_files_rewrites_json_and_reports_progress() {
        let server = MockServer::start().await;
        Mock::given(path("/images/a.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"\x89PNG\r\n\x1a\nfile".to_vec()),
            )
            .mount(&server)
            .await;
        let dir = tempfile::tempdir().unwrap();
        let (tx, mut rx) = tokio::sync::mpsc::channel(8);
        let input = format!(r#"{{"image":"{}/images/a.png"}}"#, server.uri());
        let mut options = ProcessOptions {
            download: true,
            download_dir: Some(dir.path().to_path_buf()),
            max_workers: 1,
            ..Default::default()
        };
        options.report_dir = Some(dir.path().join("reports"));
        let result = process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            Some(tx),
        )
        .await
        .unwrap();
        assert!(result.dl_str.contains("images/a.png"));
        assert!(dir.path().join("images/a.png").exists());
        assert!(matches!(rx.recv().await, Some(Progress::Started { .. })));
        assert!(matches!(rx.recv().await, Some(Progress::AssetDone { .. })));
        assert!(matches!(
            rx.recv().await,
            Some(Progress::Finished { failed: 0 })
        ));
    }

    #[tokio::test]
    async fn cancellation_while_request_is_pending_returns_cancelled() {
        let server = MockServer::start().await;
        Mock::given(path("/slow.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_delay(Duration::from_secs(5))
                    .set_body_bytes(b"image"),
            )
            .mount(&server)
            .await;
        let cancel = CancellationToken::new();
        let ctx = context(&server, cancel.clone());
        let input = format!(r#"{{"image":"{}/slow.png"}}"#, server.uri());
        let task =
            tokio::spawn(
                async move { process_images(&input, &server.uri(), ctx, &opts(), None).await },
            );
        tokio::time::sleep(Duration::from_millis(100)).await;
        cancel.cancel();
        assert!(matches!(
            tokio::time::timeout(Duration::from_secs(1), task)
                .await
                .unwrap()
                .unwrap(),
            Err(PipelineError::Cancelled)
        ));
    }

    #[tokio::test]
    async fn max_workers_one_limits_request_concurrency() {
        let server = MockServer::start().await;
        let active = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let a = active.clone();
        let p = peak.clone();
        Mock::given(wiremock::matchers::any())
            .respond_with(move |_req: &wiremock::Request| {
                let now = a.fetch_add(1, Ordering::SeqCst) + 1;
                p.fetch_max(now, Ordering::SeqCst);
                let active = a.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_millis(10)).await;
                    active.fetch_sub(1, Ordering::SeqCst);
                });
                ResponseTemplate::new(200)
                    .set_delay(Duration::from_millis(100))
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"\x89PNG\r\n\x1a\n".to_vec())
            })
            .mount(&server)
            .await;
        let input = format!(
            r#"{{"image":"{}/a.png","backgroundImage":"{}/b.png","thumbnail":"{}/c.png"}}"#,
            server.uri(),
            server.uri(),
            server.uri()
        );
        let options = ProcessOptions {
            embed: true,
            max_workers: 1,
            ..Default::default()
        };
        process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            None,
        )
        .await
        .unwrap();
        assert_eq!(peak.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn progress_arrives_when_fast_fetch_group_finishes() {
        let server = MockServer::start().await;
        Mock::given(path("/fast.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"fast".to_vec()),
            )
            .mount(&server)
            .await;
        Mock::given(path("/slow.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_delay(Duration::from_millis(700))
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"slow".to_vec()),
            )
            .mount(&server)
            .await;
        let input = format!(
            r#"{{"image":"{}/fast.png","backgroundImage":"{}/slow.png"}}"#,
            server.uri(),
            server.uri()
        );
        let (tx, mut rx) = tokio::sync::mpsc::channel(8);
        let task = tokio::spawn(async move {
            process_images(
                &input,
                &server.uri(),
                context(&server, CancellationToken::new()),
                &opts(),
                Some(tx),
            )
            .await
        });
        assert!(matches!(rx.recv().await, Some(Progress::Started { .. })));
        let event = tokio::time::timeout(Duration::from_millis(500), rx.recv())
            .await
            .unwrap();
        assert!(
            matches!(event, Some(Progress::AssetDone { ref name, .. }) if name.ends_with("fast.png"))
        );
        task.await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn site_root_relative_asset_reuses_existing_file_without_http() {
        let server = MockServer::start().await;
        let site = tempfile::tempdir().unwrap();
        fs::create_dir_all(site.path().join("loading")).unwrap();
        fs::write(site.path().join("loading/point.png"), b"existing").unwrap();
        let input = r#"{"image":"loading/point.png"}"#;
        let options = ProcessOptions {
            site_folder: Some(site.path().to_path_buf()),
            download: true,
            download_dir: Some(site.path().join("out")),
            ..Default::default()
        };
        let result = process_images(
            input,
            "http://host/",
            context(&server, CancellationToken::new()),
            &options,
            None,
        )
        .await
        .unwrap();
        assert!(result.dl_str.contains("loading/point.png"));
        assert!(server.received_requests().await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn website_mode_keeps_identical_bodies_in_separate_files() {
        let server = MockServer::start().await;
        Mock::given(wiremock::matchers::any())
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"same-body".to_vec()),
            )
            .mount(&server)
            .await;
        let dir = tempfile::tempdir().unwrap();
        let site = tempfile::tempdir().unwrap();
        let options = ProcessOptions {
            download: true,
            download_dir: Some(dir.path().to_path_buf()),
            site_folder: Some(site.path().to_path_buf()),
            max_workers: 2,
            ..Default::default()
        };
        let input = format!(
            r#"{{"image":"{}/a.png","backgroundImage":"{}/b.png"}}"#,
            server.uri(),
            server.uri()
        );
        process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            None,
        )
        .await
        .unwrap();
        assert!(dir.path().join("images/a.png").exists());
        assert!(dir.path().join("images/b.png").exists());
    }

    #[tokio::test]
    async fn cancellation_after_fetch_returns_cancelled_without_committing_files() {
        let server = MockServer::start().await;
        Mock::given(path("/asset.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"asset".to_vec()),
            )
            .mount(&server)
            .await;
        let dir = tempfile::tempdir().unwrap();
        let cancel = CancellationToken::new();
        let options = ProcessOptions {
            download: true,
            download_dir: Some(dir.path().to_path_buf()),
            max_workers: 1,
            ..Default::default()
        };
        let input = format!(r#"{{"image":"{}/asset.png"}}"#, server.uri());
        let (tx, mut rx) = tokio::sync::mpsc::channel(8);
        let task_ctx = context(&server, cancel.clone());
        let task = tokio::spawn(async move {
            process_images(&input, &server.uri(), task_ctx, &options, Some(tx)).await
        });
        while !matches!(rx.recv().await, Some(Progress::AssetDone { .. })) {}
        cancel.cancel();
        assert!(matches!(task.await.unwrap(), Err(PipelineError::Cancelled)));
        assert!(!dir.path().join("images/asset.png").exists());
    }

    #[tokio::test]
    async fn worker_join_panic_is_preserved_as_pipeline_error() {
        let join_error = tokio::spawn(async { panic!("worker panic") })
            .await
            .unwrap_err();
        assert!(matches!(
            PipelineError::from(join_error),
            PipelineError::Worker(_)
        ));
    }

    #[test]
    fn no_clobber_commit_uses_next_suffix() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("images")).unwrap();
        fs::write(dir.path().join("images/a.png"), b"original").unwrap();
        let cancel = CancellationToken::new();
        let written = write_no_clobber(
            dir.path(),
            "images/a.png",
            &crate::fetch::Payload::Mem(tokio_util::bytes::Bytes::from_static(b"new")),
            &cancel,
        )
        .unwrap()
        .unwrap();
        assert_eq!(written, "images/a_1.png");
        assert_eq!(
            fs::read(dir.path().join("images/a.png")).unwrap(),
            b"original"
        );
        assert_eq!(fs::read(dir.path().join(written)).unwrap(), b"new");
    }

    #[tokio::test]
    async fn symlinked_download_ancestor_becomes_failed_asset_when_creation_is_available() {
        let server = MockServer::start().await;
        Mock::given(path("/asset.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(b"unsafe".to_vec()),
            )
            .mount(&server)
            .await;
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let link = root.path().join("images");
        #[cfg(windows)]
        let created = std::os::windows::fs::symlink_dir(outside.path(), &link);
        #[cfg(unix)]
        let created = std::os::unix::fs::symlink(outside.path(), &link);
        if created.is_err() {
            return;
        }
        let options = ProcessOptions {
            download: true,
            download_dir: Some(root.path().to_path_buf()),
            ..Default::default()
        };
        let input = format!(r#"{{"image":"{}/asset.png"}}"#, server.uri());
        let result = process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            None,
        )
        .await
        .unwrap();
        assert_eq!(result.failed.len(), 1);
        assert!(!outside.path().join("asset.png").exists());
    }

    #[tokio::test]
    async fn spooled_streaming_output_is_identical_to_in_memory() {
        let server = MockServer::start().await;
        let mut big = b"\x89PNG\r\n\x1a\n".to_vec();
        big.extend((0..300_000u32).map(|i| (i % 251) as u8));
        Mock::given(path("/big.png"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("Content-Type", "image/png")
                    .set_body_bytes(big),
            )
            .mount(&server)
            .await;
        let input = format!(r#"{{"rows":[{{"image":"{}/big.png"}}]}}"#, server.uri());
        let mut options = opts();
        options.download = true;
        let mem_dl = tempfile::tempdir().unwrap();
        options.download_dir = Some(mem_dl.path().to_path_buf());
        let in_memory = process_images(
            &input,
            &server.uri(),
            context(&server, CancellationToken::new()),
            &options,
            None,
        )
        .await
        .unwrap();

        let work = tempfile::tempdir().unwrap();
        let spool = work.path().join("spool");
        let mut ctx = Arc::try_unwrap(context(&server, CancellationToken::new()))
            .ok()
            .unwrap();
        ctx.spool_dir = Some(spool.clone());
        let file_dl = work.path().join("pkg");
        options.download_dir = Some(file_dl.clone());
        options.embed_out = Some(work.path().join("embedded.json"));
        let streamed = process_images(&input, &server.uri(), Arc::new(ctx), &options, None)
            .await
            .unwrap();

        assert!(streamed.embed_str.is_empty());
        assert!(streamed.embed_changed && in_memory.embed_changed);
        let file = fs::read_to_string(streamed.embed_file.unwrap()).unwrap();
        assert_eq!(
            file, in_memory.embed_str,
            "streamed embed must be byte-identical"
        );
        assert_eq!(streamed.dl_str, in_memory.dl_str);
        assert_eq!(
            fs::read(file_dl.join("images/big.png")).unwrap(),
            fs::read(mem_dl.path().join("images/big.png")).unwrap()
        );
        assert_eq!(fs::read_dir(&spool).unwrap().count(), 1, "body was spooled");
    }
}
