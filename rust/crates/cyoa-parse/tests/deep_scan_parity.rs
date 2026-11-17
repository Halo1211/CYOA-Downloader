#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_deep_scan.py` (live Python output).
use cyoa_parse::deep_scan::deep_scan_project_assets;
use serde_json::{Value, json};

/// Inputs Python rejects (`json.loads` fails) but Rust's JSON5 fallback accepts.
/// Documented "better" difference in FEATURE_PARITY.md.
const JSON5_IMPROVED: &[&str] = &[r#"{"image": "a.png",}"#];

#[test]
fn deep_scan_matches_python() {
    let cases: Value =
        serde_json::from_str(include_str!("../../../fixtures/deep_scan/cases.json")).unwrap();
    let mut fails = Vec::new();
    for c in cases.as_array().unwrap() {
        let src = c["json"].as_str().unwrap();
        let r = deep_scan_project_assets(src);
        let got = json!({"images": r.images, "audio": r.audio, "youtube": r.youtube});
        let want = json!({"images": c["images"], "audio": c["audio"], "youtube": c["youtube"]});
        if JSON5_IMPROVED.contains(&src) {
            assert!(want["images"].as_array().unwrap().is_empty());
            assert_eq!(got["images"].as_array().unwrap().len(), 1, "{src}");
            continue;
        }
        if got != want {
            fails.push(format!("{src}\n  got  {got}\n  want {want}"));
        }
    }
    assert!(fails.is_empty(), "{}", fails.join("\n"));
}
