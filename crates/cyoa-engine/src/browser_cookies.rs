//! Browser cookie import (F-045, owner decision D3).
//!
//! Python reads installed-browser cookies with yt-dlp's native extractor
//! (`cookiesfrombrowser`) and `browser_cookie3`. The Rust port asks the
//! external `yt-dlp` executable to export the cookie jar of a browser profile
//! (`--cookies-from-browser BROWSER[:PROFILE] --cookies FILE`) into a private
//! temporary file that is parsed and deleted immediately, so this crate never
//! decrypts browser databases itself. Access controls are never bypassed: a
//! locked or app-bound-encrypted database simply yields no cookies, as in
//! Python.
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// Per-export private folder; removed (with the cookie jar) on drop.
struct PrivateDir(std::path::PathBuf);

impl PrivateDir {
    fn create() -> Result<Self, String> {
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.subsec_nanos());
        let dir = std::env::temp_dir().join(format!(
            "cyoa-cookies-{}-{}-{nanos:08x}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&dir).map_err(|e| format!("could not create temp dir: {e}"))?;
        Ok(Self(dir))
    }
}

impl Drop for PrivateDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// One Netscape cookie-jar entry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BrowserCookie {
    pub domain: String,
    pub include_subdomains: bool,
    pub path: String,
    pub secure: bool,
    pub name: String,
    pub value: String,
}

/// Browser-cookie import can be switched off with
/// `CYOA_DISABLE_BROWSER_COOKIES=1`; unit-test builds never read real browser
/// profiles (Python's fixtures stub the profile lookup the same way).
pub fn enabled() -> bool {
    !cfg!(test)
        && std::env::var_os("CYOA_DISABLE_BROWSER_COOKIES").is_none_or(|v| v.is_empty() || v == "0")
}

/// Python `_ytdlp_browser_profiles`: installed profiles worth trying
/// (`None` = the browser's default profile).
pub fn browser_profiles(browser: &str) -> Vec<Option<String>> {
    if !enabled() {
        return Vec::new();
    }
    if !cfg!(windows) {
        return vec![None];
    }
    let local = std::env::var("LOCALAPPDATA").unwrap_or_default();
    let appdata = std::env::var("APPDATA").unwrap_or_default();
    let browser = browser.to_lowercase();
    let root = match browser.as_str() {
        "chrome" => Path::new(&local)
            .join("Google")
            .join("Chrome")
            .join("User Data"),
        "edge" => Path::new(&local)
            .join("Microsoft")
            .join("Edge")
            .join("User Data"),
        "brave" => Path::new(&local)
            .join("BraveSoftware")
            .join("Brave-Browser")
            .join("User Data"),
        "chromium" => Path::new(&local).join("Chromium").join("User Data"),
        "firefox" => Path::new(&appdata)
            .join("Mozilla")
            .join("Firefox")
            .join("Profiles"),
        _ => return Vec::new(),
    };
    if local.is_empty() && appdata.is_empty() || !root.is_dir() {
        return Vec::new();
    }
    let mut names: Vec<String> = std::fs::read_dir(&root)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.file_type().is_ok_and(|t| t.is_dir()))
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| n == "Default" || n.starts_with("Profile "))
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    if browser == "firefox" {
        if names.is_empty() {
            return vec![None];
        }
        return names.into_iter().map(Some).collect();
    }
    std::iter::once(None)
        .chain(names.into_iter().filter(|n| n != "Default").map(Some))
        .collect()
}

/// yt-dlp `--cookies-from-browser` argument for a browser/profile pair.
pub fn cookies_from_browser_arg(browser: &str, profile: Option<&str>) -> String {
    match profile {
        Some(p) => format!("{browser}:{p}"),
        None => browser.to_owned(),
    }
}

/// Python `_is_cookie_database_lock_error`.
pub fn is_cookie_database_lock_error(error: Option<&str>) -> bool {
    let text = error.unwrap_or("").to_lowercase();
    text.contains("cookie database")
        && [
            "could not copy chrome cookie database",
            "could not copy chromium cookie database",
            "permission denied",
            "access is denied",
        ]
        .iter()
        .any(|m| text.contains(m))
}

/// Parse a Netscape cookie jar (as written by yt-dlp / curl). Entries with an
/// empty value are dropped (Python: an empty jar is not authentication).
pub fn parse_netscape(text: &str) -> Vec<BrowserCookie> {
    let mut out = Vec::new();
    for raw in text.lines() {
        let line = raw.trim_end_matches('\r');
        let line = match line.strip_prefix("#HttpOnly_") {
            Some(rest) => rest,
            None if line.starts_with('#') || line.trim().is_empty() => continue,
            None => line,
        };
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() < 7 || f[5].is_empty() || f[6].is_empty() {
            continue;
        }
        out.push(BrowserCookie {
            domain: f[0].to_owned(),
            include_subdomains: f[1].eq_ignore_ascii_case("TRUE"),
            path: if f[2].is_empty() {
                "/".into()
            } else {
                f[2].to_owned()
            },
            secure: f[3].eq_ignore_ascii_case("TRUE"),
            name: f[5].to_owned(),
            value: f[6..].join("\t"),
        });
    }
    out
}

