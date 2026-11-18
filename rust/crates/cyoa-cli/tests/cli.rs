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
fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
        .env(
            "USERPROFILE",
            std::env::temp_dir().join("cyoa-cli-test-home"),
        )
        .env("HOME", std::env::temp_dir().join("cyoa-cli-test-home"))
        .args(args)
        .output()
        .unwrap()
}

#[test]
fn help_succeeds() {
    let out = cli(&["--help"]);
    assert!(out.status.success());
    assert!(String::from_utf8_lossy(&out.stdout).contains("Download and package"));
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
        .env(
            "USERPROFILE",
            std::env::temp_dir().join("cyoa-cli-test-home"),
        )
        .env("HOME", std::env::temp_dir().join("cyoa-cli-test-home"))
        .args(["https://example.com/project.json", "--output"])
        .arg(file)
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("output"));
}

fn mock_server() -> (String, thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}/project.json", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let worker = thread::spawn(move || {
        let started = Instant::now();
        let mut last_request = Instant::now();
        while started.elapsed() < Duration::from_secs(30)
            && last_request.elapsed() < Duration::from_secs(3)
        {
            match listener.accept() {
                Ok((mut stream, _)) => {
                    last_request = Instant::now();
                    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
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
    (base, worker)
}

#[test]
fn both_mode_embeds_and_zips_without_overwriting() {
    let output_dir = temp_dir();
    let (url, worker) = mock_server();
    for _ in 0..2 {
        let out = Command::new(env!("CARGO_BIN_EXE_cyoa-downloader"))
            .env(
                "USERPROFILE",
                std::env::temp_dir().join("cyoa-cli-test-home"),
            )
            .env("HOME", std::env::temp_dir().join("cyoa-cli-test-home"))
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
