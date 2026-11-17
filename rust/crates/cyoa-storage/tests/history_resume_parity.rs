#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Byte parity against `rust/fixtures/export_history_resume.py` (live Python output).
use cyoa_storage::{history, resume};
use serde_json::Value;
use std::path::PathBuf;

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/history_resume/cases.json"))
        .expect("fixture")
}

fn temp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("cyoa-hr-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("temp");
    dir
}

#[test]
fn job_keys_match_python() {
    let c = cases();
    for case in c["job_keys"].as_array().unwrap() {
        let a = case["args"].as_array().unwrap();
        let got = resume::resume_job_key(
            a[0].as_str().unwrap(),
            a[1].as_str().unwrap(),
            a[2].as_str().unwrap(),
        );
        assert_eq!(got, case["ok"].as_str().unwrap(), "{a:?}");
    }
}

#[test]
fn resume_bytes_match_python() {
    let c = cases();
    let fixed = c["fixed_time"].as_str().unwrap().to_owned();
    for case in c["resume"].as_array().unwrap() {
        let dir = temp(case["name"].as_str().unwrap());
        let list = |k: &str| -> Vec<String> {
            case[k]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned())
                .collect()
        };
        resume::save_resume_state(&dir, &list("completed"), &list("failed"), || fixed.clone())
            .expect("save");
        let got = std::fs::read_to_string(dir.join("download_state.json")).expect("read");
        assert_eq!(got, case["bytes"].as_str().unwrap(), "{}", case["name"]);
        let loaded = resume::load_resume_state(&dir);
        assert_eq!(loaded.completed, list("completed"));
        resume::clear_resume_state(&dir).expect("clear");
        assert!(!dir.join("download_state.json").exists());
    }
}

#[test]
fn history_bytes_match_python() {
    let c = cases();
    let fixed = c["fixed_time"].as_str().unwrap().to_owned();
    for case in c["history"].as_array().unwrap() {
        let path = temp("history").join("download_history.json");
        for (i, call) in case["calls"].as_array().unwrap().iter().enumerate() {
            let a = call.as_array().unwrap();
            history::record_history(
                &path,
                a[0].as_str().unwrap(),
                a[1].as_str().unwrap(),
                a[2].as_str().unwrap(),
                a[3].as_bool().unwrap(),
                || fixed.clone(),
                &format!("{:032x}", i + 1),
            )
            .expect("record");
        }
        assert_eq!(
            std::fs::read_to_string(&path).expect("read"),
            case["bytes"].as_str().unwrap()
        );
    }
}
