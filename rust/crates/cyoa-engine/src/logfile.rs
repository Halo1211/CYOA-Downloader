//! `cyoa_downloader.log`: port of `cyoa_downloader_app/logging_setup.py`.
//!
//! A tiny event-only `tracing::Subscriber` (no new dependencies):
//! - line format `YYYY-MM-DD HH:MM:SS,mmm - LEVEL - message` (Python
//!   `%(asctime)s - %(levelname)s - %(message)s`, WARN rendered `WARNING`);
//! - INFO and above, like the Python logger level;
//! - secret redaction identical to `_redact_sensitive_text`;
//! - `<output_dir>/cyoa_downloader.log` rotated like `RotatingFileHandler`
//!   (5 MiB, 7 backups), retargeted by every `setup_file_logging` call;
//! - optional console copy on stderr (Python's StreamHandler).

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{LazyLock, Mutex, OnceLock};

use regex::Regex;
use tracing::{Event, Level, Metadata, Subscriber, field::Visit, span};

pub const LOG_FILE_NAME: &str = "cyoa_downloader.log";
const MAX_BYTES: u64 = 5 * 1024 * 1024;
const BACKUP_COUNT: u32 = 7;

static BEARER: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+").ok());
static URL_QUERY: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(r#"(?i)([?&](?:key|api[_-]?key|access[_-]?token|token|signature|sig)=)[^&#\s'"<>]+"#)
        .ok()
});
static USERINFO: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?P<scheme>https?|socks[45]h?)://(?P<username>[^/@:\s]+):(?P<password>[^/@\s]+)@",
    )
    .ok()
});
static SECRET: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)\b(",
        r"(?:[a-z0-9]+[_-])*api[_-]?key(?:[_-][a-z0-9]+)*|",
        r"(?:[a-z0-9]+[_-])*(?:token|password|passwd|secret|cookie|authorization|credential|bearer)",
        r"(?:[_-][a-z0-9]+)*",
        r")\b",
        r#"(\s*[:=]\s*|['"]?\s*:\s*['"]?)"#,
        r#"(['"][^'"]+['"]|[^,\s'"}&]+)"#,
    ))
    .ok()
});

/// Python `_redact_sensitive_text` (same patterns, same order).
pub fn redact(text: &str) -> String {
    // A pattern that failed to compile would be a programming error; fail
    // closed by withholding the whole line rather than leaking it.
    let (Some(bearer), Some(query), Some(userinfo), Some(secret)) =
        (&*BEARER, &*URL_QUERY, &*USERINFO, &*SECRET)
    else {
        return "<redaction unavailable>".to_owned();
    };
    let t = bearer.replace_all(text, "Bearer __REDACTED__");
    let t = query.replace_all(&t, "${1}__REDACTED__");
    let t = userinfo.replace_all(&t, "${scheme}://${username}:__REDACTED__@");
    secret.replace_all(&t, "${1}${2}__REDACTED__").into_owned()
}

/// `RotatingFileHandler(maxBytes=5 MiB, backupCount=7)`.
struct Rotating {
    path: PathBuf,
    file: File,
    size: u64,
}

impl Rotating {
    fn open(path: PathBuf) -> std::io::Result<Self> {
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        let size = file.metadata()?.len();
        Ok(Self { path, file, size })
    }

    fn rollover(&mut self) -> std::io::Result<()> {
        let name = |i: u32| PathBuf::from(format!("{}.{i}", self.path.display()));
        for i in (1..BACKUP_COUNT).rev() {
            let src = name(i);
            if src.exists() {
                let dst = name(i + 1);
                let _ = fs::remove_file(&dst);
                fs::rename(&src, &dst)?;
            }
        }
        let first = name(1);
        let _ = fs::remove_file(&first);
        fs::rename(&self.path, &first)?;
        *self = Self::open(self.path.clone())?;
        Ok(())
    }

    fn write_line(&mut self, line: &str) {
        // Python text mode: "\n" terminator translated to CRLF on Windows.
        let bytes = crate::reports::os_text(&format!("{line}\n")).into_bytes();
        // Python: rollover when tell() + len(msg) >= maxBytes (and the file is non-empty).
        if self.size > 0 && self.size + bytes.len() as u64 >= MAX_BYTES && self.rollover().is_err()
        {
            return;
        }
        if self.file.write_all(&bytes).is_ok() {
            self.size += bytes.len() as u64;
        }
    }
}

struct Sink {
    file: Mutex<Option<Rotating>>,
    console: bool,
}

static SINK: OnceLock<Sink> = OnceLock::new();

