#![allow(clippy::expect_used)]
use cyoa_storage::archive::{ArchiveError, ArchiveLimits, validate_zip_path};
use cyoa_storage::atomic_io::{atomic_write_bytes, atomic_write_json};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../fixtures/storage")
        .join(name)
}

#[test]
fn archive_verdicts_match_python_export() {
    let verdicts: Value =
        serde_json::from_slice(&fs::read(fixture("verdicts.json")).expect("fixtures generated"))
            .expect("valid verdicts");
    let cases = [
        "normal.zip",
        "traversal.zip",
        "absolute.zip",
        "drive.zip",
        "device.zip",
        "case_duplicate.zip",
        "symlink.zip",
        "ratio_bomb.zip",
        "too_many.zip",
    ];
    for name in cases {
        let expected = &verdicts[name];
        let result = validate_zip_path(fixture("zips").join(name), ArchiveLimits::default());
        match expected["result"].as_str().expect("result") {
            "accept" => assert!(result.is_ok(), "{name}: {result:?}"),
            "error" => {
                let error = result.expect_err(name);
                assert_eq!(expected["class"], "ValueError", "{name}: {error:?}");
                let actual = match error {
                    ArchiveError::Validation(s) => s,
                    other => panic!("{name}: wrong error {other:?}"),
                };
                let prefix = expected["message_prefix"].as_str().expect("prefix");
                assert_eq!(
                    actual.chars().take(100).collect::<String>(),
                    prefix,
                    "{name}"
                );
            }
            other => panic!("unknown verdict {other}"),
        }
    }
}

#[test]
fn atomic_write_replaces_and_cleans_up() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("target.txt");
    fs::write(&path, b"old").expect("seed");
    atomic_write_bytes(&path, b"new").expect("replace");
    assert_eq!(fs::read(&path).expect("read"), b"new");
    assert_eq!(fs::read_dir(dir.path()).expect("list").count(), 1);

    let blocked = dir.path().join("is-a-directory");
    fs::create_dir(&blocked).expect("directory");
    assert!(atomic_write_bytes(&blocked, b"failure").is_err());
    assert_eq!(fs::read_dir(dir.path()).expect("list").count(), 2);
    assert_eq!(fs::read_dir(&blocked).expect("list blocked dir").count(), 0);
}

#[test]
fn json_writes_match_python_bytes() {
    let root = fixture("");
    let samples: Value =
        serde_json::from_slice(&fs::read(root.join("json_samples.json")).expect("sample fixture"))
            .expect("samples");
    let dir = tempfile::tempdir().expect("tempdir");
    for name in ["unicode", "nested", "ordering"] {
        let path = dir.path().join("actual.json");
        atomic_write_json(&path, &samples[name]).expect("write JSON");
        let expected =
            fs::read(root.join(format!("json_{name}.expected"))).expect("expected bytes");
        assert_eq!(fs::read(path).expect("actual bytes"), expected, "{name}");
    }
}
