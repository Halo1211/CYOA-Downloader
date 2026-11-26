//! F-033 Retry Audio (Python GUI `_retry_youtube_audio`): re-download the
//! YouTube URLs from every `skipped_youtube_audio.txt` under the output folder
//! into the folder of the project that references them, then patch that
//! project JSON. Difference: the patched JSON is written with LF (Python's
//! text-mode write produces CRLF on Windows); the JSON value is identical.

use crate::audio::{AudioConfig, YtDlp, download_youtube_audio, patch_youtube_refs_in_json};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use tokio_util::sync::CancellationToken;

/// `(url, report folder)` pairs in first-seen order; `None` = no report file.
pub fn skipped_audio_entries(outdir: &Path) -> Option<Vec<(String, PathBuf)>> {
    let mut files = Vec::new();
    collect(outdir, &mut files);
    let reports: Vec<&PathBuf> = files
        .iter()
        .filter(|p| {
            p.file_name()
                .is_some_and(|n| n == "skipped_youtube_audio.txt")
        })
        .collect();
    if reports.is_empty() {
        return None;
    }
    let mut entries: Vec<(String, PathBuf)> = Vec::new();
    for r in reports {
        let Ok(text) = fs::read_to_string(r) else {
            continue;
        };
        let folder = r.parent().map(Path::to_path_buf).unwrap_or_default();
        for line in text.lines() {
            let line = line.trim();
            if !line.is_empty() && line.starts_with("http") {
                let item = (line.to_owned(), folder.clone());
                if !entries.contains(&item) {
                    entries.push(item);
                }
            }
        }
    }
    Some(entries)
}

fn collect(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
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
        collect(&d, out);
    }
}

fn reference_keys(url: &str) -> Vec<String> {
    static ID: std::sync::LazyLock<Option<regex::Regex>> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})").ok()
    });
    let mut keys = vec![url.to_owned()];
    if let Some(m) = ID
        .as_ref()
        .and_then(|r| r.captures(url))
        .and_then(|c| c.get(1))
    {
        keys.push(m.as_str().to_owned());
    }
    keys
}

/// Per-folder work: URLs to download there and project files to patch.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct AudioGroup {
    pub urls: BTreeSet<String>,
    pub projects: BTreeMap<PathBuf, String>,
}

/// Python grouping of entries by the project that references each URL.
pub fn group_entries(
    outdir: &Path,
    entries: &[(String, PathBuf)],
) -> BTreeMap<PathBuf, AudioGroup> {
    let mut files = Vec::new();
    collect(outdir, &mut files);
    let candidates: BTreeMap<PathBuf, String> = files
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
        .filter_map(|p| {
            fs::read(&p)
                .ok()
                .map(|b| (p, String::from_utf8_lossy(&b).into_owned()))
        })
        .collect();
    let mut groups: BTreeMap<PathBuf, AudioGroup> = BTreeMap::new();
    for (url, report_folder) in entries {
        let keys = reference_keys(url);
        let mut matches: Vec<(&PathBuf, &String)> = candidates
            .iter()
            .filter(|(_, t)| keys.iter().any(|k| t.contains(k.as_str())))
            .collect();
        // Two downloads can reference the same video: the project beside the
        // skip report that listed it comes first.
        if matches.iter().any(|(p, _)| p.starts_with(report_folder)) {
            matches.retain(|(p, _)| p.starts_with(report_folder));
        }
        let rank = |p: &PathBuf| {
            let lower = p.to_string_lossy().to_lowercase();
            let base = p
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            (
                if base == "project.json" {
                    0
                } else if lower.ends_with("_original.json") {
                    2
                } else {
                    1
                },
                lower,
            )
        };
        matches.sort_by_key(|(p, _)| rank(p));
        if let Some((path, text)) = matches.first() {
            let (mut path, mut text) = ((*path).clone(), (*text).clone());
            let lower = path.to_string_lossy().to_lowercase();
            if lower.ends_with("_original.json") {
                let s = path.to_string_lossy();
                let target =
                    PathBuf::from(format!("{}.json", &s[..s.len() - "_original.json".len()]));
                if let Some(t) = candidates.get(&target) {
                    (path, text) = (target, t.clone());
                }
            }
            let folder = path.parent().map(Path::to_path_buf).unwrap_or_default();
            let g = groups.entry(folder).or_default();
            g.urls.insert(url.clone());
            g.projects.insert(path, text);
        } else {
            groups
                .entry(report_folder.clone())
                .or_default()
                .urls
                .insert(url.clone());
        }
    }
    if groups.is_empty() {
        let g = groups.entry(outdir.to_path_buf()).or_default();
        g.urls.extend(entries.iter().map(|(u, _)| u.clone()));
    }
    groups
}

