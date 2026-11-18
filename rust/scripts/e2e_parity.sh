#!/usr/bin/env bash
# End-to-end Python-vs-Rust parity on a local static fixture site (no internet).
# Usage (from repo root): bash rust/scripts/e2e_parity.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
PORT=${PORT:-8765}
URL="http://127.0.0.1:$PORT/mycyoa/project.json"
(cd "$ROOT/rust/fixtures/e2e_site" && python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
sleep 1
(cd "$ROOT" && python cyoa_downloader.py "$URL" --both -o "$WORK/py" -w 0 >/dev/null 2>&1)
(cd "$ROOT/rust" && cargo run -q -p cyoa-cli --offline -- "$URL" --both -o "$WORK/rs" -w 0 >/dev/null 2>&1)
STEM=mycyoa_project.json
fail=0
for f in "$STEM.json" "${STEM}_original.json"; do cmp -s "$WORK/py/$f" "$WORK/rs/$f" && echo "OK   $f" || { echo "DIFF $f"; fail=1; }; done
strip() { grep -v -e downloaded_at -e Generated -e '^# ---' "$1"; }
for f in "${STEM}_metadata.json" failed_images.txt failed_assets.txt; do
  diff -q <(strip "$WORK/py/$f") <(strip "$WORK/rs/$f") >/dev/null && echo "OK   $f (timestamps ignored)" || { echo "DIFF $f"; fail=1; }
done
for d in py rs; do mkdir -p "$WORK/z$d"; (cd "$WORK/z$d" && unzip -q "$WORK/$d/$STEM.zip"); done
diff -rq "$WORK/zpy" "$WORK/zrs" >/dev/null && echo "OK   $STEM.zip contents" || { echo "DIFF $STEM.zip"; fail=1; }
exit $fail
