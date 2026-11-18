"""End-to-end CYOAP Vue fixture: Python `--cyoap-vue-folder` output for a site.

Run from the repo root: python rust/fixtures/export_cyoap_site.py
Serves rust/scripts/cyoap_parity.py's SITE on 127.0.0.1, runs the Python CLI
with one worker and records every output file. "127.0.0.1:<port>" is replaced
by the placeholder "HOST" and `Generated` timestamp lines are dropped, so a
Rust test can serve the same files (with the recorded Content-Type) from any
port and compare byte for byte. Writes rust/fixtures/cyoap_site.json.
"""

import functools
import http.server
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rust" / "scripts"))
from cyoap_parity import SITE  # noqa: E402
from icc_parity import Quiet  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="cyoap-fixture-"))
srv = work / "srv"
for rel, data in SITE.items():
    (srv / rel).parent.mkdir(parents=True, exist_ok=True)
    (srv / rel).write_bytes(data)
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
host = f"127.0.0.1:{httpd.server_address[1]}"
home = work / "home"
home.mkdir()
env = dict(os.environ, USERPROFILE=str(home), HOME=str(home))
out = work / "out"
subprocess.run([sys.executable, "cyoa_downloader.py", f"http://{host}/cy/", "game", "--cyoap-vue-folder",
                "-o", str(out), "-w", "0", "-t", "1"], cwd=ROOT, env=env, check=True,
               capture_output=True)
httpd.shutdown()

outputs = {}
for p in sorted((out / "game").rglob("*")):
    if not p.is_file() or p.name == "cyoa_downloader.log":
        continue
    rel = p.relative_to(out / "game").as_posix()
    data = p.read_bytes()
    try:
        text = data.decode("utf-8")
        text = "\n".join(l for l in text.replace("\r\n", "\n").split("\n")
                         if not l.startswith(("Generated", "# Generated", "# --- ")))
        outputs[rel] = {"text": text.replace(host, "HOST"), "crlf": b"\r\n" in data}
    except UnicodeDecodeError:
        outputs[rel] = {"hex": data.hex()}

site = {}
for rel, data in SITE.items():
    ctype = Quiet.extensions_map.get(Path(rel).suffix) if hasattr(Quiet, "extensions_map") else None
    ctype = ctype or mimetypes.guess_type(rel)[0] or "application/octet-stream"
    site["/" + rel] = {"ct": ctype, "hex": data.hex()}

target = ROOT / "rust" / "fixtures" / "cyoap_site.json"
target.write_text(json.dumps({"site": site, "start_path": "/cy/", "outputs": outputs},
                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("wrote", target, len(outputs), "outputs")
