#!/usr/bin/env bash
# GUI end-to-end: the Rust desktop app's real "Add URL" + "Download All"
# handlers (CYOA_DESKTOP_E2E) against the local fixture site, compared with
# the Python CLI output. Usage (from repo root): bash scripts/gui_e2e.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=${PORT:-8766}
URL="http://127.0.0.1:$PORT/mycyoa/project.json"
(cd "$ROOT/fixtures/e2e_site" && python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/home"
w() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else echo "$1"; fi; }
HOMEDIR=$(w "$WORK/home")
(cd "$ROOT" && cargo build -q -p cyoa-desktop --offline)
sleep 1
STEM=mycyoa_project.json
fail=0
for pair in "Embed:" "Zip:--zip" "Both:--both"; do
  mode=${pair%%:*}; flag=${pair#*:}
  mkdir -p "$WORK/py_$mode" "$WORK/rs_$mode"
  (cd "$ROOT" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" python old/cyoa_downloader.py "$URL" $flag -o "$WORK/py_$mode" -w 0 >/dev/null 2>&1) || true
  (cd "$ROOT" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" CYOA_DESKTOP_E2E="$URL|$(w "$WORK/rs_$mode")|$mode" \
     timeout 300 target/debug/cyoa-desktop.exe >/dev/null 2>&1) || true
  status=$(cat "$WORK/rs_$mode/gui_e2e_status.txt" 2>/dev/null || echo "no status")
  case "$status" in Done*) echo "OK   GUI $mode job: ${status%%	*}";; *) echo "FAIL GUI $mode job: $status"; fail=1; continue;; esac
  for f in "$STEM.json" "$STEM.zip"; do
    [ -f "$WORK/py_$mode/$f" ] || [ -f "$WORK/rs_$mode/$f" ] || continue
    if [ "$f" = "$STEM.zip" ]; then
      for d in py rs; do mkdir -p "$WORK/z${d}_$mode"; (cd "$WORK/z${d}_$mode" && unzip -q "$WORK/${d}_$mode/$f"); done
      diff -rq "$WORK/zpy_$mode" "$WORK/zrs_$mode" >/dev/null && echo "OK   $mode $f contents" || { echo "DIFF $mode $f"; fail=1; }
    else
      cmp -s "$WORK/py_$mode/$f" "$WORK/rs_$mode/$f" && echo "OK   $mode $f" || { echo "DIFF $mode $f"; fail=1; }
    fi
  done
done
exit $fail
