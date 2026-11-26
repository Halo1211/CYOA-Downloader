//! CYOAP Vue support (project/cyoap_vue.py).
//!
//! Verified against Python: `scan_cyoap_assets` and
//! `candidate_urls_for_cyoap_asset` (`fixtures/cyoap.json`), and the whole
//! `try_download_cyoap_vue_site` output on a CYOAP site
//! (`fixtures/cyoap_site.json`, every file byte-identical incl. reports).
//! Assets are fetched sequentially (Python's one-worker order).

use cyoa_net::client::{FetchClient, FetchOptions};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};
use tokio_util::sync::CancellationToken;
use url::Url;

const IMAGE_FIELDS: &[&str] = &[
    "image",
    "backgroundImage",
    "rowBackgroundImage",
    "objectBackgroundImage",
    "defaultImage",
    "bgImage",
    "bg",
    "img",
    "thumbnail",
    "coverImage",
    "headerImage",
    "icon",
    "portrait",
    "avatar",
    "picture",
    "addonBackgroundImage",
    "rowBorderImage",
    "objectBorderImage",
    "addonBorderImage",
    "backpackBgImage",
    "loadingBgImage",
    "favicon",
    "negativeImage",
    "selectedImage",
    "unselectedImage",
    "borderImage",
    "loadingImage",
    "rowImage",
    "choiceImage",
];
const IMAGES: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];
const AUDIO: &[&str] = &[
    ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba",
];
const VIDEO: &[&str] = &[".mp4", ".webm", ".ogv", ".mkv", ".mov", ".m4v"];

