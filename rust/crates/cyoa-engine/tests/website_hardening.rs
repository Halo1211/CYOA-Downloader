//! Regression tests for the Sol review of the website mirror (session 5).
#![allow(clippy::unwrap_used, clippy::expect_used)]

use cyoa_engine::website::{PathAllocator, html, js, mirror::contained_path, normalize_remote_url};
use std::path::Path;

const START: &str = "https://host.example/cyoa/";

#[test]
fn multibyte_references_do_not_panic() {
    for raw in ["💣💣.png", "ü", "ééééééé://x", "h💣tps:/x", "😀"] {
        let _ = normalize_remote_url(raw, Some(START), START);
    }
}

#[test]
fn collision_identity_uses_casefold() {
    let mut a = PathAllocator::new(START);
    let first = a.allocate("https://cdn.example/stra\u{df}e.png", "", "");
    let second = a.allocate("https://cdn.example/strasse.png", "", "");
    assert_ne!(first.to_lowercase(), second.to_lowercase());
    assert!(second.ends_with("_1.png"), "{first} / {second}");
}

#[test]
fn allocated_paths_are_written_literally_and_contained() {
    let root = Path::new("out-root");
    let p = contained_path(root, "a%2Fb.png").unwrap();
    assert_eq!(p, root.join("a%2Fb.png"), "no second percent-decoding");
    assert!(contained_path(root, "../escape.png").is_none());
    assert!(contained_path(root, "a/../../x").is_none());
    assert!(contained_path(root, "").is_none());
    assert!(contained_path(root, "/abs").is_none());
}

#[test]
fn hostile_nesting_is_rejected_without_stack_overflow() {
    let ok = "<div>".repeat(400);
    assert!(html::parse(&ok).is_ok());
    let deep = "<div>".repeat(200_000);
    // Run on a small stack to prove no recursion depends on input depth.
    let handle = std::thread::Builder::new()
        .stack_size(1 << 20)
        .spawn(move || html::parse(&deep).is_err())
        .unwrap();
    assert!(handle.join().unwrap());
}

#[test]
fn numeric_range_overflow_is_skipped() {
    // start near u128::MAX and an unbounded start must not panic or wrap.
    let text = format!(
        "r = Math.floor(Math.random() * 2) + {}; el.src = \"c/\" + r + \".png\"; \
         q = Math.floor(Math.random() * 2) + 99999999999999999999999999999999999999999; el.src = \"d/\" + q + \".png\";",
        u128::MAX
    );
    let paths = js::infer_runtime_chunk_paths(&text);
    assert!(paths.is_empty());
}
