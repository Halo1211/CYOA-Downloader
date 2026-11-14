"""Compare a trusted previous media pipeline against streamed project outputs."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from cyoa_downloader_app.core.media_io import atomic_write_text_chunks
    from cyoa_downloader_app.download import image_pipeline as pipeline

    tree = ast.parse(args.control.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_process_images")
    namespace = dict(vars(pipeline))
    paths = ["hero.png", "./hero.png", "folder/hero.png", "images/a.png?v=1", "images/a.png?v=2",
             "https://example.test/img.png", "https://cdn.example.test/img.png Credit Artist",
             "https:/cdn.example.test/other.png"]
    cases = []
    with tempfile.TemporaryDirectory(prefix="cyoa-embed-compat-") as scratch, ExitStack() as stack:
        logging.disable(logging.CRITICAL)
        for name in ("_cache_get", "_cache_get_file"):
            stack.enter_context(patch.object(pipeline, name, lambda *a, **kw: None))
        for name in ("_cache_put", "_cache_put_file", "_throttle_bandwidth", "_domain_record_success"):
            stack.enter_context(patch.object(pipeline, name, lambda *a, **kw: None))
        stack.enter_context(patch.object(pipeline, "vpn_requirement_satisfied", lambda: True))
        stack.enter_context(patch.object(pipeline, "_ssrf_block_cross_origin", lambda *a: False))
        stack.enter_context(patch.object(pipeline, "_is_gallery_dl_site", lambda *a: ""))
        def response(url, **_):
            result = requests.Response()
            result.url, result.status_code = url, 200
            result.headers["Content-Type"] = "audio/mpeg" if ".mp3" in url else "image/png"
            result._content = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(url.encode()).digest() * 5
            result._content_consumed = True
            return result
        stack.enter_context(patch.object(pipeline, "fetch_response", response))
        namespace.update(vars(pipeline))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(args.control), "exec"), namespace)
        control = namespace["_process_images"]
        for i in range(40):
            project = {"title": "雪 α 🎲", "rows": [
                {"image" if i % 2 else "ImAgE": paths[i % len(paths)],
                 "backgroundImage": paths[(i + 1) % len(paths)],
                 "audio": "audio/song.mp3", "useAudioURL": True, "bgmId": "audio/song.mp3"},
                {"objectBackgroundImage": paths[(i + 2) % len(paths)],
                 "text": '<img src="https://example.test/inline.png"> label',
                 "image": "data:image/png;base64,cHJlc2VydmU="},
            ]}
            text = json.dumps(project, ensure_ascii=i % 2 == 0, indent=2 if i % 3 else None)
            if i % 4 == 0:
                text = text.replace("/", "\\/")
            for both in (False, True):
                root = Path(scratch) / f"{i}-{both}"
                root.mkdir()
                old = control(text, "https://example.test/", embed=True, embed_audio=True,
                              download=both, temp_folder=str(root / "old"), max_workers=1, output_dir=str(root))
                # Explicit regression fix: credits must not become base64 bytes
                # in a plain image field. Prose/HTML attribution stays intact.
                corrected, credits_fixed = re.subn(
                    r'("(?:image|backgroundImage|objectBackgroundImage)"\s*:\s*"data:image/[^\"]+) Credit Artist"',
                    r'\1"', old[0], flags=re.IGNORECASE)
                old = (corrected, old[1], old[2])
                new = pipeline.process_images(text, "https://example.test/", embed=True, embed_audio=True,
                                              download=both, temp_folder=str(root / "new"), max_workers=1,
                                              output_dir=str(root))
                offset = next((j for j, pair in enumerate(zip(new[0], old[0], strict=False)) if pair[0] != pair[1]), 0)
                assert new == old, (f"API output differs, fixture {i}, both={both}, offset={offset}; "
                                   f"old={old[0][max(0,offset-45):offset+90]!r}; "
                                   f"new={new[0][max(0,offset-45):offset+90]!r}")
                output = root / "streamed.json"
                streamed = pipeline.process_images(text, "https://example.test/", embed=True, embed_audio=True,
                    download=both, temp_folder=str(root / "sink"), max_workers=1, output_dir=str(root),
                    embed_writer=lambda chunks, output=output: atomic_write_text_chunks(output, chunks))
                assert output.read_bytes() == old[0].encode(), f"Stream differs, fixture {i}, both={both}"
                assert streamed[1:] == old[1:]
                if both:
                    for file in (root / "old").rglob("*"):
                        if file.is_file():
                            relative = file.relative_to(root / "old")
                            assert file.read_bytes() == (root / "sink" / relative).read_bytes()
                cases.append({"fixture": i, "both": both, "credit_cleanup_fields": credits_fixed,
                              "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    args.report.write_text(json.dumps({"passed": len(cases), "cases": cases}, indent=2), encoding="utf-8")
    print(f"{len(cases)} fixtures match previous outputs with explicit image-credit cleanup")


if __name__ == "__main__":
    main()
