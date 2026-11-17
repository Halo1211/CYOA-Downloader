#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_localize.py` (real `_process_images` naming slice).
use cyoa_engine::localize::{asset_file_name, asset_rel_path};
use serde_json::Value;

#[test]
fn asset_names_match_python() {
    let cases: Value =
        serde_json::from_str(include_str!("../../../fixtures/localize/names.json")).unwrap();
    let mut fails = Vec::new();
    for c in cases.as_array().unwrap() {
        let s = |k: &str| c[k].as_str().unwrap().to_owned();
        // content was exported as latin-1 text: map chars back to bytes.
        let content: Vec<u8> = s("content").chars().map(|ch| ch as u32 as u8).collect();
        let img = c["is_image"].as_bool().unwrap();
        let fname = asset_file_name(&s("resolved"), &s("base"), &s("mime"), &content, img);
        let rel = asset_rel_path(&s("resolved"), &s("base"), &s("mime"), &content, img);
        if fname != s("fn") || rel != s("rel") {
            fails.push(format!(
                "{}\n  got  {fname} | {rel}\n  want {} | {}",
                s("resolved"),
                s("fn"),
                s("rel")
            ));
        }
    }
    assert!(fails.is_empty(), "{}", fails.join("\n"));
}
