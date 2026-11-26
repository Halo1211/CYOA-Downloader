//! gallery-dl fallback (Python `integrations/gallery_dl.py`).
//!
//! Off by default. `smart` only hands page/post/gallery URLs of known hosts
//! to the external `gallery-dl` executable; `force` hands it any http(s)
//! image URL. One file (`--range 1`) is downloaded into a private temp dir
//! with a 90 s limit; the largest media file wins.
//!
//! Difference from Python: availability is probed only through the
//! executable (`gallery-dl --version`); Python also accepts an importable
//! `gallery_dl` module, which a Rust build cannot use.

use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::OnceLock,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

/// Python `_GALLERY_DL_HOSTS` (host -> extractor key).
const HOSTS: &[(&str, &str)] = &[
    ("www.pixiv.net", "pixiv"),
    ("pixiv.net", "pixiv"),
    ("www.deviantart.com", "deviantart"),
    ("deviantart.com", "deviantart"),
    ("danbooru.donmai.us", "danbooru"),
    ("donmai.us", "danbooru"),
    ("e621.net", "e621"),
    ("e926.net", "e621"),
    ("gelbooru.com", "gelbooru"),
    ("hypnohub.net", "hypnohub"),
    ("rule34.xxx", "rule34"),
    ("sankaku.app", "sankaku"),
    ("zerochan.net", "zerochan"),
    ("www.zerochan.net", "zerochan"),
    ("chan.sankakucomplex.com", "sankaku"),
    ("x.com", "twitter"),
    ("twitter.com", "twitter"),
    ("www.twitter.com", "twitter"),
];

/// Python `asset_scan._RAW_GALLERY_DL_CDN_HOSTS`.
const RAW_CDN_HOSTS: &[&str] = &[
    "i.pximg.net",
    "img-original.pximg.net",
    "img-zip-ugoira.pximg.net",
    "pbs.twimg.com",
    "c.deviantart.com",
    "a.deviantart.net",
    "wixmp.com",
    "cdn.donmai.us",
    "static1.e621.net",
    "static1.e926.net",
    "img3.sankakucomplex.com",
    "img.sankakucomplex.com",
    "img1.gelbooru.com",
    "img2.gelbooru.com",
    "img.hypnohub.net",
    "img.rule34.xxx",
    "img3.rule34.xxx",
    "img.rule34.paheal.net",
];

/// Python `IMAGE_EXTENSIONS`.
const IMAGE_EXTENSIONS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".ico",
];

const TIMEOUT: Duration = Duration::from_secs(90);

/// The run was cancelled while gallery-dl was working.
#[derive(Debug, PartialEq, Eq)]
pub struct Cancelled;

/// Private `cyoa_gdl_*` scratch directory removed on drop.
struct TempDir(PathBuf);

