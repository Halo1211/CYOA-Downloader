# Windows Rust release

The Rust CLI and desktop application are the primary products. The Python
reference remains in `old/`; it is not included in the Windows release package.

## Build and check

Run from the repository root on Windows with the stable Rust toolchain:

```powershell
cargo fmt --all -- --check
cargo check --all-targets
cargo clippy --all-targets -- -D warnings
cargo test
cargo build --release -p cyoa-cli -p cyoa-desktop
.\target\release\cyoa-downloader.exe --version
.\target\release\cyoa-downloader.exe --help
```

The executables are `target/release/cyoa-downloader.exe` and
`target/release/cyoa-desktop.exe`.

## Package

The `Rust Windows release` workflow builds the executables, gathers third-party
license texts, packages both executables into one file,
`CYOA-Downloader-Windows-x64.zip`, and uploads it as the workflow artifact. A
version tag such as `v1.1.6` publishes that ZIP as the only GitHub Release
asset. Manual workflow dispatch builds and uploads the artifact without
publishing a release.

The ZIP contains both executables, this project's license, the application
README and guide, third-party notices, and the full license bundle. Chromium,
FFmpeg, JavaScript runtimes, and RAR helpers remain optional external tools.