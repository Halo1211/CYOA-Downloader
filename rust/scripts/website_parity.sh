#!/usr/bin/env bash
# Website (ICC) classic mirror parity: Python WebsiteDownloader._download_html vs
# Rust MirrorSink::mirror_classic on rust/fixtures/site_mirror (local http.server).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d); PORT=${PORT:-8767}
URL="http://127.0.0.1:$PORT/game/"
(cd "$ROOT/rust/fixtures/site_mirror" && python -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
mkdir -p "$WORK/home"; HOMEDIR="$WORK/home"; command -v cygpath >/dev/null && HOMEDIR=$(cygpath -w "$HOMEDIR")
sleep 1
(cd "$ROOT" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" python - "$URL" "$WORK/py" <<'PY'
import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
from cyoa_downloader_app.download.website import WebsiteDownloader
d = WebsiteDownloader(sys.argv[1], sys.argv[2], max_workers=1, archive_strategy="classic")
import os; os.makedirs(sys.argv[2], exist_ok=True)
d._download_html(d.start_url, d.start_html_local)
for item in d._failed_items:
    print("FAILED\t%s\t%s" % (item["url"], item["error"]))
PY
) > "$WORK/py_failed.txt"
(cd "$ROOT/rust" && cargo build -q -p cyoa-engine --example mirror_classic --offline)
(cd "$ROOT/rust" && USERPROFILE="$HOMEDIR" HOME="$HOMEDIR" ./target/debug/examples/mirror_classic "$URL" "$WORK/rs") > "$WORK/rs_failed.txt"
fail=0
diff <(cd "$WORK/py" && find . -type f | sort) <(cd "$WORK/rs" && find . -type f | sort) && echo "OK   file list" || { echo "DIFF file list"; fail=1; }
for f in $(cd "$WORK/py" && find . -type f | sort); do
  [ -f "$WORK/rs/$f" ] || continue
  cmp -s "$WORK/py/$f" "$WORK/rs/$f" && echo "OK   $f" || { echo "DIFF $f"; diff <(cat "$WORK/py/$f") <(cat "$WORK/rs/$f") | head -6; fail=1; }
done
diff <(tr -d '\r' < "$WORK/py_failed.txt" | sort) <(tr -d '\r' < "$WORK/rs_failed.txt" | sort) && echo "OK   failed list" || { echo "DIFF failed list"; fail=1; }
exit $fail
