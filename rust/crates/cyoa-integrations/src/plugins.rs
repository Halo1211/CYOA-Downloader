//! F-044 plugins (owner decision D4, approved 2026-11-20): Python plugins
//! were in-process callables (`integrations/plugins.py`); Rust runs external
//! executables declared in `~/.cyoa_downloader/plugins/plugins.json`.
//!
//! Manifest: `{"plugins": [{"name": "...", "kind": "asset_scanner" |
//! "engine_detector", "command": ["exe", "arg", ...]}]}`. Relative
//! executables resolve inside the plugins folder. Each call starts the
//! process directly (no shell), writes one JSON request to stdin and reads
//! one JSON reply from stdout (capped, with a timeout; the process is killed
//! on timeout). Failures are isolated and logged at debug like Python.
//!
//! Requests and replies:
//! - asset scanner: `{"kind":"asset_scanner","text":…,"file_url":…,
//!   "base_url":…,"file_ext":…}` → `{"urls": ["…", …]}`
//! - engine detector: `{"kind":"engine_detector","html":…,"mode":…}` →
//!   `{"result": {…} | null}`
//!
//! Built-in scanners/detectors are native Rust and always run first.

use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

/// Largest reply read from a plugin.
const MAX_REPLY_BYTES: usize = 4 * 1024 * 1024;
/// Per-call time limit.
const CALL_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PluginKind {
    AssetScanner,
    EngineDetector,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plugin {
    pub name: String,
    pub kind: PluginKind,
    pub command: Vec<String>,
}

/// Registered plugins in manifest order (`_PluginRegistry`).
#[derive(Debug, Clone)]
pub struct PluginRegistry {
    dir: PathBuf,
    plugins: Vec<Plugin>,
    /// Per-call time limit (default 20 s).
    pub timeout: Duration,
}

/// `~/.cyoa_downloader/plugins`.
pub fn default_dir() -> Option<PathBuf> {
    cyoa_storage::settings::default_path()
        .parent()
        .map(|p| p.join("plugins"))
}

impl PluginRegistry {
    /// Load `plugins.json` from `dir`; a missing file means no plugins.
    /// Invalid entries and duplicate names (Python refuses re-registration
    /// without `override`) are skipped with a warning.
    pub fn load(dir: &Path) -> Self {
        let mut reg = Self {
            dir: dir.to_path_buf(),
            plugins: Vec::new(),
            timeout: CALL_TIMEOUT,
        };
        let Ok(text) = std::fs::read_to_string(dir.join("plugins.json")) else {
            return reg;
        };
        let entries = match serde_json::from_str::<Value>(&text) {
            Ok(v) => v.get("plugins").and_then(Value::as_array).cloned(),
            Err(e) => {
                tracing::warn!("plugins.json is invalid: {e}");
                None
            }
        };
        for entry in entries.unwrap_or_default() {
            let name = entry.get("name").and_then(Value::as_str).unwrap_or("");
            let kind = match entry.get("kind").and_then(Value::as_str) {
                Some("asset_scanner") => PluginKind::AssetScanner,
                Some("engine_detector") => PluginKind::EngineDetector,
                _ => {
                    tracing::warn!("plugin {name:?}: unknown kind");
                    continue;
                }
            };
            let command: Vec<String> = entry
                .get("command")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_owned)
                        .collect()
                })
                .unwrap_or_default();
            if name.trim().is_empty() || command.is_empty() {
                tracing::warn!("plugin entry needs a name and a command");
                continue;
            }
            if reg.plugins.iter().any(|p| p.name == name && p.kind == kind) {
                tracing::warn!("plugin '{name}' already registered; duplicate ignored");
                continue;
            }
            reg.plugins.push(Plugin {
                name: name.to_owned(),
                kind,
                command,
            });
        }
        reg
    }

    /// The default plugins folder's registry (empty when absent).
    pub fn load_default() -> Self {
        default_dir().map_or_else(
            || Self {
                dir: PathBuf::new(),
                plugins: Vec::new(),
                timeout: CALL_TIMEOUT,
            },
            |d| Self::load(&d),
        )
    }

    pub fn is_empty(&self) -> bool {
        self.plugins.is_empty()
    }

    pub fn names(&self, kind: PluginKind) -> Vec<String> {
        self.plugins
            .iter()
            .filter(|p| p.kind == kind)
            .map(|p| p.name.clone())
            .collect()
    }

    /// `run_asset_scanner_plugins` (user plugins only): union of all valid
    /// replies; a reply with an invalid entry is discarded (Python TypeError).
    pub fn run_asset_scanners(
        &self,
        text: &str,
        file_url: &str,
        base_url: &str,
        file_ext: &str,
    ) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        let request = json!({
            "kind": "asset_scanner",
            "text": text,
            "file_url": file_url,
            "base_url": base_url,
            "file_ext": file_ext,
        });
        for p in self
            .plugins
            .iter()
            .filter(|p| p.kind == PluginKind::AssetScanner)
        {
            match self
                .call(p, &request)
                .and_then(|reply| scanner_urls(&reply))
            {
                Ok(urls) => out.extend(urls),
                Err(e) => {
                    tracing::debug!("[plugin:scanner:{}] failed on {file_url}: {e}", p.name);
                }
            }
        }
        out
    }

    /// `run_engine_detector_plugins`: the first non-empty object wins.
    pub fn run_engine_detectors(&self, html: &str, mode: &str) -> Option<Map<String, Value>> {
        let request = json!({"kind": "engine_detector", "html": html, "mode": mode});
        for p in self
            .plugins
            .iter()
            .filter(|p| p.kind == PluginKind::EngineDetector)
        {
            match self.call(p, &request) {
                Ok(reply) => match reply.get("result") {
                    Some(Value::Object(o)) if !o.is_empty() => return Some(o.clone()),
                    Some(Value::Null) | None => {}
                    Some(_) => {
                        tracing::debug!(
                            "[plugin:detector:{}] ignored non-dictionary result",
                            p.name
                        );
                    }
                },
                Err(e) => tracing::debug!("[plugin:detector:{}] failed: {e}", p.name),
            }
        }
        None
    }

    fn program(&self, command: &[String]) -> PathBuf {
        let exe = PathBuf::from(&command[0]);
        if exe.is_relative() && exe.components().count() > 1 || self.dir.join(&exe).is_file() {
            self.dir.join(exe)
        } else {
            exe
        }
    }

    /// One plugin call: spawn, send the request, read the bounded reply.
    fn call(&self, plugin: &Plugin, request: &Value) -> Result<Value, String> {
        let mut child = Command::new(self.program(&plugin.command))
            .args(&plugin.command[1..])
            .current_dir(&self.dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("could not start: {e}"))?;
        let body = serde_json::to_vec(request).map_err(|e| e.to_string())?;
        let mut stdin = child.stdin.take().ok_or("no stdin")?;
        let writer = std::thread::spawn(move || {
            let _ = stdin.write_all(&body);
            let _ = stdin.write_all(b"\n");
        });
        let mut stdout = child.stdout.take().ok_or("no stdout")?;
        let reader = std::thread::spawn(move || {
            let mut buf = Vec::new();
            let mut chunk = [0u8; 8192];
            loop {
                match stdout.read(&mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        buf.extend_from_slice(&chunk[..n]);
                        if buf.len() > MAX_REPLY_BYTES {
                            return Err("reply too large");
                        }
                    }
                }
            }
            Ok(buf)
        });
        let deadline = Instant::now() + self.timeout;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("timed out".into());
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                Err(e) => return Err(e.to_string()),
            }
        }
        let _ = writer.join();
        let bytes = reader
            .join()
            .map_err(|_| "reader panicked".to_owned())?
            .map_err(str::to_owned)?;
        let text = String::from_utf8_lossy(&bytes);
        let line = text.lines().rfind(|l| !l.trim().is_empty()).unwrap_or("");
        serde_json::from_str(line).map_err(|e| format!("invalid reply: {e}"))
    }
}

