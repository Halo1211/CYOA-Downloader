"""Exercise frozen CSV/XLSX/XLS imports and the retained native Selenium helper."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def write_xls(path, rows):
    """Write a small BIFF8/CFB fixture without an external writer or test data."""
    def record(tag, body=b""):
        return struct.pack("<HH", tag, len(body)) + body
    def bof(kind):
        return record(0x0809, struct.pack("<HHHHII", 0x0600, kind, 0x0DBB, 0x07CC, 0, 6))
    strings = [str(value) for row in rows for value in row]
    encoded = []
    for value in strings:
        data = value.encode("utf-16le")
        encoded.append(struct.pack("<HB", len(data) // 2, 1) + data)
    sst = record(0x00FC, struct.pack("<II", len(strings), len(strings)) + b"".join(encoded))
    prefix = bof(5) + record(0x0042, struct.pack("<H", 1200)) + sst
    sheet_name = b"Queue"
    def bounds(offset):
        return record(0x0085, struct.pack("<IBBBB", offset, 0, 0, len(sheet_name), 0) + sheet_name)
    globals_size = len(prefix) + len(bounds(0)) + len(record(0x000A))
    sheet = bof(0x0010) + record(0x0200, struct.pack("<IIHHH", 0, len(rows), 0, len(rows[0]), 0))
    index = 0
    for row_index, row in enumerate(rows):
        for column, _value in enumerate(row):
            sheet += record(0x00FD, struct.pack("<HHHI", row_index, column, 0, index))
            index += 1
    workbook = prefix + bounds(globals_size) + record(0x000A) + sheet + record(0x000A)
    assert len(workbook) <= 4096, "Fixture exceeds its single FAT sector"
    workbook = workbook.ljust(4096, b"\0")
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, 0, *([free] * 108))
    def directory_entry(name, kind, child, first_sector, size):
        entry = bytearray(128)
        name_bytes = (name + "\0").encode("utf-16le")
        entry[:len(name_bytes)] = name_bytes
        struct.pack_into("<HBBIII", entry, 64, len(name_bytes), kind, 1, free, free, child)
        struct.pack_into("<IQ", entry, 116, first_sector, size)
        return entry
    directory = (directory_entry("Root Entry", 5, 1, end, 0)
                 + directory_entry("Workbook", 2, free, 2, 4096) + bytes(256))
    fat = struct.pack("<128I", fat_sector, end, *range(3, 10), end, *([free] * 118))
    path.write_bytes(header + fat + directory + workbook)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--runtime-root", type=Path, help="Extracted portable _internal folder")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    command = ([str(args.executable.resolve(strict=True))] if args.executable else
               [sys.executable, str(Path(__file__).resolve().parents[1] / "cyoa_downloader.py")])
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFeYAAAAASUVORK5CYII=")
    counts, checks = {}, []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            counts[self.path] = counts.get(self.path, 0) + 1
            body = png if self.path == "/hero.png" else json.dumps({"title": "Batch fixture", "rows": [{"image": "hero.png"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if self.path == "/hero.png" else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="cyoa-batch-smoke-") as temporary:
            root = Path(temporary)
            for extension in ("csv", "xlsx", "xls"):
                rows = [["url", "filename", "mode"], [f"http://127.0.0.1:{server.server_port}/project.json", f"Résumé 日本 {extension}", "embed"]]
                fixture = root / f"queue.{extension}"
                if extension == "csv":
                    with fixture.open("w", encoding="utf-8-sig", newline="") as stream:
                        csv.writer(stream).writerows(rows)
                elif extension == "xlsx":
                    import openpyxl
                    workbook = openpyxl.Workbook()
                    for row in rows:
                        workbook.active.append(row)
                    workbook.save(fixture)
                    workbook.close()
                else:
                    write_xls(fixture, rows)
                profile = root / f"profile-{extension}"
                profile.mkdir()
                output = root / extension
                environment = dict(os.environ, USERPROFILE=str(profile), PYTHONIOENCODING="utf-8")
                result = subprocess.run(command + ["--list", str(fixture), "--output", str(output), "--proxy-mode", "disabled",
                                                  "--cloudflare", "off", "--allow-internal-hosts", "--no-ytdlp", "--no-selenium",
                                                  "--no-deep-scan", "--threads", "1"],
                                        cwd=root, env=environment, capture_output=True, timeout=120, check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if result.returncode:
                    raise RuntimeError(f"{extension} failed: {result.stderr[-2000:]!r}")
                files = list(output.rglob(f"Résumé 日本 {extension}.json"))
                assert len(files) == 1, f"{extension}: imported Unicode output name missing"
                data = json.loads(files[0].read_text("utf-8"))
                image = data["rows"][0]["image"]
                assert image.startswith("data:image/png;base64,")
                assert hashlib.sha256(base64.b64decode(image.split(",", 1)[1])).digest() == hashlib.sha256(png).digest()
                checks.append(f"{extension.upper()}: real reader, Unicode queue name, complete embedded PNG bytes")
            if args.runtime_root:
                helper = args.runtime_root / "selenium/webdriver/common/windows/selenium-manager.exe"
                result = subprocess.run([str(helper), "--version"], capture_output=True, timeout=15, check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                assert result.returncode == 0 and b"selenium" in result.stdout.lower()
                checks.append("Native Selenium Manager retained and --version runs offline")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"passed": len(checks), "checks": checks, "http_requests": counts}, indent=2), "utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