pub fn scan_cyoap_assets(v: &Value, images: &mut BTreeSet<String>, media: &mut BTreeSet<String>) {
    match v {
        Value::Array(a) => {
            for x in a {
                scan_cyoap_assets(x, images, media);
            }
        }
        Value::Object(o) => {
            for (k, x) in o {
                if let Value::String(s) = x {
                    let raw = s.trim();
                    if raw.is_empty() || raw.starts_with("data:") {
                        continue;
                    }
                    // Python `urlparse(raw).path.lower()`; urlparse raises
                    // ValueError for an unbalanced IPv6 bracket -> skipped.
                    let parsed = crate::localize::py_urlparse(raw);
                    if parsed.netloc.contains('[') != parsed.netloc.contains(']') {
                        continue;
                    }
                    let path = parsed.path.to_lowercase();
                    // `os.path.splitext(path)[1]` (leading dots of the name ignored).
                    let name = path.rsplit(['/', '\\']).next().unwrap_or("");
                    let trimmed = name.trim_start_matches('.');
                    let ext = trimmed
                        .rfind('.')
                        .map(|i| trimmed[i..].to_owned())
                        .unwrap_or_default();
                    if IMAGE_FIELDS.contains(&k.as_str()) || IMAGES.contains(&ext.as_str()) {
                        images.insert(raw.to_owned());
                    } else if AUDIO.contains(&ext.as_str()) || VIDEO.contains(&ext.as_str()) {
                        media.insert(raw.to_owned());
                    }
                } else {
                    scan_cyoap_assets(x, images, media);
                }
            }
        }
        _ => {}
    }
}
fn quote_path(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || "/:_.-".contains(c) {
                c.to_string()
            } else {
                let mut b = [0; 4];
                c.encode_utf8(&mut b)
                    .as_bytes()
                    .iter()
                    .map(|x| format!("%{x:02X}"))
                    .collect()
            }
        })
        .collect()
}
fn canon(s: String) -> String {
    cyoa_core::url::canonicalize_url(&s).unwrap_or(s)
}
pub fn candidate_urls_for_cyoap_asset(base_url: &str, value: &str, kind: &str) -> Vec<String> {
    let value = value.trim();
    if value.is_empty() || value.starts_with("data:") {
        return vec![];
    }
    if value.starts_with('/') && !value.starts_with("//") {
        return vec![canon(crate::fetch_plan::python_urljoin(base_url, value))];
    }
    let explicit =
        value.starts_with("http://") || value.starts_with("https://") || value.starts_with("//");
    let host_like = value.split_once('/').is_some_and(|(h, _)| h.contains('.'));
    if explicit || host_like {
        let abs = if value.starts_with("//") {
            crate::fetch_plan::python_urljoin(base_url, value)
        } else {
            value.to_owned()
        };
        if let Ok(c) = cyoa_core::url::canonicalize_url(&abs) {
            return vec![c];
        }
    }
    let norm = value.trim_start_matches('/');
    let quoted = quote_path(norm);
    let mut out = Vec::new();
    let mut add = |s: &str| out.push(crate::fetch_plan::python_urljoin(base_url, s));
    if kind == "images" && !norm.starts_with("dist/") {
        add(&format!("dist/images/{norm}"));
        add(&format!("dist/images/{quoted}"));
    }
    add(norm);
    add(&quoted);
    if kind != "images" && !norm.starts_with("dist/") {
        for d in [
            "dist/audio/",
            "dist/media/",
            "dist/images/",
            "audio/",
            "media/",
        ] {
            add(&format!("{d}{norm}"));
            add(&format!("{d}{quoted}"));
        }
    }
    let mut seen = BTreeSet::new();
    out.retain(|u| seen.insert(u.clone()));
    out
}
async fn get(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Result<(u16, String, Vec<u8>, String), String> {
    let u = Url::parse(url).map_err(|e| e.to_string())?;
    let r = client
        .get(
            &u,
            &FetchOptions {
                return_error_response: true,
                ..Default::default()
            },
            cancel,
        )
        .await
        .map_err(|e| e.to_string())?;
    let status = r.status.as_u16();
    let ct = r
        .headers
        .get("content-type")
        .and_then(|x| x.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let final_url = r.final_url.to_string();
    let b = FetchClient::bytes(r, 64 * 1024 * 1024)
        .await
        .map_err(|e| e.to_string())?
        .to_vec();
    Ok((status, ct, b, final_url))
}
/// Auto-detect probe that follows the entry page's redirect like the
/// downloader: `/play` -> `/game/index.html` probes `/game/dist/` first.
pub async fn probe_cyoap_vue_redirect_aware(
    client: &FetchClient,
    start_url: &str,
    cancel: &CancellationToken,
) -> bool {
    let original_base = crate::website::js::directory_base_url(start_url);
    if let Ok((_, _, _, final_url)) = get(client, start_url, cancel).await
        && final_url != start_url
        && cyoa_core::url::same_origin(&final_url, &original_base)
    {
        let page_base = crate::fetch_plan::python_urljoin(&final_url, "./");
        if page_base != original_base
            && probe_cyoap_vue_structure(client, &page_base, cancel)
                .await
                .is_some()
        {
            return true;
        }
    }
    probe_cyoap_vue_structure(client, &original_base, cancel)
        .await
        .is_some()
}

pub async fn probe_cyoap_vue_structure(
    client: &FetchClient,
    base_url: &str,
    cancel: &CancellationToken,
) -> Option<(String, String)> {
    let base =
        crate::fetch_plan::python_urljoin(&format!("{}/", base_url.trim_end_matches('/')), "");
    let mut texts = Vec::new();
    let mut total = 0usize;
    for (path, obj) in [
        ("dist/platform.json", true),
        ("dist/nodes/list.json", false),
    ] {
        let u = crate::fetch_plan::python_urljoin(&base, path);
        let (status, ct, b, _) = get(client, &u, cancel).await.ok()?;
        if status != 200 || ct.to_ascii_lowercase().contains("text/html") {
            return None;
        }
        let t = String::from_utf8_lossy(&b).into_owned();
        let st = t.trim_start().to_ascii_lowercase();
        if st.starts_with("<!doctype html") || st.starts_with("<html") {
            return None;
        }
        let v: Value = serde_json::from_str(&t).ok()?;
        if obj && !v.is_object() || !obj && !v.is_array() {
            return None;
        }
        total += b.len();
        texts.push(t);
    }
    if total > 256 * 1024 {
        return None;
    }
    Some((texts.remove(0), texts.remove(0)))
}
fn local(root: &Path, url: &str) -> Result<PathBuf, String> {
    cyoa_core::url::cyoap_local_path(&root.to_string_lossy(), url).map_err(|e| e.to_string())
}

/// Python `pathlib.Path.write_text` (text mode: CRLF on Windows).
fn write_text_mode(p: &Path, t: &str) -> Result<(), String> {
    if let Some(d) = p.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    fs::write(p, crate::reports::os_text(t)).map_err(|e| e.to_string())
}

/// Python `atomic_write_text` / `atomic_write_bytes` (no newline translation).
fn write_raw(p: &Path, bytes: &[u8]) -> Result<(), String> {
    if let Some(d) = p.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    cyoa_storage::atomic_io::atomic_write_bytes(p, bytes)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn rel_of(root: &Path, p: &Path) -> String {
    p.strip_prefix(root)
        .unwrap_or(p)
        .to_string_lossy()
        .replace('\\', "/")
}

/// Success/failure bookkeeping of `try_download_cyoap_vue_site`.
#[derive(Default)]
struct Results {
    ok: Vec<crate::reports::ReportItem>,
    bad: Vec<crate::reports::ReportItem>,
    ok_groups: BTreeMap<String, Vec<String>>,
    bad_groups: BTreeMap<String, Vec<String>>,
}

impl Results {
    fn success(&mut self, root: &Path, url: &str, local: &Path, kind: &str) {
        let rel = rel_of(root, local);
        self.ok_groups
            .entry(kind.to_owned())
            .or_default()
            .push(rel.clone());
        self.ok.push(crate::reports::ReportItem {
            url: url.to_owned(),
            local: rel,
            kind: kind.to_owned(),
            error: String::new(),
        });
    }

    fn failed(&mut self, url: &str, kind: &str, error: &str) {
        self.bad_groups
            .entry(kind.to_owned())
            .or_default()
            .push(url.to_owned());
        self.bad.push(crate::reports::ReportItem {
            url: url.to_owned(),
            local: String::new(),
            kind: kind.to_owned(),
            error: error.to_owned(),
        });
    }
}

struct RemoteResponse {
    body: Vec<u8>,
    charset: String,
    content_type: String,
    final_url: String,
}

async fn fetch_remote(
    client: &FetchClient,
    url: &str,
    ssrf_base: &str,
    cancel: &CancellationToken,
) -> Result<RemoteResponse, String> {
    if cyoa_parse::discover::ssrf_block_cross_origin(url, ssrf_base, false) {
        tracing::warn!("[SSRF blocked] CYOAP Vue asset: {url}");
        return Err("blocked: cross-origin internal host".into());
    }
    // Python fetch_response returns None for HTTP errors -> "request failed".
    let (status, ct, body, final_url) = get(client, url, cancel)
        .await
        .map_err(|_| "request failed".to_owned())?;
    if status >= 400 {
        return Err("request failed".into());
    }
    if status != 200 {
        return Err(format!("HTTP {status}"));
    }
    let charset = ct
        .split(';')
        .skip(1)
        .filter_map(|p| p.trim().strip_prefix("charset="))
        .map(|c| c.trim_matches(['"', '\'']).to_owned())
        .find(|c| {
            !matches!(
                c.to_lowercase().as_str(),
                "iso-8859-1" | "iso8859-1" | "latin-1" | "latin1" | ""
            )
        })
        .unwrap_or_default();
    Ok(RemoteResponse {
        body,
        charset,
        content_type: ct,
        final_url,
    })
}

/// Error text used to carry cancellation out of `try_download_cyoap_vue_site`.
pub const CYOAP_CANCELLED: &str = "download cancelled";
/// Safety caps (Python has none; far above any real CYOAP site).
const MAX_CYOAP_NODES: usize = 20_000;
const MAX_CYOAP_SITE_REQUESTS: usize = 20_000;

fn text_of(body: &[u8], charset: &str) -> String {
    cyoa_parse::project::try_decode_bytes(body, charset)
}

const BINARY_SITE_EXT: &[&str] = &[
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".svg", ".avif", ".ico", ".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus", ".weba",
    ".mp4", ".webm", ".ogv", ".mkv", ".mov", ".m4v",
];

fn url_ext(url: &str) -> String {
    let path = crate::localize::py_urlparse(url).path.to_lowercase();
    let name = path.rsplit('/').next().unwrap_or("");
    let trimmed = name.trim_start_matches('.');
    trimmed
        .rfind('.')
        .map(|i| trimmed[i..].to_owned())
        .unwrap_or_default()
}

fn replace_values(v: &Value, map: &BTreeMap<String, String>) -> Value {
    match v {
        Value::String(s) => Value::String(map.get(s).cloned().unwrap_or_else(|| s.clone())),
        Value::Array(a) => Value::Array(a.iter().map(|x| replace_values(x, map)).collect()),
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, x)| (k.clone(), replace_values(x, map)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn json_files(root: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = fs::read_dir(root) else { return };
    for e in rd.flatten() {
        let Ok(ft) = e.file_type() else { continue };
        if ft.is_dir() {
            json_files(&e.path(), out);
        } else if ft.is_file() && e.path().extension().is_some_and(|x| x == "json") {
            out.push(e.path());
        }
    }
}

fn reserve_output_folder(base: &Path) -> io::Result<PathBuf> {
    if let Some(parent) = base.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut candidate = base.to_path_buf();
    let mut counter = 1u64;
    loop {
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {
                let mut name = base.as_os_str().to_owned();
                name.push(format!("_{counter}"));
                candidate = PathBuf::from(name);
                counter += 1;
            }
            Err(e) => return Err(e),
        }
    }
}

async fn fetch_dist(
    client: &FetchClient,
    base: &str,
    cancel: &CancellationToken,
) -> Option<(Value, String, Vec<Value>, String)> {
    let join = crate::fetch_plan::python_urljoin;
    let platform = fetch_remote(client, &join(base, "dist/platform.json"), base, cancel)
        .await
        .ok()?;
    let platform_text = text_of(&platform.body, &platform.charset);
    let platform_obj: Value = serde_json::from_str(&platform_text).ok()?;
    if !platform_obj.is_object() {
        return None;
    }
    let list = fetch_remote(client, &join(base, "dist/nodes/list.json"), base, cancel)
        .await
        .ok()?;
    let list_text = text_of(&list.body, &list.charset);
    let file_list = serde_json::from_str(&list_text).ok()?;
    Some((platform_obj, platform_text, file_list, list_text))
}

/// Python `try_download_cyoap_vue_site`. Assets are processed sequentially
/// in sorted order (one of Python's thread-pool orders; Python is
/// deterministic only with one worker).
/// `output_base` is the preferred working directory name.
pub async fn try_download_cyoap_vue_site(
    client: &FetchClient,
    start_url: &str,
    output_base: &Path,
    zip_output: bool,
    max_workers: usize,
    cancel: &CancellationToken,
) -> Result<Option<PathBuf>, String> {
    let _ = max_workers;
    let join = crate::fetch_plan::python_urljoin;
    let original_base = crate::website::js::directory_base_url(start_url);
    let entry = fetch_remote(client, start_url, &original_base, cancel).await;
    let page_url = entry
        .as_ref()
        .ok()
        .map(|response| response.final_url.as_str())
        .filter(|url| cyoa_core::url::same_origin(url, &original_base))
        .unwrap_or(start_url);
    let page_is_html = matches!(url_ext(page_url).as_str(), ".html" | ".htm");
    let page_base = join(page_url, "./");
    let archive_base = if page_is_html || crate::localize::py_urlparse(page_url).path.ends_with('/')
    {
        page_base.as_str()
    } else {
        original_base.as_str()
    };
    let mut base = if page_url == start_url {
        original_base.as_str()
    } else {
        page_base.as_str()
    };
    let mut dist = fetch_dist(client, base, cancel).await;
    if dist.is_none() && base != original_base && !cancel.is_cancelled() {
        // Some aliases expose the shell at the redirect target and dist at the start URL.
        base = original_base.as_str();
        dist = fetch_dist(client, base, cancel).await;
    }
    if cancel.is_cancelled() {
        return Err(CYOAP_CANCELLED.into());
    }
    let Some((platform_obj, platform_text, file_list, list_text)) = dist else {
        return Ok(None);
    };
    let platform_url = join(base, "dist/platform.json");
    let list_url = join(base, "dist/nodes/list.json");
    let mut res = Results::default();
    let mut corrected: BTreeMap<String, String> = BTreeMap::new();
    tracing::info!("cyoap_vue detected: using dist/platform.json + dist/nodes/list.json flow");
    let output_folder = &reserve_output_folder(output_base).map_err(|e| e.to_string())?;
    let project_local = |remote: &str| match remote.strip_prefix(base) {
        Some(relative) if base != archive_base => {
            local(output_folder, &join(archive_base, relative))
        }
        _ => local(output_folder, remote),
    };

    let platform_local = project_local(&platform_url)?;
    write_text_mode(&platform_local, &platform_text)?;
    res.success(output_folder, &platform_url, &platform_local, "json");
    let (mut images, mut media) = (BTreeSet::new(), BTreeSet::new());
    scan_cyoap_assets(&platform_obj, &mut images, &mut media);

    let list_local = project_local(&list_url)?;
    write_text_mode(&list_local, &list_text)?;
    res.success(output_folder, &list_url, &list_local, "json");

    let mut seen_nodes = BTreeSet::new();
    for name in file_list
        .iter()
        .filter_map(Value::as_str)
        .take(MAX_CYOAP_NODES)
    {
        if cancel.is_cancelled() {
            return Err(CYOAP_CANCELLED.into());
        }
        if !seen_nodes.insert(name) {
            continue;
        }
        let name = cyoa_core::paths::py_strip(name);
        if name.is_empty() {
            continue;
        }
        let node_url = join(base, &format!("dist/nodes/{name}"));
        let text = match fetch_remote(client, &node_url, &list_url, cancel).await {
            Ok(r) => text_of(&r.body, &r.charset),
            Err(e) => {
                res.failed(&node_url, "json", &e);
                continue;
            }
        };
        let node_local = project_local(&node_url)?;
        write_text_mode(&node_local, &text)?;
        res.success(output_folder, &node_url, &node_local, "json");
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            scan_cyoap_assets(&v, &mut images, &mut media);
        }
    }

    let suffix_re = regex::Regex::new(r"(?i)(\.(?:avif|gif|jpe?g|png|svg|webp))\d+$").ok();
    let assets: Vec<(String, &str)> = images
        .into_iter()
        .filter(|i| !i.starts_with("data:"))
        .map(|i| (i, "images"))
        .chain(
            media
                .into_iter()
                .filter(|i| !i.starts_with("data:"))
                .map(|i| (i, "media")),
        )
        .collect();
    if !assets.is_empty() {
        tracing::info!(
            "Downloading {} cyoap_vue asset(s) with 1 thread(s)…",
            assets.len()
        );
    }
    'assets: for (item, bucket) in assets {
        if cancel.is_cancelled() {
            return Err(CYOAP_CANCELLED.into());
        }
        for remote in candidate_urls_for_cyoap_asset(base, &item, bucket) {
            let Ok(response) = fetch_remote(client, &remote, base, cancel).await else {
                continue;
            };
            let p = project_local(&remote)?;
            write_raw(&p, &response.body)?;
            res.success(output_folder, &remote, &p, bucket);
            continue 'assets;
        }
        let fixed = match (&suffix_re, bucket) {
            (Some(re), "images") => re.replace(&item, "$1").into_owned(),
            _ => item.clone(),
        };
        if fixed != item {
            for remote in candidate_urls_for_cyoap_asset(base, &fixed, bucket) {
                let p = project_local(&remote)?;
                if p.is_file() {
                    corrected.insert(item.clone(), fixed.clone());
                    continue 'assets;
                }
                let Ok(response) = fetch_remote(client, &remote, base, cancel).await else {
                    continue;
                };
                write_raw(&p, &response.body)?;
                corrected.insert(item.clone(), fixed.clone());
                res.success(output_folder, &remote, &p, bucket);
                continue 'assets;
            }
        }
        res.failed(
            &item,
            bucket,
            "asset not found in candidate cyoap_vue locations",
        );
    }

    if !corrected.is_empty() {
        let mut files = Vec::new();
        json_files(output_folder, &mut files);
        for path in files {
            let Ok(bytes) = fs::read(&path) else { continue };
            let text =
                String::from_utf8_lossy(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes))
                    .into_owned();
            let Ok(original) = serde_json::from_str::<Value>(&text) else {
                tracing::warn!(
                    "Could not repair CYOAP asset reference in {}",
                    path.display()
                );
                continue;
            };
            let repaired = replace_values(&original, &corrected);
            if repaired != original
                && let Ok(out) = serde_json::to_string(&repaired)
            {
                write_raw(&path, out.as_bytes())?;
            }
        }
    }

    let mut entry_ok = false;
    let page = entry.as_ref().map_err(String::as_str).and_then(|response| {
        let body = response
            .body
            .strip_prefix(b"\xef\xbb\xbf")
            .unwrap_or(&response.body);
        let text = text_of(body, &response.charset);
        if text.trim().is_empty() {
            Err("empty entry page")
        } else {
            Ok(text)
        }
    });
    match page {
        Err(e) => res.failed(start_url, "html", e),
        Ok(page_text) => {
            entry_ok = true;
            let entry_url = if page_is_html {
                page_url.to_owned()
            } else if !crate::localize::py_urlparse(page_url).path.ends_with('/')
                && matches!(url_ext(start_url).as_str(), ".html" | ".htm")
            {
                start_url.to_owned()
            } else {
                join(archive_base, "index.html")
            };
            let page_local = local(output_folder, &entry_url)?;
            if let Some(dir) = page_local.parent() {
                fs::create_dir_all(dir).map_err(|e| e.to_string())?;
            }
            let mut file = fs::File::create_new(&page_local)
                .map_err(|e| format!("Could not create entry {}: {e}", page_local.display()))?;
            file.write_all(crate::reports::os_text(&page_text).as_bytes())
                .map_err(|e| e.to_string())?;
            drop(file);
            res.success(output_folder, start_url, &page_local, "html");
            site_assets(
                client,
                page_url,
                &entry_url,
                &page_text,
                output_folder,
                &mut res,
                cancel,
            )
            .await?;
            match localize_page(client, &page_local, page_url, cancel).await {
                Ok((styles, loaders)) => {
                    if styles > 0 {
                        tracing::info!("Localized {styles} external cyoap_vue style reference(s)");
                    }
                    if loaders > 0 {
                        tracing::info!("Localized {loaders} cyoap_vue runtime font loader(s)");
                    }
                }
                Err(e) => tracing::warn!("cyoap_vue external style localization skipped: {e}"),
            }
        }
    }

    if cancel.is_cancelled() {
        return Err(CYOAP_CANCELLED.into());
    }
    let report = crate::reports::BackupReport {
        start_url: start_url.to_owned(),
        project_url: platform_url,
        project_root: "dist/platform.json".into(),
        project_aliases: Vec::new(),
        downloaded: res.ok,
        failed: res.bad.clone(),
        downloaded_groups: res.ok_groups,
        failed_groups: res.bad_groups,
        notes: vec![
            "Engine mode: cyoap_vue".into(),
            "Downloaded dist/platform.json and dist/nodes/list.json when available.".into(),
        ],
    };
    let text =
        crate::reports::format_backup_report_text(&report, &crate::reports::local_timestamp());
    write_text_mode(&output_folder.join("backup_report.txt"), &text)?;
    tracing::info!("cyoap_vue backup report saved: backup_report.txt");
    if !res.bad.is_empty() {
        let items: Vec<crate::reports::FailedItem<'_>> = res
            .bad
            .iter()
            .map(|x| crate::reports::FailedItem {
                url: &x.url,
                path: &x.local,
                error: &x.error,
                kind: &x.kind,
            })
            .collect();
        if let Err(e) = crate::reports::write_asset_failure_summary(
            &items,
            output_folder,
            start_url,
            "Broken cyoap_vue Asset Report",
        ) {
            tracing::debug!("Broken asset report could not be written: {e}");
        }
    }
    // Without its entry page the archive is not playable: report failure
    // (reports above are kept for diagnosis) instead of publishing it.
    if !entry_ok {
        return Err(format!(
            "cyoap_vue entry page could not be downloaded: {start_url}"
        ));
    }
    let written = if zip_output {
        let name = output_folder
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let parent = output_folder.parent().unwrap_or(Path::new("."));
        tracing::info!("Zipping → {name}.zip");
        let (folder, parent) = (output_folder.to_path_buf(), parent.to_path_buf());
        let cancel_b = cancel.clone();
        tokio::task::spawn_blocking(move || {
            let zip = crate::output::zip_package_cancellable(
                &folder,
                &format!("{name}.zip"),
                &parent,
                &|| cancel_b.is_cancelled(),
            )
            .map_err(|e| {
                if cancel_b.is_cancelled() {
                    CYOAP_CANCELLED.to_owned()
                } else {
                    e.to_string()
                }
            })?;
            let _ = fs::remove_dir_all(&folder);
            Ok::<PathBuf, String>(zip)
        })
        .await
        .map_err(|e| e.to_string())??
    } else {
        tracing::info!("ICC folder kept: {}", output_folder.display());
        output_folder.to_path_buf()
    };
    tracing::info!("cyoap_vue website download complete.");
    Ok(Some(written))
}

/// Same-origin site assets of the entry page (BFS like Python).
async fn site_assets(
    client: &FetchClient,
    start_url: &str,
    entry_url: &str,
    page_text: &str,
    root: &Path,
    res: &mut Results,
    cancel: &CancellationToken,
) -> Result<(), String> {
    let join = crate::fetch_plan::python_urljoin;
    let mut queue = std::collections::VecDeque::new();
    let without_fragment = |mut url: String| {
        if let Some(offset) = url.find('#') {
            url.truncate(offset);
        }
        url
    };
    // Resolve each reference against both the live document and its archived location.
    let resolve = |remote_base: &str, saved_base: &str, raw: &str| {
        if raw.is_empty() || raw.starts_with('#') {
            return None;
        }
        let remote = without_fragment(join(remote_base, raw));
        cyoa_core::url::same_origin(&remote, start_url)
            .then(|| (remote, without_fragment(join(saved_base, raw))))
    };
    let src_tags = ["script", "img", "audio", "video", "source"];
    if let Ok(doc) = crate::website::html::parse(page_text) {
        let mut links = Vec::new();
        let mut srcs = Vec::new();
        collect(&doc, &mut |e| {
            if e.name == "link"
                && let Some(h) = e.attr("href")
            {
                links.push(cyoa_core::paths::py_strip(h).to_owned());
            }
            if src_tags.contains(&e.name.as_str())
                && let Some(s) = e.attr("src")
            {
                srcs.push(cyoa_core::paths::py_strip(s).to_owned());
            }
        });
        for v in links.into_iter().chain(srcs) {
            if !v.is_empty() {
                queue.extend(resolve(start_url, entry_url, &v));
            }
        }
    }
    queue.extend(resolve(start_url, entry_url, "favicon.ico"));
    let css_refs =
        regex::Regex::new(r#"(?i)(?:@import\s+)?url\(([^)]+)\)|@import\s+["']?([^"')\s]+)"#).ok();
    let mut seen = BTreeSet::new();
    let mut downloaded = Vec::new();
    let mut stylesheets = BTreeMap::new();
    let mut relocated_assets = BTreeMap::new();
    let path_key = |path: &Path| {
        let relative = rel_of(root, path);
        if cfg!(windows) {
            relative.to_lowercase()
        } else {
            relative
        }
    };
    let mut occupied_paths: BTreeMap<String, String> = res
        .ok
        .iter()
        .map(|item| {
            (
                path_key(&root.join(&item.local)),
                without_fragment(item.url.clone()),
            )
        })
        .collect();
    let path_exists = |path: &Path, paths: &BTreeMap<String, String>| {
        if paths.contains_key(&path_key(path)) {
            Ok(true)
        } else {
            path.try_exists().map_err(|e| e.to_string())
        }
    };
    while let Some((remote, saved_url)) = queue.pop_front() {
        if cancel.is_cancelled() {
            return Err(CYOAP_CANCELLED.into());
        }
        if !seen.insert((remote.clone(), saved_url.clone())) {
            continue;
        }
        if seen.len() > MAX_CYOAP_SITE_REQUESTS {
            tracing::warn!("cyoap_vue site asset limit reached ({MAX_CYOAP_SITE_REQUESTS})");
            break;
        }
        let mut p = local(root, &saved_url)?;
        if occupied_paths.get(&path_key(&p)) == Some(&remote) {
            continue;
        }
        let ext = url_ext(&remote);
        let fetched = fetch_remote(client, &remote, start_url, cancel).await;
        let RemoteResponse {
            body,
            charset,
            content_type,
            final_url,
        } = match fetched {
            Ok(v) => v,
            Err(e) => {
                res.failed(&remote, "assets", &e);
                continue;
            }
        };
        let is_css = ext == ".css"
            || content_type
                .split(';')
                .next()
                .is_some_and(|mime| mime.trim().eq_ignore_ascii_case("text/css"));
        if path_exists(&p, &occupied_paths)? {
            let stem = p.file_stem().unwrap_or_default().to_string_lossy();
            let ext = p
                .extension()
                .map(|ext| format!(".{}", ext.to_string_lossy()))
                .unwrap_or_default();
            for suffix in 1.. {
                let candidate = p.with_file_name(format!("{stem}_{suffix}{ext}"));
                if !path_exists(&candidate, &occupied_paths)? {
                    p = candidate;
                    break;
                }
            }
            relocated_assets.insert((remote.clone(), saved_url.clone()), p.clone());
        }
        occupied_paths.insert(path_key(&p), remote.clone());
        // Extensionless/unknown URLs (`/sprite` serving PNG) must not be decoded
        // as text: keep the bytes whenever they are not valid UTF-8.
        let texty = matches!(
            ext.as_str(),
            ".js" | ".mjs" | ".html" | ".htm" | ".json" | ".txt" | ".svg"
        );
        if !is_css
            && (BINARY_SITE_EXT.contains(&ext.as_str())
                || (!texty && std::str::from_utf8(&body).is_err()))
        {
            write_raw(&p, &body)?;
            downloaded.push((remote, p, "assets"));
            continue;
        }
        let text = text_of(&body, &charset);
        write_raw(&p, text.as_bytes())?;
        let kind = if is_css {
            "css"
        } else if matches!(ext.as_str(), ".js" | ".mjs") {
            "js"
        } else {
            "assets"
        };
        if is_css {
            // Remote dependencies resolve against the stylesheet's final URL
            // (`/theme` -> `/assets/theme.css` makes `url(icon.svg)` mean
            // `/assets/icon.svg`); local paths stay relative to the saved file.
            let css_base = if final_url.is_empty() {
                remote.clone()
            } else {
                final_url
            };
            if let Some(re) = &css_refs {
                for c in re.captures_iter(&text) {
                    if let Some(m) = c.get(1).or_else(|| c.get(2)) {
                        let raw = m.as_str().trim().trim_matches(['\'', '"']);
                        queue.extend(resolve(&css_base, &saved_url, raw));
                    }
                }
            }
            stylesheets.insert(p.clone(), (remote.clone(), saved_url, css_base));
        }
        downloaded.push((remote, p, kind));
    }

    // Allocate suffixes after the crawl so real .css assets keep their paths.
    let mut renamed_styles = BTreeMap::new();
    for (path, (remote, saved_url, _)) in &stylesheets {
        if !url_ext(saved_url).is_empty() || path.extension().is_some_and(|ext| ext == "css") {
            continue;
        }
        let mut dest = path.with_extension("css");
        for suffix in 1.. {
            if !path_exists(&dest, &occupied_paths)? {
                break;
            }
            dest = path.with_extension(format!("{suffix}.css"));
        }
        fs::rename(path, &dest).map_err(|e| e.to_string())?;
        occupied_paths.remove(&path_key(path));
        occupied_paths.insert(path_key(&dest), remote.clone());
        relocated_assets.insert((remote.clone(), saved_url.clone()), dest.clone());
        renamed_styles.insert(path.clone(), dest);
    }
    if !relocated_assets.is_empty() {
        let reference = |remote_base: &str, saved_base: &str, source: &Path, raw: &str| {
            let target = resolve(remote_base, saved_base, raw)?;
            let preferred;
            let dest = if let Some(dest) = relocated_assets.get(&target) {
                dest
            } else if raw.starts_with('?') {
                // A query-only reference inherits the stylesheet's filename.
                preferred = local(root, &target.1).ok()?;
                if preferred == source
                    || occupied_paths.get(&path_key(&preferred)) != Some(&target.0)
                {
                    return None;
                }
                &preferred
            } else {
                return None;
            };
            let target_url = Url::parse(&join(saved_base, raw)).ok()?;
            let offline_url = |path| {
                Url::parse(&format!(
                    "https://offline.invalid/{}",
                    quote_path(&rel_of(root, path))
                ))
                .ok()
            };
            let mut dest_url = offline_url(dest)?;
            dest_url.set_query(target_url.query());
            dest_url.set_fragment(target_url.fragment());
            offline_url(source)?.make_relative(&dest_url)
        };
        if let Some(re) = &css_refs {
            for (path, (_, url, css_base)) in &stylesheets {
                let path = renamed_styles.get(path).unwrap_or(path);
                let css = fs::read_to_string(path).map_err(|e| e.to_string())?;
                let revised = re.replace_all(&css, |c: &regex::Captures<'_>| {
                    let matched = &c[0];
                    let Some(value) = c.get(1).or_else(|| c.get(2)) else {
                        return matched.to_owned();
                    };
                    let raw = value.as_str().trim().trim_matches(['\'', '"']);
                    let Some(replacement) = reference(css_base, url, path, raw) else {
                        return matched.to_owned();
                    };
                    let start = value.start() - c.get(0).map_or(0, |m| m.start())
                        + value.as_str().find(raw).unwrap_or(0);
                    format!(
                        "{}{replacement}{}",
                        &matched[..start],
                        &matched[start + raw.len()..]
                    )
                });
                if revised != css {
                    write_raw(path, revised.as_bytes())?;
                }
            }
        }
        if let Ok(mut doc) = crate::website::html::parse(page_text) {
            let path = local(root, entry_url)?;
            let mut changed = false;
            collect_mut(&mut doc, &mut |tag| {
                let attr = if tag.name == "link" {
                    "href"
                } else if src_tags.contains(&tag.name.as_str()) {
                    "src"
                } else {
                    return;
                };
                if let Some(raw) = tag.attr(attr)
                    && let Some(replacement) =
                        reference(start_url, entry_url, &path, cyoa_core::paths::py_strip(raw))
                {
                    tag.set_attr(attr, replacement);
                    changed = true;
                }
            });
            if changed {
                write_text_mode(&path, &crate::website::html::serialize(&doc))?;
            }
        }
    }
    for (url, path, kind) in downloaded {
        res.success(root, &url, renamed_styles.get(&path).unwrap_or(&path), kind);
    }
    Ok(())
}

fn collect(el: &crate::website::html::Element, f: &mut dyn FnMut(&crate::website::html::Element)) {
    for child in &el.children {
        if let crate::website::html::Node::Element(e) = child
            && !e.removed
        {
            f(e);
            collect(e, f);
        }
    }
}

fn collect_mut(
    el: &mut crate::website::html::Element,
    f: &mut dyn FnMut(&mut crate::website::html::Element),
) {
    for child in &mut el.children {
        if let crate::website::html::Node::Element(e) = child
            && !e.removed
        {
            f(e);
            collect_mut(e, f);
        }
    }
}

/// `localize_cyoap_external_styles` + `localize_cyoap_runtime_fonts`.
/// Cross-origin downloads go through a `MirrorSink` rooted at the page
/// folder (Python uses a `WebsiteDownloader` there); same-origin references
/// are left untouched.
async fn localize_page(
    client: &FetchClient,
    page: &Path,
    page_url: &str,
    cancel: &CancellationToken,
) -> Result<(usize, usize), String> {
    let (page, page_url, client, cancel) = (
        page.to_path_buf(),
        page_url.to_owned(),
        client.clone(),
        cancel.clone(),
    );
    let handle = tokio::runtime::Handle::current();
    tokio::task::spawn_blocking(move || {
        let folder = page.parent().map(Path::to_path_buf).unwrap_or_default();
        let base = crate::website::js::directory_base_url(&page_url);
        let mut sink = crate::website::mirror::MirrorSink::new(
            folder.clone(),
            &page_url,
            &base,
            client,
            handle,
            cancel,
            512 << 20,
        );
        let styles = localize_external_styles(&mut sink, &page, &page_url)?;
        let loaders = localize_runtime_fonts(&mut sink, &page, &page_url)?;
        Ok((styles, loaders))
    })
    .await
    .map_err(|e| e.to_string())?
}

fn localize_external_styles(
    sink: &mut crate::website::mirror::MirrorSink,
    page: &Path,
    page_url: &str,
) -> Result<usize, String> {
    use crate::website::AssetSink;
    use crate::website::html::{Node, StrKind};
    let join = crate::fetch_plan::python_urljoin;
    let text = fs::read_to_string(page).map_err(|e| e.to_string())?;
    let mut doc = crate::website::html::parse(&text).map_err(|e| e.to_string())?;
    let mut localized = 0usize;
    let local_ref = |sink: &mut crate::website::mirror::MirrorSink, remote: &str, kind: &str| {
        if cyoa_core::url::same_origin(remote, page_url) {
            return None;
        }
        sink.download(remote, kind, Some(page_url))
    };
    let url_re = regex::Regex::new(r#"(?i)url\(\s*(['"]?)([^)'"]+)['"]?\s*\)"#).ok();
    collect_mut(&mut doc, &mut |tag| {
        let rels: BTreeSet<String> = tag
            .attr("rel")
            .map(|r| r.split_whitespace().map(str::to_lowercase).collect())
            .unwrap_or_default();
        if tag.name == "link"
            && let Some(href) = tag.attr("href").map(str::to_owned)
        {
            if rels.contains("preconnect")
                && !cyoa_core::url::same_origin(&join(page_url, &href), page_url)
            {
                tag.removed = true;
                localized += 1;
                return;
            }
            if rels.contains("stylesheet")
                && let Some(rep) = local_ref(sink, &join(page_url, &href), "css")
            {
                tag.set_attr("href", rep);
                localized += 1;
            }
        }
        if tag.name == "style"
            && let Some(re) = &url_re
        {
            let css: String = tag
                .children
                .iter()
                .filter_map(|n| match n {
                    Node::Text(_, s) => Some(s.as_str()),
                    _ => None,
                })
                .collect();
            let mut count = 0usize;
            let revised = re
                .replace_all(&css, |c: &regex::Captures<'_>| {
                    let quote = c.get(1).map_or("", |m| m.as_str());
                    let raw = c.get(2).map_or("", |m| m.as_str());
                    if !(raw.starts_with("http://")
                        || raw.starts_with("https://")
                        || raw.starts_with("//"))
                    {
                        return c[0].to_owned();
                    }
                    match local_ref(sink, &join(page_url, raw), "fonts") {
                        Some(rep) => {
                            count += 1;
                            format!("url({quote}{rep}{quote})")
                        }
                        None => c[0].to_owned(),
                    }
                })
                .into_owned();
            if revised != css {
                localized += count;
                tag.children = vec![Node::Text(StrKind::Text, revised)];
            }
        }
        if tag.name == "script"
            && let Some(src) = tag.attr("src")
        {
            let source = join(page_url, src);
            let p = crate::localize::py_urlparse(&source);
            if crate::localize::py_hostname(p.netloc) == "ajax.googleapis.com"
                && p.path.starts_with("/ajax/libs/webfont/")
            {
                tag.removed = true;
                localized += 1;
            }
        }
    });
    if localized > 0 {
        write_raw(page, crate::website::html::serialize(&doc).as_bytes())?;
    }
    Ok(localized)
}

fn localize_runtime_fonts(
    sink: &mut crate::website::mirror::MirrorSink,
    page: &Path,
    page_url: &str,
) -> Result<usize, String> {
    use crate::website::AssetSink;
    let folder = page.parent().unwrap_or(Path::new("."));
    let mut scripts = Vec::new();
    for sub in ["js", "assets"] {
        js_files(&folder.join(sub), &mut scripts);
    }
    let families_re = regex::Regex::new(r"families:\[([^\]]+)\]").ok();
    let quoted_re = regex::Regex::new(r#"["']([^"']+)["']"#).ok();
    let mut families = BTreeSet::new();
    let mut changed = 0usize;
    for script in scripts {
        let Ok(bytes) = fs::read(&script) else {
            continue;
        };
        let content = String::from_utf8_lossy(&bytes).into_owned();
        if let (Some(fr), Some(qr)) = (&families_re, &quoted_re) {
            for m in fr.captures_iter(&content) {
                for q in qr.captures_iter(m.get(1).map_or("", |x| x.as_str())) {
                    let fam = q.get(1).map_or("", |x| x.as_str());
                    if fam.chars().count() <= 150 {
                        families.insert(fam.to_owned());
                    }
                }
            }
        }
        let revised = content.replace(
            "\"https://fonts.googleapis.com/css\"",
            "\"css/offline-fonts.css\"",
        );
        if revised != content {
            write_raw(&script, revised.as_bytes())?;
            changed += 1;
        }
    }
    if changed > 0 || (!page_url.is_empty() && !families.is_empty()) {
        let css_file = folder.join("css").join("offline-fonts.css");
        let mut imports = Vec::new();
        let has_imports = fs::read(&css_file)
            .map(|b| String::from_utf8_lossy(&b).contains("@import"))
            .unwrap_or(false);
        if !page_url.is_empty() && !families.is_empty() && !has_imports {
            for family in families.iter().take(16) {
                let name = family.split('&').next().unwrap_or("");
                let remote = format!(
                    "https://fonts.googleapis.com/css?family={}&display=swap",
                    crate::website::payload::quote_plus(name)
                );
                if let Some(saved) = sink.download(&remote, "css", Some(page_url)) {
                    let rel = saved
                        .strip_prefix("css/")
                        .map_or_else(|| format!("../{saved}"), str::to_owned);
                    imports.push(format!("@import url(\"{rel}\");"));
                }
            }
        }
        if !imports.is_empty() {
            write_raw(&css_file, (imports.join("\n") + "\n").as_bytes())?;
        } else if !css_file.exists() {
            write_raw(
                &css_file,
                b"/* Local font fallback for offline viewing. */\n",
            )?;
        }
    }
    Ok(changed)
}

fn js_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let Ok(ft) = e.file_type() else { continue };
        if ft.is_dir() {
            js_files(&e.path(), out);
        } else if ft.is_file() && e.path().extension().is_some_and(|x| x == "js") {
            out.push(e.path());
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use cyoa_net::{client::ClientConfig, policy::DestinationPolicy};
    use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../../fixtures/cyoap.json")).unwrap()
    }

    #[test]
    fn candidate_urls_match_python() {
        let mut bad = Vec::new();
        for c in fixture()["candidates"].as_array().unwrap() {
            let got = candidate_urls_for_cyoap_asset(
                c["base"].as_str().unwrap(),
                c["value"].as_str().unwrap(),
                c["kind"].as_str().unwrap(),
            );
            let want: Vec<String> = c["out"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned())
                .collect();
            if got != want {
                bad.push(format!(
                    "{} {} {}\n  rust   {got:?}\n  python {want:?}",
                    c["base"], c["value"], c["kind"]
                ));
            }
        }
        assert!(
            bad.is_empty(),
            "{} mismatches:\n{}",
            bad.len(),
            bad.join("\n")
        );
    }

    #[test]
    fn scan_matches_python() {
        for s in fixture()["scans"].as_array().unwrap() {
            let (mut images, mut media) = (BTreeSet::new(), BTreeSet::new());
            scan_cyoap_assets(&s["doc"], &mut images, &mut media);
            let list = |k: &str| -> BTreeSet<String> {
                s[k].as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_owned())
                    .collect()
            };
            assert_eq!(images, list("images"), "{}", s["doc"]);
            assert_eq!(media, list("media"), "{}", s["doc"]);
        }
    }

    async fn mock_cyoap_site(
        start_path: &str,
        entry: ResponseTemplate,
    ) -> (MockServer, FetchClient) {
        let server = MockServer::start().await;
        for (suffix, response) in [
            (
                "dist/platform.json",
                ResponseTemplate::new(200).set_body_json(serde_json::json!({})),
            ),
            (
                "dist/nodes/list.json",
                ResponseTemplate::new(200).set_body_json(serde_json::json!([])),
            ),
            ("", entry),
        ] {
            Mock::given(path(format!("{start_path}{suffix}")))
                .respond_with(response)
                .mount(&server)
                .await;
        }
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&server.uri()).unwrap()),
                allow_internal: true,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        (server, client)
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn concurrent_cyoap_downloads_reserve_distinct_folders() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(200).set_body_string("<html>game</html>"),
        )
        .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        let cancel = CancellationToken::new();
        let (first, second) = tokio::join!(
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &cancel),
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &cancel),
        );
        let first = first.unwrap().unwrap();
        let second = second.unwrap().unwrap();
        assert_ne!(first, second);
        assert!(first.join("demo/index.html").is_file());
        assert!(second.join("demo/index.html").is_file());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn redirected_entry_is_saved_alongside_its_relative_script() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(302).insert_header("location", "/play/"),
        )
        .await;
        Mock::given(path("/play/"))
            .respond_with(
                ResponseTemplate::new(200).set_body_string(r#"<script src="app.js"></script>"#),
            )
            .mount(&server)
            .await;
        Mock::given(path("/play/dist/platform.json"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({"play": true})),
            )
            .mount(&server)
            .await;
        Mock::given(path("/play/dist/nodes/list.json"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!(["node.json"])),
            )
            .mount(&server)
            .await;
        Mock::given(path("/play/dist/nodes/node.json"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({"node": true})),
            )
            .mount(&server)
            .await;
        Mock::given(path("/play/app.js"))
            .respond_with(ResponseTemplate::new(200).set_body_string("window.ready = true;"))
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        let written =
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
                .await
                .unwrap()
                .unwrap();
        let page = written.join("play/index.html");
        assert!(
            fs::read_to_string(page)
                .unwrap()
                .contains(r#"src="app.js""#)
        );
        assert_eq!(
            fs::read_to_string(written.join("play/app.js")).unwrap(),
            "window.ready = true;"
        );
        for (file, expected) in [
            ("platform.json", serde_json::json!({"play": true})),
            ("nodes/list.json", serde_json::json!(["node.json"])),
            ("nodes/node.json", serde_json::json!({"node": true})),
        ] {
            let data = fs::read(written.join("play/dist").join(file)).unwrap();
            assert_eq!(serde_json::from_slice::<Value>(&data).unwrap(), expected);
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn redirected_document_parent_is_probed_before_original_base() {
        let (server, client) = mock_cyoap_site(
            "/old/",
            ResponseTemplate::new(302).insert_header("location", "/new/index.html"),
        )
        .await;
        for (route, body) in [
            ("/new/index.html", "<html>new</html>"),
            ("/new/dist/platform.json", r#"{"new":true}"#),
            ("/new/dist/nodes/list.json", "[]"),
        ] {
            Mock::given(path(route))
                .respond_with(ResponseTemplate::new(200).set_body_string(body))
                .mount(&server)
                .await;
        }
        let tmp = tempfile::tempdir().unwrap();
        let start = format!("{}/old/", server.uri());
        let written = try_download_cyoap_vue_site(
            &client,
            &start,
            &tmp.path().join("game"),
            false,
            1,
            &CancellationToken::new(),
        )
        .await
        .unwrap()
        .unwrap();

        assert_eq!(
            fs::read_to_string(written.join("new/dist/platform.json")).unwrap(),
            r#"{"new":true}"#
        );
        assert!(written.join("new/index.html").is_file());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn invalid_dist_pair_falls_back_to_original_base() {
        for (platform, list) in [("{}", "<html>shell</html>"), ("{}", "{}"), ("[]", "[]")] {
            let (server, client) = mock_cyoap_site(
                "/old/",
                ResponseTemplate::new(302).insert_header("location", "/new/"),
            )
            .await;
            for (route, body) in [
                ("/new/", "<html>new</html>"),
                ("/new/dist/platform.json", platform),
                ("/new/dist/nodes/list.json", list),
            ] {
                Mock::given(path(route))
                    .respond_with(ResponseTemplate::new(200).set_body_string(body))
                    .mount(&server)
                    .await;
            }
            let tmp = tempfile::tempdir().unwrap();
            let start = format!("{}/old/", server.uri());
            let written = try_download_cyoap_vue_site(
                &client,
                &start,
                &tmp.path().join("game"),
                false,
                1,
                &CancellationToken::new(),
            )
            .await
            .unwrap()
            .unwrap();

            let report = fs::read_to_string(written.join("backup_report.txt")).unwrap();
            assert!(report.contains("/old/dist/platform.json"));
            assert_eq!(
                fs::read_to_string(written.join("new/dist/nodes/list.json")).unwrap(),
                "[]"
            );
        }
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn redirect_to_json_variant_preserves_data_and_entry() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(302).insert_header("location", "/demo/dist/platform.json?view=1"),
        )
        .await;
        Mock::given(path("/demo/dist/platform.json"))
            .and(wiremock::matchers::query_param("view", "1"))
            .respond_with(ResponseTemplate::new(200).set_body_string("<html>entry</html>"))
            .with_priority(1)
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
            .await
            .unwrap()
            .unwrap();

        assert_eq!(
            fs::read_to_string(out.join("demo/dist/platform.json")).unwrap(),
            "{}"
        );
        assert_eq!(
            fs::read_to_string(out.join("demo/index.html")).unwrap(),
            "<html>entry</html>"
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn entry_refuses_to_overwrite_a_downloaded_file() {
        use wiremock::matchers::query_param;
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(200).set_body_string("<html>entry</html>"),
        )
        .await;
        Mock::given(path("/demo/dist/nodes/list.json"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(serde_json::json!(["../../index.html?node=1"])),
            )
            .with_priority(1)
            .mount(&server)
            .await;
        Mock::given(path("/demo/index.html"))
            .and(query_param("node", "1"))
            .respond_with(ResponseTemplate::new(200).set_body_string("{}"))
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        let result =
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
                .await;

        assert!(result.is_err());
        assert_eq!(
            fs::read_to_string(out.join("demo/index.html")).unwrap(),
            "{}"
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn extensionless_redirect_saves_html_with_its_relative_script() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(302).insert_header("location", "/play"),
        )
        .await;
        Mock::given(path("/play"))
            .respond_with(
                ResponseTemplate::new(200).set_body_string(r#"<script src="app.js"></script>"#),
            )
            .mount(&server)
            .await;
        Mock::given(path("/app.js"))
            .respond_with(ResponseTemplate::new(200).set_body_string("window.ready = true;"))
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
            .await
            .unwrap()
            .unwrap();

        assert!(
            fs::read_to_string(out.join("demo/index.html"))
                .unwrap()
                .contains(r#"src="app.js""#)
        );
        assert_eq!(
            fs::read_to_string(out.join("demo/app.js")).unwrap(),
            "window.ready = true;"
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn bom_and_whitespace_entry_is_rejected() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(200).set_body_raw("\u{feff} \t\r\n", "text/html"),
        )
        .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        let result =
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
                .await;
        assert!(
            result
                .unwrap_err()
                .contains("entry page could not be downloaded")
        );
        assert!(!out.join("demo/index.html").exists());
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn extensionless_css_downloads_its_imports() {
        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(200).set_body_string(r#"<link rel="stylesheet" href="theme">"#),
        )
        .await;
        Mock::given(path("/demo/theme"))
            .respond_with(ResponseTemplate::new(200).set_body_raw(
                r#"@import "palette.css"; @import url('nested');"#,
                "text/css; charset=utf-8",
            ))
            .mount(&server)
            .await;
        Mock::given(path("/demo/palette.css"))
            .respond_with(
                ResponseTemplate::new(200).set_body_raw("body { color: black; }", "text/css"),
            )
            .mount(&server)
            .await;
        Mock::given(path("/demo/nested"))
            .respond_with(
                ResponseTemplate::new(200).set_body_raw("body { color: red; }", "text/css"),
            )
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{}/demo/", server.uri());
        let written =
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
                .await
                .unwrap()
                .unwrap();
        assert_eq!(
            fs::read_to_string(written.join("demo/palette.css")).unwrap(),
            "body { color: black; }"
        );
        assert!(
            fs::read_to_string(written.join("demo/index.html"))
                .unwrap()
                .contains(r#"href="theme.css""#)
        );
        assert_eq!(
            fs::read_to_string(written.join("demo/theme.css")).unwrap(),
            r#"@import "palette.css"; @import url('nested.css');"#
        );
        assert_eq!(
            fs::read_to_string(written.join("demo/nested.css")).unwrap(),
            "body { color: red; }"
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn colliding_site_assets_keep_distinct_content_and_references() {
        use wiremock::matchers::query_param;

        let (server, client) = mock_cyoap_site(
            "/demo/",
            ResponseTemplate::new(200).set_body_string(concat!(
                r#"<link rel="stylesheet" href="theme.css?variant=light">"#,
                r#"<link rel="stylesheet" href="theme.css?variant=dark">"#,
                r#"<link rel="stylesheet" href="THEME.css?variant=upper">"#,
                r#"<link rel="stylesheet" href="bundle.css">"#,
            )),
        )
        .await;
        for (route, variant, body) in [
            ("/demo/theme.css", "light", "body { color: white; }"),
            (
                "/demo/theme.css",
                "dark",
                r#"@import "?variant=light"; body { color: black; }"#,
            ),
            ("/demo/THEME.css", "upper", "body { color: red; }"),
        ] {
            Mock::given(path(route))
                .and(query_param("variant", variant))
                .respond_with(ResponseTemplate::new(200).set_body_raw(body, "text/css"))
                .expect(1)
                .mount(&server)
                .await;
        }
        Mock::given(path("/demo/bundle.css"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_raw(r#"@import url("theme.css?variant=dark#part");"#, "text/css"),
            )
            .mount(&server)
            .await;
        let tmp = tempfile::tempdir().unwrap();
        let start = format!("{}/demo/", server.uri());
        let written = try_download_cyoap_vue_site(
            &client,
            &start,
            &tmp.path().join("game"),
            false,
            1,
            &CancellationToken::new(),
        )
        .await
        .unwrap()
        .unwrap();
        let folder = written.join("demo");
        let page = fs::read_to_string(folder.join("index.html")).unwrap();
        let upper = if cfg!(windows) {
            "THEME_2.css"
        } else {
            "THEME.css"
        };
        for (file, variant, body) in [
            ("theme.css", "light", "body { color: white; }"),
            (
                "theme_1.css",
                "dark",
                r#"@import "theme.css?variant=light"; body { color: black; }"#,
            ),
            (upper, "upper", "body { color: red; }"),
        ] {
            assert!(page.contains(&format!(r#"href="{file}?variant={variant}""#)));
            assert_eq!(fs::read_to_string(folder.join(file)).unwrap(), body);
        }
        assert_eq!(
            fs::read_to_string(folder.join("bundle.css")).unwrap(),
            r#"@import url("theme_1.css?variant=dark#part");"#
        );
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn cyoap_site_matches_python() {
        use cyoa_net::client::ClientConfig;
        use cyoa_net::policy::DestinationPolicy;
        use wiremock::{Mock, MockServer, ResponseTemplate, matchers::path};
        let fx: Value =
            serde_json::from_str(include_str!("../../../fixtures/cyoap_site.json")).unwrap();
        let server = MockServer::start().await;
        for (p, spec) in fx["site"].as_object().unwrap() {
            let hex = spec["hex"].as_str().unwrap();
            let body: Vec<u8> = (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                .collect();
            // Python http.server also serves `dir/index.html` at `dir/`.
            let mut paths = vec![p.clone()];
            if let Some(dir) = p.strip_suffix("index.html") {
                paths.push(dir.to_owned());
            }
            for served in paths {
                Mock::given(path(served.as_str()))
                    .respond_with(
                        ResponseTemplate::new(200)
                            .insert_header("content-type", spec["ct"].as_str().unwrap())
                            .set_body_bytes(body.clone()),
                    )
                    .mount(&server)
                    .await;
            }
        }
        let uri = server.uri();
        let host = uri.trim_start_matches("http://").to_owned();
        let client = FetchClient::new(ClientConfig {
            policy: DestinationPolicy {
                origin: Some(Url::parse(&uri).unwrap()),
                allow_internal: true,
            },
            ..ClientConfig::default()
        })
        .unwrap();
        let tmp = tempfile::tempdir().unwrap();
        let out = tmp.path().join("game");
        let start = format!("{uri}{}", fx["start_path"].as_str().unwrap());
        let done =
            try_download_cyoap_vue_site(&client, &start, &out, false, 1, &CancellationToken::new())
                .await
                .unwrap();
        assert_eq!(done.as_deref(), Some(out.as_path()));
        fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
            for e in fs::read_dir(dir).unwrap().flatten() {
                if e.file_type().unwrap().is_dir() {
                    walk(&e.path(), out);
                } else {
                    out.push(e.path());
                }
            }
        }
        let mut produced = Vec::new();
        walk(&out, &mut produced);
        let rel_of = |p: &Path| {
            p.strip_prefix(&out)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/")
        };
        let expected = fx["outputs"].as_object().unwrap();
        let mut bad = Vec::new();
        for p in &produced {
            let rel = rel_of(p);
            let Some(spec) = expected.get(&rel) else {
                bad.push(format!("extra file {rel}"));
                continue;
            };
            let bytes = fs::read(p).unwrap();
            if let Some(text) = spec.get("text") {
                let raw = String::from_utf8(bytes).unwrap();
                if cfg!(windows) && raw.contains("\r\n") != spec["crlf"].as_bool().unwrap() {
                    bad.push(format!("{rel}: CRLF mismatch"));
                }
                let got = raw
                    .replace("\r\n", "\n")
                    .split('\n')
                    .filter(|l| {
                        !l.starts_with("Generated")
                            && !l.starts_with("# Generated")
                            && !l.starts_with("# --- ")
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
                    .replace(&host, "HOST");
                if got != text.as_str().unwrap() {
                    bad.push(format!(
                        "{rel}:\n--- rust\n{got}\n--- python\n{}",
                        text.as_str().unwrap()
                    ));
                }
            } else {
                let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
                if hex != spec["hex"].as_str().unwrap() {
                    bad.push(format!("{rel}: bytes differ"));
                }
            }
        }
        for name in expected.keys() {
            if !produced.iter().any(|p| rel_of(p) == *name) {
                bad.push(format!("missing file {name}"));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }
}
