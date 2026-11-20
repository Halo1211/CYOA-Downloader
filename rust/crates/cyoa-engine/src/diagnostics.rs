//! F-051: `--dependency-check` and `--self-test` (Python
//! `diagnostics/dependency_check.py`, `diagnostics/self_test.py`).
//!
//! Intentional difference: the Rust binary has no Python modules to probe.
//! The dependency report keeps Python's layout and summary lines but lists
//! the capabilities compiled into the binary plus the external programs the
//! Rust port actually runs. The self-test keeps Python's output format and
//! exercises the Rust implementations of the same safety contracts.

use std::path::{Path, PathBuf};

const RULE: &str = "========================================================================";
const THIN: &str = "------------------------------------------------------------------------";

/// `shutil.which`, then the conventional Windows install folders (Python
/// `_first_executable`).
pub fn first_executable(names: &[&str]) -> Option<PathBuf> {
    for name in names {
        if let Some(p) = crate::itch::which(name) {
            return Some(p);
        }
    }
    if cfg!(windows) {
        let env = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_owned());
        let roots = [
            env("PROGRAMFILES", r"C:\Program Files"),
            env("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            env("LOCALAPPDATA", ""),
        ];
        for name in names {
            let rels: &[&str] = match *name {
                "7z" => &[r"7-Zip\7z.exe"],
                "7zz" => &[r"7-Zip\7zz.exe"],
                "unrar" | "UnRAR" => &[r"WinRAR\UnRAR.exe", r"UnRAR\UnRAR.exe"],
                _ => &[],
            };
            for rel in rels {
                for root in roots.iter().filter(|r| !r.is_empty()) {
                    let c = Path::new(root).join(rel);
                    if c.is_file() {
                        return Some(c);
                    }
                }
            }
        }
    }
    None
}

/// Python `_installed_browser` (PATH, then common Windows locations).
pub fn installed_browser() -> Option<PathBuf> {
    installed_chromium().or_else(|| first_executable(&["firefox"]))
}

/// A Chromium-family browser (the CDP browser strategy cannot drive Firefox).
pub fn installed_chromium() -> Option<PathBuf> {
    if let Some(p) = first_executable(&[
        "chrome",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "msedge",
    ]) {
        return Some(p);
    }
    if !cfg!(windows) {
        return None;
    }
    let env = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_owned());
    let local = env("LOCALAPPDATA", "");
    let pf = env("PROGRAMFILES", r"C:\Program Files");
    let pf86 = env("PROGRAMFILES(X86)", r"C:\Program Files (x86)");
    [
        Path::new(&local).join(r"Google\Chrome\Application\chrome.exe"),
        Path::new(&local).join(r"Microsoft\Edge\Application\msedge.exe"),
        Path::new(&local).join(r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        Path::new(&pf).join(r"Google\Chrome\Application\chrome.exe"),
        Path::new(&pf86).join(r"Google\Chrome\Application\chrome.exe"),
        Path::new(&pf86).join(r"Microsoft\Edge\Application\msedge.exe"),
    ]
    .into_iter()
    .find(|c| c.is_file())
}

/// Python `_ffmpeg_install_guide`.
pub fn ffmpeg_install_guide() -> &'static str {
    "FFMPEG install guide:\n  Windows : winget install Gyan.FFmpeg  OR  choco install ffmpeg; then reopen Terminal.\n  Linux   : sudo apt install ffmpeg  OR  sudo dnf install ffmpeg  OR  sudo pacman -S ffmpeg.\n  macOS   : brew install ffmpeg.\n  Verify  : ffmpeg -version\n  Note    : Missing ffmpeg only disables/limits media conversion features that need it; normal JSON/asset downloads continue."
}

fn row(status: &str, name: &str, group: &str, detail: &str) -> String {
    format!("{status:7}  {name:18}  {group:22}  {detail}")
}

