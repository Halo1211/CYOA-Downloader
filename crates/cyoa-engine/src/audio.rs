use regex::Regex;
use serde_json::Value;
use std::{
    collections::{BTreeMap, HashSet},
    env, fs, io,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
use tokio_util::sync::CancellationToken;

const AUDIO_EXTS: &[&str] = &[".mp3", ".m4a", ".opus", ".webm", ".ogg", ".aac", ".wav"];

/// Return Python-compatible ID for a YouTube video or other audio URL.
pub fn audio_id_for(url: &str) -> String {
    let Ok(re) = Regex::new(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})") else {
        return "unknown".into();
    };
    if let Some(c) = re.captures(url) {
        return c[1].to_owned();
    }
    let path = url
        .split_once("://")
        .map(|(_, x)| x.split_once('/').map_or("", |(_, p)| p))
        .unwrap_or("");
    let slug = path.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    let clean = Regex::new(r"[^A-Za-z0-9._-]+")
        .map(|r| r.replace_all(slug, "-").into_owned())
        .unwrap_or_else(|_| slug.into());
    let slug = clean
        .trim_matches(|c| c == '.' || c == '-' || c == '_')
        .chars()
        .take(64)
        .collect::<String>();
    format!(
        "{}-{}",
        if slug.is_empty() { "audio" } else { &slug },
        sha1_hex(url.as_bytes())[..8].to_owned()
    )
}

fn sha1_hex(input: &[u8]) -> String {
    let mut data = input.to_vec();
    let bits = (data.len() as u64).wrapping_mul(8);
    data.push(0x80);
    while data.len() % 64 != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bits.to_be_bytes());
    let (mut h0, mut h1, mut h2, mut h3, mut h4) = (
        0x67452301u32,
        0xefcdab89,
        0x98badcfe,
        0x10325476,
        0xc3d2e1f0,
    );
    for chunk in data.as_chunks::<64>().0 {
        let mut w = [0u32; 80];
        for (i, b) in chunk.as_chunks::<4>().0.iter().enumerate() {
            w[i] = u32::from_be_bytes([b[0], b[1], b[2], b[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h0, h1, h2, h3, h4);
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5a827999),
                20..=39 => (b ^ c ^ d, 0x6ed9eba1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1bbcdc),
                _ => (b ^ c ^ d, 0xca62c1d6),
            };
            let t = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        h0 = h0.wrapping_add(a);
        h1 = h1.wrapping_add(b);
        h2 = h2.wrapping_add(c);
        h3 = h3.wrapping_add(d);
        h4 = h4.wrapping_add(e);
    }
    format!("{h0:08x}{h1:08x}{h2:08x}{h3:08x}{h4:08x}")
}

pub fn summarize_ytdlp_error(error: Option<&str>) -> String {
    let text = error.filter(|s| !s.is_empty()).unwrap_or("no output file");
    let lower = text.to_lowercase();
    if lower.contains("cookie database")
        && [
            "could not copy chrome cookie database",
            "could not copy chromium cookie database",
            "permission denied",
            "access is denied",
        ]
        .iter()
        .any(|m| lower.contains(m))
    {
        return "browser cookie database is locked; close Chrome/Edge/Brave completely and retry, or select an exported Netscape cookies.txt in YouTube cookies".into();
    }
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let l = collapsed.to_lowercase();
    let special = if l.contains("no supported javascript runtime") {
        Some(
            "YouTube now requires a JavaScript runtime for audio extraction; install/update yt-dlp[default], install Deno, and restart CYOA Downloader",
        )
    } else if l.contains("signature solving failed") || l.contains("challenge solving failed") {
        Some(
            "YouTube format signatures could not be solved; install/update Deno and yt-dlp, then retry",
        )
    } else if l.contains("provided youtube account cookies are no longer valid") {
        Some(
            "YouTube rejected the exported cookies; log in again and export a fresh Netscape cookies.txt",
        )
    } else {
        None
    };
    special
        .map(str::to_owned)
        .unwrap_or_else(|| collapsed.chars().take(500).collect())
}

