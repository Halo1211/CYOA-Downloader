#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Cross-language parity with `fixtures/export_checkpoint.py` (live Python output).
use cyoa_storage::archive_checkpoint::{path_for, python_json};
use serde_json::{Map, Value};
use std::path::Path;

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/checkpoint/cases.json")).unwrap()
}

#[cfg(windows)]
#[test]
fn db_names_match_python_path_for() {
    let root = Path::new("C:/__cyoa_fixture_root__");
    for case in cases()["names"].as_array().unwrap() {
        let folder = case["folder"].as_str().unwrap();
        let got = path_for(Path::new(folder), root).unwrap();
        let name = got.file_name().unwrap().to_string_lossy().into_owned();
        assert_eq!(name, case["name"].as_str().unwrap(), "{folder}");
    }
}

#[test]
fn payloads_match_python_json_dumps() {
    for case in cases()["payloads"].as_array().unwrap() {
        let mut data: Map<String, Value> = case["meta"].as_object().unwrap().clone();
        data.insert("local".into(), Value::String("dir/a b.png".into()));
        data.insert("size".into(), Value::from(7));
        data.insert("sha256".into(), Value::String("ab".repeat(32)));
        assert_eq!(
            python_json(&Value::Object(data)),
            case["json"].as_str().unwrap()
        );
    }
}
