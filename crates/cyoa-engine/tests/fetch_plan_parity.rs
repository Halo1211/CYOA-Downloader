#![allow(clippy::expect_used, clippy::unwrap_used)]
//! Parity with `fixtures/export_fetch_plan.py` (fetch_one pre-network decisions).
use cyoa_engine::fetch_plan::{direct_attempts, resolve_asset_url, ssrf_block_cross_origin};
use serde_json::Value;

const INTERNAL: &[&str] = &[
    "127.0.0.1",
    "localhost",
    "169.254.169.254",
    "10.0.0.5",
    "::1",
];

fn cases() -> Value {
    serde_json::from_str(include_str!("../../../fixtures/fetch_plan/cases.json")).unwrap()
}

#[test]
fn resolve_matches_python() {
    let mut fails = Vec::new();
    for c in cases()["resolve"].as_array().unwrap() {
        let (b, p, want) = (
            c["base"].as_str().unwrap(),
            c["path"].as_str().unwrap(),
            c["url"].as_str().unwrap(),
        );
        let got = resolve_asset_url(p, b);
        if got != want {
            fails.push(format!("{b} + {p:?}\n  got  {got}\n  want {want}"));
        }
    }
    assert!(fails.is_empty(), "{}", fails.join("\n"));
}

#[test]
fn ssrf_matches_python() {
    let mut fails = Vec::new();
    for c in cases()["ssrf"].as_array().unwrap() {
        let (a, b, want) = (
            c["asset"].as_str().unwrap(),
            c["base"].as_str().unwrap(),
            c["block"].as_bool().unwrap(),
        );
        let got = ssrf_block_cross_origin(a, b, false, |h: &str| {
            INTERNAL.contains(&h.to_lowercase().as_str())
        });
        if got != want {
            fails.push(format!("{a} vs {b:?}: got {got} want {want}"));
        }
    }
    assert!(fails.is_empty(), "{}", fails.join("\n"));
}

#[test]
fn attempts_match_python() {
    for c in cases()["attempts"].as_array().unwrap() {
        let n = direct_attempts(
            c["img"].as_bool().unwrap(),
            c["url"].as_str().unwrap(),
            c["pre"].as_bool().unwrap(),
        );
        assert_eq!(u64::from(n), c["n"].as_u64().unwrap(), "{c}");
    }
}
