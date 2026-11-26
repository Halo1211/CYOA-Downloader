use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use cyoa_net::client::{FetchClient, FetchOptions};
use serde_json::Value;
use tokio_util::sync::CancellationToken;
use url::Url;

const KEYRING_SERVICE: &str = "cyoa_downloader_itch";
const KEYRING_USER: &str = "itch_api_key";
const HTML5_MARKER: &str = ".cyoa_itch_archive.json";

pub fn is_itch_url(url: &str) -> bool {
    Url::parse(url).ok().is_some_and(|parsed| {
        matches!(parsed.scheme(), "http" | "https")
            && parsed.host_str().is_some_and(|host| {
                let host = host.to_ascii_lowercase();
                host == "itch.io"
                    || host == "itch.zone"
                    || host.ends_with(".itch.io")
                    || host.ends_with(".itch.zone")
            })
    })
}

pub fn build_itch_command(
    prefix: &[String],
    page_url: &str,
    dest: &str,
    api_key: Option<&str>,
    mirror_web: bool,
    parallel: i32,
) -> Result<Vec<String>, String> {
    if !(1..=16).contains(&parallel) {
        return Err("itch-dl parallel must be between 1 and 16".into());
    }
    let mut command = prefix.to_vec();
    command.extend([page_url.to_owned(), "--download-to".into(), dest.into()]);
    if mirror_web {
        command.push("--mirror-web".into());
    }
    if parallel > 1 {
        command.extend(["--parallel".into(), parallel.to_string()]);
    }
    if let Some(key) = api_key.filter(|key| !key.is_empty()) {
        command.extend(["--api-key".into(), key.into()]);
    }
    Ok(command)
}

pub fn redact_itch_command(command: &[String]) -> String {
    let mut output = Vec::with_capacity(command.len());
    let mut secrets = Vec::new();
    let mut skip_next = false;
    for token in command {
        if skip_next {
            secrets.push(token.as_str());
            output.push("***".to_owned());
            skip_next = false;
        } else if token == "--api-key" || token == "--api_key" {
            output.push(token.clone());
            skip_next = true;
        } else {
            output.push(token.clone());
        }
    }
    let mut safe = output.join(" ");
    // Python joins first, then replaces each secret everywhere, including inside option names.
    for secret in secrets.into_iter().filter(|secret| !secret.is_empty()) {
        safe = safe.replace(secret, "***");
    }
    safe
}

pub fn redact_output(message: &str, api_key: Option<&str>) -> String {
    match api_key.filter(|key| !key.is_empty()) {
        Some(key) => message.replace(key, "***"),
        None => message.to_owned(),
    }
}