impl TempDir {
    fn create() -> Option<Self> {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let dir =
            std::env::temp_dir().join(format!("cyoa_gdl_{}_{n}_{nanos:x}", std::process::id()));
        std::fs::create_dir(&dir).ok().map(|()| Self(dir))
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Process-local gallery-dl configuration (Python `_set_gallery_dl_mode`).
#[derive(Debug)]
pub struct GalleryDl {
    pub mode: String,
    pub path: String,
    pub config: String,
    /// Python `_get_active_proxy()` when not bypassed for the URL.
    pub proxy: Option<String>,
    /// Manual-mode `no_proxy` list (Python `_should_bypass_manual_proxy`).
    pub no_proxy: String,
    available: OnceLock<bool>,
}

impl GalleryDl {
    /// Python mode normalization: anything but smart/force is off.
    pub fn new(mode: &str, path: &str, config: &str) -> Self {
        let m = mode.trim().to_lowercase();
        let mode = if m == "smart" || m == "force" {
            m
        } else {
            "off".into()
        };
        let path = path.trim();
        Self {
            mode,
            path: if path.is_empty() {
                "gallery-dl".into()
            } else {
                path.to_owned()
            },
            config: config.trim().to_owned(),
            proxy: None,
            no_proxy: String::new(),
            available: OnceLock::new(),
        }
    }

    pub fn enabled(&self) -> bool {
        self.mode != "off"
    }

    /// Python `_is_gallery_dl_candidate`: the extractor key, or None.
    pub fn candidate(&self, url: &str) -> Option<String> {
        if self.mode == "off" {
            return None;
        }
        let parsed = url::Url::parse(url).ok()?;
        let scheme = parsed.scheme().to_lowercase();
        let host = parsed.host_str().unwrap_or("").to_lowercase();
        if !(scheme == "http" || scheme == "https") || host.is_empty() {
            return None;
        }
        let path = crate::localize::py_urlparse(url).path.to_lowercase();
        if self.mode == "smart" && is_probable_raw_cdn_asset(url) {
            return None;
        }
        if let Some((_, key)) = HOSTS.iter().find(|(h, _)| *h == host) {
            return Some((*key).to_owned());
        }
        if self.mode == "force" {
            return Some(host);
        }
        [
            "/artworks/",
            "/posts/",
            "/post/",
            "/view/",
            "/gallery/",
            "/status/",
        ]
        .iter()
        .any(|t| path.contains(t))
        .then_some(host)
    }

    /// Python `_gallery_dl_is_available` (cached; `<path> --version` exit 0).
    pub fn is_available(&self) -> bool {
        *self.available.get_or_init(|| {
            let mut cmd = Command::new(&self.path);
            cmd.arg("--version")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            no_window(&mut cmd);
            match cmd.spawn() {
                Ok(mut child) => {
                    let started = Instant::now();
                    loop {
                        match child.try_wait() {
                            Ok(Some(status)) => break status.success(),
                            Ok(None) if started.elapsed() < Duration::from_secs(5) => {
                                std::thread::sleep(Duration::from_millis(50));
                            }
                            _ => {
                                let _ = child.kill();
                                let _ = child.wait();
                                break false;
                            }
                        }
                    }
                }
                Err(e) => {
                    tracing::debug!("gallery-dl availability probe failed: {e}");
                    false
                }
            }
        })
    }

    /// Python `_fetch_via_gallery_dl`: bytes of the largest downloaded media
    /// file, or None. Cancellation is reported as `Err(())`.
    pub fn fetch(
        &self,
        url: &str,
        cancel: &CancellationToken,
    ) -> Result<Option<Vec<u8>>, Cancelled> {
        if cancel.is_cancelled() {
            return Err(Cancelled);
        }
        if self.candidate(url).is_none() {
            return Ok(None);
        }
        if !self.is_available() {
            tracing::debug!("[gallery-dl] unavailable; install with: pip install gallery-dl");
            return Ok(None);
        }
        let Some(tmp) = TempDir::create() else {
            return Ok(None);
        };
        let out_log = tmp.0.join(".cyoa_gdl_output");
        let dest = tmp.0.join("dl");
        let mut cmd = Command::new(&self.path);
        cmd.arg("--destination").arg(&dest).args([
            "--no-mtime",
            "--no-download-archive",
            "--range",
            "1",
            "-q",
        ]);
        if !self.config.is_empty() && Path::new(&self.config).exists() {
            cmd.arg("--config").arg(&self.config);
        }
        if let Some(proxy) = &self.proxy
            && !bypass(&self.no_proxy, url)
        {
            cmd.arg("--proxy").arg(proxy);
        }
        // `--` keeps a URL from ever being parsed as an option.
        cmd.arg("--").arg(url).stdin(Stdio::null());
        match std::fs::File::create(&out_log) {
            Ok(f) => {
                let err = f
                    .try_clone()
                    .map(Stdio::from)
                    .unwrap_or_else(|_| Stdio::null());
                cmd.stdout(Stdio::from(f)).stderr(err);
            }
            Err(_) => {
                cmd.stdout(Stdio::null()).stderr(Stdio::null());
            }
        }
        no_window(&mut cmd);
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                tracing::debug!("[gallery-dl] {e}");
                return Ok(None);
            }
        };
        let started = Instant::now();
        let code = loop {
            if cancel.is_cancelled() {
                let _ = child.kill();
                let _ = child.wait();
                return Err(Cancelled);
            }
            match child.try_wait() {
                Ok(Some(status)) => break status.code().unwrap_or(-1),
                Ok(None) if started.elapsed() < TIMEOUT => {
                    std::thread::sleep(Duration::from_millis(250));
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    tracing::warn!("[gallery-dl] Timeout: {url}");
                    return Ok(None);
                }
                Err(e) => {
                    tracing::debug!("[gallery-dl] {e}");
                    return Ok(None);
                }
            }
        };
        let mut files = Vec::new();
        collect_files(&dest, &mut files);
        // Only image outputs: a post's MP4 must not be embedded as an image.
        let is_image = |p: &Path| {
            p.extension()
                .and_then(|e| e.to_str())
                .map(str::to_ascii_lowercase)
                .is_some_and(|e| {
                    matches!(
                        e.as_str(),
                        "jpg" | "jpeg" | "png" | "gif" | "webp" | "avif" | "bmp" | "svg" | "jfif"
                    )
                })
        };
        let Some(best) = files
            .iter()
            .filter(|(p, _)| is_image(p))
            .max_by_key(|(_, len)| *len)
            .map(|(p, _)| p.clone())
        else {
            let tail = read_tail(&out_log, 4096);
            let short: String = tail.chars().take(200).collect();
            tracing::debug!("[gallery-dl] No output for {url} | rc={code} | output={short}");
            return Ok(None);
        };
        // Bounded like a direct fetch (512 MiB).
        if std::fs::metadata(&best).map_or(true, |m| m.len() > MAX_MEDIA_BYTES) {
            tracing::warn!("[gallery-dl] Output too large or unreadable: {url}");
            return Ok(None);
        }
        let data = std::fs::read(&best).unwrap_or_default();
        if data.len() < 64 {
            return Ok(None);
        }
        let name = best
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        tracing::info!("  [gallery-dl ✓] {name} ({}KB)", data.len() / 1024);
        Ok(Some(data))
    }
}