/// Python `dependency_check_report` (Rust capability set; see module docs).
pub fn dependency_check_report() -> String {
    let mut lines = vec![
        format!(
            "CYOA Downloader v{} dependency check",
            cyoa_core::app_version()
        ),
        RULE.to_owned(),
        "Runtime mode: native Rust executable".to_owned(),
    ];
    // Built into the binary: always present.
    let builtin: &[(&str, &str, &str)] = &[
        (
            "HTTP client",
            "required",
            "Core HTTP downloader (reqwest/rustls)",
        ),
        (
            "HTML parser",
            "required",
            "HTML/ICC parsing (BeautifulSoup-compatible port)",
        ),
        (
            "JSON5 parser",
            "optional-parser",
            "Lenient JSON5 parsing fallback",
        ),
        (
            "CSV/XLSX import",
            "optional-batch",
            "CSV/TXT/XLSX/XLS queue import",
        ),
        (
            "SOCKS proxy",
            "optional-network",
            "SOCKS4/5 and SOCKS5h proxy transport",
        ),
        ("Custom DNS", "optional-network", "UDP/TCP/DoT/DoH resolver"),
        (
            "OS keyring",
            "optional-security",
            "Keyring storage for secrets",
        ),
        (
            "ZIP backend",
            "required",
            "ZIP read/write with validation limits",
        ),
        ("SQLite", "required", "Checkpoints and CYOA Manager library"),
    ];
    let mut ok = 0;
    let mut total = 0;
    for (name, group, purpose) in builtin {
        total += 1;
        ok += 1;
        lines.push(row("OK", name, group, &format!("{purpose} [built in]")));
    }
    // External programs the Rust port runs.
    let externals: [(&str, &[&str], &str, &str, &str); 6] = [
        (
            "yt-dlp",
            &["yt-dlp"],
            "optional-media",
            "YouTube/SoundCloud and supported media download",
            "pip install yt-dlp  OR  winget install yt-dlp.yt-dlp",
        ),
        (
            "YouTube JS runtime",
            &["deno", "node"],
            "optional-media-cli",
            "JavaScript runtime used by yt-dlp for YouTube",
            "winget install DenoLand.Deno",
        ),
        (
            "gallery-dl",
            &["gallery-dl"],
            "optional-fallback",
            "Gallery/post downloader fallback",
            "pip install gallery-dl",
        ),
        (
            "RAR helper",
            &["tar", "unrar", "UnRAR", "7z"],
            "optional-viewer",
            ".rar offline viewer import (system tar)",
            "Windows 10+ includes tar.exe; or winget install 7zip.7zip",
        ),
        (
            "ffmpeg",
            &["ffmpeg"],
            "optional-media-cli",
            "Media conversion/merge used by yt-dlp",
            "winget install Gyan.FFmpeg",
        ),
        (
            "Browser (CDP)",
            &[],
            "optional-headless",
            "Chrome/Edge for the browser strategy",
            "Install Chrome or Edge",
        ),
    ];
    for (name, exes, group, purpose, install) in externals {
        total += 1;
        let found = if exes.is_empty() {
            installed_browser()
        } else if name == "YouTube JS runtime" {
            let found: Vec<&str> = exes
                .iter()
                .copied()
                .filter(|e| crate::itch::which(e).is_some())
                .collect();
            if found.is_empty() {
                None
            } else {
                Some(PathBuf::from(found.join(", ")))
            }
        } else {
            first_executable(exes)
        };
        match found {
            Some(p) => {
                ok += 1;
                lines.push(row("OK", name, group, &p.display().to_string()));
            }
            None => {
                lines.push(row("MISSING", name, group, purpose));
                lines.push(row("", "", "install", install));
            }
        }
    }
    lines.push(THIN.to_owned());
    lines.push(format!(
        "Installed modules/capabilities: {ok}/{total} detected"
    ));
    lines.push("Required modules: OK".to_owned());
    lines.push("Optional modules are only needed when the related feature is enabled.".to_owned());
    lines.push(ffmpeg_install_guide().to_owned());
    lines.push(crate::itch::itch_backend_status());
    lines.join("\n")
}

struct SelfTest {
    lines: Vec<String>,
    passed: usize,
    total: usize,
}

impl SelfTest {
    fn check(&mut self, name: &str, result: Result<String, String>) {
        self.total += 1;
        match result {
            Ok(detail) => {
                self.passed += 1;
                self.lines.push(if detail.is_empty() {
                    format!("PASS  {name}")
                } else {
                    format!("PASS  {name} - {detail}")
                });
            }
            Err(e) => self.lines.push(format!("FAIL  {name} - {e}")),
        }
    }
}

