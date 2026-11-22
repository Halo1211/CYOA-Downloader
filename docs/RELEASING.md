# Windows Rust release

The Rust desktop application is the primary product. The runnable Python
reference remains under `old/` and is not part of the Windows release.

## Build and check

Run from the repository root on Windows with the stable Rust toolchain:

```powershell
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
cargo test
cargo build --locked --release -p cyoa-desktop
.\target\release\cyoa-desktop.exe
```

The release assets are the standalone desktop executable
`CYOA-Downloader-Windows-x64.exe` and the companion archive
`CYOA-Downloader-Third-Party-Licenses.zip`. The CLI remains buildable from
source with `cargo run -p cyoa-cli -- --help`.

## GitHub release

Pushing a `v*` tag builds and publishes the Windows desktop `.exe` plus the
third-party license archive. The executable artifact is uploaded without a ZIP
wrapper; the license archive is a separate asset. A workflow dispatch builds
the artifacts without publishing a release. The executable is unsigned; optional
browser, FFmpeg, JavaScript runtime, and RAR integrations remain external.

The license archive contains the generated notices and full license texts, including bundled font licenses. The current notices index is `docs/THIRD_PARTY_NOTICES.md`.
