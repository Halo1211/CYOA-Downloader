use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use thiserror::Error;

use crate::atomic_io::{AtomicWriteError, atomic_write_json};

const RESUME_FILE: &str = "download_state.json";

#[derive(Debug, Error)]
pub enum ResumeError {
    #[error(transparent)]
    AtomicWrite(#[from] AtomicWriteError),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Default, Serialize)]
pub struct ResumeState {
    pub completed: Vec<String>,
    pub failed: Vec<String>,
}

#[derive(Serialize)]
struct SavedResume<'a> {
    format_version: u8,
    completed: &'a [String],
    failed: &'a [String],
    updated_at: String,
}

/// Port of Python `resume_job_key`: stable identity for one queued output.
pub fn resume_job_key(url: &str, file_name: &str, mode: &str) -> String {
    use sha2::{Digest, Sha256};
    // Python: str(mode or "auto").strip().lower() — only an empty value becomes "auto".
    let mode = if mode.is_empty() {
        "auto".to_owned()
    } else {
        cyoa_core::paths::py_strip(mode).to_lowercase()
    };
    // json.dumps([...], ensure_ascii=False, separators=(",", ":")) == serde_json compact output.
    let payload = serde_json::json!([
        cyoa_core::paths::py_strip(url),
        cyoa_core::paths::py_strip(file_name),
        cyoa_core::paths::py_strip(&mode)
    ])
    .to_string();
    let digest = Sha256::digest(payload.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("job-v2:{hex}")
}

fn resume_path(output_dir: impl AsRef<Path>) -> PathBuf {
    output_dir.as_ref().join(RESUME_FILE)
}

/// Port of `storage/resume.py` load: missing or corrupt files yield an empty state.
pub fn load_resume_state(output_dir: impl AsRef<Path>) -> ResumeState {
    let Ok(text) = fs::read_to_string(resume_path(output_dir)) else {
        return ResumeState::default();
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return ResumeState::default();
    };
    let Some(object) = value.as_object() else {
        return ResumeState::default();
    };

    fn strings(value: Option<&serde_json::Value>) -> Vec<String> {
        value
            .and_then(serde_json::Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(serde_json::Value::as_str)
            .map(str::to_owned)
            .collect()
    }

    ResumeState {
        completed: strings(object.get("completed")),
        failed: strings(object.get("failed")),
    }
}

/// Port of `storage/resume.py` save. Unlike Python, write errors are returned.
pub fn save_resume_state(
    output_dir: impl AsRef<Path>,
    completed: &[String],
    failed: &[String],
    clock: impl FnOnce() -> String,
) -> Result<(), ResumeError> {
    fs::create_dir_all(output_dir.as_ref())?;
    atomic_write_json(
        resume_path(output_dir),
        &SavedResume {
            format_version: 2,
            completed,
            failed,
            updated_at: clock(),
        },
    )?;
    Ok(())
}

pub fn clear_resume_state(output_dir: impl AsRef<Path>) -> Result<(), ResumeError> {
    match fs::remove_file(resume_path(output_dir)) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

#[cfg(test)]
#[allow(clippy::expect_used)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_DIR: AtomicU64 = AtomicU64::new(0);

    fn temp_dir() -> PathBuf {
        let id = NEXT_DIR.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("cyoa-resume-{}-{id}", std::process::id()));
        fs::create_dir_all(&path).expect("create temporary directory");
        path
    }

    #[test]
    fn load_filters_non_string_values_and_preserves_order_and_duplicates() {
        let dir = temp_dir();
        fs::write(
            dir.join(RESUME_FILE),
            r#"{"completed":["a",3,"a"],"failed":"bad"}"#,
        )
        .expect("write state");
        let state = load_resume_state(&dir);
        assert_eq!(state.completed, ["a", "a"]);
        assert!(state.failed.is_empty());
    }

    #[test]
    fn corrupt_file_loads_empty_state() {
        let dir = temp_dir();
        fs::write(dir.join(RESUME_FILE), "{").expect("write corrupt state");
        assert!(load_resume_state(dir).completed.is_empty());
    }
}