fn video_id(url: &str) -> Option<String> {
    Regex::new(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")
        .ok()?
        .captures(url)
        .map(|c| c[1].to_owned())
}

/// Patch JSON project references to downloaded local audio paths.
pub fn patch_youtube_refs_in_json(project: &str, yt_map: &[(String, String)]) -> String {
    if yt_map.is_empty() {
        return project.into();
    }
    let mut root: Value = match serde_json::from_str(project) {
        Ok(v) => v,
        Err(_) => return patch_raw(project, yt_map),
    };
    let paths: HashSet<&str> = yt_map.iter().map(|(_, p)| p.as_str()).collect();
    let refs: BTreeMap<String, String> = yt_map
        .iter()
        .map(|(u, p)| (u.trim().to_owned(), p.clone()))
        .collect();
    let mut ids = BTreeMap::new();
    for (u, p) in yt_map {
        if let Some(id) = video_id(u) {
            ids.insert(id, p.clone());
        }
    }
    fn patch_text(s: &str, map: &[(String, String)], ids: &BTreeMap<String, String>) -> String {
        let mut out = s.to_owned();
        for (u, p) in map {
            let raw = u.trim();
            let unescaped = raw.replace("&amp;", "&");
            let escaped = unescaped
                .replace("&", "&amp;")
                .replace("\"", "&quot;")
                .replace("'", "&#x27;");
            for v in [raw.to_owned(), unescaped, escaped] {
                if !v.is_empty() {
                    out = out.replace(&v, p);
                }
            }
            if let Some(id) = video_id(u)
                && let Ok(re) = Regex::new(&format!(
                    r#"(?i)(playVideo\s*\(\s*['\"]){}(['\"]\s*\))"#,
                    regex::escape(&id)
                ))
            {
                out = re
                    .replace_all(&out, format!("${{1}}{p}${{2}}"))
                    .into_owned();
            }
        }
        let _ = ids;
        out
    }
    fn walk(
        v: &mut Value,
        map: &[(String, String)],
        refs: &BTreeMap<String, String>,
        ids: &BTreeMap<String, String>,
        paths: &HashSet<&str>,
    ) {
        match v {
            Value::Array(a) => {
                for x in a {
                    if let Value::String(s) = x {
                        // `bgmList: ["dQw4w9WgXcQ"]`: a bare ID whose audio
                        // was downloaded becomes its local path.
                        *s = match ids.get(s.trim()) {
                            Some(p) => p.clone(),
                            None => patch_text(s, map, ids),
                        }
                    } else {
                        walk(x, map, refs, ids, paths)
                    }
                }
            }
            Value::Object(o) => {
                let bgm = o
                    .get("bgmId")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_owned();
                let next = if paths.contains(bgm.as_str()) {
                    Some(bgm.clone())
                } else {
                    refs.get(bgm.trim())
                        .cloned()
                        .or_else(|| ids.get(&bgm).cloned())
                        .or_else(|| video_id(&bgm).and_then(|id| ids.get(&id).cloned()))
                };
                if let Some(p) = next {
                    o.insert("bgmId".into(), Value::String(p));
                    o.insert("useAudioURL".into(), Value::Bool(true));
                }
                for x in o.values_mut() {
                    if let Value::String(s) = x {
                        *s = patch_text(s, map, ids)
                    } else if x.is_object() || x.is_array() {
                        walk(x, map, refs, ids, paths)
                    }
                }
            }
            _ => (),
        }
    }
    walk(&mut root, yt_map, &refs, &ids, &paths);
    if let Value::Object(o) = &mut root {
        if let Some(Value::Object(app)) = o.get_mut("app") {
            app.insert("useAudioURL".into(), Value::Bool(true));
        } else {
            o.insert("useAudioURL".into(), Value::Bool(true));
        }
    }
    serde_json::to_string(&root).unwrap_or_else(|_| project.into())
}
fn patch_raw(project: &str, map: &[(String, String)]) -> String {
    let mut out = project.to_owned();
    let mut any = false;
    for (u, p) in map {
        if let Some(id) = video_id(u) {
            for target in [id, u.clone()] {
                if let Ok(re) =
                    Regex::new(&format!(r#""bgmId"\s*:\s*"{}""#, regex::escape(&target)))
                {
                    let replaced = re
                        .replace_all(&out, format!("\"bgmId\":\"{p}\",\"useAudioURL\":true"))
                        .into_owned();
                    any |= replaced != out;
                    out = replaced;
                }
            }
        }
    }
    // Python `out[:200]` counts characters; slicing bytes could split one.
    let head_end = out.char_indices().nth(200).map_or(out.len(), |(i, _)| i);
    if any && !out[..head_end].contains("\"useAudioURL\":true") {
        out = out.replacen("{\"version\":", "{\"useAudioURL\":true,\"version\":", 1);
    }
    out
}

pub fn write_youtube_skip_log(
    items: &[String],
    dir: &Path,
    source_url: &str,
    reasons: &BTreeMap<String, String>,
) -> io::Result<()> {
    use std::io::Write;
    if items.is_empty() {
        return Ok(());
    }
    fs::create_dir_all(dir)?;
    let path = dir.join("skipped_youtube_audio.txt");
    let is_new = !path.exists();
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    let mut s = String::new();
    if is_new {
        s.push_str("# Skipped YouTube audio URLs\n# ============================================================\n# WHY these cannot be made offline:\n#   YouTube ToS prohibits downloading streams.\n#   Streams use signed time-limited URLs (DASH/HLS) — no static file.\n#   Old ICC viewer (Vue) creates YT.Player via JavaScript.\n#   The original reference is preserved; no placeholder is generated.\n#\n# WORKAROUND (manual, personal archival only):\n#   1. pip install yt-dlp\n#   2. yt-dlp -x --audio-format mp3 <youtube_url>\n#   3. Place .mp3 in: <output_folder>/audio/\n#   4. Edit project.json: bgmId -> 'audio/filename.mp3', useAudioURL -> true\n#   Respect copyright — for personal use only.\n# ============================================================\n\n");
    }
    s.push_str(&format!(
        "# --- {} ---\n",
        crate::reports::local_timestamp()
    ));
    if !source_url.is_empty() {
        s.push_str(&format!("# Source CYOA : {source_url}\n"));
    }
    s.push_str(&format!("# Count       : {}\n", items.len()));
    for u in items {
        s.push_str(u);
        s.push('\n');
        if let Some(r) = reasons.get(u)
            && !r.is_empty()
        {
            s.push_str(&format!(
                "# Reason      : {}\n",
                r.chars().take(500).collect::<String>()
            ));
        }
    }
    s.push('\n');
    f.write_all(crate::reports::os_text(&s).as_bytes())?;
    tracing::warn!(
        "{} YouTube audio URL(s) kept as external links (cannot go offline). See: {}",
        items.len(),
        path.display()
    );
    Ok(())
}

#[derive(Clone, Debug)]
pub struct YtAttempt {
    pub url: String,
    pub outtmpl: PathBuf,
    pub public_clients: bool,
    pub cookiefile: Option<PathBuf>,
    /// yt-dlp `--cookies-from-browser BROWSER[:PROFILE]` (Python
    /// `cookiesfrombrowser`): yt-dlp reads the browser profile itself.
    pub cookies_from_browser: Option<String>,
    pub ffmpeg_location: Option<PathBuf>,
}
pub trait YtDlp {
    fn available(&self) -> bool;
    fn run(&self, a: &YtAttempt, cancel: &CancellationToken) -> Result<(), String>;
}
#[derive(Clone, Debug)]
pub struct YtDlpExe {
    pub path: String,
}
impl Default for YtDlpExe {
    fn default() -> Self {
        Self {
            path: "yt-dlp".into(),
        }
    }
}
fn bounded_reader<R: std::io::Read + Send + 'static>(mut r: R) -> thread::JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut out = Vec::new();
        let mut buf = [0u8; 4096];
        loop {
            match r.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let take = n.min(65536usize.saturating_sub(out.len()));
                    out.extend_from_slice(&buf[..take]);
                }
            }
        }
        out
    })
}
impl YtDlp for YtDlpExe {
    fn available(&self) -> bool {
        let mut c = Command::new(&self.path);
        c.arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            c.creation_flags(0x08000000);
        }
        let Ok(mut child) = c.spawn() else {
            return false;
        };
        let start = Instant::now();
        loop {
            if let Ok(Some(s)) = child.try_wait() {
                return s.success();
            }
            if start.elapsed() > Duration::from_secs(10) {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
            thread::sleep(Duration::from_millis(50));
        }
    }
    fn run(&self, a: &YtAttempt, cancel: &CancellationToken) -> Result<(), String> {
        let mut c = Command::new(&self.path);
        c.args([
            "-f",
            "bestaudio[ext=m4a]/bestaudio/best",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "192",
            "-o",
        ])
        .arg(&a.outtmpl)
        .args([
            "-q",
            "--no-warnings",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--extractor-retries",
            "3",
            "--file-access-retries",
            "3",
            "--socket-timeout",
            "30",
            "--continue",
            "--sleep-interval",
            "1",
            "--max-sleep-interval",
            "3",
        ]);
        if a.public_clients {
            c.args([
                "--extractor-args",
                "youtube:player_client=tv,mweb;formats=incomplete",
            ]);
        }
        if let Some(p) = &a.cookiefile {
            c.arg("--cookies").arg(p);
        }
        if let Some(b) = &a.cookies_from_browser {
            c.arg("--cookies-from-browser").arg(b);
        }
        if let Some(p) = &a.ffmpeg_location {
            c.arg("--ffmpeg-location").arg(p);
        }
        c.arg("--")
            .arg(&a.url)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            c.creation_flags(0x08000000);
        }
        let mut child = c.spawn().map_err(|e| e.to_string())?;
        let stdout = child.stdout.take().map(bounded_reader);
        let stderr = child.stderr.take().map(bounded_reader);
        let started = Instant::now();
        loop {
            if cancel.is_cancelled() {
                kill_tree(&mut child);
                return Err("download cancelled".into());
            }
            if started.elapsed() > YTDLP_DEADLINE {
                kill_tree(&mut child);
                return Err("yt-dlp timed out".into());
            }
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                let mut out = stdout.and_then(|h| h.join().ok()).unwrap_or_default();
                let err = stderr.and_then(|h| h.join().ok()).unwrap_or_default();
                out.extend_from_slice(&err);
                out.truncate(65536);
                let text = String::from_utf8_lossy(&out).into_owned();
                return if status.success() { Ok(()) } else { Err(text) };
            }
            thread::sleep(Duration::from_millis(250));
        }
    }
}
/// Kill yt-dlp and its children (ffmpeg postprocessing keeps writing
/// otherwise); Windows does not kill child processes with the parent.
fn kill_tree(child: &mut std::process::Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x08000000)
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}
/// Spawn, poll every 100 ms, kill on cancellation or deadline; true when
/// the process exited successfully in time.
fn run_bounded(mut cmd: Command, limit: Duration, cancel: &CancellationToken) -> bool {
    let Ok(mut child) = cmd.spawn() else {
        return false;
    };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if !cancel.is_cancelled() && started.elapsed() < limit => {
                thread::sleep(Duration::from_millis(100));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// YouTube / SoundCloud hosts accepted for yt-dlp (parsed, anchored).
fn is_media_host(url: &str) -> bool {
    let Ok(u) = url::Url::parse(url) else {
        return false;
    };
    if !matches!(u.scheme(), "http" | "https") {
        return false;
    }
    let host = u.host_str().unwrap_or("").to_ascii_lowercase();
    [
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "soundcloud.com",
    ]
    .iter()
    .any(|d| host == *d || host.ends_with(&format!(".{d}")))
}

/// Total time allowed for one yt-dlp invocation.
const YTDLP_DEADLINE: Duration = Duration::from_secs(30 * 60);

#[derive(Clone, Debug, Default)]
pub struct AudioConfig {
    pub enabled: bool,
    pub explicit_cookies: Option<PathBuf>,
    /// Probe installed browser profiles (Python `_ytdlp_browser_profiles`);
    /// the differential fixture disables it like `make_audio.py` does.
    pub browser_cookies: bool,
}
fn usable(p: &Path) -> bool {
    p.is_file() && p.metadata().map(|m| m.len() > 0).unwrap_or(false)
}
fn any_audio(dir: &Path, id: &str) -> Option<String> {
    let mut files = fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|f| {
            Path::new(f).file_stem().and_then(|s| s.to_str()) == Some(id)
                && AUDIO_EXTS.iter().any(|x| f.to_lowercase().ends_with(x))
                && usable(&dir.join(f))
        })
        .collect::<Vec<_>>();
    files.sort();
    files.into_iter().next()
}
fn ffmpeg_path() -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    for d in env::split_paths(&path) {
        for name in if cfg!(windows) {
            vec!["ffmpeg.exe", "ffmpeg"]
        } else {
            vec!["ffmpeg"]
        } {
            let p = d.join(name);
            if p.is_file() {
                return Some(d);
            }
        }
    }
    None
}
fn cookies(cfg: &AudioConfig, out: &Path, log: &Path) -> Vec<PathBuf> {
    let mut c = Vec::new();
    if let Some(p) = &cfg.explicit_cookies {
        c.push(p.clone())
    }
    for key in ["CYOA_YTDLP_COOKIES", "YTDLP_COOKIES"] {
        if let Some(p) = env::var_os(key) {
            c.push(PathBuf::from(p));
        }
    }
    c.push(out.join("cookies.txt"));
    c.push(log.join("cookies.txt"));
    let mut seen = HashSet::new();
    c.into_iter()
        .filter_map(|p| {
            let p = fs::canonicalize(&p).ok()?;
            if p.is_file() && seen.insert(p.clone()) {
                Some(p)
            } else {
                None
            }
        })
        .collect()
}
fn bot_error(e: Option<&str>) -> bool {
    let l = e.unwrap_or("").to_lowercase();
    [
        "sign in to confirm",
        "bot",
        "confirm your age",
        "private video",
        "video unavailable",
        "http error 403",
        "this video is not available",
    ]
    .iter()
    .any(|x| l.contains(x))
}