/// Run the grouped retry; returns `(downloaded tracks, patched projects)`.
pub fn retry_audio_groups(
    groups: &BTreeMap<PathBuf, AudioGroup>,
    cfg: &AudioConfig,
    ytdlp: &dyn YtDlp,
    cancel: &CancellationToken,
) -> (usize, usize) {
    let (mut downloaded, mut patched) = (0, 0);
    for (folder, group) in groups {
        let urls: Vec<String> = group.urls.iter().cloned().collect();
        let result = download_youtube_audio(&urls, folder, folder, "", cfg, ytdlp, cancel);
        downloaded += result.len();
        for (path, snapshot) in &group.projects {
            // yt-dlp can run for minutes: patch the file as it is now, not the
            // grouping-time snapshot, so edits made meanwhile survive.
            let text = &std::fs::read_to_string(path).unwrap_or_else(|_| snapshot.clone());
            let new = patch_youtube_refs_in_json(text, &result);
            if &new == text {
                continue;
            }
            match cyoa_storage::atomic_io::atomic_write_text(path, &new) {
                Ok(_) => {
                    patched += 1;
                    tracing::info!("Updated audio paths: {}", path.display());
                }
                Err(e) => {
                    tracing::error!("Audio project update failed for {}: {e}", path.display())
                }
            }
        }
    }
    tracing::info!(
        "[Retry Audio] {downloaded} track(s) downloaded, {patched} project file(s) patched."
    );
    (downloaded, patched)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn grouping_prefers_live_project_and_falls_back_to_report_folder() {
        let t = tempfile::tempdir().unwrap();
        let (a, b) = (t.path().join("A"), t.path().join("B"));
        fs::create_dir_all(&a).unwrap();
        fs::create_dir_all(&b).unwrap();
        let yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
        fs::write(
            a.join("skipped_youtube_audio.txt"),
            format!("{yt}\nnot-url\n{yt}\n"),
        )
        .unwrap();
        fs::write(
            b.join("skipped_youtube_audio.txt"),
            "https://youtu.be/AAAAAAAAAAA\n",
        )
        .unwrap();
        // Only the raw backup still mentions the video id: patch its live sibling.
        fs::write(a.join("My_original.json"), "{\"a\":\"dQw4w9WgXcQ\"}").unwrap();
        fs::write(a.join("My.json"), "{\"a\":\"audio/x.mp3\"}").unwrap();
        fs::write(a.join("My_metadata.json"), "{\"a\":\"dQw4w9WgXcQ\"}").unwrap();
        let entries = skipped_audio_entries(t.path()).unwrap();
        assert_eq!(entries.len(), 2);
        let groups = group_entries(t.path(), &entries);
        let ga = &groups[&a];
        assert_eq!(
            ga.urls.iter().cloned().collect::<Vec<_>>(),
            vec![yt.to_owned()]
        );
        assert_eq!(
            ga.projects.keys().cloned().collect::<Vec<_>>(),
            vec![a.join("My.json")]
        );
        assert!(groups[&b].projects.is_empty());
        assert!(skipped_audio_entries(&t.path().join("none")).is_none());
    }
}
