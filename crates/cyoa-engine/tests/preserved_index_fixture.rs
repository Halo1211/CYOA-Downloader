//! F-037: preserved-index helpers must equal Python on `fixtures/preserved_index.json`.
#![allow(clippy::unwrap_used)]
use cyoa_engine::preserved_index::{
    Family, build_preserved_index, html_site_identity, is_hashed_runtime_asset,
    merge_legacy_index_customizations, strip_project_interceptor_markup,
};
use serde_json::Value;
use std::path::Path;

fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

#[test]
fn preserved_index_matches_python() {
    let f: Value = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/preserved_index.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let mut bad = Vec::new();
    let mut check = |label: &str, got: String, want: &str| {
        if got != want {
            bad.push(format!("{label}\n  rust {got:?}\n  py   {want:?}"));
        }
    };
    for (i, c) in f["merge"].as_array().unwrap().iter().enumerate() {
        check(
            &format!("merge #{i}"),
            merge_legacy_index_customizations(s(&c[0]), s(&c[1])),
            s(&c[2]),
        );
    }
    for (i, c) in f["plus2"].as_array().unwrap().iter().enumerate() {
        check(
            &format!("plus2 #{i}"),
            build_preserved_index(s(&c[0]), Family::IccPlus2, s(&c[1]))
                .unwrap_or_else(|e| format!("ERR {e}")),
            s(&c[2]),
        );
    }
    for (i, c) in f["remix"].as_array().unwrap().iter().enumerate() {
        let got = build_preserved_index(s(&c[0]), Family::IccRemix, s(&c[1]));
        match (&got, c[2].as_str()) {
            (Ok(g), Some(w)) => check(&format!("remix #{i}"), g.clone(), w),
            (Err(_), None) => {}
            _ => check(
                &format!("remix #{i}"),
                format!("{got:?}"),
                &c[2].to_string(),
            ),
        }
    }
    for (i, c) in f["identity"].as_array().unwrap().iter().enumerate() {
        let (t, fav) = html_site_identity(s(&c[0]));
        check(
            &format!("identity #{i}"),
            format!("{t}|{fav}"),
            &format!("{}|{}", s(&c[1][0]), s(&c[1][1])),
        );
    }
    for c in f["hashed"].as_array().unwrap() {
        check(
            &format!("hashed {}", c[0]),
            is_hashed_runtime_asset(s(&c[0]), s(&c[1])).to_string(),
            &c[2].as_bool().unwrap().to_string(),
        );
    }
    for (i, c) in f["strip"].as_array().unwrap().iter().enumerate() {
        check(
            &format!("strip #{i}"),
            strip_project_interceptor_markup(s(&c[0])),
            s(&c[1]),
        );
    }
    assert!(bad.is_empty(), "{}", bad.join("\n"));
}