pub fn download_youtube_audio(
    urls: &[String],
    out_dir: &Path,
    log_dir: &Path,
    source_url: &str,
    cfg: &AudioConfig,
    ytdlp: &dyn YtDlp,
    cancel: &CancellationToken,
) -> Vec<(String, String)> {
    let target = if log_dir.as_os_str().is_empty() {
        out_dir
    } else {
        log_dir
    };
    if !ytdlp.available() {
        tracing::warn!(
            "{} YouTube audio URL(s) — yt-dlp tidak terinstall.\n  Install: pip install yt-dlp  (+ ffmpeg for MP3 conversion)",
            urls.len()
        );
        let _ = write_youtube_skip_log(urls, target, source_url, &BTreeMap::new());
        return vec![];
    }
    if !cfg.enabled {
        tracing::info!(
            "{} YouTube audio URL(s) dilewati (YT Audio dimatikan).",
            urls.len()
        );
        let _ = write_youtube_skip_log(urls, target, source_url, &BTreeMap::new());
        return vec![];
    }
    let audio = out_dir.join("audio");
    if let Err(e) = fs::create_dir_all(&audio) {
        tracing::warn!("Could not create audio directory: {e}");
        return vec![];
    }
    if cyoa_core::paths::is_link_or_junction(&audio) {
        tracing::warn!("Refusing linked audio directory: {}", audio.display());
        return vec![];
    }
    let mut result = Vec::new();
    let mut failed = Vec::new();
    let mut reasons = BTreeMap::new();
    tracing::info!("yt-dlp: Downloading {} YouTube audio track(s)…", urls.len());
    for raw in urls {
        if cancel.is_cancelled() {
            break;
        }
        let clean = raw.trim();
        let clean = if clean.starts_with("http") {
            clean.to_owned()
        } else {
            format!("https://www.youtube.com/watch?v={clean}")
        };
        // Security divergence from Python (Sol review): the media regexes
        // are unanchored, so only real YouTube/SoundCloud hosts are handed
        // to yt-dlp (it fetches outside the destination policy).
        if !is_media_host(&clean) {
            let reason = "not a YouTube/SoundCloud host; not passed to yt-dlp".to_owned();
            tracing::warn!("  yt-dlp: skipped {clean} — {reason}");
            failed.push(raw.clone());
            reasons.insert(raw.clone(), reason);
            continue;
        }
        let id = audio_id_for(&clean);
        let expected = audio.join(format!("{id}.mp3"));
        let rel = format!("audio/{id}.mp3");
        if usable(&expected) {
            tracing::info!("  yt-dlp: already exists — {rel}");
            result.push((raw.clone(), rel));
            continue;
        }
        let tmpl = audio.join(format!("{id}.%(ext)s"));
        let ff = ffmpeg_path();
        let mut last = None;
        let first = YtAttempt {
            url: clean.clone(),
            outtmpl: tmpl.clone(),
            public_clients: true,
            cookiefile: None,
            cookies_from_browser: None,
            ffmpeg_location: ff.clone(),
        };
        tracing::info!("  yt-dlp: trying public TV/mobile clients");
        let _ = ytdlp.run(&first, cancel).map_err(|e| last = Some(e));
        if cancel.is_cancelled() {
            break;
        }
        let mut found = any_audio(&audio, &id);
        if found.is_none() && !bot_error(last.as_deref()) {
            tracing::info!("  yt-dlp: public clients failed; trying default clients");
            let a = YtAttempt {
                public_clients: false,
                ..first.clone()
            };
            if let Err(e) = ytdlp.run(&a, cancel) {
                last = Some(e)
            }
            if cancel.is_cancelled() {
                break;
            }
            found = any_audio(&audio, &id)
        }
        let manual = cookies(cfg, out_dir, target);
        if found.is_none() {
            for cookie in &manual {
                tracing::info!("  yt-dlp: trying supplied browser cookie file");
                let a = YtAttempt {
                    public_clients: false,
                    cookiefile: Some(cookie.clone()),
                    ..first.clone()
                };
                if let Err(e) = ytdlp.run(&a, cancel) {
                    last = Some(e)
                }
                if cancel.is_cancelled() {
                    break;
                }
                found = any_audio(&audio, &id);
                if found.is_some() {
                    tracing::info!("  yt-dlp: cookie file authentication succeeded");
                    break;
                }
            }
        }
        if cancel.is_cancelled() {
            break;
        }
        if found.is_none() && manual.is_empty() {
            tracing::info!(
                "  yt-dlp: first attempt failed {}, retrying with browser cookies…",
                if bot_error(last.as_deref()) {
                    "(bot-detected)"
                } else {
                    "(unknown error)"
                }
            );
            // Python: installed browsers in this order, every local profile;
            // a locked cookie database skips that browser's other profiles.
            'browsers: for browser in ["chrome", "edge", "brave", "chromium", "firefox"] {
                let profiles = if cfg.browser_cookies {
                    crate::browser_cookies::browser_profiles(browser)
                } else {
                    Vec::new()
                };
                for profile in profiles {
                    if cancel.is_cancelled() {
                        break 'browsers;
                    }
                    let a = YtAttempt {
                        public_clients: false,
                        cookies_from_browser: Some(
                            crate::browser_cookies::cookies_from_browser_arg(
                                browser,
                                profile.as_deref(),
                            ),
                        ),
                        ..first.clone()
                    };
                    let err = ytdlp.run(&a, cancel).err();
                    if err.is_some() {
                        last = err.clone();
                    }
                    found = any_audio(&audio, &id);
                    if found.is_some() {
                        let suffix = profile
                            .as_deref()
                            .map(|p| format!(" ({p})"))
                            .unwrap_or_default();
                        tracing::info!("  yt-dlp: cookie source → {browser}{suffix}");
                        break 'browsers;
                    }
                    if crate::browser_cookies::is_cookie_database_lock_error(err.as_deref()) {
                        tracing::debug!(
                            "  yt-dlp: skipping remaining {browser} profiles because its cookie database is locked"
                        );
                        break;
                    }
                }
            }
        } else if found.is_none() {
            tracing::info!(
                "  yt-dlp: supplied cookies.txt did not produce a file; automatic browser-cookie probing skipped"
            );
        }
        if let Some(file) = found {
            let ext = Path::new(&file)
                .extension()
                .and_then(|x| x.to_str())
                .unwrap_or("")
                .to_lowercase();
            if ext == "mp3" {
                result.push((raw.clone(), format!("audio/{file}")));
                tracing::info!("  yt-dlp OK: audio/{file}");
            } else {
                let src = audio.join(&file);
                let dst = expected.clone();
                let mut converted = false;
                {
                    let exe = ffmpeg_path()
                        .map(|dir| {
                            dir.join(if cfg!(windows) {
                                "ffmpeg.exe"
                            } else {
                                "ffmpeg"
                            })
                        })
                        .unwrap_or_else(|| PathBuf::from("ffmpeg"));
                    // Unique per conversion: concurrent jobs in one process must
                    // not share (and publish) each other's temp file.
                    static TMP_SEQ: std::sync::atomic::AtomicU64 =
                        std::sync::atomic::AtomicU64::new(0);
                    let tmp = audio.join(format!(
                        ".cyoa-audio-{}-{}.part.mp3",
                        std::process::id(),
                        TMP_SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
                    ));
                    let mut ffmpeg = Command::new(exe);
                    ffmpeg
                        .args(["-i"])
                        .arg(&src)
                        .args(["-q:a", "2", "-y"])
                        .arg(&tmp)
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null());
                    #[cfg(windows)]
                    {
                        use std::os::windows::process::CommandExt;
                        ffmpeg.creation_flags(0x0800_0000);
                    }
                    // Python: subprocess.run(..., timeout=60); cancellable here.
                    if run_bounded(ffmpeg, Duration::from_secs(60), cancel)
                        && usable(&tmp)
                        && fs::rename(&tmp, &dst).is_ok()
                    {
                        let _ = fs::remove_file(&src);
                        converted = true;
                    } else {
                        let _ = fs::remove_file(&tmp);
                    }
                }
                if converted {
                    result.push((raw.clone(), rel.clone()));
                    tracing::info!("  yt-dlp OK (converted .{ext}→mp3): {rel}");
                } else {
                    result.push((raw.clone(), format!("audio/{file}")));
                    tracing::info!("  yt-dlp OK (kept .{ext}): audio/{file}");
                }
            }
        } else {
            let reason = summarize_ytdlp_error(last.as_deref());
            tracing::warn!("  yt-dlp: file not found after download: {clean} — {reason}");
            failed.push(raw.clone());
            reasons.insert(raw.clone(), reason);
        }
    }
    if !failed.is_empty() {
        let _ = write_youtube_skip_log(&failed, target, source_url, &reasons);
    }
    if !result.is_empty() {
        tracing::info!(
            "yt-dlp: {}/{} track(s) downloaded. Files in: {}",
            result.len(),
            urls.len(),
            audio.display()
        );
    }
    result
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    #[test]
    fn sha1_vectors() {
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
    }
    #[test]
    fn fixture_summaries_and_ids() {
        let f: Value = serde_json::from_str(include_str!("../../../fixtures/audio.json")).unwrap();
        for row in f["summaries"].as_array().unwrap() {
            let error = row[0].as_str();
            assert_eq!(summarize_ytdlp_error(error), row[1].as_str().unwrap());
        }
        assert_eq!(
            audio_id_for("https://soundcloud.com/artist/some-track"),
            "some-track-89f2d61b"
        );
    }
    #[test]
    fn fixture_patch_cases() {
        let f: Value = serde_json::from_str(include_str!("../../../fixtures/audio.json")).unwrap();
        for row in f["patch"].as_array().unwrap() {
            let map = row["map"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(u, p)| (u.clone(), p.as_str().unwrap().into()))
                .collect::<Vec<_>>();
            assert_eq!(
                patch_youtube_refs_in_json(row["project"].as_str().unwrap(), &map),
                row["expected"].as_str().unwrap()
            );
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod flow_tests {
    use super::*;
    use std::sync::Mutex;
    struct Fake {
        attempts: Mutex<Vec<YtAttempt>>,
        available: bool,
    }
    impl YtDlp for Fake {
        fn available(&self) -> bool {
            self.available
        }
        fn run(&self, a: &YtAttempt, _: &CancellationToken) -> Result<(), String> {
            self.attempts.lock().unwrap().push(a.clone());
            if a.url.contains("BBBBBBBBBBB") {
                return Err(
                    "ERROR: [youtube] BBBBBBBBBBB: Sign in to confirm you're not a bot".into(),
                );
            }
            let p = PathBuf::from(a.outtmpl.to_string_lossy().replace("%(ext)s", "mp3"));
            fs::write(p, format!("ID3fake-mp3-for-{}", a.url)).map_err(|e| e.to_string())
        }
    }
    fn fixture() -> Value {
        serde_json::from_str(include_str!("../../../fixtures/audio.json")).unwrap()
    }
    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }
    fn normalized_skip(s: &str) -> String {
        if s.is_empty() {
            return String::new();
        }
        s.replace("\r\n", "\n")
            .lines()
            .map(|l| {
                if l.starts_with("# --- ") {
                    "# --- TIME ---"
                } else {
                    l
                }
            })
            .collect::<Vec<_>>()
            .join("\n")
            + "\n"
    }
    #[test]
    fn fixture_download_flows() {
        let f = fixture();
        for row in f["flows"].as_array().unwrap() {
            let td = tempfile::tempdir().unwrap();
            let urls = row["urls"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_str().unwrap().to_owned())
                .collect::<Vec<_>>();
            let audio = td.path().join("audio");
            fs::create_dir_all(&audio).unwrap();
            for name in row["preexisting"].as_array().unwrap() {
                fs::write(audio.join(name.as_str().unwrap()), b"old").unwrap();
            }
            let fake = Fake {
                attempts: Mutex::new(Vec::new()),
                available: true,
            };
            let got = download_youtube_audio(
                &urls,
                td.path(),
                td.path(),
                "https://cyoa.example/",
                &AudioConfig {
                    enabled: true,
                    explicit_cookies: None,
                    browser_cookies: false,
                },
                &fake,
                &CancellationToken::new(),
            );
            let got: serde_json::Map<String, Value> = got
                .into_iter()
                .map(|(u, p)| (u, Value::String(p)))
                .collect();
            assert_eq!(Value::Object(got), row["result"]);
            let files = fs::read_dir(&audio)
                .unwrap()
                .filter_map(Result::ok)
                .map(|e| {
                    let n = e.file_name().to_string_lossy().to_string();
                    (
                        format!("audio/{n}"),
                        Value::String(hex(&fs::read(e.path()).unwrap())),
                    )
                })
                .collect::<serde_json::Map<_, _>>();
            assert_eq!(Value::Object(files), row["files"]);
            let skip = td.path().join("skipped_youtube_audio.txt");
            let actual = if skip.exists() {
                normalized_skip(&fs::read_to_string(skip).unwrap())
            } else {
                String::new()
            };
            assert_eq!(actual, normalized_skip(row["skip_log"].as_str().unwrap()));
            let expected_attempts = row["attempts"].as_array().unwrap();
            let attempts = fake.attempts.lock().unwrap();
            assert_eq!(attempts.len(), expected_attempts.len());
            for (a, e) in attempts.iter().zip(expected_attempts) {
                assert_eq!(a.public_clients, e["extractor_args"].is_object());
                assert!(a.cookiefile.is_none());
            }
        }
    }
    #[test]
    fn fixture_disabled_flow() {
        let f = fixture();
        let row = &f["disabled"];
        let td = tempfile::tempdir().unwrap();
        let urls = row["urls"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x.as_str().unwrap().to_owned())
            .collect::<Vec<_>>();
        let fake = Fake {
            attempts: Mutex::new(Vec::new()),
            available: true,
        };
        let got = download_youtube_audio(
            &urls,
            td.path(),
            td.path(),
            "https://cyoa.example/",
            &AudioConfig {
                enabled: false,
                explicit_cookies: None,
                browser_cookies: false,
            },
            &fake,
            &CancellationToken::new(),
        );
        assert!(got.is_empty());
        assert!(fake.attempts.lock().unwrap().is_empty());
        let actual = normalized_skip(
            &fs::read_to_string(td.path().join("skipped_youtube_audio.txt")).unwrap(),
        );
        assert_eq!(actual, normalized_skip(row["skip_log"].as_str().unwrap()));
    }
}

#[cfg(test)]
mod host_tests {
    #[test]
    fn only_real_media_hosts_reach_ytdlp() {
        for (u, ok) in [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", true),
            ("https://youtu.be/dQw4w9WgXcQ", true),
            ("https://m.soundcloud.com/a/b", true),
            ("http://127.0.0.1:8080/youtube.com/a", false),
            ("https://evil.example/www.youtube.com/watch?v=x", false),
            ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", false),
            ("file:///youtube.com/x", false),
        ] {
            assert_eq!(super::is_media_host(u), ok, "{u}");
        }
    }
}
