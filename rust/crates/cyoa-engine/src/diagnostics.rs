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
    if let Some(p) = first_executable(&[
        "chrome",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "msedge",
        "firefox",
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
