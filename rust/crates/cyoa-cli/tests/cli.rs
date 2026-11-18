#![allow(clippy::unwrap_used, clippy::expect_used)]
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::{Duration, Instant},
};

static NEXT: AtomicU64 = AtomicU64::new(0);
fn temp_dir() -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "cyoa-cli-test-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&path).unwrap();
    path
}
/// Per-test isolated profile (tests run in parallel; a shared home lets runs
/// race on the image cache and settings.json). Stable within one test thread.
fn test_home() -> PathBuf {
    let name = thread::current()
        .name()
        .unwrap_or("unnamed")
        .replace("::", "_");
    std::env::temp_dir().join(format!("cyoa-cli-test-home-{}-{name}", std::process::id()))
}
fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
        .env("USERPROFILE", test_home())
        .env("HOME", test_home())
        .args(args)
        .output()
        .unwrap()
}

#[test]
fn help_succeeds() {
    let out = cli(&["--help"]);
    assert!(out.status.success());
    assert!(String::from_utf8_lossy(&out.stdout).contains("Download and process a CYOA project"));
}
#[test]
fn rejects_invalid_url() {
    let out = cli(&["not-a-url"]);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("invalid URL"));
}
#[test]
fn rejects_output_file() {
    let dir = temp_dir();
    let file = dir.join("not-a-directory");
    fs::write(&file, "file").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
        .env("USERPROFILE", test_home())
        .env("HOME", test_home())
        .args(["https://example.com/project.json", "--output"])
        .arg(file)
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("output"));
}

/// Mock server thread, stopped explicitly by `join` (no idle timeout: under
/// a loaded parallel test run the gap between two CLI runs can exceed any
/// fixed idle window, which made `both_mode_*` flaky).
struct ServerHandle {
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    thread: thread::JoinHandle<()>,
}

impl ServerHandle {
    fn join(self) -> thread::Result<()> {
        self.stop.store(true, Ordering::Relaxed);
        self.thread.join()
    }
}

fn mock_server() -> (String, ServerHandle) {
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop_flag = stop.clone();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}/project.json", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let worker = thread::spawn(move || {
        let started = Instant::now();
        // Hard cap only as a safety net if a test never joins.
        while !stop_flag.load(Ordering::Relaxed) && started.elapsed() < Duration::from_secs(120) {
            match listener.accept() {
                Ok((mut stream, _)) => {
                    // Accepted sockets inherit non-blocking mode on Windows; a
                    // WouldBlock read would be misparsed as a request for "/".
                    let _ = stream.set_nonblocking(false);
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                    let mut request = [0u8; 4096];
                    let n = stream.read(&mut request).unwrap_or(0);
                    let line = String::from_utf8_lossy(&request[..n]);
                    let path = line.split_whitespace().nth(1).unwrap_or("/");
                    let (status, content_type, body): (&str, &str, &[u8]) = match path {
                        "/project.json" => ("200 OK", "application/json", br#"{"rows":[{"image":"/a.png"},{"image":"/b.png"},{"image":"/missing.png"}]}"#),
                        "/a.png" => ("200 OK", "image/png", b"first image bytes"),
                        "/b.png" => ("200 OK", "image/png", b"second image bytes"),
                        _ => ("404 Not Found", "text/plain", b"not found"),
                    };
                    let header = format!(
                        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = stream.write_all(header.as_bytes());
                    let _ = stream.write_all(body);
                    let _ = stream.flush();
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10))
                }
                Err(_) => break,
            }
        }
    });
    (
        base,
        ServerHandle {
            stop,
            thread: worker,
        },
    )
}

#[test]
fn both_mode_embeds_and_zips_without_overwriting() {
    let output_dir = temp_dir();
    let (url, worker) = mock_server();
    for _ in 0..2 {
        let out = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
            .env("USERPROFILE", test_home())
            .env("HOME", test_home())
            .args(["--both", "--threads", "2", "--wait", "0", "--output"])
            .arg(&output_dir)
            .arg(&url)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    worker.join().unwrap();
    let embedded = fs::read_to_string(output_dir.join("project.json.json")).unwrap();
    assert!(embedded.contains("data:image/png;base64,"));
    assert!(embedded.contains("/missing.png"));
    assert!(output_dir.join("project.json_1.json").exists());
    assert!(output_dir.join("project.json_original.json").exists());
    assert!(output_dir.join("project.json_metadata.json").exists());
    assert!(output_dir.join("failed_images.txt").exists());
    assert!(output_dir.join("failed_assets.txt").exists());
    let archives: Vec<_> = fs::read_dir(&output_dir)
        .unwrap()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "zip"))
        .collect();
    assert_eq!(archives.len(), 2);
    let bytes = fs::read(&archives[0]).unwrap();
    let archive = String::from_utf8_lossy(&bytes);
    assert!(archive.contains("images/a.png"));
    assert!(archive.contains("images/b.png"));
    assert!(archive.contains("project.json"));
}

