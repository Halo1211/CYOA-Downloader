"""Archives preserve bytes while avoiding futile compression of dense media."""

import random
import zipfile

from cyoa_downloader_app.download import package


def test_zip_stores_dense_media_but_compresses_text_and_sparse_media(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    bodies = {
        "dense.PNG": random.Random(42).randbytes(300_000),
        "sparse.png": b"compressible media fixture" * 20000,
        "app.js": b"function choice(){return 'story';}\n" * 10000,
        "tiny.jpg": b"tiny" * 10,
    }
    for name, body in bodies.items():
        (source / name).write_bytes(body)
    path = package.zip_temp_folder(str(source), str(tmp_path / "result.zip"))
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in archive.namelist()} == bodies
        assert archive.getinfo("dense.PNG").compress_type == zipfile.ZIP_STORED
        assert all(archive.getinfo(name).compress_type == zipfile.ZIP_DEFLATED for name in bodies if name != "dense.PNG")
