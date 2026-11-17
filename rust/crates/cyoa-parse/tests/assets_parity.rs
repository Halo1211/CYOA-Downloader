#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `rust/fixtures/export_assets.py` (live Python output).
use cyoa_parse::assets as a;
use serde_json::{Value, json};

#[test]
fn asset_reference_helpers_match_python() {
    let c: Value =
        serde_json::from_str(include_str!("../../../fixtures/assets/cases.json")).unwrap();
    assert_eq!(json!(a::IMAGE_FIELDS), c["image_fields"]);
    assert_eq!(json!(a::AUDIO_FIELDS), c["audio_fields"]);
    let mut fails = Vec::new();
    for k in c["refs"].as_array().unwrap() {
        let got: Vec<String> = a::extract_image_references(
            k["v"].as_str().unwrap(),
            k["bare"].as_bool().unwrap(),
            k["prose"].as_bool().unwrap(),
        )
        .into_iter()
        .collect();
        if json!(got) != k["ok"] {
            fails.push(format!(
                "{} bare={} prose={}: got {:?} want {}",
                k["v"], k["bare"], k["prose"], got, k["ok"]
            ));
        }
    }
    for k in c["srcsets"].as_array().unwrap() {
        let got: Vec<Vec<String>> = a::srcset_candidates(k["v"].as_str().unwrap())
            .into_iter()
            .map(|(x, y)| vec![x, y])
            .collect();
        if json!(got) != k["ok"] {
            fails.push(format!("srcset {}: got {:?} want {}", k["v"], got, k["ok"]));
        }
    }
    for k in c["media"].as_array().unwrap() {
        let v = k["v"].as_str().unwrap();
        let got = json!({"yt": a::is_youtube_url(v), "id": a::is_youtube_id(v), "sc": a::is_soundcloud_url(v)});
        if got["yt"] != k["yt"] || got["id"] != k["id"] || got["sc"] != k["sc"] {
            fails.push(format!("media {v}: got {got} want {k}"));
        }
    }
    assert!(
        fails.is_empty(),
        "{} mismatches:\n{}",
        fails.len(),
        fails.join("\n")
    );
}
