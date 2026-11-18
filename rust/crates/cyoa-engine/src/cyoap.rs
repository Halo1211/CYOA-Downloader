//! CYOAP Vue support (project/cyoap_vue.py) — WORK IN PROGRESS, not wired.
//!
//! Verified against Python fixtures (`rust/fixtures/cyoap.json`):
//! `scan_cyoap_assets`, `candidate_urls_for_cyoap_asset`.
//! `probe_cyoap_vue_structure` / `try_download_cyoap_vue_site` are a partial
//! draft (Luna, s6): site-asset BFS, external style/font localization,
//! JSON value repair and Python result ordering are still missing, so the
//! CLI does not call them yet. See FEATURE_PARITY F-022.

use cyoa_net::client::{FetchClient, FetchOptions};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
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
    let p = cyoa_core::url::cyoap_local_path(&root.to_string_lossy(), url)
        .map_err(|e| e.to_string())?;
    Ok(p)
}
fn write_text(p: &Path, t: &str) -> Result<(), String> {
    if let Some(d) = p.parent() {
        fs::create_dir_all(d).map_err(|e| e.to_string())?
    }
    fs::write(p, crate::reports::os_text(t)).map_err(|e| e.to_string())
}
#[derive(Default)]
struct Results {
    ok: Vec<crate::reports::ReportItem>,
    bad: Vec<crate::reports::ReportItem>,
    og: BTreeMap<String, Vec<String>>,
    fg: BTreeMap<String, Vec<String>>,
}
async fn fetch_text(
    client: &FetchClient,
    u: &str,
    base: &str,
    referrer: &str,
    cancel: &CancellationToken,
) -> Result<String, String> {
    if cyoa_parse::discover::ssrf_block_cross_origin(
        u,
        if referrer.is_empty() { base } else { referrer },
        false,
    ) {
        return Err("blocked: cross-origin internal host".into());
    }
    let (s, _, b, _) = get(client, u, cancel).await?;
    if s != 200 {
        return Err(format!("HTTP {s}"));
    }
    Ok(String::from_utf8_lossy(&b).into_owned())
}
pub async fn try_download_cyoap_vue_site(
    client: &FetchClient,
    start_url: &str,
    output_folder: &Path,
    zip_output: bool,
    max_workers: usize,
    cancel: &CancellationToken,
) -> Result<bool, String> {
    let base =
        crate::fetch_plan::python_urljoin(&format!("{}/", start_url.trim_end_matches('/')), "");
    let pu = crate::fetch_plan::python_urljoin(&base, "dist/platform.json");
    let lu = crate::fetch_plan::python_urljoin(&base, "dist/nodes/list.json");
    let mut res = Results::default();
    let pt = match fetch_text(client, &pu, &base, &base, cancel).await {
        Ok(t) => t,
        Err(_) => return Ok(false),
    };
    let pobj: Value = match serde_json::from_str(&pt) {
        Ok(v) => v,
        Err(_) => return Ok(false),
    };
    let lt = match fetch_text(client, &lu, &base, &base, cancel).await {
        Ok(t) => t,
        Err(_) => return Ok(false),
    };
    let list: Value =
        serde_json::from_str(&lt).map_err(|e| format!("cyoap_vue list.json invalid: {e}"))?;
    if !list.is_array() {
        return Err("cyoap_vue list.json invalid: list.json is not a list".into());
    }
    crate::output::prepare_clean_output_folder(output_folder).map_err(|e| e.to_string())?;
    let mut images = BTreeSet::new();
    let mut media = BTreeSet::new();
    scan_cyoap_assets(&pobj, &mut images, &mut media);
    for (u, t, k) in [(&pu, &pt, "json"), (&lu, &lt, "json")] {
        let p = local(output_folder, u)?;
        write_text(&p, t)?;
        let rel = p
            .strip_prefix(output_folder)
            .unwrap_or(&p)
            .to_string_lossy()
            .replace('\\', "/");
        res.og.entry(k.into()).or_default().push(rel.clone());
        res.ok.push(crate::reports::ReportItem {
            url: u.clone(),
            local: rel,
            kind: k.into(),
            error: String::new(),
        });
    }
    for f in list
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|s| !s.trim().is_empty())
    {
        let u = crate::fetch_plan::python_urljoin(&base, &format!("dist/nodes/{}", f.trim()));
        if let Ok(t) = fetch_text(client, &u, &base, &lu, cancel).await {
            let p = local(output_folder, &u)?;
            write_text(&p, &t)?;
            let rel = p
                .strip_prefix(output_folder)
                .unwrap_or(&p)
                .to_string_lossy()
                .replace('\\', "/");
            res.og.entry("json".into()).or_default().push(rel.clone());
            res.ok.push(crate::reports::ReportItem {
                url: u,
                local: rel,
                kind: "json".into(),
                error: String::new(),
            });
            if let Ok(v) = serde_json::from_str(&t) {
                scan_cyoap_assets(&v, &mut images, &mut media)
            }
        }
    }
    let mut all: Vec<(String, String)> = images
        .into_iter()
        .map(|x| (x, "images".into()))
        .chain(media.into_iter().map(|x| (x, "media".into())))
        .collect();
    all.sort_by(|a, b| a.1.cmp(&b.1).then(a.0.cmp(&b.0)));
    let _workers = max_workers.max(1);
    let suffix_repair = regex::Regex::new(r"(?i)(\.(?:avif|gif|jpe?g|png|svg|webp))\d+$").ok();
    for (item, kind) in all {
        let mut success = false;
        for u in candidate_urls_for_cyoap_asset(&base, &item, &kind) {
            if cyoa_parse::discover::ssrf_block_cross_origin(&u, &base, false) {
                continue;
            }
            if let Ok((200, _, b, _)) = get(client, &u, cancel).await {
                let p = local(output_folder, &u)?;
                if let Some(d) = p.parent() {
                    fs::create_dir_all(d).map_err(|e| e.to_string())?
                }
                fs::write(&p, b).map_err(|e| e.to_string())?;
                let rel = p
                    .strip_prefix(output_folder)
                    .unwrap_or(&p)
                    .to_string_lossy()
                    .replace('\\', "/");
                res.og.entry(kind.clone()).or_default().push(rel.clone());
                res.ok.push(crate::reports::ReportItem {
                    url: u,
                    local: rel,
                    kind: kind.clone(),
                    error: String::new(),
                });
                success = true;
                break;
            }
        }
        if !success {
            let corrected = if kind == "images" {
                suffix_repair
                    .as_ref()
                    .map(|r| r.replace(&item, "$1").to_string())
                    .unwrap_or_else(|| item.clone())
            } else {
                item.clone()
            };
            if corrected != item {
                for u in candidate_urls_for_cyoap_asset(&base, &corrected, &kind) {
                    if let Ok((200, _, b, _)) = get(client, &u, cancel).await {
                        let p = local(output_folder, &u)?;
                        if let Some(d) = p.parent() {
                            fs::create_dir_all(d).map_err(|e| e.to_string())?
                        }
                        fs::write(&p, b).map_err(|e| e.to_string())?;
                        let rel = p
                            .strip_prefix(output_folder)
                            .unwrap_or(&p)
                            .to_string_lossy()
                            .replace('\\', "/");
                        res.og.entry(kind.clone()).or_default().push(rel.clone());
                        res.ok.push(crate::reports::ReportItem {
                            url: u,
                            local: rel,
                            kind: kind.clone(),
                            error: String::new(),
                        });
                        success = true;
                        break;
                    }
                }
            }
        }
        if !success {
            res.fg.entry(kind.clone()).or_default().push(item.clone());
            res.bad.push(crate::reports::ReportItem {
                url: item,
                local: String::new(),
                kind: kind.clone(),
                error: "asset not found in candidate cyoap_vue locations".into(),
            })
        }
    }
    let page = fetch_text(client, start_url, &base, &base, cancel)
        .await
        .ok();
    if let Some(t) = page {
        let pu = crate::fetch_plan::python_urljoin(
            start_url,
            &format!("{}/", start_url.trim_end_matches('/')),
        );
        let p = local(output_folder, &pu)?;
        write_text(&p, &t)?;
        res.ok.push(crate::reports::ReportItem {
            url: start_url.into(),
            local: p
                .strip_prefix(output_folder)
                .unwrap_or(&p)
                .to_string_lossy()
                .replace('\\', "/"),
            kind: "html".into(),
            error: String::new(),
        });
    }
    let report = crate::reports::BackupReport {
        start_url: start_url.into(),
        project_url: pu,
        project_root: "dist/platform.json".into(),
        downloaded: res.ok,
        failed: res.bad.clone(),
        downloaded_groups: res.og,
        failed_groups: res.fg,
        notes: vec![
            "Engine mode: cyoap_vue".into(),
            "Downloaded dist/platform.json and dist/nodes/list.json when available.".into(),
        ],
        ..Default::default()
    };
    let txt =
        crate::reports::format_backup_report_text(&report, &crate::reports::local_timestamp());
    write_text(&output_folder.join("backup_report.txt"), &txt)?;
    if !res.bad.is_empty() {
        let items: Vec<_> = res
            .bad
            .iter()
            .map(|x| crate::reports::FailedItem {
                url: &x.url,
                path: &x.local,
                error: &x.error,
                kind: &x.kind,
            })
            .collect();
        crate::reports::write_asset_failure_summary(
            &items,
            output_folder,
            start_url,
            "Broken cyoap_vue Asset Report",
        )
        .map_err(|e| e.to_string())?
    }
    if zip_output {
        let name = output_folder
            .file_name()
            .unwrap_or_default()
            .to_string_lossy();
        let parent = output_folder.parent().unwrap_or(Path::new("."));
        crate::output::zip_package(output_folder, &format!("{name}.zip"), parent)
            .map_err(|e| e.to_string())?;
        fs::remove_dir_all(output_folder).map_err(|e| e.to_string())?;
    }
    Ok(true)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

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
}