const MAX_MEDIA_BYTES: u64 = 512 << 20;

/// Last `n` bytes of a file without reading all of it.
fn read_tail(path: &Path, n: u64) -> String {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut f) = std::fs::File::open(path) else {
        return String::new();
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(n)));
    let mut buf = Vec::new();
    let _ = f.take(n).read_to_end(&mut buf);
    String::from_utf8_lossy(&buf).into_owned()
}

/// Python `_is_probable_raw_cdn_asset`.
pub fn is_probable_raw_cdn_asset(url: &str) -> bool {
    let Ok(parsed) = url::Url::parse(url) else {
        return true;
    };
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    let path = crate::localize::py_urlparse(url).path.to_lowercase();
    let ext = cyoa_parse::deep_scan::py_splitext_ext(&path);
    RAW_CDN_HOSTS.contains(&host.as_str()) || IMAGE_EXTENSIONS.contains(&ext)
}

/// Python `_gdl_collect_files`: media files over 64 bytes, excluding
/// .json/.log/.txt/.part.
fn collect_files(dir: &Path, out: &mut Vec<(PathBuf, u64)>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for e in rd.flatten() {
        let path = e.path();
        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if meta.is_dir() {
            collect_files(&path, out);
        } else if meta.is_file() {
            let lower = path.to_string_lossy().to_lowercase();
            if [".json", ".log", ".txt", ".part"]
                .iter()
                .any(|x| lower.ends_with(x))
            {
                continue;
            }
            if meta.len() > 64 {
                out.push((path, meta.len()));
            }
        }
    }
}

/// requests `should_bypass_proxies(url, no_proxy=...)` host rules: exact
/// host, `.suffix` / `suffix` domain match, optional `:port`, `*` = all.
fn bypass(no_proxy: &str, url: &str) -> bool {
    let Ok(parsed) = url::Url::parse(url) else {
        return false;
    };
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    let host_port = parsed
        .port_or_known_default()
        .map(|p| format!("{host}:{p}"))
        .unwrap_or_else(|| host.clone());
    no_proxy
        .split(',')
        .map(|e| e.trim().to_lowercase())
        .filter(|e| !e.is_empty())
        .any(|entry| {
            if entry == "*" {
                return true;
            }
            let bare = entry.trim_start_matches('.');
            host == bare
                || host_port == bare
                || host.ends_with(&format!(".{bare}"))
                || host_port.ends_with(&format!(".{bare}"))
        })
}

#[cfg(windows)]
fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_cmd: &mut Command) {}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn candidate_matches_python() {
        let smart = GalleryDl::new("smart", "", "");
        let force = GalleryDl::new("FORCE", "", "");
        let off = GalleryDl::new("bogus", "", "");
        assert_eq!(off.mode, "off");
        let cases = [
            (
                "https://www.pixiv.net/en/artworks/1",
                Some("pixiv"),
                Some("pixiv"),
            ),
            ("https://i.pximg.net/img/a.png", None, Some("i.pximg.net")),
            (
                "https://example.com/posts/5",
                Some("example.com"),
                Some("example.com"),
            ),
            ("https://example.com/a.png", None, Some("example.com")),
            ("https://example.com/page", None, Some("example.com")),
            ("ftp://example.com/posts/1", None, None),
            ("https://x.com/u/status/1", Some("twitter"), Some("twitter")),
        ];
        for (url, s, f) in cases {
            assert_eq!(smart.candidate(url).as_deref(), s, "smart {url}");
            assert_eq!(force.candidate(url).as_deref(), f, "force {url}");
            assert_eq!(off.candidate(url), None);
        }
    }

    #[test]
    fn proxy_bypass_rules() {
        assert!(bypass("localhost, .example.com", "http://a.example.com/x"));
        assert!(bypass("example.com", "https://example.com/x"));
        assert!(bypass("example.com:8080", "http://example.com:8080/x"));
        assert!(!bypass("example.com", "https://notexample.com/x"));
        assert!(bypass("*", "https://anything/x"));
        assert!(!bypass("", "https://anything/x"));
    }

    #[test]
    fn missing_executable_is_unavailable() {
        let g = GalleryDl::new("force", "definitely-not-a-gallery-dl-binary-xyz", "");
        assert!(!g.is_available());
        assert_eq!(
            g.fetch("https://example.com/a.png", &CancellationToken::new()),
            Ok(None)
        );
    }
}