fn ensure(cond: bool, msg: &str) -> Result<(), String> {
    if cond { Ok(()) } else { Err(msg.to_owned()) }
}

/// Python `run_internal_self_test`: offline smoke tests in a temp folder.
/// PASS / WARN / FAIL counts of [`runtime_report`].
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct DiagCounts {
    pub pass: usize,
    pub warn: usize,
    pub fail: usize,
}

/// Python `build_diagnostic_report` (Diagnostics Center): one line per check
/// as `"<STATUS:4>  <name:28>  <detail>"`, a header and a count footer.
/// Python-interpreter checks (modules, frozen bundle, Selenium/Playwright)
/// are replaced by the Rust port's built-in components and CDP browser.
/// Network checks contact example.com (DNS) and Google's generate_204.
pub fn runtime_report(
    output_dir: &Path,
    check_network: bool,
    language: &str,
) -> (String, DiagCounts) {
    let is_id = language.to_lowercase().starts_with("id");
    let mut lines: Vec<String> = Vec::new();
    let mut counts = DiagCounts::default();
    let mut add = |status: &str, name: &str, detail: &str| {
        match status {
            "PASS" => counts.pass += 1,
            "WARN" => counts.warn += 1,
            _ => counts.fail += 1,
        }
        lines.push(
            format!("{status:4}  {name:28}  {detail}")
                .trim_end()
                .to_owned(),
        );
    };
    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_default();
    add(
        "PASS",
        "Runtime mode",
        &format!("native Rust executable: {exe}"),
    );
    for (name, detail) in [
        ("component: HTTP client", "built in (reqwest/rustls)"),
        ("component: HTML parser", "built in"),
        ("component: JSON5 parser", "built in"),
        ("component: CSV/XLSX import", "built in"),
        ("component: SOCKS proxy", "built in"),
        ("component: custom DNS", "built in (UDP/TCP/DoT/DoH)"),
        ("component: OS keyring", "built in"),
        ("component: ZIP backend", "built in"),
        ("component: SQLite", "built in"),
        ("component: HTTP/2", "built in"),
    ] {
        add("PASS", name, detail);
    }
    match first_executable(&["deno", "node"]) {
        Some(_) => {
            let found: Vec<&str> = ["deno", "node"]
                .into_iter()
                .filter(|n| first_executable(&[n]).is_some())
                .collect();
            add("PASS", "YouTube JavaScript runtime", &found.join(", "));
        }
        None => add(
            "WARN",
            "YouTube JavaScript runtime",
            "Deno/Node not found; YouTube audio may fail",
        ),
    }
    match installed_chromium() {
        Some(p) => add("PASS", "Browser (CDP)", &p.display().to_string()),
        None => add(
            "WARN",
            "Browser (CDP)",
            "Chrome/Edge not found; Install: winget install Google.Chrome",
        ),
    }
    match first_executable(&["tar", "unrar", "UnRAR", "7z"]) {
        Some(p) => add("PASS", "RAR extraction helper", &p.display().to_string()),
        None => add(
            "WARN",
            "RAR extraction helper",
            "unrar/7z not found; ZIP still works. Install: winget install 7zip.7zip",
        ),
    }
    match first_executable(&["yt-dlp"]) {
        Some(p) => add("PASS", "yt-dlp CLI (optional)", &p.display().to_string()),
        None => add(
            "WARN",
            "yt-dlp CLI (optional)",
            "not on PATH; YouTube audio is unavailable",
        ),
    }
    match first_executable(&["gallery-dl"]) {
        Some(p) => add(
            "PASS",
            "gallery-dl CLI (optional)",
            &p.display().to_string(),
        ),
        None => add(
            "WARN",
            "gallery-dl CLI (optional)",
            "not on PATH; gallery fallback is unavailable",
        ),
    }
    match first_executable(&["ffmpeg"]) {
        Some(p) => add("PASS", "FFmpeg", &p.display().to_string()),
        None => add(
            "WARN",
            "FFmpeg",
            "not found; YouTube MP3 conversion is unavailable",
        ),
    }
    match crate::itch::detect_itch_backend() {
        (Some(_), label) => add("PASS", "itch-dl backend", &label),
        (None, _) => add(
            "WARN",
            "itch-dl backend",
            "not found (install uv/pipx or `pip install itch-dl`)",
        ),
    }
    let probe = |dir: &Path| -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;
        let path = dir.join(format!(".cyoa_diag_probe.{}.tmp", std::process::id()));
        std::fs::write(&path, "ok")?;
        std::fs::remove_file(&path)
    };
    if output_dir.as_os_str().is_empty() {
        add("WARN", "Output folder", "no output folder selected yet");
    } else {
        match probe(output_dir) {
            Ok(()) => add(
                "PASS",
                "Output folder writable",
                &output_dir.display().to_string(),
            ),
            Err(e) => add(
                "FAIL",
                "Output folder writable",
                &format!("{}: {e}", output_dir.display()),
            ),
        }
    }
    let cache_dir =
        cyoa_storage::cache::ImageCache::default_root().unwrap_or_else(std::env::temp_dir);
    match probe(&cache_dir) {
        Ok(()) => add(
            "PASS",
            "Cache folder writable",
            &cache_dir.display().to_string(),
        ),
        Err(e) => add("WARN", "Cache folder writable", &e.to_string()),
    }
    let settings_path = cyoa_storage::settings::default_path();
    let mut store = cyoa_storage::settings::SettingsStore::new(&settings_path);
    let st = match store.load() {
        Ok(s) => {
            add("PASS", "settings.json valid", &format!("{} keys", s.len()));
            s.clone()
        }
        Err(e) => {
            add(
                "WARN",
                "settings.json valid",
                &format!("could not load: {e}"),
            );
            cyoa_storage::settings::defaults()
        }
    };
    if let Some(dir) = settings_path.parent() {
        let _ = std::fs::create_dir_all(dir);
        add(
            if dir.is_dir() { "PASS" } else { "FAIL" },
            "Settings folder",
            &dir.display().to_string(),
        );
    }
    if settings_path.exists() {
        add(
            "PASS",
            "settings.json path",
            &settings_path.display().to_string(),
        );
    } else {
        add(
            "WARN",
            "settings.json path",
            &format!("not created yet: {}", settings_path.display()),
        );
    }
    match cyoa_storage::history::default_history_path().filter(|p| p.exists()) {
        Some(p) => add("PASS", "History file", &p.display().to_string()),
        None => add("WARN", "History file", "not created yet"),
    }
    let safe_keys = [
        "enable_deep_scan",
        "enable_selenium_fallback",
        "enable_serve_preview",
        "enable_cheat_panel",
        "enable_itch_downloader",
        "gallery_dl_mode",
        "auto_detect_output",
        "gallery_dl_path",
        "ai_mode",
        "ai_provider",
        "cloudflare_mode",
        "use_http2",
        "download_fonts",
        "download_youtube_audio",
    ];
    let visible: Vec<String> = safe_keys
        .iter()
        .filter(|k| !cyoa_storage::settings::is_secret_setting_key(k))
        .filter_map(|k| {
            st.get(*k).map(|v| {
                let mut text = match v {
                    serde_json::Value::String(s) => s.clone(),
                    serde_json::Value::Bool(true) => "True".into(),
                    serde_json::Value::Bool(false) => "False".into(),
                    serde_json::Value::Null => "None".into(),
                    other => other.to_string(),
                };
                if text.chars().count() > 80 {
                    text = text.chars().take(77).collect::<String>() + "...";
                }
                format!("{k}={text}")
            })
        })
        .collect();
    add(
        "PASS",
        "Feature settings",
        &if visible.is_empty() {
            "defaults".to_owned()
        } else {
            visible.join("; ")
        },
    );
    let text = |k: &str| {
        st.get(k)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .trim()
            .to_owned()
    };
    let proxy_configured = match text("proxy_mode").as_str() {
        "manual" => {
            !(text("proxy").is_empty()
                && text("proxy_http").is_empty()
                && text("proxy_https").is_empty())
        }
        "disabled" => false,
        _ => [
            "HTTPS_PROXY",
            "https_proxy",
            "HTTP_PROXY",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
        ]
        .iter()
        .any(|k| std::env::var(k).is_ok_and(|v| !v.is_empty())),
    };
    add(
        if proxy_configured { "PASS" } else { "WARN" },
        "Proxy setting",
        if proxy_configured {
            "configured"
        } else {
            "not configured"
        },
    );
    let limit = st
        .get("image_cache_max_mb")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(2048);
    match cyoa_storage::cache::ImageCache::open(&cache_dir, limit) {
        Ok(cache) => {
            let s = cache.stats();
            add(
                "PASS",
                "Image cache",
                &format!(
                    "{} entries, {} MB at {}",
                    s.entries,
                    s.size_bytes / (1024 * 1024),
                    cache_dir.display()
                ),
            );
        }
        Err(e) => add("WARN", "Image cache", &format!("probe error: {e}")),
    }
    let mut cfg = text("gallery_dl_config");
    if cfg.is_empty() {
        cfg = if cfg!(windows) {
            let base = std::env::var("APPDATA").unwrap_or_else(|_| {
                format!(
                    "{}\\AppData\\Roaming",
                    std::env::var("USERPROFILE").unwrap_or_default()
                )
            });
            format!("{base}\\gallery-dl\\config.json")
        } else {
            format!(
                "{}/.config/gallery-dl/config.json",
                std::env::var("HOME").unwrap_or_default()
            )
        };
    }
    let cfg_path = std::path::absolute(PathBuf::from(&cfg)).unwrap_or_else(|_| PathBuf::from(&cfg));
    if cfg_path.exists() {
        match std::fs::read_to_string(&cfg_path)
            .map_err(|e| e.to_string())
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).map_err(|e| e.to_string()))
        {
            Ok(_) => add(
                "PASS",
                "gallery-dl config",
                &format!("valid JSON: {}", cfg_path.display()),
            ),
            Err(e) => add(
                "WARN",
                "gallery-dl config",
                &format!("exists but invalid JSON: {e}"),
            ),
        }
    } else {
        add(
            "WARN",
            "gallery-dl config",
            &format!("not found: {}", cfg_path.display()),
        );
    }
    if !output_dir.as_os_str().is_empty() {
        let found: Vec<&str> = [
            "backup_report.txt",
            "failed_assets.txt",
            "failed_images.txt",
            "skipped_youtube_audio.txt",
            "cyoa_downloader.log",
        ]
        .into_iter()
        .filter(|n| output_dir.join(n).exists())
        .collect();
        add(
            if found.is_empty() { "WARN" } else { "PASS" },
            "Output reports",
            &if found.is_empty() {
                "no report files found yet".to_owned()
            } else {
                found.join(", ")
            },
        );
    }
    if check_network {
        use std::net::ToSocketAddrs;
        match ("example.com", 443).to_socket_addrs() {
            Ok(_) => add("PASS", "DNS resolution", "example.com resolved"),
            Err(e) => add("FAIL", "DNS resolution", &format!("failed: {e}")),
        }
        match internet_probe() {
            Ok(status) if status == 204 || status == 200 => {
                add("PASS", "Internet connectivity", &format!("HTTP {status}"));
            }
            Ok(status) => add(
                "WARN",
                "Internet connectivity",
                &format!("unexpected HTTP {status}"),
            ),
            Err(e) => add(
                "FAIL",
                "Internet connectivity",
                &format!("no connection: {e}"),
            ),
        }
    } else {
        add("WARN", "Network checks", "skipped by request");
    }
    let rule_eq = "=".repeat(52);
    let rule_dash = "-".repeat(52);
    let header = if is_id {
        format!("Diagnostik CYOA Downloader v{}", cyoa_core::app_version())
    } else {
        format!("CYOA Downloader v{} diagnostics", cyoa_core::app_version())
    };
    let footer = format!(
        "PASS: {}   WARN: {}   FAIL: {}",
        counts.pass, counts.warn, counts.fail
    );
    let mut all = vec![header, rule_eq];
    all.extend(lines);
    all.push(rule_dash);
    all.push(footer);
    (all.join("\n"), counts)
}

