#!/usr/bin/env bash
# Build a local Rust release distribution folder (nothing is uploaded).
# Usage (from repo root): bash scripts/make_dist.sh
# Output: dist/cyoa-downloader-rust-<version>/ (dist/ is gitignored).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(grep -m1 '^version' "$ROOT/Cargo.toml" | sed 's/.*"\(.*\)".*/\1/')
OUT="$ROOT/dist/cyoa-downloader-rust-$VERSION"
if [ -e "$OUT" ]; then
  echo "refusing to overwrite $OUT (remove it first)" >&2
  exit 1
fi
(cd "$ROOT" && cargo build --release --offline -p cyoa-cli -p cyoa-desktop)
mkdir -p "$OUT"
EXE=""; [ -f "$ROOT/target/release/cyoa-downloader.exe" ] && EXE=".exe"
cp "$ROOT/target/release/cyoa-downloader$EXE" "$ROOT/target/release/cyoa-desktop$EXE" "$OUT/"
cp "$ROOT/docs/RUST_GUIDE.md" "$OUT/README.md"
(cd "$ROOT" && python scripts/third_party_notices.py --bundle "$OUT/LICENSES")
cp "$ROOT/docs/THIRD_PARTY_NOTICES.md" "$OUT/"
# Smoke check of the packaged CLI (no network).
"$OUT/cyoa-downloader$EXE" --version
"$OUT/cyoa-downloader$EXE" --help >/dev/null
echo "dist ready: $OUT"