fn level_name(level: Level) -> &'static str {
    match level {
        Level::ERROR => "ERROR",
        Level::WARN => "WARNING",
        Level::INFO => "INFO",
        Level::DEBUG => "DEBUG",
        Level::TRACE => "TRACE",
    }
}

/// `time.strftime('%Y-%m-%d %H:%M:%S') + ',%03d' % msecs`.
fn asctime() -> String {
    let base = crate::reports::local_timestamp();
    let ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_millis())
        .unwrap_or(0);
    format!("{base},{ms:03}")
}

#[derive(Default)]
struct Message(String);

impl Visit for Message {
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        if field.name() == "message" {
            self.0 = value.to_owned();
        } else {
            self.0.push_str(&format!(" {}={value}", field.name()));
        }
    }

    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.0 = format!("{value:?}");
        } else {
            self.0.push_str(&format!(" {}={value:?}", field.name()));
        }
    }
}

struct LogSubscriber;

impl Subscriber for LogSubscriber {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        *metadata.level() <= Level::INFO
    }

    fn new_span(&self, _: &span::Attributes<'_>) -> span::Id {
        span::Id::from_u64(1)
    }

    fn record(&self, _: &span::Id, _: &span::Record<'_>) {}

    fn record_follows_from(&self, _: &span::Id, _: &span::Id) {}

    fn event(&self, event: &Event<'_>) {
        let Some(sink) = SINK.get() else { return };
        let mut msg = Message::default();
        event.record(&mut msg);
        let line = format!(
            "{} - {} - {}",
            asctime(),
            level_name(*event.metadata().level()),
            redact(&msg.0)
        );
        if sink.console {
            eprintln!("{line}");
        }
        if let Ok(mut file) = sink.file.lock()
            && let Some(f) = file.as_mut()
        {
            f.write_line(&line);
        }
    }

    fn enter(&self, _: &span::Id) {}

    fn exit(&self, _: &span::Id) {}
}

/// Install the global subscriber once. `console` mirrors Python's stderr
/// StreamHandler. Returns false if another subscriber was already installed.
pub fn init(console: bool) -> bool {
    if SINK.get().is_some() {
        return true;
    }
    let _ = SINK.set(Sink {
        file: Mutex::new(None),
        console,
    });
    tracing::subscriber::set_global_default(LogSubscriber).is_ok()
}

/// Python `setup_file_logging(output_dir)`: (re)target the log file.
pub fn setup_file_logging(output_dir: &Path) {
    let Some(sink) = SINK.get() else { return };
    let path = output_dir.join(LOG_FILE_NAME);
    let opened = fs::create_dir_all(output_dir).and_then(|()| Rotating::open(path.clone()));
    let ok = opened.is_ok();
    if let Ok(mut slot) = sink.file.lock() {
        // Old handler is closed first, like Python removing previous handlers.
        *slot = None;
        match opened {
            Ok(r) => *slot = Some(r),
            Err(e) => {
                drop(slot);
                tracing::warn!("Could not create log file at {}: {e}", path.display());
            }
        }
    }
    if ok {
        tracing::info!("Log file: {}", path.display());
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    #[test]
    fn rotation_shifts_backups() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join(LOG_FILE_NAME);
        fs::write(&path, vec![b'x'; (MAX_BYTES - 2) as usize]).unwrap();
        fs::write(tmp.path().join(format!("{LOG_FILE_NAME}.1")), "old1").unwrap();
        let mut r = Rotating::open(path.clone()).unwrap();
        r.write_line("new line");
        assert_eq!(
            fs::read_to_string(tmp.path().join(format!("{LOG_FILE_NAME}.2"))).unwrap(),
            "old1"
        );
        assert_eq!(
            fs::metadata(tmp.path().join(format!("{LOG_FILE_NAME}.1")))
                .unwrap()
                .len(),
            MAX_BYTES - 2
        );
        assert!(fs::read_to_string(&path).unwrap().starts_with("new line"));
    }

    #[test]
    fn redaction_matches_python() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/redact.json");
        let cases: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap();
        let mut bad = Vec::new();
        for c in cases.as_array().unwrap() {
            let input = c["in"].as_str().unwrap();
            let want = c["out"].as_str().unwrap();
            let got = redact(input);
            if got != want {
                bad.push(format!("{input:?}\n  rust:   {got:?}\n  python: {want:?}"));
            }
        }
        assert!(bad.is_empty(), "{}", bad.join("\n"));
    }

    #[test]
    fn level_names_match_python() {
        assert_eq!(level_name(Level::WARN), "WARNING");
        assert_eq!(level_name(Level::ERROR), "ERROR");
    }
}
