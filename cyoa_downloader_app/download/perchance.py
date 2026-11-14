"""Archive public Perchance HTML applications from their published source manifest."""

from __future__ import annotations

import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from requests import RequestException

from ..core.atomic_io import atomic_write_text
from ..core.cancellation import _raise_if_cancelled
from ..core.paths import _safe_archive_join
from ..logging_setup import logger
from ..network.fetch import fetch_response
from .package import atomic_stream_response_to_file
from .website import WebsiteDownloader


def _read_public_json(url: str):
    response = fetch_response(url, timeout=25, quiet=True)
    if response is None:
        return None
    try:
        return response.json()
    except (RequestException, ValueError) as exc:
        logger.debug("Perchance source metadata unavailable: %s", type(exc).__name__)
        return None
    finally:
        response.close()


def _standalone_template(generator: dict) -> bool:
    template = generator.get("outputTemplate")
    manifest = generator.get("srcManifest")
    if not isinstance(template, str) or not isinstance(manifest, dict) or not manifest or len(manifest) > 5000:
        return False
    # Ordinary HTML/JS applications can run directly. Generators whose visible
    # HTML still needs Perchance's expression compiler keep the normal fallback.
    soup = BeautifulSoup(template, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return not any(re.search(r"\[[A-Za-z_$][\w.$]*(?:\([^\]\n]*\))?\]", text) for text in soup.stripped_strings)


def fetch_perchance_source(url: str) -> dict | None:
    parsed = urlparse(url)
    name = parsed.path.strip("/")
    if parsed.hostname not in {"perchance.org", "www.perchance.org"} or not re.fullmatch(r"[a-z0-9_-]{1,160}", name):
        return None
    version = _read_public_json("https://perchance.org/api/generatorPageDataVersion/" + quote(name))
    if not isinstance(version, dict) or version.get("status") != "success" or version.get("isPrivate"):
        return None
    try:
        token = "-".join(
            str(int(version.get(key) or 0)) for key in ("lastEditTime", "saveCount", "editorPayloadRevision", "schema")
        )
    except (TypeError, ValueError, OverflowError):
        return None
    data = _read_public_json("https://perchance.org/api/generatorPageData/" + quote(name) + "/" + token)
    generator = data.get("generator") if isinstance(data, dict) else None
    if not isinstance(generator, dict) or generator.get("isPrivate") or not _standalone_template(generator):
        return None
    if data.get("dependencies") or generator.get("imports"):
        return None
    return data


def archive_perchance_source(source: dict, url: str, folder: str, *, max_workers: int = 4) -> dict:
    """Retain the source and every manifest file, then localize ordinary HTML."""
    generator = source["generator"]
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(root / "perchance_source.json"), json.dumps(source, ensure_ascii=False, indent=2))
    files = []
    substitutions = {}
    local_files = {}
    for authored, item in generator["srcManifest"].items():
        if not isinstance(authored, str) or not isinstance(item, dict):
            raise TypeError("Invalid Perchance source manifest")
        if any(part in {"", ".", ".."} for part in authored.replace("\\", "/").split("/")):
            raise ValueError("Invalid Perchance source path")
        key = str(item.get("key") or "")
        if not re.fullmatch(r"[a-f0-9]{16,64}\.[a-zA-Z0-9]{1,12}", key):
            raise ValueError("Invalid Perchance upload key")
        local = Path(_safe_archive_join(str(root), "src/" + authored, decode_url=False))
        remote = "https://user.uploads.dev/file/" + key
        substitutions[remote] = quote(local.relative_to(root).as_posix(), safe="/")
        local_files[remote] = local
        files.append((remote, local))

    def fetch_file(pair):
        _raise_if_cancelled()
        remote, local = pair
        response = fetch_response(remote, timeout=25, as_bytes=True, quiet=True)
        if response is None:
            return remote
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            atomic_stream_response_to_file(response, str(local))
            return None
        except (OSError, RequestException, ValueError) as exc:
            logger.warning("Perchance source asset failed: %s (%s)", remote, type(exc).__name__)
            return remote
        finally:
            response.close()

    with ThreadPoolExecutor(max_workers=max(1, min(8, max_workers))) as pool:
        failed = [remote for remote in pool.map(fetch_file, files) if remote]
    substitutions = {remote: relative for remote, relative in substitutions.items() if remote not in failed}
    # Runtime image maps resolve URL strings against the viewer document. Keep
    # original authored files too, so a future export can reconstruct the source.
    for _remote, local in files:
        if local.suffix.lower() not in {".js", ".mjs", ".css", ".html", ".json"} or not local.is_file():
            continue
        text = local.read_text(encoding="utf-8")
        updated = text
        for remote, relative in substitutions.items():
            if local.suffix.lower() == ".css":
                relative = quote(os.path.relpath(local_files[remote], local.parent).replace("\\", "/"), safe="/")
            updated = updated.replace(remote, relative)
        if updated != text:
            backup = root / "__original_site__" / local.relative_to(root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, backup)
            atomic_write_text(str(local), updated)
    template = generator["outputTemplate"]
    for remote, relative in substitutions.items():
        template = template.replace(remote, relative)
    downloader = WebsiteDownloader(url, str(root), max_workers=max_workers, ai_mode="off")
    try:
        for remote, local in files:
            if remote in failed or not local.is_file():
                continue
            # These authored paths are virtual on Perchance: fetching /src/*
            # returns its hosting shell, not the manifest's uploaded file.
            alias = urljoin(url, local.relative_to(root).as_posix())
            downloader._downloaded[alias] = str(local)
            downloader._downloaded[remote] = str(local)
            downloader._source_for_local[str(local)] = remote
        downloader.download_html_page(url, str(root / "index.html"), html_text=template)
        downloader.write_manifest()
        integrity = downloader.validate_integrity()
    finally:
        downloader.close()
        downloader.session.close()
    manifest = {
        "format": 1,
        "start_url": url,
        "auto_profile": {"detected_engine": "perchance_source_manifest", "effective_strategy": "classic"},
        "pages": [{"url": url, "local": "index.html"}],
        "source_files": [os.path.relpath(local, root).replace("\\", "/") for _, local in files],
        "failed_assets": failed,
        "integrity": integrity,
    }
    atomic_write_text(str(root / "archive_manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2))
    logger.info("Perchance source archive: %d files, %d failures", len(files), len(failed))
    return manifest
