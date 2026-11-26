//! F-033 Retry Images (Python GUI `_retry_failed_images`): re-download the
//! URLs listed in every `failed_images.txt` under the output folder and embed
//! them as `data:` URIs into the project JSON files that reference them.

use cyoa_net::client::{FetchClient, FetchOptions, header};
use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
};
use tokio_util::sync::CancellationToken;

/// What Retry Images found before downloading (Python's early dialogs).
#[derive(Debug, PartialEq, Eq)]
pub enum RetryImagesPlan {
    /// "failed_images.txt tidak ditemukan di:\n{outdir}"
    NoReports,
    /// "No failed image URLs found in failed_images.txt"
    NoUrls,
    /// "Tidak ada .json project ditemukan di:\n{outdir}"
    NoProjects,
    Ready {
        urls: Vec<String>,
        json_files: Vec<PathBuf>,
    },
}

fn walk_files(root: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = fs::read_dir(root) else { return };
    let mut entries: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
    entries.sort();
    let mut dirs = Vec::new();
    for p in entries {
        if cyoa_core::paths::is_link_or_junction(&p) {
            continue;
        }
        if p.is_dir() {
            dirs.push(p);
        } else {
            out.push(p);
        }
    }
    for d in dirs {
        walk_files(&d, out);
    }
}

/// Python: collect failed URLs (first column, `http*`, first-seen order) and
/// project JSON candidates (`*.json` minus metadata/state/history files).
pub fn plan_retry_images(outdir: &Path) -> RetryImagesPlan {
    let mut files = Vec::new();
    walk_files(outdir, &mut files);
    let reports: Vec<&PathBuf> = files
        .iter()
        .filter(|p| p.file_name().is_some_and(|n| n == "failed_images.txt"))
        .collect();
    if reports.is_empty() {
        return RetryImagesPlan::NoReports;
    }
    let mut urls: Vec<String> = Vec::new();
    for r in reports {
        let Ok(bytes) = fs::read(r) else {
            tracing::warn!("Cannot read {} during image retry", r.display());
            continue;
        };
        for line in String::from_utf8_lossy(&bytes).lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let url = line.split('\t').next().unwrap_or("").trim();
            if url.starts_with("http") && !urls.iter().any(|u| u == url) {
                urls.push(url.to_owned());
            }
        }
    }
    if urls.is_empty() {
        return RetryImagesPlan::NoUrls;
    }
    let json_files: Vec<PathBuf> = files
        .into_iter()
        .filter(|p| {
            let n = p
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            n.ends_with(".json")
                && !n.ends_with("_metadata.json")
                && n != "download_state.json"
                && n != "download_history.json"
        })
        .collect();
    if json_files.is_empty() {
        return RetryImagesPlan::NoProjects;
    }
    RetryImagesPlan::Ready { urls, json_files }
}

async fn fetch_data_uri(
    client: &FetchClient,
    url: &str,
    cancel: &CancellationToken,
) -> Result<String, String> {
    let parsed = url::Url::parse(url).map_err(|e| e.to_string())?;
    let mut opts = FetchOptions::default();
    opts.headers.insert(
        header::USER_AGENT,
        header::HeaderValue::from_static("Mozilla/5.0"),
    );
    let resp = client
        .get(&parsed, &opts, cancel)
        .await
        .map_err(|e| e.to_string())?;
    let mut mime = resp
        .headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_owned();
    if mime.is_empty() {
        mime = crate::fetch::guess_mime_from_url(url)
            .unwrap_or("image/webp")
            .to_owned();
    }
    if !mime.starts_with("image/") {
        return Err(format!("response is not an image ({mime})"));
    }
    let body = FetchClient::bytes(resp, 256 * 1024 * 1024)
        .await
        .map_err(|e| e.to_string())?;
    if body.is_empty() {
        return Err("downloaded image is empty".into());
    }
    Ok(format!(
        "data:{mime};base64,{}",
        crate::pipeline::encode_base64(&body)
    ))
}

/// Characters that continue a URL: a match followed by one is a prefix of
/// a longer URL (`a.png` in `a.png?size=2`), not a reference to it.
fn continues_url(c: char) -> bool {
    c.is_ascii_alphanumeric() || "?&=._~%/-+#".contains(c)
}

fn find_bounded(text: &str, needle: &str) -> Option<usize> {
    if needle.is_empty() {
        return None;
    }
    let mut from = 0;
    while let Some(i) = text[from..].find(needle).map(|i| i + from) {
        let end = i + needle.len();
        if !text[end..].chars().next().is_some_and(continues_url) {
            return Some(i);
        }
        from = i + 1;
        while !text.is_char_boundary(from) {
            from += 1;
        }
    }
    None
}