#[test]
fn list_mode_rejects_missing_file_and_reports_unsupported_modes() {
    let home = test_home();
    let out = temp_dir();
    let missing = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
        .env("USERPROFILE", &home)
        .env("HOME", &home)
        .args(["-L"])
        .arg(out.join("nope.txt"))
        .arg("-o")
        .arg(&out)
        .output()
        .unwrap();
    assert_eq!(missing.status.code(), Some(2));

    let list = out.join("list.txt");
    fs::write(
        &list,
        "# c\nhttps://a.example/x | Name | icc_folder\nnot a url\n",
    )
    .unwrap();
    let run = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
        .env("USERPROFILE", &home)
        .env("HOME", &home)
        .arg("-L")
        .arg(&list)
        .arg("-o")
        .arg(&out)
        .output()
        .unwrap();
    assert_eq!(run.status.code(), Some(1));
    let raw = fs::read_to_string(out.join("failed_urls.txt")).unwrap();
    // Python appends in text mode: CRLF on Windows, LF elsewhere.
    if cfg!(windows) {
        assert!(!raw.replace("\r\n", "").contains('\n'), "bare LF: {raw:?}");
    }
    let log = raw.replace("\r\n", "\n");
    assert!(log.starts_with("# Failed batch URL downloads\n# Format: url<TAB>error_message\n\n"));
    assert!(log.contains(
        "https://a.example/x\tmode 'website_folder' is not supported by the Rust port yet\n"
    ));
}

#[test]
fn network_flags_are_persisted_to_settings_like_python() {
    let home = temp_dir();
    let out = temp_dir();
    let settings = home.join(".cyoa_downloader").join("settings.json");
    let run = |extra: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
            .env("USERPROFILE", &home)
            .env("HOME", &home)
            .arg("http://127.0.0.1:9/p/project.json")
            .arg("-o")
            .arg(&out)
            .args(extra)
            .output()
            .unwrap()
    };
    // Invalid configuration: rejected before anything is saved.
    let bad = run(&["--vpn-policy", "bogus", "--dns", "1.1.1.1"]);
    assert_eq!(bad.status.code(), Some(2));
    assert!(
        !settings.exists(),
        "settings written despite invalid config"
    );

    run(&[
        "--proxy-mode",
        "disabled",
        "--proxy-bypass",
        "a.test",
        "--dns",
        "1.1.1.1",
        "--dns-port",
        "5353",
        "--no-dns-ipv6",
        "--vpn-policy",
        "require",
        "--vpn-interface",
        "zz-no-such-if",
    ]);
    let saved: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&settings).unwrap()).unwrap();
    assert_eq!(saved["proxy_mode"], "disabled");
    assert_eq!(saved["proxy_no_proxy"], "a.test");
    assert_eq!(saved["dns"], "1.1.1.1");
    assert_eq!(saved["dns_protocol"], "udp");
    assert_eq!(saved["dns_port"], 5353);
    assert_eq!(saved["dns_ipv6"], false);
    assert_eq!(saved["vpn_policy"], "require");
    assert_eq!(saved["vpn_interface"], "zz-no-such-if");
}

#[test]
fn cyoa_manager_registers_embedded_json_and_zip() {
    use cyoa_integrations::cyoa_manager::list_projects;
    let home = test_home();
    let out = temp_dir();
    let db = out.join("library.sqlite3");
    fs::write(&db, b"").unwrap(); // Python requires the configured DB to exist.
    let settings_dir = home.join(".cyoa_downloader");
    fs::create_dir_all(&settings_dir).unwrap();
    fs::write(
        settings_dir.join("settings.json"),
        serde_json::json!({ "cyoa_mgr_db_path": db.to_string_lossy() }).to_string(),
    )
    .unwrap();
    for mode in ["--zip", "--both"] {
        let (url, worker) = mock_server();
        let run = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
            .env("USERPROFILE", &home)
            .env("HOME", &home)
            .args([mode, "--cyoa-manager", "-w", "0", "--output"])
            .arg(&out)
            .arg(&url)
            .arg("game")
            .output()
            .unwrap();
        worker.join().unwrap();
        assert!(
            run.status.success(),
            "{}",
            String::from_utf8_lossy(&run.stderr)
        );
    }
    let projects = list_projects(Some(&db));
    assert_eq!(projects.len(), 2, "{projects:?}");
    // ZIP: extracted beside the archive; both: the embedded JSON.
    assert!(projects.iter().all(|p| p.name == "game"));
    assert!(projects.iter().any(|p| {
        p.file_path.ends_with("project.json")
            && std::path::Path::new(&p.file_path)
                .parent()
                .unwrap()
                .ends_with("game_manager")
    }));
    assert!(projects.iter().any(|p| p.file_path.ends_with("game.json")));
    assert!(
        projects
            .iter()
            .all(|p| p.project_json_url.ends_with("/project.json"))
    );
}
