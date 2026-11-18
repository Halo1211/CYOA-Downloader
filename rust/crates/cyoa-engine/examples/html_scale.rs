//! Scaling probe for the html.parser port: `cargo run --release --example html_scale`.
fn main() {
    let n = 10_000usize;
    for (label, doc) in [
        (
            "511-then-text",
            format!("{}{}", "<div>".repeat(511), "x".repeat(200_000)),
        ),
        (
            "10-then-text",
            format!("{}{}", "<div>".repeat(10), "x".repeat(200_000)),
        ),
        ("text-only", "x".repeat(200_000)),
        ("nested-400", "<div>".repeat(400)),
        ("nested-511", "<div>".repeat(511)),
        ("nested-10k", "<div>".repeat(n)),
        (
            "nested-closed-10k",
            format!("{}{}", "<i>".repeat(400), "<b></b>".repeat(n)),
        ),
        (
            "deep-then-text",
            format!("{}{}", "<div>".repeat(600), "x".repeat(200_000)),
        ),
    ] {
        let t = std::time::Instant::now();
        let ok = cyoa_engine::website::html::parse(&doc).is_ok();
        println!("{label:18} ok={ok} {:?}", t.elapsed());
    }
}