fn replace_bounded(text: &str, needle: &str, with: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(i) = find_bounded(rest, needle) {
        out.push_str(&rest[..i]);
        out.push_str(with);
        rest = &rest[i + needle.len()..];
    }
    out.push_str(rest);
    out
}

/// Python `_do_retry`: returns the number of patched references.
pub async fn retry_failed_images(
    client: &FetchClient,
    urls: &[String],
    json_files: &[PathBuf],
    cancel: &CancellationToken,
) -> usize {
    let mut patched = 0usize;
    let mut embedded: HashMap<String, String> = HashMap::new();
    for path in json_files {
        if cancel.is_cancelled() {
            break;
        }
        let Ok(bytes) = fs::read(path) else {
            tracing::warn!("  Cannot read {}", path.display());
            continue;
        };
        let mut text = String::from_utf8_lossy(&bytes).into_owned();
        let mut file_patched = 0usize;
        let mut changed = false;
        // Counted only once the file is written (a locked file patches nothing).
        for url in urls {
            // Bounded matches only: a bare substring replace would also
            // rewrite the prefix of `a.png?size=2`.
            let plain = url.as_str();
            let escaped = url.replace('/', "\\/");
            if find_bounded(&text, plain).is_none() && find_bounded(&text, &escaped).is_none() {
                continue;
            }
            let name = url.rsplit('/').next().unwrap_or(url);
            if let Some(uri) = embedded.get(url) {
                text = replace_bounded(&replace_bounded(&text, plain, uri), &escaped, uri);
                changed = true;
                file_patched += 1;
                tracing::info!("  Reused retry image: {name}");
                continue;
            }
            match fetch_data_uri(client, url, cancel).await {
                Ok(uri) => {
                    text = replace_bounded(&replace_bounded(&text, plain, &uri), &escaped, &uri);
                    embedded.insert(url.clone(), uri);
                    tracing::info!("  ✓ Re-embedded: {name}");
                    changed = true;
                    file_patched += 1;
                }
                Err(e) => {
                    let short: String = url.chars().take(60).collect();
                    tracing::warn!("  ✗ Still failing: {short} — {e}");
                }
            }
        }
        if changed {
            match cyoa_storage::atomic_io::atomic_write_text(path, &text) {
                Ok(_) => {
                    patched += file_patched;
                    tracing::info!(
                        "  Updated: {}",
                        path.file_name()
                            .map(|n| n.to_string_lossy().into_owned())
                            .unwrap_or_default()
                    )
                }
                Err(e) => tracing::error!("  Write failed for {}: {e}", path.display()),
            }
        }
    }
    if patched > 0 {
        tracing::info!("[Retry Images] Done — {patched} gambar berhasil di-embed.");
    } else {
        tracing::warn!("[Retry Images] No images were successfully downloaded.");
    }
    patched
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn bounded_replace_skips_longer_urls() {
        let text =
            r#"{"a":"https://x/a.png","b":"https://x/a.png?size=2","c":"url(https://x/a.png)"}"#;
        assert_eq!(
            replace_bounded(text, "https://x/a.png", "data:"),
            r#"{"a":"data:","b":"https://x/a.png?size=2","c":"url(data:)"}"#
        );
    }

    #[test]
    fn plan_reads_reports_and_projects() {
        let t = tempfile::tempdir().unwrap();
        assert_eq!(plan_retry_images(t.path()), RetryImagesPlan::NoReports);
        fs::create_dir_all(t.path().join("a")).unwrap();
        fs::write(
            t.path().join("a/failed_images.txt"),
            "# header\n\nnot-url\tx\n",
        )
        .unwrap();
        assert_eq!(plan_retry_images(t.path()), RetryImagesPlan::NoUrls);
        fs::write(
            t.path().join("a/failed_images.txt"),
            "https://x.example/a.png\tHTTP 404\nhttps://x.example/a.png\nhttps://x.example/b.png\n",
        )
        .unwrap();
        assert_eq!(plan_retry_images(t.path()), RetryImagesPlan::NoProjects);
        fs::write(t.path().join("a/p.json"), "{}").unwrap();
        fs::write(t.path().join("a/p_metadata.json"), "{}").unwrap();
        fs::write(t.path().join("download_state.json"), "{}").unwrap();
        match plan_retry_images(t.path()) {
            RetryImagesPlan::Ready { urls, json_files } => {
                assert_eq!(
                    urls,
                    vec!["https://x.example/a.png", "https://x.example/b.png"]
                );
                assert_eq!(json_files, vec![t.path().join("a/p.json")]);
            }
            other => panic!("{other:?}"),
        }
    }
}