/// `Cookie` header value for `url` (domain, path and secure matching), or
/// `None` when no cookie applies.
pub fn cookie_header_for(cookies: &[BrowserCookie], url: &url::Url) -> Option<String> {
    let host = url.host_str()?.trim_end_matches('.').to_lowercase();
    let path = url.path();
    let secure = url.scheme() == "https";
    let pairs: Vec<String> = cookies
        .iter()
        .filter(|c| {
            let d = c.domain.trim_start_matches('.').to_lowercase();
            let domain_ok = host == d
                || (c.domain.starts_with('.') || c.include_subdomains)
                    && host.ends_with(&format!(".{d}"));
            let path_ok = path.strip_prefix(&c.path).is_some_and(|suffix| {
                suffix.is_empty() || c.path.ends_with('/') || suffix.starts_with('/')
            });
            domain_ok && path_ok && (!c.secure || secure)
        })
        .map(|c| format!("{}={}", c.name, c.value))
        .collect();
    (!pairs.is_empty()).then(|| pairs.join("; "))
}

/// Export one browser profile's cookies through `yt-dlp`. The jar is written
/// to a private temporary directory, parsed, and deleted before returning.
pub fn export_with_ytdlp(
    ytdlp: &str,
    browser: &str,
    profile: Option<&str>,
    timeout: Duration,
) -> Result<Vec<BrowserCookie>, String> {
    if !enabled() {
        return Err("browser cookie import disabled".into());
    }
    let dir = PrivateDir::create()?;
    let jar = dir.0.join("cookies.txt");
    let mut child = Command::new(ytdlp)
        .arg("--cookies-from-browser")
        .arg(cookies_from_browser_arg(browser, profile))
        .arg("--cookies")
        .arg(&jar)
        .args([
            "--skip-download",
            "--simulate",
            "--no-warnings",
            "--",
            "about:blank",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("yt-dlp unavailable: {e}"))?;
    let mut stderr = child.stderr.take();
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(e) = stderr.as_mut() {
            let _ = std::io::Read::read_to_string(&mut std::io::Read::take(e, 64 * 1024), &mut s);
        }
        s
    });
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("yt-dlp cookie export timed out".into());
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(e) => return Err(e.to_string()),
        }
    }
    let stderr = reader.join().unwrap_or_default();
    let text = std::fs::read_to_string(&jar).unwrap_or_default();
    let cookies = parse_netscape(&text);
    // `dir` drops here and removes the jar.
    if cookies.is_empty() {
        let reason = stderr
            .lines()
            .find(|l| l.contains("ERROR"))
            .unwrap_or("no cookies exported")
            .trim()
            .to_owned();
        return Err(reason);
    }
    Ok(cookies)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const JAR: &str = "# Netscape HTTP Cookie File\n\
.example.com\tTRUE\t/\tTRUE\t0\tsid\tabc\n\
#HttpOnly_shop.example.com\tFALSE\t/cart\tFALSE\t0\tcart\t42\n\
other.org\tFALSE\t/\tFALSE\t0\tempty\t\n";

    #[test]
    fn parses_netscape_and_drops_empty_values() {
        let c = parse_netscape(JAR);
        assert_eq!(c.len(), 2);
        assert_eq!(c[0].name, "sid");
        assert!(c[0].secure);
        assert_eq!(c[1].domain, "shop.example.com");
        assert_eq!(c[1].path, "/cart");
    }

    #[test]
    fn header_matches_domain_path_and_scheme() {
        let c = parse_netscape(JAR);
        let u = |s: &str| url::Url::parse(s).unwrap();
        assert_eq!(
            cookie_header_for(&c, &u("https://www.example.com/a")).as_deref(),
            Some("sid=abc")
        );
        assert_eq!(cookie_header_for(&c, &u("http://www.example.com/a")), None);
        assert_eq!(
            cookie_header_for(&c, &u("https://shop.example.com/cart/x")).as_deref(),
            Some("sid=abc; cart=42")
        );
        assert_eq!(cookie_header_for(&c, &u("https://example.net/")), None);
    }

    #[test]
    fn cookie_path_requires_a_slash_boundary() {
        let cookies = parse_netscape(JAR);
        let exact = url::Url::parse("https://shop.example.com/cart").unwrap();
        let sibling = url::Url::parse("https://shop.example.com/cartoon").unwrap();
        assert_eq!(
            cookie_header_for(&cookies, &exact).as_deref(),
            Some("sid=abc; cart=42")
        );
        assert_eq!(
            cookie_header_for(&cookies, &sibling).as_deref(),
            Some("sid=abc")
        );
    }

    #[test]
    fn lock_error_detection_matches_python() {
        assert!(is_cookie_database_lock_error(Some(
            "ERROR: Could not copy Chrome cookie database. See https://..."
        )));
        assert!(!is_cookie_database_lock_error(Some(
            "ERROR: Failed to decrypt with DPAPI"
        )));
        assert!(!is_cookie_database_lock_error(None));
    }

    #[test]
    fn profile_argument() {
        assert_eq!(cookies_from_browser_arg("chrome", None), "chrome");
        assert_eq!(
            cookies_from_browser_arg("edge", Some("Profile 1")),
            "edge:Profile 1"
        );
    }
}
