#!/usr/bin/env bash
# F-037 V3: Python-vs-Rust parity of the offline viewer step (orchestrator.py
# 797-997) on the local fixture site, in embed / zip / both modes.
# Usage (from repo root): bash rust/scripts/viewer_parity.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
PORT=${PORT:-8766}
URL="http://127.0.0.1:$PORT/mycyoa/project.json"
(cd "$ROOT/rust/fixtures/e2e_site" && python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
if [ -z "${RUST_BIN:-}" ]; then
  (cd "$ROOT/rust" && cargo build -q -p cyoa-cli --offline)
  RUST_BIN="$ROOT/rust/target/debug/cyoa-downloader"
fi
# Isolated home with a registered viewer (strategy C: fetch() patch) and the
# viewer step enabled through the Python settings keys.
make_home() {  # $1 = home dir, $2 = preferred id
  python - "$1" "$2" <<'PY'
import json, os, sys, zipfile
home, pref = sys.argv[1], sys.argv[2]
vdir = os.path.join(home, ".cyoa_downloader", "offline_viewers")
os.makedirs(vdir, exist_ok=True)
with zipfile.ZipFile(os.path.join(vdir, "fetchv.zip"), "w") as z:
    z.writestr("Fetch Viewer/index.html",
               '<!DOCTYPE html><html><head><title>V</title></head><body>'
               '<div id="projectSize">0</div><script src="app.js"></script></body></html>')
    z.writestr("Fetch Viewer/app.js", 'fetch("project.json").then(r=>r.json())')
with zipfile.ZipFile(os.path.join(vdir, "orig.zip"), "w") as z:
    z.writestr("index.html", "<html><head></head><body>o</body></html>")
    z.writestr("js/app.js", "/*! Delete and replace this part with your project */{a:1};go()")
manifest = {
    "fetchv": {"name": "Fetch Viewer", "zip_filename": "fetchv.zip", "viewer_type": "custom"},
    "orig": {"name": "Viewer 1.8 [Original]", "zip_filename": "orig.zip", "viewer_type": "icc_original"},
}
with open(os.path.join(vdir, "viewers.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
with open(os.path.join(home, ".cyoa_downloader", "settings.json"), "w", encoding="utf-8") as f:
    json.dump({"offline_viewer_json_enabled": True, "offline_viewer_preferred_id": pref}, f)
PY
}
sleep 1
fail=0
STEM=mycyoa_project.json
for pref in fetchv auto; do
  for flag in "" "-z" "--both"; do
    name="${pref}${flag:-embed}"
    for d in py rs; do
      H="$WORK/home_${name}_$d"; mkdir -p "$H"; make_home "$H" "$pref"
      HW="$H"; command -v cygpath >/dev/null && HW=$(cygpath -w "$H")
      if [ $d = py ]; then
        (cd "$ROOT" && USERPROFILE="$HW" HOME="$HW" python cyoa_downloader.py "$URL" $flag -o "$WORK/$name/$d" -w 0 >"$WORK/$name.$d.log" 2>&1) || true
      else
        (cd "$ROOT/rust" && USERPROFILE="$HW" HOME="$HW" "$RUST_BIN" "$URL" $flag -o "$WORK/$name/$d" -w 0 >"$WORK/$name.$d.log" 2>&1) || true
      fi
    done
    P="$WORK/$name/py"; R="$WORK/$name/rs"
    for sub in "${STEM}_offline" audio; do
      if [ ! -e "$P/$sub" ] && [ ! -e "$R/$sub" ]; then continue; fi
      if [ ! -e "$P/$sub" ] || [ ! -e "$R/$sub" ]; then echo "MISSING [$name] $sub (py=$([ -e "$P/$sub" ] && echo y || echo n) rs=$([ -e "$R/$sub" ] && echo y || echo n))"; fail=1; continue; fi
      if diff -r "$P/$sub" "$R/$sub" >"$WORK/$name.$sub.diff"; then echo "OK   [$name] $sub"; else echo "DIFF [$name] $sub"; head -20 "$WORK/$name.$sub.diff"; fail=1; fi
    done
    pv=$(grep -c "Offline viewer ready" "$WORK/$name.py.log" || true)
    echo "     [$name] python viewer runs: $pv"
  done
done
exit $fail