pub(crate) fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    #[cfg(windows)]
    let extensions: Vec<String> = std::env::var("PATHEXT")
        .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into())
        .split(';')
        .map(str::to_owned)
        .collect();
    #[cfg(not(windows))]
    let extensions = vec![String::new()];
    for dir in std::env::split_paths(&path) {
        for extension in &extensions {
            let suffix = if name.contains('.') {
                String::new()
            } else {
                extension.clone()
            };
            let candidate = dir.join(format!("{name}{suffix}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

#[cfg(windows)]
fn no_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_command: &mut Command) {}

fn probe(command: &[String]) -> bool {
    for argument in ["--help", "--version"] {
        let Some(program) = command.first() else {
            return false;
        };
        let mut child = Command::new(program);
        child.args(&command[1..]).arg(argument).stdin(Stdio::null());
        child.stdout(Stdio::piped()).stderr(Stdio::piped());
        no_window(&mut child);
        let Ok(mut child) = child.spawn() else {
            continue;
        };
        // Drain the pipes on threads: a descendant that inherits stdout can keep
        // it open after the launcher exits, and `wait_with_output` would then
        // block past the 25 s deadline.
        let (tx, rx) = std::sync::mpsc::channel::<Vec<u8>>();
        for pipe in [
            child
                .stdout
                .take()
                .map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
            child
                .stderr
                .take()
                .map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
        ]
        .into_iter()
        .flatten()
        {
            let tx = tx.clone();
            thread::spawn(move || {
                let mut pipe = pipe;
                let mut buf = Vec::new();
                let _ = std::io::Read::read_to_end(&mut pipe, &mut buf);
                let _ = tx.send(buf);
            });
        }
        drop(tx);
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Some(status),
                Ok(None) if start.elapsed() < Duration::from_secs(25) => {
                    thread::sleep(Duration::from_millis(50));
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
            }
        };
        let Some(status) = status else { continue };
        let mut collected = Vec::new();
        let deadline = start + Duration::from_secs(27);
        while let Some(left) = deadline.checked_duration_since(Instant::now()) {
            match rx.recv_timeout(left) {
                Ok(chunk) => {
                    collected.extend_from_slice(&chunk);
                    collected.push(b'\n');
                }
                Err(_) => break,
            }
        }
        let text = String::from_utf8_lossy(&collected).to_lowercase();
        if matches!(status.code(), Some(0..=2))
            && text.contains("itch-dl")
            && (text.contains("--download-to") || text.contains("version"))
        {
            return true;
        }
    }
    false
}

pub fn detect_itch_backend() -> (Option<Vec<String>>, String) {
    if let Some(path) = which("itch-dl") {
        let command = vec![path.to_string_lossy().into_owned()];
        if probe(&command) {
            return (Some(command), "itch-dl (PATH)".into());
        }
    }
    if let Some(path) = which("uvx") {
        let command = vec![path.to_string_lossy().into_owned(), "itch-dl".into()];
        if probe(&command) {
            return (Some(command), "uvx itch-dl".into());
        }
    }
    if let Some(path) = which("pipx") {
        let command = vec![
            path.to_string_lossy().into_owned(),
            "run".into(),
            "itch-dl".into(),
        ];
        if probe(&command) {
            return (Some(command), "pipx run itch-dl".into());
        }
    }
    (None, "not found".into())
}

pub fn itch_backend_status() -> String {
    let (command, label) = detect_itch_backend();
    if command.is_some() {
        format!("itch-dl backend: AVAILABLE via {label}")
    } else {
        "itch-dl backend: NOT FOUND. Install one of: `uv` (uvx), `pipx`, or `pip install itch-dl`. See https://github.com/DragoonAethis/itch-dl".into()
    }
}

pub trait ItchKeyring: Send + Sync {
    fn get_password(&self, service: &str, user: &str) -> Option<String>;
}

/// The OS keyring (Python `keyring.get_password`); errors read as absent.
pub struct OsItchKeyring;

impl ItchKeyring for OsItchKeyring {
    fn get_password(&self, service: &str, user: &str) -> Option<String> {
        cyoa_storage::secrets::native_keyring()
            .get_password(service, user)
            .ok()
            .flatten()
    }
}

pub struct ResolvedKey {
    pub key: Option<String>,
    pub source: &'static str,
}

pub fn resolve_itch_api_key(
    explicit_key: Option<&str>,
    keyring: Option<&dyn ItchKeyring>,
) -> ResolvedKey {
    if let Some(key) = explicit_key.filter(|key| !key.is_empty()) {
        return ResolvedKey {
            key: Some(key.into()),
            source: "session",
        };
    }
    if let Ok(key) = std::env::var("ITCH_API_KEY") {
        let key = key.trim();
        if !key.is_empty() {
            return ResolvedKey {
                key: Some(key.into()),
                source: "env",
            };
        }
    }
    let settings_path = cyoa_storage::settings::default_path();
    let settings: Value = fs::read_to_string(settings_path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or(Value::Null);
    let storage = settings
        .get("itch_key_storage")
        .and_then(Value::as_str)
        .unwrap_or("session");
    if storage == "keyring"
        && let Some(key) = keyring
            .and_then(|api| api.get_password(KEYRING_SERVICE, KEYRING_USER))
            .filter(|key| !key.is_empty())
    {
        return ResolvedKey {
            key: Some(key),
            source: "keyring",
        };
    }
    let plain = settings
        .get("itch_api_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    if !plain.is_empty() {
        if storage != "plain" {
            tracing::warn!(
                "itch.io key found in plaintext settings.json. Install 'keyring' and set itch_key_storage=keyring for safer storage."
            );
        }
        return ResolvedKey {
            key: Some(plain.into()),
            source: "plain",
        };
    }
    ResolvedKey {
        key: None,
        source: "none",
    }
}

#[derive(Debug, thiserror::Error)]
pub enum RunError {
    #[error("itch-dl cancelled")]
    Cancelled,
    #[error("itch-dl timed out (1h limit).")]
    Timeout,
    #[error("{0}")]
    Io(#[from] std::io::Error),
}

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);
fn temp_output() -> std::io::Result<(PathBuf, File)> {
    let id = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let path =
        std::env::temp_dir().join(format!("cyoa-itch-{}-{stamp}-{id}.log", std::process::id()));
    let file = OpenOptions::new()
        .write(true)
        .read(true)
        .create_new(true)
        .open(&path)?;
    Ok((path, file))
}

fn terminate(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

pub fn run_itch_process(
    command: &[String],
    cancel: &CancellationToken,
) -> Result<(i32, String), RunError> {
    let Some(program) = command.first() else {
        return Err(
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "empty itch-dl command").into(),
        );
    };
    let (path, output) = temp_output()?;
    let mut child_command = Command::new(program);
    child_command
        .args(&command[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::from(output.try_clone()?))
        .stderr(Stdio::from(output));
    no_window(&mut child_command);
    let mut child = child_command.spawn()?;
    let started = Instant::now();
    let status = loop {
        if cancel.is_cancelled() {
            terminate(&mut child);
            let _ = fs::remove_file(&path);
            return Err(RunError::Cancelled);
        }
        if started.elapsed() >= Duration::from_secs(3600) {
            terminate(&mut child);
            let _ = fs::remove_file(&path);
            return Err(RunError::Timeout);
        }
        match child.try_wait()? {
            Some(status) => break status,
            None => thread::sleep(Duration::from_millis(250)),
        }
    };
    let mut file = File::open(&path)?;
    let len = file.metadata()?.len();
    file.seek(SeekFrom::Start(len.saturating_sub(4096)))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    let _ = fs::remove_file(&path);
    Ok((
        status.code().unwrap_or(-1),
        String::from_utf8_lossy(&bytes).into_owned(),
    ))
}

pub trait ItchBackend: Send + Sync {
    fn detect(&self) -> (Option<Vec<String>>, String);
    fn run(&self, cmd: &[String], cancel: &CancellationToken) -> Result<(i32, String), String>;
}

pub struct SystemItchBackend;
impl ItchBackend for SystemItchBackend {
    fn detect(&self) -> (Option<Vec<String>>, String) {
        detect_itch_backend()
    }
    fn run(&self, cmd: &[String], cancel: &CancellationToken) -> Result<(i32, String), String> {
        run_itch_process(cmd, cancel).map_err(|error| error.to_string())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ItchResult {
    pub ok: bool,
    pub saved: usize,
    pub failed: usize,
    pub existing: usize,
    pub skipped_auth: bool,
    pub backend: String,
    pub returncode: Option<i32>,
    pub message: String,
    /// HTML5 ZIP preparation (itch.py `offline_*` keys).
    pub offline_ready: usize,
    pub offline_cached: usize,
    pub offline_failed: usize,
    pub offline_encrypted: usize,
    pub offline_entries: Vec<PathBuf>,
}

fn snapshot(root: &Path) -> HashMap<String, (u64, u128)> {
    fn walk(root: &Path, current: &Path, found: &mut HashMap<String, (u64, u128)>) {
        let Ok(entries) = fs::read_dir(current) else {
            return;
        };
        let entries: Vec<_> = entries.flatten().collect();
        let skip: Vec<PathBuf> = entries
            .iter()
            .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
            .filter(|entry| entry.path().join(HTML5_MARKER).is_file())
            .map(|entry| entry.path())
            .collect();
        for entry in entries {
            let path = entry.path();
            if skip.contains(&path) {
                continue;
            }
            if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                walk(root, &path, found);
            } else if let Ok(metadata) = entry.metadata() {
                let modified = metadata
                    .modified()
                    .ok()
                    .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                    .map_or(0, |d| d.as_nanos());
                if let Ok(relative) = path.strip_prefix(root) {
                    found.insert(
                        relative.to_string_lossy().replace('\\', "/"),
                        (metadata.len(), modified),
                    );
                }
            }
        }
    }
    let mut found = HashMap::new();
    walk(root, root, &mut found);
    found
}

/// itch.py 410-444: expand every saved HTML5 ZIP into an offline folder.
fn prepare_html5_archives(
    dest: &Path,
    after: &HashMap<String, (u64, u128)>,
    page_url: &str,
    fetcher: &dyn crate::offline_viewer::AssetFetcher,
    result: &mut ItchResult,
    cancel: &CancellationToken,
) {
    use crate::itch_html5::{Html5Error, materialize_itch_html5_archive};
    let mut relatives: Vec<&String> = after.keys().collect();
    relatives.sort();
    for relative in relatives {
        if cancel.is_cancelled() {
            result.ok = false;
            result.message = "itch.io: cancelled while preparing HTML5 archives.".into();
            return;
        }
        if !relative.to_lowercase().ends_with(".zip") {
            continue;
        }
        // Snapshot keys rewrite `\` to `/`; on Unix a file literally named
        // `..\..\x.zip` would become a traversal. Never leave `dest`.
        if relative
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
        {
            continue;
        }
        let archive = dest.join(relative);
        match materialize_itch_html5_archive(&archive, page_url, fetcher) {
            Ok((None, _, _)) => {}
            Ok((Some(entry), reused, complete)) => {
                if !complete {
                    result.offline_failed += 1;
                    tracing::warn!(
                        "[itch] HTML5 offline assets incomplete: {}",
                        entry.display()
                    );
                } else if reused {
                    result.offline_cached += 1;
                    tracing::info!("[itch] HTML5 offline entry: {}", entry.display());
                } else {
                    result.offline_ready += 1;
                    tracing::info!("[itch] HTML5 offline entry: {}", entry.display());
                }
                result.offline_entries.push(entry);
            }
            Err(Html5Error::Encrypted) => {
                result.offline_encrypted += 1;
                tracing::warn!("[itch] ZIP needs a password; original retained: {relative}");
            }
            Err(e) => {
                result.offline_failed += 1;
                tracing::warn!("[itch] HTML5 offline preparation failed for {relative}: {e}");
            }
        }
    }
    if result.offline_failed > 0 {
        result.ok = false;
        result.failed = result.offline_failed;
    }
    if !result.offline_entries.is_empty()
        || result.offline_failed > 0
        || result.offline_encrypted > 0
    {
        result.message.push_str(&format!(
            " HTML5 offline: {} prepared, {} reused, {} failed, {} password-protected ZIP(s) left intact.",
            result.offline_ready, result.offline_cached, result.offline_failed, result.offline_encrypted
        ));
    }
}

/// itch.py `download_itch_assets`. `html5` is Python's `prepare_html5`:
/// `Some(fetcher)` expands HTML5 ZIPs into reusable offline folders.
#[allow(clippy::too_many_arguments)]
pub fn download_itch_assets(
    page_url: &str,
    output_dir: &Path,
    explicit_key: Option<&str>,
    mirror_web: bool,
    parallel: i32,
    cancel: &CancellationToken,
    backend: &dyn ItchBackend,
    html5: Option<&dyn crate::offline_viewer::AssetFetcher>,
) -> Result<ItchResult, RunError> {
    download_itch_assets_with(
        page_url,
        output_dir,
        mirror_web,
        parallel,
        cancel,
        backend,
        &|| resolve_itch_api_key(explicit_key, Some(&OsItchKeyring)),
        html5,
    )
}

/// `download_itch_assets` with an injectable key resolver (tests never
/// read the user's settings or keyring).
#[allow(clippy::too_many_arguments)]
pub fn download_itch_assets_with(
    page_url: &str,
    output_dir: &Path,
    mirror_web: bool,
    parallel: i32,
    cancel: &CancellationToken,
    backend: &dyn ItchBackend,
    resolve_key: &dyn Fn() -> ResolvedKey,
    html5: Option<&dyn crate::offline_viewer::AssetFetcher>,
) -> Result<ItchResult, RunError> {
    let mut result = ItchResult::default();
    if !is_itch_url(page_url) {
        result.message = "Not an itch.io URL.".into();
        return Ok(result);
    }
    let (prefix, label) = backend.detect();
    result.backend = label.clone();
    let Some(prefix) = prefix else {
        result.message = "itch-dl backend not found. Install one of: `uv` (provides uvx), `pipx`, or `pip install itch-dl`. See https://github.com/DragoonAethis/itch-dl".into();
        tracing::warn!("[itch] {}", result.message);
        return Ok(result);
    };
    let resolved = resolve_key();
    let dest = output_dir.join("itch_assets");
    if let Err(error) = fs::create_dir_all(&dest) {
        result.message = format!("Cannot create itch_assets folder: {error}");
        return Ok(result);
    }
    let dest_text = dest.to_string_lossy();
    let command = match build_itch_command(
        &prefix,
        page_url,
        &dest_text,
        resolved.key.as_deref(),
        mirror_web,
        parallel,
    ) {
        Ok(command) => command,
        Err(error) => {
            result.message = error;
            return Ok(result);
        }
    };
    tracing::info!(
        "[itch] running: {} (key source: {})",
        redact_itch_command(&command),
        resolved.source
    );
    let before = snapshot(&dest);
    match backend.run(&command, cancel) {
        Ok((code, output_tail)) => {
            result.returncode = Some(code);
            let after = snapshot(&dest);
            result.saved = after
                .iter()
                .filter(|(path, meta)| before.get(*path) != Some(*meta))
                .count();
            result.existing = after.len().saturating_sub(result.saved);
            if code == 0 {
                if after.is_empty() {
                    result.failed = 1;
                    result.message = format!(
                        "itch-dl exited successfully via {label}, but no files were saved in itch_assets/. Check whether this page offers downloadable files."
                    );
                } else {
                    result.ok = true;
                    result.message = format!(
                        "itch.io: completed via {label}; {} new/updated, {} existing file(s) in itch_assets/ (key source: {}).",
                        result.saved, result.existing, resolved.source
                    );
                    if let Some(fetcher) = html5 {
                        prepare_html5_archives(
                            &dest,
                            &after,
                            page_url,
                            fetcher,
                            &mut result,
                            cancel,
                        );
                    }
                }
            } else {
                result.failed = 1;
                result.skipped_auth = resolved.key.is_none();
                result.message = format!(
                    "itch-dl exited with code {code} via {label}; {} file(s) saved. {}Details logged.",
                    result.saved,
                    if resolved.key.is_none() {
                        "(no API key — some projects need auth) "
                    } else {
                        ""
                    }
                );
                if !output_tail.trim().is_empty() {
                    tracing::warn!(
                        "[itch] itch-dl output tail:\n{}",
                        redact_output(
                            &output_tail
                                .chars()
                                .rev()
                                .take(600)
                                .collect::<String>()
                                .chars()
                                .rev()
                                .collect::<String>(),
                            resolved.key.as_deref()
                        )
                    );
                }
            }
        }
        Err(_error) if cancel.is_cancelled() => return Err(RunError::Cancelled),
        Err(error) if error.contains("timed out") => {
            result.message = "itch-dl timed out (1h limit).".into();
            tracing::warn!("[itch] {}", result.message);
        }
        Err(error) => {
            result.message = format!(
                "itch-dl run error: {}",
                redact_output(&error, resolved.key.as_deref())
            );
            tracing::warn!("[itch] {}", result.message);
        }
    }
    Ok(result)
}

pub async fn itch_test_connection(
    explicit_key: Option<&str>,
    client: &FetchClient,
    backend: &dyn ItchBackend,
) -> (bool, String) {
    let (command, label) = backend.detect();
    let backend_line = if command.is_some() {
        format!("backend: {label}")
    } else {
        "backend: NOT FOUND (install uv/pipx or `pip install itch-dl`)".into()
    };
    let resolved = resolve_itch_api_key(explicit_key, Some(&OsItchKeyring));
    let cancel = CancellationToken::new();
    let Ok(url) = Url::parse(if resolved.key.is_some() {
        // Deviation: Python's `itch.io/api/1/key/me?api_key=` now answers
        // `{"errors": ...}` for valid keys (verified 2026-11-21). The current
        // API takes the key as a Bearer token, which also keeps it out of URLs.
        "https://api.itch.io/profile"
    } else {
        "https://itch.io/"
    }) else {
        return (
            false,
            format!("itch.io probe error: invalid endpoint; {backend_line}."),
        );
    };
    let mut headers = cyoa_net::client::header::HeaderMap::new();
    if let Some(key) = resolved.key.as_deref()
        && let Ok(mut value) =
            cyoa_net::client::header::HeaderValue::from_str(&format!("Bearer {key}"))
    {
        value.set_sensitive(true);
        headers.insert(cyoa_net::client::header::AUTHORIZATION, value);
    }
    let response = client
        .get(
            &url,
            &FetchOptions {
                headers,
                return_error_response: true,
                ..FetchOptions::default()
            },
            &cancel,
        )
        .await;
    match response {
        Ok(response) => {
            let status = response.status.as_u16();
            if resolved.key.is_some() {
                let body = FetchClient::text(response, 1024 * 1024)
                    .await
                    .unwrap_or_default();
                let parsed: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
                if status == 200 && parsed.get("user").is_some_and(Value::is_object) {
                    let user = parsed
                        .pointer("/user/username")
                        .and_then(Value::as_str)
                        .unwrap_or("?");
                    (
                        command.is_some(),
                        format!(
                            "itch.io auth OK as '{user}' (key source: {}); {backend_line}.",
                            resolved.source
                        ),
                    )
                } else {
                    (
                        false,
                        format!(
                            "itch.io auth failed (HTTP {status}). Check the API key (source: {}); {backend_line}.",
                            resolved.source
                        ),
                    )
                }
            } else if status < 400 {
                (
                    false,
                    format!(
                        "itch.io reachable; {backend_line}. An itch.io API key is required by itch-dl to download."
                    ),
                )
            } else {
                (
                    false,
                    format!("itch.io not reachable (HTTP {status}); {backend_line}."),
                )
            }
        }
        Err(error) => (
            false,
            format!(
                "itch.io probe error: {}; {backend_line}.",
                redact_output(&error.to_string(), resolved.key.as_deref())
            ),
        ),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    #[test]
    fn fixture_urls_commands_redaction_and_parallel_match_python() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/itch.json")).unwrap();
        for row in fixture["urls"].as_array().unwrap() {
            assert_eq!(
                is_itch_url(row[0].as_str().unwrap()),
                row[1].as_bool().unwrap()
            );
        }
        for row in fixture["commands"].as_array().unwrap() {
            let prefix = row["prefix"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned())
                .collect::<Vec<_>>();
            let key = row["key"].as_str();
            let actual = build_itch_command(
                &prefix,
                "https://dev.itch.io/g",
                "OUT/itch_assets",
                key,
                row["mirror"].as_bool().unwrap(),
                row["parallel"].as_i64().unwrap() as i32,
            )
            .unwrap();
            assert_eq!(
                actual,
                serde_json::from_value::<Vec<String>>(row["cmd"].clone()).unwrap()
            );
            assert_eq!(
                redact_itch_command(&actual),
                row["redacted"].as_str().unwrap()
            );
        }
        for row in fixture["bad_parallel"].as_array().unwrap() {
            assert_eq!(
                build_itch_command(&[], "", "", None, false, row[0].as_i64().unwrap() as i32)
                    .unwrap_err(),
                row[1].as_str().unwrap()
            );
        }
        for row in fixture["redact_extra"].as_array().unwrap() {
            let cmd = serde_json::from_value::<Vec<String>>(row[0].clone()).unwrap();
            assert_eq!(redact_itch_command(&cmd), row[1].as_str().unwrap());
        }
    }

    struct FakeBackend {
        code: i32,
        create_file: bool,
        calls: Mutex<usize>,
    }
    impl ItchBackend for FakeBackend {
        fn detect(&self) -> (Option<Vec<String>>, String) {
            (Some(vec!["fake-itch".into()]), "fake".into())
        }
        fn run(
            &self,
            cmd: &[String],
            _cancel: &CancellationToken,
        ) -> Result<(i32, String), String> {
            *self.calls.lock().unwrap() += 1;
            if self.create_file {
                let dest = Path::new(
                    cmd.iter()
                        .position(|value| value == "--download-to")
                        .map(|index| &cmd[index + 1])
                        .unwrap(),
                );
                // itch-dl skips files it already has.
                if !dest.join("game.zip").exists() {
                    fs::write(dest.join("game.zip"), b"zip").map_err(|error| error.to_string())?;
                }
            }
            Ok((self.code, "diagnostic".into()))
        }
    }

    #[test]
    fn fake_backend_download_tracks_new_and_existing_files() {
        let output = tempfile::tempdir().unwrap();
        let backend = FakeBackend {
            code: 0,
            create_file: true,
            calls: Mutex::new(0),
        };
        let first = download_itch_assets(
            "https://dev.itch.io/game",
            output.path(),
            Some("key"),
            false,
            1,
            &CancellationToken::new(),
            &backend,
            None,
        )
        .unwrap();
        let second = download_itch_assets(
            "https://dev.itch.io/game",
            output.path(),
            Some("key"),
            false,
            1,
            &CancellationToken::new(),
            &backend,
            None,
        )
        .unwrap();
        assert_eq!((first.saved, first.existing), (1, 0));
        assert_eq!((second.saved, second.existing), (0, 1));
    }

    #[test]
    fn fake_backend_empty_and_auth_failure_messages_match_python() {
        let output = tempfile::tempdir().unwrap();
        let empty = FakeBackend {
            code: 0,
            create_file: false,
            calls: Mutex::new(0),
        };
        let result = download_itch_assets(
            "https://dev.itch.io/game",
            output.path(),
            Some("key"),
            false,
            1,
            &CancellationToken::new(),
            &empty,
            None,
        )
        .unwrap();
        assert_eq!(
            result.message,
            "itch-dl exited successfully via fake, but no files were saved in itch_assets/. Check whether this page offers downloadable files."
        );
        let failed = FakeBackend {
            code: 2,
            create_file: false,
            calls: Mutex::new(0),
        };
        let result = download_itch_assets_with(
            "https://dev.itch.io/game",
            output.path(),
            false,
            1,
            &CancellationToken::new(),
            &failed,
            &|| ResolvedKey {
                key: None,
                source: "none",
            },
            None,
        )
        .unwrap();
        assert!(result.skipped_auth);
        assert!(
            result
                .message
                .contains("(no API key — some projects need auth) Details logged.")
        );
    }
}
