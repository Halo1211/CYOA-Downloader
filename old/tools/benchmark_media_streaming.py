"""Isolated media-pipeline RAM benchmark with generated HTTP bodies, no internet."""

from __future__ import annotations

import argparse
import ast
import base64
import gc
import hashlib
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sample(args):
    from cyoa_downloader_app.download import image_pipeline as pipeline
    if args.validate_output:
        from cyoa_downloader_app.core.json_summary import load_json_summary
        from cyoa_downloader_app.download.orchestrator import _classify_project_image_references

    logging.disable(logging.CRITICAL)
    size = args.asset_mib * 1024 * 1024
    content_reads = [0]

    def chunks(number, chunk_size=65536):
        header = (b"ID3" if args.media_type == "audio" else b"\x89PNG\r\n\x1a\n") + str(number).encode().ljust(32, b" ")
        yield header
        remaining = size - len(header)
        block = b"x" * chunk_size
        while remaining:
            yield block[:min(len(block), remaining)]
            remaining -= min(len(block), remaining)

    class Response:
        status_code = 200

        def __init__(self, url):
            self.headers = {"Content-Type": "audio/mpeg" if args.media_type == "audio" else "image/png", "Content-Length": str(size)}
            self.url = url
            self.number = int(url.rsplit("/", 1)[-1].split(".")[0])
            self._body = None

        @property
        def content(self):
            content_reads[0] += 1
            if self._body is None:
                self._body = b"".join(chunks(self.number))
            return self._body

        def iter_content(self, chunk_size=65536):
            return chunks(self.number, chunk_size)

        def raise_for_status(self):
            pass

        def close(self):
            pass

    field = args.media_type
    extension = "mp3" if field == "audio" else "png"
    project = json.dumps({"rows": [{field: f"{field}/{i}.{extension}"} for i in range(args.assets)]})
    process = psutil.Process()
    gc.collect()
    baseline_rss = process.memory_info().rss
    peak_rss = [baseline_rss]
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.01):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    watcher = threading.Thread(target=monitor, daemon=True)
    with tempfile.TemporaryDirectory(prefix="cyoa-media-memory-") as scratch, ExitStack() as stack:
        for name in ("_cache_put", "_cache_put_file", "_throttle_bandwidth", "_domain_record_success"):
            if hasattr(pipeline, name):
                stack.enter_context(patch.object(pipeline, name, lambda *a, **kw: None))
        for name in ("_cache_get", "_cache_get_file"):
            if hasattr(pipeline, name):
                stack.enter_context(patch.object(pipeline, name, lambda *a, **kw: None))
        stack.enter_context(patch.object(pipeline, "vpn_requirement_satisfied", lambda: True))
        stack.enter_context(patch.object(pipeline, "_ssrf_block_cross_origin", lambda *a: False))
        stack.enter_context(patch.object(pipeline, "fetch_response", lambda url, **kw: Response(url)))
        if args.gallery_file_fixture:
            from cyoa_downloader_app.integrations import gallery_dl

            def extract(cmd):
                directory = Path(cmd[cmd.index("--destination") + 1])
                number = int(cmd[-1].rsplit("/", 1)[-1].split(".")[0])
                with (directory / "fixture.png").open("wb") as handle:
                    for chunk in chunks(number):
                        handle.write(chunk)
                return 0, ""

            for name, value in (
                ("_run_gallery_dl", extract), ("_is_gallery_dl_candidate", lambda *a: "fixture"),
                ("_gallery_dl_is_available", lambda: True), ("_get_active_proxy", lambda: ""),
                ("_gallery_dl_config", ""),
            ):
                stack.enter_context(patch.object(gallery_dl, name, value))
            gallery_fetch = gallery_dl._fetch_via_gallery_dl
            if args.control_gallery:
                tree = ast.parse(args.control_gallery.read_text(encoding="utf-8"))
                nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == "_fetch_via_gallery_dl"]
                namespace = dict(vars(gallery_dl))
                exec(compile(ast.Module(body=nodes, type_ignores=[]), str(args.control_gallery), "exec"), namespace)
                gallery_fetch = namespace["_fetch_via_gallery_dl"]
            stack.enter_context(patch.object(pipeline, "_is_gallery_dl_site", lambda *a: "fixture"))
            stack.enter_context(patch.object(pipeline, "_fetch_via_gallery_dl", gallery_fetch))
        process_media = pipeline.process_images
        if args.control_pipeline:
            tree = ast.parse(args.control_pipeline.read_text(encoding="utf-8"))
            nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"process_images", "_process_images"}]
            namespace = dict(vars(pipeline))
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(args.control_pipeline), "exec"), namespace)
            process_media = namespace["process_images"]
        gc.collect()
        baseline_rss = process.memory_info().rss
        peak_rss[0] = baseline_rss
        tracemalloc.start()
        watcher.start()
        started = time.perf_counter()
        try:
            options = {}
            if args.stream_output:
                from cyoa_downloader_app.download.package import save_text_chunks_to_file
                options["embed_writer"] = lambda chunks: save_text_chunks_to_file(chunks, "embedded.json", scratch)
            embedded, localized, resolved = process_media(
                project, "https://example.test/", embed=args.mode != "zip", download=args.mode != "embed",
                embed_audio=True, temp_folder=scratch, max_workers=args.workers, **options)
            if args.mode != "zip" and not args.stream_output:
                from cyoa_downloader_app.download.package import save_string_to_file
                save_string_to_file(embedded, "embedded.json", scratch)
            if args.mode != "zip" and args.validate_output:
                with (Path(scratch) / "embedded.json").open(encoding="utf-8") as handle:
                    if args.stream_output:
                        summary = load_json_summary(handle)
                    else:
                        summary = json.loads(handle.read())
                if field == "image":
                    assert _classify_project_image_references(summary)["data"] == args.assets
            seconds = time.perf_counter() - started
            _, peak_traced = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            stop.set()
            watcher.join(2)
        rows = json.loads(localized)["rows"]
        assert len(resolved) == args.assets
        for i, row in enumerate(rows if args.mode != "embed" else []):
            expected = hashlib.sha256()
            for block in chunks(i):
                expected.update(block)
            path = Path(scratch) / row[field]
            actual = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    actual.update(block)
            assert path.stat().st_size == size
            assert actual.digest() == expected.digest()
        if args.mode != "zip":
            expected = hashlib.sha256()
            offset = 0
            for match in re.finditer(r'"' + field + r'"\s*:\s*"([^\"]+)"', project):
                expected.update(project[offset:match.start()].encode())
                number = int(match.group(1).rsplit("/", 1)[-1].split(".")[0])
                mime = "audio/mpeg" if field == "audio" else "image/png"
                expected.update(match.group(0)[:match.start(1)-match.start()].encode())
                expected.update(f'data:{mime};base64,'.encode())
                expected.update(base64.b64encode(b"".join(chunks(number))))
                expected.update(b'"')
                offset = match.end()
            expected.update(project[offset:].encode())
            with (Path(scratch) / "embedded.json").open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").digest() == expected.digest()
        assert not list(Path(scratch).rglob("*.part"))
    return {"seconds": seconds, "baseline_rss_bytes": baseline_rss, "peak_rss_bytes": peak_rss[0],
            "rss_growth_bytes": peak_rss[0] - baseline_rss, "peak_tracemalloc_bytes": peak_traced,
            "content_reads": content_reads[0], "verified_files": len(rows) if args.mode != "embed" else 0,
            "verified_embedded_assets": args.assets if args.mode != "zip" else 0,
            "verified_bytes": size * len(rows)}


