//! `cargo run -p cyoa-engine --example mirror_classic -- <start_url> <out_dir>`
//! Runs the classic website mirror stage (Python `_download_html(start_url,
//! index.html)`) for parity checks (`rust/scripts/website_parity.sh`).

use cyoa_engine::website::{js::directory_base_url, mirror::MirrorSink};
use cyoa_net::{
    client::{ClientConfig, FetchClient},
    policy::DestinationPolicy,
};
use std::path::PathBuf;
use tokio_util::sync::CancellationToken;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let (Some(start), Some(out)) = (args.next(), args.next()) else {
        return Err("usage: mirror_classic <start_url> <out_dir>".into());
    };
    let start = cyoa_core::url::canonicalize_url(&start).map_err(|e| format!("{e:?}"))?;
    let base = directory_base_url(&start);
    let client = FetchClient::new(ClientConfig {
        policy: DestinationPolicy {
            origin: Some(url::Url::parse(&start)?),
            allow_internal: false,
        },
        ..ClientConfig::default()
    })?;
    let handle = tokio::runtime::Handle::current();
    let failed = tokio::task::spawn_blocking(move || {
        let mut sink = MirrorSink::new(
            PathBuf::from(out),
            &start,
            &base,
            client,
            handle,
            CancellationToken::new(),
            512 * 1024 * 1024,
        );
        sink.mirror_classic(None).map(|()| sink.failed)
    })
    .await??;
    for (url, error) in failed {
        println!("FAILED\t{url}\t{error}");
    }
    Ok(())
}
