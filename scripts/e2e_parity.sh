#!/usr/bin/env bash
# End-to-end Python-vs-Rust parity on a local static fixture site (no internet).
# Usage (from repo root): bash scripts/e2e_parity.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=${PORT:-8765}
URL="http://127.0.0.1:$PORT/mycyoa/project.json"
(cd "$ROOT/fixtures/e2e_site" && python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
# Isolated profile for the two downloaders only (not cargo): never touch the
# user's real image cache/settings.
mkdir -p "$WORK/home"
HOMEDIR="$WORK/home"; command -v cygpath >/dev/null && HOMEDIR=$(cygpath -w "$HOMEDIR")
# RUST_BIN=<path> tests a prebuilt binary (e.g. the release/dist exe).
if [ -z "${RUST_BIN:-}" ]; then
  (cd "$ROOT" && cargo build -q -p cyoa-cli --offline)
  RUST_BIN="$ROOT/target/debug/cyoa-downloader"
fi
sleep 1
(cd "$ROOT" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" python old/cyoa_downloader.py "$URL" --both -o "$WORK/py" -w 0 >/dev/null 2>&1)
(cd "$ROOT" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" "$RUST_BIN" "$URL" --both -o "$WORK/rs" -w 0 >/dev/null 2>&1)
STEM=mycyoa_project.json
fail=0
for f in "$STEM.json" "${STEM}_original.json"; do cmp -s "$WORK/py/$f" "$WORK/rs/$f" && echo "OK   $f" || { echo "DIFF $f"; fail=1; }; done
strip() { grep -v -e downloaded_at -e Generated -e '^# ---' "$1"; }
for f in "${STEM}_metadata.json" failed_images.txt failed_assets.txt; do
  # Both files must exist (a missing pair must not compare equal), and the
  # comparison is byte-exact apart from timestamp lines (CRLF included).
  if [ ! -f "$WORK/py/$f" ] || [ ! -f "$WORK/rs/$f" ]; then echo "MISSING $f"; fail=1; continue; fi
  cmp -s <(strip "$WORK/py/$f") <(strip "$WORK/rs/$f") && echo "OK   $f (timestamps ignored, byte-exact)" || { echo "DIFF $f"; fail=1; }
done
for d in py rs; do mkdir -p "$WORK/z$d"; (cd "$WORK/z$d" && unzip -q "$WORK/$d/$STEM.zip"); done
diff -rq "$WORK/zpy" "$WORK/zrs" >/dev/null && echo "OK   $STEM.zip contents" || { echo "DIFF $STEM.zip"; fail=1; }
exit $fail
