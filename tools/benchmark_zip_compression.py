"""Compare complete ZIP creation/validation on identical valid PNG and text."""

from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyoa_downloader_app.download import package


def run(repeats: int) -> dict:
    results = {}
    with tempfile.TemporaryDirectory(prefix="cyoa-zip-benchmark-") as scratch:
        root = Path(scratch).resolve()
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        source = root / "site"
        source.mkdir()
        raw = random.Random(42).randbytes(2048 * 2048 * 3)
        buffer = io.BytesIO()
        Image.frombytes("RGB", (2048, 2048), raw).save(buffer, format="PNG")
        (source / "hero.png").write_bytes(buffer.getvalue())
        (source / "project.json").write_text(json.dumps({"rows": [{"id": i, "text": "A story with many choices " * 30} for i in range(4000)]}), encoding="utf-8")
        original = zipfile.ZipFile.write

        def deflate(archive, filename, arcname=None, **kwargs):
            kwargs["compress_type"] = zipfile.ZIP_DEFLATED
            return original(archive, filename, arcname, **kwargs)

        # Alternate implementations each repetition to reduce order bias.
        for repetition in range(repeats):
            for label in (("control", "optimized") if repetition % 2 == 0 else ("optimized", "control")):
                with patch.object(zipfile.ZipFile, "write", deflate if label == "control" else original):
                    started = time.perf_counter()
                    cpu = time.process_time()
                    target = Path(package.zip_temp_folder(str(source), str(root / f"{label}.zip")))
                    sample = {"seconds": time.perf_counter() - started, "cpu_seconds": time.process_time() - cpu, "zip_bytes": target.stat().st_size}
                with zipfile.ZipFile(target) as archive:
                    assert archive.testzip() is None
                    assert all(archive.read(name) == (source / name).read_bytes() for name in archive.namelist())
                results.setdefault(label, []).append(sample)
        return {
            "input_bytes": sum(path.stat().st_size for path in source.iterdir()),
            "samples": results,
            "medians": {label: {key: statistics.median(sample[key] for sample in samples) for key in ("seconds", "cpu_seconds", "zip_bytes")} for label, samples in results.items()},
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(run(args.repeats), indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