/// Validate a scanner reply (Python: an iterable of non-empty strings).
fn scanner_urls(reply: &Value) -> Result<Vec<String>, String> {
    let urls = match reply.get("urls") {
        Some(Value::Array(a)) => a,
        Some(Value::Null) | None => return Ok(Vec::new()),
        Some(_) => return Err("Asset scanner must return an iterable of URL strings".into()),
    };
    urls.iter()
        .map(|u| match u.as_str() {
            Some(s) if !s.trim().is_empty() => Ok(s.to_owned()),
            _ => Err("Asset scanner returned an invalid URL entry".to_owned()),
        })
        .collect()
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    /// A tiny plugin written as a script for the platform shell-less
    /// interpreter: Python is required by the repository's fixtures anyway.
    fn python() -> Option<String> {
        ["python", "python3"]
            .into_iter()
            .find(|p| {
                Command::new(p)
                    .arg("--version")
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .is_ok_and(|s| s.success())
            })
            .map(str::to_owned)
    }

    fn write(dir: &Path, name: &str, body: &str) {
        std::fs::write(dir.join(name), body).unwrap();
    }

    #[test]
    fn scanners_and_detectors_over_json_stdio() {
        let Some(py) = python() else {
            eprintln!("skipped: no python");
            return;
        };
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path();
        write(
            d,
            "scan.py",
            "import sys, json\nr = json.loads(sys.stdin.readline())\n\
             print(json.dumps({'urls': [r['file_url'] + '#x', 'img/' + r['file_ext']]}))\n",
        );
        write(d, "bad.py", "print('{\"urls\": [\"ok\", 5]}')\n");
        write(d, "slow.py", "import time\ntime.sleep(60)\n");
        write(
            d,
            "detect.py",
            "import sys, json\nr = json.loads(sys.stdin.readline())\n\
             print(json.dumps({'result': {'engine': 'x', 'mode': r['mode']}}))\n",
        );
        write(d, "none.py", "print('{\"result\": null}')\n");
        let manifest = json!({"plugins": [
            {"name": "scan", "kind": "asset_scanner", "command": [py, "scan.py"]},
            {"name": "scan", "kind": "asset_scanner", "command": [py, "scan.py"]},
            {"name": "bad", "kind": "asset_scanner", "command": [py, "bad.py"]},
            {"name": "missing", "kind": "asset_scanner", "command": ["no-such-plugin-exe"]},
            {"name": "none", "kind": "engine_detector", "command": [py, "none.py"]},
            {"name": "detect", "kind": "engine_detector", "command": [py, "detect.py"]},
            {"name": "weird", "kind": "other", "command": [py]}
        ]});
        write(d, "plugins.json", &manifest.to_string());
        let mut reg = PluginRegistry::load(d);
        reg.timeout = Duration::from_secs(2);
        assert_eq!(
            reg.names(PluginKind::AssetScanner),
            ["scan", "bad", "missing"]
        );
        let urls = reg.run_asset_scanners("t", "https://h/a.js", "https://h/", ".js");
        assert_eq!(
            urls.into_iter().collect::<Vec<_>>(),
            ["https://h/a.js#x", "img/.js"]
        );
        let found = reg.run_engine_detectors("<html>", "auto").unwrap();
        assert_eq!(found["engine"], "x");
        assert_eq!(found["mode"], "auto");

        // Timeout kills the process.
        let slow = Plugin {
            name: "slow".into(),
            kind: PluginKind::AssetScanner,
            command: vec![py, "slow.py".into()],
        };
        let started = Instant::now();
        assert!(reg.call(&slow, &json!({})).is_err());
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn missing_or_invalid_manifest_means_no_plugins() {
        let dir = tempfile::tempdir().unwrap();
        assert!(PluginRegistry::load(dir.path()).is_empty());
        std::fs::write(dir.path().join("plugins.json"), "{oops").unwrap();
        assert!(PluginRegistry::load(dir.path()).is_empty());
    }
}