/// GET https://www.google.com/generate_204 (8 s), on a private runtime.
fn internet_probe() -> Result<u16, String> {
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| e.to_string())?;
    let client = cyoa_net::client::FetchClient::new(cyoa_net::client::ClientConfig {
        total_timeout: Some(std::time::Duration::from_secs(8)),
        ..Default::default()
    })
    .map_err(|e| e.to_string())?;
    let url = url::Url::parse("https://www.google.com/generate_204").map_err(|e| e.to_string())?;
    let opts = cyoa_net::client::FetchOptions {
        return_error_response: true,
        ..Default::default()
    };
    let cancel = tokio_util::sync::CancellationToken::new();
    rt.block_on(async {
        client
            .get(&url, &opts, &cancel)
            .await
            .map(|r| r.status.as_u16())
            .map_err(|e| e.to_string())
    })
}

pub fn run_internal_self_test() -> (bool, String) {
    let mut t = SelfTest {
        lines: vec![
            format!(
                "CYOA Downloader v{} internal self-test",
                cyoa_core::app_version()
            ),
            "====================================================".to_owned(),
        ],
        passed: 0,
        total: 0,
    };
    let tmp = std::env::temp_dir().join(format!("cyoa_selftest_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    if let Err(e) = std::fs::create_dir_all(&tmp) {
        return (
            false,
            format!("self-test could not create a temp folder: {e}"),
        );
    }
    let root = tmp.to_string_lossy().into_owned();

    t.check(
        "safe_join normal path",
        cyoa_core::paths::safe_join(&root, "assets/image.png", "file")
            .map(|p| p.display().to_string())
            .map_err(|e| e.to_string()),
    );
    t.check(
        "archive traversal rejection",
        match cyoa_core::paths::safe_archive_rel_path("../evil.txt", false) {
            Err(_) => Ok("rejected as expected".into()),
            Ok(p) => Err(format!("accepted {p}")),
        },
    );
    t.check(
        "safe_join traversal rejection",
        match cyoa_core::paths::safe_join(&root, "../../outside.txt", "file") {
            Ok(p) if !p.starts_with(&tmp) => Err(format!("escaped to {}", p.display())),
            _ => Ok("contained".into()),
        },
    );
    t.check(
        "failure summary writes failed_assets.txt",
        (|| {
            std::fs::write(tmp.join("broken_assets_report.html"), "old")
                .map_err(|e| e.to_string())?;
            let item = crate::reports::FailedItem {
                url: "https://example.com/a.png",
                path: "a.png",
                error: "HTTP 404",
                kind: "image",
            };
            crate::reports::write_asset_failure_summary(
                &[item],
                &tmp,
                "https://example.com/",
                "self-test",
            )
            .map_err(|e| e.to_string())?;
            ensure(
                tmp.join("failed_assets.txt").is_file(),
                "failed_assets.txt missing",
            )?;
            Ok(tmp.join("failed_assets.txt").display().to_string())
        })(),
    );
    t.check(
        "stale broken_assets_report removed",
        ensure(
            !tmp.join("broken_assets_report.html").exists(),
            "still present",
        )
        .map(|()| String::new()),
    );
    t.check(
        "merge-copy preserves existing assets",
        (|| {
            let (src, dst) = (tmp.join("merge_src"), tmp.join("merge_dst"));
            std::fs::create_dir_all(&src).map_err(|e| e.to_string())?;
            std::fs::create_dir_all(&dst).map_err(|e| e.to_string())?;
            std::fs::write(src.join("new.png"), "n").map_err(|e| e.to_string())?;
            std::fs::write(dst.join("keep.png"), "k").map_err(|e| e.to_string())?;
            let r = cyoa_core::paths::copytree_merge_safe(&src, &dst, "self-test");
            ensure(
                dst.join("keep.png").is_file() && dst.join("new.png").is_file(),
                "merge lost a file",
            )?;
            Ok(format!("copied={}", r.copied))
        })(),
    );
    let report = dependency_check_report();
    t.check(
        "dependency report generated",
        Ok(report.lines().next().unwrap_or_default().to_owned()),
    );
    t.check(
        "dependency report includes ffmpeg guide",
        ensure(report.contains("FFMPEG install guide"), "guide missing").map(|()| String::new()),
    );
    t.check(
        "unsafe URL schemes rejected",
        (|| {
            for u in [
                "file:///etc/passwd",
                "javascript:alert(1)",
                "data:text/html,x",
            ] {
                let url = url::Url::parse(u).map_err(|e| e.to_string())?;
                let policy = cyoa_net::policy::DestinationPolicy {
                    allow_internal: false,
                    origin: None,
                };
                ensure(policy.check(&url).is_err(), &format!("{u} accepted"))?;
            }
            Ok("file/javascript/data".into())
        })(),
    );
    t.check(
        "SSRF internal-host guard",
        (|| {
            for h in [
                "127.0.0.1",
                "169.254.169.254",
                "10.0.0.1",
                "localhost",
                "[::1]",
            ] {
                ensure(
                    cyoa_net::policy::host_is_internal(h.trim_matches(['[', ']'])),
                    &format!("{h} not blocked"),
                )?;
            }
            ensure(
                !cyoa_net::policy::host_is_internal("8.8.8.8"),
                "public host blocked",
            )?;
            Ok("loopback/link-local/private blocked".into())
        })(),
    );
    t.check(
        "manifest round-trip: write / intact / corruption detected",
        (|| {
            let pkg = tmp.join("pkg");
            std::fs::create_dir_all(&pkg).map_err(|e| e.to_string())?;
            std::fs::write(pkg.join("project.json"), "{}").map_err(|e| e.to_string())?;
            let (wrote, _) = crate::verify::write_package_manifest(&pkg);
            let (intact, _) = crate::verify::verify_output_package(&pkg);
            std::fs::write(pkg.join("project.json"), "{\"x\":1}").map_err(|e| e.to_string())?;
            let (after, _) = crate::verify::verify_output_package(&pkg);
            ensure(wrote && intact && !after, "manifest contract broken")?;
            Ok(format!(
                "wrote={wrote} intact={intact} corruption_detected={}",
                !after
            ))
        })(),
    );
    t.check(
        "decode robustness: arbitrary bytes + utf-8 fidelity",
        (|| {
            let arbitrary: Vec<u8> = (0..=255u8).collect();
            let _ = cyoa_parse::project::try_decode_bytes(&arbitrary, "");
            let s = "été 日本 ✓";
            ensure(
                cyoa_parse::project::try_decode_bytes(s.as_bytes(), "") == s,
                "utf-8 changed",
            )?;
            Ok("arbitrary_ok=true utf8_roundtrip=true".into())
        })(),
    );
    t.check(
        "settings export redacts secrets",
        (|| {
            let path = tmp.join("settings.json");
            let mut store = cyoa_storage::settings::SettingsStore::new(&path);
            store.load().map_err(|e| e.to_string())?;
            store
                .values_mut()
                .insert("proxy".into(), "http://user:pw@proxy.example:8080".into());
            store.save().map_err(|e| e.to_string())?;
            let out = tmp.join("export.json");
            let (ok, msg) = cyoa_storage::settings::export_settings_file(&mut store, &out);
            ensure(ok, &msg)?;
            let text = std::fs::read_to_string(&out).map_err(|e| e.to_string())?;
            ensure(!text.contains("user:pw"), "proxy credentials exported")?;
            Ok(String::new())
        })(),
    );
    t.check(
        "ZIP validation rejects traversal members",
        (|| {
            let zp = tmp.join("evil.zip");
            let mut z = zip::ZipWriter::new(std::fs::File::create(&zp).map_err(|e| e.to_string())?);
            z.start_file("../evil.txt", zip::write::SimpleFileOptions::default())
                .map_err(|e| e.to_string())?;
            z.finish().map_err(|e| e.to_string())?;
            let r = cyoa_storage::archive::validate_zip_path(
                &zp,
                cyoa_storage::archive::ArchiveLimits::default(),
            );
            ensure(r.is_err(), "traversal member accepted")?;
            Ok(String::new())
        })(),
    );
    t.check(
        "log redaction hides credentials",
        (|| {
            let red =
                crate::logfile::redact("token=abcdef123456 https://user:secret@host.example/x");
            ensure(!red.contains("secret"), &red)?;
            Ok(String::new())
        })(),
    );

    let _ = std::fs::remove_dir_all(&tmp);
    t.lines
        .push("----------------------------------------------------".into());
    t.lines
        .push(format!("Result: {}/{} passed", t.passed, t.total));
    (t.passed == t.total, t.lines.join("\n"))
}

#[cfg(test)]
mod tests {
    #[test]
    fn self_test_passes() {
        let (ok, text) = super::run_internal_self_test();
        assert!(ok, "{text}");
        assert!(text.contains("Result: "));
    }
}