def sample(args):
    with tempfile.TemporaryDirectory(prefix="cyoa-media-profile-") as profile, patch.dict(os.environ, {"USERPROFILE": profile}):
        return _sample(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=int, default=32)
    parser.add_argument("--asset-mib", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mode", choices=("zip", "embed", "both"), default="zip")
    parser.add_argument("--media-type", choices=("image", "audio"), default="audio")
    parser.add_argument("--stream-output", action="store_true")
    parser.add_argument("--validate-output", action="store_true")
    parser.add_argument("--control-pipeline", type=Path, help="Trusted pre-change source snapshot")
    parser.add_argument("--gallery-file-fixture", action="store_true", help="Simulate an extractor producing files, without network")
    parser.add_argument("--control-gallery", type=Path, help="Trusted pre-change gallery helper snapshot")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.gallery_file_fixture and args.media_type != "image":
        parser.error("--gallery-file-fixture requires --media-type image")
    if args.sample:
        print(json.dumps(sample(args)))
        return
    samples = []
    for _ in range(args.runs):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--sample", "--assets", str(args.assets),
                                "--asset-mib", str(args.asset_mib), "--workers", str(args.workers),
                                "--mode", args.mode, "--media-type", args.media_type,
                                *(["--stream-output"] if args.stream_output else []),
                                *(["--validate-output"] if args.validate_output else []),
                                *(["--gallery-file-fixture"] if args.gallery_file_fixture else []),
                                *(["--control-gallery", str(args.control_gallery)] if args.control_gallery else []),
                                *(["--control-pipeline", str(args.control_pipeline)] if args.control_pipeline else [])],
                               capture_output=True, text=True, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        samples.append(json.loads(child.stdout))
    result = {"assets": args.assets, "asset_mib": args.asset_mib, "workers": args.workers,
              "mode": args.mode, "media_type": args.media_type, "stream_output": args.stream_output, "samples": samples,
              "validate_output": args.validate_output, "control_pipeline": str(args.control_pipeline or ""),
              "gallery_file_fixture": args.gallery_file_fixture, "control_gallery": str(args.control_gallery or ""),
              "medians": {key: statistics.median(row[key] for row in samples) for key in samples[0]},
              "note": ("Generated extractor files, in-process fixture (not gallery-dl subprocess RAM); " if args.gallery_file_fixture else "Generated response bodies; ") +
                      "actual temporary output, cache disabled; hash validation outside timer. No internet."}
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}, indent=2))


if __name__ == "__main__":
    main()
